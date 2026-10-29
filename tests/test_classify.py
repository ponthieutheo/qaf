import json

import pytest

from qaf_gate import classify as C
from qaf_gate.schema import parse_plan

EXISTANTS = {"dom0", "sys-net", "sys-firewall", "work", "vault", "debian-13-xfce", "agent-dev"}
AGENT = {"agent-dev"}


def classer(*ops, influence=False, mode=C.NORMAL):
    p = parse_plan(json.dumps({"version": 1, "request": "R-0001", "ops": list(ops)}).encode())
    return C.classer(p, agent_qubes=AGENT, existing=EXISTANTS, influence=influence, mode=mode)


@pytest.mark.parametrize("op,niveau", [
    ({"op": "vm.list"}, C.N0),
    ({"op": "vm.info", "name": "vault"}, C.N0),
    ({"op": "vm.create", "name": "neuf", "template": "debian-13-xfce", "label": "red"}, C.N1),
    ({"op": "vm.start", "name": "agent-dev"}, C.N1),
    ({"op": "vm.start", "name": "work"}, C.N2),
    ({"op": "vm.shutdown", "name": "work"}, C.N2),
    ({"op": "vm.remove", "name": "agent-dev"}, C.N1),
    ({"op": "vm.remove", "name": "work"}, C.N3),
    ({"op": "vm.set_netvm", "name": "agent-dev", "netvm": "sys-firewall"}, C.N2),  # C2
    ({"op": "vm.set_netvm", "name": "vault", "netvm": "sys-net"}, C.N4),  # coffre
    ({"op": "vm.set_netvm", "name": "work", "netvm": "sys-net"}, C.N3),
    ({"op": "vm.start", "name": "vault"}, C.N3),
    ({"op": "vm.remove", "name": "vault"}, C.N4),
    ({"op": "dispvm.run_task", "task": "x", "template": "vault"}, C.N4),
    # H1 : modèle de jetable hors de la liste blanche de dom0 = N4.
    ({"op": "dispvm.run_task", "task": "x", "template": "work"}, C.N4),
    ({"op": "dispvm.run_task", "task": "x", "template": "agent-dev"}, C.N4),
    ({"op": "dispvm.run_task", "task": "x", "template": "qaf-dvm"}, C.N2),
    # Infrastructure des jetables : la modifier = N4.
    ({"op": "vm.start", "name": "qaf-dvm"}, C.N4),
    ({"op": "vm.set_netvm", "name": "qaf-dvm", "netvm": "sys-firewall"}, C.N4),
    ({"op": "vm.remove", "name": "tpl-qaf-hand"}, C.N4),
    ({"op": "vm.info", "name": "qaf-dvm"}, C.N0),
    ({"op": "vm.set_netvm", "name": "agent-dev", "netvm": None}, C.N1),
    ({"op": "vm.set_netvm", "name": "work", "netvm": None}, C.N2),
    ({"op": "dispvm.run_task", "task": "x"}, C.N2),
    ({"op": "vm.shutdown", "name": "dom0"}, C.N4),
])
def test_niveaux(op, niveau):
    assert classer(op).niveaux == (niveau,)


def test_modele_de_jetable_liste_par_utilisateur_n3():
    # Un modèle ajouté par l'utilisateur à la liste blanche peut contenir ses données : N3.
    p = parse_plan(json.dumps({"version": 1, "request": "R-0001", "ops": [
        {"op": "dispvm.run_task", "task": "x", "template": "default-dvm"}]}).encode())
    r = C.classer(p, agent_qubes=AGENT, existing=EXISTANTS, influence=False,
                  jetables=C.JETABLES_DEFAUT | {"default-dvm"})
    assert r.niveaux == (C.N3,) and r.decision == C.CONFIRMER


@pytest.mark.parametrize("niveau,influence,mode,attendu", [
    (C.N0, True, C.NORMAL, C.AUTO),
    (C.N1, True, C.NORMAL, C.AUTO),
    (C.N2, False, C.NORMAL, C.AUTO),
    (C.N2, True, C.NORMAL, C.CONFIRMER),
    (C.N3, False, C.NORMAL, C.CONFIRMER),
    (C.N3, True, C.NORMAL, C.CONFIRMER_RENFORCE),
    (C.N4, False, C.NORMAL, C.REFUSER),
    (C.N2, False, C.PILOTE, C.AUTO),
    (C.N2, True, C.PILOTE, C.CONFIRMER),  # ex. réseau pour un qube de l'agent (C2)
    (C.N3, False, C.PILOTE, C.AUTO),
    (C.N3, True, C.PILOTE, C.CONFIRMER_RENFORCE),  # D5 : jamais levé
    (C.N4, False, C.PILOTE, C.REFUSER),
])
def test_tableau_decisions(niveau, influence, mode, attendu):
    assert C.decision(niveau, influence, mode) == attendu


def test_decision_la_plus_stricte_du_plan():
    r = classer({"op": "vm.list"}, {"op": "vm.remove", "name": "work"}, {"op": "vm.start", "name": "work"},
                influence=True)
    assert r.niveaux == (C.N0, C.N3, C.N2) and r.decision == C.CONFIRMER_RENFORCE


def test_qube_cree_dans_le_plan_compte_comme_agent():
    r = classer({"op": "vm.create", "name": "neuf", "template": "debian-13-xfce", "label": "red"},
                {"op": "vm.start", "name": "neuf"}, {"op": "vm.remove", "name": "neuf"}, influence=True)
    assert r.niveaux == (C.N1, C.N1, C.N1) and r.decision == C.AUTO


def test_creer_un_nom_existant_ne_le_donne_pas_a_l_agent():
    # « créer » work (échouera) puis le supprimer : reste un qube de l'utilisateur
    r = classer({"op": "vm.create", "name": "work", "template": "debian-13-xfce", "label": "red"},
                {"op": "vm.remove", "name": "work"})
    assert r.niveaux == (C.N1, C.N3) and r.decision == C.CONFIRMER


def test_mode_inconnu():
    with pytest.raises(ValueError):
        classer({"op": "vm.list"}, mode="yolo")


def test_coffre_toujours_confirme_meme_en_pilote():
    r = classer({"op": "vm.start", "name": "vault"}, mode=C.PILOTE)
    assert r.niveaux == (C.N3,) and r.decision == C.CONFIRMER
