"""Vocabulaire fermé de la notation.

Tout ce qu'un correcteur (Claude aujourd'hui, Jev demain) renvoie est un choix
dans une liste fermée ou un niveau sur une échelle décrite en mots. C'est ce qui
rend les notes comparables dans le temps, entre correcteurs, et analysables
statistiquement — ta « signature d'imprécision » se lit directement ici.
"""

from __future__ import annotations

from enum import Enum


class Erreur(str, Enum):
    EXACTE = "exacte"
    JUSTE_IMPRECISE = "juste_imprecise"        # bonne zone, contours flous
    TROP_GENERALE = "trop_generale"            # hyperonyme : « une sorte de mot » pour acronyme
    TROP_SPECIFIQUE = "trop_specifique"        # un cas particulier pris pour le tout
    CONNOTATION_SEULE = "connotation_seule"    # seulement la couleur affective (« péjoratif, triste »)
    CONTEXTE_SEUL = "contexte_seul"            # où/quand on l'emploie, pas ce que ça veut dire
    SENS_VOISIN = "sens_voisin"                # sens d'un voisin sémantique (sigle pour acronyme)
    CONFUSION_FORMELLE = "confusion_formelle"  # sens d'un mot qui lui ressemble (thuriféraire → atrabilaire)
    AUTRE_SENS = "autre_sens"                  # autre sens valide d'un mot polysémique
    CONTRESENS = "contresens"                  # l'inverse ou sans rapport
    VIDE = "vide"                              # pas de réponse / « je ne sais pas »


DESCRIPTION_ERREUR = {
    Erreur.EXACTE: "La définition est correcte et précise.",
    Erreur.JUSTE_IMPRECISE: "La bonne idée, mais floue ou incomplète.",
    Erreur.TROP_GENERALE: "Décrit une catégorie plus large (hyperonyme) sans le trait distinctif.",
    Erreur.TROP_SPECIFIQUE: "Décrit seulement un cas particulier du sens.",
    Erreur.CONNOTATION_SEULE: "Ne donne que la tonalité affective ou évaluative du mot.",
    Erreur.CONTEXTE_SEUL: "Décrit le contexte d'emploi et non le sens.",
    Erreur.SENS_VOISIN: "Donne le sens d'un mot sémantiquement voisin.",
    Erreur.CONFUSION_FORMELLE: "Donne le sens d'un mot qui ressemble par la forme ou le son.",
    Erreur.AUTRE_SENS: "Donne un autre sens, valide, du même mot.",
    Erreur.CONTRESENS: "Sens inverse ou sans rapport.",
    Erreur.VIDE: "Pas de réponse exploitable.",
}

# Échelle de précision 1..5, décrite en mots (forme exacte d'une question « Score » chez Jev).
PRECISION = {
    1: "aucune idée du sens",
    2: "souvenir vague : zone de sens ou connotation seulement",
    3: "approximatif : l'essentiel, sans le trait distinctif",
    4: "juste : définition correcte",
    5: "experte : précise, nuancée, distingue des voisins",
}

# Précision → note FSRS. Un souvenir vague est traité comme un échec : c'est précisément
# ce qu'on veut réapprendre (et la précision fine reste dans le journal).
NOTE_PAR_PRECISION = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4}

ERREURS_FAUTIVES = {
    Erreur.SENS_VOISIN, Erreur.CONFUSION_FORMELLE, Erreur.CONTRESENS, Erreur.VIDE,
    Erreur.CONNOTATION_SEULE, Erreur.CONTEXTE_SEUL,
}
