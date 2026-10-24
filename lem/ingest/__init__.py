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
import unicodedata
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

# wordfreq estime une locution d'après ses mots, comme s'ils étaient indépendants : « ligne de Hunter » 3,7,
# « N. S. J. C. » 6,1. On part donc du mot le plus rare et on retire un point de Zipf par mot supplémentaire.
PENALITE_LOCUTION = 1.0


def zipf_estime(forme: str, langue: str = "fr") -> float:
    from wordfreq import tokenize, zipf_frequency

    mots = tokenize(forme, langue)
    if len(mots) <= 1:
        return zipf_frequency(forme, langue)
    return max(0.0, min(zipf_frequency(m, langue) for m in mots) - PENALITE_LOCUTION * (len(mots) - 1))


def zipf_wordfreq(con: sqlite3.Connection, ecraser: bool = False) -> int:
    rows = con.execute("SELECT id, forme FROM entree" + ("" if ecraser else " WHERE zipf IS NULL")).fetchall()
    con.executemany("UPDATE entree SET zipf = ? WHERE id = ?", ((zipf_estime(r["forme"]), r["id"]) for r in rows))
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
    "onomatopoeia": "interj", "pron": "pron", "conj": "conj", "det": "det", "article": "det", "num": "num",
    "particle": "part", "postp": "prep",
    "nom": "nom", "adjectif": "adj", "verbe": "verbe", "adverbe": "adv", "locution": "expr",
}
# Ni des mots à apprendre ni des sens : affixes, lettres, symboles, variantes typographiques.
POS_IGNORES = {"prefix", "suffix", "infix", "interfix", "circumfix", "character", "symbol", "typographic variant",
               "punctuation"}
TYPE_PAR_POS = {"nom_propre": "nom_propre", "expr": "expression"}
LANGUES_ORIGINE = (
    "grec", "latin", "anglais", "allemand", "italien", "espagnol", "arabe", "néerlandais", "danois",
    "norvégien", "suédois", "islandais", "russe", "japonais", "chinois", "hébreu", "persan", "turc",
    "portugais", "occitan", "provençal", "breton", "gaulois", "francique", "sanskrit", "hindi", "malais",
    "nahuatl", "quechua", "tupi", "polonais", "tchèque", "hongrois", "finnois", "basque", "catalan",
)
RE_ORIGINE = re.compile(r"\b(?:du|de l['’]|de la|des|emprunt(?:é)? (?:au|à l['’]|à la|du|de l['’]))\s*"
                        r"(?:ancien |bas |moyen |vieux |vieil )?(" + "|".join(LANGUES_ORIGINE) + r")", re.I)
RE_MORPHEME = re.compile(r"(?<![\w-])((?:-[a-zàâäéèêëîïôöùûüç]{2,})|(?:[a-zàâäéèêëîïôöùûüç]{2,}-))(?![\w])"
                         r"(?:\s*\(?\s*«\s*([^»]{1,40})\s*»)?")
RE_DOMAINE_GLOSE = re.compile(r"^\s*\(([^)]{2,40})\)\s*")
RE_SIGLE = re.compile(r"^\s*(?:sigle|acronyme|abréviation)\b", re.I)
RE_LOCUTION_EXPR = re.compile(r"^Locution[- ](?:verbale|adverbiale|phrase|interjective|prépositive|conjonctive|pronominale)")
# raw_tags du Wiktionnaire : domaines mêlés aux régions (« Québec ») et aux qualificatifs (« Très rare »).
# On ne garde qu'un domaine connu de la hiérarchie, ou un nom de discipline reconnaissable à son suffixe.
RE_DISCIPLINE = re.compile(r"^[a-zàâäéèêëîïôöùûüçœ’ -]+(?:logie|graphie|nomie|technie|culture|iatrie|métrie)$")


# API → code phonétique de Lexique (une lettre par phonème), pour que `entree.phon` reste comparable
# d'une source à l'autre (similarite.sim_forme compare deux transcriptions).
API_LEXIQUE = [("ɑ̃", "@"), ("ɛ̃", "5"), ("ɔ̃", "§"), ("œ̃", "1"), ("ʁ", "R"), ("ʀ", "R"), ("r", "R"), ("ʃ", "S"),
               ("ʒ", "Z"), ("ɛ", "E"), ("ɔ", "O"), ("ø", "2"), ("œ", "9"), ("ə", "°"), ("ɑ", "a"), ("ɲ", "N"),
               ("ŋ", "G"), ("ɥ", "8"), ("ɡ", "g"), ("χ", "x")]
