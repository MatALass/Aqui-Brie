"""Démonstrateur Streamlit du générateur d'infographies AQUI'Brie.

Lancement : streamlit run app.py
L'interface ne contient aucune logique de figure ni de calcul : elle appelle la
bibliothèque aquibrie_viz, qui sert aussi au mode rapport en ligne de commande.

Performance — trois niveaux de cache :
1. disque (aquibrie_viz.donnees) : les données Hub'Eau survivent aux redémarrages,
   seules les mesures récentes sont redemandées ;
2. mémoire (st.cache_data) : données et calculs dérivés, partagés entre sessions ;
3. affichage : une seule page s'exécute à la fois (st.navigation), et les petits
   éléments interactifs (exports, choix d'une année…) sont des fragments qui se
   réexécutent seuls, sans recalculer la page.
"""
from __future__ import annotations

import io
import tempfile
import threading
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from aquibrie_viz import bulletin, donnees, fiche, hubeau, rapport, reference, templates
from aquibrie_viz import indicators as ind
from aquibrie_viz.export import FORMATS, ExportError, demarrer_moteur, figure_to_bytes
from aquibrie_viz.templates import fr_date, fr_num
from aquibrie_viz.theme import COLORS, QUALITE, REMPLISSAGE, SITUATION, STATUS

st.set_page_config(page_title="AQUI'Brie — infographies", page_icon="💧", layout="wide")

RACINE = Path(__file__).resolve().parent
HISTORIQUE_DEBUT = date(1970, 1, 1)  # toute l'histoire disponible, pour les comparaisons
QUALITE_DEBUT_MIN = date(2000, 1, 1)  # au-delà, trop de requêtes pour une démo interactive
PERIODES = {"1 an": 1, "5 ans": 5, "10 ans": 10, "20 ans": 20, "Tout": 0}
SUBSTANCES = reference.SUBSTANCES
TTL = timedelta(hours=1)  # cache mémoire ; le cache disque a ses propres règles de fraîcheur

if "rapport" not in st.session_state:
    st.session_state.rapport = []  # liste de spécifications de figures (voir aquibrie_viz.rapport)
    st.session_state.rapport_titre = "Bulletin de situation de la nappe du Champigny"
    st.session_state.seuils_perso = {}  # seuils indicatifs modifiés, par station


@st.cache_resource
def _prechauffer_export() -> None:
    """Ouvre Chrome pour les exports en tâche de fond, une fois par serveur : le premier
    aperçu de fiche ne paie pas les ~2 s de démarrage."""
    threading.Thread(target=demarrer_moteur, daemon=True).start()


_prechauffer_export()

# --- Habillage à la charte ---------------------------------------------------

st.markdown(
    f"""
    <style>
      .ab-header {{ display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap; }}
      .ab-wordmark {{ font-size:2.6rem; font-weight:800; letter-spacing:.02em; line-height:1; }}
      .ab-aqui {{ color:{COLORS['aqui']}; }} .ab-brie {{ color:{COLORS['brie']}; }}
      .ab-tagline {{ color:{COLORS['muted']}; font-size:1.05rem; }}
      .ab-dots {{ border-bottom:4px dotted {COLORS['brie']}; width:220px; margin:.4rem 0 1.2rem; }}
      .ab-badge {{ display:inline-block; padding:.15rem .7rem; border-radius:1rem; font-size:.85rem;
                   font-weight:700; background:{COLORS['aqui_mist']}; color:{COLORS['aqui_dark']}; }}
      .ab-kpi {{ background:white; border:1px solid {COLORS['grid']}; border-radius:.9rem;
                 padding:.9rem 1.1rem; min-height:8.6rem; }}
      .ab-kpi-label {{ color:{COLORS['muted']}; font-size:.85rem; font-weight:600;
                       text-transform:uppercase; letter-spacing:.04em; }}
      .ab-kpi-value {{ color:{COLORS['abyss']}; font-size:1.9rem; font-weight:800; line-height:1.2; }}
      .ab-kpi-note {{ color:{COLORS['muted']}; font-size:.88rem; }}
      .ab-pastille {{ display:inline-block; width:.8rem; height:.8rem; border-radius:50%;
                      margin-right:.35rem; vertical-align:middle; }}
      .ab-swatch {{ border-radius:.8rem; padding:1rem; height:120px; display:flex; flex-direction:column;
                    justify-content:flex-end; font-weight:700; border:1px solid {COLORS['grid']};
                    margin-bottom:1.2rem; }}
      .ab-etape {{ background:white; border:1px solid {COLORS['grid']}; border-radius:.9rem;
                   padding:1rem 1.2rem; height:100%; }}
      .ab-etape b {{ color:{COLORS['aqui_dark']}; }}
    </style>
    <div class="ab-header">
      <span class="ab-wordmark"><span class="ab-aqui">AQUI'</span><span class="ab-brie">Brie</span></span>
      <span class="ab-tagline">Générateur d'infographies · nappe des calcaires de Champigny</span>
    </div>
    <div class="ab-dots"></div>
    """,
    unsafe_allow_html=True,
)


def kpi(col, libelle: str, valeur: str, note: str = "", couleur: str | None = None) -> None:
    pastille = f"<span class='ab-pastille' style='background:{couleur}'></span>" if couleur else ""
    style = "" if len(valeur) <= 12 else " style='font-size:1.35rem'"  # textes longs : plus petits
    col.markdown(
        f"<div class='ab-kpi'><div class='ab-kpi-label'>{libelle}</div>"
        f"<div class='ab-kpi-value'{style}>{pastille}{valeur}</div>"
        f"<div class='ab-kpi-note'>{note}</div></div>",
        unsafe_allow_html=True,
    )


def aide(texte: str) -> None:
    with st.expander("Comment lire ce graphique ?"):
        st.markdown(texte)


def memoire(cle: str, defaut):
    """Streamlit oublie les choix faits sur une page quand on en change : on les garde à part."""
    if cle not in st.session_state:
        st.session_state[cle] = st.session_state.get(f"_m_{cle}", defaut)
    return cle


def retenir(cle: str) -> None:
    st.session_state[f"_m_{cle}"] = st.session_state[cle]


# --- Données : cache disque (donnees) + cache mémoire (st.cache_data) -----------

@st.cache_data(ttl=TTL, show_spinner="Recherche des piézomètres…")
def charger_stations(departements: tuple[str, ...], masse_eau: str | None) -> pd.DataFrame:
    return donnees.stations(list(departements), masse_eau)


@st.cache_data(ttl=TTL, show_spinner="Chargement de l'historique de la station…")
def charger_chronique(code_bss: str) -> pd.DataFrame:
    return donnees.chronique(code_bss, HISTORIQUE_DEBUT)


