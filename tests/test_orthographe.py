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
    assert scheduler.dues(con, now() + timedelta(days=1), traces=("ortho",))      # la faute revient (sous un jour)
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


def test_dictee_reperee_et_jamais_comptee_comme_faute(con, carte, rng):
    from lem.journal import Frappe, nettoyer_saisie

    assert Frappe(None, 0, 0, 1).mode == "dictee" and Frappe(None, 8, 1, 0).mode == "clavier"
    assert nettoyer_saisie(" Acronyme. ") == "Acronyme"
    p = {**REGISTRE["cartes"].defauts(), "nouveaux": 4, "exercice": "prod", "confusables": False}
    partie = PartieCartes(con, p, carte, rng=rng)
    it = partie.suivant()
    faute = it.forme[0] + it.forme[2:]
    res = partie.repondre(it, faute + ".", 2500, Frappe(300, 0, 0, 1))      # Whisper a mal écrit
    assert res.correct and res.prod.mode == "dicte" and not res.fautes
    assert con.execute("SELECT mode_saisie FROM evenement WHERE id = ?", (res.evenement_id,)).fetchone()[0] == "dictee"
    assert not con.execute("SELECT 1 FROM evenement WHERE trace = 'ortho'").fetchone()
    g = partie._item("graphie", it.entree_id)
    with pytest.raises(ValueError):
        partie.repondre(g, it.forme, 900, Frappe(100, 0, 0, 1))


def test_migration_colonne_mode_saisie(tmp_path):
    import sqlite3

    from lem.db import connect

    chemin = tmp_path / "ancienne.sqlite"
    vieille = sqlite3.connect(chemin)
    vieille.execute("CREATE TABLE evenement (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id INTEGER, "
                    "jeu TEXT NOT NULL, type TEXT NOT NULL, entree_id INTEGER, sens_id INTEGER, trace TEXT, "
                    "reponse TEXT, correct INTEGER, note_auto INTEGER, latence_ms INTEGER, premiere_frappe_ms INTEGER, "
                    "frappes INTEGER, effacements INTEGER, data TEXT NOT NULL DEFAULT '{}')")
    vieille.commit()
    vieille.close()
    con = connect(chemin)
    assert "mode_saisie" in {r[1] for r in con.execute("PRAGMA table_info(evenement)")}


def test_profil_avec_graphie_juste(con):
    """Une carte de graphie réussie écrit « "graphie": null » : l'Atlas ne doit pas planter (vraie base)."""
    from lem.journal import Evenement, ecrire
    from lem.orthographe import profil

    a = con.execute("SELECT id FROM entree WHERE forme = 'acronyme'").fetchone()[0]
    ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="ortho", reponse="acronyme", correct=True,
                                data={"exercice": "graphie", "graphie": None}))
    assert sum(occ for _, occ in profil(con).values()) >= 1
