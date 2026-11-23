"""LaBOG : grille 4×4 (façon Boggle / solitaire-play.com/labog), calibrée sur TES mots connus.

Règles :
  * 16 lettres en 4×4, déplacement 8-voisins (horizontal, vertical, diagonal), une case utilisée au plus une fois ;
  * mots de `long_min` à `long_max` lettres (4 à 9 par défaut), sans noms propres ni sigles ;
  * deux compteurs en direct, activables à volonté : nombre de mots par initiale et par longueur.

Génération de grille (escalade locale rapide) :
  * au lieu d'un tirage aléatoire qui produit des grilles remplies de mots obscurs, on part d'un mot-ancre connu
    (7–9 lettres) posé sur un chemin libre + lettres fréquentes du français (dés de Boggle) ;
  * puis on mute case par case (~300 essais en < 0,35 s grâce à un trie en mémoire) pour maximiser le nombre de
    mots CONNUS (lemmes avec `P(connu) ≥ seuil_connu` et formes usuelles attestées) tout en limitant les mots
    rares/inconnus attestés à `exceptions` (5 par défaut).
  * la liste cible de la partie ne contient QUE tes mots connus : aucun mot inconnu dans les compteurs ni dans le
    bilan. Si tu trouves malgré tout un mot rare du dictionnaire présent sur la grille, il compte en bonus.
"""

from __future__ import annotations

import math
import random
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass

from lem.games.base import Jeu, Param, enregistrer
from lem.journal import Evenement, Frappe, ecrire, nettoyer_saisie
from lem.memory.knowledge import POIDS_PRODUIT, CarteConnaissance
from lem.orthographe import Diagnostic
from lem.texte import normaliser

TYPES_HORS_JEU = ("nom_propre", "sigle")

# Dés classiques du Boggle français (16 dés × 6 faces).
DES_FRANCAIS = (
    "ETUKNO", "EVGTIN", "DECAMP", "IELRUW",
    "EHIFSE", "RECALS", "ENTDOS", "OFXRIA",
    "NAVEDZ", "EIOATA", "GLENYU", "BMAQJO",
    "TLIBRA", "SPULTE", "AIMSOR", "ENHRIS",
)
# Alphabet pondéré pour les mutations (lettres productives en français).
LETTRES_MUTATION = "EEEEEEEEEEEEAAAAAAAIIIIIIINNNNNNOOOOOORRRRRRSSSSSSTTTTTTUUUUULLLLDDCCMMPPBVG"

# Points LaBOG / Boggle selon la longueur.
POINTS_LONGUEUR = {3: 1, 4: 1, 5: 2, 6: 3, 7: 5, 8: 8, 9: 11}

