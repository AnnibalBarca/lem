from datetime import timedelta

import numpy as np

from lem.db import ajouter_relation, now, regler_horloge, upsert_entree
from lem.games import REGISTRE
from lem.games.bombe import PartieBombe
from lem.games.synonymes import (
    PartieSynonymes,
    etats_synonymes,
    note_synonyme,
    resume,
)
from lem.journal import Evenement, ecrire
from lem.lexique import Lexique


def _peupler_synonymes(con):
    """Ajoute quelques grappes de synonymes réalistes dans la base de test."""
    mots = [
        ("équanimité", "nom", 2.0, "formal", "Égalité d'humeur, calme constant."),
        ("ataraxie", "nom", 1.7, "literary", "Tranquillité de l'âme."),
        ("flegme", "nom", 3.2, None, "Calme imperturbable."),
        ("impassibilité", "nom", 2.5, "formal", "Caractère de ce qui est impassible."),
        ("sérénité", "nom", 3.8, None, "Calme moral."),
        ("dissension", "nom", 2.9, "literary", "Désaccord profond entre personnes."),
        ("désaccord", "nom", 4.1, None, "Manque d'accord."),
        ("mésentente", "nom", 3.3, None, "Défaut d'entente."),
        ("discorde", "nom", 3.4, "literary", "Désunion, querelle."),
        ("flagorneur", "nom", 2.4, "literary", "Flatteur servile."),
        ("encenseur", "nom", 1.9, "literary", "Celui qui loue avec excès."),
    ]
    ids = {}
    for forme, pos, zipf, reg, defi in mots:
        eid = upsert_entree(con, forme, pos, zipf=zipf)
        con.execute("INSERT INTO sens(entree_id, rang, definition, registre) VALUES(?, 1, ?, ?)", (eid, defi, reg))
        con.execute(
            "INSERT OR IGNORE INTO connaissance(entree_id, p_prior, p_connu, maj) VALUES(?, 0.5, 0.5, '2026-01-01')",
            (eid,),
        )
        ids[forme] = eid

    for a, b in [
        ("équanimité", "ataraxie"),
        ("équanimité", "flegme"),
        ("équanimité", "impassibilité"),
        ("équanimité", "sérénité"),
        ("dissension", "désaccord"),
        ("dissension", "mésentente"),
        ("dissension", "discorde"),
        ("flatteur", "flagorneur"),
        ("flatteur", "encenseur"),
        ("flatteur", "thuriféraire"),
        ("flatteur", "laudateur"),
    ]:
        ea = ids.get(a) or con.execute("SELECT id FROM entree WHERE forme = ?", (a,)).fetchone()[0]
        eb = ids.get(b) or con.execute("SELECT id FROM entree WHERE forme = ?", (b,)).fetchone()[0]
        ajouter_relation(con, ea, eb, "synonyme", 1.0, "wiktionnaire")
    con.commit()
    return ids


def _partie(con, **params):
    p = {**REGISTRE["synonymes"].defauts(), **params}
    return PartieSynonymes(con, p, lexique=Lexique(con))


def test_note_synonyme():
    assert note_synonyme(0, None, 5) == 1
    assert note_synonyme(1, 7000, 5) == 2
    assert note_synonyme(1, 2000, 5) == 3
    assert note_synonyme(2, 2000, 5) == 4


def test_synonymes_tour_upgrade_et_retour(con):
    _peupler_synonymes(con)
    g = _partie(con, nouveaux=3, ordre="hasard")
    t = g.suivant()
    assert t is not None and t.nouveau and t.n_synonymes >= 2
    # Refus du mot lui-même
    assert not g.proposer(t.forme, 800).valide
    # Vrai synonyme
    syn_id = next(iter(g.graphe[t.entree_id]))
    syn_forme = con.execute("SELECT forme FROM entree WHERE id = ?", (syn_id,)).fetchone()[0]
    r = g.proposer(syn_forme, 1200)
    assert r.valide and r.mode == "synonyme" and r.inedit
    # Doublon refusé
    assert not g.proposer(syn_forme, 1300).valide
    b = g.terminer()
    assert b.note == 3 and b.ancre is not None and b.etendue[0] >= 1
    assert any(x.statut == "trouve" for x in b.synonymes)

    # Mot séché → revient 4 tours plus loin
    t2 = g.suivant()
    b2 = g.terminer()
    assert b2.note == 1
    vus = []
    while g.restant():
        nx = g.suivant()
        if nx is None:
            break
        vus.append(nx.entree_id)
        g.terminer()
    assert t2.entree_id in vus


