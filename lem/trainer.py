"""The word-bomb trainer: which words should you learn to never run dry?

A prompt of the Bomb is a 2- or 3-letter sequence (« sub-500 »: fewer than 500 valid words contain it). You
survive a prompt if you can produce ONE word containing the sequence, fast. So the vocabulary that matters is
not "the most common words" but the words that cover many sequences you cannot answer yet — the long,
multisyllabic ones (*électroencéphalographie*, *anticonstitutionnellement*) that contain a dozen rare
sequences each.

Three steps, all computed from the database and your knowledge map:

  1. coverage  how much you already know of each sequence: k(s) = Σ P(known) over the valid words that
               contain s (a word counts through its entry's P(connu)) ;
  2. need      how much you are likely to be hurt by a sequence: need(s) = w(s) · exp(−k(s) / K0), where w(s)
               is the chance that s is thrown at you (proportional to the number of words that contain it,
               among the sequences the standard mode draws: `sub`, at least `minimum`, `attested` words in
               use) and K0 how many words you need to feel safe (≈ 2) ;
  3. set cover choose words greedily by the need they remove, so that each new word covers sequences the
               previous ones did not: the shortest list that closes the most gaps.

Everything is vectorised (numpy) over the ~2 million valid word forms; ≈ 25 s on the real database, once per
call. Nothing here writes to the database: `lem gaps` prints, the Cards read `ideal_words` when their
intention is « bombe ».
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from lem.suites import BASE, LONGUEURS, IndexSuites, code, paires, suite_de

K0 = 2.0                    # words you must know for a sequence to count as covered
DECAY = 0.35                # a sequence covered by a chosen word keeps this share of its need
MIN_LENGTH = 8              # shorter words cover too little to be worth a study slot
KNOWN = 0.6                 # P(known) from which a word is "yours" and stops being a candidate


@dataclass
class Gap:
    sequence: str
    words: int              # valid words containing it (sub-N counts)
    known_mass: float       # k(s): expected number of those words you know
    need: float


@dataclass
class IdealWord:
    form: str
    entry_id: int | None
    definition: str
    p_known: float | None
    score: float            # need removed when it was chosen (diminishing: set cover)
    covers: list[tuple[str, int]] = field(default_factory=list)     # (sequence, words) newly closed
    prompts: int = 0        # distinct drawable sequences (sub-N) the word answers


def _p_per_norm(lex, carte, valid: np.ndarray) -> np.ndarray:
    """P(known) of each norm (the best of its entries) ; 0 for invalid norms."""
    entry_of_row = np.repeat(np.arange(len(lex.eids)), np.diff(lex.e_ptr))
    p_entry = np.zeros(len(lex.eids))
    pos = np.searchsorted(lex.eids, carte.ids)
    ok = (pos < len(lex.eids)) & (lex.eids[np.minimum(pos, len(lex.eids) - 1)] == carte.ids)
    # A word only counts as yours if frequency says an adult knows it (prior ≥ 0.45) or you gave evidence on it:
    # the θ of the knowledge map also lift words you never met, and a trainer must not trust that for rare words.
    prior = np.array([carte._prior.get(int(i), 0.0) for i in carte.ids])
    seen = np.array([carte._nobs.get(int(i), 0) > 0 for i in carte.ids])
    p = carte.p_tous()
    p = np.where((prior >= 0.45) | seen, p, np.minimum(p, KNOWN - 0.01))
    p_entry[pos[ok]] = p[ok]
    p_norm = np.zeros(len(lex.normes))
    np.maximum.at(p_norm, lex.e_nid, p_entry[entry_of_row])
    return np.where(valid, p_norm, 0.0)


def analyse(lex, carte, *, sub: int = 500, minimum: int = 3, attested: int = 2,
            lengths: tuple[int, ...] = LONGUEURS, suites: IndexSuites | None = None) -> "Analysis":
    suites = suites or IndexSuites(lex)
    ids = np.nonzero(suites.valide)[0]
    norms = [lex.normes.restore_key(int(i)) for i in ids]
    p_norm = _p_per_norm(lex, carte, suites.valide)[ids]
    known = {L: np.zeros(BASE ** L) for L in LONGUEURS}
    from lem.suites import _compter

    _compter(norms, known, poids=p_norm)
    need = {}
    for L in LONGUEURS:
        n = suites.comptes[L].astype(float)
        eligible = (L in lengths) & (n >= minimum) & (n < sub) & (suites.attestes[L] >= attested)
        w = np.where(eligible, n, 0.0)
        w = w / max(w.sum(), 1.0)
        need[L] = w * np.exp(-known[L] / K0)
    a = Analysis(lex, carte, suites, ids, norms, p_norm, known, need)
    a.need_static = {L: np.where(need[L] > 0, 1.0, 0.0) for L in LONGUEURS}      # which sequences can be drawn
    return a


class Analysis:
    def __init__(self, lex, carte, suites, ids, norms, p_norm, known, need):
        self.lex, self.carte, self.suites = lex, carte, suites
        self.ids, self.norms, self.p_norm, self.known, self.need = ids, norms, p_norm, known, need

    # -- the gaps ----------------------------------------------------------------------------------
    def gaps(self, top: int = 25) -> list[Gap]:
        out = []
        for L, need in self.need.items():
            for i in np.argsort(-need)[:top]:
                if need[i] > 0:
                    s = suite_de(int(i), L)
                    out.append(Gap(s, int(self.suites.comptes[L][i]), float(self.known[L][i]), float(need[i])))
        out.sort(key=lambda g: -g.need)
        return out[:top]

    def total_need(self) -> float:
        return float(sum(n.sum() for n in self.need.values()))

    # -- the words ---------------------------------------------------------------------------------
    def _candidates(self, n: int, attested_only: bool, min_length: int) -> np.ndarray:
        """Indices (in self.norms) of the `n` unknown words that remove the most need, before set cover."""
        lengths = np.fromiter((len(x) for x in self.norms), dtype=np.int32, count=len(self.norms))
        score = np.zeros(len(self.norms))
        for a, L, idx, c in paires(self.norms):
            score += np.bincount(idx, weights=self.need[L][c], minlength=len(self.norms))
        eligible = (self.p_norm < KNOWN) & (lengths >= min_length) & (score > 0)
        if attested_only:
            eligible &= self.suites.atteste[self.ids]
        value = np.where(eligible, score / np.sqrt(lengths), 0.0)
        top = np.argpartition(-value, min(n, len(value) - 1))[:n]
        return top[value[top] > 0]

    def ideal_words(self, count: int = 40, *, attested_only: bool = True, min_length: int = MIN_LENGTH,
                    pool: int = 6000) -> list[IdealWord]:
        """Greedy set cover: `count` words, each chosen for the need it removes that the previous ones did not."""
        cand = self._candidates(pool, attested_only, min_length)
        if not len(cand):
            return []
        cand_norms = [self.norms[i] for i in cand]
        # (candidate, sequence) pairs, once per word
        rows, cols, lens = [], [], []
        for a, L, idx, c in paires(cand_norms):
            rows.append(idx)
            cols.append(c + (0 if L == 2 else BASE ** 2))        # one index space: L=2 then L=3
            lens.append(np.full(len(idx), L))
        rows, cols = np.concatenate(rows), np.concatenate(cols)
        need = np.concatenate([self.need[2], self.need[3]]).copy()
        lengths = np.array([len(x) for x in cand_norms], dtype=float)
        chosen: list[IdealWord] = []
        alive = np.ones(len(cand), dtype=bool)
        for _ in range(count):
            gain = np.bincount(rows, weights=need[cols], minlength=len(cand)) / np.sqrt(lengths)
            gain[~alive] = -1
            k = int(np.argmax(gain))
            if gain[k] <= 0:
                break
            mine = cols[rows == k]
            closed = [(int(c), float(need[c])) for c in mine if need[c] > 1e-9]
            closed.sort(key=lambda x: -x[1])
            chosen.append(self._describe(cand_norms[k], float(gain[k] * math.sqrt(lengths[k])), closed))
            need[mine] *= DECAY
            alive[k] = False
        return chosen

    def _describe(self, norm: str, score: float, closed: list[tuple[int, float]]) -> IdealWord:
        from lem.recherche import _enrichir

        lex = self.lex
        forms = lex.formes_de_norme(norm)
        form = min(forms, key=lambda f: (not f.isalpha(), len(f), f)) if forms else norm
        entries = list(lex.entrees(form))
        info = _enrichir(self.carte.con, entries[:5]) if entries else {}
        eid = next((e for e in entries if e in info), entries[0] if entries else None)
        p = float(self.carte.p(eid)) if eid is not None else None
        covers = []
        for c, _ in closed[:12]:
            L = 2 if c < BASE ** 2 else 3
            c0 = c if L == 2 else c - BASE ** 2
            s = suite_de(c0, L)
            covers.append((s, int(self.suites.comptes[L][c0])))
        prompts = 0
        for _, L, _, cs in paires([norm]):
            prompts += int((self.need_static[L][cs] > 0).sum())
        return IdealWord(form, eid, info[eid][2] if eid in info else "", p, score, covers, prompts)


def ideal_entry_ids(con, lex, carte, count: int = 60, **kw) -> list[int]:
    """Entry ids of the ideal words, best first (what the Cards study when their intention is « bombe »)."""
    return [w.entry_id for w in analyse(lex, carte, **kw).ideal_words(count) if w.entry_id is not None]
