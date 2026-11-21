import numpy as np
import pandas as pd
import pytest

from aquibrie_viz import indicators as ind

SEUILS = {"Vigilance": 10, "Alerte": 9, "Alerte renforcée": 8, "Crise": 7}


def test_statut_niveau_retient_le_seuil_le_plus_grave():
    assert ind.statut_niveau(11, SEUILS) is None
    assert ind.statut_niveau(9.5, SEUILS) == "Vigilance"
    assert ind.statut_niveau(7.5, SEUILS) == "Alerte renforcée"
    assert ind.statut_niveau(6, SEUILS) == "Crise"


def test_tendance_en_cm_par_jour():
    dates = pd.date_range("2026-01-01", periods=31, freq="D")
    df = pd.DataFrame({"date": dates, "niveau": [100 - 0.02 * i for i in range(31)]})
    assert ind.tendance_cm_par_jour(df) == pytest.approx(-2.0, abs=1e-6)


def test_serie_journaliere_comble_les_petits_trous_seulement():
    dates = list(pd.date_range("2026-01-01", "2026-01-10")) + list(pd.date_range("2026-03-01", "2026-03-05"))
    df = pd.DataFrame({"date": dates, "niveau": np.arange(len(dates), dtype=float)})
    df = df.drop(index=[3, 4])  # trou de 2 jours, à combler
    serie = ind.serie_journaliere(df, lacune_max=15).set_index("date")["niveau"]
    assert serie.loc["2026-01-04"] == pytest.approx(3.0)
    assert np.isnan(serie.loc["2026-02-01"])  # trou de 50 jours, laissé vide


def test_projection_vers_le_prochain_seuil():
    dates = pd.date_range("2026-10-01", periods=31, freq="D")
    df = pd.DataFrame({"date": dates, "niveau": [11 - 0.02 * i for i in range(31)]})  # -2 cm/j
    p = ind.projection_seuil(df, SEUILS)
    assert p.seuil == "Vigilance"
    assert p.jours == 20  # 10,40 m → 10 m à 2 cm/j


def test_pas_de_projection_si_le_niveau_monte():
    dates = pd.date_range("2026-10-01", periods=31, freq="D")
    df = pd.DataFrame({"date": dates, "niveau": [11 + 0.01 * i for i in range(31)]})
    assert ind.projection_seuil(df, SEUILS) is None


def test_situation_classe_un_niveau_exceptionnellement_bas(chronique):
    chro = chronique.copy()
    chro.loc[chro.index[-30:], "niveau"] -= 5  # chute brutale des 30 derniers jours
    sit = ind.situation(chro)
    assert sit["classe"] == "Très bas"
    assert sit["n_annees"] >= 15


def test_situation_normale(chronique):
    assert ind.situation(chronique)["classe"] in {"Proche de la normale", "Bas", "Haut"}


def test_recharge_annuelle():
    from conftest import fabriquer_chronique

    # Sans variabilité interannuelle, la recharge vaut l'amplitude du cycle : 2 × 3 m.
    chro = fabriquer_chronique(seed=1)
    chro["niveau"] = 130 + 3 * np.cos(2 * np.pi * (chro["date"].dt.day_of_year - 100) / 365.25)
    rech = ind.recharge_annuelle(chro)
    assert rech.loc[~rech["en_cours"], "recharge"].between(5.9, 6.1).all()
    # Données jusqu'au 31 octobre : l'année 2026-27 vient de commencer, elle n'est pas affichée.
    assert rech["annee_hydro"].iloc[-1] == "2025-26" and not rech["en_cours"].iloc[-1]


def test_recharge_nulle_si_la_nappe_ne_fait_que_baisser():
    dates = pd.date_range("2021-09-01", "2022-08-31", freq="MS")  # une mesure par mois
    df = pd.DataFrame({"date": dates, "niveau": np.linspace(130, 126, len(dates))})
    rech = ind.recharge_annuelle(df)
    assert len(rech) == 1 and rech["recharge"].iloc[0] == 0


def test_recharge_en_cours_apres_le_premier_janvier():
    dates = pd.date_range("2025-09-01", "2026-03-15", freq="7D")
    niveau = np.r_[np.full(15, 125.0), np.linspace(125, 127, len(dates) - 15)]
    rech = ind.recharge_annuelle(pd.DataFrame({"date": dates, "niveau": niveau}))
    assert rech["en_cours"].iloc[-1] and rech["recharge"].iloc[-1] == pytest.approx(2.0)


def test_anomalies_mensuelles(chronique):
    anom = ind.anomalies_mensuelles(chronique)
    assert anom.shape[1] == 12
    # Chaque année est décalée uniformément : son anomalie est la même tous les mois.
    assert anom.std(axis=1).max() < 0.05


def test_synthese_qualite(analyses):
    synth = ind.synthese_stations_qualite(analyses, norme=0.1)
    cat = dict(zip(synth["bss_id"], synth["categorie"], strict=False))
    assert cat == {"S1": "Dépassement de la norme", "S2": "Détecté, sous la norme", "S3": "Jamais quantifié"}
    annuel = ind.synthese_annuelle_qualite(analyses, norme=0.1)
    assert annuel["n_stations"].eq(3).all()
    assert annuel["pct_depassement"].iloc[0] == pytest.approx(100 / 3)
