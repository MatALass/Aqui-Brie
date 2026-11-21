"""Modèles de figures. Chaque modèle prend des données normalisées et renvoie une
figure Plotly à la charte, avec un titre qui énonce le message.

Niveaux : chronique_seuils, annee_vs_historique, recharge_annuelle,
          calendrier_anomalies, situation_stations, carte_situation
Qualité : concentration_norme, carte_qualite, qualite_annuelle
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import indicators as ind
from .theme import COLORS, QUALITE, REMPLISSAGE, SITUATION, STATUS, TEMPLATE_NAME, apply_signature, title_with_subtitle

SOURCE_HUBEAU = "Hub'Eau · BRGM, banque ADES"

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc."]

SEUIL_ARTICLE = {
    "Vigilance": "de vigilance",
    "Alerte": "d'alerte",
    "Alerte renforcée": "d'alerte renforcée",
    "Crise": "de crise",
}

# Réexportés pour compatibilité : la logique vit dans indicators.
statut_niveau = ind.statut_niveau
tendance_cm_par_jour = ind.tendance_cm_par_jour


def fr_date(ts: pd.Timestamp) -> str:
    return f"{ts.day} {MOIS[ts.month - 1]} {ts.year}"


def fr_num(x: float, decimals: int = 2) -> str:
    return f"{x:,.{decimals}f}".replace(",", " ").replace(".", ",")


def _metres(ecart_m: float) -> str:
    """0,42 m → « 42 cm » ; 1,37 m → « 1,37 m »."""
    return f"{fr_num(abs(ecart_m), 2)} m" if abs(ecart_m) >= 1 else f"{fr_num(abs(ecart_m) * 100, 0)} cm"


def _accent_point(fig: go.Figure, x, y, texte: str, xanchor: str = "right") -> None:
    """Point de mise en avant : pastille vert anis cerclée, étiquette sombre."""
    fig.add_trace(go.Scatter(
        x=[x], y=[y], mode="markers", showlegend=False, hoverinfo="skip",
        marker=dict(size=17, color=COLORS["brie"], line=dict(color=COLORS["abyss"], width=2.5)),
    ))
    fig.add_annotation(
        x=x, y=y, text=texte, showarrow=False, xanchor=xanchor, yanchor="bottom",
        xshift=-12 if xanchor == "right" else 12, yshift=10,
        align="right" if xanchor == "right" else "left",
        font=dict(color=COLORS["abyss"], size=16), bgcolor="rgba(251,250,245,0.9)", borderpad=3,
    )


def _etiquette_droite(fig: go.Figure, y: float, texte: str, couleur: str, encre: str = "white") -> None:
    fig.add_annotation(
        xref="paper", x=1, y=y, xanchor="left", xshift=10, showarrow=False, text=texte,
        font=dict(size=14, color=encre), bgcolor=couleur, borderpad=4,
    )


# ---------------------------------------------------------------------------
# 1. Chronique piézométrique avec seuils et projection
# ---------------------------------------------------------------------------

def chronique_seuils(
    df: pd.DataFrame, seuils: dict[str, float], station: str,
    source: str = SOURCE_HUBEAU, seuils_officiels: bool = False,
    projection: ind.Projection | None = None,
) -> go.Figure:
    serie = ind.serie_journaliere(df)  # les longues lacunes restent visibles (trait interrompu)
    dernier = df.iloc[-1]
    statut = ind.statut_niveau(dernier["niveau"], seuils)
    tendance = ind.tendance_cm_par_jour(df)

    titre = (f"La nappe est sous le seuil {SEUIL_ARTICLE[statut]}" if statut
             else "La nappe est au-dessus de tous les seuils")
    sens = ""
    if tendance is not None:
        sens = f" · {'+' if tendance >= 0 else '−'}{fr_num(abs(tendance), 1)} cm/jour sur 30 jours"
    nature = "seuils réglementaires" if seuils_officiels else "seuils indicatifs"
    sous_titre = f"{station} · {nature}{sens}"

    y_bas = min(df["niveau"].min(), min(seuils.values())) - 0.6
    y_haut = max(df["niveau"].max(), max(seuils.values())) + 0.6

    fig = go.Figure()
    # L'eau « remplit » le graphique depuis le bas : la nappe vue comme un volume.
    # tozeroy s'interrompt aux lacunes comme la courbe ; l'axe, borné, coupe le remplissage au bas du cadre.
    fig.add_trace(go.Scatter(
        x=serie["date"], y=serie["niveau"], mode="lines", name="Niveau", fill="tozeroy",
        fillcolor="rgba(0,150,158,0.10)", line=dict(color=COLORS["aqui"], width=2.4),
        connectgaps=False, hovertemplate="%{x|%d/%m/%Y} · %{y:.2f} m NGF<extra></extra>",
    ))

    for nom, valeur in sorted(seuils.items(), key=lambda kv: -kv[1]):
        fig.add_hline(y=valeur, line=dict(color=STATUS[nom], width=1.4, dash="dash"))
        _etiquette_droite(fig, valeur, f"<b>{nom}</b> {fr_num(valeur, 1)} m", STATUS[nom],
                          encre=COLORS["abyss"] if nom == "Vigilance" else "white")

    if projection is not None:
        couleur = STATUS[projection.seuil]
        fig.add_trace(go.Scatter(
            x=[dernier["date"], projection.date], y=[dernier["niveau"], projection.valeur_seuil],
            mode="lines+markers", showlegend=False, hoverinfo="skip",
            line=dict(color=couleur, width=2.4, dash="dot"),
            marker=dict(size=[0, 10], color=couleur, symbol="diamond"),
        ))
        fig.add_annotation(
            x=projection.date, y=projection.valeur_seuil, yanchor="top", yshift=-10, showarrow=False,
            text=(f"<b>Si la baisse continue</b> ({fr_num(projection.pente_cm_j, 1)} cm/j)<br>"
                  f"seuil {SEUIL_ARTICLE[projection.seuil]} vers le {fr_date(projection.date)}"),
            font=dict(size=13, color=couleur), bgcolor="rgba(251,250,245,0.9)", align="center",
        )

    _accent_point(
        fig, dernier["date"], dernier["niveau"],
        f"<b>{fr_num(dernier['niveau'])} m</b><br>"
        f"<span style='font-size:13px;color:{COLORS['muted']}'>{fr_date(dernier['date'])}</span>",
    )
    fig.update_layout(
        template=TEMPLATE_NAME,
        title=title_with_subtitle(titre, sous_titre),
        yaxis=dict(title="Altitude de la nappe (m NGF)", range=[y_bas, y_haut]),
        showlegend=False,
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 2. Année choisie face à l'historique
# ---------------------------------------------------------------------------

def annee_vs_historique(
    df: pd.DataFrame, annee: int, station: str, source: str = SOURCE_HUBEAU, min_annees: int = 3
) -> go.Figure:
    d = ind.serie_journaliere(df)
    d = d.assign(annee=d["date"].dt.year, jour=d["date"].dt.day_of_year)
    d = d[d["jour"] <= 365]
    histo = d[(d["annee"] != annee)].dropna(subset=["niveau"])
    courante = d[d["annee"] == annee]
    if histo["annee"].nunique() < min_annees or courante["niveau"].notna().sum() == 0:
        raise ValueError(f"Il faut au moins {min_annees} années d'historique et des mesures sur {annee}.")

    # Bande des percentiles 10–90 (« 8 années sur 10 ») plutôt que min–max : sur un long
    # historique, une seule année exceptionnelle élargirait la bande au point d'écraser la courbe.
    g = histo.groupby("jour")["niveau"]
    env = pd.DataFrame({"min": g.quantile(0.1), "median": g.median(), "max": g.quantile(0.9),
                        "n": g.count()})
    env = env.reindex(range(1, 366))
    env.loc[env["n"] < min_annees, ["min", "median", "max"]] = np.nan  # pas assez d'années ce jour-là
    # Lissage sur 7 jours : l'enveloppe ne doit pas sauter quand une station change de fréquence de mesure.
    env[["min", "median", "max"]] = env[["min", "median", "max"]].rolling(7, center=True, min_periods=3).mean()
    ref = pd.Timestamp(year=2001, month=1, day=1)  # année non bissextile servant d'axe commun
    env["x"] = ref + pd.to_timedelta(env.index - 1, unit="D")
    courante = courante.assign(x=ref + pd.to_timedelta(courante["jour"] - 1, unit="D"))

    dernier = courante.dropna(subset=["niveau"]).iloc[-1]
    mediane_jour = env.loc[int(dernier["jour"]), "median"]
    a0, a1 = histo["annee"].min(), histo["annee"].max()
    if np.isnan(mediane_jour) or abs(dernier["niveau"] - mediane_jour) < 0.05:
        titre = f"En {annee}, la nappe est proche de la normale"
    else:
        ecart = dernier["niveau"] - mediane_jour
        titre = f"En {annee}, la nappe est {_metres(ecart)} {'au-dessus' if ecart > 0 else 'en dessous'} de la médiane"
    sous_titre = f"{station} · comparaison avec {histo['annee'].nunique()} années ({a0}–{a1})"

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=env["x"], y=env["max"], mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=env["x"], y=env["min"], mode="lines", line=dict(width=0), fill="tonexty",
        fillcolor="rgba(0,150,158,0.13)", name=f"8 années sur 10 ({a0}–{a1})", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=env["x"], y=env["median"], mode="lines", name="Médiane",
        line=dict(color=COLORS["aqui"], width=1.8, dash="dot"),
        hovertemplate="Médiane · %{y:.2f} m<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=courante["x"], y=courante["niveau"], mode="lines", name=str(annee), connectgaps=False,
        line=dict(color=COLORS["abyss"], width=3.4),
        hovertemplate="%{x|%d/%m} · %{y:.2f} m<extra></extra>",
    ))
    _accent_point(fig, dernier["x"], dernier["niveau"], f"<b>{fr_num(dernier['niveau'])} m</b>",
                  xanchor="left")
    fig.update_layout(
        template=TEMPLATE_NAME,
        title=title_with_subtitle(titre, sous_titre),
        yaxis_title="Altitude de la nappe (m NGF)",
        xaxis=dict(tickvals=[ref + pd.DateOffset(months=m) for m in range(12)], ticktext=MOIS),
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 3. Recharge par année hydrologique
# ---------------------------------------------------------------------------

def recharge_annuelle(rech: pd.DataFrame, station: str, source: str = SOURCE_HUBEAU) -> go.Figure:
    if rech.empty:
        raise ValueError("Pas assez de données pour calculer la recharge.")
    completes = rech[~rech["en_cours"]]
    mediane = float(completes["recharge"].median()) if not completes.empty else float("nan")
    derniere = rech.iloc[-1]
    if derniere["en_cours"] and derniere["recharge"] < 0.05:
        titre = f"Recharge {derniere['annee_hydro']} : la nappe n'a pas encore commencé à remonter"
    elif derniere["en_cours"]:
        titre = f"Recharge {derniere['annee_hydro']} en cours : +{_metres(derniere['recharge'])} à ce jour"
    elif derniere["recharge"] < 0.05:
        titre = f"Aucune recharge en {derniere['annee_hydro']} : la nappe n'est pas remontée"
    elif not np.isnan(mediane):
        comparaison = "supérieure" if derniere["recharge"] > mediane else "inférieure"
        titre = (f"Recharge {derniere['annee_hydro']} : +{_metres(derniere['recharge'])}, "
                 f"{comparaison} à la médiane")
    else:
        titre = f"Recharge {derniere['annee_hydro']} : +{_metres(derniere['recharge'])}"
    sous_titre = f"{station} · remontée annuelle du niveau (année hydrologique sept. → août)"

    couleurs = [COLORS["aqui_light"] if c else COLORS["aqui"] for c in rech["en_cours"]]
    fig = go.Figure(go.Bar(
        x=rech["annee_hydro"], y=rech["recharge"], marker=dict(color=couleurs, cornerradius=6),
        customdata=np.stack([rech["bas"], rech["haut"]], axis=1),
        hovertemplate=("%{x} · recharge %{y:.2f} m<br>du plus bas (%{customdata[0]:.2f} m) "
                       "au plus haut (%{customdata[1]:.2f} m)<extra></extra>"),
    ))
    if not np.isnan(mediane):
        fig.add_hline(y=mediane, line=dict(color=COLORS["abyss"], width=1.4, dash="dot"))
        _etiquette_droite(fig, mediane, f"<b>Médiane</b> {fr_num(mediane, 2)} m", COLORS["abyss"])
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre),
        yaxis_title="Recharge (m)", showlegend=False, bargap=0.25,
        xaxis=dict(type="category"),
    )
    if derniere["en_cours"]:
        fig.add_annotation(x=derniere["annee_hydro"], y=derniere["recharge"], yanchor="bottom",
                           text="en cours", showarrow=False, font=dict(size=13, color=COLORS["muted"]))
    # Une recharge nulle est une information (la nappe n'est pas remontée) : on l'écrit.
    for _, ligne in rech[(rech["recharge"] < 0.05) & ~rech["en_cours"]].iterrows():
        fig.add_annotation(x=ligne["annee_hydro"], y=0, yanchor="bottom", yshift=4, showarrow=False,
                           text="aucune", font=dict(size=13, color=STATUS["Alerte renforcée"]))
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 4. Calendrier des anomalies mensuelles
# ---------------------------------------------------------------------------

ECHELLE_ANOMALIE = [
    [0.0, SITUATION["Très bas"]], [0.3, SITUATION["Bas"]], [0.5, "#F1EEE4"],
    [0.7, SITUATION["Haut"]], [1.0, SITUATION["Très haut"]],
]


def calendrier_anomalies(anom: pd.DataFrame, station: str, source: str = SOURCE_HUBEAU) -> go.Figure:
    if anom.empty:
        raise ValueError("Historique trop court pour calculer des anomalies mensuelles.")
    valeurs = anom.stack()
    recent = valeurs.dropna()
    # Combien de mois consécutifs, en remontant depuis le dernier, sont sous la médiane ?
    signes = np.sign(recent.to_numpy()[::-1])
    serie = int(np.argmax(signes != signes[0])) if (signes != signes[0]).any() else len(signes)
    position = "sous" if signes[0] < 0 else "au-dessus de"
    titre = (f"Depuis {serie} mois, la nappe est {position} ses niveaux habituels" if serie > 1
             else "Le dernier mois est " + ("sous" if signes[0] < 0 else "au-dessus de") + " la normale")
    sous_titre = f"{station} · écart du niveau moyen mensuel à la médiane du même mois"

    borne = float(np.nanpercentile(np.abs(anom.to_numpy()), 95)) or 1.0
    texte = anom.map(lambda v: "" if pd.isna(v) else f"{'+' if v > 0 else ''}{v:.1f}".replace(".", ","))
    fig = go.Figure(go.Heatmap(
        z=anom.to_numpy(), x=MOIS, y=[str(a) for a in anom.index],
        text=texte.to_numpy(), texttemplate="%{text}", textfont=dict(size=11),
        colorscale=ECHELLE_ANOMALIE, zmid=0, zmin=-borne, zmax=borne, xgap=3, ygap=3,
        colorbar=dict(title=dict(text="écart (m)", side="top"), thickness=14, len=0.7,
                      outlinewidth=0, tickfont=dict(size=13)),
        hovertemplate="%{x} %{y} · écart %{z:.2f} m<extra></extra>",
    ))
    hauteur = max(520, 26 * len(anom) + 260)
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre), height=hauteur,
        yaxis=dict(autorange="reversed", showgrid=False, type="category", tickfont=dict(size=13)),
        xaxis=dict(side="top", showline=False, ticks=""),
        plot_bgcolor=COLORS["paper"],
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 5. Situation de toutes les stations
# ---------------------------------------------------------------------------

def situation_stations(sit: pd.DataFrame, source: str = SOURCE_HUBEAU) -> go.Figure:
    if sit.empty:
        raise ValueError("Aucune station n'a assez d'historique.")
    n_bas = int(sit["classe"].isin(["Très bas", "Bas"]).sum())
    titre = (f"{n_bas} station{'s' if n_bas > 1 else ''} sur {len(sit)} sous leurs niveaux habituels" if n_bas
             else f"Aucune des {len(sit)} stations n'est sous ses niveaux habituels")
    sous_titre = ("Rang du dernier niveau mesuré parmi le même jour des années précédentes "
                  "(0 % = plus bas jamais observé, 100 % = plus haut)")

    s = sit.sort_values("percentile", ascending=True)
    fig = go.Figure()
    bornes = [0] + [b for b, _ in ind.CLASSES_SITUATION[:-1]] + [100]
    for (x0, x1), classe in zip(zip(bornes[:-1], bornes[1:], strict=False), ind.ORDRE_CLASSES, strict=False):
        fig.add_vrect(x0=x0, x1=x1, fillcolor=SITUATION[classe], opacity=0.16, line_width=0, layer="below")
        fig.add_annotation(x=(x0 + x1) / 2, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=classe, font=dict(size=13, color=COLORS["muted"]))
    fig.add_trace(go.Scatter(
        x=s["percentile"], y=s["commune"], mode="markers",
        marker=dict(size=16, color=[SITUATION[c] for c in s["classe"]],
                    line=dict(color=COLORS["abyss"], width=1.2)),
        customdata=np.stack([s["valeur"], s["mediane"], s["n_annees"],
                             s["date"].dt.strftime("%d/%m/%Y")], axis=1),
        hovertemplate=("<b>%{y}</b><br>%{x:.0f} % des années précédentes étaient plus basses<br>"
                       "niveau %{customdata[0]:.2f} m (médiane %{customdata[1]:.2f} m)<br>"
                       "%{customdata[2]} années comparées · mesure du %{customdata[3]}<extra></extra>"),
    ))
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre), showlegend=False,
        height=max(520, 30 * len(s) + 260), margin=dict(l=190),
        xaxis=dict(range=[-2, 102], showgrid=False, tickvals=[0, 10, 30, 50, 70, 90, 100],
                   ticksuffix=" %"),
        yaxis=dict(type="category", showgrid=True, tickfont=dict(size=14)),
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 6. Cartes
# ---------------------------------------------------------------------------

def _cadrage(lon: pd.Series, lat: pd.Series, largeur_px: int = 1000, hauteur_px: int = 470) -> dict:
    """Centre et zoom qui englobent toutes les stations avec une marge.

    MapLibre (moteur des cartes Plotly) découpe le monde en tuiles de 512 px ; en projection
    Mercator, un degré de latitude occupe 1/cos(latitude) fois plus de place qu'un degré de
    longitude. Les dimensions en pixels sont celles, prudentes, de la zone de carte.
    """
    if lon.empty:
        return dict(center=dict(lon=2.95, lat=48.6), zoom=8)
    centre_lat = (lat.max() + lat.min()) / 2
    etendue_lon = max(lon.max() - lon.min(), 0.05) * 1.3
    etendue_lat = max(lat.max() - lat.min(), 0.05) * 1.3
    zoom_lon = math.log2(largeur_px * 360 / (512 * etendue_lon))
    zoom_lat = math.log2(hauteur_px * 360 * math.cos(math.radians(centre_lat)) / (512 * etendue_lat))
    return dict(center=dict(lon=float((lon.max() + lon.min()) / 2), lat=float(centre_lat)),
                zoom=round(min(zoom_lon, zoom_lat), 2))


def _points_cartes(fig: go.Figure, df: pd.DataFrame, couleur: str, nom: str, taille: int,
                   survol: str, customdata=None) -> None:
    """Pastille colorée cerclée de blanc (deux traces, Scattermap n'ayant pas de contour)."""
    if df.empty:
        return
    fig.add_trace(go.Scattermap(lon=df["lon"], lat=df["lat"], mode="markers", hoverinfo="skip",
                                showlegend=False, marker=dict(size=taille + 5, color="white")))
    fig.add_trace(go.Scattermap(
        lon=df["lon"], lat=df["lat"], mode="markers", name=nom, text=df["libelle"],
        customdata=customdata, marker=dict(size=taille, color=couleur), hovertemplate=survol,
    ))


def _mise_en_page_carte(fig: go.Figure, titre: str, sous_titre: str, lon, lat, source: str) -> go.Figure:
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre),
        map=dict(style="carto-positron", **_cadrage(lon, lat)),
        margin=dict(l=20, r=20, t=145, b=90), height=720,
        legend=dict(orientation="h", y=1.01, x=0, font=dict(size=15)),
    )
    return apply_signature(fig, source)


