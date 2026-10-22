# Passation — distiller un correcteur de définitions français (« notre Jev »)

Mandat pour une session Claude Code (cloud ou locale). À lire en entier avant d'écrire une ligne.
Contexte : [README.md](../README.md), [DESIGN.md](../DESIGN.md) § 2 (les LLM : où et pourquoi), le contrat de
notation `lem/grading/taxonomie.py`, `lem/grading/jev.py`, `lem/grading/claude_headless.py`.

## 0. Objectif

Remplacer, pour la notation **en direct** des définitions, la température cosinus de bge-m3 (`lem/grading/direct.py`)
par un petit modèle **qui lit la définition de référence et la réponse ensemble** (cross-encodeur) et rend un
verdict typé, comme Jev le ferait : précision 1–5, type d'erreur (taxonomie fermée), confiance calibrée.

Contraintes de l'application cible : **Mac, 3–4 Go de RAM au total pour lem**, hors ligne. Le modèle final doit
tenir en **≤ 600 Mo** (idéalement ≤ 300 Mo quantifié) et juger une réponse en **≤ 50 ms** sur CPU Apple / GPU.

On optimise **contre le français**, pas contre l'utilisateur : des définitions et des réponses d'apprenant
**générées**, jugées par un modèle fort, sur tout le lexique.

## 1. Règles de coordination

* Ton propre clone : `git clone git@github.com:AnnibalBarca/lem.git ~/lem-distill` (jamais `~/lem` ni
  `~/lem-dev`, d'autres sessions y travaillent). Branche `local/distillation` depuis `origin/local/integration`.
* Ne touche pas `~/.lem/lem.sqlite` s'il existe. Les données d'entraînement vont dans `~/lem-distill-data/`
  (hors dépôt). Le dépôt ne reçoit que du code, des scripts, la doc et de **petits** échantillons de test.
* Fichiers à toi : `lem/grading/distille.py` (inférence), `scripts/distillation/` (génération, étiquetage,
  entraînement, évaluation), `tests/test_distille.py`, une section de README. Ne modifie pas `direct.py` ni les
  jeux : on branchera ton modèle ensuite, derrière la même interface que `noter_recep`.
* Code, commentaires, commits en français, dans le style du dépôt. Petits commits ; `.venv/bin/pytest` vert.

## 2. Étapes

### 2.1 Vérifier l'environnement (et le dire)
`nvidia-smi`, RAM, disque, accès réseau (Hugging Face, kaikki.org), présence de `claude` en ligne de commande.
**S'il n'y a pas de GPU**, l'entraînement complet n'est pas pour toi : fais tout le reste (données, étiquettes,
code, essai sur ~500 exemples sur CPU) et écris la commande exacte que l'utilisateur lancera sur sa RTX 5070 Ti
(16 Go, WSL2) ou sur un GPU loué (A100 ≈ 1,4 $/h). Ne lance rien de coûteux sans le dire.

### 2.2 Les définitions de référence
Source : le Wiktionnaire français extrait par wiktextract (`https://kaikki.org/frwiktionary/raw-wiktextract-data.jsonl.gz`,
737 Mo ; **pas** kaikki.org/dictionary/French qui est le Wiktionnaire anglais). Réutilise le lecteur
`lem.ingest.wiktionnaire` sur une base temporaire (il gère déjà les « Forme de … », les gabarits « Définition
manquante », les domaines). Échantillonne ~15 000 sens : mots courants et rares, polysèmes, expressions ;
**ni noms propres ni sigles**.

### 2.3 Les réponses d'apprenant (génération)
Pour chaque sens, 3 à 4 réponses plausibles d'un francophone qui joue : juste en d'autres mots, synonymes en
liste, trop générale (« une espèce de petit oiseau » pour *oiseau-moqueur*), trop spécifique, connotation seule,
sens d'un autre mot proche (confusable : *thuriféraire* défini comme *atrabilaire*), contresens, vide/« je ne
sais pas ». Courtes, avec fautes de frappe parfois, comme au clavier. Varier le registre.
Générateur : un LLM (Claude via `claude -p --model haiku` si disponible et autorisé ; sinon un modèle ouvert
local). **Note la provenance** de chaque exemple.

### 2.4 Les étiquettes (le professeur)
Le professeur juge chaque paire avec **exactement** le vocabulaire fermé de `lem/grading/taxonomie.py` :
précision 1–5, erreur ∈ taxonomie, sens visé, mot réellement défini, confiance. Professeur : Claude Sonnet
(`claude -p --model sonnet`, par lots de 20 comme `claude_headless.py`), ou à défaut le plus fort modèle
disponible. Garde ~1 000 exemples jugés **deux fois** (accord du professeur avec lui-même = plafond atteignable).
Objectif : 40 000 à 60 000 paires étiquetées. Reprise possible : écrire au fil de l'eau (JSONL), jamais
tout en mémoire.

### 2.5 L'élève
Base : `BAAI/bge-reranker-v2-m3` (568 M, XLM-RoBERTa, bon en français d'après LAMAR, arXiv 2607.22042 — à
vérifier sur nos données). Entrée : `[définition de référence] [SEP] [réponse]` (option : plusieurs sens
concaténés pour les polysèmes). Têtes : régression de la précision (1–5) + classification de l'erreur.
Entraînement : quelques époques, lr ~2e-5, fp16/bf16 ; valider sur un jeu tenu à l'écart **par mot** (pas
d'un même mot à la fois en entraînement et en test). Calibration de la confiance (température ou isotonique).
Comparer à deux bases : la température bge-m3 actuelle, et un petit modèle candidat (MiniLM multilingue ~100 M)
pour voir ce que coûte la taille.

### 2.6 Évaluer, exporter
* Accord avec le professeur : κ pondéré (précision), F1 macro (erreur), Spearman ; et l'accord du professeur
  avec lui-même comme plafond.
* Latence CPU et mémoire ; export ONNX + quantification int8 ; mesure de la perte due à la quantification.
* Un petit jeu « canari » à la main (thuriféraire, oiseau-moqueur, mitose, acronyme…) dans les tests.

## 3. Livrables

* `scripts/distillation/` : `echantillonner.py`, `generer_reponses.py`, `etiqueter.py`, `entrainer.py`,
  `evaluer.py`, `exporter.py`, chacun reprenable et documenté (`--help`).
* `lem/grading/distille.py` : charge le modèle exporté (chemin `~/.lem/modeles/`), expose
  `juger(definitions, reponse) -> verdict` au format de la taxonomie ; aucune dépendance lourde au chargement de lem
  si le modèle est absent (repli sur `direct.py`).
* Un rapport `docs/rapport-distillation.md` : données (taille, provenance, coût), résultats (tableau des
  métriques contre les deux bases et le plafond), latence/mémoire, limites, et la commande pour réentraîner.
* Branche `local/distillation` poussée.
