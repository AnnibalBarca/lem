"""FSRS (v6, via py-fsrs) — état de chaque trace mémorielle, dérivé du journal.

Une trace = (entrée, sens, direction). Deux directions par sens :
  recep : mot → sens   (vocabulaire passif)
  prod  : sens → mot   (vocabulaire actif)
  ortho : graphie exacte (créée à la première faute, au niveau du lemme)

L'état n'est jamais « écrit à la main » : il est rejoué depuis le journal avec la
meilleure note disponible pour chaque réponse (humain > Jev > Claude > embedding).
Une notation différée (Claude en fin de session) se contente donc de rejouer.
Le fuzzing est désactivé : le rejeu doit être strictement déterministe.

Tri des mots nouveaux (statut de la trace) :
  * Un mot nouveau est d'abord SONDÉ (rôle « sonde » : mot → sens). Réussi (note ≥ 3), il est connu :
    pas de parcours d'apprentissage, une seule VÉRIFICATION dans 30 à 60 jours (statut « connu_sonde »).
  * Vérification réussie : le mot est acquis, retiré des révisions (statut « verifie »).
    Ratée : il rentre dans le parcours FSRS normal, comme un mot appris.
  * Sonde ratée ou floue : parcours FSRS normal (étapes d'apprentissage, puis intervalles).
Comme tout le reste, ces statuts se rejouent : si Claude rejuge une sonde, le mot change de voie.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from fsrs import Card, Rating, Scheduler, State

from lem.db import get_reglage, iso, now, parse_iso
from lem.journal import rang_correcteur

TRACES = ("recep", "prod", "ortho")


def ordonnanceur(con: sqlite3.Connection, trace: str | None = None) -> Scheduler:
    """Le Scheduler FSRS d'une trace. Les paramètres peuvent être propres à chaque trace (« par_trace »,
    ajustés par lem optimiser) : retrouver un mot n'est pas reconnaître un sens, et l'un oublie plus vite."""
    cfg = get_reglage(con, "fsrs", {}) or {}
    kwargs = {"enable_fuzzing": False, "desired_retention": cfg.get("retention", 0.9)}
    # Étapes d'apprentissage (s) : retours FSRS dans la session d'une carte nouvelle ou oubliée. Aucune par
    # défaut : au banc d'essai (lem/simulation.py), ces retours à 1 et 10 min réussissent à 99 % mais coûtent
    # un tiers des cartes et font surestimer la mémoire à FSRS ; une récupération réussie de chaque côté
    # dans la session (duel, puis sens → mot), puis l'espacement d'une session à l'autre, rendent 24 à 100 %
    # de mots retenus en plus. Réglage « etapes » / « etapes_reapprentissage » pour les rétablir.
    kwargs["learning_steps"] = tuple(timedelta(seconds=x) for x in cfg.get("etapes", ()))
    kwargs["relearning_steps"] = tuple(timedelta(seconds=x) for x in cfg.get("etapes_reapprentissage", ()))
    params = (cfg.get("par_trace") or {}).get(trace) if trace else None
    params = params or cfg.get("parametres")
    if params:
        kwargs["parameters"] = tuple(params)
    return Scheduler(**kwargs)


class _Ordonnanceurs(dict):
    """Un Scheduler par trace, construit à la demande (rejeu et listes de révisions)."""

    def __init__(self, con: sqlite3.Connection):
        super().__init__()
        self.con = con

    def __missing__(self, trace: str) -> Scheduler:
        s = self[trace] = ordonnanceur(self.con, trace)
        return s


def _card_id(entree_id: int, sens_id: int, trace: str) -> int:
    h = hashlib.blake2b(f"{entree_id}:{sens_id}:{trace}".encode(), digest_size=6).digest()
    return int.from_bytes(h, "big")


def carte_neuve(entree_id: int, sens_id: int, trace: str, quand: datetime) -> Card:
    return Card(card_id=_card_id(entree_id, sens_id, trace), due=quand)


STABILITE_CONNU = 45.0            # jours : un mot réussi à la sonde
VERIFICATION_JOURS = (30, 60)      # la vérification unique tombe entre 1 et 2 mois (déterministe par mot)
RETRAIT_JOURS = 3650               # un mot vérifié ne revient plus (sauf rejeu qui le remettrait en jeu)


def delai_verification(entree_id: int, sens_id: int, trace: str) -> int:
    lo, hi = VERIFICATION_JOURS
    h = int.from_bytes(hashlib.blake2b(f"verif:{entree_id}:{sens_id}:{trace}".encode(), digest_size=2).digest(), "big")
    return lo + h % (hi - lo + 1)


def carte_connue(cle: tuple[int, int, str], quand: datetime) -> Card:
    """État FSRS d'un mot connu dès la sonde : révision lointaine, stabilité élevée."""
    return Card(card_id=_card_id(*cle), state=State.Review, step=None, stability=STABILITE_CONNU,
                difficulty=3.0, due=quand + timedelta(days=delai_verification(*cle)), last_review=quand)


