"""Carte de connaissance : P(je connais ce mot) pour TOUT le lexique.

Modèle de type Rasch / Elo, entièrement déterministe et explicable :

    logit P(connu | mot) = logit P_prior(mot) + Σ θ_d  (d ∈ dimensions(mot)) + résidu(mot)

* P_prior : « adulte cultivé moyen ». Une sigmoïde de la fréquence Zipf dont le
  seuil est calibré pour que Σ P_prior ≈ `cible_connus` lemmes (45 000 par défaut).
* θ_d : TES écarts à cet adulte moyen, par dimension : bande de fréquence,
  origine (grec, latin…), type (expression, nom propre…), domaine, morphème.
  Rater « acronyme » baisse θ(morph:-onyme) → « paronyme », « éponyme »
  baissent sans avoir été testés. C'est la propagation.
* résidu : ce qui est propre au mot lui-même, une fois qu'il a été observé.

Mise à jour en ligne façon Elo : θ_d += lr_d · (y − p), avec un pas qui décroît
avec le nombre d'observations. Tout est rejouable depuis le journal.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np

from lem.db import get_reglage, iso, set_reglage

# Pas d'apprentissage de base par famille de dimension, et constante de décroissance.
LR = {"global": 0.03, "zipf": 0.08, "origine": 0.15, "type": 0.15, "domaine": 0.2, "morph": 0.3}
LR_RESIDU = 1.2
TAU = 40.0
THETA_MAX = 4.0


@dataclass
class ParamsPrior:
    cible_connus: float = 45_000.0   # lemmes reconnus par un adulte cultivé (ordre de grandeur, Brysbaert 2016)
    pente: float = 1.6               # logit par unité Zipf
    zipf_absent: float = 0.6         # Zipf attribué aux mots absents des corpus (turiféraire…)
    seuil: float | None = None       # z0, calculé par calibrer()


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def bande_zipf(z: float | None) -> str:
    z = 0.0 if z is None else z
    return f"zipf:{min(int(z), 5)}"


def calibrer_seuil(zipfs: np.ndarray, cible: float, pente: float) -> float:
    """Trouve z0 tel que Σ σ(pente·(z − z0)) = cible (bissection, monotone en z0)."""
    cible = min(cible, len(zipfs) * 0.98)
    lo, hi = -10.0, 15.0
    for _ in range(80):
        mid = (lo + hi) / 2
        total = float(sigmoid(pente * (zipfs - mid)).sum())
        if total > cible:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def calibrer(con: sqlite3.Connection, params: ParamsPrior | None = None) -> ParamsPrior:
    """Calcule le prior « adulte cultivé » et (ré)initialise la table connaissance."""
    params = params or ParamsPrior(**get_reglage(con, "prior", {}))
    rows = con.execute(
        "SELECT id, zipf FROM entree WHERE langue = 'fr' AND type NOT IN ('nom_propre')"
    ).fetchall()
    if not rows:
        return params
    z = np.array([params.zipf_absent if not r["zipf"] else r["zipf"] for r in rows], dtype=float)
    params.seuil = calibrer_seuil(z, params.cible_connus, params.pente)
    set_reglage(con, "prior", asdict(params))

    # Les noms propres ne comptent pas dans la cible mais reçoivent un prior par la même courbe.
    tous = con.execute("SELECT id, zipf FROM entree").fetchall()
    maj = iso()
    con.executemany(
        """INSERT INTO connaissance(entree_id, p_prior, p_connu, maj) VALUES(?, ?, ?, ?)
           ON CONFLICT(entree_id) DO UPDATE SET p_prior = excluded.p_prior""",
        (
            (r["id"], p, p, maj)
            for r in tous
            for p in [float(sigmoid(params.pente * ((r["zipf"] or params.zipf_absent) - params.seuil)))]
        ),
    )
    con.commit()
    return params


SQL_DOMAINES = """SELECT DISTINCT s.entree_id, d.nom FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
                  JOIN domaine d ON d.id = sd.domaine_id"""
SQL_MORPHS = "SELECT em.entree_id, m.forme FROM entree_morpheme em JOIN morpheme m ON m.id = em.morpheme_id"


def _dimensions(zipf, origine, type_, domaines, morphs) -> list[str]:
    dims = ["global", bande_zipf(zipf), f"type:{type_}"]
    if origine:
        dims.append(f"origine:{origine}")
    dims += [f"domaine:{d}" for d in domaines]
    dims += [f"morph:{m}" for m in morphs]
    return dims


class CarteConnaissance:
    """Vue en mémoire de la carte, mise à jour en direct pendant une session."""

    def __init__(self, con: sqlite3.Connection, lexique=None):
        self.con = con
        self.lexique = lexique          # lem.lexique.Lexique : dimensions déjà en tableaux (chargement rapide)
        self.theta: dict[str, float] = {}
        self.n_theta: dict[str, int] = defaultdict(int)
        for r in con.execute("SELECT dimension, valeur, n_obs FROM theta"):
            self.theta[r["dimension"]] = r["valeur"]
            self.n_theta[r["dimension"]] = r["n_obs"]
        self._dims: dict[int, list[str]] = {}
        self._prior: dict[int, float] = {}
        self._residu: dict[int, float] = {}
        self._nobs: dict[int, int] = {}
        for r in con.execute("SELECT entree_id, p_prior, residu, n_obs FROM connaissance"):
            self._prior[r["entree_id"]] = r["p_prior"]
            self._residu[r["entree_id"]] = r["residu"]
            self._nobs[r["entree_id"]] = r["n_obs"]
        self._vec = None          # structure vectorielle (p_tous), chargée à la demande
        self._version = 0         # incrémentée à chaque changement de θ ou de résidu

    # -- dimensions -------------------------------------------------------
    def dimensions(self, entree_id: int) -> list[str]:
        if entree_id in self._dims:
            return self._dims[entree_id]
        e = self.con.execute("SELECT zipf, origine, type FROM entree WHERE id = ?", (entree_id,)).fetchone()
        if e is None:
            return []
        domaines = [r[1] for r in self.con.execute(SQL_DOMAINES + " WHERE s.entree_id = ?", (entree_id,))]
        morphs = [r[1] for r in self.con.execute(SQL_MORPHS + " WHERE em.entree_id = ?", (entree_id,))]
        dims = _dimensions(e["zipf"], e["origine"], e["type"], domaines, morphs)
        self._dims[entree_id] = dims
        return dims

    # -- lecture ----------------------------------------------------------
    def p(self, entree_id: int) -> float:
        prior = self._prior.get(entree_id, 0.5)
        x = logit(prior) + sum(self.theta.get(d, 0.0) for d in self.dimensions(entree_id))
        x += self._residu.get(entree_id, 0.0)
        return 1.0 / (1.0 + math.exp(-x))

    def p_tous(self) -> np.ndarray:
        """P(connu) de toutes les entrées de la carte d'un coup (même calcul que `p`), dans l'ordre de `ids`.

        Les dimensions de chaque entrée sont chargées une fois, en tableaux : colonnes catégorielles (bande
        de Zipf, type, origine) et liens (domaines, morphèmes). θ est relu à chaque changement. Sert aux jeux
        qui somment P sur des centaines de milliers de mots (K de la Bombe)."""
        v = self._vec
        if v is None:
            v = self._vec = self._charger_vec()
        if v["version"] != self._version:
            n = len(v["ids"])
            th = lambda noms: np.array([self.theta.get(d, 0.0) for d in noms], dtype=np.float64)  # noqa: E731
            x = v["logit_prior"] + np.where(v["connue"], self.theta.get("global", 0.0), 0.0)
            for cle, noms in (("zipf", [f"zipf:{b}" for b in range(6)]), ("type", v["types"]),
                              ("origine", v["origines"])):
                code = v[cle]
                ok = code >= 0
                x[ok] += th(noms)[code[ok]]
            x += np.bincount(v["lignes"], weights=th(v["noms"])[v["dims"]], minlength=n)
            x += v["residu"]
            v["p"], v["version"] = sigmoid(x), self._version
        return v["p"]

    def p_de(self, ids) -> list[float]:
        """P(connu) d'un lot d'entrées : `p_tous` pour celles de la carte, `p` pour les autres."""
        ids = list(ids)
        pos = self.positions(ids)
        p = self.p_tous()
        return [float(p[k]) if k >= 0 else self.p(i) for i, k in zip(ids, pos)]

    @property
    def ids(self) -> np.ndarray:
        if self._vec is None:
            self._vec = self._charger_vec()
        return self._vec["ids"]

    def positions(self, ids) -> np.ndarray:
        """Indices dans `p_tous()` des entrées données (−1 si absente de la carte)."""
        if self._vec is None:
            self._vec = self._charger_vec()
        pos = self._vec["pos"]
        return np.array([pos.get(int(i), -1) for i in ids], dtype=np.int64)

    def _charger_vec(self) -> dict:
        if self.lexique is not None:
            return self._charger_vec_lexique()
        ids = np.fromiter(self._prior, dtype=np.int64, count=len(self._prior))
        n = len(ids)
        pos = {int(i): k for k, i in enumerate(ids)}
        # Colonnes catégorielles : code −1 = pas de dimension (entrée inconnue, origine vide).
        connue = np.zeros(n, dtype=bool)
        zipf, types, origines = np.full(n, -1), np.full(n, -1), np.full(n, -1)
        noms_t: dict[str, int] = {}
        noms_o: dict[str, int] = {}
        for eid, z, origine, type_ in self.con.execute("SELECT id, zipf, origine, type FROM entree"):
            k = pos.get(eid)
            if k is None:
                continue
            connue[k] = True
            zipf[k] = int(bande_zipf(z)[5:])
            types[k] = noms_t.setdefault(f"type:{type_}", len(noms_t))
            if origine:
                origines[k] = noms_o.setdefault(f"origine:{origine}", len(noms_o))
        noms: dict[str, int] = {}
        lignes, dims = [], []
        for sql, prefixe in ((SQL_DOMAINES, "domaine:"), (SQL_MORPHS, "morph:")):
            for eid, nom in self.con.execute(sql):
                k = pos.get(eid)
                if k is not None and connue[k]:
                    lignes.append(k)
                    dims.append(noms.setdefault(prefixe + nom, len(noms)))
        prior = np.clip(np.array([self._prior[int(i)] for i in ids], dtype=np.float64), 1e-6, 1 - 1e-6)
        return {"ids": ids, "pos": pos, "connue": connue, "zipf": zipf, "types": list(noms_t), "type": types,
                "origines": list(noms_o), "origine": origines, "noms": list(noms),
                "lignes": np.array(lignes, dtype=np.int64), "dims": np.array(dims, dtype=np.int64),
                "logit_prior": np.log(prior / (1 - prior)), "version": -1, "p": None,
                "residu": np.fromiter((self._residu.get(int(i), 0.0) for i in ids), dtype=np.float64, count=n)}

    def _charger_vec_lexique(self) -> dict:
        """Même structure que _charger_vec, tirée de l'index du lexique (≈ 0,2 s au lieu de 3 s)."""
        lex = self.lexique
        ids = np.fromiter(self._prior, dtype=np.int64, count=len(self._prior))
        n = len(ids)
        pos = {int(i): k for k, i in enumerate(ids)}
        pl = np.searchsorted(lex.eids, ids)
        pl_ok = np.minimum(pl, len(lex.eids) - 1)
        connue = (pl < len(lex.eids)) & (lex.eids[pl_ok] == ids)
        zipf = np.where(connue, lex.zipf[pl_ok], -1).astype(np.int64)
        types = np.where(connue, lex.code_type[pl_ok], -1).astype(np.int64)
        origines = np.where(connue, lex.origine[pl_ok], -1).astype(np.int64)
        inv = np.full(len(lex.eids), -1, dtype=np.int64)       # position dans l'index → position dans la carte
        inv[pl[connue]] = np.nonzero(connue)[0]
        lig = inv[lex.d_lig]
        garde = lig >= 0
        prior = np.clip(np.array([self._prior[int(i)] for i in ids], dtype=np.float64), 1e-6, 1 - 1e-6)
        return {"ids": ids, "pos": pos, "connue": connue, "zipf": zipf,
                "types": [f"type:{t}" for t in lex.noms_types], "type": types,
                "origines": [f"origine:{o}" for o in lex.noms_origines], "origine": origines,
                "noms": lex.noms_dims, "lignes": lig[garde], "dims": lex.d_dim[garde].astype(np.int64),
                "logit_prior": np.log(prior / (1 - prior)), "version": -1, "p": None,
                "residu": np.fromiter((self._residu.get(int(i), 0.0) for i in ids), dtype=np.float64, count=n)}

    def n_obs(self, entree_id: int) -> int:
        return self._nobs.get(entree_id, 0)

    # -- écriture ---------------------------------------------------------
    def observer(self, entree_id: int, y: float, poids: float = 1.0, persister: bool = True) -> float:
        """Intègre une observation y ∈ [0, 1] (1 = connu). Renvoie le nouveau P."""
        p = self.p(entree_id)
        err = (y - p) * poids
        for d in self.dimensions(entree_id):
            famille = d.split(":", 1)[0]
            lr = LR.get(famille, 0.1) / (1.0 + self.n_theta[d] / TAU)
            self.theta[d] = max(-THETA_MAX, min(THETA_MAX, self.theta.get(d, 0.0) + lr * err))
            self.n_theta[d] += 1
        n = self._nobs.get(entree_id, 0)
        self._residu[entree_id] = self._residu.get(entree_id, 0.0) + LR_RESIDU / (1.0 + n / 3.0) * err
        self._nobs[entree_id] = n + 1
        self._version += 1
        if self._vec is not None and (k := self._vec["pos"].get(entree_id)) is not None:
            self._vec["residu"][k] = self._residu[entree_id]
        nouveau = self.p(entree_id)
        if persister:
            self._persister(entree_id, nouveau)
        return nouveau

    def _persister(self, entree_id: int, p: float) -> None:
        maj = iso()
        self.con.executemany(
            """INSERT INTO theta(dimension, valeur, n_obs, maj) VALUES(?, ?, ?, ?)
               ON CONFLICT(dimension) DO UPDATE SET valeur = excluded.valeur, n_obs = excluded.n_obs, maj = excluded.maj""",
            ((d, self.theta[d], self.n_theta[d], maj) for d in self.dimensions(entree_id)),
        )
        self.con.execute(
            """INSERT INTO connaissance(entree_id, p_prior, p_connu, residu, n_obs, maj) VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(entree_id) DO UPDATE SET p_connu = excluded.p_connu, residu = excluded.residu,
               n_obs = excluded.n_obs, maj = excluded.maj""",
            (entree_id, self._prior.get(entree_id, 0.5), p, self._residu[entree_id], self._nobs[entree_id], maj),
        )
        self.con.commit()

    def recalculer_tout(self) -> None:
        """Propage les θ à tout le lexique (fin de session) : p_connu de chaque entrée."""
        maj = iso()
        self.con.executemany(
            "UPDATE connaissance SET p_connu = ?, maj = ? WHERE entree_id = ?",
            ((self.p(eid), maj, eid) for eid in list(self._prior)),
        )
        self.con.commit()

    def remettre_a_zero(self) -> None:
        self.theta.clear()
        self.n_theta.clear()
        self._residu = {k: 0.0 for k in self._residu}
        self._nobs = {k: 0 for k in self._nobs}
        self._version += 1
        if self._vec is not None:
            self._vec["residu"][:] = 0.0
        self.con.execute("DELETE FROM theta")
        self.con.execute("UPDATE connaissance SET residu = 0, n_obs = 0, p_connu = p_prior")
        self.con.commit()


# Valeur d'une observation selon la note (1..4) — « vague » compte comme à moitié connu.
Y_PAR_NOTE = {1: 0.15, 2: 0.6, 3: 0.9, 4: 1.0}


def score_apprentissage(p: float, zipf: float | None, interet: float = 1.0, famille_inconnue: int = 0) -> float:
    """Priorité d'un mot nouveau : ce qui manque × ce qui sert × ce qui débloque.

    (1 − p)              : on n'apprend pas ce qu'on sait déjà.
    zone proximale       : les mots flous (p ~ 0.2–0.6, « turiféraire ») sont les moins chers à consolider.
    utilité              : fréquence et intérêt du domaine.
    levier               : nombre de mots inconnus partageant ses morphèmes.
    """
    z = 0.0 if zipf is None else zipf
    proximal = 0.5 + 2.0 * p * (1 - p)
    utilite = interet * (0.6 + z / 6.0)
    levier = 1.0 + 0.3 * math.log1p(famille_inconnue)
    return (1 - p) * proximal * utilite * levier
