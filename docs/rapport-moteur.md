# Rapport — moteur d'apprentissage v2 (Cartes)

Branche `local/moteur`, partie de `local/integration`, 23 octobre 2026.

## 1. Ce qui a changé

| Avant (v1) | Maintenant (v2) |
|---|---|
| File construite une fois au début de la session | **Planificateur** : la carte suivante est choisie à chaque fois (`lem/memory/planificateur.py`) |
| Chaque mot nouveau : 3 cartes d'affilée (sens, duel, mot) | **Sonde** d'abord. Connu : 1 carte, puis une seule vérification dans 30 à 60 jours, puis retiré. Inconnu : duel 3 cartes plus loin, sens → mot 10 cartes et 5 min plus loin, puis FSRS |
| Le mot à retrouver juste après avoir lu sa définition (faux succès) | Au moins 10 cartes et 5 minutes d'écart ; s'il n'a pas eu le temps de venir, il ouvre la session suivante |
| Deux cartes d'un même mot pouvaient se suivre | Jamais à moins de 4 cartes d'intervalle |
| 10 mots nouveaux par jour, quelle que soit la charge | Budget = (capacité de la session − charge des 7 jours) / 6 ; une sonde réussie ne consomme pas le budget |
| Choix des mots : manque × fréquence | + levier des morphèmes, intérêts par domaine (`lem interets`), et information qu'apporte la sonde sur ta carte |
| Paramètres FSRS par défaut, communs | Paramètres **par trace** (sens, mot, graphie) ajustés chaque semaine sur ton journal, sans torch |
| Carte de connaissance : mise à jour en direct, non reconstructible ; chaque révision gonflait tout le lexique | Observations journalisées, `rejouer` à la meilleure note disponible ; seules les observations sur ce que tu savais sans étude propagent aux familles |
| A priori calibré sur toutes les entrées (acronyme 0,14) | Calibré sur les lemmes (une graphie une fois, sans sigles ni composés de Zipf nul) ; recalibrage automatique d'une base ancienne |
| Juge local seulement (qwen3:8b, trop lourd pour le Mac) | `/juge auto` : Ollama s'il est présent, sinon **Claude Haiku** en headless |
| — | `/intention equilibre / decouvrir / consolider / confusables / graphie`, `/minutes` |
| — | Fin de session : bilan ; « mot connu » annoncé discrètement |

## 2. Méthode : le banc d'essai

On ne juge pas un ordonnanceur à l'œil. `lem/simulation.py` (`lem simuler`) :

* **Lexique synthétique** de 3 000 entrées : fréquences, domaines hiérarchisés, origines, morphèmes,
  quelques synonymes, et 15 % de **paronymes** (une lettre de différence avec un autre mot).
* **Élève simulé dont on connaît la vérité** :
  * ce qu'il sait au départ, structuré comme un vrai lecteur (fréquence, affinités par domaine,
    origine, morphème, bruit) : environ 19 % de mots connus, 24 % flous, 57 % inconnus ;
  * pour chaque mot appris, deux mémoires (sens, forme) qui oublient selon la courbe de FSRS
    R = (1 + t/9S)⁻¹ ;
  * ces mémoires se renforcent à chaque récupération réussie, d'autant plus que le souvenir était
    fragile (SInc ∝ e^{0,8(1−R)} − 1, comme FSRS) ;
  * savoir le sens ne donne pas la forme ;
  * **interférence** : en cherchant un mot, l'élève peut sortir son paronyme, d'autant plus que le
    paronyme est bien appris et que la paire n'a pas encore été distinguée. Un duel réussi distingue
    la paire, la liste « à ne pas confondre » un peu.
* **Trois profils** : faible, moyen (ordre de grandeur de la population de FSRS), fort. Les
  paramètres de l'élève ne sont pas ceux de FSRS : le moteur doit s'adapter à un élève qu'il ne
  connaît pas.
* **Sessions** de 15 min par jour pendant 30 jours, jouées par le **vrai** code (`PartieCartes`,
  journal, FSRS, carte), avec une horloge simulée. Durées : 14 s pour une définition, 7 s pour
  retrouver un mot, 5 s pour un duel, 4 s de révélation.
* **Mesure principale** (celle des simulateurs FSRS) : l'espérance du nombre de mots, non connus au
  départ, dont l'élève retrouverait le sens / le mot **un mois après la fin**, sans jouer entre-temps
  (Σ R). On la divise par les heures de jeu, et on retranche ce qu'il aurait retenu sans jouer.
* **Mesures secondaires** :
  * mots « acquis » (R ≥ 0,9 pour le sens, ≥ 0,8 pour le mot) ;
  * minutes passées sur des mots déjà connus ;
  * justesse de la carte de connaissance (score de Brier, AUC).

