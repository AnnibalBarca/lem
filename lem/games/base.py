"""Socle commun des jeux : paramètres déclaratifs, commandes « /… », registre.

Chaque jeu déclare ses paramètres une fois ; l'interface en tire automatiquement
l'écran de réglage ET les commandes tapables en cours de partie :
    /domaine science,chimie    /etat flous    /vies 5    /params    /aide
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lem.games.pool import ETATS, Filtres


@dataclass
class Param:
    nom: str
    libelle: str
    type: type
    defaut: Any
    aide: str = ""
    min: float | None = None
    max: float | None = None
    choix: tuple | None = None
    alias: tuple[str, ...] = ()

    def convertir(self, brut: str) -> Any:
        brut = brut.strip()
        if self.type is bool:
            if brut.lower() in {"1", "oui", "on", "vrai", "true", "o", "y"}:
                return True
            if brut.lower() in {"0", "non", "off", "faux", "false", "n"}:
                return False
            raise ValueError(f"{self.nom} attend oui/non")
        if self.type is list:
            return [x.strip().lower() for x in brut.split(",") if x.strip() and x.strip() not in {"-", "tous", "aucun"}]
        if self.type in (int, float):
            v = self.type(brut.replace(",", ".") if self.type is float else brut)
            if self.min is not None and v < self.min or self.max is not None and v > self.max:
                bornes = f"{self.min if self.min is not None else '−∞'} et {self.max if self.max is not None else '+∞'}"
                raise ValueError(f"{self.nom} doit être entre {bornes}")
            return v
        if self.choix and brut not in self.choix:
            raise ValueError(f"{self.nom} ∈ {{{', '.join(self.choix)}}}")
        return brut

    def afficher(self, v: Any) -> str:
        if isinstance(v, list):
            return ",".join(v) or "—"
        if isinstance(v, bool):
            return "oui" if v else "non"
        if v is None:
            return "—"
        return f"{v:g}" if isinstance(v, float) else str(v)


# Filtres partagés par tous les jeux qui puisent dans le lexique.
PARAMS_FILTRES = [
    Param("domaine", "Domaines", list, [], "ex. science,droit — inclut les sous-domaines", alias=("dom", "d")),
    Param("origine", "Origines", list, [], "grec, latin, anglais, arabe…", alias=("ori",)),
    Param("type", "Types", list, [], "mot, expression, nom_propre, etranger…"),
    Param("etat", "État mémoire", str, "tous", "", choix=ETATS),
    Param("zipf_min", "Fréquence min (Zipf)", float, None, "0 = introuvable, 7 = « de »", 0, 8),
    Param("zipf_max", "Fréquence max (Zipf)", float, None, "", 0, 8),
]


def filtres_depuis(params: dict) -> Filtres:
    return Filtres(
        domaines=params.get("domaine") or [],
        origines=params.get("origine") or [],
        types=params.get("type") or [],
        etat=params.get("etat") or "tous",
        zipf_min=params.get("zipf_min"),
        zipf_max=params.get("zipf_max"),
    )


@dataclass
class Jeu:
    id: str
    nom: str
    accroche: str
    description: str
    params: list[Param] = field(default_factory=list)

    def defauts(self) -> dict:
        return {p.nom: p.defaut for p in self.params}

    def param(self, nom: str) -> Param | None:
        for p in self.params:
            if p.nom == nom or nom in p.alias:
                return p
        return None


REGISTRE: dict[str, Jeu] = {}


def enregistrer(jeu: Jeu) -> Jeu:
    REGISTRE[jeu.id] = jeu
    return jeu


@dataclass
class ResultatCommande:
    ok: bool
    message: str
    change: bool = False


def executer_commande(jeu: Jeu, params: dict, texte: str) -> ResultatCommande:
    """Interprète « /nom valeur ». Modifie `params` en place."""
    morceaux = texte.strip().lstrip("/").split(maxsplit=1)
    if not morceaux:
        return ResultatCommande(False, "commande vide")
    nom = morceaux[0].lower()
    if nom in {"aide", "help", "?"}:
        lignes = [f"/{p.nom} <{p.type.__name__}>  {p.libelle}{' — ' + p.aide if p.aide else ''}" for p in jeu.params]
        return ResultatCommande(True, "\n".join(lignes + ["/params  état courant", "/quitter"]))
    if nom == "params":
        return ResultatCommande(True, "  ".join(f"{p.nom}={p.afficher(params.get(p.nom))}" for p in jeu.params))
    p = jeu.param(nom)
    if p is None:
        return ResultatCommande(False, f"commande inconnue : /{nom} (tape /aide)")
    if len(morceaux) == 1:
        return ResultatCommande(True, f"{p.nom} = {p.afficher(params.get(p.nom))}")
    try:
        params[p.nom] = p.convertir(morceaux[1])
    except ValueError as exc:
        return ResultatCommande(False, str(exc))
    return ResultatCommande(True, f"{p.nom} → {p.afficher(params[p.nom])}", change=True)
