"""Échantillonne les sens à faire définir : des MOTS (ni noms propres, ni sigles, ni locutions), stratifiés par
fréquence, avec pour chacun ses voisins de forme et de sens (pour générer et reconnaître les confusions).

    python scripts/distillation/echantillonner.py --n 15000

Lecture seule de la base ; écrit DONNEES/echantillon.jsonl (repris s'il existe déjà).
"""

from __future__ import annotations

import argparse
import random
import sqlite3

from commun import BASE, DONNEES, RE_MOT, RE_PREFIXE, RE_RENVOI, ajouter_jsonl, lire_jsonl

# Bandes de fréquence (Zipf) et part de l'échantillon : surtout la zone où l'on apprend (1,5–4,5).
BANDES = [(0.0, 1.0, 0.08), (1.0, 2.0, 0.22), (2.0, 3.0, 0.32), (3.0, 4.0, 0.25), (4.0, 5.0, 0.09), (5.0, 9.0, 0.04)]
MAX_SENS = 8


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=15000)
    ap.add_argument("--graine", type=int, default=0)
    a = ap.parse_args()
    sortie = DONNEES / "echantillon.jsonl"
    deja = {o["entree_id"] for o in lire_jsonl(sortie)}
    if len(deja) >= a.n:
        print(f"déjà {len(deja)} sens échantillonnés")
        return
    rng = random.Random(a.graine)
    con = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sens: dict[int, list[dict]] = {}
    for r in con.execute("SELECT entree_id, id, definition FROM sens WHERE definition != '' ORDER BY entree_id, rang"):
        if not RE_RENVOI.match(r["definition"]):
            sens.setdefault(r["entree_id"], []).append({"id": r["id"], "definition": r["definition"]})
    pool = [r for r in con.execute(
        """SELECT id, forme, pos, COALESCE(zipf, 0) AS zipf FROM entree
           WHERE langue = 'fr' AND type IN ('mot', 'compose') AND instr(forme, ' ') = 0""")
            if r["id"] in sens and RE_MOT.match(r["forme"]) and not RE_PREFIXE.match(r["forme"])]
    print(f"mots candidats : {len(pool)}")
    choisis = []
    for lo, hi, part in BANDES:
        bande = [r for r in pool if lo <= r["zipf"] < hi and r["id"] not in deja]
        choisis += rng.sample(bande, min(len(bande), round(part * (a.n - len(deja)))))
    rng.shuffle(choisis)

    # Voisins : par la forme (même index que les grappes de confusables) et par le sens (bge-m3).
    from lem.confusables import IndexFormel
    from lem.contexte import Contexte

    ctx = Contexte(BASE)
    index = IndexFormel(ctx.con, {r["id"] for r in pool}, ctx.lexique)
    me = ctx.magasin_entrees
    formes = {r["id"]: r["forme"] for r in pool}

    def premiere_definition(eid: int) -> str | None:
        s = sens.get(eid)
        return s[0]["definition"] if s else None

    lot = []
    for k, r in enumerate(choisis, 1):
        ss = sens[r["id"]][:MAX_SENS]
        cible = ss[0] if len(ss) == 1 or rng.random() < 0.6 else rng.choice(ss[1:])
        voisins: list[dict] = []
        for vid, _ in index.proches(r["forme"], k=6):
            if formes.get(vid, r["forme"]) != r["forme"] and sum(v["nature"] == "forme" for v in voisins) < 2:
                voisins.append({"mot": formes[vid], "definition": premiere_definition(vid), "nature": "forme"})
        v = me.vecteur(r["id"]) if me is not None else None
        if v is not None:
            for x in me.voisins(v, k=10, exclure={r["id"]}):
                if formes.get(x.id, r["forme"]) != r["forme"] and all(w["mot"] != formes[x.id] for w in voisins) \
                        and sum(w["nature"] == "sens" for w in voisins) < 2:
                    voisins.append({"mot": formes[x.id], "definition": premiere_definition(x.id), "nature": "sens"})
        lot.append({"entree_id": r["id"], "mot": r["forme"], "categorie": r["pos"], "zipf": round(r["zipf"], 2),
                    "cible": cible, "sens": ss, "voisins": voisins})
        if len(lot) == 500:
            ajouter_jsonl(sortie, lot)
            lot = []
            print(f"  {k}/{len(choisis)}", flush=True)
    ajouter_jsonl(sortie, lot)
    print(f"échantillon : {len(lire_jsonl(sortie))} sens → {sortie}")


if __name__ == "__main__":
    main()