@st.cache_data(ttl=TTL, show_spinner="Chargement des chroniques de toutes les stations…")
def charger_toutes_chroniques(codes: tuple[str, ...]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    # 20 ans suffisent pour situer le niveau actuel.
    return donnees.chroniques(list(codes), date(date.today().year - 19, 1, 1))


@st.cache_data(ttl=TTL, show_spinner="Chargement des analyses de qualité…")
def charger_analyses(code_param: str, debut: date, fin: date, masse_eau: str | None,
                     departements: tuple[str, ...]) -> pd.DataFrame:
    return donnees.analyses(code_param, debut, fin, masse_eau, list(departements))


# --- Calculs dérivés, mis en cache par clé simple (codes) plutôt que par tableau ---

@st.cache_data(ttl=TTL, show_spinner="Situation de chaque piézomètre…")
def situation_territoire(departements: tuple[str, ...], masse_eau: str | None,
                         codes: tuple[str, ...]) -> tuple[pd.DataFrame, list[str]]:
    stations = charger_stations(departements, masse_eau)
    chroniques, echecs = charger_toutes_chroniques(codes)
    if len(echecs) == len(codes):
        raise hubeau.HubEauError("aucune chronique n'a pu être chargée")  # non mis en cache
    return ind.situation_territoire(stations[stations["code_bss"].isin(codes)], chroniques), echecs


@st.cache_data(ttl=TTL, show_spinner=False)
def calculs_station(code_bss: str, nom: str) -> dict | None:
    """Tout ce qui ne dépend que de l'historique d'une station."""
    historique = charger_chronique(code_bss)
    if historique.empty:
        return None
    seuils, officiels = reference.seuils_station(nom, historique)
    return {"seuils": seuils, "officiels": officiels, "recharge": ind.recharge_annuelle(historique),
            "anomalies": ind.anomalies_mensuelles(historique), "situation": ind.situation(historique),
            "tendance": ind.tendance_cm_par_jour(historique)}


@st.cache_data(ttl=TTL, show_spinner="Composition de la fiche…")
def fiche_json(departements, masse_eau, codes, code_bss, nom, commune, seuils: tuple, officiels: bool):
    """Fiche A4 sérialisée (JSON) : sert de clé au cache de l'aperçu PNG."""
    sit, _ = situation_territoire(departements, masse_eau, codes)
    calc = calculs_station(code_bss, nom)
    try:
        fig = fiche.fiche_synthese(sit, commune, charger_chronique(code_bss), dict(seuils), officiels,
                                   calc["recharge"])
    except ValueError as exc:
        return None, str(exc)
    return fig.to_json(), None


# Les exports sont gardés sur disque : une fiche déjà rendue n'est plus jamais recalculée.
@st.cache_data(show_spinner="Préparation du fichier…", persist="disk", max_entries=300)
def exporter(figure_json: str, fmt: str) -> bytes:
    return figure_to_bytes(pio.from_json(figure_json), fmt)


@st.cache_data(ttl=60, show_spinner=False)
def etat_cache() -> donnees.EtatCache:
    return donnees.etat()


def vider_caches_memoire() -> None:
    for fonction in (charger_stations, charger_chronique, charger_toutes_chroniques, charger_analyses,
                     situation_territoire, calculs_station, fiche_json, etat_cache):
        fonction.clear()


class FournisseurStreamlit:
    """Le mode rapport réutilise les données déjà chargées par l'interface (cache)."""

    def __init__(self, departements: tuple[str, ...]):
        self.departements = departements

    def stations(self, masse_eau):
        return charger_stations(self.departements, masse_eau)

    def chronique(self, code_bss):
        return charger_chronique(code_bss)

    def chroniques(self, codes):
        return charger_toutes_chroniques(tuple(codes))[0]

    def analyses(self, code_param, debut, fin, masse_eau):
        return charger_analyses(code_param, debut, fin, masse_eau, self.departements)


# --- Rapport : panier de figures ---------------------------------------------

def _sans_id(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k != "id"}


def ajouter_au_rapport(spec: dict) -> None:
    deja = [s for s in st.session_state.rapport if _sans_id(s) == spec]
    if deja:
        st.toast("Cette figure est déjà dans le rapport.", icon="ℹ️")
        return
    existants = {s["id"] for s in st.session_state.rapport}
    base = spec["modele"].split("_")[0] + ("_" + spec["station"].split("/")[0] if spec.get("station") else "")
    st.session_state.rapport.append({"id": rapport.identifiant_libre(base, existants), **spec})
    st.toast(f"Ajoutée au rapport ({len(st.session_state.rapport)} figure(s)).", icon="✅")


# --- Affichage et export des figures ------------------------------------------

@st.fragment
def boutons(fig: go.Figure, nom: str, spec: dict | None, formats: tuple[str, ...] = ("png", "svg", "pdf", "html"),
            empiler: bool = False) -> None:
    """Sous chaque figure : ajout au rapport, puis exports à la demande.

    Fragment : ouvrir les exports ou ajouter au rapport ne réexécute que ces boutons.
    """
    nom = nom.replace("/", "-").replace(" ", "-")
    c1, c2 = (st.container(), st.container()) if empiler else st.columns([1, 3])
    if spec is not None:
        c1.button("＋ Ajouter au rapport", key=f"ajout-{nom}", on_click=ajouter_au_rapport, args=(spec,),
                  width="stretch")
    if not c2.toggle("Exporter cette infographie", key=f"export-{nom}"):
        return
    colonnes = st.columns(len(formats))
    figure_json = fig.to_json()  # seulement quand les exports sont demandés
    for col, fmt in zip(colonnes, formats, strict=False):
        mime, libelle = FORMATS[fmt]
        try:
            data = exporter(figure_json, fmt)
        except ExportError as exc:
            col.warning(f"{libelle} indisponible : {exc}")
            continue
        col.download_button(libelle, data, file_name=f"{nom}.{fmt}", mime=mime, width="stretch",
                            key=f"{nom}-{fmt}")


ZOOM_RAPIDE = dict(
    buttons=[dict(count=1, label="1 an", step="year", stepmode="backward"),
             dict(count=5, label="5 ans", step="year", stepmode="backward"),
             dict(count=10, label="10 ans", step="year", stepmode="backward"),
             dict(label="Tout", step="all")],
    bgcolor=COLORS["limestone"], activecolor=COLORS["aqui_light"], font=dict(size=13),
    x=1, xanchor="right", y=1.02, yanchor="bottom",
)


def afficher(fig: go.Figure, nom: str, spec: dict | None = None, boutons_temps: bool = False) -> None:
    """Affiche une figure à la charte, puis ses boutons."""
    fig.update_layout(height=fig.layout.height or 640, paper_bgcolor=COLORS["paper"],
                      plot_bgcolor=COLORS["paper"])
    if boutons_temps:
        # Boutons de zoom seulement à l'écran : ajoutés juste pour l'affichage, retirés avant l'export
        # (évite de copier la figure, coûteux sur un historique de 50 ans).
        fig.update_xaxes(rangeselector=ZOOM_RAPIDE)
    # theme=None : sinon Streamlit remplace la charte (marges, couleurs, police) par la sienne.
    st.plotly_chart(fig, width="stretch", theme=None, key=f"graphique-{nom}")
    if boutons_temps:
        fig.update_xaxes(rangeselector=None)
    boutons(fig, nom, spec)


def figure_ou_message(fabrique, *args, **kwargs) -> go.Figure | None:
    """Les modèles lèvent ValueError quand les données ne suffisent pas : on l'affiche proprement."""
    try:
        return fabrique(*args, **kwargs)
    except ValueError as exc:
        st.info(str(exc))
        return None


def historique_station() -> pd.DataFrame:
    try:
        return charger_chronique(code)
    except hubeau.HubEauError as exc:
        st.error(f"Hub'Eau ne répond pas pour cette station, et elle n'est pas encore en cache. {exc}")
        st.stop()


def seuils_courants(calc: dict) -> dict[str, float]:
    """Seuils officiels, ou indicatifs éventuellement modifiés sur la page Station."""
    if calc["officiels"]:
        return calc["seuils"]
    perso = st.session_state.seuils_perso.get(code, {})
    return {nom: perso.get(nom, val) for nom, val in calc["seuils"].items()}


# --- Barre latérale (commune à toutes les pages) ---------------------------------

st.sidebar.markdown("### Périmètre")
perimetre = st.sidebar.radio("Zone étudiée", ["Nappe du Champigny (masse d'eau)", "Départements 77, 91, 94"],
                             label_visibility="collapsed")
masse_eau = reference.CODE_MASSE_EAU_CHAMPIGNY if perimetre.startswith("Nappe") else None
departements = tuple(reference.DEPARTEMENTS_CHAMPIGNY)

try:
    stations = charger_stations(departements, masse_eau)
    if stations.empty and masse_eau:
        st.sidebar.info("Aucune station renvoyée pour la masse d'eau : recherche par département.")
        masse_eau = None
        stations = charger_stations(departements, None)
except hubeau.HubEauError as exc:
    st.error(f"Impossible de joindre l'API Hub'Eau, et aucune donnée n'est encore en cache. {exc}\n\n"
             "Vérifiez la connexion Internet puis rechargez la page.")
    st.stop()

if st.sidebar.toggle("Stations actives uniquement", value=True,
                     help="Stations ayant transmis une mesure depuis moins d'un an."):
    limite = pd.Timestamp.today() - pd.DateOffset(years=1)
    stations = stations[stations["date_fin_mesure"] >= limite].reset_index(drop=True)
if stations.empty:
    st.warning("Aucun piézomètre trouvé pour ce périmètre.")
    st.stop()
codes = tuple(stations["code_bss"])

st.sidebar.markdown("### Période")
choix_periode = st.sidebar.segmented_control("Période", list(PERIODES) + ["Dates…"], default="10 ans",
                                             label_visibility="collapsed") or "10 ans"
fin = date.today()
if choix_periode == "Dates…":
    plage = st.sidebar.date_input("Du … au …", (fin - timedelta(days=365 * 5), fin),
                                  min_value=HISTORIQUE_DEBUT, max_value=fin, format="DD/MM/YYYY")
    debut, fin = (plage if isinstance(plage, tuple) and len(plage) == 2 else (plage[0], fin))
    periode_ans = max(1, fin.year - debut.year)  # le rapport raisonne en années glissantes
elif PERIODES[choix_periode]:
    periode_ans = PERIODES[choix_periode]
    debut = date(fin.year - periode_ans, fin.month, 1)
else:
    periode_ans, debut = 0, HISTORIQUE_DEBUT

st.sidebar.markdown("### Station")
noms = stations["nom"].tolist()
defaut = next((i for i, n in enumerate(noms) if reference.seuils_pour(n)), 0)
nom_station = st.sidebar.selectbox(f"{len(noms)} piézomètres", noms, index=defaut)
station = stations.loc[stations["nom"] == nom_station].iloc[0]
code = station["code_bss"]

with st.sidebar.expander("Comprendre les données"):
    st.markdown(
        "- **Piézomètre** : forage qui mesure le niveau de la nappe en un point.\n"
        "- **m NGF** : altitude de l'eau en mètres au-dessus du niveau de la mer "
        "(Nivellement général de la France). Plus la valeur est haute, plus la nappe est pleine.\n"
        "- **Seuils** : niveaux fixés par les arrêtés sécheresse. Sous un seuil, des restrictions "
        "d'usage de l'eau s'appliquent.\n"
        "- **Recharge** : remontée de la nappe, en hiver et au printemps, grâce aux pluies infiltrées.\n"
        "- **Limite de quantification (LQ)** : plus petite concentration que le laboratoire sait "
        "mesurer. En dessous, la substance n'est pas forcément absente.\n"
        "- **Norme** : limite de qualité de l'eau potable (0,1 µg/L par pesticide, 50 mg/L pour les nitrates)."
    )
with st.sidebar.expander("D'où viennent les données ?"):
    st.markdown("API publique **Hub'Eau** (BRGM, banque nationale ADES), mise à jour quotidiennement.")
    for params in hubeau.stations_params(list(departements), masse_eau):
        st.markdown(f"- [Liste des piézomètres]({hubeau.api_url(hubeau.ENDPOINT_STATIONS, params)})")
    url_chro = hubeau.api_url(hubeau.ENDPOINT_CHRONIQUES, hubeau.chronique_params(code, debut, fin))
    st.markdown(f"- [Mesures de la station choisie]({url_chro})")
    st.markdown("[Documentation de l'API](https://hubeau.eaufrance.fr/page/api-piezometrie)")
    etat = etat_cache()
    maj = etat.derniere_maj.strftime("%d/%m/%Y à %H:%M") if etat.derniere_maj else "jamais"
    st.markdown(f"**Copie locale** : {etat.fichiers} fichiers, {etat.taille_mo:.1f} Mo, "
                f"dernière mise à jour {maj}. Les mesures récentes sont redemandées toutes les 6 heures ; "
                "l'historique n'est téléchargé qu'une fois.")
    if st.button("Actualiser maintenant depuis Hub'Eau", width="stretch"):
        donnees.marquer_perime()
        vider_caches_memoire()
        st.rerun()


# =============================================================================
# 1. Vue d'ensemble du territoire
# =============================================================================
def page_ensemble() -> None:
    try:
        sit, echecs = situation_territoire(departements, masse_eau, codes)
    except hubeau.HubEauError as exc:
        st.error(f"Hub'Eau ne répond pas et aucune donnée n'est en cache. {exc}")
        return
    if echecs:
        noms_echecs = stations.loc[stations["code_bss"].isin(echecs), "commune"].tolist()
        st.warning(f"Hub'Eau n'a pas répondu pour {len(echecs)} station(s) : {', '.join(noms_echecs)}. "
                   "Elles sont absentes de cette vue ; rechargez la page dans quelques minutes.")

    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "Piézomètres suivis", str(len(stations)), f"{len(sit)} avec assez d'historique")
    n_bas = int(sit["classe"].isin(["Très bas", "Bas"]).sum())
    kpi(c2, "Sous les niveaux habituels", f"{n_bas} / {len(sit)}", "classes « bas » et « très bas »",
        SITUATION["Bas"])
    n_baisse = int((sit["tendance"] < 0).sum())
    kpi(c3, "En baisse sur 30 jours", f"{n_baisse} / {len(sit)}", "pente négative du niveau")
    derniere = sit["date"].max() if not sit.empty else None
    kpi(c4, "Dernière mesure", derniere.strftime("%d/%m/%Y") if derniere is not None else "–",
        "toutes stations confondues")
    st.write("")

    if sit.empty:
        st.info("Aucune station n'a assez d'historique (5 ans minimum) pour situer son niveau actuel.")
        return
    afficher(templates.carte_situation(sit, stations, code), "carte_situation",
             {"modele": "carte_situation", "station": code})
    aide("Chaque point est un piézomètre. Sa couleur compare le dernier niveau mesuré au **même "
         "jour des années précédentes** : brique = parmi les plus bas jamais observés, bleu-vert = "
         "parmi les plus hauts. Survolez un point pour voir son niveau.")
    afficher(templates.situation_stations(sit), "classement_stations", {"modele": "situation_stations"})
    aide("Les stations sont classées de la plus basse à la plus haute, **par rapport à leur propre "
         "historique**. Une station à 20 % signifie que 80 % des années précédentes étaient plus hautes "
         "à la même date. Cela permet de comparer des stations dont les altitudes sont très différentes.")


