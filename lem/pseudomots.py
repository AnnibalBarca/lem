"""Générateur de pseudo-mots (chaîne de Markov d'ordre 3 sur les lemmes).

Version naïve, comme tu l'anticipais : un œil entraîné aux sèmes les repérera parfois.
Elle sert surtout à détecter la surestimation dans l'étalonnage (dire « je connais »
à un mot qui n'existe pas). À remplacer par un générateur syllabique plus fin (type Wuggy).
"""

from __future__ import annotations

import random
from collections import defaultdict


class GenerateurPseudoMots:
    def __init__(self, mots: list[str], ordre: int = 3, rng: random.Random | None = None):
        self.ordre = ordre
        self.rng = rng or random.Random()
        self.reels = set(mots)
        self.trans: dict[str, list[str]] = defaultdict(list)
        for m in mots:
            if not m.isalpha() or len(m) < 5:
                continue
            m = "^" * ordre + m + "$"
            for i in range(len(m) - ordre):
                self.trans[m[i : i + ordre]].append(m[i + ordre])

    def generer(self, longueur_min: int = 7, longueur_max: int = 13, essais: int = 200) -> str | None:
        for _ in range(essais):
            etat, out = "^" * self.ordre, []
            while len(out) < longueur_max + 2:
                suivants = self.trans.get(etat)
                if not suivants:
                    break
                c = self.rng.choice(suivants)
                if c == "$":
                    break
                out.append(c)
                etat = etat[1:] + c
            mot = "".join(out)
            if longueur_min <= len(mot) <= longueur_max and mot not in self.reels:
                return mot
        return None
