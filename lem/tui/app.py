"""Interface terminal de lem (Textual).

Monochrome par défaut ; la couleur ne porte que du sens (cf. theme.py).
Accueil → menu aux flèches → réglages du jeu → partie. Tout écrit dans la base
au fil de l'eau ; les commandes « /… » changent les paramètres en cours de partie.
"""

from __future__ import annotations

import random
import re
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from lem.contexte import Contexte
from lem.db import now
from lem.games import REGISTRE
from lem.games.base import Jeu, executer_commande
from lem.grading.taxonomie import PRECISION
from lem.journal import Frappe, fermer_session, ouvrir_session
from lem.orthographe import GRAPHIE, Diagnostic, surligner
from lem.similarite import MESSAGES, Proximite
from lem.tui import logo
from lem.tui.theme import BLANC, CSS, GRIS, PROXIMITES, SOMBRE, barre, thermometre


class Chrono:
    """Mesure une réponse : latence, première frappe, frappes, effacements, dictée.

    Ta dictée (Whisper, F8 / Option droite) insère le texte d'un bloc : on le repère pour ne
    pas confondre ses mesures avec celles de la frappe, ni ses fautes avec les tiennes."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.t0 = time.monotonic()
        self.premiere: float | None = None
        self.frappes = 0
        self.effacements = 0
        self.blocs = 0
        self._long = 0

    def changement(self, valeur: str) -> None:
        if valeur and self.premiere is None:
            self.premiere = time.monotonic()
        delta = len(valeur) - self._long
        if delta < 0:
            self.effacements += 1
        elif delta >= 4:
            self.blocs += 1
        else:
            self.frappes += 1
        self._long = len(valeur)

    def mesures(self) -> tuple[int, Frappe]:
        lat = int((time.monotonic() - self.t0) * 1000)
        pf = None if self.premiere is None else int((self.premiere - self.t0) * 1000)
        return lat, Frappe(pf, self.frappes, self.effacements, self.blocs)


LIBELLES_FAUTE = {"accent": "accent", "consonne_double": "consonne double", "h_muet": "h muet",
                  "omission": "lettre manquante", "ajout": "lettre en trop", "substitution": "lettre erronée",
                  "inversion": "lettres inversées", "frappe": "touche voisine", "grapheme": "graphème",
                  "trait_union": "trait d'union", "inconnu": "graphie inconnue"}


def ligne_graphie(d: Diagnostic) -> Text:
    """✎ graphie  thuriféraire  ← tu as écrit t̶uriféraire  · h muet · orthographe"""
    bon, tape = surligner(d.saisie, d.correcte)
    t = Text("✎ graphie  ", style=f"bold {GRAPHIE}") + bon + Text("   tu as écrit ", style=GRIS) + tape
    t.append("   " + " · ".join(LIBELLES_FAUTE.get(x, x) for x in d.types), style=GRAPHIE)
    if d.distance >= 0:
        t.append(f" · {d.nature}", style=GRIS)
    return t


def ligne_proximite(prox: Proximite | None, mot: str = "") -> Text:
    if prox is None or prox == Proximite.AUCUNE:
        return Text("")
    return Text(f"● {MESSAGES[prox]}", style=f"bold {PROXIMITES[prox]}") + Text(f"  {mot}", style=GRIS)


# ── Accueil ─────────────────────────────────────────────────────────────────────────────────────

MENU = [
    ("cartes", "Cartes", "définir · retrouver · distinguer"),
    ("bombe", "Bombe", "des lettres, un mot, avant l'explosion"),
    ("syllabaire", "Syllabaire", "les suites rares de la Bombe, par cœur"),
    ("memoire", "Mémoire verbale", "vu ou nouveau ? entraînement au test, ou mots étranges"),
    ("mot_ou_pas", "Mot ou pas mot", "décision lexicale chronométrée"),
    ("etalonnage", "Étalonnage", "placer ta carte de connaissance"),
    ("atlas", "Atlas", "ta carte, tes confusions, ta signature"),
    ("noter", "Noter avec Claude", "notation différée des définitions"),
    ("quitter", "Quitter", ""),
]


def mots_recents(con, n: int = 12) -> list[str]:
    """Les mots des dernières parties (réponses, cibles, cartes), du plus récent au plus ancien, en capitales
    sans trait d'union ni espace : de quoi remplir les cubes du logo."""
    rows = con.execute(
        """SELECT e.forme FROM evenement ev JOIN entree e ON e.id = ev.entree_id
           WHERE ev.type IN ('reponse', 'revelation') AND e.type NOT IN ('nom_propre', 'sigle')
           ORDER BY ev.id DESC LIMIT 300""").fetchall()
    mots = [re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]", "", r[0]).upper() for r in rows]
    return list(dict.fromkeys(m for m in mots if len(m) >= 4))[:n]


JEUX_RAPIDES = ["cartes", "bombe", "syllabaire", "memoire", "mot_ou_pas", "etalonnage"]


def ecran_de(jeu_id: str):
    return {"cartes": EcranCartes, "bombe": EcranBombe, "syllabaire": EcranSyllabaire, "memoire": EcranMemoire,
            "mot_ou_pas": EcranDecision, "etalonnage": EcranDecision}[jeu_id]


class Accueil(Screen):
    BINDINGS = [Binding("q", "app.quit", "Quitter")]

    def compose(self) -> ComposeResult:
        with Vertical():
            with Center():
                yield Static(id="logo")
            yield Static("apprendre le lexique français entier — en sachant ce que tu sais", classes="sous-titre")
            yield Static(id="stats", classes="sous-titre")
            with Center():
                yield OptionList(*self._options(), id="menu")
        yield Footer()

    def _options(self) -> list[Option]:
        out = []
        for cle, nom, desc in MENU:
            num = f"{JEUX_RAPIDES.index(cle) + 1} " if cle in JEUX_RAPIDES else "  "
            t = Text(num, style=SOMBRE) + Text(f"{nom:<20}", style="bold") + Text(desc, style=GRIS)
            out.append(Option(t, id=cle))
        return out

    def on_mount(self) -> None:
        self._rng = random.Random(7)
        self._t = 0
        self._mots = logo.mots_pour_logo(mots_recents(self.app.ctx.con))
        self._anim = self.set_interval(0.06, self._animer)
        self.query_one("#menu").focus()
        self.rafraichir()

    def _animer(self) -> None:
        self._t += 1
        p = min(1.0, self._t / 22)
        self.query_one("#logo", Static).update(logo.brouiller(p, self._rng, mots=self._mots) if p < 1
                                               else logo.rendre(mots=self._mots))
        if p >= 1:
            self._anim.stop()

    def rafraichir(self) -> None:
        s = self.app.ctx.stats()
        t = Text()
        t.append(f"{s['entrees']:,}".replace(",", " "), style=BLANC)
        t.append(" entrées · ", style=GRIS)
        t.append(f"~{s['connus_estimes']:,}".replace(",", " "), style=BLANC)
        t.append(" connues (estimé) · ", style=GRIS)
        t.append(str(s["dues"]), style=BLANC)
        t.append(" à réviser · ", style=GRIS)
        t.append(str(s["a_noter"]), style=BLANC)
        t.append(" à noter", style=GRIS)
        self.query_one("#stats", Static).update(t)

    def on_screen_resume(self) -> None:
        self.rafraichir()

    def on_key(self, ev) -> None:
        """1 à 6 : lance le jeu tout de suite, avec ses derniers réglages (Entrée ouvre les réglages)."""
        if ev.character and ev.character in "123456" and int(ev.character) <= len(JEUX_RAPIDES):
            ev.stop()
            jeu = REGISTRE[JEUX_RAPIDES[int(ev.character) - 1]]
            self.app.push_screen(ecran_de(jeu.id)(jeu, dict(self.app.ctx.params(jeu))))

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        cle = ev.option.id
        if cle == "quitter":
            self.app.exit()
        elif cle == "atlas":
            self.app.push_screen(EcranAtlas())
        elif cle == "noter":
            self.app.push_screen(EcranNoter())
        else:
            self.app.push_screen(Reglages(REGISTRE[cle]))


