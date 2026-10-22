"""Juge local : verdict typé, écrit au journal, préféré au cosinus par FSRS (Ollama simulé)."""

import json

import httpx

from lem.grading import local
from lem.grading.claude_headless import appliquer, element
from lem.journal import Evenement, ecrire
from lem.memory import scheduler

from .conftest import eid


class _Rep:
    def __init__(self, contenu):
        self._c = contenu

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": json.dumps(self._c)}, "models": [{"name": "qwen3:8b"}]}


def test_juge_local_verdict_et_priorite(con, monkeypatch):
    envoye = {}

    def post(url, json=None, timeout=None):
        envoye.update(json)
        return _Rep({"sens_vise_id": None, "precision": 4, "erreur": "exacte", "mot_reellement_defini": None,
                     "confiance": 0.9, "commentaire": "Juste."})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _Rep({}))
    a = eid(con, "thuriféraire")
    ev = ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="flatteur, flagorneur",
                                     note_auto=1, correct=False))
    juge = local.JugeLocal("qwen3:8b")
    assert juge.disponible()
    el = element(con, ev)
    v = juge.juger(el)
    assert v.precision == 4 and v.id == ev
    assert envoye["format"]["properties"]["erreur"]["enum"][0] == "exacte"      # sortie contrainte au schéma
    assert "flatteur" in envoye["messages"][1]["content"]
    appliquer(con, [el], [v], juge.correcteur)
    assert scheduler.note_finale(con, ev, 1) == 3                    # le juge (précision 4 → 3) bat le cosinus (1)


def test_juge_local_coupe(con):
    from lem.db import set_reglage

    set_reglage(con, "juge_local", "non")
    assert local.modele_reglage(con) is None


def test_juge_claude_haiku_en_direct(con, monkeypatch):
    """Sur le Mac : Claude Haiku en headless, même interface et même priorité (claude > local)."""
    from lem.db import set_reglage
    from lem.grading.claude_headless import JugeClaude
    from lem.grading.local import juge_depuis_reglage

    a = eid(con, "thuriféraire")
    ev = ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="morne, triste",
                                     note_auto=3))
    vu = {}

    def appel(prompt, modele):
        vu["modele"], vu["prompt"] = modele, prompt
        return json.dumps({"result": json.dumps([{"id": ev, "precision": 1, "erreur": "confusion_formelle",
                                                  "mot_reellement_defini": "atrabilaire", "confiance": 0.9}])})

    juge = JugeClaude("haiku", appel=appel)
    el = element(con, ev)
    v = juge.juger(el)
    assert vu["modele"] == "haiku" and "morne" in vu["prompt"] and v.precision == 1
    appliquer(con, [el], [v], juge.correcteur)
    assert juge.correcteur == "claude:haiku" and scheduler.note_finale(con, ev, 3) == 1

    monkeypatch.setattr(JugeClaude, "disponible", lambda self: True)
    monkeypatch.setattr(local.JugeLocal, "disponible", lambda self: False)
    assert isinstance(juge_depuis_reglage(con), JugeClaude)                    # auto, sans Ollama : Haiku
    set_reglage(con, "juge_local", "sonnet")
    assert juge_depuis_reglage(con).modele == "sonnet"
    set_reglage(con, "juge_local", "qwen3:8b")
    assert juge_depuis_reglage(con) is None                                     # modèle Ollama absent
