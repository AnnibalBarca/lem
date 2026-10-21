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


class CarteConnaissance:
    """Vue en mémoire de la carte, mise à jour en direct pendant une session."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
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

    # -- dimensions -------------------------------------------------------
    def dimensions(self, entree_id: int) -> list[str]:
        if entree_id in self._dims:
            return self._dims[entree_id]
        e = self.con.execute("SELECT zipf, origine, type FROM entree WHERE id = ?", (entree_id,)).fetchone()
        if e is None:
            return []
        dims = ["global", bande_zipf(e["zipf"]), f"type:{e['type']}"]
        if e["origine"]:
            dims.append(f"origine:{e['origine']}")
        for r in self.con.execute(
            """SELECT DISTINCT d.nom FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
               JOIN domaine d ON d.id = sd.domaine_id WHERE s.entree_id = ?""",
            (entree_id,),
        ):
            dims.append(f"domaine:{r['nom']}")
        for r in self.con.execute(
            "SELECT m.forme FROM entree_morpheme em JOIN morpheme m ON m.id = em.morpheme_id WHERE em.entree_id = ?",
            (entree_id,),
        ):
            dims.append(f"morph:{r['forme']}")
        self._dims[entree_id] = dims
        return dims

    # -- lecture ----------------------------------------------------------
    def p(self, entree_id: int) -> float:
        prior = self._prior.get(entree_id, 0.5)
        x = logit(prior) + sum(self.theta.get(d, 0.0) for d in self.dimensions(entree_id))
        x += self._residu.get(entree_id, 0.0)
        return 1.0 / (1.0 + math.exp(-x))

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
