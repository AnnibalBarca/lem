"""Deux jeux de décision lexicale, un seul moteur.

Étalonnage  : « Connais-tu ce mot ? » oui / non. Mots choisis là où l'information est
              maximale (P ≈ 0,5, dimensions variées), avec ~15 % de pseudo-mots pour mesurer
              la surestimation. Un « oui » sur quatre demande une définition (vérifiée plus tard
              par Claude). C'est ce qui place ta carte de connaissance au départ.
Mot ou pas  : décision lexicale rapide : mot réel ou pseudo-mot ? Temps de réponse mesuré,
              erreurs = vies. Les mots réels sont tirés de tes zones floues.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass

from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.journal import Evenement, ecrire
from lem.memory.knowledge import CarteConnaissance
from lem.pseudomots import GenerateurPseudoMots

ETALONNAGE = enregistrer(Jeu(
    id="etalonnage",
    nom="Étalonnage",
    accroche="Placer ta carte : connais-tu ces mots ?",
    description="Test adaptatif oui/non avec pseudo-mots et vérifications par définition.",
    params=[
        Param("questions", "Questions", int, 120, "", 10, 1000),
        Param("pseudo", "Part de pseudo-mots", float, 0.15, "", 0, 0.5),
        Param("verification", "Part de « oui » à définir", float, 0.25, "", 0, 1),
        *PARAMS_FILTRES,
    ],
))

MOT_OU_PAS = enregistrer(Jeu(
    id="mot_ou_pas",
    nom="Mot ou pas mot",
    accroche="Ce mot existe-t-il ? Vite.",
    description="Décision lexicale chronométrée sur tes zones floues.",
    params=[
        Param("vies", "Vies", int, 3, "", 1, 10),
        Param("pseudo", "Part de pseudo-mots", float, 0.5, "", 0.1, 0.9),
        *PARAMS_FILTRES,
    ],
))


@dataclass
class Question:
    texte: str
    entree_id: int | None      # None = pseudo-mot
    verifier: bool = False


class PartieDecision:
    def __init__(self, con: sqlite3.Connection, jeu: str, params: dict, carte: CarteConnaissance | None = None,
                 session_id: int | None = None, rng: random.Random | None = None, lexique=None):
        self.con, self.jeu, self.params, self.session_id = con, jeu, params, session_id
        self.carte = carte or CarteConnaissance(con)
        self.rng = rng or random.Random()
        self.vivier = [r for r in selectionner(con, filtres_depuis(params)) if r["type"] != "nom_propre"]
        if lexique is None:
            from lem.lexique import Lexique
            lexique = Lexique(con)
        # Les normes du lexique (automate) : un pseudo-mot ne doit être aucune forme réelle, lemme ou fléchie.
        self.reels = lexique.normes
        self.gen = GenerateurPseudoMots(lexique.lemmes, rng=self.rng, reels=self.reels)
        self.poses: set[int] = set()
        self.n = 0
        self.fausses_alertes = 0
        self.pseudos_vus = 0
        self.vies = params.get("vies", 10**9)
        self.score = 0
        self.question: Question | None = None

    @property
    def finie(self) -> bool:
        return self.vies <= 0 or (self.jeu == "etalonnage" and self.n >= self.params["questions"])

    def _mot_informatif(self) -> sqlite3.Row | None:
        libres = [r for r in self.vivier if r["id"] not in self.poses]
        if not libres:
            return None
        ech = self.rng.sample(libres, min(300, len(libres)))
        if self.jeu == "etalonnage":
            # Information de Fisher d'un item de Rasch : p(1 − p). Petite part d'aléa pour varier les dimensions.
            ech.sort(key=lambda r: -(self.carte.p(r["id"]) * (1 - self.carte.p(r["id"])) + 0.05 * self.rng.random()))
        else:
            ech.sort(key=lambda r: abs(self.carte.p(r["id"]) - 0.5) + 0.2 * self.rng.random())
        return ech[0]

    def suivante(self) -> Question:
        self.n += 1
        if self.rng.random() < self.params["pseudo"]:
            p = self.gen.generer()
            if p:
                self.question = Question(p, None)
                return self.question
        r = self._mot_informatif()
        if r is None:
            self.question = Question(self.gen.generer() or "xyzzy", None)
            return self.question
        self.poses.add(r["id"])
        verifier = self.jeu == "etalonnage" and self.rng.random() < self.params.get("verification", 0)
        self.question = Question(r["forme"], r["id"], verifier)
        return self.question

    def repondre(self, oui: bool, latence_ms: int) -> bool:
        q = self.question
        reel = q.entree_id is not None
        if not reel:
            self.pseudos_vus += 1
            self.fausses_alertes += int(oui)
        correct = oui == reel
        if self.jeu == "mot_ou_pas":
            self.score += int(correct)
            self.vies -= int(not correct)
        ev = ecrire(self.con, self.session_id, Evenement(
            self.jeu, "reponse", entree_id=q.entree_id, trace="forme", reponse="oui" if oui else "non",
            correct=correct, latence_ms=latence_ms,
            data={"pseudo": None if reel else q.texte, "fausses_alertes": self.fausses_alertes, "fsrs": False}))
        if reel:
            # Un « oui » vaut moins si tu as tendance à dire oui aux pseudo-mots.
            taux_fa = self.fausses_alertes / self.pseudos_vus if self.pseudos_vus else 0.0
            if self.jeu == "etalonnage":
                self.carte.observer(q.entree_id, 0.9 if oui else 0.05, poids=1.0 - 0.8 * taux_fa if oui else 1.0,
                                    source="etalonnage", evenement_id=ev)
            else:
                self.carte.observer(q.entree_id, 0.7 if oui else 0.1, poids=0.4, source="mot_ou_pas", evenement_id=ev)
        return correct

    def verification(self, definition: str, latence_ms: int) -> int:
        """Définition demandée après un « oui » : notée plus tard par Claude (exercice recep)."""
        q = self.question
        ev = ecrire(self.con, self.session_id, Evenement(
            self.jeu, "reponse", entree_id=q.entree_id, trace="recep", reponse=definition,
            latence_ms=latence_ms, data={"verification": True}))
        # En attente : la carte ne bouge qu'une fois la définition notée (Claude, juge), au prochain rejeu.
        self.carte.observer(q.entree_id, None, source="etalonnage:verification", evenement_id=ev, depuis_note=True)
        return ev
