"""Profil d'un mot : six mémoires, une seule fiche.

Un mot n'est pas « su » d'un bloc. Six mémoires distinctes, chacune nourrie par les jeux qui la mesurent :

  connu      le reconnais-tu ?                     carte de connaissance P(connu) : sonde des Cartes, Bombe,
                                                   Syllabaire, Étalonnage, Mot ou pas mot
  definit    sais-tu dire ce qu'il veut dire ?     FSRS « recep » (Cartes, mot → sens, jugé)
  produit    le retrouves-tu à partir du sens ?    FSRS « prod » (Cartes, sens → mot)
  graphie    l'écris-tu juste ?                    trace « ortho » (fautes, dans tous les jeux) ; sans faute,
                                                   juste s'il a déjà été tapé au clavier
  trouvable  le sors-tu à partir de lettres, vite ?  Bombe et Syllabaire : nombre de fois, temps médian
  mnemo      l'as-tu encodé ?                      Mémoire verbale : reconnu quand il repasse ; tes images

Elles sont corrélées sans se confondre : on peut sortir « thuriféraire » à la Bombe sans savoir le définir,
et l'inverse. Une seule valeur cacherait ce qui manque ; six valeurs disent quoi travailler. Chacune vaut de 0
à 1 (pour les mémoires FSRS, la probabilité de réussir aujourd'hui), ou None quand aucun jeu ne l'a encore
mesurée : on ne devine pas.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass, field

from fsrs import Card

from lem.db import now

MEMOIRES = ("connu", "definit", "produit", "graphie", "trouvable", "mnemo")
LIBELLES = {"connu": "reconnu", "definit": "défini", "produit": "retrouvé", "graphie": "écrit juste",
            "trouvable": "trouvé vite", "mnemo": "encodé"}
LATENCE_REFERENCE_MS = 5000      # trouvable : un mot sorti en 5 s ou moins compte comme rapide


@dataclass
class Profil:
    entree_id: int
    forme: str
    connu: float | None = None
    definit: float | None = None
    produit: float | None = None
    graphie: float | None = None
    trouvable: float | None = None
    mnemo: float | None = None
    detail: dict = field(default_factory=dict)

    def valeurs(self) -> dict[str, float | None]:
        return {m: getattr(self, m) for m in MEMOIRES}


def _ids_clause(ids) -> tuple[str, list]:
    if ids is None:
        return "", []
    ids = list(ids)
    return f" AND entree_id IN ({', '.join('?' * len(ids))})", ids


def score_trouvable(n: int, mediane_ms: float | None) -> float | None:
    """Plus tu l'as sorti souvent, et vite, plus il est trouvable."""
    if not n:
        return None
    vitesse = min(1.0, LATENCE_REFERENCE_MS / max(1.0, mediane_ms or LATENCE_REFERENCE_MS))
    return (1 - math.exp(-n / 2)) * vitesse