# =============================================================================
# 2. Une station
# =============================================================================
@st.fragment
def section_annee(base_annuelle: pd.DataFrame, periode_annuelle: int) -> None:
    """Fragment : changer d'année ne recalcule que ce graphique."""
    annees = sorted(base_annuelle["date"].dt.year.unique(), reverse=True)
    annee = st.selectbox("Année à comparer à l'historique", annees, key=f"annee-{code}")
    fig = figure_ou_message(templates.annee_vs_historique, base_annuelle, int(annee), nom_station)
    if fig:
        afficher(fig, f"annee_{annee}_{code}",
                 {"modele": "annee_vs_historique", "station": code, "periode_ans": periode_annuelle,
                  "annee": "derniere" if annee == annees[0] else int(annee)})
        aide("La bande colorée couvre les niveaux observés **8 années sur 10** à la même date (les "
             "années les plus extrêmes sont exclues pour ne pas écraser le graphique) ; la ligne "
             "pointillée en est la médiane. La courbe foncée est l'année choisie. Choisissez « 20 ans » "
             "ou « Tout » dans la barre latérale pour comparer à un historique plus long.")


def page_station() -> None:
    st.markdown(f"#### {nom_station}")
    historique = historique_station()
    calc = calculs_station(code, nom_station)
    if calc is None:
        st.info("Aucune mesure disponible pour cette station.")
        return
    officiels = calc["officiels"]
    seuils = seuils_courants(calc)
    if officiels:
        st.markdown("<span class='ab-badge'>Seuils réglementaires issus du document AQUI'Brie</span>",
                    unsafe_allow_html=True)
    else:
        with st.expander("Seuils indicatifs : aucun seuil réglementaire n'est renseigné pour cette station"):
            st.caption("Valeurs par défaut calculées sur l'historique. Remplacez-les par les seuils "
                       "des arrêtés sécheresse si vous les connaissez : ils seront repris dans la fiche "
                       "et dans le rapport.")
            c = st.columns(4)
            for i, (nom, val) in enumerate(seuils.items()):
                seuils[nom] = c[i].number_input(nom, step=0.1, key=memoire(f"seuil-{code}-{nom}", val))
            st.session_state.seuils_perso[code] = dict(seuils)
    seuils_spec = None if officiels else [round(v, 2) for v in seuils.values()]
    periode = historique[(historique["date"] >= pd.Timestamp(debut)) & (historique["date"] <= pd.Timestamp(fin))]

    dernier = historique.iloc[-1]
    tendance, sit_station = calc["tendance"], calc["situation"]
    proj = ind.projection_seuil(historique, seuils)
    statut = ind.statut_niveau(dernier["niveau"], seuils)

    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "Dernier niveau", f"{templates.fr_num(dernier['niveau'])} m",
        f"mesuré le {templates.fr_date(dernier['date'])}")
    kpi(c2, "Tendance 30 jours",
        "–" if tendance is None else f"{'+' if tendance >= 0 else '−'}{templates.fr_num(abs(tendance), 1)} cm/j",
        "en hausse" if (tendance or 0) >= 0 else "en baisse")
    kpi(c3, "Par rapport aux autres années", sit_station["classe"] if sit_station else "–",
        f"rang {sit_station['percentile']:.0f} % sur {sit_station['n_annees']} ans" if sit_station else
        "historique insuffisant", SITUATION[sit_station["classe"]] if sit_station else None)
    if statut:
        kpi(c4, "Seuil franchi", statut, f"sous {templates.fr_num(seuils[statut], 1)} m", STATUS[statut])
    elif proj:
        kpi(c4, f"Seuil {proj.seuil.lower()}", f"dans {proj.jours} j",
            f"vers le {templates.fr_date(proj.date)} si la baisse continue", STATUS[proj.seuil])
    else:
        kpi(c4, "Seuils", "Aucun franchi", "pas de franchissement prévu à 1 an")
    st.write("")

    if periode.empty:
        st.info("Pas de mesures sur la période choisie : élargissez-la dans la barre latérale.")
    else:
        afficher(templates.chronique_seuils(periode, seuils, nom_station, seuils_officiels=officiels,
                                            projection=proj),
                 f"niveau_{code}",
                 {"modele": "chronique_seuils", "station": code, "periode_ans": periode_ans,
                  "seuils": seuils_spec},
                 boutons_temps=True)
        aide("La courbe montre le niveau de la nappe ; les lignes pointillées, les seuils "
             "réglementaires. Les interruptions de la courbe sont des périodes sans mesure. Si le niveau "
             "baisse, la ligne en pointillés prolonge la tendance des 30 derniers jours jusqu'au prochain "
             "seuil : c'est une **extrapolation**, pas une prévision météorologique.")

    # Base des comparaisons annuelles : la période choisie si elle couvre au moins 5 ans,
    # sinon les 20 dernières années (1 an de recul ne permet aucune comparaison).
    if periode["date"].dt.year.nunique() >= 5:
        base_annuelle, periode_annuelle = periode, periode_ans
    else:
        base_annuelle, periode_annuelle = historique[historique["date"].dt.year >= fin.year - 20], 20
        st.caption("La période choisie est trop courte pour les comparaisons annuelles : "
                   "elles portent sur les 20 dernières années.")
    premiere_annee = base_annuelle["date"].dt.year.min()

    section_annee(base_annuelle, periode_annuelle)

    # Recharge et calendrier en pleine largeur (côte à côte, les libellés deviennent illisibles).
    recharge = calc["recharge"]
    if not recharge.empty:  # année hydrologique commencée en septembre de l'année « debut »
        recharge = recharge[recharge["debut"] >= premiere_annee]
    fig = figure_ou_message(templates.recharge_annuelle, recharge, nom_station)
    if fig:
        afficher(fig, f"recharge_{code}",
                 {"modele": "recharge_annuelle", "station": code, "periode_ans": periode_annuelle})
        aide("Chaque barre mesure la plus forte remontée de la nappe dans l'année hydrologique "
             "(septembre → août), en général entre les basses eaux d'automne et les hautes eaux de "
             "printemps. « Aucune » signifie que la nappe n'est pas remontée de l'année. Une faible "
             "recharge annonce une saison suivante tendue. Les années trop peu mesurées sont omises.")
    fig = figure_ou_message(templates.calendrier_anomalies,
                            calc["anomalies"].loc[lambda a: a.index >= premiere_annee], nom_station)
    if fig:
        afficher(fig, f"calendrier_{code}",
                 {"modele": "calendrier_anomalies", "station": code, "periode_ans": periode_annuelle})
        aide("Chaque case compare le niveau moyen d'un mois à la médiane de ce mois sur **tout** "
             "l'historique. Brique : plus bas que d'habitude ; bleu-vert : plus haut. Les longues suites de "
             "cases brique révèlent les sécheresses.")


