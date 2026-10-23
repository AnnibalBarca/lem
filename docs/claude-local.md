# Prompt pour Claude Code en local (Mac)

À coller tel quel dans Claude Code, lancé dans un dossier de travail sur le Mac. Le Mac est désormais la
machine cible : il faut un logiciel local impeccable, avant toute idée de téléphone ou de synchronisation.

---

Tu travailles sur **lem** (https://github.com/AnnibalBarca/lem), un moteur d'apprentissage du lexique
français en terminal (Python, Textual, SQLite, FSRS, bge-m3). Lis d'abord `README.md`, `DESIGN.md`,
`docs/rapport-integration-wsl.md` (la vraie base, telle qu'ingérée sur le PC) et `docs/rapport-moteur.md`
(le moteur des Cartes v2, réglé sur un élève simulé). Ta mission : **mettre lem en service sur ce Mac et
vérifier qu'il y est impeccable**, puis me rendre un rapport chiffré.

Machine : MacBook M4 15 pouces, 16 Go de RAM. En même temps tournent souvent bge-m3 (Ollama) et une dictée
Whisper (touche Option droite) : **aucun autre modèle local**. Le juge des définitions est Claude Haiku en
headless, sur l'abonnement. Les données vont dans `~/.lem/` (jamais dans le dépôt).

## Étapes

1. **Code.** Clone le dépôt, branche `local/moteur` (la plus avancée ; elle contient `local/integration`).
   `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest` : tout doit passer. Puis
   `uv tool install -e .` et vérifie que `lem` est dans le PATH.
2. **La base.** Deux voies ; demande-moi laquelle si je ne l'ai pas dit :
   - **A, copier la base du PC** (recommandée : elle garde mon journal, ma carte, mes vecteurs). Sur le PC,
     lem fermé : `python3 -c "import sqlite3; sqlite3.connect('$HOME/.lem/lem.sqlite').execute('PRAGMA wal_checkpoint(TRUNCATE)')"`,
     puis copier `~/.lem/lem.sqlite` (environ 2,7 Go) vers `~/.lem/lem.sqlite` sur le Mac (AirDrop, clé, scp).
     Le dossier `~/.lem/cache/` se reconstruit seul : ne pas le copier.
   - **B, reconstruire sur le Mac** : `lem telecharger lexique wiktionnaire --ingerer`, puis
     `ollama pull bge-m3 && lem index --zipf-min 0 --rang-max 3`, puis `lem calibrer`. Mesure la durée de
     l'indexation (sur le PC : 1 h 44 avec la carte graphique).
3. **Première ouverture.** Lance `lem` une fois et ferme-le. Une base jouée avec l'ancien moteur est mise à
   niveau seule (`lem/memory/migration.py` : observations reconstituées, FSRS rejoué, a priori recalibré).
   Vérifie avant/après :
   - P(connu) de *acronyme*, *pomme de terre*, *thuriféraire* et *isotope* ;
   - la répartition des états dans l'Atlas ;
   - le nombre de lignes de `observation` ;
   - `SELECT statut, COUNT(*) FROM trace_etat GROUP BY statut`.
4. **Mémoire et temps.** Avec Ollama (bge-m3 chargé) et, si possible, la dictée Whisper ouverte, mesure :
   - la RAM de lem (`/usr/bin/time -l`, ou le Moniteur d'activité) à l'accueil, puis dans chaque jeu ;
   - le temps d'ouverture de chaque jeu ;
   - la latence d'une carte à la suivante ;
   - `noter_recep`.

   Cible : lem sous 3 à 4 Go. Si c'est trop, essaie `LEM_VECTEURS=float16` et dis ce que ça coûte.
5. **Le juge Haiku.** Dans les Cartes : `/juge auto` doit choisir Haiku (pas de modèle Ollama de jugement
   sur le Mac). Mesure le délai du verdict (`claude -p` par définition). Vérifie que l'écran reste libre
   pendant ce temps et que le verdict remplace bien la note proposée. Si le délai dépasse 10 s, propose une
   amélioration, par exemple regrouper deux ou trois définitions par appel, ou trouver des options de
   `claude -p` qui allègent le démarrage (vérifie-les dans `claude --help`, n'invente rien).
6. **Rendu.** Joue 3 à 5 minutes à chaque jeu dans le Terminal macOS, et dans iTerm2 ou Ghostty s'ils
   sont là. Vérifie :
   - le logo en cubes, les couleurs (qui ne servent qu'à signifier) ;
   - les flèches, Tab, Échap ;
   - les touches Option et accents (é, è, ç, œ) ;
   - la dictée Whisper (Option droite) : elle doit être repérée comme dictée et jamais comptée comme
     faute de graphie ;
   - la taille de fenêtre minimale lisible.

   Corrige ce qui accroche, dans le style existant.
7. **Le moteur des Cartes.** Une session réelle de 10 minutes. Vérifie :
   - la sonde (« le connais-tu ? », Entrée seule = je ne sais pas) ;
   - qu'aucune carte d'un même mot ne suit sa sœur ;
   - que « sens → mot » vient une dizaine de cartes après la découverte ;
   - le bilan de fin de session ;
   - `/intention`, `/minutes`, `lem interets science=2`.

   Note tout ce qui te semble mal dosé ; ne change pas les réglages sans un passage au banc
   (`lem simuler --graines 3`).
8. **Optimiseur.** `lem optimiser --essai` : rapporte le nombre de révisions espacées par trace et, s'il y
   en a assez, la perte récente avant/après.

## Règles

- Ne change ni l'architecture (journal en ajout seul, état dérivé et rejouable), ni le design (monochrome ;
  la couleur ne sert qu'à signifier).
- **Graphie** : aucune variante orthographique n'est acceptée ; seule exception, réglable et choisie par
  l'utilisateur : les accents manquants (`accents_tolerants`). N'ajoute aucune autre tolérance.
- Pas de modèle local de jugement sur le Mac (mémoire) ; Claude Haiku en headless.
- Aucune donnée dans le dépôt (`~/.lem/` uniquement). Pas de secrets dans les commits. Pas de `sudo` sans
  me demander.
- Travaille sur une branche `local/mac` partie de `local/moteur`, en commits petits et clairs (français,
  style du dépôt), tests verts, puis pousse-la. Ne pousse ni sur `main` ni sur les autres branches.
- Termine par un rapport `docs/rapport-mac.md` : la machine, ce qui marche, ce que tu as corrigé, les
  chiffres (RAM, temps, latences, juge), et ce qui reste à faire.
