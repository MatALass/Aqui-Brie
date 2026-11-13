"""Client pour l'API publique Hub'Eau (piézométrie et qualité des nappes).

Documentation officielle : https://hubeau.eaufrance.fr/page/api-piezometrie
Liste des filtres disponibles : https://inrae.github.io/hubeau/reference/get_qualite_nappes.html

Toutes les fonctions renvoient des DataFrames aux colonnes normalisées, pour que
les modèles de figures ne dépendent pas du format de l'API. Le jour où l'outil
lira le SQL Server d'AQUI'Brie, seul ce module sera remplacé.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlencode

import pandas as pd
import requests

BASE_URL = "https://hubeau.eaufrance.fr/api/v1"
ENDPOINT_STATIONS = "niveaux_nappes/stations"
ENDPOINT_CHRONIQUES = "niveaux_nappes/chroniques"
ENDPOINT_ANALYSES = "qualite_nappes/analyses"

TIMEOUT_S = 60
ATTENTES_RETRY_S = [2, 5]  # pauses avant le 2e et le 3e essai
PAGE_SIZE = 5000
# Hub'Eau refuse d'aller au-delà de 20 000 enregistrements par requête (page × taille).
MAX_RECORDS = 20000
MAX_PAGES = MAX_RECORDS // PAGE_SIZE
# Requêtes simultanées au plus, tous appels confondus : au-delà, Hub'Eau ralentit et
# finit par ne plus répondre à temps (c'était la cause des délais dépassés).
REQUETES_SIMULTANEES = 6
_limiteur = threading.BoundedSemaphore(REQUETES_SIMULTANEES)
# Une tranche de 10 ans de mesures journalières (≈ 3 650 lignes) tient en une seule page.
TRANCHE_ANS = 10
CHAMPS_CHRONIQUE = "date_mesure,niveau_nappe_eau"

STATION_COLUMNS = ["code_bss", "bss_id", "nom", "commune", "lon", "lat", "date_fin_mesure"]
ANALYSE_COLUMNS = ["bss_id", "commune", "lon", "lat", "date", "resultat", "quantifie", "lq"]


class HubEauError(RuntimeError):
    """Erreur d'accès à l'API Hub'Eau (réseau, code HTTP, format)."""



def _dates(valeurs) -> pd.Series:
    """Dates sans fuseau horaire. Hub'Eau renvoie parfois des dates avec fuseau
    (« 2024-05-02T00:00:00Z ») : on les ramène toutes en dates locales simples."""
    dates = pd.to_datetime(pd.Series(valeurs), errors="coerce", utc=True, format="ISO8601")
    return dates.dt.tz_localize(None).astype("datetime64[ns]")

def api_url(endpoint: str, params: dict) -> str:
    """URL complète d'une requête, à afficher pour la traçabilité des données."""
    return f"{BASE_URL}/{endpoint}?{urlencode(params)}"


def _requete(url: str, query: dict | None, endpoint: str) -> dict:
    """Un appel HTTP, réessayé en cas de lenteur ou d'erreur passagère du serveur.

    Hub'Eau ralentit quand il reçoit beaucoup de requêtes à la fois : un délai dépassé
    ne signifie pas que les données sont absentes, il suffit souvent de réessayer.
    """
    derniere_erreur = ""
    for tentative, attente in enumerate(ATTENTES_RETRY_S + [None]):
        try:
            with _limiteur:
                response = requests.get(url, params=query, timeout=TIMEOUT_S)
            # Hub'Eau répond 206 quand d'autres pages suivent.
            if response.status_code in (200, 206):
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504):
                raise HubEauError(f"Hub'Eau a répondu {response.status_code} sur {endpoint}")
            derniere_erreur = f"code {response.status_code}"
        except (requests.Timeout, requests.ConnectionError) as exc:
            derniere_erreur = type(exc).__name__
        if attente is None:
            break
        time.sleep(attente)
    raise HubEauError(f"API Hub'Eau injoignable sur {endpoint} après {tentative + 1} essais "
                      f"({derniere_erreur}).")


def _get_all(endpoint: str, params: dict) -> tuple[pd.DataFrame, bool]:
    """Appelle un endpoint et suit la pagination.

    Renvoie (données, complet). « complet » est faux si la limite de 20 000
    enregistrements a été atteinte : l'appelant doit alors découper sa requête.
    """
    url: str | None = f"{BASE_URL}/{endpoint}"
    query: dict | None = {**params, "size": PAGE_SIZE}
    rows: list[dict] = []
    for _ in range(MAX_PAGES):
        payload = _requete(url, query, endpoint)
        rows.extend(payload.get("data", []))
        url = payload.get("next")
        if not url:
            return pd.DataFrame(rows), True
        query = None  # l'URL « next » contient déjà les paramètres
    return pd.DataFrame(rows), False