def carte_situation(sit: pd.DataFrame, stations: pd.DataFrame, selection: str | None = None,
                    source: str = SOURCE_HUBEAU) -> go.Figure:
    """Stations colorées selon leur situation ; en gris celles sans historique suffisant."""
    base = stations.dropna(subset=["lon", "lat"]).assign(libelle=lambda d: d["commune"])
    sans = base[~base["code_bss"].isin(sit["code_bss"])]
    fig = go.Figure()
    _points_cartes(fig, sans, COLORS["muted"], "Historique insuffisant", 12,
                   "<b>%{text}</b><br>historique insuffisant<extra></extra>")
    for classe in ind.ORDRE_CLASSES:
        groupe = sit[sit["classe"] == classe].assign(libelle=lambda d: d["commune"])
        _points_cartes(fig, groupe, SITUATION[classe], classe, 17,
                       "<b>%{text}</b><br>" + classe + " · %{customdata:.2f} m NGF<extra></extra>",
                       customdata=groupe["valeur"])
    choisie = base[base["code_bss"] == selection]
    if not choisie.empty:
        fig.add_trace(go.Scattermap(
            lon=choisie["lon"], lat=choisie["lat"], mode="markers+text", text=choisie["commune"],
            textposition="top right", textfont=dict(size=15, color=COLORS["abyss"]),
            marker=dict(size=30, color=COLORS["brie"], opacity=0.55), name="Station choisie",
            hoverinfo="skip",
        ))
    n_bas = int(sit["classe"].isin(["Très bas", "Bas"]).sum())
    if not len(sit):
        titre = f"{len(base)} piézomètres suivis"
    elif n_bas:
        titre = f"{n_bas} piézomètre{'s' if n_bas > 1 else ''} sur {len(sit)} sous les niveaux habituels"
    else:
        titre = f"Aucun des {len(sit)} piézomètres n'est sous les niveaux habituels"
    return _mise_en_page_carte(fig, titre, "Situation au dernier relevé, comparée au même jour des "
                               "années précédentes", base["lon"], base["lat"], source)


