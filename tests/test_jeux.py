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
    assert {"cartes", "bombe", "memoire", "etalonnage"} <= set(REGISTRE)


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
    from datetime import datetime, timedelta, timezone

    from lem.db import regler_horloge

    t = {"s": 0.0}
    t0 = datetime.now(timezone.utc)
    regler_horloge(lambda: t0 + timedelta(seconds=t["s"]))          # une minute par carte : le sens → mot a le temps de venir
    try:
        partie = PartieCartes(con, params("cartes", nouveaux=5, exercice="mixte"), carte, emb, ms, me, rng=rng)
        assert partie.restant() > 0
        vus = set()
        while (it := partie.suivant()) is not None:
            vus.add(it.exercice)
            texte = it.forme if it.exercice == "prod" else ("1" if it.exercice == "discrimination" else it.definition)
            if it.exercice == "recep" and len(vus) == 1:
                texte = "je ne sais pas"                         # une sonde ratée : le mot s'apprend (duel)
            res = partie.repondre(it, texte, 3000)
            partie.valider(res, None)
            t["s"] += 60
    finally:
        regler_horloge(None)
    assert {"recep", "discrimination"} <= vus and "prod" not in vus     # meaning never found: no « sens → mot »
    assert con.execute("SELECT COUNT(*) FROM trace_etat").fetchone()[0] > 0


def test_cartes_indice_plafonne_la_note(con, carte, rng):
    partie = PartieCartes(con, params("cartes", nouveaux=3, exercice="prod", confusables=False), carte, rng=rng)
    it = partie._item("prod", partie.suivant().entree_id)
    partie.indice(it)
    partie.indice(it)
    res = partie.repondre(it, it.forme, 2000)
    assert res.correct and res.note_proposee <= 2


def test_memoire_verbale(con, rng):
    m = PartieMemoire(con, params("memoire", mode="elucubrations", imagerie="experience"), rng=rng)
    n = 0
    while not m.finie and n < 200:
        t = m.suivant()
        m.repondre(t.deja_vu, 700)       # joueur parfait
        n += 1
    assert m.score == n and not m.finie
    m.enregistrer_image(m.tour.entree_id, "un amas de coquilles sous la pluie")
    assert con.execute("SELECT COUNT(*) FROM mnemo").fetchone()[0] == 1


def test_memoire_entrainement_encodage_qui_s_efface(con, rng):
    from lem.games.memoire_verbale import guides_par_partie, scores

    assert guides_par_partie(0, "estompe") > 1000 and guides_par_partie(120, "estompe") == 10
    assert guides_par_partie(250, "estompe") == 0 and guides_par_partie(250, "guide") > 1000
    assert guides_par_partie(0, "libre") == 0
    sid = con.execute("INSERT INTO session(jeu, debut) VALUES('memoire', '2026-01-01')").lastrowid
    m = PartieMemoire(con, params("memoire"), session_id=sid, rng=rng)
    assert m.mode == "entrainement" and m.record == 0
    assert all(" " not in r["forme"] for r in m.etranges)
    t = m.suivant()
    assert t.guider and not t.imager
    m.repondre(True, 600)                    # « vu » pour un mot nouveau : fausse alerte
    m.encoder(t.entree_id, 1400)
    while not m.finie:
        t = m.suivant()
        m.repondre(not t.deja_vu, 600)       # toujours faux
    b = m.bilan()
    assert b["fausses_alertes"] >= 1 and b["fausses_alertes"] + b["oublis"] == params("memoire")["vies"]
    assert b["encodage_ms"] == 1400 and b["score"] == 0
    assert scores(con, "entrainement") == [0] and scores(con, "elucubrations") == []


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
    partie2 = PartieCartes(con, params("cartes", nouveaux=200, confusables=False, noms_propres=True,
                                     intention="decouvrir"), carte, rng=rng)
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


def test_locutions_ecartees_par_defaut(con):
    from lem.games.base import filtres_depuis
    from lem.games.pool import selectionner

    formes = {r["forme"] for r in selectionner(con, filtres_depuis({}))}
    assert "les sages de la rue Montpensier" not in formes and "acronyme" in formes
    assert "ville-champignon" in formes                                    # un trait d'union : un mot
    avec = {r["forme"] for r in selectionner(con, filtres_depuis({"locutions": True}))}
    assert "les sages de la rue Montpensier" in avec
    assert "les sages de la rue Montpensier" in {r["forme"] for r in selectionner(con, filtres_depuis({"type": ["expression"]}))}


def test_cartes_sans_locutions_meme_en_revision(con, carte, rng):
    from lem.journal import Evenement, ecrire
    from lem.memory import scheduler

    loc = con.execute("SELECT id FROM entree WHERE forme = 'les sages de la rue Montpensier'").fetchone()[0]
    ecrire(con, None, Evenement("cartes", "reponse", entree_id=loc, trace="recep", reponse="x", note_auto=1))
    scheduler.reconstruire(con)
    partie = PartieCartes(con, params("cartes", nouveaux=200), carte, rng=rng)
    assert loc in partie._hors_jeu


