"""Calibrage des seuils sur TES données (au lieu des valeurs par défaut).

bge-m3 a un cosinus de base élevé : deux textes sans rapport tournent autour de 0,4.
Les seuils « similaire par le sens » et la température des définitions doivent donc
être mesurés sur la base réelle :

  seuil_sens     : milieu entre le 95e centile des paires d'entrées au hasard et la
                   médiane des paires de synonymes (relations Wiktionnaire / JeuxDeMots).
  température    : 0° = 90e centile des paires de sens au hasard ;
                   100° = médiane des meilleures paires sens ↔ sens entre synonymes
                   (une « bonne paraphrase »).
"""

from __future__ import annotations

import random
import sqlite3

import numpy as np

from lem.db import set_reglage
from lem.vecteurs import Magasin


def calibrer_seuils(con: sqlite3.Connection, modele: str, n_hasard: int = 4000, graine: int = 0) -> dict:
    rng = random.Random(graine)
    me, ms = Magasin(con, "entree", modele), Magasin(con, "sens", modele)
    if len(me) < 50 or len(ms) < 50:
        raise ValueError("pas assez de vecteurs : lance `lem index` d'abord")
    paires_syn = [(a, b) for a, b in con.execute("SELECT source_id, cible_id FROM relation WHERE type = 'synonyme'")
                  if a in me.pos and b in me.pos]
    if len(paires_syn) < 20:
        raise ValueError("pas assez de paires de synonymes vectorisées pour calibrer")
    ids_e = list(me.pos)
    hasard_e = [me.sim(*rng.sample(ids_e, 2)) for _ in range(n_hasard)]
    syn_e = [me.sim(a, b) for a, b in paires_syn[:20000]]
    seuil_sens = (float(np.percentile(hasard_e, 95)) + float(np.median(syn_e))) / 2

    ids_s = list(ms.pos)
    hasard_s = [ms.sim(*rng.sample(ids_s, 2)) for _ in range(n_hasard)]
    sens_par_entree: dict[int, list[int]] = {}
    for sid, eid in con.execute("SELECT id, entree_id FROM sens"):
        if sid in ms.pos:
            sens_par_entree.setdefault(eid, []).append(sid)
    meilleures = []
    for a, b in paires_syn[:5000]:
        sa, sb = sens_par_entree.get(a), sens_par_entree.get(b)
        if sa and sb:
            meilleures.append(max(ms.sim(x, y) for x in sa for y in sb))
    bas = float(np.percentile(hasard_s, 90))
    haut = float(np.median(meilleures)) if meilleures else bas + 0.3
    if haut <= bas + 0.05:
        haut = bas + 0.05

    resultat = {
        "proximite": {"sens": round(seuil_sens, 4)},
        "definition": {"bas": round(bas, 4), "haut": round(haut, 4)},
        "mesures": {
            "paires_synonymes": len(paires_syn), "hasard_entrees_p95": round(float(np.percentile(hasard_e, 95)), 4),
            "synonymes_mediane": round(float(np.median(syn_e)), 4), "hasard_sens_p90": round(bas, 4),
        },
    }
    set_reglage(con, "seuils_proximite", resultat["proximite"])
    set_reglage(con, f"seuils_definition:{modele}", resultat["definition"])
    con.commit()
    return resultat