def note_finale(con: sqlite3.Connection, evenement_id: int, note_auto: int | None) -> int | None:
    """Meilleure note disponible pour une réponse."""
    rows = con.execute(
        "SELECT correcteur, note FROM notation WHERE evenement_id = ? AND note IS NOT NULL ORDER BY id DESC",
        (evenement_id,),
    ).fetchall()
    if rows:
        meilleur = min(rows, key=lambda r: rang_correcteur(r["correcteur"]))
        return meilleur["note"]
    return note_auto


def _sauver(con: sqlite3.Connection, entree_id: int, sens_id: int, trace: str, card: Card, reps: int, lapses: int,
            statut: str | None = None) -> None:
    con.execute(
        """INSERT INTO trace_etat(entree_id, sens_id, trace, card_json, due, stabilite, difficulte, reps, lapses, maj, statut)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(entree_id, sens_id, trace) DO UPDATE SET card_json = excluded.card_json, due = excluded.due,
             stabilite = excluded.stabilite, difficulte = excluded.difficulte, reps = excluded.reps,
             lapses = excluded.lapses, maj = excluded.maj, statut = excluded.statut""",
        (
            entree_id,
            sens_id,
            trace,
            card.to_json(),
            iso(card.due),
            card.stability,
            card.difficulty,
            reps,
            lapses,
            iso(),
            statut,
        ),
    )


def _fautes_d_accent(con: sqlite3.Connection, rows) -> set[int]:
    """Ids of the spelling events whose only fault was an accent: accents are not faults (setting
    `accents_tolerants`, on by default), so those answers, recorded before the policy, are replayed as if absent.
    The journal itself is untouched."""
    from lem.db import get_reglage
    from lem.texte import normaliser

    if not get_reglage(con, "accents_tolerants", True):
        return set()
    out: set[int] = set()
    ortho = [r for r in rows if r["trace"] == "ortho"]
    if not ortho:
        return out
    formes: dict[int, set[str]] = {}
    for r in ortho:
        if r["entree_id"] not in formes:
            formes[r["entree_id"]] = {normaliser(f[0]) for f in con.execute(
                "SELECT forme FROM forme WHERE entree_id = ?", (r["entree_id"],))}
        saisie = (con.execute("SELECT reponse FROM evenement WHERE id = ?", (r["id"],)).fetchone()[0] or "").strip()
        types = (json.loads(r["data"]).get("graphie") or {}).get("types")
        if types == ["accent"] or (saisie and normaliser(saisie) in formes[r["entree_id"]]):
            out.add(r["id"])
    return out


