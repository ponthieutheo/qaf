"""Services qrexec qaf.Console et qaf.Connexion (sys-agent, installés dans
tpl-agent), appelés par dom0 depuis le panneau de l'agent de la barre de
Plasma (docs/AGENT-PANNEAU.md §10, qaf_ui/ecrans/agent_panneau.py).

qaf.Console (« >_ », clic droit de l'icône) : la console de l'agent permanent,
c'est-à-dire la fenêtre « Agent » de qaf.Request (même terminal, même titre,
même fichier de pid : une seule fenêtre « Agent » à la fois, demande.py). Si
elle est ouverte, rien (dom0 l'amène devant) ; sinon un terminal avec claude,
qui reprend la conversation de ~/agent s'il y en a une (--continue). Aucun
texte n'est envoyé ni tapé : on écrit soi-même dans la console.
La console suit le fournisseur actif (qaf_mcp/fournisseurs.py) ; QAF_REQUEST en est
retirée, et aucune valeur littérale n'est posée à sa place.
qaf.Connexion+FOURNISSEUR : un terminal « Connexion » avec l'assistant du
fournisseur (qaf_mcp/assistant.py), qui reste ouvert jusqu'à Entrée pour qu'on lise
le résultat. Le résultat du dernier assistant est effacé avant d'ouvrir le terminal :
un ancien échec ne s'affiche pas.

qaf.Console n'accepte aucun argument ; celui de qaf.Connexion vient de qrexec
(QREXEC_SERVICE_ARGUMENT, « qaf.Connexion+codex ») et doit être un des 35 ids.
L'appelant doit être dom0. Bibliothèque standard seulement.
"""

import os
import subprocess
import sys
import time

from . import demande as d
from . import fournisseurs
from .fournisseurs_table import IDS, PAR_ID, PYTHON

CONNEXIONS = IDS  # tous ; le terminal ouvre l'assistant du fournisseur
TITRE_CONNEXION = "Connexion"
ASSISTANT = ('import sys; sys.path.insert(0, "/usr/lib/qaf"); from qaf_mcp.assistant import main; '
             'sys.exit(main(sys.argv[1:]))')


def commande_console(suite, lancement=None):
    """argv du terminal « Agent » de la console : le CLI du fournisseur actif, --continue
    s'il y a une conversation de la même famille."""
    agent = [*lancement.env, *lancement.argv] if lancement else [d.CLAUDE, *(("--continue",) if suite else ())]
    return [d.TERMINAL, "--disable-server", d.GEOMETRIE, f"--title={d.TITRE}", "-x", d.ENV,
            *d.ENV_SYS_AGENT, *agent]


def commande_connexion(fournisseur):
    return [d.TERMINAL, "--disable-server", "--geometry=100x30",
            f"--title={TITRE_CONNEXION} {PAR_ID[fournisseur].nom}", "-x", PYTHON, "-I", "-c", ASSISTANT, fournisseur]


def terminal_ouvert(fichier, proc="/proc"):
    """Vrai si le terminal « Agent » noté dans `fichier` tourne encore (même
    contrôle que demande.fermer_precedent : notre terminal, notre titre)."""
    try:
        with open(fichier, encoding="ascii") as f:
            pid = int(f.read(16).strip())
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
            argv = f.read(1 << 16).split(b"\0")
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return pid > 1 and argv[:1] == [d.TERMINAL.encode()] and f"--title={d.TITRE}".encode() in argv


def _appelant(environ, service):
    if environ.get("QREXEC_REMOTE_DOMAIN") != "dom0":
        print(f"refusé : {service} n'accepte que dom0", file=sys.stderr)
        return False
    return True


def console(environ=None, lancer=subprocess.Popen, racine=None, proc="/proc"):
    environ = os.environ if environ is None else environ
    if not _appelant(environ, "qaf.Console"):
        return 1
    if environ.get("QREXEC_SERVICE_ARGUMENT", ""):
        print("refusé : qaf.Console ne prend aucun argument", file=sys.stderr)
        return 1
    fichier = d.fichier_terminal(racine)
    if fichier and terminal_ouvert(fichier, proc):
        print("ok ouverte")
        return 0
    cwd = d.dossier_de_travail(environ)
    env = {k: v for k, v in environ.items() if k not in (d.VARIABLE_JETON, "QAF_REQUEST")}
    j = d.jeton(environ)
    if j:
        env[d.VARIABLE_JETON] = j
    maison = environ.get("HOME") or "/"
    choisi, suite, lanc = d.agent_choisi(maison, d.conversation_existe(environ, cwd), None, environ.get("PATH"))
    if lanc:
        env = {k: v for k, v in env.items() if k not in lanc.retire}
        fournisseurs.effacer_demande(maison)  # la console ne porte aucune demande
    p = lancer(commande_console(suite, lanc), stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=cwd, env=env)
    d.noter_terminal(fichier, getattr(p, "pid", None))
    fournisseurs.noter_lancement(maison, choisi, time.time())
    print("ok")
    return 0


def connexion(environ=None, lancer=subprocess.Popen):
    environ = os.environ if environ is None else environ
    if not _appelant(environ, "qaf.Connexion"):
        return 1
    f = environ.get("QREXEC_SERVICE_ARGUMENT", "")
    if f not in CONNEXIONS:
        print("refusé : fournisseur inconnu", file=sys.stderr)
        return 1
    fournisseurs.effacer_connexion(environ.get("HOME") or "/")
    env = {k: v for k, v in environ.items() if k not in (d.VARIABLE_JETON, "QAF_REQUEST")}
    lancer(commande_connexion(f), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL, start_new_session=True, cwd=environ.get("HOME") or "/", env=env)
    print("ok")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["console"]:
        return console()
    if argv == ["connexion"]:
        return connexion()
    print("usage : console | connexion", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