Chaque configuration est jouée avec 3 à 5 graines par profil ; ± indique l'écart-type entre graines.

## 3. Résultats finaux

v1 = moteur de `local/integration` ; v2 = réglages par défaut de cette branche (optimiseur hebdomadaire
compris). 5 graines par cellule.

**Avec paronymes et interférence** (modèle par défaut du banc ; un mot « retenu » est retrouvé, et non
remplacé par son paronyme) :

| Élève | Sens par heure | Mot par heure | Total | Carte (Brier) |
|---|---|---|---|---|
| moyen | 17,9 ± 0,3 → 18,7 ± 0,9 | 7,4 ± 0,2 → **16,1 ± 0,7** | **+38 %** | 0,56 → **0,08** |
| faible | 9,1 ± 0,7 → **12,7 ± 1,2** | 2,3 ± 0,3 → **10,1 ± 0,9** | **+99 %** | 0,38 → **0,08** |
| fort | 22,9 ± 0,2 → 22,9 ± 0,8 | 14,0 ± 0,4 → **20,8 ± 1,7** | **+18 %** | 0,64 → **0,08** |

Les duels (v2 sans duels → v2) : sens +2 % (moyen), +2 % (faible), +7 % (fort) ; mot +5 %, +6 %, +5 %.
L'effet est petit mais va dans le même sens sur les trois profils : ils restent, activés par défaut.

**Sans interférence** (première version du banc) :

| Élève | Sens retenus par heure | Mots retenus par heure | Total | Carte (Brier) | Mots acquis par heure (sens / mot) |
|---|---|---|---|---|---|
| moyen | 19,8 ± 0,3 → 19,9 ± 0,7 | 7,5 ± 0,8 → **17,4 ± 1,1** | **+36 %** | 0,57 → **0,08** | 4,1 / 2,6 → 11,8 / 9,7 |
| faible | 10,5 ± 0,4 → **13,7 ± 0,9** | 2,2 ± 0,3 → **10,5 ± 1,2** | **+90 %** | 0,41 → **0,08** | 0,5 / 0,1 → 5,7 / 5,0 |
| fort | 25,9 ± 0,3 → 25,3 ± 0,5 | 16,0 ± 0,4 → **23,1 ± 1,3** | **+16 %** | 0,66 → **0,08** | 11,2 / 7,4 → 15,0 / 14,4 |

Lecture :
* **Le gain est surtout sur le mot** (retrouver le mot à partir du sens) et sur l'élève faible. Le sens
  est au même niveau que v1, sauf pour l'élève faible où il progresse.
* **La carte de ce que tu sais devient juste.** Dans v1, chaque révision réussie d'un mot étudié
  remontait tout le lexique.
* **Le coût de la sonde** : 9 à 14 minutes sur 450 passées sur des mots déjà connus (contre 1 dans v1),
  le prix de l'exploration. Elle est rentable, puisque l'exploration seule fait gagner 15 à 50 % (§ 4).

## 4. Ce que les expériences ont appris

