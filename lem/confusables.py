"""Grappes de mots confondables : acronyme / sigle / homonyme / éponyme…

Pour une entrée cible, on réunit trois voisinages puis on les compare sur tous les axes :
  * formels    : rapidfuzz sur les lemmes (même allure, même son) + familles d'affixes ;
  * sémantiques: plus proches voisins bge-m3 ;
  * personnels : tes confusions enregistrées dans le journal.
Le résultat est trié par un score combiné et peut être restreint à un domaine (« science »).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from lem.db import domaines_descendants
from lem.similarite import Comparaison, comparer, normaliser


@dataclass
class Confusable:
    entree_id: int
    forme: str
    comparaison: Comparaison
    score: float


class IndexFormel:
    """Lemmes normalisés, pour la recherche floue (≈ 30 ms sur 160 k lemmes)."""

    def __init__(self, con: sqlite3.Connection, entree_ids: set[int] | None = None, lexique=None):
        if lexique is not None:                     # lemmes déjà normalisés dans l'index du lexique
            paires = [(int(e), f) for e, f in zip(lexique.eids, lexique.lemmes)
                      if entree_ids is None or int(e) in entree_ids]
        else:
            paires = [(r["id"], normaliser(r["forme"])) for r in con.execute("SELECT id, forme FROM entree")
                      if entree_ids is None or r["id"] in entree_ids]
        self.ids = [e for e, _ in paires]
        self.formes = [f for _, f in paires]
        self._fins: dict[int, dict[str, list[int]]] = {}

    def proches(self, forme: str, k: int = 20, seuil: float = 0.45) -> list[tuple[int, float]]:
        # Tous les cœurs (cdist, workers=-1) : 551 k lemmes en quelques ms au lieu de ≈ 40 ms.
        sc = process.cdist([normaliser(forme)], self.formes, scorer=Levenshtein.normalized_similarity,
                           score_cutoff=seuil, workers=-1, dtype=np.float32)[0]
        n = min(k, int((sc > 0).sum()))
        if n == 0:
            return []
        top = np.argpartition(-sc, n - 1)[:n]
        top = top[np.lexsort((top, -sc[top]))]          # score décroissant, puis ordre de l'index (comme extract)
        return [(self.ids[i], float(sc[i])) for i in top]

    def meme_terminaison(self, forme: str, n: int = 4, k: int = 30) -> list[int]:
        f = normaliser(forme)
        if len(f) < n + 2:
            return []
        if n not in self._fins:                 # index des terminaisons, construit une fois (ordre conservé)
            fins: dict[str, list[int]] = {}
            for j, x in enumerate(self.formes):
                if len(x) >= n:
                    fins.setdefault(x[-n:], []).append(j)
            self._fins[n] = fins
        out = []
        for j in self._fins[n].get(f[-n:], ()):
            if self.formes[j] != f:
                out.append(self.ids[j])
                if len(out) == k:
                    break
        return out


def grappe(con: sqlite3.Connection, entree_id: int, magasin=None, index: IndexFormel | None = None,
           k: int = 8, domaines: list[str] | None = None) -> list[Confusable]:
    cible = con.execute("SELECT forme FROM entree WHERE id = ?", (entree_id,)).fetchone()
    if cible is None:
        return []
    index = index or IndexFormel(con)
    candidats: set[int] = set()
    candidats.update(i for i, _ in index.proches(cible["forme"], k=3 * k))
    candidats.update(index.meme_terminaison(cible["forme"]))
    if magasin is not None and len(magasin):
        v = magasin.vecteur(entree_id)
        if v is not None:
            candidats.update(x.id for x in magasin.voisins(v, k=3 * k, exclure={entree_id}))
    candidats.update(
        r[0] for r in con.execute(
            """SELECT cible_id FROM relation WHERE source_id = ? AND type IN ('confusion_perso', 'paronyme', 'synonyme')
               UNION SELECT source_id FROM relation WHERE cible_id = ? AND type IN ('confusion_perso', 'paronyme')""",
            (entree_id, entree_id),
        )
    )
    candidats.discard(entree_id)
    if domaines:
        dids = domaines_descendants(con, domaines)
        if dids:
            ok = {
                r[0] for r in con.execute(
                    f"""SELECT DISTINCT s.entree_id FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
                        WHERE sd.domaine_id IN ({', '.join('?' * len(dids))})""",
                    dids,
                )
            }
            candidats &= ok
    out = []
    for c in candidats:
        comp = comparer(con, entree_id, c, magasin)
        sem = comp.sens if comp.sens is not None else 0.0
        score = max(comp.forme, sem) + 0.5 * min(comp.forme, sem) + min(comp.memoire, 3) * 0.3
        forme = con.execute("SELECT forme FROM entree WHERE id = ?", (c,)).fetchone()["forme"]
        out.append(Confusable(c, forme, comp, score))
    out.sort(key=lambda x: -x.score)
    return out[:k]
