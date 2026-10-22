import json
from datetime import timedelta

import pytest

from lem.db import set_reglage, now
from lem.grading.direct import decouper_synonymes, noter_prod, noter_recep
from lem.journal import Evenement, ajouter_notation, ecrire
from lem.memory import scheduler
from lem.memory.knowledge import CarteConnaissance, calibrer_seuil
from lem.similarite import Proximite, etiqueter, sim_forme

from .conftest import eid


def test_prior_calibre_sur_la_cible(con):
    total = con.execute("SELECT SUM(p_prior) FROM connaissance c JOIN entree e ON e.id = c.entree_id "
                        "WHERE e.type != 'nom_propre'").fetchone()[0]
    assert total == pytest.approx(45, abs=1.5)


def test_calibrer_seuil_monotone():
    import numpy as np

    z = np.linspace(0, 7, 1000)
    assert calibrer_seuil(z, 200, 1.6) > calibrer_seuil(z, 600, 1.6)


def test_propagation_par_morpheme(con, carte):
    avant = carte.p(eid(con, "paronyme"))
    for _ in range(3):
        carte.observer(eid(con, "acronyme"), 0.0)
    apres = carte.p(eid(con, "paronyme"))
    assert apres < avant                      # -onyme partagé : la famille baisse sans être testée
    assert carte.p(eid(con, "sigle")) > apres  # sigle ne partage pas le morphème


def test_rejeu_fsrs_deterministe_et_priorite_des_correcteurs(con):
    a = eid(con, "thuriféraire")
    t0 = now() - timedelta(days=10)
    ev1 = ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="triste", note_auto=3, ts=t0))
    ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="flatteur", note_auto=3,
                                ts=t0 + timedelta(days=3)))
    scheduler.reconstruire(con)
    s1 = con.execute("SELECT card_json, stabilite FROM trace_etat").fetchall()
    scheduler.reconstruire(con)
    s2 = con.execute("SELECT card_json, stabilite FROM trace_etat").fetchall()
    assert [tuple(r) for r in s1] == [tuple(r) for r in s2]

    # Claude juge « triste » vague → note 1 : la stabilité doit baisser après rejeu.
    ajouter_notation(con, ev1, "claude:sonnet", note=1, precision=2, erreur="connotation_seule")
    scheduler.reconstruire(con, [a])
    s3 = con.execute("SELECT stabilite FROM trace_etat").fetchone()[0]
    assert s3 < s1[0]["stabilite"]


def test_dues_trie_par_retrievabilite(con):
    for forme, jours in (("sigle", 30), ("acronyme", 2)):
        ecrire(con, None, Evenement("cartes", "reponse", entree_id=eid(con, forme), trace="recep", note_auto=3,
                                    ts=now() - timedelta(days=jours)))
    scheduler.reconstruire(con)
    dues = scheduler.dues(con, now() + timedelta(days=60))
    assert dues[0].retrievabilite <= dues[-1].retrievabilite


def test_similarite_formelle():
    assert sim_forme("acronyme", "homonyme") > 0.55
    assert sim_forme("acronyme", "sigle") < 0.3
    assert sim_forme("absorption", "adsorption") > 0.85
    assert etiqueter(0.8, 0.9) == Proximite.JUMEAU
    assert etiqueter(0.2, 0.9) == Proximite.SENS
    assert etiqueter(0.8, 0.1) == Proximite.FORME
    assert etiqueter(0.1, 0.1, memoire=1) == Proximite.MEMOIRE
    assert etiqueter(0.1, None) == Proximite.AUCUNE


def test_synonymes_vs_phrase():
    assert decouper_synonymes("flatteur, laudateur") == ["flatteur", "laudateur"]
    assert decouper_synonymes("quelqu'un qui flatte excessivement les puissants") is None


def test_recep_synonymes_reconnus(con):
    v = noter_recep(con, eid(con, "thuriféraire"), "flatteur, laudateur")
    assert set(v.synonymes_reconnus) == {"flatteur", "laudateur"}
    assert v.note_auto == 3


def test_recep_dictionnaire_inverse(con, emb, magasins):
    ms, me = magasins
    defi_sigle = con.execute("SELECT definition FROM sens WHERE entree_id = ?", (eid(con, "sigle"),)).fetchone()[0]
    v = noter_recep(con, eid(con, "acronyme"), defi_sigle, emb, ms, me)
    assert v.mot_reellement_defini and v.mot_reellement_defini[1] == "sigle"
    assert v.note_auto == 1


