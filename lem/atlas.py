"""Atlas : ce que le journal dit de toi, calculé (pas de LLM).

  1. Carte      : combien de mots, dans quel état (estimé pour tout le lexique).
  2. Profil     : tes θ — où tu t'écartes de « l'adulte cultivé moyen » (origines, domaines, morphèmes).
  3. Signature  : la répartition de tes types d'erreur de définition (trop générale, connotation seule…).
  4. Confusions : tes paires confondues, avec la nature de la proximité.
  5. Métacognition : ton auto-évaluation face à celle du correcteur.
  6. Vitesse    : latences médianes par jeu.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import Counter

from rich.text import Text

from lem.grading.taxonomie import Erreur
from lem.similarite import comparer
from lem.tui.theme import BLANC, ETATS, GRIS, PROXIMITES, SOMBRE, barre, etat_de


def _titre(t: Text, s: str) -> None:
    t.append(f"\n{s}\n", style=f"bold {BLANC}")


def rapport(con: sqlite3.Connection) -> Text:
    t = Text()

    # 1. Carte
    _titre(t, "CARTE")
    rows = con.execute(
        """SELECT c.p_connu, c.n_obs, t.stabilite, t.entree_id IS NOT NULL AS en_cours
           FROM connaissance c LEFT JOIN (SELECT entree_id, MAX(stabilite) AS stabilite FROM trace_etat
           GROUP BY entree_id) t ON t.entree_id = c.entree_id"""
    ).fetchall()
    compte = Counter(etat_de(r["p_connu"], r["n_obs"], bool(r["en_cours"]), r["stabilite"]) for r in rows)
    total = max(1, len(rows))
    estime = sum(r["p_connu"] for r in rows)
    t.append(f"  {len(rows)} entrées · ~{estime:.0f} connues (estimé)\n", style=GRIS)
    for cle, (coul, lib) in ETATS.items():
        n = compte.get(cle, 0)
        t.append(f"  {lib:<16}", style=coul)
        t += barre(n / total, 30, coul)
        t.append(f" {n}\n", style=GRIS)

    # 2. Profil
    _titre(t, "PROFIL — écarts à l'adulte cultivé moyen")
    th = con.execute("SELECT dimension, valeur, n_obs FROM theta WHERE n_obs >= 3 AND dimension != 'global' "
                     "ORDER BY valeur").fetchall()
    if not th:
        t.append("  pas encore assez d'observations (joue l'Étalonnage)\n", style=SOMBRE)
    for r in [*th[:6], *th[-6:]] if len(th) > 12 else th:
        v = r["valeur"]
        coul = ETATS["acquis"][0] if v > 0 else ETATS["inconnu"][0]
        t.append(f"  {r['dimension']:<26}", style=BLANC)
        t.append(("+" if v > 0 else "−") + "█" * min(20, round(abs(v) * 5)), style=coul)
        t.append(f"  {v:+.2f} ({r['n_obs']} obs.)\n", style=GRIS)

    # 3. Signature d'imprécision
    _titre(t, "SIGNATURE — tes façons de définir")
    err = Counter(r[0] for r in con.execute(
        "SELECT erreur FROM notation WHERE erreur IS NOT NULL AND (correcteur LIKE 'claude:%' OR correcteur LIKE 'jev:%')"))
    n_err = sum(err.values())
    if not n_err:
        t.append("  aucune définition notée par Claude pour l'instant (menu « Noter avec Claude »)\n", style=SOMBRE)
    for e in Erreur:
        if err.get(e.value):
            t.append(f"  {e.value:<20}", style=BLANC)
            t += barre(err[e.value] / n_err, 30)
            t.append(f" {err[e.value]}\n", style=GRIS)

    # 4. Confusions
    _titre(t, "CONFUSIONS")
    paires = con.execute(
        """SELECT r.source_id, r.cible_id, r.poids, a.forme AS fa, b.forme AS fb FROM relation r
           JOIN entree a ON a.id = r.source_id JOIN entree b ON b.id = r.cible_id
           WHERE r.type = 'confusion_perso' ORDER BY r.poids DESC LIMIT 15"""
    ).fetchall()
    if not paires:
        t.append("  aucune confusion enregistrée\n", style=SOMBRE)
    for p in paires:
        comp = comparer(con, p["source_id"], p["cible_id"])
        coul = PROXIMITES[comp.proximite]
        t.append("  ● ", style=coul)
        t.append(f"{p['fa']}", style=f"bold {BLANC}")
        t.append(" → ", style=GRIS)
        t.append(f"{p['fb']}", style=BLANC)
        t.append(f"   ×{p['poids']:.1f} · forme {comp.forme:.2f}\n", style=GRIS)

    # 5. Métacognition
    _titre(t, "MÉTACOGNITION — toi vs le correcteur")
    duos = con.execute(
        """SELECT s.note AS soi, c.note AS ext FROM notation s JOIN notation c ON c.evenement_id = s.evenement_id
           WHERE s.correcteur = 'soi' AND c.correcteur LIKE 'claude:%'"""
    ).fetchall()
    if duos:
        ecart = statistics.mean(d["soi"] - d["ext"] for d in duos)
        accord = sum(d["soi"] == d["ext"] for d in duos) / len(duos)
        t.append(f"  accord {accord:.0%} · écart moyen {ecart:+.2f} ", style=BLANC)
        t.append("(positif = tu te surestimes)\n", style=GRIS)
    else:
        t.append("  pas encore de paires auto-évaluation / Claude\n", style=SOMBRE)

    # 6. Vitesse
    _titre(t, "VITESSE — latence médiane")
    for jeu, in con.execute("SELECT DISTINCT jeu FROM evenement WHERE type = 'reponse'"):
        lats = [r[0] for r in con.execute(
            "SELECT latence_ms FROM evenement WHERE jeu = ? AND type = 'reponse' AND latence_ms IS NOT NULL", (jeu,))]
        if lats:
            t.append(f"  {jeu:<14}{statistics.median(lats) / 1000:5.1f} s", style=BLANC)
            t.append(f"   ({len(lats)} réponses)\n", style=GRIS)
    return t
