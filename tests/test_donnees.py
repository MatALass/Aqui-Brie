"""Tests du cache disque : on compte les appels au client Hub'Eau (simulé)."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from aquibrie_viz import donnees, hubeau


@pytest.fixture(autouse=True)
def cache_temporaire(tmp_path, monkeypatch):
    monkeypatch.setenv("AQUIBRIE_CACHE", str(tmp_path / "cache"))
    # Ces tests comptent les appels à Hub'Eau : ni instantané, ni mode hors ligne.
    monkeypatch.setenv("AQUIBRIE_INSTANTANE", str(tmp_path / "instantane"))
    monkeypatch.delenv("AQUIBRIE_HORS_LIGNE", raising=False)


class FauxClient:
    def __init__(self, monkeypatch):
        self.appels = []
        self.panne = False
        monkeypatch.setattr(hubeau, "chronique_piezo", self.chronique)
        monkeypatch.setattr(hubeau, "analyses_annee", self.analyses)
        monkeypatch.setattr(hubeau, "stations_piezo", self.stations)

    def _verifier(self):
        if self.panne:
            raise hubeau.HubEauError("délai dépassé")

    def chronique(self, code, debut, fin):
        self._verifier()
        self.appels.append(("chronique", debut, fin))
        dates = pd.date_range(debut, min(fin, date(2026, 11, 5)), freq="D")
        return pd.DataFrame({"date": dates, "niveau": [float(i) for i in range(len(dates))]})

    def analyses(self, code_param, annee, masse_eau, departements):
        self._verifier()
        self.appels.append(("analyses", annee))
        return pd.DataFrame({"bss_id": ["S1"], "commune": ["Melun"], "lon": [2.6], "lat": [48.5],
                             "date": [pd.Timestamp(annee, 6, 1)], "resultat": [1.0], "quantifie": [True],
                             "lq": [0.5]})

    def stations(self, departements, masse_eau):
        self._verifier()
        self.appels.append(("stations",))
        return pd.DataFrame({"code_bss": ["A"], "nom": ["Melun (A)"]})


def vieillir(heures: float, monkeypatch):
    """Simule le passage du temps pour le cache."""
    plus_tard = datetime.now() + timedelta(hours=heures)
    monkeypatch.setattr(donnees, "_maintenant", lambda: plus_tard)


def test_chronique_telechargee_une_seule_fois(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.chronique("X", date(2010, 1, 1))
    df = donnees.chronique("X", date(2015, 1, 1), date(2016, 12, 31))
    assert len(client.appels) == 1
    assert df["date"].min() == pd.Timestamp("2015-01-01") and df["date"].max() == pd.Timestamp("2016-12-31")


def test_seul_l_historique_manquant_est_telecharge(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.chronique("X", date(2010, 1, 1))
    donnees.chronique("X", date(1990, 1, 1))
    assert client.appels[1] == ("chronique", date(1990, 1, 1), date(2009, 12, 31))
    assert donnees.chronique("X", date(1990, 1, 1))["date"].min() == pd.Timestamp("1990-01-01")


def test_mise_a_jour_incrementale_apres_six_heures(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.chronique("X", date(2010, 1, 1))
    vieillir(7, monkeypatch)
    donnees.chronique("X", date(2010, 1, 1))
    _, debut, _ = client.appels[1]
    assert debut > date(2026, 9, 1)  # seulement les dernières semaines, pas tout l'historique


def test_cache_utilise_si_hub_eau_en_panne(monkeypatch):
    client = FauxClient(monkeypatch)
    attendu = donnees.chronique("X", date(2010, 1, 1))
    vieillir(48, monkeypatch)
    client.panne = True
    pd.testing.assert_frame_equal(donnees.chronique("X", date(2010, 1, 1)), attendu)
    assert donnees.stations is not None


def test_panne_sans_cache_leve_une_erreur(monkeypatch):
    client = FauxClient(monkeypatch)
    client.panne = True
    with pytest.raises(hubeau.HubEauError):
        donnees.chronique("X", date(2010, 1, 1))


def test_changer_de_periode_ne_retelecharge_que_les_annees_manquantes(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.analyses("1340", date(2020, 1, 1), date(2026, 11, 5), "HG103")
    assert len(client.appels) == 7
    df = donnees.analyses("1340", date(2016, 1, 1), date(2026, 11, 5), "HG103")
    assert sorted(a for _, a in client.appels[7:]) == [2016, 2017, 2018, 2019]
    assert df["date"].dt.year.nunique() == 11


def test_annees_anciennes_figees_annee_en_cours_rafraichie(monkeypatch):
    client = FauxClient(monkeypatch)
    annee = date.today().year
    donnees.analyses("1340", date(annee - 3, 1, 1), date.today(), "HG103")
    vieillir(30, monkeypatch)  # 30 heures plus tard
    donnees.analyses("1340", date(annee - 3, 1, 1), date.today(), "HG103")
    assert sorted(a for _, a in client.appels[4:]) == [annee - 1, annee]


def test_stations_rafraichies_une_fois_par_jour(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.stations(["77"], "HG103")
    donnees.stations(["77"], "HG103")
    vieillir(25, monkeypatch)
    donnees.stations(["77"], "HG103")
    assert client.appels.count(("stations",)) == 2


def test_etat_et_peremption(monkeypatch):
    client = FauxClient(monkeypatch)
    donnees.chronique("X", date(2010, 1, 1))
    assert donnees.etat().fichiers == 1
    donnees.marquer_perime()
    donnees.chronique("X", date(2010, 1, 1))
    assert len(client.appels) == 2


def test_instantane_utilise_quand_hub_eau_est_en_panne(tmp_path, monkeypatch):
    client = FauxClient(monkeypatch)
    monkeypatch.setenv("AQUIBRIE_CACHE", str(tmp_path / "instantane"))
    donnees.chronique("A", date(2020, 1, 1))  # construit l'« instantané »
    monkeypatch.setenv("AQUIBRIE_CACHE", str(tmp_path / "cache_vide"))
    client.panne = True
    df = donnees.chronique("A", date(2020, 1, 1))
    assert not df.empty and df["date"].max() == pd.Timestamp("2026-11-05")


def test_mode_hors_ligne_n_appelle_jamais_hub_eau(monkeypatch):
    client = FauxClient(monkeypatch)
    monkeypatch.setenv("AQUIBRIE_HORS_LIGNE", "1")
    with pytest.raises(hubeau.HubEauError, match="hors ligne"):
        donnees.chronique("A", date(2020, 1, 1))
    assert client.appels == []
