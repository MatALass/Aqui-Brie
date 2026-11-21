"""Indicateurs de l'Info Piézo d'AQUI'Brie, recalculés à partir de Hub'Eau.

Le bulletin d'AQUI'Brie situe chaque piézomètre « à la manière d'une jauge comprise entre
0 et 100 » : la position du niveau entre le minimum et le maximum mesurés depuis 2003.
Il classe ensuite les piézomètres en quatre tranches (très faible sous 25, faible sous 50,
satisfaisant de 50 à 75, très satisfaisant au-dessus de 75).

Ce module ajoute une projection par analogie : on applique au niveau du jour la baisse (ou la
hausse) observée à la même date chaque année passée. Cela répond à la question que pose le
bulletin (« le seuil de vigilance sera-t-il franchi cet hiver ? ») avec une fourchette, et
non une simple droite prolongée.

Fonctions pures, sans Streamlit ni Plotly, testées dans tests/test_bulletin.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ANNEE_REFERENCE = 2003  # début du réseau départemental : le bulletin compare à « depuis 2003 »

# (borne haute exclue, libellé), du plus bas au plus haut
TRANCHES_REMPLISSAGE = [
    (25, "Très faible"),
    (50, "Faible"),
    (75, "Satisfaisant"),
    (float("inf"), "Très satisfaisant"),
]
ORDRE_TRANCHES = [nom for _, nom in TRANCHES_REMPLISSAGE]

# Une mesure plus ancienne que cela n'est pas « la situation du jour ».
FRAICHEUR_MAX_JOURS = 15


def classe_remplissage(taux: float | None) -> str | None:
    """Tranche du bulletin. Une valeur pile sur une borne va dans la tranche du dessus
    (50 est « satisfaisant ») : le bulletin compte « au-dessus de 50 % » les satisfaisants."""
    if taux is None or pd.isna(taux):
        return None
    for borne, nom in TRANCHES_REMPLISSAGE:
        if taux < borne:
            return nom
    return ORDRE_TRANCHES[-1]


def niveau_a_date(df: pd.DataFrame, jour: pd.Timestamp,
                  fraicheur_max: int = FRAICHEUR_MAX_JOURS) -> tuple[float, pd.Timestamp] | None:
    """Dernière mesure à la date voulue ou avant, si elle a moins de fraicheur_max jours."""
    avant = df[(df["date"] <= jour)].dropna(subset=["niveau"])
    if avant.empty:
        return None
    derniere = avant.iloc[-1]
    if (jour - derniere["date"]).days > fraicheur_max:
        return None
    return float(derniere["niveau"]), derniere["date"]


def taux_remplissage(df: pd.DataFrame, jour: pd.Timestamp, depuis: int = ANNEE_REFERENCE,
                     min_annees: int = 5) -> float | None:
    """Position du niveau du jour entre le minimum et le maximum mesurés depuis `depuis`
    jusqu'au jour inclus, de 0 (minimum historique) à 100 (maximum historique).

    None si la mesure du jour manque, si l'historique couvre moins de min_annees ans, ou si
    le niveau n'a jamais varié (min = max).
    """
    mesure = niveau_a_date(df, jour)
    if mesure is None:
        return None
    periode = df[(df["date"] >= pd.Timestamp(depuis, 1, 1)) & (df["date"] <= jour)]["niveau"].dropna()
    if periode.empty:
        return None
    couverture = (df.loc[periode.index, "date"].max() - df.loc[periode.index, "date"].min()).days / 365.25
    if couverture < min_annees:
        return None
    bas, haut = float(periode.min()), float(periode.max())
    if haut - bas < 1e-9:
        return None
    return round((mesure[0] - bas) / (haut - bas) * 100, 1)


def synthese_remplissage(stations: pd.DataFrame, chroniques: dict[str, pd.DataFrame],
                         jour: pd.Timestamp) -> pd.DataFrame:
    """Une ligne par piézomètre : taux de remplissage, tranche, date de la mesure utilisée.

    stations : colonnes code_bss, commune, lon, lat. Les piézomètres sans chronique ou sans
    mesure récente restent dans le tableau avec un taux vide : le lecteur voit qu'ils manquent.
    """
    lignes = []
    for st in stations.itertuples(index=False):
        df = chroniques.get(st.code_bss)
        mesure = niveau_a_date(df, jour) if df is not None and not df.empty else None
        taux = taux_remplissage(df, jour) if mesure is not None else None
        lignes.append({
            "code_bss": st.code_bss,
            "commune": st.commune,
            "lon": st.lon,
            "lat": st.lat,
            "niveau": mesure[0] if mesure else np.nan,
            "date_mesure": mesure[1] if mesure else pd.NaT,
            "taux": taux if taux is not None else np.nan,
            "tranche": classe_remplissage(taux),
        })
    out = pd.DataFrame(lignes, columns=["code_bss", "commune", "lon", "lat", "niveau", "date_mesure",
                                        "taux", "tranche"])
    return out.sort_values("taux", na_position="last").reset_index(drop=True)


def compte_tranches(synthese: pd.DataFrame) -> dict[str, int]:
    """Nombre de piézomètres par tranche, et au-dessus / au-dessous de 50."""
    mesures = synthese.dropna(subset=["taux"])
    comptes = {nom: int((mesures["tranche"] == nom).sum()) for nom in ORDRE_TRANCHES}
    comptes["au-dessus de 50"] = int((mesures["taux"] >= 50).sum())
    comptes["sous 50"] = int((mesures["taux"] < 50).sum())
    comptes["sans mesure"] = int(synthese["taux"].isna().sum())
    return comptes


def variation(df: pd.DataFrame, debut: pd.Timestamp, fin: pd.Timestamp) -> float | None:
    """Évolution du niveau entre deux dates (m), positive si la nappe monte."""
    a, b = niveau_a_date(df, debut), niveau_a_date(df, fin)
    if a is None or b is None:
        return None
    return round(b[0] - a[0], 2)


# ---------------------------------------------------------------------------
# Projection par analogie avec les années passées
# ---------------------------------------------------------------------------

@dataclass
class Projection:
    trajectoires: pd.DataFrame  # index : date future ; une colonne par année passée (niveaux projetés)
    depart: pd.Timestamp
    niveau_depart: float
    seuil: float
    franchissements: pd.Series  # année passée -> date de franchissement projetée (NaT si jamais)

    @property
    def nb_annees(self) -> int:
        return self.trajectoires.shape[1]

    @property
    def part_franchissement(self) -> float:
        """Part des années passées qui, rejouées depuis aujourd'hui, passent sous le seuil."""
        return float(self.franchissements.notna().mean()) if len(self.franchissements) else 0.0

    def quantile(self, q: float) -> pd.Series:
        return self.trajectoires.quantile(q, axis=1)


