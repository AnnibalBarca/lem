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
  lem atlas                imprime l'atlas dans le terminal
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
        for nom in ("lexique", "wiktionnaire", "jeuxdemots"):
            if nom in chemins:
                f = {"lexique": ingest.lexique, "wiktionnaire": ingest.wiktionnaire, "jeuxdemots": ingest.jeuxdemots}[nom]
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

    ctx = _ctx(args)
    p = calibrer(ctx.con, ParamsPrior(cible_connus=args.cible, pente=args.pente))
    print(f"seuil Zipf calibré : {p.seuil:.2f} (≈ {args.cible:.0f} lemmes connus)")


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
    print(f"{n} réponses notées.")


def cmd_nettoyer(args) -> None:
    from lem import ingest

    b = ingest.nettoyer_sens(_ctx(args).con)
    print(f"définitions nettoyées : {b['nettoyes']} · sens vides supprimés : {b['supprimes']} · "
          f"vidés mais gardés (cités par le journal) : {b['vides_gardes']}")
    if b["nettoyes"]:
        print("→ `lem index` (avec les mêmes options) recalcule leurs vecteurs.")


def cmd_rebuild(args) -> None:
    from lem.memory import scheduler

    ctx = _ctx(args)
    n = scheduler.reconstruire(ctx.con)
    ctx.carte.recalculer_tout()
    print(f"{n} traces FSRS reconstruites ; carte recalculée.")


def cmd_atlas(args) -> None:
    from rich.console import Console

    from lem.atlas import rapport

    Console().print(rapport(_ctx(args).con))


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
    i.add_argument("source", choices=["lexique", "wiktionnaire", "jeuxdemots", "wordfreq"])
    i.add_argument("fichier", nargs="?")
    i.set_defaults(f=cmd_ingest)
    t = sp.add_parser("telecharger")
    t.add_argument("sources", nargs="*", choices=["lexique", "wiktionnaire", "jeuxdemots"])
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
    sp.add_parser("nettoyer", help="retire les gabarits « Définition manquante… » d'une base déjà ingérée").set_defaults(f=cmd_nettoyer)
    sp.add_parser("rebuild").set_defaults(f=cmd_rebuild)
    sp.add_parser("atlas").set_defaults(f=cmd_atlas)

    args = ap.parse_args(argv)
    (getattr(args, "f", None) or cmd_jouer)(args)


if __name__ == "__main__":
    main()
