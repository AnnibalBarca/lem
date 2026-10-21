import random

import pytest

from lem import ingest
from lem.db import connect
from lem.memory.knowledge import CarteConnaissance, ParamsPrior, calibrer
from lem.vecteurs import HashEmbedder, Magasin, indexer


class HashSemantique(HashEmbedder):
    """Pour les tests : on fait comme si les n-grammes portaient le sens."""

    semantique = True


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