# ── Réglages ────────────────────────────────────────────────────────────────────────────────────

class Reglages(Screen):
    BINDINGS = [Binding("escape", "retour", "Retour"), Binding("j", "jouer", "Jouer")]

    def __init__(self, jeu: Jeu):
        super().__init__()
        self.jeu = jeu
        self.params: dict = {}
        self.edite = None

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(logo.rangee(self.jeu.nom[:12].replace(" ", "·")), id="titre")
                yield Static(Text(self.jeu.description, style=GRIS))
                yield Static("")
                yield OptionList(id="params")
                yield Input(id="edition", placeholder="nouvelle valeur — Entrée pour valider, Échap pour annuler")
                yield Static(id="aide", classes="discret")
        yield Footer()

    def on_mount(self) -> None:
        self.params = self.app.ctx.params(self.jeu)
        self.query_one("#edition").display = False
        self._remplir()
        self.query_one("#params").focus()

    def _remplir(self) -> None:
        ol = self.query_one("#params", OptionList)
        ol.clear_options()
        ol.add_option(Option(Text("▶  Jouer", style="bold"), id="__jouer"))
        for p in self.jeu.params:
            t = Text(f"   {p.libelle:<30}", style=GRIS) + Text(p.afficher(self.params.get(p.nom)), style=BLANC)
            ol.add_option(Option(t, id=p.nom))

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        p = self.jeu.param(ev.option.id or "")
        aide = ""
        if p:
            aide = p.aide or ""
            if p.nom == "domaine":
                from lem.games.pool import domaines_disponibles

                dispo = [f"{n} ({k})" for n, k in domaines_disponibles(self.app.ctx.con)[:12] if k]
                aide = "disponibles : " + ", ".join(dispo)
            if p.choix:
                aide = (aide + " · Entrée fait défiler : " + " / ".join(p.choix)).strip(" ·")
        self.query_one("#aide", Static).update(aide)

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        if ev.option.id == "__jouer":
            self.action_jouer()
            return
        p = self.jeu.param(ev.option.id)
        if p.type is bool:
            self.params[p.nom] = not self.params.get(p.nom)
        elif p.choix:
            i = p.choix.index(self.params.get(p.nom)) if self.params.get(p.nom) in p.choix else -1
            self.params[p.nom] = p.choix[(i + 1) % len(p.choix)]
        else:
            self.edite = p
            champ = self.query_one("#edition", Input)
            champ.value = p.afficher(self.params.get(p.nom)).replace("—", "")
            champ.display = True
            champ.focus()
            return
        self._sauver()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        p, self.edite = self.edite, None
        champ = self.query_one("#edition", Input)
        champ.display = False
        if p is not None:
            try:
                self.params[p.nom] = None if ev.value.strip() == "" and p.type in (int, float) and p.defaut is None \
                    else p.convertir(ev.value)
                self._sauver()
            except ValueError as exc:
                self.query_one("#aide", Static).update(Text(str(exc), style=PROXIMITES[Proximite.JUMEAU]))
        self.query_one("#params").focus()

    def _sauver(self) -> None:
        self.app.ctx.sauver_params(self.jeu, self.params)
        idx = self.query_one("#params", OptionList).highlighted
        self._remplir()
        self.query_one("#params", OptionList).highlighted = idx

    def action_retour(self) -> None:
        if self.edite is not None:
            self.edite = None
            self.query_one("#edition").display = False
            self.query_one("#params").focus()
        else:
            self.app.pop_screen()

    def action_jouer(self) -> None:
        self.app.push_screen(ecran_de(self.jeu.id)(self.jeu, dict(self.params)))


# ── Socle des parties ───────────────────────────────────────────────────────────────────────────

