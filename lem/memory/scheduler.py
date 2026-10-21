"""FSRS (v6, via py-fsrs) — état de chaque trace mémorielle, dérivé du journal.

Une trace = (entrée, sens, direction). Deux directions par sens :
  recep : mot → sens   (vocabulaire passif)
  prod  : sens → mot   (vocabulaire actif)

L'état n'est jamais « écrit à la main » : il est rejoué depuis le journal avec la
meilleure note disponible pour chaque réponse (humain > Jev > Claude > embedding).
Une notation différée (Claude en fin de session) se contente donc de rejouer.
Le fuzzing est désactivé : le rejeu doit être strictement déterministe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from fsrs import Card, Rating, Scheduler

from lem.db import get_reglage, iso, now, parse_iso
from lem.journal import rang_correcteur

TRACES = ("recep", "prod")


def ordonnanceur(con: sqlite3.Connection) -> Scheduler:
    cfg = get_reglage(con, "fsrs", {})
    kwargs = {"enable_fuzzing": False, "desired_retention": cfg.get("retention", 0.9)}
    if cfg.get("parametres"):
        kwargs["parameters"] = tuple(cfg["parametres"])
    return Scheduler(**kwargs)


def _card_id(entree_id: int, sens_id: int, trace: str) -> int:
    h = hashlib.blake2b(f"{entree_id}:{sens_id}:{trace}".encode(), digest_size=6).digest()
    return int.from_bytes(h, "big")


def carte_neuve(entree_id: int, sens_id: int, trace: str, quand: datetime) -> Card:
    return Card(card_id=_card_id(entree_id, sens_id, trace), due=quand)


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


def _sauver(con: sqlite3.Connection, entree_id: int, sens_id: int, trace: str, card: Card, reps: int, lapses: int) -> None:
    con.execute(
        """INSERT INTO trace_etat(entree_id, sens_id, trace, card_json, due, stabilite, difficulte, reps, lapses, maj)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(entree_id, sens_id, trace) DO UPDATE SET card_json = excluded.card_json, due = excluded.due,
             stabilite = excluded.stabilite, difficulte = excluded.difficulte, reps = excluded.reps,
             lapses = excluded.lapses, maj = excluded.maj""",
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
        ),
    )


def planifier(
    con: sqlite3.Connection,
    entree_id: int,
    sens_id: int | None,
    trace: str,
    note: int,
    quand: datetime | None = None,
) -> Card:
    """Chemin rapide en session : applique une note à l'état courant."""
    quand = quand or now()
    sens_id = sens_id or 0
    sched = ordonnanceur(con)
    row = con.execute(
        "SELECT card_json, reps, lapses FROM trace_etat WHERE entree_id = ? AND sens_id = ? AND trace = ?",
        (entree_id, sens_id, trace),
    ).fetchone()
    card = Card.from_json(row["card_json"]) if row else carte_neuve(entree_id, sens_id, trace, quand)
    reps = (row["reps"] if row else 0) + 1
    lapses = (row["lapses"] if row else 0) + (1 if note == 1 and row else 0)
    card, _ = sched.review_card(card, Rating(note), quand)
    _sauver(con, entree_id, sens_id, trace, card, reps, lapses)
    con.commit()
    return card


def reconstruire(con: sqlite3.Connection, entree_ids: list[int] | None = None) -> int:
    """Rejoue le journal pour (re)construire trace_etat. Renvoie le nombre de traces."""
    sched = ordonnanceur(con)
    filtre, args = "", []
    if entree_ids:
        filtre = f"AND entree_id IN ({', '.join('?' * len(entree_ids))})"
        args = list(entree_ids)
        con.execute(f"DELETE FROM trace_etat WHERE entree_id IN ({', '.join('?' * len(entree_ids))})", args)
    else:
        con.execute("DELETE FROM trace_etat")
    rows = con.execute(
        f"""SELECT id, ts, entree_id, COALESCE(sens_id, 0) AS sens_id, trace, note_auto, data
            FROM evenement WHERE type = 'reponse' AND trace IN ('recep', 'prod') {filtre}
            ORDER BY ts, id""",
        args,
    ).fetchall()
    etats: dict[tuple[int, int, str], tuple[Card, int, int]] = {}
    for r in rows:
        if json.loads(r["data"]).get("fsrs") is False:
            continue
        note = note_finale(con, r["id"], r["note_auto"])
        if note is None:
            continue
        cle = (r["entree_id"], r["sens_id"], r["trace"])
        quand = parse_iso(r["ts"])
        card, reps, lapses = etats.get(cle) or (carte_neuve(*cle, quand), 0, 0)
        if reps and note == 1:
            lapses += 1
        card, _ = sched.review_card(card, Rating(note), quand)
        etats[cle] = (card, reps + 1, lapses)
    for (eid, sid, trace), (card, reps, lapses) in etats.items():
        _sauver(con, eid, sid, trace, card, reps, lapses)
    con.commit()
    return len(etats)


@dataclass
class TraceDue:
    entree_id: int
    sens_id: int
    trace: str
    due: datetime
    retrievabilite: float


def dues(con: sqlite3.Connection, quand: datetime | None = None, limite: int = 200,
         traces: tuple[str, ...] = TRACES, entree_ids: set[int] | None = None) -> list[TraceDue]:
    """Traces à réviser, les plus fragiles (R la plus basse) d'abord."""
    quand = quand or now()
    sched = ordonnanceur(con)
    rows = con.execute(
        f"""SELECT entree_id, sens_id, trace, card_json, due FROM trace_etat
            WHERE due <= ? AND trace IN ({', '.join('?' * len(traces))})""",
        (iso(quand), *traces),
    ).fetchall()
    out = []
    for r in rows:
        if entree_ids is not None and r["entree_id"] not in entree_ids:
            continue
        card = Card.from_json(r["card_json"])
        out.append(TraceDue(r["entree_id"], r["sens_id"], r["trace"], parse_iso(r["due"]),
                            sched.get_card_retrievability(card, quand)))
    out.sort(key=lambda t: t.retrievabilite)
    return out[:limite]


def retrievabilite(con: sqlite3.Connection, entree_id: int, sens_id: int, trace: str,
                   quand: datetime | None = None) -> float | None:
    row = con.execute(
        "SELECT card_json FROM trace_etat WHERE entree_id = ? AND sens_id = ? AND trace = ?",
        (entree_id, sens_id or 0, trace),
    ).fetchone()
    if not row:
        return None
    return ordonnanceur(con).get_card_retrievability(Card.from_json(row["card_json"]), quand or now())
