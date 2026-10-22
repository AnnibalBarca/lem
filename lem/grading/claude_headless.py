"""Notation différée par Claude Code en mode headless (`claude -p`), sur ton abonnement.

À la fin d'une session (ou quand tu veux : `lem noter`), les définitions écrites
pendant les jeux sont envoyées par lots à Claude, qui renvoie pour chacune un
verdict EN VOCABULAIRE FERMÉ (cf. taxonomie.py). Les verdicts sont écrits dans
`notation` (jamais à la place d'une note existante), puis FSRS est rejoué.

Le même contrat (sens visé, précision 1–5, erreur ∈ taxonomie, mot réellement
défini) est celui qu'on posera à Jev : Choice / Score / Choice.
"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from lem.grading.direct import entrees_de_forme
from lem.grading.taxonomie import DESCRIPTION_ERREUR, NOTE_PAR_PRECISION, PRECISION, Erreur
from lem.journal import ajouter_notation
from lem.memory import scheduler
from lem.similarite import enregistrer_confusion

TAILLE_LOT = 20


class Verdict(BaseModel):
    id: int
    sens_vise_id: int | None = None
    precision: int = Field(ge=1, le=5)
    erreur: Erreur
    mot_reellement_defini: str | None = None
    confiance: float = Field(default=0.8, ge=0, le=1)
    commentaire: str = ""

    @field_validator("mot_reellement_defini")
    @classmethod
    def _vide(cls, v):
        return v or None


@dataclass
class Element:
    evenement_id: int
    entree_id: int
    payload: dict


def a_noter(con: sqlite3.Connection, limite: int = 500) -> list[Element]:
    rows = con.execute(
        """SELECT ev.id, ev.entree_id, ev.reponse, ev.data, e.forme, e.pos FROM evenement ev
           JOIN entree e ON e.id = ev.entree_id
           WHERE ev.type = 'reponse' AND ev.trace = 'recep' AND COALESCE(ev.reponse, '') != ''
             AND NOT EXISTS (SELECT 1 FROM notation n WHERE n.evenement_id = ev.id
                             AND (n.correcteur LIKE 'claude:%' OR n.correcteur LIKE 'jev:%'))
           ORDER BY ev.id LIMIT ?""",
        (limite,),
    ).fetchall()
    out = []
    for r in rows:
        sens = [
            {"id": s["id"], "definition": s["definition"]}
            for s in con.execute("SELECT id, definition FROM sens WHERE entree_id = ? ORDER BY rang", (r["entree_id"],))
        ]
        voisins = json.loads(r["data"]).get("voisins", [])[:6]
        out.append(Element(r["id"], r["entree_id"], {
            "id": r["id"], "mot": r["forme"], "categorie": r["pos"], "sens": sens,
            "voisins": voisins, "reponse": r["reponse"],
        }))
    return out


def consignes() -> str:
    """Les consignes et le vocabulaire fermé, communs à Claude, au juge local et (demain) à Jev."""
    erreurs = "\n".join(f'  - "{e.value}" : {DESCRIPTION_ERREUR[e]}' for e in Erreur)
    precision = "\n".join(f"  {k} = {v}" for k, v in PRECISION.items())
    return f"""Tu es correcteur lexicographique. Un apprenant francophone natif a vu un MOT et a écrit ce qu'il croit
être son sens (texte libre ou liste de synonymes). Juge sa réponse par rapport aux sens de référence. Les
« voisins » sont des mots qu'on confond souvent avec la cible : sers-t'en pour détecter qu'il a décrit un
autre mot.

Échelle de précision :
{precision}

Valeurs autorisées pour "erreur" :
{erreurs}

Règles :
- une liste de synonymes justes vaut précision 4 ; un autre sens réel du mot = "autre_sens" ;
- ne sois pas indulgent sur les définitions vagues : la précision 2 existe pour elles ;
- une réponse qui décrit un AUTRE mot ("sens_voisin", "confusion_formelle") ou sans rapport ("contresens")
  vaut précision 1, sauf si une partie du sens de la cible y est juste ;
- "mot_reellement_defini" : seulement un AUTRE mot que la cible, que la réponse définit mieux qu'elle ;
  jamais la cible elle-même, jamais un morceau de définition ; sinon null."""


def construire_prompt(lot: list[Element]) -> str:
    items = "\n".join(json.dumps(e.payload, ensure_ascii=False) for e in lot)
    return f"""{consignes()}