def carte_qualite(synth: pd.DataFrame, parametre: str, unite: str, source: str = SOURCE_HUBEAU) -> go.Figure:
    s = synth.dropna(subset=["lon", "lat"]).assign(libelle=lambda d: d["commune"])
    fig = go.Figure()
    for categorie in QUALITE:
        groupe = s[s["categorie"] == categorie]
        taille = 13 if categorie == "Jamais quantifié" else 17
        _points_cartes(
            fig, groupe, QUALITE[categorie], categorie, taille,
            ("<b>%{text}</b><br>%{customdata[0]} analyses · max %{customdata[1]} " + unite
             + "<br>%{customdata[2]} dépassement(s)<extra></extra>"),
            customdata=np.stack([groupe["n"],
                                 groupe["maximum"].round(3).astype(object).where(groupe["maximum"].notna(), "–"),
                                 groupe["n_depassements"]], axis=1) if not groupe.empty else None,
        )
    n_dep = int((s["n_depassements"] > 0).sum())
    if n_dep:
        titre = (f"{parametre} : {n_dep} station{'s' if n_dep > 1 else ''} sur {len(s)} "
                 f"{'ont' if n_dep > 1 else 'a'} dépassé la norme")
    else:
        titre = f"{parametre} : aucune des {len(s)} stations n'a dépassé la norme"
    return _mise_en_page_carte(fig, titre, "Stations de suivi de la qualité des eaux souterraines, "
                               "sur la période choisie", s["lon"], s["lat"], source)


