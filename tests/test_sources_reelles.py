"""Lecteurs vérifiés sur des extraits réels des sources (Lexique 3.83, wiktextract frwiktionary, octobre 2026)."""

from pathlib import Path

from lem import ingest
from lem.db import connect

DATA = Path(__file__).parent / "data"


def _entree(con, forme, pos="nom"):
    return con.execute("SELECT * FROM entree WHERE forme = ? AND pos = ?", (forme, pos)).fetchone()


def test_lexique_reel():
    con = connect(":memory:")
    assert ingest.lexique(con, DATA / "lexique383_extrait.tsv") == 4
    sigle = _entree(con, "sigle")
    assert sigle["zipf"] > 3 and sigle["phon"] == "sigl"
    formes = {r[0] for r in con.execute("SELECT forme FROM forme WHERE entree_id = ?", (sigle["id"],))}
    assert formes == {"sigle", "sigles"}


def test_wiktionnaire_reel():
    con = connect(":memory:")
    ingest.lexique(con, DATA / "lexique383_extrait.tsv")
    # acronymes (forme de nom) et sigle (forme du verbe sigler) ne sont pas des entrées
    assert ingest.wiktionnaire(con, DATA / "frwiktionary_extrait.jsonl") == 7
    assert _entree(con, "acronymes") is None and _entree(con, "sigle", "verbe") is None
    assert not con.execute("SELECT 1 FROM sens WHERE definition LIKE 'Pluriel d%'").fetchone()

    acro = _entree(con, "acronyme")
    assert acro["origine"] == "anglais" and acro["zipf"] is not None
    assert acro["phon"] == "akRonim"                                        # Lexique d'abord
    assert _entree(con, "kjökkenmödding")["phon"] == "kj9k9nm9diG"         # API convertie au code Lexique
    assert "acronymes" in {r[0] for r in con.execute("SELECT forme FROM forme WHERE entree_id = ?", (acro["id"],))}
    morph = {r[0] for r in con.execute(
        "SELECT m.forme FROM morpheme m JOIN entree_morpheme em ON em.morpheme_id = m.id WHERE em.entree_id = ?",
        (acro["id"],))}
    assert {"acro-", "-onym"} <= morph

    def domaines(forme):
        return {tuple(r) for r in con.execute(
            """SELECT d.nom, p.nom FROM entree e JOIN sens s ON s.entree_id = e.id
               JOIN sens_domaine sd ON sd.sens_id = s.id JOIN domaine d ON d.id = sd.domaine_id
               LEFT JOIN domaine p ON p.id = d.parent_id WHERE e.forme = ?""", (forme,))}

    assert domaines("kjökkenmödding") == {("archéologie", "humanités")}   # topic « archeology » traduit
    assert ("liturgie", "société") in domaines("thuriféraire")              # raw_tag « Liturgie »
    assert ("typographie", "langage") in domaines("sigle")
    assert not any(d == "sud des vosges & haute-saône" for d, _ in domaines("sigle"))  # région, pas domaine

    assert _entree(con, "kjökkenmödding")["origine"] == "danois"            # « Emprunt au danois »
    assert _entree(con, "mitose")["origine"] == "grec"
    assert _entree(con, "thuriféraire")["origine"] == "latin"
    assert _entree(con, "ONU", "nom_propre")["type"] == "sigle"
    thur = _entree(con, "thuriféraire")
    assert con.execute("SELECT COUNT(*) FROM sens WHERE entree_id = ?", (thur["id"],)).fetchone()[0] == 3
