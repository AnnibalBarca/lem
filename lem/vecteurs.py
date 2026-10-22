"""Embeddings (bge-m3 via Ollama) et magasin de vecteurs en mémoire.

Ordre de grandeur sur un Mac M4 (16 Go) :
  * 160 k entrées + ~300 k sens × 1024 dim × float16 ≈ 0,9 Go sur disque ;
  * en RAM on garde du float32 (≈ 2 Go pour 530 k vecteurs) : un produit matriciel par requête ;
  * une requête « plus proches voisins » sur 300 k vecteurs : quelques dizaines de ms.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
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

        for essai in range(4):
            try:
                r = httpx.post(f"{self.url}/api/embed", json={"model": self.nom, "input": list(textes)}, timeout=120)
                r.raise_for_status()
                break
            except httpx.HTTPError:
                # Le processus d'Ollama peut redémarrer en cours de route (vu sous Windows) : on réessaie.
                if essai == 3:
                    raise
                time.sleep(2 ** essai)
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
            progression=None, zipf_min: float | None = None, rang_max: int | None = None) -> int:
    """Calcule les vecteurs manquants pour 'sens' ou 'entree'. Renvoie le nombre ajouté.

    zipf_min / rang_max restreignent l'indexation (entrées attestées, sens principaux) ; une indexation
    complète ultérieure ne calcule que ce qui manque."""
    filtre, params = "", [embedder.nom]
    if zipf_min is not None:
        filtre += " AND e.zipf > ?"
        params.append(zipf_min)
    if objet == "sens":
        if rang_max is not None:
            filtre += " AND s.rang <= ?"
            params.append(rang_max)
        rows = con.execute(
            f"""SELECT s.id, e.forme, COALESCE(s.definition_precise, s.definition) AS d FROM sens s
               JOIN entree e ON e.id = s.entree_id
               WHERE s.id NOT IN (SELECT objet_id FROM embedding WHERE objet = 'sens' AND modele = ?){filtre}""",
            params,
        ).fetchall()
    else:
        rows = con.execute(
            f"""SELECT e.id, e.forme, (SELECT COALESCE(definition_precise, definition) FROM sens
                   WHERE entree_id = e.id ORDER BY rang LIMIT 1) AS d FROM entree e
               WHERE e.id NOT IN (SELECT objet_id FROM embedding WHERE objet = 'entree' AND modele = ?){filtre}""",
            params,
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


DOSSIER_CACHE = Path(os.environ.get("LEM_HOME", Path.home() / ".lem")) / "cache"


def _chemin_base(con: sqlite3.Connection) -> str:
    """Chemin du fichier de la base ('' pour une base en mémoire)."""
    return next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "") or ""


class Magasin:
    """Vecteurs normalisés d'un type d'objet pour un modèle, avec recherche des plus proches voisins.

    Mode compact (défaut) : en RAM, seulement une empreinte d'un bit par dimension (128 octets par vecteur,
    ≈ 70 Mo pour 530 k vecteurs) ; les vecteurs exacts (float16) restent sur disque, projetés en mémoire
    (mmap) et lus à la demande. Une requête : distance de Hamming sur toutes les empreintes, puis calcul exact
    sur les meilleurs candidats. Les fichiers vivent dans ~/.lem/cache/<base>/ et sont refaits si les vecteurs
    de la base changent. LEM_VECTEURS=float32 garde tout en RAM (≈ 2 Go pour 530 k vecteurs, un peu plus rapide).
    """

    CANDIDATS = 256        # candidats recalculés exactement après le tri par empreinte

    def __init__(self, con: sqlite3.Connection, objet: str, modele: str):
        self.objet, self.modele = objet, modele
        n, mn, mx, somme = con.execute(
            "SELECT COUNT(*), MIN(objet_id), MAX(objet_id), TOTAL(objet_id) FROM embedding WHERE objet = ? AND modele = ?",
            (objet, modele)).fetchone()
        self.complet = os.environ.get("LEM_VECTEURS", "").lower() == "float32"
        base = _chemin_base(con)
        if n == 0:
            self.ids, self.m, self.bits = np.zeros(0, np.int64), np.zeros((0, 1), np.float32), np.zeros((0, 1), np.uint64)
        elif not base:                                   # base en mémoire (tests) : tout en RAM
            self.ids, m = self._lire(con)
            self.m, self.bits = m.astype(np.float32), self._empreintes(m)
        else:
            sig = hashlib.blake2b(f"{n}|{mn}|{mx}|{somme}".encode(), digest_size=6).hexdigest()
            dossier = DOSSIER_CACHE / hashlib.blake2b(os.path.realpath(base).encode(), digest_size=4).hexdigest()
            racine = dossier / f"{objet}-{modele.replace(':', '_')}-{sig}"
            if not (racine.with_suffix(".bits.npy")).exists():
                self._construire(con, racine, n)
            self.ids = np.load(racine.with_suffix(".ids.npy"))
            self.bits = np.load(racine.with_suffix(".bits.npy"))
            m = np.load(racine.with_suffix(".f16.npy"), mmap_mode="r")
            self.m = np.asarray(m, dtype=np.float32) if self.complet else m
        self.pos = {int(i): k for k, i in enumerate(self.ids)}

    # -- construction ------------------------------------------------------------------------------
    def _lire(self, con: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray]:
        rows = con.execute("SELECT objet_id, vecteur FROM embedding WHERE objet = ? AND modele = ? ORDER BY objet_id",
                           (self.objet, self.modele)).fetchall()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        return ids, np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1)

    @staticmethod
    def _empreintes(m: np.ndarray) -> np.ndarray:
        return np.packbits(np.asarray(m) > 0, axis=1).view(np.uint64)

    def _construire(self, con: sqlite3.Connection, racine: Path, n: int, lot: int = 20000) -> None:
        """Copie les vecteurs de la base vers le cache, par lots (jamais tout en RAM)."""
        racine.parent.mkdir(parents=True, exist_ok=True)
        for ancien in racine.parent.glob(f"{self.objet}-{self.modele.replace(':', '_')}-*"):
            ancien.unlink()                              # une seule version du cache par objet et modèle
        cur = con.execute("SELECT objet_id, vecteur FROM embedding WHERE objet = ? AND modele = ? ORDER BY objet_id",
                          (self.objet, self.modele))
        premier = cur.fetchone()
        dim = len(premier[1]) // 2
        tmp = racine.with_suffix(".f16.tmp.npy")
        m = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.float16, shape=(n, dim))
        ids = np.empty(n, dtype=np.int64)
        bits = np.empty((n, dim // 64), dtype=np.uint64)
        k, bloc = 0, [premier]
        while bloc:
            bloc += cur.fetchmany(lot - len(bloc))
            v = np.frombuffer(b"".join(r[1] for r in bloc), dtype=np.float16).reshape(len(bloc), dim)
            m[k:k + len(bloc)] = v
            ids[k:k + len(bloc)] = [r[0] for r in bloc]
            bits[k:k + len(bloc)] = self._empreintes(v)
            k += len(bloc)
            bloc = cur.fetchmany(lot)
        m.flush()
        del m
        tmp.rename(racine.with_suffix(".f16.npy"))
        np.save(racine.with_suffix(".ids.npy"), ids)
        np.save(racine.with_suffix(".bits.npy"), bits)        # écrit en dernier : il marque un cache complet

    # -- lecture -----------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.ids)

    def _lignes(self, ks) -> np.ndarray:
        ks = np.asarray(ks, dtype=np.int64)
        if not len(ks):
            return np.zeros((0, self.m.shape[1]), dtype=np.float32)
        ordre = np.argsort(ks)                           # lecture du disque dans l'ordre
        out = np.empty((len(ks), self.m.shape[1]), dtype=np.float32)
        out[ordre] = self.m[ks[ordre]]
        return out

    def vecteur(self, oid: int) -> np.ndarray | None:
        k = self.pos.get(oid)
        return None if k is None else np.asarray(self.m[k], dtype=np.float32)

    def sim(self, a: int, b: int) -> float | None:
        va, vb = self.vecteur(a), self.vecteur(b)
        if va is None or vb is None:
            return None
        return float(va @ vb)

    def sims(self, v: np.ndarray, ids: Sequence[int]) -> dict[int, float]:
        ks = [self.pos[i] for i in ids if i in self.pos]
        if not ks:
            return {}
        s = self._lignes(ks) @ v.astype(np.float32)
        return {int(self.ids[k]): float(x) for k, x in zip(ks, s)}

    def voisins(self, v: np.ndarray, k: int = 10, exclure: set[int] | None = None,
                parmi: np.ndarray | None = None) -> list[Voisin]:
        if len(self) == 0:
            return []
        v = v.astype(np.float32)
        exclure = exclure or set()
        if parmi is not None:
            ks = np.array([self.pos[int(i)] for i in parmi if int(i) in self.pos], dtype=np.int64)
        elif self.complet or isinstance(self.m, np.ndarray) and not isinstance(self.m, np.memmap):
            ks = np.arange(len(self))
        else:
            # 1. Empreintes : distance de Hamming à la requête, sur tous les vecteurs (≈ 10 ms pour 300 k).
            q = self._empreintes(v[None, :])[0]
            ham = np.bitwise_count(self.bits ^ q).sum(axis=1, dtype=np.int32)
            c = min(len(self), max(self.CANDIDATS, 8 * (k + len(exclure))))
            ks = np.argpartition(ham, c - 1)[:c]
        # 2. Calcul exact sur les candidats.
        scores = (self.m @ v if len(ks) == len(self) and not isinstance(self.m, np.memmap) else self._lignes(ks) @ v)
        n = min(len(ks), k + len(exclure))
        top = np.argpartition(-scores, n - 1)[:n]
        top = top[np.argsort(-scores[top])]
        out = [Voisin(int(self.ids[ks[t]]), float(scores[t])) for t in top if int(self.ids[ks[t]]) not in exclure]
        return out[:k]
