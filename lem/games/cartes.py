"""Cartes (façon Anki), avec notation en direct puis différée.

Exercices
  graphie        : une de tes fautes revient : la définition est montrée, tu dois écrire le mot
                   EXACTEMENT. Toute faute (dans n'importe quel exercice) crée cette trace et la
                   carte revient trois cartes plus loin, puis à intervalles espacés.
  recep          : le MOT est montré → tu écris sa définition, en texte libre OU en synonymes
                   (« flatteur, laudateur »). Notation bge-m3 immédiate (température, dictionnaire
                   inversé), puis Claude/Jev en fin de session.
  prod           : la DÉFINITION est montrée → tu écris le mot. Tab révèle une lettre de plus :
                   le nombre de lettres nécessaires mesure la profondeur du « mot sur le bout de la langue ».
  discrimination : une définition, quatre mots d'une même grappe de confusables → lequel ?

Après chaque réponse : révélation (sens, voisins confondables et ce qui les distingue), puis
auto-évaluation 1–4 (Entrée = accepter la note proposée). Tout est écrit au journal ; FSRS est rejoué.

L'ordre des cartes est décidé carte après carte par le planificateur (lem/memory/planificateur.py) :
  * un mot nouveau est d'abord SONDÉ (mot → sens ; Entrée seule = « je ne sais pas ») : connu, il ne coûte
    qu'une carte et revient une seule fois, dans 1 à 2 mois ; sinon il entre en apprentissage : retour du
    sens aux étapes FSRS, duel quelques cartes plus loin, « sens → mot » plus loin encore ;
  * une carte ratée revient dans la session (étapes FSRS), jamais collée à une carte du même mot ;
  * le nombre de mots nouveaux suit la charge de révisions à venir ;
  * l'intention (équilibre, découvrir, consolider, confusables, graphie) règle ces dosages.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta

from lem.confusables import IndexFormel, grappe
from lem.db import iso, now
from lem.games.bombe import TYPES_HORS_JEU
from lem.games.base import PARAMS_FILTRES, Jeu, Param, enregistrer, filtres_depuis
from lem.games.pool import selectionner
from lem.grading.direct import VerdictProd, VerdictRecep, noter_prod, noter_recep
from lem.journal import Evenement, Frappe, ajouter_notation, ecrire, nettoyer_saisie
from lem.memory import scheduler
from lem.memory.knowledge import Y_PAR_NOTE, CarteConnaissance, score_apprentissage
from lem.memory import planificateur as plan_mod
from lem.memory.planificateur import Planificateur, Tache
from lem.orthographe import Diagnostic, accents_manquants, accents_tolerants, diagnostiquer, enregistrer_faute

CARTES = enregistrer(Jeu(
    id="cartes",
    nom="Cartes",
    accroche="Définir, retrouver, distinguer.",
    description="Répétition espacée FSRS : mot → sens, sens → mot, et duels de confusables.",
    params=[
        Param("intention", "Intention", str, "equilibre",
              "equilibre | decouvrir (plus de mots nouveaux) | consolider (révisions seules, fragiles d'abord) | "
              "confusables (mots proches de ceux que tu apprends) | graphie (tes fautes)",
              choix=("equilibre", "decouvrir", "consolider", "confusables", "graphie")),
        Param("minutes", "Durée visée (min)", int, 15, "règle le nombre de mots nouveaux selon la charge", 3, 240),
        Param("exercice", "Exercice", str, "mixte", "recep | prod | mixte",
              choix=("recep", "prod", "mixte")),
        Param("jugement_differe", "Jugement en file", bool, True,
              "tu définis, Entrée, la carte suivante vient tout de suite : Claude juge en arrière-plan, son verdict "
              "s'affiche sous la carte d'après et reste au journal", alias=("file",)),
        Param("confusables", "Duels de confusables", bool, True, "ajoute un duel à chaque mot appris"),
        Param("graphie", "Reprise des fautes de graphie", bool, True, "tes fautes reviennent jusqu'à être justes"),
        Param("nouveaux", "Mots nouveaux (max)", int, 20, "plafond ; la charge de révisions décide en dessous", 0, 200),
        Param("revisions_max", "Révisions max", int, 150, "", 0, 2000),
        Param("noms_propres", "Noms propres et sigles", bool, False, "les apprendre aussi (prénoms, lieux, sigles)"),
        Param("diversite", "Diversité des mots nouveaux", float, 0.3, "0 = aucune ; chaque mot choisi rend moins "
              "probables ses cousins de morphème (assez de -iser : la session ne doit pas se ressembler)", 0, 0.95),
        *PARAMS_FILTRES,
    ],
    ephemeres=("intention",),
))


@dataclass
class Item:
    exercice: str                 # recep | prod | discrimination | graphie
    entree_id: int
    sens_id: int
    forme: str
    pos: str
    definition: str
    voisins: list[dict] = field(default_factory=list)     # [{mot, definition, proximite}]
    voisins_differes: bool = False                        # grappe à calculer quand la carte sort
    options: list[tuple[int, str]] = field(default_factory=list)
    nouveau: bool = False
    indices: int = 0
    debut: object = None
    role: str = "revision"        # sonde | apprentissage | revision | verification | reprise
    tache: object = None          # la tâche du planificateur qui a produit la carte


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
    fautes: list[Diagnostic] = field(default_factory=list)    # à retaper juste avant de continuer


class PartieCartes:
    def __init__(self, con: sqlite3.Connection, params: dict, carte: CarteConnaissance | None = None,
                 embedder=None, magasin_sens=None, magasin_entrees=None, session_id: int | None = None,
                 rng: random.Random | None = None, lexique=None):
        self.con, self.params = con, params
        self.carte = carte or CarteConnaissance(con)
        self.embedder, self.magasin_sens, self.magasin_entrees = embedder, magasin_sens, magasin_entrees
        self.session_id = session_id
        self.rng = rng or random.Random()
        self.index = IndexFormel(con, lexique=lexique)
        self.construire_file()

    # -- file ---------------------------------------------------------------
    def _sens_principal(self, entree_id: int, sens_id: int = 0) -> sqlite3.Row | None:
        # Un sens sans définition (gabarit du Wiktionnaire retiré) ne fait jamais une carte.
        if sens_id:
            s = self.con.execute("SELECT * FROM sens WHERE id = ? AND definition != ''", (sens_id,)).fetchone()
            if s:
                return s
        return self.con.execute("SELECT * FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang LIMIT 1",
                                (entree_id,)).fetchone()

    def _sens_indice(self, entree_id: int, forme: str, prefere: int = 0) -> sqlite3.Row | None:
        """The sense to show as a clue when the player must write the word: the preferred one if it is a good
        clue (lem.definitions), else the first sense that is. None: the word gets recognition cards only."""
        from lem.definitions import first_usable_sense

        lignes = self.con.execute("SELECT id, definition FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang",
                                  (entree_id,)).fetchall()
        lignes = sorted(lignes, key=lambda r: r["id"] != prefere)
        choix = first_usable_sense([(r["id"], r["definition"]) for r in lignes], forme)
        return self.con.execute("SELECT * FROM sens WHERE id = ?", (choix[0],)).fetchone() if choix else None

    def _voisins(self, entree_id: int) -> list[dict]:
        voisins = []
        for c in grappe(self.con, entree_id, self.magasin_entrees, self.index, k=5):
            d = self._sens_principal(c.entree_id)
            if d:
                voisins.append({"mot": c.forme, "definition": d["definition"], "proximite": c.comparaison.proximite.value})
        return voisins

    def _item(self, exercice: str, entree_id: int, sens_id: int = 0, nouveau: bool = False,
              voisins: bool = True) -> Item | None:
        """voisins=False : grappe de confusables calculée seulement quand la carte sort (`suivant`)."""
        e = self.con.execute("SELECT forme, pos FROM entree WHERE id = ?", (entree_id,)).fetchone()
        s = self._sens_principal(entree_id, sens_id)
        if e is None or s is None:
            return None
        if exercice in ("prod", "graphie"):          # the clue must point to ONE word (« Microphone. » → micro: no)
            s = self._sens_indice(entree_id, e["forme"], s["id"])
            if s is None:
                return None
        it = Item(exercice, entree_id, s["id"], e["forme"], e["pos"],
                  s["definition_precise"] or s["definition"], self._voisins(entree_id) if voisins else [], nouveau=nouveau)
        it.voisins_differes = not voisins
        return it

    def construire_file(self) -> None:
        """Remplit les réservoirs du planificateur : révisions dues, mots nouveaux classés, budget."""
        p = self.params
        f = filtres_depuis(p)
        intention = p.get("intention", "equilibre")
        filtre_actif = any([f.domaines, f.origines, f.types, f.etat != "tous", f.zipf_min, f.zipf_max])
        vivier = selectionner(self.con, f)
        ids = {r["id"] for r in vivier} if filtre_actif else None
        traces = ("recep", "prod") if p["exercice"] == "mixte" else (p["exercice"],)
        if intention == "graphie":
            traces = ("ortho", "prod")
        elif p.get("graphie", True):
            traces = (*traces, "ortho")

        # Ni prénoms, ni lieux, ni sigles, sauf si on le demande — ni en mots nouveaux, ni en révision.
        exclus = () if p.get("noms_propres") else TYPES_HORS_JEU
        self._hors_jeu = {r[0] for r in self.con.execute(
            f"SELECT id FROM entree WHERE type IN ({', '.join('?' * len(exclus))})", exclus)} if exclus else set()
        # Des mots, pas des locutions (« chaudière à œufs », « détournement de mineur »), sauf /locutions oui.
        if not p.get("locutions"):
            self._hors_jeu |= {r[0] for r in self.con.execute(
                "SELECT id FROM entree WHERE type = 'expression' OR instr(forme, ' ') > 0")}
        vivier = [r for r in vivier if r["type"] not in exclus]
        from lem.games.pool import secondaires

        self._hors_jeu |= secondaires(self.con)            # feminine / plural entries, spelling variants: never due

        # Révisions : dues maintenant (consolider : aussi les fragiles des deux jours à venir).
        quand = now() + timedelta(days=2) if intention == "consolider" else now()
        revisions = []
        for t in scheduler.dues(self.con, quand, limite=p["revisions_max"], traces=traces, entree_ids=ids):
            if t.entree_id in self._hors_jeu:
                continue
            if intention == "decouvrir" and t.retrievabilite > 0.85 and t.statut is None:
                continue                                   # découvrir : seulement les révisions qui pressent
            role = "verification" if t.statut == "connu_sonde" else "revision"
            revisions.append(Tache("graphie" if t.trace == "ortho" else t.trace, t.entree_id, t.sens_id,
                                   role, r=t.retrievabilite))
        revisions = _espacer_soeurs(revisions)

        # Mots ouverts lors d'une session précédente (sonde ratée) dont la carte « sens → mot » n'a pas eu le
        # temps de venir : elle ouvre la session suivante, au lieu d'être perdue.
        if "prod" in traces and intention != "consolider":
            for (e, s_id) in self.con.execute(
                    """SELECT t.entree_id, t.sens_id FROM trace_etat t WHERE t.trace = 'recep' AND t.statut IS NULL
                       AND NOT EXISTS (SELECT 1 FROM trace_etat u WHERE u.entree_id = t.entree_id AND u.trace = 'prod')
                       ORDER BY t.maj LIMIT ?""", (p["revisions_max"],)):
                if e not in self._hors_jeu and (ids is None or e in ids):
                    revisions.append(Tache("prod", e, s_id, "apprentissage", nouveau=True, r=0.5))

        # Budget de mots nouveaux : la capacité de la session moins la charge de révisions à venir.
        capacite = p.get("minutes", 15) * 60 / plan_mod.SECONDES_PAR_CARTE
        charge = scheduler.charge(self.con, 7)
        budget = max(0, min(p["nouveaux"], round((capacite - charge) / plan_mod.COUT_MOT)))
        if intention == "decouvrir":
            budget = p["nouveaux"]
        elif intention in ("consolider", "graphie"):
            budget = 0

        candidats = self._classer_candidats(vivier, intention, 6 * max(budget, 1)) if budget else []
        directions = {"mixte": ("recep", "prod"), "recep": ("recep",), "prod": ("prod",)}[p["exercice"]]
        self.plan = Planificateur(revisions, candidats, budget, duels=bool(p["confusables"]), directions=directions)

    def _classer_candidats(self, vivier, intention: str, n: int) -> list[int]:
        """Les mots nouveaux les plus utiles à sonder, du plus au moins prioritaire."""
        deja = {r[0] for r in self.con.execute("SELECT DISTINCT entree_id FROM trace_etat")}
        cands = [r for r in vivier if r["id"] not in deja]
        if not cands:
            return []
        p_c = dict(zip((r["id"] for r in cands), self.carte.p_de(r["id"] for r in cands)))
        cands = [r for r in cands if p_c[r["id"]] < 0.9]
        if intention == "confusables":
            proches = _proches_des_mots_en_cours(self.con)
            cands = [r for r in cands if r["id"] in proches] or cands
        reveles = dict(self.con.execute(
            """SELECT entree_id, COUNT(*) FROM evenement WHERE type = 'revelation' AND entree_id IS NOT NULL
               GROUP BY entree_id""").fetchall())
        levier = _levier_morphemes(self.con)
        interets = _interets(self.con)
        dom = _domaines_de(self.con, [r["id"] for r in cands]) if interets else {}

        def valeur(r):
            interet = max((interets.get(d, 1.0) for d in dom.get(r["id"], ())), default=1.0)
            v = score_apprentissage(p_c[r["id"]], r["zipf"], interet, levier.get(r["id"], 0),
                                    exploration=plan_mod.EXPLORATION)
            return v * (1 + 0.5 * reveles.get(r["id"], 0))

        cands.sort(key=valeur, reverse=True)
        return self._diversifier(cands, valeur, n)

    def _diversifier(self, cands, valeur, n: int) -> list[int]:
        """Choix glouton : chaque mot retenu rend moins probables, par `diversite`, ceux qui partagent un morphème
        avec lui. Sans cela, le levier des morphèmes met en tête des dizaines de mots de la même famille
        (érotiser, corporaliser, particulariser…) et la session se ressemble de bout en bout."""
        d = self.params.get("diversite", 0.6)
        if not d or len(cands) <= 1:
            return [r["id"] for r in cands[:n]]
        top = cands[: 4 * n]
        ids = [r["id"] for r in top]
        morphs: dict[int, list[int]] = {}
        for a in range(0, len(ids), 900):
            lot = ids[a:a + 900]
            for e, m in self.con.execute(
                    f"SELECT entree_id, morpheme_id FROM entree_morpheme WHERE entree_id IN ({', '.join('?' * len(lot))})", lot):
                morphs.setdefault(e, []).append(m)
        base = {r["id"]: valeur(r) for r in top}
        vus: dict[int, int] = {}
        choisis: list[int] = []
        restants = list(top)
        while restants and len(choisis) < n:
            meilleur = max(restants, key=lambda r: base[r["id"]] * (1 - d) ** sum(vus.get(m, 0) for m in morphs.get(r["id"], ())))
            restants.remove(meilleur)
            choisis.append(meilleur["id"])
            for m in morphs.get(meilleur["id"], ()):
                vus[m] = vus.get(m, 0) + 1
        return choisis

    def _item_de(self, t: Tache) -> Item | None:
        it = self._item(t.exercice if t.exercice != "discrimination" else "recep", t.entree_id, t.sens_id,
                        nouveau=t.nouveau or t.role in ("sonde", "apprentissage"), voisins=False)
        if it is None:
            return None
        it.role, it.tache = t.role, t
        if t.exercice == "discrimination":
            it.voisins, it.voisins_differes = self._voisins(t.entree_id), False
            d = self._duel(it)
            if d is None:
                return None
            d.role, d.tache = t.role, t
            return d
        return it

    @property
    def file(self) -> list[Item]:
        """Aperçu des prochaines cartes (ordre approximatif), sans effet sur la session."""
        out = []
        for t in self.plan.apercu():
            it = self._item(t.exercice if t.exercice != "discrimination" else "recep", t.entree_id, t.sens_id,
                            nouveau=t.nouveau, voisins=False)
            if it:
                it.exercice, it.role = t.exercice, t.role
                out.append(it)
        return out

    def _duel(self, it: Item) -> Item | None:
        rivaux = [(v["mot"], v) for v in it.voisins[:3]]
        if not rivaux:
            return None
        indice = self._sens_indice(it.entree_id, it.forme, it.sens_id)
        if indice is None:                           # no definition that points to one word: no duel either
            return None
        it.sens_id, it.definition = indice["id"], indice["definition_precise"] or indice["definition"]
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
        while True:
            t = self.plan.prochaine(now().timestamp())
            if t is None:
                return None
            it = self._item_de(t)
            if it is not None:
                break
        if it.voisins_differes:
            it.voisins, it.voisins_differes = self._voisins(it.entree_id), False
        it.debut = now()
        ecrire(self.con, self.session_id, Evenement("cartes", "stimulus", entree_id=it.entree_id, sens_id=it.sens_id,
                                                    trace=None, data={"exercice": it.exercice, "nouveau": it.nouveau,
                                                                      "role": it.role}))
        return it

    def indice(self, it: Item) -> str:
        """Révèle une lettre de plus du mot (exercice prod)."""
        it.indices = min(len(it.forme), it.indices + 1)
        return it.forme[: it.indices] + "·" * (len(it.forme) - it.indices)

    def repondre(self, it: Item, texte: str, latence_ms: int, frappe: Frappe | None = None) -> Resultat:
        frappe = frappe or Frappe()
        if it.exercice in ("prod", "graphie"):
            texte = nettoyer_saisie(texte)
        if it.exercice == "graphie" and frappe.dictee:
            raise ValueError("la graphie se tape au clavier : une dictée écrit à ta place")
        sens = [(r["id"], r["definition"]) for r in
                self.con.execute("SELECT id, definition FROM sens WHERE entree_id = ? AND definition != '' ORDER BY rang", (it.entree_id,))]
        data = {"exercice": it.exercice, "voisins": it.voisins, "indices": it.indices, "nouveau": it.nouveau,
                "saisie": frappe.mode, "role": it.role}
        recep = prod = None
        fautes: list[Diagnostic] = []
        if it.exercice == "graphie":
            correct = texte.strip().lower() == it.forme.lower() or (
                accents_tolerants(self.con) and accents_manquants(texte, it.forme))
            note = 3 if correct and not it.indices else 1
            trace = "ortho"
            diag = None if correct else diagnostiquer(self.con, texte, cible_id=it.entree_id)
            data["graphie"] = diag.resume() if diag else None
            if not correct:
                fautes.append(diag or Diagnostic(texte, it.forme, it.entree_id, -1, ["inconnu"], False, 0.0))
        elif it.exercice == "recep":
            recep = noter_recep(self.con, it.entree_id, texte, self.embedder, self.magasin_sens, self.magasin_entrees)
            note, correct = recep.note_auto, None if recep.note_auto is None else recep.note_auto >= 2
            data.update({"temperature": recep.temperature, "sens_vise_id": recep.sens_vise_id,
                         "synonymes_reconnus": recep.synonymes_reconnus,
                         "mot_reellement_defini": recep.mot_reellement_defini[1] if recep.mot_reellement_defini else None})
            trace = "recep"
        elif it.exercice == "prod":
            prod = noter_prod(self.con, it.entree_id, texte, self.magasin_entrees)
            if frappe.dictee and prod.mode == "graphie":
                # Mot retrouvé ; la graphie est celle de Whisper, pas la tienne : ni faute, ni preuve.
                prod = VerdictProd(True, "dicte", it.entree_id, None, 3)
            note, correct = prod.note_auto, prod.correct
            if correct and it.indices:
                note = min(note, 2 if it.indices <= len(it.forme) // 3 else 1)
            elif correct and note == 3 and _rapide(latence_ms, frappe, len(it.forme)):
                note = 4                                   # retrouvé d'emblée, sans hésitation : facile
            data.update({"mode": prod.mode, "proximite": prod.comparaison.proximite.value if prod.comparaison else None})
            trace = "prod"
            if prod.graphie:
                fautes.append(prod.graphie)
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
        if recep and not frappe.dictee:              # fautes d'une dictée = fautes de Whisper
            fautes += recep.fautes
        eid = ecrire(self.con, self.session_id, Evenement(
            "cartes", "reponse", entree_id=it.entree_id, sens_id=None if trace == "ortho" else it.sens_id, trace=trace,
            reponse=texte, correct=correct, note_auto=note, latence_ms=latence_ms, frappe=frappe, data=data))
        for d in fautes:
            if it.exercice != "graphie":          # en exercice graphie, la réponse elle-même est la trace ortho
                enregistrer_faute(self.con, self.session_id, "cartes", d)
            self._insister(d)
        if it.exercice == "prod" and prod and not prod.correct and prod.entree_donnee:
            from lem.similarite import enregistrer_confusion
            enregistrer_confusion(self.con, it.entree_id, prod.entree_donnee)
        if it.exercice == "discrimination" and not correct and data.get("choisi"):
            from lem.similarite import enregistrer_confusion
            enregistrer_confusion(self.con, it.entree_id, data["choisi"])
        return Resultat(it, texte, correct, note, eid, recep, prod, sens, fautes)

    def _insister(self, d: Diagnostic) -> None:
        """Remet le mot mal écrit trois cartes plus loin (une seule fois par session et par mot)."""
        if not self.params.get("noms_propres") and self.con.execute(
                "SELECT type FROM entree WHERE id = ?", (d.entree_id,)).fetchone()[0] in TYPES_HORS_JEU:
            return
        self.plan.ajouter(Tache("graphie", d.entree_id, 0, "reprise", apres=self.plan.n + 3))

    def recopier(self, res: Resultat, attendu: str, texte: str, latence_ms: int) -> bool:
        """Après une faute : tu retapes la bonne graphie. Journalisé, hors FSRS."""
        ok = texte.strip().lower() == attendu.lower() or (accents_tolerants(self.con) and accents_manquants(texte, attendu))
        ecrire(self.con, self.session_id, Evenement(
            "cartes", "recopie", entree_id=res.item.entree_id, reponse=texte, correct=ok, latence_ms=latence_ms,
            data={"attendu": attendu, "fsrs": False}))
        return ok

    def valider(self, res: Resultat, note_soi: int | None = None) -> str | None:
        """Clôt une carte : auto-évaluation éventuelle, carte de connaissance, FSRS, suite de la session.

        La note retenue est la meilleure disponible (scheduler.note_finale), comme au rejeu : un juge
        externe (Claude, juge local) passe avant l'auto-évaluation, qui passe avant le cosinus.
        Renvoie la voie du mot après une sonde (« connu » / « apprentissage »), sinon « suivi »."""
        it = res.item
        if note_soi is not None:
            ajouter_notation(self.con, res.evenement_id, "soi", note=note_soi,
                             brut={"proposee": res.note_proposee})
        note = scheduler.note_finale(self.con, res.evenement_id, res.note_proposee)
        if it.exercice != "graphie":                               # la graphie n'informe pas le sens
            y = None if note is None else Y_PAR_NOTE[note]
            # Sonde et vérification disent ce que tu savais sans étude : elles corrigent toute la famille du mot.
            # Une révision ou une étape d'apprentissage ne corrige que le mot.
            informe = it.role in ("sonde", "verification")
            self.carte.observer(it.entree_id, y, 1.5 if it.role == "sonde" else 1.0,
                                source=f"cartes:{it.role}:{it.exercice}", evenement_id=res.evenement_id,
                                depuis_note=True, propager=informe)
        if it.exercice != "discrimination":
            scheduler.reconstruire(self.con, [it.entree_id, *{d.entree_id for d in res.fautes}])
        voie = None
        if it.tache is not None:
            trace = {"graphie": "ortho", "discrimination": None}.get(it.exercice, it.exercice)
            sens = 0 if trace == "ortho" else it.sens_id
            ech = scheduler.echeance(self.con, it.entree_id, sens, trace) if trace else None
            voie = self.plan.apres_reponse(it.tache, now().timestamp(), note, ech.timestamp() if ech else None,
                                           a_des_voisins=bool(it.voisins))
        return voie

    def restant(self) -> int:
        return self.plan.restant()


def _rapide(latence_ms: int | None, frappe: Frappe, n_lettres: int) -> bool:
    """Réponse sans hésitation : première touche en moins d'1,5 s, mot entier en moins de 1,5 s + 0,25 s/lettre."""
    if latence_ms is None or frappe.dictee:
        return False
    pf = frappe.premiere_frappe_ms
    return latence_ms <= 1500 + 250 * n_lettres and (pf is None or pf <= 1500)


