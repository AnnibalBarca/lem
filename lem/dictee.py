"""Hands-free dictation: lem tells voxmac when a field is open, voxmac does the listening.

voxmac (the user's push-to-talk dictation, Parakeet on the Mac) owns the microphone. lem never records: it sends
one-line datagrams on a local Unix socket (~/.voxmac/ctl.sock, user-only), and voxmac types each finished
sentence into the focused field — without Enter, which stays the player's.

    listen            an answer field is open: listen, type what is said
    listen chiffres   a 1–4 choice is open: « deux » is typed as 2
    stop              no field open (or the keyboard is required): do not type anything

A datagram socket is the lightest way for two processes to talk: no connection, no handshake, and lem works
exactly as before when voxmac is not running (the send fails silently). voxmac stops listening by itself when it
hears nothing for a few minutes, so a crashed lem cannot leave it typing; lem repeats `listen` every 60 s.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

CHEMIN_DEFAUT = Path.home() / ".voxmac" / "ctl.sock"
BATTEMENT_S = 60.0


class Dictee:
    def __init__(self, chemin: str | Path | None = None, actif: bool = True) -> None:
        self.chemin = str(chemin or os.environ.get("LEM_DICTEE_SOCK") or CHEMIN_DEFAUT)
        self.actif = actif
        self.mode: str | None = None          # last mode sent: "texte" | "chiffres" | None (stopped)
        self.envoye_a = 0.0
        self._sock: socket.socket | None = None

    def _envoyer(self, message: str) -> bool:
        if not self.actif:
            return False
        try:
            if self._sock is None:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self._sock.setblocking(False)
            self._sock.sendto(message.encode(), self.chemin)
            return True
        except OSError:          # voxmac not running, socket gone, buffer full: dictation is optional
            return False

    def regler(self, mode: str | None) -> None:
        """Make voxmac match the screen: "texte", "chiffres" or None. Only talks when something changed, or as a
        heartbeat."""
        maintenant = time.monotonic()
        if mode == self.mode and (mode is None or maintenant - self.envoye_a < BATTEMENT_S):
            return
        self._envoyer("stop" if mode is None else "listen" if mode == "texte" else f"listen {mode}")
        self.mode, self.envoye_a = mode, maintenant

    def arreter(self) -> None:
        self.regler(None)
