import socket
import tempfile
from pathlib import Path

from lem.dictee import Dictee


def _serveur(tmp_path):
    chemin = Path(tempfile.mkdtemp(dir="/tmp")) / "c.sock"      # sun_path is limited to 104 bytes on macOS
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(str(chemin))
    s.settimeout(1)
    return s, chemin


def test_protocole_et_pas_de_bavardage(tmp_path):
    s, chemin = _serveur(tmp_path)
    d = Dictee(chemin)
    d.regler("texte")
    d.regler("texte")                     # unchanged: nothing sent
    d.regler("chiffres")
    d.regler(None)
    d.arreter()                           # already stopped: nothing sent
    recus = [s.recv(64).decode() for _ in range(3)]
    assert recus == ["listen", "listen chiffres", "stop"]
    s.settimeout(0.1)
    try:
        s.recv(64)
        assert False, "unexpected datagram"
    except socket.timeout:
        pass


def test_voxmac_absent_ne_gene_pas(tmp_path):
    d = Dictee(tmp_path / "absent.sock")
    d.regler("texte")
    d.arreter()                           # no exception, dictation is optional


def test_battement(tmp_path, monkeypatch):
    from lem import dictee

    s, chemin = _serveur(tmp_path)
    d = Dictee(chemin)
    d.regler("texte")
    d.envoye_a -= dictee.BATTEMENT_S + 1
    d.regler("texte")                     # heartbeat keeps voxmac listening
    assert [s.recv(64).decode() for _ in range(2)] == ["listen", "listen"]


def test_desactive(tmp_path):
    s, chemin = _serveur(tmp_path)
    Dictee(chemin, actif=False).regler("texte")
    s.settimeout(0.1)
    try:
        s.recv(64)
        assert False
    except socket.timeout:
        pass


class Enregistreur:
    actif = True

    def __init__(self):
        self.modes = []

    def regler(self, mode):
        self.modes.append(mode)

    def arreter(self):
        self.modes.append(None)


async def test_cartes_dicte_selon_l_ecran(tmp_path):
    from lem.cli import main
    from lem.contexte import Contexte
    from lem.games.cartes import Item
    from lem.tui.app import EcranCartes, LemApp

    chemin = tmp_path / "t.sqlite"
    main(["--db", str(chemin), "demo", "--sans-jeu"])
    ctx = Contexte(chemin)
    ctx.__dict__["juge"] = None
    ctx.__dict__["dictee"] = rec = Enregistreur()
    app = LemApp(ctx)
    async with app.run_test(size=(110, 50)) as pilot:
        await pilot.pause(1.6)
        await pilot.press("enter", "j")
        e = app.screen
        assert isinstance(e, EcranCartes)
        while not e.pret:
            await pilot.pause(0.05)
        await pilot.pause(0.2)
        assert rec.modes[-1] in {"texte", "chiffres"}              # a field is open: voxmac listens
        e.recopies, e.resultat = [], None
        base = e.partie._item("recep", e.item.entree_id)
        for exercice, attendu in (("recep", "texte"), ("prod", "texte"), ("graphie", None)):
            e.item, e.resultat = Item(exercice, base.entree_id, base.sens_id, base.forme, base.pos, base.definition), None
            assert e.mode_dictee() == attendu
        e.item = Item("discrimination", base.entree_id, base.sens_id, base.forme, base.pos, base.definition)
        assert e.mode_dictee() == "chiffres"
        e.item.exercice = "recep"
        e.resultat = object()
        assert e.mode_dictee() == "chiffres"                       # grading 1–4 after the reveal
        e.recopies = ["mot"]
        assert e.mode_dictee() is None                             # spelling retyped by hand
    assert rec.modes[-1] is None                                   # leaving the game stops the dictation
