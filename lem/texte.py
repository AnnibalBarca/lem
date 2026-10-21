"""Normalisation de texte partagée (sans dépendance interne)."""

from __future__ import annotations

import unicodedata


def sans_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normaliser(s: str) -> str:
    """Minuscules, sans accents, ligatures dépliées : « Œuvre » → « oeuvre »."""
    return sans_accents(s.strip().lower().replace("œ", "oe").replace("æ", "ae"))
