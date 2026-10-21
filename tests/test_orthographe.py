from datetime import timedelta

import pytest

from lem.db import now

from lem.games import REGISTRE
from lem.games.bombe import PartieBombe
from lem.games.cartes import PartieCartes
from lem.grading.direct import fautes_dans, noter_recep
from lem.memory import scheduler
from lem.orthographe import classer, cle_phonetique, diagnostiquer, motifs, profil, risque, surligner

from .conftest import eid


@pytest.mark.parametrize("saisie,correcte,types", [
    ("turiféraire", "thuriféraire", ["h_muet"]),
    ("eminent", "éminent", ["accent"]),
    ("apeller", "appeler", ["consonne_double"]),
    ("occurence", "occurrence", ["consonne_double"]),
    ("acornyme", "acronyme", ["inversion"]),
    ("acrinyme", "acronyme", ["frappe"]),
    ("acronime", "acronyme", ["grapheme"]),
    ("dilemne", "dilemme", ["grapheme"]),
])
def test_classer(saisie, correcte, types):
    assert classer(saisie, correcte) == types


def test_homophones():
    assert cle_phonetique("turiféraire") == cle_phonetique("thuriféraire")
    assert cle_phonetique("acronime") == cle_phonetique("acronyme")
    assert cle_phonetique("acrinyme") != cle_phonetique("acronyme")


def test_diagnostic_sans_cible(con):
    d = diagnostiquer(con, "turiféraire")
    assert d.correcte == "thuriféraire" and d.homophone and d.nature == "orthographe"
    assert diagnostiquer(con, "thuriféraire") is None          # forme exacte : pas de faute
    assert diagnostiquer(con, "zzzzzzzz") is None               # pas un mot reconnaissable
    d = diagnostiquer(con, "hemoragie")
    assert d.correcte == "hémorragie" and set(d.types) >= {"accent"} or "consonne_double" in d.types


def test_surligner():
    bon, tape = surligner("turiféraire", "thuriféraire")
    assert bon.plain == "thuriféraire" and "·" in tape.plain or "t" in tape.plain


def test_aucune_variante_dans_la_bombe(con, carte, rng):
    p = {**REGISTRE["bombe"].defauts(), "mode": "libre", "solutions_min": 1}
    b = PartieBombe(con, p, carte, rng=rng)
    b.invite.suite = "rif"
    r = b.repondre("turiféraire", 900)
    assert not r.valide and r.graphie and r.graphie.correcte == "thuriféraire"
    assert scheduler.dues(con, now() + timedelta(minutes=5), traces=("ortho",))   # la faute revient
    assert b.repondre("thuriféraire", 900).valide


def test_cartes_faute_puis_insistance(con, carte, rng):
    p = {**REGISTRE["cartes"].defauts(), "nouveaux": 4, "exercice": "prod", "confusables": False}
    partie = PartieCartes(con, p, carte, rng=rng)
    it = partie.suivant()
    faute = it.forme[0] + it.forme[2:]                          # une lettre omise
    res = partie.repondre(it, faute, 2500)
    assert res.prod.mode == "graphie" and not res.correct
    assert res.fautes and res.fautes[0].correcte == it.forme
    assert any(x.exercice == "graphie" and x.entree_id == it.entree_id for x in partie.file[:4])
    assert not partie.recopier(res, it.forme, faute, 900)
    assert partie.recopier(res, it.forme, it.forme, 900)
    partie.valider(res)
    traces = {r[0] for r in con.execute("SELECT trace FROM trace_etat WHERE entree_id = ?", (it.entree_id,))}
    assert {"prod", "ortho"} <= traces


def test_fautes_dans_une_definition(con):
    fautes = fautes_dans(con, "un flateur qui encense, un laudateur")
    assert [f.correcte for f in fautes] == ["flatteur"]
    v = noter_recep(con, eid(con, "thuriféraire"), "flateur, laudateur")
    assert v.fautes and v.fautes[0].correcte == "flatteur"


def test_profil_et_risque(con):
    assert "consonne_double" in motifs("occurrence") and "grec" in motifs("rythme")
    prof = profil(con)
    assert 0 < risque("occurrence", prof) < 1
