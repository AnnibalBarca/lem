"""Le journal : la seule source de vérité sur l'utilisateur.

Tout jeu écrit ici, et seulement ici. FSRS, la carte de connaissance et l'atlas
des confusions sont des projections recalculables de ces lignes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lem.db import dumps, iso, now


@dataclass
class Frappe:
    """Mesures de saisie captées par l'interface pour une réponse."""

    premiere_frappe_ms: int | None = None
    frappes: int = 0
    effacements: int = 0
    blocs: int = 0                  # insertions de plusieurs caractères d'un coup (dictée, collage)

    @property
    def mode(self) -> str:
        """clavier | dictee | mixte. La dictée (Whisper) écrit à ta place : ce n'est pas TA graphie."""
        if not self.blocs:
            return "clavier"
        return "dictee" if self.frappes <= 2 else "mixte"

    @property
    def dictee(self) -> bool:
        return self.mode != "clavier"


@dataclass
class Evenement:
    jeu: str
    type: str
    entree_id: int | None = None
    sens_id: int | None = None
    trace: str | None = None
    reponse: str | None = None
    correct: bool | None = None
    note_auto: int | None = None
    latence_ms: int | None = None
    frappe: Frappe = field(default_factory=Frappe)
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime | None = None


def nettoyer_saisie(texte: str) -> str:
    """Retire ce qu'ajoute une dictée autour d'un mot isolé : espaces, ponctuation finale, guillemets."""
    return texte.strip().strip("«»\"'“”").strip().rstrip(".!?…;:,").strip()


def ouvrir_session(con: sqlite3.Connection, jeu: str, params: dict, contexte: dict | None = None) -> int:
    ctx = {"heure_locale": datetime.now().strftime("%H:%M"), **(contexte or {})}
    sid = con.execute(
        "INSERT INTO session(jeu, params, debut, contexte) VALUES(?, ?, ?, ?)",
        (jeu, dumps(params), iso(), dumps(ctx)),
    ).lastrowid
    con.commit()
    return sid


def fermer_session(con: sqlite3.Connection, session_id: int) -> None:
    con.execute("UPDATE session SET fin = ? WHERE id = ?", (iso(), session_id))
    con.commit()


def ecrire(con: sqlite3.Connection, session_id: int | None, ev: Evenement) -> int:
    eid = con.execute(
        """INSERT INTO evenement(ts, session_id, jeu, type, entree_id, sens_id, trace, reponse,
               correct, note_auto, latence_ms, premiere_frappe_ms, frappes, effacements, mode_saisie, data)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            iso(ev.ts or now()),
            session_id,
            ev.jeu,
            ev.type,
            ev.entree_id,
            ev.sens_id,
            ev.trace,
            ev.reponse,
            None if ev.correct is None else int(ev.correct),
            ev.note_auto,
            ev.latence_ms,
            ev.frappe.premiere_frappe_ms,
            ev.frappe.frappes,
            ev.frappe.effacements,
            ev.frappe.mode if (ev.frappe.frappes or ev.frappe.blocs) else None,
            dumps(ev.data),
        ),
    ).lastrowid
    con.commit()
    return eid


def ajouter_notation(
    con: sqlite3.Connection,
    evenement_id: int,
    correcteur: str,
    *,
    note: int | None,
    precision: int | None = None,
    sens_vise_id: int | None = None,
    erreur: str | None = None,
    mot_reellement_defini: str | None = None,
    confiance: float | None = None,
    brut: dict | None = None,
) -> int:
    nid = con.execute(
        """INSERT INTO notation(evenement_id, correcteur, ts, note, precision, sens_vise_id, erreur,
               mot_reellement_defini, confiance, brut)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            evenement_id,
            correcteur,
            iso(),
            note,
            precision,
            sens_vise_id,
            erreur,
            mot_reellement_defini,
            confiance,
            dumps(brut or {}),
        ),
    ).lastrowid
    con.commit()
    return nid


def ajouter_mnemo(
    con: sqlite3.Connection,
    entree_id: int,
    type: str,
    contenu: str,
    *,
    sens_id: int | None = None,
    fichier: str | None = None,
    source: str = "manuel",
) -> int:
    mid = con.execute(
        "INSERT INTO mnemo(entree_id, sens_id, type, contenu, fichier, source, ts) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (entree_id, sens_id, type, contenu, fichier, source, iso()),
    ).lastrowid
    con.commit()
    return mid


# Priorité des correcteurs : la note la plus fiable l'emporte pour FSRS.
# « soi » = ton auto-évaluation après révélation (façon Anki) ; elle passe après les
# correcteurs externes, et l'écart soi ↔ correcteur est lui-même une mesure (métacognition).
PRIORITE_CORRECTEUR = ("humain", "jev", "claude", "local", "soi", "embedding")


def rang_correcteur(nom: str) -> int:
    famille = nom.split(":", 1)[0]
    return PRIORITE_CORRECTEUR.index(famille) if famille in PRIORITE_CORRECTEUR else len(PRIORITE_CORRECTEUR)
