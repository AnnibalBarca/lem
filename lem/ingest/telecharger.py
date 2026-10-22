"""Téléchargement des sources lexicales — HORS du dépôt, dans $LEM_HOME/sources (~/.lem/sources).

Le dépôt ne contient jamais de données : seulement le code qui les va chercher.
Les URL ci-dessous sont celles connues à la rédaction ; elles peuvent bouger. Chaque source
essaie ses URL dans l'ordre, et `--url` permet d'en imposer une.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

DOSSIER = Path(os.environ.get("LEM_HOME", Path.home() / ".lem")) / "sources"


@dataclass
class Source:
    nom: str
    urls: list[str]
    fichier: str
    note: str
    page: str | None = None          # page d'index où chercher le dernier dump (JeuxDeMots)
    motif: str | None = None


SOURCES = {
    "lexique": Source(
        "Lexique 3.83",
        ["http://www.lexique.org/databases/Lexique383/Lexique383.tsv",
         "http://www.lexique.org/databases/Lexique383/Lexique383.zip"],
        "Lexique383.tsv",
        "≈ 25 Mo · CC BY-SA 4.0 · New, Pallier et al.",
    ),
    "wiktionnaire": Source(
        "Wiktionnaire français (wiktextract, kaikki.org)",
        ["https://kaikki.org/frwiktionary/raw-wiktextract-data.jsonl.gz"],
        "frwiktionary.jsonl.gz",
        "≈ 1–2 Go compressé (toutes langues ; l'ingestion garde le français) · CC BY-SA. "
        "Attention : kaikki.org/dictionary/French est le Wiktionnaire ANGLAIS (gloses en anglais) — pas celui-là.",
    ),
    "grammalecte": Source(
        "Grammalecte (dictionnaire orthographique, toutes variantes)",
        ["https://grammalecte.net/zip/Grammalecte-fr-v2.3.0.zip"],
        "grammalecte.zip",
        "≈ 6 Mo · dictionnaire sous MPL 2.0 (Dicollecte), code GPL v3 — lu localement, jamais copié dans le dépôt. "
        "Ajoute ≈ 19 000 formes (conjugaisons, pluriels, rectifications de 1990) que le Wiktionnaire n'a pas.",
    ),
    "jeuxdemots": Source(
        "JeuxDeMots (réseau lexical)",
        [],
        "jeuxdemots.txt",
        "≈ plusieurs Go décompressé · licence du projet JeuxDeMots (LIRMM)",
        page="https://www.jeuxdemots.org/JDM-LEXICALNET-FR/",
        motif=r'href="([^"]*LEXICALNET[^"]*\.zip)"',
    ),
}


def _urls(src: Source, client: httpx.Client) -> list[str]:
    if not src.page:
        return src.urls
    page = client.get(src.page)
    page.raise_for_status()
    liens = sorted(set(re.findall(src.motif, page.text)))
    return [httpx.URL(src.page).join(lien).__str__() for lien in liens[-1:]] + src.urls


def telecharger(nom: str, url: str | None = None, dossier: Path = DOSSIER,
                progression: Callable[[int, int | None], None] | None = None) -> Path:
    src = SOURCES[nom]
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / src.fichier
    if cible.exists() and cible.stat().st_size > 0:
        return cible
    erreurs = []
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as client:
        for u in ([url] if url else _urls(src, client)):
            tmp = dossier / (Path(httpx.URL(u).path).name + ".part")
            try:
                with client.stream("GET", u) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0)) or None
                    n = 0
                    with open(tmp, "wb") as f:
                        for bloc in r.iter_bytes(1 << 20):
                            f.write(bloc)
                            n += len(bloc)
                            if progression:
                                progression(n, total)
            except httpx.HTTPError as exc:
                erreurs.append(f"{u} : {exc}")
                tmp.unlink(missing_ok=True)
                continue
            final = tmp.with_suffix("")
            tmp.rename(final)
            if final.suffix == ".zip":
                return _extraire(final, cible)
            if final != cible:
                final.rename(cible)
            return cible
    raise RuntimeError(f"{src.nom} : aucune URL n'a répondu.\n  " + "\n  ".join(erreurs)
                       + "\n  → télécharge-le à la main dans " + str(dossier) + f" sous le nom {src.fichier}")


def _extraire(archive: Path, cible: Path) -> Path:
    with zipfile.ZipFile(archive) as z:
        membres = sorted(z.infolist(), key=lambda m: -m.file_size)
        with z.open(membres[0]) as src, open(cible, "wb") as dst:
            while bloc := src.read(1 << 20):
                dst.write(bloc)
    archive.unlink()
    return cible
