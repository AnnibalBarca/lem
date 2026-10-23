# Rapport PC — la vraie base sur le moteur v2

Branche `local/pc` (depuis `local/moteur`), session Claude Code du PC (WSL2, RTX 5070 Ti), 23 octobre 2026.
Exécution de [claude-local-pc.md](claude-local-pc.md), partie 1.

## 1. La base

* Copie de sauvegarde, faite lem fermé (API de sauvegarde de SQLite) : `~/.lem/lem-v1-sauvegarde.sqlite`
  (2,7 Go, `integrity_check` ok). **Ne jamais la supprimer.**
* Journal à la migration : 333 événements, 0 notation, 7 sessions (Bombe surtout, quelques Cartes).

## 2. Migration v1 → v2

Déclenchée par le chemin qu'emprunte lem à l'ouverture (`Contexte.carte` → `migration.migrer`) :
**5,5 s**, `version_moteur` 0 → 2.

### Ce qui ne doit pas bouger — n'a pas bougé

| Table | Avant | Après |
|---|---|---|
| `evenement` | 333 | 333 |
| `notation` | 0 | 0 |
| `session` | 7 | 7 |
| `trace_etat` | 17 (15 graphie, 2 sens) | 17 (idem) |
| `theta` (dimensions) | 105 | 105 |
| `observation` | (table absente en v1) | 119 reconstituées |

Les 17 traces ont le statut nul : c'est le parcours FSRS normal ; `connu_sonde` / `verifie` n'existent que
pour les sondes, et aucune sonde n'a encore été jouée.

### Ce qui bouge

| Mot | a priori v1 → v2 | P(connu) v1 → v2 |
|---|---|---|
| acronyme | 0,140 → 0,176 | 0,355 → 0,504 |
| pomme de terre | 0,161 → 0,203 | 0,381 → 0,523 |
| thuriféraire | 0,164 → 0,205 | 0,437 → 0,611 |
| isotope (nom / adj.) | 0,233 / 0,264 → 0,286 / 0,322 | 0,508 / 0,550 → 0,655 / 0,692 |
| sigle | 0,426 → 0,495 | 0,717 → 0,833 |

| Carte | v1 | v2 |
|---|---|---|
| Seuil de l'a priori (Zipf) | 3,25 | 3,08 |
| Σ P_prior (hors noms propres et sigles) | 44 623 | 51 789 |
| Σ P(connu) (hors noms propres et sigles) | 77 522 | 104 214 |
| Entrées « acquises » (P ≥ 0,9) | 10 559 | 19 936 |
| θ les plus forts | type:mot +1,01 · origine:français +0,60 · zipf:2 +0,39 | type:mot +0,93 · origine:français +0,55 · origine:latin +0,35 |

Deux causes, mesurées séparément :

1. **L'a priori (voulu).** v2 calibre la cible de 45 000 sur les lemmes (une graphie une fois, sans sigles
   ni composés de fréquence inconnue) : le seuil descend de 3,25 à 3,08 et chaque mot gagne un peu. C'est le
   correctif prévu (*acronyme* 0,14 → 0,18).
2. **Les θ restent hauts, à cause de la Bombe — un biais de sélection.** Des 119 observations
   reconstituées : 68 mots spontanés de la Bombe (y = 1, poids 0,5), 3 cibles trouvées (y = 1),
   45 révélations (y = 0,4, poids 0,3), 3 réponses aux Cartes (sans propagation). À la Bombe, on ne tape que
   des mots qu'on connaît : ces observations sont positives **par construction**, et elles propagent à tout le
   lexique (type, origine, bande de fréquence).

**Expérience** (sur une copie, rejeu de la carte, rien de changé dans le code) :

| Rejeu | Σ P(connu) hors NP | « acquises » | θ les plus forts |
|---|---|---|---|
| tel que migré | 104 214 | 19 936 | type:mot +0,93, origine:français +0,55 |
| mots spontanés de la Bombe sans propagation | **63 463** | **6 922** | nom_propre +0,26, type:mot +0,22 |
| aucune observation de la Bombe propagée | 51 792 | 4 743 | — |

**68 mots tapés à la Bombe ajoutent 41 000 mots « connus » à la carte.**

**Proposition pour la session cloud** (le moteur est à elle ; à passer au banc avant toute adoption) : un mot
spontané de la Bombe ne corrige que son propre résidu, sans propager ; seules les cibles (que le moteur a
choisies, donc non sélectionnées par le joueur) et les révélations propageraient. Le banc devrait simuler un
joueur de Bombe qui ne tape que des mots connus, pour mesurer la dérive de la carte (Brier) avec et sans.