RE_API_BRUIT = re.compile(r"[\\\[\]/.ˈˌ‿ːː͡( )]")


def api_vers_lexique(api: str) -> str:
    api = unicodedata.normalize("NFC", RE_API_BRUIT.sub("", api.split(",")[0]))
    for a, b in API_LEXIQUE:
        api = api.replace(unicodedata.normalize("NFC", a), b)
    return api


# Gabarits du Wiktionnaire laissés dans les gloses : « Définition manquante ou à compléter. (Ajouter) ».
RE_GABARIT = re.compile(r"(?:Définition manquante ou à compléter|Exemple d’utilisation manquant|Exemple d'utilisation manquant)"
                        r"\.?\s*\(Ajouter\)\s*\}?\s*[.…]*|\(définition à vérifier\)|^#\s*")
RE_FIN_PENDANTE = re.compile(r"(?:[,:;]|\b(?:à|de|des|du|et|en|le|la|les|un|une)|…)\s*$", re.I)


def nettoyer_definition(texte: str) -> str | None:
    """Retire les gabarits « Définition manquante… (Ajouter) ». None s'il ne reste pas une vraie définition
    (« Toucher. » reste ; « pronominal », « En ski, », « Habitant de… » non)."""
    t = re.sub(r"!!.*?!!", " ", RE_GABARIT.sub(" ", texte))       # notes de rédaction « !! … !! »
    t = re.sub(r"\s+", " ", t).strip(" .…!?")
    if not t or not re.search(r"[a-zà-ÿ]{2}", t, re.I) or RE_FIN_PENDANTE.search(t):
        return None
    if " " not in t and not texte.rstrip().endswith((".", "…")) and t[0].islower():
        return None                                  # un seul mot sans majuscule ni point : une étiquette
    return t if t.endswith((".", "!", "?", "»", ")")) else t + "."


def retirer_locutions(con: sqlite3.Connection, lot: int = 2000) -> dict[str, int]:
    """Supprime de la base les entrées de plusieurs mots (« pomme de terre », « au fil de l'eau ») et les
    expressions : lem travaille sur des mots. Sont gardées, sans être jouées (les filtres les écartent), celles
    que le journal, une trace, une observation ou une image mentale cite : rien de ce que tu as joué ne
    disparaît. Supprime avec elles leurs sens, formes, relations, vecteurs et leur ligne de la carte."""
    cites: set[int] = set()
    for sql in ("SELECT DISTINCT entree_id FROM evenement WHERE entree_id IS NOT NULL",
                "SELECT DISTINCT entree_id FROM observation", "SELECT DISTINCT entree_id FROM trace_etat",
                "SELECT DISTINCT entree_id FROM mnemo"):
        cites |= {r[0] for r in con.execute(sql)}
    ids = [r[0] for r in con.execute("SELECT id FROM entree WHERE type = 'expression' OR instr(forme, ' ') > 0")]
    a_supprimer = [i for i in ids if i not in cites]
    for k in range(0, len(a_supprimer), lot):
        bloc = a_supprimer[k:k + lot]
        marques = ", ".join("?" * len(bloc))
        con.execute(f"DELETE FROM embedding WHERE objet = 'sens' AND objet_id IN "
                    f"(SELECT id FROM sens WHERE entree_id IN ({marques}))", bloc)
        con.execute(f"DELETE FROM embedding WHERE objet = 'entree' AND objet_id IN ({marques})", bloc)
        con.execute(f"DELETE FROM connaissance WHERE entree_id IN ({marques})", bloc)
        con.execute(f"DELETE FROM entree WHERE id IN ({marques})", bloc)       # cascade : sens, formes, relations
        con.commit()
    return {"supprimees": len(a_supprimer), "gardees_car_citees": len(ids) - len(a_supprimer)}


