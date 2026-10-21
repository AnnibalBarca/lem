"""Graphie : aucune variante n'est acceptée ; chaque faute est identifiée, classée, estimée.

Une saisie qui n'est pas une forme exacte du lexique (casse mise à part) est refusée.
Si elle est proche d'un mot réel, on produit un diagnostic :
  * le mot visé (le plus probable) et la distance (Damerau-Levenshtein) ;
  * les types de faute : accent, consonne_double, h_muet, omission, ajout, substitution,
    inversion, frappe (touche voisine AZERTY), graphème (i/y, c/k/qu, s/ç…), trait_union ;
  * homophone : la saisie se prononce comme le mot → faute d'orthographe plutôt que de frappe ;
  * une confiance.
La faute est écrite au journal sur une trace FSRS dédiée, « ortho » : le mot revient,
d'abord dans la même session (il faut le retaper juste), puis à intervalles espacés.

Le profil orthographique (taux d'erreur par motif : consonnes doubles, h muet, graphies
grecques, accents…) estime le risque de faute sur n'importe quel mot, même jamais tapé.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein, OSA
from rich.text import Text

from lem.journal import Evenement, Frappe, ecrire
from lem.texte import normaliser, sans_accents

GRAPHIE = "#ffff5f"

# Clavier AZERTY : voisinage des touches (fautes de frappe).
_RANGEES = ["azertyuiop", "qsdfghjklm", "wxcvbn"]
_POS = {c: (r, i) for r, ligne in enumerate(_RANGEES) for i, c in enumerate(ligne)}

_GRAPHEMES = [set("iy"), set("ckq"), set("sçcz"), set("eéèêë"), set("aàâ"), set("oô"), set("uùûü"), set("iîï"),
              set("fv"), set("gj"), set("mn"), set("td"), set("xs")]


def voisines_azerty(a: str, b: str) -> bool:
    if a not in _POS or b not in _POS:
        return False
    (ra, ia), (rb, ib) = _POS[a], _POS[b]
    return abs(ra - rb) + abs(ia - ib) == 1 or (abs(ra - rb) == 1 and abs(ia - ib) <= 1)


def cle_phonetique(mot: str) -> str:
    """Clé phonétique approximative du français (pour repérer les homophones graphiques)."""
    s = mot.lower().replace("œ", "e").replace("æ", "e")
    regles = [
        (r"chr", "kr"), (r"ph", "f"), (r"th", "t"), (r"rh", "r"), (r"ch", "S"), (r"qu", "k"), (r"ck", "k"),
        (r"cc(?=[eiyéèê])", "ks"), (r"c(?=[eiyéèêë])", "s"), (r"ç", "s"), (r"c", "k"), (r"q", "k"),
        (r"gu(?=[eiéèê])", "g"), (r"g(?=[eiyéèê])", "j"), (r"x", "ks"), (r"y", "i"), (r"h", ""),
        (r"eau|au|ô", "o"), (r"ai|ei|è|ê|ë", "E"), (r"é", "E"),
        (r"(?<=[aeiouE])s(?=[aeiouE])", "z"),
        (r"[ae]n(?=[^aeiouynE]|$)|[ae]m(?=[bp])", "A"), (r"(?:ai|ei|i|y)n(?=[^aeiounE]|$)|im(?=[bp])", "I"),
        (r"on(?=[^aeiounE]|$)|om(?=[bp])", "O"),
        (r"([bcdfgjklmnprstvz])\1", r"\1"),
        (r"[àâ]", "a"), (r"[îï]", "i"), (r"[ùûü]", "u"),
        (r"(?<=.)e$", ""), (r"(?<=.)(?:er|ez|et)$", "E"), (r"(?<=..)[stdxzp]$", ""), (r"-", ""),
    ]
    for motif, rempl in regles:
        s = re.sub(motif, rempl, s)
    return s


def seuil_distance(n: int) -> int:
    return 1 if n <= 4 else 2 if n <= 9 else 3


def classer(saisie: str, correcte: str) -> list[str]:
    s, c = saisie.lower(), correcte.lower()
    if s == c:
        return []
    if sans_accents(s) == sans_accents(c):
        return ["accent"]
    types: list[str] = []
    if OSA.distance(s, c) < Levenshtein.distance(s, c):
        types.append("inversion")
    for tag, i, j in Levenshtein.editops(s, c):
        if tag == "insert":                     # lettre de la bonne graphie absente de la saisie
            ch = c[j]
            if ch == "h":
                types.append("h_muet")
            elif ch == "-":
                types.append("trait_union")
            elif (j > 0 and c[j - 1] == ch) or (j + 1 < len(c) and c[j + 1] == ch):
                types.append("consonne_double")
            else:
                types.append("omission")
        elif tag == "delete":                   # lettre en trop dans la saisie
            ch = s[i]
            if (i > 0 and s[i - 1] == ch) or (i + 1 < len(s) and s[i + 1] == ch):
                types.append("consonne_double")
            elif ch == "h":
                types.append("h_muet")
            else:
                types.append("ajout")
        else:
            a, b = s[i], c[j]
            if sans_accents(a) == sans_accents(b):
                types.append("accent")
            elif any(a in g and b in g for g in _GRAPHEMES):
                types.append("grapheme")
            elif voisines_azerty(sans_accents(a), sans_accents(b)):
                types.append("frappe")
            else:
                types.append("substitution")
    if "inversion" in types and OSA.distance(s, c) == 1:
        types = ["inversion"]
    elif "inversion" in types:
        types = [t for t in types if t not in {"substitution", "frappe"}] or types
    return list(dict.fromkeys(types))


@dataclass
class Diagnostic:
    saisie: str
    correcte: str
    entree_id: int
    distance: int
    types: list[str]
    homophone: bool
    confiance: float
    alternatives: list[str] = field(default_factory=list)

    @property
    def nature(self) -> str:
        if self.homophone or "accent" in self.types or "consonne_double" in self.types or "h_muet" in self.types:
            return "orthographe"
        if set(self.types) <= {"frappe", "inversion"}:
            return "frappe"
        return "orthographe"

    def resume(self) -> dict:
        d = asdict(self)
        d["nature"] = self.nature
        return d


def _evaluer(saisie: str, correcte: str, entree_id: int, ambigus: int = 1) -> Diagnostic | None:
    s, c = saisie.lower(), correcte.lower()
    d = OSA.distance(s, c)
    homo = cle_phonetique(s) == cle_phonetique(c)
    if d == 0 or (d > seuil_distance(len(c)) and not homo and sans_accents(s) != sans_accents(c)):
        return None
    conf = max(0.05, 1 - d / max(len(c), 1)) * (1.15 if homo else 1.0) / ambigus
    return Diagnostic(saisie, correcte, entree_id, d, classer(s, c), homo, round(min(conf, 1.0), 3))


def est_forme(con: sqlite3.Connection, saisie: str) -> bool:
    return con.execute("SELECT 1 FROM forme WHERE forme = ? LIMIT 1", (saisie.strip().lower(),)).fetchone() is not None


def diagnostiquer(con: sqlite3.Connection, saisie: str, cible_id: int | None = None,
                  candidats: list[str] | None = None) -> Diagnostic | None:
    """Diagnostique une saisie qui n'est PAS une forme exacte. None = pas un mot reconnaissable."""
    s = saisie.strip().lower()
    if len(s) < 2 or est_forme(con, s):
        return None
    # 1. La cible attendue, si on en a une (cartes, bombe en apprentissage).
    if cible_id is not None:
        formes = [r[0] for r in con.execute("SELECT forme FROM forme WHERE entree_id = ?", (cible_id,))]
        meilleur = min((_evaluer(s, f, cible_id) for f in formes), key=lambda x: x.distance if x else 99, default=None)
        if meilleur:
            return meilleur
    # 2. Même mot sans accents (érreur d'accent quasi certaine).
    rows = con.execute("SELECT forme, entree_id FROM forme WHERE norme = ?", (normaliser(s),)).fetchall()
    if rows:
        f, eid = rows[0]
        d = _evaluer(s, f, eid, ambigus=max(1, len({r[0] for r in rows})))
        if d:
            d.alternatives = sorted({r[0] for r in rows} - {f})
            return d
    # 3. Recherche floue parmi des candidats plausibles.
    if candidats is None:
        n = len(s)
        candidats = [r[0] for r in con.execute(
            "SELECT forme FROM forme WHERE forme >= ? AND forme < ? AND length(forme) BETWEEN ? AND ?",
            (s[0], chr(ord(s[0]) + 1), n - 3, n + 3))]
        if s[0] in "aeiouy":           # h muet initial : « omme » → « homme »
            candidats += [r[0] for r in con.execute(
                "SELECT forme FROM forme WHERE forme >= 'h' AND forme < 'i' AND length(forme) BETWEEN ? AND ?",
                (n - 2, n + 4))]
    if not candidats:
        return None
    res = process.extract(s, candidats, scorer=OSA.distance, limit=3, score_cutoff=seuil_distance(len(s)) + 1)
    if not res:
        return None
    meilleur_mot, dist, _ = res[0]
    ex_aequo = [m for m, d, _ in res if d == dist]
    eid = con.execute("SELECT entree_id FROM forme WHERE forme = ? LIMIT 1", (meilleur_mot,)).fetchone()[0]
    d = _evaluer(s, meilleur_mot, eid, ambigus=len(ex_aequo))
    if d:
        d.alternatives = [m for m in ex_aequo if m != meilleur_mot]
    return d


