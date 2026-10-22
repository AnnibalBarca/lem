# Rapport d'intégration — vraie base sur Windows + WSL2

22 octobre 2026, branche `local/integration`. Exécution de [claude-local-wsl.md](claude-local-wsl.md).

## 1. La machine

| | |
|---|---|
| CPU | 16 cœurs logiques |
| RAM vue par WSL | 15 Gio (+ 4 Gio de swap) — pas besoin de `.wslconfig` |
| Disque `~` | 1 To, 929 Go libres |
| WSL | 2.7.11, noyau 6.18.33, Ubuntu 22.04, Windows 10.0.26200 |
| GPU | NVIDIA RTX 5070 Ti 16 Go, visible dans WSL (`nvidia-smi`) |
| Ollama | **voie B** : Ollama 0.40.7 déjà installé côté Windows, joignable depuis WSL par `localhost` (réseau « mirrored ») ; aucun `sudo`, rien modifié côté Windows. `bge-m3` entièrement en VRAM. |

## 2. Ce qui marche

* Dépendances, `uv tool install -e .`, `lem` dans le PATH, **64 tests verts** (56 au départ).
* Téléchargement des sources (66 s), ingestion complète (≈ 2 min 20), wordfreq, prior, vecteurs, calibrage.
* Tous les jeux s'ouvrent et répondent sur la vraie base (parcours automatisé par le pilote Textual sur une
  copie de la base) : Cartes, Bombe, Mémoire verbale, Mot ou pas mot, Étalonnage, Atlas.