class EcranPartie(Screen):
    BINDINGS = [Binding("escape", "quitter", "Menu")]

    def __init__(self, jeu: Jeu, params: dict):
        super().__init__()
        self.jeu, self.params = jeu, params
        self.chrono = Chrono()
        self.session_id: int | None = None
        self.partie = None
        self.occupe = False          # un calcul tourne en fond : saisies ignorées, bombe en pause

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(id="entete")
                yield Static(id="stimulus")
                yield Input(id="saisie", placeholder="ta réponse — /aide pour les commandes")
                yield Static(id="retour")
                yield VerticalScroll(Static(id="detail"))
        yield Footer()

    @property
    def ctx(self) -> Contexte:
        return self.app.ctx

    def on_mount(self) -> None:
        self.session_id = ouvrir_session(self.ctx.con, self.jeu.id, self.params)
        self.maj("stimulus", Text("chargement…", style=SOMBRE))
        self.en_fond(self.construire, self._construite)

    def construire(self):
        """Construit la partie (lourd sur la vraie base : vivier, formes, carte). Appelé hors de l'interface."""

    def _construite(self, partie) -> None:
        self.partie = partie
        self.demarrer()
        self.query_one("#saisie").focus()

    def en_fond(self, calcul, puis) -> None:
        """Lance `calcul()` dans un fil à part, puis `puis(résultat)` dans l'interface.

        Sur la vraie base (550 k entrées, 2 M formes), construire une partie ou juger une réponse prend
        de 0,3 à 6 s : dans le fil de l'interface, l'écran gelait. Un seul calcul à la fois (`occupe`)."""
        self.occupe = True

        def tache():
            try:
                r = calcul()
            except Exception as exc:          # remonté dans l'interface, pas perdu dans le fil
                self.app.call_from_thread(self._echec_fond, exc)
                return
            self.app.call_from_thread(self._fin_fond, puis, r)

        self.run_worker(tache, thread=True, group="calcul")

    def _fin_fond(self, puis, r) -> None:
        self.occupe = False
        if self.is_mounted:
            puis(r)

    def _echec_fond(self, exc: Exception) -> None:
        self.occupe = False
        raise exc

    @property
    def pret(self) -> bool:
        return self.partie is not None and not self.occupe

    def on_unmount(self) -> None:
        if self.session_id:
            fermer_session(self.ctx.con, self.session_id)

    def demarrer(self) -> None: ...

    def reconfigurer(self) -> None: ...

    def maj(self, id_: str, contenu) -> None:
        self.query_one(f"#{id_}", Static).update(contenu)

    def on_input_changed(self, ev: Input.Changed) -> None:
        if ev.value and not ev.value.startswith("/"):     # "" = champ vidé par le programme
            self.chrono.changement(ev.value)

    def commande(self, texte: str) -> bool:
        """Traite « /… ». Renvoie True si la saisie était une commande."""
        if not texte.startswith("/"):
            return False
        if texte.strip("/ ").lower() in {"quitter", "q", "menu"}:
            self.action_quitter()
            return True
        if texte.strip("/ ").lower().split()[:1] == ["accents"]:
            self._accents(texte.strip("/ ").split()[1:])
            self.query_one("#saisie", Input).value = ""
            return True
        if texte.strip("/ ").lower().split()[:1] == ["mot"]:
            self._mot(texte.strip("/ ").split(maxsplit=1)[1:])
            self.query_one("#saisie", Input).value = ""
            return True
        if texte.strip("/ ").lower().split()[:1] == ["juge"]:
            self._juge(texte.strip("/ ").split()[1:])
            self.query_one("#saisie", Input).value = ""
            return True
        res = executer_commande(self.jeu, self.params, texte)
        style = BLANC if res.ok else PROXIMITES[Proximite.JUMEAU]
        self.maj("retour", Text(res.message, style=style))
        if res.change:
            self.ctx.sauver_params(self.jeu, self.params, en_partie=True)
            if self.pret:
                self.reconfigurer()
        self.query_one("#saisie", Input).value = ""
        return True

    def _accents(self, args: list[str]) -> None:
        """/accents oui|non — réglage global, pour tous les jeux : accents manquants tolérés ou non."""
        from lem.db import set_reglage
        from lem.orthographe import accents_tolerants

        if args:
            if args[0].lower() not in {"oui", "non"}:
                self.maj("retour", Text("/accents oui | non", style=GRIS))
                return
            set_reglage(self.ctx.con, "accents_tolerants", args[0].lower() == "oui")
            self.ctx.con.commit()
            if self.partie is not None and hasattr(self.partie, "accents_tolerants"):
                self.partie.accents_tolerants = args[0].lower() == "oui"
        etat = accents_tolerants(self.ctx.con)
        self.maj("retour", Text("accents manquants : " + ("tolérés (pas une faute)" if etat else "comptés comme fautes"),
                                style=BLANC))

    def _mot(self, args: list[str]) -> None:
        """/mot <forme> — les six mémoires d'un mot (lem.profil)."""
        from lem.profil import bande, profils

        if not args:
            self.maj("retour", Text("/mot <forme>", style=GRIS))
            return
        ids = [r[0] for r in self.ctx.con.execute(
            "SELECT DISTINCT entree_id FROM forme WHERE forme = ? LIMIT 4", (args[0].strip().lower(),))]
        if not ids:
            self.maj("retour", Text(f"« {args[0]} » n'est pas dans le lexique", style=GRIS))
            return
        t = Text()
        for i, p in enumerate(profils(self.ctx.con, ids).values()):
            if i:
                t.append("\n")
            t.append_text(bande(p))
        self.maj("retour", t)

    def _juge(self, args: list[str]) -> None:
        """/juge auto | haiku | sonnet | <modèle Ollama> | non — juge en direct des définitions (réglage global)."""
        from lem.db import set_reglage
        from lem.grading.local import modele_reglage

        if args:
            set_reglage(self.ctx.con, "juge_local", args[0])
            self.ctx.con.commit()
            self.ctx.__dict__.pop("juge", None)        # relu au prochain usage
        m, j = modele_reglage(self.ctx.con), self.ctx.juge
        if not m:
            etat = "coupé"
        elif j:
            etat = f"{m} → {j.correcteur}"
        else:
            etat = f"{m} — indisponible (ni `claude` ni le modèle Ollama)"
        self.maj("retour", Text(f"juge : {etat}", style=BLANC))

    def action_quitter(self) -> None:
        self.app.pop_screen()


# ── Cartes ──────────────────────────────────────────────────────────────────────────────────────

