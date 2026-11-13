"""Données synthétiques pour faire tourner la démo sans réseau.

Elles imitent la forme des vraies données (cycle saisonnier, tendance, bruit,
valeurs non quantifiées) mais ne représentent aucune station réelle.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

STATIONS = pd.DataFrame(
    {
        "code_bss": ["DEMO-001", "DEMO-002", "DEMO-003", "DEMO-004"],
        "bss_id": ["DEMO-001", "DEMO-002", "DEMO-003", "DEMO-004"],
        "nom": [
            "Station fictive A — secteur Melun",
            "Station fictive B — secteur Nangis",
            "Station fictive C — secteur Provins",
            "Station fictive D — secteur Yerres",
        ],
        "lon": [2.66, 3.01, 3.30, 2.62],
        "lat": [48.54, 48.55, 48.56, 48.67],
        "date_fin_mesure": pd.Timestamp.today().normalize(),
    }
)


def _rng(seed_text: str) -> np.random.Generator:
    return np.random.default_rng(sum(ord(c) for c in seed_text))


def stations_piezo(departements: list[str] | None = None, masse_eau: str | None = None) -> pd.DataFrame:
    return STATIONS.copy()


def chronique_piezo(code_bss: str, debut: date, fin: date) -> pd.DataFrame:
    rng = _rng(code_bss)
    dates = pd.date_range(debut, fin, freq="D")
    t = np.arange(len(dates))
    doy = dates.dayofyear.to_numpy()
    base = 60 + rng.uniform(-5, 5)
    saison = 1.6 * np.cos(2 * np.pi * (doy - 75) / 365.25)  # hautes eaux au printemps
    pluriannuel = 2.2 * np.sin(2 * np.pi * t / (365.25 * 6.5))  # cycles longs
    bruit = np.cumsum(rng.normal(0, 0.015, len(dates)))
    niveau = base + saison + pluriannuel + bruit - bruit.mean()
    return pd.DataFrame({"date": dates, "niveau": niveau.round(3)})


def analyses_qualite(bss_id: str, code_param: str, debut: date) -> pd.DataFrame:
    rng = _rng(bss_id + code_param)
    dates = pd.date_range(debut, date.today(), freq="2MS")
    n = len(dates)
    if code_param == "1340":  # nitrates, mg/L
        valeurs = 38 + 6 * np.sin(np.arange(n) / 4) + rng.normal(0, 4, n) + np.linspace(0, 6, n)
        lq = np.full(n, 0.5)
        quantifie = np.ones(n, dtype=bool)
    else:  # pesticide, µg/L
        valeurs = np.clip(rng.lognormal(-3.3, 0.8, n), 0, None)
        lq = np.full(n, 0.02)
        quantifie = valeurs > lq
        valeurs = np.where(quantifie, valeurs, np.nan)
    return pd.DataFrame(
        {"date": dates, "resultat": np.round(valeurs, 3), "quantifie": quantifie, "lq": lq}
    )
