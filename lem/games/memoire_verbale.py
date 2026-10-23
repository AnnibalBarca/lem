"""Mémoire verbale (Human Benchmark « Verbal Memory »), en deux modes.

Un mot s'affiche : déjà VU dans cette partie, ou NOUVEAU ? Trois vies.

  entrainement   fidèle au benchmark : des mots courants, comme ceux du test. La difficulté n'est pas de
                 retenir des mots inconnus, c'est de distinguer « vu tout à l'heure » de « familier depuis
                 toujours ». La parade : encoder chaque mot nouveau en une image, vite. Le jeu t'y entraîne :
                 après « nouveau », une invite « image ? » attend que tu aies ton image (Espace) et mesure
                 le temps qu'elle t'a pris. L'invite s'efface quand ton record monte (`encodage` = estompe),
                 parce que le vrai test n'en a pas : à 200, tu encodes seul.
                 En fin de partie, tes deux sortes d'erreurs, qui ne se corrigent pas de la même façon :
                 fausses alertes (« vu » pour un mot nouveau : la familiarité t'a trompé) et oublis
                 (« nouveau » pour un mot vu : l'encodage a manqué).
  elucubrations  le jeu d'origine : les régions les plus étranges du lexique (noms propres, mots étrangers,
                 termes techniques rarissimes, composés : kjøkkenmødding, chlorfenvinphos,
                 pouligny-saint-pierre, midshipmen…), mêlés aux mots « presque acquis » des autres jeux.
                 La définition paraît après « nouveau » (`definitions`) : le jeu t'apprend des mots.
                 Imagerie : à la première apparition d'un mot, un champ s'ouvre pour l'image mentale que tu
                 fabriques (au clavier ou avec ta propre dictée Whisper) ; elle rejoint la table mnemo et te
                 revient après ta réponse quand le mot repasse. En mode « experience », un mot sur deux
                 seulement est imagé, de façon déterministe, pour mesurer sur TES données l'effet de
                 l'imagerie.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import statistics
from dataclasses import dataclass
from functools import lru_cache

from lem.games.base import Jeu, Param, enregistrer
from lem.journal import Evenement, Frappe, ajouter_mnemo, ecrire
from lem.texte import normaliser

MEMOIRE = enregistrer(Jeu(
    id="memoire",
    nom="Mémoire verbale",
    accroche="Vu ou nouveau ? Entraînement au benchmark, ou les mots les plus étranges du français.",
    description="Human Benchmark : entraînement fidèle au test (mots courants, encodage en images qui "
                "s'efface), ou élucubrations lexicales qui t'apprennent des mots.",
    params=[
        Param("mode", "Mode", str, "entrainement", "entrainement = comme le test · elucubrations = mots étranges",
              choix=("entrainement", "elucubrations")),
        Param("vies", "Vies", int, 3, "", 1, 10),
        Param("encodage", "Invite « image ? »", str, "estompe",
              "entraînement : libre = jamais · guide = toujours · estompe = s'efface quand ton record monte",
              choix=("libre", "guide", "estompe")),
        Param("presque_acquis", "Part de mots presque acquis", float, 0.25, "élucubrations, 0 à 1", 0, 1),
        Param("etrangete", "Niveau d'étrangeté", float, 0.7, "élucubrations : 0 = mots rares, 1 = les plus abscons",
              0, 1),
        Param("imagerie", "Imagerie mentale écrite", str, "off", "élucubrations : off | toujours | experience",
              choix=("off", "toujours", "experience")),
        Param("definitions", "Définitions", bool, True, "élucubrations : la définition après « nouveau »"),
    ],
))

LETTRES_RARES = set("kwyzøåæœqxj")
# Entraînement : des mots courants, comme ceux du test (ni trop rares, ni mots-outils).
ZIPF_COURANTS = (2.8, 5.3)
# Invites « image ? » selon le record : (record en dessous duquel, nombre de mots nouveaux guidés par partie).
PALIERS_ENCODAGE = ((50, 10_000), (100, 30), (200, 10))


@lru_cache(maxsize=None)
def _diacritique_etranger(c: str) -> bool:
    return c != normaliser(c) and c not in "éèêàçùûôîïë"


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
    if any(_diacritique_etranger(c) for c in f):
        s += 0.8                                        # diacritiques non français
    return s


def scores(con: sqlite3.Connection, mode: str = "entrainement") -> list[int]:
    """Score de chaque partie jouée dans ce mode, de la plus ancienne à la plus récente."""
    rows = con.execute(
        """SELECT session_id, SUM(correct) FROM evenement WHERE jeu = 'memoire' AND type = 'reponse'
           AND COALESCE(json_extract(data, '$.mode'), 'elucubrations') = ? AND session_id IS NOT NULL
           GROUP BY session_id ORDER BY session_id""", (mode,)).fetchall()
    return [int(r[1] or 0) for r in rows]


def guides_par_partie(record: int, encodage: str) -> int:
    """Combien de mots nouveaux reçoivent l'invite « image ? » dans une partie."""
    if encodage == "libre":
        return 0
    if encodage == "guide":
        return 10_000
    return next((n for seuil, n in PALIERS_ENCODAGE if record < seuil), 0)