class EcranCartes(EcranPartie):
    BINDINGS = [Binding("escape", "quitter", "Menu"), Binding("tab", "indice", "Indice (lettre)", priority=True),
                Binding("up", "choix(-1)", "Choix", show=False, priority=True),
                Binding("down", "choix(1)", "Choix", show=False, priority=True)]
    AVANCE_AUTO = 0.8          # secondes de ✓ à l'écran avant la carte suivante, quand le mot est juste

    def construire(self):
        from lem.games.cartes import PartieCartes

        return PartieCartes(self.ctx.con, self.params, self.ctx.carte, self.ctx.embedder,
                            self.ctx.magasin_sens, self.ctx.magasin_entrees, self.session_id, lexique=self.ctx.lexique)

    def demarrer(self) -> None:
        self.item = None
        self.resultat = None
        self.faits = 0
        self.debut_session = now()
        self.suivant()

    def reconfigurer(self) -> None:
        self.partie.params = self.params
        self.en_fond(self.partie.construire_file, lambda _: self.suivant())

    def _entete(self) -> None:
        from lem.games.base import filtres_depuis

        ex = {"recep": "mot → sens", "prod": "sens → mot", "discrimination": "duel de confusables",
              "graphie": "graphie exacte"}
        roles = {"sonde": "nouveau ?", "apprentissage": "en apprentissage", "verification": "vérification",
                 "reprise": "reprise", "revision": "révision"}
        plan = self.partie.plan
        t = Text("CARTES  ", style="bold") + Text(ex.get(self.item.exercice, ""), style=BLANC)
        t += Text(f"   {roles.get(self.item.role, '')}", style="#5f87d7" if self.item.role in ("sonde", "apprentissage")
                  else GRIS)
        t += Text(f"   {self.faits} faites · ~{self.partie.restant()} à venir", style=GRIS)
        if plan.connus or plan.entres:
            t += Text(f" · {plan.connus} connus · {plan.entres} appris", style=GRIS)
        resume = filtres_depuis(self.params).resume()
        if resume != "tout le lexique" or self.params.get("intention", "equilibre") != "equilibre":
            t += Text(f" · {self.params.get('intention', 'equilibre')} · {resume}", style=SOMBRE)
        self.maj("entete", t)

    def suivant(self) -> None:
        self.resultat = None
        self.recopies: list[str] = []
        self.essais = 0
        self.item = self.partie.suivant()
        saisie = self.query_one("#saisie", Input)
        saisie.value = ""
        self.maj("retour", "")
        self.maj("detail", "")
        if self.item is None:
            self._fin_de_session()
            return
        self._entete()
        it = self.item
        if it.exercice == "recep":
            st = logo.rangee(it.forme) if len(it.forme) <= 18 and " " not in it.forme \
                else Text(it.forme, style=f"bold {BLANC}")
            if it.role == "sonde":
                st += Text(f"\n{it.pos} — le connais-tu ? Définis-le ; Entrée seule : je ne sais pas", style=SOMBRE)
                saisie.placeholder = "définition, synonymes… ou Entrée"
            elif it.role == "verification":
                st += Text(f"\n{it.pos} — vérification : tu le connaissais il y a un mois ou deux", style=SOMBRE)
                saisie.placeholder = "définition ou synonymes…"
            else:
                st += Text(f"\n{it.pos} — définis-le, en phrase ou en synonymes séparés par des virgules", style=SOMBRE)
                saisie.placeholder = "définition ou synonymes…"
        elif it.exercice == "graphie":
            st = Text("✎ ta graphie revient\n\n", style=f"bold {GRAPHIE}") + Text(it.definition, style=BLANC)
            st += Text(f"\n\n{it.pos} · {len(it.forme)} lettres · sans aucune faute", style=SOMBRE)
            saisie.placeholder = "le mot, exactement…"
        elif it.exercice == "prod":
            st = Text(it.definition, style=BLANC) + Text(f"\n\n{it.pos} · {len(it.forme)} lettres · Tab = une lettre",
                                                         style=SOMBRE)
            saisie.placeholder = "le mot…"
        else:
            self.choix = 0
            st = self._options()
            saisie.placeholder = "↑ ↓ puis Entrée, ou un numéro…"
        self.maj("stimulus", st)
        self.chrono.reset()

    def _options(self) -> Text:
        it = self.item
        st = Text(it.definition + "\n\n", style=BLANC)
        for i, (_, f) in enumerate(it.options):
            choisi = i == self.choix
            st += Text(f" {'›' if choisi else ' '} {i + 1}  ", style=f"bold {BLANC}" if choisi else GRIS)
            st += Text(f"{f}\n", style=f"bold {BLANC}" if choisi else BLANC)
        return st

    def action_choix(self, pas: int) -> None:
        if self.pret and self.item and self.item.exercice == "discrimination" and self.resultat is None:
            self.choix = (self.choix + pas) % len(self.item.options)
            self.maj("stimulus", self._options())

    def action_indice(self) -> None:
        if self.pret and self.item and self.item.exercice == "prod" and self.resultat is None:
            self.maj("retour", Text("indice : ", style=GRIS) + Text(self.partie.indice(self.item), style=f"bold {BLANC}"))

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        texte = ev.value
        if self.commande(texte) or not self.pret or self.item is None:
            return
        ev.input.value = ""
        if self.resultat is None:
            lat, frappe = self.chrono.mesures()
            if self.item.exercice == "graphie" and frappe.dictee:
                self._clavier_exige()
                return
            if self.item.exercice == "discrimination" and not texte.strip():
                texte = str(self.choix + 1)                 # Entrée sur l'option choisie aux flèches
            self.resultat = self.partie.repondre(self.item, texte, lat, frappe)
            self.recopies = list(dict.fromkeys(d.correcte for d in self.resultat.fautes))
            self._reveler()
            self._demander_recopie()
            if self.item.exercice == "recep":
                self._juger(self.resultat)
            if self.resultat.correct and not self.recopies and self.item.exercice in ("prod", "graphie", "discrimination"):
                self._auto = self.set_timer(self.AVANCE_AUTO, lambda: self._avancer(None))
        elif self.recopies:
            self._recopie(texte)
        else:
            note = int(texte) if texte.strip() in {"1", "2", "3", "4"} and self.item.exercice != "graphie" else None
            self._avancer(note)

    def _juger(self, res) -> None:
        """Le juge local note la définition en fond ; l'écran reste libre, le verdict arrive ~1 s après."""
        from lem.grading.claude_headless import element

        juge = self.ctx.juge
        el = element(self.ctx.con, res.evenement_id) if juge else None
        if el is None:
            return
        res.juge_en_cours = True
        self._reveler()

        def tache():
            try:
                v = juge.juger(el)
            except Exception as exc:                     # Ollama absent, lent, hors schéma : on garde le cosinus
                self.app.call_from_thread(self._verdict_local, res, el, None, exc)
                return
            self.app.call_from_thread(self._verdict_local, res, el, v, None)

        self.run_worker(tache, thread=True, group="juge")

    def _verdict_local(self, res, el, v, exc) -> None:
        from lem.grading.claude_headless import appliquer
        from lem.grading.taxonomie import NOTE_PAR_PRECISION
        from lem.memory import scheduler

        res.juge_en_cours = False
        if v is None:
            if self.resultat is res and self.is_mounted:
                self._reveler()
            return
        appliquer(self.ctx.con, [el], [v], self.ctx.juge.correcteur)
        self.ctx.con.commit()
        if self.resultat is not res or not self.is_mounted:  # carte déjà passée : on rejoue FSRS pour ce mot
            scheduler.reconstruire(self.ctx.con, [el.entree_id])
            return
        res.note_proposee = NOTE_PAR_PRECISION[v.precision]  # Entrée = la note du juge
        res.verdict_local = v
        self._reveler()

    def _avancer(self, note: int | None) -> None:
        """Clôt la carte et passe à la suivante (une seule fois, qu'on ait tapé Entrée ou attendu)."""
        if getattr(self, "_auto", None) is not None:
            self._auto.stop()
            self._auto = None
        if self.resultat is None or not self.is_mounted:
            return
        voie = self.partie.valider(self.resultat, note)
        if voie == "connu":
            self.app.notify(f"« {self.resultat.item.forme} » : connu — une seule vérification, dans un mois ou deux",
                            timeout=3)
        self.faits += 1
        self.suivant()

    def _fin_de_session(self) -> None:
        """Bilan : ce que la session a fait, et comment continuer."""
        plan = self.partie.plan
        minutes = (now() - self.debut_session).total_seconds() / 60 if getattr(self, "debut_session", None) else 0
        self.maj("entete", Text("CARTES", style="bold") + Text("   session terminée", style=GRIS))
        t = logo.rangee("fin") + Text("\n\n")
        lignes = [(f"{self.faits}", "cartes"), (f"{plan.entres}", "mots appris (ils reviendront)"),
                  (f"{plan.connus}", "mots déjà connus (une vérification dans 1 à 2 mois)"),
                  (f"{minutes:.0f}", "minutes")]
        larg = max(len(lib) for _, lib in lignes)
        for n, lib in lignes:                               # lignes de même largeur : le bloc reste aligné
            t += Text(f"{n:>6}  ", style=f"bold {BLANC}") + Text(f"{lib.ljust(larg)}\n", style=GRIS)
        t += Text("\nContinuer : /nouveaux 10 · /intention decouvrir · /intention consolider — ou Échap.", style=SOMBRE)
        self.maj("stimulus", t)
        self.query_one("#saisie", Input).placeholder = "/commande, ou Échap"

    def _demander_recopie(self) -> None:
        saisie = self.query_one("#saisie", Input)
        if self.recopies:
            saisie.placeholder = f"retape la bonne graphie ({len(self.recopies[0])} lettres) — sans regarder si tu peux"
            self.chrono.reset()
        elif self.item.exercice == "graphie":
            saisie.placeholder = "Entrée pour continuer"
        else:
            saisie.placeholder = "1–4, ou Entrée"

    def _clavier_exige(self) -> None:
        self.maj("retour", Text("✎ la graphie se tape au clavier — une dictée écrit à ta place", style=f"bold {GRAPHIE}"))
        self.chrono.reset()

    def _recopie(self, texte: str) -> None:
        lat, frappe = self.chrono.mesures()
        if frappe.dictee:
            self._clavier_exige()
            return
        attendu = self.recopies[0]
        ok = self.partie.recopier(self.resultat, attendu, texte, lat)
        if ok:
            self.recopies.pop(0)
            self.essais = 0
            self.maj("retour", Text(f"✓ {attendu}", style=f"bold {BLANC}"))
        else:
            self.essais += 1
            bon, tape = surligner(texte or "·", attendu)
            msg = Text("✎ encore : ", style=f"bold {GRAPHIE}") + bon + Text("   ← ", style=GRIS) + tape
            if self.essais >= 3:
                self.recopies.pop(0)
                self.essais = 0
                msg.append("   (il reviendra)", style=GRIS)
            self.maj("retour", msg)
        self._demander_recopie()

    def _reveler(self) -> None:
        r, it = self.resultat, self.item
        t = Text()
        if r.correct is True:
            t.append("✓ juste", style=f"bold {BLANC}")
        elif r.correct is False:
            t.append("✗ non", style=f"bold {BLANC}")
        else:
            t.append("? à juger", style=f"bold {GRIS}")
        if r.recep:
            t.append("   ")
            t += thermometre(r.recep.temperature)
            if r.recep.mot_reellement_defini and getattr(r, "verdict_local", None) is None:
                t.append("\n")
                t += ligne_proximite(Proximite.SENS, f"tu as plutôt défini « {r.recep.mot_reellement_defini[1]} »")
            if r.recep.synonymes_reconnus:
                t.append(f"\nsynonymes reconnus : {', '.join(r.recep.synonymes_reconnus)}", style=GRIS)
            for mot, comp in r.recep.confusions:
                t.append("\n")
                t += ligne_proximite(comp.proximite, mot)
        v = getattr(r, "verdict_local", None)
        if v is not None:
            t.append(f"\njuge ({self.ctx.juge.modele if self.ctx.juge else '?'}) : ", style=GRIS)
            t.append(f"{v.precision}/5 {PRECISION[v.precision]}", style=f"bold {BLANC}")
            t.append(f" · {v.erreur.value.replace('_', ' ')}", style=GRIS)
            if v.mot_reellement_defini:
                t.append("\n")
                t += ligne_proximite(Proximite.SENS, f"tu as plutôt défini « {v.mot_reellement_defini} »")
            if v.commentaire:
                t.append(f"\n{v.commentaire}", style=GRIS)
        elif getattr(r, "juge_en_cours", False):
            t.append(f"\n… le juge ({self.ctx.juge.modele if self.ctx.juge else ''}) lit ta définition", style=SOMBRE)
        if r.prod and r.prod.comparaison:
            t.append("\n")
            t += ligne_proximite(r.prod.comparaison.proximite, f"« {r.reponse} »")
        for d in r.fautes:
            t.append("\n")
            t += ligne_graphie(d)
        self.maj("retour", t)

        d = Text(f"{it.forme}", style=f"bold {BLANC}") + Text(f"  {it.pos}\n", style=GRIS)
        for i, (_, defi) in enumerate(r.sens, 1):
            d.append(f"  {i}. {defi}\n", style=BLANC)
        if it.voisins:
            d.append("\nà ne pas confondre avec\n", style=GRIS)
            for v in it.voisins[:5]:
                coul = PROXIMITES.get(Proximite(v.get("proximite", "aucune")), GRIS)
                d.append("  ● ", style=coul)
                d.append(f"{v['mot']:<18}", style=f"bold {BLANC}")
                d.append(f"{v['definition']}\n", style=GRIS)
        prop = r.note_proposee
        if it.exercice != "graphie":
            d.append("\nnote : 1 raté · 2 difficile · 3 bien · 4 facile   ", style=GRIS)
            d.append(f"Entrée = {prop}" if prop else "Entrée = laisser Claude juger", style=BLANC)
        self.maj("detail", d)


