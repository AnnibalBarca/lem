"""Juge distillé : absent → rien de chargé ; décodage et calibrage ; priorité ; canaris sur un vrai modèle."""

import json

import numpy as np
import pytest

from lem.grading import distille
from lem.grading.claude_headless import Element, Verdict, appliquer
from lem.grading.taxonomie import Erreur
from lem.journal import Evenement, ecrire
from lem.memory import scheduler

from .conftest import eid

ERREURS = [e.value for e in Erreur]


def test_absent_rien_ne_se_charge(tmp_path, monkeypatch, con):
    from lem.db import set_reglage
    from lem.grading import local

    monkeypatch.setattr(distille, "DOSSIER_MODELES", tmp_path / "modeles")
    j = distille.JugeDistille()
    assert j.dossier is None and not j.disponible()
    set_reglage(con, "juge_local", "distille")
    assert local.juge_depuis_reglage(con) is None
    monkeypatch.setattr(local.JugeLocal, "disponible", lambda self: False)
    monkeypatch.setattr("lem.grading.claude_headless.JugeClaude.disponible", lambda self: False)
    set_reglage(con, "juge_local", "auto")
    assert local.juge_depuis_reglage(con) is None


class _Encodage:
    ids, attention_mask = [0, 5, 6, 2], [1, 1, 1, 1]


class _Tok:
    def encode(self, a, b):
        self.vu = (a, b)
        return _Encodage()


class _Session:
    def run(self, _, entrees):
        assert entrees["input_ids"].shape == (1, 4)
        p = np.array([[0.0, 0.0, 0.0, 3.0, 0.0]])                     # précision 4
        e = np.zeros((1, len(ERREURS)))
        e[0, ERREURS.index("exacte")] = 5.0
        return p, e


def test_decodage_et_confiance(tmp_path, monkeypatch):
    d = tmp_path / "modeles" / "distille-essai"
    d.mkdir(parents=True)
    (d / "modele.onnx").write_bytes(b"")
    meta = {"erreurs": ERREURS, "temperature_precision": 2.0, "temperature_erreur": 1.0, "max_len": 256}
    tok = _Tok()
    monkeypatch.setitem(distille.JugeDistille._cache, str(d), (_Session(), tok, meta))
    j = distille.JugeDistille(d)
    assert j.correcteur == "distille:essai"
    el = Element(7, 1, {"mot": "thuriféraire", "categorie": "nom", "sens": [{"id": 1, "definition": "Flatteur."}],
                        "voisins": [{"mot": "atrabilaire", "definition": "Morose."}], "reponse": "flagorneur"})
    v = j.juger(el)
    assert v.precision == 4 and v.erreur == Erreur.EXACTE and v.id == 7
    assert 0.2 < v.confiance < 0.9                         # température 2 : confiance adoucie
    assert "thuriféraire (nom) — 1. Flatteur." in tok.vu[0] and "voisins : atrabilaire" in tok.vu[0]
    assert tok.vu[1] == "flagorneur"


def test_priorite_entre_claude_et_local(con):
    a = eid(con, "thuriféraire")
    ev = ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="x", note_auto=1))
    el = Element(ev, a, {})

    def verdict(p):
        return Verdict(id=ev, precision=p, erreur=Erreur.EXACTE, confiance=0.9)

    appliquer(con, [el], [verdict(2)], "local:qwen3:8b")
    appliquer(con, [el], [verdict(5)], "distille:essai")
    assert scheduler.note_finale(con, ev, 1) == 4          # distillé (5 → 4) avant local (2 → 1)
    appliquer(con, [el], [verdict(3)], "claude:sonnet")
    assert scheduler.note_finale(con, ev, 1) == 2          # Claude avant le distillé


# Canaris écrits à la main : un vrai modèle installé doit les tenir (sinon le test est sauté).
CANARIS = [
    ("thuriféraire", "nom", ["Clerc qui porte l'encensoir.", "Flatteur ou flatteuse ; flagorneur."],
     "flatteur, flagorneur", (4, 5)),
    ("thuriféraire", "nom", ["Clerc qui porte l'encensoir.", "Flatteur ou flatteuse ; flagorneur."],
     "morne, triste", (1, 2)),
    ("oiseau-moqueur", "nom", ["Passereau de la famille des Mimidae."], "une espèce de petit oiseau", (2, 3)),
    ("mitose", "nom", ["Division cellulaire asexuée, contrairement à la méiose."],
     "division d'une cellule en deux cellules filles identiques", (4, 5)),
    ("mitose", "nom", ["Division cellulaire asexuée, contrairement à la méiose."], "méiose", (1, 2)),
    ("acronyme", "nom", ["Sigle qui se prononce comme un mot ordinaire."], "sigle qui se prononce comme un mot", (4, 5)),
    ("acronyme", "nom", ["Sigle qui se prononce comme un mot ordinaire."], "un animal marin", (1, 1)),
    ("atrabilaire", "adj", ["Qui est d'une humeur triste et irritable."], "je ne sais pas", (1, 1)),
]


@pytest.mark.skipif(distille.dernier_modele() is None or not distille.JugeDistille().disponible(),
                    reason="pas de modèle distillé installé")
def test_canaris_sur_le_vrai_modele():
    j = distille.JugeDistille()
    rates = []
    for i, (mot, cat, sens, rep, (lo, hi)) in enumerate(CANARIS):
        el = Element(i, 0, {"mot": mot, "categorie": cat, "sens": [{"id": k, "definition": d} for k, d in enumerate(sens)],
                            "voisins": [], "reponse": rep})
        v = j.juger(el)
        if not lo <= v.precision <= hi:
            rates.append((mot, rep, v.precision))
    assert len(rates) <= 1, json.dumps(rates, ensure_ascii=False)
