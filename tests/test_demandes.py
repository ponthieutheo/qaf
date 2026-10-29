"""Registre des demandes de l'utilisateur et portée (C13).

La palette transmet le texte à l'exécuteur, qui tire l'identifiant, calcule les
qubes nommés et confronte ensuite chaque plan à sa demande.
"""

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import pytest

from qaf_gate import classify as C
from qaf_gate import confirm, executor, proto, registry, schema, validator
from qaf_gate.journal import Journal, verify
from qaf_gate.registry import Demandes, Registry, noms_cites
from backend_sim import SimBackend

EXISTANTS = {"dom0", "sys-net", "sys-firewall", "work", "vault", "loisirs", "debian-13-xfce", "dev.web"}


# --- extraction des noms --------------------------------------------------

@pytest.mark.parametrize("texte,attendu", [
    ("démarre work", {"work"}),
    ("Démarre WORK.", {"work"}),  # casse ignorée, point final ôté
    ("arrête work, puis loisirs !", {"work", "loisirs"}),
    ("ouvre l'espace work-", {"work"}),
    ("le network est lent", set()),  # pas de sous-chaîne
    ("workspace et perso2", set()),
    ("lance dev.web stp", {"dev.web"}),
    ("lance dev.web.", {"dev.web"}),
    ("crée-moi un espace dev Debian avec git", set()),  # dev n'existe pas encore
    ("supprime le qube «vault»", {"vault"}),
    ("", set()),
])
def test_noms_cites(texte, attendu):
    assert noms_cites(texte, EXISTANTS) == attendu


def test_noms_cites_ne_nomme_que_l_existant():
    assert noms_cites("supprime fantome et work", {"work"}) == {"work"}


# --- registre des demandes -----------------------------------------------

class Horloge:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_ajout_identifiant_tire_et_persistant(tmp_path):
    h = Horloge()
    d = Demandes(str(tmp_path / "demandes.json"), horloge=h)
    rid, e = d.ajouter("démarre work", EXISTANTS)
    assert schema._REQUEST_RE.match(rid) and len(rid) == 8 and rid != "R-0000"
    assert e == {"texte": "démarre work", "ts": int(h.t), "qubes": ["work"], "agent": "sys-agent"}
    relu = Demandes(str(tmp_path / "demandes.json"), horloge=h)
    assert relu.valide(rid) == {**e, "vu": int(h.t)}
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".registre-")]  # écriture atomique


def test_identifiants_distincts(tmp_path):
    d = Demandes(str(tmp_path / "d.json"))
    ids = {d.ajouter(f"demande {i}", EXISTANTS)[0] for i in range(50)}
    assert len(ids) == 50


def test_inconnue_et_expiree(tmp_path):
    h = Horloge()
    d = Demandes(str(tmp_path / "d.json"), horloge=h)
    rid, _ = d.ajouter("x", EXISTANTS)
    assert d.valide("R-0000") is None and d.valide("R-123456" if rid != "R-123456" else "R-654321") is None
    s = "S-0123456789abcdef"
    while h.t < 1_000_000 + registry.DUREE_DEMANDE - registry.INACTIVITE:  # session active : la demande tient
        h.t += registry.INACTIVITE - 1
        assert d.valide(rid, s) is not None
    h.t = 1_000_000 + registry.DUREE_DEMANDE - 1
    assert d.valide(rid, s) is not None
    h.t += 1  # plafond absolu, même avec une session active
    assert d.valide(rid, s) is None
    h.t = 0  # horloge revenue en arrière : pas de demande « du futur »
    assert d.valide(rid, s) is None


# --- PARCOURS-1 M3 : demande liée à la session de l'agent ------------------------------

S1, S2 = "S-0000000000000001", "S-0000000000000002"


def test_session_notee_pour_l_affichage_seulement(tmp_path):
    # Revue du 25/10 : l'identifiant de session vient de sys-agent ; il ne décide jamais de la validité.
    h = Horloge()
    d = Demandes(str(tmp_path / "d.json"), horloge=h)
    rid, _ = d.ajouter("démarre work", EXISTANTS)
    assert d.valide(rid, S1)["session"] == S1
    assert d.valide(rid, S2)["session"] == S1  # toujours valide, la première session reste notée
    assert d.valide(rid) is not None
    assert Demandes(str(tmp_path / "d.json"), horloge=h).valide(rid, S2)["session"] == S1


def test_demande_expire_dix_minutes_apres_le_dernier_plan(tmp_path):
    h = Horloge()
    d = Demandes(str(tmp_path / "d.json"), horloge=h)
    rid, _ = d.ajouter("démarre work", EXISTANTS)
    h.t += registry.INACTIVITE - 1
    assert d.valide(rid) is not None  # chaque plan prolonge, avec ou sans session
    h.t += registry.INACTIVITE - 1
    assert d.valide(rid, S1) is not None
    h.t += registry.INACTIVITE
    assert d.valide(rid, S1) is None