# ── Bombe ───────────────────────────────────────────────────────────────────────────────────────

class EcranBombe(EcranPartie):
    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(id="entete")
                with Center():
                    yield Static(id="stimulus")
                yield Static(id="minuterie")
                yield Input(id="saisie", placeholder="un mot qui contient la suite — /d : un mot qui marche")
                yield Static(id="retour")
                yield Static(id="detail")
        yield Footer()

    def commande(self, texte: str) -> bool:
        """/d (ou /c), seul : un mot qui marche pour la suite. Avec un argument, /d reste « domaine »."""
        if texte.strip().lower().endswith(("/d", "/c")) and " " not in texte.strip():
            self.query_one("#saisie", Input).value = ""
            if self.pret and not self.fini:
                self.en_fond(self.partie.indice, self._indice)
            return True
        return super().commande(texte)

    def _indice(self, ind) -> None:
        t = Text()
        if ind is None:
            t.append("aucun mot à proposer pour cette suite", style=GRIS)
        else:
            t.append("indice : ", style=GRIS)
            t.append(ind["mot"], style=f"bold {BLANC}")
            t.append("   tu le connais" if ind["connu"] else "   noté « vu » : tu le croiseras", style=GRIS)
        self.maj("retour", t)

    def construire(self):
        from lem.games.bombe import PartieBombe

        return PartieBombe(self.ctx.con, self.params, self.ctx.carte, self.ctx.magasin_entrees, self.session_id,
                           lexique=self.ctx.lexique)

    def demarrer(self) -> None:
        self.historique: list[Text] = []
        self.t_invite = time.monotonic()
        self.fini = False
        self._afficher_invite()
        if not hasattr(self, "minuteur"):
            self.minuteur = self.set_interval(0.1, self._tic)

    def reconfigurer(self) -> None:
        def calcul():
            self.partie.params = self.params
            self.partie.reconfigurer()
            self.partie.invite = self.partie.nouvelle_invite()
        self.en_fond(calcul, lambda _: self._afficher_invite())

    def _afficher_invite(self) -> None:
        inv = self.partie.invite
        self.maj("stimulus", logo.rangee(inv.suite))
        self.t_invite = time.monotonic()
        self.chrono.reset()
        self._entete()

    def _entete(self) -> None:
        from lem.games.base import filtres_depuis

        p, inv = self.partie, self.partie.invite
        t = Text("♥ " * p.vies, style=f"bold {BLANC}") + Text("♡ " * max(0, self.params["vies_max"] - p.vies), style=SOMBRE)
        t += Text(f"  {p.score:6.1f} pts", style=BLANC)
        t += Text(f"   {self.params['mode']} · sub-{self.params['sub']} · {filtres_depuis(self.params).resume()}", style=GRIS)
        if inv.cible_id is not None and inv.repetitions:
            t += Text(f"   ↻ {inv.repetitions}", style=BLANC)
        t += Text(f"   {inv.n_solutions} sol.", style=SOMBRE)
        self.maj("entete", t)

    def _tic(self) -> None:
        if self.partie is None or self.fini:
            return
        if self.occupe:                        # le temps de calcul ne compte pas contre le joueur
            self.t_invite += 0.1
            return
        inv = self.partie.invite
        reste = inv.duree - (time.monotonic() - self.t_invite)
        frac = max(0.0, reste / inv.duree)
        self.maj("minuterie", barre(frac, 60, BLANC if frac > 0.25 else "#ff5f5f"))
        if reste <= 0:
            r = self.partie.exploser()
            self.query_one("#saisie", Input).value = ""      # la tentative ne vaut plus pour la nouvelle suite
            self._retour(r, None)
            if self.partie.finie:
                self._fin()
            else:
                self._afficher_invite()

    def _retour(self, r, mot: str | None) -> None:
        t = Text()
        if r.message == "BOUM":
            t.append("BOUM", style="bold #ff5f5f")
        elif r.graphie:
            t += ligne_graphie(r.graphie)
            t.append("\nretape-le juste pour passer la bombe", style=GRIS)
        elif r.cible_trouvee:
            t.append("● cible trouvée", style="bold #5fd75f")
        elif r.proximite and r.proximite != Proximite.AUCUNE:
            t += ligne_proximite(r.proximite, mot or "")
        elif not r.valide:
            t.append(r.message, style=GRIS)
        if r.valide and r.aide and mot:
            t.append(f"{'' if not t.plain else '   '}{mot}", style=f"bold {BLANC}")
            t.append("  donné par /d : vu, pas trouvé", style=GRIS)
        elif r.valide and r.trouvaille and mot:
            n, med = r.trouvaille["n"], r.trouvaille["latence_mediane_ms"]
            t.append(f"{'' if not t.plain else '   '}{mot}", style=f"bold {BLANC}")
            t.append("  1ʳᵉ fois" if n <= 1 else f"  {n}ᵉ fois · d'habitude {med / 1000:.1f} s".replace(".", ","),
                     style=GRIS)
        if r.vie_gagnee:
            t.append("   +♥", style=f"bold {BLANC}")
        if r.revelation:
            t.append("\ncible : ", style=GRIS)
            t.append(r.revelation[0], style=f"bold {BLANC}")
            t.append(f" — {r.revelation[1]}", style=GRIS)
        self.maj("retour", t)
        if r.valide and mot:
            coul = PROXIMITES.get(r.proximite, BLANC) if r.proximite else BLANC
            self.historique = [Text(mot, style=coul)] + self.historique[:11]
        self._detail()

    def _detail(self) -> None:
        from lem.games.bombe import ALPHABET_BONUS

        d = Text()
        for h in self.historique:
            d += h + Text("  ")
        if self.params["regain"] == "alphabet":
            d.append("\n")
            for c in ALPHABET_BONUS:
                d.append(c.upper() + " ", style=BLANC if c in self.partie.lettres else SOMBRE)
        self.maj("detail", d)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        texte = ev.value.strip()
        if self.fini:
            if texte.lower() in {"", "r", "rejouer"} and self.pret:
                ev.input.value = ""
                self.maj("stimulus", Text("chargement…", style=SOMBRE))
                self.en_fond(self.construire, self._rejouer)
            return
        if not texte or self.commande(texte) or not self.pret:
            return
        # La saisie reste dans le champ tant que le mot n'est pas validé : « meurtree » se corrige d'un effacement.
        lat, frappe = self.chrono.mesures()
        self.en_fond(lambda: self.partie.repondre(texte, lat, frappe), lambda r: self._apres_reponse(r, texte))

    def _rejouer(self, partie) -> None:
        self.partie = partie
        self.fini = False
        self.demarrer()

    def _apres_reponse(self, r, texte: str) -> None:
        self._retour(r, r.forme or texte)
        if r.valide:
            self.query_one("#saisie", Input).value = ""
            self._afficher_invite()

    def _fin(self) -> None:
        self.fini = True
        self.maj("stimulus", logo.rangee("fin"))
        self.maj("minuterie", Text(f"score {self.partie.score:.1f} — Entrée pour rejouer, Échap pour le menu", style=BLANC))


