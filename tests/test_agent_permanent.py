"""Agent permanent (docs/AGENT-JETABLE.md D28, décision du 27/10) :
sys-agent est L'agent, sa mémoire survit, il est influencé en permanence.

- agent_long vrai par défaut (portier.json livré, Config, exécuteur) ;
- une demande neuve est liée à sys-agent par dom0, sans jetable ; seul
  l'appelant qrexec sys-agent peut la citer ; toute demande forgée est refusée
  ou classée « sans demande » ;
- influence permanente : N2 et plus toujours confirmés, lectures automatiques ;
- qaf.Request dans sys-agent : une seule fenêtre « Agent », conversation reprise ;
- palette : sys-agent reconnu par le nom rendu par le Doorman, jamais choisi.
"""

import io
import json
import os
import pathlib
import signal
import subprocess

import pytest

from qaf_gate import classify as C
from qaf_gate import pause, proto, validator
from qaf_mcp import demande
from qaf_ui import confirmation, palette

from test_agent_jetable import journal, monde, plan

RACINE = pathlib.Path(__file__).parent.parent
LONG = frozenset({"sys-agent"})


def permanent(d, reponses=()):
    """Exécuteur configuré comme main() avec le portier.json livré (agent_long vrai)."""
    return monde(d, reponses, appelants=LONG)


def demander(ex, texte, **extra):
    r = ex.traiter_demande({"type": "demande", "texte": texte, **extra})
    assert r["statut"] == proto.FAIT, r
    return r["agent"], r["request"]


@pytest.fixture
def dossier(tmp_path):
    return str(tmp_path)


# --- agent_long vrai par défaut -------------------------------------------------------

def test_portier_json_livre_agent_permanent():
    livre = json.loads((RACINE / "packaging/portier.json").read_text(encoding="utf-8"))
    assert livre["agent_long"] is True
    cfg = C.Config.de_json(livre)
    assert cfg.agent_long and cfg.appelants == {"sys-agent"}
    assert C.Config().agent_long  # portier.json absent : comme le Doorman livré


def test_influence_vraie_des_le_demarrage(dossier):
    ex, _ = permanent(dossier)
    assert ex.etat["influence"] is True
    ex2, _ = monde(dossier)  # agent_long faux : jetables, drapeau commun faux au départ
    assert ex2.etat["influence"] is False


# --- demande liée à sys-agent par dom0 -------------------------------------------------

def test_demande_va_a_sys_agent_sans_jetable(dossier):
    ex, _ = permanent(dossier)
    agent, rid = demander(ex, "démarre personal")
    assert agent == "sys-agent"
    assert ex.demandes.demandes[rid]["agent"] == "sys-agent"
    assert not any(q.klass == "DispVM" for q in ex.backend.list_qubes()) and ex.agents.agents == {}
    types = [e["type"] for e in journal(dossier)]
    assert "agent_debut" not in types
    assert [e for e in journal(dossier) if e["type"] == "demande"][-1]["agent"] == "sys-agent"
    # La demande suivante remplace la précédente (P1) : un seul rid valide pour sys-agent.
    _, rid2 = demander(ex, "liste mes qubes")
    assert rid2 != rid and rid not in ex.demandes.demandes


def test_plan_de_sys_agent_porte_le_texte_de_sa_demande(dossier):
    ex, conf = permanent(dossier, reponses=[True])
    _, rid = demander(ex, "démarre personal")
    assert plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)["statut"] == "fait"
    d = conf.demandes[0]
    assert d.appelant == "sys-agent" and d.texte_demande == "démarre personal" and d.influence is True


def test_influence_permanente_n2_confirme_lecture_automatique(dossier):
    ex, conf = permanent(dossier, reponses=[True, True])
    _, rid = demander(ex, "démarre personal puis arrête-le")
    assert plan(ex, "sys-agent", {"op": "vm.list"}, request=rid)["statut"] == "fait"
    assert conf.demandes == []  # lecture : jamais de fenêtre
    # Le même N2 sur un qube nommé, automatique pour un agent neuf, est confirmé.
    assert plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)["statut"] == "fait"
    assert plan(ex, "sys-agent", {"op": "vm.shutdown", "name": "personal"}, request=rid)["statut"] == "fait"
    assert [d.decision for d in conf.demandes] == [C.CONFIRMER, C.CONFIRMER]
    assert all(d.influence for d in conf.demandes)
    # Classement direct : N2 et N3 jamais automatiques avec l'influence.
    for niveau in (C.N2, C.N3):
        assert C.decision(niveau, True, C.NORMAL) in (C.CONFIRMER, C.CONFIRMER_RENFORCE)
        assert C.decision(niveau, True, C.PILOTE) in (C.CONFIRMER, C.CONFIRMER_RENFORCE)


