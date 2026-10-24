import random

import pytest

from lem import ingest
from lem.db import connect
from lem.memory.knowledge import CarteConnaissance, ParamsPrior, calibrer
from lem.vecteurs import HashEmbedder, Magasin, indexer


class HashSemantique(HashEmbedder):
    """Pour les tests : on fait comme si les n-grammes portaient le sens."""

    semantique = True


@pytest.fixture(autouse=True)
def cache_isole(tmp_path, monkeypatch):
    """Les caches (index du lexique, vecteurs) des bases de test vont dans un dossier temporaire, jamais
    dans le vrai ~/.lem/cache (constaté : 69 dossiers laissés par les tests de l'interface)."""
    from lem import vecteurs

    monkeypatch.setattr(vecteurs, "DOSSIER_CACHE", tmp_path / "cache-lem")


@pytest.fixture(autouse=True)
def pas_de_vrai_claude(monkeypatch):
    """Tests never reach the real `claude` command (it would spend quota and make runs depend on the machine)."""
    monkeypatch.setenv("LEM_CLAUDE_CMD", "lem-claude-absent-pendant-les-tests")


@pytest.fixture(autouse=True)
def pas_de_vraie_dictee(monkeypatch):
    """Tests never talk to the user's real voxmac (it would start typing into whatever window has the focus)."""
    monkeypatch.setenv("LEM_DICTEE", "0")


@pytest.fixture
def con():
    c = connect(":memory:")
    ingest.demo(c)
    calibrer(c, ParamsPrior(cible_connus=45))
    return c


@pytest.fixture
def emb(con):
    e = HashSemantique()
    indexer(con, e, "sens")
    indexer(con, e, "entree")
    return e


@pytest.fixture
def magasins(con, emb):
    return Magasin(con, "sens", emb.nom), Magasin(con, "entree", emb.nom)


@pytest.fixture
def carte(con):
    return CarteConnaissance(con)


@pytest.fixture
def rng():
    return random.Random(42)


def eid(con, forme):
    return con.execute("SELECT id FROM entree WHERE forme = ?", (forme,)).fetchone()[0]
