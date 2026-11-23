"""Synonymes : entraînement rapide aux synonymes (français soutenu, Sciences Po, littéraire).

Un mot soutenu ou littéraire s'affiche. Tu donnes autant de synonymes que tu peux avant la fin du temps
(Entrée seule : je sèche). Validation immédiate sans LLM :
  1. synonyme direct ou réciproque dans la base (Wiktionnaire) ;
  2. proche synonyme par cosinus bge-m3 (filet de sécurité si le Wiktionnaire est incomplet) ;
  3. sinon diagnostic orthographique (trace « ortho ») et proximité colorée (confusions).

Puis le nuage de synonymes se déplie : trouvés maintenant, déjà produits un autre jour, manqués (classés
par registre et fréquence), définition du mot et une ancre à retenir.

Chaque mot a sa répétition espacée (FSRS, trace « synonyme »), rejouée depuis le journal :
  1  rien trouvé ;  2  premier synonyme plus lent que `ancre_s` ;  3  premier synonyme à temps ;
  4  premier synonyme en moins de la moitié de `ancre_s`, et au moins deux synonymes.
Un mot raté ou hésitant revient une fois dans la session, quatre mots plus loin.
Chaque synonyme produit nourrit la carte de connaissance (production spontanée) et signale les
« montées de registre » (un synonyme plus rare ou plus littéraire que le mot demandé).
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from fsrs import Card, Rating

from lem.db import now, parse_iso
from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.journal import Evenement, Frappe, ecrire, nettoyer_saisie
from lem.memory.knowledge import POIDS_PRODUIT, CarteConnaissance, score_apprentissage
from lem.orthographe import Diagnostic, accents_manquants, accents_tolerants, diagnostiquer, enregistrer_faute
from lem.similarite import Comparaison, Proximite, comparer, enregistrer_confusion
from lem.texte import normaliser

TYPES_HORS_JEU = ("nom_propre", "sigle")
REGISTRES_SOUTENUS = frozenset({"formal", "literary", "littéraire", "soutenu"})
REGISTRES_VIEILLIS = frozenset({"dated", "archaic", "vieilli"})
REGISTRES_FAMILIERS = frozenset({"familiar", "familier", "slang", "vulgar", "argot", "vulgaire"})
ECART_RETOUR = 4
SEUIL_SEM_DEFAUT = 0.76
MARGE_UPGRADE_ZIPF = 0.3

SYNONYMES = enregistrer(Jeu(
    id="synonymes",
    nom="Synonymes",
    accroche="Un mot soutenu ou littéraire. Tous ses synonymes, vite.",
    description="Entraînement rapide aux synonymes (français soutenu, Sciences Po, littéraire) : "
                "validation immédiate sans LLM (Wiktionnaire + bge-m3), nuage déplié et répétition espacée.",
    params=[
        Param("mode", "Mode", str, "libre", "libre = sans chrono · chrono = avec minuterie",
              choix=("libre", "chrono")),
        Param("registre", "Registre", str, "standard",
              "standard = soutenu + littéraire (sans familier) · litteraire = formel/littéraire d'abord · tous = tout",
              choix=("standard", "litteraire", "tous")),
        Param("synonymes_min", "Synonymes min", int, 2,
              "nombre minimal de synonymes connus en base pour tirer un mot", 1, 50),
        Param("nouveaux", "Mots nouveaux par session", int, 10, "", 0, 200),
        Param("duree", "Temps mode chrono (s)", float, 20.0,
              "temps par mot en mode chrono", 5, 300),
        Param("ancre_s", "Premier synonyme en moins de (s)", float, 5.0,
              "plus lent : le mot compte comme hésitant", 1, 60),
        Param("seuil_sem", "Seuil proximité bge-m3", float, SEUIL_SEM_DEFAUT,
              "cosinus minimal pour accepter un proche synonyme absent du Wiktionnaire (0 = désactivé)", 0.0, 1.0),
        Param("ordre", "Ordre des nouveaux", str, "equilibre",
              "equilibre = utile + soutenu · rares = littéraires d'abord · hasard",
              choix=("equilibre", "rares", "hasard")),
        *PARAMS_FILTRES,
    ],
))


def _card_id(entree_id: int) -> int:
    return int.from_bytes(hashlib.blake2b(f"synonyme:{entree_id}".encode(), digest_size=6).digest(), "big")


def note_synonyme(trouves: int, premiere_ms: int | None, ancre_s: float) -> int:
    if not trouves or premiere_ms is None:
        return 1
    if premiere_ms > ancre_s * 1000:
        return 2
    if premiere_ms <= ancre_s * 500 and trouves >= 2:
        return 4
    return 3


@dataclass
class EtatSynonyme:
    entree_id: int
    card: Card
    vues: int = 0
    derniere_note: int | None = None


def etats_synonymes(con: sqlite3.Connection) -> dict[int, EtatSynonyme]:
    """Rejoue le journal de Synonymes : l'état FSRS de chaque mot déjà travaillé."""
    from lem.memory.scheduler import ordonnanceur

    sched = ordonnanceur(con, "synonyme")
    etats: dict[int, EtatSynonyme] = {}
    for r in con.execute(
        """SELECT ts, entree_id, note_auto FROM evenement
           WHERE jeu = 'synonymes' AND type = 'mot' AND entree_id IS NOT NULL AND note_auto IS NOT NULL
           ORDER BY ts, id"""
    ):
        eid, quand = int(r["entree_id"]), parse_iso(r["ts"])
        e = etats.get(eid) or EtatSynonyme(eid, Card(card_id=_card_id(eid), due=quand))
        e.card, _ = sched.review_card(e.card, Rating(r["note_auto"]), quand)
        e.vues += 1
        e.derniere_note = r["note_auto"]
        etats[eid] = e
    return etats