# ── Syllabaire (entraînement de la Bombe) ───────────────────────────────────────────────────────

NOTES_SUITE = {1: "séchée", 2: "hésitante", 3: "sûre", 4: "réflexe"}


def exemple_souligne(exemple: str, suite: str, style: str) -> Text:
    """La forme, avec la suite soulignée (quand les accents ne décalent pas les lettres)."""
    from lem.texte import normaliser

    t = Text(exemple, style=style)
    n = normaliser(exemple)
    i = n.find(suite)
    if i >= 0 and len(n) == len(exemple):
        t.stylize("underline", i, i + len(suite))
    return t


class EcranSyllabaire(EcranPartie):
    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(id="entete")
                with Center():
                    yield Static(id="stimulus")
                yield Static(id="minuterie")
                yield Input(id="saisie", placeholder="tous les mots que tu trouves — Entrée seule : fini")
                yield Static(id="retour")
                yield VerticalScroll(Static(id="detail"))
        yield Footer()

    def construire(self):
        from lem.games.syllabaire import PartieSyllabaire

        return PartieSyllabaire(self.ctx.con, self.params, self.ctx.carte, self.session_id, lexique=self.ctx.lexique)

    def demarrer(self) -> None:
        self.etape = "jeu"               # jeu | bilan | fin
        if not hasattr(self, "minuteur"):
            self.minuteur = self.set_interval(0.1, self._tic)
        self._suivante()

    def reconfigurer(self) -> None:
        self.en_fond(self.partie.reconfigurer, lambda _: self._suivante())

    def _suivante(self) -> None:
        tour = self.partie.suivante()
        if tour is None:
            self._fin()
            return
        self.etape = "jeu"
        self.t_tour = time.monotonic()
        self.chrono.reset()
        self.maj("stimulus", logo.rangee(tour.suite))
        self.maj("retour", Text(""))
        self.maj("detail", Text(""))
        self._entete()
        self.query_one("#saisie", Input).value = ""

    def _entete(self) -> None:
        t, p = self.partie.tour, self.params
        quoi = "nouvelle" if t.nouvelle else "retour" if t.retour else "révision"
        e = Text("SYLLABAIRE  ", style="bold") + Text(quoi, style=BLANC)
        e += Text(f"   sub-{p['seuil']} · {t.n} mots · encore {self.partie.restant()}", style=GRIS)
        self.maj("entete", e)

    def _tic(self) -> None:
        if self.partie is None or self.etape != "jeu" or self.partie.tour is None:
            return
        if self.occupe:
            self.t_tour += 0.1
            return
        duree = self.params["duree"]
        ecoule = time.monotonic() - self.t_tour
        frac = max(0.0, 1 - ecoule / duree)
        self.maj("minuterie", barre(frac, 60, BLANC if frac > 0.25 else "#ff5f5f"))
        if ecoule >= duree:
            self._terminer()

    def _terminer(self) -> None:
        self.etape = "attente"
        duree_ms = int((time.monotonic() - self.t_tour) * 1000)
        self.en_fond(lambda: self.partie.terminer(duree_ms), self._bilan)

    def _bilan(self, b) -> None:
        self.etape = "bilan"
        self.maj("minuterie", Text(""))
        tete = Text(NOTES_SUITE[b.note], style=f"bold {BLANC}")
        t = self.partie.tour
        if t.premiere_ms is not None:
            tete.append(f"   premier mot en {t.premiere_ms / 1000:.1f} s".replace(".", ","), style=GRIS)
        tete.append(f"   tu en as {b.etendue[0]} sur {b.etendue[1]}", style=GRIS)
        jours = f"{b.jours:.0f} j" if b.jours >= 1 else "cette session" if b.note <= 2 else "demain"
        tete.append(f"   revient : {jours}", style=GRIS)
        self.maj("retour", tete)
        d = Text()
        if b.ancre:
            d.append("ancre  ", style=GRIS)
            d += exemple_souligne(b.ancre.exemple, b.suite, f"bold {BLANC}")
            if b.definition_ancre:
                d.append(f" — {b.definition_ancre[:110]}", style=GRIS)
            d.append("\n\n")
        styles = {"trouve": f"bold {BLANC}", "deja": BLANC, "manque": GRIS}
        for i, x in enumerate(b.lemmes[:80]):
            d += exemple_souligne(x.exemple, b.suite, styles[x.statut])
            d.append("   ")
        if len(b.lemmes) > 80:
            d.append(f"… et {len(b.lemmes) - 80} autres", style=SOMBRE)
        d.append("\n\n")
        d.append("gras", style=f"bold {BLANC}")
        d.append(" trouvés maintenant · ", style=SOMBRE)
        d.append("blanc", style=BLANC)
        d.append(" trouvés un autre jour · ", style=SOMBRE)
        d.append("gris", style=GRIS)
        d.append(" à apprendre — Entrée : suite suivante", style=SOMBRE)
        self.maj("detail", d)

    def _fin(self) -> None:
        self.etape = "fin"
        s = self.partie.bilan_session()
        self.maj("stimulus", logo.rangee("fin"))
        self.maj("minuterie", Text(""))
        self.maj("retour", Text(f"{s['suites']} suites · {s['sures']} sûres · {s['hesitantes']} hésitantes · "
                                f"{s['seches']} séchées", style=BLANC))
        self.maj("detail", Text("Entrée : d'autres suites nouvelles · Échap : menu", style=GRIS))

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        texte = ev.value.strip()
        if texte and self.commande(texte):
            return
        if not self.pret:
            return
        if self.etape == "bilan":
            ev.input.value = ""
            self._suivante()
        elif self.etape == "fin":
            ev.input.value = ""
            self.partie.encore()
            self._suivante()
        elif self.etape == "jeu":
            if not texte:
                self._terminer()
                return
            lat = int((time.monotonic() - self.t_tour) * 1000)
            _, frappe = self.chrono.mesures()
            self.en_fond(lambda: self.partie.proposer(texte, lat, frappe), lambda r: self._reponse(r, texte))

    def _reponse(self, r, texte: str) -> None:
        if r.valide:
            t = Text("✓ ", style=f"bold {BLANC}") + Text(r.forme, style=f"bold {BLANC}")
            if r.inedit:
                t.append("   premier jamais trouvé", style=GRIS)
            self.maj("retour", t)
            self.query_one("#saisie", Input).value = ""
            self.chrono.reset()
            self.maj("detail", Text("   ".join(self.partie.tour.donnes), style=BLANC))
        elif r.graphie:
            self.maj("retour", ligne_graphie(r.graphie))
        else:
            self.maj("retour", Text(r.message, style=GRIS))

