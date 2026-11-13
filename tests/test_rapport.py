"""Tests du mode rapport, avec un fournisseur de données simulé (aucun réseau)."""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import fabriquer_chronique

from aquibrie_viz import rapport as rp

RACINE = Path(__file__).resolve().parents[1]
CODES = ["02603X0009/S1", "02581X0045/P", "02606X0001/F", "02615X0010/P", "01857X0004/S1", "02212X0012/P1"]


class FauxFournisseur:
    def __init__(self):
        self.appels_chronique = 0

    def stations(self, masse_eau):
        communes = ["Beauchery-Saint-Martin", "Melun", "Nangis", "Provins", "Coulommiers", "Fontenay"]
        return pd.DataFrame({
            "code_bss": CODES, "bss_id": [f"BSS{i}" for i in range(6)],
            "nom": [f"{c} ({code})" for c, code in zip(communes, CODES)], "commune": communes,
            "lon": np.linspace(2.6, 3.4, 6), "lat": np.linspace(48.5, 48.8, 6),
            "date_fin_mesure": pd.Timestamp("2026-10-31"),
        })

    def chronique(self, code):
        self.appels_chronique += 1
        return fabriquer_chronique(base=130 - CODES.index(code), seed=CODES.index(code))

    def chroniques(self, codes):  # téléchargement groupé, compté à part
        return {c: fabriquer_chronique(base=130 - CODES.index(c), seed=CODES.index(c)) for c in codes}

    def analyses(self, code_param, debut, fin, masse_eau):
        dates = pd.date_range(debut, fin, freq="3MS")
        lignes = [{"bss_id": s, "commune": s, "lon": 2.8, "lat": 48.6, "date": d, "resultat": v,
                   "quantifie": True, "lq": 0.5}
                  for s, v in [("BSS000PQJR", 65.0), ("BSS2", 30.0)] for d in dates]
        return pd.DataFrame(lignes)


@pytest.fixture
def exemple() -> rp.Rapport:
    return rp.lire((RACINE / "rapports" / "exemple_bulletin.toml").read_text(encoding="utf-8"))


def test_le_fichier_d_exemple_est_valide(exemple):
    assert len(exemple.figures) == 8
    assert {f["modele"] for f in exemple.figures} <= set(rp.MODELES)


def test_aller_retour_toml(exemple):
    exemple.figures[3]["seuils"] = [127.5, 125.5, 123.6, 121.6]
    relu = rp.lire(rp.ecrire(exemple))
    assert relu.figures == exemple.figures and relu.titre == exemple.titre


@pytest.mark.parametrize("spec, message", [
    ({"id": "a", "modele": "inconnu"}, "Modèle inconnu"),
    ({"id": "A B", "modele": "carte_situation"}, "Identifiant invalide"),
    ({"id": "a", "modele": "chronique_seuils"}, "station"),
    ({"id": "a", "modele": "carte_qualite", "substance": "Uranium"}, "substance"),
])
def test_specifications_invalides(spec, message):
    with pytest.raises(rp.RapportError, match=message):
        rp.valider(spec)


def test_identifiants_en_double():
    texte = '[[figure]]\nid = "x"\nmodele = "carte_situation"\n[[figure]]\nid = "x"\nmodele = "situation_stations"\n'
    with pytest.raises(rp.RapportError, match="même identifiant"):
        rp.lire(texte)


def test_identifiant_libre():
    assert rp.identifiant_libre("Niveau Beauchery!", {"niveau_beauchery"}) == "niveau_beauchery_2"


def test_toutes_les_figures_se_construisent(exemple):
    ctx = rp.Contexte(FauxFournisseur(), aujourd_hui=date(2026, 11, 5))
    for spec in exemple.figures:
        fig = rp.construire(spec, ctx)
        assert rp.titre_brut(fig), spec["id"]


def test_les_chroniques_sont_chargees_une_seule_fois(exemple):
    f = FauxFournisseur()
    ctx = rp.Contexte(f, aujourd_hui=date(2026, 11, 5))
    for spec in exemple.figures:
        if spec.get("station"):
            rp.construire(spec, ctx)
    assert f.appels_chronique == 1


def test_annee_derniere_suit_les_donnees():
    ctx = rp.Contexte(FauxFournisseur(), aujourd_hui=date(2026, 11, 5))
    fig = rp.construire({"id": "a", "modele": "annee_vs_historique", "station": CODES[0], "periode_ans": 10}, ctx)
    assert "2026" in rp.titre_brut(fig)


def test_figure_en_erreur_n_empeche_pas_les_autres(tmp_path):
    rapport = rp.Rapport(figures=[
        {"id": "ok", "modele": "situation_stations"},
        {"id": "ko", "modele": "concentration_norme", "substance": "Nitrates", "bss_qualite": "INEXISTANTE"},
    ])
    res = rp.generer(rapport, tmp_path, FauxFournisseur(), formats={"svg"})
    assert "ko" in res.erreurs and (tmp_path / "ok.svg").exists()


def test_generation_complete(exemple, tmp_path):
    """Génère PNG, PowerPoint et Word (avec le gabarit d'exemple). Nécessite Chrome."""
    pytest.importorskip("pptx")
    res = rp.generer(exemple, tmp_path, FauxFournisseur(), formats={"png", "pptx", "docx"},
                     gabarit_word=RACINE / "rapports" / "gabarit_exemple.docx")
    assert not res.erreurs
    assert len(res.images) == 8 and all(p.stat().st_size > 10_000 for p in res.images)
    # Sans Internet (environnement de test), les cartes sont exportées sans fond et signalées.
    assert set(res.avertissements) <= {"carte_situation", "nitrates_carte"}

    from docx import Document
    from pptx import Presentation

    pptx_path = next(p for p in res.documents if p.suffix == ".pptx")
    assert len(Presentation(pptx_path).slides) == 9  # couverture + 8 figures
    docx_path = next(p for p in res.documents if p.suffix == ".docx")
    document = Document(docx_path)
    texte = "\n".join(p.text for p in document.paragraphs)
    assert "Bulletin de situation" in texte and "{{ titre }}" not in texte
    assert not any(f"{{{{ fig_{f['id']} }}}}" in texte for f in exemple.figures)
    assert len(document.inline_shapes) == 4  # les 4 balises de figure du gabarit
