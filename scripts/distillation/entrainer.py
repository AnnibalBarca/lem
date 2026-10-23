"""Entraîne l'élève : un cross-encodeur qui lit (mot, sens de référence, voisins) et la réponse ensemble, et
prédit la précision (1–5) et le type d'erreur de la taxonomie fermée — comme le professeur.

    python scripts/distillation/entrainer.py --base nreimers/mMiniLMv2-L12-H384-distilled-from-XLMR-Large --nom minilm
    python scripts/distillation/entrainer.py --base BAAI/bge-reranker-base --nom base

Découpage par MOT (un mot n'est jamais à la fois en entraînement et en test), stable d'une exécution à l'autre.
Écrit DONNEES/modeles/<nom>/ (poids PyTorch, tokenizer, meta.json avec la température de calibrage).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import cohen_kappa_score, f1_score
from torch import nn
from transformers import AutoModel, AutoTokenizer

from commun import DONNEES, lire_jsonl

from lem.grading.distille import MAX_SENS, MAX_VOISINS, texte_reference
from lem.grading.taxonomie import Erreur

ERREURS = [e.value for e in Erreur]


def partie(mot: str) -> str:
    """test (10 %), validation (5 %) ou entrainement, par mot."""
    h = int(hashlib.blake2b(mot.encode(), digest_size=4).hexdigest(), 16) % 100
    return "test" if h < 10 else "validation" if h < 15 else "entrainement"


def exemples() -> dict[str, list[dict]]:
    echantillon = {o["cible"]["id"]: o for o in lire_jsonl(DONNEES / "echantillon.jsonl")}
    reponses = lire_jsonl(DONNEES / "reponses.jsonl")
    out: dict[str, list[dict]] = {"entrainement": [], "validation": [], "test": []}
    for e in lire_jsonl(DONNEES / "etiquettes.jsonl"):
        r = reponses[e["id"]]
        it = echantillon[r["sens_id"]]
        v = e["verdict"]
        out[partie(it["mot"])].append({"id": e["id"], "a": texte_reference(it), "b": r["reponse"],
                                       "precision": v["precision"] - 1, "erreur": ERREURS.index(v["erreur"])})
    return out


class Eleve(nn.Module):
    def __init__(self, base: str):
        super().__init__()
        self.encodeur = AutoModel.from_pretrained(base)
        h = self.encodeur.config.hidden_size
        self.abandon = nn.Dropout(0.1)
        self.precision = nn.Linear(h, 5)
        self.erreur = nn.Linear(h, len(ERREURS))

    def forward(self, input_ids, attention_mask):
        cls = self.encodeur(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state[:, 0]
        cls = self.abandon(cls)
        return self.precision(cls), self.erreur(cls)


def lots(xs, tok, taille, max_len, melanger, rng):
    ordre = rng.permutation(len(xs)) if melanger else np.arange(len(xs))
    for k in range(0, len(xs), taille):
        b = [xs[i] for i in ordre[k:k + taille]]
        enc = tok([x["a"] for x in b], [x["b"] for x in b], truncation="only_first", max_length=max_len,
                  padding=True, return_tensors="pt")
        yield enc, torch.tensor([x["precision"] for x in b]), torch.tensor([x["erreur"] for x in b])


@torch.no_grad()
def predire(modele, xs, tok, max_len, dev):
    modele.eval()
    if not xs:
        return torch.zeros(0, 5), torch.zeros(0, len(ERREURS))
    lp, le = [], []
    for enc, _, _ in lots(xs, tok, 64, max_len, False, None):
        with torch.autocast(dev, dtype=torch.bfloat16, enabled=dev == "cuda"):
            p, e = modele(enc["input_ids"].to(dev), enc["attention_mask"].to(dev))
        lp.append(p.float().cpu())
        le.append(e.float().cpu())
    return torch.cat(lp), torch.cat(le)


def mesures(logits_p, logits_e, xs) -> dict:
    if not xs:
        return {"kappa_pondere": 0.0, "precision_exacte": 0.0, "precision_a_1_pres": 0.0, "f1_macro_erreur": 0.0, "n": 0}
    vp, ve = [x["precision"] for x in xs], [x["erreur"] for x in xs]
    pp, pe = logits_p.argmax(1).tolist(), logits_e.argmax(1).tolist()
    return {"kappa_pondere": round(cohen_kappa_score(vp, pp, weights="quadratic"), 4),
            "precision_exacte": round(float(np.mean(np.array(vp) == np.array(pp))), 4),
            "precision_a_1_pres": round(float(np.mean(np.abs(np.array(vp) - np.array(pp)) <= 1)), 4),
            "f1_macro_erreur": round(f1_score(ve, pe, average="macro", labels=list(range(len(ERREURS))),
                                              zero_division=0), 4),
            "n": len(xs)}


def calibrer(logits: torch.Tensor, verite: list[int]) -> float:
    """Température qui minimise la log-vraisemblance négative sur la validation (confiance calibrée)."""
    if not verite:
        return 1.0
    y = torch.tensor(verite)
    meilleure, t_opt = math.inf, 1.0
    for t in np.linspace(0.5, 4.0, 36):
        nll = nn.functional.cross_entropy(logits / t, y).item()
        if nll < meilleure:
            meilleure, t_opt = nll, float(t)
    return t_opt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="nreimers/mMiniLMv2-L12-H384-distilled-from-XLMR-Large")
    ap.add_argument("--nom", default="minilm")
    ap.add_argument("--epoques", type=int, default=4)
    ap.add_argument("--lot", type=int, default=32)
    ap.add_argument("--lr", type=float, default=4e-5)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--graine", type=int, default=0)
    a = ap.parse_args()
    torch.manual_seed(a.graine)
    rng = np.random.default_rng(a.graine)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    donnees = exemples()
    print({k: len(v) for k, v in donnees.items()}, "·", dev, flush=True)
    tok = AutoTokenizer.from_pretrained(a.base)
    modele = Eleve(a.base).to(dev)
    opt = torch.optim.AdamW(modele.parameters(), lr=a.lr, weight_decay=0.01)
    etapes = a.epoques * math.ceil(len(donnees["entrainement"]) / a.lot)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=max(1, etapes), pct_start=0.1)
    ce = nn.CrossEntropyLoss()
    sortie = DONNEES / "modeles" / a.nom
    meilleur = -1.0
    t0 = time.time()
    for ep in range(a.epoques):
        modele.train()
        for enc, yp, ye in lots(donnees["entrainement"], tok, a.lot, a.max_len, True, rng):
            with torch.autocast(dev, dtype=torch.bfloat16, enabled=dev == "cuda"):
                p, e = modele(enc["input_ids"].to(dev), enc["attention_mask"].to(dev))
                perte = ce(p.float(), yp.to(dev)) + ce(e.float(), ye.to(dev))
            opt.zero_grad()
            perte.backward()
            nn.utils.clip_grad_norm_(modele.parameters(), 1.0)
            opt.step()
            sched.step()
        lp, le = predire(modele, donnees["validation"], tok, a.max_len, dev)
        m = mesures(lp, le, donnees["validation"])
        print(f"époque {ep + 1}/{a.epoques} · {time.time() - t0:.0f} s · validation {m}", flush=True)
        score = m["kappa_pondere"] + m["f1_macro_erreur"]
        if score > meilleur:
            meilleur = score
            sortie.mkdir(parents=True, exist_ok=True)
            torch.save(modele.state_dict(), sortie / "eleve.pt")
    modele.load_state_dict(torch.load(sortie / "eleve.pt"))
    lp, le = predire(modele, donnees["validation"], tok, a.max_len, dev)
    t_p = calibrer(lp, [x["precision"] for x in donnees["validation"]])
    t_e = calibrer(le, [x["erreur"] for x in donnees["validation"]])
    lp, le = predire(modele, donnees["test"], tok, a.max_len, dev)
    test = mesures(lp, le, donnees["test"])
    tok.save_pretrained(sortie)
    meta = {"base": a.base, "nom": a.nom, "erreurs": ERREURS, "max_len": a.max_len, "temperature_precision": t_p,
            "temperature_erreur": t_e, "test": test, "effectifs": {k: len(v) for k, v in donnees.items()},
            "duree_s": round(time.time() - t0), "max_sens": MAX_SENS, "max_voisins": MAX_VOISINS}
    (sortie / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("test :", test, "· températures", t_p, t_e, flush=True)


if __name__ == "__main__":
    main()