# Voisins 8-connexes pour chaque case 0..15 d'une grille 4×4.
VOISINS_4X4: tuple[tuple[int, ...], ...] = tuple(
    tuple(
        (r + dr) * 4 + (c + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and 0 <= r + dr < 4 and 0 <= c + dc < 4
    )
    for r in range(4)
    for c in range(4)
)

_FIN = ""   # sentinelle dans le trie dict : noeud[_FIN] = mot complet

LABOG = enregistrer(Jeu(
    id="labog",
    nom="LaBOG",
    accroche="Grille 4×4 de 16 lettres — uniquement tes mots connus.",
    description="Façon LaBOG / Boggle : grilles denses optimisées sur ta carte de connaissance "
                "(avec compteurs par initiale et par longueur).",
    params=[
        Param("mode", "Mode", str, "libre", "libre = sans chrono · chrono = avec minuterie",
              choix=("libre", "chrono")),
        Param("par_initiale", "Compter par initiale", bool, True,
              "affiche toujours le nombre de mots trouvés / total par lettre initiale",
              alias=("initiales", "init")),
        Param("par_longueur", "Compter par longueur", bool, True,
              "affiche toujours le nombre de mots trouvés / total par nombre de lettres",
              alias=("longueurs", "long")),
        Param("long_min", "Lettres min", int, 4, "longueur minimale des mots (LaBOG : 4)", 3, 9),
        Param("long_max", "Lettres max", int, 9, "longueur maximale des mots (LaBOG : 9)", 4, 16),
        Param("seuil_connu", "Seuil mot connu P(connu)", float, 0.72,
              "probabilité minimale dans ta carte pour qu'un mot soit attendu", 0.1, 0.99),
        Param("zipf_min", "Fréquence min (Zipf)", float, 3.0,
              "fréquence Zipf minimale pour les mots connus", 0.0, 7.0),
        Param("exceptions", "Exceptions max", int, 5,
              "mots rares/inconnus tolérés sur la grille pendant la génération", 0, 100),
        Param("cible_mots", "Mots visés (min)", int, 50,
              "densité minimale visée par le générateur", 10, 300),
        Param("duree", "Durée mode chrono (s)", int, 180,
              "durée en secondes en mode chrono", 15, 1800),
    ],
))


def points_mot(longueur: int) -> int:
    if longueur <= 4:
        return 1
    return POINTS_LONGUEUR.get(longueur, 11 + (longueur - 9) * 3)


def construire_trie(mots) -> dict:
    """Trie en dictionnaires imbriqués : ~0,1 ms par résolution de grille 4×4, zéro avertissement."""
    racine: dict = {}
    for m in mots:
        n = racine
        for ch in m:
            n = n.setdefault(ch, {})
        n[_FIN] = m
    return racine


def resoudre_grille(grille: tuple[str, ...], trie: dict,
                    long_min: int = 4, long_max: int = 9) -> set[str]:
    """Tous les mots du `trie` (longueur `long_min..long_max`) traçables sur une grille 4×4."""
    if not trie:
        return set()
    trouves: set[str] = set()
    voisins = VOISINS_4X4

    def dfs(pos: int, masque: int, noeud: dict, prof: int) -> None:
        if prof >= long_min and _FIN in noeud:
            trouves.add(noeud[_FIN])
        if prof >= long_max:
            return
        for v in voisins[pos]:
            bit = 1 << v
            if masque & bit:
                continue
            suiv = noeud.get(grille[v])
            if suiv is not None:
                dfs(v, masque | bit, suiv, prof + 1)

    for i in range(16):
        suiv = trie.get(grille[i])
        if suiv is not None:
            dfs(i, 1 << i, suiv, 1)
    return trouves


def chemin_mot(grille: tuple[str, ...], mot: str) -> list[int] | None:
    """Chemin de cases 0..15 qui épelle `mot` (normalisé) sur `grille`, ou None s'il n'existe pas."""
    mot = normaliser(mot)
    if not mot:
        return None
    n = len(mot)
    voisins = VOISINS_4X4

    def dfs(pos: int, idx: int, masque: int, chemin: list[int]) -> list[int] | None:
        if idx == n:
            return chemin
        cible = mot[idx]
        for v in voisins[pos]:
            bit = 1 << v
            if not (masque & bit) and grille[v] == cible:
                res = dfs(v, idx + 1, masque | bit, [*chemin, v])
                if res is not None:
                    return res
        return None

    premier = mot[0]
    for i in range(16):
        if grille[i] == premier:
            res = dfs(i, 1, 1 << i, [i])
            if res is not None:
                return res
    return None


def tourner_grille(grille: tuple[str, ...]) -> tuple[str, ...]:
    """Rotation 90° horaire d'une grille 4×4."""
    return tuple(grille[(3 - c) * 4 + r] for r in range(4) for c in range(4))


_WORDFREQ_FR: dict[str, float] | None = None


def _freqs_fr() -> dict[str, float]:
    """Dictionnaire {forme: fréquence} de wordfreq chargé une fois (~50 ms)."""
    global _WORDFREQ_FR
    if _WORDFREQ_FR is None:
        from wordfreq import get_frequency_dict

        _WORDFREQ_FR = get_frequency_dict("fr")
    return _WORDFREQ_FR


class IndexLabog:
    """Dictionnaires compacts (connus / rares attestés / tous) pour la résolution et la génération."""

    def __init__(self, con: sqlite3.Connection, carte: CarteConnaissance, lexique,
                 long_min: int = 4, long_max: int = 9, seuil_connu: float = 0.72, zipf_min: float = 3.0):
        self.con = con
        self.lex = lexique
        self.long_min, self.long_max = long_min, long_max
        self.seuil_connu, self.zipf_min = seuil_connu, zipf_min

        p_tous = carte.p_tous()
        pos_carte = {int(i): k for k, i in enumerate(carte.ids)}
        connus_eids: set[int] = set()
        tous_eids: set[int] = set()
        eid_forme: dict[int, str] = {}
        eid_zipf: dict[int, float] = {}

        for eid, forme, z, typ in con.execute(
            """SELECT id, forme, COALESCE(zipf, 0.0), type FROM entree
               WHERE langue = 'fr' AND type NOT IN ('nom_propre', 'sigle', 'expression')
                 AND instr(forme, ' ') = 0 AND instr(forme, '-') = 0
                 AND forme NOT GLOB '[A-Z]*'"""
        ):
            tous_eids.add(eid)
            eid_forme[eid] = forme
            eid_zipf[eid] = float(z or 0.0)
            k = pos_carte.get(eid)
            p = float(p_tous[k]) if k is not None else 0.5
            if p >= seuil_connu or z >= zipf_min:
                connus_eids.add(eid)

        petite_base = len(tous_eids) < 1000
        if len(connus_eids) < 25:
            connus_eids = set(tous_eids)

        freqs = _freqs_fr()
        # Seuil de fréquence sur la FORME elle-même : écarte les conjugaisons rarissimes (irritas, irritât…)
        # tout en gardant pluriels et formes usuelles (portes, portait, porter…).
        seuil_f_connu = 10.0 ** (max(2.6, zipf_min - 0.2) - 9.0)
        seuil_f_rare = 10.0 ** (1.6 - 9.0)

        self.norme_vers_connu: dict[str, tuple[int, str]] = {}
        self.norme_vers_tous: dict[str, tuple[int, str]] = {}
        rares_attestes: set[str] = set()
        ancres: list[str] = []

        for forme, norme, eid in con.execute(
            "SELECT forme, norme, entree_id FROM forme WHERE length(norme) BETWEEN ? AND ?",
            (long_min, long_max),
        ):
            if eid not in tous_eids or not norme.isascii() or not norme.isalpha():
                continue
            prev_t = self.norme_vers_tous.get(norme)
            if prev_t is None or eid_zipf[eid] > eid_zipf.get(prev_t[0], -1.0):
                self.norme_vers_tous[norme] = (eid, forme)
            f_wf = freqs.get(forme, 0.0)
            est_usuelle = petite_base or f_wf >= seuil_f_connu
            if eid in connus_eids and est_usuelle:
                prev_c = self.norme_vers_connu.get(norme)
                if prev_c is None or eid_zipf[eid] > eid_zipf.get(prev_c[0], -1.0):
                    self.norme_vers_connu[norme] = (eid, forme)
                if 7 <= len(norme) <= min(9, long_max) and norme == normaliser(eid_forme[eid]):
                    ancres.append(norme)
            elif f_wf >= seuil_f_rare or (petite_base and eid not in connus_eids):
                rares_attestes.add(norme)

        rares_attestes -= self.norme_vers_connu.keys()
        self.ancres = list(dict.fromkeys(ancres))
        self.trie_connus = construire_trie(self.norme_vers_connu.keys())
        self.trie_rares = construire_trie(rares_attestes)
        self.trie_tous = construire_trie(self.norme_vers_tous.keys())


def _chemin_aleatoire(longueur: int, rng: random.Random) -> list[int] | None:
    """Chemin simple aléatoire de `longueur` cases sur la grille 4×4 (pour planter un mot-ancre)."""
    for _ in range(20):
        pos = rng.randrange(16)
        chemin = [pos]
        vus = {pos}
        for _ in range(longueur - 1):
            libres = [v for v in VOISINS_4X4[pos] if v not in vus]
            if not libres:
                break
            pos = rng.choice(libres)
            chemin.append(pos)
            vus.add(pos)
        if len(chemin) == longueur:
            return chemin
    return None


def _grille_initiale(index: IndexLabog, rng: random.Random) -> list[str]:
    """Grille de départ : dés français + un mot-ancre connu de 7–9 lettres si disponible."""
    des = list(DES_FRANCAIS)
    rng.shuffle(des)
    grille = [rng.choice(d).lower() for d in des]
    if index.ancres:
        mot = rng.choice(index.ancres)
        chemin = _chemin_aleatoire(len(mot), rng)
        if chemin is not None:
            for pos, ch in zip(chemin, mot):
                grille[pos] = ch
    return grille


def generer_grille(index: IndexLabog, rng: random.Random, exceptions: int = 5,
                   cible_mots: int = 50, budget_s: float = 0.35,
                   essais_max: int = 350) -> tuple[tuple[str, ...], set[str], set[str]]:
    """Génère une grille 4×4 dense en mots connus, avec le moins possible de mots rares attestés (≤ `exceptions`).

    Renvoie (grille, mots_connus, mots_bonus_tous_hors_connus).
    """
    t0 = time.monotonic()

    def evaluer(g: tuple[str, ...]) -> tuple[float, set[str], int]:
        connus = resoudre_grille(g, index.trie_connus, index.long_min, index.long_max)
        rares = resoudre_grille(g, index.trie_rares, index.long_min, index.long_max)
        n_longs = sum(1 for m in connus if len(m) >= 7)
        n_init = len({m[0] for m in connus})
        exces = max(0, len(rares) - exceptions)
        score = len(connus) + 2.5 * n_longs + 0.8 * n_init - 2.0 * exces
        return score, connus, len(rares)

    meilleur_g = tuple(_grille_initiale(index, rng))
    meilleur_s, meilleur_c, meilleur_r = evaluer(meilleur_g)

    courant = list(meilleur_g)
    score_c = meilleur_s
    stagnation = 0

    for _ in range(essais_max):
        if time.monotonic() - t0 >= budget_s:
            break
        if stagnation >= 35:
            courant = _grille_initiale(index, rng)
            sc, co, nr = evaluer(tuple(courant))
            score_c = sc
            stagnation = 0
            if sc > meilleur_s:
                meilleur_s, meilleur_g, meilleur_c, meilleur_r = sc, tuple(courant), co, nr
            continue
        pos = rng.randrange(16)
        ancienne = courant[pos]
        nouvelle = rng.choice(LETTRES_MUTATION).lower()
        if nouvelle == ancienne:
            continue
        courant[pos] = nouvelle
        g_t = tuple(courant)
        sc, co, nr = evaluer(g_t)
        if sc >= score_c:
            stagnation = 0 if sc > score_c else stagnation + 1
            score_c = sc
            if sc > meilleur_s:
                meilleur_s, meilleur_g, meilleur_c, meilleur_r = sc, g_t, co, nr
                stagnation = 0
                if len(meilleur_c) >= max(cible_mots, 90) and meilleur_r <= exceptions:
                    break
        else:
            courant[pos] = ancienne
            stagnation += 1

    tous = resoudre_grille(meilleur_g, index.trie_tous, index.long_min, index.long_max)
    return meilleur_g, meilleur_c, tous - meilleur_c


@dataclass
class ReponseLabog:
    valide: bool
    message: str = ""
    forme: str | None = None
    norme: str | None = None
    entree_id: int | None = None
    points: int = 0
    bonus: bool = False               # mot rare du lexique, hors liste cible
    chemin: list[int] | None = None
    graphie: Diagnostic | None = None


@dataclass
class MotBilan:
    norme: str
    forme: str
    entree_id: int
    trouve: bool
    definition: str = ""


@dataclass
class BilanLabog:
    score: int
    score_max: int
    trouves: int
    total: int
    bonus: list[str]
    mots: list[MotBilan]
    exceptions_grille: int


class PartieLabog:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 session_id: int | None = None, rng: random.Random | None = None, lexique=None,
                 grille_forcee: tuple[str, ...] | None = None):
        self.con, self.params, self.session_id = con, params, session_id
        self.rng = rng or random.Random()
        if lexique is None:
            from lem.lexique import Lexique
            lexique = Lexique(con)
        self.lex = lexique
        self.carte = carte or CarteConnaissance(con, lexique)
        self.index = IndexLabog(
            con, self.carte, self.lex,
            long_min=params["long_min"],
            long_max=params["long_max"],
            seuil_connu=params["seuil_connu"],
            zipf_min=params["zipf_min"],
        )
        self.grille: tuple[str, ...] = ()
        self.cibles: set[str] = set()
        self.inconnus: set[str] = set()
        self.trouves: set[str] = set()
        self.trouves_ordre: list[tuple[str, int, bool]] = []      # (forme, points, bonus)
        self.bonus: set[str] = set()
        self.score = 0
        self.score_max = 0
        self.indices_vus: set[str] = set()
        self.nouvelle_grille(grille_forcee)

    def nouvelle_grille(self, grille_forcee: tuple[str, ...] | None = None) -> None:
        if grille_forcee is not None:
            self.grille = tuple(c.lower() for c in grille_forcee)
            self.cibles = resoudre_grille(self.grille, self.index.trie_connus,
                                          self.params["long_min"], self.params["long_max"])
            tous = resoudre_grille(self.grille, self.index.trie_tous,
                                   self.params["long_min"], self.params["long_max"])
            self.inconnus = tous - self.cibles
        else:
            self.grille, self.cibles, self.inconnus = generer_grille(
                self.index, self.rng,
                exceptions=self.params["exceptions"],
                cible_mots=self.params["cible_mots"],
            )
        self.trouves.clear()
        self.trouves_ordre.clear()
        self.bonus.clear()
        self.indices_vus.clear()
        self.score = 0
        self.score_max = sum(points_mot(len(m)) for m in self.cibles)
        ecrire(self.con, self.session_id, Evenement(
            "labog", "stimulus", data={"grille": "".join(self.grille), "n_cibles": len(self.cibles),
                                       "n_exceptions": len(self.inconnus), "fsrs": False}))

    def tourner(self) -> None:
        self.grille = tourner_grille(self.grille)

    def compteurs_initiales(self) -> list[tuple[str, int, int]]:
        """[(lettre_maj, trouves, total), ...] trié A→Z."""
        totaux = Counter(m[0].upper() for m in self.cibles)
        faits = Counter(m[0].upper() for m in self.trouves)
        return [(c, faits.get(c, 0), totaux[c]) for c in sorted(totaux)]

    def compteurs_longueurs(self) -> list[tuple[int, int, int]]:
        """[(longueur, trouves, total), ...] trié par longueur croissante."""
        totaux = Counter(len(m) for m in self.cibles)
        faits = Counter(len(m) for m in self.trouves)
        return [(L, faits.get(L, 0), totaux[L]) for L in sorted(totaux)]

    def proposer(self, texte: str, latence_ms: int, frappe: Frappe | None = None) -> ReponseLabog:
        texte = nettoyer_saisie(texte)
        norme = normaliser(texte)
        if not norme or not norme.isalpha():
            return ReponseLabog(False, "saisie invalide")
        if len(norme) < self.params["long_min"]:
            return ReponseLabog(False, f"trop court ({self.params['long_min']} lettres min)")
        if len(norme) > self.params["long_max"]:
            return ReponseLabog(False, f"trop long ({self.params['long_max']} lettres max)")
        if norme in self.trouves or norme in self.bonus:
            return ReponseLabog(False, "déjà trouvé")

        chemin = chemin_mot(self.grille, norme)
        if chemin is None:
            return ReponseLabog(False, "introuvable sur la grille")

        if norme in self.cibles:
            eid, forme = self.index.norme_vers_connu[norme]
            pts = points_mot(len(norme))
            self.trouves.add(norme)
            self.trouves_ordre.insert(0, (forme, pts, False))
            self.score += pts
            ev = ecrire(self.con, self.session_id, Evenement(
                "labog", "reponse", entree_id=eid, trace="prod", reponse=forme, correct=True,
                latence_ms=latence_ms, frappe=frappe or Frappe(),
                data={"longueur": len(norme), "points": pts, "bonus": False, "fsrs": False}))
            self.carte.observer(eid, 1.0, poids=POIDS_PRODUIT, source="labog", evenement_id=ev, propager=False)
            return ReponseLabog(True, "", forme=forme, norme=norme, entree_id=eid, points=pts,
                                bonus=False, chemin=chemin)

        if norme in self.inconnus:
            eid, forme = self.index.norme_vers_tous[norme]
            pts = 1
            self.bonus.add(norme)
            self.trouves_ordre.insert(0, (forme, pts, True))
            self.score += pts
            ev = ecrire(self.con, self.session_id, Evenement(
                "labog", "reponse", entree_id=eid, trace="prod", reponse=forme, correct=True,
                latence_ms=latence_ms, frappe=frappe or Frappe(),
                data={"longueur": len(norme), "points": pts, "bonus": True, "fsrs": False}))
            self.carte.observer(eid, 1.0, poids=POIDS_PRODUIT, source="labog:bonus", evenement_id=ev, propager=False)
            return ReponseLabog(True, "mot bonus (hors liste connue)", forme=forme, norme=norme,
                                entree_id=eid, points=pts, bonus=True, chemin=chemin)

        ecrire(self.con, self.session_id, Evenement(
            "labog", "tentative", reponse=texte, correct=False, latence_ms=latence_ms,
            frappe=frappe or Frappe(), data={"raison": "mot inconnu", "fsrs": False}))
        return ReponseLabog(False, "mot inconnu")

    def indice(self) -> dict | None:
        """Un indice sur un mot connu non encore trouvé : 2 premières lettres + longueur + définition."""
        restants = [m for m in self.cibles if m not in self.trouves and m not in self.indices_vus]
        if not restants:
            restants = [m for m in self.cibles if m not in self.trouves]
        if not restants:
            return None
        restants.sort(key=lambda m: (-len(m), m))
        choisi = self.rng.choice(restants[: max(1, min(8, len(restants)))])
        self.indices_vus.add(choisi)
        eid, forme = self.index.norme_vers_connu[choisi]
        d = self.con.execute(
            "SELECT COALESCE(definition_precise, definition) FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
            (eid,),
        ).fetchone()
        return {
            "norme": choisi,
            "entree_id": eid,
            "amorce": forme[:2].upper() + "·" * (len(forme) - 2),
            "longueur": len(forme),
            "definition": " ".join((d[0] if d else "").split()),
        }

    def terminer(self) -> BilanLabog:
        ecrire(self.con, self.session_id, Evenement(
            "labog", "grille", correct=bool(self.trouves),
            data={"grille": "".join(self.grille), "trouves": len(self.trouves), "total": len(self.cibles),
                  "bonus": len(self.bonus), "score": self.score, "score_max": self.score_max,
                  "exceptions": len(self.inconnus), "fsrs": False}))
        manques = sorted((m for m in self.cibles if m not in self.trouves), key=lambda m: (-len(m), m))
        defs: dict[int, str] = {}
        eids_defs = list({self.index.norme_vers_connu[m][0] for m in manques[:30]})
        if eids_defs:
            for eid, df in self.con.execute(
                f"""SELECT entree_id, COALESCE(definition_precise, definition) FROM sens
                    WHERE entree_id IN ({', '.join('?' * len(eids_defs))}) AND definition != '' AND rang = 1""",
                eids_defs,
            ):
                defs[eid] = " ".join(df.split())
        mots: list[MotBilan] = []
        for m in sorted(self.cibles, key=lambda x: (-len(x), x not in self.trouves, x)):
            eid, forme = self.index.norme_vers_connu[m]
            mots.append(MotBilan(m, forme, eid, m in self.trouves, defs.get(eid, "")))
        return BilanLabog(
            score=self.score,
            score_max=self.score_max,
            trouves=len(self.trouves),
            total=len(self.cibles),
            bonus=[self.index.norme_vers_tous[m][1] for m in sorted(self.bonus)],
            mots=mots,
            exceptions_grille=len(self.inconnus),
        )


def resume(con: sqlite3.Connection) -> dict:
    """Pour l'Atlas : grilles jouées, score record, couverture moyenne."""
    rows = con.execute(
        """SELECT json_extract(data, '$.trouves'), json_extract(data, '$.total'), json_extract(data, '$.score')
           FROM evenement WHERE jeu = 'labog' AND type = 'grille' ORDER BY id"""
    ).fetchall()
    if not rows:
        return {"parties": 0, "record": 0, "couverture": 0.0}
    scores = [int(r[2] or 0) for r in rows]
    couvs = [(r[0] or 0) / max(1, r[1] or 1) for r in rows]
    return {"parties": len(rows), "record": max(scores), "couverture": sum(couvs) / len(couvs)}