def test_refus_non_confirme_rien_n_est_fait(dossier):
    ex, conf = permanent(dossier, reponses=[False])
    _, rid = demander(ex, "démarre personal")
    r = plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)
    assert r["statut"] == proto.REFUSE and ex.backend.info("personal").state != "Running"


# --- demandes forgées ---------------------------------------------------------------

def test_demande_forgee_par_un_autre_qube_refusee(dossier):
    ex, conf = permanent(dossier, reponses=[True])
    _, rid = demander(ex, "démarre personal")
    # Un qube ordinaire ou un faux jetable qui cite la demande de sys-agent : refusé
    # (identité qrexec, registre des demandes lié à sys-agent).
    for appelant in ("work", "personal", "disp12"):
        r = plan(ex, appelant, {"op": "vm.start", "name": "personal"}, request=rid)
        assert r["statut"] == proto.REFUSE and r["raison"] in ("appelant non autorisé", "agent inconnu"), r
    assert conf.demandes == [] and ex.backend.info("personal").state != "Running"
    # Le registre ne rend la demande qu'à sys-agent.
    assert ex.demandes.vivante(rid, "sys-agent") and ex.demandes.vivante(rid, "disp12") is None
    # Le validateur, lui aussi, ne laisse passer que sys-agent ou un nom de jetable.
    v = validator.Validateur(lambda m: proto.reponse(proto.FAIT),
                             config=validator.Config(appelants=LONG, pauses=(f"{dossier}/p", f"{dossier}/q")))
    assert v.traiter("work", "ok", b"{}")["raison"] == "appelant non autorisé"


def test_sys_agent_qui_cite_une_demande_inventee_ou_remplacee(dossier):
    ex, conf = permanent(dossier, reponses=[True, True])
    _, ancien = demander(ex, "démarre personal")
    _, rid = demander(ex, "liste mes qubes")
    for faux in ("R-123456", ancien):
        assert faux != rid
        plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=faux)
    # Sans demande valide : classé « sans demande », plancher de portée → renforcée.
    assert [d.texte_demande for d in conf.demandes] == [None, None]
    assert all(d.decision == C.CONFIRMER_RENFORCE for d in conf.demandes)


def test_palette_ne_peut_pas_choisir_sys_agent_ni_un_autre_nom(dossier):
    ex, _ = permanent(dossier)
    for agent in ("sys-agent", "work", "dom0"):
        r = ex.traiter_demande({"type": "demande", "texte": "x", "agent": agent})
        assert r["statut"] == proto.REFUSE, r
    assert ex.demandes.demandes == {}


def test_continuer_avec_un_jetable_reste_possible(dossier):
    # Un jetable lancé avant le passage en agent permanent garde sa suite (§8).
    ex, _ = monde(dossier)
    a, _ = demander(ex, "liste")
    ex.config = C.Config(appelants=LONG, jetables=frozenset({"qaf-dvm"}))
    agent, rid = demander(ex, "continue", agent=a)
    assert agent == a and ex.agents.agents[a]["request"] == rid
    assert demander(ex, "nouvelle")[0] == "sys-agent"


# --- fenêtre de confirmation --------------------------------------------------------

def test_fenetre_dit_agent_permanent(dossier):
    ex, conf = permanent(dossier, reponses=[True])
    _, rid = demander(ex, "démarre personal")
    plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)
    d = conf.demandes[0]
    msg = {"type": "confirmer", "appelant": d.appelant, "canonique": d.canonique.decode(),
           "niveaux": list(d.niveaux), "decision": d.decision, "jeton": d.jeton, "influence": d.influence,
           "qubes": [list(q) for q in d.qubes], "expire_dans": 100, "texte_demande": d.texte_demande,
           "age_demande": d.age_demande, "hors_portee": list(d.hors_portee)}
    v = confirmation.rediger(confirmation.lire_requete(msg))
    assert v.influence and confirmation.ALERTE_PERMANENT in v.alerte
    assert confirmation.ALERTE_INFLUENCE not in v.alerte


