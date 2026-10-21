# lem — conception

## 0. Principe

Une seule vérité : **le journal** (`evenement`, `notation`, `mnemo`). Tout le reste est dérivé et se
recalcule (`lem rebuild`) :

```
          sources lexicales                     jeux (TUI)
  Lexique · Wiktionnaire · JDM · wordfreq   cartes · bombe · mémoire · décision
                 │                                   │  écrivent
                 ▼                                   ▼
        ┌──────────────────┐               ┌────────────────────┐
        │ LEXIQUE (statique)│◀── lisent ───│ JOURNAL (ajout seul)│◀── Claude / Jev (notation différée)
        └──────────────────┘               └────────────────────┘
                 │                                   │ rejoue
                 └──────────────┬────────────────────┘
                                ▼
            DÉRIVÉ : carte de connaissance (θ, P(connu)) · FSRS · confusions · atlas
```

Aucun LLM n'a besoin de « lire la note d'un mot » : l'état d'un mot est **numérique**
(P(connu), stabilité, difficulté, dates) et la notation est en **vocabulaire fermé**.

## 1. Le modèle de mémoire

### 1.1 Carte de connaissance (tout le lexique, testé ou non)

`lem/memory/knowledge.py` — modèle de type Rasch / Elo :

```
logit P(connu | mot) = logit P_prior(mot) + Σ θ_d (d ∈ dimensions(mot)) + résidu(mot)
```

* **P_prior = l'adulte cultivé moyen.** Une sigmoïde de la fréquence Zipf, dont le seuil est calibré
  pour que Σ P_prior ≈ 45 000 lemmes (ordre de grandeur de Brysbaert et al., 2016). On ne construit
  rien de zéro : on part de là et le système s'adapte.
* **θ_d = tes écarts** à cet adulte, par dimension : bande de fréquence, origine (grec, latin,
  anglais…), type (expression, nom propre…), domaine, morphème. Rater « acronyme » fait baisser
  θ(morph:-onyme), donc « paronyme » et « éponyme » baissent sans avoir été testés. C'est la
  propagation, et c'est ce qui permet d'attaquer **au coût minimal**.
* **Priorité d'apprentissage** = (1 − p) × zone proximale (les mots flous comme *thuriféraire* sont les
  moins chers à consolider) × utilité (fréquence, intérêt du domaine) × levier (mots inconnus de la
  même famille morphologique).

### 1.2 FSRS (v6, `py-fsrs`) par trace

Une trace = (entrée, sens, direction) : `recep` (mot → sens, vocabulaire passif) et `prod`
(sens → mot, vocabulaire actif). L'état est **rejoué** depuis le journal, avec la meilleure note
disponible (humain > Jev > Claude > auto-évaluation > embedding). Le fuzzing est désactivé pour que
le rejeu soit déterministe. Une notation différée se contente donc de rejouer.

### 1.3 Unités : lemme, polysémie, expressions, concepts

`entree` (mot, composé, expression, nom propre, mot étranger) → `sens` (polysémie) → `concept`
(« les sages de la rue Montpensier » et « Conseil constitutionnel » pointent vers le même concept).
La mémoire est suivie **par sens**. Le champ `langue` est déjà là : le même moteur servira pour
d'autres langues.

Les images mentales et les dessins sont rattachés à une entrée et à un sens (`mnemo`). Ils
cohabitent sans complexité supplémentaire.

## 2. Les LLM : où, et pourquoi

### 2.1 Jev (TypeSafe AI)

Jev est sorti en accès anticipé le 15 septembre 2026. Ce n'est **pas un modèle de chat** : il ne
génère pas de texte et renvoie des décisions typées, avec une probabilité calibrée. Trois formes de
question :
* **Choice** : une option parmi ≤ 255, avec une probabilité par option ;
* **Score** : un niveau sur une échelle de 2 à 10 niveaux décrits en mots ;
* **Noul** : oui ou non.

