"""Bombe (façon JKLM / WordBomb), branchée sur ta mémoire.

Modes
  standard      : comme JKLM — une suite de lettres courante, n'importe quel mot français qui la contient.
                  Toujours branché sur la base : chaque mot trouvé est journalisé (temps, fautes) et nourrit
                  la carte de connaissance ; le jeu affiche combien de fois tu l'as trouvé et ton temps habituel.
  conscient     : le tuteur. La suite est tirée d'un mot CIBLE : pour moitié un mot étudié dans les Cartes
                  (le plus tôt dû d'abord), sinon un mot flou ou utile. Tout mot valide fait passer la bombe.
                  Jamais plus de `long_max` lettres (3) : la suite ne doit pas forcer la main vers sa cible.
  confusables   : la cible est choisie dans une grappe de confusables et la suite de lettres est
                  prise dans leur partie commune (-onym- → homonyme, paronyme, éponyme…).

Une cible ne paraît qu'une fois par partie. Ratée, elle revient UNE fois, plus tard (`ecart` invites),
avec sa suite la plus distinctive (au plus `long_max` lettres) : un rappel espacé, pas un allongement
immédiat de la même suite. Elle n'est révélée que si elle était à portée (au plus
`revelation_max` solutions) ; sinon la suite était une suite libre comme une autre.

Comme JKLM, ni noms propres ni sigles (ni comme cibles ni comme réponses) ; une graphie qui est aussi un
nom commun reste valide (« angélique » oui, « anvers » non). Le paramètre `noms_propres` les réadmet.

Chaque mot donné est comparé à la cible : « Presque pareil ! » (rouge), « Similaire par le sens »
(orange), etc. — ce n'est pas un hasard, c'est un voisin dans TA mémoire.
"""

from __future__ import annotations

import random
import re
import sqlite3
import statistics
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from lem.confusables import IndexFormel
from lem.definitions import resume_etymologie
from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.journal import Evenement, Frappe, ecrire, nettoyer_saisie
from lem.memory import scheduler
from lem.memory.knowledge import POIDS_PRODUIT, Y_PAR_NOTE, CarteConnaissance, score_apprentissage
from lem.orthographe import Diagnostic, accents_manquants, accents_tolerants, diagnostiquer, enregistrer_faute
from lem.similarite import Comparaison, Proximite, comparer, enregistrer_confusion
from lem.texte import normaliser

ALPHABET_BONUS = "abcdefghijlmnopqrstuv"

ANCIENS_MODES = {"libre": "standard", "apprentissage": "conscient"}
SEUIL_CONNU = 0.6            # P(connu) à partir de laquelle /d te donne « un mot que tu connais »
SEUIL_CONNU_PRIOR = 0.45     # … et a priori de fréquence à partir duquel un mot compte connu sans preuve sur lui

BOMBE = enregistrer(Jeu(
    id="bombe",
    nom="Bombe",
    accroche="Une suite de lettres. Un mot. Avant l'explosion.",
    description="JKLM branché sur ta mémoire : répétition intelligente, cibles floues, confusables.",
    params=[
        Param("mode", "Mode", str, "standard", "standard | conscient | confusables | synonymes",
              choix=("standard", "conscient", "confusables", "synonymes"), anciens=ANCIENS_MODES),
        *PARAMS_FILTRES,
        Param("long_min", "Lettres min", int, 2, "longueur minimale de la suite", 2, 10),
        Param("long_max", "Lettres max", int, 3, "longueur maximale de la suite", 2, 12),
        Param("sub", "Difficulté : moins de N mots", int, 500, "une suite est tirée si moins de N mots la "
              "contiennent (sub-500 : les invites où l'on sèche) ; 100000 = toutes, même les plus faciles",
              5, 100_000, alias=("difficulte", "diff")),
        Param("solutions_min", "Solutions min", int, 3, "mode standard : mots possibles minimum par suite", 1, 10_000),
        Param("vies", "Vies", int, 3, "", 1, 20),
        Param("vies_max", "Vies max", int, 3, "", 1, 20),
        Param("regain", "Regain de vie", str, "alphabet", "aucun | alphabet | mot_rare",
              choix=("aucun", "alphabet", "mot_rare")),
        Param("temps_min", "Bombe min (s)", float, 10.0, "jamais moins, même quand le jeu accélère", 1, 120),
        Param("temps_max", "Bombe max (s)", float, 15.0, "", 1, 300),
        Param("retours", "Retours d'une cible ratée", int, 1, "une cible ratée revient plus tard, une fois", 0, 3),
        Param("ecart", "Écart avant retour", int, 8, "nombre d'invites avant le retour d'une cible ratée", 2, 100),
        Param("revelation_max", "Révélation si ≤ solutions", int, 30, "la cible n'est révélée que si elle était à portée", 1, 10_000),
        Param("revision", "Part de révision", float, 0.5, "part des cibles prises parmi les mots étudiés dans les Cartes", 0, 1),
        Param("noms_propres", "Noms propres et sigles", bool, False, "les accepter (JKLM les refuse)"),
        Param("adaptatif", "Adaptatif", bool, True, "ajuste difficulté et durée à ta réussite"),
    ],
))


