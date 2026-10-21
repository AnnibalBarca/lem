from lem.games import REGISTRE
from lem.games.base import executer_commande
from lem.games.bombe import PartieBombe
from lem.games.cartes import PartieCartes
from lem.games.decision import PartieDecision
from lem.games.memoire_verbale import PartieMemoire, etrangete
from lem.texte import normaliser

from .conftest import eid


def params(jeu, **kw):
    return {**REGISTRE[jeu].defauts(), **kw}


def test_registre_complet():
    assert {"cartes", "bombe", "memoire", "etalonnage", "mot_ou_pas"} <= set(REGISTRE)


def test_commandes_en_partie():
    jeu = REGISTRE["bombe"]
    p = jeu.defauts()
    assert executer_commande(jeu, p, "/domaine science,droit").change
    assert p["domaine"] == ["science", "droit"]
    assert executer_commande(jeu, p, "/vies 5").ok and p["vies"] == 5
    assert not executer_commande(jeu, p, "/vies 99").ok
    assert executer_commande(jeu, p, "/mode confusables").ok and p["mode"] == "confusables"
    assert not executer_commande(jeu, p, "/mode n_importe").ok
    assert not executer_commande(jeu, p, "/inconnue 3").ok
    assert "domaine" in executer_commande(jeu, p, "/aide").message


def _mot_valide(partie, suite, sauf):
    """Une forme EXACTE (accents compris) du vivier qui contient la suite."""
    for eid in partie.formes_vivier:
        for (f,) in partie.con.execute("SELECT forme FROM forme WHERE entree_id = ?", (eid,)):
            if suite in normaliser(f) and normaliser(f) not in sauf:
                return f
    return None


def test_bombe_apprentissage_repetition_et_extension(con, carte, rng):
    p = params("bombe", mode="apprentissage", long_min=2, long_max=2, repetitions_max=5, k_haut=300)
    b = PartieBombe(con, p, carte, rng=rng)
    inv = b.invite
    assert inv.cible_id is not None and inv.suite in normaliser(inv.cible_forme)
    cible = normaliser(inv.cible_forme)
    autre = _mot_valide(b, inv.suite, {cible})
    if autre and inv.repetitions:
        suite_avant = inv.suite
        r = b.repondre(autre, 1200)
        assert r.valide and not r.cible_trouvee
        if r.revelation is None:
            assert b.invite is inv and len(b.invite.suite) > len(suite_avant)
            assert b.invite.suite in cible
    # Trouver la cible fait passer à une nouvelle invite.
    cible_courante = b.invite.cible_forme
    r = b.repondre(cible_courante, 900)
    assert r.valide and r.cible_trouvee


def test_bombe_rejette_mot_invalide_ou_reutilise(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="libre", solutions_min=1), carte, rng=rng)
    assert not b.repondre("xyzqwv", 500).valide
    mot = _mot_valide(b, b.invite.suite, set())
    assert b.repondre(mot, 500).valide
    b.invite.suite = normaliser(mot)[:2]
    assert b.repondre(mot, 500).message == "déjà utilisé"
    sans = normaliser(mot)
    if sans != mot:                                   # mot accentué tapé sans accents : refusé
        assert not b.repondre(sans, 500).valide


def test_bombe_explosion_et_fin(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="libre", vies=2, solutions_min=1), carte, rng=rng)
    b.exploser()
    b.exploser()
    assert b.finie


def test_bombe_filtre_domaine(con, carte, rng):
    b = PartieBombe(con, params("bombe", domaine=["science"]), carte, rng=rng)
    noms = {r["forme"] for r in b.vivier.values()}
    assert "mitose" in noms and "trempe" in noms and "thuriféraire" not in noms   # métallurgie ⊂ science


def test_bombe_confusables(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="confusables", domaine=["linguistique"], long_min=3, long_max=4),
                    carte, rng=rng)
    assert b.invite.cible_id is not None


def test_cartes_file_et_reponses(con, carte, rng, emb, magasins):
    ms, me = magasins
    partie = PartieCartes(con, params("cartes", nouveaux=5, exercice="mixte"), carte, emb, ms, me, rng=rng)
    assert partie.restant() > 0
    vus = set()
    while (it := partie.suivant()) is not None:
        vus.add(it.exercice)
        texte = it.forme if it.exercice == "prod" else ("1" if it.exercice == "discrimination" else it.definition)
        res = partie.repondre(it, texte, 3000)
        partie.valider(res, None)
    assert {"recep", "prod"} <= vus
    assert con.execute("SELECT COUNT(*) FROM trace_etat").fetchone()[0] > 0


def test_cartes_indice_plafonne_la_note(con, carte, rng):
    partie = PartieCartes(con, params("cartes", nouveaux=3, exercice="prod", confusables=False), carte, rng=rng)
    it = partie.suivant()
    partie.indice(it)
    partie.indice(it)
    res = partie.repondre(it, it.forme, 2000)
    assert res.correct and res.note_proposee <= 2


def test_memoire_verbale(con, rng):
    m = PartieMemoire(con, params("memoire", imagerie="experience"), rng=rng)
    n = 0
    while not m.finie and n < 200:
        t = m.suivant()
        m.repondre(t.deja_vu, 700)       # joueur parfait
        n += 1
    assert m.score == n and not m.finie
    m.enregistrer_image(m.tour.entree_id, "un amas de coquilles sous la pluie")
    assert con.execute("SELECT COUNT(*) FROM mnemo").fetchone()[0] == 1


def test_etrangete_ordre():
    assert etrangete("kjökkenmödding", 0, "etranger", "danois") > etrangete("maison", 5.5, "mot", "latin")


def test_etalonnage_met_a_jour_la_carte(con, carte, rng):
    d = PartieDecision(con, "etalonnage", params("etalonnage", questions=20, pseudo=0.2), carte, rng=rng)
    while not d.finie:
        q = d.suivante()
        d.repondre(q.entree_id is not None, 800)
        if q.verifier:
            d.verification("une définition", 3000)
    assert d.fausses_alertes == 0
    assert con.execute("SELECT COUNT(*) FROM theta").fetchone()[0] > 0