# =============================================================================
# 3. Qualité de l'eau
# =============================================================================
@st.fragment
def detail_qualite(analyses: pd.DataFrame, synth: pd.DataFrame, nom_param: str, spec_q: dict) -> None:
    """Fragment : changer de station ne recalcule pas la carte ni le graphique annuel."""
    param = SUBSTANCES[nom_param]
    st.markdown("##### Détail par station")
    libelles = (synth["commune"] + " · " + synth["bss_id"] + " · "
                + synth["n_depassements"].astype(int).astype(str) + " dépassement(s)").tolist()
    choix = st.selectbox("Station (les plus touchées en premier)", libelles, key=f"qualite-station-{nom_param}")
    bss = synth.loc[libelles.index(choix), "bss_id"]
    detail = analyses[analyses["bss_id"] == bss]
    afficher(templates.concentration_norme(detail, param["norme"], nom_param, param["unite"],
                                           f"{detail['commune'].iloc[0]} ({bss})"),
             f"qualite_{nom_param}_{bss}",
             {"modele": "concentration_norme", "bss_qualite": bss, **spec_q}, boutons_temps=True)
    aide("Points pleins : concentrations mesurées (en brique au-dessus de la norme). Cercles vides : "
         "résultats inférieurs à la limite de quantification, placés à cette limite.")


