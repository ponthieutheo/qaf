#!/usr/bin/python3 -I
"""Talon du service qrexec qaf.Plan (dom0), installé tel quel dans
/etc/qubes-rpc/qaf.Plan.

Il tourne sous l'utilisateur de bureau de dom0 (C4) : il ne fait donc AUCUN
parsing. Il lit au plus 64 Kio sur stdin avec un délai, puis transmet au
validateur (socket Unix) trois trames : l'appelant (QREXEC_REMOTE_DOMAIN,
fourni par dom0, fiable), un statut de lecture, les octets bruts. Il recopie
la réponse du validateur sur stdout.

Bibliothèque standard seulement et aucun import de qaf_gate : il est lancé avec
`python3 -I`, sans le reste du Doorman dans son chemin.
"""

import os
import select
import socket
import struct
import sys
import time

MAX_ENTREE = 64 * 1024  # = schema.MAX_BYTES (vérifié par les tests)
DELAI_LECTURE = 10.0  # secondes pour recevoir tout le plan
DELAI_REPONSE = 2580.0  # = proto.DELAI_TALON (confirmation < exécuteur < validateur < talon)
MAX_REPONSE = 1024 * 1024
SOCKET = os.environ.get("QAF_STUB_SOCKET", "/run/qaf-gate/validator.sock")


def lire_borne(fd, maxi=MAX_ENTREE, delai=DELAI_LECTURE):
    """Lit fd jusqu'à EOF. Renvoie (statut, octets), statut parmi
    "ok", "trop_grand" (plus de maxi octets), "delai" (délai dépassé).
    Ne lit jamais plus de maxi + 1 octets."""
    fin = time.monotonic() + delai
    morceaux, total = [], 0
    while True:
        reste = fin - time.monotonic()
        if reste <= 0 or not select.select([fd], [], [], reste)[0]:
            return "delai", b""
        bloc = os.read(fd, min(65536, maxi + 1 - total))
        if not bloc:
            return "ok", b"".join(morceaux)
        morceaux.append(bloc)
        total += len(bloc)
        if total > maxi:
            return "trop_grand", b""


def _envoyer(s, data):
    s.sendall(struct.pack(">I", len(data)) + data)


def _recevoir(s, maxi):
    def exact(n):
        buf = b""
        while len(buf) < n:
            bloc = s.recv(min(n - len(buf), 65536))
            if not bloc:
                raise OSError("connexion fermée")
            buf += bloc
        return buf

    (n,) = struct.unpack(">I", exact(4))
    if n > maxi:
        raise OSError("réponse trop longue")
    return exact(n)


def main():
    appelant = os.environ.get("QREXEC_REMOTE_DOMAIN", "").encode("ascii", "replace")
    statut, donnees = lire_borne(sys.stdin.fileno())
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(DELAI_REPONSE)
            s.connect(SOCKET)
            for trame in (appelant[:64], statut.encode("ascii"), donnees):
                _envoyer(s, trame)
            reponse = _recevoir(s, MAX_REPONSE)
    except OSError:
        reponse = b'{"raison":"c\\u0153ur du syst\\u00e8me indisponible","statut":"erreur"}'
    sys.stdout.buffer.write(reponse + b"\n")
    sys.stdout.flush()
    return 0  # le statut est dans la réponse JSON, que le talon ne lit pas


if __name__ == "__main__":
    sys.exit(main())