Réponds UNIQUEMENT par un tableau JSON, un objet par élément, sans texte autour :
[{{"id": <id>, "sens_vise_id": <id du sens de référence visé ou null>, "precision": <1-5>,
   "erreur": <une valeur autorisée>, "mot_reellement_defini": <mot qu'il a en fait défini, ou null>,
   "confiance": <0-1>, "commentaire": <15 mots max, en français>}}]

Éléments (JSON lignes) :
{items}
"""


def element(con: sqlite3.Connection, evenement_id: int) -> Element | None:
    """L'élément à juger pour une réponse donnée (même forme que pour un lot)."""
    r = con.execute("""SELECT ev.id, ev.entree_id, ev.reponse, ev.data, e.forme, e.pos FROM evenement ev
                       JOIN entree e ON e.id = ev.entree_id WHERE ev.id = ?""", (evenement_id,)).fetchone()
    if r is None or not (r["reponse"] or "").strip():
        return None
    sens = [{"id": s["id"], "definition": s["definition"]} for s in con.execute(
        "SELECT id, definition FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang", (r["entree_id"],))]
    return Element(r["id"], r["entree_id"], {
        "id": r["id"], "mot": r["forme"], "categorie": r["pos"], "sens": sens,
        "voisins": json.loads(r["data"]).get("voisins", [])[:6], "reponse": r["reponse"]})


def extraire_json(texte: str) -> list[dict]:
    """Récupère le tableau JSON dans la sortie de `claude -p --output-format json`."""
    try:
        enveloppe = json.loads(texte)
        if isinstance(enveloppe, dict) and "result" in enveloppe:
            texte = enveloppe["result"]
        elif isinstance(enveloppe, list):
            return enveloppe
    except json.JSONDecodeError:
        pass
    debut, fin = texte.find("["), texte.rfind("]")
    if debut < 0 or fin < debut:
        raise ValueError("aucun tableau JSON dans la réponse")
    return json.loads(texte[debut : fin + 1])


def appeler_claude(prompt: str, modele: str = "sonnet") -> str:
    cmd = shlex.split(os.environ.get("LEM_CLAUDE_CMD", "claude")) + ["-p", "--output-format", "json", "--model", modele]
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"claude a échoué ({res.returncode}) : {res.stderr.strip()[:500]}")
    return res.stdout


def appliquer(con: sqlite3.Connection, lot: list[Element], verdicts: list[Verdict], correcteur: str) -> int:
    par_id = {e.evenement_id: e for e in lot}
    n = 0
    for v in verdicts:
        el = par_id.get(v.id)
        if el is None:
            continue
        note = NOTE_PAR_PRECISION[v.precision]
        if v.erreur == Erreur.AUTRE_SENS and v.precision >= 4:
            note = 3
        ajouter_notation(
            con, v.id, correcteur, note=note, precision=v.precision, sens_vise_id=v.sens_vise_id,
            erreur=v.erreur.value, mot_reellement_defini=v.mot_reellement_defini, confiance=v.confiance,
            brut=v.model_dump(mode="json"),
        )
        if v.mot_reellement_defini:
            for autre in entrees_de_forme(con, v.mot_reellement_defini):
                enregistrer_confusion(con, el.entree_id, autre)
        n += 1
    return n


def noter(con: sqlite3.Connection, modele: str = "sonnet", limite: int = 500,
          appel: Callable[[str, str], str] = appeler_claude, journal=print) -> int:
    elements = a_noter(con, limite)
    if not elements:
        journal("Rien à noter.")
        return 0
    total, touches = 0, set()
    for i in range(0, len(elements), TAILLE_LOT):
        lot = elements[i : i + TAILLE_LOT]
        try:
            brut = extraire_json(appel(construire_prompt(lot), modele))
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            journal(f"lot {i // TAILLE_LOT + 1} : échec ({exc}) — il sera repris au prochain passage.")
            continue
        verdicts = []
        for obj in brut:
            try:
                verdicts.append(Verdict.model_validate(obj))
            except ValidationError as exc:
                journal(f"verdict rejeté : {exc.errors()[0]['msg']}")
        total += appliquer(con, lot, verdicts, f"claude:{modele}")
        touches.update(e.entree_id for e in lot)
        journal(f"lot {i // TAILLE_LOT + 1} : {len(verdicts)}/{len(lot)} notés")
    if touches:
        scheduler.reconstruire(con, sorted(touches))
    return total
