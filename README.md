# lem

Apprendre **le lexique français entier**, en sachant ce que tu sais déjà, en attaquant au moindre
coût ce qui te manque, puis en consolidant par répétition espacée (FSRS). Une bibliothèque de jeux
en terminal, tous branchés sur une même base : chaque réponse, dans chaque jeu, nourrit la carte de
ta mémoire.

```
 ░░░░░               ░░░░░░░░░░░░░░░░░   ░░░░░           ░░░░░
┌───┐▒              ┌───┬───┬───┬───┐▒  ┌───┐▒          ┌───┐▒
│ L │▒              │ E │ S │ P │ R │▒  │ M │▒░░░░   ░░░│ E │▒
├───┤▒              ├───┼───┴───┴───┘   ├───┼───┐▒  ┌───┼───┤▒
│ A │▒              │ I │▒░░░░░░░░      │ M │ O │▒░░│ I │ R │▒
├───┤▒              ├───┼───┬───┐▒      ├───┼───┼───┼───┼───┤▒
│ N │▒              │ T │ E │ S │▒      │ E │▒  │ M │▒  │ E │▒
├───┤▒              ├───┼───┴───┘       ├───┤▒  └───┘   ├───┤▒
│ G │▒░░░░░░░░░░░░  │ P │▒░░░░░░░░░░░░  │ M │▒          │ O │▒
├───┼───┬───┬───┐▒  ├───┼───┬───┬───┐▒  ├───┤▒          ├───┤▒
│ U │ E │ L │ A │▒  │ R │ I │ T │ E │▒  │ I │▒          │ R │▒
└───┴───┴───┴───┘   └───┴───┴───┴───┘   └───┘           └───┘
```

## Les jeux

| Jeu | Ce qu'il fait | Ce qu'il mesure |
|---|---|---|
| **Cartes** | mot → sens (texte libre ou synonymes), sens → mot (Tab = une lettre), duels de confusables | précision, type d'erreur, mot réellement défini, lettres nécessaires |
| **Bombe** | façon JKLM : une suite de lettres, un mot. Modes libre / apprentissage / confusables, répétition intelligente, extension vers la cible | vocabulaire actif spontané, disponibilité par suite, confusions |
| **Mémoire verbale** | façon Human Benchmark, avec les mots les plus étranges du français, et capture d'images mentales | mémoire de reconnaissance, effet de l'imagerie (mode expérience) |
| **Mot ou pas mot** | décision lexicale chronométrée contre des pseudo-mots | temps d'accès lexical |
| **Étalonnage** | test adaptatif oui/non, avec pseudo-mots et vérifications | place ta carte de connaissance |
| **Atlas** | ta carte, ton profil, ta signature d'imprécision, tes confusions, ta métacognition | — |

Dans tous les jeux : `/aide`, `/domaine science,droit`, `/etat flous`, `/vies 5`, `/mode confusables`, `/params`…
Les réglages sont enregistrés dans la base.

## Installation (macOS)

```sh
brew install uv ollama           # si besoin
git clone https://github.com/AnnibalBarca/lem && cd lem
uv tool install -e .             # installe la commande `lem`
lem demo                         # base de démo (~80 mots), sans rien télécharger
```

## La vraie base

```sh
# 1. Sources (à télécharger une fois)
#    Lexique 3.83      http://www.lexique.org  → Lexique383.tsv
#    Wiktionnaire fr   https://kaikki.org/frwiktionary/  → extraction JSONL (wiktextract)
#    JeuxDeMots        https://www.jeuxdemots.org/JDM-LEXICALNET-FR/  → dump texte (optionnel)
lem ingest lexique ~/Downloads/Lexique383.tsv
lem ingest wiktionnaire ~/Downloads/fr-extract.jsonl.gz
lem ingest wordfreq
lem ingest jeuxdemots ~/Downloads/JDM-LEXICALNET-FR.txt    # optionnel
lem prior --cible 45000          # a priori « adulte cultivé moyen »

# 2. Vecteurs sémantiques (une fois, ~1 h sur un M4)
ollama pull bge-m3 && lem index

# 3. Jouer
lem
```

Pour noter les définitions avec ton abonnement Claude, en fin de session : menu « Noter avec Claude »,
ou `lem noter --modele sonnet` (ou `haiku` pour les grosses fournées). La commande `claude` (Claude
Code) doit être installée.

## Développement

```sh
uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest
```

L'architecture, les choix et les chiffres (coûts, calcul, mémoire) sont dans [DESIGN.md](DESIGN.md).
