"""Arrêt d'urgence (kill switch) : tant qu'un fichier de pause existe, le
validateur et l'exécuteur refusent tout, et l'exécuteur s'arrête entre deux
opérations.

Deux fichiers :
- PAUSE_MANUELLE, posé par l'utilisateur avec `qaf-pause` (dossier de root : ni le
  validateur ni l'exécuteur ne peuvent l'effacer) ;
- PAUSE_AUTO, posé par l'exécuteur à la demande du validateur (débit dépassé),
  dans le dossier de l'exécuteur où le validateur n'écrit pas ; effacé par
  `qaf-pause off`.
Seul os.lstat est utilisé : le contenu n'est jamais lu et la raison renvoyée
est fixe. Tout ce qui existe à ces chemins (fichier, lien symbolique, FIFO…)
compte comme une pause sans être ouvert ; un état illisible aussi (échec fermé).

Suspension des confirmations (qaf-suspendre, « sudoless » d'Omarchy, 28/10) :
voir suspendue(). La pause l'emporte toujours ; N4 reste refusé.
"""

import json
import os
import stat
import sys
import time

PAUSE_MANUELLE = "/var/lib/qaf/pause"
PAUSE_AUTO = "/var/lib/qaf-exec/pause-auto"
CHEMINS = (PAUSE_MANUELLE, PAUSE_AUTO)
RAISONS = ("pause manuelle (qaf-pause)", "pause automatique (débit dépassé)")
SUSPENSION = "/var/lib/qaf/suspension"


def raison(chemins=CHEMINS):
    """Raison FIXE de la pause, ou None si le Doorman n'est pas en pause."""
    for i, p in enumerate(chemins):
        try:
            os.lstat(p)
        except FileNotFoundError:
            continue
        except OSError:
            return f"{RAISONS[min(i, 1)]}, état illisible"
        return RAISONS[min(i, 1)]
    return None


def suspendue(chemin=SUSPENSION, maintenant=time.time, proprietaire=0):
    """Vrai si la suspension écrite par qaf-suspendre court encore. Absente, pas à
    root, modifiable par d'autres ou illisible : non (échec fermé)."""
    try:
        st = os.lstat(chemin)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != proprietaire or st.st_mode & 0o022:
            return False
        with open(chemin, encoding="ascii") as f:
            fin = json.loads(f.read(128))["jusqua"]
        return fin is None or (type(fin) is int and maintenant() < fin)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def mettre(chemin, texte, brut=False):
    """Écrit un fichier de pause (texte pour les humains, jamais relu)."""
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texte if brut else texte[:200] + "\n")


def lever(chemins=CHEMINS):
    for p in chemins:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


def main(argv=None, chemins=CHEMINS):
    """qaf-pause [on [raison] | off | status] — à lancer en root dans dom0."""
    args = sys.argv[1:] if argv is None else argv
    action = args[0] if args else "on"
    if action == "on":
        os.makedirs(os.path.dirname(chemins[0]), mode=0o755, exist_ok=True)
        mettre(chemins[0], " ".join(args[1:]) or "pause manuelle (qaf-pause)")
        print("Doorman en pause : tout plan est refusé.")
    elif action == "off":
        lever(chemins)
        print("Pause levée.")
    elif action == "status":
        r = raison(chemins)
        print(f"EN PAUSE : {r}" if r else "actif")
    else:
        print("usage : qaf-pause [on [raison] | off | status]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
