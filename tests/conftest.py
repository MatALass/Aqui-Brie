"""Données synthétiques pour les tests (aucun appel réseau)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest


def fabriquer_chronique(debut=date(2010, 1, 1), fin=date(2026, 10, 31), base=130.0, seed=1) -> pd.DataFrame:
    """Chronique au cycle saisonnier marqué : basses eaux en automne, hautes eaux au printemps."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(debut, fin, freq="D")
    doy = dates.day_of_year.to_numpy()
    # Décalage propre à chaque année (années sèches / humides), nul pour la dernière année.
    annees = dates.year.to_numpy()
    decalages = {a: rng.normal(0, 1.0) for a in np.unique(annees)}
    decalages[annees.max()] = 0.0
    decalage = np.array([decalages[a] for a in annees])
    niveau = (base + 3 * np.cos(2 * np.pi * (doy - 100) / 365.25) + decalage
              + rng.normal(0, 0.02, len(dates)))
    return pd.DataFrame({"date": dates, "niveau": niveau})


@pytest.fixture
def chronique() -> pd.DataFrame:
    return fabriquer_chronique()


@pytest.fixture
def stations() -> pd.DataFrame:
    return pd.DataFrame({
        "code_bss": ["A", "B", "C"],
        "nom": ["Melun (A)", "Nangis (B)", "Provins (C)"],
        "commune": ["Melun", "Nangis", "Provins"],
        "lon": [2.66, 3.01, 3.30],
        "lat": [48.54, 48.55, 48.56],
        "date_fin_mesure": pd.Timestamp("2026-10-31"),
    })


@pytest.fixture
def analyses() -> pd.DataFrame:
    dates = pd.date_range("2018-01-01", "2025-12-01", freq="3MS")
    lignes = []
    for station, niveau in [("S1", 0.25), ("S2", 0.05), ("S3", 0.0)]:
        for d in dates:
            quantifie = niveau > 0
            lignes.append({"bss_id": station, "commune": station, "lon": 2.8, "lat": 48.6, "date": d,
                           "resultat": niveau if quantifie else np.nan, "quantifie": quantifie, "lq": 0.02})
    return pd.DataFrame(lignes)
