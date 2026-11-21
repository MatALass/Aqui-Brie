"""L'application tourne sans réseau, sur l'instantané livré avec le paquet.

La page d'accueil est exécutée en mode hors ligne avec un cache vide : c'est la situation d'un
premier lancement sans Internet, d'une démo en ligne derrière un proxy ou de la CI. Les autres
pages sont des fonctions (st.navigation), qu'AppTest ne sait pas ouvrir : la CI les ouvre une
par une sur l'application lancée, avec scripts/smoke_pages.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aquibrie_viz import donnees

APP = str(Path(__file__).resolve().parents[1] / "app.py")

pytestmark = pytest.mark.skipif(not donnees.INSTANTANE.exists(), reason="instantané absent")


@pytest.fixture
def hors_ligne(tmp_path, monkeypatch):
    monkeypatch.setenv("AQUIBRIE_HORS_LIGNE", "1")
    monkeypatch.setenv("AQUIBRIE_CACHE", str(tmp_path / "cache"))


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(APP, default_timeout=240)


def test_accueil_tourne_hors_ligne(hors_ligne):
    at = _app().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_info_piezo_refait_le_bulletin_65(hors_ligne):
    at = _app().run()
    at.toggle(key="ip_comparer").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]
    tableau = at.dataframe[0].value
    lignes = dict(zip(tableau["Indicateur"], tableau["Recalculé ici"], strict=True))
    assert lignes["Beauchery : écart au seuil de vigilance"] == "+4,80 m"  # publié : +4,8 m
    assert lignes["Beauchery : baisse depuis le n°64"] == "1,29 m"  # publié : 1,3 m
    assert int(lignes["Piézomètres au-dessus de 50 %"]) == 15  # publié : 16, voir le README