def nettoyer_sens(con: sqlite3.Connection) -> dict[str, int]:
    """Applique `nettoyer_definition` à une base déjà ingérée. Les sens vides sont supprimés, sauf s'ils sont
    cités par le journal (définition vidée : les jeux les ignorent). Les sens réécrits perdent leur vecteur,
    que `lem index` recalcule."""
    bilan = {"nettoyes": 0, "supprimes": 0, "vides_gardes": 0}
    cites = {r[0] for r in con.execute("SELECT DISTINCT sens_id FROM evenement WHERE sens_id IS NOT NULL")}
    cites |= {r[0] for r in con.execute("SELECT DISTINCT sens_id FROM trace_etat")}
    rows = con.execute("SELECT id, definition FROM sens WHERE definition LIKE '%(Ajouter)%' "
                       "OR definition LIKE '%(définition à vérifier)%'").fetchall()
    touchees: set[int] = set()
    for sid, d in rows:
        propre = nettoyer_definition(d)
        touchees.add(con.execute("SELECT entree_id FROM sens WHERE id = ?", (sid,)).fetchone()[0])
        con.execute("DELETE FROM embedding WHERE objet = 'sens' AND objet_id = ?", (sid,))
        if propre:
            con.execute("UPDATE sens SET definition = ? WHERE id = ?", (propre, sid))
            bilan["nettoyes"] += 1
        elif sid in cites:
            con.execute("UPDATE sens SET definition = '' WHERE id = ?", (sid,))
            bilan["vides_gardes"] += 1
        else:
            con.execute("DELETE FROM sens WHERE id = ?", (sid,))
            bilan["supprimes"] += 1
    # Le vecteur d'une entrée est fait sur sa première définition : à refaire pour les entrées touchées.
    con.executemany("DELETE FROM embedding WHERE objet = 'entree' AND objet_id = ?", ((e,) for e in touchees))
    con.commit()
    return bilan


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


def _sujets() -> dict[str, str | None]:
    sujets = json.loads(resources.files("lem.data").joinpath("sujets_wiktextract.json").read_text(encoding="utf-8"))
    sujets.pop("_doc", None)
    return sujets


def _forme_de(e: dict, s: dict) -> str | None:
    """Lemme dont ce sens n'est qu'une forme fléchie (« Pluriel d'acronyme. »), sinon None."""
    if "form-of" not in (s.get("tags") or []) and "form-of" not in (e.get("tags") or []):
        return None
    return next((f.get("word") for f in s.get("form_of", []) if f.get("word")), None) or ""


def wiktionnaire(con: sqlite3.Connection, path: str | Path, progression: Progression = None,
                 langue: str = "fr") -> int:
    """Wiktionnaire français extrait par wiktextract (kaikki.org/frwiktionary), une entrée JSON par ligne.

    Les entrées « Forme de … » (≈ 1,5 M : pluriels, féminins, conjugaisons) ne deviennent pas des entrées :
    elles s'ajoutent aux formes fléchies de leur lemme."""
    parents = charger_domaines(con)
    sujets = _sujets()
    connus = set(parents) | {d for d in sujets.values() if d}
    n = 0
    synonymes: list[tuple[int, str]] = []
    flexions: list[tuple[str, str, str]] = []          # (forme, lemme, pos)

    def domaines_du_sens(s: dict) -> list[str]:
        ds = [sujets.get(t, t) for t in s.get("topics", [])]
        for t in s.get("raw_tags", []):
            t = t.strip().lower()
            if t in connus or RE_DISCIPLINE.match(t):
                ds.append(t)
        return [d for d in ds if d]

    for i, e in enumerate(_lignes(path)):
        if e.get("lang_code", langue) != langue or not e.get("word") or e.get("pos") in POS_IGNORES:
            continue
        pos = POS_WIKT.get(e.get("pos", ""), e.get("pos", ""))
        mot = e["word"]
        if " " in mot.strip() or pos == "expr":       # des mots, pas des locutions (ni dans le jeu, ni dans la base)
            continue
        sens = []
        for s in e.get("senses", []):
            lemme = _forme_de(e, s)
            if lemme is None:
                sens.append(s)
            elif lemme:
                flexions.append((mot, lemme, pos))
        if not sens:
            continue
        etyms = e.get("etymology_texts") or [e.get("etymology_text") or ""]
        etym = " ".join(etyms).strip()
        titre = e.get("pos_title") or ""
        type_ = TYPE_PAR_POS.get(pos)
        if RE_SIGLE.match(etyms[0] or "") or titre.startswith(("Sigle", "Acronyme")):
            type_ = "sigle"
        elif RE_LOCUTION_EXPR.match(titre):
            type_ = "expression"
        type_ = type_ or ("compose" if re.search(r"[ \-]", mot) else "mot")
        phon = next((api_vers_lexique(s["ipa"]) for s in e.get("sounds", []) if s.get("ipa")), None)
        # L'origine se lit dans le premier paragraphe d'étymologie ; les suivants racontent l'histoire du mot.
        eid = upsert_entree(con, mot, pos, langue=langue, type=type_, phon=phon,
                            origine=origine_depuis(etyms[0]), etymologie=etym or None)
        ajouter_formes(con, eid, [f.get("form", "") for f in e.get("forms", []) if " " not in f.get("form", " ")])
        rang = con.execute("SELECT COALESCE(MAX(rang), 0) FROM sens WHERE entree_id = ?", (eid,)).fetchone()[0]
        for s in sens:
            gloses = s.get("glosses") or s.get("raw_glosses") or []
            if not gloses:
                continue
            definition = gloses[-1]
            domaines = domaines_du_sens(s)
            m = RE_DOMAINE_GLOSE.match(definition)
            if m:
                if m.group(1).strip().lower() in connus or RE_DISCIPLINE.match(m.group(1).strip().lower()):
                    domaines.append(m.group(1))
                definition = definition[m.end():]
            definition = nettoyer_definition(definition) if "(Ajouter)" in definition \
                or "(définition à vérifier)" in definition else definition
            if not definition or not definition.strip():
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
    # Formes fléchies : rattachées au lemme de même catégorie, à défaut à tout lemme de cette graphie.
    for forme, lemme, pos in flexions:
        cibles = con.execute("SELECT id FROM entree WHERE forme = ? AND langue = ? AND pos = ?",
                             (lemme, langue, pos)).fetchall() or \
            con.execute("SELECT id FROM entree WHERE forme = ? AND langue = ?", (lemme, langue)).fetchall()
        for r in cibles:
            ajouter_formes(con, r["id"], [forme])
    con.commit()
    return n