def test_seule_la_derniere_demande_est_valide(tmp_path):
    # P1 : l'agent ne peut pas citer une demande précédente (plus large) pour
    # faire sauter l'alerte « hors portée ».
    h = Horloge()
    d = Demandes(str(tmp_path / "d.json"), horloge=h)
    ancienne, _ = d.ajouter("arrête work et loisirs", EXISTANTS)
    h.t += 60
    courante, _ = d.ajouter("quelle heure est-il ?", EXISTANTS)
    assert d.valide(ancienne) is None and d.valide(courante) is not None
    assert list(d.demandes) == [courante]
    assert Demandes(str(tmp_path / "d.json"), horloge=h).valide(ancienne) is None  # aussi après relecture
    assert d.age(d.valide(courante)) == 0
    h.t += 90
    assert d.age(d.valide(courante)) == 90


def test_ancien_registre_a_plusieurs_demandes_ne_vaut_rien(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"version": 1, "demandes": {
        "R-000001": {"texte": "a", "ts": 1_000_000, "qubes": []},
        "R-000002": {"texte": "b", "ts": 1_000_000, "qubes": []}}}))
    d = Demandes(str(p), horloge=Horloge())
    assert d.valide("R-000001") is None and d.valide("R-000002") is None


@pytest.mark.parametrize("texte", ["", "   \n ", "a" * (registry.MAX_TEXTE + 1), "x\x1b[31m", "a‮b",
                                   "a b", None, 42])
def test_texte_refuse(tmp_path, texte):
    d = Demandes(str(tmp_path / "d.json"))
    with pytest.raises(ValueError):
        d.ajouter(texte, EXISTANTS)
    assert not os.path.exists(tmp_path / "d.json")


def test_registre_illisible_refuse(tmp_path):
    p = tmp_path / "d.json"
    p.write_text('{"version": 99, "demandes": {}}')
    with pytest.raises(ValueError):
        Demandes(str(p))


# --- portée dans le classement -------------------------------------------

def classer(*ops, portee=None, influence=False, mode=C.NORMAL, agent=("agent-dev",)):
    p = schema.parse_plan(json.dumps({"version": 1, "request": "R-123456", "ops": list(ops)}).encode())
    return C.classer(p, agent_qubes=set(agent), existing=EXISTANTS | set(agent), influence=influence,
                     mode=mode, portee=portee)


CREER = {"op": "vm.create", "name": "neuf", "template": "debian-13-xfce", "label": "gray"}


def test_sans_portee_rien_ne_change():
    assert classer({"op": "vm.shutdown", "name": "work"}).decision == C.AUTO
    assert classer({"op": "vm.shutdown", "name": "work"}).hors_portee == ()


def test_qube_nomme_dans_la_demande():
    cl = classer({"op": "vm.shutdown", "name": "work"}, portee={"work"})
    assert (cl.decision, cl.hors_portee) == (C.AUTO, ())


def test_qube_non_nomme_confirmation_renforcee():
    cl = classer({"op": "vm.shutdown", "name": "loisirs"}, portee={"work"})
    assert (cl.decision, cl.hors_portee) == (C.CONFIRMER_RENFORCE, (0,))


def test_qube_de_l_agent_hors_portee_aussi():
    # N1 sur un qube de l'agent créé pour une AUTRE demande : ne découle pas de
    # celle-ci, c'est journalisé (hors_portee), mais n2 (a) (AGENT-JETABLE
    # §13.2) : le plancher de portée ne vaut qu'à partir de N2.
    cl = classer({"op": "vm.start", "name": "agent-dev"}, portee=set())
    assert cl.niveaux == (C.N1,) and cl.decision == C.AUTO and cl.hors_portee == (0,)


def test_creation_dans_le_plan_entre_dans_la_portee():
    cl = classer(CREER, {"op": "vm.start", "name": "neuf"}, portee={"debian-13-xfce"})
    assert (cl.decision, cl.hors_portee) == (C.AUTO, ())


def test_tous_les_qubes_references_sont_compares():
    # Portée = tous les champs de type qube (schema.CHAMPS_QUBE) : netvm de
    # vm.set_netvm, template d'un jetable. Le modèle d'un vm.create n'expose rien :
    # « crée-moi un espace dev Debian » passe sans nommer debian-13-xfce.
    cl = classer(CREER, portee=set())
    assert (cl.decision, cl.hors_portee) == (C.AUTO, ())
    cl = classer({**CREER, "template": "vault"}, portee=set())
    assert cl.decision == C.REFUSER  # un coffre comme modèle reste N4
    cl = classer({"op": "vm.set_netvm", "name": "agent-dev", "netvm": "sys-net"}, portee={"agent-dev"})
    assert cl.hors_portee == (0,)
    cl = classer({"op": "vm.set_netvm", "name": "agent-dev", "netvm": "sys-net"}, portee={"agent-dev", "sys-net"})
    assert cl.hors_portee == ()
    cl = classer({"op": "vm.set_netvm", "name": "agent-dev", "netvm": None}, portee={"agent-dev"})
    assert cl.hors_portee == ()  # netvm null : aucun qube nommé