# --- qaf.Request dans sys-agent ------------------------------------------------------

def entree(rid="R-004211", texte="liste mes qubes"):
    return io.BytesIO(json.dumps({"request": rid, "texte": texte}).encode())


class Popen:
    def __init__(self, pid):
        self.pid = pid
        self.lances = []

    def __call__(self, argv, **kw):
        self.lances.append((argv, kw))
        return self


def test_qaf_request_refuse_un_appel_qui_ne_vient_pas_de_dom0(tmp_path):
    p = Popen(4242)
    for dom in ("work", "disp12", "sys-agent", ""):
        env = {"QREXEC_REMOTE_DOMAIN": dom, "HOME": str(tmp_path)}
        assert demande.main(entree(), env, p, jetable=False, racine_verrou=str(tmp_path / "run")) == 1
    assert p.lances == []


def test_qaf_request_une_seule_fenetre_puis_continue(tmp_path):
    fermes = []
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    run = str(tmp_path / "run")
    p = Popen(4242)
    assert demande.main(entree(), env, p, jetable=False, racine_verrou=run, fermer=fermes.append) == 0
    argv, kw = p.lances[0]
    assert argv[0] == demande.TERMINAL and "--continue" not in argv and "QAF_REQUEST=R-004211" in argv
    assert "/bin/sh" not in argv and "sudo" not in " ".join(argv)  # jamais d'extinction de sys-agent
    pid = tmp_path / "run" / "qaf-request" / "terminal"
    assert pid.read_text() == "4242\n" and fermes == [str(pid)]
    # Une conversation existe dans ~/agent : la demande suivante la reprend.
    (tmp_path / "agent").mkdir()
    projet = tmp_path / ".claude" / "projects" / str(tmp_path / "agent").replace("/", "-").replace("_", "-")
    projet.mkdir(parents=True)
    (projet / "abc.jsonl").write_text("{}\n")
    p2 = Popen(4343)
    assert demande.main(entree("R-004212", "et maintenant ?"), env, p2, jetable=False, racine_verrou=run,
                        fermer=fermes.append) == 0
    argv = p2.lances[0][0]
    assert argv[-4:] == ["/usr/bin/claude", "--continue", "--", "et maintenant ?"]
    assert "QAF_REQUEST=R-004212" in argv and p2.lances[0][1]["cwd"] == str(tmp_path / "agent")
    assert fermes == [str(pid)] * 2 and pid.read_text() == "4343\n"


def test_qaf_request_jeton_longue_duree_aussi_dans_sys_agent(tmp_path):
    (tmp_path / ".config/qaf").mkdir(parents=True)
    (tmp_path / ".config/qaf/jeton-claude").write_text("sk-ant-oat01-abc\n")
    p = Popen(5)
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    demande.main(entree(), env, p, jetable=False, racine_verrou=str(tmp_path / "run"), fermer=lambda f: None)
    argv, kw = p.lances[0]
    assert kw["env"][demande.VARIABLE_JETON] == "sk-ant-oat01-abc" and "sk-ant" not in " ".join(argv)


def faux_proc(tmp_path, pid, argv):
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    return str(tmp_path / "proc")


def test_fermer_precedent_seulement_notre_terminal(tmp_path):
    fichier = tmp_path / "terminal"
    fichier.write_text("777\n")
    notre = demande.commande_terminal("R-004211", "x")
    proc = faux_proc(tmp_path, 777, notre)
    signaux = []

    def tuer(pid, sig):
        signaux.append((pid, sig))
        if sig == signal.SIGTERM:  # le terminal se ferme
            for n in os.listdir(f"{proc}/{pid}"):
                os.unlink(f"{proc}/{pid}/{n}")
            os.rmdir(f"{proc}/{pid}")
    assert demande.fermer_precedent(str(fichier), proc, tuer, dormir=lambda s: None) is True
    assert signaux == [(777, signal.SIGTERM)]
    # pid réutilisé par un autre processus, ou un terminal qui n'est pas « Agent » : jamais tué.
    for i, autre in enumerate((["/usr/bin/firefox"], ["/usr/bin/xfce4-terminal", "--title=Connexion"])):
        pid = 800 + i
        fichier.write_text(f"{pid}\n")
        faux_proc(tmp_path, pid, autre)
        assert demande.fermer_precedent(str(fichier), proc, tuer, dormir=lambda s: None) is False
    for contenu in ("", "abc", "1", "-5"):
        fichier.write_text(contenu)
        assert demande.fermer_precedent(str(fichier), proc, tuer, dormir=lambda s: None) is False
    assert signaux == [(777, signal.SIGTERM)]


