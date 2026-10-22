# Prompt pour Claude Code sous Windows + WSL2

À coller tel quel dans Claude Code, lancé **dans WSL2** (Ubuntu ou équivalent), depuis Windows Terminal.

---

Tu travailles sur **lem** (https://github.com/AnnibalBarca/lem), un moteur d'apprentissage du lexique
français en terminal (Python, Textual, SQLite, FSRS, bge-m3). Le code a été écrit dans un environnement
cloud qui n'avait accès ni aux dictionnaires ni à Ollama. Ta mission : **brancher la vraie base sur ce
PC Windows, via WSL2**, et me rendre un rapport chiffré. Lis d'abord `README.md` et `DESIGN.md`.

## Règles propres à WSL2

- Tout se passe **dans le système de fichiers Linux de WSL** : dépôt dans `~/lem`, données dans
  `~/.lem/`. Jamais sous `/mnt/c/…`, où SQLite et les lectures massives sont très lents.
- L'interface se lance dans **Windows Terminal**, pas dans l'ancienne console : rendu des caractères de
  boîte, couleurs 24 bits, police Cascadia Mono ou une autre police à chasse fixe complète.
- J'ai ma propre dictée Whisper (F8). lem n'en embarque pas : ne lui en ajoute pas, et vérifie que
  rien dans l'interface n'intercepte F8.

## Étapes

1. **Machine.** Relève la configuration : `nproc`, `free -h`, `df -h ~`, la version de WSL
   (`wsl.exe --version` si disponible), et la présence d'un GPU NVIDIA (`nvidia-smi`). Si la RAM vue par
   WSL est inférieure à 8 Go, propose-moi un `%UserProfile%\.wslconfig` (`memory=…`) **sans le modifier
   toi-même**.
2. **Prérequis.** Dans WSL : `git`, `curl`, `build-essential` ; `uv` (installeur officiel d'Astral) ;
   Claude Code (`claude`). Clone le dépôt dans `~/lem`, puis
   `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest`. Tous les tests doivent passer avant
   d'aller plus loin. Installe aussi la commande : `uv tool install -e .`, et vérifie que `lem` est dans
   le PATH.
3. **Ollama et bge-m3.** Choisis l'une des deux voies et explique-moi pourquoi :
   - **A (par défaut)** : Ollama installé **dans WSL** (script officiel d'ollama.com). Si `nvidia-smi`
     marche dans WSL, Ollama utilisera le GPU (CUDA sur WSL) ; sinon le CPU.
   - **B** : Ollama est **déjà** installé côté Windows. Depuis WSL, `localhost` ne l'atteint qu'en mode
     réseau « mirrored ». Sinon, exporte `OLLAMA_HOST=http://<IP de l'hôte Windows>:11434` (IP de la
     passerelle : `ip route show default`) ; Ollama doit alors écouter sur toutes les interfaces côté
     Windows (`OLLAMA_HOST=0.0.0.0`). lem lit `OLLAMA_HOST`. Ne modifie pas `.wslconfig` ni le pare-feu
     Windows sans me demander.

   Puis `ollama pull bge-m3`, et vérifie (adapte l'hôte en voie B) :
   `curl -s http://localhost:11434/api/embed -d '{"model":"bge-m3","input":["test"]}'`.
4. **Sources.** `lem telecharger lexique wiktionnaire`. Si une URL a bougé, trouve la bonne (Lexique 3.83
   sur lexique.org ; l'extraction wiktextract du Wiktionnaire **français** sur kaikki.org/frwiktionary —
   PAS kaikki.org/dictionary/French, qui est le Wiktionnaire anglais), mets à jour
   `lem/ingest/telecharger.py` et relance. JeuxDeMots est optionnel (`lem telecharger jeuxdemots`).
5. **Vérifier les lecteurs sur les vrais fichiers avant d'ingérer.** Les lecteurs (`lem/ingest/__init__.py`)
   ont été écrits d'après le format documenté, sans voir les fichiers réels. Inspecte une vingtaine de
   lignes de chaque source : en-têtes du TSV de Lexique ; dans le JSONL wiktextract, les champs `word`,
   `lang_code`, `pos`, `senses[].glosses`, `topics` / `raw_tags`, `etymology_texts`, `forms`, `synonyms`,
   `sounds`. Corrige les lecteurs si besoin et ajoute un test avec un extrait réel (quelques lignes) dans
   `tests/`.
6. **Ingestion.** `lem ingest lexique …`, `lem ingest wiktionnaire …`, `lem ingest wordfreq`, puis
   `lem prior --cible 45000`. Rapporte : nombre d'entrées, de sens, de formes, de domaines, de morphèmes,
   de relations de synonymie, la répartition par origine (grec, latin…) et la durée de chaque étape.
   Contrôle à la main une dizaine d'entrées (acronyme, thuriféraire, sigle, mitose, kjökkenmödding,
   « les sages de la rue Montpensier » si elle existe) : définitions en français, domaines, origine,
   morphèmes plausibles.
7. **Vecteurs.** `lem index`. Mesure le débit (textes par seconde). Si l'estimation dépasse 2 h, propose-moi
   de restreindre l'indexation (lemmes avec Zipf > 0, sens de rang ≤ 3) avant de lancer. Rapporte la
   taille de la base ensuite.
8. **Calibrage.** `lem calibrer`. Rapporte les seuils obtenus et les mesures (synonymes vs hasard). Si
   l'écart entre la médiane des synonymes et le 95e centile du hasard est trop faible (< 0,05), dis-le.
9. **Performance.** Mesure, sur la base réelle : temps de démarrage de `lem` ; latence d'une notation
   `noter_recep` (plongement, puis dictionnaire inversé sur tous les sens) ; construction d'une
   `PartieBombe` (dictionnaire de ~1 M formes) ; `CarteConnaissance.recalculer_tout()`. Si les plus proches
   voisins dépassent 150 ms et que la RAM le permet, garde la matrice en float32 dans `lem/vecteurs.py`.
   Si `recalculer_tout` dépasse 10 s, vectorise-le (numpy) sans changer son résultat.
10. **Essai réel.** Lance `lem` dans Windows Terminal et joue 2 minutes à chaque jeu. Vérifie le rendu du
    logo en cubes, des couleurs et des touches (flèches, Tab, Échap). Lance `lem noter --modele haiku` sur
    les quelques définitions produites, pour vérifier la chaîne Claude headless de bout en bout.

## Règles

- Ne change ni l'architecture (journal en ajout seul, état dérivé et rejouable), ni le design
  (monochrome ; la couleur ne sert qu'à signifier).
- **Graphie stricte** : aucune variante orthographique n'est acceptée, accents compris. Ne réintroduis
  jamais de tolérance.
- Aucune donnée dans le dépôt (`~/.lem/` uniquement). Pas de secrets dans les commits.
- Ne modifie rien côté Windows (`.wslconfig`, pare-feu, services) sans me le demander.
- Travaille sur une branche `local/integration`, en commits petits et clairs, puis pousse-la. Ne pousse
  pas sur `main`.
- Termine par un rapport : la machine, ce qui marche, ce que tu as corrigé, les chiffres, et ce qui reste
  à faire.