@st.cache_data(ttl=TTL, show_spinner=False)
def syntheses_qualite(code_param: str, debut_q: date, fin_q: date, masse_eau: str | None,
                      departements: tuple[str, ...], norme: float):
    analyses = charger_analyses(code_param, debut_q, fin_q, masse_eau, departements)
    if analyses.empty:
        return analyses, None, None
    return (analyses, ind.synthese_stations_qualite(analyses, norme),
            ind.synthese_annuelle_qualite(analyses, norme))


def page_qualite() -> None:
    c1, c2 = st.columns([2, 3])
    noms_substances = list(SUBSTANCES)
    nom_param = c1.selectbox("Substance", noms_substances, key=memoire("substance", noms_substances[0]),
                             on_change=retenir, args=("substance",))
    param = SUBSTANCES[nom_param]
    c2.markdown(f"<br>Norme de qualité : **{templates.fr_num(param['norme'], 2)} {param['unite']}** "
                f"· code SANDRE {param['code']}", unsafe_allow_html=True)
    debut_q = max(debut, QUALITE_DEBUT_MIN)
    if debut_q > debut:
        st.caption(f"Pour la qualité, la période commence au {debut_q.strftime('%d/%m/%Y')} "
                   "(limite de cette démonstration).")
    try:
        analyses, synth, annuel = syntheses_qualite(param["code"], debut_q, fin, masse_eau, departements,
                                                    param["norme"])
    except hubeau.HubEauError as exc:
        st.error(str(exc))
        return
    if analyses.empty:
        st.info("Aucune analyse de cette substance sur le périmètre et la période choisis.")
        return

    c = st.columns(4)
    kpi(c[0], "Analyses", f"{len(analyses):,}".replace(",", " "), f"{debut_q.year}–{fin.year}")
    kpi(c[1], "Stations analysées", str(len(synth)), "suivi de la qualité des eaux souterraines")
    kpi(c[2], "Analyses quantifiées", f"{analyses['quantifie'].mean() * 100:.0f} %",
        "substance mesurée au-dessus de la LQ", QUALITE["Détecté, sous la norme"])
    n_dep = int((synth["n_depassements"] > 0).sum())
    kpi(c[3], "Stations en dépassement", f"{n_dep} / {len(synth)}", "au moins une analyse > norme",
        QUALITE["Dépassement de la norme"])
    st.write("")

    spec_q = {"substance": nom_param, "periode_ans": periode_ans}
    afficher(templates.carte_qualite(synth, nom_param, param["unite"]), f"carte_{nom_param}",
             {"modele": "carte_qualite", **spec_q})
    aide("Chaque point est une station de suivi de la qualité. Brique : au moins une analyse au-dessus de "
         "la norme sur la période ; sable : substance détectée mais toujours sous la norme ; gris : jamais "
         "quantifiée.")
    afficher(templates.qualite_annuelle(annuel, nom_param), f"qualite_annuelle_{nom_param}",
             {"modele": "qualite_annuelle", **spec_q})
    aide("Pour chaque année, la part des analyses où la substance est mesurée, et la part au-dessus de "
         "la norme. Le nombre d'analyses est indiqué sous chaque année : une année avec peu d'analyses "
         "est moins représentative.")

    detail_qualite(analyses, synth, nom_param, spec_q)
    with st.expander("Tableau des stations"):
        st.dataframe(
            synth[["commune", "bss_id", "n", "n_quanti", "maximum", "n_depassements", "date_derniere"]]
            .rename(columns={"commune": "Commune", "bss_id": "Station", "n": "Analyses",
                             "n_quanti": "Quantifiées", "maximum": f"Maximum ({param['unite']})",
                             "n_depassements": "Dépassements", "date_derniere": "Dernière analyse"}),
            hide_index=True, width="stretch",
        )


