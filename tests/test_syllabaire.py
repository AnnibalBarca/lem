from datetime import timedelta

from lem.db import now, regler_horloge
from lem.games import REGISTRE
from lem.games.syllabaire import PartieSyllabaire, etats_suites, note_suite, resume
from lem.journal import Evenement, ecrire
from lem.lexique import Lexique
from lem.suites import IndexSuites, code, suite_de


def _partie(con, **params):
    p = {**REGISTRE["syllabaire"].defauts(), **params}
    return PartieSyllabaire(con, p, lexique=Lexique(con))


def test_index_compte_chaque_forme_une_fois(con):
    lex = Lexique(con)
    ix = IndexSuites(lex)
    normes = [lex.normes.restore_key(i) for i in range(len(lex.normes)) if ix.valide[i]]
    for s in ("on", "ony", "me", "qu", "ph", "rh"):
        assert ix.n(s) == sum(s in x for x in normes)
    assert ix.n("o") == 0 and ix.n("abcd") == 0 and ix.n("é") == 0
    assert suite_de(code("nym"), 3) == "nym"
    assert all(1 <= n < 10 for _, n in ix.rares(10))
    assert sorted(ix.solutions("ony")) == sorted(x for x in normes if "ony" in x)


def test_index_exclut_noms_propres(con):
    lex = Lexique(con)
    propres = [r[0] for r in con.execute("SELECT forme FROM entree WHERE type = 'nom_propre'")]
    if not propres:
        return
    ix, tout = IndexSuites(lex), IndexSuites(lex, exclus=())
    assert sum(tout.repartition().values()) >= sum(ix.repartition().values())


def test_tour_note_et_retour(con):
    g = _partie(con, nouvelles=3)
    t = g.suivante()
    assert t.nouvelle and g.params["minimum"] <= t.n < g.params["seuil"]
    sols = g.index.solutions(t.suite)
    r = g.proposer(sols[0], 1200)
    assert r.valide and r.inedit
    assert not g.proposer(sols[0], 1300).valide                    # déjà donné
    assert "ne contient pas" in g.proposer("zzz", 1400).message
    b = g.terminer()
    assert b.note == 3 and b.lemmes and b.ancre is not None
    assert b.etendue[0] >= 1 and any(x.statut == "trouve" for x in b.lemmes)
    # Une suite séchée revient dans la session, quatre suites plus loin.
    t2 = g.suivante()
    b2 = g.terminer()
    assert b2.note == 1 and b2.ancre is not None
    vus = [g.suivante().suite for _ in range(6) if g.restant()]
    assert t2.suite in vus


def test_note_suite():
    assert note_suite(0, None, 5) == 1
    assert note_suite(1, 7000, 5) == 2
    assert note_suite(1, 2000, 5) == 3
    assert note_suite(3, 2000, 5) == 4


def test_rejeu_et_revisions_dues(con):
    g = _partie(con, nouvelles=1)
    t = g.suivante()
    g.proposer(g.index.solutions(t.suite)[0], 1000)
    g.terminer()
    etats = etats_suites(con)
    assert t.suite in etats and etats[t.suite].vues == 1
    assert etats[t.suite].card.due > now()
    plus_tard = now() + timedelta(days=30)
    regler_horloge(lambda: plus_tard)
    try:
        g2 = _partie(con, nouvelles=0)
        assert g2.dues == [t.suite]
        assert g2.suivante().suite == t.suite
        assert resume(con, g2.lex)["dues"] == 1
    finally:
        regler_horloge(None)


def test_mots_de_la_bombe_comptent_dans_l_etendue(con):
    g = _partie(con)
    s = g.nouvelles[0]
    mot = g.index.solutions(s)[0]
    ecrire(con, None, Evenement("bombe", "reponse", trace="prod", reponse=mot, correct=True))
    g.reconfigurer()
    assert any(x.statut == "deja" for x in g.deplier(s))


def test_graphie_diagnostiquee(con):
    g = _partie(con, seuil=100)
    g.tour = None
    while (t := g.suivante()) is not None and t.suite != "nym":
        g.terminer()
    if t is None:
        return
    r = g.proposer("acronime", 1000)
    assert not r.valide


async def test_tui_syllabaire(tmp_path):
    from textual.widgets import Input

    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranSyllabaire, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("down", "down", "enter", "j")
        e = app.screen
        assert isinstance(e, EcranSyllabaire)
        while not e.pret:
            await pilot.pause(0.05)
        suite = e.partie.tour.suite
        mot = e.partie.index.solutions(suite)[0]
        await pilot.press(*mot, "enter")
        while e.occupe:
            await pilot.pause(0.02)
        assert e.query_one("#saisie", Input).value == ""
        assert mot in str(e.query_one("#detail").render())
        await pilot.press("enter")                                  # Entrée seule : fini
        while e.occupe or e.etape != "bilan":
            await pilot.pause(0.02)
        assert "ancre" in str(e.query_one("#detail").render())
        await pilot.press("enter")                                  # suite suivante
        await pilot.pause(0.1)
        assert e.etape == "jeu" and e.partie.tour.suite != suite