# ---------------------------------------------------------------------------
# 7. Concentration d'un paramètre face à sa norme (une station)
# ---------------------------------------------------------------------------

def concentration_norme(
    df: pd.DataFrame, norme: float, parametre: str, unite: str, station: str,
    source: str = SOURCE_HUBEAU,
) -> go.Figure:
    quanti = df[df["quantifie"] & df["resultat"].notna()]
    non_quanti = df[~df["quantifie"]]
    depassements = int((quanti["resultat"] > norme).sum())
    total = len(df)
    pluriel = lambda n: "s" if n > 1 else ""  # noqa: E731

    if depassements:
        titre = f"{parametre} : {depassements} analyse{pluriel(depassements)} sur {total} au-dessus de la norme"
    elif quanti.empty:
        titre = f"{parametre} : jamais quantifié sur {total} analyse{pluriel(total)}"
    else:
        titre = f"{parametre} : aucune des {total} analyses ne dépasse la norme"
    sous_titre = (f"{station} · norme {fr_num(norme, 2)} {unite} · "
                  f"{len(non_quanti)} résultat{pluriel(len(non_quanti))} sous la limite de quantification")

    sommet = quanti["resultat"].max() if not quanti.empty else 0
    y_max = max(sommet, norme) * 1.18
    fig = go.Figure()
    fig.add_hrect(y0=norme, y1=y_max, fillcolor=STATUS["Alerte renforcée"], opacity=0.06, line_width=0)
    fig.add_trace(go.Scatter(
        x=quanti["date"], y=quanti["resultat"], mode="lines+markers", name="Quantifié",
        line=dict(color=COLORS["aqui_light"], width=1.2),
        marker=dict(size=10, color=np.where(quanti["resultat"] > norme, STATUS["Alerte renforcée"],
                                            COLORS["aqui"]),
                    line=dict(color=COLORS["paper"], width=1.5)),
        hovertemplate="%{x|%d/%m/%Y} · %{y} " + unite + "<extra></extra>",
    ))
    if not non_quanti.empty:
        fig.add_trace(go.Scatter(
            x=non_quanti["date"], y=non_quanti["lq"].fillna(0), mode="markers",
            name="Non quantifié (< LQ)",
            marker=dict(size=10, symbol="circle-open", color=COLORS["muted"], line=dict(width=1.8)),
            hovertemplate="%{x|%d/%m/%Y} · inférieur à %{y} " + unite + "<extra></extra>",
        ))
    fig.add_hline(y=norme, line=dict(color=STATUS["Alerte renforcée"], width=1.6, dash="dash"))
    _etiquette_droite(fig, norme, f"<b>Norme</b> {fr_num(norme, 2)} {unite}", STATUS["Alerte renforcée"])
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre),
        yaxis=dict(title=f"Concentration ({unite})", range=[0, y_max]),
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 8. Qualité : évolution annuelle sur le territoire
# ---------------------------------------------------------------------------

