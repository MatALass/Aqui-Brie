"""Mode rapport : régénérer d'un coup toutes les infographies d'un rapport.

Un rapport est une liste de spécifications de figures (fichier TOML), écrites en
termes relatifs : « les 10 dernières années », « la dernière année ». Le même
fichier, relancé l'an prochain, produit donc les figures à jour sans rien modifier.

Sorties : un dossier d'images (PNG haute résolution, SVG), une présentation
PowerPoint à la charte, et un document Word, soit à partir d'un gabarit fourni
(balises {{ fig_<id> }}), soit sous forme d'un document simple.

En ligne de commande :
    python -m aquibrie_viz.rapport rapports/exemple_bulletin.toml --sortie sorties
"""
from __future__ import annotations

import argparse
import re
import tomllib
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from . import donnees, fiche, hubeau, reference, templates
from . import indicators as ind
from .export import demarrer_moteur
from .theme import COLORS

# ---------------------------------------------------------------------------
# Catalogue des modèles et de leurs paramètres
# ---------------------------------------------------------------------------

MODELES = {
    "carte_situation": "Carte de situation du territoire",
    "situation_stations": "Classement des stations",
    "chronique_seuils": "Niveau et seuils d'une station",
    "annee_vs_historique": "Année comparée à l'historique",
    "recharge_annuelle": "Recharge annuelle",
    "calendrier_anomalies": "Calendrier des écarts mensuels",
    "carte_qualite": "Qualité : carte des stations",
    "qualite_annuelle": "Qualité : évolution annuelle",
    "concentration_norme": "Qualité : une station",
    "fiche_synthese": "Fiche grand public (A4)",
}
MODELES_STATION = {"chronique_seuils", "annee_vs_historique", "recharge_annuelle",
                   "calendrier_anomalies", "fiche_synthese"}
MODELES_QUALITE = {"carte_qualite", "qualite_annuelle", "concentration_norme"}
PERIODE_PAR_DEFAUT = 10


class RapportError(ValueError):
    """Fichier de rapport invalide."""


def valider(spec: dict) -> None:
    if spec.get("modele") not in MODELES:
        raise RapportError(f"Modèle inconnu : {spec.get('modele')!r}. Modèles possibles : {', '.join(MODELES)}")
    if not re.fullmatch(r"[a-z0-9_]+", str(spec.get("id", ""))):
        raise RapportError(f"Identifiant invalide {spec.get('id')!r} : lettres minuscules, chiffres et _ uniquement.")
    if spec["modele"] in MODELES_STATION and not spec.get("station"):
        raise RapportError(f"La figure {spec['id']!r} a besoin d'une station (code BSS).")
    if spec["modele"] in MODELES_QUALITE and spec.get("substance") not in reference.SUBSTANCES:
        raise RapportError(f"La figure {spec['id']!r} a besoin d'une substance parmi : "
                           f"{', '.join(reference.SUBSTANCES)}")
    if spec["modele"] == "concentration_norme" and not spec.get("bss_qualite"):
        raise RapportError(f"La figure {spec['id']!r} a besoin d'une station qualité (bss_qualite).")


def description(spec: dict, noms_stations: dict[str, str] | None = None) -> str:
    """Libellé lisible d'une spécification, pour l'interface."""
    morceaux = [MODELES.get(spec.get("modele"), spec.get("modele", "?"))]
    if spec.get("station"):
        morceaux.append((noms_stations or {}).get(spec["station"], spec["station"]))
    if spec.get("substance"):
        morceaux.append(spec["substance"])
    if spec.get("bss_qualite"):
        morceaux.append(spec["bss_qualite"])
    if spec.get("modele") == "annee_vs_historique":
        morceaux.append("dernière année" if spec.get("annee", "derniere") == "derniere" else str(spec["annee"]))
    if spec.get("modele") not in {"carte_situation", "situation_stations", "fiche_synthese"}:
        p = spec.get("periode_ans", PERIODE_PAR_DEFAUT)
        morceaux.append("tout l'historique" if p == 0 else f"{p} dernières années")
    return " · ".join(morceaux)


