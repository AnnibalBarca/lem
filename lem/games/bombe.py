"""Bombe (façon JKLM / WordBomb), branchée sur ta mémoire.

Modes
  libre         : comme JKLM — une suite de lettres, n'importe quel mot français qui la contient.
  apprentissage : la suite de lettres est tirée d'un mot CIBLE (flou ou en cours d'apprentissage).
                  Tout mot valide fait passer la bombe, mais tant que la cible n'est pas trouvée,
                  la même suite revient — puis s'allonge vers la cible.
  confusables   : la cible est choisie dans une grappe de confusables et la suite de lettres est
                  prise dans leur partie commune (-onym- → homonyme, paronyme, éponyme…).

Combien de fois répéter une suite ? Selon K = Σ P(connu) des mots du filtre qui la contiennent :
  K ≥ k_haut (300)      → pas de répétition : trop de solutions, aucun intérêt ;
  K ≤ repetitions_max   → répéter ⌈K⌉ fois (« si j'en connais 5, la proposer 5 fois ») ;
  entre les deux        → interpolation logarithmique.
À chaque mot donné qui n'est pas la cible : la suite s'allonge d'une lettre vers la cible, en
évitant d'être contenue dans le mot que tu viens de donner. Arrivée au mot entier : révélation.

Chaque mot donné est comparé à la cible : « Presque pareil ! » (rouge), « Similaire par le sens »
(orange), etc. — ce n'est pas un hasard, c'est un voisin dans TA mémoire.
"""

from __future__ import annotations

import math
import random
import sqlite3
import statistics
from collections import deque
from dataclasses import dataclass, field

from lem.confusables import IndexFormel
from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.journal import Evenement, Frappe, ecrire, nettoyer_saisie
from lem.memory.knowledge import CarteConnaissance, score_apprentissage
from lem.orthographe import Diagnostic, diagnostiquer, enregistrer_faute
from lem.similarite import Comparaison, Proximite, comparer, enregistrer_confusion
from lem.texte import normaliser

ALPHABET_BONUS = "abcdefghijlmnopqrstuv"

BOMBE = enregistrer(Jeu(
    id="bombe",
    nom="Bombe",
    accroche="Une suite de lettres. Un mot. Avant l'explosion.",
    description="JKLM branché sur ta mémoire : répétition intelligente, cibles floues, confusables.",
    params=[
        Param("mode", "Mode", str, "apprentissage", "libre | apprentissage | confusables",
              choix=("libre", "apprentissage", "confusables")),
        *PARAMS_FILTRES,
        Param("long_min", "Lettres min", int, 2, "longueur minimale de la suite", 2, 10),
        Param("long_max", "Lettres max", int, 3, "longueur maximale de la suite", 2, 12),
        Param("solutions_min", "Solutions min", int, 3, "mode libre : mots possibles minimum par suite", 1, 10_000),
        Param("vies", "Vies", int, 3, "", 1, 20),
        Param("vies_max", "Vies max", int, 3, "", 1, 20),
        Param("regain", "Regain de vie", str, "alphabet", "aucun | alphabet | mot_rare",
              choix=("aucun", "alphabet", "mot_rare")),
        Param("temps_min", "Bombe min (s)", float, 6.0, "", 1, 120),
        Param("temps_max", "Bombe max (s)", float, 14.0, "", 1, 300),
        Param("k_haut", "Seuil sans répétition", int, 300, "au-delà, la suite ne revient pas", 2, 100_000),
        Param("repetitions_max", "Répétitions max", int, 5, "", 0, 20),
        Param("adaptatif", "Adaptatif", bool, True, "ajuste difficulté et durée à ta réussite"),
    ],
))


def budget_repetitions(k: float, k_haut: float, r_max: int) -> int:
    if r_max <= 0 or k >= k_haut:
        return 0
    if k <= r_max:
        return max(1, math.ceil(k))
    return max(1, round(r_max * math.log(k_haut / k) / math.log(k_haut / r_max)))


@dataclass
class Invite:
    suite: str                      # normalisée
    cible_id: int | None = None
    cible_forme: str | None = None
    repetitions: int = 0            # répétitions restantes
    k: float = 0.0                  # mots connus attendus contenant la suite
    n_solutions: int = 0
    duree: float = 10.0
    donnes: list[str] = field(default_factory=list)   # mots donnés sur cette cible


@dataclass
class Retour:
    valide: bool
    message: str
    entree_id: int | None = None
    proximite: Proximite | None = None
    comparaison: Comparaison | None = None
    cible_trouvee: bool = False
    revelation: tuple[str, str] | None = None   # (forme, définition)
    vie_gagnee: bool = False
    points: float = 0.0
    graphie: Diagnostic | None = None           # faute de graphie identifiée