TYPES_HORS_JEU = ("nom_propre", "sigle")
# Rectifications orthographiques de 1990 : les composés d'éléments savants se soudent (« néobabylonien »
# pour « néo-babylonien », « autoécole » pour « auto-école »). La forme soudée est donc correcte.
PREFIXES_SOUDES = tuple(sorted((
    "aero", "afro", "agro", "anglo", "anti", "archi", "audio", "auto", "bio", "cardio", "contre", "cyber",
    "eco", "electro", "entre", "euro", "extra", "franco", "geo", "greco", "hydro", "hyper", "hypo", "infra",
    "inter", "intra", "judeo", "macro", "maxi", "mega", "micro", "mini", "multi", "neo", "para", "photo",
    "poly", "post", "pre", "proto", "pseudo", "psycho", "radio", "retro", "semi", "socio", "supra", "tele",
    "thermo", "turbo", "ultra", "video"), key=len, reverse=True))
SEP = "\x00"
LETTRES = re.compile(r"[a-z]+")         # une suite : des lettres a–z seulement (isalpha() accepte Ɲ, ², ß…)


def trouvailles(con: sqlite3.Connection, entree_id: int) -> dict:
    """Score de trouvaille d'un mot, tiré du journal : combien de fois tu l'as trouvé à la Bombe, ton temps
    médian pour le trouver (de l'apparition de la suite à la validation), tes fautes de graphie dessus."""
    lat = [r[0] for r in con.execute(
        """SELECT latence_ms FROM evenement WHERE entree_id = ? AND jeu = 'bombe' AND type = 'reponse'
           AND correct = 1 AND latence_ms IS NOT NULL AND COALESCE(json_extract(data, '$.aide'), 0) = 0""",
        (entree_id,))]
    fautes = con.execute("""SELECT COUNT(*) FROM evenement WHERE entree_id = ? AND jeu = 'bombe' AND type = 'reponse'
                            AND trace = 'ortho'""", (entree_id,)).fetchone()[0]
    return {"n": len(lat), "latence_mediane_ms": statistics.median(lat) if lat else None, "fautes": fautes}


class _Valides:
    """Vue « forme → entrées valides » sur l'index du lexique (types exclus retirés)."""

    def __init__(self, lex, exclus: tuple[str, ...]):
        self.lex, self.exclus = lex, exclus

    def get(self, forme: str, defaut=None):
        return self.lex.entrees(forme, self.exclus) or defaut

    def __contains__(self, forme: str) -> bool:
        return bool(self.lex.entrees(forme, self.exclus))


@dataclass
class Invite:
    suite: str                      # normalisée (ou mot demandé en mode synonymes)
    cible_id: int | None = None
    cible_forme: str | None = None
    repetitions: int = 0            # retours restants si la cible est ratée
    k: float = 0.0                  # mots connus attendus contenant la suite
    n_solutions: int = 0
    duree: float = 10.0
    donnes: list[str] = field(default_factory=list)   # mots donnés sur cette cible
    est_synonyme: bool = False
    cible_pos: str = ""
    cible_zipf: float = 0.0


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
    forme: str | None = None                    # la forme reconnue (accentuée si la saisie ne l'était pas)
    trouvaille: dict | None = None              # ce que la Bombe sait de toi sur ce mot (cf. trouvailles)
    aide: bool = False                          # le mot venait de /d : vu, pas trouvé


