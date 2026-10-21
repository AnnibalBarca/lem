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

    def __init__(self, con: sqlite3.Connection, entree_ids: set[int] | None = None):
        rows = con.execute("SELECT id, forme FROM entree").fetchall()
        rows = [r for r in rows if entree_ids is None or r["id"] in entree_ids]
        self.ids = [r["id"] for r in rows]
        self.formes = [normaliser(r["forme"]) for r in rows]

    def proches(self, forme: str, k: int = 20, seuil: float = 0.45) -> list[tuple[int, float]]:
        res = process.extract(normaliser(forme), self.formes, scorer=Levenshtein.normalized_similarity,
                              limit=k, score_cutoff=seuil)
        return [(self.ids[i], s) for _, s, i in res]

    def meme_terminaison(self, forme: str, n: int = 4, k: int = 30) -> list[int]:
        f = normaliser(forme)
        if len(f) < n + 2:
            return []
        fin = f[-n:]
        return [i for i, x in zip(self.ids, self.formes) if x.endswith(fin) and x != f][:k]


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