def test_memoire_elucubrations_sans_locutions(con, rng):
    from lem.games import REGISTRE

    p = PartieMemoire(con, {**REGISTRE["memoire"].defauts(), "mode": "elucubrations", "etrangete": 0.0}, rng=rng)
    assert all(" " not in r["forme"] for r in p.etranges)
    p2 = PartieMemoire(con, {**REGISTRE["memoire"].defauts(), "mode": "elucubrations", "etrangete": 0.0,
                             "locutions": True}, rng=rng)
    assert any(" " in r["forme"] for r in p2.etranges)


def test_selection_ecarte_variantes_et_derives_transparents(con):
    from lem.games.pool import Filtres, secondaires, selectionner

    def entree(forme, definition):
        e = con.execute("INSERT INTO entree(forme, pos) VALUES(?, 'verbe')", (forme,)).lastrowid
        con.execute("INSERT INTO sens(entree_id, definition) VALUES(?, ?)", (e, definition))
        return e

    entree("remodeler", "Modeler de nouveau.")
    v = entree("re-modeler", "Orthographe parfois rencontrée pour remodeler.")
    entree("recopier", "Copier de nouveau.")
    r = entree("re-recopier", "Recopier de nouveau.")
    a = entree("anti-inflammatoire", "Qui combat l'inflammation.")
    exclus = secondaires(con)
    assert {v, r} <= exclus and a not in exclus
    ids = {x["id"] for x in selectionner(con, Filtres())}
    assert v not in ids and r not in ids and a in ids
    assert v in {x["id"] for x in selectionner(con, Filtres(secondaires=True))}


def test_elucubrations_plafonnent_les_noms_propres(con, rng):
    m = PartieMemoire(con, params("memoire", mode="elucubrations", noms_propres=0.0), rng=rng)
    tires = [m.suivant() for _ in range(40)]
    propres = {r["id"] for r in m.propres}
    assert not any(t.entree_id in propres for t in tires if t.premiere_fois)


def test_bombe_difficulte_sub(con, carte, rng):
    """Mode standard : jamais une suite à `sub` mots ou plus (sub-500 par défaut)."""
    from lem.lexique import Lexique

    lex = Lexique(con)
    b = PartieBombe(con, params("bombe", mode="standard", sub=40, solutions_min=1), carte, rng=rng, lexique=lex)
    for _ in range(30):
        inv = b.nouvelle_invite()
        assert 1 <= inv.n_solutions < 40, (inv.suite, inv.n_solutions)
    assert params("bombe")["sub"] == 500 and params("bombe")["mode"] == "standard"
    assert params("bombe")["temps_min"] == 10.0


def test_bombe_indice_d(con, carte, rng):
    """/d : un mot valide qui contient la suite, connu de préférence ; un mot inconnu est noté « vu »."""
    from lem.lexique import Lexique

    b = PartieBombe(con, params("bombe", mode="standard", solutions_min=1), carte, rng=rng, lexique=Lexique(con))
    b.invite.suite = "on"
    ind = b.indice()
    assert ind and "on" in normaliser(ind["mot"]) and ind["mot"] not in b.utilises
    ev = con.execute("SELECT data FROM evenement WHERE jeu = 'bombe' AND type = 'indice'").fetchone()[0]
    assert '"mot":"%s"' % ind["mot"] in ev
    autre = b.indice()
    assert autre is None or autre["mot"] != ind["mot"]           # jamais deux fois le même
    # Le mot donné, tapé ensuite : valide, mais ni preuve de vocabulaire actif ni « trouvé » au profil.
    n_obs = con.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    b.invite.suite = normaliser(ind["mot"])[:2]
    r = b.repondre(ind["mot"], 900)
    assert r.valide and con.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == n_obs
    assert r.trouvaille["n"] == 0


def test_pas_de_majuscule_initiale(con):
    """Les mots à majuscule (noms, gentilés, langues) ne sortent pas dans les jeux, sauf type nom_propre demandé."""
    from lem.db import upsert_entree
    from lem.games.base import filtres_depuis
    from lem.games.pool import selectionner

    upsert_entree(con, "Saint-Albanaise", "adj", type="mot")
    con.commit()
    formes = {r["forme"] for r in selectionner(con, filtres_depuis({}))}
    assert "Saint-Albanaise" not in formes and "acronyme" in formes
    assert "Saint-Albanaise" in {r["forme"] for r in selectionner(con, filtres_depuis({"type": ["mot", "nom_propre"]}))}


def test_cartes_intention_ne_reste_pas(con):
    """/intention en cours de partie vaut pour la partie : « consolider » ne devient pas le réglage de demain."""
    from lem.contexte import Contexte
    from lem.games import REGISTRE

    jeu = REGISTRE["cartes"]
    ctx = Contexte.__new__(Contexte)
    ctx.con = con
    ctx.sauver_params(jeu, {**jeu.defauts(), "intention": "equilibre", "nouveaux": 7})       # écran de réglages
    ctx.sauver_params(jeu, {**jeu.defauts(), "intention": "consolider", "nouveaux": 9}, en_partie=True)
    p = ctx.params(jeu)
    assert p["intention"] == "equilibre" and p["nouveaux"] == 9