def test_champs_qube_tires_du_schema():
    assert schema.CHAMPS_QUBE["vm.create"] == ("name", "template")
    assert schema.CHAMPS_QUBE["vm.set_netvm"] == ("name", "netvm")
    assert schema.CHAMPS_QUBE["dispvm.run_task"] == ("template",)
    assert schema.CHAMPS_QUBE["vm.list"] == ()
    assert set(schema.CHAMPS_QUBE) == set(schema.OPS)


def test_creation_d_un_nom_existant_n_entre_pas_dans_la_portee():
    creer_work = {**CREER, "name": "work"}
    cl = classer(creer_work, {"op": "vm.shutdown", "name": "work"}, portee={"debian-13-xfce"})
    assert cl.hors_portee == (1,)


def test_lecture_exemptee():
    cl = classer({"op": "vm.list"}, {"op": "vm.info", "name": "loisirs"}, portee=set())
    assert (cl.decision, cl.hors_portee) == (C.AUTO, ())


def test_jetable_modele_hors_portee():
    assert classer({"op": "dispvm.run_task", "task": "x"}, portee=set()).decision == C.AUTO
    cl = classer({"op": "dispvm.run_task", "task": "x", "template": "qaf-dvm"}, portee=set())
    assert cl.hors_portee == (0,) and cl.decision == C.CONFIRMER_RENFORCE
    cl = classer({"op": "dispvm.run_task", "task": "x", "template": "agent-dev"}, portee=set())
    assert cl.decision == C.REFUSER  # H1 : hors liste blanche


def test_pilote_ne_leve_pas_l_alerte():
    cl = classer({"op": "vm.shutdown", "name": "loisirs"}, portee={"work"}, mode=C.PILOTE)
    assert cl.decision == C.CONFIRMER_RENFORCE


def test_n4_reste_refuse():
    cl = classer({"op": "vm.remove", "name": "vault"}, portee=set())
    assert cl.decision == C.REFUSER


def test_coffre_nomme_reste_confirme():
    # Nommer le coffre dans la demande n'affaiblit rien : toujours confirmé.
    cl = classer({"op": "vm.start", "name": "vault"}, portee={"vault"}, mode=C.PILOTE)
    assert cl.decision == C.CONFIRMER


OPS_FUZZ = [{"op": "vm.list"}, {"op": "vm.info", "name": "work"}, {"op": "vm.start", "name": "work"},
            {"op": "vm.shutdown", "name": "agent-dev"}, {"op": "vm.remove", "name": "loisirs"},
            {"op": "vm.set_netvm", "name": "work", "netvm": "sys-net"},
            {"op": "vm.set_netvm", "name": "agent-dev", "netvm": None},
            {"op": "dispvm.run_task", "task": "t"}, {"op": "dispvm.run_task", "task": "t", "template": "work"},
            CREER, {"op": "vm.start", "name": "neuf"}, {"op": "vm.remove", "name": "vault"}]


@pytest.mark.parametrize("influence", [False, True])
@pytest.mark.parametrize("mode", [C.NORMAL, C.PILOTE])
def test_la_portee_n_affaiblit_jamais(influence, mode):
    import itertools
    ordre = (C.AUTO, C.CONFIRMER, C.CONFIRMER_RENFORCE, C.REFUSER)
    portees = [set(), {"work"}, {"loisirs", "agent-dev"}, {"work", "loisirs", "agent-dev", "vault"}]
    for ops in itertools.product(OPS_FUZZ, repeat=2):
        sans = classer(*ops, influence=influence, mode=mode)
        for p in portees:
            avec = classer(*ops, influence=influence, mode=mode, portee=p)
            assert avec.niveaux == sans.niveaux
            assert ordre.index(avec.decision) >= ordre.index(sans.decision), (ops, p)


# --- exécuteur -----------------------------------------------------------

@pytest.fixture
def dossier():
    d = tempfile.mkdtemp(prefix="qaf-", dir="/tmp")  # chemins de socket courts
    yield d
    shutil.rmtree(d, ignore_errors=True)


def faire(d, reponses=(), horloge=None, influence=False):
    conf = confirm.Scripte(reponses)
    dem = Demandes(f"{d}/demandes.json", **({"horloge": horloge} if horloge else {}))
    ex = executor.Executeur(SimBackend(), Registry(f"{d}/registre.json"), Journal(f"{d}/journal.jsonl"),
                            conf, pauses=(f"{d}/pause",), pause_auto=f"{d}/pause-auto", demandes=dem)
    ex.etat = {"influence": influence, "mode": C.NORMAL}
    ex.backend.create_appvm("loisirs", "debian-13-xfce", "yellow")
    return ex, conf


def envoyer(ex, *ops, request):
    p = schema.parse_plan(json.dumps({"version": 1, "request": request, "ops": list(ops)}).encode())
    return ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": schema.canonicalize(p).decode()})


def demande(ex, texte):
    r = ex.traiter_demande({"type": "demande", "texte": texte})
    assert r["statut"] == proto.FAIT, r
    return r["request"]


