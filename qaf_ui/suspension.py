"""qaf-suspendre : suspension des confirmations du Doorman, comme le Passwordless
Sudo d'Omarchy (28/10). À lancer en root dans dom0 (sudo) : seul l'utilisateur y a
la main. Le Doorman lit le fichier (qaf_gate.pause.suspendue) ; ici, seulement
l'écrire, l'effacer et dire où on en est (icône de la barre : qaf-suspension).
"""

import json
import os
import sys
import time

from qaf_gate import pause

from .i18n import t

DUREES = {"15m": 900, "1h": 3600, "1j": 86400, "permanent": None}


def echeance(chemin=pause.SUSPENSION, maintenant=time.time, proprietaire=0):
    """None : pas de suspension ; "permanent" ; sinon secondes restantes."""
    if not pause.suspendue(chemin, maintenant, proprietaire):
        return None
    with open(chemin, encoding="ascii") as f:
        fin = json.loads(f.read(128))["jusqua"]
    return "permanent" if fin is None else fin - int(maintenant())


def main(argv=None, chemin=pause.SUSPENSION, maintenant=time.time, proprietaire=0):
    """qaf-suspendre [15m | 1h | 1j | permanent | off | etat] — root, dans dom0."""
    args = sys.argv[1:] if argv is None else argv
    action = args[0] if args else "etat"
    if action in DUREES:
        d = DUREES[action]
        # 0644 quel que soit l'umask hérité de sudo (0077 vu sur une vraie machine : fichier
        # en 0600, illisible par l'exécuteur et par l'icône, donc sans effet). Un
        # fichier déjà là garderait son mode : il est d'abord retiré.
        ancien = os.umask(0o022)
        try:
            pause.lever((chemin,))
            pause.mettre(chemin, json.dumps({"jusqua": None if d is None else int(maintenant()) + d}), brut=True)
        finally:
            os.umask(ancien)
        print(t("suspendre.permanent") if d is None else t("suspendre.pour", duree=action))
    elif action == "off":
        pause.lever((chemin,))
        print(t("suspendre.retablies"))
    elif action == "etat":
        e = echeance(chemin, maintenant, proprietaire)
        print(t("suspendre.etat.actives") if e is None else t("suspendre.etat.permanent") if e == "permanent"
              else t("suspendre.etat.secondes", s=e))
    else:
        print(t("suspendre.usage"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
