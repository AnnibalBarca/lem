import threading

from lem.grading.file import FileJugement


class JugeFactice:
    modele = "factice"

    def __init__(self, echoue=()):
        self.appels, self.echoue = [], set(echoue)

    def appel(self, prompt, modele):
        return "OK"

    def juger(self, el):
        self.appels.append(el)
        if el in self.echoue:
            raise RuntimeError("hors schéma")
        return f"verdict-{el}"


def test_chaque_element_recoit_un_rappel_et_la_file_se_vide():
    recus, fini = [], threading.Event()

    def rappel(r):
        recus.append(r)
        if len(recus) == 4:
            fini.set()

    f = FileJugement(JugeFactice(echoue={"b"}), rappel)
    for el in "abcd":
        f.soumettre(el, jeton=el.upper())
    assert fini.wait(5)
    assert sorted(r.jeton for r in recus) == ["A", "B", "C", "D"]
    assert [r.erreur is not None for r in sorted(recus, key=lambda r: r.jeton)] == [False, True, False, False]
    assert {r.verdict for r in recus if r.erreur is None} == {"verdict-a", "verdict-c", "verdict-d"}
    assert f.en_attente == 0 and len(f.durees) == 3 and f.latence_mediane_s is not None


def test_un_rappel_defaillant_ne_tue_pas_le_worker():
    fini = threading.Event()
    n = []

    def rappel(r):
        n.append(r)
        if len(n) == 1:
            raise ValueError("boom")
        fini.set()

    f = FileJugement(JugeFactice(), rappel, workers=1)
    f.soumettre("a")
    f.soumettre("b")
    assert fini.wait(5)


class JugeLent(JugeFactice):
    correcteur = "claude:factice"

    def juger(self, el):
        import time

        from lem.grading.claude_headless import Verdict

        time.sleep(0.4)
        return Verdict.model_validate({"id": el.evenement_id, "sens_vise_id": None, "precision": 4,
                                       "erreur": "exacte", "mot_reellement_defini": None, "confiance": 0.9,
                                       "commentaire": "ok"})


async def test_cartes_jugement_differe_ne_bloque_pas(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.tui.app import EcranCartes, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    ctx = Contexte(chemin)
    ctx.__dict__["juge"] = JugeLent()
    app = LemApp(ctx)
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("enter", "j")
        e = app.screen
        assert isinstance(e, EcranCartes)
        while not e.pret:
            await pilot.pause(0.05)
        e.item = e.partie._item("recep", e.item.entree_id)
        e.item.role, e.resultat = "revision", None
        faits = e.faits
        await pilot.press(*"un mot long", "enter")
        assert e.faits == faits + 1                                 # moved on at once, verdict not awaited
        assert e.precedent is not None and ctx.file_jugement.en_attente == 1
        assert "en attente" in str(e.query_one("#detail").render())
        for _ in range(60):
            await pilot.pause(0.1)
            if ctx.file_jugement.en_attente == 0 and getattr(e.precedent, "verdict_local", None):
                break
        assert e.precedent.verdict_local.precision == 4
        assert "juge : 4/5" in str(e.query_one("#detail").render())
    n = ctx.con.execute("SELECT COUNT(*) FROM notation WHERE correcteur = 'claude:factice'").fetchone()[0]
    assert n == 1
