"""Contexte partagé d'une exécution : base, embedder, magasins de vecteurs, carte de connaissance."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from lem.db import DEFAULT_DB, connect, get_reglage, set_reglage
from lem.memory.knowledge import CarteConnaissance
from lem.vecteurs import HashEmbedder, Magasin, OllamaEmbedder


class Contexte:
    def __init__(self, chemin: str | Path | None = None):
        self.chemin = str(chemin or DEFAULT_DB)
        self.con = connect(self.chemin)

    @cached_property
    def embedder(self):
        """bge-m3 si Ollama répond ET que la base a des vecteurs bge-m3 ; sinon le repli par n-grammes."""
        modeles = {r[0] for r in self.con.execute("SELECT DISTINCT modele FROM embedding")}
        o = OllamaEmbedder()
        if o.nom in modeles and o.disponible():
            return o
        if HashEmbedder.nom in modeles:
            return HashEmbedder()
        return None

    @cached_property
    def magasin_sens(self):
        return Magasin(self.con, "sens", self.embedder.nom) if self.embedder else None

    @cached_property
    def magasin_entrees(self):
        # La proximité « par le sens » n'a de valeur qu'avec un embedder sémantique.
        if self.embedder and self.embedder.semantique:
            return Magasin(self.con, "entree", self.embedder.nom)
        return None

    @cached_property
    def lexique(self):
        """Index compact des formes (lem.lexique), partagé par tous les jeux de la session."""
        from lem.lexique import Lexique

        return Lexique(self.con)

    @cached_property
    def juge(self):
        """Juge local (LLM via Ollama) si un modèle est réglé et disponible, sinon None."""
        from lem.grading.local import JugeLocal, modele_reglage

        m = modele_reglage(self.con)
        j = JugeLocal(m) if m else None
        return j if j and j.disponible() else None

    @cached_property
    def carte(self) -> CarteConnaissance:
        return CarteConnaissance(self.con, self.lexique)

    def params(self, jeu) -> dict:
        p = {**jeu.defauts(), **get_reglage(self.con, f"params:{jeu.id}", {})}
        for d in jeu.params:                       # valeurs renommées depuis l'enregistrement
            p[d.nom] = d.migrer(p.get(d.nom))
        return p

    def sauver_params(self, jeu, params: dict) -> None:
        set_reglage(self.con, f"params:{jeu.id}", params)
        self.con.commit()

    def stats(self) -> dict:
        c = self.con
        return {
            "entrees": c.execute("SELECT COUNT(*) FROM entree").fetchone()[0],
            "sens": c.execute("SELECT COUNT(*) FROM sens").fetchone()[0],
            "connus_estimes": round(c.execute("SELECT COALESCE(SUM(p_connu), 0) FROM connaissance").fetchone()[0]),
            "dues": c.execute("SELECT COUNT(*) FROM trace_etat WHERE due <= strftime('%Y-%m-%dT%H:%M:%f', 'now')").fetchone()[0],
            "a_noter": c.execute(
                """SELECT COUNT(*) FROM evenement ev WHERE ev.type = 'reponse' AND ev.trace = 'recep'
                   AND COALESCE(ev.reponse, '') != '' AND NOT EXISTS (SELECT 1 FROM notation n
                   WHERE n.evenement_id = ev.id AND (n.correcteur LIKE 'claude:%' OR n.correcteur LIKE 'jev:%'))"""
            ).fetchone()[0],
            "reponses": c.execute("SELECT COUNT(*) FROM evenement WHERE type = 'reponse'").fetchone()[0],
        }
