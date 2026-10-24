"""Commande `lem`.

  lem                      ouvre l'interface (menu des jeux)
  lem demo                 crée une base de démonstration (~80 mots) et l'ouvre
  lem telecharger [S…]     télécharge les sources dans ~/.lem/sources (hors du dépôt) ;
                           --ingerer enchaîne ingestion + prior
  lem ingest <source> F    lexique | wiktionnaire | jeuxdemots | wordfreq
  lem prior [--cible N]    calibre l'a priori « adulte cultivé » (N lemmes connus)
  lem index                calcule les vecteurs bge-m3 (Ollama) des sens et des entrées
  lem calibrer             mesure les seuils (sens, température) sur la base réelle
  lem noter [--modele M]   notation différée par Claude Code headless (sonnet par défaut)
  lem nettoyer             retire les gabarits « Définition manquante… » (base déjà ingérée)
  lem rebuild              rejoue tout le journal (FSRS + carte de connaissance)
  lem interets science=2   poids par domaine (sous-domaines compris) pour le choix des mots nouveaux
  lem optimiser            ajuste les paramètres FSRS sur ton journal (aussi fait chaque semaine en fond)
  lem simuler              banc d'essai du moteur sur un élève simulé
  lem suites [--seuil 50]  les suites rares de la Bombe (sub-N) : combien, lesquelles, tes progrès
  lem atlas                imprime l'atlas dans le terminal
  lem mots [--jeu bombe] [--tri connu] [--csv]   tous les mots vus et ce que tu en sais
  lem mot <forme>          le profil d'un mot : ses six mémoires (reconnu, défini, retrouvé, écrit, trouvé, encodé)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from lem.db import DEFAULT_DB


def _ctx(args):
    from lem.contexte import Contexte

    return Contexte(args.db)


def cmd_jouer(args) -> None:
    from lem.tui.app import lancer

    lancer(_ctx(args), args.modele)


def cmd_demo(args) -> None:
    from lem import ingest
    from lem.db import connect
    from lem.memory.knowledge import ParamsPrior, calibrer
    from lem.vecteurs import embedder_par_defaut, indexer

    chemin = Path(args.db if args.db != str(DEFAULT_DB) else DEFAULT_DB.with_name("demo.sqlite"))
    if chemin.exists() and not args.garder:
        chemin.unlink()
    con = connect(chemin)
    if not con.execute("SELECT 1 FROM entree LIMIT 1").fetchone():
        print(f"démo : {ingest.demo(con)} entrées")
        calibrer(con, ParamsPrior(cible_connus=45))
        emb = embedder_par_defaut()
        print(f"vecteurs : {emb.nom}" + ("" if emb.semantique else " (repli sans Ollama : formes seulement)"))
        indexer(con, emb, "sens")
        indexer(con, emb, "entree")
    con.close()
    args.db = str(chemin)
    if not args.sans_jeu:
        cmd_jouer(args)


def cmd_ingest(args) -> None:
    from lem import ingest

    ctx = _ctx(args)
    prog = lambda n: print(f"  … {n}", end="\r", flush=True)  # noqa: E731
    if args.source == "lexique":
        n = ingest.lexique(ctx.con, args.fichier, prog)
    elif args.source == "wiktionnaire":
        n = ingest.wiktionnaire(ctx.con, args.fichier, prog)
    elif args.source == "jeuxdemots":
        n = ingest.jeuxdemots(ctx.con, args.fichier, progression=prog)
    elif args.source == "grammalecte":
        n = ingest.grammalecte(ctx.con, args.fichier, prog)
        print(f"\n→ `lem ingest wordfreq` puis `lem prior` pour donner une fréquence aux nouvelles entrées.")
    elif args.source == "wordfreq":
        n = ingest.zipf_wordfreq(ctx.con)
    else:
        sys.exit(f"source inconnue : {args.source}")
    print(f"{args.source} : {n} éléments")


def cmd_telecharger(args) -> None:
    from lem import ingest
    from lem.ingest.telecharger import DOSSIER, SOURCES, telecharger

    noms = args.sources or ["lexique", "wiktionnaire"]
    chemins = {}
    for nom in noms:
        print(f"{SOURCES[nom].nom} — {SOURCES[nom].note}")
        def prog(n, total):
            print(f"  {n / 1e6:8.1f} Mo" + (f" / {total / 1e6:.1f}" if total else ""), end="\r", flush=True)
        try:
            chemins[nom] = telecharger(nom, args.url if len(noms) == 1 else None, progression=prog)
            print(f"\n  → {chemins[nom]}")
        except RuntimeError as exc:
            print(f"\n{exc}")
    print(f"dossier des sources : {DOSSIER} (hors du dépôt)")
    if args.ingerer and chemins:
        ctx = _ctx(args)
        ingest.charger_domaines(ctx.con)
        for nom in ("lexique", "wiktionnaire", "grammalecte", "jeuxdemots"):
            if nom in chemins:
                f = {"lexique": ingest.lexique, "wiktionnaire": ingest.wiktionnaire, "grammalecte": ingest.grammalecte,
                     "jeuxdemots": ingest.jeuxdemots}[nom]
                print(f"ingestion {nom} : {f(ctx.con, chemins[nom])}")
        print(f"wordfreq : {ingest.zipf_wordfreq(ctx.con)}")
        args.cible, args.pente = 45_000, 1.6
        cmd_prior(args)


def cmd_calibrer(args) -> None:
    import json

    from lem.calibrage import calibrer_seuils

    print(json.dumps(calibrer_seuils(_ctx(args).con, args.modele_embedding), ensure_ascii=False, indent=2))


def cmd_prior(args) -> None:
    from lem.memory.knowledge import ParamsPrior, calibrer

    from lem.memory.knowledge import CarteConnaissance

    ctx = _ctx(args)
    p = calibrer(ctx.con, ParamsPrior(cible_connus=args.cible, pente=args.pente))
    CarteConnaissance(ctx.con).rejouer()
    print(f"seuil Zipf calibré : {p.seuil:.2f} (≈ {args.cible:.0f} lemmes connus) ; carte rejouée.")


def cmd_index(args) -> None:
    from lem.vecteurs import OllamaEmbedder, indexer

    ctx = _ctx(args)
    emb = OllamaEmbedder(args.modele_embedding)
    if not emb.disponible():
        sys.exit("Ollama ne répond pas : `ollama serve` puis `ollama pull bge-m3`.")
    for objet in ("sens", "entree"):
        n = indexer(ctx.con, emb, objet, zipf_min=args.zipf_min, rang_max=args.rang_max,
                    progression=lambda a, b: print(f"  {objet} {a}/{b}", end="\r", flush=True))
        print(f"\n{objet} : {n} vecteurs")


def cmd_noter(args) -> None:
    from lem.grading.claude_headless import noter

    ctx = _ctx(args)
    n = noter(ctx.con, args.modele, args.limite)
    if n:
        ctx.carte.rejouer()
    print(f"{n} réponses notées." + (" Carte de connaissance rejouée." if n else ""))


def cmd_nettoyer(args) -> None:
    from lem import ingest

    ctx = _ctx(args)
    loc = ingest.retirer_locutions(ctx.con)
    print(f"locutions retirées de la base : {loc['supprimees']} · gardées parce que le journal les cite : "
          f"{loc['gardees_car_citees']}")
    if loc["supprimees"]:
        from lem.memory.knowledge import estimation_connus, recalibrer_prior

        # Le lexique a changé de taille : la cible « adulte cultivé » se recompte sur les mots qui restent.
        p = recalibrer_prior(ctx.con)
        if p:
            connus, prior = estimation_connus(ctx.con)
            print(f"a priori recalibré (seuil Zipf {p.seuil:.2f}, ≈ {prior:,.0f} lemmes pour l'adulte moyen) ; "
                  f"carte rejouée : ≈ {connus:,.0f} lemmes connus estimés".replace(",", " "))
    b = ingest.nettoyer_sens(ctx.con)
    print(f"définitions nettoyées : {b['nettoyes']} · sens vides supprimés : {b['supprimes']} · "
          f"vidés mais gardés (cités par le journal) : {b['vides_gardes']}")
    if b["nettoyes"]:
        print("→ `lem index` (avec les mêmes options) recalcule leurs vecteurs.")


def cmd_interets(args) -> None:
    """lem interets science=2 droit=0.5 … ; sans argument : affiche ; « aucun » : efface."""
    from lem.db import domaines_descendants, get_reglage, set_reglage

    ctx = _ctx(args)
    actuels = dict(get_reglage(ctx.con, "interets", {}) or {})
    if args.poids == ["aucun"]:
        actuels = {}
    for item in args.poids if args.poids != ["aucun"] else []:
        nom, _, w = item.partition("=")
        nom = nom.strip().lower()
        if not domaines_descendants(ctx.con, [nom]):
            sys.exit(f"domaine inconnu : {nom}")
        actuels[nom] = float(w or 1)
    set_reglage(ctx.con, "interets", actuels)
    ctx.con.commit()
    print("intérêts : " + (", ".join(f"{k} ×{v:g}" for k, v in actuels.items()) or "aucun (tous les domaines à égalité)"))


def cmd_optimiser(args) -> None:
    from lem.memory.optimiseur import MIN_REVISIONS, optimiser

    ctx = _ctx(args)
    raps = optimiser(ctx.con, adopter=not args.essai)
    for trace, r in raps.items():
        nom = {"recep": "sens (mot → sens)", "prod": "mot (sens → mot)", "ortho": "graphie"}[trace]
        if r.n_revisions < MIN_REVISIONS:
            print(f"{nom:<20} {r.n_revisions} révisions espacées — il en faut {MIN_REVISIONS}, paramètres inchangés")
        else:
            verdict = "adoptés" if r.adopte and not args.essai else ("meilleurs (essai)" if r.adopte else "gardés")
            print(f"{nom:<20} {r.n_revisions} révisions · perte récente {r.validation_avant} → {r.validation_apres} · {verdict}")


def cmd_simuler(args) -> None:
    import json
    from dataclasses import asdict

    from lem.simulation import comparer, simuler

    cfg = json.loads(args.params) if args.params else {}
    if args.graines > 1:
        r = comparer({"moteur": cfg}, graines=range(args.graines), jours=args.jours, minutes=args.minutes,
                     params_eleve=args.eleve, optimiser_chaque=args.optimiser)["moteur"]
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        r = simuler(cfg, jours=args.jours, minutes=args.minutes, params_eleve=args.eleve,
                    optimiser_chaque=args.optimiser, journal=print)
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2))


def cmd_suites(args) -> None:
    from lem.games.syllabaire import etats_suites
    from lem.suites import IndexSuites

    ctx = _ctx(args)
    ix = IndexSuites(ctx.lexique)
    print("suites de 2 et 3 lettres, par nombre de mots valides (ni noms propres ni sigles) :")
    for tranche, n in ix.repartition().items():
        print(f"  {tranche:>8} mots : {n:>6} suites")
    rares = ix.rares(args.seuil, minimum=args.minimum, attestes=args.attestes)
    etats = etats_suites(ctx.con)
    vues = [s for s, _ in rares if s in etats]
    sures = [s for s in vues if (etats[s].card.stability or 0) >= 21]
    print(f"sub-{args.seuil} (au moins {args.minimum} mots, dont {args.attestes} en usage) : {len(rares)} suites · {len(vues)} travaillées "
          f"· {len(sures)} sûres (stabilité ≥ 21 j)")
    print("  " + "  ".join(f"{s.upper()}:{n}" for s, n in rares[: args.exemples]))


def cmd_rebuild(args) -> None:
    from lem.memory import scheduler

    from lem.memory.migration import retablir_observations

    ctx = _ctx(args)
    retablir_observations(ctx.con)          # base v1 : sans cela, le rejeu effacerait la carte
    n = scheduler.reconstruire(ctx.con)
    k = ctx.carte.rejouer()
    print(f"{n} traces FSRS reconstruites ; carte de connaissance rejouée ({k} observations).")


def cmd_atlas(args) -> None:
    from rich.console import Console

    from lem.atlas import rapport

    ctx = _ctx(args)
    Console().print(rapport(ctx.con, ctx.lexique))


def cmd_mots(args) -> None:
    """Tous les mots que les jeux t'ont montrés ou que tu as donnés, et ce que le journal dit de chacun."""
    import csv
    from datetime import datetime

    from lem.profil import LIBELLES, MEMOIRES, profils

    ctx = _ctx(args)
    ctx.carte                                   # base ancienne : mise à niveau d'abord
    filtre, params = "", []
    if args.jeu:
        filtre, params = " AND jeu = ?", [args.jeu]
    vus = {r["entree_id"]: r for r in ctx.con.execute(
        f"""SELECT entree_id, GROUP_CONCAT(DISTINCT jeu) AS jeux, COUNT(*) AS n, MAX(ts) AS dernier
            FROM evenement WHERE entree_id IS NOT NULL AND type IN ('stimulus', 'reponse', 'revelation', 'indice')
            {filtre} GROUP BY entree_id""", params)}
    ps = profils(ctx.con, list(vus))
    noms = dict(ctx.con.execute(f"SELECT id, forme FROM entree WHERE id IN ({', '.join('?' * len(vus))})", list(vus))) \
        if vus else {}
    lignes = []
    for eid, v in vus.items():
        x = ps.get(eid)
        lignes.append({"mot": noms.get(eid, "?"), "jeux": v["jeux"], "vu": v["n"], "dernier": v["dernier"][:16],
                       **{m: (None if x is None else getattr(x, m)) for m in MEMOIRES},
                       "trouve": (x.detail.get("trouve") if x else None)})
    cle = {"recent": lambda r: r["dernier"], "connu": lambda r: -(r["connu"] or 0), "vu": lambda r: -r["vu"],
           "alpha": lambda r: r["mot"].lower()}[args.tri]
    lignes.sort(key=cle, reverse=(args.tri == "recent"))
    lignes = lignes[: args.max] if args.max else lignes
    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["mot", "jeux", "vu", "dernier", *[LIBELLES[m] for m in MEMOIRES]])
        for r in lignes:
            w.writerow([r["mot"], r["jeux"], r["vu"], r["dernier"], *[("" if r[m] is None else f"{r[m]:.2f}") for m in MEMOIRES]])
        return
    pct = lambda v: "   ·" if v is None else f"{v:4.0%}"  # noqa: E731
    print(f"{len(vus)} mots vus"
          + (f" (jeu : {args.jeu})" if args.jeu else "") + f" · tri : {args.tri}\n")
    print(f"{'mot':<22}{'jeux':<22}{'vu':>3}  {'dernier':<16}" + "".join(f"{LIBELLES[m][:8]:>9}" for m in MEMOIRES))
    for r in lignes:
        print(f"{r['mot'][:21]:<22}{(r['jeux'] or '').replace('cartes', 'C').replace('bombe', 'B').replace('syllabaire', 'S').replace('memoire', 'M').replace('mot_ou_pas', 'D').replace('etalonnage', 'É')[:21]:<22}"
              f"{r['vu']:>3}  {r['dernier']:<16}" + "".join(f"{pct(r[m]):>9}" for m in MEMOIRES))
    print("\njeux : C Cartes · B Bombe · S Syllabaire · M Mémoire verbale · D Mot ou pas mot · É Étalonnage ; "
          "· = pas encore mesuré. Détail d'un mot : lem mot <mot>.")


