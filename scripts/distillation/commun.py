"""Outils communs à la distillation du correcteur de définitions (cf. docs/passation-distillation.md).

Les données vivent dans ~/lem-distill-data/ (jamais dans le dépôt). Chaque script écrit en JSONL au fil de
l'eau et reprend là où il s'est arrêté : un arrêt (quota, coupure) ne perd rien.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DONNEES = Path(os.environ.get("LEM_DISTILL_DATA", Path.home() / "lem-distill-data"))
BASE = Path(os.environ.get("LEM_BASE", Path.home() / ".lem" / "lem.sqlite"))

# Sens qui ne sont qu'un renvoi : rien à définir pour un apprenant.
RE_RENVOI = re.compile(r"^(?:Variante|Ancienne orthographe|Orthographe|Graphie|Forme|Pluriel|Féminin|Masculin|"
                       r"Participe|Singulier|Abréviation|Synonyme|Autre orthographe|"
                       r"Première|Deuxième|Troisième)\b", re.I)
# Un mot : lettres minuscules (accents compris), apostrophe ou trait d'union intérieurs ; pas « chap. ».
RE_MOT = re.compile(r"^[a-zàâäéèêëîïôöùûüÿçœæ]+(?:['’-][a-zàâäéèêëîïôöùûüÿçœæ]+)*$")
# Composés à préfixe avec trait d'union (re-modeler, supra-normaliser…) : dérivés transparents ou variantes.
RE_PREFIXE = re.compile(r"^(?:re|ré|anti|supra|social|semi|sur|sous|non|néo|auto|contre|demi|ex|extra|hyper|"
                        r"hypo|inter|mini|multi|post|pré|pro|pseudo|super|ultra|redouble)-", re.I)


def lire_jsonl(chemin: Path) -> list[dict]:
    """Lit un fichier JSONL. Une coupure de courant peut laisser une DERNIÈRE ligne incomplète (ou des octets
    nuls) : elle est ignorée. Une ligne illisible au milieu reste une erreur (les identifiants en dépendent)."""
    if not chemin.exists():
        return []
    with open(chemin, encoding="utf-8") as f:
        lignes = [ligne for ligne in (x.replace("\x00", "") for x in f) if ligne.strip()]
    out = []
    for i, ligne in enumerate(lignes):
        try:
            out.append(json.loads(ligne))
        except json.JSONDecodeError:
            if i < len(lignes) - 1:
                raise
    return out


def ajouter_jsonl(chemin: Path, objets) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as f:
        for o in objets:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