* `lem noter --modele haiku` : chaîne Claude headless de bout en bout, 6/6 définitions notées en 16 s,
  verdicts cohérents (sens visé, précision, type d'erreur, commentaire).

## 3. Ce que j'ai corrigé

| Problème trouvé sur les vrais fichiers | Correction |
|---|---|
| **1,55 M** des 2,11 M entrées françaises du dump wiktextract sont des « Forme de … » (pluriels, conjugaisons) : le lecteur en faisait des entrées, avec des sens « Pluriel d'acronyme. » | Elles deviennent des **formes fléchies** de leur lemme : 2,3 M formes acceptées par la Bombe (l'équivalent de Morphalou, gratuit). |
| Les domaines arrivent en **codes anglais** (`botany`, `law`…) ; les `raw_tags` mêlent domaines, régions (« Québec ») et qualificatifs (« Très rare ») | `lem/data/sujets_wiktextract.json` (269 codes → français), filtre des `raw_tags`, hiérarchie étendue à ~520 domaines rattachés aux 7 macro-domaines. |
| Origine lue dans toute l'étymologie (« pomme de terre » → néerlandais, à cause d'une phrase historique) ; « Emprunt au danois » non reconnu | Premier paragraphe seulement ; tournure « Emprunt au ». |
| API du Wiktionnaire et code phonétique de Lexique mélangés dans `entree.phon` (comparés par `sim_forme`) | API convertie au code de Lexique. |
| Sigles, locutions, affixes, lettres, symboles mal typés | `sigle`, `expression` ; affixes, lettres et symboles ignorés. |
| wordfreq estime une locution d'après ses mots (« ligne de Hunter » 3,7 ; « N. S. J. C. » 6,1) : ~100 k a priori gonflés, **seuil du prior 3,95** | Mot le plus rare moins 1 point par mot en plus : **seuil 3,24**. |
| Indexation complète : 1,26 M textes à ~85/s = 4 h ; le runner d'Ollama (Windows) tombe sur les lots > 256 | `lem index --zipf-min 0 --rang-max 3` (529 k textes), reprise automatique si Ollama redémarre. |
| Bombe : 15 s et 1,3 Go à la construction, **2,5 s par invite** | K vectorisé (`CarteConnaissance.p_tous`, même calcul que `p`, testé), formes en tuples, cache des comptes : 6,5 s, 1 Go, **0,3 s**. |
| Démarrage des jeux : Cartes 25 s, Mémoire 16 s, Mot ou pas mot 12 s | 4 s, 2 s, 4 s (mêmes résultats, tests d'égalité). |
| Plus proches voisins 468 ms | Matrice float32 en RAM : **26 ms** ; `noter_recep` 500 → **52 ms**. `LEM_VECTEURS=float16` pour les petites machines. |
| `noter_recep` notait 1 de bonnes définitions : sur 300 k sens, un voisin dépasse presque toujours la cible de 0,02 (« flatteur, flagorneur » pour *thuriféraire* : 82°, note 1, « mot défini : flagorneur ») | Le « mot réellement défini » n'est cherché que sous 45°. |

## 4. Les chiffres

### Ingestion

| Étape | Durée | Résultat |
|---|---|---|
| `lem telecharger lexique wiktionnaire` | 66 s | Lexique 25 Mo, wiktextract fr 737 Mo |
| `lem ingest lexique` | 1,8 s | 50 808 lemmes × catégorie |
| `lem ingest wiktionnaire` | 132 s (500 Mo de RAM) | 558 919 entrées lues |
| `lem ingest wordfreq` | 6 s | 498 952 zipf complétés |
| `lem prior --cible 45000` | 1,7 s | seuil Zipf **3,24** |
| `lem index --zipf-min 0 --rang-max 3` | **1 h 44** (~85 textes/s) | 303 072 sens + 226 560 entrées |
| `lem calibrer` | 4 s | voir § 4.3 |

### Contenu de la base

| | |
|---|---|
| Entrées | **549 760** : 302 184 mots, 138 672 noms propres, 88 752 composés, 18 544 expressions, 1 608 sigles |
| Sens | **707 935** (545 219 entrées en ont au moins un) |
| Formes | **2 305 542** (2 000 638 graphies distinctes) |
| Domaines | 524 ; 312 832 liens sens–domaine. Par macro-domaine : humanités 132 k (dont géographie 122 k), science 86 k, société 30 k, technique 24 k, vie pratique 24 k, arts 9 k, langage 8 k |
| Morphèmes | 4 426 ; 123 512 liens (-iser, re-, -ment, dé-, -ique, -eur, -able, auto-…) |
| Synonymie | 144 963 relations (Wiktionnaire) |
| Origine | français 277 k, sans étymologie 191 k, latin 28 k, anglais 15 k, grec 10,5 k, espagnol 5,2 k, italien 4,6 k, allemand 3,5 k, occitan 3,4 k, arabe 2,0 k, russe 1,3 k, japonais 1,2 k… |
| Lemmes de Lexique enrichis par le Wiktionnaire | 36 884 / 42 442 |
| Taille | 527 Mo sans vecteurs, **2,7 Go** avec |

Contrôle à la main : *acronyme* (anglais, acro- / -onym, 2 sens, « acronymes »), *thuriféraire* (latin, 3 sens
dont « Flatteur », domaine liturgie), *sigle* (latin, 4 sens dont typographie, « cigle »), *mitose* (grec,
biologie cellulaire), *kjökkenmödding* (danois, archéologie), *atrabilaire* (médecine, anatomie ; synonymes
irascible, maussade), *pomme de terre* (cuisine, agriculture ; synonymes patate, parmentière…), *ONU* (sigle,
« o.n.u. »). Définitions en français partout. **« Les sages de la rue Montpensier » n'existe pas** dans le
Wiktionnaire français.

### Calibrage (`lem calibrer`)

| Mesure | Valeur |
|---|---|
| Paires de synonymes vectorisées | 84 794 |
| Entrées au hasard, 95e centile | 0,560 |
| Synonymes, médiane | 0,635 |
| **Écart** | **0,075** (> 0,05 : utilisable, mais serré) |
| Seuil « similaire par le sens » | 0,598 |
| Température : 0° = 90e centile des sens au hasard | 0,531 |
| Température : 100° = médiane des meilleures paires entre synonymes | 0,692 |

### Performance (vraie base, vecteurs chargés)

| Mesure | Avant | Après |
|---|---|---|
| `lem --help` | 0,04 s | |
| Chargement carte + vecteurs (une fois par session) | | ≈ 5 s, ≈ 3 Go de RAM |
| Plus proches voisins (303 k sens) | 468 ms | **26 ms** |
| Plongement d'un texte (bge-m3, GPU) | 13 ms | |
| `noter_recep` complet | ≈ 500 ms | **52 ms** |
| `PartieBombe` (2 M formes) | 15,3 s, 1,3 Go | **6,5 s**, 1,0 Go |
| Nouvelle invite de la Bombe | 2,5 s | **0,3 s** |
| `CarteConnaissance.recalculer_tout()` (550 k entrées) | 4,5 s | (< 10 s : non vectorisé) |
| Ouverture des jeux dans l'interface | 5 à 25 s | 2,5 à 10 s (Bombe la plus lente, chargement de session compris) |

## 5. Ce qui reste à faire

1. **Toi, dans Windows Terminal** : `lem`, 2 minutes par jeu ; vérifier le logo en cubes, les couleurs, les
   flèches, Tab, Échap, et la dictée Win + H. Je n'ai pu tester l'interface que par le pilote Textual.
2. **Notation directe** : les sens sont plongés sous la forme « mot : définition », les réponses sans le mot.
   Une longue paraphrase juste (« division d'une cellule en deux cellules filles identiques » pour *mitose*)
   tombe à 3°, et le « mot défini » d'une réponse courte est souvent un mot qui *ressemble* à la réponse
   (« morne, triste » → *morné*). Pistes, par ordre d'effet attendu : réindexer les sens sans la forme
   (1 h) ; reclasser les 20 meilleurs voisins avec un cross-encoder (`bge-reranker-v2-m3`) ; ou faire noter en
   direct par un LLM local (qwen2.5:14b est déjà installé, ~1 s sur la 5070 Ti). Claude corrige tout en fin
   de session dans tous les cas.
3. **Prior trop bas sur des mots courants** (*acronyme* 0,14 ; *pomme de terre* 0,16) : la cible de 45 000
   se répartit sur 410 k entrées, dont des doublons de catégorie (*isotope* nom et adjectif comptent deux fois)
   et des milliers de composés techniques. Piste : calibrer sur les graphies distinctes, ou exclure du compte
   les composés et expressions de Zipf nul. À trancher avec l'Étalonnage (150 questions) qui corrigera θ.
4. **Noms communs féminins du Wiktionnaire** (*fatigante* « personne importune », *réparatrice*) choisis
   comme mots nouveaux dans les Cartes : utiles mais peu prioritaires. Piste : baisser la priorité des sens
   « Celle qui… / Femme qui… ».
5. **Indexation complète** (730 k textes restants, ≈ 2 h 30) : `lem index` sans option, quand le PC est libre.
6. **JeuxDeMots** non branché (optionnel) : le format du dump et les identifiants de relations restent à
   vérifier (`JDM_TYPES`).
7. Ollama sous Windows plafonne à ~85 textes/s (une séquence à la fois). Pour une réindexation, Ollama
   dans WSL avec `OLLAMA_NUM_PARALLEL` ou sentence-transformers sur le GPU iraient 10 à 20 fois plus vite.
8. **Téléphone et synchronisation** : voir [passation-mobile-sync.md](passation-mobile-sync.md).
