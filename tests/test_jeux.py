import numpy as np
import pytest

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


def test_bombe_cible_ratee_revient_plus_tard_une_fois(con, carte, rng):
    p = params("bombe", mode="conscient", long_min=2, long_max=2, retours=1, ecart=3, revelation_max=10_000)
    b = PartieBombe(con, p, carte, rng=rng)
    inv = b.invite
    assert inv.cible_id is not None and inv.suite in normaliser(inv.cible_forme)
    cible, suite = inv.cible_id, inv.suite
    autre = _mot_valide(b, inv.suite, {normaliser(inv.cible_forme)})
    r = b.repondre(autre, 1200) if autre else b.exploser()
    assert not r.cible_trouvee and r.revelation and r.revelation[0] == inv.cible_forme   # à portée : révélée
    assert b.invite is not inv and b.invite.cible_id != cible                         # pas d'allongement immédiat
    vues = []
    for _ in range(6):
        vues.append((b.invite.cible_id, b.invite.suite))
        b.invite = b.nouvelle_invite()
    retours = [s for c, s in vues if c == cible]
    assert len(retours) == 1 and len(retours[0]) <= 2                                  # une fois, jamais > long_max
    assert len({c for c, _ in vues}) == len(vues)                                      # sinon, jamais deux fois


def test_bombe_revelation_seulement_a_portee(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="conscient", long_min=2, long_max=2, revelation_max=0), carte, rng=rng)
    assert b.exploser().revelation is None


def test_bombe_sans_noms_propres(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng)
    assert "pouligny-saint-pierre" not in b.valides
    assert all(r["type"] not in ("nom_propre", "sigle") for r in b.vivier.values())
    b2 = PartieBombe(con, params("bombe", mode="standard", solutions_min=1, noms_propres=True), carte, rng=rng)
    assert "pouligny-saint-pierre" in b2.valides


def test_bombe_revise_les_mots_des_cartes(con, carte, rng):
    from lem.memory import scheduler
    from lem.journal import Evenement, ecrire

    eid_ = con.execute("SELECT id FROM entree WHERE forme = 'thuriféraire'").fetchone()[0]
    ecrire(con, None, Evenement("cartes", "reponse", entree_id=eid_, trace="recep", note_auto=3, correct=True))
    scheduler.reconstruire(con)
    b = PartieBombe(con, params("bombe", mode="conscient", revision=1.0, long_min=2, long_max=2), carte, rng=rng)
    assert b.invite.cible_id == eid_


def test_bombe_rejette_mot_invalide_ou_reutilise(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng)
    assert not b.repondre("xyzqwv", 500).valide
    mot = _mot_valide(b, b.invite.suite, set())
    assert b.repondre(mot, 500).valide
    b.invite.suite = normaliser(mot)[:2]
    assert b.repondre(mot, 500).message == "déjà utilisé"
    sans = normaliser(mot)
    if sans != mot:                                   # mot accentué tapé sans accents : refusé
        assert not b.repondre(sans, 500).valide


def test_bombe_explosion_et_fin(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="standard", vies=2, solutions_min=1), carte, rng=rng)
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


def test_calcul_vectoriel_identique(con, carte, rng):
    """p_tous et la Bombe vectorisée donnent exactement ce que donnait le calcul entrée par entrée."""
    ids = list(carte._prior)
    for eid in rng.sample(ids, 10):
        carte.observer(eid, rng.random(), persister=False)
    p = carte.p_tous()
    for eid, k in zip(carte.ids, range(len(p))):
        assert p[k] == pytest.approx(carte.p(int(eid)), abs=1e-12)
    b = PartieBombe(con, params("bombe", mode="standard"), carte, rng=rng)
    for suite in ("on", "ym", "e", "zzz"):
        n, k = 0, 0.0
        for eid, fs in b.formes_vivier.items():
            if any(suite in f for f in fs):
                n, k = n + 1, k + carte.p(eid)
        assert b.solutions(suite) == (n, pytest.approx(k, abs=1e-9))
    carte.observer(ids[0], 1.0, persister=False)              # θ change : K suit
    n, k = b.solutions("e")
    assert k == pytest.approx(sum(carte.p(e) for e, fs in b.formes_vivier.items() if any("e" in f for f in fs)))


def test_meme_terminaison_indexee(con):
    from lem.confusables import IndexFormel

    index = IndexFormel(con)
    for forme in ("acronyme", "homonyme", "thuriféraire", "mitose", "abc"):
        f = normaliser(forme)
        attendu = [i for i, x in zip(index.ids, index.formes) if len(f) >= 6 and x.endswith(f[-4:]) and x != f][:30]
        assert index.meme_terminaison(forme) == attendu


