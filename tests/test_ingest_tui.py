import json

from lem import ingest
from lem.db import connect
from lem.tui import logo


def test_lexique_tsv(tmp_path):
    f = tmp_path / "Lexique383.tsv"
    f.write_text(
        "ortho\tphon\tlemme\tcgram\tfreqlemfilms2\tfreqlemlivres\torthosyll\n"
        "acronyme\takʁOnim\tacronyme\tNOM\t0.5\t1.2\ta-cro-nyme\n"
        "acronymes\takʁOnim\tacronyme\tNOM\t0.5\t1.2\ta-cro-nymes\n"
        "de\td°\tde\tPRE\t1000\t1000\tde\n",
        encoding="utf-8",
    )
    con = connect(":memory:")
    assert ingest.lexique(con, f) == 1
    e = con.execute("SELECT * FROM entree WHERE forme = 'acronyme'").fetchone()
    assert e["syllabes"] == "a-cro-nyme" and e["zipf"] > 2.5
    assert con.execute("SELECT COUNT(*) FROM forme").fetchone()[0] == 2


def test_wiktionnaire_jsonl(tmp_path):
    f = tmp_path / "fr.jsonl"
    lignes = [
        {"word": "acronyme", "lang_code": "fr", "pos": "noun",
         "etymology_texts": ["Composé de acro- (« extrémité ») et de -onyme (« nom »), du grec ancien."],
         "senses": [{"glosses": ["(Linguistique) Sigle qui se prononce comme un mot."]}],
         "synonyms": [{"word": "sigle"}], "forms": [{"form": "acronymes", "tags": ["plural"]}],
         "sounds": [{"ipa": "a.kʁɔ.nim"}]},
        {"word": "sigle", "lang_code": "fr", "pos": "noun", "etymology_texts": ["Du latin sigla."],
         "senses": [{"glosses": ["Suite d'initiales."], "topics": ["linguistique"]}]},
        {"word": "acronym", "lang_code": "en", "pos": "noun", "senses": [{"glosses": ["x"]}]},
        {"word": "les sages de la rue Montpensier", "lang_code": "fr", "pos": "phrase",
         "senses": [{"glosses": ["Le Conseil constitutionnel."]}]},
    ]
    f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lignes), encoding="utf-8")
    con = connect(":memory:")
    assert ingest.wiktionnaire(con, f) == 2                      # « les sages de la rue Montpensier » : une locution, écartée
    a = con.execute("SELECT * FROM entree WHERE forme = 'acronyme'").fetchone()
    assert a["origine"] == "grec"
    morph = {r[0] for r in con.execute("SELECT m.forme FROM morpheme m JOIN entree_morpheme em ON em.morpheme_id = m.id")}
    assert {"acro-", "-onyme"} <= morph
    dom = con.execute("""SELECT d.nom, p.nom AS parent FROM sens_domaine sd JOIN domaine d ON d.id = sd.domaine_id
                         LEFT JOIN domaine p ON p.id = d.parent_id""").fetchall()
    assert ("linguistique", "langage") in {tuple(r) for r in dom}
    assert con.execute("SELECT definition FROM sens WHERE entree_id = ?", (a["id"],)).fetchone()[0].startswith("Sigle")
    assert con.execute("SELECT COUNT(*) FROM relation WHERE type = 'synonyme'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM entree WHERE forme LIKE 'les sages%'").fetchone()[0] == 0
    assert con.execute("SELECT origine FROM entree WHERE forme = 'sigle'").fetchone()[0] == "latin"


def test_jeuxdemots(tmp_path, con):
    f = tmp_path / "jdm.txt"
    f.write_text("e;1;'acronyme';1;50\ne;2;'sigle';1;50\nr;10;1;2;5;80\nr;11;1;2;99;80\nr;12;1;2;0;3\n", encoding="utf-8")
    assert ingest.jeuxdemots(con, f) == 1


def test_logo_rendu():
    t = logo.rendre()
    lignes = t.plain.split("\n")
    assert len(lignes) == 12 and max(map(len, lignes)) <= 80
    assert "┼" in t.plain and "▒" in t.plain
    assert "│ L │" in t.plain


async def test_tui_parcours(tmp_path, monkeypatch):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranBombe, EcranCartes, LemApp, Reglages

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("enter")                       # Cartes
        assert isinstance(app.screen, Reglages)
        await pilot.press("j")
        assert isinstance(app.screen, EcranCartes)
        await pilot.press(*"un mot", "enter")
        await pilot.press("3", "enter")
        await pilot.press(*"/nouveaux 2", "enter")
        await pilot.press("escape", "escape")
        await pilot.press("down", "enter")               # Bombe
        await pilot.press("j")
        assert isinstance(app.screen, EcranBombe)
        await pilot.press(*"/mode libre", "enter")
        await pilot.press(*"zzzz", "enter")
        await pilot.press("escape", "escape")
        await pilot.press("down", "down", "down", "down", "down", "enter")   # Atlas
        await pilot.press("escape")
    con = connect(chemin)
    assert con.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM evenement WHERE jeu = 'cartes' AND type = 'reponse'").fetchone()[0] == 1


