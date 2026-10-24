"""Juge local : un LLM servi par Ollama note une définition en direct, dans le vocabulaire fermé.

Même consignes, même schéma et même taxonomie que la notation par Claude (`claude_headless.consignes`) ; le
verdict est écrit dans `notation` avec le correcteur « local:<modèle> », qui passe après Claude et Jev mais
avant l'auto-évaluation et le cosinus (`journal.PRIORITE_CORRECTEUR`). Ollama contraint la sortie au schéma
JSON : la réponse est toujours lisible.

Le modèle se règle par `/juge <modèle>` ou `/juge non` (réglage global « juge_local »). Sur un Mac de 16 Go,
préférer un modèle de 4 à 8 milliards de paramètres ; le correcteur distillé (docs/passation-distillation.md)
le remplacera.
"""

from __future__ import annotations

import json
import sqlite3
import time

import httpx

from lem.grading.claude_headless import Element, Verdict, consignes
from lem.grading.taxonomie import Erreur
from lem.texte import normaliser
from lem.vecteurs import url_ollama

MODELE_DEFAUT = "qwen3:8b"

SCHEMA = {
    "type": "object",
    "properties": {
        "sens_vise_id": {"type": ["integer", "null"]},
        "precision": {"type": "integer", "minimum": 1, "maximum": 5},
        "erreur": {"type": "string", "enum": [e.value for e in Erreur]},
        "mot_reellement_defini": {"type": ["string", "null"]},
        "confiance": {"type": "number", "minimum": 0, "maximum": 1},
        "commentaire": {"type": "string"},
    },
    "required": ["sens_vise_id", "precision", "erreur", "mot_reellement_defini", "confiance", "commentaire"],
}


def modele_reglage(con: sqlite3.Connection) -> str | None:
    """Le juge choisi (`/juge`) : « auto » par défaut, un modèle Ollama, « haiku »/« sonnet » (Claude), None si coupé."""
    from lem.db import get_reglage

    m = get_reglage(con, "juge_local", "auto")
    return None if not m or m == "non" else m


MODELES_CLAUDE = ("haiku", "sonnet", "opus")


def juge_depuis_reglage(con: sqlite3.Connection):
    """Le juge en direct à utiliser, ou None.

    auto  : Claude Haiku si la commande `claude` existe (rien de plus en mémoire, l'abonnement fait le travail ;
            l'alias « haiku » suit le dernier Haiku), sinon le juge distillé s'il est installé (~/.lem/modeles,
            hors ligne), sinon le modèle local par défaut s'il est déjà dans Ollama (PC avec GPU).
    distille : le juge distillé (cf. lem/grading/distille.py).
    haiku | sonnet | opus | claude:<modèle> : Claude Code headless.
    autre : un modèle Ollama (qwen3:8b…)."""
    from lem.grading.claude_headless import JugeClaude
    from lem.grading.distille import JugeDistille

    m = modele_reglage(con)
    if m is None:
        return None
    if m == "distille":
        j = JugeDistille()
        return j if j.disponible() else None
    if m == "auto":
        for j in (JugeClaude("haiku"), JugeDistille(), JugeLocal(MODELE_DEFAUT)):
            if j.disponible():
                return j
        return None
    if m.startswith("claude:") or m in MODELES_CLAUDE:
        j = JugeClaude(m.split(":", 1)[-1])
    else:
        j = JugeLocal(m)
    return j if j.disponible() else None


class JugeLocal:
    def __init__(self, modele: str = MODELE_DEFAUT, url: str | None = None, timeout: float = 30):
        self.modele = modele
        self.url = (url or url_ollama()).rstrip("/")
        self.timeout = timeout

    @property
    def correcteur(self) -> str:
        return f"local:{self.modele}"

    def disponible(self) -> bool:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3)
            noms = {m["name"] for m in r.json().get("models", [])}
            return self.modele in noms or f"{self.modele}:latest" in noms
        except (httpx.HTTPError, ValueError):
            return False

    def juger(self, el: Element) -> Verdict:
        """Un verdict pour une réponse. Lève une exception si Ollama ne répond pas ou répond hors schéma."""
        t = time.monotonic()
        corps = {
            "model": self.modele,
            "messages": [
                {"role": "system", "content": consignes() + "\n\nRéponds par un seul objet JSON conforme au schéma."},
                {"role": "user", "content": json.dumps(el.payload, ensure_ascii=False)},
            ],
            "format": SCHEMA,
            "stream": False,
            "think": False,                     # modèles « à raisonnement » : verdict direct, pas de pensée
            "options": {"temperature": 0},
            "keep_alive": "30m",
        }
        r = httpx.post(f"{self.url}/api/chat", json=corps, timeout=self.timeout)
        r.raise_for_status()
        brut = json.loads(r.json()["message"]["content"])
        # Ollama impose la structure du schéma, pas les bornes numériques : « confiance: 100 » arrive.
        c = float(brut.get("confiance") or 0.5)
        brut["confiance"] = min(1.0, max(0.0, c / 100 if c > 1 else c))
        brut["precision"] = min(5, max(1, int(brut.get("precision") or 1)))
        # « Mot réellement défini » : un autre mot, d'un à trois mots, pas la cible ni un bout de définition.
        m = (brut.get("mot_reellement_defini") or "").strip(" «»\"'.")
        if not m or len(m.split()) > 3 or normaliser(m) == normaliser(el.payload.get("mot", "")):
            m = None
        brut["mot_reellement_defini"] = m
        v = Verdict.model_validate({**brut, "id": el.evenement_id})
        self.derniere_latence = time.monotonic() - t
        return v
