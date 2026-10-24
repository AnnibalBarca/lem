from lem import utilite
from lem.games import REGISTRE
from lem.games.cartes import PartieCartes


class MagasinFactice:
    def __init__(self, vecteurs):
        self.v = vecteurs

    def vecteur(self, i):
        return self.v.get(i)


def _ajouter(con, forme, definition="", zipf=2.0, type_="mot"):
    cur = con.execute("INSERT INTO entree(forme, pos, type, zipf) VALUES(?, 'adj', ?, ?)", (forme, type_, zipf))
    if definition:
        con.execute("INSERT INTO sens(entree_id, rang, definition) VALUES(?, 0, ?)", (cur.lastrowid, definition))
    return cur.lastrowid


def test_composants_prefixe_et_trait_d_union(con):
    base = _ajouter(con, "cartésien")
    belle, jour = _ajouter(con, "belle"), _ajouter(con, "jour")
    assert utilite.composants(con, "anti-cartésien") == [("cartésien", base)]
    assert utilite.composants(con, "belle-de-jour") == [("belle", belle), ("jour", jour)]
    assert utilite.composants(con, "cartésien") == []


def test_derive_transparent_vaut_moins_que_compose_opaque_avec_les_vecteurs(con):
    base = _ajouter(con, "cartésien")
    anti = _ajouter(con, "anti-cartésien")
    belle, jour = _ajouter(con, "belle"), _ajouter(con, "jour")
    bdj = _ajouter(con, "belle-de-jour")
    mag = MagasinFactice({base: [1.0, 0.0], anti: [0.95, 0.05], belle: [0.0, 1.0], jour: [-1.0, 0.0], bdj: [0.7, 0.7]})
    t_anti = utilite.transparence(con, anti, "anti-cartésien", mag)
    t_bdj = utilite.transparence(con, bdj, "belle-de-jour", mag)
    assert t_anti > 0.9 and t_bdj < 0.1
    f = utilite.facteurs(con, [{"id": anti, "forme": "anti-cartésien"}, {"id": bdj, "forme": "belle-de-jour"}], mag)
    assert f[anti] < 0.25 and f[bdj] > 0.9


def test_repli_lexical_sans_vecteurs(con):
    _ajouter(con, "cartésien")
    anti = _ajouter(con, "anti-cartésien", "Opposé au cartésianisme.")
    assert utilite.transparence(con, anti, "anti-cartésien", None) == 1.0
    belle, jour = _ajouter(con, "belle"), _ajouter(con, "jour")
    bdj = _ajouter(con, "belle-de-jour", "Plante grimpante aux fleurs vivaces.")
    assert utilite.transparence(con, bdj, "belle-de-jour", None) == 0.0


def test_mot_ecarte_ne_revient_pas(con, carte):
    params = REGISTRE["cartes"].defauts()
    e = con.execute("SELECT id FROM entree WHERE type = 'mot' LIMIT 1").fetchone()[0]
    PartieCartes(con, params, carte).ecarter(e)
    assert e in utilite.ecartes(con)
    p = PartieCartes(con, params, carte)
    assert e in p._hors_jeu
    vus = set()
    for _ in range(60):
        it = p.suivant()
        if it is None:
            break
        vus.add(it.entree_id)
    assert vus and e not in vus