# =============================================================================
# 4. Fiche grand public
# =============================================================================
def page_fiche() -> None:
    st.markdown("#### Fiche « La nappe aujourd'hui »")
    st.caption("Une page A4 pour sensibiliser le grand public : mairies, lettres d'information, salons. "
               f"Elle met en avant la station choisie dans la barre latérale ({station['commune']}).")
    historique_station()
    calc = calculs_station(code, nom_station)
    if calc is None:
        st.info("Aucune mesure disponible pour cette station.")
        return
    seuils = seuils_courants(calc)
    try:
        contenu, message = fiche_json(departements, masse_eau, codes, code, nom_station, station["commune"],
                                      tuple(seuils.items()), calc["officiels"])
    except hubeau.HubEauError as exc:
        st.error(str(exc))
        return
    if contenu is None:
        st.info(message)
        return
    fig_fiche = pio.from_json(contenu)
    gauche, droite = st.columns([3, 2])
    with gauche:
        try:  # aperçu identique au fichier exporté, mis en cache sur disque
            st.image(exporter(contenu, "png"), width="stretch")
        except ExportError:
            st.plotly_chart(fig_fiche, width="content", theme=None, config={"staticPlot": True})
    with droite:
        st.markdown(
            "**Ce que la fiche montre**\n"
            "- un message principal, calculé à partir des données ;\n"
            "- une goutte par point de mesure, colorée selon son niveau par rapport aux autres années ;\n"
            "- trois chiffres clés et la courbe de la station de référence ;\n"
            "- un encart pédagogique et le lien vers les restrictions en vigueur.\n\n"
            "Elle se régénère à chaque mise à jour des données : la même fiche peut être diffusée "
            "chaque mois sans retouche."
        )
        boutons(fig_fiche, f"fiche_{code}", {"modele": "fiche_synthese", "station": code},
                formats=("png", "pdf"), empiler=True)


# =============================================================================
# 5. Rapport
# =============================================================================
def page_rapport() -> None:
    st.markdown("#### Composer un rapport et le régénérer en un clic")
    e1, e2, e3 = st.columns(3)
    e1.markdown("<div class='ab-etape'><b>1. Choisir</b><br>Cliquez sur « ＋ Ajouter au rapport » sous "
                "chaque infographie utile, ou partez de l'exemple.</div>", unsafe_allow_html=True)
    e2.markdown("<div class='ab-etape'><b>2. Enregistrer</b><br>Téléchargez le modèle de rapport (.toml). "
                "Il décrit les figures en termes relatifs : « les 10 dernières années ».</div>",
                unsafe_allow_html=True)
    e3.markdown("<div class='ab-etape'><b>3. Régénérer</b><br>L'an prochain, rechargez le modèle et "
                "cliquez sur « Générer » : toutes les figures sont à jour.</div>", unsafe_allow_html=True)
    st.write("")

    charge = st.file_uploader("Charger un modèle de rapport (.toml)", type=["toml"])
    c1, c2 = st.columns(2)
    if charge is not None and c1.button("Remplacer le rapport par ce fichier", width="stretch"):
        try:
            r = rapport.lire(charge.getvalue().decode("utf-8"))
            st.session_state.rapport, st.session_state.rapport_titre = r.figures, r.titre
            st.rerun()
        except rapport.RapportError as exc:
            st.error(str(exc))
    if c2.button("Charger le rapport d'exemple", width="stretch"):
        r = rapport.lire((RACINE / "rapports" / "exemple_bulletin.toml").read_text(encoding="utf-8"))
        st.session_state.rapport, st.session_state.rapport_titre = r.figures, r.titre
        st.rerun()

    figures_rapport = st.session_state.rapport
    if not figures_rapport:
        st.info("Le rapport est vide. Ajoutez des figures depuis les autres pages, ou chargez l'exemple.")
        return
    st.session_state.rapport_titre = st.text_input("Titre du rapport", st.session_state.rapport_titre)
    noms_stations = dict(zip(stations["code_bss"], stations["commune"], strict=False))
    st.markdown(f"**{len(figures_rapport)} figure(s), dans l'ordre du rapport**")
    for i, spec in enumerate(list(figures_rapport)):
        l1, l2, l3, l4 = st.columns([12, 1, 1, 1])
        l1.markdown(f"{i + 1}. {rapport.description(spec, noms_stations)} · `{spec['id']}`")
        if l2.button("↑", key=f"haut-{spec['id']}", disabled=i == 0, help="Monter"):
            figures_rapport[i - 1], figures_rapport[i] = figures_rapport[i], figures_rapport[i - 1]
            st.rerun()
        if l3.button("↓", key=f"bas-{spec['id']}", disabled=i == len(figures_rapport) - 1, help="Descendre"):
            figures_rapport[i + 1], figures_rapport[i] = figures_rapport[i], figures_rapport[i + 1]
            st.rerun()
        if l4.button("✕", key=f"suppr-{spec['id']}", help="Retirer"):
            figures_rapport.pop(i)
            st.rerun()

    modele = rapport.Rapport(st.session_state.rapport_titre,
                             "masse_eau" if masse_eau else "departements", figures_rapport)
    st.write("")
    f1, f2 = st.columns(2)
    formats = f1.multiselect("Fichiers à produire", ["PNG", "SVG", "PDF", "PowerPoint", "Word"],
                             default=["PNG", "PowerPoint", "Word"])
    gabarit = f2.file_uploader("Gabarit Word (facultatif)", type=["docx"],
                               help="Un document Word contenant des balises {{ fig_<id> }} là où les "
                                    "figures doivent apparaître. Sans gabarit, un document simple est créé.")
    f2.download_button("Télécharger le gabarit Word d'exemple",
                       (RACINE / "rapports" / "gabarit_exemple.docx").read_bytes(),
                       file_name="gabarit_exemple.docx")

    g1, g2 = st.columns(2)
    g1.download_button("Enregistrer le modèle de rapport (.toml)", rapport.ecrire(modele),
                       file_name="rapport_aquibrie.toml", width="stretch")
    if g2.button("Générer le rapport", type="primary", width="stretch", disabled=not formats):
        correspondance = {"PNG": "png", "SVG": "svg", "PDF": "pdf", "PowerPoint": "pptx", "Word": "docx"}
        barre = st.progress(0.0, "Préparation…")
        with tempfile.TemporaryDirectory() as dossier:
            chemin_gabarit = None
            if gabarit is not None:
                chemin_gabarit = Path(dossier) / "gabarit.docx"
                chemin_gabarit.write_bytes(gabarit.getvalue())
            try:
                res = rapport.generer(modele, Path(dossier) / "sortie", FournisseurStreamlit(departements),
                                      {correspondance[f] for f in formats}, chemin_gabarit,
                                      progression=lambda p, m: barre.progress(min(p, 1.0), m))
            except rapport.RapportError as exc:
                st.error(str(exc))
                res = None
            if res is not None:
                tampon = io.BytesIO()
                with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
                    for chemin in res.fichiers:
                        zf.write(chemin, chemin.name)
                    zf.writestr("rapport_aquibrie.toml", rapport.ecrire(modele))
                st.session_state.rapport_zip = (tampon.getvalue(), len(res.fichiers), res.erreurs,
                                                res.avertissements)
        barre.empty()

    if "rapport_zip" in st.session_state:
        contenu, n_fichiers, erreurs, avertissements = st.session_state.rapport_zip
        st.success(f"{n_fichiers} fichier(s) générés.")
        for ident, msg in avertissements.items():
            st.warning(f"`{ident}` : {msg}")
        for ident, msg in erreurs.items():
            st.error(f"`{ident}` non générée : {msg}")
        st.download_button("Télécharger le rapport (.zip)", contenu, file_name="rapport_aquibrie.zip",
                           type="primary")
    st.caption("En ligne de commande, le même modèle se régénère sans interface : "
               "`python -m aquibrie_viz.rapport rapport_aquibrie.toml --sortie sorties`")


