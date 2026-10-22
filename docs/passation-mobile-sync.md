# Passation — téléphone (Android) et synchronisation entre machines

Document de travail pour une session Claude Code qui reprend ce chantier. À lire en entier avant d'écrire
une ligne. Contexte : [README.md](../README.md), [DESIGN.md](../DESIGN.md), et l'état réel de la base
dans [rapport-integration-wsl.md](rapport-integration-wsl.md).

## 0. Objectif

L'utilisateur veut jouer **sur son téléphone Android comme dans le terminal**, et que sa progression soit la
même partout : plusieurs PC, le téléphone. Priorité : le téléphone est « capital ».

Livrables, dans l'ordre :

1. **Carte de connaissance rejouable** depuis le journal (prérequis, § 3).
2. **`lem sync`** : synchronisation par journaux append-only, un par machine (§ 4).
3. **lem natif sur Android via Termux** : même code, hors ligne, synchronisé (§ 5).
4. **`lem web`** : la même interface dans un navigateur (repli pour tout appareil, iPhone compris) (§ 6).

## 1. Règles de coordination (d'autres sessions travaillent en parallèle)

* Travaille dans **ton propre clone** : `git clone git@github.com:AnnibalBarca/lem.git ~/lem-mobile`.
  **Jamais dans `~/lem`** (autre session).
