"""Données de référence propres à la nappe du Champigny.

Les seuils ci-dessous sont recopiés du document de présentation d'AQUI'Brie
(graphique « Piézomètre de Beauchery-Saint-Martin », mis à jour le 16 mars 2026).
Ils devront être confirmés par l'association et complétés pour les autres
piézomètres de référence des arrêtés sécheresse.
"""
from __future__ import annotations

import unicodedata

import pandas as pd

# Masse d'eau « Tertiaire du Brie-Champigny et du Soissonnais » (FRHG103),
# au format attendu par le filtre codes_masse_eau_edl de Hub'Eau.
CODE_MASSE_EAU_CHAMPIGNY = "HG103"

# Départements du territoire d'AQUI'Brie.
DEPARTEMENTS_CHAMPIGNY = ["77", "91", "94"]

SEUILS_CONNUS: dict[str, dict[str, float]] = {
    "BEAUCHERY": {
        "Vigilance": 127.5,
        "Alerte": 125.5,
        "Alerte renforcée": 123.6,
        "Crise": 121.6,
    },
}


def _normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return sans_accents.upper()


def seuils_pour(nom_station: str) -> dict[str, float] | None:
    """Seuils réglementaires connus pour une station, d'après son nom de commune."""
    nom = _normaliser(nom_station)
    for cle, seuils in SEUILS_CONNUS.items():
        if cle in nom:
            return dict(seuils)
    return None


# Quantiles de l'historique servant de seuils indicatifs quand aucun seuil
# réglementaire n'est connu (affichés comme tels, jamais comme officiels).
QUANTILES_INDICATIFS = {"Vigilance": 0.30, "Alerte": 0.20, "Alerte renforcée": 0.10, "Crise": 0.03}


def seuils_indicatifs(chronique: pd.DataFrame) -> dict[str, float]:
    q = chronique["niveau"].quantile
    return {nom: round(float(q(p)), 2) for nom, p in QUANTILES_INDICATIFS.items()}


def seuils_station(nom_station: str, chronique: pd.DataFrame) -> tuple[dict[str, float], bool]:
    """(seuils, officiels) : réglementaires s'ils sont connus, sinon indicatifs."""
    officiels = seuils_pour(nom_station)
    return (officiels, True) if officiels else (seuils_indicatifs(chronique), False)


# Substances suivies : code SANDRE, norme de qualité de l'eau potable, unité.
# Codes à faire confirmer par AQUI'Brie, qui pourra en ajouter.
SUBSTANCES = {
    "Nitrates": {"code": "1340", "norme": 50.0, "unite": "mg/L"},
    "Atrazine": {"code": "1107", "norme": 0.1, "unite": "µg/L"},
    "Atrazine déséthyl": {"code": "1108", "norme": 0.1, "unite": "µg/L"},
    "Glyphosate": {"code": "1506", "norme": 0.1, "unite": "µg/L"},
    "AMPA": {"code": "1907", "norme": 0.1, "unite": "µg/L"},
}

# Chiffre clé repris de la présentation d'AQUI'Brie (cahier des charges du projet).
HABITANTS_ALIMENTES = "près d'un million"
