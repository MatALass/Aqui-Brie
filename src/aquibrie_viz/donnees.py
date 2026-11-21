"""Accès aux données avec cache disque persistant et mise à jour incrémentale.

Pourquoi : sans cache, chaque démarrage retéléchargeait ~60 requêtes Hub'Eau. Or
l'essentiel des données ne change jamais (une mesure de 2012 reste la même).

Stratégie par type de données :
- chroniques : historique complet conservé par station ; seules les dernières
  semaines sont redemandées quand le cache a plus de FRAICHEUR_CHRONIQUE ;
- liste des stations : redemandée une fois par jour ;
- analyses de qualité : une année passée est figée (rafraîchie tous les 30 jours au
  cas où des résultats seraient ajoutés tardivement) ; l'année en cours et la
  précédente sont rafraîchies chaque jour.

Si Hub'Eau ne répond pas et qu'une version en cache existe, elle est utilisée
(données éventuellement un peu anciennes plutôt qu'une page d'erreur).

Emplacement du cache : variable d'environnement AQUIBRIE_CACHE, sinon
~/.cache/aquibrie. Le supprimer force un retéléchargement complet.

Instantané : une copie figée des données Hub'Eau est livrée avec le paquet
(aquibrie_viz/instantane, ~5 Mo). Une donnée absente du cache est d'abord lue dans
l'instantané, puis mise à jour si Hub'Eau répond. Sans réseau (ou avec
AQUIBRIE_HORS_LIGNE=1), l'application tourne donc quand même, sur des données datées.
Régénérer l'instantané : python -m aquibrie_viz.donnees --instantane
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import hubeau

FRAICHEUR_CHRONIQUE = timedelta(hours=6)
FRAICHEUR_STATIONS = timedelta(days=1)
FRAICHEUR_ANALYSES_RECENTES = timedelta(days=1)
FRAICHEUR_ANALYSES_ANCIENNES = timedelta(days=30)
# Les dernières semaines sont redemandées à chaque mise à jour : Hub'Eau peut corriger
# ou compléter des mesures récentes après coup.
RECOUVREMENT = timedelta(days=21)


INSTANTANE = Path(__file__).parent / "instantane"


def dossier_cache() -> Path:
    return Path(os.environ.get("AQUIBRIE_CACHE", Path.home() / ".cache" / "aquibrie"))


def dossier_instantane() -> Path:
    return Path(os.environ.get("AQUIBRIE_INSTANTANE", INSTANTANE))


def hors_ligne() -> bool:
    """AQUIBRIE_HORS_LIGNE=1 : ne jamais appeler Hub'Eau (tests, démo sans réseau)."""
    return os.environ.get("AQUIBRIE_HORS_LIGNE", "").strip().lower() in ("1", "true", "oui", "yes")


def _api(fonction, *args, **kwargs):
    """Appel à Hub'Eau, court-circuité en mode hors ligne."""
    if hors_ligne():
        raise hubeau.HubEauError("mode hors ligne (AQUIBRIE_HORS_LIGNE)")
    return fonction(*args, **kwargs)


# ---------------------------------------------------------------------------
# Stockage : un fichier parquet + un fichier de métadonnées par clé
# ---------------------------------------------------------------------------

_verrous: dict[str, threading.Lock] = {}
_verrou_global = threading.Lock()


def _verrou(cle: str) -> threading.Lock:
    """Un verrou par clé : deux sessions qui demandent la même station n'interrogent Hub'Eau qu'une fois."""
    with _verrou_global:
        return _verrous.setdefault(cle, threading.Lock())


def _chemins(categorie: str, cle: str, base: Path | None = None) -> tuple[Path, Path]:
    nom = hashlib.sha1(cle.encode()).hexdigest()[:16]
    dossier = (base or dossier_cache()) / categorie
    return dossier / f"{nom}.parquet", dossier / f"{nom}.json"


def _uniformiser(df: pd.DataFrame) -> pd.DataFrame:
    """Même résolution de dates qu'il s'agisse d'un téléchargement ou d'une relecture parquet."""
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):  # cache écrit par une version antérieure
            df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype("datetime64[ns]")
    return df


def _lire(categorie: str, cle: str) -> tuple[pd.DataFrame | None, dict]:
    """Cache, sinon instantané livré avec le paquet, sinon (None, {})."""
    for base in (dossier_cache(), dossier_instantane()):
        donnees, meta = _chemins(categorie, cle, base)
        if not (donnees.exists() and meta.exists()):
            continue
        try:
            return _uniformiser(pd.read_parquet(donnees)), json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, S112 - fichier corrompu : on le considère absent
            continue
    return None, {}