def plans(d):
    with open(f"{d}/journal.jsonl", encoding="utf-8") as f:
        return [e["event"] for e in map(json.loads, f)]


def test_demande_enregistree_et_journalisee(dossier):
    ex, _ = faire(dossier)
    r = ex.traiter_demande({"type": "demande", "texte": "arrête work et loisirs"})
    assert r["statut"] == "fait" and r["qubes"] == ["loisirs", "work"]
    e = [x for x in plans(dossier) if x["type"] == "demande"][-1]
    assert e["request"] == r["request"] and "texte" not in e and len(e["sha256_texte"]) == 64
    assert verify(f"{dossier}/journal.jsonl") >= 1


@pytest.mark.parametrize("msg", [{"type": "demande"}, {"type": "plan", "texte": "x"},
                                 {"type": "demande", "texte": "x", "request": "R-0001"},
                                 {"type": "demande", "texte": ""}, {"type": "demande", "texte": 3}])
def test_demande_mal_formee(dossier, msg):
    ex, _ = faire(dossier)
    assert ex.traiter_demande(msg)["statut"] in (proto.ERREUR, proto.REFUSE)
    assert ex.demandes.demandes == {}


def test_sans_registre_pas_de_demande(dossier):
    ex = executor.Executeur(SimBackend(), Registry(f"{dossier}/r.json"), Journal(f"{dossier}/j.jsonl"))
    assert ex.traiter_demande({"type": "demande", "texte": "x"})["statut"] == proto.ERREUR


def test_plan_dans_la_portee_execute(dossier):
    ex, conf = faire(dossier)
    rid = demande(ex, "démarre loisirs")
    assert envoyer(ex, {"op": "vm.start", "name": "loisirs"}, request=rid)["statut"] == "fait"
    assert conf.demandes == [] and plans(dossier)[-1]["statut"] == "fait"


def test_plan_hors_portee_alerte_et_cote_a_cote(dossier):
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "démarre loisirs")
    r = envoyer(ex, {"op": "vm.start", "name": "loisirs"}, {"op": "vm.shutdown", "name": "work"}, request=rid)
    assert r["statut"] == "refuse" and ex.backend.qubes["loisirs"].state == "Halted"
    (d,) = conf.demandes
    assert d.decision == C.CONFIRMER_RENFORCE and d.hors_portee == (1,) and d.texte_demande == "démarre loisirs"
    texte = confirm.resume(d)
    assert "Ta demande (il y a 0 min) : « démarre loisirs »" in texte and rid not in texte and "[N" not in texte
    lignes = texte.splitlines()
    assert "ne découle pas de ta demande" in lignes[-1] and "ne découle pas" not in lignes[-2]
    p = [x for x in plans(dossier) if x["type"] == "plan"][-1]
    assert p["demande_connue"] is True and p["hors_portee"] == [1]
    # Panneau de l'agent (ECRANS-QAF §9.2) : noms visés, alignés sur « ops », validés par le schéma.
    assert p["cibles"] == ["loisirs", "work"]


def test_journal_cibles_sans_qube_vise(dossier):
    ex, _conf = faire(dossier)
    rid = demande(ex, "liste mes qubes et démarre loisirs")
    envoyer(ex, {"op": "vm.list"}, {"op": "vm.start", "name": "loisirs"}, request=rid)
    p = [x for x in plans(dossier) if x["type"] == "plan"][-1]
    assert p["ops"] == ["vm.list", "vm.start"] and p["cibles"] == [None, "loisirs"]


def test_journal_cible_d_un_modele_retire(dossier):
    # Parcours 4 : « a retiré un modèle » sans son nom ; template.* nomme le modèle
    # dans « template », pas dans « name ».
    ex, _conf = faire(dossier)
    rid = demande(ex, "retire le modèle tpl-essai et installe debian-13-minimal")
    envoyer(ex, {"op": "template.remove", "template": "tpl-essai"},
            {"op": "template.install", "template": "debian-13-minimal", "repo": "official"}, request=rid)
    p = [x for x in plans(dossier) if x["type"] == "plan"][-1]
    assert p["cibles"] == ["tpl-essai", "debian-13-minimal"]


def test_demande_inconnue_influencee_et_portee_vide(dossier):
    ex, conf = faire(dossier, reponses=[False, False])
    # N2 sur un qube de l'utilisateur, « non influencé » d'après l'état : renforcé car demande inconnue.
    assert envoyer(ex, {"op": "vm.start", "name": "loisirs"}, request="R-0000")["statut"] == "refuse"
    assert conf.demandes[-1].decision == C.CONFIRMER_RENFORCE
    # La fenêtre reçoit la vraie influence (aucun contenu de qube lu) : PARCOURS-1 M2.
    assert conf.demandes[-1].texte_demande is None and conf.demandes[-1].influence is False
    assert "Tu n'as rien demandé dans la palette" in confirm.resume(conf.demandes[-1])
    # Créer puis démarrer son qube dans le même plan : le modèle d'un vm.create
    # ne compte pas dans la portée, le qube créé y entre : automatique.
    creer = {"op": "vm.create", "name": "neuf", "template": "debian-13-xfce", "label": "gray"}
    n = len(conf.demandes)
    assert envoyer(ex, creer, {"op": "vm.start", "name": "neuf"}, request="R-0000")["statut"] == "fait"
    assert len(conf.demandes) == n  # aucune confirmation demandée
    # Mais donner le réseau à ce qube ensuite (N2) : influencé + hors portée.
    r = envoyer(ex, {"op": "vm.set_netvm", "name": "neuf", "netvm": "sys-firewall"}, request="R-0000")
    assert r["statut"] == "refuse" and conf.demandes[-1].decision == C.CONFIRMER_RENFORCE
    p = [x for x in plans(dossier) if x["type"] == "plan"][-1]
    assert p["demande_connue"] is False


