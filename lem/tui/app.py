"""Interface terminal de lem (Textual).

Monochrome par défaut ; la couleur ne porte que du sens (cf. theme.py).
Accueil → menu aux flèches → réglages du jeu → partie. Tout écrit dans la base
au fil de l'eau ; les commandes « /… » changent les paramètres en cours de partie.
"""

from __future__ import annotations

import random
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
from lem.games import REGISTRE
from lem.games.base import Jeu, executer_commande
from lem.journal import Frappe, fermer_session, ouvrir_session
from lem.similarite import MESSAGES, Proximite
from lem.tui import logo
from lem.tui.theme import BLANC, CSS, GRIS, PROXIMITES, SOMBRE, barre, thermometre


class Chrono:
    """Mesure une réponse : latence, première frappe, frappes, effacements."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.t0 = time.monotonic()
        self.premiere: float | None = None
        self.frappes = 0
        self.effacements = 0
        self._long = 0

    def changement(self, valeur: str) -> None:
        if valeur and self.premiere is None:
            self.premiere = time.monotonic()
        if len(valeur) < self._long:
            self.effacements += 1
        else:
            self.frappes += 1
        self._long = len(valeur)

    def mesures(self) -> tuple[int, Frappe]:
        lat = int((time.monotonic() - self.t0) * 1000)
        pf = None if self.premiere is None else int((self.premiere - self.t0) * 1000)
        return lat, Frappe(pf, self.frappes, self.effacements)


def ligne_proximite(prox: Proximite | None, mot: str = "") -> Text:
    if prox is None or prox == Proximite.AUCUNE:
        return Text("")
    return Text(f"● {MESSAGES[prox]}", style=f"bold {PROXIMITES[prox]}") + Text(f"  {mot}", style=GRIS)


# ── Accueil ─────────────────────────────────────────────────────────────────────────────────────

MENU = [
    ("cartes", "Cartes", "définir · retrouver · distinguer"),
    ("bombe", "Bombe", "des lettres, un mot, avant l'explosion"),
    ("memoire", "Mémoire verbale", "vu ou nouveau ? les mots les plus étranges"),
    ("mot_ou_pas", "Mot ou pas mot", "décision lexicale chronométrée"),
    ("etalonnage", "Étalonnage", "placer ta carte de connaissance"),
    ("atlas", "Atlas", "ta carte, tes confusions, ta signature"),
    ("noter", "Noter avec Claude", "notation différée des définitions"),
    ("quitter", "Quitter", ""),
]


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
            t = Text(f"{nom:<20}", style="bold") + Text(desc, style=GRIS)
            out.append(Option(t, id=cle))
        return out

    def on_mount(self) -> None:
        self._rng = random.Random(7)
        self._t = 0
        self._anim = self.set_interval(0.06, self._animer)
        self.query_one("#menu").focus()
        self.rafraichir()

    def _animer(self) -> None:
        self._t += 1
        p = min(1.0, self._t / 22)
        self.query_one("#logo", Static).update(logo.brouiller(p, self._rng) if p < 1 else logo.rendre())
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
        ecran = {"cartes": EcranCartes, "bombe": EcranBombe, "memoire": EcranMemoire,
                 "mot_ou_pas": EcranDecision, "etalonnage": EcranDecision}[self.jeu.id]
        self.app.push_screen(ecran(self.jeu, dict(self.params)))


# ── Socle des parties ───────────────────────────────────────────────────────────────────────────

class EcranPartie(Screen):
    BINDINGS = [Binding("escape", "quitter", "Menu")]

    def __init__(self, jeu: Jeu, params: dict):
        super().__init__()
        self.jeu, self.params = jeu, params
        self.chrono = Chrono()
        self.session_id: int | None = None

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
        self.demarrer()
        self.query_one("#saisie").focus()

    def on_unmount(self) -> None:
        if self.session_id:
            fermer_session(self.ctx.con, self.session_id)

    def demarrer(self) -> None: ...

    def reconfigurer(self) -> None: ...

    def maj(self, id_: str, contenu) -> None:
        self.query_one(f"#{id_}", Static).update(contenu)

    def on_input_changed(self, ev: Input.Changed) -> None:
        if not ev.value.startswith("/"):
            self.chrono.changement(ev.value)

    def commande(self, texte: str) -> bool:
        """Traite « /… ». Renvoie True si la saisie était une commande."""
        if not texte.startswith("/"):
            return False
        if texte.strip("/ ").lower() in {"quitter", "q", "menu"}:
            self.action_quitter()
            return True
        res = executer_commande(self.jeu, self.params, texte)
        style = BLANC if res.ok else PROXIMITES[Proximite.JUMEAU]
        self.maj("retour", Text(res.message, style=style))
        if res.change:
            self.ctx.sauver_params(self.jeu, self.params)
            self.reconfigurer()
        self.query_one("#saisie", Input).value = ""
        return True

    def action_quitter(self) -> None:
        self.app.pop_screen()


# ── Cartes ──────────────────────────────────────────────────────────────────────────────────────

class EcranCartes(EcranPartie):
    BINDINGS = [Binding("escape", "quitter", "Menu"), Binding("tab", "indice", "Indice (lettre)", priority=True)]

    def demarrer(self) -> None:
        from lem.games.cartes import PartieCartes

        self.partie = PartieCartes(self.ctx.con, self.params, self.ctx.carte, self.ctx.embedder,
                                   self.ctx.magasin_sens, self.ctx.magasin_entrees, self.session_id)
        self.item = None
        self.resultat = None
        self.faits = 0
        self.suivant()

    def reconfigurer(self) -> None:
        self.partie.params = self.params
        self.partie.construire_file()
        self.suivant()

    def _entete(self) -> None:
        from lem.games.base import filtres_depuis

        ex = {"recep": "mot → sens", "prod": "sens → mot", "discrimination": "duel de confusables"}
        t = Text(f"CARTES  ", style="bold") + Text(f"{ex.get(self.item.exercice, '')}", style=BLANC)
        t += Text(f"   {self.faits} faites · {self.partie.restant()} restantes · {filtres_depuis(self.params).resume()}", style=GRIS)
        if self.item.nouveau:
            t += Text("   nouveau", style="#5f87d7")
        self.maj("entete", t)

    def suivant(self) -> None:
        self.resultat = None
        self.item = self.partie.suivant()
        saisie = self.query_one("#saisie", Input)
        saisie.value = ""
        self.maj("retour", "")
        self.maj("detail", "")
        if self.item is None:
            self.maj("entete", Text("CARTES", style="bold"))
            self.maj("stimulus", Text("Rien à réviser avec ces filtres.\n", style=BLANC)
                     + Text("Change-les (/domaine, /etat, /nouveaux 20) ou Échap pour revenir.", style=GRIS))
            return
        self._entete()
        it = self.item
        if it.exercice == "recep":
            st = logo.rangee(it.forme) if len(it.forme) <= 18 and " " not in it.forme \
                else Text(it.forme, style=f"bold {BLANC}")
            st += Text(f"\n{it.pos} — définis-le, en phrase ou en synonymes séparés par des virgules", style=SOMBRE)
            saisie.placeholder = "définition ou synonymes…"
        elif it.exercice == "prod":
            st = Text(it.definition, style=BLANC) + Text(f"\n\n{it.pos} · {len(it.forme)} lettres · Tab = une lettre",
                                                         style=SOMBRE)
            saisie.placeholder = "le mot…"
        else:
            st = Text(it.definition + "\n\n", style=BLANC)
            for i, (_, f) in enumerate(it.options, 1):
                st += Text(f"  {i}  ", style=f"bold {BLANC}") + Text(f"{f}\n", style=BLANC)
            saisie.placeholder = "numéro…"
        self.maj("stimulus", st)
        self.chrono.reset()

    def action_indice(self) -> None:
        if self.item and self.item.exercice == "prod" and self.resultat is None:
            self.maj("retour", Text("indice : ", style=GRIS) + Text(self.partie.indice(self.item), style=f"bold {BLANC}"))

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        texte = ev.value
        if self.commande(texte) or self.item is None:
            return
        ev.input.value = ""
        if self.resultat is None:
            lat, frappe = self.chrono.mesures()
            self.resultat = self.partie.repondre(self.item, texte, lat, frappe)
            self._reveler()
        else:
            note = int(texte) if texte.strip() in {"1", "2", "3", "4"} else None
            self.partie.valider(self.resultat, note)
            self.faits += 1
            self.suivant()

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
            if r.recep.mot_reellement_defini:
                t.append("\n")
                t += ligne_proximite(Proximite.SENS, f"tu as plutôt défini « {r.recep.mot_reellement_defini[1]} »")
            if r.recep.synonymes_reconnus:
                t.append(f"\nsynonymes reconnus : {', '.join(r.recep.synonymes_reconnus)}", style=GRIS)
            for mot, comp in r.recep.confusions:
                t.append("\n")
                t += ligne_proximite(comp.proximite, mot)
        if r.prod and r.prod.comparaison:
            t.append("\n")
            t += ligne_proximite(r.prod.comparaison.proximite, f"« {r.reponse} »")
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
        d.append("\nnote : 1 raté · 2 difficile · 3 bien · 4 facile   ", style=GRIS)
        d.append(f"Entrée = {prop}" if prop else "Entrée = laisser Claude juger", style=BLANC)
        self.maj("detail", d)
        self.query_one("#saisie", Input).placeholder = "1–4, ou Entrée"


# ── Bombe ───────────────────────────────────────────────────────────────────────────────────────

class EcranBombe(EcranPartie):
    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="zone"):
                yield Static(id="entete")
                with Center():
                    yield Static(id="stimulus")
                yield Static(id="minuterie")
                yield Input(id="saisie", placeholder="un mot qui contient la suite — /aide")
                yield Static(id="retour")
                yield Static(id="detail")
        yield Footer()

    def demarrer(self) -> None:
        from lem.games.bombe import PartieBombe

        self.partie = PartieBombe(self.ctx.con, self.params, self.ctx.carte, self.ctx.magasin_entrees, self.session_id)
        self.historique: list[Text] = []
        self.t_invite = time.monotonic()
        self.fini = False
        self._afficher_invite()
        self.minuteur = self.set_interval(0.1, self._tic)

    def reconfigurer(self) -> None:
        self.partie.params = self.params
        self.partie.reconfigurer()
        self.partie.invite = self.partie.nouvelle_invite()
        self._afficher_invite()

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
        t += Text(f"   {self.params['mode']} · {filtres_depuis(self.params).resume()}", style=GRIS)
        if inv.cible_id is not None and inv.repetitions:
            t += Text(f"   ↻ {inv.repetitions}", style=BLANC)
        t += Text(f"   {inv.n_solutions} sol.", style=SOMBRE)
        self.maj("entete", t)

    def _tic(self) -> None:
        if self.fini:
            return
        inv = self.partie.invite
        reste = inv.duree - (time.monotonic() - self.t_invite)
        frac = max(0.0, reste / inv.duree)
        self.maj("minuterie", barre(frac, 60, BLANC if frac > 0.25 else "#ff5f5f"))
        if reste <= 0:
            r = self.partie.exploser()
            self._retour(r, None)
            if self.partie.finie:
                self._fin()
            else:
                self._afficher_invite()

    def _retour(self, r, mot: str | None) -> None:
        t = Text()
        if r.message == "BOUM":
            t.append("BOUM", style="bold #ff5f5f")
        elif r.cible_trouvee:
            t.append("● cible trouvée", style="bold #5fd75f")
        elif r.proximite and r.proximite != Proximite.AUCUNE:
            t += ligne_proximite(r.proximite, mot or "")
        elif not r.valide:
            t.append(r.message, style=GRIS)
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
            if texte.lower() in {"", "r", "rejouer"}:
                ev.input.value = ""
                self.fini = False
                self.demarrer()
            return
        if not texte or self.commande(texte):
            return
        ev.input.value = ""
        lat, frappe = self.chrono.mesures()
        r = self.partie.repondre(texte, lat, frappe)
        self._retour(r, texte)
        if r.valide:
            self._afficher_invite()

    def _fin(self) -> None:
        self.fini = True
        self.maj("stimulus", logo.rangee("fin"))
        self.maj("minuterie", Text(f"score {self.partie.score:.1f} — Entrée pour rejouer, Échap pour le menu", style=BLANC))

# ── Mémoire verbale ─────────────────────────────────────────────────────────────────────────────

class EcranMemoire(EcranPartie):
    BINDINGS = [Binding("escape", "quitter", "Menu"), Binding("v", "vu", "Vu"), Binding("n", "nouveau", "Nouveau")]

    def demarrer(self) -> None:
        from lem.games.memoire_verbale import PartieMemoire

        self.partie = PartieMemoire(self.ctx.con, self.params, self.session_id)
        self.attente_image = None
        self.query_one("#saisie").display = False
        self.maj("detail", Text("V = déjà vu · N = nouveau", style=GRIS))
        self._suivant()

    def _suivant(self) -> None:
        tour = self.partie.suivant()
        style = f"bold {BLANC}"
        mot = logo.rangee(tour.forme) if len(tour.forme) <= 18 and " " not in tour.forme else Text(tour.forme, style=style)
        self.maj("stimulus", mot)
        self.maj("entete", Text("♥ " * self.partie.vies, style=f"bold {BLANC}") + Text(f"  {self.partie.score}", style=BLANC)
                 + Text("   mémoire verbale", style=GRIS))
        self.chrono.reset()

    def _repondre(self, vu: bool) -> None:
        if self.attente_image is not None or self.partie.finie:
            return
        lat, _ = self.chrono.mesures()
        tour = self.partie.tour
        ok = self.partie.repondre(vu, lat)
        self.maj("retour", Text("✓" if ok else "✗", style=f"bold {BLANC if ok else '#ff5f5f'}"))
        if self.partie.finie:
            self.maj("stimulus", logo.rangee("fin"))
            self.maj("entete", Text(f"score {self.partie.score}", style=f"bold {BLANC}"))
            return
        if tour.premiere_fois and tour.imager:
            self.attente_image = tour.entree_id
            champ = self.query_one("#saisie", Input)
            champ.display = True
            champ.placeholder = f"ton image mentale pour « {tour.forme} » (écris ou dicte) — Entrée pour passer"
            champ.focus()
            return
        self._suivant()

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

    def demarrer(self) -> None:
        from lem.games.decision import PartieDecision

        self.partie = PartieDecision(self.ctx.con, self.jeu.id, self.params, self.ctx.carte, self.session_id)
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
        if self.verif or self.partie.finie:
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

        self.query_one("#corps", Static).update(rapport(self.app.ctx.con))


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
            self.app.call_from_thread(log.write, Text(f"\n{n} réponses notées. FSRS rejoué.", style=f"bold {BLANC}"))
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

    def on_unmount(self) -> None:
        self.ctx.carte.recalculer_tout()


def lancer(ctx: Contexte, modele_notation: str = "sonnet") -> None:
    LemApp(ctx, modele_notation).run()

