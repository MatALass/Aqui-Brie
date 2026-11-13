import pandas as pd
import pytest

from aquibrie_viz import indicators as ind
from aquibrie_viz import reference, templates
from aquibrie_viz.export import figure_to_bytes

SEUILS = {"Vigilance": 129, "Alerte": 128, "Alerte renforcée": 127.5, "Crise": 127}


def _noms(fig):
    return [t.name for t in fig.data if t.name]


def _a_signature(fig):
    return any("Source" in (a.text or "") for a in fig.layout.annotations)


def test_chronique_seuils_avec_projection(chronique):
    chro = chronique.copy()
    chro.loc[chro.index[-30:], "niveau"] = [129.5 - 0.02 * i for i in range(30)]
    proj = ind.projection_seuil(chro, SEUILS)
    fig = templates.chronique_seuils(chro, SEUILS, "Station test", projection=proj)
    assert "seuil" in fig.layout.title.text
    assert any("Si la baisse continue" in (a.text or "") for a in fig.layout.annotations)
    assert _a_signature(fig)


def test_annee_vs_historique(chronique):
    fig = templates.annee_vs_historique(chronique, 2026, "Station test")
    assert {"2026", "Médiane"} <= set(_noms(fig))


def test_annee_sans_historique_leve_une_erreur(chronique):
    une_annee = chronique[chronique["date"].dt.year == 2026]
    with pytest.raises(ValueError):
        templates.annee_vs_historique(une_annee, 2026, "Station test")


def test_recharge_et_calendrier(chronique):
    fig = templates.recharge_annuelle(ind.recharge_annuelle(chronique), "Station test")
    assert "Recharge" in fig.layout.title.text
    fig = templates.calendrier_anomalies(ind.anomalies_mensuelles(chronique), "Station test")
    assert "mois" in fig.layout.title.text


def test_situation_et_carte(chronique, stations):
    chroniques = {c: chronique for c in stations["code_bss"]}
    sit = ind.situation_territoire(stations, chroniques)
    assert len(sit) == 3
    fig = templates.situation_stations(sit)
    assert "Aucune des 3 stations" in fig.layout.title.text  # même chronique partout : situation normale
    carte = templates.carte_situation(sit, stations, selection="B")
    assert "Station choisie" in _noms(carte)


def test_qualite(analyses):
    synth = ind.synthese_stations_qualite(analyses, 0.1)
    carte = templates.carte_qualite(synth, "Atrazine", "µg/L")
    assert "1 station sur 3 a dépassé" in carte.layout.title.text
    fig = templates.qualite_annuelle(ind.synthese_annuelle_qualite(analyses, 0.1), "Atrazine")
    assert "%" in fig.layout.title.text


def test_concentration_compte_les_depassements():
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=4, freq="MS"),
        "resultat": [0.05, 0.2, None, 0.15],
        "quantifie": [True, True, False, True],
        "lq": [0.02] * 4,
    })
    fig = templates.concentration_norme(df, 0.1, "Atrazine", "µg/L", "Station test")
    assert "2 analyses sur 4" in fig.layout.title.text
    assert "Non quantifié (< LQ)" in _noms(fig)


def test_seuils_connus_par_commune():
    seuils = reference.seuils_pour("Beauchery-Saint-Martin (02603X0009/S1)")
    assert seuils["Vigilance"] == 127.5 and seuils["Crise"] == 121.6
    assert reference.seuils_pour("Melun (X)") is None


def test_export_html(chronique):
    fig = templates.annee_vs_historique(chronique, 2026, "Station test")
    assert b"<html>" in figure_to_bytes(fig, "html")[:100]