def reconstruire(con: sqlite3.Connection, entree_ids: list[int] | None = None) -> int:
    """Rejoue le journal pour (re)construire trace_etat. Renvoie le nombre de traces."""
    scheds = _Ordonnanceurs(con)
    filtre, args = "", []
    if entree_ids:
        filtre = f"AND entree_id IN ({', '.join('?' * len(entree_ids))})"
        args = list(entree_ids)
        con.execute(f"DELETE FROM trace_etat WHERE entree_id IN ({', '.join('?' * len(entree_ids))})", args)
    else:
        con.execute("DELETE FROM trace_etat")
    rows = con.execute(
        f"""SELECT id, ts, entree_id, COALESCE(sens_id, 0) AS sens_id, trace, note_auto, data
            FROM evenement WHERE type = 'reponse' AND trace IN ('recep', 'prod', 'ortho') {filtre}
            ORDER BY ts, id""",
        args,
    ).fetchall()
    etats: dict[tuple[int, int, str], tuple[Card, int, int, str | None]] = {}
    ignorer = _fautes_d_accent(con, rows)
    for r in rows:
        data = json.loads(r["data"])
        if data.get("fsrs") is False or r["id"] in ignorer:
            continue
        note = note_finale(con, r["id"], r["note_auto"])
        if note is None:
            continue
        cle = (r["entree_id"], r["sens_id"], r["trace"])
        sched = scheds[r["trace"]]
        quand = parse_iso(r["ts"])
        card, reps, lapses, statut = etats.get(cle) or (carte_neuve(*cle, quand), 0, 0, None)
        role = data.get("role")
        if reps and note == 1:
            lapses += 1
        if role == "sonde" and reps == 0 and note >= 3:
            card, statut = carte_connue(cle, quand), "connu_sonde"
        elif statut == "connu_sonde" and note >= 3:
            # Vérification unique réussie : acquis, on ne le revoit plus.
            card, _ = sched.review_card(card, Rating(note), quand)
            card.due, statut = quand + timedelta(days=RETRAIT_JOURS), "verifie"
        else:
            card, _ = sched.review_card(card, Rating(note), quand)
            if statut in ("connu_sonde", "verifie") and note == 1:
                statut = None                       # oublié : il rentre dans le parcours normal
        etats[cle] = (card, reps + 1, lapses, statut)
    for (eid, sid, trace), (card, reps, lapses, statut) in etats.items():
        _sauver(con, eid, sid, trace, card, reps, lapses, statut)
    con.commit()
    return len(etats)


@dataclass
class TraceDue:
    entree_id: int
    sens_id: int
    trace: str
    due: datetime
    retrievabilite: float
    statut: str | None = None


def dues(con: sqlite3.Connection, quand: datetime | None = None, limite: int = 200,
         traces: tuple[str, ...] = TRACES, entree_ids: set[int] | None = None) -> list[TraceDue]:
    """Traces à réviser, les plus fragiles (R la plus basse) d'abord."""
    quand = quand or now()
    scheds = _Ordonnanceurs(con)
    rows = con.execute(
        f"""SELECT entree_id, sens_id, trace, card_json, due, statut FROM trace_etat
            WHERE due <= ? AND trace IN ({', '.join('?' * len(traces))})""",
        (iso(quand), *traces),
    ).fetchall()
    out = []
    for r in rows:
        if entree_ids is not None and r["entree_id"] not in entree_ids:
            continue
        card = Card.from_json(r["card_json"])
        out.append(TraceDue(r["entree_id"], r["sens_id"], r["trace"], parse_iso(r["due"]),
                            scheds[r["trace"]].get_card_retrievability(card, quand), r["statut"]))
    out.sort(key=lambda t: t.retrievabilite)
    return out[:limite]


def echeance(con: sqlite3.Connection, entree_id: int, sens_id: int, trace: str) -> datetime | None:
    """Prochaine échéance d'une trace (None si elle n'existe pas)."""
    row = con.execute("SELECT due FROM trace_etat WHERE entree_id = ? AND sens_id = ? AND trace = ?",
                      (entree_id, sens_id or 0, trace)).fetchone()
    return parse_iso(row[0]) if row else None


def charge(con: sqlite3.Connection, jours: int = 7, quand: datetime | None = None) -> float:
    """Révisions attendues par jour sur les `jours` prochains jours (dues comprises)."""
    quand = quand or now()
    n = con.execute("SELECT COUNT(*) FROM trace_etat WHERE due <= ?",
                    (iso(quand + timedelta(days=jours)),)).fetchone()[0]
    return n / jours


def retrievabilite(con: sqlite3.Connection, entree_id: int, sens_id: int, trace: str,
                   quand: datetime | None = None) -> float | None:
    row = con.execute(
        "SELECT card_json FROM trace_etat WHERE entree_id = ? AND sens_id = ? AND trace = ?",
        (entree_id, sens_id or 0, trace),
    ).fetchone()
    if not row:
        return None
    return ordonnanceur(con, trace).get_card_retrievability(Card.from_json(row["card_json"]), quand or now())