def surligner(saisie: str, correcte: str, style_faute: str = f"bold underline {GRAPHIE}") -> tuple[Text, Text]:
    """(bonne graphie avec les zones fautives soulignées, saisie avec les lettres fautives barrées)."""
    s, c = saisie, correcte
    bon, tape = Text(), Text()
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(s.lower(), c.lower()):
        if tag == "equal":
            bon.append(c[j1:j2], "bold #f2f2f2")
            tape.append(s[i1:i2], "#8a8a8a")
        else:
            bon.append(c[j1:j2], style_faute)
            tape.append(s[i1:i2] or "·", "strike #ff5f5f")
    return bon, tape


def enregistrer_faute(con: sqlite3.Connection, session_id: int | None, jeu: str, diag: Diagnostic,
                      latence_ms: int | None = None, frappe: Frappe | None = None) -> int:
    """Écrit la faute sur la trace « ortho » du mot et (re)planifie cette trace."""
    from lem.memory import scheduler

    eid = ecrire(con, session_id, Evenement(
        jeu, "reponse", entree_id=diag.entree_id, trace="ortho", reponse=diag.saisie, correct=False,
        note_auto=1, latence_ms=latence_ms, frappe=frappe or Frappe(), data={"graphie": diag.resume()}))
    scheduler.reconstruire(con, [diag.entree_id])
    return eid


