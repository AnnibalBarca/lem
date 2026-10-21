"""Bibliothèque de jeux. Importer ce paquet enregistre tous les jeux dans REGISTRE."""

from lem.games import bombe, cartes, decision, memoire_verbale  # noqa: F401
from lem.games.base import REGISTRE, Jeu  # noqa: F401
