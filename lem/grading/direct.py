"""Notation en direct, sans LLM : < 50 ms, déterministe.

Mot → sens (recep)  : la réponse (texte libre OU liste de synonymes) est plongée avec bge-m3,
                      comparée à tous les sens de la cible, puis au lexique entier :
                      c'est un « dictionnaire inversé ». Si ta définition tombe plus près d'un
                      autre mot (thuriféraire → atrabilaire), on le sait immédiatement.
                      « Température » façon Cemantix : 0–100 °.
Sens → mot (prod)   : forme exacte / forme fléchie / synonyme reconnu / mot proche étiqueté.

Ces notes sont PROVISOIRES (correcteur 'embedding') : Claude ou Jev les remplacent ensuite.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from lem.db import get_reglage
from lem.orthographe import Diagnostic, diagnostiquer
from lem.similarite import Comparaison, comparer, normaliser


@dataclass
class VerdictRecep:
    temperature: float | None             # 0..100, None si pas d'embedder sémantique
    sim_cible: float | None
    sens_vise_id: int | None
    mot_reellement_defini: tuple[int, str] | None   # (entree_id, forme)
    synonymes_reconnus: list[str] = field(default_factory=list)
    confusions: list[tuple[str, Comparaison]] = field(default_factory=list)
    note_auto: int | None = None
    fautes: list[Diagnostic] = field(default_factory=list)   # fautes de graphie dans ta réponse


@dataclass
class VerdictProd:
    correct: bool
    mode: str                              # exact | flechi | synonyme | graphie | autre_mot | inconnu
    entree_donnee: int | None
    comparaison: Comparaison | None
    note_auto: int
    graphie: Diagnostic | None = None


def seuils(con: sqlite3.Connection, modele: str) -> dict:
    defaut = {"bas": 0.45, "haut": 0.80, "marge": 0.02}
    return {**defaut, **get_reglage(con, f"seuils_definition:{modele}", {})}


def temperature(sim: float, bas: float, haut: float) -> float:
    return float(np.clip((sim - bas) / (haut - bas), 0, 1) * 100)


def decouper_synonymes(reponse: str) -> list[str] | None:
    """« flatteur, encenseur ; laudateur » → liste ; une phrase → None."""
    morceaux = [m.strip() for m in re.split(r"[,;/]", reponse) if m.strip()]
    if len(morceaux) >= 1 and all(len(m.split()) <= 3 for m in morceaux) and len(reponse.split()) <= 3 * len(morceaux):
        return morceaux
    return None


def entrees_de_forme(con: sqlite3.Connection, forme: str) -> list[int]:
    """Entrées dont `forme` est une forme EXACTE (casse mise à part). Aucune variante tolérée."""
    rows = con.execute("SELECT DISTINCT entree_id FROM forme WHERE forme = ?", (forme.strip().lower(),))
    return [r[0] for r in rows]


RE_MOT = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿœæŒÆ]+(?:-[A-Za-zÀ-ÖØ-öø-ÿœæ]+)*")


def fautes_dans(con: sqlite3.Connection, texte: str, confiance_min: float = 0.6) -> list[Diagnostic]:
    """Repère les mots mal orthographiés d'un texte libre (définition, synonymes)."""
    from wordfreq import zipf_frequency

    out = []
    for k, m in enumerate(RE_MOT.finditer(texte)):
        tok = m.group(0)
        if len(tok) < 3 or (k > 0 and tok[0].isupper()):
            continue
        if con.execute("SELECT 1 FROM forme WHERE forme = ? LIMIT 1", (tok.lower(),)).fetchone():
            continue
        accent_seul = con.execute("SELECT 1 FROM forme WHERE norme = ? LIMIT 1", (normaliser(tok),)).fetchone()
        # Mot courant absent d'une base partielle : wordfreq le connaît, ce n'est pas une faute.
        if not accent_seul and zipf_frequency(tok.lower(), "fr") >= 2.0:
            continue
        d = diagnostiquer(con, tok)
        if d and d.confiance >= confiance_min:
            out.append(d)
    return out


def synonymes(con: sqlite3.Connection, entree_id: int) -> set[int]:
    return {
        r[0] for r in con.execute(
            """SELECT cible_id FROM relation WHERE source_id = ? AND type = 'synonyme'
               UNION SELECT source_id FROM relation WHERE cible_id = ? AND type = 'synonyme'""",
            (entree_id, entree_id),
        )
    }


