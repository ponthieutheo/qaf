"""Journaux de démonstration du panneau agent (rendus hors écran et tests).

Chaque scénario écrit un VRAI journal chaîné (qaf_gate.journal.Journal) dans un
dossier temporaire, puis renvoie la fenêtre du panneau branchée dessus :
  tools/rendu-ui.sh agent_demo:actif   docs/captures/design/ecrans/agent-actif.png --exiger
  tools/rendu-ui.sh agent_demo:attente docs/captures/design/ecrans/agent-attente.png --exiger
  tools/rendu-ui.sh agent_demo:pause   docs/captures/design/ecrans/agent-pause.png --exiger
Jamais installé dans dom0.
"""

import dataclasses
import os
import tempfile
import time
from unittest import mock

from qaf_gate.journal import Journal
from qaf_ui import palette
from qaf_ui.ecrans import agent_etat as ae

H = "a" * 64
A = "disp6000"  # agent jetable de la démonstration (AGENT-JETABLE)


def _plan(sha, ops, decision, cibles=None, request="R-1042", appelant=A):
    ev = {"type": "plan", "appelant": appelant, "request": request, "sha256": sha, "ops": ops,
          "niveaux": [1] * len(ops), "decision": decision, "demande_connue": True, "hors_portee": []}
    if cibles is not None:
        ev["cibles"] = cibles
    return ev


def _op(sha, i, op, ok=True, request="R-1042", appelant=A):
    return {"type": "op", "appelant": appelant, "request": request, "sha256": sha, "i": i, "op": op,
            "ok": ok, "code": None if ok else "refus"}


PROPRE = [palette.AgentOuvert(A, 9 * 60, False, "crée un espace dev, donne-lui Internet et installe tree")]
INFLUENCE = [palette.AgentOuvert(A, 9 * 60, True, "crée un espace dev, donne-lui Internet et installe tree"),
             palette.AgentOuvert("disp5859", 60, False, "liste mes qubes")]
# Demandes tapées par l'utilisateur, pour les rendus en anglais et en chinois (tools/rendu-ui.sh … --langue en|zh).
DEMANDES = {"en": {"crée un espace dev, donne-lui Internet et installe tree":
                   "create a dev qube, give it Internet and install tree", "liste mes qubes": "list my qubes"},
            "zh": {"crée un espace dev, donne-lui Internet et installe tree":
                   "创建一个 dev qube，给它联网，再安装 tree", "liste mes qubes": "列出我的 qube"}}


def _dans_la_langue(agents):
    from qaf_ui import i18n
    table = DEMANDES.get(i18n.langue())
    if table is None:
        return agents
    return [dataclasses.replace(a, texte=table.get(a.texte, a.texte)) for a in agents]


def ecrire(evenements, dossier=None, debut=None):
    """Écrit les événements (décalage en secondes, événement) ; renvoie le chemin."""
    dossier = dossier or tempfile.mkdtemp(prefix="qaf-agent-")
    chemin = os.path.join(dossier, "journal.jsonl")
    j = Journal(chemin)
    debut = debut if debut is not None else int(time.time()) - 600
    for decalage, ev in evenements:
        with mock.patch("time.time", return_value=debut + decalage):
            j.append(ev)
    return chemin


def scenario_actif():
    return [
        (0, {"type": "demande", "request": "R-1042", "qubes": [], "sha256_texte": H, "agent": A}),
        (1, {"type": "agent_debut", "agent": A, "uuid": "u-1", "cli": "claude"}),
        (5, _plan("1" * 64, ["vm.create", "vm.set_netvm"], "auto", ["dev", "dev"])),
        (6, _op("1" * 64, 0, "vm.create")),
        (40, _op("1" * 64, 1, "vm.set_netvm")),
        (60, _plan("2" * 64, ["vm.set_netvm"], "refuser", ["vault"])),
        (90, {"source": "validateur", "event": {"type": "refus", "appelant": A,
                                                 "raison": "N4 : R-0001 <b>coffre</b>", "ops": ["prefs.set"]}}),
        (120, _plan("3" * 64, ["vm.start", "pkg.install"], "auto", ["dev", "dev"])),
        (121, _op("3" * 64, 0, "vm.start")),
        (180, _op("3" * 64, 1, "pkg.install")),
    ]


def scenario_attente():
    return scenario_actif() + [
        (300, _plan("4" * 64, ["qube.screenshot"], "auto", ["untrusted"])),
        (301, _op("4" * 64, 0, "qube.screenshot")),
        (500, _plan("5" * 64, ["vm.set_netvm"], "confirmer_renforce", ["loisirs"])),
    ]


def scenario_pause():
    return scenario_attente()[:-1] + [
        (420, _plan("6" * 64, ["qube.exec", "vm.remove"], "auto", ["dev", "travail"])),
        (421, _op("6" * 64, 0, "qube.exec")),
        (422, {"type": "interruption", "appelant": A, "request": "R-1042", "sha256": "6" * 64,
               "op": 1, "raison": "pause manuelle (qaf-pause)"}),
    ]


def source(evenements, en_pause=False, debut_executeur=None, agents=None, permanent=False, dev=False):
    """Fonction source() du panneau sur un journal temporaire. agents : liste
    des agents ouverts que rendrait le Doorman (None : portier muet) ;
    permanent : agent permanent sys-agent (D28), faux pour les scènes de jetables ;
    dev : politique de l'exception de développement posée (mode développement)."""
    dossier = tempfile.mkdtemp(prefix="qaf-agent-")
    maintenant = int(time.time())
    chemin = ecrire(evenements, dossier, debut=maintenant - 600)
    pauses = (os.path.join(dossier, "pause"), os.path.join(dossier, "pause-auto"))
    if en_pause:
        open(pauses[0], "w").close()
    politique_dev = os.path.join(dossier, "20-qaf-dev-dom0.policy")
    if dev:
        open(politique_dev, "w").close()
    debut = debut_executeur if debut_executeur is not None else maintenant - 3600
    return lambda: ae.instantane(chemin, pauses, portier=lambda: (True, debut),
                                 agents=lambda: None if agents is None else list(agents),
                                 permanent=lambda: permanent, dev=politique_dev)


def _fenetre(evenements, **kw):
    from qaf_ui.ecrans import agent
    return agent.construire(source=source(evenements, **kw),
                            basculer=lambda m: ae.Resultat(True, "essai"))


def actif():
    return _fenetre(scenario_actif(), agents=_dans_la_langue(PROPRE))


def attente():
    return _fenetre(scenario_attente(), agents=_dans_la_langue(INFLUENCE))


def pause():
    return _fenetre(scenario_pause(), en_pause=True, agents=_dans_la_langue(INFLUENCE[:1]))


def pret():
    """Aucun agent ouvert : le dernier s'est fermé ; le fil garde ce qu'il a fait."""
    fin = scenario_actif() + [(400, {"type": "agent_tue", "agent": A, "raison": "inactif"})]
    return _fenetre(fin, agents=[])


def permanent_dev():
    """Agent permanent sys-agent, exception de développement posée."""
    return _fenetre(scenario_actif(), agents=[], permanent=True, dev=True)
