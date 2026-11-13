"""Fiche de sensibilisation grand public, au format A4 portrait.

Une page qui se lit en dix secondes : un message, une image (une goutte par
point de mesure), trois chiffres, une courbe, une explication. Pensée pour être
imprimée, affichée en mairie ou insérée dans une lettre d'information.

Toute la mise en page est exprimée en pixels sur une page de 1240 × 1754
(A4 à 150 dpi ; l'export à l'échelle 2 donne du 300 dpi).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import indicators as ind
from .reference import HABITANTS_ALIMENTES
from .templates import MOIS, SEUIL_ARTICLE, fr_date, fr_num
from .theme import COLORS, FONT, SITUATION, STATUS

LARGEUR, HAUTEUR = 1240, 1754
MARGE = 70

# Goutte d'eau dessinée en pixels autour de son centre (y vers le haut).
GOUTTE = ("M 0 26 C 0 26 17 4 17 -6 C 17 -16 9 -24 0 -24 "
          "C -9 -24 -17 -16 -17 -6 C -17 4 0 26 0 26 Z")


def _px(x: float, y_haut: float) -> tuple[float, float]:
    """Pixel (depuis le coin haut gauche) → coordonnées « paper » de Plotly."""
    return x / LARGEUR, 1 - y_haut / HAUTEUR


class _Page:
    """Petit outil de mise en page : texte, rectangles et gouttes en pixels.

    Les éléments sont accumulés puis posés en une seule fois par `poser()` : Plotly
    revalide toute la mise en page à chaque add_shape/add_annotation, ce qui rendait
    la fiche (≈ 80 éléments) quatre fois plus lente à construire.
    """

    def __init__(self, fig: go.Figure):
        self.fig = fig
        self.annotations: list[dict] = []
        self.shapes: list[dict] = []

    def poser(self) -> None:
        self.fig.update_layout(annotations=list(self.fig.layout.annotations) + self.annotations,
                               shapes=list(self.fig.layout.shapes) + self.shapes)

    def texte(self, x, y, texte, taille=20, couleur=None, gras=False, ancre="left", vertical="top"):
        px, py = _px(x, y)
        self.annotations.append(dict(
            x=px, y=py, xref="paper", yref="paper", xanchor=ancre, yanchor=vertical, showarrow=False,
            align=ancre if ancre != "center" else "center",
            text=f"<b>{texte}</b>" if gras else texte,
            font=dict(family=FONT, size=taille, color=couleur or COLORS["abyss"]),
        ))

    def rect(self, x0, y0, x1, y1, fond, bord=None, epaisseur=0, dessous=True):
        a, b = _px(x0, y0)
        c, d = _px(x1, y1)
        self.shapes.append(dict(type="rect", xref="paper", yref="paper", x0=a, y0=d, x1=c, y1=b,
                           fillcolor=fond, line=dict(color=bord or fond, width=epaisseur),
                           layer="below" if dessous else "above"))

    def ligne_pointillee(self, x0, x1, y, couleur, epaisseur=5):
        a, b = _px(x0, y)
        c, _ = _px(x1, y)
        self.shapes.append(dict(type="line", xref="paper", yref="paper", x0=a, x1=c, y0=b, y1=b,
                           line=dict(color=couleur, width=epaisseur, dash="dot")))

    def goutte(self, x, y, couleur, echelle=1.0, contour="white"):
        px, py = _px(x, y)
        chemin = GOUTTE if echelle == 1 else " ".join(
            str(round(float(t) * echelle, 1)) if t.lstrip("-").replace(".", "").isdigit() else t
            for t in GOUTTE.split())
        self.shapes.append(dict(type="path", path=chemin, xsizemode="pixel", ysizemode="pixel",
                           xref="paper", yref="paper", xanchor=px, yanchor=py,
                           fillcolor=couleur, line=dict(color=contour, width=2)))


def _message_principal(sit: pd.DataFrame) -> tuple[str, str, str]:
    """(chiffre mis en valeur, suite du message, couleur du chiffre)."""
    n, n_bas = len(sit), int(sit["classe"].isin(["Très bas", "Bas"]).sum())
    n_haut = int(sit["classe"].isin(["Haut", "Très haut"]).sum())
    if n_bas:
        return f"{n_bas} point{'s' if n_bas > 1 else ''} de mesure sur {n}", \
            f"{'sont' if n_bas > 1 else 'est'} sous {'leurs' if n_bas > 1 else 'son'} niveau{'x' if n_bas > 1 else ''} habituel{'s' if n_bas > 1 else ''}", \
            SITUATION["Très bas"]
    if n_haut > n / 2:
        return f"{n_haut} points de mesure sur {n}", "sont au-dessus de leurs niveaux habituels", COLORS["aqui_dark"]
    return "La nappe est à son niveau habituel", f"sur les {n} points de mesure comparables", COLORS["aqui_dark"]


def fiche_synthese(
    sit: pd.DataFrame,
    commune: str,
    chronique: pd.DataFrame,
    seuils: dict[str, float],
    seuils_officiels: bool,
    recharge: pd.DataFrame,
    source: str = "Hub'Eau · BRGM, banque ADES",
) -> go.Figure:
    """Fiche A4 « La nappe aujourd'hui ».

    sit : situation de chaque station (indicators.situation_territoire).
    commune, chronique, seuils : la station de référence mise en avant.
    recharge : recharge annuelle de cette station (indicators.recharge_annuelle).
    """
    if sit.empty or chronique.empty:
        raise ValueError("Pas assez de données pour composer la fiche.")

    fig = go.Figure()
    page = _Page(fig)
    date_situation = pd.Timestamp(sit["date"].max())

    # --- En-tête ---------------------------------------------------------
    page.rect(0, 0, LARGEUR, 190, COLORS["abyss"])
    page.texte(MARGE, 52, (f"<span style='color:{COLORS['aqui_light']}'>AQUI'</span>"
                           f"<span style='color:{COLORS['brie']}'>Brie</span>"), taille=46, gras=True)
    page.texte(MARGE, 122, "LA NAPPE DES CALCAIRES DE CHAMPIGNY", taille=20, couleur="white", gras=True)
    page.texte(LARGEUR - MARGE, 122, f"Situation au {fr_date(date_situation)}", taille=20,
               couleur=COLORS["aqui_light"], ancre="right")
    page.ligne_pointillee(MARGE, MARGE + 330, 210, COLORS["brie"])

    # --- Message principal -------------------------------------------------
    chiffre, suite, couleur = _message_principal(sit)
    page.texte(MARGE, 250, chiffre, taille=50, couleur=couleur, gras=True)
    page.texte(MARGE, 318, suite, taille=40, gras=True)
    page.texte(MARGE, 385, "Chaque goutte représente un point de mesure de la nappe. Sa couleur compare "
               "le niveau d'aujourd'hui<br>au niveau relevé le même jour les années précédentes.",
               taille=19, couleur=COLORS["muted"])

    # --- Isotype : une goutte par station ---------------------------------
    ordre = {c: i for i, c in enumerate(ind.ORDRE_CLASSES)}
    gouttes = sit.assign(rang=sit["classe"].map(ordre)).sort_values(["rang", "percentile"])
    par_ligne = 12
    pas = (LARGEUR - 2 * MARGE) / par_ligne
    n_lignes = int(np.ceil(len(gouttes) / par_ligne))
    echelle = 1.35 if n_lignes <= 3 else 1.0
    pas_y = 92 if n_lignes <= 3 else 70
    y0 = 495
    for i, classe in enumerate(gouttes["classe"]):
        ligne, col = divmod(i, par_ligne)
        page.goutte(MARGE + pas * (col + 0.5), y0 + pas_y * ligne, SITUATION[classe], echelle)
    y_legende = y0 + pas_y * n_lignes + 10
    x = MARGE
    for classe in ind.ORDRE_CLASSES:
        n = int((sit["classe"] == classe).sum())
        if n == 0:
            continue
        page.goutte(x + 10, y_legende + 4, SITUATION[classe], 0.62)
        libelle = f"{classe.lower()} ({n})"
        page.texte(x + 28, y_legende - 10, libelle, taille=18)
        x += 48 + 10.5 * len(libelle)

    # --- Trois chiffres clés ----------------------------------------------
    y_cartes, h_cartes = y_legende + 70, 215
    largeur_carte = (LARGEUR - 2 * MARGE - 2 * 30) / 3
    dernier = chronique.dropna(subset=["niveau"]).iloc[-1]
    statut = ind.statut_niveau(dernier["niveau"], seuils)
    if statut:
        etat_seuil, couleur_seuil = f"sous le seuil {SEUIL_ARTICLE[statut]}", STATUS[statut]
    else:
        plus_haut = max(seuils, key=seuils.get)
        etat_seuil = f"au-dessus du seuil {SEUIL_ARTICLE[plus_haut]}"
        couleur_seuil = COLORS["aqui_dark"]
    if not seuils_officiels:
        etat_seuil += " (indicatif)"

    completes = recharge[~recharge["en_cours"]] if not recharge.empty else recharge
    if len(completes) >= 3:
        derniere_r = completes.iloc[-1]
        valeur_r = f"+{fr_num(derniere_r['recharge'], 1)} m" if derniere_r["recharge"] >= 0.05 else "Aucune"
        texte_r = (f"de recharge en {derniere_r['annee_hydro']}<br>contre "
                   f"{fr_num(completes['recharge'].median(), 1)} m une année ordinaire")
    else:
        tendance = ind.tendance_cm_par_jour(chronique)
        valeur_r = "–" if tendance is None else f"{'+' if tendance >= 0 else '−'}{fr_num(abs(tendance), 1)} cm"
        texte_r = "de variation par jour<br>sur le dernier mois"

    cartes = [
        (HABITANTS_ALIMENTES.capitalize(), "de Franciliens boivent l'eau<br>de cette nappe", COLORS["aqui_dark"]),
        (f"{fr_num(dernier['niveau'], 1)} m",
         f"niveau à {commune}<br><span style='color:{couleur_seuil}'><b>{etat_seuil}</b></span>",
         COLORS["abyss"]),
        (valeur_r, texte_r, COLORS["aqui_dark"]),
    ]
    for i, (valeur, texte, couleur_val) in enumerate(cartes):
        x0 = MARGE + i * (largeur_carte + 30)
        page.rect(x0, y_cartes, x0 + largeur_carte, y_cartes + h_cartes, "white", COLORS["grid"], 1.5)
        page.rect(x0, y_cartes, x0 + largeur_carte, y_cartes + 8, COLORS["brie"] if i == 0 else COLORS["aqui"])
        taille_val = 42 if len(valeur) <= 9 else 30
        page.texte(x0 + 28, y_cartes + 45, valeur, taille=taille_val, couleur=couleur_val, gras=True)
        page.texte(x0 + 28, y_cartes + 112, texte, taille=19)

    # --- Mini-courbe : la station de référence sur deux ans ----------------
    y_titre_courbe = y_cartes + h_cartes + 55
    y_courbe0, y_courbe1 = y_titre_courbe + 60, 1490
    page.texte(MARGE, y_titre_courbe, f"Le niveau de la nappe à {commune} depuis deux ans", taille=26, gras=True)
    recent = ind.serie_journaliere(chronique[chronique["date"] >= dernier["date"] - pd.DateOffset(years=2)])
    bas = min(recent["niveau"].min(), min(seuils.values())) - 0.5
    haut = max(recent["niveau"].max(), max(seuils.values())) + 0.5
    x_dom = (MARGE / LARGEUR, (LARGEUR - MARGE - 230) / LARGEUR)
    y_dom = (1 - y_courbe1 / HAUTEUR, 1 - y_courbe0 / HAUTEUR)
    fig.add_trace(go.Scatter(x=recent["date"], y=recent["niveau"], mode="lines", fill="tozeroy",
                             fillcolor="rgba(0,150,158,0.14)", line=dict(color=COLORS["aqui"], width=4),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[dernier["date"]], y=[dernier["niveau"]], mode="markers", showlegend=False,
                             marker=dict(size=20, color=COLORS["brie"], line=dict(color=COLORS["abyss"], width=3))))
    for nom, valeur in seuils.items():
        page.shapes.append(dict(type="line", xref="x domain", yref="y", x0=0, x1=1, y0=valeur, y1=valeur,
                                line=dict(color=STATUS[nom], width=2, dash="dash")))
        page.annotations.append(dict(xref="x domain", yref="y", x=1, y=valeur, xanchor="left", xshift=12, showarrow=False,
                           text=f"<b>{nom}</b>", font=dict(family=FONT, size=16, color="white" if nom != "Vigilance" else COLORS["abyss"]),
                           bgcolor=STATUS[nom], borderpad=4))
    debut_axe = recent["date"].min()
    ticks = pd.date_range(debut_axe.normalize() + pd.offsets.MonthBegin(1), dernier["date"], freq="6MS")
    fig.update_layout(
        xaxis=dict(domain=x_dom, showgrid=False, showline=True, linecolor=COLORS["grid"],
                   tickvals=ticks, ticktext=[f"{MOIS[t.month - 1]} {t.year}" for t in ticks],
                   tickfont=dict(family=FONT, size=16, color=COLORS["muted"]), fixedrange=True,
                   automargin=False),
        yaxis=dict(domain=y_dom, range=[bas, haut], gridcolor=COLORS["grid"], griddash="dot",
                   ticksuffix=" m", tickfont=dict(family=FONT, size=16, color=COLORS["muted"]),
                   zeroline=False, fixedrange=True, automargin=False),
    )

    # --- Comprendre --------------------------------------------------------
    page.rect(MARGE, 1545, LARGEUR - MARGE, 1680, COLORS["limestone"])
    page.texte(MARGE + 30, 1565, "Comprendre", taille=22, gras=True, couleur=COLORS["aqui_dark"])
    page.texte(MARGE + 30, 1602,
               "La nappe se recharge en hiver grâce aux pluies qui s'infiltrent, puis baisse au printemps et en été.<br>"
               "Lorsqu'elle passe sous un seuil, des restrictions d'usage de l'eau peuvent s'appliquer :<br>"
               "<b>retrouvez celles en vigueur près de chez vous sur vigieau.gouv.fr</b>.",
               taille=18)

    # --- Pied de page ------------------------------------------------------
    page.texte(MARGE, 1712, f"Source : {source} · fiche générée le {fr_date(pd.Timestamp.today())}",
               taille=15, couleur=COLORS["muted"])
    page.texte(LARGEUR - MARGE, 1700, (f"<span style='color:{COLORS['aqui']}'>AQUI'</span>"
                                        f"<span style='color:{COLORS['brie']}'>Brie</span>"),
               taille=28, gras=True, ancre="right")

    page.poser()
    fig.update_layout(
        width=LARGEUR, height=HAUTEUR, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=COLORS["paper"], plot_bgcolor=COLORS["paper"],
        font=dict(family=FONT, color=COLORS["abyss"]), showlegend=False,
        meta={"titre": f"La nappe aujourd'hui : {chiffre} {suite}"},  # pas de titre affiché, mais nommé
    )
    return fig