def identifiant_libre(base: str, existants: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or "figure"
    candidat, i = base, 2
    while candidat in existants:
        candidat, i = f"{base}_{i}", i + 1
    return candidat


# ---------------------------------------------------------------------------
# Fichier TOML
# ---------------------------------------------------------------------------

@dataclass
class Rapport:
    titre: str = "Infographies de la nappe du Champigny"
    perimetre: str = "masse_eau"  # ou "departements"
    figures: list[dict] = field(default_factory=list)


def lire(texte: str) -> Rapport:
    try:
        donnees = tomllib.loads(texte)
    except tomllib.TOMLDecodeError as exc:
        raise RapportError(f"Fichier de rapport illisible : {exc}") from exc
    rapport = Rapport(titre=donnees.get("titre", Rapport.titre),
                      perimetre=donnees.get("perimetre", "masse_eau"),
                      figures=list(donnees.get("figure", [])))
    if rapport.perimetre not in {"masse_eau", "departements"}:
        raise RapportError("perimetre doit valoir « masse_eau » ou « departements ».")
    ids = [f.get("id") for f in rapport.figures]
    if len(ids) != len(set(ids)):
        raise RapportError("Deux figures portent le même identifiant.")
    for spec in rapport.figures:
        valider(spec)
    return rapport


def _toml_valeur(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_valeur(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ecrire(rapport: Rapport) -> str:
    """Sérialise un rapport en TOML (format simple : valeurs, listes de nombres)."""
    lignes = [
        "# Rapport d'infographies AQUI'Brie : relancer ce fichier régénère toutes les figures.",
        "# periode_ans : nombre d'années jusqu'à aujourd'hui (0 = tout l'historique).",
        '# annee : "derniere" pour l\'année la plus récente, ou une année précise.',
        "",
        f"titre = {_toml_valeur(rapport.titre)}",
        f"perimetre = {_toml_valeur(rapport.perimetre)}",
    ]
    for spec in rapport.figures:
        lignes += ["", "[[figure]]"]
        lignes += [f"{cle} = {_toml_valeur(val)}" for cle, val in spec.items() if val is not None]
    return "\n".join(lignes) + "\n"


# ---------------------------------------------------------------------------
# Accès aux données
# ---------------------------------------------------------------------------

class Fournisseur(Protocol):
    """Ce dont la génération a besoin. Hub'Eau aujourd'hui, SQL Server demain."""

    def stations(self, masse_eau: str | None) -> pd.DataFrame: ...
    def chronique(self, code_bss: str) -> pd.DataFrame: ...
    def chroniques(self, codes: list[str]) -> dict[str, pd.DataFrame]: ...
    def analyses(self, code_param: str, debut: date, fin: date, masse_eau: str | None) -> pd.DataFrame: ...


class FournisseurHubEau:
    """Hub'Eau via le cache disque partagé avec l'application : un rapport généré après
    une consultation ne retélécharge presque rien."""

    def stations(self, masse_eau):
        return donnees.stations(reference.DEPARTEMENTS_CHAMPIGNY, masse_eau)

    def chronique(self, code_bss):
        return donnees.chronique(code_bss, date(1970, 1, 1))

    def chroniques(self, codes):
        return donnees.chroniques(codes, date(date.today().year - 19, 1, 1))[0]

    def analyses(self, code_param, debut, fin, masse_eau):
        return donnees.analyses(code_param, debut, fin, masse_eau, reference.DEPARTEMENTS_CHAMPIGNY)


class Contexte:
    """Données d'une génération, chargées une seule fois même si plusieurs figures les utilisent."""

    def __init__(self, fournisseur: Fournisseur, perimetre: str = "masse_eau", aujourd_hui: date | None = None):
        self.f = fournisseur
        self.masse_eau = reference.CODE_MASSE_EAU_CHAMPIGNY if perimetre == "masse_eau" else None
        self.aujourd_hui = aujourd_hui or date.today()
        self._stations: pd.DataFrame | None = None
        self._chroniques: dict[str, pd.DataFrame] = {}
        self._situation: pd.DataFrame | None = None
        self._analyses: dict[tuple, pd.DataFrame] = {}

    @property
    def stations(self) -> pd.DataFrame:
        if self._stations is None:
            st = self.f.stations(self.masse_eau)
            if st.empty and self.masse_eau:
                st = self.f.stations(None)
            limite = pd.Timestamp(self.aujourd_hui) - pd.DateOffset(years=1)
            self._stations = st[st["date_fin_mesure"].isna() | (st["date_fin_mesure"] >= limite)].reset_index(drop=True)
        return self._stations

    def station(self, code: str) -> pd.Series:
        lignes = self.stations[self.stations["code_bss"] == code]
        if lignes.empty:
            raise ValueError(f"Station {code} introuvable (ou inactive depuis plus d'un an).")
        return lignes.iloc[0]

    def chronique(self, code: str) -> pd.DataFrame:
        if code not in self._chroniques:
            self._chroniques[code] = self.f.chronique(code)
        return self._chroniques[code]

    def debut(self, periode_ans: int) -> pd.Timestamp:
        if not periode_ans:
            return pd.Timestamp("1970-01-01")
        return pd.Timestamp(self.aujourd_hui.year - periode_ans, self.aujourd_hui.month, 1)

    @property
    def situation(self) -> pd.DataFrame:
        if self._situation is None:
            chro = self.f.chroniques(list(self.stations["code_bss"]))
            self._situation = ind.situation_territoire(self.stations, chro)
        return self._situation

    def analyses(self, substance: str, periode_ans: int) -> pd.DataFrame:
        cle = (substance, periode_ans)
        if cle not in self._analyses:
            debut = max(self.debut(periode_ans).date(), date(2000, 1, 1))
            code = reference.SUBSTANCES[substance]["code"]
            self._analyses[cle] = self.f.analyses(code, debut, self.aujourd_hui, self.masse_eau)
        return self._analyses[cle]


# ---------------------------------------------------------------------------
# Construction des figures
# ---------------------------------------------------------------------------

def _seuils(spec: dict, nom: str, chronique: pd.DataFrame) -> tuple[dict[str, float], bool]:
    """Seuils réglementaires connus ; sinon ceux saisis dans la spec ; sinon indicatifs."""
    officiels = reference.seuils_pour(nom)
    if officiels:
        return officiels, True
    if spec.get("seuils") and len(spec["seuils"]) == 4:
        return dict(zip(reference.QUANTILES_INDICATIFS, map(float, spec["seuils"]), strict=False)), False
    return reference.seuils_indicatifs(chronique), False


def construire(spec: dict, ctx: Contexte) -> go.Figure:
    """Construit la figure décrite par une spécification. Lève ValueError si les données manquent."""
    valider(spec)
    modele = spec["modele"]
    periode = int(spec.get("periode_ans", PERIODE_PAR_DEFAUT))
    debut = ctx.debut(periode)

    if modele == "carte_situation":
        return templates.carte_situation(ctx.situation, ctx.stations, spec.get("station"))
    if modele == "situation_stations":
        return templates.situation_stations(ctx.situation)

    if modele in MODELES_QUALITE:
        sub = reference.SUBSTANCES[spec["substance"]]
        analyses = ctx.analyses(spec["substance"], periode)
        if analyses.empty:
            raise ValueError(f"Aucune analyse de {spec['substance']} sur la période.")
        if modele == "carte_qualite":
            return templates.carte_qualite(ind.synthese_stations_qualite(analyses, sub["norme"]),
                                           spec["substance"], sub["unite"])
        if modele == "qualite_annuelle":
            return templates.qualite_annuelle(ind.synthese_annuelle_qualite(analyses, sub["norme"]),
                                              spec["substance"])
        detail = analyses[analyses["bss_id"] == spec["bss_qualite"]]
        if detail.empty:
            raise ValueError(f"Aucune analyse à la station {spec['bss_qualite']}.")
        return templates.concentration_norme(detail, sub["norme"], spec["substance"], sub["unite"],
                                             f"{detail['commune'].iloc[0]} ({spec['bss_qualite']})")

    st = ctx.station(spec["station"])
    historique = ctx.chronique(spec["station"])
    if historique.empty:
        raise ValueError(f"Aucune mesure pour la station {st['nom']}.")
    seuils, officiels = _seuils(spec, st["nom"], historique)
    periode_df = historique[historique["date"] >= debut]
    if periode_df.empty:
        raise ValueError(f"Aucune mesure pour {st['nom']} sur la période demandée.")

    if modele == "chronique_seuils":
        return templates.chronique_seuils(periode_df, seuils, st["nom"], seuils_officiels=officiels,
                                          projection=ind.projection_seuil(historique, seuils))
    if modele == "annee_vs_historique":
        annee = spec.get("annee", "derniere")
        annee = int(periode_df["date"].dt.year.max()) if annee == "derniere" else int(annee)
        return templates.annee_vs_historique(periode_df, annee, st["nom"])
    if modele == "recharge_annuelle":
        return templates.recharge_annuelle(ind.recharge_annuelle(periode_df), st["nom"])
    if modele == "calendrier_anomalies":
        anomalies = ind.anomalies_mensuelles(historique)
        return templates.calendrier_anomalies(anomalies[anomalies.index >= debut.year], st["nom"])
    # fiche_synthese
    return fiche.fiche_synthese(ctx.situation, st["commune"], historique, seuils, officiels,
                                ind.recharge_annuelle(historique))


def titre_brut(fig: go.Figure) -> str:
    """Titre d'une figure sans balises HTML ni sous-titre (pour Word et PowerPoint)."""
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    texte = (fig.layout.title.text or meta.get("titre") or "").split("<br>")[0]
    return re.sub(r"<[^>]+>", "", texte).strip()


def dimensions(fig: go.Figure) -> tuple[int, int]:
    return int(fig.layout.width or 1400), int(fig.layout.height or 750)


# ---------------------------------------------------------------------------
# Génération des fichiers
# ---------------------------------------------------------------------------

@dataclass
class Resultat:
    images: list[Path] = field(default_factory=list)
    documents: list[Path] = field(default_factory=list)
    erreurs: dict[str, str] = field(default_factory=dict)  # id de figure → message
    avertissements: dict[str, str] = field(default_factory=dict)

    @property
    def fichiers(self) -> list[Path]:
        return self.images + self.documents


def _exporter_images(figures: dict[str, go.Figure], dossier: Path, formats: set[str],
                     resultat: Resultat) -> None:
    """Exporte les images ; une figure qui échoue est retirée de `figures` et signalée."""
    taches = [(ident, fig, dossier / f"{ident}.{fmt}")
              for ident, fig in figures.items() for fmt in sorted(formats & {"png", "svg", "pdf"})]
    if not taches:
        return
    for _, _, chemin in taches:
        chemin.unlink(missing_ok=True)
    # Un seul lancement de Chrome pour toutes les images (beaucoup plus rapide qu'une par une).
    demarrer_moteur()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # « kopts ignored » : sans objet avec le moteur partagé
            pio.write_images([f for _, f, _ in taches], [c for _, _, c in taches],
                             width=[dimensions(f)[0] for _, f, _ in taches],
                             height=[dimensions(f)[1] for _, f, _ in taches], scale=2)
    except Exception:  # noqa: BLE001 — on reprend figure par figure ci-dessous
        pass
    # L'export par lot peut échouer en silence : on vérifie chaque fichier et on reprend les manquants.
    for ident, fig, chemin in taches:
        if chemin.exists():
            continue
        l, h = dimensions(fig)
        try:
            fig.write_image(chemin, width=l, height=h, scale=2)
        except Exception as exc:  # noqa: BLE001
            if fig.layout.map and fig.layout.map.style and fig.layout.map.style != "white-bg":
                # Fond de carte inaccessible (pas d'Internet) : on garde les points, sans fond.
                try:
                    go.Figure(fig).update_layout(map_style="white-bg").write_image(chemin, width=l, height=h, scale=2)
                    resultat.avertissements[ident] = "fond de carte inaccessible : carte exportée sans fond"
                    continue
                except Exception:  # noqa: BLE001
                    pass
            resultat.erreurs[ident] = f"export impossible ({str(exc).splitlines()[0]})"
    for ident in resultat.erreurs:
        figures.pop(ident, None)
    resultat.images = [c for i, _, c in taches if i in figures and c.exists()]


def _pptx(rapport: Rapport, figures: dict[str, go.Figure], images: dict[str, Path], chemin: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    def rgb(hexa: str) -> RGBColor:
        return RGBColor.from_string(hexa.lstrip("#"))

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    vide = prs.slide_layouts[6]

    couverture = prs.slides.add_slide(vide)
    fond = couverture.background.fill
    fond.solid()
    fond.fore_color.rgb = rgb(COLORS["abyss"])

    def texte(slide, x, y, l, h, contenu, taille, couleur, gras=False):
        boite = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(l), Inches(h)).text_frame
        boite.word_wrap = True
        p = boite.paragraphs[0]
        for morceau, teinte in (contenu if isinstance(contenu, list) else [(contenu, couleur)]):
            run = p.add_run()
            run.text = morceau
            run.font.size, run.font.bold, run.font.name = Pt(taille), gras, "Nunito"
            run.font.color.rgb = rgb(teinte)

    texte(couverture, 0.8, 0.7, 6, 1, [("AQUI'", COLORS["aqui_light"]), ("Brie", COLORS["brie"])], 40, "", True)
    texte(couverture, 0.8, 2.6, 11.5, 1.6, rapport.titre, 40, "#FFFFFF", True)
    texte(couverture, 0.8, 4.3, 11.5, 0.6, f"Infographies générées le {templates.fr_date(pd.Timestamp.today())}",
          20, COLORS["aqui_light"])
    texte(couverture, 0.8, 6.6, 11.5, 0.5, "Source des données : Hub'Eau · BRGM, banque ADES", 14,
          COLORS["aqui_light"])

    for ident, fig in figures.items():
        slide = prs.slides.add_slide(vide)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = rgb(COLORS["paper"])
        l, h = dimensions(fig)
        max_l, max_h = Inches(12.6), Inches(6.9)
        echelle = min(max_l / l, max_h / h)
        larg, haut = Emu(int(l * echelle)), Emu(int(h * echelle))
        slide.shapes.add_picture(str(images[ident]), int((prs.slide_width - larg) / 2),
                                 int((prs.slide_height - haut) / 2), larg, haut)
        slide.notes_slide.notes_text_frame.text = titre_brut(fig)
    prs.save(chemin)


def _docx(rapport: Rapport, figures: dict[str, go.Figure], images: dict[str, Path], chemin: Path,
          gabarit: Path | None) -> None:
    from docx.shared import Mm

    if gabarit:
        from docxtpl import DocxTemplate, InlineImage

        doc = DocxTemplate(str(gabarit))
        contexte = {"titre": rapport.titre, "date": templates.fr_date(pd.Timestamp.today())}
        for ident, fig in figures.items():
            l, h = dimensions(fig)
            largeur = Mm(160) if l >= h else Mm(120)
            contexte[f"fig_{ident}"] = InlineImage(doc, str(images[ident]), width=largeur)
            contexte[f"titre_{ident}"] = titre_brut(fig)
        balises = doc.get_undeclared_template_variables(context={})
        manquantes = sorted(b for b in balises if b.startswith("fig_") and b not in contexte)
        if manquantes:
            raise RapportError("Le gabarit Word contient des balises sans figure correspondante : "
                               + ", ".join(manquantes))
        doc.render(contexte)
        doc.save(chemin)
        return

    from docx import Document

    doc = Document()
    doc.styles["Normal"].font.name = "Nunito"
    doc.add_heading(rapport.titre, level=0)
    doc.add_paragraph(f"Infographies générées le {templates.fr_date(pd.Timestamp.today())}. "
                      "Source des données : Hub'Eau (BRGM, banque ADES).")
    for ident, fig in figures.items():
        doc.add_heading(titre_brut(fig), level=2)
        l, h = dimensions(fig)
        doc.add_picture(str(images[ident]), width=Mm(165) if l >= h else Mm(130))
    doc.save(chemin)


def generer(
    rapport: Rapport,
    dossier: Path,
    fournisseur: Fournisseur | None = None,
    formats: set[str] | None = None,
    gabarit_word: Path | None = None,
    progression: Callable[[float, str], None] | None = None,
) -> Resultat:
    """Construit toutes les figures du rapport et écrit les fichiers demandés.

    formats : parmi png, svg, pdf, pptx, docx. Une figure en erreur (données
    manquantes…) est signalée dans Resultat.erreurs sans bloquer les autres.
    """
    formats = set(formats or {"png", "pptx", "docx"})
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    ctx = Contexte(fournisseur or FournisseurHubEau(), rapport.perimetre)
    signaler = progression or (lambda p, m: None)
    resultat = Resultat()

    figures: dict[str, go.Figure] = {}
    for i, spec in enumerate(rapport.figures):
        signaler(i / max(len(rapport.figures), 1) * 0.7, f"Construction : {description(spec)}")
        try:
            figures[spec["id"]] = construire(spec, ctx)
        except (ValueError, hubeau.HubEauError) as exc:
            resultat.erreurs[spec["id"]] = str(exc)
    if not figures:
        return resultat

    signaler(0.75, "Export des images…")
    formats_images = formats & {"png", "svg", "pdf"}
    if formats & {"pptx", "docx"}:
        formats_images |= {"png"}  # PowerPoint et Word ont besoin des PNG
    _exporter_images(figures, dossier, formats_images, resultat)
    if not figures:
        return resultat
    pngs = {ident: dossier / f"{ident}.png" for ident in figures}

    nom = re.sub(r"[^A-Za-z0-9_-]+", "_", rapport.titre).strip("_") or "rapport"
    if "pptx" in formats:
        signaler(0.88, "Création de la présentation PowerPoint…")
        _pptx(rapport, figures, pngs, dossier / f"{nom}.pptx")
        resultat.documents.append(dossier / f"{nom}.pptx")
    if "docx" in formats:
        signaler(0.94, "Création du document Word…")
        _docx(rapport, figures, pngs, dossier / f"{nom}.docx", gabarit_word)
        resultat.documents.append(dossier / f"{nom}.docx")
    signaler(1.0, "Terminé")
    return resultat


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère les infographies d'un rapport AQUI'Brie.")
    parser.add_argument("fichier", type=Path, help="fichier de rapport (.toml)")
    parser.add_argument("--sortie", type=Path, default=Path("sorties"), help="dossier de sortie")
    parser.add_argument("--formats", default="png,pptx,docx", help="png,svg,pdf,pptx,docx")
    parser.add_argument("--gabarit", type=Path, default=None, help="gabarit Word avec balises {{ fig_<id> }}")
    args = parser.parse_args(argv)

    rapport = lire(args.fichier.read_text(encoding="utf-8"))
    res = generer(rapport, args.sortie, formats=set(args.formats.split(",")), gabarit_word=args.gabarit,
                  progression=lambda p, m: print(f"[{p:4.0%}] {m}"))
    for chemin in res.fichiers:
        print(f"  ✔ {chemin}")
    for ident, msg in res.avertissements.items():
        print(f"  ! {ident} : {msg}")
    for ident, msg in res.erreurs.items():
        print(f"  ✘ {ident} : {msg}")
    return 1 if res.erreurs else 0


if __name__ == "__main__":
    raise SystemExit(main())
