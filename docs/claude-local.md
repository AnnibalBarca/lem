# Prompt pour Claude Code en local (Mac)

À coller tel quel dans Claude Code, lancé dans un dossier de travail sur ton Mac.

---

Tu travailles sur **lem** (https://github.com/AnnibalBarca/lem), un moteur d'apprentissage du lexique
français en terminal (Python, Textual, SQLite, FSRS, bge-m3). Le code a été écrit dans un environnement
cloud qui n'avait pas accès aux dictionnaires ni à Ollama. Ta mission : **brancher la vraie base sur
ce Mac** et me rendre un rapport chiffré. Lis d'abord `README.md` et `DESIGN.md`.

Machine : MacBook M4 15 pouces, 16 Go de RAM. Les données vont dans `~/.lem/` (jamais dans le dépôt).

## Étapes

1. **Prérequis.** Vérifie ou installe `uv`, `ollama` (Homebrew) et Claude Code (`claude`). Clone le
   dépôt, puis `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest`. Tous les tests doivent passer
   avant d'aller plus loin.
2. **Sources.** `lem telecharger lexique wiktionnaire`. Si une URL a bougé, trouve la bonne (Lexique 3.83
   sur lexique.org ; l'extraction wiktextract du Wiktionnaire **français** sur kaikki.org/frwiktionary —
   PAS kaikki.org/dictionary/French, qui est le Wiktionnaire anglais), mets à jour
   `lem/ingest/telecharger.py` et relance. JeuxDeMots est optionnel (`lem telecharger jeuxdemots`).
3. **Vérifier les lecteurs sur les vrais fichiers avant d'ingérer.** Les lecteurs (`lem/ingest/__init__.py`)
   ont été écrits d'après le format documenté, sans voir les fichiers réels. Inspecte une vingtaine de
   lignes de chaque source : en-têtes du TSV de Lexique ; dans le JSONL wiktextract, les champs `word`,
   `lang_code`, `pos`, `senses[].glosses`, `topics` / `raw_tags`, `etymology_texts`, `forms`, `synonyms`,
   `sounds`. Corrige les lecteurs si besoin et ajoute un test avec un extrait réel (quelques lignes) dans
   `tests/`.
4. **Ingestion.** `lem ingest lexique …`, `lem ingest wiktionnaire …`, `lem ingest wordfreq`, puis
   `lem prior --cible 45000`. Rapporte : nombre d'entrées, de sens, de formes, de domaines, de morphèmes,
   de relations de synonymie, la répartition par origine (grec, latin…) et la durée de chaque étape.
   Contrôle à la main une dizaine d'entrées (acronyme, thuriféraire, sigle, mitose, kjökkenmödding,
   « les sages de la rue Montpensier » si elle existe) : définitions en français, domaines, origine,
   morphèmes plausibles.
5. **Vecteurs.** `ollama pull bge-m3`, puis `lem index`. Mesure le débit (textes par seconde). Si
   l'estimation dépasse 2 h, propose-moi de restreindre l'indexation (lemmes avec Zipf > 0, sens de rang ≤ 3)
   avant de lancer. Rapporte la taille de la base ensuite.
6. **Calibrage.** `lem calibrer`. Rapporte les seuils obtenus et les mesures (synonymes vs hasard). Si
   l'écart entre la médiane des synonymes et le 95e centile du hasard est trop faible (< 0,05), dis-le.
7. **Performance.** Mesure, sur la base réelle : temps de démarrage de `lem` ; latence d'une notation
   `noter_recep` (plongement, puis dictionnaire inversé sur tous les sens) ; construction d'une
   `PartieBombe` (dictionnaire de ~1 M formes) ; `CarteConnaissance.recalculer_tout()`. Si les plus proches
   voisins dépassent 150 ms et que la RAM le permet, garde la matrice en float32 dans `lem/vecteurs.py`.
   Si `recalculer_tout` dépasse 10 s, vectorise-le (numpy) sans changer son résultat.
8. **Essai réel.** Lance `lem` et joue 2 minutes à chaque jeu. Vérifie le rendu du logo et des couleurs
   dans le Terminal macOS et dans iTerm2 si présent. Lance `lem noter --modele haiku` sur les quelques
   définitions produites, pour vérifier la chaîne Claude headless de bout en bout.

## Règles

- Ne change ni l'architecture (journal en ajout seul, état dérivé et rejouable), ni le design
  (monochrome ; la couleur ne sert qu'à signifier).
- **Graphie** : aucune variante orthographique n'est acceptée ; seule exception, réglable et choisie par
  l'utilisateur : les accents manquants (`accents_tolerants`). N'ajoute aucune autre tolérance.
- Aucune donnée dans le dépôt (`~/.lem/` uniquement). Pas de secrets dans les commits.
- Travaille sur une branche `local/integration`, en commits petits et clairs, puis pousse-la. Ne pousse
  pas sur `main`.
- Termine par un rapport : ce qui marche, ce que tu as corrigé, les chiffres, et ce qui reste à faire.
