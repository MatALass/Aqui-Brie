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


# Réseau des bulletins « Info Piézo » d'AQUI'Brie, reconstitué à partir de Hub'Eau.
# Le bulletin n°65 (juin 2026) parle de « 26 piézomètres consultables » sans en donner les
# codes. On retient les 26 piézomètres actifs en 2026 qui captent le Champigny : le réseau
# départemental (codes en /P1 à /P19, suivis depuis 2002-2003, souvent sans masse d'eau
# renseignée dans Hub'Eau) et les piézomètres de la masse d'eau HG103. La liste exacte est
# à faire confirmer par AQUI'Brie.
RESEAU_INFO_PIEZO: dict[str, str] = {
    "01846X0361/P1": "Roissy-en-Brie",
    "02203X0106/P3": "Gretz-Armainvilliers",
    "02215X0049/P5": "Courpalay",
    "02218X0033/P6": "Bannost-Villegagnon",
    "02205X0121/P7": "Moissy-Cramayel",
    "02207X0069/P8": "Champdeuil",
    "02581X0103/P9": "Savigny-le-Temple",
    "02582X0268/P11": "Le Mée-sur-Seine",
    "02583X0065/P12": "Maincy",
    "02594X0094/P13": "Maison-Rouge",
    "02596X0045/P14": "Villeneuve-les-Bordes",
    "02222X0034/P16": "Cerneux",
    "02581X0104/P18": "Saint-Fargeau-Ponthierry",
    "02206X0118/P19": "Évry-Grégy-sur-Yerre",
    "02211X0020/F1": "La Houssaye-en-Brie",
    "02592X0036/F1": "Nangis",
    "01853X0002/S1": "Signy-Signets",
    "02206X0022/S1": "Montereau-sur-le-Jard",
    "02225X0016/S1": "Saint-Hilliers",
    "02603X0009/S1": "Beauchery-Saint-Martin",
    "02584X0024/FCH7": "Châtillon-la-Borde",
    "02208X0036/FCH9": "Verneuil-l'Étang",
    "02217X0045/B": "Saint-Just-en-Brie",
    "02212X0032/F": "Pézarches",
    "02206X0085/F": "Brie-Comte-Robert",
    "02216X0029/F": "Voinsles",
}

# Piézomètres de référence des arrêtés sécheresse de Seine-et-Marne : Montereau-sur-le-Jard
# pour le Champigny ouest, Saint-Martin-Chennetron (commune de Beauchery-Saint-Martin) pour
# le Champigny est.
REFERENCE_OUEST = "02206X0022/S1"
REFERENCE_EST = "02603X0009/S1"

# Chiffres publiés dans l'Info Piézo n°65 (AQUI'Brie, 15 juin 2026, mesures du 13 juin 2026),
# pour comparer ce que l'outil recalcule avec ce que l'association a publié.
INFO_PIEZO_65 = {
    "url": "https://www.aquibrie.fr/publications/info-piezo-ndeg65-avril-juin-2026",
    "date": "2026-06-13",
    "debut_periode": "2026-03-16",  # date de l'Info Piézo n°64
    "piezometres": 26,
    "au_dessus_50": 16,
    "sous_50": 10,
    "sous_50_communes": [
        "Cerneux", "Bannost-Villegagnon", "Courpalay", "Maison-Rouge", "Saint-Hilliers",
        "Villeneuve-les-Bordes", "Évry-Grégy-sur-Yerre", "Brie-Comte-Robert", "Dammarie-les-Lys",
        "Saint-Fargeau-Ponthierry",
    ],
    "ecart_vigilance_m": {REFERENCE_OUEST: 2.3, REFERENCE_EST: 4.8},
    "baisse_m": {REFERENCE_OUEST: 0.5, REFERENCE_EST: 1.3},
    "projection_est": "sous le seuil de vigilance début décembre",
}

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
