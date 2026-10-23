"""Paramètres FSRS personnels, ajustés sur ton journal — sans torch.

L'optimiseur officiel de py-fsrs exige torch et pandas (plusieurs centaines de Mo) : trop lourd pour un Mac
où tournent déjà bge-m3 et Whisper. Ici, une descente par coordonnées sur les paramètres qui comptent le
plus (stabilités initiales, croissance de la stabilité, oubli, décroissance), avec la perte de FSRS :
l'entropie croisée entre la rétention prédite et la réponse (juste / ratée) à chaque révision espacée
d'au moins un jour.

Un jeu de paramètres par trace (recep : reconnaître le sens ; prod : retrouver le mot ; ortho : l'écrire),
parce que ces mémoires n'oublient pas au même rythme : un jeu commun raccourcirait les intervalles du sens
pour suivre ceux de la forme. Une trace sans assez de données garde le jeu commun.

Garde-fous :
  * au moins `MIN_REVISIONS` révisions espacées, sinon on garde les paramètres par défaut ;
  * une pénalité L2 vers les paramètres par défaut, d'autant plus forte que les données sont rares ;
  * validation chronologique : les paramètres ne sont adoptés que s'ils prédisent mieux les 20 %
    les plus récents des révisions (jamais vus pendant l'ajustement) que les paramètres actuels.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass

from fsrs import Card, Rating, Scheduler
from fsrs.scheduler import DEFAULT_PARAMETERS, LOWER_BOUNDS_PARAMETERS, UPPER_BOUNDS_PARAMETERS

from lem.db import get_reglage, iso, now, parse_iso, set_reglage

MIN_REVISIONS = 150
# Stabilités initiales, croissance, oubli, pénalité « difficile » / bonus « facile », effet des révisions
# du même jour (17-19 : les retours dans la session) et décroissance de la courbe.
INDICES = (0, 1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20)


@dataclass
class Rapport:
    n_revisions: int
    perte_avant: float
    perte_apres: float
    validation_avant: float
    validation_apres: float
    adopte: bool
    parametres: list[float]
    raison: str = ""


def sequences(con: sqlite3.Connection, trace: str | None = None) -> list[list[tuple[float, int]]]:
    """Les révisions de chaque trace (d'un type donné, ou toutes), dans l'ordre : [(horodatage s, note), …]."""
    from lem.memory.scheduler import note_finale

    par_cle: dict[tuple, list[tuple[float, int]]] = {}
    connus: set[tuple] = set()
    for r in con.execute(
            """SELECT id, ts, entree_id, COALESCE(sens_id, 0), trace, note_auto, data FROM evenement
               WHERE type = 'reponse' AND trace IN ('recep', 'prod', 'ortho') ORDER BY ts, id"""):
        if trace is not None and r[4] != trace:
            continue
        data = json.loads(r[6])
        if data.get("fsrs") is False:
            continue
        n = note_finale(con, r[0], r[5])
        if n is None:
            continue
        cle = (r[2], r[3], r[4])
        if cle not in par_cle and data.get("role") == "sonde" and n >= 3:
            connus.add(cle)            # mot connu avant toute étude : il ne dit rien de ta façon d'apprendre
        par_cle.setdefault(cle, []).append((parse_iso(r[1]).timestamp(), n))
    return [s for c, s in par_cle.items() if len(s) >= 2 and c not in connus]


def _perte(params, seqs, coupure: float | None = None, apres_coupure: bool = False) -> tuple[float, int]:
    """Entropie croisée moyenne sur les révisions espacées d'au moins un jour (avant ou après `coupure`)."""
    from datetime import datetime, timezone

    sched = Scheduler(parameters=tuple(params), enable_fuzzing=False)
    total, n = 0.0, 0
    for seq in seqs:
        card = Card(card_id=1, due=datetime.fromtimestamp(seq[0][0], timezone.utc))
        precedent = None
        for t, note in seq:
            quand = datetime.fromtimestamp(t, timezone.utc)
            if precedent is not None and t - precedent >= 86400 and card.stability:
                compte = coupure is None or ((t >= coupure) == apres_coupure)
                if compte:
                    r = min(max(sched.get_card_retrievability(card, quand), 1e-4), 1 - 1e-4)
                    y = 1.0 if note > 1 else 0.0
                    total -= y * math.log(r) + (1 - y) * math.log(1 - r)
                    n += 1
            card, _ = sched.review_card(card, Rating(note), quand)
            precedent = t
    return (total / n if n else float("nan")), n


def _ajuster(seqs, actuels: list[float], max_tours: int = 6) -> Rapport:
    """Descente par coordonnées sur une famille de séquences ; validation sur les 20 % les plus récents."""
    _, n_tot = _perte(actuels, seqs)
    if n_tot < MIN_REVISIONS:
        return Rapport(n_tot, float("nan"), float("nan"), float("nan"), float("nan"), False, actuels,
                       f"{n_tot} révisions espacées ; il en faut {MIN_REVISIONS}")
    # Coupure chronologique : 80 % pour ajuster, les 20 % les plus récents pour valider.
    instants = sorted(t2 for s in seqs for (t1, _), (t2, _) in zip(s, s[1:]) if t2 - t1 >= 86400)
    coupure = instants[int(0.8 * len(instants))]
    lam = 2.0 / n_tot                       # pénalité vers les défauts, qui s'efface avec les données

    def objectif(p):
        perte, _ = _perte(p, seqs, coupure, apres_coupure=False)
        reg = sum(((p[i] - DEFAULT_PARAMETERS[i]) / (UPPER_BOUNDS_PARAMETERS[i] - LOWER_BOUNDS_PARAMETERS[i])) ** 2
                  for i in INDICES)
        return perte + lam * reg

    p = list(actuels)
    meilleur = objectif(p)
    perte_avant = meilleur
    pas = {i: 0.15 * (UPPER_BOUNDS_PARAMETERS[i] - LOWER_BOUNDS_PARAMETERS[i]) for i in INDICES}
    for _ in range(max_tours):
        progres = False
        for i in INDICES:
            for signe in (1, -1):
                essai = list(p)
                essai[i] = min(max(p[i] + signe * pas[i], LOWER_BOUNDS_PARAMETERS[i]), UPPER_BOUNDS_PARAMETERS[i])
                if essai[i] == p[i]:
                    continue
                v = objectif(essai)
                if v < meilleur - 1e-6:
                    p, meilleur, progres = essai, v, True
                    break
            else:
                pas[i] /= 2
        if not progres and max(pas.values()) < 1e-3:
            break
    val_avant, _ = _perte(actuels, seqs, coupure, apres_coupure=True)
    val_apres, _ = _perte(p, seqs, coupure, apres_coupure=True)
    adopte = val_apres < val_avant * 0.995
    return Rapport(n_tot, round(perte_avant, 5), round(meilleur, 5), round(val_avant, 5), round(val_apres, 5),
                   adopte, [round(x, 5) for x in p], "" if adopte else "pas d'amélioration sur les révisions récentes")


def optimiser(con: sqlite3.Connection, max_tours: int = 6, adopter: bool = True) -> dict[str, Rapport]:
    """Ajuste un jeu de paramètres par trace (recep, prod, ortho) ; adopte ceux qui prédisent mieux."""
    cfg = get_reglage(con, "fsrs", {}) or {}
    par_trace = dict(cfg.get("par_trace") or {})
    commun = list(cfg.get("parametres") or DEFAULT_PARAMETERS)
    rapports = {}
    for trace in ("recep", "prod", "ortho"):
        actuels = list(par_trace.get(trace) or commun)
        r = rapports[trace] = _ajuster(sequences(con, trace), actuels, max_tours)
        if r.adopte and adopter:
            par_trace[trace] = r.parametres
    cfg.update({"optimise_le": iso(now()), "n_revisions_brutes": _n_brut(con), "par_trace": par_trace})
    set_reglage(con, "fsrs", cfg)
    con.commit()
    if adopter and any(r.adopte for r in rapports.values()):
        from lem.memory.scheduler import reconstruire

        reconstruire(con)                     # les échéances suivent les nouveaux paramètres
    return rapports


def _n_brut(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM evenement WHERE type = 'reponse' AND trace IN ('recep', 'prod', 'ortho')"
                       ).fetchone()[0]


def a_optimiser(con: sqlite3.Connection, jours: int = 7, nouvelles: int = 100) -> bool:
    """Vrai s'il est temps de réajuster : jamais fait (et assez de données) ou fait il y a ≥ `jours` jours
    avec au moins `nouvelles` réponses depuis."""
    cfg = get_reglage(con, "fsrs", {}) or {}
    n = _n_brut(con)
    if not cfg.get("optimise_le"):
        return n >= MIN_REVISIONS
    ecart = (now() - parse_iso(cfg["optimise_le"])).total_seconds() / 86400
    return ecart >= jours and n - cfg.get("n_revisions_brutes", 0) >= nouvelles


@dataclass
class Bilan:
    n: int
    reussite: float | None          # part des révisions espacées réussies
    prevue: float | None            # rétention moyenne que FSRS prévoyait pour ces révisions


def bilan(con: sqlite3.Connection, trace: str, jours: int = 30) -> Bilan:
    """Révisions espacées (≥ 1 jour) des `jours` derniers jours pour une trace : réussite réelle et prévue.
    Un écart durable dit que les paramètres ne collent pas à ta mémoire (l'optimiseur le corrige)."""
    from datetime import datetime, timezone

    from lem.memory.scheduler import ordonnanceur

    sched = ordonnanceur(con, trace)
    depuis = now().timestamp() - jours * 86400
    ok, n, somme_r = 0, 0, 0.0
    for seq in sequences(con, trace):
        card = Card(card_id=1, due=datetime.fromtimestamp(seq[0][0], timezone.utc))
        precedent = None
        for t, note in seq:
            quand = datetime.fromtimestamp(t, timezone.utc)
            if precedent is not None and t - precedent >= 86400 and t >= depuis and card.stability:
                somme_r += sched.get_card_retrievability(card, quand)
                ok += note > 1
                n += 1
            card, _ = sched.review_card(card, Rating(note), quand)
            precedent = t
    return Bilan(n, ok / n if n else None, somme_r / n if n else None)