* Branche `local/mobile` depuis `origin/local/integration`. Ne pousse ni sur `main` ni sur `local/integration`.
* **Ne touche jamais `~/.lem/lem.sqlite`** (la vraie base de l'utilisateur, 2,7 Go). Teste sur des bases
  de démo (`lem --db /tmp/a.sqlite demo`) ou sur une **copie** (`sqlite3 … ".backup /tmp/copie.sqlite"`).
* Une session « thème » peut modifier `lem/tui/theme.py` et `lem/tui/app.py` sur `local/theme`. Garde tes
  changements d'interface **dans des fichiers à toi** (`lem/tui/mobile.py`, `lem/web.py`) et limite
  `app.py` à quelques lignes d'accroche ; s'il le faut, rebase sur `origin/local/theme` avant de pousser.
* Ne modifie rien côté Windows (pare-feu, services, `.wslconfig`) : **propose** la commande, l'utilisateur
  décide. Pas de `sudo` sans lui demander.
* Règles du projet, inchangées : journal en ajout seul, état dérivé rejouable ; monochrome, la couleur ne
  sert qu'à signifier ; **graphie exigeante** (aucune variante ; seuls les accents manquants sont tolérés, réglage `accents_tolerants`) ; aucune donnée dans
  le dépôt ; code, interface et commits en français, dans le style existant ; petits commits ; tests verts.

## 2. Ce qui est déjà prêt (à réutiliser, pas à refaire)

| Brique | Où | Utilité ici |
|---|---|---|
| Dossier des données | `LEM_HOME` (défaut `~/.lem`), base `lem.sqlite`, sources dans `sources/` | une base par appareil |
| Base choisie à la ligne de commande | `lem --db CHEMIN …` | tests, copies |
| Plongements par Ollama, hôte réglable | `lem/vecteurs.py` : `OllamaEmbedder`, `OLLAMA_HOST` | le téléphone peut plonger **chez le PC** via Tailscale |
| Interface d'embedder | `Embedder` (Protocol) ; `HashEmbedder` = repli hors ligne, non sémantique | brancher un autre moteur (ONNX, llama.cpp) sans toucher aux jeux |
| Vecteurs en RAM | `Magasin` ; `LEM_VECTEURS=float16` divise la RAM par deux (≈ 1,1 Go au lieu de 2,2) | machines modestes |
| Contexte d'exécution | `lem/contexte.py` : base, embedder, magasins, carte — tout paresseux | point d'entrée unique des interfaces |
| Notation différée | `lem noter` (Claude headless : `claude -p`) | peut tourner sur le PC après sync |
| Rejeu FSRS | `scheduler.reconstruire(con, entree_ids)` | après import d'événements |
| P de toute la carte en numpy | `CarteConnaissance.p_tous()` / `p_de(ids)` | coûts maîtrisés sur 550 k entrées |

Ordres de grandeur mesurés sur le PC (16 cœurs, RTX 5070 Ti) : chargement carte + vecteurs ≈ 5 s et ≈ 3 Go
de RAM ; Bombe 6,5 s à construire (2 M formes, 1 Go) ; 0,3 s par invite ; `noter_recep` 52 ms ;
plongement d'un texte 13 ms (GPU).

## 3. Prérequis : rendre la carte de connaissance rejouable

**Constat.** FSRS se rejoue depuis le journal, mais pas la carte : `CarteConnaissance.observer(eid, y, poids)`
est appelée par les jeux (`cartes.py:282`, `bombe.py:325, 334, 403`, `decision.py:122, 124`) avec des
`y` et `poids` calculés sur place, **qui ne sont écrits nulle part**. `lem rebuild` recalcule P avec les θ
*courants*, il ne les reconstruit pas. Deux appareils qui jouent séparément ne peuvent donc pas fusionner
leurs θ.

**À faire.**
* Chaque appel à `observer` écrit ses paramètres dans le journal : le plus simple est d'ajouter
  `"obs": {"y": …, "poids": …}` au `data` de l'événement qui l'a provoqué (ou un événement `type =
  'observation'` lié). Une seule fonction d'aide, utilisée par tous les jeux.
* `CarteConnaissance.rejouer(con)` : remet θ, résidus et `n_obs` à zéro et rejoue toutes les observations
  dans l'ordre (`ts`, puis identifiant global). `lem rebuild` l'appelle.
* Les observations notées après coup (Claude remplace la note directe) : décider et documenter si le
  rejeu prend la meilleure note disponible (comme FSRS, `rang_correcteur`) — c'est le plus cohérent.
* Test : une partie jouée en direct puis `lem rebuild` donne **exactement** les mêmes θ, résidus et P.

## 4. `lem sync` : un journal par machine

**Principe.** Ne jamais synchroniser le fichier SQLite (Dropbox / OneDrive / Syncthing corrompent une base
ouverte). Le journal est la vérité ; chaque machine **écrit seulement son propre fichier** dans un dossier
partagé, et lit ceux des autres. Pas de conflit d'écriture possible ; chaque appareil peut jouer hors ligne.

```
~/lem-sync/                        (dossier synchronisé : Syncthing recommandé, OneDrive possible)
  journal-pc-salon.jsonl           une ligne JSON par ligne de journal, ajout seul
  journal-pc-bureau.jsonl
  journal-pixel.jsonl
  reglages-pc-salon.json           réglages, dernier écrit gagnant (horodaté)
  mnemo/<uuid>.<ext>               fichiers joints (dessins), si un jour
```

**À faire.**
* Identifiant global stable pour `session`, `evenement`, `notation`, `mnemo` : `uid TEXT UNIQUE`
  (UUID v7, triable) et `machine TEXT` (`LEM_MACHINE`, défaut : nom d'hôte). Migration par
  `lem.db.MIGRATIONS` ; les lignes existantes reçoivent un uid sans rien perdre.
* **Références portables.** `entree.id` diffère d'une ingestion à l'autre : dans le fichier d'échange, une
  entrée est `(forme, langue, pos)` ; un sens est `(entrée, rang, empreinte de la définition)` avec repli
  sur le rang si la définition a changé. Les références introuvables à l'import sont **mises en attente**
  (table dédiée) et signalées, jamais perdues ; un import ultérieur les résout.
* `lem sync [--dossier DIR]` (défaut `$LEM_SYNC` ou `~/lem-sync`) :
  1. ajoute à `journal-<machine>.jsonl` les lignes locales pas encore exportées ;
  2. importe les lignes des autres fichiers absentes localement (idempotent, par `uid`) ;
  3. rejoue FSRS (`scheduler.reconstruire` sur les entrées touchées) et la carte (§ 3) ;
  4. affiche un résumé : exportées, importées, en attente.
* Sync automatique, optionnelle : à l'ouverture et à la fermeture de `lem` si `LEM_SYNC` est défini.
* Tests : deux bases de démo jouent des sessions différentes puis se synchronisent dans les deux sens ;
  les états dérivés (`trace_etat`, `connaissance`, `theta`) sont **identiques** à ceux d'une base unique
  ayant reçu tous les événements ; relancer `sync` n'ajoute rien ; une entrée absente côté cible reste en
  attente puis se résout.
* README : section « Synchroniser plusieurs machines ».

## 5. Android natif : lem dans Termux

**Pourquoi Termux plutôt qu'une application réécrite.** Termux (F-Droid) fournit un vrai Linux en
espace utilisateur : Python, numpy, et un terminal où Textual fonctionne. C'est **le même code**, hors ligne,
avec la même base et la même synchronisation. Une application Kotlin ou Flutter, ce serait réécrire les
jeux, FSRS, la carte et l'orthographe : des mois, pour un résultat moins fidèle. Termux a en plus une
**rangée de touches** configurable (Échap, Tab, flèches), ce qui règle le problème du clavier.

**Le calcul suffit-il ?** Estimation à vérifier sur l'appareil (téléphone récent, 8 à 12 Go de RAM) :

| Poste | Besoin | Verdict |
|---|---|---|
| Base complète | 2,7 Go de stockage (527 Mo sans vecteurs) | OK |
| FSRS, carte, Cartes, Mémoire, Mot ou pas mot, Étalonnage | quelques centaines de Mo | OK, plus lent qu'un PC (×3 à ×5) |
| Bombe (2 M formes en mémoire) | ≈ 1 Go | OK sur 8 Go ; mesurer le temps de construction |
| Vecteurs en RAM | 1,1 Go en float16 (`LEM_VECTEURS=float16`) | possible mais lourd ; voisins lents sans BLAS float16 |
| Plongement bge-m3 (568 M paramètres) | Ollama existe dans Termux (CPU) : ~0,1–0,5 s par texte, à mesurer | possible ; sinon plongement **chez le PC** (`OLLAMA_HOST` via Tailscale) |
| Notation Claude | `claude` dans Termux (Node) à vérifier ; sinon le PC note après `lem sync` | OK en différé |

**À faire.**
* `scripts/android-termux.sh` : paquets Termux (`python`, `python-numpy`, `rust` si une roue manque —
  vérifier `pydantic-core`, `rapidfuzz`, `wordfreq`/`regex`), `pip install -e .`, et
  `~/.termux/termux.properties` avec une rangée `ESC TAB ← ↓ ↑ → /`. Documenter chaque étape réellement
  testée ; ce qui ne l'a pas été est marqué comme tel.
* **Profil léger** (`LEM_PROFIL=mobile`, ou détection de la RAM) : vecteurs float16 ou pas de vecteurs
  locaux ; dictionnaire inversé désactivé si les voisins dépassent ~300 ms ; tout reste noté plus tard par
  Claude. Le profil ne change **jamais** les règles du jeu (graphie stricte, journal), seulement le coût.
* **Base du téléphone** : `lem exporter-lexique FICHIER` produit une base sans journal (lexique + vecteurs,
  éventuellement restreints) à copier une fois ; le journal arrive ensuite par `lem sync`. Alternative :
  `lem telecharger --ingerer` sur le téléphone (737 Mo à télécharger) — mesurer.
* **Écran étroit** : en portrait, Termux fait ~45–60 colonnes. Vérifier chaque écran à 50 × 30 (pilote
  Textual, `size=(50, 30)`) ; logo compact sous une largeur seuil ; rien de coupé. Code dans
  `lem/tui/mobile.py` autant que possible.
* **Dictée** : le clavier Android (Gboard) dicte dans Termux. Vérifier que `mode_saisie` la repère comme la
  dictée Whisper (commit « Dictée externe ») et qu'elle n'est jamais comptée comme faute.
* Si un appareil Android est branché en ADB ou joignable, tester pour de vrai ; sinon fournir une procédure
  pas à pas que l'utilisateur exécute, et la liste des mesures à rapporter.

## 6. `lem web` : la même interface dans un navigateur

Repli universel (iPhone, tablette, PC d'un tiers) et accès sans rien installer.
* `lem web [--hote 0.0.0.0] [--port 8000]` avec `textual-serve` (dépendance optionnelle `[web]`) : sert
  `LemApp` tel quel.
* Barre tactile (Indice, Menu, flèches) affichée seulement sur écran étroit ; « ajouter à l'écran
  d'accueil » documenté.
* Accès à distance : **Tailscale** sur le PC et le téléphone (rien d'exposé à Internet). Côté Windows, il
  faudra peut-être une règle de pare-feu Hyper-V pour le port : **la proposer, ne pas l'appliquer**.
* Le serveur web écrit dans la base de la machine hôte : une seule session de jeu à la fois (verrou simple).

## 7. Critères de fin

* `.venv/bin/pytest` vert, nouveaux tests compris (rejeu de la carte, sync bidirectionnelle, écrans 50 × 30).
* Sur deux bases de démo : jouer sur A, `lem sync` sur A puis B, et B affiche la même carte, les mêmes
  révisions dues, le même Atlas.
* Procédure Android écrite, testée autant que possible, avec les mesures réelles (temps d'ouverture de
  chaque jeu, RAM, latence de notation) ou la liste exacte de ce qui reste à mesurer.
* Branche `local/mobile` poussée ; un rapport court en fin de travail : ce qui marche, ce qui est mesuré,
  ce qui reste.