def cmd_mot(args) -> None:
    from rich.console import Console
    from rich.text import Text

    from lem.profil import LIBELLES, MEMOIRES, profils
    from lem.tui.theme import BLANC, GRIS, SOMBRE, barre

    ctx = _ctx(args)
    ids = [r[0] for r in ctx.con.execute("SELECT id FROM entree WHERE forme = ?", (args.forme,))]
    if not ids:
        ids = [r[0] for r in ctx.con.execute("SELECT DISTINCT entree_id FROM forme WHERE forme = ?",
                                             (args.forme.lower(),))]
    if not ids:
        print(f"« {args.forme} » n'est pas dans le lexique")
        return
    ctx.carte                                   # base ancienne : mise à niveau d'abord
    for eid, x in profils(ctx.con, ids).items():
        t = Text(f"{x.forme}\n", style=f"bold {BLANC}")
        for m in MEMOIRES:
            v = getattr(x, m)
            t.append(f"  {LIBELLES[m]:<14}", style=BLANC)
            if v is None:
                t.append("pas encore mesuré\n", style=SOMBRE)
            else:
                t += barre(v, 20)
                t.append(f" {v:.0%}\n", style=GRIS)
        d = x.detail
        if d:
            infos = []
            if "trouve" in d:
                n, med = d["trouve"]
                infos.append(f"trouvé {n} fois" + (f", en {med / 1000:.1f} s".replace(".", ",") if med else ""))
            if "reconnu" in d:
                infos.append(f"reconnu {d['reconnu'][0]} fois sur {d['reconnu'][1]}")
            if "images" in d:
                infos.append(f"{d['images']} image(s) mentale(s)")
            if "statut" in d:
                infos.append(d["statut"].replace("_", " "))
            t.append("  " + " · ".join(infos) + "\n", style=GRIS)
        Console().print(t)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="lem", description="Apprendre le lexique français entier.")
    ap.add_argument("--db", default=os.environ.get("LEM_DB", str(DEFAULT_DB)), help="chemin de la base SQLite")
    ap.add_argument("--modele", default=os.environ.get("LEM_MODELE", "sonnet"), help="modèle Claude pour la notation")
    sp = ap.add_subparsers(dest="cmd")

    sp.add_parser("jouer").set_defaults(f=cmd_jouer)
    d = sp.add_parser("demo")
    d.add_argument("--garder", action="store_true", help="ne pas recréer la base de démo")
    d.add_argument("--sans-jeu", action="store_true")
    d.set_defaults(f=cmd_demo)
    i = sp.add_parser("ingest")
    i.add_argument("source", choices=["lexique", "wiktionnaire", "grammalecte", "jeuxdemots", "wordfreq"])
    i.add_argument("fichier", nargs="?")
    i.set_defaults(f=cmd_ingest)
    t = sp.add_parser("telecharger")
    t.add_argument("sources", nargs="*", choices=["lexique", "wiktionnaire", "grammalecte", "jeuxdemots"])
    t.add_argument("--url", help="URL imposée (une seule source)")
    t.add_argument("--ingerer", action="store_true", help="enchaîne ingestion, wordfreq et prior")
    t.set_defaults(f=cmd_telecharger)
    c = sp.add_parser("calibrer")
    c.add_argument("--modele-embedding", default="bge-m3")
    c.set_defaults(f=cmd_calibrer)
    p = sp.add_parser("prior")
    p.add_argument("--cible", type=float, default=45_000)
    p.add_argument("--pente", type=float, default=1.6)
    p.set_defaults(f=cmd_prior)
    x = sp.add_parser("index")
    x.add_argument("--modele-embedding", default="bge-m3")
    x.add_argument("--zipf-min", type=float, default=None, help="seulement les entrées de Zipf > ce seuil (ex. 0)")
    x.add_argument("--rang-max", type=int, default=None, help="seulement les sens de rang ≤ ce rang (ex. 3)")
    x.set_defaults(f=cmd_index)
    n = sp.add_parser("noter")
    n.add_argument("--limite", type=int, default=500)
    n.add_argument("--modele", default=argparse.SUPPRESS, help="sonnet | haiku | opus")
    n.set_defaults(f=cmd_noter)
    sp.add_parser("nettoyer", help="retire les locutions et les gabarits « Définition manquante… » d'une base déjà ingérée").set_defaults(f=cmd_nettoyer)
    sp.add_parser("rebuild").set_defaults(f=cmd_rebuild)
    it = sp.add_parser("interets", help="poids par domaine pour le choix des mots nouveaux : science=2 droit=0.5")
    it.add_argument("poids", nargs="*")
    it.set_defaults(f=cmd_interets)
    o = sp.add_parser("optimiser", help="ajuste les paramètres FSRS sur ton journal (un jeu par type de carte)")
    o.add_argument("--essai", action="store_true", help="mesurer sans adopter")
    o.set_defaults(f=cmd_optimiser)
    s = sp.add_parser("simuler", help="banc d'essai du moteur sur un élève simulé")
    s.add_argument("--jours", type=int, default=30)
    s.add_argument("--minutes", type=float, default=15.0)
    s.add_argument("--eleve", default="moyen", choices=["faible", "moyen", "fort"])
    s.add_argument("--graines", type=int, default=1)
    s.add_argument("--optimiser", type=int, default=7, help="optimiseur FSRS tous les N jours (0 : jamais)")
    s.add_argument("--params", help="paramètres du jeu de Cartes, en JSON")
    s.set_defaults(f=cmd_simuler)
    sp.add_parser("atlas").set_defaults(f=cmd_atlas)
    mm = sp.add_parser("mots", help="tous les mots que les jeux t'ont montrés ou que tu as donnés, et ce que tu en sais")
    mm.add_argument("--jeu", choices=["cartes", "bombe", "syllabaire", "memoire", "mot_ou_pas", "etalonnage"])
    mm.add_argument("--tri", choices=["recent", "connu", "vu", "alpha"], default="recent")
    mm.add_argument("--max", type=int, default=0, help="seulement les N premiers (0 : tous)")
    mm.add_argument("--csv", action="store_true", help="sortie CSV, pour un tableur")
    mm.set_defaults(f=cmd_mots)
    mo = sp.add_parser("mot", help="le profil d'un mot : ses six mémoires")
    mo.add_argument("forme")
    mo.set_defaults(f=cmd_mot)
    su = sp.add_parser("suites", help="les suites rares de la Bombe (sub-N) et tes progrès au Syllabaire")
    su.add_argument("--seuil", type=int, default=50)
    su.add_argument("--minimum", type=int, default=3)
    su.add_argument("--attestes", type=int, default=2, help="dont au moins N mots en usage (wordfreq)")
    su.add_argument("--exemples", type=int, default=30)
    su.set_defaults(f=cmd_suites)

    args = ap.parse_args(argv)
    (getattr(args, "f", None) or cmd_jouer)(args)


if __name__ == "__main__":
    main()
