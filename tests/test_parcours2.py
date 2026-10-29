"""PARCOURS-2 rejoué avec l'agent jetable (docs/AGENT-JETABLE.md §13.1, §14) :
p2 crée tpl-essai-dev et essai-dev ; p6, p7 et p9, chacun tapé comme une
nouvelle demande (donc un agent neuf), ne demandent plus aucune fenêtre.
Avant : 1, 1 et 2 confirmations renforcées (« ne découle pas de ta demande »)."""

from qaf_gate import classify as C
from qaf_gate import proto
from test_agent_jetable import dossier, journal, lancer, monde, plan  # noqa: F401 (fixture)


def p2(ex):
    a, rid = lancer(ex, "crée-moi un espace essai-dev et installe tree dedans")
    for op in ({"op": "vm.clone", "name": "tpl-essai-dev", "source": "fedora-42-xfce", "label": "black"},
               {"op": "pkg.install", "name": "tpl-essai-dev", "packages": ["tree"]},
               {"op": "vm.create", "name": "essai-dev", "template": "tpl-essai-dev", "label": "blue"}):
        assert plan(ex, a, op, request=rid)["statut"] == "fait", op
    return a


def test_p6_mise_a_jour_du_modele_de_l_agent(dossier):  # noqa: F811
    ex, conf = monde(dossier)
    p2(ex)
    b, rid = lancer(ex, "mets à jour essai-dev")
    r = plan(ex, b, {"op": "update.run", "targets": ["tpl-essai-dev"], "apply": "none"}, request=rid)
    assert r["statut"] == "fait" and conf.demandes == []
    assert journal(dossier)[-3]["hors_portee"] == []


def test_p9_installation_durable(dossier):  # noqa: F811
    ex, conf = monde(dossier)
    p2(ex)
    b, rid = lancer(ex, "installe tree durablement dans essai-dev")
    r = plan(ex, b, {"op": "pkg.install", "name": "tpl-essai-dev", "packages": ["tree"]}, request=rid)
    assert r["statut"] == "fait" and conf.demandes == []


def test_p7_suppression_de_l_espace_et_de_son_modele(dossier):  # noqa: F811
    ex, conf = monde(dossier)
    p2(ex)
    b, rid = lancer(ex, "supprime essai-dev")
    r = plan(ex, b, {"op": "vm.remove", "name": "essai-dev"},
             {"op": "template.remove", "template": "tpl-essai-dev"}, request=rid)
    assert r["statut"] == "fait" and conf.demandes == []


def test_p7_modele_supprime_plus_tard_par_un_autre_agent_confirmation_simple(dossier):  # noqa: F811
    # R-9 : une fois l'espace supprimé, le modèle n'entre plus dans la portée par
    # n1 ; le supprimer (sans retour) depuis un autre jetable demande une
    # confirmation simple, sans code.
    ex, conf = monde(dossier, reponses=[True])
    p2(ex)
    b, rid = lancer(ex, "supprime essai-dev")
    assert plan(ex, b, {"op": "vm.remove", "name": "essai-dev"}, request=rid)["statut"] == "fait"
    r = plan(ex, b, {"op": "template.remove", "template": "tpl-essai-dev"}, request=rid)
    assert r["statut"] == proto.FAIT and [d.decision for d in conf.demandes] == [C.CONFIRMER]


def test_un_modele_de_l_agent_qui_porte_un_qube_de_utilisateur_reste_a_nommer(dossier):  # noqa: F811
    ex, conf = monde(dossier)
    p2(ex)
    ex.backend.create_appvm("loisirs", "tpl-essai-dev", "green")  # qube de l'utilisateur sur ce modèle
    b, rid = lancer(ex, "installe tree durablement dans essai-dev")
    r = plan(ex, b, {"op": "pkg.install", "name": "tpl-essai-dev", "packages": ["tree"]}, request=rid)
    assert r["statut"] == proto.REFUSE and conf.demandes[-1].decision == C.CONFIRMER_RENFORCE