@dataclass
class Tour:
    entree_id: int
    forme: str
    deja_vu: bool
    premiere_fois: bool
    imager: bool                   # élucubrations : champ d'image mentale écrite
    guider: bool = False           # entraînement : invite « image ? »


class PartieMemoire:
    def __init__(self, con: sqlite3.Connection, params: dict, session_id: int | None = None,
                 rng: random.Random | None = None):
        self.con, self.params, self.session_id = con, params, session_id
        self.params.setdefault("mode", "elucubrations")
        self.rng = rng or random.Random()
        self.vies = params["vies"]
        self.score = 0
        self.vus: list[int] = []
        self.formes: dict[int, str] = {}
        self.dernier: int | None = None
        self.tour: Tour | None = None
        self.fausses_alertes = 0       # « vu » pour un mot nouveau
        self.oublis = 0                # « nouveau » pour un mot vu
        self.latences: list[int] = []
        self.encodages: list[int] = []
        self.guides = 0
        self.historique = scores(con, self.mode)
        self.record = max(self.historique, default=0)
        self.max_guides = guides_par_partie(self.record, params.get("encodage", "estompe"))
        self._construire_vivier()

    @property
    def mode(self) -> str:
        return self.params.get("mode") or "elucubrations"

    def _construire_vivier(self) -> None:
        if self.mode == "entrainement":
            lo, hi = ZIPF_COURANTS
            rows = self.con.execute(
                """SELECT id, forme FROM entree WHERE type = 'mot' AND zipf BETWEEN ? AND ?
                   AND length(forme) BETWEEN 3 AND 12 AND forme NOT LIKE '% %' AND forme NOT LIKE '%-%'
                   AND forme = lower(forme)""", (lo, hi)).fetchall()
            if len(rows) < 200:                    # petite base (démo) : tous les mots simples
                rows = self.con.execute("""SELECT id, forme FROM entree WHERE type = 'mot'
                                           AND forme NOT LIKE '% %'""").fetchall()
            uniques = {}
            for r in rows:                         # homographes : un seul mot à l'écran
                uniques.setdefault(r["forme"], r)
            self.etranges = list(uniques.values())
            self.presque = []
        else:
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
        if self.mode == "entrainement":
            return False
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
            guider = self.mode == "entrainement" and self.guides < self.max_guides
            self.tour = Tour(r["id"], r["forme"], False, True, self._imager(r["id"]), guider)
        self.dernier = self.tour.entree_id
        return self.tour

    def repondre(self, dit_vu: bool, latence_ms: int) -> bool:
        t = self.tour
        correct = dit_vu == t.deja_vu
        if correct:
            self.score += 1
        else:
            self.vies -= 1
            if dit_vu:
                self.fausses_alertes += 1
            else:
                self.oublis += 1
        if not t.deja_vu:
            self.vus.append(t.entree_id)
            if t.guider:
                self.guides += 1
        self.latences.append(latence_ms)
        ecrire(self.con, self.session_id, Evenement(
            "memoire", "reponse", entree_id=t.entree_id, trace="forme", reponse="VU" if dit_vu else "NOUVEAU",
            correct=correct, latence_ms=latence_ms,
            data={"deja_vu": t.deja_vu, "position": self.score, "n_vus": len(self.vus), "mode": self.mode,
                  "guide": t.guider or None,
                  "condition_imagerie": self._imager(t.entree_id) if self.params.get("imagerie") == "experience"
                  and self.mode != "entrainement" else None,
                  "fsrs": False}))
        return correct

    def encoder(self, entree_id: int, duree_ms: int) -> None:
        """Entraînement : l'image est là (Espace) ; on garde le temps qu'elle a pris."""
        self.encodages.append(duree_ms)
        ecrire(self.con, self.session_id, Evenement("memoire", "encodage", entree_id=entree_id, latence_ms=duree_ms,
                                                    data={"mode": self.mode}))

    def enregistrer_image(self, entree_id: int, texte: str, frappe: Frappe | None = None) -> None:
        if texte.strip():
            ajouter_mnemo(self.con, entree_id, "image_mentale", texte.strip(), source="memoire")

    def image(self, entree_id: int) -> str | None:
        """Ta dernière image mentale pour ce mot, s'il y en a une."""
        r = self.con.execute("""SELECT contenu FROM mnemo WHERE entree_id = ? AND type = 'image_mentale'
                                ORDER BY id DESC LIMIT 1""", (entree_id,)).fetchone()
        return r["contenu"] if r else None

    def definition(self, entree_id: int) -> str | None:
        r = self.con.execute("SELECT definition FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1",
                             (entree_id,)).fetchone()
        return r["definition"] if r else None

    def bilan(self) -> dict:
        precedents = self.historique[-10:]
        return {"score": self.score, "record": max(self.record, self.score), "nouveau_record": self.score > self.record,
                "fausses_alertes": self.fausses_alertes, "oublis": self.oublis,
                "mediane_precedente": statistics.median(precedents) if precedents else None,
                "latence_ms": statistics.median(self.latences) if self.latences else None,
                "encodage_ms": statistics.median(self.encodages) if self.encodages else None,
                "guides": self.guides, "parties": len(self.historique) + 1}

    @property
    def finie(self) -> bool:
        return self.vies <= 0