async def test_cartes_fleches_et_avance_auto(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.games.cartes import Item
    from lem.tui.app import EcranCartes, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("enter", "j")
        ecran = app.screen
        assert isinstance(ecran, EcranCartes)
        while not ecran.pret:
            await pilot.pause(0.05)
        # Duel : ↓ ↓ puis Entrée choisit la 3e option.
        it = ecran.partie._item("recep", ecran.item.entree_id)
        autres = [(i, f) for i, f in ecran.ctx.con.execute("SELECT id, forme FROM entree WHERE id != ? LIMIT 3",
                                                            (it.entree_id,))]
        ecran.item = Item("discrimination", it.entree_id, it.sens_id, it.forme, it.pos, it.definition, [],
                          options=[autres[0], autres[1], (it.entree_id, it.forme), autres[2]])
        ecran.resultat = None
        ecran.choix = 0
        await pilot.press("down", "down", "enter")
        assert ecran.resultat is not None and ecran.resultat.correct
        faits = ecran.faits
        await pilot.pause(ecran.AVANCE_AUTO + 0.4)            # juste : la carte suivante vient seule
        assert ecran.faits == faits + 1
        # Sens → mot juste, sans accent : avance aussi toute seule.
        it = ecran.partie._item("prod", ecran.partie.con.execute(
            "SELECT id FROM entree WHERE forme = 'éminent'").fetchone()[0])
        ecran.item, ecran.resultat = it, None
        await pilot.press(*"eminent", "enter")
        assert ecran.resultat.correct
        await pilot.pause(ecran.AVANCE_AUTO + 0.4)
        assert ecran.faits == faits + 2


def test_logo_mots_recents():
    m = logo.mots_pour_logo(["OISEAUMOQUEUR", "LAURENTIE", "CRÉOLE", "AH"])
    assert m == {"L": "CRÉOLE", "E": "OISEAUMOQUEUR", "M": "LAURENTIE"}     # 8 cubes pour L, 13 pour E et M
    assert "É" in logo.rendre(mots=m).plain and "G" not in logo.rendre(mots=m).plain   # plus LANGUE
    assert logo.mots_pour_logo([]) == {}


async def test_bombe_saisie_gardee_si_refusee(tmp_path):
    from textual.widgets import Input

    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranBombe, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("down", "enter", "j")
        e = app.screen
        assert isinstance(e, EcranBombe)
        while not e.pret:
            await pilot.pause(0.05)
        e.partie.invite.suite = "min"
        await pilot.press(*"eminentt", "enter")                 # refusé : reste dans le champ
        while e.occupe:
            await pilot.pause(0.02)
        assert e.query_one("#saisie", Input).value == "eminentt"
        await pilot.press("backspace", "enter")                  # corrigé d'un effacement
        await pilot.pause(0.05)
        while e.occupe:
            await pilot.pause(0.02)
        assert e.query_one("#saisie", Input).value == ""
        assert "1ʳᵉ fois" in str(e.query_one("#retour").render())


async def test_cartes_session_complete_sonde_et_bilan(tmp_path):
    """Une session entière au pilote : sondes (Entrée seule = je ne sais pas), notes, bilan de fin."""
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.games import REGISTRE
    from lem.tui.app import EcranCartes, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    ctx = Contexte(chemin)
    ctx.sauver_params(REGISTRE["cartes"], {**REGISTRE["cartes"].defauts(), "nouveaux": 2, "minutes": 3})
    app = LemApp(ctx)
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("enter", "j")
        ecran = app.screen
        assert isinstance(ecran, EcranCartes)
        while not ecran.pret:
            await pilot.pause(0.05)
        assert ecran.item.role == "sonde"
        assert "le connais-tu" in str(ecran.query_one("#stimulus").render())
        for _ in range(60):
            if ecran.item is None:
                break
            if ecran.resultat is None:
                if ecran.item.exercice == "prod":
                    await pilot.press(*ecran.item.forme, "enter")
                elif ecran.item.exercice == "discrimination":
                    await pilot.press("enter")
                else:
                    await pilot.press("enter")                     # sonde : je ne sais pas
            else:
                await pilot.press("enter")                         # note proposée
            await pilot.pause(0.15)
        await pilot.pause(1.0)
        assert ecran.item is None
        bilan = str(ecran.query_one("#stimulus").render())
        assert "mots appris" in bilan and "cartes" in bilan
    con = ctx.con
    roles = {r[0] for r in con.execute("SELECT json_extract(data, '$.role') FROM evenement WHERE type = 'reponse'")}
    assert "sonde" in roles


async def test_memoire_entrainement_invite_image(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranMemoire, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("down", "down", "down", "enter", "j")
        e = app.screen
        assert isinstance(e, EcranMemoire)
        while not e.pret:
            await pilot.pause(0.05)
        premier = e.partie.tour.forme
        await pilot.press("n")                                     # nouveau : l'invite « image ? » attend
        assert e.attente_encodage is not None and "image ?" in str(e.query_one("#detail").render())
        await pilot.press("v")                                     # ignoré tant que l'image n'est pas là
        assert e.partie.score == 1
        await pilot.press("space")
        assert e.attente_encodage is None and e.partie.encodages
        assert e.partie.tour.forme != premier or e.partie.tour.deja_vu


async def test_accueil_touche_chiffre_lance_le_jeu(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranBombe, EcranCartes, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    app = LemApp(Contexte(chemin))
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("2")                                  # Bombe, sans passer par les réglages
        assert isinstance(app.screen, EcranBombe)
        await pilot.press("escape")
        await pilot.press("1")
        assert isinstance(app.screen, EcranCartes)