# =============================================================================
# 6. Charte graphique
# =============================================================================
def page_charte() -> None:
    st.subheader("« Strates & eau » — proposition de charte")
    st.markdown(
        "Les deux couleurs du logo portent tout le sens. Le **bleu-vert AQUI** représente l'eau : "
        "c'est la couleur de toutes les données. Le **vert anis Brie** est l'accent : il désigne ce "
        "qui est mis en avant. Les **états** utilisent des terres chaudes, pour ne jamais être "
        "confondus avec l'eau. Le **fond calcaire** rappelle la roche qui porte la nappe."
    )

    def nuancier(couleurs: dict[str, tuple[str, str]], texte_clair: set[str]) -> None:
        cols = st.columns(len(couleurs))
        for col, (nom, (hexa, role)) in zip(cols, couleurs.items(), strict=False):
            encre = "white" if nom in texte_clair else COLORS["abyss"]
            col.markdown(
                f"<div class='ab-swatch' style='background:{hexa};color:{encre}'>"
                f"{nom}<br><span style='font-weight:500;font-size:.85rem'>{hexa} · {role}</span></div>",
                unsafe_allow_html=True,
            )

    st.markdown("##### Couleurs principales")
    nuancier({"AQUI": (COLORS["aqui"], "l'eau, les données"), "Brie": (COLORS["brie"], "l'accent"),
              "Abysse": (COLORS["abyss"], "texte, titres"), "Calcaire": (COLORS["paper"], "fond"),
              "Strate": (COLORS["limestone"], "fond secondaire")}, texte_clair={"AQUI", "Abysse"})
    st.markdown("##### Seuils réglementaires")
    nuancier({nom: (hexa, "seuil") for nom, hexa in STATUS.items()},
             texte_clair={"Alerte", "Alerte renforcée", "Crise"})
    st.markdown("##### Situation par rapport aux années précédentes")
    nuancier({nom: (hexa, "situation") for nom, hexa in SITUATION.items()},
             texte_clair={"Très bas", "Très haut"})
    st.markdown("##### Règles d'usage")
    st.markdown(
        "- **Typographie : Nunito**, police arrondie et libre de droits (licence OFL), proche du logotype.\n"
        "- **Le titre dit le message**, le sous-titre donne le contexte (station, période, seuils).\n"
        "- **Le vert anis ne sert qu'aux pastilles et aux aplats**, jamais au texte ni aux traits fins.\n"
        "- **Terres chaudes = manque d'eau ou dépassement ; bleu-vert = eau.** Jamais l'inverse.\n"
        "- **Signature** : pointillés verts sous le titre, source en pied à gauche, logotype à droite."
    )
    st.caption("Couleurs du logo mesurées sur le document de l'association. Proposition à valider.")


# =============================================================================
# 0. Info Piézo : le bulletin d'AQUI'Brie recalculé à partir de Hub'Eau
# =============================================================================
@st.cache_data(ttl=TTL, show_spinner="Chargement des piézomètres de l'Info Piézo…")
def charger_reseau_info_piezo() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[str]]:
    toutes = charger_stations(departements, None)
    reseau = toutes[toutes["code_bss"].isin(reference.RESEAU_INFO_PIEZO)].copy()
    reseau["commune"] = reseau["code_bss"].map(reference.RESEAU_INFO_PIEZO)
    chroniques, echecs = donnees.chroniques(list(reseau["code_bss"]), HISTORIQUE_DEBUT)
    return reseau.reset_index(drop=True), chroniques, echecs


