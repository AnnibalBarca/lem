"""Syllabaire : l'entraînement de la Bombe sur les suites rares, apprises par cœur.

Une suite rare (« sub-50 » : moins de `seuil` formes la contiennent, cf. lem.suites) s'affiche. Tu donnes
autant de mots que tu peux avant la fin du temps (Entrée seule : je sèche). Puis la suite se déplie : tous ses
mots, regroupés par lemme, ceux que tu viens de trouver, ceux que tu avais déjà trouvés un jour (ici ou à la
Bombe), ceux qui te manquent, et une ANCRE : le mot à sortir sans réfléchir quand la suite tombe en partie.

Deux mesures par suite, parce que la Bombe en demande deux :
  survie   le premier mot, et en combien de temps (une partie se gagne en sortant UN mot, vite) ;
  étendue  la part de ses lemmes que tu as déjà produits, ici ou à la Bombe (pour ne jamais être à court,
           et parce que chaque mot trouvé est un mot de vocabulaire actif).

Chaque suite a sa répétition espacée (FSRS, trace « suite »), rejouée depuis le journal à chaque partie :
  1  rien trouvé ;  2  un premier mot plus lent que `ancre_s` ;  3  un premier mot à temps ;
  4  un premier mot en moins de la moitié de `ancre_s`, et au moins deux mots.
Une suite ratée ou hésitante revient une fois dans la session, quatre suites plus loin.
Chaque mot donné nourrit la carte de connaissance (production spontanée, comme à la Bombe).
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from fsrs import Card, Rating

from lem.db import now, parse_iso
from lem.games.base import Jeu, Param, enregistrer
from lem.journal import Evenement, Frappe, ecrire, nettoyer_saisie
from lem.memory.knowledge import CarteConnaissance
from lem.orthographe import Diagnostic, accents_manquants, accents_tolerants, diagnostiquer, enregistrer_faute
from lem.suites import TYPES_HORS_JEU, IndexSuites
from lem.texte import normaliser

SYLLABAIRE = enregistrer(Jeu(
    id="syllabaire",
    nom="Syllabaire",
    accroche="Les suites rares de la Bombe, apprises par cœur.",
    description="Entraînement de la Bombe : les suites que moins de N mots contiennent (« sub-50 »). "
                "Tous les mots que tu trouves, puis ceux qui te manquaient et ton ancre ; "
                "chaque suite a sa répétition espacée.",
    params=[
        Param("seuil", "Moins de N mots (sub-N)", int, 50, "une suite est rare si moins de N formes la contiennent",
              2, 100_000, alias=("sub",)),
        Param("minimum", "Au moins N mots", int, 3, "écarte les suites à un ou deux mots", 1, 1000),
        Param("attestes", "Dont N en usage", int, 2, "mots courants ou rares mais attestés (wordfreq) ; écarte les "
              "suites qui ne tiennent qu'à des sigles, des gentilés ou des graphies inclusives", 0, 1000),
        Param("long_min", "Lettres min", int, 2, "", 2, 3),
        Param("long_max", "Lettres max", int, 3, "", 2, 3),
        Param("nouvelles", "Suites nouvelles par session", int, 8, "", 0, 200),
        Param("duree", "Temps par suite (s)", float, 25.0, "pour trouver tous les mots que tu peux", 5, 300),
        Param("ancre_s", "Premier mot en moins de (s)", float, 5.0, "plus lent : la suite compte comme hésitante",
              1, 60),
        Param("ordre", "Ordre des nouvelles", str, "frequentes", "frequentes = celles qui sortent le plus en partie",
              choix=("frequentes", "rares", "hasard")),
        Param("noms_propres", "Noms propres et sigles", bool, False, "les accepter (JKLM les refuse)"),
    ],
))

ECART_RETOUR = 4


def _card_id(suite: str) -> int:
    return int.from_bytes(hashlib.blake2b(f"suite:{suite}".encode(), digest_size=6).digest(), "big")


def note_suite(trouves: int, premiere_ms: int | None, ancre_s: float) -> int:
    if not trouves or premiere_ms is None:
        return 1
    if premiere_ms > ancre_s * 1000:
        return 2
    if premiere_ms <= ancre_s * 500 and trouves >= 2:
        return 4
    return 3


@dataclass
class EtatSuite:
    suite: str
    card: Card
    vues: int = 0
    derniere_note: int | None = None


def etats_suites(con: sqlite3.Connection) -> dict[str, EtatSuite]:
    """Rejoue le journal du Syllabaire : l'état FSRS de chaque suite déjà travaillée."""
    from lem.memory.scheduler import ordonnanceur

    sched = ordonnanceur(con, "suite")
    etats: dict[str, EtatSuite] = {}
    for r in con.execute("""SELECT ts, reponse, note_auto FROM evenement WHERE jeu = 'syllabaire' AND type = 'suite'
                            AND note_auto IS NOT NULL ORDER BY ts, id"""):
        s, quand = r["reponse"], parse_iso(r["ts"])
        e = etats.get(s) or EtatSuite(s, Card(card_id=_card_id(s), due=quand))
        e.card, _ = sched.review_card(e.card, Rating(r["note_auto"]), quand)
        e.vues += 1
        e.derniere_note = r["note_auto"]
        etats[s] = e
    return etats


