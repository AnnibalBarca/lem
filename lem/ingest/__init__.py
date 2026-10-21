"""Ingestion des sources lexicales → SQLite.

Sources (à télécharger sur ta machine, cf. README) :
  * Lexique 3.83         lexique.org                 formes, lemmes, fréquences, phonétique, syllabes
  * Wiktionnaire (fr)    kaikki.org (wiktextract)    sens, étymologie, domaines, synonymes, formes
  * JeuxDeMots           jeuxdemots.org              réseau d'associations (dump texte)
  * wordfreq             paquet Python               fréquences Zipf (repli)
L'ordre conseillé : domaines → lexique → wiktionnaire → wordfreq → jeuxdemots → `lem prior`.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Callable, Iterator

from lem.db import (ajouter_formes, ajouter_relation, domaine_id, morpheme_id, upsert_entree)
from lem.texte import normaliser

Progression = Callable[[int], None] | None


def _ouvrir(path: str | Path):
    path = Path(path)
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path), encoding="utf-8")
    return open(path, encoding="utf-8")


# -- domaines --------------------------------------------------------------------------------------

def charger_domaines(con: sqlite3.Connection) -> dict[str, str]:
    """Crée la hiérarchie de domaines. Renvoie {sous-domaine: macro-domaine}."""
    arbre = json.loads(resources.files("lem.data").joinpath("domaines.json").read_text(encoding="utf-8"))
    parent = {}
    for macro, enfants in arbre.items():
        if macro.startswith("_"):
            continue
        domaine_id(con, macro)
        for e in enfants:
            domaine_id(con, e, macro)
            parent[e] = macro
    con.commit()
    return parent


def lier_domaine(con: sqlite3.Connection, sens_id: int, nom: str, parents: dict[str, str]) -> None:
    nom = nom.strip().strip("()").lower()
    if not nom or len(nom) > 40:
        return
    did = domaine_id(con, nom, parents.get(nom))
    con.execute("INSERT OR IGNORE INTO sens_domaine(sens_id, domaine_id) VALUES(?, ?)", (sens_id, did))


# -- démo ------------------------------------------------------------------------------------------

def demo(con: sqlite3.Connection) -> int:
    data = json.loads(resources.files("lem.data").joinpath("demo.json").read_text(encoding="utf-8"))
    parents = charger_domaines(con)
    ids = {}
    for e in data["entrees"]:
        eid = upsert_entree(con, e["forme"], e.get("pos", ""), type=e.get("type", "mot"), origine=e.get("origine"))
        ids[e["forme"]] = eid
        ajouter_formes(con, eid, [e["forme"], *e.get("formes", [])])
        concept = None
        if e.get("concept"):
            concept = con.execute("INSERT INTO concept(label) VALUES(?)", (e["concept"],)).lastrowid
        for rang, s in enumerate(e["sens"], 1):
            sid = con.execute(
                "INSERT INTO sens(entree_id, rang, definition, registre, concept_id) VALUES(?, ?, ?, ?, ?)",
                (eid, rang, s["definition"], s.get("registre"), concept),
            ).lastrowid
            for d in s.get("domaines", []):
                lier_domaine(con, sid, d, parents)
        for pos, (forme, glose) in enumerate(e.get("morphemes", [])):
            mid = morpheme_id(con, forme, glose, e.get("origine"))
            con.execute("INSERT OR IGNORE INTO entree_morpheme VALUES(?, ?, ?)", (eid, mid, pos))
    for a, b, t in data.get("relations", []):
        ajouter_relation(con, ids[a], ids[b], t, origine="demo")
    con.commit()
    zipf_wordfreq(con)
    return len(ids)


# -- wordfreq --------------------------------------------------------------------------------------

def zipf_wordfreq(con: sqlite3.Connection, ecraser: bool = False) -> int:
    from wordfreq import zipf_frequency

    rows = con.execute("SELECT id, forme FROM entree" + ("" if ecraser else " WHERE zipf IS NULL")).fetchall()
    con.executemany("UPDATE entree SET zipf = ? WHERE id = ?",
                    ((zipf_frequency(r["forme"], "fr"), r["id"]) for r in rows))
    con.commit()
    return len(rows)


# -- Lexique 3.83 ----------------------------------------------------------------------------------

CGRAM = {"NOM": "nom", "VER": "verbe", "ADJ": "adj", "ADV": "adv", "AUX": "verbe", "ONO": "interj"}


def lexique(con: sqlite3.Connection, path: str | Path, progression: Progression = None) -> int:
    """Lexique383.tsv : une ligne par forme fléchie × catégorie."""
    lemmes: dict[tuple[str, str], dict] = {}
    with _ouvrir(path) as f:
        lecteur = csv.DictReader(f, delimiter="\t")
        lecteur.fieldnames = [re.sub(r"^\d+_", "", c) for c in lecteur.fieldnames]
        for i, r in enumerate(lecteur):
            pos = CGRAM.get((r.get("cgram") or "").split(":")[0])
            if not pos or not r.get("lemme"):
                continue
            cle = (r["lemme"], pos)
            d = lemmes.setdefault(cle, {"formes": set(), "freq": 0.0, "phon": None, "syll": None})
            d["formes"].add(r["ortho"])
            if r["ortho"] == r["lemme"]:
                d["phon"] = d["phon"] or r.get("phon")
                d["syll"] = d["syll"] or r.get("orthosyll")
            try:
                fl = (float(r.get("freqlemlivres") or 0) + float(r.get("freqlemfilms2") or 0)) / 2
                d["freq"] = max(d["freq"], fl)
            except ValueError:
                pass
            if progression and i % 20000 == 0:
                progression(i)
    for (lemme, pos), d in lemmes.items():
        zipf = math.log10(d["freq"]) + 3 if d["freq"] > 0 else None
        eid = upsert_entree(con, lemme, pos, zipf=zipf, phon=d["phon"], syllabes=d["syll"])
        ajouter_formes(con, eid, d["formes"])
    con.commit()
    return len(lemmes)


# -- Wiktionnaire (wiktextract / kaikki, JSON lignes) ---------------------------------------------

POS_WIKT = {
    "noun": "nom", "name": "nom_propre", "adj": "adj", "verb": "verbe", "adv": "adv", "phrase": "expr",
    "proverb": "expr", "idiom": "expr", "prep_phrase": "expr", "intj": "interj", "prep": "prep",
    "nom": "nom", "adjectif": "adj", "verbe": "verbe", "adverbe": "adv", "locution": "expr",
}
TYPE_PAR_POS = {"nom_propre": "nom_propre", "expr": "expression"}
LANGUES_ORIGINE = (
    "grec", "latin", "anglais", "allemand", "italien", "espagnol", "arabe", "néerlandais", "danois",
    "norvégien", "suédois", "islandais", "russe", "japonais", "chinois", "hébreu", "persan", "turc",
    "portugais", "occitan", "provençal", "breton", "gaulois", "francique", "sanskrit", "hindi", "malais",
    "nahuatl", "quechua", "tupi", "polonais", "tchèque", "hongrois", "finnois", "basque", "catalan",
)
RE_ORIGINE = re.compile(r"\b(?:du|de l['’]|de la|des|emprunté (?:au|à l['’]))\s*(?:ancien |bas |moyen |vieux )?("
                        + "|".join(LANGUES_ORIGINE) + r")", re.I)
RE_MORPHEME = re.compile(r"(?<![\w-])((?:-[a-zàâäéèêëîïôöùûüç]{2,})|(?:[a-zàâäéèêëîïôöùûüç]{2,}-))(?![\w])"
                         r"(?:\s*\(?\s*«\s*([^»]{1,40})\s*»)?")
RE_DOMAINE_GLOSE = re.compile(r"^\s*\(([^)]{2,40})\)\s*")


def origine_depuis(etymologie: str) -> str | None:
    m = RE_ORIGINE.search(etymologie or "")
    return m.group(1).lower() if m else ("francais" if etymologie else None)


def morphemes_depuis(etymologie: str) -> list[tuple[str, str]]:
    return [(m.group(1).lower(), (m.group(2) or "").strip()) for m in RE_MORPHEME.finditer(etymologie or "")][:5]


def _lignes(path: str | Path) -> Iterator[dict]:
    with _ouvrir(path) as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne:
                try:
                    yield json.loads(ligne)
                except json.JSONDecodeError:
                    continue


def wiktionnaire(con: sqlite3.Connection, path: str | Path, progression: Progression = None,
                 langue: str = "fr") -> int:
    parents = charger_domaines(con)
    n = 0
    synonymes: list[tuple[int, str]] = []
    for i, e in enumerate(_lignes(path)):
        if e.get("lang_code", langue) != langue or not e.get("word"):
            continue
        pos = POS_WIKT.get(e.get("pos", ""), e.get("pos", ""))
        mot = e["word"]
        etym = " ".join(e.get("etymology_texts") or [e.get("etymology_text") or ""]).strip()
        type_ = TYPE_PAR_POS.get(pos) or ("compose" if re.search(r"[ \-]", mot) else "mot")
        phon = next((s.get("ipa") for s in e.get("sounds", []) if s.get("ipa")), None)
        eid = upsert_entree(con, mot, pos, langue=langue, type=type_, phon=phon,
                            origine=origine_depuis(etym), etymologie=etym or None)
        ajouter_formes(con, eid, [f.get("form", "") for f in e.get("forms", []) if " " not in f.get("form", " ")])
        rang = con.execute("SELECT COALESCE(MAX(rang), 0) FROM sens WHERE entree_id = ?", (eid,)).fetchone()[0]
        for s in e.get("senses", []):
            gloses = s.get("glosses") or s.get("raw_glosses") or []
            if not gloses:
                continue
            definition = gloses[-1]
            domaines = list(s.get("topics", [])) + list(s.get("raw_tags", []))
            m = RE_DOMAINE_GLOSE.match(definition)
            if m:
                domaines.append(m.group(1))
                definition = definition[m.end():]
            if not definition.strip():
                continue
            rang += 1
            exemple = next((x.get("text") for x in s.get("examples", []) if x.get("text")), None)
            registre = next((t for t in s.get("tags", []) if t in {"literary", "formal", "familiar", "dated", "archaic"}), None)
            sid = con.execute(
                "INSERT INTO sens(entree_id, rang, definition, exemple, registre) VALUES(?, ?, ?, ?, ?)",
                (eid, rang, definition.strip(), exemple, registre),
            ).lastrowid
            for d in domaines:
                lier_domaine(con, sid, d, parents)
        for p, (forme, glose) in enumerate(morphemes_depuis(etym)):
            mid = morpheme_id(con, forme, glose)
            con.execute("INSERT OR IGNORE INTO entree_morpheme VALUES(?, ?, ?)", (eid, mid, p))
        synonymes += [(eid, s["word"]) for s in e.get("synonyms", []) if s.get("word")]
        n += 1
        if n % 5000 == 0:
            con.commit()
            if progression:
                progression(n)
    for eid, mot in synonymes:
        for r in con.execute("SELECT id FROM entree WHERE forme = ? AND langue = ?", (mot, langue)):
            ajouter_relation(con, eid, r["id"], "synonyme", origine="wiktionnaire")
    con.commit()
    return n


# -- JeuxDeMots ------------------------------------------------------------------------------------

# Types de relations JDM retenus (identifiants à vérifier sur le dump réel).
JDM_TYPES = {0: "association", 5: "synonyme", 6: "hyperonyme", 7: "antonyme"}


def jeuxdemots(con: sqlite3.Connection, path: str | Path, poids_min: int = 25, progression: Progression = None) -> int:
    """Dump texte JDM : lignes « e;eid;'nom';type;w » puis « r;rid;n1;n2;type;w »."""
    noeuds: dict[int, int] = {}
    index = {normaliser(r["forme"]): r["id"] for r in con.execute("SELECT id, forme FROM entree WHERE langue = 'fr'")}
    n = 0
    with _ouvrir(path) as f:
        for ligne in f:
            if ligne.startswith("e;"):
                parts = ligne.rstrip("\n").split(";")
                if len(parts) >= 3:
                    nom = parts[2].strip("'")
                    eid = index.get(normaliser(nom))
                    if eid:
                        noeuds[int(parts[1])] = eid
            elif ligne.startswith("r;"):
                parts = ligne.rstrip("\n").split(";")
                if len(parts) < 6:
                    continue
                t, w = int(parts[4]), int(parts[5])
                if t not in JDM_TYPES or w < poids_min:
                    continue
                a, b = noeuds.get(int(parts[2])), noeuds.get(int(parts[3]))
                if a and b and a != b:
                    ajouter_relation(con, a, b, JDM_TYPES[t], w / 100, "jeuxdemots")
                    n += 1
                    if progression and n % 50000 == 0:
                        con.commit()
                        progression(n)
    con.commit()
    return n
