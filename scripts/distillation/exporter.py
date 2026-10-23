"""Exporte un élève entraîné en ONNX, le quantifie en int8, et l'installe dans ~/.lem/modeles/distille-<version>/.

    python scripts/distillation/exporter.py --nom minilm [--version 2026-10-24-minilm]

Mesure ensuite la taille, la latence sur ce CPU (un fil et quatre fils) et l'écart de prédiction dû à la
quantification, sur les exemples de test.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import time
from datetime import date
from pathlib import Path

import numpy as np
import torch

from commun import DONNEES

from entrainer import Eleve, exemples

DOSSIER_MODELES = Path(os.environ.get("LEM_HOME", Path.home() / ".lem")) / "modeles"


class _Export(torch.nn.Module):
    def __init__(self, eleve):
        super().__init__()
        self.eleve = eleve

    def forward(self, input_ids, attention_mask):
        return self.eleve(input_ids, attention_mask)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nom", default="minilm")
    ap.add_argument("--version", default=None)
    a = ap.parse_args()
    import onnxruntime as ort
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoTokenizer

    source = DONNEES / "modeles" / a.nom
    meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
    version = a.version or f"{date.today().isoformat()}-{a.nom}"
    cible = DOSSIER_MODELES / f"distille-{version}"
    cible.mkdir(parents=True, exist_ok=True)

    eleve = Eleve(meta["base"])
    eleve.load_state_dict(torch.load(source / "eleve.pt", map_location="cpu"))
    eleve.eval()
    tok = AutoTokenizer.from_pretrained(source)
    enc = tok(["mot (nom) — 1. définition"], ["réponse"], return_tensors="pt")
    brut = cible / "modele-fp32.onnx"
    torch.onnx.export(_Export(eleve), (enc["input_ids"], enc["attention_mask"]), str(brut),
                      input_names=["input_ids", "attention_mask"], output_names=["precision", "erreur"],
                      dynamic_axes={"input_ids": {0: "lot", 1: "seq"}, "attention_mask": {0: "lot", 1: "seq"},
                                    "precision": {0: "lot"}, "erreur": {0: "lot"}}, opset_version=17,
                      dynamo=False)            # l'exportateur classique : graphe que la quantification lit bien
    quantize_dynamic(str(brut), str(cible / "modele.onnx"), weight_type=QuantType.QInt8)
    tok.backend_tokenizer.save(str(cible / "tokenizer.json"))
    (cible / "meta.json").write_text(json.dumps({**meta, "version": version}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    # Mesures : taille, latence, effet de la quantification sur les prédictions de test.
    taille = (cible / "modele.onnx").stat().st_size / 1e6
    test = exemples()["test"][:300]
    resultats = {}
    for fils in (1, 4):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = fils
        s8 = ort.InferenceSession(str(cible / "modele.onnx"), opts, providers=["CPUExecutionProvider"])
        lats, preds8 = [], []
        for x in test:
            e = tok([x["a"]], [x["b"]], truncation="only_first", max_length=meta["max_len"], return_tensors="np")
            t = time.perf_counter()
            p, _ = s8.run(None, {"input_ids": e["input_ids"].astype(np.int64),
                                 "attention_mask": e["attention_mask"].astype(np.int64)})
            lats.append((time.perf_counter() - t) * 1000)
            preds8.append(int(p[0].argmax()))
        resultats[fils] = (statistics.median(lats), sorted(lats)[int(0.95 * len(lats))], preds8)
    s32 = ort.InferenceSession(str(brut), providers=["CPUExecutionProvider"])
    accord = np.mean([int(s32.run(None, {"input_ids": tok([x["a"]], [x["b"]], truncation="only_first",
                                                          max_length=meta["max_len"], return_tensors="np")["input_ids"].astype(np.int64),
                                         "attention_mask": tok([x["a"]], [x["b"]], truncation="only_first",
                                                               max_length=meta["max_len"], return_tensors="np")["attention_mask"].astype(np.int64)})[0][0].argmax()) == p8
                      for x, p8 in zip(test, resultats[1][2])]) if test else float("nan")
    brut.unlink()
    print(f"installé : {cible}")
    print(f"taille int8 : {taille:.0f} Mo")
    for fils in (1, 4):
        print(f"latence CPU ({fils} fil{'s' if fils > 1 else ''}) : médiane {resultats[fils][0]:.1f} ms, 95e centile {resultats[fils][1]:.1f} ms")
    print(f"accord int8 / fp32 sur la précision (test) : {accord:.3f}")


if __name__ == "__main__":
    main()
