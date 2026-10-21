"""Cartes (façon Anki), avec notation en direct puis différée.

Exercices
  recep          : le MOT est montré → tu écris sa définition, en texte libre OU en synonymes
                   (« flatteur, laudateur »). Notation bge-m3 immédiate (température, dictionnaire
                   inversé), puis Claude/Jev en fin de session.
  prod           : la DÉFINITION est montrée → tu écris le mot. Tab révèle une lettre de plus :
                   le nombre de lettres nécessaires mesure la profondeur du « mot sur le bout de la langue ».
  discrimination : une définition, quatre mots d'une même grappe de confusables → lequel ?

Après chaque réponse : révélation (sens, voisins confondables et ce qui les distingue), puis
auto-évaluation 1–4 (Entrée = accepter la note proposée). Tout est écrit au journal ; FSRS est rejoué.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field

from lem.confusables import IndexFormel, grappe
from lem.db import iso, now
from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.grading.direct import VerdictProd, VerdictRecep, noter_prod, noter_recep
from lem.journal import Evenement, Frappe, ajouter_notation, ecrire
from lem.memory import scheduler
from lem.memory.knowledge import Y_PAR_NOTE, CarteConnaissance, score_apprentissage

CARTES = enregistrer(Jeu(
    id="cartes",
    nom="Cartes",
    accroche="Définir, retrouver, distinguer.",
    description="Répétition espacée FSRS : mot → sens, sens → mot, et duels de confusables.",
    params=[
        Param("exercice", "Exercice", str, "mixte", "recep | prod | mixte",
              choix=("recep", "prod", "mixte")),
        Param("confusables", "Duels de confusables", bool, True, "ajoute des duels après chaque mot nouveau"),
        Param("nouveaux", "Mots nouveaux", int, 10, "", 0, 200),
        Param("revisions_max", "Révisions max", int, 100, "", 0, 2000),
        *PARAMS_FILTRES,
    ],
))


@dataclass
class Item:
    exercice: str                 # recep | prod | discrimination
    entree_id: int
    sens_id: int
    forme: str
    pos: str
    definition: str
    voisins: list[dict] = field(default_factory=list)     # [{mot, definition, proximite}]
    options: list[tuple[int, str]] = field(default_factory=list)
    nouveau: bool = False
    indices: int = 0
    debut: object = None


@dataclass
class Resultat:
    item: Item
    reponse: str
    correct: bool | None
    note_proposee: int | None
    evenement_id: int
    recep: VerdictRecep | None = None
    prod: VerdictProd | None = None
    sens: list[tuple[int, str]] = field(default_factory=list)


class PartieCartes:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 embedder=None, magasin_sens=None, magasin_entrees=None, session_id: int | None = None,
                 rng: random.Random | None = None):
        self.con, self.params = con, params
        self.carte = carte or CarteConnaissance(con)
        self.embedder, self.magasin_sens, self.magasin_entrees = embedder, magasin_sens, magasin_entrees
        self.session_id = session_id
        self.rng = rng or random.Random()
        self.index = IndexFormel(con)
        self.file: list[Item] = []
        self.construire_file()

    # -- file ---------------------------------------------------------------
    def _sens_principal(self, entree_id: int, sens_id: int = 0) -> sqlite3.Row | None:
        if sens_id:
            return self.con.execute("SELECT * FROM sens WHERE id = ?", (sens_id,)).fetchone()
        return self.con.execute("SELECT * FROM sens WHERE entree_id = ? ORDER BY rang LIMIT 1", (entree_id,)).fetchone()

    def _item(self, exercice: str, entree_id: int, sens_id: int = 0, nouveau: bool = False) -> Item | None:
        e = self.con.execute("SELECT forme, pos FROM entree WHERE id = ?", (entree_id,)).fetchone()
        s = self._sens_principal(entree_id, sens_id)
        if e is None or s is None:
            return None
        voisins = []
        for c in grappe(self.con, entree_id, self.magasin_entrees, self.index, k=5):
            d = self._sens_principal(c.entree_id)
            if d:
                voisins.append({"mot": c.forme, "definition": d["definition"], "proximite": c.comparaison.proximite.value})
        return Item(exercice, entree_id, s["id"], e["forme"], e["pos"],
                    s["definition_precise"] or s["definition"], voisins, nouveau=nouveau)

    def construire_file(self) -> None:
        p = self.params
        f = filtres_depuis(p)
        filtre_actif = any([f.domaines, f.origines, f.types, f.etat != "tous", f.zipf_min, f.zipf_max])
        vivier = selectionner(self.con, f)
        ids = {r["id"] for r in vivier} if filtre_actif else None
        traces = ("recep", "prod") if p["exercice"] == "mixte" else (p["exercice"],)

        items: list[Item] = []
        for t in scheduler.dues(self.con, limite=p["revisions_max"], traces=traces, entree_ids=ids):
            it = self._item(t.trace, t.entree_id, t.sens_id)
            if it:
                items.append(it)

        deja = {r[0] for r in self.con.execute("SELECT DISTINCT entree_id FROM trace_etat")}
        reveles = {r[0]: r[1] for r in self.con.execute(
            """SELECT entree_id, COUNT(*) FROM evenement WHERE type IN ('revelation') AND entree_id IS NOT NULL
               GROUP BY entree_id""")}
        cands = [r for r in vivier if r["id"] not in deja and r["p_connu"] < 0.9]
        cands.sort(key=lambda r: -score_apprentissage(self.carte.p(r["id"]), r["zipf"]) * (1 + 0.5 * reveles.get(r["id"], 0)))
        nouveaux = []
        for r in cands[: p["nouveaux"]]:
            premier = "prod" if p["exercice"] == "prod" else "recep"
            it = self._item(premier, r["id"], nouveau=True)
            if not it:
                continue
            nouveaux.append(it)
            if p["confusables"] and it.voisins:
                d = self._duel(it)
                if d:
                    nouveaux.append(d)
            if p["exercice"] == "mixte":
                prod = self._item("prod", r["id"], nouveau=True)
                if prod:
                    nouveaux.append(prod)
        # Les nouveaux sont intercalés : on n'enchaîne pas recep et prod du même mot.
        self.rng.shuffle(items)
        self.file = self._entrelacer(items, nouveaux)

    def _entrelacer(self, revisions: list[Item], nouveaux: list[Item]) -> list[Item]:
        out, n = [], 0
        for i, it in enumerate(revisions):
            out.append(it)
            if i % 3 == 2 and n < len(nouveaux):
                out.append(nouveaux[n])
                n += 1
        out.extend(nouveaux[n:])
        return out

    def _duel(self, it: Item) -> Item | None:
        rivaux = [(v["mot"], v) for v in it.voisins[:3]]
        if not rivaux:
            return None
        opts = [(it.entree_id, it.forme)]
        for mot, _ in rivaux:
            r = self.con.execute("SELECT id FROM entree WHERE forme = ? LIMIT 1", (mot,)).fetchone()
            if r:
                opts.append((r["id"], mot))
        self.rng.shuffle(opts)
        return Item("discrimination", it.entree_id, it.sens_id, it.forme, it.pos, it.definition,
                    it.voisins, options=opts, nouveau=True)

    # -- déroulé ------------------------------------------------------------
    def suivant(self) -> Item | None:
        if not self.file:
            return None
        it = self.file.pop(0)
        it.debut = now()
        ecrire(self.con, self.session_id, Evenement("cartes", "stimulus", entree_id=it.entree_id, sens_id=it.sens_id,
                                                    trace=None, data={"exercice": it.exercice, "nouveau": it.nouveau}))
        return it

    def indice(self, it: Item) -> str:
        """Révèle une lettre de plus du mot (exercice prod)."""
        it.indices = min(len(it.forme), it.indices + 1)
        return it.forme[: it.indices] + "·" * (len(it.forme) - it.indices)

    def repondre(self, it: Item, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Resultat:
        frappe = frappe or Frappe()
        sens = [(r["id"], r["definition"]) for r in
                self.con.execute("SELECT id, definition FROM sens WHERE entree_id = ? ORDER BY rang", (it.entree_id,))]
        data = {"exercice": it.exercice, "voisins": it.voisins, "indices": it.indices, "nouveau": it.nouveau}
        recep = prod = None
        if it.exercice == "recep":
            recep = noter_recep(self.con, it.entree_id, texte, self.embedder, self.magasin_sens, self.magasin_entrees)
            note, correct = recep.note_auto, None if recep.note_auto is None else recep.note_auto >= 2
            data.update({"temperature": recep.temperature, "sens_vise_id": recep.sens_vise_id,
                         "synonymes_reconnus": recep.synonymes_reconnus,
                         "mot_reellement_defini": recep.mot_reellement_defini[1] if recep.mot_reellement_defini else None})
            trace = "recep"
        elif it.exercice == "prod":
            prod = noter_prod(self.con, it.entree_id, texte, self.magasin_entrees)
            note, correct = prod.note_auto, prod.correct
            if correct and it.indices:
                note = min(note, 2 if it.indices <= len(it.forme) // 3 else 1)
            data.update({"mode": prod.mode, "proximite": prod.comparaison.proximite.value if prod.comparaison else None})
            trace = "prod"
        else:
            choisi = None
            try:
                k = int(texte.strip()) - 1
                choisi = it.options[k][0] if 0 <= k < len(it.options) else None
            except ValueError:
                pass
            correct = choisi == it.entree_id
            note = 3 if correct else 1
            data.update({"options": [o[1] for o in it.options], "choisi": choisi})
            trace = "prod"
            data["fsrs"] = False   # un duel informe la carte de connaissance, pas FSRS
        eid = ecrire(self.con, self.session_id, Evenement(
            "cartes", "reponse", entree_id=it.entree_id, sens_id=it.sens_id, trace=trace, reponse=texte,
            correct=correct, note_auto=note, latence_ms=latence_ms, frappe=frappe, data=data))
        if it.exercice == "prod" and prod and not prod.correct and prod.entree_donnee:
            from lem.similarite import enregistrer_confusion
            enregistrer_confusion(self.con, it.entree_id, prod.entree_donnee)
        if it.exercice == "discrimination" and not correct and data.get("choisi"):
            from lem.similarite import enregistrer_confusion
            enregistrer_confusion(self.con, it.entree_id, data["choisi"])
        return Resultat(it, texte, correct, note, eid, recep, prod, sens)

    def valider(self, res: Resultat, note_soi: int | None = None) -> None:
        """Clôt une carte : auto-évaluation éventuelle, carte de connaissance, FSRS."""
        note = note_soi or res.note_proposee
        if note_soi is not None:
            ajouter_notation(self.con, res.evenement_id, "soi", note=note_soi,
                             brut={"proposee": res.note_proposee})
        if note is not None:
            self.carte.observer(res.item.entree_id, Y_PAR_NOTE[note])
        if res.item.exercice != "discrimination":
            scheduler.reconstruire(self.con, [res.item.entree_id])

    def restant(self) -> int:
        return len(self.file)


def resume_session(con: sqlite3.Connection, session_id: int) -> dict:
    rows = con.execute(
        "SELECT correct, note_auto, latence_ms FROM evenement WHERE session_id = ? AND type = 'reponse'",
        (session_id,),
    ).fetchall()
    jug = [r for r in rows if r["correct"] is not None]
    return {
        "reponses": len(rows),
        "reussite": (sum(r["correct"] for r in jug) / len(jug)) if jug else None,
        "latence_mediane_ms": sorted(r["latence_ms"] or 0 for r in rows)[len(rows) // 2] if rows else None,
        "fin": iso(),
    }