# ── Profil orthographique ─────────────────────────────────────────────────────────────────────

MOTIFS = {
    "consonne_double": re.compile(r"([bcdfglmnprstz])\1"),
    "h_muet": re.compile(r"^h|[^cpstr]h|(?:th|rh)"),
    "grec": re.compile(r"ph|th|rh|chr|y"),
    "accent": re.compile(r"[éèêëàâîïôûùüç]"),
    "trait_union": re.compile(r"-"),
    "finale_muette": re.compile(r"[^aeiouéèêy][stdxzp]$"),
    "nasale": re.compile(r"[aeiou][nm](?=[bcdfgjklpqrstvz]|$)"),
}
TYPE_VERS_MOTIF = {"consonne_double": "consonne_double", "h_muet": "h_muet", "accent": "accent",
                   "trait_union": "trait_union", "grapheme": "grec"}


def motifs(forme: str) -> set[str]:
    f = forme.lower()
    return {nom for nom, rx in MOTIFS.items() if rx.search(f)}


def profil(con: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """{motif: (fautes, occasions)} à partir du journal (saisies exactes ou fautives)."""
    fautes, occasions = Counter(), Counter()
    for forme, data in con.execute(
        """SELECT e.forme, ev.data FROM evenement ev JOIN entree e ON e.id = ev.entree_id
           WHERE ev.type = 'reponse' AND ev.trace IN ('prod', 'ortho') AND ev.reponse IS NOT NULL"""
    ):
        for m in motifs(forme):
            occasions[m] += 1
        if '"graphie"' in data:
            for t in json.loads(data)["graphie"].get("types", []):
                if t in TYPE_VERS_MOTIF:
                    fautes[TYPE_VERS_MOTIF[t]] += 1
    return {m: (fautes[m], occasions[m]) for m in MOTIFS}


def taux_motif(fautes: int, occasions: int, base: float = 0.04) -> float:
    """Taux de faute d'un motif, lissé vers `base` tant que les occasions sont rares."""
    return (fautes + base * 10) / (occasions + 10)


def risque(forme: str, prof: dict[str, tuple[int, int]], base: float = 0.04) -> float:
    """P(faute) estimée sur un mot : 1 − Π(1 − taux lissé de chacun de ses motifs)."""
    p_ok = 1.0
    for m in motifs(forme):
        p_ok *= 1 - taux_motif(*prof.get(m, (0, 0)), base=base)
    return 1 - p_ok