# ── Mémoire verbale ─────────────────────────────────────────────────────────────────────────────

class EcranMemoire(EcranPartie):
    BINDINGS = [Binding("escape", "quitter", "Menu"), Binding("v", "vu", "Vu"), Binding("n", "nouveau", "Nouveau"),
                Binding("space", "image", "Image prête", show=False)]

    def construire(self):
        from lem.games.memoire_verbale import PartieMemoire

        return PartieMemoire(self.ctx.con, self.params, self.session_id)

    def demarrer(self) -> None:
        self.attente_image = None        # élucubrations : champ d'image mentale écrite
        self.attente_encodage = None     # entraînement : « image ? », Espace quand elle est là
        self.query_one("#saisie").display = False
        self.maj("detail", Text("V = déjà vu · N = nouveau", style=GRIS))
        self.maj("retour", Text(""))
        self._suivant()

    def _entete(self) -> None:
        p = self.partie
        t = Text("♥ " * p.vies, style=f"bold {BLANC}") + Text(f"  {p.score}", style=BLANC)
        if p.mode == "entrainement":
            t += Text(f"   entraînement · record {p.record}", style=GRIS)
        else:
            t += Text("   élucubrations", style=GRIS)
        self.maj("entete", t)

    def _suivant(self) -> None:
        tour = self.partie.suivant()
        style = f"bold {BLANC}"
        mot = logo.rangee(tour.forme) if len(tour.forme) <= 18 and " " not in tour.forme else Text(tour.forme, style=style)
        self.maj("stimulus", mot)
        self._entete()
        self.chrono.reset()

    def _repondre(self, vu: bool) -> None:
        if not self.pret or self.attente_image is not None or self.attente_encodage is not None or self.partie.finie:
            return
        lat, _ = self.chrono.mesures()
        tour = self.partie.tour
        ok = self.partie.repondre(vu, lat)
        r = Text("✓" if ok else "✗", style=f"bold {BLANC if ok else '#ff5f5f'}")
        if not ok:
            r.append("   vu tout à l'heure" if tour.deja_vu else "   jamais vu dans cette partie", style=GRIS)
        if tour.deja_vu and (img := self.partie.image(tour.entree_id)):
            r.append(f"   ton image : {img}", style=GRIS)
        self.maj("retour", r)
        if self.partie.finie:
            self._fin()
            return
        if self.partie.mode == "elucubrations" and tour.premiere_fois and self.params.get("definitions"):
            d = self.partie.definition(tour.entree_id)
            self.maj("detail", Text(f"{tour.forme} — {d}" if d else "V = déjà vu · N = nouveau", style=GRIS))
        if tour.premiere_fois and tour.guider:
            self.attente_encodage = (tour.entree_id, time.monotonic())
            self.maj("detail", Text("image ?   une image vive, avec ce mot — Espace quand elle est là", style=BLANC))
            return
        if tour.premiere_fois and tour.imager:
            self.attente_image = tour.entree_id
            champ = self.query_one("#saisie", Input)
            champ.display = True
            champ.placeholder = f"ton image mentale pour « {tour.forme} » — Entrée pour passer"
            champ.focus()
            return
        self._suivant()

    def action_image(self) -> None:
        if self.attente_encodage is None:
            return
        eid, t0 = self.attente_encodage
        self.attente_encodage = None
        self.partie.encoder(eid, int((time.monotonic() - t0) * 1000))
        self.maj("detail", Text("V = déjà vu · N = nouveau", style=GRIS))
        self._suivant()

    def _fin(self) -> None:
        b = self.partie.bilan()
        self.maj("stimulus", logo.rangee("fin"))
        t = Text(f"score {b['score']}", style=f"bold {BLANC}")
        if self.partie.mode == "entrainement":
            t.append("   nouveau record !" if b["nouveau_record"] else f"   record {b['record']}", style=BLANC)
            if b["mediane_precedente"] is not None:
                t.append(f"   médiane des 10 dernières {b['mediane_precedente']:.0f}", style=GRIS)
        self.maj("entete", t)
        d = Text()
        d.append(f"{b['fausses_alertes']} fausse{'s' * (b['fausses_alertes'] > 1)} alerte"
                 f"{'s' * (b['fausses_alertes'] > 1)}", style=BLANC)
        d.append(" (« vu » pour un mot nouveau : la familiarité t'a trompé)\n", style=GRIS)
        d.append(f"{b['oublis']} oubli{'s' * (b['oublis'] > 1)}", style=BLANC)
        d.append(" (« nouveau » pour un mot vu : l'encodage a manqué)\n", style=GRIS)
        if b["latence_ms"]:
            d.append(f"réponse en {b['latence_ms'] / 1000:.1f} s".replace(".", ","), style=GRIS)
        if b["encodage_ms"]:
            d.append(f" · image en {b['encodage_ms'] / 1000:.1f} s ({b['guides']} invites)".replace(".", ","),
                     style=GRIS)
        d.append("\nÉchap : menu", style=SOMBRE)
        self.maj("detail", d)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if self.attente_image is not None:
            self.partie.enregistrer_image(self.attente_image, ev.value)
            self.attente_image = None
            ev.input.value = ""
            ev.input.display = False
            self.focus()
            self._suivant()

    def action_vu(self) -> None:
        self._repondre(True)

    def action_nouveau(self) -> None:
        self._repondre(False)


