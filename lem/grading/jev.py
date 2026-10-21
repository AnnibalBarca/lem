"""Adaptateur Jev (TypeSafe AI) — modèle de DÉCISION, pas de génération.

Jev (accès anticipé depuis septembre 2026) ne produit pas de texte : il répond à
trois formes de questions, avec une probabilité calibrée :
  * Choice : une option parmi ≤ 255, probabilité par option ;
  * Score  : un niveau sur une échelle ordonnée de 2 à 10 niveaux décrits en mots ;
  * Noul   : oui / non, une probabilité.

Conséquence pour lem : Jev ne peut juger que ce qu'on lui donne en liste fermée.
La base doit donc fournir les OPTIONS — c'est là que se joue l'importance réelle
des structures lexicales :

  sens (polysémie)        → indispensable : options du Choice « quel sens vise-t-il ? »
  grappes de confusables  → indispensable : options du Choice « quel mot a-t-il réellement défini ? »
  taxonomie d'erreurs     → indispensable : options du Choice « quel type d'erreur ? »
  échelle de précision    → indispensable : niveaux du Score
  morphèmes               → utiles, secondaires : Choice « quel morphème est mal compris ? »
  notes « pivots » Obsidian → inutiles : Jev ne lit pas de graphe, il lit des options.

Le client n'est pas encore implémenté (API sur liste d'attente, support du
français non vérifié). Les questions sont déjà construites : il restera à les
envoyer, puis à étalonner Jev contre tes notes et celles de Claude.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lem.grading.taxonomie import DESCRIPTION_ERREUR, PRECISION, Erreur


@dataclass
class QuestionChoice:
    contexte: str
    question: str
    options: list[str]


@dataclass
class QuestionScore:
    contexte: str
    question: str
    niveaux: list[str]


@dataclass
class QuestionsDefinition:
    sens: QuestionChoice
    precision: QuestionScore
    erreur: QuestionChoice
    mot_reel: QuestionChoice
    meta: dict = field(default_factory=dict)


def questions_definition(mot: str, sens: list[tuple[int, str]], voisins: list[tuple[str, str]],
                         reponse: str) -> QuestionsDefinition:
    ctx = f"Mot : {mot}\nRéponse de l'apprenant : {reponse}"
    return QuestionsDefinition(
        sens=QuestionChoice(ctx, "Quel sens de référence la réponse vise-t-elle ?",
                            [d for _, d in sens] + ["aucun de ces sens"]),
        precision=QuestionScore(ctx, "À quel point la réponse définit-elle correctement le mot ?",
                                [PRECISION[k] for k in sorted(PRECISION)]),
        erreur=QuestionChoice(ctx, "Quel type de réponse est-ce ?", [DESCRIPTION_ERREUR[e] for e in Erreur]),
        mot_reel=QuestionChoice(ctx, "Quel mot la réponse définit-elle réellement ?",
                                [mot] + [f"{v} : {d}" for v, d in voisins] + ["un autre mot"]),
        meta={"sens_ids": [i for i, _ in sens], "erreurs": [e.value for e in Erreur]},
    )


class JevClient:  # pragma: no cover - en attente d'accès API
    def __init__(self, cle_api: str):
        self.cle_api = cle_api

    def noter(self, q: QuestionsDefinition):
        raise NotImplementedError("Accès Jev en attente : brancher l'API TypeSafe ici.")
