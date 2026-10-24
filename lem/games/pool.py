"""Sélection des mots d'un jeu : chaque jeu interroge la base avec ses propres filtres.

États (lus dans la carte de connaissance et FSRS) :
  non_explores   : jamais observés
  inconnus       : P(connu) < 0,3
  flous          : 0,3 ≤ P < 0,75          (« turiféraire » : vu, jamais vraiment su)
  en_cours       : une trace FSRS existe
  presque_acquis : stabilité FSRS entre 3 et 30 jours, ou P ∈ [0,75 ; 0,9[ déjà observé
  connus         : P ≥ 0,9
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from lem.db import domaines_descendants
from lem.texte import normaliser

ETATS = ("tous", "non_explores", "inconnus", "flous", "en_cours", "presque_acquis", "connus")


@dataclass
class Filtres:
    domaines: list[str] = field(default_factory=list)
    origines: list[str] = field(default_factory=list)       # grec, latin, anglais…
    types: list[str] = field(default_factory=list)          # mot, expression, nom_propre…
    etat: str = "tous"
    zipf_min: float | None = None
    zipf_max: float | None = None
    longueur_min: int | None = None
    longueur_max: int | None = None
    locutions: bool = False          # expressions et entrées de plusieurs mots : écartées sauf demande
    secondaires: bool = False        # variantes graphiques et dérivés transparents (re-modeler) : écartés
    langue: str = "fr"

    def resume(self) -> str:
        morceaux = []
        if self.domaines:
            morceaux.append("domaine=" + ",".join(self.domaines))
        if self.origines:
            morceaux.append("origine=" + ",".join(self.origines))
        if self.types:
            morceaux.append("type=" + ",".join(self.types))
        if self.etat != "tous":
            morceaux.append(f"état={self.etat}")
        if self.zipf_min is not None or self.zipf_max is not None:
            morceaux.append(f"zipf={self.zipf_min or 0:g}–{self.zipf_max or 8:g}")
        return " · ".join(morceaux) or "tout le lexique"


def _clause_etat(etat: str) -> str:
    return {
        "tous": "1",
        "non_explores": "COALESCE(c.n_obs, 0) = 0",
        "inconnus": "COALESCE(c.p_connu, 0.5) < 0.3",
        "flous": "COALESCE(c.p_connu, 0.5) >= 0.3 AND COALESCE(c.p_connu, 0.5) < 0.75",
        "en_cours": "EXISTS (SELECT 1 FROM trace_etat t WHERE t.entree_id = e.id)",
        "presque_acquis": """(EXISTS (SELECT 1 FROM trace_etat t WHERE t.entree_id = e.id
                                 AND t.stabilite BETWEEN 3 AND 30)
                              OR (c.p_connu >= 0.75 AND c.p_connu < 0.9 AND c.n_obs > 0))""",
        "connus": "COALESCE(c.p_connu, 0) >= 0.9",
    }[etat]


# Entrées sans valeur d'apprentissage propre (constaté sur la vraie base : la moitié des sondes des Cartes) :
#   * celles dont tous les sens renvoient à une autre graphie (« Orthographe parfois rencontrée pour… ») ;
#   * les composés à trait d'union dont la forme soudée existe (re-modeler → remodeler) ;
#   * les « re- / ré- » + verbe existant (re-recopier, re-ébranler) : « X de nouveau », transparents.
DEFINITIONS_VARIANTES = ("Variante%", "Orthographe%", "Autre orthographe%", "Ancienne orthographe%",
                         "Ancienne forme%", "Forme alternative%", "Graphie%")
PREFIXES_ITERATIFS = ("re", "ré")
_secondaires: dict[tuple, set[int]] = {}


def secondaires(con: sqlite3.Connection) -> set[int]:
    """Les entrées écartées par défaut de la sélection des jeux (variantes, dérivés transparents)."""
    cle = (id(con), *con.execute("SELECT COUNT(*), MAX(id) FROM entree").fetchone(),
           con.execute("SELECT COUNT(*) FROM sens").fetchone()[0])
    if cle in _secondaires:
        return _secondaires[cle]
    motifs = " OR ".join("s.definition LIKE ?" for _ in DEFINITIONS_VARIANTES)
    ids = {r[0] for r in con.execute(
        f"""SELECT s.entree_id FROM sens s GROUP BY s.entree_id
            HAVING SUM(CASE WHEN {motifs} THEN 1 ELSE 0 END) = COUNT(*)""", DEFINITIONS_VARIANTES)}
    # The feminine and the plural are entries of their own in the Wiktionary (raëlienne, actrice, infirmière…): each
    # lists the other among its forms. The longer one is the secondary; in French the masculine singular is the
    # default (« Adepte du raëlisme. » → raëlien). Both stay valid answers in the Bomb: only the cards skip it.
    ids |= {r[0] for r in con.execute(
        """SELECT DISTINCT e.id FROM entree e
           JOIN forme fe ON fe.entree_id = e.id AND fe.forme != lower(e.forme)
           JOIN entree m ON m.forme = fe.forme AND m.id != e.id AND m.pos = e.pos AND m.langue = e.langue
           JOIN forme fm ON fm.entree_id = m.id AND fm.forme = lower(e.forme)
           WHERE e.langue = 'fr' AND length(m.forme) < length(e.forme) AND e.forme NOT GLOB '[A-Z]*'""")}
    normes = {normaliser(r[0]) for r in con.execute("SELECT forme FROM entree WHERE instr(forme, '-') = 0")}
    for eid, forme in con.execute("SELECT id, forme FROM entree WHERE instr(forme, '-') > 0 AND instr(forme, ' ') = 0"):
        tete, _, reste = forme.partition("-")
        n = normaliser(forme)
        if n.replace("-", "") in normes or (tete.lower() in PREFIXES_ITERATIFS and normaliser(reste) in normes):
            ids.add(eid)
    _secondaires.clear()
    _secondaires[cle] = ids
    return ids


def selectionner(con: sqlite3.Connection, f: Filtres, limite: int | None = None,
                 ordre: str = "e.id") -> list[sqlite3.Row]:
    """Renvoie des lignes (id, forme, zipf, type, origine, p_connu, n_obs)."""
    where, args = [f"e.langue = ?", _clause_etat(f.etat)], [f.langue]
    if f.domaines:
        dids = domaines_descendants(con, f.domaines)
        if not dids:
            return []
        where.append(
            f"""EXISTS (SELECT 1 FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
                WHERE s.entree_id = e.id AND sd.domaine_id IN ({', '.join('?' * len(dids))}))"""
        )
        args += dids
    if not f.locutions and "expression" not in f.types:
        where.append("e.type != 'expression' AND instr(e.forme, ' ') = 0")
    if "nom_propre" not in f.types:
        where.append("e.forme NOT GLOB '[A-Z]*'")      # majuscule initiale : nom, gentilé, langue — pas un mot du jeu
    for col, vals in (("e.origine", f.origines), ("e.type", f.types)):
        if vals:
            where.append(f"{col} IN ({', '.join('?' * len(vals))})")
            args += vals
    if f.zipf_min is not None:
        where.append("COALESCE(e.zipf, 0) >= ?")
        args.append(f.zipf_min)
    if f.zipf_max is not None:
        where.append("COALESCE(e.zipf, 0) <= ?")
        args.append(f.zipf_max)
    if f.longueur_min is not None:
        where.append("length(e.forme) >= ?")
        args.append(f.longueur_min)
    if f.longueur_max is not None:
        where.append("length(e.forme) <= ?")
        args.append(f.longueur_max)
    sql = f"""SELECT e.id, e.forme, e.zipf, e.type, e.origine, COALESCE(c.p_connu, 0.5) AS p_connu,
                     COALESCE(c.n_obs, 0) AS n_obs
              FROM entree e LEFT JOIN connaissance c ON c.entree_id = e.id
              WHERE {' AND '.join(where)} ORDER BY {ordre}"""
    if limite:
        sql += f" LIMIT {int(limite)}"
    rows = con.execute(sql, args).fetchall()
    if f.secondaires:
        return rows
    exclus = secondaires(con)
    return [r for r in rows if r["id"] not in exclus]


def domaines_disponibles(con: sqlite3.Connection) -> list[tuple[str, int]]:
    return [
        (r["nom"], r["n"]) for r in con.execute(
            """SELECT d.nom, COUNT(DISTINCT s.entree_id) AS n FROM domaine d
               LEFT JOIN sens_domaine sd ON sd.domaine_id = d.id LEFT JOIN sens s ON s.id = sd.sens_id
               GROUP BY d.id ORDER BY n DESC"""
        )
    ]