def _ecrire(categorie: str, cle: str, df: pd.DataFrame, meta: dict) -> None:
    """Écriture atomique : on écrit à côté puis on remplace, pour ne jamais laisser de fichier à moitié écrit."""
    donnees, fichier_meta = _chemins(categorie, cle)
    donnees.parent.mkdir(parents=True, exist_ok=True)
    tmp = donnees.with_suffix(f".{threading.get_ident()}.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, donnees)
    tmp_meta = fichier_meta.with_suffix(f".{threading.get_ident()}.tmp")
    tmp_meta.write_text(json.dumps({**meta, "cle": cle}, default=str), encoding="utf-8")
    os.replace(tmp_meta, fichier_meta)


def _maintenant() -> datetime:
    return datetime.now()


def _age(meta: dict) -> timedelta:
    try:
        return _maintenant() - datetime.fromisoformat(meta["maj"])
    except (KeyError, ValueError):
        return timedelta.max


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------

def stations(departements: list[str] | None = None, masse_eau: str | None = None) -> pd.DataFrame:
    cle = f"stations|{masse_eau}|{','.join(departements or [])}"
    with _verrou(cle):
        cache, meta = _lire("stations", cle)
        if cache is not None and _age(meta) < FRAICHEUR_STATIONS:
            return cache
        try:
            df = _api(hubeau.stations_piezo, departements, masse_eau)
        except hubeau.HubEauError:
            if cache is not None:
                return cache
            raise
        _ecrire("stations", cle, df, {"maj": _maintenant().isoformat()})
        return _uniformiser(df)


# ---------------------------------------------------------------------------
# Chroniques
# ---------------------------------------------------------------------------

def chronique(code_bss: str, debut: date, fin: date | None = None) -> pd.DataFrame:
    """Chronique d'une station entre debut et fin, en ne téléchargeant que ce qui manque."""
    fin = fin or date.today()
    aujourd_hui = date.today()
    with _verrou(f"chronique|{code_bss}"):
        cache, meta = _lire("chroniques", code_bss)
        morceaux = [] if cache is None else [cache]
        nouveau_meta = dict(meta)
        try:
            if cache is None:
                morceaux.append(_api(hubeau.chronique_piezo, code_bss, debut, aujourd_hui))
                nouveau_meta = {"debut": debut.isoformat(), "maj": _maintenant().isoformat()}
            else:
                debut_couvert = date.fromisoformat(meta["debut"])
                if debut < debut_couvert:  # historique plus ancien demandé
                    morceaux.append(_api(hubeau.chronique_piezo, code_bss, debut, debut_couvert - timedelta(days=1)))
                    nouveau_meta["debut"] = debut.isoformat()
                if _age(meta) > FRAICHEUR_CHRONIQUE:  # seulement les dernières semaines
                    depuis = (cache["date"].max().date() - RECOUVREMENT) if not cache.empty else debut
                    morceaux.append(_api(hubeau.chronique_piezo, code_bss, max(depuis, debut), aujourd_hui))
                    nouveau_meta["maj"] = _maintenant().isoformat()
        except hubeau.HubEauError:
            if cache is None:
                raise
            morceaux, nouveau_meta = [cache], meta  # Hub'Eau indisponible : on garde le cache
        if len(morceaux) > 1 or cache is None:
            complet = _uniformiser(pd.concat([_uniformiser(m) for m in morceaux], ignore_index=True)
                       .drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True))
            _ecrire("chroniques", code_bss, complet, nouveau_meta)
        else:
            complet = cache
    masque = (complet["date"] >= pd.Timestamp(debut)) & (complet["date"] <= pd.Timestamp(fin))
    return complet.loc[masque].reset_index(drop=True)


def chroniques(codes: list[str], debut: date, fin: date | None = None,
               workers: int = 4) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Plusieurs chroniques en parallèle. Renvoie (chroniques obtenues, codes en échec)."""
    def charger(code: str) -> pd.DataFrame | None:
        try:
            return chronique(code, debut, fin)
        except hubeau.HubEauError:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        resultats = dict(zip(codes, pool.map(charger, codes), strict=False))
    return ({c: df for c, df in resultats.items() if df is not None},
            [c for c, df in resultats.items() if df is None])


# ---------------------------------------------------------------------------
# Analyses de qualité
# ---------------------------------------------------------------------------

def _analyses_annee(code_param: str, annee: int, masse_eau: str | None, departements: list[str] | None) -> pd.DataFrame:
    cle = f"analyses|{code_param}|{masse_eau}|{','.join(departements or [])}|{annee}"
    fraicheur = (FRAICHEUR_ANALYSES_RECENTES if annee >= date.today().year - 1
                 else FRAICHEUR_ANALYSES_ANCIENNES)
    with _verrou(cle):
        cache, meta = _lire("analyses", cle)
        if cache is not None and _age(meta) < fraicheur:
            return cache
        try:
            df = _api(hubeau.analyses_annee, code_param, annee, masse_eau, departements)
        except hubeau.HubEauError:
            if cache is not None:
                return cache
            raise
        _ecrire("analyses", cle, df, {"maj": _maintenant().isoformat()})
        return _uniformiser(df)


def analyses(code_param: str, debut: date, fin: date, masse_eau: str | None = None,
             departements: list[str] | None = None) -> pd.DataFrame:
    """Analyses d'un paramètre entre deux dates. Changer de période ne retélécharge que les années manquantes."""
    annees = list(range(debut.year, fin.year + 1))
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(lambda a: _analyses_annee(code_param, a, masse_eau, departements), annees))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=hubeau.ANALYSE_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    masque = (df["date"] >= pd.Timestamp(debut)) & (df["date"] <= pd.Timestamp(fin))
    return df.loc[masque].sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# État du cache, pour l'interface