def produits(con: sqlite3.Connection) -> set[str]:
    """Normes de tous les mots produits (Bombe, Syllabaire, Synonymes, LaBOG)."""
    return {
        normaliser(r[0])
        for r in con.execute(
            """SELECT DISTINCT reponse FROM evenement
               WHERE jeu IN ('bombe', 'syllabaire', 'synonymes', 'labog') AND type = 'reponse'
                 AND correct = 1 AND trace = 'prod' AND reponse IS NOT NULL"""
        )
    }


def charger_graphe_synonymes(con: sqlite3.Connection) -> dict[int, set[int]]:
    """Graphe bidirectionnel des synonymes (uniquement des mots simples, hors noms propres et sigles)."""
    graphe: dict[int, set[int]] = {}
    for a, b in con.execute(
        """SELECT r.source_id, r.cible_id FROM relation r
           JOIN entree ea ON ea.id = r.source_id
           JOIN entree eb ON eb.id = r.cible_id
           WHERE r.type = 'synonyme'
             AND ea.langue = 'fr' AND eb.langue = 'fr'
             AND ea.type NOT IN ('nom_propre', 'sigle')
             AND eb.type NOT IN ('nom_propre', 'sigle')
             AND instr(ea.forme, ' ') = 0 AND instr(eb.forme, ' ') = 0
             AND ea.forme NOT GLOB '[A-Z]*' AND eb.forme NOT GLOB '[A-Z]*'"""
    ):
        if a != b:
            graphe.setdefault(a, set()).add(b)
            graphe.setdefault(b, set()).add(a)
    return graphe


def registres_principaux(con: sqlite3.Connection) -> dict[int, str]:
    """Registre du sens principal (rang 1) de chaque entrée qui en porte un."""
    return {
        r[0]: r[1].lower()
        for r in con.execute(
            "SELECT entree_id, registre FROM sens WHERE rang = 1 AND registre IS NOT NULL AND registre != ''"
        )
    }


@dataclass
class SynonymeCible:
    entree_id: int
    forme: str
    zipf: float
    registre: str = ""
    statut: str = "manque"     # trouve | deja | manque
    proche: bool = False       # trouvé via bge-m3 (absent du graphe Wiktionnaire)


@dataclass
class TourSynonyme:
    entree_id: int
    forme: str
    pos: str
    zipf: float
    registre: str
    n_synonymes: int
    nouveau: bool
    retour: bool = False
    donnes: list[str] = field(default_factory=list)          # formes reconnues
    donnes_normes: set[str] = field(default_factory=set)
    donnes_ids: set[int] = field(default_factory=set)
    proches_donnes: list[tuple[int, str]] = field(default_factory=list)
    upgrades: int = 0
    premiere_ms: int | None = None


@dataclass
class ReponseSynonyme:
    valide: bool
    message: str = ""
    forme: str | None = None
    entree_id: int | None = None
    mode: str = ""                                 # synonyme | proche | graphie | autre
    sim: float | None = None
    upgrade: bool = False
    inedit: bool = False
    proximite: Proximite | None = None
    comparaison: Comparaison | None = None
    graphie: Diagnostic | None = None


