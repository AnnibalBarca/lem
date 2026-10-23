"""Élève simulé : un banc d'essai pour le moteur d'apprentissage.

On ne peut pas juger un ordonnanceur à l'œil. On fabrique donc :
  * un lexique synthétique (fréquences, domaines, origines, morphèmes) ;
  * un élève dont on connaît la VÉRITÉ : quels mots il connaît au départ (structurée par domaine,
    origine et morphème, comme un vrai lecteur), et pour chaque mot appris deux mémoires
    (sens, forme) qui oublient selon une courbe de puissance et se renforcent à la récupération,
    d'autant plus que le souvenir était fragile (effet d'espacement) ;
  * des PARONYMES (15 % des mots ne diffèrent d'un autre que d'une lettre) et une INTERFÉRENCE : en
    cherchant un mot, l'élève peut sortir son voisin, d'autant plus que le voisin est bien appris et
    que la paire n'a pas été distinguée ; les duels (et, un peu, la liste « à ne pas confondre ») la
    réduisent ;
  * des sessions quotidiennes de durée fixe, jouées par le vrai moteur (PartieCartes), avec une
    horloge simulée.

On mesure ce qui compte, un mois après la fin (sans jouer entre-temps) :
  * retenus_sens / retenus_forme : l'espérance du nombre de mots, non connus au départ, dont l'élève
    retrouverait le sens / le mot (Σ R — la mesure des simulateurs FSRS), par heure de jeu ;
  * acquis_sens / acquis_forme : ceux qu'il retrouverait presque sûrement (R ≥ 0,9 / 0,8) ;
  * le temps passé sur des mots déjà connus ; la justesse de la carte de connaissance (Brier, AUC).

Les paramètres de l'élève ne sont PAS ceux de FSRS : le moteur doit s'adapter à un élève qu'il ne
connaît pas. Les chiffres absolus dépendent du modèle d'élève ; ce sont les écarts entre moteurs,
à graine égale, qui renseignent.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from lem.db import (ajouter_formes, ajouter_relation, connect, domaine_id, morpheme_id, regler_horloge, set_reglage,
                    upsert_entree)

JOUR = 86400.0

# ── Lexique synthétique ───────────────────────────────────────────────────────────────────────

DOMAINES = {"science": ["chimie", "biologie", "physique", "médecine"], "société": ["droit", "économie", "religion"],
            "arts": ["musique", "théâtre", "peinture"], "vie pratique": ["cuisine", "marine"]}
ORIGINES = [("francais", 0.55), ("latin", 0.2), ("grec", 0.12), ("anglais", 0.08), ("arabe", 0.05)]
SYLLABES = [c + v for c in "bcdfgjlmnprstvz" for v in "aeiou"] + ["ch" + v for v in "aeiou"] + ["tr" + v for v in "aeio"]


def _mot(rng: random.Random, pris: set[str]) -> str:
    while True:
        m = "".join(rng.choice(SYLLABES) for _ in range(rng.randint(2, 4))) + rng.choice(["", "", "e", "on", "ique", "al"])
        if m not in pris:
            pris.add(m)
            return m


def _variante(rng: random.Random, base: str, pris: set[str]) -> str | None:
    """Un paronyme : une voyelle ou une consonne change (« bacitone » → « bacetone »)."""
    for _ in range(20):
        i = rng.randrange(len(base))
        c = base[i]
        jeu = "aeiou" if c in "aeiou" else "bcdfgjlmnprstvz"
        m = base[:i] + rng.choice([x for x in jeu if x != c]) + base[i + 1:]
        if m not in pris:
            pris.add(m)
            return m
    return None


def lexique_synthetique(con: sqlite3.Connection, n: int = 3000, graine: int = 0, paronymes: float = 0.15) -> list[int]:
    """Remplit `con` d'un lexique de `n` entrées (dont une part de paronymes). Renvoie leurs ids."""
    rng = random.Random(graine)
    for macro, enfants in DOMAINES.items():
        domaine_id(con, macro)
        for e in enfants:
            domaine_id(con, e, macro)
    sous = [e for es in DOMAINES.values() for e in es]
    morphs = [(f"{rng.choice(SYLLABES)}-", f"glose{i}") for i in range(40)] + \
             [(f"-{rng.choice(SYLLABES)}{rng.choice('mnrl')}e", f"suffixe{i}") for i in range(20)]
    pris: set[str] = set()
    ids = []
    formes: list[str] = []
    for i in range(n):
        forme = (_variante(rng, rng.choice(formes), pris) if formes and rng.random() < paronymes else None) \
            or _mot(rng, pris)
        formes.append(forme)
        z = float(np.clip(rng.gauss(2.6, 1.3), 0.0, 6.5))
        origine = rng.choices([o for o, _ in ORIGINES], [w for _, w in ORIGINES])[0]
        eid = upsert_entree(con, forme, "nom", zipf=z, origine=origine)
        ajouter_formes(con, eid, [forme, forme + "s"])
        sid = con.execute("INSERT INTO sens(entree_id, rang, definition) VALUES(?, 1, ?)",
                          (eid, f"chose numéro {i} du lexique simulé")).lastrowid
        for d in rng.sample(sous, rng.choice([0, 1, 1, 1, 2])):
            con.execute("INSERT OR IGNORE INTO sens_domaine VALUES(?, ?)", (sid, domaine_id(con, d)))
        for pos, (m, g) in enumerate(rng.sample(morphs, rng.choice([0, 0, 1, 1, 2]))):
            con.execute("INSERT OR IGNORE INTO entree_morpheme VALUES(?, ?, ?)", (eid, morpheme_id(con, m, g), pos))
        ids.append(eid)
    for _ in range(n // 20):                      # quelques synonymes
        a, b = rng.sample(ids, 2)
        ajouter_relation(con, a, b, "synonyme", origine="simulation")
    con.commit()
    return ids


# ── L'élève (la vérité) ───────────────────────────────────────────────────────────────────────

@dataclass
class ParamsEleve:
    """Mémoire de l'élève simulé, calée sur l'ordre de grandeur de FSRS (population Anki) pour « moyen »."""
    pente: float = 1.8               # vérité de départ : logit = pente·(zipf − seuil) + écarts
    seuil: float = 2.7
    ecart_domaine: float = 1.2       # écart-type des affinités par domaine (le profil à découvrir)
    ecart_morph: float = 0.8
    origines: dict = field(default_factory=lambda: {"grec": -1.2, "latin": -0.3, "anglais": 0.4, "arabe": -0.6})
    bruit: float = 0.6
    gain: float = 30.0               # renforcement à la récupération : ×3,5 à R = 0,9 pour S = 1 j ; ×1,2 à R = 0,99
    pente_r: float = 0.8             # SInc ∝ e^{pente_r·(1−R)} − 1 (comme FSRS)
    decroissance_s: float = 0.15     # le gain diminue quand S grandit
    echec_facteur: float = 0.3       # S après un oubli
    expo_sens: float = 0.25          # S (jours) d'un sens découvert à la révélation (~6 h)
    expo_forme: float = 0.10         # S d'une forme découverte (~2 h) : savoir le sens ≠ retrouver le mot
    transfert: float = 0.10          # une récupération d'un côté renforce un peu l'autre
    interference: float = 0.6        # 0 : pas de confusion entre paronymes
    gain_duel: float = 0.35          # distinction d'une paire gagnée par un duel réussi
    gain_liste: float = 0.05         # … par la liste « à ne pas confondre » de la révélation


PROFILS = {
    "moyen": ParamsEleve(),
    "faible": ParamsEleve(gain=18.0, expo_sens=0.08, expo_forme=0.03, echec_facteur=0.2),
    "fort": ParamsEleve(gain=45.0, expo_sens=0.6, expo_forme=0.25, echec_facteur=0.45),
}


def retention(S: float, t_jours: float) -> float:
    """Courbe de puissance (FSRS 4) : R = (1 + t / 9S)^−1."""
    if S <= 0:
        return 0.0
    return 1.0 / (1.0 + max(t_jours, 0.0) / (9.0 * S))


@dataclass
class Trace:
    S: float
    t: float                          # dernière récupération ou exposition (secondes simulées)


class Eleve:
    def __init__(self, con: sqlite3.Connection, ids: list[int], params: ParamsEleve | None = None,
                 graine: int = 0, t0: float = 0.0):
        self.p = params or ParamsEleve()
        self.rng = random.Random(graine + 1000)
        self.mem: dict[tuple[int, str], Trace] = {}
        self.etat0: dict[int, str] = {}
        dom = {d: self.rng.gauss(0, self.p.ecart_domaine) for (d,) in con.execute("SELECT nom FROM domaine")}
        mor = {m: self.rng.gauss(0, self.p.ecart_morph) for (m,) in con.execute("SELECT forme FROM morpheme")}
        info = {r[0]: r for r in con.execute("SELECT id, zipf, origine FROM entree")}
        doms: dict[int, list[str]] = {}
        for e, d in con.execute("""SELECT s.entree_id, d.nom FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
                                   JOIN domaine d ON d.id = sd.domaine_id"""):
            doms.setdefault(e, []).append(d)
        mors: dict[int, list[str]] = {}
        for e, m in con.execute("SELECT em.entree_id, m.forme FROM entree_morpheme em JOIN morpheme m ON m.id = em.morpheme_id"):
            mors.setdefault(e, []).append(m)
        for e in ids:
            _, z, o = info[e]
            x = self.p.pente * ((z or 0) - self.p.seuil) + self.p.origines.get(o, 0.0)
            x += sum(dom[d] for d in doms.get(e, [])) + sum(mor[m] for m in mors.get(e, []))
            x += self.rng.gauss(0, self.p.bruit)
            q = 1 / (1 + math.exp(-x))
            if q > 0.85:
                self.etat0[e] = "connu"
                S = self.rng.uniform(200, 2000)
                self.mem[(e, "sens")] = Trace(S, t0 - self.rng.uniform(0, 30) * JOUR)
                self.mem[(e, "forme")] = Trace(S * 0.6, t0 - self.rng.uniform(0, 30) * JOUR)
            elif q > 0.45:
                self.etat0[e] = "flou"
                self.mem[(e, "sens")] = Trace(self.rng.uniform(2, 10), t0 - self.rng.uniform(20, 60) * JOUR)
                self.mem[(e, "forme")] = Trace(self.rng.uniform(0.5, 3), t0 - self.rng.uniform(20, 60) * JOUR)
            else:
                self.etat0[e] = "inconnu"
        self._voisins(con, ids)

    def _voisins(self, con: sqlite3.Connection, ids: list[int]) -> None:
        """Paronymes de chaque mot (similarité orthographique ≥ 0,7) : les candidats à l'intrusion."""
        from lem.confusables import IndexFormel

        index = IndexFormel(con)
        formes = dict(con.execute("SELECT id, forme FROM entree").fetchall())
        self.formes = formes
        self.par_forme = {f: e for e, f in formes.items()}
        self.intrus = None
        self.voisins: dict[int, list[tuple[int, float]]] = {}
        if self.p.interference <= 0:
            return
        for e in ids:
            self.voisins[e] = [(n, s) for n, s in index.proches(formes[e], k=4, seuil=0.7) if n != e]
        self.distinction: dict[frozenset, float] = {}

    def confusion(self, e: int, cote: str, t: float, parmi: set[int] | None = None) -> float:
        """Probabilité qu'en cherchant e, un voisin bien appris s'impose à sa place."""
        if self.p.interference <= 0:
            return 0.0
        p_ok = 1.0
        for n, s in self.voisins.get(e, ()):
            if parmi is not None and n not in parmi:
                continue
            d = self.distinction.get(frozenset((e, n)), 0.0)
            p_ok *= 1 - self.p.interference * s * (1 - d) * self.R(n, cote, t)
        return 1 - p_ok

    def distinguer(self, e: int, autres, gain: float) -> None:
        if self.p.interference <= 0:
            return
        for n in autres:
            if n is not None and n != e:
                k = frozenset((e, n))
                self.distinction[k] = min(1.0, self.distinction.get(k, 0.0) + gain)

    def duel(self, e: int, options: list[int], t: float) -> tuple[bool, float]:
        """Un duel : reconnaître le bon mot parmi des voisins. Réussi, il distingue les paires."""
        R = self.R(e, "sens", t)
        conf = self.confusion(e, "sens", t, set(options))
        ok = self.rng.random() < R * (1 - conf)
        if not ok and self.rng.random() < 1 / max(1, len(options)):
            ok = True                                     # au hasard, ça tombe juste
        self._renforcer(e, "sens", t, ok, facteur=0.5)
        self.distinguer(e, options, self.p.gain_duel if ok else self.p.gain_duel / 2)
        return ok, R

    def R(self, e: int, cote: str, t: float) -> float:
        tr = self.mem.get((e, cote))
        return 0.0 if tr is None else retention(tr.S, (t - tr.t) / JOUR)

    def _renforcer(self, e: int, cote: str, t: float, succes: bool, facteur: float = 1.0) -> None:
        tr = self.mem.get((e, cote))
        if tr is None:
            return
        R = retention(tr.S, (t - tr.t) / JOUR)
        if succes:
            hausse = self.p.gain * tr.S ** -self.p.decroissance_s * (math.exp(self.p.pente_r * (1 - R)) - 1)
            tr.S *= 1 + facteur * hausse
        else:
            tr.S = max(0.01, tr.S * self.p.echec_facteur)
        tr.t = t

    def tenter(self, e: int, cote: str, t: float) -> tuple[bool, float]:
        """Une tentative de récupération. Renvoie (succès, R au moment de la tentative).
        Une intrusion (le voisin s'impose) est une erreur, sans affaiblir la trace du mot lui-même ;
        `self.intrus` dit quel voisin est sorti (l'élève le tape à la place)."""
        self.intrus = None
        R = self.R(e, cote, t)
        ok = self.rng.random() < R
        if ok and self.rng.random() < self.confusion(e, cote, t):
            voisins = [n for n, _ in self.voisins.get(e, ())]
            self.intrus = max(voisins, key=lambda n: self.R(n, cote, t)) if voisins else None
            self.distinguer(e, voisins, self.p.gain_liste)
            return False, R
        self._renforcer(e, cote, t, ok)
        if ok:
            autre = "forme" if cote == "sens" else "sens"
            if (e, autre) in self.mem:
                self.mem[(e, autre)].S *= 1 + self.p.transfert
        return ok, R

    def exposer(self, e: int, t: float) -> None:
        """La révélation montre le mot et son sens : on (ré)encode les deux, sans récupération."""
        for cote, s0 in (("sens", self.p.expo_sens), ("forme", self.p.expo_forme)):
            tr = self.mem.get((e, cote))
            if tr is None:
                self.mem[(e, cote)] = Trace(s0, t)
            elif retention(tr.S, (t - tr.t) / JOUR) < 0.5:
                tr.S, tr.t = max(tr.S, s0), t

    def note(self, ok: bool, R: float) -> int:
        """L'auto-évaluation d'un élève honnête."""
        if not ok:
            return 1
        return 2 if R < 0.6 else 3 if R < 0.97 else 4


# ── Le banc ───────────────────────────────────────────────────────────────────────────────────

DUREES = {"recep": (14.0, 6.0), "prod": (7.0, 6.0), "discrimination": (5.0, 5.0), "graphie": (6.0, 6.0)}
REVELATION = 4.0


@dataclass
class Resultats:
    moteur: str
    jours: int
    minutes_jeu: float
    cartes: int
    mots_vus: int
    acquis_sens: int
    acquis_forme: int
    acquis_sens_par_heure: float
    acquis_forme_par_heure: float
    retenus_sens: float
    retenus_forme: float
    retenus_sens_par_heure: float
    retenus_forme_par_heure: float
    minutes_sur_connus: float
    brier_carte: float
    auc_carte: float
    duree_calcul_s: float
    detail: dict = field(default_factory=dict)


def _auc(scores: list[float], verites: list[bool]) -> float:
    pos = [s for s, v in zip(scores, verites) if v]
    neg = [s for s, v in zip(scores, verites) if not v]
    if not pos or not neg:
        return float("nan")
    rangs = np.argsort(np.argsort(np.array(pos + neg)))
    r_pos = rangs[: len(pos)].sum() + len(pos)
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def simuler(moteur_params: dict | None = None, *, jours: int = 30, minutes: float = 15.0, n_mots: int = 3000,
            graine: int = 0, delai_eval: int = 30, chemin: str = ":memory:", nom: str = "moteur",
            params_eleve: ParamsEleve | str | None = None, reglages: dict | None = None,
            optimiser_chaque: int = 0, journal=None) -> Resultats:
    """Joue `jours` sessions de `minutes` minutes avec le vrai moteur de Cartes, puis évalue."""
    from lem.games import REGISTRE
    from lem.games.cartes import PartieCartes
    from lem.memory.knowledge import CarteConnaissance, ParamsPrior, calibrer

    debut_calcul = time.monotonic()
    if isinstance(params_eleve, str):
        params_eleve = PROFILS[params_eleve]
    con = connect(chemin)
    ids = lexique_synthetique(con, n_mots, graine)
    calibrer(con, ParamsPrior(cible_connus=0.35 * n_mots))
    for cle, valeur in (reglages or {}).items():          # ex. {"fsrs": {"retention": 0.85}}
        set_reglage(con, cle, valeur)
    t0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    horloge = {"t": 0.0}
    regler_horloge(lambda: t0 + timedelta(seconds=horloge["t"]))
    try:
        eleve = Eleve(con, ids, params_eleve, graine)
        t_eval0 = (jours - 1) * JOUR + delai_eval * JOUR
        avant_s = {e: eleve.R(e, "sens", t_eval0) * (1 - eleve.confusion(e, "sens", t_eval0)) for e in ids}
        avant_f = {e: eleve.R(e, "forme", t_eval0) * (1 - eleve.confusion(e, "forme", t_eval0)) for e in ids}
        params = {**REGISTRE["cartes"].defauts(), **(moteur_params or {})}
        rng = random.Random(graine + 7)
        cartes = 0
        vus: set[int] = set()
        sur_connus = 0.0
        jeu_total = 0.0
        for j in range(jours):
            horloge["t"] = j * JOUR
            carte = CarteConnaissance(con)
            partie = PartieCartes(con, dict(params), carte, rng=rng)
            budget = minutes * 60
            fin = horloge["t"] + budget
            while horloge["t"] < fin:
                it = partie.suivant()
                if it is None:
                    break
                e = it.entree_id
                cote = "sens" if it.exercice in ("recep", "discrimination") else "forme"
                if it.exercice == "discrimination":
                    ok, R = eleve.duel(e, [x for x, _ in it.options], horloge["t"])
                    k = next(i for i, (x, _) in enumerate(it.options) if x == e) if ok else \
                        next((i for i, (x, _) in enumerate(it.options) if x != e), 0)
                    texte = str(k + 1)
                else:
                    ok, R = eleve.tenter(e, cote, horloge["t"])
                    if it.exercice == "recep":
                        texte = "je le connais" if ok else "?"
                    elif ok:
                        texte = it.forme
                    else:                                   # une intrusion se tape : le moteur la voit
                        texte = eleve.formes.get(eleve.intrus, "?") if eleve.intrus else "?"
                d_ok, d_ko = DUREES.get(it.exercice, (8.0, 6.0))
                duree = (d_ok if ok else d_ko) + REVELATION
                horloge["t"] += duree
                res = partie.repondre(it, texte, int(duree * 1000))
                partie.valider(res, None if it.exercice in ("graphie", "discrimination") else eleve.note(ok, R))
                eleve.exposer(e, horloge["t"])
                eleve.distinguer(e, [eleve.par_forme.get(v["mot"]) for v in it.voisins[:5]], eleve.p.gain_liste)
                cartes += 1
                vus.add(e)
                jeu_total += duree
                if eleve.etat0.get(e) == "connu":
                    sur_connus += duree
            carte.recalculer_tout()
            if optimiser_chaque and (j + 1) % optimiser_chaque == 0:
                from lem.memory.optimiseur import optimiser

                raps = optimiser(con)
                if journal:
                    journal(f"{nom} · optimiseur : " + " · ".join(
                        f"{t} {r.n_revisions} rév. {'adopté' if r.adopte else 'gardé'} ({r.validation_avant} → {r.validation_apres})"
                        for t, r in raps.items()))
            if journal:
                journal(f"{nom} · jour {j + 1}/{jours} · {cartes} cartes · {len(vus)} mots vus")
        t_eval = (jours - 1) * JOUR + delai_eval * JOUR
        non_connus = [e for e in ids if eleve.etat0[e] != "connu"]
        acq_s = sum(eleve.R(e, "sens", t_eval) >= 0.9 for e in non_connus)
        acq_f = sum(eleve.R(e, "forme", t_eval) >= 0.8 for e in non_connus)
        # Ce que l'élève aurait retenu sans jouer : les mots flous oublient aussi ; on soustrait leur R initial
        # projeté à t_eval pour ne compter que l'apport du jeu.
        # Retrouver le bon mot, pas son paronyme : R × (1 − probabilité d'intrusion).
        ret_s = sum(eleve.R(e, "sens", t_eval) * (1 - eleve.confusion(e, "sens", t_eval)) - avant_s.get(e, 0.0)
                    for e in non_connus)
        ret_f = sum(eleve.R(e, "forme", t_eval) * (1 - eleve.confusion(e, "forme", t_eval)) - avant_f.get(e, 0.0)
                    for e in non_connus)
        # Base de comparaison : ce que l'élève aurait su de toute façon (mots flous qui tiennent encore).
        base_s = sum(eleve.R(e, "sens", t_eval) >= 0.9 for e in non_connus if (e, "sens") in eleve.mem and e not in vus)
        t_fin = jours * JOUR
        p = dict(con.execute("SELECT entree_id, p_connu FROM connaissance").fetchall())
        verite = [eleve.R(e, "sens", t_fin) >= 0.9 for e in ids]
        scores = [p.get(e, 0.5) for e in ids]
        brier = float(np.mean([(s - v) ** 2 for s, v in zip(scores, verite)]))
        heures = max(jeu_total / 3600, 1e-9)
        return Resultats(
            nom, jours, round(jeu_total / 60, 1), cartes, len(vus), acq_s, acq_f,
            round(acq_s / heures, 1), round(acq_f / heures, 1), round(ret_s, 1), round(ret_f, 1),
            round(ret_s / heures, 2), round(ret_f / heures, 2), round(sur_connus / 60, 1),
            round(brier, 4), round(_auc(scores, verite), 4), round(time.monotonic() - debut_calcul, 1),
            {"etats_depart": {k: sum(v == k for v in eleve.etat0.values()) for k in ("connu", "flou", "inconnu")},
             "acquis_sans_jouer": base_s, "params": params},
        )
    finally:
        regler_horloge(None)
        con.close()


def banc_productions(n_tapes: int = 300, *, propager: bool = True, graine: int = 0, n_mots: int = 3000,
                     params_eleve: ParamsEleve | str | None = None) -> dict:
    """La carte de connaissance face aux mots tapés librement (Bombe, Syllabaire) : un biais de sélection.

    À la Bombe, on ne tape que des mots qu'on connaît : chaque observation est positive par construction. Si
    elle propage aux θ (type, origine, bande de fréquence…), tout le lexique monte. On fait taper à l'élève
    `n_tapes` mots qu'il sait produire (les plus courants plus souvent), puis on mesure la carte sur les mots
    qu'il n'a PAS tapés : Brier contre la vérité, et Σ P(connu) contre le nombre de mots qu'il connaît."""
    from lem.memory.knowledge import CarteConnaissance, ParamsPrior, calibrer

    if isinstance(params_eleve, str):
        params_eleve = PROFILS[params_eleve]
    con = connect(":memory:")
    ids = lexique_synthetique(con, n_mots, graine)
    calibrer(con, ParamsPrior(cible_connus=0.35 * n_mots))
    eleve = Eleve(con, ids, params_eleve, graine)
    carte = CarteConnaissance(con)
    rng = random.Random(graine + 7)
    zipf = dict(con.execute("SELECT id, zipf FROM entree").fetchall())
    productibles = [e for e in ids if eleve.R(e, "forme", 0.0) >= 0.9]
    tapes: list[int] = []
    pool = list(productibles)
    while pool and len(tapes) < n_tapes:
        e = rng.choices(pool, weights=[1 + (zipf.get(x) or 0) ** 2 for x in pool])[0]
        pool.remove(e)
        tapes.append(e)

    def mesure() -> dict:
        p = dict(zip(carte.ids.tolist(), carte.p_tous().tolist()))
        reste = [e for e in ids if e not in set(tapes)]
        verite = [eleve.R(e, "sens", 0.0) >= 0.9 for e in reste]
        scores = [p.get(e, 0.5) for e in reste]
        return {"brier": round(float(np.mean([(s - v) ** 2 for s, v in zip(scores, verite)])), 4),
                "somme_p": round(sum(scores), 1), "connus_reels": sum(verite)}

    avant = mesure()
    for e in tapes:
        carte.observer(e, 1.0, poids=0.5, persister=False, source="bombe:spontane", propager=propager)
    apres = mesure()
    return {"tapes": len(tapes), "propager": propager, "avant": avant, "apres": apres}


def comparer(configs: dict[str, dict], graines=(0, 1, 2), **kw) -> dict[str, dict]:
    """Moyenne de plusieurs graines par configuration. Renvoie {nom: métriques moyennes}."""
    out = {}
    for nom, cfg in configs.items():
        runs = [simuler(cfg, graine=g, nom=nom, **kw) for g in graines]
        moy = {}
        for k, v in asdict(runs[0]).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals = [getattr(r, k) for r in runs]
                moy[k] = round(float(np.mean(vals)), 3)
                moy[k + "_et"] = round(float(np.std(vals)), 3)
        out[nom] = moy
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys

    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(asdict(simuler(cfg, journal=print)), ensure_ascii=False, indent=2))
