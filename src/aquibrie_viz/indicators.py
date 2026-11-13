"""Indicateurs calculés à partir des chroniques et des analyses.

Fonctions pures (DataFrame en entrée, DataFrame ou dict en sortie), testées
indépendamment des figures et de l'interface.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LACUNE_MAX_JOURS = 15

# Position du niveau actuel par rapport aux mêmes dates des années précédentes.
# Classes inspirées de celles des bulletins de situation des nappes (BRGM),
# calculées ici simplement en percentiles.
CLASSES_SITUATION = [
    (10, "Très bas"),
    (30, "Bas"),
    (70, "Proche de la normale"),
    (90, "Haut"),
    (101, "Très haut"),
]
ORDRE_CLASSES = [c for _, c in CLASSES_SITUATION]


# ---------------------------------------------------------------------------
# Séries temporelles
# ---------------------------------------------------------------------------

def serie_journaliere(df: pd.DataFrame, lacune_max: int = LACUNE_MAX_JOURS) -> pd.DataFrame:
    """Série au pas journalier. Les trous courts (≤ lacune_max jours) sont
    interpolés ; les trous longs restent vides pour ne pas inventer de données."""
    if df.empty:
        return pd.DataFrame(columns=["date", "niveau"])
    s = df.set_index("date")["niveau"].resample("D").mean()
    vide = s.isna()
    longueur_trou = vide.groupby((~vide).cumsum()).transform("sum")
    interpole = s.interpolate(limit_area="inside")
    s = s.where(~vide | (longueur_trou > lacune_max), interpole)
    return s.rename("niveau").reset_index()


def statut_niveau(niveau: float, seuils: dict[str, float]) -> str | None:
    """Seuil le plus grave franchi (niveau sous le seuil), ou None."""
    franchis = [(nom, val) for nom, val in seuils.items() if niveau < val]
    if not franchis:
        return None
    return min(franchis, key=lambda kv: kv[1])[0]


def tendance_cm_par_jour(df: pd.DataFrame, jours: int = 30) -> float | None:
    """Pente du niveau sur les N derniers jours, en cm/jour."""
    recent = df.dropna(subset=["niveau"])
    recent = recent[recent["date"] >= recent["date"].max() - pd.Timedelta(days=jours)]
    if len(recent) < 5:
        return None
    x = (recent["date"] - recent["date"].min()).dt.total_seconds() / 86400
    return float(np.polyfit(x, recent["niveau"], 1)[0] * 100)


@dataclass
class Projection:
    seuil: str
    valeur_seuil: float
    date: pd.Timestamp
    jours: int
    pente_cm_j: float


def projection_seuil(df: pd.DataFrame, seuils: dict[str, float], horizon_jours: int = 365) -> Projection | None:
    """Si la baisse des 30 derniers jours se poursuit, quand le prochain seuil
    sera-t-il atteint ? None si le niveau monte ou si l'échéance dépasse l'horizon."""
    pente = tendance_cm_par_jour(df)
    if pente is None or pente >= 0 or df.empty:
        return None
    dernier = df.dropna(subset=["niveau"]).iloc[-1]
    en_dessous = {n: v for n, v in seuils.items() if v < dernier["niveau"]}
    if not en_dessous:
        return None
    nom, valeur = max(en_dessous.items(), key=lambda kv: kv[1])
    jours = int(np.ceil((dernier["niveau"] - valeur) / (-pente / 100)))
    if jours > horizon_jours:
        return None
    return Projection(nom, valeur, dernier["date"] + pd.Timedelta(days=jours), jours, pente)


# ---------------------------------------------------------------------------
# Situation par rapport aux années précédentes
# ---------------------------------------------------------------------------

def classe_percentile(p: float | None) -> str | None:
    if p is None or np.isnan(p):
        return None
    return next(classe for borne, classe in CLASSES_SITUATION if p < borne)


def situation(df: pd.DataFrame, fenetre: int = 3, min_annees: int = 5) -> dict | None:
    """Niveau actuel comparé au même jour calendaire (± fenetre jours) des années précédentes.

    La fenêtre reste courte : une moyenne sur plusieurs semaines biaiserait la
    comparaison près des minimums et maximums saisonniers.

    Renvoie valeur, date, percentile (0–100), classe, médiane et nombre d'années,
    ou None si l'historique est trop court.
    """
    d = serie_journaliere(df).dropna()
    if d.empty:
        return None
    dernier = d.iloc[-1]
    jour_ref = dernier["date"].day_of_year
    d = d.assign(annee=d["date"].dt.year, jour=d["date"].dt.day_of_year)
    ecart = (d["jour"] - jour_ref).abs()
    ecart = np.minimum(ecart, 365 - ecart)  # fenêtre circulaire autour du 1er janvier
    passe = d[(ecart <= fenetre) & (d["annee"] < dernier["date"].year)]
    par_annee = passe.groupby("annee")["niveau"].mean()
    if len(par_annee) < min_annees:
        return None
    percentile = float((par_annee < dernier["niveau"]).mean() * 100)
    return {
        "valeur": float(dernier["niveau"]),
        "date": dernier["date"],
        "percentile": percentile,
        "classe": classe_percentile(percentile),
        "mediane": float(par_annee.median()),
        "n_annees": int(len(par_annee)),
    }


def situation_territoire(stations: pd.DataFrame, chroniques: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Situation de chaque station : une ligne par station ayant assez d'historique."""
    lignes = []
    for _, st in stations.iterrows():
        chro = chroniques.get(st["code_bss"])
        if chro is None or chro.empty:
            continue
        sit = situation(chro)
        if sit is None:
            continue
        lignes.append({**st[["code_bss", "nom", "commune", "lon", "lat"]].to_dict(), **sit,
                       "tendance": tendance_cm_par_jour(chro)})
    colonnes = ["code_bss", "nom", "commune", "lon", "lat", "valeur", "date", "percentile",
                "classe", "mediane", "n_annees", "tendance"]
    return pd.DataFrame(lignes, columns=colonnes).sort_values("percentile").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Recharge et anomalies