def test_demande_inconnue_rend_la_session_influencee(dossier):
    # Témoin direct de l'influence : un jetable par défaut (N2, sans qube visé,
    # donc jamais hors portée) est automatique pour une demande connue, confirmé
    # pour une demande inconnue.
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "fais une recherche dans un jetable")
    assert envoyer(ex, {"op": "dispvm.run_task", "task": "x"}, request=rid)["statut"] == "fait"
    assert envoyer(ex, {"op": "dispvm.run_task", "task": "x"}, request="R-999999")["statut"] == "refuse"
    assert conf.demandes[-1].decision == C.CONFIRMER and conf.demandes[-1].hors_portee == ()


def test_demande_expiree(dossier):
    h = [1_000_000.0]
    ex, conf = faire(dossier, reponses=[False], horloge=lambda: h[0])
    rid = demande(ex, "démarre loisirs")
    h[0] += registry.DUREE_DEMANDE
    assert envoyer(ex, {"op": "vm.start", "name": "loisirs"}, request=rid)["statut"] == "refuse"
    assert conf.demandes[-1].decision == C.CONFIRMER_RENFORCE and conf.demandes[-1].texte_demande is None


def test_qube_cree_pendant_la_demande_reste_dans_sa_portee(dossier):
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "crée-moi un espace dev sur debian-13-xfce avec git")
    creer = {"op": "vm.create", "name": "dev", "template": "debian-13-xfce", "label": "blue"}
    assert envoyer(ex, creer, request=rid)["statut"] == "fait"
    # plan suivant de la même demande : démarrer dev, automatique (créé pendant la demande)
    assert envoyer(ex, {"op": "vm.start", "name": "dev"}, request=rid)["statut"] == "fait"
    assert conf.demandes == []
    # une autre demande qui ne nomme pas dev : l'arrêter ne découle pas d'elle,
    # c'est journalisé ; N1 sur un qube de l'agent reste automatique (n2 (a))
    autre = demande(ex, "quelle heure est-il ?")
    assert envoyer(ex, {"op": "vm.shutdown", "name": "dev"}, request=autre)["statut"] == "fait"
    assert conf.demandes == [] and plans(dossier)[-3]["hors_portee"] == [0]


def test_qube_recree_par_utilisateur_sous_le_meme_nom_hors_portee(dossier):
    # Le registre lie le qube à son UUID : un « dev » recréé par l'utilisateur n'est pas
    # « créé pendant la demande », même avec le même identifiant.
    ex, conf = faire(dossier, reponses=[False])  # création automatique, démarrage refusé
    rid = demande(ex, "crée un espace jetable de test")
    creer = {"op": "vm.create", "name": "dev", "template": "debian-13-xfce", "label": "blue"}
    assert envoyer(ex, creer, request=rid)["statut"] == "fait"
    ex.backend.remove("dev")
    ex.backend.create_appvm("dev", "debian-13-xfce", "blue")  # par l'utilisateur : autre UUID
    assert envoyer(ex, {"op": "vm.start", "name": "dev"}, request=rid)["statut"] == "refuse"
    assert conf.demandes[-1].hors_portee == (0,)


def test_le_validateur_ne_peut_pas_creer_de_demande(dossier):
    # Socket du validateur : le message « demande » est inconnu de traiter().
    ex, _ = faire(dossier)
    assert ex.traiter({"type": "demande", "texte": "supprime loisirs"})["statut"] == proto.ERREUR
    assert ex.demandes.demandes == {}


def test_socket_des_demandes_n_accepte_pas_de_plan(dossier):
    ex, _ = faire(dossier)
    canon = schema.canonicalize(schema.parse_plan(
        b'{"version":1,"request":"R-0001","ops":[{"op":"vm.list"}]}')).decode()
    assert ex.traiter_demande({"type": "plan", "appelant": "sys-agent", "plan": canon})["statut"] == proto.ERREUR
    assert ex.traiter_demande({"type": "contexte"})["statut"] == proto.ERREUR