class PartieBombe:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 magasin_entrees=None, session_id: int | None = None, rng: random.Random | None = None,
                 lexique=None):
        params["mode"] = ANCIENS_MODES.get(params.get("mode"), params.get("mode"))
        self.con, self.params = con, params
        self.lex = lexique
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
        self.accents_tolerants = accents_tolerants(con)
        self.n_invites = 0
        self.cibles_vues: set[int] = set()
        self.retours: list[tuple[int, int, int, int]] = []   # (échéance, cible, longueur de suite, retours restants)
        self.facteur_temps = 1.0
        self.suites = None                       # lem.suites.IndexSuites, à la demande (mode standard sans filtre)
        self._suites_cle = None
        self.aides: set[str] = set()             # mots donnés par /d : ne comptent pas comme trouvés
        self.aides_ids: set[int] = set()         # entrées données ou montrées par /d
        self._univers()
        self.reconfigurer()
        self.invite: Invite = self.nouvelle_invite()

    def noter_aides(self, eids, formes=()) -> None:
        """Marque comme aidés les mots montrés par /d (seul ou avec argument)."""
        self.aides_ids.update(int(i) for i in eids if i is not None)
        self.aides.update(f.lower() for f in formes if f)

    # -- données ------------------------------------------------------------
    def _exclus(self) -> tuple[str, ...]:
        return () if self.params.get("noms_propres") else TYPES_HORS_JEU

    def _univers(self) -> None:
        """Toutes les formes valides du français (comme JKLM, indépendamment des filtres), via l'index compact
        du lexique (lem.lexique : partagé, gardé sur disque) — plus 2 M chaînes Python à chaque partie."""
        if self.lex is None:
            from lem.lexique import Lexique
            self.lex = Lexique(self.con)
        self._candidats: dict[tuple[str, int], list[str]] = {}

    @property
    def valides(self) -> "_Valides":
        return _Valides(self.lex, self._exclus())

    @property
    def formes_vivier(self) -> dict[int, list[str]]:
        """Normes des formes de chaque entrée du vivier (calculé à la demande : tests, outils)."""
        return {e: t.split(SEP) for e, t in zip(self._ids_vivier, self._textes_vivier) if t}

    def reconfigurer(self) -> None:
        """(Re)construit le vivier après un changement de paramètres (/domaine, /etat…)."""
        exclus = self._exclus()
        rows = selectionner(self.con, filtres_depuis(self.params))
        self.vivier = {r["id"]: r for r in rows if r["type"] not in exclus}
        # Pour `solutions` : les normes de chaque entrée du vivier en une chaîne, et sa place dans la carte.
        self._ids_vivier = list(self.vivier)
        ks = np.searchsorted(self.lex.eids, np.asarray(self._ids_vivier, dtype=np.int64))
        self._textes_vivier = [self.lex.textes[k] if k < len(self.lex) and self.lex.eids[k] == e else ""
                               for k, e in zip(ks.tolist(), self._ids_vivier)]
        self._pos_vivier = self.carte.positions(self._ids_vivier)
        self._n_cache: dict[str, int] = {}
        self._cibles = [r for r in self.vivier.values() if len(r["forme"]) >= self.params["long_min"] + 1
                        and LETTRES.fullmatch(normaliser(r["forme"]).replace("-", ""))]
        # Mots étudiés dans les Cartes, du plus tôt dû au plus tard : la Bombe sert aussi à les rappeler.
        cibles = {r["id"] for r in self._cibles}
        self._etudies = [r[0] for r in self.con.execute(
            """SELECT entree_id FROM trace_etat WHERE trace IN ('recep', 'prod')
               GROUP BY entree_id ORDER BY MIN(due)""") if r[0] in cibles]
        self.index = IndexFormel(self.con, set(self.vivier), self.lex) if self.params["mode"] == "confusables" else None
        self._graphe_syn: dict[int, set[int]] = {}
        self._cibles_syn: list[sqlite3.Row] = []
        if self.params["mode"] == "synonymes":
            from lem.games.synonymes import (
                REGISTRES_FAMILIERS,
                REGISTRES_SOUTENUS,
                charger_graphe_synonymes,
                registres_principaux,
            )

            self._graphe_syn = charger_graphe_synonymes(self.con)
            regs = registres_principaux(self.con)
            stricts, tous = [], []
            for r in self.vivier.values():
                syns = self._graphe_syn.get(r["id"])
                if not syns or len(syns) < 2:
                    continue
                tous.append(r)
                reg = regs.get(r["id"], "")
                z = r["zipf"] or 0.0
                if reg not in REGISTRES_FAMILIERS and (reg in REGISTRES_SOUTENUS or 1.8 <= z <= 4.7):
                    stricts.append(r)
            self._cibles_syn = stricts if len(stricts) >= 10 else tous

    def solutions(self, suite: str) -> tuple[int, float]:
        """(nombre de mots du vivier contenant la suite, K = Σ P(connu))."""
        idx = [i for i, t in enumerate(self._textes_vivier) if suite in t]
        self._n_cache[suite] = len(idx)
        if not idx:
            return 0, 0.0
        pos = self._pos_vivier[idx]
        p = self.carte.p_tous()
        absents = pos < 0
        k = float(p[pos[~absents]].sum())
        if absents.any():
            k += sum(self.carte.p(self._ids_vivier[i]) for i, a in zip(idx, absents) if a)
        return len(idx), k

    def n_solutions(self, suite: str) -> int:
        """Nombre de mots du vivier contenant la suite (ne dépend que du vivier : mis en cache)."""
        n = self._n_cache.get(suite)
        if n is None:
            n = sum(1 for t in self._textes_vivier if suite in t)
            self._n_cache[suite] = n
        return n

    # -- choix de l'invite ----------------------------------------------------
    def _duree(self) -> float:
        tmin, tmax = self.params["temps_min"], max(self.params["temps_min"], self.params["temps_max"])
        return max(tmin, self.rng.uniform(tmin, tmax) * self.facteur_temps)

    def _longueur(self) -> int:
        lo, hi = self.params["long_min"], max(self.params["long_min"], self.params["long_max"])
        return self.rng.randint(lo, hi)

    def _sans_filtre(self) -> bool:
        p = self.params
        return not (p.get("domaine") or p.get("origine") or p.get("type") or p.get("zipf_min") is not None
                    or p.get("zipf_max") is not None) and (p.get("etat") or "tous") == "tous"

    def _suite_standard(self) -> Invite | None:
        """Une suite de 2 ou 3 lettres tirée parmi celles qui ont moins de `sub` mots (au moins `solutions_min`,
        dont deux en usage), les plus fréquentes plus souvent. None si aucune, ou si le vivier est filtré."""
        L = tuple(range(max(2, self.params["long_min"]), min(3, self.params["long_max"]) + 1))
        if not L or self.lex is None or not self._sans_filtre():
            return None
        cle = (self.params["sub"], self.params["solutions_min"], L, self._exclus())
        if self._suites_cle != cle:
            if self.suites is None or self.suites.exclus != self._exclus():
                from lem.suites import IndexSuites

                self.suites = IndexSuites(self.lex, self._exclus())
            liste = self.suites.rares(self.params["sub"], L, minimum=self.params["solutions_min"], attestes=2)
            cumul, t = [], 0.0
            for _, n in liste:
                t += n                           # tirage proportionnel au nombre de mots : surtout 100–499, parfois rare
                cumul.append(t)
            self._suites_liste, self._suites_cumul, self._suites_cle = liste, cumul, cle
        if not self._suites_liste:
            return None
        s, n = self.rng.choices(self._suites_liste, cum_weights=self._suites_cumul, k=1)[0]
        _, k = self.solutions(s)
        return Invite(s, k=k, n_solutions=n, duree=self._duree())      # n : formes valides, comme « sub-N »

    def _invite_libre(self) -> Invite:
        inv = self._suite_standard()
        if inv is not None:
            return inv
        textes = [t for t in self._textes_vivier if t]
        for _ in range(60):
            if not textes:
                break
            f = self.rng.choice(self.rng.choice(textes).split(SEP))
            L = self._longueur()
            if len(f) < L:
                continue
            i = self.rng.randint(0, len(f) - L)
            s = f[i : i + L]
            if not LETTRES.fullmatch(s):
                continue
            n, k = self.solutions(s)
            if self.params["solutions_min"] <= n < self.params["sub"]:
                return Invite(s, k=k, n_solutions=n, duree=self._duree())
        return Invite("e", k=0, n_solutions=0, duree=self._duree())

    def _choisir_cible(self) -> sqlite3.Row | None:
        etudies = [i for i in self._etudies if i not in self.cibles_vues]
        if etudies and self.rng.random() < self.params.get("revision", 0.5):
            return self.vivier[self.rng.choice(etudies[:5])]
        cands = [r for r in self._cibles if r["id"] not in self.cibles_vues]
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

    def _suite_distinctive(self, forme: str, communs: list[str] | None = None, longueur: int | None = None) -> str:
        f = normaliser(forme)
        L = min(longueur or self._longueur(), len(f) - 1)
        subs = {f[i : i + L] for i in range(len(f) - L + 1) if LETTRES.fullmatch(f[i : i + L])}
        if communs:
            partages = [s for s in subs if sum(s in normaliser(c) for c in communs) >= 1]
            if partages:
                subs = set(partages)
        if not subs:                            # jamais de trait d'union ni d'espace dans une suite
            segment = max((x for x in re.split(r"[^a-z]+", f) if x), key=len, default=f)
            return segment[:L]
        return min(subs, key=lambda s: (self.n_solutions(s) if communs is None else -sum(
            s in normaliser(c) for c in communs), self.rng.random()))

    def _invite_synonyme(self) -> Invite:
        cands = [r for r in self._cibles_syn if r["id"] not in self.cibles_vues]
        if not cands and self._cibles_syn:
            self.cibles_vues.clear()
            cands = list(self._cibles_syn)
        if not cands:
            return self._invite_libre()
        ech = self.rng.sample(cands, min(len(cands), 200))
        cible = self.rng.choice(ech)
        cid = cible["id"]
        self.cibles_vues.add(cid)
        syns = self._graphe_syn.get(cid, set())
        row = self.con.execute("SELECT pos, zipf FROM entree WHERE id = ?", (cid,)).fetchone()
        pos = (row["pos"] or "") if row else ""
        z = (row["zipf"] or 0.0) if row else 0.0
        inv = Invite(cible["forme"], cid, cible["forme"], 0, float(len(syns)), len(syns), self._duree(),
                     est_synonyme=True, cible_pos=pos, cible_zipf=z)
        ecrire(self.con, self.session_id, Evenement(
            "bombe", "stimulus", entree_id=cid, data={"mot": cible["forme"], "n": len(syns), "mode": "synonymes"}))
        return inv

    def nouvelle_invite(self) -> Invite:
        mode = self.params["mode"]
        self.n_invites += 1
        if mode == "standard":
            return self._invite_libre()
        if mode == "synonymes":
            return self._invite_synonyme()
        retour = next((r for r in self.retours if r[0] <= self.n_invites and r[1] in self.vivier), None)
        if retour is not None:
            self.retours.remove(retour)
            _, cid, longueur, reste = retour
            cible = self.vivier[cid]
            # Le retour doit désigner la cible ou presque : on allonge jusqu'à être à portée.
            plafond = self.params.get("revelation_max", 30)
            long_max = max(self.params["long_min"], self.params["long_max"])
            longueur = min(longueur, long_max)
            s = self._suite_distinctive(cible["forme"], longueur=longueur)
            for L in range(longueur + 1, min(long_max, len(normaliser(cible["forme"])) - 1) + 1):
                if self.n_solutions(s) <= plafond:
                    break
                s = self._suite_distinctive(cible["forme"], longueur=L)
            n, k = self.solutions(s)
            inv = Invite(s, cid, cible["forme"], reste, k, n, self._duree())
            ecrire(self.con, self.session_id, Evenement(
                "bombe", "stimulus", entree_id=cid, data={"suite": s, "k": round(k, 2), "n": n, "retour": True,
                                                          "repetitions": reste, "mode": mode}))
            return inv
        cible = self._choisir_cible()
        if cible is None:
            return self._invite_libre()
        self.cibles_vues.add(cible["id"])
        communs = None
        if mode == "confusables" and self.index is not None:
            from lem.confusables import grappe
            communs = [c.forme for c in grappe(self.con, cible["id"], self.magasin, self.index, k=6)]
        s = self._suite_distinctive(cible["forme"], communs)
        n, k = self.solutions(s)
        reps = self.params.get("retours", 1)
        inv = Invite(s, cible["id"], cible["forme"], reps, k, n, self._duree())
        ecrire(self.con, self.session_id, Evenement(
            "bombe", "stimulus", entree_id=cible["id"], data={"suite": s, "k": round(k, 2), "n": n,
                                                              "repetitions": reps, "mode": mode}))
        return inv

    # -- tour de jeu ---------------------------------------------------------
    def _valider(self, texte: str) -> tuple[str | None, set[int]]:
        """(forme reconnue, entrées). Sans certains accents, la forme accentuée compte si le réglage le permet."""
        cle, exclus = texte.strip().lower(), self._exclus()
        ids = self.lex.entrees(cle, exclus)
        if not ids and self.accents_tolerants:
            for f in self.lex.formes_de_norme(normaliser(cle)):
                if accents_manquants(cle, f) and (ids_f := self.lex.entrees(f, exclus)):
                    return f, set(ids_f)
        if not ids:
            soudee = self._soudure(cle, exclus)
            if soudee:
                return soudee
        return cle, set(ids)

    def _soudure(self, cle: str, exclus: tuple[str, ...]) -> tuple[str, set[int]] | None:
        """« néobabylonien » : forme soudée (1990) d'un composé « néo-babylonien » du lexique."""
        n = normaliser(cle)
        for p in PREFIXES_SOUDES:
            if not n.startswith(p) or len(n) < len(p) + 3:
                continue
            for f in self.lex.formes_de_norme(p + "-" + n[len(p):]):
                soudee = f.replace("-", "", 1)
                ok = soudee == cle or (self.accents_tolerants and accents_manquants(cle, soudee))
                if ok and (ids := self.lex.entrees(f, exclus)):
                    return soudee, set(ids)
        return None

    def _diagnostic(self, texte: str) -> Diagnostic | None:
        """Faute de graphie ? Candidats : formes valides contenant la suite, de longueur voisine (±2) — une
        faute plausible garde à peu près la longueur du mot visé, et ce filtre évite de reconstruire des
        centaines de milliers de formes pour une suite courante."""
        from rapidfuzz import process
        from rapidfuzz.distance import OSA

        suite, norme = self.invite.suite, normaliser(texte)
        n = len(norme)
        cle = (suite, n)
        if cle not in self._candidats:
            self._candidats[cle] = self.lex.normes_contenant(suite, range(max(1, n - 2), n + 3))
        # Les 30 normes les plus proches (en C), puis seulement leurs formes : jamais 170 k chaînes en Python.
        proches = process.extract(norme, self._candidats[cle], scorer=OSA.distance, limit=30, score_cutoff=3)
        exclus = self._exclus()
        cands = [f for x, _, _ in proches for f in self.lex.formes_de_norme(x) if self.lex.entrees(f, exclus)]
        return diagnostiquer(self.con, texte, self.invite.cible_id, list(dict.fromkeys(cands)))

    def _repondre_synonyme(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Retour:
        from lem.games.synonymes import MARGE_UPGRADE_ZIPF, SEUIL_SEM_DEFAUT

        inv = self.invite
        cle, ids = self._valider(texte)
        base = {"mode": "synonymes", "cible": inv.cible_forme, "n": inv.n_solutions, "fsrs": False}
        if cle and cle != texte.strip().lower():
            base["accents_manquants"] = cle
        aide = bool(cle) and (cle in self.aides or bool(ids & self.aides_ids))
        if aide:
            base["aide"] = True
        if not cle or not ids:
            if not (frappe and frappe.dictee):
                syns = self._graphe_syn.get(inv.cible_id, set())
                cands = [r[0] for r in self.con.execute(
                    f"SELECT forme FROM entree WHERE id IN ({', '.join('?' * len(syns))})", list(syns)
                )] if syns else []
                diag = diagnostiquer(self.con, texte, None, cands or None)
                if diag:
                    enregistrer_faute(self.con, self.session_id, "bombe", diag, latence_ms, frappe)
                    return Retour(False, "graphie", entree_id=diag.entree_id, graphie=diag)
            ecrire(self.con, self.session_id, Evenement("bombe", "tentative", entree_id=inv.cible_id, reponse=texte,
                                                        correct=False, latence_ms=latence_ms,
                                                        frappe=frappe or Frappe(), data={**base, "raison": "mot inconnu"}))
            return Retour(False, "mot inconnu")
        if cle in self.utilises:
            return Retour(False, "déjà utilisé")
        if inv.cible_id in ids or normaliser(cle) == normaliser(inv.cible_forme or ""):
            return Retour(False, "c'est le mot demandé")

        syns = self._graphe_syn.get(inv.cible_id, set())
        direct = next((i for i in ids if i in syns), None)
        mode_ok, eid, sim = "", next(iter(ids)), None
        if direct is not None:
            eid, mode_ok = direct, "synonyme"
        elif self.magasin is not None:
            meilleur = max(((i, self.magasin.sim(inv.cible_id, i)) for i in ids),
                           key=lambda x: x[1] if x[1] is not None else -1.0)
            if meilleur[1] is not None and meilleur[1] >= SEUIL_SEM_DEFAUT:
                eid, sim, mode_ok = meilleur[0], meilleur[1], "proche"

        if not mode_ok:
            comp = comparer(self.con, inv.cible_id, eid, self.magasin)
            if comp.proximite in (Proximite.JUMEAU, Proximite.FORME, Proximite.SENS):
                enregistrer_confusion(self.con, inv.cible_id, eid, 0.5)
            ecrire(self.con, self.session_id, Evenement(
                "bombe", "tentative", entree_id=inv.cible_id, reponse=cle, correct=False, latence_ms=latence_ms,
                frappe=frappe or Frappe(), data={**base, "donne_id": eid, "proximite": comp.proximite.value}))
            return Retour(False, comp.message or "pas un synonyme", entree_id=eid,
                          proximite=comp.proximite, comparaison=comp)

        self.utilises.add(cle)
        self.latences.append(latence_ms)
        self.historique.append(True)
        z = self.con.execute("SELECT zipf FROM entree WHERE id = ?", (eid,)).fetchone()["zipf"] or 0.0
        upgrade = bool(inv.cible_zipf > 0 and z > 0 and z < inv.cible_zipf - MARGE_UPGRADE_ZIPF)
        points = 1.0 + max(0.0, 4.0 - z) + (2.0 if upgrade else 0.0)
        vie = self._regain(cle, z)
        msg = "synonyme soutenu !" if upgrade else ("proche synonyme" if mode_ok == "proche" else "")
        ev = ecrire(self.con, self.session_id, Evenement(
            "bombe", "reponse", entree_id=eid, trace="prod", reponse=cle, correct=True, latence_ms=latence_ms,
            frappe=frappe or Frappe(),
            data={**base, "cible_id": inv.cible_id, "type_ok": mode_ok,
                  "sim": None if sim is None else round(sim, 3), "upgrade": upgrade}))
        if not aide:
            self.carte.observer(eid, 1.0, poids=POIDS_PRODUIT, source="bombe:synonyme", evenement_id=ev, propager=False)
            self.carte.observer(inv.cible_id, 0.9, poids=0.5, source="bombe:synonyme:cible", evenement_id=ev, propager=False)
        retour = Retour(True, msg, eid, points=points, vie_gagnee=vie, forme=cle, aide=aide)
        retour.trouvaille = trouvailles(self.con, eid)
        self._adapter()
        self.invite = self.nouvelle_invite()
        self.score += retour.points
        return retour

    def repondre(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Retour:
        texte = nettoyer_saisie(texte)
        inv = self.invite
        if inv.est_synonyme:
            return self._repondre_synonyme(texte, latence_ms, frappe)
        cle, ids = self._valider(texte)
        suite_ok = inv.suite in normaliser(texte)
        base = {"suite": inv.suite, "cible": inv.cible_forme, "k": round(inv.k, 2), "n": inv.n_solutions,
                "repetitions_restantes": inv.repetitions, "fsrs": False}
        if cle and cle != texte.strip().lower():
            base["accents_manquants"] = cle
        aide = bool(cle) and (cle in self.aides or bool(ids & self.aides_ids))
        if aide:
            base["aide"] = True                  # donné par /d : un mot vu, pas un mot trouvé
        if not cle or not suite_ok or not ids or cle in self.utilises:
            raison = ("ne contient pas « %s »" % inv.suite.upper() if not suite_ok
                      else "déjà utilisé" if cle in self.utilises else "mot inconnu")
            ecrire(self.con, self.session_id, Evenement("bombe", "tentative", reponse=texte, correct=False,
                                                        latence_ms=latence_ms, frappe=frappe or Frappe(),
                                                        data={**base, "raison": raison}))
            return Retour(False, raison)

        self.utilises.add(cle)
        self.latences.append(latence_ms)
        eid = self._entree_preferee(ids, inv.cible_id, cle)
        z = self.con.execute("SELECT zipf FROM entree WHERE id = ?", (eid,)).fetchone()["zipf"] or 0.0
        points = 1.0 + max(0.0, 4.0 - z)
        vie = self._regain(cle, z)
        trace_existe = self.con.execute("SELECT 1 FROM trace_etat WHERE entree_id = ? AND trace = 'prod'",
                                        (eid,)).fetchone() is not None
        retour = Retour(True, "", eid, points=points, vie_gagnee=vie, forme=cle, aide=aide)

        if inv.cible_id is not None and eid == inv.cible_id:
            retour.cible_trouvee = True
            retour.message = "Cible trouvée !"
            retour.points += 5
            self.historique.append(True)
            ev = ecrire(self.con, self.session_id, Evenement("bombe", "reponse", entree_id=eid, trace="prod",
                                                             reponse=texte, correct=True, note_auto=3,
                                                             latence_ms=latence_ms, frappe=frappe or Frappe(),
                                                             data={**base, "fsrs": trace_existe, "cible_trouvee": True}))
            self.carte.observer(eid, 1.0, source="bombe:cible", evenement_id=ev)
            self._adapter()
            self.invite = self.nouvelle_invite()
        else:
            # Production spontanée : preuve de vocabulaire actif (sans planification FSRS).
            data = {**base, "spontane": True}
            if inv.cible_id is not None:
                comp = comparer(self.con, inv.cible_id, eid, self.magasin)
                retour.comparaison, retour.proximite = comp, comp.proximite
                retour.message = comp.message
                data.update({"sim_forme": round(comp.forme, 3), "proximite": comp.proximite.value,
                             "sim_sens": None if comp.sens is None else round(comp.sens, 3)})
                if comp.proximite in (Proximite.JUMEAU, Proximite.FORME, Proximite.SENS):
                    enregistrer_confusion(self.con, inv.cible_id, eid, 0.5)
            ev = ecrire(self.con, self.session_id, Evenement("bombe", "reponse", entree_id=eid, trace="prod",
                                                             reponse=texte, correct=True, latence_ms=latence_ms,
                                                             frappe=frappe or Frappe(), data=data))
            # Sans propagation : on ne tape que des mots qu'on connaît (biais de sélection, cf. memory/migration.py).
            if not aide:                         # un mot donné par /d n'est pas une preuve de vocabulaire actif
                poids = 1.0 if (self.carte._prior.get(eid, 0.5) < SEUIL_CONNU_PRIOR and self.carte.n_obs(eid) == 0) else POIDS_PRODUIT
                self.carte.observer(eid, 1.0, poids=poids, source="bombe:spontane", evenement_id=ev, propager=False)
            retour.revelation = self._apres_echec_cible(texte)
        retour.trouvaille = trouvailles(self.con, eid)
        prior = self.carte._prior.get(eid, 0.5)
        if trace_existe or (aide and prior < 0.65) or (not aide and retour.trouvaille["n"] <= 1 and prior < SEUIL_CONNU_PRIOR):
            scheduler.reconstruire(self.con, [eid])
        self.score += retour.points
        return retour

    def indice(self) -> dict | None:
        """/d : un mot qui contient la suite et que tu peux sortir. D'abord un mot utile à découvrir ou consolider,
        sinon un mot que tu connais. Un mot encore fragile (P(connu) < 0,6 ou sans preuve hors mots évidents)
        est noté « vu » au journal (événement `indice`) : les Cartes le reprendront en priorité.
        Un mot donné ne l'est qu'une fois. None s'il n'y a rien à proposer."""
        suite, exclus = self.invite.suite, self._exclus()
        if self.invite.est_synonyme and self.invite.cible_id is not None:
            syns = [i for i in self._graphe_syn.get(self.invite.cible_id, set()) if i not in self.aides_ids]
            if not syns:
                return None
            rows = self.con.execute(
                f"SELECT id, forme, COALESCE(zipf, 0) AS zipf FROM entree WHERE id IN ({', '.join('?' * len(syns))})",
                syns,
            ).fetchall()
            cands = [r for r in rows if r["forme"].lower() not in self.utilises and r["forme"].lower() not in self.aides]
            if not cands:
                return None
            choisi = max(cands, key=lambda r: (r["zipf"], -len(r["forme"])))
            eid, mot = choisi["id"], choisi["forme"]
            pm = self.carte.p(eid)
            self.noter_aides([eid], [mot])
            s = self.con.execute(
                "SELECT id, COALESCE(definition_precise, definition) AS def FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
                (eid,),
            ).fetchone()
            defi = " ".join((s["def"] if s else "").split())
            sens_id = s["id"] if s else 0
            etym = resume_etymologie(self.con, eid)
            ecrire(self.con, self.session_id, Evenement("bombe", "indice", entree_id=eid, sens_id=sens_id or None, data={
                "mode": "synonymes", "cible": self.invite.cible_forme, "mot": mot, "p": round(pm, 3),
                "connu": pm >= SEUIL_CONNU, "vu": pm < SEUIL_CONNU}))
            return {"mot": mot, "entree_id": eid, "sens_id": sens_id, "p": pm, "connu": pm >= SEUIL_CONNU,
                    "definition": defi, "etymologie": etym}
        normes = self.lex.normes_contenant(suite, range(max(2, len(suite)), 24))
        if len(normes) > 3000:
            normes = self.rng.sample(normes, 3000)
        from wordfreq import zipf_frequency

        cands: list[tuple[str, tuple[int, ...], float]] = []
        for x in normes:
            for f in self.lex.formes_de_norme(x):
                if f not in self.utilises and f not in self.aides and f.isalpha() and len(f) >= 2:
                    ids = tuple(i for i in self.lex.entrees(f, exclus) if i not in self.aides_ids)
                    if ids:
                        ids_v = tuple(i for i in ids if i in self.vivier)
                        cands.append((f, ids_v or ids, zipf_frequency(f, "fr")))
        if not cands:
            return None
        viv = [c for c in cands if any(i in self.vivier for i in c[1])]
        cands = viv or cands
        usuels = [c for c in cands if c[2] > 0]          # des formes en usage, pas « becquetassions »
        cands = usuels or cands
        tous = list({i for _, ids, _ in cands for i in ids})
        p = dict(zip(tous, self.carte.p_de(tous)))
        priors = {i: (pr, n_obs) for i, pr, n_obs in self._priors(tous)}
        # « Connu » demande plus qu'une probabilité qui monte avec tes réponses sur d'autres mots : soit l'a priori
        # de fréquence suffit (mot évident, ≥ 0,6), soit tu as une preuve directe sur ce mot-là.
        solide = {i: (pr >= SEUIL_CONNU or n_obs > 0) for i, (pr, n_obs) in priors.items()}
        p = {i: (v if solide.get(i, False) else min(v, priors.get(i, (0.0, 0))[0], SEUIL_CONNU - 0.01))
             for i, v in p.items()}
        scores = []
        for f, ids, z in cands:
            eid = max(ids, key=lambda i: (i in self.vivier and self.vivier[i]["forme"].lower() == f, p[i]))
            est_lemme = eid in self.vivier and self.vivier[eid]["forme"].lower() == f
            scores.append((f, eid, max(p[i] for i in ids), z, est_lemme))
        cibles = [s for s in scores if s[2] < SEUIL_CONNU and priors.get(s[1], (0.0, 0))[0] >= 0.10 and s[3] >= 1.5]
        connus = [s for s in scores if s[2] >= SEUIL_CONNU]
        if cibles:
            mot, eid, pm, _, _ = max(cibles, key=lambda s: (s[4], s[2], s[3]))
        elif connus:
            mot, eid, pm, _, _ = max(connus, key=lambda s: (s[4], min(len(s[0]), 12), s[3]))
        else:
            mot, eid, pm, _, _ = max(scores, key=lambda s: (s[4], s[2], s[3]))
        self.noter_aides([eid], [mot])
        s = self.con.execute(
            "SELECT id, COALESCE(definition_precise, definition) AS def FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
            (eid,),
        ).fetchone()
        defi = " ".join((s["def"] if s else "").split())
        sens_id = s["id"] if s else 0
        etym = resume_etymologie(self.con, eid)
        ecrire(self.con, self.session_id, Evenement("bombe", "indice", entree_id=eid, sens_id=sens_id or None, data={
            "suite": suite, "mot": mot, "p": round(pm, 3), "connu": pm >= SEUIL_CONNU, "vu": pm < SEUIL_CONNU}))
        return {"mot": mot, "entree_id": eid, "sens_id": sens_id, "p": pm, "connu": pm >= SEUIL_CONNU,
                "definition": defi, "etymologie": etym}

    def noter_retour_aide(self, eid: int, retenu: bool, avancer: bool = True, explicite: bool = False) -> tuple[str, str] | None:
        """Après /d : enregistre si tu avais le mot sur le bout de la langue (retenu=True, touche r)
        ou non (retenu=False, touche n ou expiration des 3 s), met à jour la carte et FSRS, et passe à la suite.
        Si `explicite` est vrai (touche n appuyée), même un mot présumé connu est dégradé et mis à l'étude."""
        s = self.con.execute(
            "SELECT id FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1", (eid,)
        ).fetchone()
        sens_id = s["id"] if s else None
        prior = self.carte._prior.get(eid, 0.5)
        if not retenu and (prior < 0.75 or explicite):
            ev = ecrire(self.con, self.session_id, Evenement(
                "bombe", "reponse", entree_id=eid, sens_id=sens_id, trace="recep", reponse="",
                correct=False, note_auto=1, data={"role": "sonde", "aide": "non_retenu", "fsrs": True}))
            self.carte.observer(eid, Y_PAR_NOTE[1], poids=1.5, source="bombe:aide:non",
                                evenement_id=ev, depuis_note=True, propager=True)
            scheduler.reconstruire(self.con, [eid])
        elif retenu and (prior < 0.65 or explicite):
            ev = ecrire(self.con, self.session_id, Evenement(
                "bombe", "reponse", entree_id=eid, sens_id=sens_id, trace="recep", reponse="",
                correct=True, note_auto=2, data={"role": "sonde", "aide": "retenu", "fsrs": True}))
            self.carte.observer(eid, Y_PAR_NOTE[2], poids=1.0, source="bombe:aide:retenu",
                                evenement_id=ev, depuis_note=True, propager=False)
            scheduler.reconstruire(self.con, [eid])
        elif prior < 0.65:
            scheduler.reconstruire(self.con, [eid])
        if avancer:
            return self._apres_echec_cible(None)
        return None

    def _priors(self, ids: list[int]):
        for a in range(0, len(ids), 900):
            lot = ids[a:a + 900]
            yield from self.con.execute(
                f"SELECT entree_id, p_prior, n_obs FROM connaissance WHERE entree_id IN ({', '.join('?' * len(lot))})", lot)

    def exploser(self) -> Retour:
        """Le temps est écoulé : une vie en moins."""
        self.vies -= 1
        inv = self.invite
        ecrire(self.con, self.session_id, Evenement("bombe", "explosion", entree_id=inv.cible_id,
                                                    data={"suite": inv.suite, "k": round(inv.k, 2), "n": inv.n_solutions}))
        r = Retour(False, "BOUM")
        if inv.est_synonyme and inv.cible_id is not None:
            syns = list(self._graphe_syn.get(inv.cible_id, set()))
            ex = [x[0] for x in self.con.execute(
                f"SELECT forme FROM entree WHERE id IN ({', '.join('?' * len(syns))}) ORDER BY COALESCE(zipf, 0) DESC LIMIT 4",
                syns,
            )] if syns else []
            d = self.con.execute("SELECT definition FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1",
                                 (inv.cible_id,)).fetchone()
            resume = ", ".join(dict.fromkeys(ex))
            if d and d["definition"]:
                resume = f"{resume} ({d['definition'][:75]})" if resume else d["definition"]
            r.revelation = (inv.cible_forme, resume)
            self.carte.observer(inv.cible_id, 0.35, poids=0.4, source="bombe:synonyme:boum", propager=False)
            self.historique.append(False)
            self._adapter()
            self.invite = self.nouvelle_invite()
            return r
        r.revelation = self._apres_echec_cible(None)
        return r

    @property
    def finie(self) -> bool:
        return self.vies <= 0

    # -- internes ------------------------------------------------------------
    def _entree_preferee(self, ids: set[int], cible: int | None, cle: str = "") -> int:
        if cible in ids:
            return cible
        return min(ids, key=lambda i: (
            i not in self.aides_ids,
            not (i in self.vivier and self.vivier[i]["forme"].lower() == cle),
            -(self.vivier[i]["p_connu"] if i in self.vivier else 0),
        ))

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
        """La cible n'est pas sortie : retour programmé plus tard, révélation seulement si elle était à portée."""
        inv = self.invite
        if inv.cible_id is None:
            self.invite = self.nouvelle_invite()
            return None
        if donne:
            inv.donnes.append(donne)
        if inv.repetitions > 0:
            self.retours.append((self.n_invites + self.params.get("ecart", 8), inv.cible_id,
                                 len(inv.suite) + 1, inv.repetitions - 1))
        rev = None
        if inv.n_solutions <= self.params.get("revelation_max", 30):
            self.historique.append(False)
            d = self.con.execute("SELECT definition FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1",
                                 (inv.cible_id,)).fetchone()
            ev = ecrire(self.con, self.session_id, Evenement("bombe", "revelation", entree_id=inv.cible_id,
                                                             data={"suite": inv.suite, "donnes": inv.donnes}))
            self.carte.observer(inv.cible_id, 0.4, poids=0.3, source="bombe:revelation", evenement_id=ev)
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