@dataclass
class BilanSynonyme:
    entree_id: int
    forme: str
    definition: str
    note: int
    synonymes: list[SynonymeCible]
    ancre: SynonymeCible | None
    etendue: tuple[int, int]       # (synonymes déjà produits un jour, synonymes du graphe)
    upgrades: int
    jours: float


class PartieSynonymes:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 magasin_entrees=None, session_id: int | None = None, rng: random.Random | None = None,
                 lexique=None):
        self.con, self.params, self.session_id = con, params, session_id
        self.rng = rng or random.Random()
        self.magasin = magasin_entrees
        if lexique is None:
            from lem.lexique import Lexique
            lexique = Lexique(con)
        self.lex = lexique
        self.carte = carte or CarteConnaissance(con, lexique)
        self.accents_tolerants = accents_tolerants(con)
        self.tour: TourSynonyme | None = None
        self.faites: list[tuple[int, int]] = []          # (entree_id, note)
        self.retours: list[tuple[int, int]] = []         # (numéro de tour minimum, entree_id)
        self.n_tours = 0
        self.total_upgrades = 0
        self.graphe = charger_graphe_synonymes(con)
        self.registres = registres_principaux(con)
        self.reconfigurer()

    # -- données ------------------------------------------------------------------------------------
    def reconfigurer(self) -> None:
        rows = selectionner(self.con, filtres_depuis(self.params))
        self.vivier = {r["id"]: r for r in rows if r["type"] not in TYPES_HORS_JEU}
        self.etats = etats_synonymes(self.con)
        self.produits = produits(self.con)
        self._construire_file()

    def _eligible_cible(self, eid: int, strict: bool = True) -> bool:
        r = self.vivier.get(eid)
        if r is None:
            return False
        syns = self.graphe.get(eid)
        if not syns or len(syns) < self.params.get("synonymes_min", 2):
            return False
        reg = self.registres.get(eid, "")
        mode = self.params.get("registre", "standard")
        if mode == "tous" or not strict:
            return True
        if reg in REGISTRES_FAMILIERS or "-" in r["forme"]:
            return False
        z = r["zipf"] or 0.0
        if mode == "litteraire":
            return reg in REGISTRES_SOUTENUS or reg in REGISTRES_VIEILLIS or (0.0 < z <= 3.5) or z == 0.0
        # standard : français soutenu (Sciences Po : Zipf 2,4–4,6) + mots littéraires/formels
        return (reg in REGISTRES_SOUTENUS and z >= 1.5) or (2.4 <= z <= 4.6)

    def _construire_file(self) -> None:
        t = now()
        eligibles = [eid for eid in self.vivier if self._eligible_cible(eid, strict=True)]
        if len(eligibles) < max(5, self.params.get("nouveaux", 10)):
            # Petite base (démo) ou filtre très étroit : on relâche le filtre de registre/Zipf.
            eligibles = [eid for eid in self.vivier if self._eligible_cible(eid, strict=False)]
        elig_set = set(eligibles)

        dues = [e for e in self.etats.values() if e.card.due <= t and e.entree_id in elig_set]
        dues.sort(key=lambda e: e.card.due)

        # Dédupliquer par forme minuscule (homographes nom/adj → une seule entrée, celle avec le plus de synonymes).
        meilleur_par_forme: dict[str, int] = {}
        for eid in eligibles:
            if eid in self.etats:
                continue
            f = self.vivier[eid]["forme"].lower()
            prev = meilleur_par_forme.get(f)
            if prev is None or len(self.graphe.get(eid, ())) > len(self.graphe.get(prev, ())):
                meilleur_par_forme[f] = eid
        nouveaux = list(meilleur_par_forme.values())

        ordre = self.params.get("ordre", "equilibre")
        if ordre == "hasard":
            self.rng.shuffle(nouveaux)
        elif ordre == "rares":
            nouveaux.sort(key=lambda eid: (
                0 if self.registres.get(eid, "") in REGISTRES_SOUTENUS else 1,
                self.vivier[eid]["zipf"] or 0.0,
                -len(self.graphe.get(eid, ())),
            ))
        else:
            # équilibre : favorise les mots soutenus/littéraires dans la zone proximale et riches en synonymes.
            p_map = dict(zip(nouveaux, self.carte.p_de(nouveaux))) if nouveaux else {}

            def score(eid: int) -> float:
                r = self.vivier[eid]
                z = r["zipf"] or 0.0
                reg = self.registres.get(eid, "")
                # Cloche centrée sur le français soutenu (Zipf ~3,3) + coup de pouce aux mots formels/littéraires.
                cloche = max(0.25, 1.0 - abs(z - 3.3) / 2.2) if z > 0 else 0.4
                bonus_reg = 1.35 if reg in REGISTRES_SOUTENUS else 1.0
                bonus_syn = min(1.8, 0.7 + 0.12 * len(self.graphe.get(eid, ())))
                base = 0.5 + 0.5 * score_apprentissage(p_map.get(eid, 0.5), z)
                return base * cloche * bonus_reg * bonus_syn + 0.35 * self.rng.random()

            nouveaux.sort(key=score, reverse=True)

        k = self.params.get("nouveaux", 10)
        self.dues = [e.entree_id for e in dues]
        self.nouveaux = nouveaux[:k]
        self.reserve = nouveaux[k:]

    def restant(self) -> int:
        return len(self.dues) + len(self.nouveaux) + len(self.retours)

    def encore(self, n: int | None = None) -> None:
        k = n or self.params.get("nouveaux") or 10
        self.nouveaux, self.reserve = self.reserve[:k], self.reserve[k:]

    # -- tour ---------------------------------------------------------------------------------------
    def suivant(self) -> TourSynonyme | None:
        self.n_tours += 1
        pret = next((r for r in self.retours if r[0] <= self.n_tours), None)
        if pret is not None:
            self.retours.remove(pret)
            eid, nouveau, retour = pret[1], False, True
        elif self.dues and (not self.nouveaux or len(self.dues) >= len(self.nouveaux) or self.n_tours % 3):
            eid, nouveau, retour = self.dues.pop(0), False, False
        elif self.nouveaux:
            eid, nouveau, retour = self.nouveaux.pop(0), True, False
        elif self.retours:
            _, eid = self.retours.pop(0)
            nouveau, retour = False, True
        else:
            self.tour = None
            return None
        row = self.con.execute("SELECT forme, pos, zipf FROM entree WHERE id = ?", (eid,)).fetchone()
        syns = self.graphe.get(eid, set())
        self.tour = TourSynonyme(
            entree_id=eid,
            forme=row["forme"],
            pos=row["pos"] or "",
            zipf=row["zipf"] or 0.0,
            registre=self.registres.get(eid, ""),
            n_synonymes=len(syns),
            nouveau=nouveau,
            retour=retour,
        )
        return self.tour

    def _valider_forme(self, texte: str) -> tuple[str, tuple[int, ...]]:
        cle = texte.strip().lower()
        ids = self.lex.entrees(cle, TYPES_HORS_JEU)
        if not ids and self.accents_tolerants:
            for f in self.lex.formes_de_norme(normaliser(cle)):
                if accents_manquants(cle, f) and (ids_f := self.lex.entrees(f, TYPES_HORS_JEU)):
                    return f, ids_f
        return cle, ids

    def _est_meme_mot(self, ids: tuple[int, ...], norme: str) -> bool:
        t = self.tour
        return t.entree_id in ids or norme == normaliser(t.forme)

    def proposer(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> ReponseSynonyme:
        t = self.tour
        texte = nettoyer_saisie(texte)
        norme = normaliser(texte)
        if not norme:
            return ReponseSynonyme(False, "réponse vide")
        if norme in t.donnes_normes:
            return ReponseSynonyme(False, "déjà donné")
        cle, ids = self._valider_forme(texte)
        if not ids:
            if not (frappe and frappe.dictee):
                syn_ids = self.graphe.get(t.entree_id, set())
                cands = [r[0] for r in self.con.execute(
                    f"SELECT forme FROM entree WHERE id IN ({', '.join('?' * len(syn_ids))})", list(syn_ids)
                )] if syn_ids else []
                diag = diagnostiquer(self.con, texte, None, cands or None)
                if diag:
                    enregistrer_faute(self.con, self.session_id, "synonymes", diag, latence_ms, frappe)
                    return ReponseSynonyme(False, "graphie", entree_id=diag.entree_id, mode="graphie", graphie=diag)
            return ReponseSynonyme(False, "mot inconnu")
        if self._est_meme_mot(ids, norme):
            return ReponseSynonyme(False, "c'est le mot demandé")

        syn_ids = self.graphe.get(t.entree_id, set())
        direct = next((i for i in ids if i in syn_ids), None)
        mode = ""
        eid = ids[0]
        sim: float | None = None

        if direct is not None:
            eid, mode = direct, "synonyme"
        else:
            seuil = float(self.params.get("seuil_sem", SEUIL_SEM_DEFAUT))
            if self.magasin is not None and seuil > 0:
                meilleur = max(((i, self.magasin.sim(t.entree_id, i)) for i in ids),
                               key=lambda x: x[1] if x[1] is not None else -1.0)
                if meilleur[1] is not None and meilleur[1] >= seuil:
                    eid, sim, mode = meilleur[0], meilleur[1], "proche"

        if not mode:
            comp = comparer(self.con, t.entree_id, eid, self.magasin)
            if comp.proximite in (Proximite.JUMEAU, Proximite.FORME, Proximite.SENS):
                enregistrer_confusion(self.con, t.entree_id, eid, 0.5)
            ecrire(self.con, self.session_id, Evenement(
                "synonymes", "tentative", entree_id=t.entree_id, reponse=cle, correct=False,
                latence_ms=latence_ms, frappe=frappe or Frappe(),
                data={"donne_id": eid, "proximite": comp.proximite.value, "fsrs": False}))
            return ReponseSynonyme(False, comp.message or "pas un synonyme", entree_id=eid, mode="autre",
                                   proximite=comp.proximite, comparaison=comp)

        row_rep = self.con.execute("SELECT forme, zipf FROM entree WHERE id = ?", (eid,)).fetchone()
        forme_rep = row_rep["forme"] if row_rep else cle
        z_rep = (row_rep["zipf"] or 0.0) if row_rep else 0.0
        upgrade = bool(t.zipf > 0 and z_rep > 0 and z_rep < t.zipf - MARGE_UPGRADE_ZIPF)
        inedit = norme not in self.produits

        t.donnes.append(forme_rep)
        t.donnes_normes.add(norme)
        t.donnes_normes.add(normaliser(forme_rep))
        t.donnes_ids.add(eid)
        if mode == "proche":
            t.proches_donnes.append((eid, forme_rep))
        if upgrade:
            t.upgrades += 1
            self.total_upgrades += 1
        self.produits.add(norme)
        self.produits.add(normaliser(forme_rep))
        if t.premiere_ms is None:
            t.premiere_ms = latence_ms

        ev = ecrire(self.con, self.session_id, Evenement(
            "synonymes", "reponse", entree_id=eid, trace="prod", reponse=forme_rep, correct=True,
            latence_ms=latence_ms, frappe=frappe or Frappe(),
            data={"cible_id": t.entree_id, "cible": t.forme, "mode": mode,
                  "sim": None if sim is None else round(sim, 3), "upgrade": upgrade,
                  "rang": len(t.donnes), "fsrs": False}))
        self.carte.observer(eid, 1.0, poids=POIDS_PRODUIT, source="synonymes", evenement_id=ev, propager=False)
        msg = "synonyme soutenu !" if upgrade else ("proche synonyme" if mode == "proche" else "")
        return ReponseSynonyme(True, msg, forme=forme_rep, entree_id=eid, mode=mode, sim=sim,
                               upgrade=upgrade, inedit=inedit)

    def terminer(self, duree_ms: int | None = None) -> BilanSynonyme:
        t = self.tour
        note = note_synonyme(len(t.donnes), t.premiere_ms, self.params["ancre_s"])
        ev = ecrire(self.con, self.session_id, Evenement(
            "synonymes", "mot", entree_id=t.entree_id, trace="synonyme", reponse=t.forme,
            correct=bool(t.donnes), note_auto=note, latence_ms=t.premiere_ms,
            data={"n": t.n_synonymes, "trouves": t.donnes, "upgrades": t.upgrades,
                  "nouveau": t.nouveau, "retour": t.retour, "duree_ms": duree_ms, "fsrs": False}))
        self.carte.observer(t.entree_id, 0.9 if t.donnes else 0.3, poids=0.6,
                            source="synonymes:cible", evenement_id=ev, propager=False)

        from lem.memory.scheduler import ordonnanceur

        quand = now()
        e = self.etats.get(t.entree_id) or EtatSynonyme(t.entree_id, Card(card_id=_card_id(t.entree_id), due=quand))
        e.card, _ = ordonnanceur(self.con, "synonyme").review_card(e.card, Rating(note), quand)
        e.vues += 1
        e.derniere_note = note
        self.etats[t.entree_id] = e
        self.faites.append((t.entree_id, note))
        if note <= 2 and not t.retour:
            self.retours.append((self.n_tours + ECART_RETOUR, t.entree_id))

        syns = self.deplier(t.entree_id, t.donnes_ids, t.proches_donnes)
        officiels = [x for x in syns if not x.proche]
        connus = sum(1 for x in officiels if x.statut != "manque")
        ancre = self._ancre(officiels or syns)
        d = self.con.execute(
            "SELECT COALESCE(definition_precise, definition) FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
            (t.entree_id,),
        ).fetchone()
        jours = max(0.0, (e.card.due - quand).total_seconds() / 86400)
        return BilanSynonyme(
            entree_id=t.entree_id,
            forme=t.forme,
            definition=d[0] if d else "",
            note=note,
            synonymes=syns,
            ancre=ancre,
            etendue=(connus, len(officiels)),
            upgrades=t.upgrades,
            jours=jours,
        )

    def deplier(self, entree_id: int, trouves_ids: set[int] = frozenset(),
                proches: list[tuple[int, str]] = ()) -> list[SynonymeCible]:
        syn_ids = list(self.graphe.get(entree_id, set()))
        out: list[SynonymeCible] = []
        if syn_ids:
            rows = self.con.execute(
                f"SELECT id, forme, zipf FROM entree WHERE id IN ({', '.join('?' * len(syn_ids))})", syn_ids
            ).fetchall()
            for r in rows:
                n = normaliser(r["forme"])
                statut = "trouve" if r["id"] in trouves_ids else ("deja" if n in self.produits else "manque")
                out.append(SynonymeCible(r["id"], r["forme"], r["zipf"] or 0.0,
                                         self.registres.get(r["id"], ""), statut))
        # Dédupliquer par forme minuscule (priorité : trouve > deja > manque).
        vus: set[str] = set()
        uniques: list[SynonymeCible] = []
        ordre_statut = {"trouve": 0, "deja": 1, "manque": 2}
        for x in sorted(out, key=lambda x: (ordre_statut[x.statut], -x.zipf, x.forme)):
            cle = x.forme.lower()
            if cle not in vus:
                vus.add(cle)
                uniques.append(x)
        # Ajouter les proches acceptés via bge-m3.
        for pid, pforme in proches:
            if pforme.lower() not in vus:
                vus.add(pforme.lower())
                uniques.append(SynonymeCible(pid, pforme, 0.0, self.registres.get(pid, ""), "trouve", proche=True))
        return sorted(uniques, key=lambda x: (ordre_statut[x.statut], 0 if x.registre in REGISTRES_SOUTENUS else 1, -x.zipf, x.forme))

    @staticmethod
    def _ancre(syns: list[SynonymeCible]) -> SynonymeCible | None:
        if not syns:
            return None
        soutenus = [x for x in syns if x.registre in REGISTRES_SOUTENUS]
        miens = [x for x in syns if x.statut != "manque"]
        return max(miens or soutenus or syns, key=lambda x: (x.zipf, -len(x.forme)))

    def bilan_session(self) -> dict:
        notes = [n for _, n in self.faites]
        return {
            "mots": len({e for e, _ in self.faites}),
            "seches": sum(1 for n in notes if n == 1),
            "hesitants": sum(1 for n in notes if n == 2),
            "surs": sum(1 for n in notes if n >= 3),
            "upgrades": self.total_upgrades,
        }


def resume(con: sqlite3.Connection, quand: datetime | None = None) -> dict:
    """Pour l'Atlas : mots travaillés en Synonymes, sûrs, dus, montées de registre."""
    etats = etats_synonymes(con)
    quand = quand or now()
    vues = list(etats.values())
    sures = [e for e in vues if e.card.stability and e.card.stability >= 21]
    dues = [e for e in vues if e.card.due <= quand]
    upgrades = con.execute(
        """SELECT COUNT(*) FROM evenement WHERE jeu = 'synonymes' AND type = 'reponse'
           AND correct = 1 AND json_extract(data, '$.upgrade') = 1"""
    ).fetchone()[0]
    return {"vues": len(vues), "sures": len(sures), "dues": len(dues), "upgrades": upgrades}