# -- Grammalecte (formes manquantes) ----------------------------------------------------------------

# Catégories de Grammalecte (premier champ de l'étiquette) → catégorie de lem. Les autres (éléments de
# locution « extenso », mots grammaticaux élidés « lorsqu », préfixes « arabo », noms propres) sont ignorées.
POS_GRAMMALECTE = {"N": "nom", "A": "adj", "V": "verbe", "W": "adv", "J": "interj", "B": "num"}
RE_FORME_SIMPLE = re.compile(r"^[a-zàâäéèêëîïôöùûüÿçœæ]+(?:[-'’][a-zàâäéèêëîïôöùûüÿçœæ]+)*$")


def grammalecte(con: sqlite3.Connection, path: str | Path, progression: Progression = None) -> int:
    """Ajoute les formes du dictionnaire de Grammalecte (toutes variantes) absentes du lexique : conjugaisons,
    pluriels, graphies rectifiées. Rattachées au lemme s'il existe, sinon à une nouvelle entrée SANS
    définition (valide dans la Bombe, absente des Cartes). Le code de lecture (graphspell) est celui du paquet
    téléchargé, chargé à la volée : il n'est jamais copié dans le dépôt. Renvoie le nombre de formes ajoutées."""
    import sys
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(path).extractall(tmp)
        racine = next(Path(tmp).rglob("graphspell")).parent.parent
        sys.path.insert(0, str(racine))
        try:
            from grammalecte.graphspell.ibdawg import IBDAWG
            dico = IBDAWG("fr-allvars.json")
            entrees = list(dico.select())
        finally:
            sys.path.remove(str(racine))
            for m in [m for m in sys.modules if m == "grammalecte" or m.startswith("grammalecte.")]:
                del sys.modules[m]
    connues = {r[0] for r in con.execute("SELECT DISTINCT forme FROM forme")}
    n = 0
    for i, (flex, lemme, tag) in enumerate(entrees):
        f = flex.lower()
        cat = tag[1:2] if tag.startswith(":") else ""
        pos = POS_GRAMMALECTE.get(cat)
        if (f in connues or pos is None or flex[:1].isupper() or ":1ś" in tag or not RE_FORME_SIMPLE.match(f)
                or (pos == "adv" and len(f) < 4)):
            continue
        cible = con.execute("SELECT id FROM entree WHERE forme = ? AND langue = 'fr' AND pos = ?", (lemme, pos)).fetchone() \
            or con.execute("SELECT id FROM entree WHERE forme = ? AND langue = 'fr' AND type NOT IN ('nom_propre', 'sigle')",
                           (lemme,)).fetchone()
        if not cible and " " in lemme:
            continue
        eid = cible[0] if cible else upsert_entree(
            con, lemme, pos, type="compose" if re.search(r"[ \-]", lemme) else "mot")
        ajouter_formes(con, eid, [f])
        connues.add(f)
        n += 1
        if progression and n % 2000 == 0:
            con.commit()
            progression(n)
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
