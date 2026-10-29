"""PARCOURS-3 (docs/PARCOURS-3.md §4), correctifs côté agent : n13 (extinction
au dernier terminal), n14 (claude --continue pour une suite), n15/n16/n18/n10
(skill), n16 (raison exacte du refus d'une copie de modèle), n17 (modèle livré
par Qubes toujours à l'utilisateur). n9 et n18 côté qaf_hand : tests/test_hand.py."""

import dataclasses
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from qaf_gate import classify as C
from qaf_gate import proto
from qaf_mcp import demande
from qaf_mcp.server import liste_compacte
from qaf_ui import palette
import skills_qaf
from backend_sim import _q
from exemples_ops import op
from test_backend_qubes_v3 import qubes  # noqa: F401 (fixture)
from test_ops_v3_classement import AGENT, CONFIG, classer, contexte, installation
from test_ops_v3_classement import plan as faire_plan
from test_ops_v3_execution import faire, lancer

RACINE = Path(__file__).resolve().parent.parent
SKILL = skills_qaf.tous().replace("\n", " ")  # tous les skills livrés : qaf, qaf-…


# --- n13 / n14 : qaf.Request dans un jetable ------------------------------------------

def entree(rid="R-004211", texte="liste mes qubes"):
    return io.BytesIO(json.dumps({"request": rid, "texte": texte}).encode())


def lancer_service(tmp_path, rid="R-004211", texte="liste mes qubes"):
    lances = []
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    code = demande.main(entree(rid, texte), env, lambda argv, **kw: lances.append(argv), jetable=True,
                        racine_verrou=str(tmp_path / "run"))
    assert code == 0
    return lances[0]


def test_premiere_demande_agent_neuf_puis_suite_avec_continue(tmp_path):
    argv = lancer_service(tmp_path)
    verrou = str(tmp_path / "run" / "qaf-request" / "agents")
    assert argv[:5] == ["/bin/sh", "-c", demande.APRES, "qaf-agent", verrou] and os.path.exists(verrou)
    assert "--continue" not in argv and argv[-3:] == ["/usr/bin/claude", "--", "liste mes qubes"]
    # « Continuer avec cet agent » : même jetable, la conversation est reprise (n14).
    argv = lancer_service(tmp_path, "R-004212", "oui, supprime aussi le modèle")
    assert argv[-4:] == ["/usr/bin/claude", "--continue", "--", "oui, supprime aussi le modèle"]
    assert "QAF_REQUEST=R-004212" in argv and argv[4] == verrou


def test_hors_jetable_ni_verrou_ni_continue_sans_conversation(tmp_path):
    # sys-agent (agent permanent, D28) : jamais de verrou d'extinction ; sans
    # conversation déjà écrite dans ~/.claude, pas de --continue
    # (tests/test_agent_permanent.py pour la reprise).
    lances = []
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    for _ in range(2):
        demande.main(entree(), env, lambda argv, **kw: lances.append(argv), jetable=False,
                     racine_verrou=str(tmp_path / "run"))
    assert all(a[0] == demande.TERMINAL and "--continue" not in a for a in lances)
    assert not (tmp_path / "run" / "qaf-request" / "agents").exists()


def test_verrou_impossible_pas_d_extinction(tmp_path):
    # Sans fichier de verrou, le terminal se lance sans extinction : les
    # minuteries de dom0 restent la garantie (R-8), aucun terminal n'éteint les autres.
    (tmp_path / "run").write_text("pas un dossier")
    argv = lancer_service(tmp_path)
    assert argv[0] == demande.TERMINAL and "sudo" not in " ".join(argv)


@pytest.mark.skipif(not (shutil.which("flock") and os.path.exists(demande.FLOCK)), reason="flock absent")
def test_extinction_au_dernier_terminal_seulement(tmp_path):
    # n13 : fin 1 de PARCOURS-3, fermer le 1er terminal éteignait disp6000 et ses 10 autres terminaux.
    eteint = tmp_path / "eteint"
    script = demande.APRES.replace(demande.EXTINCTION, f"echo eteint >> {eteint}")
    verrou, _ = demande.verrou_agents(str(tmp_path / "run"))
    fins = [tmp_path / f"fin{i}" for i in range(3)]

    def terminal(fin):  # un « terminal » qui dure jusqu'à ce qu'on crée son fichier de fin
        return subprocess.Popen(["/bin/sh", "-c", script, "qaf-agent", verrou, "/bin/sh", "-c",
                                 f'while [ ! -e "{fin}" ]; do sleep 0.05; done; sleep 3 &'],
                                start_new_session=True)
    ps = [terminal(f) for f in fins]
    time.sleep(0.5)
    for i, (p, f) in enumerate(zip(ps, fins)):
        f.touch()
        p.wait(timeout=10)  # le fils détaché (sleep 3) ne retient pas l'extinction
        assert eteint.exists() == (i == len(ps) - 1), i
    assert eteint.read_text() == "eteint\n"


