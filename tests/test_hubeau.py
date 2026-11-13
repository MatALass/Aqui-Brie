"""Tests du client Hub'Eau sans réseau : les réponses de l'API sont simulées."""
from datetime import date

import pandas as pd
import pytest

from aquibrie_viz import hubeau


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def test_pagination_et_normalisation_des_stations(monkeypatch):
    pages = [
        FakeResponse({"data": [{"code_bss": "01857X0004/S1", "bss_id": "BSS000AAAA",
                                "nom_commune": "BEAUCHERY-SAINT-MARTIN", "x": 3.43, "y": 48.60,
                                "date_fin_mesure": "2026-11-01"}],
                      "next": "https://hubeau.eaufrance.fr/page2"}, status=206),
        FakeResponse({"data": [{"code_bss": "02212X0012/P1", "nom_commune": "MELUN",
                                "geometry": {"type": "Point", "coordinates": [2.66, 48.54]}}],
                      "next": None}),
    ]
    appels = []

    def fake_get(url, params=None, timeout=None):
        appels.append((url, params))
        return pages[len(appels) - 1]

    monkeypatch.setattr(hubeau.requests, "get", fake_get)
    stations = hubeau.stations_piezo(masse_eau="HG103")

    assert appels[0][1]["codes_masse_eau_edl"] == "HG103"
    assert appels[1] == ("https://hubeau.eaufrance.fr/page2", None)
    assert list(stations["nom"]) == ["Beauchery-Saint-Martin (01857X0004/S1)", "Melun (02212X0012/P1)"]
    assert stations.loc[1, "lon"] == pytest.approx(2.66)


def test_chronique_normalisee(monkeypatch):
    payload = {"data": [{"date_mesure": "2026-01-02", "niveau_nappe_eau": 126.1},
                        {"date_mesure": "2026-01-01", "niveau_nappe_eau": 126.3}], "next": None}
    monkeypatch.setattr(hubeau.requests, "get", lambda *a, **k: FakeResponse(payload))
    df = hubeau.chronique_piezo("X", date(2026, 1, 1), date(2026, 1, 31))
    assert list(df.columns) == ["date", "niveau"]
    assert df["niveau"].tolist() == [126.3, 126.1]  # triée par date


def test_erreur_http(monkeypatch):
    monkeypatch.setattr(hubeau.time, "sleep", lambda s: None)
    monkeypatch.setattr(hubeau.requests, "get", lambda *a, **k: FakeResponse({}, status=500))
    with pytest.raises(hubeau.HubEauError):
        hubeau.stations_piezo(departements=["77"])


def test_analyses_une_requete_par_annee_et_normalisation(monkeypatch):
    appels = []

    def fake_get(url, params=None, timeout=None):
        appels.append(params)
        return FakeResponse({"data": [{"bss_id": "BSS001", "nom_commune_actuel": "MELUN", "longitude": 2.66,
                                       "latitude": 48.54, "date_debut_prelevement": params["date_debut_prelevement"],
                                       "resultat": 0.2, "code_remarque_analyse": 1,
                                       "limite_quantification": 0.02},
                                      {"bss_id": "BSS001", "nom_commune_actuel": "MELUN", "longitude": 2.66,
                                       "latitude": 48.54, "date_debut_prelevement": params["date_debut_prelevement"],
                                       "resultat": 0.02, "code_remarque_analyse": 10}],
                             "next": None})

    monkeypatch.setattr(hubeau.requests, "get", fake_get)
    df = hubeau.analyses_territoire("1107", date(2023, 3, 1), date(2025, 6, 30), masse_eau="HG103")
    bornes = sorted((p["date_debut_prelevement"], p["date_fin_prelevement"]) for p in appels)
    assert bornes == [("2023-03-01", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-06-30")]
    assert all(p["code_masse_eau_edl"] == "HG103" for p in appels)
    assert df["quantifie"].tolist() == [True, False] * 3
    assert df["commune"].iloc[0] == "Melun" and df["lon"].iloc[0] == pytest.approx(2.66)


def test_analyses_tronquees_levent_une_erreur(monkeypatch):
    page_pleine = FakeResponse({"data": [{}], "next": "https://hubeau.eaufrance.fr/suite"}, status=206)
    monkeypatch.setattr(hubeau.requests, "get", lambda *a, **k: page_pleine)
    with pytest.raises(hubeau.HubEauError, match="20 000"):
        hubeau.analyses_territoire("1340", date(2024, 1, 1), date(2024, 12, 31), masse_eau="HG103")


def test_reessaie_apres_un_delai_depasse(monkeypatch):
    monkeypatch.setattr(hubeau.time, "sleep", lambda s: None)
    reponses = iter([hubeau.requests.Timeout("lent"), FakeResponse({}, status=503),
                     FakeResponse({"data": [{"date_mesure": "2026-01-01", "niveau_nappe_eau": 126.0}],
                                   "next": None})])

    def fake_get(*a, **k):
        r = next(reponses)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(hubeau.requests, "get", fake_get)
    df = hubeau.chronique_piezo("X", date(2026, 1, 1), date(2026, 1, 31))
    assert df["niveau"].tolist() == [126.0]


def test_une_station_en_echec_n_empeche_pas_les_autres(monkeypatch):
    def fake_chronique(code, debut, fin):
        if code == "KO":
            raise hubeau.HubEauError("délai dépassé")
        return pd.DataFrame({"date": [pd.Timestamp("2026-01-01")], "niveau": [1.0]})

    monkeypatch.setattr(hubeau, "chronique_piezo", fake_chronique)
    chroniques, echecs = hubeau.chroniques_stations(["A", "KO", "B"], date(2020, 1, 1), date(2026, 1, 1))
    assert set(chroniques) == {"A", "B"} and echecs == ["KO"]


def test_url_affichee():
    url = hubeau.api_url(hubeau.ENDPOINT_STATIONS, {"codes_masse_eau_edl": "HG103"})
    assert url == "https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/stations?codes_masse_eau_edl=HG103"


def test_dates_avec_ou_sans_fuseau_horaire():
    """Hub'Eau mélange « 2024-05-02 » et « 2024-05-02T10:00:00Z » : tout doit devenir une date simple."""
    dates = hubeau._dates(["2024-05-02", "2024-05-02T10:00:00Z", "2024-05-03T00:00:00+02:00", None])
    assert dates.dt.tz is None
    assert dates.tolist()[:3] == [pd.Timestamp("2024-05-02"), pd.Timestamp("2024-05-02 10:00"),
                                  pd.Timestamp("2024-05-02 22:00")]
    assert pd.isna(dates.iloc[3])
