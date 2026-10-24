"""Which definitions make a good clue when the player must PRODUCE the word (sense → word, spelling cards, duels).

A clue is good if it points to one word. These do not:

  * a one-word definition (« Microphone. » for *micro*, « Chant. » for *hymne*): any synonym fits and the
    player writes the clue itself ;
  * a definition that starts with the word itself or with a longer form of it, i.e. a clipping or an
    abbreviation (*micro* → « Microphone d'un journaliste… », *chap.* → « Chapitre ») ;
  * a pointer to another word: « Abréviation de… », « Sigle de… », « Synonyme de… », « Variante de… » ;
  * a definition that contains the target word itself.

Recognition cards (word → sense) are not concerned: there the word is shown and every sense is welcome.
"""

from __future__ import annotations

import re

from lem.texte import normaliser

RE_RENVOI = re.compile(r"^(?:abr[ée]viation|apocope|aph[ée]r[èe]se|troncation|sigle|acronyme|synonyme|variante|"
                       r"autre orthographe|ancienne orthographe|forme|pluriel|f[ée]minin|masculin)\b"
                       r"(?:\s+(?:orthographique|graphique|familière|populaire))?\s*(?:de|du|des|d['’])", re.I)
RE_MOT = re.compile(r"[a-zà-ÿœæ]+", re.I)


def usable_as_clue(definition: str, form: str) -> bool:
    """True if `definition` can be shown as the clue for `form` without giving the answer away or leaving it open."""
    d = " ".join(definition.split()).strip()
    mots = RE_MOT.findall(d)
    if len(mots) <= 1 or RE_RENVOI.match(d):
        return False
    cible = normaliser(form).replace("-", "").replace("'", "")
    premier = normaliser(mots[0])
    if len(cible) >= 3 and (premier.startswith(cible) or (len(premier) >= 3 and cible.startswith(premier))):
        return False                                      # clipping : micro / microphone, chap / chapitre
    if cible in {normaliser(m) for m in mots} or (len(cible) >= 4 and cible in normaliser(d).replace("-", "")):
        return False                                      # the clue contains the answer
    return True


def first_usable_sense(senses: list[tuple[int, str]], form: str) -> tuple[int, str] | None:
    """The first (id, definition) of `senses` that is a good clue, or None."""
    return next(((i, d) for i, d in senses if usable_as_clue(d, form)), None)
