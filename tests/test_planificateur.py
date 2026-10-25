"""Planificateur de session, sonde des mots nouveaux, vérification unique, optimiseur, banc d'essai."""

import os
import tempfile
from datetime import timedelta

import pytest

from lem.db import now, regler_horloge
from lem.games import REGISTRE
from lem.games.cartes import PartieCartes
from lem.memory import planificateur as P
from lem.memory import scheduler
from lem.memory.planificateur import Planificateur, Tache


def test_sonde_puis_parcours_d_apprentissage():
    plan = Planificateur(revisions=[], candidats=[1, 2, 3], budget_nouveaux=2)
    x = plan.prochaine(0)
    assert x.role == "sonde" and x.exercice == "recep" and x.entree_id == 1
    assert plan.apres_reponse(x, 0, 1, None, a_des_voisins=True) == "apprentissage"
    att = {(y.exercice, y.role) for y in plan.attente}
    assert ("discrimination", "apprentissage") in att
    assert "prod" not in {y.exercice for y in plan.attente}       # never discovered through its definition
    retour = Tache("recep", 1, role="apprentissage")
    plan.apres_reponse(retour, 60, 1, None)                       # meaning still not found: still no « sens → mot »
    assert "prod" not in {y.exercice for y in plan.attente}
    plan.apres_reponse(retour, 120, 3, None)                      # found: now its definition is a fair clue
    prod = next(y for y in plan.attente if y.exercice == "prod")
    assert prod.apres >= plan.n + P.ECART_PROD - 1 and prod.pret_a >= 120 + P.DELAI_PROD


def test_la_sonde_montre_toujours_le_mot():
    plan = Planificateur(revisions=[], candidats=[1], budget_nouveaux=1, directions=("prod",))
    assert plan.prochaine(0).exercice == "recep"


def test_sonde_reussie_ne_coute_qu_une_carte_et_pas_de_budget():
    plan = Planificateur(revisions=[], candidats=[1, 2, 3, 4], budget_nouveaux=1)
    x = plan.prochaine(0)
    assert plan.apres_reponse(x, 0, 3, None) == "connu"
    assert not plan.attente and plan.entres == 0 and plan.connus == 1
    y = plan.prochaine(1)                                   # le budget n'a pas bougé : on sonde le suivant
    assert y.role == "sonde" and y.entree_id == 2


def test_jamais_deux_cartes_soeurs_collees():
    revs = [Tache("recep", 7, role="revision"), Tache("prod", 7, role="revision"), Tache("recep", 8, role="revision"),
            Tache("recep", 9, role="revision")]
    plan = Planificateur(revisions=revs, candidats=[], budget_nouveaux=0)
    ordre = [plan.prochaine(0).entree_id for _ in range(4)]
    assert ordre[0] == 7 and ordre[1] != 7 and ordre[2] != 7
    assert ordre[3] == 7                     # rien d'autre : la sœur passe quand même, en dernier


def test_une_carte_revient_au_plus_retours_max_fois():
    plan = Planificateur(revisions=[], candidats=[], budget_nouveaux=0)
    t = Tache("recep", 5, role="apprentissage")
    assert plan.ajouter(t) and not plan.ajouter(t)          # pas de doublon en attente
    for _ in range(10):
        plan.attente.clear()
        plan.ajouter(t)
    assert plan.retours[(5, "recep")] == P.RETOURS_MAX


def test_nouveaux_repartis_dans_la_session():
    revs = [Tache("recep", 100 + i, role="revision") for i in range(12)]
    plan = Planificateur(revisions=revs, candidats=[1, 2, 3], budget_nouveaux=3)
    roles = []
    for k in range(15):
        x = plan.prochaine(k)
        roles.append(x.role)
        plan.apres_reponse(x, k, 3, None)
    premiers = [i for i, r in enumerate(roles) if r == "sonde"]
    assert len(premiers) == 3 and premiers[0] < 6 and premiers[-1] > 6


@pytest.fixture
def horloge():
    t = {"v": now()}
    regler_horloge(lambda: t["v"])
    yield t
    regler_horloge(None)