def test_fermer_precedent_tue_si_le_terminal_ne_se_ferme_pas(tmp_path):
    fichier = tmp_path / "terminal"
    fichier.write_text("900\n")
    proc = faux_proc(tmp_path, 900, demande.commande_terminal("R-004211", "x"))
    signaux = []
    assert demande.fermer_precedent(str(fichier), proc, lambda p, s: signaux.append(s), dormir=lambda s: None,
                                    delai=0) is True
    assert signaux == [signal.SIGTERM, signal.SIGKILL]


def test_conversation_existe(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert not demande.conversation_existe(env, "/home/user/agent")
    d = tmp_path / ".claude/projects/-home-user-agent"
    d.mkdir(parents=True)
    (d / "notes.txt").write_text("")
    assert not demande.conversation_existe(env, "/home/user/agent")
    (d / "1.jsonl").write_text("")
    assert demande.conversation_existe(env, "/home/user/agent")
    assert demande.conversation_existe({"CLAUDE_CONFIG_DIR": str(tmp_path / ".claude")}, "/home/user/agent")


# --- palette (dom0) --------------------------------------------------------------

class Appel:
    def __init__(self):
        self.argv = None

    def __call__(self, argv, input=None, **kw):
        self.argv, self.entree = argv, input
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_palette_transmet_a_sys_agent_que_dom0_demarre():
    ex = Appel()
    rep = {"statut": "fait", "request": "R-004211", "qubes": [], "agent": "sys-agent"}
    r = palette.demander("range mes téléchargements", lambda m: rep, ex)
    assert r.ok and "sys-agent" in r.message and "mémoire" in r.message
    # qvm-run (dom0) démarre sys-agent s'il est arrêté : jamais --no-autostart.
    assert ex.argv == ["/usr/bin/qvm-run", "--pass-io", "--service", "sys-agent", "qaf.Request"]
    assert json.loads(ex.entree) == {"request": "R-004211", "texte": "range mes téléchargements"}


def test_palette_refuse_sys_agent_pour_une_suite_et_les_autres_noms():
    for agent, nom in ((None, "work"), (None, "sys-agent2"), ("disp4", "sys-agent"), (None, "dom0")):
        ex = Appel()
        rep = {"statut": "fait", "request": "R-004211", "agent": nom}
        r = palette.demander("x", lambda m: rep, ex, agent=agent)
        assert not r.ok and ex.argv is None, (agent, nom)


def test_constantes_alignees_sur_le_portier():
    assert palette.AGENT_PERMANENT == proto.AGENT_LONG


def test_agent_permanent_lu_dans_portier_json(tmp_path):
    p = tmp_path / "portier.json"
    assert palette.agent_permanent(str(p)) is True  # absent : comme classify.lire_config
    for contenu, attendu in (('{"agent_long": true}', True), ('{"agent_long": false}', False), ("{}", False),
                             ("pas du json", False), ('{"agent_long": "oui"}', False), ("[]", False)):
        p.write_text(contenu)
        assert palette.agent_permanent(str(p)) is attendu, contenu


def test_executeur_n_a_pas_de_jetable_a_ramasser(dossier):
    # entretenir() en mode permanent : rien à tuer, sys-agent n'est jamais dans le registre Agents.
    ex, _ = permanent(dossier)
    demander(ex, "liste")
    ex.entretenir()
    assert ex.backend.info("sys-agent") is not None and ex.agents.agents == {}



# --- suspension des confirmations (« sudoless » d'Omarchy, 28/10) -------------------

def test_portier_json_livre_avec_fenetre_de_confirmation():
    livre = json.loads((RACINE / "packaging/portier.json").read_text(encoding="utf-8"))
    assert "confirmations" not in livre
    with pytest.raises(ValueError):  # l'ancien réglage du 28/10 au matin n'existe plus
        C.Config.de_json({"confirmations": "aucune"})


def suspension(dossier, jusqua, mode=0o644):
    chemin = f"{dossier}/suspension"
    with open(chemin, "w", encoding="ascii") as f:
        f.write(json.dumps({"jusqua": jusqua}))
    os.chmod(chemin, mode)
    return chemin


def test_suspendue_echeance_proprietaire_et_droits(dossier):
    moi = os.getuid()
    assert not pause.suspendue(f"{dossier}/absent", proprietaire=moi)
    assert pause.suspendue(suspension(dossier, None), proprietaire=moi)  # permanent
    assert pause.suspendue(suspension(dossier, 2000), maintenant=lambda: 1000, proprietaire=moi)
    assert not pause.suspendue(suspension(dossier, 2000), maintenant=lambda: 2000, proprietaire=moi)
    assert not pause.suspendue(suspension(dossier, None), proprietaire=moi + 1)  # pas à root : non
    assert not pause.suspendue(suspension(dossier, None, 0o666), proprietaire=moi)  # modifiable par d'autres
    for mauvais in ("oui", "{}", '{"jusqua": "demain"}', '{"jusqua": 1.5}'):
        with open(f"{dossier}/suspension", "w", encoding="ascii") as f:
            f.write(mauvais)
        assert not pause.suspendue(f"{dossier}/suspension", proprietaire=moi)
    os.unlink(f"{dossier}/suspension")
    os.symlink(suspension(f"{dossier}", None) + "", f"{dossier}/lien")
    assert not pause.suspendue(f"{dossier}/lien", proprietaire=moi)


def test_suspension_ce_qui_serait_confirme_passe_seul_n4_reste_refuse(dossier, monkeypatch):
    ex, conf = permanent(dossier, reponses=[False])
    ex.suspension = suspension(dossier, None)
    monkeypatch.setattr(pause, "suspendue", lambda chemin: chemin == ex.suspension)
    _, rid = demander(ex, "démarre personal")
    assert plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)["statut"] == "fait"
    assert conf.demandes == []  # aucune fenêtre ouverte
    auto = [e for e in journal(dossier) if e["type"] == "confirmation"]
    assert auto[-1]["suspension"] is True and auto[-1]["accordee"] is True
    r = plan(ex, "sys-agent", {"op": "template.install", "template": "debian-13-minimal", "repo": "official"},
             request=rid)
    assert r["statut"] == proto.REFUSE and conf.demandes == []
    pause.mettre(f"{dossier}/pause", "pause")  # la pause l'emporte sur la suspension
    r = plan(ex, "sys-agent", {"op": "vm.shutdown", "name": "personal"}, request=rid)
    assert r["statut"] == proto.REFUSE


def test_sans_suspension_la_fenetre_revient(dossier):
    ex, conf = permanent(dossier, reponses=[False])
    ex.suspension = f"{dossier}/absente"
    _, rid = demander(ex, "démarre personal")
    assert plan(ex, "sys-agent", {"op": "vm.start", "name": "personal"}, request=rid)["statut"] == proto.REFUSE
    assert len(conf.demandes) == 1


def test_qaf_suspendre(dossier, capsys):
    from qaf_ui import suspension as S
    moi, chemin = os.getuid(), f"{dossier}/s"
    assert S.main(["etat"], chemin, proprietaire=moi) == 0 and "confirmations actives" in capsys.readouterr().out
    assert S.main(["1h"], chemin, maintenant=lambda: 1000, proprietaire=moi) == 0
    assert S.echeance(chemin, lambda: 1600, moi) == 3000
    assert S.main(["permanent"], chemin, proprietaire=moi) == 0 and S.echeance(chemin, proprietaire=moi) == "permanent"
    assert oct(os.stat(chemin).st_mode & 0o777) == "0o644"
    assert S.main(["off"], chemin, proprietaire=moi) == 0 and not os.path.exists(chemin)
    assert S.main(["2h"], chemin, proprietaire=moi) == 2