def qualite_annuelle(annuel: pd.DataFrame, parametre: str, source: str = SOURCE_HUBEAU) -> go.Figure:
    if annuel.empty:
        raise ValueError("Aucune analyse sur la période.")
    derniere = annuel.iloc[-1]
    titre = (f"{parametre} : {fr_num(derniere['pct_depassement'], 0)} % des analyses "
             f"au-dessus de la norme en {int(derniere['annee'])}")
    sous_titre = "Part des analyses où la substance est quantifiée, et part au-dessus de la norme"
    survol = np.stack([annuel["n_analyses"], annuel["n_stations"]], axis=1)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=annuel["annee"], y=annuel["pct_quanti"], name="Quantifiée",
        marker=dict(color=QUALITE["Détecté, sous la norme"], cornerradius=5), customdata=survol,
        hovertemplate="%{x} · %{y:.0f} % quantifiées<br>%{customdata[0]} analyses, "
                      "%{customdata[1]} stations<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=annuel["annee"], y=annuel["pct_depassement"], name="Au-dessus de la norme",
        marker=dict(color=QUALITE["Dépassement de la norme"], cornerradius=5), customdata=survol,
        hovertemplate="%{x} · %{y:.0f} % au-dessus de la norme<br>%{customdata[0]} analyses, "
                      "%{customdata[1]} stations<extra></extra>",
    ))
    # Le nombre d'analyses varie d'une année à l'autre : on l'écrit sous chaque année
    # pour éviter de lire une fausse tendance.
    etiquettes = [f"<b>{int(a)}</b><br><span style='font-size:12px;color:{COLORS['muted']}'>"
                  f"{int(n)} analyses</span>" for a, n in zip(annuel["annee"], annuel["n_analyses"], strict=False)]
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre), barmode="group",
        bargap=0.3, yaxis=dict(title="Part des analyses (%)", rangemode="tozero", ticksuffix=" %"),
        xaxis=dict(tickvals=annuel["annee"], ticktext=etiquettes), margin=dict(b=120),
    )
    return apply_signature(fig, source)