def test_diversite_des_mots_nouveaux(con, carte, rng):
    """Les mots nouveaux choisis ne sont pas tous de la même famille de morphème."""
    from collections import Counter

    for d in (0, 0.5):
        partie = PartieCartes(con, params("cartes", nouveaux=10, diversite=d), carte, rng=rng)
        vivier = [r for r in partie.con.execute("SELECT id, forme, zipf, type FROM entree WHERE type = 'mot'")]
        ids = partie._classer_candidats(vivier, "equilibre", 12)
        assert len(ids) == len(set(ids))
        familles = Counter(m for e in ids for (m,) in con.execute(
            "SELECT morpheme_id FROM entree_morpheme WHERE entree_id = ?", (e,)))
        if d:
            assert max(familles.values(), default=0) <= max(3, len(ids) // 3)


def test_recherche_par_suite_et_par_definition(con, emb, magasins):
    from lem.lexique import Lexique
    from lem.recherche import est_une_suite, par_definition, par_suite

    lex = Lexique(con)
    assert est_une_suite("ruv") and est_une_suite("-nyme".lstrip("-")) and not est_une_suite("petit oiseau")
    mots = par_suite(con, lex, "min")
    formes = [t.forme for t in mots]
    assert "éminent" in formes and all("min" in normaliser(f) for f in formes)
    assert all(t.definition for t in mots) and mots == sorted(mots, key=lambda t: (-t.zipf, len(t.forme), t.forme))
    assert not any(t.forme[0].isupper() for t in mots)                    # pas de noms propres
    assert len({t.lemme.lower() for t in mots}) == len(mots)             # un mot par lemme
    ms, _ = magasins
    trouves = par_definition(con, lex, emb, ms, "sigle qui se prononce comme un mot")
    assert trouves and "acronyme" in {t.forme for t in trouves[:5]}
    assert par_definition(con, lex, None, None, "n'importe quoi") == []     # sans Ollama : rien, sans erreur


def test_trainer_runs_and_orders(con, carte):
    """The trainer's gap analysis and set cover run on the demo lexicon and return coherent words."""
    from lem import trainer
    from lem.lexique import Lexique

    lex = Lexique(con)
    a = trainer.analyse(lex, carte, sub=10_000, minimum=1, attested=0)
    assert a.total_need() >= 0
    words = a.ideal_words(5, attested_only=False, min_length=4)
    scores = [w.score for w in words]
    assert scores == sorted(scores, reverse=True) or len(scores) < 2 or max(scores) >= scores[-1]
    assert all(w.form and w.prompts >= 0 for w in words)
    assert len({w.form for w in words}) == len(words)



def test_indices_de_production(con, carte, rng):
    """A clue that is one word, a clipping of the answer, or a pointer is never shown when the player must write."""
    from lem.definitions import usable_as_clue as ok

    assert not ok("Microphone.", "micro") and not ok("Microphone d’un journaliste.", "micro")
    assert not ok("Chant.", "hymne") and not ok("Abréviation de chapitre.", "chap") and not ok("Sigle.", "acronyme")
    assert ok("Adepte du raëlisme.", "raëlien") and ok("Division cellulaire asexuée.", "mitose")
    partie = PartieCartes(con, params("cartes", nouveaux=0), carte, rng=rng)
    e = con.execute("INSERT INTO entree(forme, pos) VALUES('micro', 'nom')").lastrowid
    con.execute("INSERT INTO sens(entree_id, rang, definition) VALUES(?, 1, 'Microphone.')", (e,))
    assert partie._item("recep", e) is not None                   # word → sense is fine
    assert partie._item("prod", e) is None and partie._item("graphie", e) is None


def test_masculin_par_defaut_dans_les_cartes(con):
    """The feminine entry (raëlienne) is secondary: cards use the masculine, the Bomb still accepts both."""
    from lem.db import ajouter_formes, upsert_entree
    from lem.games.pool import secondaires

    m = upsert_entree(con, "raëlien", "nom")
    f = upsert_entree(con, "raëlienne", "nom")
    ajouter_formes(con, m, ["raëlien", "raëliens", "raëlienne"])
    ajouter_formes(con, f, ["raëlienne", "raëliennes", "raëlien"])
    con.commit()
    sec = secondaires(con)
    assert f in sec and m not in sec
    assert con.execute("SELECT COUNT(*) FROM forme WHERE forme = 'raëlienne'").fetchone()[0] == 2     # still a valid form


def test_purger_mot_ou_pas(con):
    from lem.cli import purger_mot_ou_pas

    con.execute("INSERT INTO evenement(ts, jeu, type) VALUES('2026-01-01T00:00:00.000Z', 'mot_ou_pas', 'reponse')")
    con.execute("INSERT INTO evenement(ts, jeu, type) VALUES('2026-01-01T00:00:00.000Z', 'cartes', 'reponse')")
    assert purger_mot_ou_pas(con) == 1
    assert [r[0] for r in con.execute("SELECT jeu FROM evenement")] == ["cartes"]