## 3. Les jeux sur la vraie base

Toutes les vérifications sur une **copie** de la base migrée (ouvrir un jeu écrit au journal).

### Ouverture (interface réelle, pilote Textual, caches chauds)

Du lancement de lem au jeu prêt, chaque fois dans un processus neuf (chargement de la session compris) :

| Jeu | Prêt en | Mémoire du processus |
|---|---|---|
| Cartes | 3,9 s | 1,2 Go |
| Bombe | 3,1 s | 1,6 Go |
| Mémoire verbale | 2,5 s | 1,5 Go |
| Mot ou pas mot | 2,6 s | 1,7 Go |
| Étalonnage | 2,6 s | 2,1 Go |
| Atlas | 0,9 s | 1,9 Go |

Accueil : 0,3 s. Premier lancement sur une base sans cache : + 14 s (index du lexique et vecteurs, une fois).

### Une session de Cartes de 10 minutes

Jouée par l'interface réelle, avec l'horloge du moteur avancée comme au banc (14 s par définition, 7 s par
mot, 5 s par duel, 4 s de révélation) ; joueur scripté : 40 % des sondes connues, 70 % des mots retrouvés,
80 % des duels gagnés. `/juge auto` choisit **qwen3:8b** (Ollama du PC).

Résultat : 40 cartes en 9,2 min, bilan affiché (« 10 mots appris, 9 mots déjà connus, une vérification dans
1 à 2 mois »).

* **Sonde** : chaque mot nouveau commence par « le connais-tu ? » (19 sondes, 9 connues, 10 apprises).
* **Parcours** d'un mot appris : sonde ✗ → duel → sens → mot.
* **À revoir dans le moteur** (fichiers de la session cloud, non modifiés ici) :
  * **sœurs** : 3 fois sur 10, le duel arrive **2 cartes** après la sonde (*stoïquement* 10 → 12,
    *se reconvertir* 17 → 19, *social-démocratiser* 33 → 35), alors que la règle est « jamais à moins de 4 » ;
  * **sens → mot** : 10 à 20 cartes après la sonde (une fois 6, en fin de session), mais **1,2 à 4,9 minutes**
    seulement : la condition « et 5 minutes » n'est pas tenue dans la session.
* **Données (côté ingestion, le mien)** : la moitié des sondes portent sur des composés à trait d'union de
  préfixe (*re-ébranler*, *re-recopier*, *re-modeler* « Orthographe parfois rencontrée pour remodeler »,
  *supra-normaliser*, *redouble-cliquer*). Il y en a **938** (re-, anti-, supra-, social-…). Leur fréquence
  estimée vaut 1,9 à 2,1 (le mot le plus rare moins un point : wordfreq coupe au trait d'union et connaît
  *re* et *modeler*), d'où P(connu) ≈ 0,33, en plein dans la zone que la sonde explore. Ce sont des dérivés
  transparents ou des variantes, sans valeur d'apprentissage. **Proposition** : pénaliser davantage les
  composés à trait d'union dont le premier élément est un préfixe (et les entrées dont le seul sens est
  « Variante orthographique de… » / « Orthographe parfois rencontrée pour… »), puis `lem prior`. Cela change
  l'a priori de ces entrées : à décider avec la session cloud.

### Optimiseur

`lem optimiser --essai` : 0 révision espacée par trace (il en faut 150) ; paramètres par défaut, comme prévu
sur un journal de 333 événements.

## 4. Passage au Mac

La base est prête : WAL vidé (`PRAGMA wal_checkpoint(TRUNCATE)`), `integrity_check` ok, `version_moteur` = 2.
Pour la poser sur le Bureau Windows (lem fermé) :

```sh
cp ~/.lem/lem.sqlite /mnt/c/Users/Utilisateur/Desktop/lem.sqlite
```

(2,7 Go ; 172 Go libres sur C:). De là, AirDrop ou clé USB vers `~/.lem/lem.sqlite` sur le Mac. **Ne pas
copier** `~/.lem/cache/` : il se reconstruit au premier lancement (≈ 15 s sur le PC). La base est déjà en v2 :
la migration ne se rejouera pas sur le Mac.

## 5. Chiffres pour les prochaines fonctions

### Bombe : suites de lettres

Formes valides (règles de `lem/games/bombe.py` : ni noms propres ni sigles) : **1 883 793**. Suites a–z
présentes dans au moins une forme, et nombre de formes valides distinctes qui les contiennent :

