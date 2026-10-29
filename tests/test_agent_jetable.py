"""Agent jetable (docs/AGENT-JETABLE.md) : un agent neuf par demande, dans un
jetable de qaf-agent-dvm créé, inscrit, étiqueté et démarré par l'exécuteur.

Tests obligatoires du §14 (et de la revue §16) : identité revérifiée,
influence par jetable, demande liée par dom0, jetables vivants en P,
R-1 à R-11, n1 à n4, minuteries, ramassage, fuzz des messages de la palette.
"""

import dataclasses
import json
import os
import shutil
import socket
import tempfile
import threading

import pytest
from hypothesis import HealthCheck, example, given, settings, strategies as st

from qaf_gate import classify as C
from qaf_gate import confirm, executor, proto, schema, validator
from qaf_gate.backend import BackendError
from qaf_gate.journal import Journal
from qaf_gate.registry import Agents, Demandes, Registry
from backend_sim import SimBackend, _q, qubes_par_defaut

FERME = executor.regles_agent(("api.anthropic.com",))


class Horloge:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def backend():
    b = SimBackend(qubes_par_defaut() + [
        _q("tpl-agent", "TemplateVM", "black"),
        _q("sys-agent", "AppVM", "purple", "tpl-agent", "sys-firewall", default_dispvm=None,
           default_dispvm_herite=False),
        _q("qaf-agent-dvm", "AppVM", "purple", "tpl-agent", "sys-firewall", template_for_dispvms=True,
           default_dispvm=None, default_dispvm_herite=False),
        _q("tpl-qaf-hand", "TemplateVM", "black"),
        _q("qaf-dvm", "AppVM", "red", "tpl-qaf-hand", None, template_for_dispvms=True, default_dispvm=None,
           default_dispvm_herite=False),
    ])
    b.regles["qaf-agent-dvm"] = [dict(r) for r in FERME]
    return b