def test_chaine_par_sockets(dossier):
    ex, _ = faire(dossier)
    s_dem = executor.serveur_demandes(ex, f"{dossier}/dem.sock")
    s_ex = executor.serveur(ex, f"{dossier}/ex.sock")
    for s in (s_dem, s_ex):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        r = proto.client(f"{dossier}/dem.sock", 10)({"type": "demande", "texte": "démarre loisirs"})
        assert r["statut"] == "fait"
        assert proto.client(f"{dossier}/ex.sock", 10)({"type": "demande", "texte": "x"})["statut"] == "erreur"
        v = validator.Validateur(proto.client(f"{dossier}/ex.sock", 10), validator.Config(pauses=ex.pauses))
        plan = json.dumps({"version": 1, "request": r["request"],
                           "ops": [{"op": "vm.start", "name": "loisirs"}]}).encode()
        assert v.traiter("sys-agent", "ok", plan)["statut"] == "fait"
    finally:
        for s in (s_dem, s_ex):
            s.shutdown()
            s.server_close()


ACTIVATION = r"""
import os, socket, sys
sys.path[:0] = [sys.argv[2], os.path.join(sys.argv[2], "tests")]
d = os.path.dirname(sys.argv[1])
socks = []
for nom in ("demande", "plan"):  # ordre volontairement inverse de main()
    s = socket.socket(socket.AF_UNIX); s.bind(f"{d}/{nom}.sock"); s.listen(); socks.append(s)
fds = [os.dup(s.fileno()) for s in socks]
for s in socks:
    s.close()
for i, fd in enumerate(fds):
    os.dup2(fd, 3 + i)
    os.close(fd)
os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="2", LISTEN_FDNAMES="demande:plan")
from qaf_gate import executor
from backend_sim import SimBackend
from qaf_gate.journal import Journal
from qaf_gate.registry import Demandes, Registry
ex = executor.Executeur(SimBackend(), Registry(d + "/r.json"), Journal(d + "/j.jsonl"), pauses=(),
                        demandes=Demandes(d + "/dem.json"))
srv_dem = executor.serveur_demandes(ex, None, "demande")
srv = executor.serveur(ex, None, "plan")
for s in (srv, srv_dem):
    s.daemon_threads = False
print("pret", flush=True)
srv_dem.handle_request()
srv.handle_request()
"""


def test_activation_systemd_deux_sockets_nommes(dossier):
    racine = os.path.dirname(os.path.dirname(executor.__file__))
    p = subprocess.Popen([sys.executable, "-c", ACTIVATION, f"{dossier}/x", racine], stdout=subprocess.PIPE,
                         text=True)
    try:
        assert p.stdout.readline().strip() == "pret"
        r = proto.client(f"{dossier}/demande.sock", 10)({"type": "demande", "texte": "démarre work"})
        assert r["statut"] == "fait" and r["qubes"] == ["work"]
        assert "existants" in proto.client(f"{dossier}/plan.sock", 10)({"type": "contexte"})
    finally:
        p.wait(10)


def test_fd_systemd(monkeypatch):
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "2")
    monkeypatch.setenv("LISTEN_FDNAMES", "plan:demande")
    assert proto._fd_systemd("plan") == 3 and proto._fd_systemd("demande") == 4
    assert proto._fd_systemd("autre") is None and proto._fd_systemd(None) is None
    monkeypatch.setenv("LISTEN_FDS", "1")
    monkeypatch.setenv("LISTEN_FDNAMES", "plan")
    assert proto._fd_systemd("demande") is None  # nommé autrement : jamais pris par erreur
    assert proto._fd_systemd(None) == 3
    monkeypatch.delenv("LISTEN_FDNAMES")
    assert proto._fd_systemd("plan") == 3  # un seul, sans nom
    monkeypatch.setenv("LISTEN_PID", "1")
    assert proto._fd_systemd("plan") is None


def test_constantes_alignees():
    from qaf_mcp import demande as service
    from qaf_ui import palette
    assert palette.SOCK_DEMANDES == proto.SOCK_DEMANDES
    assert palette.MAX_TEXTE == service.MAX_TEXTE == registry.MAX_TEXTE
    assert palette._REQUEST_RE.pattern == schema._REQUEST_RE.pattern


# --- PARCOURS-1 : session, influence réelle, C13 bis, clone de modèle --------------------

def envoyer_s(ex, *ops, request, session):
    p = schema.parse_plan(json.dumps({"version": 1, "request": request, "session": session,
                                      "ops": list(ops)}).encode())
    return ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": schema.canonicalize(p).decode()})


def test_autre_session_signalee_sans_rien_decider(dossier):
    # PARCOURS-1 7b + revue du 25/10 : la liaison n'est qu'une aide d'affichage.
    ex, conf = faire(dossier, reponses=[False, False])
    rid = demande(ex, "arrête loisirs")
    assert envoyer_s(ex, {"op": "vm.list"}, request=rid, session=S1)["statut"] == "fait"
    ex.backend.start("loisirs")
    envoyer_s(ex, {"op": "vm.shutdown", "name": "loisirs"}, request=rid, session=S2)
    assert not conf.demandes  # N2 non influencé dans la portée : automatique, même depuis S2
    envoyer_s(ex, {"op": "vm.remove", "name": "loisirs"}, request=rid, session=S2)
    d = conf.demandes[-1]
    assert d.autre_session and d.texte_demande == "arrête loisirs" and d.decision == C.CONFIRMER
    envoyer_s(ex, {"op": "vm.remove", "name": "loisirs"}, request=rid, session=S1)
    assert conf.demandes[-1].autre_session is False