| Suites | Total | 1–9 | 10–49 | 50–99 | 100–499 | 500 + |
|---|---|---|---|---|---|---|
| 2 lettres | 664 | 50 | 87 | 38 | 98 | 391 |
| 3 lettres | 9 397 | 3 360 | 1 446 | 632 | 1 366 | 2 593 |

30 exemples de la bande 1–49 (tirage fixe) : EVC 1, HWB 1, PKK 1, KSR 1, JTM 1, KCD 1, TCK 1, HVE 1, TFF 1,
HRH 2, FSO 2, WCL 2, UPW 2, OFN 3, OII 4, FVE 4, HRW 6, CBU 6, JIU 8, IPM 8, LJA 8, YEV 12, OEA 14, LDT 15,
ZUM 21, ODS 35, MOA 38, NOK 40, IMY 42, LPL 47.

Lecture : la bande 1–49 (4 943 suites, dont 137 de 2 lettres) n'est pas un vivier de « mots difficiles » ;
elle est faite surtout de **sigles rangés comme mots** (*evc*, *kcd* : le Wiktionnaire ne les marque pas
comme sigles), de gentilés rares (*casteljalousain*), de graphies inclusives (*juifve*, *actifve*) et de mots
étrangers (*barwoods*). À filtrer avant d'en faire des invites. Par ailleurs, **3 726 formes valides**
contiennent des caractères hors a–z après normalisation (Ɲ, exposants…) ; la Bombe tire ses suites avec
`isalpha()`, qui les accepte : `[a-z]` serait plus sûr (`bombe.py`, session cloud).

### Mémoire verbale : le vivier

| `etrangete` | Mots du vivier | Avec une définition | Noms propres | Composés | Expressions | Mots simples | Plusieurs mots |
|---|---|---|---|---|---|---|---|
| 0,3 | 384 720 | 99 % | 28 % | 17 % | 3 % | 52 % | 22 % |
| 0,7 (défaut) | 179 536 | 99 % | 53 % | 27 % | 6 % | 14 % | 38 % |
| 1,0 | 25 648 | 100 % | 56 % | 32 % | 12 % | 0 % | 85 % |

(+ 56 mots « presque acquis » mêlés au vivier.) 30 exemples à 0,7 : Bokholt-Hanredder, Ngalula, avoir un cœur
gros comme ça, au fil de l'eau, requin-limon faucille, Bogliasco, Borthwick, Thorington, Norbury, Lubrez,
Ziehl négatif, La Boissière-École, La Ville-du-Bois, 2,2′,3,5,6,6′-hexachlorobiphényle, Lieze, méthylformique,
betisoak, paramètre d'étalement d'un liquide sur un support, Latchingdon, Schwanstetten, Orquevaux, alerte à
la bombe, Saint-Loup-sur-Aujon, Qu'Appelle, dislocation atlanto-occipitale, aristoloche à nervures peu
nombreuses, greffe à l'anglaise compliquée, buste en hermès, Icogne, recyclage des gaz d'échappement.

Lecture : au réglage par défaut, **plus de la moitié du vivier sont des toponymes**, et à 1,0 il ne reste
aucun mot simple. Le score d'étrangeté récompense les noms propres, la longueur et les espaces ; les mots
visés par DESIGN.md (*kjökkenmödding*, *chlorfenvinphos*) y sont noyés.

### Mnémoniques

Table `mnemo` : **0 ligne** (aucune image mentale saisie pour l'instant).

## 6. Corrigé

* **Atlas** : `lem atlas` plantait sur la vraie base (v1 comme v2). Une carte de graphie réussie écrit
  `"graphie": null` dans le journal ; le profil orthographique appelait `.get()` dessus
  (`lem/orthographe.py`, test de régression).
* **Tests** : les tests de l'interface écrivaient l'index et les vecteurs de leurs bases de démo dans le vrai
  `~/.lem/cache` (69 dossiers trouvés, plus des fichiers d'une version antérieure du cache). Fixture
  automatique dans `tests/conftest.py` ; dossiers parasites supprimés (il ne reste que celui de la base).

## 7. Reste à faire

* Décider (session cloud) : propagation des mots spontanés de la Bombe (§ 2), sœurs et délai de sens → mot
  (§ 3), suites `[a-z]` dans la Bombe, composition du vivier de la Mémoire verbale.
* Décider (ensemble) : fréquence des composés à préfixe et des variantes (§ 3).
* Sur le Mac : `docs/claude-local.md`, base copiée (§ 4).
* Partie 2 : la distillation du correcteur de définitions (`docs/rapport-distillation.md` à venir).
