"""Recherche de mots avec leur définition, pour les jeux (« /d ruv » à la Bombe, pendant ou après la partie).

Deux façons de chercher, selon ce qu'on tape :
  * une suite de lettres (« ruv », « -nyme ») : les mots VALIDES qui la contiennent, les plus usuels d'abord —
    ce qu'on aurait pu sortir, avec ce que ça veut dire ;
  * une description (« petit oiseau chanteur ») : le dictionnaire inversé, par le sens (bge-m3), comme le juge
    des définitions le fait pour dire « tu as plutôt défini… ».
Mêmes règles que les jeux : des mots, ni noms propres, ni sigles, ni locutions, ni majuscule initiale.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from lem.texte import normaliser

TYPES_HORS_JEU = ("nom_propre", "sigle")
RE_SUITE = re.compile(r"^[a-zà-ÿœæ'-]{2,}$", re.I)


@dataclass
class Trouve:
    forme: str                  # ce qu'on taperait
    lemme: str                  # l'entrée du dictionnaire
    definition: str
    zipf: float
    p: float | None = None      # P(connu) de l'entrée, si la carte est fournie
    autres_formes: int = 0      # formes fléchies du même mot qui contiennent aussi la suite (péruviens, péruvienne…)


def est_une_suite(requete: str) -> bool:
    """Une suite de lettres (un mot), pas une description : pas d'espace, que des lettres."""
    return bool(RE_SUITE.match(requete.strip())) and " " not in requete.strip()


def _enrichir(con: sqlite3.Connection, ids: list[int], carte=None) -> dict[int, tuple[str, float, str]]:
    """{entrée: (lemme, zipf, première définition non vide)} ; les entrées sans définition sont écartées."""
    out: dict[int, tuple[str, float, str]] = {}
    for a in range(0, len(ids), 900):
        lot = ids[a:a + 900]
        marques = ", ".join("?" * len(lot))
        zip_ = {r[0]: (r[1], r[2] or 0.0) for r in con.execute(
            f"SELECT id, forme, zipf FROM entree WHERE id IN ({marques}) AND forme NOT GLOB '[A-Z]*'", lot)}
        for eid, d in con.execute(
                f"SELECT entree_id, definition FROM sens WHERE entree_id IN ({marques}) AND definition != '' "
                f"ORDER BY entree_id, rang", lot):
            if eid in zip_ and eid not in out:
                out[eid] = (zip_[eid][0], zip_[eid][1], d)
    return out


def par_suite(con: sqlite3.Connection, lex, suite: str, *, exclus=TYPES_HORS_JEU, carte=None,
              max_formes: int = 20000) -> list[Trouve]:
    """Les mots valides qui contiennent `suite`, du plus usuel au plus rare, avec leur définition."""
    from wordfreq import zipf_frequency

    n = normaliser(suite).strip("-'")
    if len(n) < 2:
        return []
    normes = sorted(lex.normes_contenant(n, range(len(n), 40)), key=len)[:max_formes]
    formes = []
    for x in normes:
        for f in lex.formes_de_norme(x):
            ids = lex.entrees(f, exclus)
            if ids and f.isalpha():
                formes.append((f, ids))
    info = _enrichir(con, list({i for _, ids in formes for i in ids}))
    trouves: dict[str, Trouve] = {}
    for f, ids in formes:
        e = next((i for i in ids if i in info), None)
        if e is None or f in trouves:
            continue
        lemme, z, d = info[e]
        trouves[f] = Trouve(f, lemme, d, max(z, zipf_frequency(f, "fr")))
    # Un mot par lemme : sa forme de base si elle contient la suite, sinon la plus courte ; les autres comptées.
    par_lemme: dict[str, list[Trouve]] = {}
    for t in trouves.values():
        par_lemme.setdefault(t.lemme.lower(), []).append(t)
    out = []
    for groupe in par_lemme.values():
        meilleur = min(groupe, key=lambda t: (t.forme != t.lemme.lower(), len(t.forme), t.forme))
        meilleur.autres_formes = len(groupe) - 1
        meilleur.zipf = max(t.zipf for t in groupe)
        out.append(meilleur)
    out.sort(key=lambda t: (-t.zipf, len(t.forme), t.forme))
    if carte is not None and out:
        ids = {f: e for f, ids_ in formes for e in ids_[:1]}
        ps = dict(zip(ids, carte.p_de(list(ids.values()))))
        for t in out:
            t.p = ps.get(t.forme)
    return out


def par_definition(con: sqlite3.Connection, lex, embedder, magasin_sens, texte: str, *, exclus=TYPES_HORS_JEU,
                   k: int = 30, carte=None) -> list[Trouve]:
    """Dictionnaire inversé : les mots dont une définition ressemble à `texte`."""
    if embedder is None or magasin_sens is None or not len(magasin_sens):
        return []
    v = embedder.embed([texte.strip()])[0]
    voisins = magasin_sens.voisins(v, k=k)
    sens_ids = [x.id for x in voisins]
    sim = {x.id: x.sim for x in voisins}
    lignes = []
    for a in range(0, len(sens_ids), 900):
        lot = sens_ids[a:a + 900]
        lignes += con.execute(
            f"SELECT s.id, s.entree_id, s.definition, e.forme, e.zipf, e.type FROM sens s JOIN entree e ON e.id = s.entree_id "
            f"WHERE s.id IN ({', '.join('?' * len(lot))}) AND s.definition != '' AND e.forme NOT GLOB '[A-Z]*' "
            f"AND instr(e.forme, ' ') = 0", lot).fetchall()
    lignes = [r for r in lignes if r["type"] not in exclus]
    lignes.sort(key=lambda r: -sim[r["id"]])
    vus, out = set(), []
    for r in lignes:
        if r["entree_id"] in vus:
            continue
        vus.add(r["entree_id"])
        out.append(Trouve(r["forme"], r["forme"], r["definition"], r["zipf"] or 0.0, None))
    if carte is not None and out:
        ids = {r["forme"]: r["entree_id"] for r in lignes}
        ps = dict(zip(ids, carte.p_de(list(ids.values()))))
        for t in out:
            t.p = ps.get(t.forme)
    return out
