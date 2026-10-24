"""How useful is a new word to learn? Cards only: Bomb, Syllabary and Verbal memory never read or change this.

The frequency (wordfreq's Zipf, Lexique) is already in `memory.knowledge.score_apprentissage`. What it cannot see
is a word you would understand anyway because it is built from words you know: « anti-cartésien » is
« anti- » + « cartésien ». This module measures that **transparency** and turns it into a factor in ]0, 1] on the
priority of a new word. An opaque compound — « belle-de-jour », a flower whose meaning is nowhere in
« belle », « de » and « jour » — keeps a factor of 1: the word has to be learned.

Same idea as AZUR (azur-poetry): the value of a word lies in the distance between what its parts predict and
what it actually means.

  * components  : the words it is made of (hyphenated parts, or a known prefix + a base that is itself an entry);
  * transparency: cosine between the word's vector and the mean of its components' vectors (bge-m3), rescaled to
                  0..1; without vectors, the share of components whose stem appears in the word's definition;
  * factor      : 1 − 0.85 × transparency.

« Useless » (`ecarter`): a key in Cards hides a word for good. A separate table, never touched by `lem rebuild`.
"""

from __future__ import annotations

import sqlite3

import numpy as np

from lem.db import iso

PREFIXES = (
    "anti", "archi", "auto", "bi", "co", "contre", "dé", "dés", "demi", "ex", "extra", "hyper", "hypo", "in",
    "inter", "intra", "mal", "mi", "mono", "multi", "non", "para", "péri", "poly", "post", "pré", "pro", "pseudo",
    "quasi", "re", "ré", "semi", "sous", "sub", "super", "supra", "sur", "trans", "ultra", "vice", "pluri", "néo",
)
MOTS_OUTILS = {"de", "du", "des", "la", "le", "les", "l", "d", "en", "à", "au", "aux", "et", "sur", "sous", "a",
               "un", "une", "par", "pour", "sans", "ou"}
COSINUS_BAS, COSINUS_HAUT = 0.62, 0.76      # transparency 0 below, 1 above (bge-m3: unrelated ≈ 0.3–0.45)
FORCE = 0.85                                 # a fully transparent word keeps 15 % of its priority


def ecarter(con: sqlite3.Connection, entree_id: int) -> None:
    con.execute("INSERT OR IGNORE INTO mot_ecarte(entree_id, ts) VALUES(?, ?)", (entree_id, iso()))
    con.commit()


def ecartes(con: sqlite3.Connection) -> set[int]:
    return {r[0] for r in con.execute("SELECT entree_id FROM mot_ecarte")}


def _entree(con: sqlite3.Connection, forme: str) -> int | None:
    r = con.execute("SELECT id FROM entree WHERE forme = ? AND type NOT IN ('nom_propre', 'sigle') "
                    "ORDER BY zipf DESC LIMIT 1", (forme,)).fetchone()
    return r[0] if r else None


def composants(con: sqlite3.Connection, forme: str) -> list[tuple[str, int]]:
    """(component, entry id) pairs a word is built from; [] when it is not built from other entries."""
    f = forme.lower()
    if "-" in f:
        parts = [p for p in f.split("-") if p and p not in MOTS_OUTILS and len(p) > 1]
        out = [(p, i) for p in parts if (i := _entree(con, p)) is not None]
        if out:
            return out
    for pref in sorted(PREFIXES, key=len, reverse=True):
        if f.startswith(pref):
            base = f[len(pref):].lstrip("-")
            if len(base) >= 4 and (i := _entree(con, base)) is not None:
                return [(base, i)]
    return []


def _definition(con: sqlite3.Connection, entree_id: int) -> str:
    r = con.execute("SELECT definition FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
                    (entree_id,)).fetchone()
    return (r[0] if r else "").lower()


def transparence(con: sqlite3.Connection, entree_id: int, forme: str, magasin=None) -> float:
    """0 = opaque (or not built from other words), 1 = fully predictable from its components."""
    parts = composants(con, forme)
    if not parts:
        return 0.0
    if magasin is not None:
        v = magasin.vecteur(entree_id)
        vs = [w for _, i in parts if (w := magasin.vecteur(i)) is not None]
        if v is not None and vs:
            m = np.mean(vs, axis=0)
            n = float(np.linalg.norm(m) * np.linalg.norm(v))
            if n > 0:
                cos = float(v @ m) / n
                return min(1.0, max(0.0, (cos - COSINUS_BAS) / (COSINUS_HAUT - COSINUS_BAS)))
    definition = _definition(con, entree_id)
    return sum(1 for p, _ in parts if p[:5] in definition) / len(parts)


def facteurs(con: sqlite3.Connection, lignes, magasin=None) -> dict[int, float]:
    """Priority factor in [0.15, 1] for rows with `id` and `forme`."""
    return {r["id"]: 1.0 - FORCE * transparence(con, r["id"], r["forme"], magasin) for r in lignes}