@pytest.fixture
def dossier():
    d = tempfile.mkdtemp(prefix="qaf-", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def monde(d, reponses=(), **config):
    h = Horloge()
    conf = confirm.Scripte(reponses)
    cfg = C.Config(**{"appelants": frozenset(), "jetables": frozenset({"qaf-dvm"}), **config})
    ex = executor.Executeur(backend(), Registry(f"{d}/registre.json"), Journal(f"{d}/journal.jsonl"), conf,
                            pauses=(f"{d}/pause", f"{d}/pause-auto"), pause_auto=f"{d}/pause-auto",
                            demandes=Demandes(f"{d}/demandes.json", horloge=h),
                            agents=Agents(f"{d}/agents.json", horloge=h), config=cfg)
    ex.horloge_test = h
    return ex, conf


def lancer(ex, texte, **extra):
    r = ex.traiter_demande({"type": "demande", "texte": texte, **extra})
    assert r["statut"] == proto.FAIT, r
    return r["agent"], r["request"]


def plan(ex, appelant, *ops, request):
    p = schema.parse_plan(json.dumps({"version": 1, "request": request, "ops": list(ops)}).encode())
    return ex.traiter({"type": "plan", "appelant": appelant, "plan": schema.canonicalize(p).decode()})


def journal(d):
    with open(f"{d}/journal.jsonl", encoding="utf-8") as f:
        return [e["event"] for e in map(json.loads, f)]


def influence(ex, agent):
    return ex.agents.agents[agent]["influence"]


def creer_et_lire(ex, agent, rid, nom="a-dev"):
    """L'agent crée un espace, le démarre et y lit une sortie (opération I)."""
    assert plan(ex, agent, {"op": "vm.create", "name": nom, "template": "debian-13-xfce", "label": "blue"},
                {"op": "vm.start", "name": nom}, request=rid)["statut"] == "fait"
    return plan(ex, agent, {"op": "qube.exec", "name": nom, "argv": ["ls"], "timeout": 5}, request=rid)


# --- lancement (§1, R-3, R-5) ------------------------------------------------

def test_demande_lance_un_agent_neuf_etiquete_et_inscrit(dossier):
    ex, _ = monde(dossier)
    agent, rid = lancer(ex, "démarre work")
    q = ex.backend.info(agent)
    assert schema.AGENT_RE.match(agent) and q.klass == "DispVM" and q.template == "qaf-agent-dvm"
    assert q.state == "Running" and "qaf-agent" in q.tags and q.default_dispvm is None
    e = ex.agents.agents[agent]
    assert e["uuid"] == q.uuid and e["request"] == rid and e["influence"] is False and e["cli"] == "claude"
    assert ex.demandes.demandes[rid]["agent"] == agent
    types = [x["type"] for x in journal(dossier)]
    assert types[-2:] == ["agent_debut", "demande"] and journal(dossier)[-1]["agent"] == agent


def test_inscrit_avant_l_etiquette_et_le_demarrage(dossier):
    # R-5 : l'entrée du registre (avec l'UUID de CreateDisposable) précède tag.Set et start().
    ex, _ = monde(dossier)
    vus = []
    tag, start = ex.backend.tag, ex.backend.start
    ex.backend.tag = lambda n, t, a: (vus.append(("tag", n in ex.agents.agents)), tag(n, t, a))[1]
    ex.backend.start = lambda n: (vus.append(("start", n in ex.agents.agents and "qaf-agent" in ex.backend.tags[n])),
                                  start(n))[1]
    lancer(ex, "x")
    assert vus == [("tag", True), ("start", True)]


def test_echec_du_demarrage_tue_le_jetable_et_retire_l_entree(dossier):
    ex, _ = monde(dossier)

    def panne(n):
        raise BackendError("autre", "démarrage impossible")
    ex.backend.start = panne
    r = ex.traiter_demande({"type": "demande", "texte": "x"})
    assert r["statut"] == proto.ERREUR and ex.agents.agents == {} and ex.demandes.demandes == {}
    assert ex.backend.tues == ["disp1"] and "disp1" not in ex.backend.qubes


@pytest.mark.parametrize("panne", ["jetables", "global", "qube", "etiquette", "politique"])
def test_modele_mal_regle_lancement_refuse(dossier, panne):
    ex, _ = monde(dossier)
    if panne == "jetables":  # R-3 : jamais un modèle de dispvm.create
        ex.config = dataclasses.replace(ex.config, jetables=frozenset({"qaf-dvm", "qaf-agent-dvm"}))
    elif panne == "global":  # R-3 : jamais le jetable par défaut
        ex.backend.globaux["default_dispvm"] = "qaf-agent-dvm"
    elif panne == "qube":
        ex.backend._set(ex.backend.qubes["work"], default_dispvm="qaf-agent-dvm", default_dispvm_herite=False)
    elif panne == "etiquette":  # S-J2 : passerait à tous ses jetables
        ex.backend.tags["qaf-agent-dvm"] = {"qaf-agent"}
    else:  # étiquette citée par une politique : politiques illisibles = fail-closed
        ex.backend.tags["qaf-agent-dvm"] = {"projet"}
        executor_tags = executor._tags_politique
        executor._tags_politique = lambda: None
    try:
        r = ex.traiter_demande({"type": "demande", "texte": "x"})
    finally:
        if panne == "politique":
            executor._tags_politique = executor_tags
    assert r["statut"] == proto.REFUSE and "verifier" in r["raison"]
    assert not any(q.klass == "DispVM" for q in ex.backend.list_qubes()) and ex.agents.agents == {}


def test_agents_max(dossier):
    ex, _ = monde(dossier, agents_max=1)
    lancer(ex, "x")
    r = ex.traiter_demande({"type": "demande", "texte": "y"})
    assert r["statut"] == proto.REFUSE and "déjà 1 agents" in r["raison"]


def test_modele_absent_erreur(dossier):
    ex, _ = monde(dossier)
    del ex.backend.qubes["qaf-agent-dvm"]
    assert ex.traiter_demande({"type": "demande", "texte": "x"})["statut"] == proto.ERREUR


# --- identité (§5.3) ---------------------------------------------------------

@pytest.mark.parametrize("ecart", ["hors_registre", "uuid", "etiquette", "modele", "default_dispvm", "nom"])
def test_identite_revérifiee_a_chaque_plan(dossier, ecart):
    ex, _ = monde(dossier)
    agent, rid = lancer(ex, "liste")
    b, q = ex.backend, ex.backend.qubes[agent]
    appelant = agent
    if ecart == "hors_registre":  # un jetable de default-dvm, même étiqueté à la main
        appelant = b.run_dispvm_task("default-dvm", "x")
        b.tags[appelant] = {"qaf-agent"}
    elif ecart == "uuid":  # nom repris par un autre jetable (S-J4)
        b.qubes[agent] = dataclasses.replace(q, uuid="autre")
    elif ecart == "etiquette":
        b.tags[agent].discard("qaf-agent")
    elif ecart == "modele":
        b.qubes[agent] = dataclasses.replace(q, template="default-dvm")
    elif ecart == "default_dispvm":
        b.qubes[agent] = dataclasses.replace(q, default_dispvm="qaf-agent-dvm")
    else:
        appelant = "work"
    r = plan(ex, appelant, {"op": "vm.list"}, request=rid)
    assert r["statut"] == proto.REFUSE
    assert r["raison"] in ("agent inconnu", "appelant non autorisé")


def test_sys_agent_refuse_sans_agent_long(dossier):
    ex, _ = monde(dossier)
    _, rid = lancer(ex, "liste")
    assert plan(ex, "sys-agent", {"op": "vm.list"}, request=rid)["raison"] == "appelant non autorisé"


def test_demande_d_un_autre_agent_refusee(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "démarre work")
    _, rid_b = lancer(ex, "démarre personal")
    r = plan(ex, a, {"op": "vm.list"}, request=rid_b)
    assert r["statut"] == proto.REFUSE and "autre agent" in r["raison"]


# --- influence par jetable (§6, R-1, R-7) --------------------------------------

def test_influence_de_a_sans_effet_sur_b_puis_b_lit_un_qube_de_a(dossier):
    ex, conf = monde(dossier, reponses=[True])
    a, rid_a = lancer(ex, "crée a-dev")
    b, rid_b = lancer(ex, "démarre personal")
    assert creer_et_lire(ex, a, rid_a)["statut"] == "fait"
    assert influence(ex, a) and not influence(ex, b) and ex.etat["influence"] is False
    # B non influencé : N2 sur un qube nommé de l'utilisateur, automatique.
    assert plan(ex, b, {"op": "vm.start", "name": "personal"}, request=rid_b)["statut"] == "fait"
    assert conf.demandes == []
    # B lit le qube créé par A : B devient influencé (canal (1) du §6).
    assert plan(ex, b, {"op": "qube.exec", "name": "a-dev", "argv": ["cat", "x"], "timeout": 5},
                request=rid_b)["statut"] == "fait"
    assert influence(ex, b)
    assert plan(ex, b, {"op": "vm.shutdown", "name": "personal"}, request=rid_b)["statut"] == "fait"
    assert len(conf.demandes) == 1 and conf.demandes[0].influence is True


def test_pare_feu_ouvert_rend_influence(dossier):
    # R-1 : un jetable dont le pare-feu n'est plus le pare-feu fermé lit peut-être le Web.
    ex, _ = monde(dossier)
    a, rid = lancer(ex, "liste")
    assert ex.backend.regles[a] == FERME
    plan(ex, a, {"op": "vm.list"}, request=rid)
    assert not influence(ex, a)
    ex.backend.regles[a] = [{"action": "accept"}]
    assert plan(ex, a, {"op": "vm.list"}, request=rid)["statut"] == "fait"  # pas refusé pour autant
    assert influence(ex, a) and journal(dossier)[-4]["type"] == "pare_feu_ecart"


def test_continuer_avec_cet_agent_garde_l_influence(dossier):
    ex, conf = monde(dossier, reponses=[False])
    a, rid = lancer(ex, "crée a-dev")
    creer_et_lire(ex, a, rid)
    agent, rid2 = lancer(ex, "démarre personal", agent=a)
    assert agent == a and rid2 != rid and influence(ex, a)  # R-7 : jamais remise à faux
    assert ex.agents.agents[a]["request"] == rid2
    plan(ex, a, {"op": "vm.start", "name": "personal"}, request=rid2)
    assert conf.demandes and conf.demandes[-1].influence is True
    # L'ancienne demande de cet agent ne vaut plus (P1 par agent).
    assert "autre agent" in plan(ex, a, {"op": "vm.list"}, request=rid)["raison"]


@pytest.mark.parametrize("agent", [None, "", 0, False, []])
def test_champ_agent_present_mais_vide_refuse(dossier, agent):
    # Fuzz du parcours 4 : {"agent": None} passait pour « pas d'agent » et lançait
    # un agent neuf. Schéma fermé : un champ présent doit être un nom d'agent.
    ex, _ = monde(dossier)
    r = ex.traiter_demande({"type": "demande", "texte": "0", "agent": agent})
    assert r["statut"] == proto.REFUSE and ex.demandes.demandes == {}
    assert not any(q.klass == "DispVM" for q in ex.backend.list_qubes()) and not ex.agents.agents


def test_continuer_un_agent_ferme_refuse(dossier):
    ex, _ = monde(dossier)
    r = ex.traiter_demande({"type": "demande", "texte": "x", "agent": "disp77"})
    assert r["statut"] == proto.REFUSE and "plus ouvert" in r["raison"]


def test_redemarrage_de_l_executeur_influence_vraie(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "x")
    assert Agents(f"{dossier}/agents.json").agents[a]["influence"] is True


def test_agents_maj_ne_remet_jamais_l_influence_a_faux(dossier):
    ag = Agents(f"{dossier}/a.json")
    ag.add("disp1", "u", "claude")
    for champs in ({"influence": False}, {"uuid": "v"}, {"debut": 0}, {"cli": "x"}):
        with pytest.raises(ValueError):
            ag.maj("disp1", **champs)
    with pytest.raises(ValueError):
        ag.add("disp1", "v", "claude")  # jamais réattribuée à un autre UUID


# --- jetables d'agent vivants en P, tpl-agent en P (§6, R-2) ------------------

def test_operation_sur_un_jetable_d_agent_vivant_n4(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "x")
    b, rid_b = lancer(ex, "y")
    for op in ({"op": "vm.shutdown", "name": a}, {"op": "qube.exec", "name": a, "argv": ["id"], "timeout": 5},
               {"op": "vm.remove", "name": a}, {"op": "enter.open", "name": a, "minutes": 5},
               {"op": "vm.start", "name": "qaf-agent-dvm"}, {"op": "vm.set_prop", "name": "qaf-agent-dvm",
                                                             "prop": "template_for_dispvms", "value": False}):
        r = plan(ex, b, op, request=rid_b)
        assert r["statut"] == proto.REFUSE and "refuse cette action" in r["raison"], op


def test_tpl_agent_protege_sans_agent_long(dossier):
    ex, _ = monde(dossier)
    assert ex.config.agent_long is False
    a, rid = lancer(ex, "installe tree dans tpl-agent et mets-le à jour")
    for op in ({"op": "pkg.install", "name": "tpl-agent", "packages": ["tree"]},
               {"op": "update.run", "targets": ["tpl-agent"], "apply": "none"},
               {"op": "vm.set_prop", "name": "tpl-agent", "prop": "label", "value": "red"},
               {"op": "vm.tag", "name": "work", "tag": "qaf-agent", "action": "add"},
               {"op": "prefs.set", "prop": "default_dispvm", "value": "qaf-agent-dvm"},
               {"op": "vm.set_prop", "name": "work", "prop": "default_dispvm", "value": "qaf-agent-dvm"},
               {"op": "dispvm.create", "template": "qaf-agent-dvm"}):
        assert plan(ex, a, op, request=rid)["statut"] == proto.REFUSE, op


def test_classe_p_meme_sans_sys_agent():
    # R-2 : tpl-agent tire son statut P de qaf-agent-dvm, même si sys-agent n'existe plus.
    qubes = {"tpl-agent": {"klass": "TemplateVM"}, "qaf-agent-dvm": {"klass": "AppVM", "template": "tpl-agent"}}
    ctx = C.Contexte(qubes, {}, C.Config(appelants=frozenset()))
    assert ctx.classe("tpl-agent") == C.P and ctx.classe("qaf-agent-dvm") == C.P


# --- héritage d'étiquettes (R-6) ------------------------------------------------

def test_jetable_ou_clone_d_une_source_etiquetee_n4(dossier):
    ex, _ = monde(dossier)
    a, rid = lancer(ex, "crée un jetable et copie debian-13-xfce")
    ex.backend.tags["qaf-dvm"] = {"qaf-agent"}
    assert plan(ex, a, {"op": "dispvm.create"}, request=rid)["statut"] == proto.REFUSE
    ex.backend.tags["debian-13-xfce"] = {"qaf-agent"}
    assert plan(ex, a, {"op": "vm.clone", "name": "tpl-x", "source": "debian-13-xfce", "label": "black"},
                request=rid)["statut"] == proto.REFUSE


def test_etiquette_heritee():
    def ctx(tags, pol=frozenset({"projet"})):
        return C.Contexte({"m": {"tags": tags}}, {}, C.Config(), tags_politique=pol)
    assert not C.etiquette_heritee("m", ctx(("created-by-dom0", "guivm-dom0", "audiovm-dom0")))
    assert C.etiquette_heritee("m", ctx(("qaf-agent",)))
    assert C.etiquette_heritee("m", ctx(("projet",)))
    assert not C.etiquette_heritee("m", ctx(("autre",)))
    assert C.etiquette_heritee("m", ctx(("autre",), None))  # politiques illisibles
    assert not C.etiquette_heritee("m", ctx(("created-by-dom0",), None))


# --- demande par jetable (§7, n3) ---------------------------------------------

def test_nouvelle_demande_ne_retire_pas_celle_de_l_agent_precedent(dossier):
    ex, conf = monde(dossier)
    a, rid_a = lancer(ex, "démarre personal")
    lancer(ex, "quelle heure est-il ?")
    assert plan(ex, a, {"op": "vm.start", "name": "personal"}, request=rid_a)["statut"] == "fait"
    assert conf.demandes == []


# --- n1, n2, R-9 (§13.1, §13.2) -------------------------------------------------

def test_n1_modele_de_l_agent_d_un_qube_nomme(dossier):
    # p9 de PARCOURS-2 : « installe tree durablement dans essai-dev » vise son modèle.
    ex, conf = monde(dossier)
    a, rid = lancer(ex, "crée essai-dev")
    assert plan(ex, a, {"op": "vm.clone", "name": "tpl-essai-dev", "source": "fedora-42-xfce", "label": "black"},
                request=rid)["statut"] == "fait"
    assert plan(ex, a, {"op": "vm.create", "name": "essai-dev", "template": "tpl-essai-dev",
                        "label": "blue"}, request=rid)["statut"] == "fait"
    b, rid_b = lancer(ex, "installe tree durablement dans essai-dev")
    assert plan(ex, b, {"op": "vm.set_netvm", "name": "tpl-essai-dev", "netvm": "sys-firewall"},
                request=rid_b)["statut"] == "fait"  # N2 sur le modèle de l'agent : dans la portée (n1)
    assert conf.demandes == []
    assert journal(dossier)[-3]["hors_portee"] == []


def test_n1_pas_si_le_modele_porte_un_qube_de_utilisateur():
    ctx = C.Contexte({"tpl-a": {"klass": "TemplateVM"}, "a-dev": {"template": "tpl-a"},
                      "loisirs": {"template": "tpl-a"}}, {}, C.Config())
    assert C.modeles_agent({"a-dev"}, ctx, {"tpl-a", "a-dev"}) == set()
    assert C.modeles_agent({"a-dev"}, ctx, {"tpl-a", "a-dev", "loisirs"}) == {"tpl-a"}


def test_r9_suppression_d_un_qube_d_un_autre_jetable_confirmation_simple(dossier):
    ex, conf = monde(dossier, reponses=[True])
    a, rid_a = lancer(ex, "crée x-a")
    assert plan(ex, a, {"op": "vm.create", "name": "x-a", "template": "debian-13-xfce", "label": "blue"},
                request=rid_a)["statut"] == "fait"
    assert ex.registre.qubes["x-a"]["agent"] == {"nom": a, "uuid": ex.agents.agents[a]["uuid"]}
    b, rid_b = lancer(ex, "fais le ménage")
    assert plan(ex, b, {"op": "vm.remove", "name": "x-a"}, request=rid_b)["statut"] == "fait"
    assert len(conf.demandes) == 1 and conf.demandes[0].decision == C.CONFIRMER


def test_r9_le_meme_jetable_reste_automatique(dossier):
    ex, conf = monde(dossier)
    a, rid = lancer(ex, "crée x-a")
    plan(ex, a, {"op": "vm.create", "name": "x-a", "template": "debian-13-xfce", "label": "blue"}, request=rid)
    _, rid2 = lancer(ex, "fais le ménage", agent=a)
    assert plan(ex, a, {"op": "vm.remove", "name": "x-a"}, request=rid2)["statut"] == "fait"
    assert conf.demandes == []


def test_n2_n1_hors_portee_automatique_n2_renforce():
    ctx = C.Contexte({"a-dev": {"klass": "AppVM"}, "loisirs": {"klass": "AppVM"}}, {}, C.Config())

    def cl(o):
        p = schema.parse_plan(json.dumps({"version": 1, "request": "R-0001", "ops": [o]}).encode())
        return C.classer(p, agent_qubes={"a-dev"}, existing={"a-dev", "loisirs"}, influence=True, portee=set(), ctx=ctx)
    assert cl({"op": "vm.start", "name": "a-dev"}).decision == C.AUTO
    assert cl({"op": "vm.start", "name": "loisirs"}).decision == C.CONFIRMER_RENFORCE
    p = schema.parse_plan(b'{"version":1,"request":"R-0001","ops":[{"op":"vm.remove","name":"a-dev"}]}')
    r = C.classer(p, agent_qubes={"a-dev"}, existing={"a-dev"}, influence=False, portee=set(), ctx=ctx,
                  etrangers={"a-dev"})
    assert r.decision == C.CONFIRMER and r.hors_portee == (0,)


# --- n4 (§13.4) ------------------------------------------------------------------

class Expire(confirm.Confirmateur):
    def __init__(self):
        self.demandes = []

    def demander(self, d):
        self.demandes.append(d)
        return None


def test_n4_fenetre_expiree_rapportee_comme_expiree(dossier):
    ex, _ = monde(dossier)
    ex.confirmateur = Expire()
    a, rid = lancer(ex, "x")
    r = plan(ex, a, {"op": "vm.start", "name": "personal"}, request=rid)
    assert r["statut"] == proto.EXPIRE and "personne n'a répondu" in r["raison"]
    c = [e for e in journal(dossier) if e["type"] == "confirmation"][-1]
    assert c["expiree"] is True and c["accordee"] is False


@pytest.mark.parametrize("reponse,attendu", [({"accorde": False, "motif": "expire"}, None),
                                             ({"accorde": False, "motif": "refuse"}, False),
                                             ({"accorde": False}, False)])
def test_n4_fenetre_motif_expire(dossier, reponse, attendu):
    os.makedirs(f"{dossier}/ui")
    chemin = f"{dossier}/ui/confirm.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(chemin)
    srv.listen(1)

    def servir():
        c, _ = srv.accept()
        with c:
            proto.recevoir_json(c)
            proto.envoyer_json(c, reponse)
    threading.Thread(target=servir, daemon=True).start()
    p = schema.parse_plan(b'{"version":1,"request":"R-0001","ops":[{"op":"vm.list"}]}')
    d = confirm.Demande("disp12", p, schema.canonicalize(p), (2,), C.CONFIRMER, "123456")
    try:
        assert confirm.Fenetre(chemin, delai=5).demander(d) is attendu
    finally:
        srv.close()


# --- minuteries et ramassage (§8, R-5, R-8) --------------------------------------

def test_ramassage_d_un_jetable_d_agent_hors_registre(dossier):
    ex, _ = monde(dossier)
    q = ex.backend.creer_agent("qaf-agent-dvm")  # création interrompue avant le registre
    p = ex.backend.creer_agent("qaf-agent-dvm")
    ex.backend.features[p.name] = {"preload-dispvm-completed": "1"}  # préchargé en attente : gardé
    ex.entretenir()
    assert ex.backend.tues == [q.name]
    assert {"type": "agent_tue", "agent": q.name, "raison": "hors registre"} in journal(dossier)


def test_inactif_avec_demande_expiree_tue(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "x")
    ex.horloge_test.t += 59 * 60
    ex.entretenir()
    assert ex.backend.tues == []
    ex.horloge_test.t += 2 * 60
    ex.entretenir()
    assert ex.backend.tues == [a]
    ex.entretenir()  # UUID disparu : entrée et demande purgées, agent_fin journalisé
    assert ex.agents.agents == {} and ex.demandes.demandes == {}
    assert {"type": "agent_fin", "agent": a} in journal(dossier)


def test_duree_maximale_tue_meme_actif(dossier):
    ex, _ = monde(dossier)
    a, rid = lancer(ex, "x")
    for _ in range(9):
        ex.horloge_test.t += 55 * 60
        plan(ex, a, {"op": "vm.list"}, request=rid)
        ex.entretenir()
    assert ex.backend.tues == [a]
    assert [e["raison"] for e in journal(dossier) if e["type"] == "agent_tue"] == ["durée maximale"]


def test_arret_d_urgence_tue_tous_les_agents_pause_auto_les_garde(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "x")
    b, _ = lancer(ex, "y")
    open(f"{dossier}/pause-auto", "w").write("débit")
    ex.entretenir()
    assert ex.backend.tues == []
    open(f"{dossier}/pause", "w").write("arrêt d'urgence")
    ex.entretenir()
    assert sorted(ex.backend.tues) == sorted([a, b])


def test_liste_des_agents_ouverts(dossier):
    ex, _ = monde(dossier)
    a, _ = lancer(ex, "démarre work\net plus")
    ex.horloge_test.t += 120
    r = ex.traiter_demande({"type": "agents"})
    assert r["statut"] == proto.FAIT
    assert r["agents"] == [{"agent": a, "age": 120, "influence": False, "cli": "claude", "texte": "démarre work\net plus"}]


# --- validateur, fenêtre (qaf_ui) ---------------------------------------------------

def test_validateur_laisse_passer_un_nom_de_jetable_seulement():
    vus = []

    def executeur(m):
        vus.append(m["type"])
        return {"statut": "erreur", "raison": "x"} if m["type"] != "contexte" else (_ for _ in ()).throw(OSError())
    v = validator.Validateur(executeur, validator.Config(pauses=()))
    canon = b'{"ops":[{"op":"vm.list"}],"request":"R-0001","version":1}'
    assert v.traiter("work", "ok", canon)["raison"] == "appelant non autorisé"
    assert v.traiter("disp12345", "ok", canon)["raison"] == "appelant non autorisé"
    assert v.traiter("disp12", "ok", canon)["raison"] == "exécuteur indisponible"


def test_fenetre_accepte_un_jetable_d_agent():
    from qaf_ui import confirmation as c
    p = schema.parse_plan(b'{"version":1,"request":"R-0001","ops":[{"op":"vm.start","name":"work"}]}')
    msg = {"type": "confirmer", "canonique": schema.canonicalize(p).decode(), "niveaux": [2],
           "decision": C.CONFIRMER, "jeton": "123456", "influence": True, "qubes": [], "expire_dans": 100,
           "texte_demande": None, "age_demande": None, "hors_portee": []}
    assert c.lire_requete({**msg, "appelant": "disp12"}).appelant == "disp12"
    for mauvais in ("work", "disp", 7):
        with pytest.raises(c.DemandeInvalide):
            c.lire_requete({**msg, "appelant": mauvais})


# --- configuration (portier.json) ---------------------------------------------------

def test_config_agent():
    cfg = C.Config.de_json({"agent_long": False, "agents_max": 2, "agent_hotes": ["api.anthropic.com"]})
    assert cfg.agent_long is False and cfg.appelants == frozenset() and cfg.agents_max == 2
    assert C.Config.de_json(cfg.en_json()) == cfg and C.Config.de_json(C.Config().en_json()) == C.Config()
    assert C.Config.de_json({"agent_long": True}).appelants == {"sys-agent"}
    assert C.Config.de_json({}).agent_long is False  # portier.json sans la clé : agent long coupé
    for mauvais in ({"agent_long": "oui"}, {"agents_max": 0}, {"agents_max": 9}, {"agent_hotes": []},
                    {"agent_hotes": ["a b"]}, {"agent_hotes": ["x", "x"]}, {"agent_inactivite": 1},
                    {"agent_duree_max": True}):
        with pytest.raises(ValueError):
            C.Config.de_json(mauvais)
    livre = json.load(open(os.path.join(os.path.dirname(__file__), "..", "packaging", "portier.json")))
    assert C.Config.de_json(livre).agent_long is True  # agent permanent par défaut (D28, 27/10)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.dictionaries(st.sampled_from(["agent_long", "agents_max", "agent_inactivite", "agent_duree_max",
                                        "agent_hotes", "coffres", "inconnu"]),
                       st.one_of(st.none(), st.booleans(), st.integers(-5, 2000), st.text(max_size=8),
                                 st.lists(st.text(max_size=12), max_size=3))))