# ---------------------------------------------------------------------------

def recharge_annuelle(df: pd.DataFrame, mois_min: int = 8) -> pd.DataFrame:
    """Recharge par année hydrologique (1er septembre → 31 août).

    Recharge = plus forte remontée de l'année : écart maximal entre un niveau et le
    plus bas atteint avant lui dans l'année. Une année où la nappe ne fait que
    baisser a donc une recharge nulle, ce qui est une information en soi.

    Calculée sur les mesures brutes, quelle que soit leur fréquence : une année est
    retenue si au moins `mois_min` mois sur 12 ont une mesure. L'année en cours
    n'apparaît qu'à partir du 1er janvier (avant, il est trop tôt pour en parler).
    """
    colonnes = ["annee_hydro", "debut", "bas", "haut", "recharge", "en_cours"]
    d = df.dropna(subset=["niveau"]).sort_values("date")
    if d.empty:
        return pd.DataFrame(columns=colonnes)
    d = d.assign(annee=np.where(d["date"].dt.month >= 9, d["date"].dt.year, d["date"].dt.year - 1))
    derniere_date = d["date"].max()
    lignes = []
    for annee, g in d.groupby("annee"):
        fin_annee = pd.Timestamp(year=annee + 1, month=8, day=31)
        en_cours = derniere_date < fin_annee
        mois_couverts = g["date"].dt.to_period("M").nunique()
        # Sans mesure en début d'année (sept.–nov.), on rate les basses eaux : recharge sous-estimée.
        debut_mesure = g["date"].min() <= pd.Timestamp(year=annee, month=11, day=30)
        if not debut_mesure:
            continue
        if en_cours:
            if derniere_date < pd.Timestamp(year=annee + 1, month=1, day=1) or mois_couverts < 3:
                continue
        elif mois_couverts < mois_min:
            continue
        remontee = g["niveau"] - g["niveau"].cummin()
        i_haut = remontee.idxmax()
        lignes.append({
            "annee_hydro": f"{annee}-{str(annee + 1)[2:]}",
            "debut": annee,
            "bas": float(g.loc[:i_haut, "niveau"].min()),
            "haut": float(g.loc[i_haut, "niveau"]),
            "recharge": float(remontee.loc[i_haut]),
            "en_cours": bool(en_cours),
        })
    return pd.DataFrame(lignes, columns=colonnes)


def anomalies_mensuelles(df: pd.DataFrame, min_annees: int = 5) -> pd.DataFrame:
    """Écart (m) du niveau moyen de chaque mois à la médiane de ce mois sur toutes
    les années. Tableau années × mois, vide si l'historique est trop court."""
    d = serie_journaliere(df).dropna()
    if d.empty:
        return pd.DataFrame()
    mensuel = (d.assign(annee=d["date"].dt.year, mois=d["date"].dt.month)
               .groupby(["annee", "mois"])["niveau"].mean().unstack("mois"))
    if mensuel.notna().sum().max() < min_annees:
        return pd.DataFrame()
    return (mensuel - mensuel.median()).sort_index()


# ---------------------------------------------------------------------------
# Qualité de l'eau
# ---------------------------------------------------------------------------

def synthese_stations_qualite(analyses: pd.DataFrame, norme: float) -> pd.DataFrame:
    """Une ligne par station qualité : nombre d'analyses, maximum, dernière valeur,
    dépassements de la norme, et une catégorie pour la carte."""
    if analyses.empty:
        return pd.DataFrame(columns=["bss_id", "commune", "lon", "lat", "n", "n_quanti", "maximum",
                                     "derniere", "date_derniere", "n_depassements", "categorie"])
    a = analyses.assign(depasse=analyses["quantifie"] & (analyses["resultat"] > norme))
    g = a.sort_values("date").groupby("bss_id")
    synth = pd.DataFrame({
        "commune": g["commune"].last(),
        "lon": g["lon"].last(),
        "lat": g["lat"].last(),
        "n": g.size(),
        "n_quanti": g["quantifie"].sum(),
        "maximum": a[a["quantifie"]].groupby("bss_id")["resultat"].max(),
        "derniere": g["resultat"].last(),
        "date_derniere": g["date"].last(),
        "n_depassements": g["depasse"].sum(),
    }).reset_index()
    synth["categorie"] = np.select(
        [synth["n_depassements"] > 0, synth["n_quanti"] > 0],
        ["Dépassement de la norme", "Détecté, sous la norme"],
        default="Jamais quantifié",
    )
    return synth.sort_values(["n_depassements", "maximum"], ascending=False).reset_index(drop=True)


def synthese_annuelle_qualite(analyses: pd.DataFrame, norme: float) -> pd.DataFrame:
    """Par année : nombre d'analyses et de stations, part quantifiée, part au-dessus de la norme."""
    if analyses.empty:
        return pd.DataFrame(columns=["annee", "n_analyses", "n_stations", "pct_quanti", "pct_depassement"])
    a = analyses.assign(annee=analyses["date"].dt.year,
                        depasse=analyses["quantifie"] & (analyses["resultat"] > norme))
    g = a.groupby("annee")
    return pd.DataFrame({
        "n_analyses": g.size(),
        "n_stations": g["bss_id"].nunique(),
        "pct_quanti": g["quantifie"].mean() * 100,
        "pct_depassement": g["depasse"].mean() * 100,
    }).reset_index()