def page_info_piezo() -> None:
    st.markdown(
        "AQUI'Brie publie tous les deux à trois mois un **Info Piézo** : où en est la nappe du Champigny, "
        "piézomètre par piézomètre. Cette page le recalcule à partir des données publiques Hub'Eau, "
        "avec la même jauge de 0 à 100 que le bulletin."
    )
    try:
        reseau, chroniques, echecs = charger_reseau_info_piezo()
    except hubeau.HubEauError as exc:
        st.error(f"Hub'Eau ne répond pas et aucune donnée n'est en cache. {exc}")
        return
    if reseau.empty:
        st.warning("Aucun piézomètre du réseau Info Piézo n'a été trouvé dans Hub'Eau.")
        return
    if echecs:
        st.warning(f"Hub'Eau n'a pas répondu pour {len(echecs)} piézomètre(s) : ils apparaissent sans mesure.")

    publie = reference.INFO_PIEZO_65
    jour_publie = pd.Timestamp(publie["date"])
    defaut = bulletin.date_situation_commune(chroniques) or jour_publie
    c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
    comparer = c2.toggle("Refaire l'Info Piézo n°65 (13 juin 2026) et comparer aux chiffres publiés",
                         key="ip_comparer")
    jour = pd.Timestamp(c1.date_input("Situation au", defaut.date(), min_value=date(2004, 1, 1),
                                      max_value=date.today(), format="DD/MM/YYYY", key="ip_jour",
                                      disabled=comparer))
    if comparer:
        jour = jour_publie

    synth = bulletin.synthese_remplissage(reseau, chroniques, jour)
    comptes = bulletin.compte_tranches(synth)
    ouest, est = reference.REFERENCE_OUEST, reference.REFERENCE_EST
    seuil_est = reference.SEUILS_CONNUS["BEAUCHERY"]["Vigilance"]

    k1, k2, k3, k4 = st.columns(4)
    mesures = len(synth) - comptes["sans mesure"]
    kpi(k1, "Au-dessus de 50 %", f"{comptes['au-dessus de 50']} / {mesures}", "remplissage satisfaisant ou mieux",
        REMPLISSAGE["Satisfaisant"])
    kpi(k2, "Sous 50 %", f"{comptes['sous 50']} / {mesures}",
        f"dont {comptes['Très faible']} sous 25 %", REMPLISSAGE["Faible"])
    niveau_est = bulletin.niveau_a_date(chroniques.get(est, pd.DataFrame(columns=["date", "niveau"])), jour)
    if niveau_est:
        ecart = niveau_est[0] - seuil_est
        kpi(k3, "Champigny est (Beauchery)", f"{'+' if ecart >= 0 else '−'}{fr_num(abs(ecart), 2)} m",
            f"par rapport au seuil de vigilance ({fr_num(seuil_est, 1)} m NGF)",
            STATUS["Vigilance"] if ecart < 1 else None)
    else:
        kpi(k3, "Champigny est (Beauchery)", "–", "pas de mesure à cette date")
    debut_periode = jour - pd.DateOffset(months=3)
    var_ouest = bulletin.variation(chroniques.get(ouest, pd.DataFrame(columns=["date", "niveau"])),
                                   debut_periode, jour)
    kpi(k4, "Champigny ouest (Montereau)",
        "–" if var_ouest is None else f"{'+' if var_ouest >= 0 else '−'}{fr_num(abs(var_ouest), 2)} m",
        "évolution sur 3 mois")
    if comptes["sans mesure"]:
        st.caption(f"{comptes['sans mesure']} piézomètre(s) sans mesure dans les 15 jours avant cette date : "
                   "le réseau départemental arrive dans Hub'Eau par lots de plusieurs mois.")
    st.write("")

    onglet = st.segmented_control("Vue", ["Jauges", "Carte", "Projection de l'hiver", "Cycles sur 11 ans"],
                                  default="Jauges", key="ip_vue", label_visibility="collapsed") or "Jauges"
    if onglet == "Jauges":
        fig = figure_ou_message(templates.jauges_remplissage, synth, jour)
        if fig is not None:
            afficher(fig, f"info-piezo-jauges-{jour:%Y%m%d}")
        aide("Chaque barre place le niveau du jour entre le plus bas (0) et le plus haut (100) mesurés depuis "
             "2003 sur ce piézomètre. Les tranches sont celles de l'Info Piézo : sous 25 très faible, sous 50 "
             "faible, de 50 à 75 satisfaisant, au-dessus de 75 très satisfaisant.")
    elif onglet == "Carte":
        afficher(templates.carte_remplissage(synth, jour), f"info-piezo-carte-{jour:%Y%m%d}")
    elif onglet == "Projection de l'hiver":
        b = chroniques.get(est)
        proj = bulletin.projection_analogues(b, jour, seuil_est) if b is not None else None
        if proj is None:
            st.info("Pas assez de données à cette date pour projeter le niveau de Beauchery-Saint-Martin.")
        else:
            afficher(templates.projection_analogues(proj, "Beauchery-Saint-Martin (Champigny est)", observe=b),
                     f"info-piezo-projection-{jour:%Y%m%d}")
            premiers = proj.franchissements.dropna().sort_values()
            if not premiers.empty:
                st.markdown(
                    f"Si la nappe se comporte comme en **{premiers.index[0]}**, elle passe sous le seuil de "
                    f"vigilance le **{fr_date(premiers.iloc[0])}**. Comme l'an dernier "
                    f"({jour.year - 1}) : "
                    + (f"le **{fr_date(proj.franchissements[jour.year - 1])}**."
                       if jour.year - 1 in proj.franchissements and pd.notna(proj.franchissements[jour.year - 1])
                       else "pas de franchissement.")
                )
        aide("On part du niveau du jour, puis on applique la baisse (ou la hausse) observée à la même date "
             "chaque année depuis 1990. Chaque fine ligne est une année passée ; la bande contient 8 années sur "
             "10. Ce n'est pas une prévision météo : c'est l'éventail de ce que la nappe a déjà fait. Quand la "
             "date choisie est passée, le trait foncé montre ce qui s'est réellement produit.")
    else:
        colonnes = st.columns(2)
        for col, (code_ref, libelle) in zip(colonnes, [(ouest, "Montereau-sur-le-Jard"),
                                                       (est, "Beauchery-Saint-Martin")], strict=False):
            df = chroniques.get(code_ref)
            if df is None or df.empty:
                col.info(f"Pas de données pour {libelle}.")
                continue
            onze = df[(df["date"] > jour - pd.DateOffset(years=11)) & (df["date"] <= jour)]
            seuils, officiels = reference.seuils_station(libelle, df)
            with col:
                afficher(templates.chronique_seuils(onze, seuils, libelle, seuils_officiels=officiels),
                         f"info-piezo-cycles-{code_ref}-{jour:%Y%m%d}")
        st.caption("Seuils de Montereau-sur-le-Jard : indicatifs tant que les valeurs de l'arrêté-cadre "
                   "n° 2024/DDT/SEPR/191 ne sont pas saisies dans l'outil.")

    if comparer:
        st.subheader("Contrôle : chiffres recalculés et chiffres publiés")
        sous_50 = synth[synth["taux"] < 50]["commune"].tolist()
        retrouves = [c for c in publie["sous_50_communes"] if c in sous_50]
        b = chroniques.get(est)
        debut_64 = pd.Timestamp(publie["debut_periode"])
        baisse_est = -(bulletin.variation(b, debut_64, jour) or 0)
        baisse_ouest = -(bulletin.variation(chroniques[ouest], debut_64, jour) or 0)
        lignes = [
            ("Piézomètres au-dessus de 50 %", str(publie["au_dessus_50"]), str(comptes["au-dessus de 50"])),
            ("Piézomètres sous 50 %", str(publie["sous_50"]), str(comptes["sous 50"])),
            ("Communes sous 50 % citées et retrouvées", str(len(publie["sous_50_communes"])), str(len(retrouves))),
            ("Beauchery : écart au seuil de vigilance", "+4,8 m",
             f"+{fr_num(niveau_est[0] - seuil_est, 2)} m" if niveau_est else "–"),
            ("Beauchery : baisse depuis le n°64", "1,3 m", f"{fr_num(baisse_est, 2)} m"),
            ("Montereau : baisse depuis le n°64", "0,5 m", f"{fr_num(baisse_ouest, 2)} m"),
        ]
        st.dataframe(pd.DataFrame(lignes, columns=["Indicateur", "Info Piézo n°65", "Recalculé ici"]),
                     hide_index=True, width="stretch")
        manquent = [c for c in publie["sous_50_communes"] if c not in retrouves]
        st.markdown(
            f"Communes que le bulletin classe sous 50 % et pas l'outil : **{', '.join(manquent) or 'aucune'}**. "
            "Les écarts viennent surtout de la liste des 26 piézomètres, que le bulletin ne publie pas "
            "(l'outil la reconstitue), et de mesures de terrain qui ne sont pas encore dans Hub'Eau. "
            f"[Lire le bulletin publié]({publie['url']})."
        )


# --- Navigation : seule la page affichée s'exécute -----------------------------

navigation = st.navigation(
    [st.Page(page_info_piezo, title="Info Piézo", url_path="info-piezo", default=True),
     st.Page(page_ensemble, title="Vue d'ensemble", url_path="ensemble"),
     st.Page(page_station, title="Station", url_path="station"),
     st.Page(page_qualite, title="Qualité de l'eau", url_path="qualite"),
     st.Page(page_fiche, title="Fiche grand public", url_path="fiche"),
     st.Page(page_rapport, title="Rapport", url_path="rapport"),
     st.Page(page_charte, title="Charte graphique", url_path="charte")],
    position="top",
)
navigation.run()