def test_fuzz_config(d):
    try:
        cfg = C.Config.de_json(d)
    except ValueError:
        return
    assert C.Config.de_json(cfg.en_json()) == cfg
    assert 1 <= cfg.agents_max <= 8 and type(cfg.agent_long) is bool


# --- fuzz du schéma des messages de la palette ----------------------------------------

_valeur = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=12),
                    st.sampled_from(["demande", "agents", "disp1", "disp12345", "claude", "codex", "x"]),
                    st.lists(st.text(max_size=3), max_size=2))


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.dictionaries(st.sampled_from(["type", "texte", "agent", "cli", "request", "plan", "modele"]), _valeur,
                       max_size=5))
@example({"type": "demande", "texte": "0", "agent": None})
def test_fuzz_messages_de_la_palette(msg):
    d = tempfile.mkdtemp(prefix="qaf-", dir="/tmp")
    try:
        ex, _ = monde(d)
        r = ex.traiter_demande(msg)
        assert isinstance(r, dict) and r["statut"] in proto.STATUTS
        valide = (msg == {"type": "agents"} or (
            msg.get("type") == "demande" and isinstance(msg.get("texte"), str) and msg["texte"].strip()
            and set(msg) <= {"type", "texte", "agent", "cli"} and msg.get("cli", "claude") == "claude"
            and "agent" not in msg))
        if r["statut"] == proto.FAIT:
            assert valide, msg
        creees = [q.name for q in ex.backend.list_qubes() if q.klass == "DispVM"]
        assert len(creees) <= (1 if valide else 0)
        assert set(ex.agents.agents) == set(creees)  # jamais de jetable hors registre
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ancien_executeur_sans_registre_agents_demande_a_l_agent_long(dossier):
    # agents=None (anciens tests) : comportement d'avant, la demande va à sys-agent.
    ex = executor.Executeur(backend(), Registry(f"{dossier}/r.json"), Journal(f"{dossier}/j.jsonl"),
                            demandes=Demandes(f"{dossier}/d.json"))
    r = ex.traiter_demande({"type": "demande", "texte": "x"})
    assert r["agent"] == "sys-agent" and not any(q.klass == "DispVM" for q in ex.backend.list_qubes())
    assert ex.traiter_demande({"type": "demande", "texte": "x", "agent": "disp1"})["statut"] == proto.ERREUR


def test_qaf_request_reconnait_un_jetable_par_sa_classe():
    # Qubes 4.3.2 : dans un jetable, /qubes-vm-type vaut « AppVM » ;
    # seule /type dit DispVM. Sinon le jeton n'était jamais passé à claude.
    import subprocess as sp
    from qaf_mcp import demande
    vus = []

    def faux(sortie):
        def executer(argv, **kw):
            vus.append(argv)
            return sp.CompletedProcess(argv, 0, sortie, b"")
        return executer
    assert demande.est_jetable(faux(b"DispVM\n"))
    assert not demande.est_jetable(faux(b"AppVM\n"))
    assert vus[0] == ["/usr/bin/qubesdb-read", "/type"]
