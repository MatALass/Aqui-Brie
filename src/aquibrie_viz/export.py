"""Exports d'une figure. PNG, SVG et PDF passent par Kaleido, qui a besoin de Chrome
installé sur la machine (Kaleido >= 1.0). HTML fonctionne partout.
"""
from __future__ import annotations

import atexit
import threading
import warnings

import plotly.graph_objects as go

FORMATS = {
    "png": ("image/png", "PNG"),
    "svg": ("image/svg+xml", "SVG (Inkscape)"),
    "pdf": ("application/pdf", "PDF"),
    "html": ("text/html", "HTML interactif"),
}


class ExportError(RuntimeError):
    """Export statique impossible (Kaleido ou Chrome absent, fond de carte inaccessible…)."""


def taille(fig: go.Figure) -> tuple[int, int]:
    """Dimensions d'export : celles de la figure si elles sont fixées (fiche A4), sinon 1400 × hauteur."""
    return int(fig.layout.width or 1400), int(fig.layout.height or 750)


# Avec le moteur partagé, Kaleido signale à chaque export que l'option « kopts » est ignorée :
# c'est attendu, on le fait taire pour garder une console lisible.
warnings.filterwarnings("ignore", message="The kopts argument is ignored", category=UserWarning)

_verrou_serveur = threading.Lock()
_serveur_demarre = False


def demarrer_moteur() -> None:
    """Garde un Chrome ouvert entre deux exports.

    Sans cela, Kaleido lance puis ferme Chrome à chaque image : ≈ 2,3 s par export,
    contre ≈ 0,35 s une fois le moteur ouvert. Sans effet si Chrome est absent
    (l'export échouera alors avec un message explicite).
    """
    global _serveur_demarre
    with _verrou_serveur:
        if _serveur_demarre:
            return
        try:
            import kaleido

            kaleido.start_sync_server(silence_warnings=True)
            atexit.register(kaleido.stop_sync_server, silence_warnings=True)
            _serveur_demarre = True
        except Exception:  # noqa: BLE001 — Kaleido < 1.1 ou Chrome absent : mode classique
            pass


def figure_to_bytes(fig: go.Figure, fmt: str, width: int | None = None, height: int | None = None) -> bytes:
    if fmt == "html":
        return fig.to_html(include_plotlyjs="cdn", full_html=True).encode("utf-8")
    l, h = taille(fig)
    demarrer_moteur()
    try:
        # scale=2 : 2800 px de large, suffisant pour une impression A4 pleine largeur.
        with warnings.catch_warnings():  # « kopts ignored » : sans objet avec le moteur partagé
            warnings.simplefilter("ignore", UserWarning)
            return fig.to_image(format=fmt, width=width or l, height=height or h, scale=2)
    except Exception as exc:  # Kaleido lève des exceptions de types variés
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        conseil = ("fond de carte inaccessible (connexion Internet ?)" if "Map" in detail
                   else "installez Chrome ou lancez la commande « plotly_get_chrome »")
        raise ExportError(f"{conseil}. Détail : {detail}") from exc
