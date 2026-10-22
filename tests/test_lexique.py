"""L'index compact du lexique dit la même chose que la base."""

from lem import ingest
from lem.db import connect
from lem.lexique import Lexique
from lem.texte import normaliser


def _verifier(con, lex):
    for forme, in con.execute("SELECT DISTINCT forme FROM forme"):
        attendu = {r[0] for r in con.execute("SELECT entree_id FROM forme WHERE forme = ?", (forme,))}
        assert set(lex.entrees(forme)) == attendu, forme
        assert forme in lex.formes_de_norme(normaliser(forme))
    for k, eid in enumerate(lex.eids):
        fn = lex.formes_et_normes(k)
        assert {f for f, _ in fn} == {r[0] for r in con.execute("SELECT forme FROM forme WHERE entree_id = ?", (int(eid),))}
        assert lex.texte(k).split("\x00") == [n for _, n in fn] or not fn


def test_lexique_memoire_et_disque(tmp_path, monkeypatch):
    from lem import vecteurs

    monkeypatch.setattr(vecteurs, "DOSSIER_CACHE", tmp_path / "cache")
    con = connect(tmp_path / "b.sqlite")
    ingest.demo(con)
    _verifier(con, Lexique(con))                       # construit et enregistre
    assert list((tmp_path / "cache").rglob("complet"))
    lex = Lexique(con)                                 # rechargé depuis le disque
    _verifier(con, lex)
    pp = con.execute("SELECT forme FROM entree WHERE type = 'nom_propre'").fetchone()[0].lower()
    assert lex.entrees(pp) and not lex.entrees(pp, exclus=("nom_propre",))
    con.execute("INSERT INTO entree(forme, pos) VALUES('zyxt', 'nom')")
    con.execute("INSERT INTO forme(forme, norme, entree_id) VALUES('zyxt', 'zyxt', last_insert_rowid())")
    con.commit()
    assert Lexique(con).entrees("zyxt")                # signature changée : index refait
    _verifier(connect(":memory:"), Lexique(connect(":memory:")))
