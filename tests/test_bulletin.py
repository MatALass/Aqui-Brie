"""Indicateurs de l'Info Piézo : jauge 0-100, tranches, projection par analogie."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from conftest import fabriquer_chronique

from aquibrie_viz import bulletin, templates
from aquibrie_viz.indicators import situation

JOUR = pd.Timestamp("2026-06-13")


def _lineaire(debut: str, fin: str, bas: float, haut: float) -> pd.DataFrame:
    dates = pd.date_range(debut, fin, freq="D")
    return pd.DataFrame({"date": dates, "niveau": np.linspace(bas, haut, len(dates))})


@pytest.mark.parametrize(
    ("taux", "tranche"),
    [(0, "Très faible"), (24.9, "Très faible"), (25, "Faible"), (49.9, "Faible"), (50, "Satisfaisant"),
     (74.9, "Satisfaisant"), (75, "Très satisfaisant"), (100, "Très satisfaisant"), (None, None),
     (np.nan, None)],
)
def test_tranches_du_bulletin(taux, tranche):
    assert bulletin.classe_remplissage(taux) == tranche


def test_taux_de_remplissage_entre_min_et_max_depuis_2003():
    # Monte de 100 à 110 entre 2003 et 2026 : au dernier jour, on est au maximum.
    df = _lineaire("2003-01-01", "2026-06-13", 100, 110)
    assert bulletin.taux_remplissage(df, JOUR) == 100.0
    milieu = df["date"].iloc[len(df) // 2]
    assert bulletin.taux_remplissage(df, milieu) == pytest.approx(100.0, abs=0.1)  # max jusqu'au jour inclus


def test_les_mesures_d_avant_2003_ne_comptent_pas():
    avant = _lineaire("1990-01-01", "2002-12-31", 50, 50)  # un minimum très bas, hors période
    apres = _lineaire("2003-01-01", "2026-06-13", 110, 100)
    df = pd.concat([avant, apres], ignore_index=True)
    # Depuis 2003, le niveau du jour est le plus bas : 0. Avec 1990-2002, il vaudrait 83.
    assert bulletin.taux_remplissage(df, JOUR) == 0.0


def test_station_sans_historique_suffisant():
    df = _lineaire("2024-01-01", "2026-06-13", 100, 101)  # 2,5 ans
    assert bulletin.taux_remplissage(df, JOUR) is None


def test_station_sans_mesure_recente():
    df = _lineaire("2003-01-01", "2026-04-01", 100, 110)  # dernière mesure 73 jours avant
    assert bulletin.niveau_a_date(df, JOUR) is None
    assert bulletin.taux_remplissage(df, JOUR) is None


def test_niveau_constant_pas_de_taux():
    df = _lineaire("2003-01-01", "2026-06-13", 100, 100)
    assert bulletin.taux_remplissage(df, JOUR) is None


def test_synthese_garde_les_stations_sans_donnees(stations):
    chroniques = {"A": _lineaire("2003-01-01", "2026-06-13", 100, 110),
                  "B": _lineaire("2003-01-01", "2026-06-13", 110, 100),
                  # C : aucune chronique
                  }
    synth = bulletin.synthese_remplissage(stations, chroniques, JOUR)
    assert list(synth["commune"]) == ["Nangis", "Melun", "Provins"]  # tri par taux, vides à la fin
    assert synth.loc[synth["commune"] == "Provins", "tranche"].isna().all()
    comptes = bulletin.compte_tranches(synth)
    assert comptes == {"Très faible": 1, "Faible": 0, "Satisfaisant": 0, "Très satisfaisant": 1,
                       "au-dessus de 50": 1, "sous 50": 1, "sans mesure": 1}


def test_variation_entre_deux_dates():
    df = _lineaire("2026-01-01", "2026-12-31", 100, 136.4)  # +0,1 m par jour
    assert bulletin.variation(df, pd.Timestamp("2026-03-16"), JOUR) == pytest.approx(8.9, abs=0.01)
    assert bulletin.variation(df, pd.Timestamp("2020-01-01"), JOUR) is None  # pas de mesure au départ


def test_projection_par_analogie():
    df = fabriquer_chronique(debut=date(1995, 1, 1), fin=date(2026, 6, 13))
    niveau = bulletin.niveau_a_date(df, JOUR)[0]
    proj = bulletin.projection_analogues(df, JOUR, seuil=niveau - 1.0, horizon=200)
    assert proj is not None
    assert proj.nb_annees == 31  # 1995 à 2025
    assert np.allclose(proj.trajectoires.iloc[0], niveau)  # toutes partent du niveau du jour
    # Le cycle synthétique baisse de ~6 m de juin à l'automne : toutes les années franchissent le seuil.
    assert proj.part_franchissement == 1.0
    assert (proj.franchissements > JOUR).all()


def test_projection_ecarte_les_annees_trouees():
    df = fabriquer_chronique(debut=date(1995, 1, 1), fin=date(2026, 6, 13))
    trou = (df["date"] >= "2010-06-01") & (df["date"] <= "2010-12-31")
    proj = bulletin.projection_analogues(df[~trou], JOUR, seuil=0, horizon=200)
    assert 2010 not in proj.trajectoires.columns
    assert proj.part_franchissement == 0.0  # seuil à 0 m : jamais atteint


def test_projection_impossible_sans_historique():
    df = _lineaire("2024-01-01", "2026-06-13", 100, 101)
    assert bulletin.projection_analogues(df, JOUR, seuil=90) is None


def test_date_de_situation_commune():
    chroniques = {c: _lineaire("2003-01-01", fin, 1, 2) for c, fin in
                  [("a", "2026-09-17"), ("b", "2026-09-17"), ("c", "2026-09-17"), ("d", "2026-09-17"),
                   ("e", "2026-11-13")]}
    assert bulletin.date_situation_commune(chroniques) == pd.Timestamp("2026-09-17")
    assert bulletin.date_situation_commune({}) is None


def test_figures_info_piezo(stations):
    chroniques = {c: fabriquer_chronique(debut=date(1995, 1, 1), fin=date(2026, 6, 13), seed=i)
                  for i, c in enumerate(["A", "B", "C"])}
    synth = bulletin.synthese_remplissage(stations, chroniques, JOUR)
    fig = templates.jauges_remplissage(synth, JOUR)
    assert "sur 3 au-dessus de 50 %" in fig.layout.title.text
    assert templates.carte_remplissage(synth, JOUR).data
    proj = bulletin.projection_analogues(chroniques["A"], JOUR, seuil=126.0)
    fig = templates.projection_analogues(proj, "Melun", observe=chroniques["A"])
    assert "Melun" in fig.layout.title.text
    with pytest.raises(ValueError, match="Aucun piézomètre"):
        templates.jauges_remplissage(synth.assign(taux=np.nan), JOUR)


def test_situation_sans_historique_et_avec_lacunes():
    """Cas limites du classement ± 3 jours : trop peu d'années, et années trouées à la date du jour."""
    court = fabriquer_chronique(debut=date(2023, 1, 1), fin=date(2026, 6, 13))
    assert situation(court) is None
    long = fabriquer_chronique(debut=date(2005, 1, 1), fin=date(2026, 6, 13))
    trous = long[~((long["date"].dt.month == 6) & long["date"].dt.year.isin(range(2005, 2022)))]
    assert situation(trous) is None  # 4 années comparables autour du 13 juin, il en faut 5
