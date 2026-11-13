"""Charte graphique « Strates & eau » — proposition pour AQUI'Brie.

Parti pris : les deux couleurs du logo portent tout le sens.
- Le bleu-vert « AQUI » représente l'eau : c'est la couleur de toutes les données.
- Le vert anis « Brie » est l'accent : il désigne ce qui est mis en avant
  (station choisie, année en cours, dernière valeur).
- Les états réglementaires utilisent une gamme de terres chaudes (sable → brique),
  pour ne jamais être confondus avec l'eau.
- Le fond « calcaire » rappelle la roche qui porte la nappe.
- Signature : la ligne pointillée verte du papier à en-tête, sous chaque titre,
  et le logotype AQUI'Brie en pied de figure.

Les couleurs du logo ont été mesurées sur le document fourni par l'association.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

FONT = "Nunito, 'Segoe UI', Arial, sans-serif"

COLORS = {
    # Couleurs du logo (mesurées)
    "aqui": "#00969E",        # bleu-vert « AQUI » : l'eau, les données
    "brie": "#BBCD0E",        # vert anis « Brie » : l'accent
    # Déclinaisons
    "abyss": "#0B3B40",       # bleu-vert très profond : texte, titres
    "aqui_dark": "#00737A",
    "aqui_light": "#9FD4D6",
    "aqui_mist": "#E3F2F1",
    "brie_dark": "#8A9A00",
    # Neutres « calcaire »
    "paper": "#FBFAF5",
    "limestone": "#EFEBDF",
    "grid": "#E6E1D3",
    "muted": "#6E7A78",
}

# États réglementaires : terres chaudes, du sable à la brique.
STATUS = {
    "Vigilance": "#D9B54A",
    "Alerte": "#E08A3C",
    "Alerte renforcée": "#C4513A",
    "Crise": "#7D1D2E",
}

# Situation par rapport aux années précédentes : des terres (manque d'eau)
# vers le bleu-vert (eau abondante), avec un neutre « calcaire » au centre.
SITUATION = {
    "Très bas": "#9C3D22",
    "Bas": "#D9A05B",
    "Proche de la normale": "#C8CDBF",
    "Haut": "#6FBFC2",
    "Très haut": "#00737A",
}

# Qualité de l'eau : du neutre (jamais quantifié) à la brique (dépassement).
QUALITE = {
    "Jamais quantifié": "#B9C4C1",
    "Détecté, sous la norme": "#D9B54A",
    "Dépassement de la norme": "#C4513A",
}

TEMPLATE_NAME = "aquibrie"

HAUTEUR_DEFAUT = 640

pio.templates[TEMPLATE_NAME] = go.layout.Template(
    layout=go.Layout(
        font=dict(family=FONT, size=16, color=COLORS["abyss"]),
        # Titre aligné sur la zone de tracé, comme la ligne pointillée de la signature.
        # Position en pixels depuis le haut (et non en fraction de la hauteur) : sinon, dans les
        # figures hautes, le titre descend et la ligne pointillée traverse le sous-titre.
        # (apply_signature fixe y selon la hauteur réelle de la figure.)
        title=dict(font=dict(size=26, family=FONT, weight=800), xref="paper", x=0, xanchor="left",
                   yref="container", yanchor="top"),
        height=HAUTEUR_DEFAUT,
        paper_bgcolor=COLORS["paper"],
        plot_bgcolor=COLORS["paper"],
        colorway=[COLORS["aqui"], COLORS["brie_dark"], COLORS["aqui_light"], COLORS["muted"]],
        margin=dict(l=80, r=200, t=145, b=90),
        xaxis=dict(showgrid=False, showline=True, linecolor=COLORS["grid"], linewidth=1.5,
                   ticks="outside", tickcolor=COLORS["grid"], ticklen=6,
                   tickfont=dict(color=COLORS["muted"], size=15)),
        yaxis=dict(gridcolor=COLORS["grid"], griddash="dot", zeroline=False,
                   tickfont=dict(color=COLORS["muted"], size=15),
                   title=dict(font=dict(color=COLORS["muted"], size=15))),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, bgcolor="rgba(0,0,0,0)",
                    font=dict(size=14, color=COLORS["abyss"])),
        hoverlabel=dict(font_family=FONT, bgcolor="white", bordercolor=COLORS["aqui"]),
    )
)


def title_with_subtitle(title: str, subtitle: str) -> str:
    """Titre = le message ; sous-titre = le contexte (station, période)."""
    return (
        f"{title}<br>"
        f"<span style='font-size:16px;font-weight:500;color:{COLORS['muted']}'>{subtitle}</span>"
    )


def apply_signature(fig: go.Figure, source: str) -> go.Figure:
    """Ajoute la signature de la charte : pointillés verts sous le titre,
    source en pied à gauche, logotype AQUI'Brie en pied à droite."""
    # Hauteur figée (écran = export) et titre à 30 px du haut quelle que soit cette hauteur.
    hauteur = fig.layout.height or HAUTEUR_DEFAUT
    fig.update_layout(height=hauteur, title_y=1 - 30 / hauteur)
    # Ligne positionnée en pixels au-dessus de la zone de tracé, entre sous-titre et légende.
    fig.add_shape(
        type="line", xref="paper", yref="paper", ysizemode="pixel", yanchor=1,
        x0=0, x1=0.32, y0=56, y1=56,
        line=dict(color=COLORS["brie"], width=4, dash="dot"), layer="above",
    )
    # Le pied de figure se cale sur les marges réelles de la figure (elles varient selon le modèle).
    gabarit = pio.templates[TEMPLATE_NAME].layout.margin
    marge_bas = fig.layout.margin.b if fig.layout.margin.b is not None else gabarit.b
    marge_droite = fig.layout.margin.r if fig.layout.margin.r is not None else gabarit.r
    fig.add_annotation(
        text=f"Source : {source}", xref="paper", yref="paper", x=0, y=0, xanchor="left",
        yanchor="bottom", yshift=-(marge_bas - 12), showarrow=False,
        font=dict(size=13, color=COLORS["muted"]),
    )
    fig.add_annotation(
        text=(f"<b><span style='color:{COLORS['aqui']}'>AQUI'</span>"
              f"<span style='color:{COLORS['brie']}'>Brie</span></b>"),
        xref="paper", yref="paper", x=1, y=0, xanchor="right", yanchor="bottom",
        xshift=marge_droite - 16, yshift=-(marge_bas - 8), showarrow=False, font=dict(size=20),
    )
    return fig