def produits(con: sqlite3.Connection) -> set[str]:
    """Normes de tous les mots que tu as produits, à la Bombe ou au Syllabaire."""
    return {normaliser(r[0]) for r in con.execute(
        """SELECT DISTINCT reponse FROM evenement WHERE jeu IN ('bombe', 'syllabaire') AND type = 'reponse'
           AND correct = 1 AND trace = 'prod' AND reponse IS NOT NULL""")}


@dataclass
class Lemme:
    entree_id: int
    forme: str                     # le lemme, tel qu'au dictionnaire
    exemple: str                   # une forme qui contient la suite (le lemme lui-même s'il la contient)
    zipf: float
    statut: str = "manque"         # trouve (dans ce tour) | deja (trouvé un autre jour) | manque


@dataclass
class Tour:
    suite: str
    n: int                         # formes valides qui la contiennent
    nouvelle: bool
    retour: bool = False
    donnes: list[str] = field(default_factory=list)       # normes trouvées dans ce tour
    premiere_ms: int | None = None


@dataclass
class Reponse:
    valide: bool
    message: str = ""
    forme: str | None = None
    entree_id: int | None = None
    inedit: bool = False           # premier mot jamais produit pour toi
    graphie: Diagnostic | None = None


@dataclass
class Bilan:
    suite: str
    note: int
    lemmes: list[Lemme]
    ancre: Lemme | None
    definition_ancre: str
    etendue: tuple[int, int]       # (lemmes déjà produits un jour, lemmes)
    jours: float                   # avant le prochain retour de la suite