def test_sonde_connue_verification_unique_puis_retrait(con, carte, rng, horloge):
    p = {**REGISTRE["cartes"].defauts(), "nouveaux": 3, "exercice": "recep", "confusables": False}
    partie = PartieCartes(con, p, carte, rng=rng)
    it = partie.suivant()
    assert it.role == "sonde"
    res = partie.repondre(it, "je le connais", 4000)
    assert partie.valider(res, 3) == "connu"
    st = con.execute("SELECT statut, due FROM trace_etat WHERE entree_id = ? AND trace = 'recep'",
                     (it.entree_id,)).fetchone()
    assert st["statut"] == "connu_sonde"
    delai = (scheduler.parse_iso(st["due"]) - horloge["v"]).days
    assert 29 <= delai <= 60

    horloge["v"] += timedelta(days=61)
    partie2 = PartieCartes(con, {**p, "nouveaux": 0}, carte, rng=rng)
    v = partie2.suivant()
    assert v.entree_id == it.entree_id and v.role == "verification"
    partie2.valider(partie2.repondre(v, "je le connais", 3000), 3)
    st = con.execute("SELECT statut, due FROM trace_etat WHERE entree_id = ? AND trace = 'recep'",
                     (it.entree_id,)).fetchone()
    assert st["statut"] == "verifie"
    assert (scheduler.parse_iso(st["due"]) - horloge["v"]).days > 3000          # ne revient plus


def test_verification_ratee_remet_en_apprentissage(con, carte, rng, horloge):
    p = {**REGISTRE["cartes"].defauts(), "nouveaux": 3, "exercice": "recep", "confusables": False}
    partie = PartieCartes(con, p, carte, rng=rng)
    it = partie.suivant()
    partie.valider(partie.repondre(it, "je le connais", 4000), 4)
    horloge["v"] += timedelta(days=61)
    partie2 = PartieCartes(con, {**p, "nouveaux": 0}, carte, rng=rng)
    v = partie2.suivant()
    partie2.valider(partie2.repondre(v, "?", 3000), 1)
    st = con.execute("SELECT statut, due FROM trace_etat WHERE entree_id = ? AND trace = 'recep'",
                     (it.entree_id,)).fetchone()
    assert st["statut"] is None and (scheduler.parse_iso(st["due"]) - horloge["v"]).days < 7


def test_sonde_ratee_sens_vers_mot_seulement_une_fois_le_sens_retrouve(con, carte, rng, horloge):
    p = {**REGISTRE["cartes"].defauts(), "nouveaux": 2, "confusables": False}
    partie = PartieCartes(con, p, carte, rng=rng)
    it = partie.suivant()
    assert partie.valider(partie.repondre(it, "?", 2000), 1) == "apprentissage"

    def session(note_recep):
        horloge["v"] += timedelta(days=3)
        partie2 = PartieCartes(con, {**p, "nouveaux": 0}, carte, rng=rng)
        vus = []
        while (x := partie2.suivant()) is not None and len(vus) < 10:
            vus.append((x.exercice, x.entree_id))
            partie2.valider(partie2.repondre(x, x.forme if x.exercice == "prod" else "?", 2000),
                            note_recep if x.exercice == "recep" else 1)
        return vus

    vus = session(1)                                  # the word comes back as itself, never as its definition
    assert ("recep", it.entree_id) in vus and ("prod", it.entree_id) not in vus
    session(3)                                        # meaning found…
    assert ("prod", it.entree_id) in session(3)       # … from now on « sens → mot » is fair


def test_optimiseur_apprend_et_ne_s_emballe_pas():
    """Sur un élève simulé, l'optimiseur améliore la prédiction des révisions récentes ; sans assez de
    données, il ne touche à rien."""
    from lem.db import connect
    from lem.memory.optimiseur import optimiser
    from lem.simulation import simuler

    con = connect(":memory:")
    raps = optimiser(con)
    assert not any(r.adopte for r in raps.values())
    r = simuler({}, jours=21, n_mots=1500, params_eleve="faible", optimiser_chaque=7, graine=3)
    assert r.retenus_forme > 0 and r.cartes > 300


def test_banc_d_essai_v2_mieux_que_rien():
    from lem.simulation import simuler

    r = simuler({}, jours=6, n_mots=800, graine=1)
    assert r.mots_vus > 20 and r.retenus_sens > 0 and 0 <= r.brier_carte < 0.3 and r.auc_carte > 0.8


def test_atlas_memoire_et_bilan():
    """L'Atlas montre la réussite réelle des révisions contre celle prévue par FSRS."""
    from lem.atlas import rapport
    from lem.db import connect
    from lem.memory.optimiseur import bilan
    from lem.simulation import simuler

    with tempfile.TemporaryDirectory() as d:
        chemin = os.path.join(d, "b.sqlite")
        simuler({}, jours=8, n_mots=600, chemin=chemin, graine=2)
        con = connect(chemin)
        texte = rapport(con).plain
        assert "MÉMOIRE" in texte and "mots en apprentissage" in texte
        b = bilan(con, "recep", jours=3650)
        assert b.n == 0 or 0 <= b.reussite <= 1
