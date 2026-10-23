"""Évalue le juge distillé sur les mots tenus à l'écart (partie « test »), contre trois références.

    python scripts/distillation/evaluer.py [--modele ~/.lem/modeles/distille-…] [--haiku 400]

  1. le plafond   : l'accord du professeur avec lui-même (etiquettes-doubles.jsonl) ;
  2. l'existant   : la température bge-m3 de `lem.grading.direct` (notes 1–3 ramenées à l'échelle de précision) ;
  3. le remplacé  : Claude Haiku en headless sur N réponses de test (le juge qu'il remplacerait sur le Mac).

Mesures : κ pondéré (quadratique) sur la précision 1–5, F1 macro sur le type d'erreur. Écrit
DONNEES/evaluation.json.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sklearn.metrics import cohen_kappa_score, f1_score

from commun import DONNEES, lire_jsonl

from lem.grading.claude_headless import Element, appeler_claude, construire_prompt, extraire_json
from lem.grading.distille import JugeDistille
from lem.grading.taxonomie import NOTE_PAR_PRECISION, Erreur

from entrainer import partie

ERREURS = [e.value for e in Erreur]


def note(precision: int) -> int:
    return NOTE_PAR_PRECISION[int(precision)]


def notes_bge_m3(elements: list[Element]) -> dict[int, int]:
    """La note directe actuelle de lem : température bge-m3 (meilleur sens), seuils calibrés, règle de noter_recep."""
    import numpy as np

    from lem.vecteurs import OllamaEmbedder, texte_entree

    emb = OllamaEmbedder()
    bas, haut = 0.5309, 0.6918                      # seuils calibrés sur la vraie base (rapport WSL)
    out = {}
    for k in range(0, len(elements), 32):
        lot = elements[k:k + 32]
        rep = emb.embed([e.payload["reponse"] for e in lot])
        for e, v in zip(lot, rep):
            s = emb.embed([texte_entree(e.payload["mot"], x["definition"]) for x in e.payload["sens"]])
            t = max(0.0, min(100.0, (float((s @ v).max()) - bas) / (haut - bas) * 100))
            out[e.evenement_id] = 3 if t >= 70 else 2 if t >= 45 else 1
    return out


def accord(vp, pp, ve=None, pe=None) -> dict:
    out = {"n": len(vp), "kappa_pondere": round(cohen_kappa_score(vp, pp, weights="quadratic"), 3),
           "precision_exacte": round(sum(a == b for a, b in zip(vp, pp)) / max(1, len(vp)), 3)}
    if ve is not None:
        out["f1_macro_erreur"] = round(f1_score(ve, pe, average="macro", labels=ERREURS, zero_division=0), 3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modele", default=None)
    ap.add_argument("--haiku", type=int, default=400)
    a = ap.parse_args()
    echantillon = {o["cible"]["id"]: o for o in lire_jsonl(DONNEES / "echantillon.jsonl")}
    reponses = lire_jsonl(DONNEES / "reponses.jsonl")
    prof = {e["id"]: e["verdict"] for e in lire_jsonl(DONNEES / "etiquettes.jsonl")}
    test = [i for i in prof if partie(echantillon[reponses[i]["sens_id"]]["mot"]) == "test"]

    def element(i: int) -> Element:
        r = reponses[i]
        it = echantillon[r["sens_id"]]
        return Element(i, it["entree_id"], {"id": i, "mot": it["mot"], "categorie": it["categorie"], "sens": it["sens"],
                                            "voisins": [{"mot": v["mot"], "definition": v["definition"]} for v in it["voisins"]],
                                            "reponse": r["reponse"]})

    res = {}
    # 1. Plafond : le professeur contre lui-même.
    doubles = {e["id"]: e["verdict"] for e in lire_jsonl(DONNEES / "etiquettes-doubles.jsonl")}
    communs = [i for i in doubles if i in prof]
    if communs:
        res["professeur_contre_lui_meme"] = accord([prof[i]["precision"] for i in communs],
                                                   [doubles[i]["precision"] for i in communs],
                                                   [prof[i]["erreur"] for i in communs],
                                                   [doubles[i]["erreur"] for i in communs])
    # 2. L'élève distillé.
    j = JugeDistille(a.modele)
    if j.disponible():
        v = [j.juger(element(i)) for i in test]
        res["distille"] = accord([prof[i]["precision"] for i in test], [x.precision for x in v],
                                 [prof[i]["erreur"] for i in test], [x.erreur.value for x in v])
        res["distille"]["modele"] = j.modele
    # 3. Haiku sur un sous-ensemble du test.
    sous = random.Random(3).sample(test, min(a.haiku, len(test)))
    if sous:
        vh = {}
        for k in range(0, len(sous), 40):
            lot = [element(i) for i in sous[k:k + 40]]
            for o in extraire_json(appeler_claude(construire_prompt(lot), "haiku")):
                vh[o["id"]] = o
        ok = [i for i in sous if i in vh]
        res["haiku"] = accord([prof[i]["precision"] for i in ok], [vh[i]["precision"] for i in ok],
                              [prof[i]["erreur"] for i in ok], [vh[i]["erreur"] for i in ok])
        if j.disponible():
            vd = {x.id: x for x in (j.juger(element(i)) for i in ok)}
            res["distille_sur_le_meme_sous_ensemble"] = accord([prof[i]["precision"] for i in ok],
                                                               [vd[i].precision for i in ok],
                                                               [prof[i]["erreur"] for i in ok],
                                                               [vd[i].erreur.value for i in ok])
    # Tous à l'échelle de la note FSRS (1–4), seule échelle commune avec la température bge-m3.
    n_test = [i for i in test][:3000]
    els = [element(i) for i in n_test]
    b = notes_bge_m3(els)
    res["notes_fsrs"] = {"bge_m3_temperature": accord([note(prof[i]["precision"]) for i in n_test], [b[i] for i in n_test])}
    if j.disponible():
        res["notes_fsrs"]["distille"] = accord([note(prof[i]["precision"]) for i in n_test],
                                               [note(j.juger(element(i)).precision) for i in n_test])
    if sous and ok:
        res["notes_fsrs"]["haiku"] = accord([note(prof[i]["precision"]) for i in ok], [note(vh[i]["precision"]) for i in ok])
    res["effectifs"] = {"etiquettes": len(prof), "test": len(test), "doubles": len(communs)}
    (DONNEES / "evaluation.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