Contexte annoncé : 64 k tokens. Latence annoncée : 70 à 500 ms. Prix annoncé : 0,042 $ par million de
tokens en entrée, sortie gratuite (ce sont des affirmations de l'éditeur). Le support du français
n'est **pas vérifié**, et l'accès passe par une liste d'attente. Sources :
[Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)) ·
[TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/) ·
[pydantic-ai](https://pydantic.dev/docs/ai/models/typesafe/) ·
[Cloudflare](https://developers.cloudflare.com/ai/models/typesafe/jev/) ·
[DataCamp](https://www.datacamp.com/blog/system-one-models-jev) ·
[Sanity (lecture critique)](https://www.sanity.io/glossary/jev-typesafe-ai-model).

**Conséquence : quelle est l'importance réelle des structures lexicales pour Jev ?** Jev ne peut
juger que ce qu'on lui présente en liste fermée. C'est la base qui fournit les options :

| Structure | Importance pour Jev | Rôle |
|---|---|---|
| Sens (polysémie) | **indispensable** | options du Choice « quel sens vise-t-il ? » |
| Grappes de confusables | **indispensable** | options du Choice « quel mot a-t-il réellement défini ? » |
| Taxonomie d'erreurs | **indispensable** | options du Choice « quel type d'erreur ? » |
| Échelle de précision | **indispensable** | niveaux du Score |
| Morphèmes | utile, secondaire | Choice « quel morphème est mal compris ? » |
| Notes « pivots » Obsidian | **inutile** | Jev ne lit pas de graphe, il lit des options |

Le contrat (`lem/grading/taxonomie.py`, `lem/grading/jev.py`) est déjà écrit sous cette forme. Claude
reçoit aujourd'hui exactement les mêmes listes. Le jour où Jev est accessible, on l'étalonne contre
tes notes et celles de Claude, puis on bascule.

### 2.2 Aujourd'hui : Claude Code en mode headless, sur ton abonnement

`lem noter` (ou le menu) envoie les définitions par lots de 20 à `claude -p --output-format json
--model sonnet`. Pour chaque définition, le verdict revient en JSON validé : sens visé, précision 1–5,
erreur ∈ taxonomie, mot réellement défini, confiance. Il est écrit dans `notation` sans rien écraser,
puis FSRS est rejoué. Une réponse illisible n'est pas perdue : le lot sera repris au passage suivant.

### 2.3 En direct, sans LLM : bge-m3 (« température » façon Cemantix)

`lem/grading/direct.py`. En moins de 50 ms :
* ta définition (texte libre ou synonymes) est plongée avec bge-m3, puis comparée aux sens de la
  cible : on obtient une **température** de 0 à 100° ;
* elle est aussi comparée à **tous les sens du lexique** (dictionnaire inversé). Si tu définis
  *thuriféraire* par « morne, triste », le système voit que tu es plus près d'*atrabilaire* et le dit
  aussitôt ;
* les synonymes sont reconnus par le réseau (Wiktionnaire, JeuxDeMots).

Cette note est **provisoire**. Claude la remplace en fin de session.

## 3. Obsidian : verdict

Est-il structurellement nécessaire ? **Non.** Les chevauchements de concepts sont une table
plusieurs-à-plusieurs (`sens_domaine`) avec une hiérarchie (`domaine.parent_id` : « métallurgie »
est inclus dans « science »). Le graphe, c'est la table `relation`. Obsidian n'apportait que la
visualisation. Selon ta règle, **on l'oublie** : l'Atlas en terminal le remplace. Un export Obsidian
en lecture seule reste possible un jour ; ce serait une projection, jamais la source.

## 4. Base lexicale

« Massif, façon JKLM » : JKLM ne publie pas sa liste, et l'ODS (Scrabble) n'est pas libre.
L'équivalent ouvert :
* **Lexique 3.83** : environ 140 000 formes, 47 000 lemmes, fréquences, phonétique, syllabes ;
* **Wiktionnaire** (wiktextract) : sens, étymologie (d'où l'origine grecque ou latine et les
  morphèmes), domaines, synonymes, formes fléchies, expressions, noms propres ;
* **JeuxDeMots** : associations, synonymes, hyperonymes ;
* **wordfreq** : fréquences Zipf de repli ;
* **Morphalou** (environ 1 million de formes fléchies) : à brancher plus tard, utile pour que la
  Bombe accepte toutes les conjugaisons.

## 5. Les jeux : modularité

Chaque jeu déclare ses paramètres une fois (`lem/games/base.py`). L'interface en tire
automatiquement l'écran de réglage **et** les commandes `/…` en cours de partie. Chaque jeu interroge
la base avec ses propres filtres (`lem/games/pool.py` : domaine avec ses sous-domaines, origine,
type, état mémoire, fréquence, longueur).

### 5.1 Cartes

Tu vois un mot, tu écris sa définition ou des synonymes. Tu vois une définition, tu écris le mot.
Les **duels de confusables** suivent chaque mot nouveau.

Les grappes de confusables (`lem/confusables.py`) réunissent trois voisinages :
* formel : orthographe, affixes, phonétique ;
* sémantique : voisins bge-m3 ;
* personnel : tes confusions passées.

Avec le filtre « science », on obtient les équivalents scientifiques de *acronyme / sigle /
homonyme* : *absorption / adsorption*, *mitose / méiose*, *isotope / isomère / isobare*.

### 5.2 Bombe : la répétition intelligente

Pour une suite de lettres s, on calcule **K = Σ P(connu)** des mots du filtre qui contiennent s.
* K ≥ 300 : pas de répétition (trop de solutions, aucun intérêt) ;
* K ≤ 5 : la suite revient ⌈K⌉ fois (« si j'en connais 5, la proposer 5 fois ») ;
* entre les deux : interpolation logarithmique.

À chaque mot valide qui n'est pas la cible, la suite **s'allonge vers la cible**, en évitant d'être
contenue dans le mot que tu viens de donner. Arrivée au mot entier, la cible est révélée, et elle
remonte en priorité dans les Cartes.

Le mot que tu donnes est comparé à la cible, avec des couleurs qui ont un sens :

| Couleur | Message | Proximité |
|---|---|---|
| **rouge** | « Presque pareil ! » | forme et sens |
| **magenta** | « Même allure, autre sens » | forme seulement |
| **orange** | « Similaire par le sens » | sens seulement |
| **violet** | « Tu les as déjà confondus » | dans ton journal |

**Adaptatif** : un escalier psychophysique vise environ 75 % de cibles trouvées et ajuste la durée
de la bombe à ta latence médiane.

**Vies** : regain désactivé, par l'alphabet (comme JKLM) ou par un mot rare. Les mots produits
spontanément comptent comme preuves de vocabulaire actif dans la carte de connaissance, sans
alourdir FSRS.

### 5.3 Mémoire verbale

Le vivier puise dans les régions les plus étranges du lexique, selon un score d'étrangeté : rareté,
mot étranger, nom propre, lettres rares, diacritiques non français, composés. Exemples :
*kjökkenmödding*, *chlorfenvinphos*, *pouligny-saint-pierre*, *midshipmen*. On y mêle les mots
« presque acquis » des autres jeux.

**Imagerie** : à la première apparition d'un mot, tu écris ton image mentale, ou tu la dictes (la
dictée macOS marche dans tout champ de texte). En mode **expérience**, un mot sur deux seulement est
imagé, de façon déterministe : sur tes propres données, on pourra mesurer si l'imagerie t'aide au
jeu. Le dessin à la souris (canevas braille dans Textual) est faisable dans une version suivante.

### 5.4 Mot ou pas mot, et Étalonnage

Les pseudo-mots viennent d'une chaîne de Markov d'ordre 3. Elle est naïve, comme tu l'anticipais :
tu les repéreras parfois. Le générateur est à affiner (type Wuggy).

L'Étalonnage choisit ses mots là où l'information est maximale (p(1 − p)). Un « oui » donné à un
pseudo-mot réduit le poids de tes « oui » suivants.

## 6. Les données sur toi

On construit pour le vocabulaire, et on **enregistre brut dès le départ** ce qui coûte peu et
servira plus tard :
* latence, temps jusqu'à la première frappe, frappes, effacements ;
* lettres d'indice demandées (profondeur du « mot sur le bout de la langue ») ;
* réponse brute, candidats montrés, contexte (heure) ;
* auto-évaluation **et** évaluation externe (métacognition) ;
* types d'erreur en vocabulaire fermé, graphe orienté de tes confusions ;
* condition expérimentale de l'imagerie.

L'**Atlas** en tire déjà ta carte, ton profil θ, ta signature d'imprécision (« trop générale »,
« connotation seule »…), tes confusions et ton écart soi/correcteur. Prochaine représentation : une
**carte du lexique** en braille (projection UMAP des vecteurs bge-m3), avec un brouillard de guerre
(zones connues éclairées, zones inexplorées sombres) et tes confusions en arcs.

Associer *pied* et *beurre* n'a pas d'intérêt isolément. Agrégées, tes associations comparées aux
normes de population (JeuxDeMots) donnent un **indice d'idiosyncrasie**. C'est une mesure, pas un
diagnostic.

## 7. Autres jeux possibles

| Jeu | Intérêt | Verdict |
|---|---|---|
| **Cemantix branché** : deviner une cible floue par la température bge-m3 | explore les voisinages sémantiques | fort, peu coûteux |
| **Intrus** : 3 mots d'une grappe et 1 confusable | discrimination | fort, trivial à faire |
| **DAT** (Olson et al., 2021) : 10 mots aussi différents que possible, score = distance moyenne | mesure d'ampleur associative, données sur toi | fort, trivial à faire |
| **Taboo / Pyramide** : expliquer sans les mots interdits | production, notée par Claude | moyen |
| **Mots fléchés** | voir ci-dessous | phase 3 |
| **Jarnac / Scrabble** contre une IA (GADDAG, Gordon 1994 ; DAWG, Appel et Jacobson 1988) | anagrammes, orthographe ; peu de sens | plus tard, avec bonus pour les mots en apprentissage et les définitions des coups ratés |

**Mots fléchés : possible ?** Oui. Le remplissage de grille est un problème de satisfaction de
contraintes : retour arrière plus un index lettre × position, comme les compilateurs de mots croisés.
Les définitions « ultra-précises » existent déjà comme colonne (`sens.definition_precise`), à générer
hors ligne par Claude. Le « moteur de goût » est le même escalier que pour la Bombe : tu notes « trop
facile / juste / trop dur », et la bande de fréquence et la cible de P(connu) se déplacent. Mais
80 % de la valeur pédagogique est déjà dans l'exercice *sens → mot* avec indices lettre par lettre.
D'où la phase 3.

## 8. Coûts et calcul sur un MacBook M4 15 pouces (16 Go)

| Poste | Ordre de grandeur |
|---|---|
| Base SQLite complète (environ 160 k entrées, environ 300 k sens, journal) | 1 à 2 Go |
| Ingestion (Lexique et Wiktionnaire) | quelques minutes à environ 15 min, une fois |
| Vecteurs bge-m3 via Ollama, environ 450 k textes courts | environ 1 à 2 h, une fois (on peut se limiter aux lemmes fréquents) ; environ 0,9 Go en float16 |
| Vecteurs en RAM | environ 0,9 Go en float16 (environ 1,8 Go en float32, plus rapide) |
| Une réponse : plongement, plus proches voisins, FSRS | environ 20 à 60 ms pour le plongement, environ 50 à 200 ms pour les voisins sur 300 k vecteurs, microsecondes pour FSRS |
| Bombe : validation et calcul de K | dictionnaire en mémoire ; K calculé sur le vivier filtré, environ 10 à 50 ms |
| Notation par Claude headless | **0 € marginal** sur l'abonnement, dans ses limites d'usage ; environ 20 définitions par appel ; Haiku pour les grosses fournées |
| Notation par Jev (plus tard) | prix annoncé environ 0,04 $ par million de tokens : environ 0,03 $ pour 1 000 définitions |
| Enrichissement hors ligne (définitions précises, exemples) | par lots Claude headless, étalé sur quelques jours d'abonnement |

Tout tourne en local sur le Mac. Le seul service extérieur est Claude, en fin de session.

## 9. Feuille de route

1. ✅ Socle : schéma, journal, FSRS rejouable, carte de connaissance, similarités, grappes, notation
   (directe, Claude, contrat Jev).
2. ✅ Jeux : Cartes, Bombe, Mémoire verbale, Mot ou pas mot, Étalonnage, Atlas. TUI, CLI, tests.
3. **Ingestion réelle sur ton Mac**, puis `lem index` ; recalibrer les seuils de proximité et de
   température sur tes données (table `reglage`).
4. Étalonnage initial (environ 150 questions), puis 2 à 3 semaines de jeu.
5. Optimiseur FSRS sur ton journal (paramètres personnels).
6. Intrus, DAT, Cemantix branché ; carte braille du lexique.
7. Mots fléchés ; Morphalou ; dessin à la souris ; client Jev.
