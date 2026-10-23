"""Atlas : ce que le journal dit de toi, calculé (pas de LLM).

  1. Carte      : combien de mots, dans quel état (estimé pour tout le lexique).
  1bis. Mémoire : le moteur sur tes révisions — réussite réelle contre réussite prévue, mots connus à la
                  sonde, vérifiés, en apprentissage, charge à venir, paramètres FSRS.
  1ter. Six mémoires : tes mots mémoire par mémoire (lem.profil), et les écarts qui disent quoi travailler.
  1quater. Syllabaire et Mémoire verbale : suites rares sûres, record et erreurs.
  2. Profil     : tes θ — où tu t'écartes de « l'adulte cultivé moyen » (origines, domaines, morphèmes).
  3. Signature  : la répartition de tes types d'erreur de définition (trop générale, connotation seule…).
  4. Confusions : tes paires confondues, avec la nature de la proximité.
  5. Graphie    : tes fautes par type, frappe vs orthographe, mots à reprendre, risque par motif.
  6. Métacognition : ton auto-évaluation face à celle du correcteur.
  7. Vitesse    : latences médianes par jeu.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter

from rich.text import Text

from lem.grading.taxonomie import Erreur
from lem.orthographe import GRAPHIE, profil, taux_motif
from lem.similarite import comparer
from lem.tui.theme import BLANC, ETATS, GRIS, PROXIMITES, SOMBRE, barre, etat_de


def _titre(t: Text, s: str) -> None:
    t.append(f"\n{s}\n", style=f"bold {BLANC}")


def _memoire(con: sqlite3.Connection, t: Text) -> None:
    from lem.db import get_reglage
    from lem.memory import scheduler
    from lem.memory.optimiseur import bilan

    _titre(t, "MÉMOIRE — le moteur sur tes révisions (30 jours)")
    noms = {"recep": "sens", "prod": "mot", "ortho": "graphie"}
    cfg = get_reglage(con, "fsrs", {}) or {}
    par_trace = cfg.get("par_trace") or {}
    for trace, nom in noms.items():
        b = bilan(con, trace)
        t.append(f"  {nom:<10}", style=BLANC)
        if not b.n:
            t.append("pas encore de révision espacée", style=SOMBRE)
        else:
            ecart = b.reussite - b.prevue
            coul = ETATS["acquis"][0] if abs(ecart) < 0.05 else ETATS["flou"][0]
            t.append_text(barre(b.reussite, 20, coul))
            t.append(f" {b.reussite:.0%} réussies", style=BLANC)
            t.append(f" (FSRS prévoyait {b.prevue:.0%}, {b.n} révisions)", style=GRIS)
        t.append("   " + ("paramètres ajustés" if trace in par_trace else "paramètres par défaut") + "\n", style=SOMBRE)
    statuts = dict(con.execute(
        "SELECT COALESCE(statut, 'apprentissage'), COUNT(DISTINCT entree_id) FROM trace_etat GROUP BY 1").fetchall())
    t.append(f"  {statuts.get('apprentissage', 0)} mots en apprentissage · {statuts.get('connu_sonde', 0)} connus à la "
             f"sonde (vérification à venir) · {statuts.get('verifie', 0)} vérifiés\n", style=GRIS)
    t.append(f"  charge : {scheduler.charge(con, 7):.0f} révisions par jour sur la semaine", style=GRIS)
    if cfg.get("optimise_le"):
        t.append(f" · paramètres revus le {cfg['optimise_le'][:10]}", style=SOMBRE)
    t.append("\n")


def _six_memoires(con: sqlite3.Connection, t: Text) -> None:
    from lem.profil import LIBELLES, MEMOIRES, ecarts, profils

    _titre(t, "SIX MÉMOIRES — un mot n'est pas su d'un bloc")
    ps = profils(con)
    if not ps:
        t.append("  aucun mot mesuré pour l'instant\n", style=SOMBRE)
        return
    for m in MEMOIRES:
        vals = [v for x in ps.values() if (v := getattr(x, m)) is not None]
        t.append(f"  {LIBELLES[m]:<14}", style=BLANC)
        if not vals:
            t.append("pas encore mesuré\n", style=SOMBRE)
            continue
        moy = statistics.mean(vals)
        t.append_text(barre(moy, 20, ETATS["acquis"][0] if moy >= 0.8 else ETATS["flou"][0] if moy >= 0.5 else ETATS["inconnu"][0]))
        t.append(f" {moy:.0%}", style=BLANC)
        t.append(f"  sur {len(vals)} mots\n", style=GRIS)
    for titre, mots in ecarts(ps):
        t.append(f"  {titre} : ", style=GRIS)
        t.append(", ".join(x.forme for x in mots) + "\n", style=BLANC)


def _jeux_entrainement(con: sqlite3.Connection, t: Text, lexique=None) -> None:
    from lem.games.memoire_verbale import scores

    _titre(t, "ENTRAÎNEMENTS")
    if lexique is not None:
        from lem.games.syllabaire import resume

        r = resume(con, lexique)
        t.append(f"  syllabaire      sub-{r['seuil']} : {r['rares']} suites · {r['vues']} travaillées · ", style=GRIS)
        t.append(f"{r['sures']} sûres", style=BLANC)
        t.append(f" · {r['dues']} à revoir\n", style=GRIS)
    s = scores(con, "entrainement")
    if s:
        t.append(f"  mémoire verbale record {max(s)}", style=BLANC)
        dernieres = s[-10:]
        t.append(f" · médiane des {len(dernieres)} dernières {statistics.median(dernieres):.0f} · {len(s)} partie{'s' * (len(s) > 1)}\n",
                 style=GRIS)
        fa, ou = con.execute(
            """SELECT SUM(reponse = 'VU' AND correct = 0), SUM(reponse = 'NOUVEAU' AND correct = 0) FROM evenement
               WHERE jeu = 'memoire' AND type = 'reponse' AND json_extract(data, '$.mode') = 'entrainement'""").fetchone()
        t.append(f"                  erreurs : {fa or 0} fausses alertes (familiarité) · {ou or 0} oublis (encodage)\n",
                 style=GRIS)
        enc = [r[0] for r in con.execute("""SELECT latence_ms FROM evenement WHERE jeu = 'memoire'
                                             AND type = 'encodage' ORDER BY id DESC LIMIT 200""")]
        if enc:
            t.append(f"                  image en {statistics.median(enc) / 1000:.1f} s (médiane)\n".replace(".", ","),
                     style=GRIS)
    else:
        t.append("  mémoire verbale pas encore de partie d'entraînement\n", style=SOMBRE)


def rapport(con: sqlite3.Connection, lexique=None) -> Text:
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
        t.append_text(barre(n / total, 30, coul))
        t.append(f" {n}\n", style=GRIS)

    # 1bis. Mémoire
    _memoire(con, t)
    _six_memoires(con, t)
    _jeux_entrainement(con, t, lexique)

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
            t.append_text(barre(err[e.value] / n_err, 30))
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

    # 5. Graphie
    _titre(t, "GRAPHIE — aucune variante acceptée")
    fautes = [json.loads(r[0])["graphie"] for r in con.execute(
        "SELECT data FROM evenement WHERE trace = 'ortho' AND correct = 0 AND data LIKE '%\"graphie\":{%'")]
    if not fautes:
        t.append("  aucune faute enregistrée\n", style=SOMBRE)
    else:
        types = Counter(x for f in fautes for x in f.get("types", []))
        natures = Counter(f.get("nature") for f in fautes)
        t.append(f"  {len(fautes)} fautes · orthographe {natures.get('orthographe', 0)} · "
                 f"frappe {natures.get('frappe', 0)}\n", style=GRIS)
        for typ, n in types.most_common():
            t.append(f"  {typ:<20}", style=GRAPHIE)
            t.append_text(barre(n / len(fautes), 30, GRAPHIE))
            t.append(f" {n}\n", style=GRIS)
        mots = Counter(f.get("correcte") for f in fautes)
        t.append("  à reprendre : ", style=GRIS)
        t.append(", ".join(f"{m} ×{n}" for m, n in mots.most_common(10)) + "\n", style=BLANC)
    prof = profil(con)
    lignes = [(m, f, o) for m, (f, o) in prof.items() if o]
    if lignes:
        t.append("  risque par motif (fautes / occasions)\n", style=GRIS)
        for m, f, o in sorted(lignes, key=lambda x: -taux_motif(*x[1:])):
            t.append(f"    {m:<18}{f:>3} / {o:<5}", style=BLANC)
            t.append_text(barre(taux_motif(f, o) * 4, 16, GRAPHIE))
            t.append(f" {taux_motif(f, o):.0%}\n", style=GRIS)

    # 6. Métacognition
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

    # 7. Vitesse
    _titre(t, "VITESSE — latence médiane")
    groupes: dict[tuple[str, str], list[int]] = {}
    for jeu, mode, lat in con.execute(
            "SELECT jeu, COALESCE(mode_saisie, 'clavier'), latence_ms FROM evenement "
            "WHERE type = 'reponse' AND latence_ms IS NOT NULL"):
        groupes.setdefault((jeu, mode), []).append(lat)
    for (jeu, mode), lats in sorted(groupes.items()):
        t.append(f"  {jeu:<14}{statistics.median(lats) / 1000:5.1f} s", style=BLANC)
        t.append(f"   {mode} · {len(lats)} réponses\n", style=GRIS)
    return t
