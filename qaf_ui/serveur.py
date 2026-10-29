"""qaf-ui : processus de la session graphique de l'utilisateur qui affiche les
confirmations demandées par l'exécuteur (qaf_gate.confirm.Fenetre).

- Socket Unix /run/qaf-ui/confirm.sock, dans un dossier posé par tmpfiles
  (propriétaire = utilisateur de la session, groupe qaf-exec, 2750) : le
  socket hérite du groupe qaf-exec (bit setgid) et passe en 0660.
- N'écoute que l'exécuteur : uid du pair (SO_PEERCRED) = compte qaf-exec.
- Une demande à la fois ; une seconde reçoit « occupe » (refus).
- Une connexion = une demande = une réponse {"accorde", "motif", "jeton"}.
Le rendu GTK (fenetre.py) n'est importé que par main().
"""

import os
import pwd
import socket
import socketserver
import struct
import sys
import threading

from qaf_gate import proto

from . import confirmation as c

SOCK_UI = "/run/qaf-ui/confirm.sock"  # = qaf_gate.confirm.SOCK_UI
COMPTE_EXECUTEUR = "qaf-exec"
MAX_DEMANDE = 128 * 1024  # plan de 64 Kio au plus, plus le contexte


def uid_pair(s) -> int:
    cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", cred)[1]


def traiter(msg, afficher, verrou) -> dict:
    """Valide la demande puis l'affiche (afficher(req) -> réponse, bloquant
    jusqu'à la décision ou l'expiration). Toute anomalie = refus."""
    try:
        req = c.lire_requete(msg)
    except c.DemandeInvalide:
        return c.refus(c.INVALIDE)
    if not verrou.acquire(blocking=False):
        return c.refus(c.OCCUPE)
    try:
        rep = afficher(req)
    except Exception:  # rendu en panne : refus, jamais d'accord par défaut
        return c.refus(c.ERREUR)
    finally:
        verrou.release()
    if not (isinstance(rep, dict) and rep.get("accorde") in (True, False)):
        return c.refus(c.ERREUR)
    return rep


class _Gestion(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        try:
            if uid_pair(self.request) not in srv.autorises:
                return
            self.request.settimeout(10)
            msg = proto.recevoir_json(self.request, MAX_DEMANDE)
        except (OSError, proto.ProtoError):
            return
        rep = traiter(msg, srv.afficher, srv.verrou)
        try:
            self.request.settimeout(10)
            proto.envoyer_json(self.request, rep)
        except OSError:
            pass


def serveur(afficher, chemin=SOCK_UI, autorises=None):
    """Serveur prêt à servir (serve_forever) ; autorises = uids acceptés."""
    if autorises is None:
        autorises = {pwd.getpwnam(COMPTE_EXECUTEUR).pw_uid}
    ancien = os.umask(0o117)  # socket en 0660 dès sa création
    try:
        srv = proto.serveur(_Gestion, chemin, afficher=afficher, autorises=frozenset(autorises),
                            verrou=threading.Lock())
    finally:
        os.umask(ancien)
    if chemin and os.path.exists(chemin):
        os.chmod(chemin, 0o660)
    return srv


def main(clavier=None):
    """clavier : fonction () -> Clavier ; None = clavier interne (C11). Seul un
    lanceur de développement, jamais livré, en passe une."""
    from . import fenetre  # importe GTK : seulement ici

    if not fenetre.preparer():
        print("qaf-ui : pas d'affichage (DISPLAY ?)", file=sys.stderr)
        return 1  # l'unité relance ; sans qaf-ui, l'exécuteur refuse tout
    aff = fenetre.afficheur() if clavier is None else fenetre.afficheur(clavier=clavier)
    srv = serveur(aff, os.environ.get("QAF_UI_SOCKET", SOCK_UI))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return fenetre.boucle()


if __name__ == "__main__":
    sys.exit(main())
