"""Mémoire verbale (Human Benchmark « Verbal Memory »), version élucubrations.

Un mot s'affiche : déjà VU dans cette partie, ou NOUVEAU ? Trois vies.
Le vivier puise dans les régions les plus étranges du lexique : noms propres, mots
étrangers, termes techniques rarissimes, composés (kjøkkenmødding, chlorfenvinphos,
pouligny-saint-pierre, midshipmen…), mêlés aux mots « presque acquis » des autres jeux.

Imagerie : à la première apparition d'un mot, un champ s'ouvre pour l'image mentale que tu
fabriques (au clavier ou avec ta propre dictée Whisper). Elle rejoint la table mnemo. En mode « expérience », un mot sur deux seulement est imagé — de façon
déterministe — ce qui permet de mesurer, sur TES données, l'effet de l'imagerie.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from dataclasses import dataclass

from lem.games.base import Jeu, Param, enregistrer
from lem.journal import Evenement, Frappe, ajouter_mnemo, ecrire
from lem.texte import normaliser

MEMOIRE = enregistrer(Jeu(
    id="memoire",
    nom="Mémoire verbale",
    accroche="Vu ou nouveau ? Les mots les plus étranges du français.",
    description="Human Benchmark, avec élucubrations lexicales, mots presque acquis et imagerie mentale.",
    params=[
        Param("vies", "Vies", int, 3, "", 1, 10),
        Param("presque_acquis", "Part de mots presque acquis", float, 0.25, "0 à 1", 0, 1),
        Param("etrangete", "Niveau d'étrangeté", float, 0.7, "0 = mots rares, 1 = les plus abscons", 0, 1),
        Param("imagerie", "Imagerie mentale", str, "off", "off | toujours | experience",
              choix=("off", "toujours", "experience")),
    ],
))

LETTRES_RARES = set("kwyzøåæœqxj")


def etrangete(forme: str, zipf: float | None, type_: str, origine: str | None) -> float:
    """Score heuristique d'étrangeté lexicale (plus c'est haut, plus c'est abscons)."""
    f = forme.lower()
    s = max(0.0, 3.0 - (zipf or 0.0))                  # rareté
    s += {"nom_propre": 1.2, "etranger": 1.5, "compose": 0.6, "expression": 0.4}.get(type_, 0.0)
    if origine and origine not in {"latin", "francais", "ancien français"}:
        s += 0.6
    s += 0.5 * sum(c in LETTRES_RARES for c in f)
    s += 0.08 * max(0, len(f) - 8)
    s += 0.4 * (("-" in f) + (" " in f))
    if any(c != normaliser(c) and c not in "éèêàçùûôîïë" for c in f):
        s += 0.8                                        # diacritiques non français
    return s


@dataclass
class Tour:
    entree_id: int
    forme: str
    deja_vu: bool
    premiere_fois: bool
    imager: bool


class PartieMemoire:
    def __init__(self, con: sqlite3.Connection, params: dict, session_id: int | None = None,
                 rng: random.Random | None = None):
        self.con, self.params, self.session_id = con, params, session_id
        self.rng = rng or random.Random()
        self.vies = params["vies"]
        self.score = 0
        self.vus: list[int] = []
        self.formes: dict[int, str] = {}
        self.dernier: int | None = None
        self.tour: Tour | None = None
        self._construire_vivier()

    def _construire_vivier(self) -> None:
        rows = self.con.execute(
            """SELECT id, forme, zipf, type, origine FROM entree
               WHERE COALESCE(zipf, 0) < 2.5 OR type IN ('nom_propre', 'etranger', 'compose')"""
        ).fetchall()
        notes = sorted(((etrangete(r["forme"], r["zipf"], r["type"], r["origine"]), r) for r in rows),
                       key=lambda x: -x[0])
        # niveau d'étrangeté : on garde la tranche supérieure correspondante (au moins 200 mots).
        n = max(200, int(len(notes) * (1.05 - self.params["etrangete"])))
        self.etranges = [r for _, r in notes[:n]]
        self.presque = self.con.execute(
            """SELECT e.id, e.forme FROM entree e WHERE EXISTS (SELECT 1 FROM trace_etat t WHERE t.entree_id = e.id
                   AND t.stabilite BETWEEN 1 AND 30)
               OR e.id IN (SELECT entree_id FROM connaissance WHERE p_connu BETWEEN 0.6 AND 0.9 AND n_obs > 0)"""
        ).fetchall()
        for r in [*self.etranges, *self.presque]:
            self.formes[r["id"]] = r["forme"]

    def _imager(self, entree_id: int) -> bool:
        mode = self.params["imagerie"]
        if mode == "toujours":
            return True
        if mode == "experience":
            return hashlib.blake2b(str(entree_id).encode(), digest_size=1).digest()[0] % 2 == 0
        return False

    def suivant(self) -> Tour:
        p_vu = min(0.55, len(self.vus) / (len(self.vus) + 6)) if self.vus else 0.0
        candidats_vus = [v for v in self.vus if v != self.dernier]
        vus = set(self.vus)
        reste_nouveaux = any(r["id"] not in vus for r in self.etranges)
        if candidats_vus and (self.rng.random() < p_vu or not reste_nouveaux):
            eid = self.rng.choice(candidats_vus)
            self.tour = Tour(eid, self.formes[eid], True, False, False)
        else:
            source = self.presque if self.presque and self.rng.random() < self.params["presque_acquis"] else self.etranges
            libres = [r for r in source if r["id"] not in vus] or [r for r in self.etranges if r["id"] not in vus]
            r = self.rng.choice(libres)
            self.tour = Tour(r["id"], r["forme"], False, True, self._imager(r["id"]))
        self.dernier = self.tour.entree_id
        return self.tour

    def repondre(self, dit_vu: bool, latence_ms: int) -> bool:
        t = self.tour
        correct = dit_vu == t.deja_vu
        if correct:
            self.score += 1
        else:
            self.vies -= 1
        if not t.deja_vu:
            self.vus.append(t.entree_id)
        ecrire(self.con, self.session_id, Evenement(
            "memoire", "reponse", entree_id=t.entree_id, trace="forme", reponse="VU" if dit_vu else "NOUVEAU",
            correct=correct, latence_ms=latence_ms,
            data={"deja_vu": t.deja_vu, "position": self.score, "n_vus": len(self.vus),
                  "condition_imagerie": self._imager(t.entree_id) if self.params["imagerie"] == "experience" else None,
                  "fsrs": False}))
        return correct

    def enregistrer_image(self, entree_id: int, texte: str, frappe: Frappe | None = None) -> None:
        if texte.strip():
            ajouter_mnemo(self.con, entree_id, "image_mentale", texte.strip(), source="memoire")

    @property
    def finie(self) -> bool:
        return self.vies <= 0