@pytest.mark.skipif(not os.path.exists(demande.FLOCK), reason="flock absent")
def test_terminal_tue_ne_bloque_pas_l_extinction(tmp_path):
    # Robuste aux courses et aux processus tués : le noyau libère le verrou d'un mort.
    eteint = tmp_path / "eteint"
    script = demande.APRES.replace(demande.EXTINCTION, f"echo eteint >> {eteint}")
    verrou, _ = demande.verrou_agents(str(tmp_path / "run"))
    tue = subprocess.Popen(["/bin/sh", "-c", script, "qaf-agent", verrou, "sleep", "60"], start_new_session=True)
    court = subprocess.Popen(["/bin/sh", "-c", script, "qaf-agent", verrou, "sleep", "1"], start_new_session=True)
    court.wait(timeout=10)
    assert not eteint.exists()  # l'autre terminal tourne encore
    tue.kill()
    tue.wait()
    autre = subprocess.Popen(["/bin/sh", "-c", script, "qaf-agent", verrou, "true"])
    autre.wait(timeout=10)
    assert eteint.exists()


@pytest.mark.skipif(not os.path.exists(demande.FLOCK), reason="flock absent")
def test_verrou_illisible_terminal_lance_sans_extinction(tmp_path):
    # Revue du 25/10 : « exec 9>>… » qui échoue tuait le sh (builtin spécial) et le
    # terminal ne s'ouvrait pas. Avec « command exec », il s'ouvre ; pas d'extinction.
    eteint, lance = tmp_path / "eteint", tmp_path / "lance"
    script = demande.APRES.replace(demande.EXTINCTION, f"echo eteint >> {eteint}")
    r = subprocess.run(["/bin/sh", "-c", script, "qaf-agent", str(tmp_path / "absent" / "verrou"),
                        "/bin/sh", "-c", f"echo oui > {lance}"], capture_output=True, timeout=10)
    assert lance.read_text().strip() == "oui" and not eteint.exists(), r.stderr


def test_palette_dit_que_la_suite_reprend_la_conversation():
    rep = {"statut": "fait", "request": "R-0042", "agent": "disp6000", "qubes": []}
    ok = subprocess.CompletedProcess([], 0, b"ok", b"")
    r = palette.demander("et maintenant ?", appeler=lambda m: rep, executer=lambda *a, **k: ok, agent="disp6000")
    assert r.ok and palette.SUITE in r.message and "reprend la conversation de cet agent" in r.message
    r = palette.demander("liste", appeler=lambda m: rep, executer=lambda *a, **k: ok)
    assert "agent neuf" in r.message and palette.SUITE not in r.message


# --- n15, n16, n18, n10 : la skill ---------------------------------------------------

def test_skill_l_agent_est_sys_agent_permanent():
    # D28 (27/10) : l'agent est sys-agent, sa mémoire survit ; le mode jetable reste décrit.
    assert "Tu es **l'agent permanent** de l'utilisateur : tu tournes dans `sys-agent`" in SKILL
    assert "`sys-agent` n'est pas toi" not in SKILL and "**Dans un jetable** (`dispNNNN`)" in SKILL
    assert "N'écris jamais dans ta mémoire" in " ".join(SKILL.split())
    assert "copie dans sys-agent" not in SKILL and "visionneuse de sys-agent" not in SKILL


def test_skill_fin_de_tache_sans_repartir_de_zero():
    # Parcours 4 : l'agent finissait par « je repartirai de zéro » alors qu'une
    # suite (« Continuer avec cet agent ») reprend sa conversation (n14).
    assert "repartirai de zéro »" not in SKILL
    assert "Pour continuer avec moi, choisis dispNNNN dans la palette" in " ".join(SKILL.split())


def test_skill_choisit_un_modele_sans_dependant_sensible():
    i = SKILL.index("**Choisir le modèle à copier")
    bloc = SKILL[i:i + 1200]
    assert "**tous** les qubes sont des espaces ordinaires" in bloc and "demande à l'utilisateur" in bloc
    assert "le refus nomme les qubes" in bloc and "sans redemander" in bloc


def test_skill_appli_graphique_detachee_et_verification_avant_installation():
    assert "setsid APPLI </dev/null >/dev/null 2>&1 &" in SKILL
    assert "**Avant d'installer, vérifie**" in SKILL and "command -v" in SKILL
    assert "conversation reprend" in SKILL and "livré par Qubes" in SKILL


# --- n16 : raison exacte du refus d'une copie de modèle -------------------------------

def test_refus_de_copie_nomme_les_qubes_qui_dependent_du_modele():
    # p2 de PARCOURS-3 : copie de default_template refusée sans dire pourquoi.
    ctx = contexte()
    p = faire_plan(op("vm.clone", source="fedora-42-mgmt", name="tpl-neuf"))
    assert classer(op("vm.clone", source="fedora-42-mgmt", name="tpl-neuf")).decision == C.REFUSER
    m = C.motif_n4(p, ctx, AGENT)
    assert "le modèle fedora-42-mgmt sert à default-mgmt-dvm" in m and "copie un autre modèle" in m
    assert "portier" not in m and "Doorman" not in m and "N4" not in m
    # debian-13-xfce : les qubes basés dessus d'abord (6 au plus), puis « … ».
    m = C.motif_n4(faire_plan(op("vm.clone", source="debian-13-xfce", name="t")), ctx, AGENT)
    assert "sert à a-net, default-dvm, sys-firewall, sys-net, sys-usb, vault… :" in m
    # Sans contexte (anciens appels) : message général inchangé.
    assert C.motif_n4(p).endswith("ce qu'il faudrait faire et pourquoi")