class PartieBombe:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 magasin_entrees=None, session_id: int | None = None, rng: random.Random | None = None):
        self.con, self.params = con, params
        self.carte = carte or CarteConnaissance(con)
        self.magasin = magasin_entrees
        self.session_id = session_id
        self.rng = rng or random.Random()
        self.vies = params["vies"]
        self.score = 0.0
        self.utilises: set[str] = set()
        self.lettres: set[str] = set()
        self.historique: deque[bool] = deque(maxlen=10)
        self.latences: deque[int] = deque(maxlen=10)
        self.difficulte = 0.5
        self.facteur_temps = 1.0
        self._univers()
        self.reconfigurer()
        self.invite: Invite = self.nouvelle_invite()

    # -- données ------------------------------------------------------------
    def _univers(self) -> None:
        """Toutes les formes valides du français (comme JKLM, indépendamment des filtres).

        Graphie exacte exigée : accents compris. Aucune variante n'est acceptée."""
        self.valides: dict[str, set[int]] = {}
        for forme, eid in self.con.execute("SELECT forme, entree_id FROM forme"):
            self.valides.setdefault(forme, set()).add(eid)
        self._candidats: dict[str, list[str]] = {}

    def reconfigurer(self) -> None:
        """(Re)construit le vivier après un changement de paramètres (/domaine, /etat…)."""
        rows = selectionner(self.con, filtres_depuis(self.params))
        self.vivier = {r["id"]: r for r in rows}
        formes: dict[int, list[str]] = {}
        if self.vivier:
            ids = list(self.vivier)
            for i in range(0, len(ids), 900):
                bloc = ids[i : i + 900]
                for norme, eid in self.con.execute(
                    f"SELECT norme, entree_id FROM forme WHERE entree_id IN ({', '.join('?' * len(bloc))})", bloc
                ):
                    formes.setdefault(eid, []).append(norme)
        self.formes_vivier = formes
        self.index = IndexFormel(self.con, set(self.vivier)) if self.params["mode"] == "confusables" else None

    def solutions(self, suite: str) -> tuple[int, float]:
        """(nombre de mots du vivier contenant la suite, K = Σ P(connu))."""
        n, k = 0, 0.0
        for eid, fs in self.formes_vivier.items():
            if any(suite in f for f in fs):
                n += 1
                k += self.carte.p(eid)
        return n, k

    # -- choix de l'invite ----------------------------------------------------
    def _duree(self) -> float:
        tmin, tmax = self.params["temps_min"], max(self.params["temps_min"], self.params["temps_max"])
        return max(1.0, self.rng.uniform(tmin, tmax) * self.facteur_temps)

    def _longueur(self) -> int:
        lo, hi = self.params["long_min"], max(self.params["long_min"], self.params["long_max"])
        return self.rng.randint(lo, hi)

    def _invite_libre(self) -> Invite:
        formes = [f for fs in self.formes_vivier.values() for f in fs]
        for _ in range(60):
            if not formes:
                break
            f = self.rng.choice(formes)
            L = self._longueur()
            if len(f) < L:
                continue
            i = self.rng.randint(0, len(f) - L)
            s = f[i : i + L]
            if not s.isalpha():
                continue
            n, k = self.solutions(s)
            if n >= self.params["solutions_min"]:
                return Invite(s, k=k, n_solutions=n, duree=self._duree())
        return Invite("e", k=0, n_solutions=0, duree=self._duree())

    def _choisir_cible(self) -> sqlite3.Row | None:
        cands = [r for r in self.vivier.values() if len(r["forme"]) >= self.params["long_min"] + 1
                 and r["forme"].replace("-", "").isalpha()]
        if not cands:
            return None
        # difficulté ∈ [0, 1] : 0 → cibles plutôt connues, 1 → plutôt inconnues.
        def poids(r):
            p = self.carte.p(r["id"])
            base = score_apprentissage(p, r["zipf"])
            return base * (1 + self.difficulte * (1 - p) * 2) * (1 + (1 - self.difficulte) * p)
        echantillon = self.rng.sample(cands, min(len(cands), 400))
        echantillon.sort(key=poids, reverse=True)
        return self.rng.choice(echantillon[: max(1, len(echantillon) // 10)])

    def _suite_distinctive(self, forme: str, communs: list[str] | None = None) -> str:
        f = normaliser(forme)
        L = min(self._longueur(), len(f) - 1)
        subs = {f[i : i + L] for i in range(len(f) - L + 1) if f[i : i + L].isalpha()}
        if communs:
            partages = [s for s in subs if sum(s in normaliser(c) for c in communs) >= 1]
            if partages:
                subs = set(partages)
        if not subs:
            return f[:L]
        return min(subs, key=lambda s: (self.solutions(s)[0] if communs is None else -sum(
            s in normaliser(c) for c in communs), self.rng.random()))

    def nouvelle_invite(self) -> Invite:
        mode = self.params["mode"]
        if mode == "libre":
            return self._invite_libre()
        cible = self._choisir_cible()
        if cible is None:
            return self._invite_libre()
        communs = None
        if mode == "confusables" and self.index is not None:
            from lem.confusables import grappe
            communs = [c.forme for c in grappe(self.con, cible["id"], self.magasin, self.index, k=6)]
        s = self._suite_distinctive(cible["forme"], communs)
        n, k = self.solutions(s)
        reps = budget_repetitions(k, self.params["k_haut"], self.params["repetitions_max"])
        inv = Invite(s, cible["id"], cible["forme"], reps, k, n, self._duree())
        ecrire(self.con, self.session_id, Evenement(
            "bombe", "stimulus", entree_id=cible["id"], data={"suite": s, "k": round(k, 2), "n": n,
                                                              "repetitions": reps, "mode": mode}))
        return inv

    def etendre(self, suite: str, cible: str, evite: list[str]) -> str | None:
        """Allonge la suite vers la cible, en évitant les mots déjà donnés. None = mot entier atteint."""
        c = normaliser(cible)
        i = c.find(suite)
        if i < 0 or len(suite) >= len(c):
            return None
        pas = 2 if self.params.get("adaptatif") and self.difficulte < 0.3 else 1
        cands = []
        for g in range(pas + 1):
            d = pas - g
            if i - g >= 0 and i + len(suite) + d <= len(c):
                cands.append(c[i - g : i + len(suite) + d])
        cands = [x for x in cands if x != suite] or [c[max(0, i - 1) : i + len(suite) + 1]]
        if any(len(x) >= len(c) for x in cands):
            return None
        evite_n = [normaliser(e) for e in evite]
        cands.sort(key=lambda x: (sum(x in e for e in evite_n), self.solutions(x)[0]))
        return cands[0]

    # -- tour de jeu ---------------------------------------------------------
    def _valider(self, texte: str) -> tuple[str | None, set[int]]:
        cle = texte.strip().lower()
        return cle, self.valides.get(cle, set())

    def _diagnostic(self, texte: str) -> Diagnostic | None:
        suite = self.invite.suite
        if suite not in self._candidats:
            self._candidats[suite] = [k for k in self.valides if suite in normaliser(k)]
        return diagnostiquer(self.con, texte, self.invite.cible_id, self._candidats[suite])

    def repondre(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Retour:
        texte = nettoyer_saisie(texte)
        inv = self.invite
        cle, ids = self._valider(texte)
        suite_ok = inv.suite in normaliser(texte)
        base = {"suite": inv.suite, "cible": inv.cible_forme, "k": round(inv.k, 2), "n": inv.n_solutions,
                "repetitions_restantes": inv.repetitions, "fsrs": False}
        if cle and suite_ok and not ids and cle not in self.utilises and not (frappe and frappe.dictee):
            diag = self._diagnostic(texte)
            if diag:
                enregistrer_faute(self.con, self.session_id, "bombe", diag, latence_ms, frappe)
                return Retour(False, "graphie", diag.entree_id, graphie=diag)
        if not cle or not suite_ok or not ids or cle in self.utilises:
            raison = ("ne contient pas « %s »" % inv.suite.upper() if not suite_ok
                      else "déjà utilisé" if cle in self.utilises else "mot inconnu")
            ecrire(self.con, self.session_id, Evenement("bombe", "tentative", reponse=texte, correct=False,
                                                        latence_ms=latence_ms, frappe=frappe or Frappe(),
                                                        data={**base, "raison": raison}))
            return Retour(False, raison)

        self.utilises.add(cle)
        self.latences.append(latence_ms)
        eid = self._entree_preferee(ids, inv.cible_id)
        z = self.con.execute("SELECT zipf FROM entree WHERE id = ?", (eid,)).fetchone()["zipf"] or 0.0
        points = 1.0 + max(0.0, 4.0 - z)
        vie = self._regain(cle, z)
        trace_existe = self.con.execute("SELECT 1 FROM trace_etat WHERE entree_id = ? AND trace = 'prod'",
                                        (eid,)).fetchone() is not None
        retour = Retour(True, "", eid, points=points, vie_gagnee=vie)

        if inv.cible_id is not None and eid == inv.cible_id:
            retour.cible_trouvee = True
            retour.message = "Cible trouvée !"
            retour.points += 5
            self.historique.append(True)
            self.carte.observer(eid, 1.0)
            ecrire(self.con, self.session_id, Evenement("bombe", "reponse", entree_id=eid, trace="prod",
                                                        reponse=texte, correct=True, note_auto=3,
                                                        latence_ms=latence_ms, frappe=frappe or Frappe(),
                                                        data={**base, "fsrs": trace_existe, "cible_trouvee": True}))
            self._adapter()
            self.invite = self.nouvelle_invite()
        else:
            # Production spontanée : preuve de vocabulaire actif (sans planification FSRS).
            self.carte.observer(eid, 1.0, poids=0.5)
            data = {**base, "spontane": True}
            if inv.cible_id is not None:
                comp = comparer(self.con, inv.cible_id, eid, self.magasin)
                retour.comparaison, retour.proximite = comp, comp.proximite
                retour.message = comp.message
                data.update({"sim_forme": round(comp.forme, 3), "proximite": comp.proximite.value,
                             "sim_sens": None if comp.sens is None else round(comp.sens, 3)})
                if comp.proximite in (Proximite.JUMEAU, Proximite.FORME, Proximite.SENS):
                    enregistrer_confusion(self.con, inv.cible_id, eid, 0.5)
            ecrire(self.con, self.session_id, Evenement("bombe", "reponse", entree_id=eid, trace="prod",
                                                        reponse=texte, correct=True, latence_ms=latence_ms,
                                                        frappe=frappe or Frappe(), data=data))
            retour.revelation = self._apres_echec_cible(texte)
        self.score += retour.points
        return retour

    def exploser(self) -> Retour:
        """Le temps est écoulé : une vie en moins."""
        self.vies -= 1
        inv = self.invite
        ecrire(self.con, self.session_id, Evenement("bombe", "explosion", entree_id=inv.cible_id,
                                                    data={"suite": inv.suite, "k": round(inv.k, 2), "n": inv.n_solutions}))
        r = Retour(False, "BOUM")
        r.revelation = self._apres_echec_cible(None)
        return r

    @property
    def finie(self) -> bool:
        return self.vies <= 0

    # -- internes ------------------------------------------------------------
    def _entree_preferee(self, ids: set[int], cible: int | None) -> int:
        if cible in ids:
            return cible
        return min(ids, key=lambda i: -(self.vivier[i]["p_connu"] if i in self.vivier else 0))

    def _regain(self, cle: str, zipf: float) -> bool:
        mode = self.params["regain"]
        gagne = False
        if mode == "alphabet":
            self.lettres.update(c for c in cle if c in ALPHABET_BONUS)
            if set(ALPHABET_BONUS) <= self.lettres:
                self.lettres.clear()
                gagne = True
        elif mode == "mot_rare" and zipf < 1.5:
            gagne = True
        if gagne and self.vies < self.params["vies_max"]:
            self.vies += 1
            return True
        return False

    def _apres_echec_cible(self, donne: str | None) -> tuple[str, str] | None:
        inv = self.invite
        if inv.cible_id is None:
            self.invite = self.nouvelle_invite()
            return None
        if donne:
            inv.donnes.append(donne)
        if inv.repetitions > 0:
            inv.repetitions -= 1
            nouvelle = self.etendre(inv.suite, inv.cible_forme, inv.donnes)
            if nouvelle is not None:
                inv.suite = nouvelle
                inv.n_solutions, inv.k = self.solutions(nouvelle)
                inv.duree = self._duree()
                return None
        # Révélation : la cible n'est pas sortie.
        self.historique.append(False)
        self.carte.observer(inv.cible_id, 0.4, poids=0.3)
        d = self.con.execute("SELECT definition FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1",
                             (inv.cible_id,)).fetchone()
        ecrire(self.con, self.session_id, Evenement("bombe", "revelation", entree_id=inv.cible_id,
                                                    data={"suite": inv.suite, "donnes": inv.donnes}))
        rev = (inv.cible_forme, d["definition"] if d else "")
        self._adapter()
        self.invite = self.nouvelle_invite()
        return rev

    def _adapter(self) -> None:
        """Escalier psychophysique : vise ~75 % de cibles trouvées."""
        if not self.params.get("adaptatif") or len(self.historique) < 4:
            return
        taux = sum(self.historique) / len(self.historique)
        if taux > 0.8:
            self.difficulte = min(1.0, self.difficulte + 0.1)
        elif taux < 0.6:
            self.difficulte = max(0.0, self.difficulte - 0.1)
        if len(self.latences) >= 5:
            med = statistics.median(self.latences) / 1000
            moyen = (self.params["temps_min"] + self.params["temps_max"]) / 2
            if med < 0.35 * moyen:
                self.facteur_temps = max(0.5, self.facteur_temps * 0.92)
            elif med > 0.8 * moyen:
                self.facteur_temps = min(1.5, self.facteur_temps * 1.08)