def _first_column(df: pd.DataFrame, candidates: list[str], default=None) -> pd.Series:
    for col in candidates:
        if col in df.columns:
            return df[col]
    return pd.Series(default, index=df.index)


def _coordinates(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Longitude, latitude : champs x/y ou longitude/latitude, complétés par geometry."""
    lon = pd.to_numeric(_first_column(df, ["x", "longitude"]), errors="coerce")
    lat = pd.to_numeric(_first_column(df, ["y", "latitude"]), errors="coerce")
    if "geometry" in df.columns:
        coords = df["geometry"].map(
            lambda g: (g.get("coordinates") if isinstance(g, dict) else None) or [None, None])
        lon = lon.fillna(pd.to_numeric(coords.map(lambda c: c[0]), errors="coerce"))
        lat = lat.fillna(pd.to_numeric(coords.map(lambda c: c[1]), errors="coerce"))
    return lon, lat


# ---------------------------------------------------------------------------
# Piézométrie
# ---------------------------------------------------------------------------

def stations_params(departements: list[str] | None, masse_eau: str | None) -> list[dict]:
    """Paramètres des requêtes stations : une par masse d'eau, ou une par département."""
    if masse_eau:
        return [{"codes_masse_eau_edl": masse_eau}]
    return [{"code_departement": d} for d in (departements or [])]


def stations_piezo(departements: list[str] | None = None, masse_eau: str | None = None) -> pd.DataFrame:
    """Piézomètres d'une masse d'eau (prioritaire) ou d'une liste de départements."""
    frames = [_get_all(ENDPOINT_STATIONS, p)[0] for p in stations_params(departements, masse_eau)]
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if raw.empty:
        return pd.DataFrame(columns=STATION_COLUMNS)
    lon, lat = _coordinates(raw)
    commune = _first_column(raw, ["nom_commune", "libelle_pe"], "").fillna("").astype(str).str.title()
    stations = pd.DataFrame(
        {
            "code_bss": raw["code_bss"],
            "bss_id": _first_column(raw, ["bss_id"], ""),
            "nom": commune + " (" + raw["code_bss"].astype(str) + ")",
            "commune": commune,
            "lon": lon,
            "lat": lat,
            "date_fin_mesure": _dates(_first_column(raw, ["date_fin_mesure"])),
        }
    )
    return (stations.dropna(subset=["code_bss"]).drop_duplicates("code_bss")
            .sort_values("nom").reset_index(drop=True))


def chronique_params(code_bss: str, debut: date, fin: date) -> dict:
    return {"code_bss": code_bss, "date_debut_mesure": debut.isoformat(),
            "date_fin_mesure": fin.isoformat()}


def _tranche_chronique(code_bss: str, debut: date, fin: date) -> pd.DataFrame:
    """Une tranche de chronique. Ne demande que les deux champs utiles (réponse ~5× plus légère) ;
    si le serveur refuse le paramètre « fields », redemande sans."""
    params = chronique_params(code_bss, debut, fin)
    try:
        return _get_all(ENDPOINT_CHRONIQUES, {**params, "fields": CHAMPS_CHRONIQUE})[0]
    except HubEauError as exc:
        if " 400 " not in f" {exc} ":
            raise
        return _get_all(ENDPOINT_CHRONIQUES, params)[0]


def chronique_piezo(code_bss: str, debut: date, fin: date) -> pd.DataFrame:
    """Chronique journalière d'un piézomètre. Colonnes : date, niveau (m NGF).

    Découpée en tranches de 10 ans (une page chacune), téléchargées en parallèle.
    """
    tranches = []
    annee = debut.year
    while annee <= fin.year:
        tranches.append((max(debut, date(annee, 1, 1)), min(fin, date(annee + TRANCHE_ANS - 1, 12, 31))))
        annee += TRANCHE_ANS
    if len(tranches) == 1:
        frames = [_tranche_chronique(code_bss, *tranches[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(len(tranches), REQUETES_SIMULTANEES)) as pool:
            frames = list(pool.map(lambda t: _tranche_chronique(code_bss, *t), tranches))
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if raw.empty or "niveau_nappe_eau" not in raw.columns:
        return pd.DataFrame(columns=["date", "niveau"])
    df = pd.DataFrame(
        {
            "date": _dates(raw["date_mesure"]),
            "niveau": pd.to_numeric(raw["niveau_nappe_eau"], errors="coerce"),
        }
    )
    return (df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True))


def chroniques_stations(codes_bss: list[str], debut: date, fin: date,
                        workers: int = 4) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Chroniques de plusieurs stations, téléchargées en parallèle.

    Une station en échec n'empêche pas d'afficher les autres : renvoie
    (chroniques obtenues, codes des stations en échec).
    """
    def charger(code: str) -> pd.DataFrame | None:
        try:
            return chronique_piezo(code, debut, fin)
        except HubEauError:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        resultats = dict(zip(codes_bss, pool.map(charger, codes_bss)))
    echecs = [c for c, df in resultats.items() if df is None]
    return {c: df for c, df in resultats.items() if df is not None}, echecs


# ---------------------------------------------------------------------------
# Qualité des nappes
# ---------------------------------------------------------------------------

def analyses_params(code_param: str, debut: date, fin: date,
                    masse_eau: str | None = None, departement: str | None = None) -> dict:
    params = {"code_param": code_param, "date_debut_prelevement": debut.isoformat(),
              "date_fin_prelevement": fin.isoformat()}
    if masse_eau:
        params["code_masse_eau_edl"] = masse_eau
    if departement:
        params["num_departement"] = departement
    return params


def _normaliser_analyses(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=ANALYSE_COLUMNS)
    lon, lat = _coordinates(raw)
    # Code remarque SANDRE : 1 = résultat quantifié ; les autres (dont 10, « < LQ ») non.
    remarque = pd.to_numeric(_first_column(raw, ["code_remarque_analyse", "code_remarque"], 1),
                             errors="coerce").fillna(1)
    df = pd.DataFrame(
        {
            "bss_id": _first_column(raw, ["bss_id", "code_bss"], "").astype(str),
            "commune": _first_column(raw, ["nom_commune_actuel", "nom_commune"], "")
                       .fillna("").astype(str).str.title(),
            "lon": lon,
            "lat": lat,
            "date": _dates(_first_column(raw, ["date_debut_prelevement"])),
            "resultat": pd.to_numeric(_first_column(raw, ["resultat"]), errors="coerce"),
            "quantifie": remarque.eq(1),
            "lq": pd.to_numeric(_first_column(raw, ["limite_quantification", "lq_analyse"]),
                                errors="coerce"),
        }
    )
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def analyses_annee(code_param: str, annee: int, masse_eau: str | None = None,
                   departements: list[str] | None = None) -> pd.DataFrame:
    """Analyses d'un paramètre sur une année civile complète, pour la masse d'eau
    (ou chaque département). Unité de mise en cache : une année passée ne change plus."""
    zones = [{"masse_eau": masse_eau}] if masse_eau else [{"departement": d} for d in departements or []]
    frames = []
    for zone in zones:
        donnees, complet = _get_all(ENDPOINT_ANALYSES, analyses_params(
            code_param, date(annee, 1, 1), date(annee, 12, 31), **zone))
        if not complet:
            raise HubEauError("Plus de 20 000 analyses sur une année : réduisez la zone.")
        frames.append(donnees)
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _normaliser_analyses(raw)


def analyses_territoire(code_param: str, debut: date, fin: date,
                        masse_eau: str | None = None,
                        departements: list[str] | None = None) -> pd.DataFrame:
    """Toutes les analyses d'un paramètre sur la masse d'eau (ou les départements).

    Une requête par année et par zone, pour rester sous la limite de 20 000
    enregistrements. Lève une erreur si une année dépasse quand même la limite,
    plutôt que de renvoyer des données silencieusement tronquées.
    """
    zones = [{"masse_eau": masse_eau}] if masse_eau else [{"departement": d} for d in departements or []]
    requetes = [
        analyses_params(code_param, max(debut, date(a, 1, 1)), min(fin, date(a, 12, 31)), **zone)
        for a in range(debut.year, fin.year + 1) for zone in zones
    ]

    def charger(params: dict) -> pd.DataFrame:
        donnees, complet = _get_all(ENDPOINT_ANALYSES, params)
        if not complet:
            raise HubEauError("Plus de 20 000 analyses sur une année : réduisez la zone.")
        return donnees

    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(charger, requetes))
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _normaliser_analyses(raw)