class PartieSyllabaire:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 session_id: int | None = None, rng: random.Random | None = None, lexique=None):
        self.con, self.params, self.session_id = con, params, session_id
        self.rng = rng or random.Random()
        self.carte = carte or CarteConnaissance(con, lexique)
        if lexique is None:
            from lem.lexique import Lexique
            lexique = Lexique(con)
        self.lex = lexique
        self.accents_tolerants = accents_tolerants(con)
        self.tour: Tour | None = None
        self.faites: list[tuple[str, int]] = []                  # (suite, note) de la session
        self.retours: list[tuple[int, str]] = []                 # (numéro de tour minimum, suite)
        self.n_tours = 0
        self.reconfigurer()

    # -- données ------------------------------------------------------------------------------------
    def _exclus(self) -> tuple[str, ...]:
        return () if self.params.get("noms_propres") else TYPES_HORS_JEU

    def reconfigurer(self) -> None:
        self.index = IndexSuites(self.lex, self._exclus())
        self.etats = etats_suites(self.con)
        self.produits = produits(self.con)
        self._construire_file()

    def _longueurs(self) -> tuple[int, ...]:
        lo, hi = self.params["long_min"], max(self.params["long_min"], self.params["long_max"])
        return tuple(range(lo, hi + 1))

    def _eligible(self, suite: str) -> bool:
        n = self.index.n(suite)
        return (len(suite) in self._longueurs() and self.params["minimum"] <= n < self.params["seuil"]
                and self.index.n_attestes(suite) >= self.params.get("attestes", 0))

    def _construire_file(self) -> None:
        t = now()
        dues = [e for e in self.etats.values() if e.card.due <= t and self._eligible(e.suite)]
        dues.sort(key=lambda e: e.card.due)
        rares = [s for s, _ in self.index.rares(self.params["seuil"], self._longueurs(), self.params["minimum"],
                                                 self.params.get("attestes", 0)) if s not in self.etats]
        if self.params["ordre"] == "rares":
            rares.reverse()
        elif self.params["ordre"] == "hasard":
            self.rng.shuffle(rares)
        self.dues = [e.suite for e in dues]
        self.nouvelles = rares[: self.params["nouvelles"]]
        self.reserve = rares[self.params["nouvelles"]:]

    def restant(self) -> int:
        return len(self.dues) + len(self.nouvelles) + len(self.retours)

    def encore(self, n: int | None = None) -> None:
        """Session finie, on continue : une nouvelle fournée de suites nouvelles."""
        k = n or self.params["nouvelles"] or 8
        self.nouvelles, self.reserve = self.reserve[:k], self.reserve[k:]

    # -- tour ---------------------------------------------------------------------------------------
    def suivante(self) -> Tour | None:
        self.n_tours += 1
        pret = next((r for r in self.retours if r[0] <= self.n_tours), None)
        if pret is not None:
            self.retours.remove(pret)
            s, nouvelle, retour = pret[1], False, True
        # Révisions dues d'abord, une nouvelle suite tous les trois tours (plus souvent s'il en reste plus que de
        # révisions) : les nouvelles sont réparties dans la session.
        elif self.dues and (not self.nouvelles or len(self.dues) >= len(self.nouvelles) or self.n_tours % 3):
            s, nouvelle, retour = self.dues.pop(0), False, False
        elif self.nouvelles:
            s, nouvelle, retour = self.nouvelles.pop(0), True, False
        elif self.retours:
            _, s = self.retours.pop(0)
            nouvelle, retour = False, True
        else:
            self.tour = None
            return None
        self.tour = Tour(s, self.index.n(s), nouvelle, retour)
        return self.tour

    def _valider(self, texte: str) -> tuple[str, tuple[int, ...]]:
        cle, exclus = texte.strip().lower(), self._exclus()
        ids = self.lex.entrees(cle, exclus)
        if not ids and self.accents_tolerants:
            for f in self.lex.formes_de_norme(normaliser(cle)):
                if accents_manquants(cle, f) and (ids_f := self.lex.entrees(f, exclus)):
                    return f, ids_f
        return cle, ids

    def proposer(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Reponse:
        """Un mot proposé pour la suite en cours."""
        t = self.tour
        texte = nettoyer_saisie(texte)
        norme = normaliser(texte)
        if t.suite not in norme:
            return Reponse(False, f"ne contient pas « {t.suite.upper()} »")
        if norme in t.donnes:
            return Reponse(False, "déjà donné")
        cle, ids = self._valider(texte)
        if not ids:
            if not (frappe and frappe.dictee):
                cands = [f for n in self.index.solutions(t.suite) for f in self.lex.formes_de_norme(n)]
                diag = diagnostiquer(self.con, texte, None, cands)
                if diag:
                    enregistrer_faute(self.con, self.session_id, "syllabaire", diag, latence_ms, frappe)
                    return Reponse(False, "graphie", entree_id=diag.entree_id, graphie=diag)
            return Reponse(False, "mot inconnu")
        eid = ids[0]
        inedit = norme not in self.produits
        t.donnes.append(norme)
        self.produits.add(norme)
        if t.premiere_ms is None:
            t.premiere_ms = latence_ms
        ev = ecrire(self.con, self.session_id, Evenement(
            "syllabaire", "reponse", entree_id=eid, trace="prod", reponse=cle, correct=True, latence_ms=latence_ms,
            frappe=frappe or Frappe(), data={"suite": t.suite, "n": t.n, "rang": len(t.donnes), "fsrs": False}))
        # Sans propagation : un mot tapé librement est connu par construction (cf. memory/migration.py).
        self.carte.observer(eid, 1.0, poids=0.5, source="syllabaire", evenement_id=ev, propager=False)
        return Reponse(True, "", cle, eid, inedit)

    def terminer(self, duree_ms: int | None = None) -> Bilan:
        """Fin du tour (temps écoulé ou Entrée seule) : note, journal, et la suite dépliée."""
        t = self.tour
        note = note_suite(len(t.donnes), t.premiere_ms, self.params["ancre_s"])
        ecrire(self.con, self.session_id, Evenement(
            "syllabaire", "suite", trace="suite", reponse=t.suite, correct=bool(t.donnes), note_auto=note,
            latence_ms=t.premiere_ms, data={"n": t.n, "trouves": t.donnes, "nouvelle": t.nouvelle,
                                            "retour": t.retour, "duree_ms": duree_ms}))
        from lem.memory.scheduler import ordonnanceur

        quand = now()
        e = self.etats.get(t.suite) or EtatSuite(t.suite, Card(card_id=_card_id(t.suite), due=quand))
        e.card, _ = ordonnanceur(self.con, "suite").review_card(e.card, Rating(note), quand)
        e.vues += 1
        e.derniere_note = note
        self.etats[t.suite] = e
        self.faites.append((t.suite, note))
        if note <= 2 and not t.retour:
            self.retours.append((self.n_tours + ECART_RETOUR, t.suite))
        lemmes = self.deplier(t.suite, set(t.donnes))
        connus = sum(1 for x in lemmes if x.statut != "manque")
        ancre = self._ancre(lemmes)
        d = self.con.execute("SELECT definition FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1",
                             (ancre.entree_id,)).fetchone() if ancre else None
        jours = max(0.0, (e.card.due - quand).total_seconds() / 86400)
        return Bilan(t.suite, note, lemmes, ancre, d["definition"] if d else "", (connus, len(lemmes)), jours)

    def deplier(self, suite: str, donnes: set[str] = frozenset()) -> list[Lemme]:
        """Les lemmes dont une forme contient la suite, les plus courants d'abord."""
        exclus = self._exclus()
        par_entree: dict[int, list[str]] = {}
        normes_de: dict[int, set[str]] = {}
        for n in self.index.solutions(suite):
            for f in self.lex.formes_de_norme(n):
                for eid in self.lex.entrees(f, exclus):
                    par_entree.setdefault(eid, []).append(f)
                    normes_de.setdefault(eid, set()).add(n)
        if not par_entree:
            return []
        rows = self.con.execute(f"SELECT id, forme, zipf FROM entree WHERE id IN ({', '.join('?' * len(par_entree))})",
                                list(par_entree)).fetchall()
        out = []
        for r in rows:
            formes = par_entree[r["id"]]
            exemple = r["forme"] if suite in normaliser(r["forme"]) else min(formes, key=len)
            lem = Lemme(r["id"], r["forme"], exemple, r["zipf"] or 0.0)
            ns = normes_de[r["id"]]
            if ns & donnes:
                lem.statut = "trouve"
            elif ns & self.produits:
                lem.statut = "deja"
            out.append(lem)
        # Un même lemme peut avoir plusieurs entrées (nom et adjectif) : une seule ligne.
        vus, uniques = set(), []
        for x in sorted(out, key=lambda x: ({"trouve": 0, "deja": 1, "manque": 2}[x.statut], -x.zipf, x.forme)):
            if x.forme.lower() not in vus:
                vus.add(x.forme.lower())
                uniques.append(x)
        return sorted(uniques, key=lambda x: (-x.zipf, len(x.exemple), x.forme))

    @staticmethod
    def _ancre(lemmes: list[Lemme]) -> Lemme | None:
        """Le mot à sortir sans réfléchir : le tien s'il y en a un (le plus courant), sinon le plus courant."""
        if not lemmes:
            return None
        miens = [x for x in lemmes if x.statut != "manque"]
        return max(miens or lemmes, key=lambda x: (x.zipf, -len(x.exemple)))

    def bilan_session(self) -> dict:
        notes = [n for _, n in self.faites]
        return {"suites": len({s for s, _ in self.faites}), "seches": sum(1 for n in notes if n == 1),
                "hesitantes": sum(1 for n in notes if n == 2), "sures": sum(1 for n in notes if n >= 3)}


def resume(con: sqlite3.Connection, lexique, seuil: int = 50, quand: datetime | None = None,
           attestes: int = 2) -> dict:
    """Pour l'Atlas : suites rares travaillées, sûres, dues, et combien il en reste à découvrir."""
    etats = etats_suites(con)
    quand = quand or now()
    index = IndexSuites(lexique)
    rares = index.rares(seuil, minimum=3, attestes=attestes)
    eligibles = {s for s, _ in rares}
    vues = [e for e in etats.values() if e.suite in eligibles]
    sures = [e for e in vues if e.card.stability and e.card.stability >= 21]
    dues = [e for e in vues if e.card.due <= quand]
    return {"rares": len(rares), "vues": len(vues), "sures": len(sures), "dues": len(dues),
            "repartition": index.repartition(), "seuil": seuil}
