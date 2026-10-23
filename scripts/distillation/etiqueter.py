"""Fait juger les réponses générées par le professeur (Claude Sonnet en headless, sur l'abonnement).

    python scripts/distillation/etiqueter.py [--professeur sonnet] [--lot 40] [--fils 3] [--suivre]
    python scripts/distillation/etiqueter.py --doubles 1000      # second jugement : l'accord du professeur avec lui-même

Exactement la consigne et le vocabulaire fermé de la notation réelle (`claude_headless.construire_prompt`).
Une réponse = l'identifiant de sa ligne dans reponses.jsonl (fichier en ajout seul). Écrit etiquettes.jsonl
(ou etiquettes-doubles.jsonl) au fil de l'eau ; reprend où il s'est arrêté. --suivre : attend les réponses
que la génération continue d'écrire, jusqu'à ce qu'elle ait fini.
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from commun import DONNEES, ajouter_jsonl, lire_jsonl

from lem.grading.claude_headless import Element, Verdict, appeler_claude, construire_prompt, extraire_json

VERROU = threading.Lock()


def elements(echantillon: dict, reponses: list[dict], ids: list[int]) -> list[Element]:
    out = []
    for i in ids:
        r = reponses[i]
        it = echantillon[r["sens_id"]]
        out.append(Element(i, it["entree_id"], {
            "id": i, "mot": it["mot"], "categorie": it["categorie"], "sens": it["sens"],
            "voisins": [{"mot": v["mot"], "definition": v["definition"]} for v in it["voisins"]],
            "reponse": r["reponse"]}))
    return out


def juger_lot(lot: list[Element], professeur: str, sortie) -> int:
    for essai in range(3):
        try:
            brut = extraire_json(appeler_claude(construire_prompt(lot), professeur))
            break
        except Exception as exc:                          # quota, coupure, sortie illisible : on réessaie plus tard
            print(f"  lot {lot[0].evenement_id}… : échec ({str(exc)[:120]})", flush=True)
            time.sleep(60 * (essai + 1))
    else:
        return 0
    ids = {e.evenement_id for e in lot}
    lignes = []
    for o in brut:
        try:
            v = Verdict.model_validate(o)
        except Exception:
            continue
        if v.id in ids:
            lignes.append({"id": v.id, "professeur": f"claude:{professeur}", "verdict": v.model_dump(mode="json"),
                           "ts": time.time()})
    with VERROU:
        ajouter_jsonl(sortie, lignes)
    return len(lignes)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--professeur", default="sonnet")
    ap.add_argument("--lot", type=int, default=40)
    ap.add_argument("--fils", type=int, default=3)
    ap.add_argument("--limite", type=int, default=None, help="au plus N réponses jugées dans ce passage")
    ap.add_argument("--doubles", type=int, default=0, help="second jugement de N réponses tirées au hasard")
    ap.add_argument("--suivre", action="store_true")
    a = ap.parse_args()
    echantillon = {o["cible"]["id"]: o for o in lire_jsonl(DONNEES / "echantillon.jsonl")}
    sortie = DONNEES / ("etiquettes-doubles.jsonl" if a.doubles else "etiquettes.jsonl")
    total = 0
    while True:
        reponses = lire_jsonl(DONNEES / "reponses.jsonl")
        faits = {o["id"] for o in lire_jsonl(sortie)}
        if a.doubles:
            deja = {o["id"] for o in lire_jsonl(DONNEES / "etiquettes.jsonl")}
            tirage = random.Random(7).sample(sorted(deja), min(a.doubles, len(deja)))
            a_faire = [i for i in tirage if i not in faits]
            random.Random(8).shuffle(a_faire)             # d'autres voisins de lot qu'au premier jugement
        else:
            a_faire = [i for i in range(len(reponses)) if i not in faits]
        if a.limite is not None:
            a_faire = a_faire[: max(0, a.limite - total)]
        lots = [elements(echantillon, reponses, a_faire[k:k + a.lot]) for k in range(0, len(a_faire), a.lot)]
        if not a.suivre or a.doubles:
            pass
        else:
            lots = [l for l in lots if len(l) == a.lot]  # en suivant la génération : seulement des lots pleins
        t0 = time.time()
        with ThreadPoolExecutor(a.fils) as ex:
            for n in ex.map(lambda l: juger_lot(l, a.professeur, sortie), lots):
                total += n
                print(f"  {total} jugées · {(time.time() - t0) / max(1, total) * 1000:.0f} ms/réponse", flush=True)
        generation_finie = (DONNEES / "journaux" / "generation.log").exists() and \
            "fait :" in (DONNEES / "journaux" / "generation.log").read_text(encoding="utf-8")
        if not a.suivre or a.doubles or (generation_finie and not lots) or (a.limite and total >= a.limite):
            break
        if not lots:
            time.sleep(300)
    print(f"fini : {total} réponses jugées → {sortie}")


if __name__ == "__main__":
    main()