def test_prod_exact_flechi_et_confusion(con):
    a = eid(con, "acronyme")
    assert noter_prod(con, a, "Acronyme").mode == "exact"
    assert noter_prod(con, a, "acronymes").mode == "flechi"
    v = noter_prod(con, a, "homonyme")
    assert not v.correct and v.comparaison.proximite == Proximite.FORME
    assert noter_prod(con, eid(con, "thuriféraire"), "laudateur").mode == "synonyme"
    assert noter_prod(con, eid(con, "éminent"), "eminent").correct      # accent manquant : toléré par défaut
    assert not noter_prod(con, eid(con, "éminent"), "èminent").correct   # accent faux : faute
    set_reglage(con, "accents_tolerants", False)                        # mode strict : accent manquant = faute
    v = noter_prod(con, eid(con, "éminent"), "eminent")
    assert not v.correct and v.mode == "graphie" and v.graphie.types == ["accent"]


def test_accents_manquants():
    from lem.orthographe import accents_manquants

    assert accents_manquants("creolisation", "créolisation") and accents_manquants("oeuvre", "œuvre")
    assert accents_manquants("Eminent", "éminent")
    assert not accents_manquants("crèolisation", "créolisation")        # accent faux
    assert not accents_manquants("portemanteau", "porte-manteau")       # le trait d'union compte
    assert not accents_manquants("aujourdhui", "aujourd'hui")


def test_carte_connaissance_persistance(con):
    c1 = CarteConnaissance(con)
    c1.observer(eid(con, "zeugma"), 1.0)
    c2 = CarteConnaissance(con)
    assert c2.p(eid(con, "zeugma")) == pytest.approx(c1.p(eid(con, "zeugma")))


def test_claude_headless_avec_appel_simule(con):
    from lem.grading import claude_headless as ch

    a = eid(con, "thuriféraire")
    ev = ecrire(con, None, Evenement("cartes", "reponse", entree_id=a, trace="recep", reponse="morne, triste",
                                     note_auto=2, data={"voisins": [{"mot": "atrabilaire", "definition": "D'humeur sombre"}]}))
    vus = {}

    def appel(prompt, modele):
        vus["prompt"] = prompt
        return json.dumps({"type": "result", "result": "Voici :\n" + json.dumps([
            {"id": ev, "sens_vise_id": None, "precision": 1, "erreur": "confusion_formelle",
             "mot_reellement_defini": "atrabilaire", "confiance": 0.9, "commentaire": "confusion avec atrabilaire"}])})

    assert ch.noter(con, "sonnet", appel=appel, journal=lambda m: None) == 1
    assert "atrabilaire" in vus["prompt"] and "confusion_formelle" in vus["prompt"]
    n = con.execute("SELECT * FROM notation WHERE evenement_id = ?", (ev,)).fetchone()
    assert n["correcteur"] == "claude:sonnet" and n["note"] == 1
    conf = con.execute("SELECT COUNT(*) FROM relation WHERE type = 'confusion_perso'").fetchone()[0]
    assert conf == 1
    assert ch.noter(con, "sonnet", appel=appel, journal=lambda m: None) == 0   # rien à renoter


def test_claude_headless_reponse_invalide_ignoree(con):
    from lem.grading import claude_headless as ch

    ecrire(con, None, Evenement("cartes", "reponse", entree_id=eid(con, "sigle"), trace="recep", reponse="abréviation"))
    n = ch.noter(con, "sonnet", appel=lambda p, m: "pas de json", journal=lambda m: None)
    assert n == 0
    assert con.execute("SELECT COUNT(*) FROM notation").fetchone()[0] == 0


def test_calibrage_des_seuils(con, emb, magasins):
    from lem.calibrage import calibrer_seuils
    from lem.db import ajouter_relation, get_reglage

    with pytest.raises(ValueError):
        calibrer_seuils(con, emb.nom)
    ids = [r[0] for r in con.execute("SELECT id FROM entree ORDER BY id")]
    for a, b in zip(ids[::2], ids[1::2]):
        ajouter_relation(con, a, b, "synonyme")
    r = calibrer_seuils(con, emb.nom)
    assert get_reglage(con, "seuils_proximite")["sens"] == r["proximite"]["sens"]
    d = get_reglage(con, f"seuils_definition:{emb.nom}")
    assert d["haut"] > d["bas"]


@pytest.mark.parametrize("valeur,attendu", [
    ("", "http://localhost:11434"),
    ("0.0.0.0:11434", "http://localhost:11434"),
    ("172.22.0.1:11434", "http://172.22.0.1:11434"),
    ("http://hote:8080", "http://hote:8080"),
])
def test_url_ollama(monkeypatch, valeur, attendu):
    from lem.vecteurs import url_ollama

    monkeypatch.setenv("OLLAMA_HOST", valeur)
    assert url_ollama() == attendu