def _espacer_soeurs(revs: list[Tache]) -> list[Tache]:
    """Les traces d'un même mot (sens, mot, graphie) : la plus fragile d'abord, les autres en fin de file."""
    vus, tete, queue = set(), [], []
    for t in revs:
        (queue if t.entree_id in vus else tete).append(t)
        vus.add(t.entree_id)
    return tete + queue


def _levier_morphemes(con: sqlite3.Connection) -> dict[int, int]:
    """Pour chaque entrée : combien de mots encore inconnus partagent ses morphèmes."""
    return dict(con.execute(
        """WITH inconnus AS (
               SELECT em.morpheme_id, COUNT(*) AS n FROM entree_morpheme em
               JOIN connaissance c ON c.entree_id = em.entree_id WHERE c.p_connu < 0.5 GROUP BY em.morpheme_id)
           SELECT em.entree_id, SUM(i.n) FROM entree_morpheme em JOIN inconnus i ON i.morpheme_id = em.morpheme_id
           GROUP BY em.entree_id""").fetchall())


def _interets(con: sqlite3.Connection) -> dict[str, float]:
    """Poids d'intérêt par domaine (réglage « interets », ex. {"science": 2, "droit": 0.5}), sous-domaines compris."""
    from lem.db import domaines_descendants, get_reglage

    brut = get_reglage(con, "interets", {}) or {}
    out: dict[str, float] = {}
    for nom, w in brut.items():
        for did in domaines_descendants(con, [nom]):
            (n,) = con.execute("SELECT nom FROM domaine WHERE id = ?", (did,)).fetchone()
            out[n] = float(w)
    return out


def _domaines_de(con: sqlite3.Connection, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for i in range(0, len(ids), 900):
        bloc = ids[i:i + 900]
        for e, d in con.execute(
                f"""SELECT DISTINCT s.entree_id, d.nom FROM sens s JOIN sens_domaine sd ON sd.sens_id = s.id
                    JOIN domaine d ON d.id = sd.domaine_id WHERE s.entree_id IN ({', '.join('?' * len(bloc))})""", bloc):
            out.setdefault(e, []).append(d)
    return out


def _proches_des_mots_en_cours(con: sqlite3.Connection) -> set[int]:
    """Mots reliés (paronymes, confusions, synonymes) à ceux que tu apprends ou as confondus."""
    return {r[0] for r in con.execute(
        """SELECT r.cible_id FROM relation r JOIN trace_etat t ON t.entree_id = r.source_id
           WHERE r.type IN ('paronyme', 'confusion_perso', 'synonyme')
           UNION SELECT r.source_id FROM relation r JOIN trace_etat t ON t.entree_id = r.cible_id
           WHERE r.type IN ('paronyme', 'confusion_perso', 'synonyme')""")}


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
