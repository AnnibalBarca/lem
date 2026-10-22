"""Accès SQLite : connexion, schéma, utilitaires communs."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from lem.texte import normaliser

DEFAULT_DB = Path(os.environ.get("LEM_HOME", Path.home() / ".lem")) / "lem.sqlite"


_horloge = None


def now() -> datetime:
    """L'heure du moteur. Réglable (`regler_horloge`) pour simuler des semaines de jeu en quelques secondes."""
    return _horloge() if _horloge is not None else datetime.now(timezone.utc)


def regler_horloge(fn=None) -> None:
    """fn() → datetime aware ; None rend l'horloge réelle."""
    global _horloge
    _horloge = fn


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="milliseconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Ouvre (et crée si besoin) la base. ':memory:' est accepté pour les tests."""
    path = str(path or DEFAULT_DB)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Les écrans de jeu calculent dans un fil à part (tui.app.EcranPartie.en_fond), un seul à la fois.
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")   # l'optimiseur écrit en fond pendant qu'on joue
    if path != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    init_schema(con)
    return con


# Colonnes ajoutées après coup : (table, colonne, type). Appliquées aux bases existantes.
MIGRATIONS = [("evenement", "mode_saisie", "TEXT"), ("trace_etat", "statut", "TEXT"),
              ("observation", "propager", "INTEGER NOT NULL DEFAULT 1")]


def init_schema(con: sqlite3.Connection) -> None:
    sql = resources.files("lem.db").joinpath("schema.sql").read_text(encoding="utf-8")
    con.executescript(sql)
    for table, colonne, type_ in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if colonne not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {type_}")
    con.commit()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def get_reglage(con: sqlite3.Connection, cle: str, defaut: Any = None) -> Any:
    row = con.execute("SELECT valeur FROM reglage WHERE cle = ?", (cle,)).fetchone()
    return json.loads(row["valeur"]) if row else defaut


def set_reglage(con: sqlite3.Connection, cle: str, valeur: Any) -> None:
    con.execute(
        "INSERT INTO reglage(cle, valeur) VALUES(?, ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, dumps(valeur)),
    )


def upsert_entree(
    con: sqlite3.Connection,
    forme: str,
    pos: str = "",
    *,
    langue: str = "fr",
    type: str = "mot",
    **champs: Any,
) -> int:
    """Insère une entrée ou complète les champs vides d'une entrée existante. Renvoie son id."""
    row = con.execute(
        "SELECT id FROM entree WHERE forme = ? AND langue = ? AND pos = ?", (forme, langue, pos)
    ).fetchone()
    champs = {k: v for k, v in champs.items() if v not in (None, "")}
    if row:
        eid = row["id"]
        for k, v in champs.items():
            con.execute(f"UPDATE entree SET {k} = COALESCE({k}, ?) WHERE id = ?", (v, eid))
        return eid
    cols = ["forme", "langue", "pos", "type", *champs]
    cur = con.execute(
        f"INSERT INTO entree({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
        (forme, langue, pos, type, *champs.values()),
    )
    con.execute(
        "INSERT OR IGNORE INTO forme(forme, norme, entree_id) VALUES(?, ?, ?)",
        (forme.lower(), normaliser(forme), cur.lastrowid),
    )
    return cur.lastrowid


def ajouter_formes(con: sqlite3.Connection, entree_id: int, formes: Iterable[str]) -> None:
    con.executemany(
        "INSERT OR IGNORE INTO forme(forme, norme, entree_id) VALUES(?, ?, ?)",
        ((f.lower(), normaliser(f), entree_id) for f in formes if f),
    )


def domaine_id(con: sqlite3.Connection, nom: str, parent: str | None = None) -> int:
    nom = nom.strip().lower()
    row = con.execute("SELECT id FROM domaine WHERE nom = ?", (nom,)).fetchone()
    if row:
        return row["id"]
    pid = domaine_id(con, parent) if parent else None
    return con.execute("INSERT INTO domaine(nom, parent_id) VALUES(?, ?)", (nom, pid)).lastrowid


def morpheme_id(con: sqlite3.Connection, forme: str, glose: str = "", origine: str | None = None) -> int:
    row = con.execute(
        "SELECT id FROM morpheme WHERE forme = ? AND glose = ?", (forme, glose)
    ).fetchone()
    if row:
        return row["id"]
    return con.execute(
        "INSERT INTO morpheme(forme, glose, origine) VALUES(?, ?, ?)", (forme, glose, origine)
    ).lastrowid


def ajouter_relation(
    con: sqlite3.Connection, a: int, b: int, type: str, poids: float = 1.0, origine: str = ""
) -> None:
    con.execute(
        "INSERT INTO relation(source_id, cible_id, type, poids, origine) VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(source_id, cible_id, type) DO UPDATE SET poids = excluded.poids",
        (a, b, type, poids, origine),
    )


def domaines_descendants(con: sqlite3.Connection, noms: Iterable[str]) -> list[int]:
    """Ids des domaines nommés et de tous leurs sous-domaines (« science » inclut « métallurgie »)."""
    noms = [n.strip().lower() for n in noms]
    if not noms:
        return []
    rows = con.execute(
        f"""
        WITH RECURSIVE d(id) AS (
            SELECT id FROM domaine WHERE nom IN ({', '.join('?' * len(noms))})
            UNION SELECT domaine.id FROM domaine JOIN d ON domaine.parent_id = d.id
        ) SELECT id FROM d
        """,
        noms,
    ).fetchall()
    return [r["id"] for r in rows]