Toutes les expériences portent sur les profils moyen et faible (et fort quand c'est indiqué), avec
3 graines.

| Expérience | Effet | Décision |
|---|---|---|
| Carte de connaissance : les révisions de mots étudiés corrigeaient tous les θ | θ(type:mot) à +1,5 en 10 jours, P moyen 0,79, Brier 0,50 | Seules les sondes, vérifications et jeux de diagnostic propagent |
| Exploration (poids de p(1−p) dans le choix des mots) : 0 / 0,5 / 1,5 / 3 / 5 | sens par heure, élève moyen : 8,1 / 9,0 / 10,1 / 12,2 / ≈ 3 ; carte meilleure aussi | **3** : les mots à p ≈ 0,5 sont souvent flous, donc les moins chers à consolider, et la sonde corrige la carte |
| Mots ouverts à la fois : 3 / 5 / 8 | dans le bruit | 5 |
| Coût d'un mot pour le budget : 4 / 6 / 9 | dans le bruit | 6 |
| **Étapes FSRS dans la session (retours à 1 et 10 min)** | Elles réussissent à 99 % mais font un tiers des cartes, et FSRS surestime ensuite la mémoire (révisions réussies à 73 % au lieu de 90 %). **Sans elles** : +24 à +84 % de mots retenus | **Aucune étape par défaut** (réglable). Une récupération réussie de chaque côté dans la session, puis l'espacement — le « réapprentissage successif » de Rawson et Dunlosky |
| Sens → mot dans la même session, perdu si la session finissait avant | 144 mots sur 214 sans carte « mot » | Il ouvre la session suivante |
| Optimiseur FSRS commun à toutes les traces | Meilleure prédiction, mais intervalles du sens trop courts : le sens recule | **Un jeu de paramètres par trace** ; effet de la révision du même jour (w17–19) ajusté aussi |
| Optimiseur par trace, hebdomadaire | Mots retenus : +18 % (moyen), +34 % (faible), +16 % (fort) ; sens stable | Adopté, en fond à l'ouverture de lem |
| Rétention visée 0,85 / 0,90 / 0,95 | 0,85 : plus de mots vus, moins de mots retrouvés ; 0,95 : l'inverse | 0,90 |
| Sens → mot à 6 cartes et 2 min, ou à 10 cartes et 5 min | 10 et 5 min légèrement meilleur | 10 cartes, 5 min |
| Duels de rappel sur tes confusions réelles (un mot tapé pour l'autre), 4 par session | Aucun gain, dans le bruit (−1 % à −5 %) | Non retenu : le duel à la découverte suffit dans ce modèle |
| Sœurs à 2 cartes quand une carte en attente passe en avance (vu sur la vraie base, rapport PC) : écart 4 d'abord, 2 seulement à défaut | moyen : sens 19,4 → 19,5, mot 13,9 → 13,6 (bruit) ; faible : 13,1 → 13,7 et 8,2 → 9,0 ; Brier 0,08 inchangé | Adopté : la règle des 4 cartes est tenue sans rien coûter |
| Ouvrir un mot nouveau (au-delà de 5 ouverts) plutôt que d'avancer une carte en attente, pour tenir les 5 min de sens → mot | moyen : 19,1 et 13,5 ; faible : 13,7 et 8,4 ; plus de minutes sur des mots connus (moyen 27,5 contre 19,8) | Non retenu |
| Mots tapés librement (Bombe, Syllabaire) propagés aux θ (`banc_productions`, 70 et 300 mots, 3 graines) | Brier hors mots tapés 0,12 → 0,18–0,20 (70) et 0,29–0,30 (300) ; Σ P +35 à +80 % ; sans propagation, carte intacte. Sur la vraie base : 68 mots → +41 000 « connus » | **Ne propagent plus** (biais de sélection : on ne tape que ce qu'on connaît) ; migration v3 |

**Une hypothèse réfutée** : j'avais classé « une carte ratée ne revient pas dans la session » comme le
défaut n° 1 de v1. Le banc dit l'inverse pour ce modèle de mémoire : le réapprentissage immédiat est
massé (R ≈ 1), donc il rapporte peu et coûte cher. Ce qui compte, c'est **une** récupération réussie
après un vrai délai, puis l'espacement d'une session à l'autre.

## 5. Limites — à garder en tête

* **Le modèle d'élève est un modèle.** Il suit les hypothèses de FSRS : courbe de puissance, gain
  plus fort quand le souvenir est fragile. Les chiffres absolus en dépendent. Les écarts entre
  moteurs, à graine égale et sur trois profils, sont plus solides que les niveaux.
* **Le juge est parfait dans le banc** : l'élève s'auto-évalue honnêtement. En vrai, une définition
  est notée par bge-m3, puis Haiku, puis Claude. Les erreurs de notation ne sont pas simulées.
* **L'interférence est modélisée simplement.** Elle ne joue qu'entre paronymes orthographiques
  (similarité ≥ 0,7), pas entre voisins de sens (*acronyme / sigle*), que le lexique synthétique n'a
  pas. Avec ce modèle, les duels rapportent un peu (§ 3) ; l'effet réel sur des confusions de sens
  reste à mesurer sur ton journal (l'Atlas montre tes confusions).
* **Le lexique synthétique est petit** (3 000 entrées). Le temps de démarrage a été mesuré à part sur
  200 000 entrées : 3,2 s sans l'index compact du lexique, environ 1,5 s avec (le cas de lem).
* **Rien n'a été mesuré sur ta vraie base ni sur le Mac.** Une base jouée avec v1 est mise à niveau
  **seule, à la première ouverture** (`lem/memory/migration.py`, testée) :
  * les observations sont reconstituées depuis l'ancien journal (sinon le rejeu effacerait la carte) ;
  * FSRS est rejoué avec les nouveaux statuts ;
  * l'a priori est recalibré et la carte rejouée.

  Les θ changent un peu : seule la première réponse sur chaque mot propage désormais. Ensuite :
  quelques sessions de Cartes, puis `lem optimiser --essai` dès qu'il y a 150 révisions espacées.

## 6. Pour aller plus loin

* **Réglages à revoir sur ton journal réel**, une fois quelques semaines jouées :
  * le seuil de la sonde (note ≥ 3) ;
  * l'exploration ;
  * le coût d'un mot.

  `lem simuler --params '{"…"}'` permet de tester une idée avant de la jouer.
* **Interférence de sens** dans le banc : des paires de mots synthétiques « voisins de sens » (sans
  ressemblance de forme), pour mesurer les duels sur *acronyme / sigle*.
* **Sonde plus légère** pour les mots très probablement connus (p > 0,8) : une décision oui/non avec
  pseudo-mots, comme l'Étalonnage, au lieu d'une définition à écrire.
* **Calibrer le juge Haiku** contre Sonnet sur une centaine de définitions (accord, κ pondéré).

## 7. Diversité des mots nouveaux (essai de la session PC, 24 octobre)

Constat sur la vraie base : les sondes d'une session se suivent par familles (*érotiser, corporaliser,
particulariser, satelliser…*) parce que le levier des morphèmes (`-iser` : 5 600 mots) met en tête des dizaines
de cousins. `diversite` (Cartes, 0,3 par défaut, `/diversite 0` pour l'ôter) : choix glouton où chaque mot
retenu rend moins probables, de ce facteur, ceux qui partagent un morphème avec lui.

Banc, 3 graines par profil, `lem simuler --params '{"diversite": d}'` (d = 0,3 et 0,6 donnent exactement le même
résultat : le classement bascule dès qu'une pénalité existe) :

| Élève | Sens retenus/h | Mot retenus/h | Min. sur mots connus | Brier carte |
|---|---|---|---|---|
| faible | 13,8 → 13,9 | 11,2 → 10,6 | 22,0 → 18,2 | 0,078 → 0,082 |
| moyen | 19,6 → 19,4 | 16,6 → 16,6 | 20,4 → 19,5 | 0,078 → 0,081 |
| fort | 22,8 → 23,2 | 20,6 → 21,4 | 23,8 → 22,3 | 0,074 → 0,077 |

Lecture : **neutre pour les mots retenus** (écarts dans le bruit, ± 0,5 à 1,5), **un peu moins de temps perdu sur des
mots connus**, **carte légèrement moins nette** (+ 0,003 à + 0,004 de Brier, ≈ 1 écart-type). Ce n'est pas un gain
au sens de la règle du banc : adoptée parce que le banc ne mesure pas la monotonie d'une session (l'utilisateur
la trouve lassante) et que son coût mesuré est faible. À revoir sur le journal réel.

Autre changement de la même session : `/intention` tapée en cours de partie ne s'enregistre plus (une intention
« consolider » restée en réglage supprimait tout mot nouveau, sans que ce soit voulu).

## 8. Jamais de « sens → mot » pour un mot dont le sens n'a pas été retrouvé (session Mac, 25 octobre)

Constat sur la vraie base : *oiseau-moqueur*, sonde ratée le 22 (« une espèce de petit oiseau »), est revenu le
23 puis le 25 en « sens → mot » : la définition seule, pour un mot jamais reconnu. On ne découvre pas un mot par
sa définition. Règle (choix de l'utilisateur) : la sonde est toujours « mot → sens » (même en `/exercice prod`) ;
après une sonde ratée, « sens → mot » n'est programmé qu'après un « mot → sens » réussi (note ≥ 3, meilleur
correcteur) ; à la construction de la session, les révisions `prod` et les « sens → mot » laissés par une session
précédente sont écartés tant que le sens n'a jamais été retrouvé (`_reconnus` dans `lem/games/cartes.py`).

Banc, 3 graines par profil :

| Élève | Sens retenus/h | Mots retenus/h | Min. sur mots connus | Brier carte |
|---|---|---|---|---|
| faible | 13,6 → 13,2 | 11,1 → 8,0 | 18,4 → 22,3 | 0,078 → 0,073 |
| moyen | 18,8 → 19,0 | 15,9 → 12,4 | 20,4 → 25,7 | 0,080 → 0,080 |
| fort | 22,9 → 23,6 | 21,0 → 18,4 | 24,2 → 25,3 | 0,078 → 0,079 |

Lecture : **sens retenus inchangés**, carte inchangée, **mots retenus (forme) en baisse de 13 à 28 %** : le « sens
→ mot » arrive plus tard. Ce n'est pas un gain au sens de la règle du banc : adopté sciemment, parce que l'élève
simulé sait produire un mot qu'il n'a vu qu'une fois sans l'avoir compris, ce qui n'est pas le cas réel visé.
Essayée et écartée : remontrer le mot (« mot → sens ») 10 cartes et 5 minutes après la sonde ratée, puis
« sens → mot » s'il est retrouvé — formes un peu remontées (faible 8,6/h, moyen 13,0/h) mais sens en baisse chez
l'élève fort (23,6 → 20,8/h) : pas de gain net. À revoir sur le journal réel.

Même session : en « sens → mot » comme en graphie, seul le mot demandé est corrigé ; une réponse proche d'un AUTRE
mot (« petit-oiseau » pour *oiseau-moqueur*, proche de *petit oiseau*) est une mauvaise réponse, pas une faute à
retaper. La synchronisation de la dictée avec voxmac (socket Unix) est retirée.
