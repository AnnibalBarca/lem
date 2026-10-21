"""Proximités entre deux mots — le cœur des étiquettes colorées.

Quatre axes, chacun calculé sans LLM :
  forme     : orthographe + affixes communs (+ phonétique si connue)
  sens      : cosinus bge-m3
  mémoire   : TES confusions passées (relation 'confusion_perso')
  famille   : morphèmes partagés

Étiquette pour un mot u donné à la place d'un mot cible t :
  JUMEAU   (rouge)   « Presque pareil ! »                 forme ET sens proches
  FORME    (magenta) « Même allure, autre sens »          forme proche seulement
  SENS     (orange)  « Similaire par le sens »            sens proche seulement
  MEMOIRE  (violet)  « Tu les as déjà confondus »         confusion personnelle connue
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from rapidfuzz.distance import Levenshtein

from lem.db import ajouter_relation, get_reglage
from lem.texte import normaliser, sans_accents  # noqa: F401  (ré-exportés)


class Proximite(str, Enum):
    JUMEAU = "jumeau"
    FORME = "forme"
    SENS = "sens"
    MEMOIRE = "memoire"
    AUCUNE = "aucune"


MESSAGES = {
    Proximite.JUMEAU: "Presque pareil !",
    Proximite.FORME: "Même allure, autre sens",
    Proximite.SENS: "Similaire par le sens",
    Proximite.MEMOIRE: "Tu les as déjà confondus",
    Proximite.AUCUNE: "",
}

# Seuils par défaut. Le seuil « sens » dépend du modèle : bge-m3 a un cosinus de base élevé
# (~0,4 entre textes sans rapport) ; à recalibrer sur tes données (`reglage` 'seuils_proximite').
SEUIL_FORME = 0.55
SEUIL_SENS = 0.72


def _prefixe_commun(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def sim_forme(a: str, b: str, phon_a: str | None = None, phon_b: str | None = None) -> float:
    """Similarité formelle ∈ [0, 1]. acronyme/homonyme ≈ 0,66 ; sigle/acronyme ≈ 0,2."""
    a, b = normaliser(a), normaliser(b)
    if not a or not b:
        return 0.0
    ortho = Levenshtein.normalized_similarity(a, b)
    m = max(len(a), len(b))
    affixe = max(_prefixe_commun(a, b), _prefixe_commun(a[::-1], b[::-1])) / m
    s = 0.6 * ortho + 0.4 * affixe if affixe >= 3 / m else ortho
    if phon_a and phon_b:
        s = max(s, Levenshtein.normalized_similarity(phon_a, phon_b) * 0.95)
    return min(1.0, max(s, ortho))


@dataclass
class Comparaison:
    forme: float
    sens: float | None
    memoire: float
    proximite: Proximite

    @property
    def message(self) -> str:
        return MESSAGES[self.proximite]


def etiqueter(forme: float, sens: float | None, memoire: float = 0.0,
              seuil_forme: float = SEUIL_FORME, seuil_sens: float = SEUIL_SENS) -> Proximite:
    f = forme >= seuil_forme
    s = sens is not None and sens >= seuil_sens
    if f and s:
        return Proximite.JUMEAU
    if memoire > 0:
        return Proximite.MEMOIRE
    if f:
        return Proximite.FORME
    if s:
        return Proximite.SENS
    return Proximite.AUCUNE


def poids_confusion(con: sqlite3.Connection, a: int, b: int) -> float:
    row = con.execute(
        """SELECT SUM(poids) AS p FROM relation WHERE type = 'confusion_perso'
           AND ((source_id = ? AND cible_id = ?) OR (source_id = ? AND cible_id = ?))""",
        (a, b, b, a),
    ).fetchone()
    return float(row["p"] or 0.0)


def enregistrer_confusion(con: sqlite3.Connection, cible: int, donne: int, increment: float = 1.0) -> None:
    """Arc orienté cible → mot donné à sa place. Alimente l'atlas de tes confusions."""
    if cible == donne:
        return
    actuel = con.execute(
        "SELECT poids FROM relation WHERE source_id = ? AND cible_id = ? AND type = 'confusion_perso'",
        (cible, donne),
    ).fetchone()
    ajouter_relation(con, cible, donne, "confusion_perso", (actuel["poids"] if actuel else 0.0) + increment, "journal")
    con.commit()


def comparer(con: sqlite3.Connection, a: int, b: int, magasin=None, seuils: dict | None = None) -> Comparaison:
    """Compare deux entrées sur les axes forme, sens (si magasin sémantique) et mémoire."""
    ra = con.execute("SELECT forme, phon FROM entree WHERE id = ?", (a,)).fetchone()
    rb = con.execute("SELECT forme, phon FROM entree WHERE id = ?", (b,)).fetchone()
    f = sim_forme(ra["forme"], rb["forme"], ra["phon"], rb["phon"])
    s = magasin.sim(a, b) if magasin is not None else None
    mem = poids_confusion(con, a, b)
    seuils = seuils or get_reglage(con, "seuils_proximite", {})   # calibrés par `lem calibrer`
    return Comparaison(f, s, mem, etiqueter(f, s, mem, seuils.get("forme", SEUIL_FORME), seuils.get("sens", SEUIL_SENS)))