def test_lectures_de_qubesd_n_influencent_pas(dossier):
    # PARCOURS-1 M2 : vm.list, vm.info, prefs… viennent de qubesd ou de dom0.
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "démarre loisirs")
    for o in ({"op": "vm.list"}, {"op": "vm.info", "name": "loisirs"}, {"op": "prefs.get"},
              {"op": "vm.settings", "name": "loisirs"}, {"op": "sys.status"}, {"op": "update.status"}):
        envoyer_s(ex, o, request=rid, session=S1)
    assert ex.etat["influence"] is False and not ex.demandes_influencees


def lire_un_qube(ex, rid, session=S1):
    if "a-dev" not in ex.backend.qubes:
        ex.backend.create_appvm("a-dev", "debian-13-xfce", "gray")
        ex.registre.add("a-dev", ex.backend.qubes["a-dev"].uuid, rid)
        ex.backend.start("a-dev")
    r = envoyer_s(ex, {"op": "qube.exec", "name": "a-dev", "argv": ["ls"], "timeout": 5}, request=rid,
                  session=session)
    assert r["statut"] == "fait", r


def test_session_annoncee_ne_blanchit_pas_l_influence(dossier):
    # Revue du 25/10 (HIGH) : une session « neuve » annoncée par l'agent ne réduit rien.
    h = [1_000_000.0]
    ex, conf = faire(dossier, reponses=[False] * 4, horloge=lambda: h[0])
    ex.horloge = lambda: h[0]
    rid = demande(ex, "crée a-dev et démarre loisirs")
    lire_un_qube(ex, rid)
    for s in (S2, "S-ffffffffffffffff", None):
        p = {"version": 1, "request": rid, "ops": [{"op": "vm.start", "name": "loisirs"}]}
        if s:
            p["session"] = s
        canon = schema.canonicalize(schema.parse_plan(json.dumps(p).encode())).decode()
        ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": canon})
        assert conf.demandes[-1].influence is True and conf.demandes[-1].decision != C.AUTO
    # Nouvelle demande de l'utilisateur alors que la session influencée vient d'agir : le drapeau reste vrai.
    h[0] += 60
    rid2 = demande(ex, "démarre loisirs")
    assert ex.etat["influence"] is True
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid2, session="S-0000000000000009")
    assert conf.demandes[-1].influence is True
    # Le temps n'efface pas le contexte d'un claude qui tourne (revue de sécurité du 25/10).
    h[0] += 10 * registry.DUREE_DEMANDE
    demande(ex, "démarre loisirs")
    assert ex.etat["influence"] is True and rid in ex.demandes_influencees


def faire_avec_agent(dossier, reponses=()):
    ex, conf = faire(dossier, reponses=reponses)
    b = ex.backend
    b.qubes["sys-agent"] = dataclasses.replace(b.qubes["loisirs"], name="sys-agent", state="Halted")
    b.start("sys-agent")
    return ex, conf


def test_redemarrage_de_sys_agent_ne_fait_pas_retomber_l_influence(dossier):
    # Revue du 25/10 (HIGH) : sys-agent peut s'éteindre lui-même puis être relancé ;
    # un passage par Halted ou un nouveau domaine ne prouve rien (point 1).
    ex, conf = faire_avec_agent(dossier, reponses=[False] * 3)
    rid = demande(ex, "crée a-dev et démarre loisirs")
    lire_un_qube(ex, rid)
    ex.backend.shutdown("sys-agent")
    rid2 = demande(ex, "démarre loisirs")
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid2, session=S2)
    assert ex.etat["influence"] is True and conf.demandes[-1].influence is True
    ex.backend.start("sys-agent")
    rid3 = demande(ex, "démarre loisirs")
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid3, session=S2)
    assert conf.demandes[-1].influence is True
    assert not any(e.get("type") == "influence_retombee" for e in plans(dossier))


def test_anciennes_demandes_restent_influencees(dossier):
    # Point 2 : un ancien R-id qui a lu du contenu le reste, après une nouvelle demande
    # et après un redémarrage de sys-agent, même si le drapeau commun retombait.
    ex, conf = faire_avec_agent(dossier, reponses=[False])
    rid = demande(ex, "crée a-dev et démarre loisirs")
    lire_un_qube(ex, rid)
    demande(ex, "démarre loisirs")
    ex.backend.restart("sys-agent")
    demande(ex, "démarre loisirs")
    assert rid in ex.demandes_influencees
    ex.etat["influence"] = False
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid, session=S1)
    assert conf.demandes[-1].influence is True