def test_synonymes_proche_bge_m3(con):
    ids = _peupler_synonymes(con)
    cible = ids["dissension"]
    autre = ids["flegme"]

    class FauxMagasin:
        def sim(self, a, b):
            return 0.85 if {a, b} == {cible, autre} else 0.1

    p = {**REGISTRE["synonymes"].defauts(), "seuil_sem": 0.76}
    g = PartieSynonymes(con, p, magasin_entrees=FauxMagasin(), lexique=Lexique(con))
    g.nouveaux = [cible]
    g.dues = []
    t = g.suivant()
    assert t.entree_id == cible
    r = g.proposer("flegme", 1100)
    assert r.valide and r.mode == "proche" and r.sim == 0.85
    b = g.terminer()
    assert any(x.proche and x.forme == "flegme" for x in b.synonymes)


def test_synonymes_rejeu_fsrs_et_resume(con):
    _peupler_synonymes(con)
    g = _partie(con, nouveaux=1)
    t = g.suivant()
    syn_id = next(iter(g.graphe[t.entree_id]))
    syn_forme = con.execute("SELECT forme FROM entree WHERE id = ?", (syn_id,)).fetchone()[0]
    g.proposer(syn_forme, 900)
    g.terminer()
    etats = etats_synonymes(con)
    assert t.entree_id in etats and etats[t.entree_id].vues == 1
    assert resume(con)["vues"] == 1
    plus_tard = now() + timedelta(days=30)
    regler_horloge(lambda: plus_tard)
    try:
        g2 = _partie(con, nouveaux=0)
        assert t.entree_id in g2.dues
        assert resume(con)["dues"] == 1
    finally:
        regler_horloge(None)


def test_bombe_mode_synonymes(con, carte, rng):
    _peupler_synonymes(con)
    p = {**REGISTRE["bombe"].defauts(), "mode": "synonymes"}
    b = PartieBombe(con, p, carte, rng=rng, lexique=Lexique(con))
    inv = b.invite
    assert inv.est_synonyme and inv.cible_id is not None and inv.n_solutions >= 2
    # Refus du mot lui-même
    assert not b.repondre(inv.cible_forme, 700).valide
    # Indice /d renvoie un synonyme valide
    ind = b.indice()
    assert ind is not None and ind["entree_id"] in b._graphe_syn[inv.cible_id]
    # Réponse avec un autre synonyme (non aidé)
    autre_syn = next(
        (i for i in b._graphe_syn[inv.cible_id] if i != ind["entree_id"]),
        ind["entree_id"],
    )
    forme_syn = con.execute("SELECT forme FROM entree WHERE id = ?", (autre_syn,)).fetchone()[0]
    r = b.repondre(forme_syn, 1100)
    assert r.valide and r.points > 0
    assert b.invite.cible_id != inv.cible_id
    # Explosion révèle des synonymes
    r_exp = b.exploser()
    assert not r_exp.valide and r_exp.revelation is not None


async def test_tui_synonymes(tmp_path):
    from textual.widgets import Input

    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranSynonymes, LemApp

    chemin = tmp_path / "syn.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    ctx = Contexte(chemin)
    _peupler_synonymes(ctx.con)
    app = LemApp(ctx)
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("6")
        e = app.screen
        assert isinstance(e, EcranSynonymes)
        while not e.pret:
            await pilot.pause(0.05)
        t = e.partie.tour
        assert t is not None
        syn_id = next(iter(e.partie.graphe[t.entree_id]))
        syn_forme = ctx.con.execute("SELECT forme FROM entree WHERE id = ?", (syn_id,)).fetchone()[0]
        await pilot.press(*syn_forme, "enter")
        while e.occupe:
            await pilot.pause(0.02)
        assert e.query_one("#saisie", Input).value == ""
        assert syn_forme in str(e.query_one("#detail").render())
        await pilot.press("enter")
        while e.occupe or e.etape != "bilan":
            await pilot.pause(0.02)
        assert "ancre" in str(e.query_one("#detail").render())
