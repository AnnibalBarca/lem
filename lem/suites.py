"""Suites de lettres de la Bombe : combien de mots valides contiennent chaque suite.

« Sub-50 » (jargon de JKLM) : une suite que moins de 50 mots contiennent. Ce sont les invites où l'on sèche ;
les apprendre par cœur (le Syllabaire), c'est ne plus jamais sécher. Un mot, ici, est une FORME valide (comme
JKLM : « mangeaient » compte à part de « manger ») sous les règles de la Bombe : ni noms propres ni sigles.
Une forme compte une fois par suite, même si la suite y paraît deux fois.

Calcul vectorisé (numpy) sur toutes les normes valides, suites de 2 et 3 lettres de a à z ; sur la vraie
base (≈ 2 M normes), quelques secondes. Gardé sur disque à côté de l'index du lexique (lem.lexique), rechargé
ensuite instantanément, refait avec lui quand le lexique change.
"""

from __future__ import annotations

import numpy as np

LONGUEURS = (2, 3)
BASE = 27                    # 0 = hors alphabet (coupe une suite), 1..26 = a..z
TYPES_HORS_JEU = ("nom_propre", "sigle")
_LUT = np.zeros(256, dtype=np.int64)
_LUT[97:123] = np.arange(1, 27)


def code(suite: str) -> int:
    c = 0
    for ch in suite:
        c = c * BASE + ord(ch) - 96
    return c


def suite_de(c: int, longueur: int) -> str:
    lettres = []
    for _ in range(longueur):
        c, r = divmod(c, BASE)
        lettres.append(chr(96 + r))
    return "".join(reversed(lettres))


def _compter(normes: list[str], comptes: dict[int, np.ndarray], tranche: int = 200_000) -> None:
    """Ajoute à `comptes[L]` le nombre de normes contenant chaque suite de L lettres (une fois par norme)."""
    for a in range(0, len(normes), tranche):
        bloc = "\x00".join(normes[a:a + tranche]) + "\x00"
        b = np.frombuffer(bloc.encode("utf-8"), dtype=np.uint8)
        v = _LUT[b]                                   # les octets non a–z (accents restants, tirets…) valent 0
        fid = np.cumsum(b == 0)                       # numéro de la norme de chaque octet, dans la tranche
        for L in LONGUEURS:
            n = len(v) - L + 1
            if n <= 0:
                continue
            ok = np.ones(n, dtype=bool)
            c = np.zeros(n, dtype=np.int64)
            for j in range(L):
                vj = v[j:j + n]
                ok &= vj > 0
                c = c * BASE + vj
            cles = np.unique(fid[:n][ok] * BASE ** L + c[ok])     # une fois par norme
            comptes[L] += np.bincount(cles % BASE ** L, minlength=BASE ** L)


class IndexSuites:
    """Nombre de formes valides par suite, et les solutions d'une suite."""

    def __init__(self, lex, exclus: tuple[str, ...] = TYPES_HORS_JEU):
        self.lex, self.exclus = lex, tuple(exclus)
        nom = "suites-" + ("-".join(sorted(self.exclus)) or "tout") + ".npz"
        fichier = lex.racine / nom if getattr(lex, "racine", None) else None
        if fichier is not None and fichier.exists():
            t = np.load(fichier)
            self.valide = t["valide"]
            self.comptes = {L: t[f"n{L}"] for L in LONGUEURS}
            return
        self._construire()
        if fichier is not None:
            tmp = fichier.with_name(fichier.stem + ".tmp.npz")
            np.savez(tmp, valide=self.valide, **{f"n{L}": self.comptes[L] for L in LONGUEURS})
            tmp.replace(fichier)

    def _construire(self) -> None:
        lex = self.lex
        exclus = np.isin(lex.code_type, [i for i, t in enumerate(lex.noms_types) if t in self.exclus])
        entree_de_ligne = np.repeat(np.arange(len(lex.eids)), np.diff(lex.e_ptr))
        ids = np.unique(lex.e_nid[~exclus[entree_de_ligne]])
        self.valide = np.zeros(len(lex.normes), dtype=bool)
        self.valide[ids] = True
        normes = [lex.normes.restore_key(int(i)) for i in ids]
        self.comptes = {L: np.zeros(BASE ** L, dtype=np.int64) for L in LONGUEURS}
        _compter(normes, self.comptes)

    # -- lecture -----------------------------------------------------------------------------------
    def n(self, suite: str) -> int:
        """Nombre de formes valides contenant la suite (0 si elle n'est pas faite de 2 ou 3 lettres a–z)."""
        if len(suite) not in self.comptes or not suite.isascii() or not suite.isalpha() or not suite.islower():
            return 0
        return int(self.comptes[len(suite)][code(suite)])

    def rares(self, seuil: int, longueurs: tuple[int, ...] = LONGUEURS, minimum: int = 1) -> list[tuple[str, int]]:
        """Les suites qu'au moins `minimum` et moins de `seuil` formes contiennent, des plus fréquentes aux plus
        rares (une suite proche du seuil sort plus souvent comme invite)."""
        out = []
        for L in longueurs:
            c = self.comptes.get(L)
            if c is None:
                continue
            idx = np.nonzero((c >= minimum) & (c < seuil))[0]
            out += [(suite_de(int(i), L), int(c[i])) for i in idx]
        return sorted(out, key=lambda x: (-x[1], x[0]))

    def repartition(self, bornes: tuple[int, ...] = (1, 10, 50, 100, 500)) -> dict[str, int]:
        """Nombre de suites par tranche de solutions : {"1–9": …, "10–49": …, …, "500+": …}."""
        tout = np.concatenate([self.comptes[L][self.comptes[L] > 0] for L in LONGUEURS])
        out = {}
        for lo, hi in zip(bornes, bornes[1:]):
            out[f"{lo}–{hi - 1}"] = int(((tout >= lo) & (tout < hi)).sum())
        out[f"{bornes[-1]}+"] = int((tout >= bornes[-1]).sum())
        return out

    def solutions(self, suite: str) -> list[str]:
        """Les normes valides qui contiennent la suite."""
        normes = self.lex.normes_contenant(suite, range(len(suite), 80))
        return [x for x in normes if self.valide[self.lex.normes[x]]]
