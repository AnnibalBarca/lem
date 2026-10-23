"""Mise à niveau d'une base jouée avec le moteur v1 (une seule fois, à l'ouverture).

v1 mettait la carte de connaissance à jour sans rien écrire : la table `observation` est vide sur une
base existante. Un rejeu l'effacerait. On reconstitue donc les observations à partir du journal, avec
les règles qu'appliquaient les jeux :

  Cartes        y = note (meilleure disponible au rejeu) ; propage seulement la première réponse sur un mot
                (ce que tu savais avant d'étudier), comme une sonde aujourd'hui
  Bombe         cible trouvée y = 1 ; mot spontané y = 1 (poids 0,5) ; révélation y = 0,4 (poids 0,3)
  Étalonnage    oui y = 0,9 ; non y = 0,05 ; définition de vérification : en attente de note
  Mot ou pas    oui y = 0,7 ; non y = 0,1 (poids 0,4)

Puis FSRS est rejoué en entier (nouveaux statuts, pas d'étapes dans la session), et la carte aussi.
"""

from __future__ import annotations

import json
import sqlite3

from lem.db import get_reglage, set_reglage

VERSION_MOTEUR = 2


def retablir_observations(con: sqlite3.Connection) -> int:
    """Reconstitue la table observation depuis le journal (si elle est vide). Renvoie le nombre écrit."""
    if con.execute("SELECT 1 FROM observation LIMIT 1").fetchone():
        return 0
    lignes = []
    vus: set[int] = set()
    for r in con.execute(
            """SELECT id, ts, jeu, type, entree_id, trace, reponse, data FROM evenement
               WHERE entree_id IS NOT NULL AND type IN ('reponse', 'revelation') ORDER BY ts, id"""):
        data = json.loads(r["data"] or "{}")
        e, jeu = r["entree_id"], r["jeu"]
        obs = None                                   # (y, poids, source, depuis_note, propager)
        if jeu == "cartes" and r["type"] == "reponse" and r["trace"] in ("recep", "prod"):
            obs = (None, 1.0, f"migration:cartes:{data.get('exercice', r['trace'])}", True, e not in vus)
        elif jeu == "bombe" and r["type"] == "reponse" and data.get("cible_trouvee"):
            obs = (1.0, 1.0, "migration:bombe:cible", False, True)
        elif jeu == "bombe" and r["type"] == "reponse" and data.get("spontane"):
            obs = (1.0, 0.5, "migration:bombe:spontane", False, True)
        elif jeu == "bombe" and r["type"] == "revelation":
            obs = (0.4, 0.3, "migration:bombe:revelation", False, True)
        elif jeu == "etalonnage" and r["type"] == "reponse" and r["trace"] == "forme":
            obs = (0.9 if r["reponse"] == "oui" else 0.05, 1.0, "migration:etalonnage", False, True)
        elif jeu == "etalonnage" and r["type"] == "reponse" and data.get("verification"):
            obs = (None, 1.0, "migration:etalonnage:verification", True, True)
        elif jeu == "mot_ou_pas" and r["type"] == "reponse":
            obs = (0.7 if r["reponse"] == "oui" else 0.1, 0.4, "migration:mot_ou_pas", False, True)
        if obs is None:
            continue
        y, poids, source, depuis_note, propager = obs     # y None + depuis_note : la note de l'événement
        lignes.append((r["ts"], r["id"], e, y, poids, source, int(depuis_note), int(propager)))
        vus.add(e)
    con.executemany(
        """INSERT INTO observation(ts, evenement_id, entree_id, y, poids, source, depuis_note, propager)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?)""", lignes)
    con.commit()
    return len(lignes)


def migrer(con: sqlite3.Connection) -> bool:
    """Met une base v1 au niveau du moteur v2 (idempotent). Renvoie True si quelque chose a été fait."""
    if (get_reglage(con, "version_moteur", 0) or 0) >= VERSION_MOTEUR:
        return False
    if not con.execute("SELECT 1 FROM entree LIMIT 1").fetchone():
        set_reglage(con, "version_moteur", VERSION_MOTEUR)
        con.commit()
        return False
    from lem.memory import scheduler
    from lem.memory.knowledge import CarteConnaissance, mettre_a_jour_prior

    retablir_observations(con)
    scheduler.reconstruire(con)
    if not mettre_a_jour_prior(con):            # recalibre et rejoue si besoin ; sinon on rejoue ici
        CarteConnaissance(con).rejouer()
    set_reglage(con, "version_moteur", VERSION_MOTEUR)
    con.commit()
    return True
