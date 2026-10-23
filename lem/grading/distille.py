"""Juge distillé : un petit cross-encodeur (ONNX, int8) entraîné à reproduire le jugement de Claude Sonnet.

Il lit le mot, ses sens de référence et ses voisins d'une part, la réponse d'autre part, et rend la précision
(1–5) et le type d'erreur de la taxonomie fermée, avec une confiance calibrée. Même interface que `JugeLocal`
et `JugeClaude` ; correcteur « distille:<version> », entre Claude et le modèle local dans
`journal.PRIORITE_CORRECTEUR`. Il ne donne ni le sens visé ni le mot réellement défini (le dictionnaire
inversé de bge-m3 s'en charge).

Modèle : `~/.lem/modeles/distille-<version>/` (modele.onnx, tokenizer.json, meta.json), produit par
`scripts/distillation/exporter.py`. Dépendances légères et facultatives (`pip install -e ".[distille]"` :
onnxruntime, tokenizers) ; sans elles ou sans modèle, `disponible()` est faux et lem ne charge rien.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from lem.grading.claude_headless import Element, Verdict

DOSSIER_MODELES = Path(os.environ.get("LEM_HOME", Path.home() / ".lem")) / "modeles"
MAX_SENS, MAX_VOISINS = 6, 4


def texte_reference(payload: dict) -> str:
    """Ce que l'élève lit en premier : le mot, ses sens, ses voisins. Identique à l'entraînement et en jeu."""
    sens = " | ".join(f"{k}. {s['definition']}" for k, s in enumerate(payload.get("sens", [])[:MAX_SENS], 1))
    voisins = " ; ".join(f"{v['mot']} : {v['definition']}" for v in payload.get("voisins", [])[:MAX_VOISINS]
                         if v.get("definition"))
    return f"{payload['mot']} ({payload.get('categorie', '')}) — {sens}" + (f" — voisins : {voisins}" if voisins else "")


def dernier_modele() -> Path | None:
    """Le modèle distillé le plus récent (nom de dossier le plus grand), ou None."""
    if not DOSSIER_MODELES.exists():
        return None
    candidats = sorted(d for d in DOSSIER_MODELES.glob("distille-*") if (d / "modele.onnx").exists())
    return candidats[-1] if candidats else None


def _softmax(x, t: float):
    import numpy as np

    z = x / t
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


class JugeDistille:
    _cache: dict[str, tuple] = {}          # dossier → (session, tokenizer, meta), chargés une fois par processus

    def __init__(self, dossier: str | Path | None = None):
        self.dossier = Path(dossier) if dossier else dernier_modele()
        self.derniere_latence = None

    @property
    def modele(self) -> str:
        return self.dossier.name.removeprefix("distille-") if self.dossier else "absent"

    @property
    def correcteur(self) -> str:
        return f"distille:{self.modele}"

    def disponible(self) -> bool:
        if self.dossier is None or not (self.dossier / "modele.onnx").exists():
            return False
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
        except ImportError:
            return False
        return True

    def _charger(self):
        cle = str(self.dossier)
        if cle not in self._cache:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            meta = json.loads((self.dossier / "meta.json").read_text(encoding="utf-8"))
            tok = Tokenizer.from_file(str(self.dossier / "tokenizer.json"))
            tok.enable_truncation(max_length=meta.get("max_len", 256), strategy="only_first")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(4, os.cpu_count() or 1)
            sess = ort.InferenceSession(str(self.dossier / "modele.onnx"), opts, providers=["CPUExecutionProvider"])
            self._cache[cle] = (sess, tok, meta)
        return self._cache[cle]

    def juger(self, el: Element) -> Verdict:
        import numpy as np

        t = time.monotonic()
        sess, tok, meta = self._charger()
        enc = tok.encode(texte_reference(el.payload), el.payload.get("reponse") or "")
        ids = np.array([enc.ids], dtype=np.int64)
        masque = np.array([enc.attention_mask], dtype=np.int64)
        logits_p, logits_e = sess.run(None, {"input_ids": ids, "attention_mask": masque})
        pp = _softmax(logits_p[0], meta.get("temperature_precision", 1.0))
        pe = _softmax(logits_e[0], meta.get("temperature_erreur", 1.0))
        v = Verdict(id=el.evenement_id, sens_vise_id=None, precision=int(pp.argmax()) + 1,
                    erreur=meta["erreurs"][int(pe.argmax())], mot_reellement_defini=None,
                    confiance=float(pp.max()), commentaire="")
        self.derniere_latence = time.monotonic() - t
        return v