# ---------------------------------------------------------------------------
# 7. Info Piézo : taux de remplissage et projection par analogie
# ---------------------------------------------------------------------------

def _titre_remplissage(synth: pd.DataFrame) -> str:
    mesures = synth.dropna(subset=["taux"])
    haut = int((mesures["taux"] >= 50).sum())
    return f"{haut} piézomètre{'s' if haut > 1 else ''} sur {len(mesures)} au-dessus de 50 % de remplissage"


def jauges_remplissage(synth: pd.DataFrame, jour: pd.Timestamp, source: str = SOURCE_HUBEAU) -> go.Figure:
    """Une jauge de 0 à 100 par piézomètre, comme dans l'Info Piézo d'AQUI'Brie."""
    from .bulletin import ANNEE_REFERENCE, ORDRE_TRANCHES, TRANCHES_REMPLISSAGE

    s = synth.dropna(subset=["taux"]).sort_values("taux", ascending=True)
    if s.empty:
        raise ValueError("Aucun piézomètre n'a de mesure à cette date.")
    fig = go.Figure()
    bornes = [0] + [b for b, _ in TRANCHES_REMPLISSAGE[:-1]] + [100]
    for (x0, x1), nom in zip(zip(bornes[:-1], bornes[1:], strict=False), ORDRE_TRANCHES, strict=False):
        fig.add_vrect(x0=x0, x1=x1, fillcolor=REMPLISSAGE[nom], opacity=0.12, line_width=0, layer="below")
        fig.add_annotation(x=(x0 + x1) / 2, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=nom, font=dict(size=13, color=COLORS["muted"]))
    fig.add_trace(go.Bar(
        x=s["taux"], y=s["commune"], orientation="h",
        marker=dict(color=[REMPLISSAGE[t] for t in s["tranche"]], line=dict(width=0)),
        text=[f"{fr_num(t, 0)} %" for t in s["taux"]], textposition="outside", cliponaxis=False,
        textfont=dict(size=13, color=COLORS["abyss"]),
        customdata=np.stack([s["niveau"], s["date_mesure"].dt.strftime("%d/%m/%Y"), s["code_bss"]], axis=1),
        hovertemplate=("<b>%{y}</b> (%{customdata[2]})<br>remplissage %{x:.0f} %<br>"
                       "niveau %{customdata[0]:.2f} m NGF, mesure du %{customdata[1]}<extra></extra>"),
    ))
    fig.add_vline(x=50, line=dict(color=COLORS["abyss"], width=1.4, dash="dot"))
    manquants = int(synth["taux"].isna().sum())
    note = f" · {manquants} sans mesure récente" if manquants else ""
    sous_titre = (f"Au {fr_date(jour)} · position du niveau entre le minimum (0) et le maximum (100) "
                  f"mesurés depuis {ANNEE_REFERENCE}{note}")
    fig.update_layout(
        template=TEMPLATE_NAME, title=title_with_subtitle(_titre_remplissage(synth), sous_titre),
        showlegend=False, height=max(560, 28 * len(s) + 260), margin=dict(l=200, r=80), bargap=0.25,
        xaxis=dict(range=[0, 108], tickvals=[0, 25, 50, 75, 100], ticksuffix=" %", showgrid=False),
        yaxis=dict(type="category", tickfont=dict(size=14)),
    )
    return apply_signature(fig, source)