# ---------------------------------------------------------------------------

@dataclass
class EtatCache:
    dossier: Path
    fichiers: int
    taille_mo: float
    derniere_maj: datetime | None


def etat() -> EtatCache:
    base = dossier_cache()
    metas = list(base.glob("*/*.json")) if base.exists() else []
    donnees = list(base.glob("*/*.parquet")) if base.exists() else []
    majs = []
    for m in metas:
        try:
            majs.append(datetime.fromisoformat(json.loads(m.read_text(encoding="utf-8"))["maj"]))
        except Exception:  # noqa: BLE001
            continue
    return EtatCache(base, len(donnees), sum(f.stat().st_size for f in donnees) / 1e6, max(majs, default=None))


def marquer_perime() -> None:
    """Force une mise à jour à la prochaine lecture, sans perdre l'historique déjà téléchargé."""
    base = dossier_cache()
    for m in base.glob("*/*.json") if base.exists() else []:
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
            meta["maj"] = "1970-01-01T00:00:00"
            m.write_text(json.dumps(meta), encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue


# ---------------------------------------------------------------------------
# Préchargement (à planifier chaque nuit sur le serveur)
# ---------------------------------------------------------------------------

def prechauffer(annees_qualite: int = 10) -> str:
    """Remplit ou met à jour le cache : ainsi aucun utilisateur n'attend Hub'Eau."""
    from . import reference

    debut = datetime.now()
    sta = stations(reference.DEPARTEMENTS_CHAMPIGNY, reference.CODE_MASSE_EAU_CHAMPIGNY)
    stations(reference.DEPARTEMENTS_CHAMPIGNY, None)  # repli par département et réseau Info Piézo
    actives = sta[sta["date_fin_mesure"] >= pd.Timestamp.today() - pd.DateOffset(years=1)]
    codes = sorted(set(actives["code_bss"]) | set(reference.RESEAU_INFO_PIEZO))
    _, echecs = chroniques(codes, date(1970, 1, 1))
    aujourd_hui = date.today()
    for substance in reference.SUBSTANCES.values():
        analyses(substance["code"], date(aujourd_hui.year - annees_qualite, 1, 1), aujourd_hui,
                 reference.CODE_MASSE_EAU_CHAMPIGNY, reference.DEPARTEMENTS_CHAMPIGNY)
    e = etat()
    return (f"{len(codes)} stations, {len(echecs)} échec(s), {e.fichiers} fichiers ({e.taille_mo:.1f} Mo) "
            f"en {(datetime.now() - debut).total_seconds():.0f} s → {e.dossier}")


def construire_instantane(dossier: Path = INSTANTANE) -> str:
    """Écrit l'instantané livré avec le paquet : un cache complet, construit de zéro."""
    import shutil

    if dossier.exists():
        shutil.rmtree(dossier)
    ancien = os.environ.get("AQUIBRIE_CACHE")
    os.environ["AQUIBRIE_CACHE"] = str(dossier)
    try:
        resume = prechauffer()
    finally:
        if ancien is None:
            os.environ.pop("AQUIBRIE_CACHE")
        else:
            os.environ["AQUIBRIE_CACHE"] = ancien
    (dossier / "LISEZMOI.txt").write_text(
        f"Instantané des données Hub'Eau du {date.today().isoformat()}.\n"
        "Régénéré par : python -m aquibrie_viz.donnees --instantane\n",
        encoding="utf-8",
    )
    return resume


def date_instantane() -> date | None:
    lisezmoi = dossier_instantane() / "LISEZMOI.txt"
    if not lisezmoi.exists():
        return None
    try:
        return date.fromisoformat(lisezmoi.read_text(encoding="utf-8").split("du ")[1][:10])
    except (IndexError, ValueError):
        return None


if __name__ == "__main__":
    import sys

    print(construire_instantane() if "--instantane" in sys.argv else prechauffer())