def profils(con: sqlite3.Connection, ids=None) -> dict[int, Profil]:
    """Les profils des mots `ids` (tous les mots qu'un jeu a mesurés, si None)."""
    from lem.memory.scheduler import _Ordonnanceurs

    filtre, args = _ids_clause(ids)
    quand = now()
    out: dict[int, Profil] = {}

    def p(eid: int) -> Profil:
        if eid not in out:
            out[eid] = Profil(eid, "")
        return out[eid]

    # FSRS : défini, retrouvé, graphie (la meilleure trace du mot, parmi ses sens).
    scheds = _Ordonnanceurs(con)
    for r in con.execute(f"SELECT entree_id, trace, card_json, statut FROM trace_etat WHERE 1 {filtre}", args):
        champ = {"recep": "definit", "prod": "produit", "ortho": "graphie"}.get(r["trace"])
        if champ is None:
            continue
        rr = scheds[r["trace"]].get_card_retrievability(Card.from_json(r["card_json"]), quand)
        x = p(r["entree_id"])
        setattr(x, champ, max(rr, getattr(x, champ) or 0.0))
        if r["statut"]:
            x.detail["statut"] = r["statut"]
    # Graphie sans faute : juste, s'il a été tapé au clavier sans faute.
    for r in con.execute(
            f"""SELECT DISTINCT entree_id FROM evenement WHERE type = 'reponse' AND correct = 1 AND trace = 'prod'
                AND COALESCE(mode_saisie, 'clavier') = 'clavier' AND entree_id IS NOT NULL {filtre}""", args):
        x = p(r[0])
        if x.graphie is None:
            x.graphie = 1.0
    # Trouvable : Bombe et Syllabaire.
    lat: dict[int, list[int]] = {}
    for r in con.execute(
            f"""SELECT entree_id, latence_ms FROM evenement WHERE jeu IN ('bombe', 'syllabaire') AND type = 'reponse'
                AND correct = 1 AND trace = 'prod' AND entree_id IS NOT NULL {filtre}""", args):
        lat.setdefault(r[0], []).append(r[1])
    for eid, ls in lat.items():
        vals = [v for v in ls if v is not None]
        med = statistics.median(vals) if vals else None
        x = p(eid)
        x.trouvable = score_trouvable(len(ls), med)
        x.detail["trouve"] = (len(ls), med)
    # Encodé : Mémoire verbale, quand le mot repasse ; et tes images.
    for r in con.execute(
            f"""SELECT entree_id, SUM(correct), COUNT(*) FROM evenement WHERE jeu = 'memoire' AND type = 'reponse'
                AND json_extract(data, '$.deja_vu') = 1 {filtre} GROUP BY entree_id""", args):
        x = p(r[0])
        x.mnemo = (r[1] + 1) / (r[2] + 2)                  # lissé : une seule reconnaissance ne vaut pas 100 %
        x.detail["reconnu"] = (r[1], r[2])
    for r in con.execute(f"SELECT entree_id, COUNT(*) FROM mnemo WHERE 1 {filtre} GROUP BY entree_id", args):
        p(r[0]).detail["images"] = r[1]
    # Connu : la carte, pour les mots déjà dans un profil (ou demandés).
    cles = list(out) if ids is None else list(ids)
    for a in range(0, len(cles), 900):
        lot = cles[a:a + 900]
        q = ", ".join("?" * len(lot))
        for r in con.execute(f"SELECT entree_id, p_connu FROM connaissance WHERE entree_id IN ({q})", lot):
            p(r[0]).connu = r[1]
        for r in con.execute(f"SELECT id, forme FROM entree WHERE id IN ({q})", lot):
            p(r[0]).forme = r[1]
    return out


def profil(con: sqlite3.Connection, entree_id: int) -> Profil:
    return profils(con, [entree_id]).get(entree_id) or Profil(entree_id, "")


# Les écarts qui disent quoi travailler : (titre, condition, tri).
ECARTS = (
    ("reconnus, pas définis", lambda x: (x.connu or 0) >= 0.8 and x.definit is not None and x.definit < 0.5,
     lambda x: x.definit),
    ("définis, pas retrouvés", lambda x: (x.definit or 0) >= 0.7 and x.produit is not None and x.produit < 0.5,
     lambda x: x.produit),
    ("lents à trouver", lambda x: (x.detail.get("trouve", (0, None))[1] or 0) > LATENCE_REFERENCE_MS,
     lambda x: -x.detail["trouve"][1]),
    ("mal écrits", lambda x: x.graphie is not None and x.graphie < 0.7, lambda x: x.graphie),
    ("mal encodés", lambda x: x.mnemo is not None and x.mnemo < 0.5, lambda x: x.mnemo),
)


def ecarts(ps: dict[int, Profil], n: int = 8) -> list[tuple[str, list[Profil]]]:
    out = []
    for titre, cond, tri in ECARTS:
        mots = sorted((x for x in ps.values() if cond(x)), key=tri)
        if mots:
            out.append((titre, mots[:n]))
    return out


def bande(p: Profil) -> "Text":
    """Une ligne : les six mémoires d'un mot, chacune en barre courte (pour /mot et lem mot)."""
    from rich.text import Text

    from lem.tui.theme import BLANC, ETATS, GRIS, SOMBRE

    t = Text(f"{p.forme}  ", style=f"bold {BLANC}")
    for m in MEMOIRES:
        v = getattr(p, m)
        t.append(f"{LIBELLES[m]} ", style=GRIS)
        if v is None:
            t.append("—   ", style=SOMBRE)
        else:
            coul = ETATS["acquis"][0] if v >= 0.8 else ETATS["flou"][0] if v >= 0.5 else ETATS["inconnu"][0]
            t.append(f"{v:.0%}   ", style=coul)
    return t