def projection_analogues(df: pd.DataFrame, jour: pd.Timestamp, seuil: float, horizon: int = 270,
                         depuis: int = 1990, lacune_max: int = 15, couverture_min: float = 0.9) -> Projection | None:
    """Rejoue, à partir du niveau du jour, l'évolution observée chaque année passée à la même date.

    Pour chaque année A (depuis `depuis`, avant l'année de `jour`), la trajectoire est
    niveau_du_jour + (niveau(A, date + t) - niveau(A, date)), pour t de 0 à horizon jours.
    Une année est écartée si plus de 10 % de sa période manque (trous de plus de lacune_max
    jours non interpolés). None s'il n'y a pas de mesure du jour ou moins de 5 années utilisables.
    """
    mesure = niveau_a_date(df, jour)
    if mesure is None:
        return None
    serie = df.dropna(subset=["niveau"]).set_index("date")["niveau"].resample("D").mean()
    serie = serie.interpolate(limit=lacune_max, limit_area="inside")
    futur = pd.date_range(jour, periods=horizon + 1, freq="D")
    trajectoires = {}
    for annee in range(depuis, jour.year):
        try:
            t0 = jour.replace(year=annee)
        except ValueError:  # 29 février
            t0 = jour.replace(year=annee, day=28)
        morceau = serie.reindex(pd.date_range(t0, periods=horizon + 1, freq="D"))
        if pd.isna(morceau.iloc[0]) or morceau.isna().mean() > 1 - couverture_min:
            continue
        trajectoires[annee] = mesure[0] + (morceau.to_numpy() - morceau.iloc[0])
    if len(trajectoires) < 5:
        return None
    traj = pd.DataFrame(trajectoires, index=futur)
    franchit = {}
    for annee, valeurs in traj.items():
        sous = valeurs[valeurs < seuil]
        franchit[annee] = sous.index[0] if not sous.empty else pd.NaT
    return Projection(traj, jour, mesure[0], seuil, pd.Series(franchit, dtype="datetime64[ns]"))


def date_situation_commune(chroniques: dict[str, pd.DataFrame], part: float = 0.8) -> pd.Timestamp | None:
    """Date la plus récente à laquelle au moins `part` des piézomètres ont une mesure.

    Hub'Eau ne reçoit pas les réseaux au même rythme (le réseau départemental arrive par lots
    de plusieurs mois) : situer la nappe « au dernier relevé » laisserait la moitié des
    jauges vides. On prend donc la date que couvre la grande majorité des piézomètres.
    """
    dernieres = sorted(df["date"].max() for df in chroniques.values() if df is not None and not df.empty)
    if not dernieres:
        return None
    rang = int(np.floor((1 - part) * len(dernieres)))
    return pd.Timestamp(dernieres[min(rang, len(dernieres) - 1)]).normalize()
