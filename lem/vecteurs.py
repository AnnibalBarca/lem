"""Embeddings (bge-m3 via Ollama) et magasin de vecteurs en mémoire.

Ordre de grandeur sur un Mac M4 (16 Go) :
  * 160 k entrées + ~300 k sens × 1024 dim × float16 ≈ 0,9 Go sur disque ;
  * en RAM on charge en float16 et on calcule en float32 par blocs ;
  * une requête « plus proches voisins » sur 300 k vecteurs : quelques dizaines de ms.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

MODELE_DEFAUT = "bge-m3"


class Embedder(Protocol):
    nom: str
    semantique: bool

    def embed(self, textes: Sequence[str]) -> np.ndarray: ...


def _normaliser(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


def url_ollama() -> str:
    """OLLAMA_HOST (même variable que le client ollama), sinon localhost.

    Sous WSL2 avec Ollama installé côté Windows : soit le mode réseau « mirrored »
    (localhost marche), soit OLLAMA_HOST=http://<IP de l'hôte Windows>:11434.
    """
    hote = os.environ.get("OLLAMA_HOST", "").strip() or "localhost:11434"
    if hote.startswith("0.0.0.0"):
        hote = hote.replace("0.0.0.0", "localhost", 1)
    return hote if "://" in hote else f"http://{hote}"


class OllamaEmbedder:
    """bge-m3 servi par Ollama (`ollama pull bge-m3`)."""

    semantique = True

    def __init__(self, modele: str = MODELE_DEFAUT, url: str | None = None):
        self.nom = modele
        self.url = (url or url_ollama()).rstrip("/")

    def embed(self, textes: Sequence[str]) -> np.ndarray:
        import httpx

        r = httpx.post(f"{self.url}/api/embed", json={"model": self.nom, "input": list(textes)}, timeout=120)
        r.raise_for_status()
        return _normaliser(np.asarray(r.json()["embeddings"], dtype=np.float32))

    def disponible(self) -> bool:
        try:
            self.embed(["test"])
            return True
        except Exception:
            return False


class HashEmbedder:
    """Repli hors ligne : n-grammes de caractères hachés. Capte la FORME, pas le sens.

    Sert aux tests et à la démo sans Ollama. `semantique = False` : les jeux le savent
    et n'affichent pas d'étiquette « similaire par le sens » fondée sur lui.
    """

    nom = "hash-ngram"
    semantique = False

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, textes: Sequence[str]) -> np.ndarray:
        m = np.zeros((len(textes), self.dim), dtype=np.float32)
        for i, t in enumerate(textes):
            t = f"  {t.lower()}  "
            for j in range(len(t) - 2):
                h = int.from_bytes(hashlib.blake2b(t[j : j + 3].encode(), digest_size=4).digest(), "big")
                m[i, h % self.dim] += 1.0
        return _normaliser(m)


def embedder_par_defaut() -> Embedder:
    o = OllamaEmbedder()
    return o if o.disponible() else HashEmbedder()


def texte_entree(forme: str, definition: str | None) -> str:
    return f"{forme} : {definition}" if definition else forme


def indexer(con: sqlite3.Connection, embedder: Embedder, objet: str = "sens", lot: int = 64,
            progression=None) -> int:
    """Calcule les vecteurs manquants pour 'sens' ou 'entree'. Renvoie le nombre ajouté."""
    if objet == "sens":
        rows = con.execute(
            """SELECT s.id, e.forme, COALESCE(s.definition_precise, s.definition) AS d FROM sens s
               JOIN entree e ON e.id = s.entree_id
               WHERE s.id NOT IN (SELECT objet_id FROM embedding WHERE objet = 'sens' AND modele = ?)""",
            (embedder.nom,),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT e.id, e.forme, (SELECT COALESCE(definition_precise, definition) FROM sens
                   WHERE entree_id = e.id ORDER BY rang LIMIT 1) AS d FROM entree e
               WHERE e.id NOT IN (SELECT objet_id FROM embedding WHERE objet = 'entree' AND modele = ?)""",
            (embedder.nom,),
        ).fetchall()
    total = 0
    for i in range(0, len(rows), lot):
        bloc = rows[i : i + lot]
        vecs = embedder.embed([texte_entree(r["forme"], r["d"]) for r in bloc])
        con.executemany(
            "INSERT OR REPLACE INTO embedding(objet, objet_id, modele, vecteur) VALUES(?, ?, ?, ?)",
            ((objet, r[0], embedder.nom, v.astype(np.float16).tobytes()) for r, v in zip(bloc, vecs)),
        )
        con.commit()
        total += len(bloc)
        if progression:
            progression(total, len(rows))
    return total


@dataclass
class Voisin:
    id: int
    sim: float


class Magasin:
    """Matrice de vecteurs normalisés pour un type d'objet et un modèle."""

    def __init__(self, con: sqlite3.Connection, objet: str, modele: str):
        self.objet, self.modele = objet, modele
        rows = con.execute(
            "SELECT objet_id, vecteur FROM embedding WHERE objet = ? AND modele = ? ORDER BY objet_id",
            (objet, modele),
        ).fetchall()
        self.ids = np.array([r[0] for r in rows], dtype=np.int64)
        self.pos = {int(i): k for k, i in enumerate(self.ids)}
        if rows:
            self.m = np.vstack([np.frombuffer(r[1], dtype=np.float16) for r in rows])
        else:
            self.m = np.zeros((0, 1), dtype=np.float16)

    def __len__(self) -> int:
        return len(self.ids)

    def vecteur(self, oid: int) -> np.ndarray | None:
        k = self.pos.get(oid)
        return None if k is None else self.m[k].astype(np.float32)

    def sim(self, a: int, b: int) -> float | None:
        va, vb = self.vecteur(a), self.vecteur(b)
        if va is None or vb is None:
            return None
        return float(va @ vb)

    def sims(self, v: np.ndarray, ids: Sequence[int]) -> dict[int, float]:
        ks = [self.pos[i] for i in ids if i in self.pos]
        if not ks:
            return {}
        s = self.m[ks].astype(np.float32) @ v.astype(np.float32)
        return {int(self.ids[k]): float(x) for k, x in zip(ks, s)}

    def voisins(self, v: np.ndarray, k: int = 10, exclure: set[int] | None = None,
                parmi: np.ndarray | None = None, bloc: int = 65536) -> list[Voisin]:
        if len(self) == 0:
            return []
        v = v.astype(np.float32)
        scores = np.empty(len(self), dtype=np.float32)
        for i in range(0, len(self), bloc):
            scores[i : i + bloc] = self.m[i : i + bloc].astype(np.float32) @ v
        if parmi is not None:
            masque = np.full(len(self), -np.inf, dtype=np.float32)
            idx = [self.pos[int(i)] for i in parmi if int(i) in self.pos]
            masque[idx] = 0
            scores = scores + masque
        exclure = exclure or set()
        n = min(len(self), k + len(exclure))
        top = np.argpartition(-scores, n - 1)[:n]
        top = top[np.argsort(-scores[top])]
        out = [Voisin(int(self.ids[t]), float(scores[t])) for t in top
               if int(self.ids[t]) not in exclure and np.isfinite(scores[t])]
        return out[:k]