# ── Décision (étalonnage, mot ou pas mot) ───────────────────────────────────────────────────────

class EcranDecision(EcranPartie):
    BINDINGS = [Binding("escape", "quitter", "Menu"), Binding("o", "oui", "Oui"), Binding("n", "non", "Non")]

    def construire(self):
        from lem.games.decision import PartieDecision

        return PartieDecision(self.ctx.con, self.jeu.id, self.params, self.ctx.carte, self.session_id,
                              lexique=self.ctx.lexique)

    def demarrer(self) -> None:
        self.verif = False
        self.query_one("#saisie").display = False
        q = "Connais-tu ce mot ?" if self.jeu.id == "etalonnage" else "Mot réel ?"
        self.maj("detail", Text(f"{q}   O = oui · N = non", style=GRIS))
        self._suivante()

    def _suivante(self) -> None:
        if self.partie.finie:
            self.maj("stimulus", logo.rangee("fin"))
            self.maj("entete", Text(f"{self.partie.n} questions · fausses alertes {self.partie.fausses_alertes}",
                                    style=BLANC))
            return
        q = self.partie.suivante()
        self.maj("stimulus", Text(q.texte, style=f"bold {BLANC}"))
        tete = Text(f"{self.jeu.nom.upper()}  ", style="bold") + Text(f"{self.partie.n}", style=BLANC)
        if self.jeu.id == "mot_ou_pas":
            tete += Text("   " + "♥ " * self.partie.vies, style=BLANC) + Text(f"  {self.partie.score}", style=GRIS)
        else:
            tete += Text(f" / {self.params['questions']}", style=GRIS)
        self.maj("entete", tete)
        self.chrono.reset()

    def _repondre(self, oui: bool) -> None:
        if not self.pret or self.verif or self.partie.finie:
            return
        lat, _ = self.chrono.mesures()
        ok = self.partie.repondre(oui, lat)
        if self.jeu.id == "mot_ou_pas":
            self.maj("retour", Text("✓" if ok else "✗", style=f"bold {BLANC if ok else '#ff5f5f'}"))
        q = self.partie.question
        if oui and q.verifier:
            self.verif = True
            champ = self.query_one("#saisie", Input)
            champ.display = True
            champ.placeholder = f"vérification : que veut dire « {q.texte} » ?"
            champ.focus()
            self.chrono.reset()
            return
        self._suivante()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if self.verif:
            lat, _ = self.chrono.mesures()
            self.partie.verification(ev.value, lat)
            self.verif = False
            ev.input.value = ""
            ev.input.display = False
            self.focus()
            self._suivante()

    def action_oui(self) -> None:
        self._repondre(True)

    def action_non(self) -> None:
        self._repondre(False)


# ── Atlas ───────────────────────────────────────────────────────────────────────────────────────

class EcranAtlas(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Retour")]

    def compose(self) -> ComposeResult:
        with Center():
            with VerticalScroll(id="zone"):
                yield Static(logo.rangee("atlas"))
                yield Static(id="corps")
        yield Footer()

    def on_mount(self) -> None:
        from lem.atlas import rapport

        self.query_one("#corps", Static).update(rapport(self.app.ctx.con, self.app.ctx.lexique))


# ── Notation par Claude ─────────────────────────────────────────────────────────────────────────

class EcranNoter(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Retour")]

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(logo.rangee("noter"))
                yield Static(Text("Claude Code en mode headless note tes définitions (abonnement).", style=GRIS))
                yield RichLog(id="journal", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.noter()

    @work(thread=True, exclusive=True)
    def noter(self) -> None:
        from lem.db import connect
        from lem.grading.claude_headless import noter

        log = self.query_one("#journal", RichLog)
        con = connect(self.app.ctx.chemin)
        modele = self.app.modele_notation
        self.app.call_from_thread(log.write, Text(f"modèle : {modele}", style=GRIS))
        try:
            n = noter(con, modele, journal=lambda m: self.app.call_from_thread(log.write, m))
            if n:
                self.app.call_from_thread(self.app.ctx.carte.rejouer)
            self.app.call_from_thread(log.write, Text(f"\n{n} réponses notées. FSRS et carte rejoués.", style=f"bold {BLANC}"))
        except FileNotFoundError:
            self.app.call_from_thread(log.write, Text("commande `claude` introuvable — installe Claude Code.", style="#ff5f5f"))
        finally:
            con.close()


# ── Application ─────────────────────────────────────────────────────────────────────────────────

class LemApp(App):
    CSS = CSS
    TITLE = "lem"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, ctx: Contexte, modele_notation: str = "sonnet"):
        super().__init__()
        self.ctx = ctx
        self.modele_notation = modele_notation

    def on_mount(self) -> None:
        self.push_screen(Accueil())
        self.run_worker(self._optimiser_en_fond, thread=True, group="optimiseur")

    def _optimiser_en_fond(self) -> None:
        """Une fois par semaine (assez de révisions nouvelles) : paramètres FSRS ajustés sur ton journal,
        sur une connexion à part ; les échéances suivent dès la session suivante."""
        from lem.db import connect
        from lem.memory.optimiseur import a_optimiser, optimiser

        if self.ctx.chemin == ":memory:":
            return
        con = connect(self.ctx.chemin)
        try:
            if a_optimiser(con):
                raps = optimiser(con)
                adoptes = [t for t, r in raps.items() if r.adopte]
                if adoptes:
                    noms = {"recep": "sens", "prod": "mot", "ortho": "graphie"}
                    self.call_from_thread(self.notify, "Intervalles ajustés à ta mémoire : "
                                          + ", ".join(noms[t] for t in adoptes), timeout=6)
        except Exception:                        # jamais bloquant : on réessaiera à la prochaine ouverture
            pass
        finally:
            con.close()

    def on_unmount(self) -> None:
        self.ctx.carte.recalculer_tout()


def lancer(ctx: Contexte, modele_notation: str = "sonnet") -> None:
    LemApp(ctx, modele_notation).run()

