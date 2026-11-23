import random

from lem.db import upsert_entree
from lem.games import REGISTRE
from lem.games.labog import (
    IndexLabog,
    PartieLabog,
    chemin_mot,
    construire_trie,
    generer_grille,
    points_mot,
    resoudre_grille,
    resume,
    tourner_grille,
)
from lem.lexique import Lexique


def _peupler_labog(con):
    """Ajoute des mots courants (connus) et un mot rare (inconnu) pour tester la séparation."""
    connus = [
        ("porte", 5.0), ("port", 4.8), ("pore", 3.6), ("robe", 4.5), ("rose", 4.7),
        ("sort", 4.6), ("sorte", 4.8), ("note", 4.9), ("noter", 4.3), ("toner", 3.5),
        ("torte", 3.2), ("forte", 4.9), ("fort", 5.1), ("foret", 4.4), ("terme", 4.7),
        ("terre", 5.2), ("rente", 4.0), ("reste", 5.0), ("rester", 5.1), ("serre", 4.2),
        ("serrer", 4.3), ("tente", 4.4), ("tenter", 4.5), ("entre", 5.5), ("entrer", 5.0),
        ("notre", 5.6), ("votre", 5.5), ("retour", 5.0), ("route", 5.1), ("tour", 5.0),
        ("tourner", 4.7), ("porter", 4.9), ("sortir", 5.0), ("sentir", 4.9), ("sens", 5.1),
    ]
    for forme, z in connus:
        eid = upsert_entree(con, forme, "nom", zipf=z)
        con.execute("INSERT INTO sens(entree_id, rang, definition) VALUES(?, 1, ?)", (eid, f"Définition de {forme}."))
        con.execute(
            "INSERT OR REPLACE INTO connaissance(entree_id, p_prior, p_connu, maj) VALUES(?, 0.95, 0.95, '2026-01-01')",
            (eid,),
        )
    # Mot inconnu / obscur sur une grille de test
    eid_r = upsert_entree(con, "rton", "nom", zipf=0.0)
    con.execute("INSERT INTO sens(entree_id, rang, definition) VALUES(?, 1, 'Mot obscur.')", (eid_r,))
    con.execute(
        "INSERT OR REPLACE INTO connaissance(entree_id, p_prior, p_connu, maj) VALUES(?, 0.05, 0.05, '2026-01-01')",
        (eid_r,),
    )
    con.commit()


def test_solveur_chemin_et_rotation():
    # Grille :
    # P O R T
    # E S T E
    # N O R E
    # T E R S
    grille = tuple("portestenoreters")
    trie = construire_trie(["port", "porte", "sort", "sorte", "note", "terre", "zzzz"])
    trouves = resoudre_grille(grille, trie, 4, 9)
    assert {"port", "porte", "sort", "sorte", "note", "terre"} <= trouves
    assert "zzzz" not in trouves
    ch = chemin_mot(grille, "porte")
    assert ch is not None and len(ch) == 5 and len(set(ch)) == 5
    assert "".join(grille[i] for i in ch) == "porte"
    assert chemin_mot(grille, "zzzz") is None

    # 4 rotations ramènent la grille identique et conservent les mots
    g2 = tourner_grille(grille)
    assert resoudre_grille(g2, trie, 4, 9) == trouves
    assert tourner_grille(tourner_grille(tourner_grille(g2))) == grille
    assert points_mot(4) == 1 and points_mot(7) == 5 and points_mot(9) == 11


def test_labog_uniquement_connus_et_compteurs(con, carte):
    _peupler_labog(con)
    p = {**REGISTRE["labog"].defauts(), "seuil_connu": 0.72, "zipf_min": 3.0}
    grille = tuple("portestenoreters")
    # Recharger la carte pour intégrer les nouveaux mots connus
    from lem.memory.knowledge import CarteConnaissance

    lex = Lexique(con)
    carte2 = CarteConnaissance(con, lex)
    g = PartieLabog(con, p, carte2, rng=random.Random(42), lexique=lex, grille_forcee=grille)

    assert "porte" in g.cibles and "sorte" in g.cibles
    assert "rton" not in g.cibles and "rton" in g.inconnus

    # Compteurs initiaux
    init_avant = dict((c, (f, t)) for c, f, t in g.compteurs_initiales())
    long_avant = dict((L, (f, t)) for L, f, t in g.compteurs_longueurs())
    assert init_avant["P"][0] == 0 and init_avant["P"][1] >= 1
    assert long_avant[5][0] == 0 and long_avant[5][1] >= 1

    # Réponse valide
    r = g.proposer("porte", 900)
    assert r.valide and not r.bonus and r.points == points_mot(5)
    assert not g.proposer("porte", 1000).valide               # doublon
    assert not g.proposer("por", 1000).valide                 # trop court
    assert not g.proposer("zzzz", 1000).valide                # pas sur la grille

    # Compteurs mis à jour
    init_apres = dict((c, (f, t)) for c, f, t in g.compteurs_initiales())
    long_apres = dict((L, (f, t)) for L, f, t in g.compteurs_longueurs())
    assert init_apres["P"][0] == 1 and long_apres[5][0] == 1

    # Mot rare sur la grille : accepté en bonus, ne pollue pas les cibles
    r_bonus = g.proposer("rton", 1100)
    assert r_bonus.valide and r_bonus.bonus and r_bonus.points == 1
    assert len(g.trouves) == 1 and len(g.bonus) == 1

    # Indice et bilan
    ind = g.indice()
    assert ind is not None and ind["longueur"] >= 4
    b = g.terminer()
    assert b.trouves == 1 and b.total == len(g.cibles) and "rton" in b.bonus
    assert resume(con)["parties"] == 1


def test_labog_generateur(con):
    _peupler_labog(con)
    from lem.memory.knowledge import CarteConnaissance

    lex = Lexique(con)
    carte = CarteConnaissance(con, lex)
    idx = IndexLabog(con, carte, lex, long_min=4, long_max=9, seuil_connu=0.72, zipf_min=3.0)
    g, connus, inconnus = generer_grille(idx, random.Random(7), exceptions=5, cible_mots=10, budget_s=0.3, essais_max=60)
    assert len(g) == 16 and len(connus) >= 1
    assert all(m in idx.norme_vers_connu for m in connus)
    assert not (connus & inconnus)


async def test_tui_labog(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranLabog, LemApp

    chemin = tmp_path / "labog.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    ctx = Contexte(chemin)
    _peupler_labog(ctx.con)
    app = LemApp(ctx)
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("7")
        e = app.screen
        assert isinstance(e, EcranLabog)
        while not e.pret:
            await pilot.pause(0.05)
        detail = str(e.query_one("#detail").render())
        assert "initiales" in detail and "longueurs" in detail
        # Bascule des options en direct
        await pilot.press(*"/par_initiale non", "enter")
        await pilot.pause(0.05)
        assert "initiales" not in str(e.query_one("#detail").render())
        assert "longueurs" in str(e.query_one("#detail").render())
        # Proposer un mot valide de la grille
        mot = next(iter(e.partie.cibles))
        await pilot.press(*mot, "enter")
        while e.occupe:
            await pilot.pause(0.02)
        assert mot in e.partie.trouves
        # Entrée seule → bilan
        await pilot.press("enter")
        while e.occupe or e.etape != "bilan":
            await pilot.pause(0.02)
        assert "score" in str(e.query_one("#retour").render())
