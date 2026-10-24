# Rapport — distillation d'un correcteur de définitions (en pause)

Décision du 24 octobre 2026 : **suspendue**. Claude Haiku note déjà très bien les définitions ; distiller Sonnet
dans un petit modèle coûterait beaucoup de quota (≈ 1 330 appels Sonnet) pour un plafond qui n'est pas plus haut.
Le code reste, prêt à reprendre. Aucun modèle utile n'a été entraîné ni installé.

* **Données** (`~/lem-distill-data/`, hors dépôt, à conserver) : 15 001 mots échantillonnés (`echantillon.jsonl`),
  9 102 réponses générées par qwen3:8b (`reponses.jsonl`, 2 249 mots couverts), **4 917 étiquettes** de Sonnet
  (`etiquettes.jsonl`). Cible initiale : ≈ 60 000 réponses.
* **Modèle** : un seul essai (`modeles/essai`, MiniLM, entraîné sur ≈ 1 000 étiquettes : κ = 0, il prédit toujours
  la même classe). Exporté seulement en ONNX int8 de test (118 Mo, ≈ 3 ms par verdict dans lem, sans PyTorch) ;
  rien dans `~/.lem/modeles/`. Le juge `lem/grading/distille.py` est branché mais inactif sans modèle.
* **Pourquoi arrêté** : l'étiquetage a cessé au quota (`claude a échoué (1)`), la génération sur une coupure d'Ollama.
* **À mesurer avant de reprendre** : l'accord de Haiku avec Sonnet sur ≈ 400 des étiquettes tenues à l'écart
  (≈ 10 appels). Si le κ pondéré dépasse ≈ 0,8, la distillation n'a plus d'objet.

Reprise (depuis `~/lem-distill`, environnement `.venv-ml`, Ollama lancé) :

```sh
sh ~/lem-distill-data/journaux/lancer.sh            # génération + étiquetage, reprenables
python scripts/distillation/etiqueter.py --doubles 1000   # accord du professeur avec lui-même (plafond)
python scripts/distillation/entrainer.py --nom minilm      # puis --base BAAI/bge-reranker-base --nom base
python scripts/distillation/exporter.py --nom minilm       # ONNX int8 dans ~/.lem/modeles/
python scripts/distillation/evaluer.py --haiku 400         # contre le professeur, bge-m3 et Haiku
```

Puis `/juge distille` dans lem. Prévoir ≥ 10 000 étiquettes pour un élève qui apprend, et d'abord le test Haiku
ci-dessus : `evaluer.py` le fait déjà (partie « Haiku »).
