"""Lanceur du serveur MCP qaf pour les CLI autres que Claude Code.

Plusieurs CLI filtrent l'environnement de leurs serveurs MCP, ou les font lancer par
un démon : QAF_REQUEST peut ne pas arriver. Le lanceur reprend donc l'identifiant,
dans l'ordre : variable valide, puis ~/.config/qaf/demande écrit par qaf.Request. Une
interpolation non résolue (« ${QAF_REQUEST} » arrivé tel quel) n'est pas valide et
disparaît : sinon le plan serait refusé au lieu de valoir R-0000. Sans rien, le repli
habituel de demande_courante s'applique.

Aucun risque de droits : l'identifiant n'est qu'un renvoi, le Doorman ne croit que
son propre registre (qaf_mcp/demande.py). Lancé par python3 -I avec son chemin absolu :
ni PYTHONPATH ni cwd, que certaines CLI refusent ou ne fixent pas.
"""

import os
import re
import sys

BIBLIOTHEQUE = "/usr/lib/qaf"
VARIABLE = "QAF_REQUEST"
FICHIER = (".config", "qaf", "demande")
FORMAT = re.compile(r"\AR-[0-9]{4,8}\Z")  # = qaf_gate.schema._REQUEST_RE (vérifié par les tests)


def preparer(environ):
    """Pose ou retire QAF_REQUEST dans `environ`."""
    if FORMAT.match(environ.get(VARIABLE, "")):
        return
    environ.pop(VARIABLE, None)
    maison = environ.get("HOME") or os.path.expanduser("~")
    try:
        with open(os.path.join(maison, *FICHIER), encoding="ascii") as f:
            rid = f.read(32).strip()
    except (OSError, UnicodeDecodeError):
        return
    if FORMAT.match(rid):
        environ[VARIABLE] = rid


def main():
    preparer(os.environ)
    if BIBLIOTHEQUE not in sys.path:
        sys.path.insert(0, BIBLIOTHEQUE)
    from qaf_mcp.server import main as serveur
    serveur()


if __name__ == "__main__":
    main()