def test_refus_du_portier_rend_la_raison_a_l_agent(tmp_path):
    qs = [*installation().list_qubes()]
    ex = faire(tmp_path, qubes=qs)
    r = lancer(ex, op("vm.clone", source="fedora-42-mgmt", name="tpl-neuf"))
    assert r["statut"] == proto.REFUSE and "fedora-42-mgmt sert à default-mgmt-dvm" in r["raison"]


# --- n17 : un modèle livré par Qubes reste à l'utilisateur -------------------------------------

def ctx_livre(livre=True):
    sim = installation()
    sim.qubes["debian-13-xfce"] = dataclasses.replace(sim.qubes["debian-13-xfce"], livre=livre)
    # t2c : le modèle ne porte qu'un espace de l'agent (dev-demo au registre).
    for n in ("sys-net", "sys-firewall", "sys-usb", "default-dvm", "vault", "personal", "untrusted", "a-net", "a-dvm"):
        sim.qubes[n] = dataclasses.replace(sim.qubes[n], template="fedora-42-xfce")
    sim.qubes["dev-demo"] = _q("dev-demo", "AppVM", "gray", "debian-13-xfce", None)
    return contexte(sim, CONFIG)


@pytest.mark.parametrize("o", [op("pkg.install", name="debian-13-xfce", packages=["tree"]),
                               op("pkg.remove", name="debian-13-xfce", packages=["tree"]),
                               op("qube.exec", name="debian-13-xfce", argv=["ls"], timeout=5),
                               op("update.run", targets=["debian-13-xfce"], apply="none")])
def test_ecrire_dans_un_modele_livre_toujours_confirme(o):
    agent = AGENT | {"dev-demo", "debian-13-xfce"}
    portee = {"debian-13-xfce"}  # nommé dans la demande, session propre (t2c)
    assert classer(o, ctx=ctx_livre(), agent=agent, portee=portee).decision == C.CONFIRMER
    assert classer(o, ctx=ctx_livre(), agent=agent, portee=portee, mode=C.PILOTE).decision == C.CONFIRMER
    if o["op"] in ("pkg.install", "update.run"):  # sans le correctif (pas livré) : automatique
        assert classer(o, ctx=ctx_livre(False), agent=agent, portee=portee).decision == C.AUTO


def test_modele_livre_jamais_a_l_agent_mais_son_clone_oui():
    ctx = ctx_livre()
    # Même au registre de l'agent, avec une descendance toute à l'agent : M (sans le correctif : A).
    sim_global = dataclasses.replace(ctx, prefs={**ctx.prefs, "default_template": "fedora-42-xfce"})
    agent = AGENT | {"debian-13-xfce", "dev-demo"}
    assert sim_global.classe("debian-13-xfce", agent) == C.M
    sans = ctx_livre(False)
    assert dataclasses.replace(sans, prefs={**sans.prefs, "default_template": "fedora-42-xfce"}).classe(
        "debian-13-xfce", agent) == C.A
    # Le clone fait par l'agent (registre, pas livré) reste à l'agent : automatique.
    assert classer(op("pkg.install", name="tpl-a", packages=["tree"]), ctx=ctx).decision == C.AUTO
    assert C.modeles_agent({"dev-demo"}, ctx, AGENT | {"dev-demo", "debian-13-xfce"}) == set()


def test_backend_reconnait_un_modele_livre(qubes):  # noqa: F811
    b, app = qubes
    tpl = app.domains["debian-13-xfce"]
    assert b.info("debian-13-xfce").livre is False
    tpl.features["template-name"] = "debian-13-xfce"  # qvm-template install
    assert b.info("debian-13-xfce").livre is True
    tpl.features["template-name"] = "debian-13"  # clone : garde la valeur de sa source
    assert b.info("debian-13-xfce").livre is False
    tpl.__dict__["installed_by_rpm"] = True  # ancien paquet RPM
    assert b.info("debian-13-xfce").livre is True
    assert b.info("work").livre is False  # seuls les modèles


def test_liste_de_l_agent_marque_les_modeles_livres():
    sortie = json.dumps({"statut": "fait", "resultats": [{"op": "vm.list", "resultat": [
        {"name": "debian-13-xfce", "state": "Halted", "klass": "TemplateVM", "label": "black", "livre": True},
        {"name": "tpl-a", "state": "Halted", "klass": "TemplateVM", "label": "gray", "livre": False}]}]}).encode()
    lignes = liste_compacte(sortie).splitlines()
    assert lignes[3].endswith("| livré par Qubes") and "livré" not in lignes[4]
