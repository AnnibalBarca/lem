"""Le logo : trois lettres géantes bâties de cubes, chaque cube portant une lettre.

L est fait de LANGUE, E d'ESPRIT, M de MÉMOIRE. Les cubes partagent leurs arêtes
(grille de caractères de boîte) et une extrusion ombrée vers le haut-droite donne
l'illusion de perspective : face supérieure claire, face latérale plus sombre.
"""

from __future__ import annotations

import random

from rich.text import Text

GLYPHES = {
    "L": ["X...", "X...", "X...", "X...", "XXXX"],
    "E": ["XXXX", "X...", "XXX.", "X...", "XXXX"],
    "M": ["X...X", "XX.XX", "X.X.X", "X...X", "X...X"],
}
MOTS = {"L": "LANGUE", "E": "ESPRIT", "M": "MEMOIRE"}
CW, CH = 4, 2        # une cellule = 4 colonnes × 2 lignes (arêtes partagées)
DX, DY = 1, 1        # profondeur de l'extrusion

BOITE = {
    (0, 0, 1, 1): "─", (1, 1, 0, 0): "│", (0, 1, 0, 1): "┌", (0, 1, 1, 0): "┐", (1, 0, 0, 1): "└",
    (1, 0, 1, 0): "┘", (1, 1, 0, 1): "├", (1, 1, 1, 0): "┤", (0, 1, 1, 1): "┬", (1, 0, 1, 1): "┴",
    (1, 1, 1, 1): "┼", (0, 0, 0, 1): "─", (0, 0, 1, 0): "─", (1, 0, 0, 0): "│", (0, 1, 0, 0): "│",
}

STYLE = {"arete": "bold #f2f2f2", "lettre": "bold #ffffff", "dessus": "#8a8a8a", "cote": "#4a4a4a"}


def cellules(texte: str = "LEM") -> list[tuple[int, int, str]]:
    out, x0 = [], 0
    for ch in texte:
        g, k = GLYPHES[ch], 0
        for r, ligne in enumerate(g):
            for c, v in enumerate(ligne):
                if v == "X":
                    out.append((r, x0 + c, MOTS[ch][k % len(MOTS[ch])]))
                    k += 1
        x0 += len(g[0]) + 1
    return out


def _aretes(cells, ox: int, oy: int) -> dict[tuple[int, int], str]:
    hz, vt = set(), set()
    for r, c, _ in cells:
        hz |= {(r, c), (r + 1, c)}
        vt |= {(r, c), (r, c + 1)}
    g = {}
    for rl, c in hz:
        for x in range(ox + c * CW + 1, ox + (c + 1) * CW):
            g[(oy + rl * CH, x)] = "─"
    for r, cl in vt:
        for y in range(oy + r * CH + 1, oy + (r + 1) * CH):
            g[(y, ox + cl * CW)] = "│"
    points = {(rl, cl) for rl, c in hz for cl in (c, c + 1)} | {(rl, cl) for r, cl in vt for rl in (r, r + 1)}
    for rl, cl in points:
        cle = (int((rl - 1, cl) in vt), int((rl, cl) in vt), int((rl, cl - 1) in hz), int((rl, cl) in hz))
        g[(oy + rl * CH, ox + cl * CW)] = BOITE.get(cle, "┼")
    return g


def grille(texte: str = "LEM", lettres: dict | None = None) -> dict[tuple[int, int], tuple[str, str]]:
    return grille_cellules(cellules(texte), lettres)


def grille_cellules(cells, lettres: dict | None = None) -> dict[tuple[int, int], tuple[str, str]]:
    g: dict[tuple[int, int], tuple[str, str]] = {}
    pleines = {(r, c) for r, c, _ in cells}
    for r, c, _ in cells:                       # extrusion (arrière), décalée vers le haut-droite
        for y in range(r * CH, (r + 1) * CH + 1):
            for x in range(DX + c * CW, DX + (c + 1) * CW + 1):
                dessus = y == r * CH and (r - 1, c) not in pleines
                if g.get((y, x), ("",))[0] != "cote":
                    g[(y, x)] = ("dessus", "░") if dessus else ("cote", "▒")
    for r, c, _ in cells:                       # les faces avant masquent l'arrière
        for y in range(DY + r * CH, DY + (r + 1) * CH + 1):
            for x in range(c * CW, (c + 1) * CW + 1):
                g.pop((y, x), None)
    for k, v in _aretes(cells, 0, DY).items():
        g[k] = ("arete", v)
    for i, (r, c, l) in enumerate(cells):
        g[(DY + r * CH + 1, c * CW + 2)] = ("lettre", (lettres or {}).get(i, l))
    return g


def rendre(texte: str = "LEM", lettres: dict | None = None) -> Text:
    return _texte(grille(texte, lettres))


def rangee(mot: str, style_lettre: str | None = None) -> Text:
    """Un mot écrit dans une rangée de cubes (la suite de lettres de la Bombe, par ex.)."""
    cells = [(0, i, ch.upper()) for i, ch in enumerate(mot)]
    t = _texte(grille_cellules(cells))
    if style_lettre:
        t.highlight_regex(r"[A-ZÀ-Ý]", style_lettre)
    return t


def _texte(g: dict[tuple[int, int], tuple[str, str]]) -> Text:
    h, w = max(y for y, _ in g) + 1, max(x for _, x in g) + 1
    t = Text(no_wrap=True)
    for y in range(h):
        for x in range(w):
            role, ch = g.get((y, x), ("", " "))
            t.append(ch, STYLE.get(role, ""))
        if y < h - 1:
            t.append("\n")
    return t


def brouiller(progres: float, rng: random.Random, texte: str = "LEM") -> Text:
    """Effet de décodage : les lettres des cubes défilent puis se figent de gauche à droite."""
    cells = cellules(texte)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n_fixes = int(progres * len(cells))
    lettres = {i: rng.choice(alphabet) for i in range(n_fixes, len(cells))}
    return rendre(texte, lettres)


def largeur(texte: str = "LEM") -> int:
    return max(x for _, x in grille(texte)) + 1
