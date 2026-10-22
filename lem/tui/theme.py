"""La couleur ne sert qu'à signifier. Tout le reste est blanc, gris, noir.

Quatre usages seulement :
  * l'état de la mémoire (froid → chaud : inconnu → acquis) ;
  * la proximité entre deux mots (rouge, magenta, orange, violet) ;
  * la graphie : jaune vif, réservé aux fautes d'orthographe (lem.orthographe.GRAPHIE) ;
  * les catégories (domaines), avec une teinte stable par nom.
"""

from __future__ import annotations

import hashlib

from rich.text import Text

from lem.similarite import Proximite

BLANC = "#f2f2f2"
GRIS = "#8a8a8a"
SOMBRE = "#4a4a4a"

ETATS = {
    "non_explore": ("#4a4a4a", "non exploré"),
    "inconnu": ("#5f87d7", "inconnu"),
    "flou": ("#d7af5f", "flou"),
    "en_cours": ("#87d7d7", "en cours"),
    "presque": ("#afd787", "presque acquis"),
    "acquis": ("#5fd75f", "acquis"),
}

PROXIMITES = {
    Proximite.JUMEAU: "#ff5f5f",
    Proximite.FORME: "#d75fd7",
    Proximite.SENS: "#ffaf5f",
    Proximite.MEMOIRE: "#af87ff",
    Proximite.AUCUNE: GRIS,
}

PALETTE_CATEGORIES = ["#87afd7", "#d7af87", "#87d7af", "#d787af", "#afafd7", "#d7d787", "#87d7d7", "#d7a0a0"]


def etat_de(p: float | None, n_obs: int = 0, en_cours: bool = False, stabilite: float | None = None) -> str:
    if stabilite is not None and 3 <= stabilite <= 30:
        return "presque"
    if en_cours:
        return "en_cours"
    if n_obs == 0:
        if p is not None and p >= 0.85:
            return "acquis"
        return "inconnu" if p is not None and p <= 0.15 else "non_explore"
    if p is None or p < 0.3:
        return "inconnu"
    if p < 0.75:
        return "flou"
    if p < 0.9:
        return "presque"
    return "acquis"


def couleur_categorie(nom: str) -> str:
    h = hashlib.blake2b(nom.encode(), digest_size=1).digest()[0]
    return PALETTE_CATEGORIES[h % len(PALETTE_CATEGORIES)]


def thermometre(temperature: float | None, largeur: int = 24) -> Text:
    """Barre de température façon Cemantix : bleu glacé → rouge brûlant."""
    if temperature is None:
        return Text("— pas de mesure sémantique —", style=SOMBRE)
    n = round(temperature / 100 * largeur)
    t = Text()
    for i in range(largeur):
        f = i / max(1, largeur - 1)
        r, g, b = int(95 + f * 160), int(135 - f * 40), int(215 - f * 120)
        t.append("█" if i < n else "·", style=f"#{r:02x}{g:02x}{b:02x}" if i < n else SOMBRE)
    t.append(f"  {temperature:4.0f}°", style=BLANC)
    return t


def barre(valeur: float, largeur: int = 20, style: str = BLANC) -> Text:
    n = round(max(0.0, min(1.0, valeur)) * largeur)
    return Text("█" * n, style=style) + Text("░" * (largeur - n), style=SOMBRE)


CSS = f"""
Screen {{
    background: #000000;
    color: {BLANC};
}}
#logo {{ width: auto; height: auto; margin: 1 0 1 0; }}
#menu {{ margin: 1 0 0 0; }}
.centre {{ align-horizontal: center; width: 100%; }}
.sous-titre {{ color: {GRIS}; text-align: center; width: 100%; }}
.discret {{ color: {SOMBRE}; }}
OptionList {{
    background: #000000;
    border: none;
    width: 72;
    height: auto;
    max-height: 14;
    padding: 0 1;
}}
OptionList > .option-list--option-highlighted {{
    background: #1c1c1c;
    color: #ffffff;
    text-style: bold;
}}
OptionList:focus > .option-list--option-highlighted {{
    background: #262626;
}}
Input {{
    background: #000000;
    border: round #3a3a3a;
    color: #ffffff;
    width: 100%;
}}
Input:focus {{ border: round {BLANC}; }}
Footer {{ background: #0a0a0a; color: {GRIS}; }}
FooterKey .footer-key--key {{ background: #1c1c1c; color: {BLANC}; }}
#zone {{ width: 90; max-width: 100%; height: 1fr; padding: 1 2; }}
#entete {{ height: 1; color: {GRIS}; }}
#stimulus {{ height: auto; min-height: 5; content-align: center middle; text-align: center; padding: 1 0; }}
#retour {{ height: auto; min-height: 1; padding: 0 0 1 0; }}
#detail {{ height: auto; }}
#journal {{ height: 1fr; background: #000000; border: none; }}
Toast {{ background: #111111; color: {BLANC}; border-left: outer {GRIS}; }}
Toast.-information {{ border-left: outer {GRIS}; }}
Toast .toast--title {{ color: {GRIS}; }}
"""
