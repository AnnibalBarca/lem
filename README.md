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

**Graphie exigeante.** Aucune variante n'est acceptée ; seuls les accents *manquants* sont tolérés par
défaut (« creolisation » vaut « créolisation », un accent faux reste une faute ; `/accents non` les exige,
pour tous les jeux). Chaque faute est identifiée
(accent, consonne double, h muet, inversion, touche voisine…), classée (orthographe ou frappe),
montrée lettre par lettre en jaune, et le mot doit être retapé juste. Il revient ensuite trois cartes
plus loin, puis à intervalles espacés, sur une trace FSRS dédiée.

**Dictée.** lem n'embarque pas de dictée : ta propre dictée (Whisper, F8 sous Windows, Option droite
sous macOS) écrit dans les champs comme un clavier. lem repère la saisie dictée (insertion d'un bloc)
et en tient compte : la graphie et le recopiage se tapent au clavier, une faute écrite par la dictée
n'est jamais comptée comme la tienne, et les temps de réponse sont séparés (clavier / dictée).

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

Les données vont dans `~/.lem/` (sources dans `~/.lem/sources`), **jamais dans le dépôt**.

```sh
# 1. Sources : Lexique 3.83 + Wiktionnaire français (wiktextract), puis ingestion et a priori
lem telecharger lexique wiktionnaire --ingerer
lem telecharger jeuxdemots && lem ingest jeuxdemots ~/.lem/sources/jeuxdemots.txt   # optionnel

# 2. Vecteurs sémantiques, puis seuils mesurés sur la base réelle
#    (1,26 M textes ; --zipf-min 0 --rang-max 3 se limite aux entrées attestées et aux 3 premiers sens :
#     529 k textes, 1 h 44 avec Ollama sous Windows. Un `lem index` ultérieur complète ce qui manque.)
ollama pull bge-m3 && lem index --zipf-min 0 --rang-max 3 && lem calibrer

# 3. Jouer
lem
```

Sur une machine modeste, `LEM_VECTEURS=float16` divise par deux la RAM des vecteurs (≈ 1,1 Go au lieu de 2,2).
Résultats et chiffres de la première intégration réelle : [docs/rapport-integration-wsl.md](docs/rapport-integration-wsl.md) ;
téléphone et synchronisation entre machines : [docs/passation-mobile-sync.md](docs/passation-mobile-sync.md).

Pour faire tout ça par un Claude Code local : [docs/claude-local.md](docs/claude-local.md) (macOS) ou
[docs/claude-local-wsl.md](docs/claude-local-wsl.md) (Windows + WSL2). Sous WSL2, si Ollama tourne côté
Windows, `OLLAMA_HOST` indique à lem où le trouver.

Pour noter les définitions avec ton abonnement Claude, en fin de session : menu « Noter avec Claude »,
ou `lem noter --modele sonnet` (ou `haiku` pour les grosses fournées). La commande `claude` (Claude
Code) doit être installée.

## Développement

```sh
uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest
```

L'architecture, les choix et les chiffres (coûts, calcul, mémoire) sont dans [DESIGN.md](DESIGN.md).
