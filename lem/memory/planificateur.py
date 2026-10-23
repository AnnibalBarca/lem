"""Planificateur de session : quelle carte maintenant ?

Le moteur v1 construisait une file une fois pour toutes. Le planificateur décide carte après carte, avec
l'horloge du moteur, à partir de quatre réservoirs :

  attente     cartes à remontrer dans la session : étapes d'apprentissage FSRS (1 min, 10 min), duel et
              « sens → mot » d'un mot qu'on vient de découvrir, graphie à reprendre. Chacune a une heure
              (`pret_a`) et un numéro de carte minimum (`apres`).
  revisions   traces FSRS dues, les plus fragiles d'abord (et la vérification unique des mots connus).
  candidats   mots nouveaux, déjà classés par valeur ; chacun commence par une SONDE (mot → sens).
  budget      nombre de mots nouveaux qui peuvent ENTRER en apprentissage (les sondes réussies ne comptent
              pas : un mot connu coûte une carte, pas six).

Règles, dans l'ordre :
  1. une carte en attente dont l'heure est venue passe d'abord (le réapprentissage est urgent) ;
  2. sinon, un mot nouveau ou une révision, en proportion des réserves (les nouveaux sont répartis dans
     la session au lieu d'arriver tous à la fin), tant que le nombre de mots en cours d'apprentissage
     ne dépasse pas EN_COURS_MAX (on consolide avant d'ouvrir) ;
  3. jamais deux cartes du même mot à moins de ECART_SOEURS cartes d'intervalle (sauf reprise de graphie),
     pour qu'une carte ne souffle pas la réponse de sa sœur ;
  4. s'il ne reste que des cartes en attente pas encore mûres, la plus proche passe en avance ; s'il ne
     reste que des sœurs trop proches, la moins récente passe quand même.

Le planificateur ne touche pas à la base : PartieCartes lui fournit les réservoirs et lui rapporte les
réponses ; il est testable seul.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ECART_SOEURS = 4         # cartes minimum entre deux cartes du même mot
ECART_DUEL = 3           # le duel d'un mot découvert, au plus tôt 3 cartes après
ECART_PROD = 10          # « sens → mot » d'un mot découvert : au plus tôt 10 cartes et 5 minutes après
DELAI_PROD = 300.0
HORIZON = 30 * 60.0      # une échéance FSRS à moins de 30 min revient dans la session
EN_COURS_MAX = 5         # mots en apprentissage ouverts en même temps
RETOURS_MAX = 3          # une même carte revient au plus 3 fois dans une session ; au-delà, à la suivante
SONDES_PAR_MOT = 4       # au plus 4 sondes par mot de budget (si tout est connu, on n'insiste pas)
SECONDES_PAR_CARTE = 10.0  # pour traduire la durée visée en nombre de cartes
COUT_MOT = 6.0           # cartes qu'un mot appris coûte sa première semaine (sonde, retours, duel, sens → mot)
EXPLORATION = 3.0        # poids de l'information qu'apporte une sonde dans le choix des mots nouveaux


@dataclass
class Tache:
    exercice: str                   # recep | prod | discrimination | graphie
    entree_id: int
    sens_id: int = 0
    role: str = "revision"          # sonde | apprentissage | revision | verification | reprise
    pret_a: float = 0.0             # heure (s) à partir de laquelle la montrer
    apres: int = 0                  # numéro de carte minimum
    r: float = 1.0                  # retrievabilité (révisions), pour l'ordre
    nouveau: bool = False

    @property
    def cle(self) -> tuple[int, str, str]:
        return (self.entree_id, self.exercice, self.role)


@dataclass
class Planificateur:
    revisions: list[Tache] = field(default_factory=list)
    candidats: list[int] = field(default_factory=list)
    budget_nouveaux: int = 10
    duels: bool = True
    directions: tuple[str, ...] = ("recep", "prod")         # exercices pratiqués ; le premier sert de sonde
    attente: list[Tache] = field(default_factory=list)
    n: int = 0                                            # cartes montrées
    dernier: dict[int, int] = field(default_factory=dict)  # mot → numéro de sa dernière carte
    en_cours: set[int] = field(default_factory=set)        # mots en apprentissage dans la session
    sondes: int = 0
    entres: int = 0                                       # mots entrés en apprentissage (budget consommé)
    connus: int = 0                                       # sondes réussies
    retours: dict = field(default_factory=dict)           # (mot, exercice) → nombre de retours dans la session
    _rev_depuis_nouveau: int = 0

    # -- réserves --------------------------------------------------------------------------------
    def nouveaux_possibles(self) -> bool:
        return (self.entres < self.budget_nouveaux and self.sondes < SONDES_PAR_MOT * max(1, self.budget_nouveaux)
                and bool(self.candidats))

    def restant(self) -> int:
        reste_nouveaux = min(len(self.candidats), max(0, self.budget_nouveaux - self.entres)) if self.nouveaux_possibles() else 0
        return len(self.attente) + len(self.revisions) + reste_nouveaux

    def _libre(self, entree_id: int, ecart: int = ECART_SOEURS) -> bool:
        d = self.dernier.get(entree_id)
        return d is None or self.n - d >= ecart

    # -- choix -----------------------------------------------------------------------------------
    def prochaine(self, t: float) -> Tache | None:
        # 1. Les cartes en attente dont l'heure est venue.
        pretes = [x for x in self.attente if x.pret_a <= t and x.apres <= self.n
                  and (x.role == "reprise" or self._libre(x.entree_id))]
        if pretes:
            x = min(pretes, key=lambda x: (x.pret_a, x.apres))
            return self._prendre(x, self.attente)

        # 2. Nouveau mot ou révision, en proportion.
        rev = next((x for x in self.revisions if self._libre(x.entree_id) and x.entree_id not in self.en_cours), None)
        peut_ouvrir = self.nouveaux_possibles() and len(self.en_cours) < EN_COURS_MAX
        if peut_ouvrir and (rev is None or self._rev_depuis_nouveau >= self._cadence()):
            e = self._candidat()
            if e is not None:
                self._rev_depuis_nouveau = 0
                self.sondes += 1
                return self._noter(Tache(self.directions[0], e, role="sonde", nouveau=True))
        if rev is not None:
            self._rev_depuis_nouveau += 1
            return self._prendre(rev, self.revisions)

        # 3. Plus que des cartes en attente pas encore mûres : la plus proche passe en avance.
        if self.attente:
            libres = [x for x in self.attente if x.role == "reprise" or self._libre(x.entree_id, 2)]
            x = min(libres or self.attente, key=lambda x: (x.pret_a, x.apres))
            return self._prendre(x, self.attente)
        if self.nouveaux_possibles():
            e = self._candidat()
            if e is not None:
                self.sondes += 1
                return self._noter(Tache(self.directions[0], e, role="sonde", nouveau=True))
        # 4. Il ne reste que des cartes sœurs de mots vus à l'instant : la moins récente passe quand même
        #    (mieux qu'une session qui s'arrête avec des révisions dues).
        if self.revisions:
            x = min(self.revisions, key=lambda x: self.dernier.get(x.entree_id, -1))
            return self._prendre(x, self.revisions)
        return None

    def _cadence(self) -> int:
        """Révisions entre deux nouveaux mots, pour étaler les nouveaux sur toute la session."""
        reste_n = max(1, self.budget_nouveaux - self.entres)
        return max(1, math.floor(len(self.revisions) / reste_n))

    def _candidat(self) -> int | None:
        while self.candidats:
            e = self.candidats.pop(0)
            if e not in self.en_cours and e not in self.dernier:
                return e
        return None

    def _prendre(self, x: Tache, reservoir: list[Tache]) -> Tache:
        reservoir.remove(x)
        return self._noter(x)

    def _noter(self, x: Tache) -> Tache:
        self.dernier[x.entree_id] = self.n
        self.n += 1
        return x

    # -- retours ---------------------------------------------------------------------------------
    def ajouter(self, x: Tache) -> bool:
        """Une carte à remontrer dans la session (sans doublon, et pas plus de RETOURS_MAX fois)."""
        if any(y.cle == x.cle for y in self.attente):
            return False
        k = (x.entree_id, x.exercice)
        if self.retours.get(k, 0) >= RETOURS_MAX:
            return False
        self.retours[k] = self.retours.get(k, 0) + 1
        self.attente.append(x)
        return True

    def apres_reponse(self, tache: Tache, t: float, note: int | None, echeance: float | None,
                      a_des_voisins: bool = False) -> str:
        """Ce que la réponse déclenche. `echeance` : prochaine échéance FSRS de la trace (s), ou None.

        Renvoie la voie du mot : « connu » (sonde réussie), « apprentissage » ou « suivi »."""
        e = tache.entree_id
        if tache.role == "sonde":
            if note is not None and note >= 3:
                self.connus += 1
                return "connu"
            # Sonde ratée ou floue : le mot entre en apprentissage. Révélation faite, on programme la
            # suite : retour du sens à l'étape FSRS, duel, puis « sens → mot » plus tard.
            self.entres += 1
            self.en_cours.add(e)
            if echeance is not None and echeance - t <= HORIZON:      # étape FSRS courte, si on en a réglé
                self.ajouter(Tache(tache.exercice, e, tache.sens_id, "apprentissage", pret_a=echeance,
                                   apres=self.n + ECART_SOEURS))
            if self.duels and a_des_voisins:
                self.ajouter(Tache("discrimination", e, tache.sens_id, "apprentissage", apres=self.n + ECART_DUEL))
            if tache.exercice == "recep" and "prod" in self.directions:
                self.ajouter(Tache("prod", e, tache.sens_id, "apprentissage", pret_a=t + DELAI_PROD,
                                   apres=self.n + ECART_PROD, nouveau=True))
            return "apprentissage"
        if tache.exercice in ("recep", "prod", "graphie") and echeance is not None and echeance - t <= HORIZON:
            # Étape d'apprentissage ou de réapprentissage : la carte revient dans la session.
            if self.ajouter(Tache(tache.exercice, e, tache.sens_id, "apprentissage", pret_a=echeance,
                                  apres=self.n + ECART_SOEURS)):
                self.en_cours.add(e)
        if not any(y.entree_id == e for y in self.attente):
            self.en_cours.discard(e)
        return "suivi"

    def apercu(self) -> list[Tache]:
        """Ordre approximatif des prochaines cartes (pour l'affichage et les tests) ; sans effet."""
        att = sorted(self.attente, key=lambda x: (x.pret_a, x.apres))
        nouv = [Tache(self.directions[0], e, role="sonde", nouveau=True)
                for e in self.candidats[: max(0, self.budget_nouveaux - self.entres)]]
        return att + self.revisions + nouv
