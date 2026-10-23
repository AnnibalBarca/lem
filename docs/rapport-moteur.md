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
  quelques synonymes.
* **Élève simulé dont on connaît la vérité** :
  * ce qu'il sait au départ, structuré comme un vrai lecteur (fréquence, affinités par domaine,
    origine, morphème, bruit) : environ 19 % de mots connus, 24 % flous, 57 % inconnus ;
  * pour chaque mot appris, deux mémoires (sens, forme) qui oublient selon la courbe de FSRS
    R = (1 + t/9S)⁻¹ ;
  * ces mémoires se renforcent à chaque récupération réussie, d'autant plus que le souvenir était
    fragile (SInc ∝ e^{0,8(1−R)} − 1, comme FSRS) ;
  * savoir le sens ne donne pas la forme.
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
* **Pas d'interférence entre mots proches** (*acronyme / sigle*) dans le modèle : l'effet des duels
  n'est pas mesuré, ils coûtent du temps sans rien rapporter au banc. Ils restent, parce que c'est
  ce que tu as demandé et que la discrimination est un effet bien documenté.
* **Le lexique synthétique est petit** (3 000 entrées). Le temps de démarrage a été mesuré à part sur
  200 000 entrées : 3,2 s sans l'index compact du lexique, environ 1,5 s avec (le cas de lem).
* **Rien n'a été mesuré sur ta vraie base ni sur le Mac.** À faire demain :
  * `lem rebuild` : les nouveaux statuts et la nouvelle table d'observations partent vides, l'ancien
    journal reste valable ;
  * le recalibrage de l'a priori se fait seul à l'ouverture ;
  * quelques sessions de Cartes ;
  * `lem optimiser --essai` dès qu'il y a 150 révisions espacées.

## 6. Pour aller plus loin

* **Réglages à revoir sur ton journal réel**, une fois quelques semaines jouées :
  * le seuil de la sonde (note ≥ 3) ;
  * l'exploration ;
  * le coût d'un mot.

  `lem simuler --params '{"…"}'` permet de tester une idée avant de la jouer.
* **Mesurer l'effet des duels** : ajouter au modèle d'élève une interférence entre confusables.
* **Sonde plus légère** pour les mots très probablement connus (p > 0,8) : une décision oui/non avec
  pseudo-mots, comme l'Étalonnage, au lieu d'une définition à écrire.
* **Calibrer le juge Haiku** contre Sonnet sur une centaine de définitions (accord, κ pondéré).
