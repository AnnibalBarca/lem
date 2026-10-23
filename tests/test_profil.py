from lem.atlas import rapport
from lem.journal import Evenement, ajouter_mnemo, ecrire
from lem.memory import scheduler
from lem.profil import ecarts, profil, profils, score_trouvable

from tests.conftest import eid


def test_six_memoires_d_un_mot(con, carte):
    e = eid(con, "acronyme")
    assert profil(con, e).definit is None and profil(con, e).trouvable is None     # rien mesuré : on ne devine pas
    ecrire(con, None, Evenement("cartes", "reponse", entree_id=e, trace="recep", reponse="x", correct=True,
                                note_auto=3))
    ecrire(con, None, Evenement("bombe", "reponse", entree_id=e, trace="prod", reponse="acronyme", correct=True,
                                latence_ms=9000, data={"fsrs": False}))
    ecrire(con, None, Evenement("memoire", "reponse", entree_id=e, trace="forme", reponse="VU", correct=True,
                                data={"deja_vu": True}))
    ajouter_mnemo(con, e, "image_mentale", "un sigle qui se prononce")
    scheduler.reconstruire(con)
    p = profil(con, e)
    assert p.forme == "acronyme" and p.connu is not None
    assert 0.8 < p.definit <= 1 and p.produit is None
    assert p.graphie == 1.0                                         # tapé juste, jamais de faute
    assert p.trouvable < score_trouvable(1, 1000)                   # trouvé, mais lentement
    assert p.mnemo == 2 / 3 and p.detail["images"] == 1
    assert any(t == "lents à trouver" for t, _ in ecarts(profils(con)))


def test_atlas_avec_six_memoires(con):
    t = rapport(con).plain
    assert "SIX MÉMOIRES" in t and "ENTRAÎNEMENTS" in t