def carte_remplissage(synth: pd.DataFrame, jour: pd.Timestamp, source: str = SOURCE_HUBEAU) -> go.Figure:
    """Carte des piézomètres de l'Info Piézo, colorés par tranche de remplissage."""
    from .bulletin import ORDRE_TRANCHES

    base = synth.dropna(subset=["lon", "lat"]).assign(libelle=lambda d: d["commune"])
    fig = go.Figure()
    _points_cartes(fig, base[base["taux"].isna()], COLORS["muted"], "Sans mesure récente", 12,
                   "<b>%{text}</b><br>pas de mesure récente<extra></extra>")
    for nom in ORDRE_TRANCHES:
        groupe = base[base["tranche"] == nom]
        _points_cartes(fig, groupe, REMPLISSAGE[nom], nom, 18,
                       "<b>%{text}</b><br>" + nom + " · %{customdata:.0f} %<extra></extra>",
                       customdata=groupe["taux"])
    return _mise_en_page_carte(fig, _titre_remplissage(synth), f"Taux de remplissage au {fr_date(jour)}",
                               base["lon"], base["lat"], source)


def projection_analogues(proj, station: str, nom_seuil: str = "Vigilance", observe: pd.DataFrame | None = None,
                         annee_mise_en_avant: int | None = None, source: str = SOURCE_HUBEAU) -> go.Figure:
    """Éventail des trajectoires possibles : chaque année passée rejouée depuis le niveau du jour.

    observe : mesures réelles après la date de départ, si elles existent. Elles montrent si la
    projection était juste (c'est le cas quand on refait un bulletin déjà publié).
    """
    traj = proj.trajectoires
    annee_mise_en_avant = annee_mise_en_avant or proj.depart.year - 1
    n_sous = int(proj.franchissements.notna().sum())
    fin = traj.index[-1]
    if n_sous:
        titre = (f"{n_sous} année{'s' if n_sous > 1 else ''} sur {proj.nb_annees} passerai"
                 f"{'en' if n_sous > 1 else ''}t sous le seuil {SEUIL_ARTICLE[nom_seuil]} avant le {fr_date(fin)}")
    else:
        titre = (f"Aucune des {proj.nb_annees} années passées ne ferait passer la nappe "
                 f"sous le seuil {SEUIL_ARTICLE[nom_seuil]}")
    sous_titre = (f"{station} · niveau du {fr_date(proj.depart)} ({fr_num(proj.niveau_depart)} m), "
                  f"puis l'évolution de chaque année depuis {traj.columns.min()} à la même date")
    fig = go.Figure()
    for annee, valeurs in traj.items():
        fig.add_trace(go.Scatter(x=traj.index, y=valeurs, mode="lines", showlegend=False,
                                 line=dict(color="rgba(0,150,158,0.18)", width=1),
                                 hovertemplate=f"comme en {annee} : " + "%{y:.2f} m<extra></extra>"))
    bas, haut, mediane = proj.quantile(0.1), proj.quantile(0.9), proj.quantile(0.5)
    fig.add_trace(go.Scatter(x=list(traj.index) + list(traj.index[::-1]), y=list(haut) + list(bas[::-1]),
                             fill="toself", fillcolor="rgba(0,150,158,0.14)", line=dict(width=0),
                             name="8 années sur 10", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=traj.index, y=mediane, mode="lines", name="Année médiane",
                             line=dict(color=COLORS["aqui_dark"], width=3),
                             hovertemplate="année médiane : %{y:.2f} m<extra></extra>"))
    if annee_mise_en_avant in traj.columns:
        fig.add_trace(go.Scatter(x=traj.index, y=traj[annee_mise_en_avant], mode="lines",
                                 name=f"Comme en {annee_mise_en_avant}",
                                 line=dict(color=COLORS["brie_dark"], width=2.6, dash="dash"),
                                 hovertemplate=f"comme en {annee_mise_en_avant} : " + "%{y:.2f} m<extra></extra>"))
    if observe is not None and not observe.empty:
        vu = observe[(observe["date"] >= proj.depart) & (observe["date"] <= fin)]
        if not vu.empty:
            fig.add_trace(go.Scatter(x=vu["date"], y=vu["niveau"], mode="lines", name="Mesuré depuis",
                                     line=dict(color=COLORS["abyss"], width=3),
                                     hovertemplate="mesuré : %{x|%d/%m/%Y} · %{y:.2f} m<extra></extra>"))
    fig.add_hline(y=proj.seuil, line=dict(color=STATUS[nom_seuil], width=1.6, dash="dash"))
    _etiquette_droite(fig, proj.seuil, f"<b>{nom_seuil}</b> {fr_num(proj.seuil, 1)} m", STATUS[nom_seuil],
                      encre=COLORS["abyss"] if nom_seuil == "Vigilance" else "white")
    _accent_point(fig, proj.depart, proj.niveau_depart, f"<b>{fr_num(proj.niveau_depart)} m</b>", xanchor="left")
    # Cadré sur la bande 8 années sur 10 et le seuil : quelques années très humides écraseraient le reste.
    y_bas = min(float(bas.min()), proj.seuil, proj.niveau_depart) - 1
    y_haut = max(float(haut.max()), proj.niveau_depart) + 1
    fig.update_layout(template=TEMPLATE_NAME, title=title_with_subtitle(titre, sous_titre),
                      yaxis=dict(title="Altitude de la nappe (m NGF)", range=[y_bas, y_haut]),
                      legend=dict(orientation="h", y=1.01, x=0.35))
    return apply_signature(fig, source)