def noter_recep(con: sqlite3.Connection, entree_id: int, reponse: str, embedder=None,
                magasin_sens=None, magasin_entrees=None) -> VerdictRecep:
    reponse = reponse.strip()
    if not reponse or normaliser(reponse) in {"?", "je ne sais pas", "jsp", "aucune idee"}:
        return VerdictRecep(None, None, None, None, note_auto=1)
    fautes = fautes_dans(con, reponse)

    syns_reconnus: list[str] = []
    confusions: list[tuple[str, Comparaison]] = []
    liste = decouper_synonymes(reponse)
    if liste:
        syn_ids = synonymes(con, entree_id)
        for mot in liste:
            for eid in entrees_de_forme(con, mot):
                if eid in syn_ids:
                    syns_reconnus.append(mot)
                elif eid != entree_id:
                    comp = comparer(con, entree_id, eid, magasin_entrees)
                    if comp.proximite.value != "aucune":
                        confusions.append((mot, comp))

    if embedder is None or not getattr(embedder, "semantique", False) or magasin_sens is None or not len(magasin_sens):
        note = 3 if syns_reconnus else None
        return VerdictRecep(None, None, None, None, syns_reconnus, confusions, note, fautes)

    s = seuils(con, embedder.nom)
    v = embedder.embed([reponse])[0]
    sens_cible = [r[0] for r in con.execute("SELECT id FROM sens WHERE entree_id = ?", (entree_id,))]
    sims_cible = magasin_sens.sims(v, sens_cible)
    sens_vise, sim_c = max(sims_cible.items(), key=lambda kv: kv[1]) if sims_cible else (None, 0.0)

    # Dictionnaire inversé : quel sens du lexique entier ta définition décrit-elle le mieux ?
    mot_reel = None
    for vo in magasin_sens.voisins(v, k=5, exclure=set(sens_cible)):
        if vo.sim > sim_c + s["marge"]:
            r = con.execute(
                "SELECT e.id, e.forme FROM sens s JOIN entree e ON e.id = s.entree_id WHERE s.id = ?", (vo.id,)
            ).fetchone()
            if r and r["id"] != entree_id:
                mot_reel = (r["id"], r["forme"])
        break

    t = temperature(sim_c, s["bas"], s["haut"])
    if mot_reel:
        note = 1
    elif syns_reconnus or t >= 70:
        note = 3
    elif t >= 45:
        note = 2
    else:
        note = 1
    return VerdictRecep(t, sim_c, sens_vise, mot_reel, syns_reconnus, confusions, note, fautes)


def noter_prod(con: sqlite3.Connection, entree_id: int, reponse: str, magasin_entrees=None) -> VerdictProd:
    """Sens → mot, graphie exacte exigée. Une faute sur le bon mot = mot retrouvé mais mal écrit."""
    reponse = reponse.strip()
    cible = con.execute("SELECT forme FROM entree WHERE id = ?", (entree_id,)).fetchone()["forme"]
    if not reponse:
        return VerdictProd(False, "inconnu", None, None, 1)
    if reponse.lower() == cible.lower():
        return VerdictProd(True, "exact", entree_id, None, 3)
    ids = entrees_de_forme(con, reponse)
    if entree_id in ids:
        return VerdictProd(True, "flechi", entree_id, None, 3)
    syn = synonymes(con, entree_id)
    for i in ids:
        if i in syn:
            return VerdictProd(True, "synonyme", i, comparer(con, entree_id, i, magasin_entrees), 2)
    if ids:
        comp = comparer(con, entree_id, ids[0], magasin_entrees)
        return VerdictProd(False, "autre_mot", ids[0], comp, 1)
    diag = diagnostiquer(con, reponse, cible_id=entree_id)
    if diag and diag.entree_id == entree_id:
        # Le mot est retrouvé (trace prod : « difficile »), mais la graphie est fausse (trace ortho : raté).
        return VerdictProd(False, "graphie", entree_id, None, 2, diag)
    if diag:
        comp = comparer(con, entree_id, diag.entree_id, magasin_entrees)
        return VerdictProd(False, "autre_mot", diag.entree_id, comp, 1, diag)
    return VerdictProd(False, "inconnu", None, None, 1)
