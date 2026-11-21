"""Ouvre chaque page de l'application en cours d'exécution et échoue sur la moindre erreur.

Utilise l'API agent de Streamlit (aucun navigateur). L'application doit avoir été lancée avec
--server.enableAgentApi=true, par exemple en CI :

    AQUIBRIE_HORS_LIGNE=1 streamlit run app.py --server.headless=true --server.port=8765 \\
        --server.enableAgentApi=true &
    python scripts/smoke_pages.py http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

# Serveur local : ne jamais passer par un proxy HTTP défini dans l'environnement.
_OUVRIR = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

PAGES = ["", "ensemble", "station", "qualite", "fiche", "rapport", "charte"]


def _post(base: str, payload: dict) -> dict:
    req = urllib.request.Request(f"{base}/_stcore/agent/interact", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with _OUVRIR(req, timeout=300) as r:
        return json.loads(r.read())


def _problemes(noeud, trouves: list[str]) -> list[str]:
    if isinstance(noeud, dict):
        if noeud.get("type") in ("exception", "error"):
            trouves.append(json.dumps(noeud, ensure_ascii=False)[:300])
        for v in noeud.values():
            _problemes(v, trouves)
    elif isinstance(noeud, list):
        for v in noeud:
            _problemes(v, trouves)
    return trouves


def main(base: str) -> int:
    for _ in range(60):
        try:
            _OUVRIR(f"{base}/_stcore/health", timeout=5)
            break
        except OSError:
            time.sleep(1)
    echecs = 0
    for page in PAGES:
        reponse = _post(base, {"page": page})
        problemes = _problemes(reponse.get("tree", reponse), [])
        etat = reponse.get("status")
        ok = etat == "ready" and not problemes
        echecs += not ok
        print(f"{'ok ' if ok else 'ÉCHEC'} {page or '(accueil)':<11} {etat} {' | '.join(problemes)}")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"))