def test_pas_de_tache_propre_qui_blanchirait_l_influence(dossier):
    # Point 3, option (b) : ~/.claude survit au redémarrage, aucun message de la
    # palette ne remet l'influence à zéro ni ne redémarre sys-agent.
    ex, conf = faire_avec_agent(dossier)
    rid = demande(ex, "crée a-dev et démarre loisirs")
    lire_un_qube(ex, rid)
    avant = ex.backend.qubes["sys-agent"]
    for m in ({"type": "tache_propre"}, {"type": "tache_propre", "texte": ""}, {"type": "reinitialiser"}):
        assert ex.traiter_demande(m)["statut"] == "erreur"
        assert ex.traiter(m)["statut"] == "erreur"
    assert ex.backend.qubes["sys-agent"] == avant and ex.etat["influence"] is True
    assert not hasattr(ex, "tache_propre")


def test_demande_influencee_le_reste(dossier):
    # L'influence est gardée par demande (R-xxxxxx tiré par dom0), pas par session.
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "crée a-dev et démarre loisirs")
    lire_un_qube(ex, rid)
    ex.etat["influence"] = False  # même si le drapeau commun retombait
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid, session=S2)
    assert conf.demandes[-1].influence is True


def test_contenu_de_qube_influence_puis_nouvelle_demande(dossier):
    ex, conf = faire(dossier, reponses=[False, False])
    ex.backend.create_appvm("a-dev", "debian-13-xfce", "gray")
    rid = demande(ex, "crée un espace a-dev")
    ex.registre.add("a-dev", ex.backend.qubes["a-dev"].uuid, rid)
    ex.backend.start("a-dev")
    ex.backend.reponses_main["qaf.Exec"] = {"code": 0, "b64": "e30=", "octets": 2}
    r = envoyer_s(ex, {"op": "qube.exec", "name": "a-dev", "argv": ["ls"], "timeout": 5}, request=rid, session=S1)
    assert r["statut"] == "fait" and ex.etat["influence"] is True and rid in ex.demandes_influencees
    envoyer_s(ex, {"op": "vm.start", "name": "loisirs"}, request=rid, session=S1)
    assert conf.demandes[-1].influence is True


def test_c13_bis_reseau_par_defaut_sans_le_nommer(dossier):
    # PARCOURS-1 2c : « connecte-le » ne nomme pas sys-firewall, la route par défaut.
    ex, conf = faire(dossier, reponses=[False])
    rid = demande(ex, "crée un espace essai-dev")
    creer = {"op": "vm.create", "name": "essai-dev", "template": "debian-13-xfce", "label": "gray"}
    assert envoyer_s(ex, creer, request=rid, session=S1)["statut"] == "fait"
    net = {"op": "vm.set_netvm", "name": "essai-dev", "netvm": "sys-firewall"}
    assert envoyer_s(ex, net, request=rid, session=S1)["statut"] == "fait"  # N2, non influencé, dans la portée
    assert conf.demandes == []
    # Une autre route que celle par défaut (sys-net) doit être nommée.
    net["netvm"] = "sys-net"
    assert envoyer_s(ex, net, request=rid, session=S1)["statut"] == "refuse"
    assert conf.demandes[-1].non_nommes == ("sys-net",) and conf.demandes[-1].hors_portee == (0,)


def test_clone_de_modele_puis_installation_automatique(dossier):
    # PARCOURS-1 B2 : installer durablement sans toucher au modèle de l'utilisateur.
    ex, conf = faire(dossier)
    ex.backend.features["debian-13-xfce"] = {"os-distribution": "debian"}
    rid = demande(ex, "crée-moi un espace essai-dev et installe tree dedans")
    ex.backend.features["fedora-42-xfce"] = {"os-distribution": "fedora"}
    # fedora-42-xfce ne sert qu'à work (debian-13-xfce sert à sys-net : confirmé, voir test_parcours1).
    ex.backend.features["fedora-42-xfce"] = {"os-distribution": "fedora"}
    clone = {"op": "vm.clone", "name": "tpl-essai", "source": "fedora-42-xfce", "label": "gray"}
    r = envoyer_s(ex, clone, request=rid, session=S1)
    assert r["statut"] == "fait" and conf.demandes == []
    q = ex.backend.qubes["tpl-essai"]
    assert q.klass == "TemplateVM" and q.netvm is None and ex.registre.is_agent("tpl-essai", q.uuid)
    creer = {"op": "vm.create", "name": "essai-dev", "template": "tpl-essai", "label": "gray"}
    assert envoyer_s(ex, creer, request=rid, session=S1)["statut"] == "fait"
    p = [x for x in plans(dossier) if x["type"] == "plan"]
    inst = {"op": "pkg.install", "name": "tpl-essai", "packages": ["tree"]}
    envoyer_s(ex, inst, request=rid, session=S1)
    p = [x for x in plans(dossier) if x["type"] == "plan"][-1]
    assert p["decision"] == C.AUTO and conf.demandes == []  # classe A : automatique
    # Le modèle de l'utilisateur, lui, n'a pas bougé et reste confirmé.
    envoyer_s(ex, {"op": "pkg.install", "name": "debian-13-xfce", "packages": ["tree"]}, request=rid, session=S1)
    assert [x for x in plans(dossier) if x["type"] == "plan"][-1]["decision"] != C.AUTO
