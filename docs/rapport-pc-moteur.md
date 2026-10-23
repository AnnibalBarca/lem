# Rapport PC — la vraie base sur le moteur v2

Branche `local/pc` (depuis `local/moteur`), session Claude Code du PC (WSL2, RTX 5070 Ti), 23 octobre 2026.
Exécution de [claude-local-pc.md](claude-local-pc.md), partie 1. **Version provisoire** : poussée dès que les
chiffres avant/après existaient ; la suite (jeux, juge, optimiseur, passage au Mac, chiffres pour les
prochaines fonctions) vient compléter ce document.

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

## 3. Corrigé

* **Atlas** : `lem atlas` plantait sur la vraie base (v1 comme v2). Une carte de graphie réussie écrit
  `"graphie": null` dans le journal ; le profil orthographique appelait `.get()` dessus
  (`lem/orthographe.py`, commit `676450f`, test de régression).

## 4. À suivre dans ce rapport

Temps d'ouverture des jeux, session de Cartes au pilote, `/juge auto`, `lem optimiser --essai`, passage au
Mac, chiffres pour la Bombe, la Mémoire verbale et les mnémoniques.
