"""Deferred judging queue: answers go in, verdicts come out later, the game never waits.

The player answers, presses Enter and the next card appears at once. Each definition is handed to this queue;
a few worker threads call the judge (Claude Haiku headless by default) and report back through `rappel`.
The answer is already in the journal, so nothing is lost if the verdict never comes: `lem noter` judges what
is left, and replaying FSRS picks the best note available (see `scheduler.note_finale`).

Pure threading, no UI and no database access here: the caller applies verdicts on its own thread.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Resultat:
    """What the queue reports for one submitted item."""

    element: object
    jeton: object
    verdict: object | None
    erreur: Exception | None
    duree_s: float


@dataclass
class FileJugement:
    """`rappel(Resultat)` is called from a worker thread, once per submitted item."""

    juge: object
    rappel: Callable[[Resultat], None]
    workers: int = 2
    _file: queue.Queue = field(default_factory=queue.Queue, init=False, repr=False)
    _verrou: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _en_cours: int = field(default=0, init=False)
    _fils: list = field(default_factory=list, init=False, repr=False)
    durees: list[float] = field(default_factory=list, init=False)

    def demarrer(self) -> None:
        """Start the workers (idempotent) and warm the judge up, so the first real verdict is not the slow one."""
        if self._fils:
            return
        for i in range(self.workers):
            t = threading.Thread(target=self._boucle, name=f"juge-{i}", daemon=True)
            t.start()
            self._fils.append(t)
        threading.Thread(target=self._prechauffer, name="juge-chaud", daemon=True).start()

    def _prechauffer(self) -> None:
        appel = getattr(self.juge, "appel", None)
        if appel is None:
            return
        try:
            appel("Réponds OK.", getattr(self.juge, "modele", "haiku"))
        except Exception:      # the warm-up is a courtesy: a failure shows up on the first real verdict
            pass

    def soumettre(self, element, jeton=None) -> None:
        self.demarrer()
        with self._verrou:
            self._en_cours += 1
        self._file.put((element, jeton))

    @property
    def en_attente(self) -> int:
        """Items submitted whose verdict has not been reported yet."""
        with self._verrou:
            return self._en_cours

    @property
    def latence_mediane_s(self) -> float | None:
        d = sorted(self.durees)
        return d[len(d) // 2] if d else None

    def _boucle(self) -> None:
        while True:
            element, jeton = self._file.get()
            t = time.monotonic()
            verdict = erreur = None
            try:
                verdict = self.juge.juger(element)
            except Exception as exc:      # judge absent, slow, out of schema: the caller keeps the cosine note
                erreur = exc
            duree = time.monotonic() - t
            with self._verrou:
                self._en_cours -= 1
                if erreur is None:
                    self.durees.append(duree)
            try:
                self.rappel(Resultat(element, jeton, verdict, erreur, duree))
            except Exception:             # a broken callback must not kill the worker
                pass