def test_carte_graphie_repondue(con, carte, rng):
    """Une faute dans la Bombe crée une trace ortho ; y répondre dans les Cartes ne doit pas planter
    (sens_id 0 violait la clé étrangère vers sens ; la convention est NULL, comme dans la Bombe)."""
    from lem.orthographe import diagnostiquer, enregistrer_faute

    diag = diagnostiquer(con, "acronime")
    assert diag is not None
    enregistrer_faute(con, None, "bombe", diag)
    partie = PartieCartes(con, params("cartes", nouveaux=0, exercice="recep"), carte, rng=rng)
    it = partie._item("graphie", diag.entree_id, 0)          # la carte que la file présenterait une fois due
    res = partie.repondre(it, it.forme, 2000)
    partie.valider(res, None)
    assert res.correct
    assert con.execute("SELECT COUNT(*) FROM evenement WHERE jeu = 'cartes' AND trace = 'ortho'").fetchone()[0] == 1


def test_bombe_suite_sans_trait_d_union(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="conscient"), carte, rng=rng)
    for forme in ("ville-champignon", "demi-petit-cousin", "pouligny-saint-pierre"):
        for L in range(2, len(forme)):
            assert b._suite_distinctive(forme, longueur=L).isalpha()


def test_bombe_retour_cible_courte(con, carte, rng):
    b = PartieBombe(con, params("bombe", mode="conscient", ecart=2), carte, rng=rng)
    cid = min((i for i, r in b.vivier.items() if r["forme"].isalpha()), key=lambda i: len(b.vivier[i]["forme"]))
    b.retours.append((b.n_invites, cid, 30, 0))         # longueur demandée > longueur du mot
    inv = b.nouvelle_invite()
    assert inv.cible_id == cid and inv.suite.isalpha()


def test_cartes_ignorent_les_sens_sans_definition(con, carte, rng):
    eid_ = con.execute("SELECT id FROM entree WHERE forme = 'acronyme'").fetchone()[0]
    con.execute("UPDATE sens SET definition = '' WHERE entree_id = ?", (eid_,))
    partie = PartieCartes(con, params("cartes", nouveaux=0), carte, rng=rng)
    assert partie._item("recep", eid_) is None


def test_bombe_accents_manquants(con, carte, rng):
    from lem.db import set_reglage

    b = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng)
    b.invite.suite = "min"
    r = b.repondre("eminent", 800)
    assert r.valide and r.forme == "éminent"                     # reconnu, affiché avec ses accents
    set_reglage(con, "accents_tolerants", False)
    b2 = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng)
    b2.invite.suite = "min"
    assert not b2.repondre("eminent", 800).valide


def test_cartes_sans_noms_propres(con, carte, rng):
    from lem.orthographe import Diagnostic

    pp = con.execute("SELECT id FROM entree WHERE type = 'nom_propre'").fetchone()[0]
    partie = PartieCartes(con, params("cartes", nouveaux=200, confusables=False), carte, rng=rng)
    assert all(it.entree_id != pp for it in partie.file)                       # jamais en mot nouveau
    partie._insister(Diagnostic("pouligni", "pouligny-saint-pierre", pp, 1, ["substitution"], False, 0.9))
    assert all(it.entree_id != pp for it in partie.file)                       # ni en reprise de graphie
    partie2 = PartieCartes(con, params("cartes", nouveaux=200, confusables=False, noms_propres=True), carte, rng=rng)
    assert any(it.entree_id == pp for it in partie2.file)


def test_carte_depuis_lexique_identique(con, rng):
    from lem.lexique import Lexique
    from lem.memory.knowledge import CarteConnaissance

    a, b = CarteConnaissance(con), CarteConnaissance(con, Lexique(con))
    for eid in rng.sample(list(a._prior), 15):
        y = rng.random()
        a.observer(eid, y, persister=False)
        b.observer(eid, y, persister=False)
    pa, pb = a.p_tous(), b.p_tous()
    assert list(a.ids) == list(b.ids)
    assert np.allclose(pa, pb, atol=1e-12)
    for k, eid in enumerate(b.ids):
        assert pb[k] == pytest.approx(b.p(int(eid)), abs=1e-12)


def test_bombe_anciens_modes_et_temps_min(con, carte, rng):
    from lem.contexte import Contexte
    from lem.db import set_reglage
    from lem.games.bombe import BOMBE

    b = PartieBombe(con, params("bombe", mode="apprentissage"), carte, rng=rng)
    assert b.params["mode"] == "conscient"
    assert executer_commande(BOMBE, dict(BOMBE.defauts()), "/mode libre").ok      # ancien nom accepté
    b.facteur_temps = 0.3                                                           # le jeu accélère…
    assert all(b._duree() >= b.params["temps_min"] for _ in range(50))             # … jamais sous 10 s
    set_reglage(con, "params:bombe", {"mode": "libre"})
    ctx = Contexte.__new__(Contexte)
    ctx.con = con
    assert ctx.params(BOMBE)["mode"] == "standard"


def test_bombe_formes_soudees_1990(con, carte, rng):
    from lem.db import ajouter_formes, upsert_entree

    eid_ = upsert_entree(con, "néo-babylonien", "adj", type="compose")
    ajouter_formes(con, eid_, ["néo-babyloniennes"])
    con.commit()
    b = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng)
    b.invite.suite = "ba"
    r = b.repondre("neobabyloniennes", 900)
    assert r.valide and r.forme == "néobabyloniennes" and r.entree_id == eid_
    b.invite.suite = "ba"
    assert not b.repondre("neobabylonienes", 900).valide         # soudure oui, faute non
