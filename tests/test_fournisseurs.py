"""Fournisseurs de l'agent, côté sys-agent : table, réglages, lancement, service
qaf.Fournisseur, qaf.Usage v=2, assistant, installation. Sans réseau : faux serveurs
locaux et faux téléchargements."""

import hashlib
import http.server
import io
import json
import os
import pathlib
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

import pytest

from qaf_gate.schema import _REQUEST_RE, CLIS
from qaf_mcp import assistant, console, demande, lanceur
from qaf_mcp import fournisseurs as f4
from qaf_mcp import fournisseurs_table as table
from qaf_mcp import usage as usage_sys
from qaf_ui import usage as dom0

CLE = "faux-cle-CLE-SECRETE-NE-SORT-PAS"
JETON_OAUTH = "faux-oauth-JETON-OAUTH"
OMARCHY = ("claude", "codex", "grok", "kimi", "cursor", "grokbot", "copilot", "muse", "agy", "kimchi", "hermes",
           "devin", "vibe", "qwen", "opencode", "pi", "omp", "ori", "crush", "openclaw", "dsh", "mcode", "kiro-cli",
           "freebuff", "kilo", "amp", "commandcode", "zai", "ollama", "minimax", "openrouter", "deepseek", "fireworks",
           "synthetic", "local")
RACINE = pathlib.Path(__file__).parent.parent


def preparer_maison(tmp_path):
    m = str(tmp_path)
    (tmp_path / "agent").mkdir(exist_ok=True)
    (tmp_path / ".claude.json").write_text(json.dumps({
        "mcpServers": {"qaf": {"command": "/usr/bin/python3"}},
        "projects": {str(tmp_path / "agent"): {"hasTrustDialogAccepted": True}}}))
    return m


def tiers_pret(tmp_path, id="openrouter", modele="~anthropic/claude-opus-latest[1m]", essai=False):
    m = preparer_maison(tmp_path)
    f = table.PAR_ID[id]
    base = f.base_url or "http://10.0.0.20:8080"
    f4.ecrire_cle(m, id, CLE)
    f4.generer_settings(m, id, base, modele, cle=True, essai=essai)
    f4.noter_test(m, id, f4.nettoyer_url(base), modele)
    return m


def cli_prete(tmp_path, id="codex"):
    m = preparer_maison(tmp_path)
    f = table.PAR_ID[id]
    b = pathlib.Path(m, ".local", "bin", pathlib.Path(f.binaires[-1]).name)
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("#!/bin/sh\n")
    p = pathlib.Path(m, f.preuves[0].chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    f4.poser_mcp(f, m)
    return m


class Proc:
    pid = 4321


def lanceur_note(lances):
    return lambda argv, **k: lances.append((argv, k)) or Proc()


def entree(rid="R-004211", texte="liste mes qubes"):
    return io.BytesIO(json.dumps({"request": rid, "texte": texte}).encode())


def env_dom0(m, **k):
    return {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": m, "PATH": "/usr/bin:/bin", **k}


# --- table ---------------------------------------------------------------------------

def test_liste_ordre_et_ids_d_omarchy():
    assert table.IDS == OMARCHY and len(table.IDS) == 35 and table.IDS_HORS_CLAUDE == OMARCHY[1:]
    assert table.PAR_ID["ollama"].base_url == "https://ollama.com" and table.PAR_ID["local"].base_url == ""
    assert table.PAR_ID["grokbot"].famille == "connexion_seule"


def test_meme_liste_que_dom0():
    if not hasattr(dom0, "IDS"):
        pytest.skip("qaf_ui/ pas encore au contrat v=2 (lot voisin)")
    assert tuple(dom0.IDS) == table.IDS


def test_le_doorman_n_est_pas_touche():
    assert CLIS == ("claude",)


def valeurs(x):
    """Chaque texte d'une structure, clés comprises : un drapeau ou un réglage."""
    if isinstance(x, dict):
        return [v for k, w in x.items() for v in [k, *valeurs(w)]]
    if isinstance(x, (list, tuple)):
        return [v for w in x for v in valeurs(w)]
    return [x] if isinstance(x, str) else []


def test_aucun_mode_tout_passe():
    for f in table.FOURNISSEURS:
        mots = [*f.lancer.args, *f.lancer.suite, f.lancer.option, *f.login, f.login_tui, f.login_page,
                *valeurs(f.lancer.env)]
        if f.mcp:
            mots += valeurs(f.mcp.entree) + list(f.mcp.apres)
            for _, _, v in f.mcp.reglages + f.mcp.socle + f.mcp.exige:
                mots += valeurs(v)
        assert not set(mots) & set(table.INTERDITS), f.id
    for id in table.IDS_HORS_CLAUDE:
        f = table.PAR_ID[id]
        assert "yolo" not in repr(f).lower() and "skip-permissions" not in repr(f)


def test_les_choix_d_omarchy_sans_validation_sont_interdits():
    for mot in ("--dangerously-allow-all", "-y", "--trust-all-tools", "--auto-approve", "yolo", "self-drive",
                "danger-full-access", "bypassPermissions", "bypass", "dont-ask", "accept-edits", "--auto"):
        assert mot in table.INTERDITS, mot


def test_cli_qui_validerait_seulement_si_on_la_regle():
    t = table.PAR_ID
    assert t["omp"].lancer.args == ("--approval-mode", "always-ask-cached")  # sans lui : yolo
    assert t["commandcode"].lancer.args == ("--permission-mode", "default")
    assert t["openclaw"].lancer.args == ("chat",)  # sans passerelle
    assert t["dsh"].lancer.retire == ("DSH_PERMISSION_MODE",)
    assert not any(k == "DSH_PERMISSION_MODE" for k, _ in t["dsh"].lancer.env)
    assert t["openclaw"].mcp.exige[0][1:] == (("tools", "exec", "mode"), "ask")
    assert t["kilo"].mcp.exige[0][2] == {"bash": "ask", "edit": "ask"}


def test_sans_validation_prouvee_jamais_l_agent(tmp_path):
    m = str(tmp_path)
    assert tuple(dom0.JAMAIS_AGENT) == tuple(f.id for f in table.FOURNISSEURS if f.famille == "connexion_seule")
    assert set(dom0.JAMAIS_AGENT) == {"grokbot", "pi", "ori", "mcode", "kiro-cli", "freebuff", "amp"}
    for id in ("pi", "freebuff", "amp"):
        f = table.PAR_ID[id]
        assert f.mcp is None and f.install is not None and f.note, id
        b = pathlib.Path(m, ".local/bin", id)
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text("#!/bin/sh\n")
        p = pathlib.Path(m, f.preuves[0].chemin)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"a": "b"}')
        assert f4.preuve(f, m) and f4.binaire(f, m)
        assert not f4.activable(id, m) and id not in f4.agents(m) and f4.etat(id, m) == "connexion_seule"
        f4.ecrire_actif(m, id)
        assert f4.actif_effectif(m) == "claude"
        with pytest.raises(f4.Refus):
            f4.lancement(id, m)
        with pytest.raises(ValueError):
            dom0.argv_bascule(id)


def test_assistant_d_une_cli_sans_agent_n_ecrit_aucun_serveur(tmp_path):
    m = preparer_maison(tmp_path)
    b = pathlib.Path(m, ".local/bin/pi")
    b.parent.mkdir(parents=True)
    b.write_text("#!/bin/sh\n")
    p = pathlib.Path(m, ".pi/agent/auth.json")
    p.parent.mkdir(parents=True)
    p.write_text('{"openai": {}}')
    ok, t = faire("pi", m, [], executer=Exe())
    assert ok is True and "ne demande jamais rien" in t and "connexion seule" in t and "Utiliser" not in t
    assert not pathlib.Path(m, ".pi/agent/mcp.json").exists()


def test_textes_des_notes_dans_les_trois_langues():
    for f in table.FOURNISSEURS:
        if f.note:
            assert len(assistant.TEXTES[f.note]) == 3, f.id


def test_exige_retire_de_l_activation_quand_le_reglage_change(tmp_path):
    f = table.PAR_ID["kilo"]
    m = str(tmp_path)
    cli_posee(m, "kilo")
    preuve_de(m, f)
    conf = pathlib.Path(m, ".config/kilo/kilo.json")
    conf.parent.mkdir(parents=True)
    conf.write_text('{"theme": "nuit"}')
    f4.poser_mcp(f, m)
    assert f4.activable("kilo", m)
    d = json.loads(conf.read_text())
    d["permission"] = "allow"
    conf.write_text(json.dumps(d))
    assert f4.mcp_declare(f, m) and not f4.mcp_configure(f, m) and not f4.activable("kilo", m)
    with pytest.raises(f4.Refus) as e:
        f4.lancement("kilo", m)
    assert e.value.args[0] == "non_activable"


def test_exige_ne_force_jamais_un_choix_deja_la(tmp_path):
    m = str(tmp_path)
    k = pathlib.Path(m, ".config/kilo/kilo.json")
    k.parent.mkdir(parents=True)
    k.write_text('{"permission": "allow"}')
    with pytest.raises(f4.Refus) as e:
        f4.poser_mcp(table.PAR_ID["kilo"], m)
    assert e.value.args[0] == "validation_contredite" and json.loads(k.read_text()) == {"permission": "allow"}


def test_kilo_permission_gagne_sur_ses_fichiers():
    """Ses fichiers se fondent, le dernier gagne : la surcouche d'exécution passe après eux."""
    f = table.PAR_ID["kilo"]
    env = dict(f.lancer.env)
    assert json.loads(env["KILO_PERMISSION"]) == {"bash": "ask", "edit": "ask"}
    assert env["KILO_DISABLE_PROJECT_CONFIG"] == "1" and "KILO_CONFIG_CONTENT" in f.lancer.retire


@pytest.mark.parametrize("fichier", [".config/kilo/kilo.jsonc", ".config/kilo/kilo.json", ".kilo/kilo.json"])
def test_kilo_permission_d_un_agent_refusee(tmp_path, fichier):
    m = str(tmp_path)
    f = table.PAR_ID["kilo"]
    f4.poser_mcp(f, m)
    assert f4.mcp_configure(f, m)
    p = pathlib.Path(m, fichier)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text()) if p.exists() else {}
    p.write_text(json.dumps({**d, "agent": {"code": {"permission": {"bash": "allow"}}}}))
    assert not f4.mcp_configure(f, m)
    with pytest.raises(f4.Refus):
        f4.poser_mcp(f, m)


def test_refuse_fichier_illisible_mais_pas_fichier_absent_ou_semence(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["kilo"]
    f4.poser_mcp(f, m)
    p = pathlib.Path(m, ".config/kilo/kilo.jsonc")
    p.write_text('{"$schema": "https://app.kilo.ai/config.json"}')  # ce que Kilo sème
    assert f4.mcp_configure(f, m)
    p.write_text('{// avec commentaire\n}')
    assert not f4.mcp_configure(f, m)


def test_openclaw_borne_les_fichiers_et_refuse_la_surcharge_d_un_agent(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["openclaw"]
    f4.poser_mcp(f, m)
    c = pathlib.Path(m, ".openclaw/openclaw.json")
    d = json.loads(c.read_text())
    assert d["tools"]["fs"]["workspaceOnly"] is True and f4.mcp_configure(f, m)
    for ajout in ({"entries": {"x": {"tools": {"exec": {"mode": "full"}}}}}, {"defaults": {"workspace": "/home/user"}}):
        c.write_text(json.dumps({**d, "agents": ajout}))
        assert not f4.mcp_configure(f, m), ajout
    c.write_text(json.dumps({**d, "tools": {**d["tools"], "fs": {"workspaceOnly": False}}}))
    assert not f4.mcp_configure(f, m)


def cli_posee(m, id):
    f = table.PAR_ID[id]
    b = pathlib.Path(f.binaires[0].replace("~", m, 1))
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("#!/bin/sh\n")
    return f, b


def preuve_de(m, f):
    p = pathlib.Path(m, f.preuves[0].chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("records:\n  a/b:\n    kind: api-key\n" if f.preuves[0].genre == "texte" else "{}")


def test_echec_de_la_sous_commande_defait_la_declaration(tmp_path):
    """Sinon la ligne resterait activable sans sa coupure de mise à jour."""
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "cursor")
    preuve_de(m, f)
    ok, t = faire("cursor", m, [], executer=Exe(code=1))
    assert ok is False and "n'a pas accepté" in t and "prêt" not in t
    assert not f4.mcp_declare(f, m) and not f4.activable("cursor", m)


def test_la_connexion_tourne_avec_l_environnement_du_lancement(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "dsh")
    env = {"HOME": m, "PATH": "/usr/bin", "LANG": "fr_FR.UTF-8", "DSH_PERMISSION_MODE": "danger-full-access"}
    vu = {}

    class Connexion(Exe):
        def __call__(self, argv, **k):
            vu.update(k["env"], patch=f4.mcp_declare(f, m), cwd=k.get("cwd"))
            preuve_de(m, f)
            return super().__call__(argv, **k)

    ok, t = faire("dsh", m, ["o"], env=env, executer=Connexion())
    assert ok is True and vu["patch"] and vu["DSH_TELEMETRY_DISABLED"] == "1" and "DSH_PERMISSION_MODE" not in vu
    assert "page web" in t and "Settings → Models" in t and "tape Settings" not in t


def test_pose_de_la_premiere_heure_defaite_si_la_connexion_n_est_pas_vue(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "dsh")
    ok, t = faire("dsh", m, [], executer=Exe())
    assert ok is False and "non vue" in t and not f4.mcp_declare(f, m)


def test_commande_d_etat_requise_quand_la_preuve_de_fichier_est_faible(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "ori")
    p = pathlib.Path(m, f.preuves[0].chemin)
    p.parent.mkdir(parents=True)
    p.write_text("")
    ok, t = faire("ori", m, [], executer=Exe(code=1))
    assert ok is False and "non vue" in t
    exe = Exe()
    assert faire("ori", m, [], executer=exe)[0] is True and exe.argv[-1][1:] == ["auth"]


def test_seules_les_preuves_faibles_portent_une_commande_d_etat():
    assert {f.id for f in table.FOURNISSEURS if f.etat_commande} == {"ori", "openclaw", "kiro-cli", "commandcode"}


def test_les_consignes_de_connexion_correspondent_a_ce_qui_est_lance():
    t = table.PAR_ID
    assert not t["crush"].login and t["crush"].login_tui == "ctrl+l"  # « crush login » seul = Hyper seulement
    assert t["kilo"].login == ("auth", "login") and not t["kilo"].login_tui
    assert t["dsh"].login_page and not t["dsh"].login_tui


def test_pi_se_connecte_dans_un_dossier_vide_sans_ressources_de_projet(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "pi")
    vu = {}

    class Connexion(Exe):
        def __call__(self, argv, **k):
            vu.update(argv=argv, cwd=k.get("cwd"))
            p = pathlib.Path(m, ".pi/agent/auth.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"a": "b"}')
            return super().__call__(argv, **k)

    ok, t = faire("pi", m, [], executer=Connexion())
    assert ok is True and vu["argv"][1:] == ["--no-approve"] and "n'y tape que la connexion" in t
    assert vu["cwd"] != m and os.listdir(vu["cwd"]) == []


def test_etat_reel_d_une_ligne_en_connexion_seule(tmp_path):
    m = str(tmp_path)
    assert f4.etat("pi", m) == "non_installe" and f4.etat("grokbot", m) == "connexion_seule"
    f, _ = cli_posee(m, "pi")
    assert f4.etat("pi", m) == "deconnecte"
    p = pathlib.Path(m, f.preuves[0].chemin)
    p.parent.mkdir(parents=True)
    p.write_text('{"a": "b"}')
    assert f4.etat("pi", m) == "connexion_seule" and not f4.activable("pi", m)


def test_gel_des_dependances_jamais_apres_le_releve():
    assert table.GEL_DEPENDANCES <= "2026-10-31T23:59:59Z"


def test_exige_json_pose_et_retire(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["kilo"]
    f4.poser_mcp(f, m)
    d = json.loads(pathlib.Path(m, ".config/kilo/kilo.json").read_text())
    assert d["permission"] == {"bash": "ask", "edit": "ask"} and d["autoupdate"] is False
    assert d["mcp"]["qaf"]["command"][0] == table.PYTHON and f4.mcp_configure(f, m)
    f4.retirer("kilo", m)
    assert "permission" not in json.loads(pathlib.Path(m, ".config/kilo/kilo.json").read_text())


def test_openclaw_exige_le_mode_ask_jusque_dans_le_fichier(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["openclaw"]
    f4.poser_mcp(f, m)
    c = pathlib.Path(m, ".openclaw/openclaw.json")
    d = json.loads(c.read_text())
    assert d["tools"]["exec"]["mode"] == "ask" and f4.mcp_configure(f, m)
    d["tools"]["exec"]["mode"] = "full"
    c.write_text(json.dumps(d))
    assert not f4.mcp_configure(f, m)


def test_patch_cordis_de_dsh(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["dsh"]
    c = pathlib.Path(m, ".dsh/cordis.patch.yml")
    c.parent.mkdir()
    c.write_text("- id: autre\n  disabled: true\n")
    f4.poser_mcp(f, m)
    f4.poser_mcp(f, m)  # idempotent
    t = c.read_text()
    assert t.startswith("- id: autre\n  disabled: true\n") and t.count("- insert:\n    - id: qaf\n") == 1
    assert "\n- id: session-log-deepseek\n  disabled: true\n" in t
    assert f'command: "{table.PYTHON}"' in t and f"args: {json.dumps(list(table.COMMANDE_MCP[1:]))}" in t
    assert f4.mcp_configure(f, m)
    f4.retirer("dsh", m)
    assert c.read_text() == "- id: autre\n  disabled: true\n" and not f4.mcp_declare(f, m)


def test_patch_cordis_refuse_ce_qu_il_ne_connait_pas(tmp_path):
    m = str(tmp_path)
    c = pathlib.Path(m, ".dsh/cordis.patch.yml")
    c.parent.mkdir()
    c.write_text("cle: valeur\n")
    with pytest.raises(f4.Refus) as e:
        f4.poser_mcp(table.PAR_ID["dsh"], m)
    assert e.value.args[0] == "config_illisible"
    c.write_text("- insert:\n    - id: qaf\n      name: autre\n")
    with pytest.raises(f4.Refus) as e:
        f4.poser_mcp(table.PAR_ID["dsh"], m)
    assert e.value.args[0] == "config_autre_qaf"


def test_preuve_json_non_vide(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["pi"]
    p = pathlib.Path(m, ".pi/agent/auth.json")
    p.parent.mkdir(parents=True)
    assert not f4.preuve(f, m)
    p.write_text("{}")  # pi le crée ainsi dès son chargement
    assert not f4.preuve(f, m)
    p.write_text("[1]")
    assert not f4.preuve(f, m)
    p.write_text('{"openai": {"key": "secret"}}')
    assert f4.preuve(f, m)


def test_preuve_sqlite_sans_lire_de_valeur(tmp_path):
    import sqlite3
    m = str(tmp_path)
    f = table.PAR_ID["omp"]
    p = pathlib.Path(m, ".omp/agent/agent.db")
    p.parent.mkdir(parents=True)
    assert not f4.preuve(f, m)
    p.write_text("pas une base")
    assert not f4.preuve(f, m)
    p.unlink()
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE autre (x)")
    c.commit()
    assert not f4.preuve(f, m)  # sans la table
    c.execute("CREATE TABLE auth_credentials (cle)")
    c.commit()
    assert not f4.preuve(f, m)  # table vide : base créée avant toute connexion
    c.execute("INSERT INTO auth_credentials VALUES ('secret')")
    c.commit()
    c.close()
    assert f4.preuve(f, m)


def test_kiro_lie_ses_binaires_voisins(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["kiro-cli"]
    d = pathlib.Path(m, "b")
    d.mkdir()
    for n in ("kiro-cli", "kiro-cli-chat", "kiro-cli-term"):
        (d / n).write_text("#!/bin/sh\n")
    f4._lier(f, m, str(d / "kiro-cli"))
    for n in ("kiro-cli", "kiro-cli-chat", "kiro-cli-term"):
        assert os.path.realpath(pathlib.Path(m, ".local/bin", n)) == os.path.realpath(d / n)
    (d / "kiro-cli-term").unlink()
    with pytest.raises(f4.Refus):
        f4._lier(f, m, str(d / "kiro-cli"))


def test_openclaw_avec_son_node_prive(tmp_path):
    """Node 24 épinglé, en tête du PATH à l'installation, à la connexion, au lancement."""
    m = str(tmp_path)
    f = table.PAR_ID["openclaw"]
    n = table.NODE
    assert re.fullmatch("[0-9a-f]{64}", n.sha256) and "v" + table.NODE_VERSION in n.url
    vus = []
    d = pathlib.Path(f4.dossier_node(m), "bin")
    d.mkdir(parents=True)
    (d / "node").write_text("")

    class R:
        returncode = 0

    f4.installer(f, m, None, lambda argv, **k: vus.append((argv, k)) or R, {"PATH": "/usr/bin"})
    assert vus[0][1]["env"]["PATH"].startswith(f"{d}:{m}/.local/bin:") and "openclaw@2026.10.9" in vus[0][0]
    b = pathlib.Path(m, ".local/bin/openclaw")
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("#!/bin/sh\n")
    f4.poser_mcp(f, m)
    assert f4.lancement("openclaw", m).env[0].startswith(f"PATH={d}:")
    assert assistant.Assistant("openclaw", m, {"PATH": "/usr/bin"}).env()["PATH"].startswith(f"{d}:")
    assert not assistant.Assistant("kimi", m, {"PATH": "/usr/bin"}).env()["PATH"].startswith(f"{d}:")


def test_outil_prive_refuse_une_empreinte_fausse(tmp_path):
    with pytest.raises(f4.Refus):
        f4._poser_outil(table.Install("archive", url="https://x/y", sha256="0" * 64, bin_dans="bin/node"),
                        str(tmp_path / "o"), lambda req, timeout: Rep(b"pas ca"))


def test_lancements_des_nouvelles_cli(tmp_path):
    m = str(tmp_path)
    attendus = {"omp": ["--approval-mode", "always-ask-cached", "--", "x"],
                "commandcode": ["--permission-mode", "default", "--", "x"],
                "kilo": ["--prompt=x"],
                "openclaw": ["chat", "--message", " x"]}
    for id, fin in attendus.items():
        f = table.PAR_ID[id]
        b = pathlib.Path(m, ".local/bin", id)
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text("#!/bin/sh\n")
        f4.poser_mcp(f, m)
        assert f4.lancement(id, m, "x").argv[1:] == fin, id
    b = pathlib.Path(m, ".local/bin/crush")
    b.write_text("#!/bin/sh\n")
    f4.poser_mcp(table.PAR_ID["crush"], m)
    assert f4.lancement("crush", m, "x").argv[0] == f4.SH and "run" not in f4.lancement("crush", m, "x").argv
    assert f4.lancement("crush", m).argv[1:] == []


def test_installations_decidees():
    t = table.PAR_ID
    assert len(t["agy"].install.sha256) == 64 and t["agy"].install.url.startswith(
        "https://github.com/google-antigravity/antigravity-cli/releases/download/1.4.12/")
    assert len(t["kimchi"].install.sha256) == 64 and t["kimchi"].install.mode == "archive"
    assert "install.sh" not in t["kimchi"].install.url
    assert t["hermes"].install.mode == "script" and "--commit" in t["hermes"].install.args
    assert len(t["hermes"].install.args[t["hermes"].install.args.index("--commit") + 1]) == 40
    assert t["kimi"].install.node_min == (22, 19)
    for f in table.FOURNISSEURS:
        if f.famille == "cli" and f.id != "claude":
            assert f.install is not None and f.version, f.id
            assert f.binaires and all(b.startswith(("/usr/bin/", "~/")) for b in f.binaires), f.id


def test_chaque_cli_declare_qaf_par_le_lanceur():
    for f in table.FOURNISSEURS:
        if f.famille == "cli" and f.id != "claude":
            assert table.LANCEUR in json.dumps(f4.entree_mcp(f)), f.id


# --- fuite OAuth ----------------------------------------------------------------------

def reglages(m, id):
    return json.loads(pathlib.Path(f4.chemin_settings(m, id)).read_text())


def test_settings_0600_identifiant_explicite(tmp_path):
    m = tiers_pret(tmp_path)
    r = reglages(m, "openrouter")
    assert r["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api" and r["env"]["ANTHROPIC_API_KEY"] == ""
    assert r["apiKeyHelper"] == "cat " + f4.chemin_cle(m, "openrouter")
    assert CLE not in json.dumps(r)
    for p in (f4.chemin_settings(m, "openrouter"), f4.chemin_cle(m, "openrouter")):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(f4.dossier(m, "openrouter")).st_mode) == 0o700


def test_serveur_local_sans_cle_toujours_un_jeton_de_remplissage(tmp_path):
    m = preparer_maison(tmp_path)
    f4.generer_settings(m, "local", "http://127.0.0.1:11434/v1/", "qwen3:4b")
    r = reglages(m, "local")
    assert r["env"]["ANTHROPIC_AUTH_TOKEN"] == f4.JETON_REMPLISSAGE and r["env"]["ANTHROPIC_API_KEY"] == ""
    assert r["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    assert r["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1" and r["env"]["ENABLE_TOOL_SEARCH"] == "false"
    assert r["env"]["ANTHROPIC_MODEL"] == "qwen3:4b" and r["skipWebFetchPreflight"] is True
    assert f4.reglages_valides("local", m)


def test_cle_exigee_hors_local(tmp_path):
    with pytest.raises(f4.Refus):
        f4.generer_settings(str(tmp_path), "openrouter", "https://openrouter.ai/api", "x")


@pytest.mark.parametrize("casser", ["sans_identifiant", "jeton_vide", "cle_absente", "groupe", "dossier_non_approuve",
                                    "api_key_pleine", "sans_base", "lien"])
def test_aucun_lancement_tiers_sans_identifiant_explicite(tmp_path, casser):
    m = tiers_pret(tmp_path)
    s = pathlib.Path(f4.chemin_settings(m, "openrouter"))
    r = json.loads(s.read_text())
    if casser == "sans_identifiant":
        del r["apiKeyHelper"]
    elif casser == "jeton_vide":
        del r["apiKeyHelper"]
        r["env"]["ANTHROPIC_AUTH_TOKEN"] = ""
    elif casser == "api_key_pleine":
        r["env"]["ANTHROPIC_API_KEY"] = "x"
    elif casser == "sans_base":
        del r["env"]["ANTHROPIC_BASE_URL"]
    s.write_text(json.dumps(r))
    if casser == "cle_absente":
        os.unlink(f4.chemin_cle(m, "openrouter"))
    elif casser == "groupe":
        s.chmod(0o640)
    elif casser == "dossier_non_approuve":
        (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"qaf": {}}}))
    elif casser == "lien":
        vrai = tmp_path / "ailleurs.json"
        vrai.write_text(s.read_text())
        vrai.chmod(0o600)
        s.unlink()
        s.symlink_to(vrai)
    f4.ecrire_actif(m, "openrouter")
    assert not f4.reglages_valides("openrouter", m)
    with pytest.raises(f4.Refus):
        f4.lancement("openrouter", m, "x")
    # L'agent repart sur Claude, chemin d'origine, sans base URL tierce.
    assert f4.actif_effectif(m) == "claude"
    lances = []
    assert demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                        fermer=lambda f: None) == 0
    argv = lances[0][0]
    assert "--settings" not in argv and argv[-3:] == [demande.CLAUDE, "--", "liste mes qubes"]


def test_jeton_claude_jamais_chez_un_tiers(tmp_path):
    m = tiers_pret(tmp_path)
    (tmp_path / ".config/qaf").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".config/qaf/jeton-claude").write_text(JETON_OAUTH + "\n")
    f4.ecrire_actif(m, "openrouter")
    lances = []
    env = env_dom0(m, ANTHROPIC_AUTH_TOKEN="x", ANTHROPIC_BASE_URL="http://x", CLAUDE_CODE_OAUTH_TOKEN="y")
    assert demande.main(entree(), env, lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                        fermer=lambda f: None) == 0
    argv, k = lances[0]
    assert argv[argv.index(demande.CLAUDE):argv.index(demande.CLAUDE) + 3] == [
        demande.CLAUDE, "--settings", f4.chemin_settings(m, "openrouter")]
    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert var not in k["env"]
    assert JETON_OAUTH not in repr(k) and JETON_OAUTH not in " ".join(argv)


def test_jamais_de_cle_dans_un_argv(tmp_path):
    m = tiers_pret(tmp_path)
    f4.ecrire_actif(m, "openrouter")
    lances = []
    demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                 fermer=lambda f: None)
    console.console(env_dom0(m), lanceur_note(lances), str(tmp_path / "r2"), str(tmp_path / "proc"))
    assert len(lances) == 2
    for argv, k in lances:
        assert CLE not in " ".join(argv) and CLE not in repr(k["env"])


# --- bascule -----------------------------------------------------------------------

def bascule(m, id, **k):
    out = io.StringIO()
    code = f4.bascule({"QREXEC_REMOTE_DOMAIN": k.get("appelant", "dom0"), "QREXEC_SERVICE_ARGUMENT": id, "HOME": m}, out)
    return code, out.getvalue()


def test_bascule_ok_une_ligne_atomique(tmp_path):
    m = tiers_pret(tmp_path)
    assert bascule(m, "openrouter") == (0, "ok\n")
    assert f4.lire_actif(m) == "openrouter" and f4.actif_effectif(m) == "openrouter"
    assert stat.S_IMODE(os.stat(os.path.join(f4.racine(m), "actif")).st_mode) == 0o600
    assert not [n for n in os.listdir(f4.racine(m)) if n.startswith(".qaf-")]
    assert bascule(m, "claude") == (0, "ok\n") and f4.lire_actif(m) == "claude"


@pytest.mark.parametrize("id", ["", "pirate", "../actif", "grokbot", "CLAUDE", "local\n"])
def test_id_inconnu_ou_non_activable_refuse(tmp_path, id):
    m = preparer_maison(tmp_path)
    assert bascule(m, id) == (1, "non\n")
    assert not os.path.exists(os.path.join(f4.racine(m), "actif"))


def test_appelant_autre_que_dom0_refuse(tmp_path):
    m = tiers_pret(tmp_path)
    for dom in ("work", "sys-agent", ""):
        assert bascule(m, "openrouter", appelant=dom) == (1, "non\n")
    assert f4.lire_actif(m) == "claude"


def test_sans_mcp_qaf_jamais_l_agent(tmp_path):
    m = cli_prete(tmp_path)
    assert bascule(m, "codex") == (0, "ok\n")
    f4.retirer_mcp(table.PAR_ID["codex"], m)
    assert bascule(m, "codex") == (1, "non\n") or f4.actif_effectif(m) == "claude"
    assert f4.actif_effectif(m) == "claude"
    (tmp_path / ".claude.json").write_text("{}")
    assert not f4.activable("openrouter", m)


def test_test_obligatoire_avant_activation(tmp_path):
    m = tiers_pret(tmp_path)
    os.unlink(os.path.join(f4.dossier(m, "openrouter"), "test.json"))
    assert bascule(m, "openrouter") == (1, "non\n")
    f4.noter_test(m, "openrouter", "https://openrouter.ai/api", "autre/modele")  # test d'un autre modèle
    assert bascule(m, "openrouter") == (1, "non\n")


def test_actif_illisible_vaut_claude(tmp_path):
    m = str(tmp_path)
    assert f4.lire_actif(m) == "claude"
    d = pathlib.Path(f4.racine(m))
    d.mkdir(parents=True)
    for brut in (b"pirate\n", b"\xff\xfe", b"x" * 500):
        (d / "actif").write_bytes(brut)
        assert f4.lire_actif(m) == "claude"


# --- lancement -----------------------------------------------------------------------

def test_formes_de_lancement(tmp_path):
    m = cli_prete(tmp_path, "codex")
    a = f4.lancement("codex", m, "-x fais ça").argv
    assert a[-2:] == ["--", "-x fais ça"] and "--sandbox" in a
    assert f4.lancement("codex", m, "t", suite=True).argv[-4:] == ["resume", "--last", "--", "t"]
    m = cli_prete(tmp_path, "copilot")
    a = f4.lancement("copilot", m, "--allow-all").argv
    assert a[-2:] == ["-i", " --allow-all"]  # l'espace : jamais pris pour une option
    m = cli_prete(tmp_path, "qwen")
    assert f4.lancement("qwen", m, "--yolo").argv[-1] == "--prompt-interactive=--yolo"
    m = cli_prete(tmp_path, "kimi")
    a = f4.lancement("kimi", m, "ma demande").argv
    assert a[:2] == [f4.SH, "-c"] and a[4] == "ma demande" and "-p" not in a
    assert a[-1].endswith("/kimi")
    assert f4.lancement("kimi", m).argv[-1].endswith("/kimi")  # console : aucun texte


def test_chemins_absolus_et_path(tmp_path):
    m = cli_prete(tmp_path, "grok")
    l = f4.lancement("grok", m, "t", chemin="/usr/bin")
    assert l.argv[0] == os.path.join(m, ".local/bin/grok") and "--no-auto-update" in l.argv
    assert l.env[0] == f"PATH={m}/.local/bin:/usr/bin"


def test_grokbot_et_claude_ne_passent_pas_par_lancement(tmp_path):
    for id in ("grokbot", "claude", "pirate"):
        with pytest.raises(f4.Refus):
            f4.lancement(id, str(tmp_path))


def test_demande_cli_active_ecrit_la_demande_pour_le_lanceur(tmp_path):
    m = cli_prete(tmp_path)
    f4.ecrire_actif(m, "codex")
    lances = []
    assert demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                        fermer=lambda f: None) == 0
    argv, _ = lances[0]
    assert "QAF_REQUEST=R-004211" in argv and argv[-2:] == ["--", "liste mes qubes"]
    assert pathlib.Path(m, ".config/qaf/demande").read_text() == "R-004211\n"


def test_console_sans_demande_ni_valeur_litterale(tmp_path):
    m = cli_prete(tmp_path)
    f4.ecrire_actif(m, "codex")
    f4.ecrire_demande(m, "R-004211")
    lances = []
    assert console.console(env_dom0(m, QAF_REQUEST="R-1"), lanceur_note(lances), str(tmp_path / "r"),
                           str(tmp_path / "proc")) == 0
    argv, k = lances[0]
    assert not any("QAF_REQUEST" in a for a in argv) and "QAF_REQUEST" not in k["env"]
    assert not any("${" in a for a in argv)
    assert not pathlib.Path(m, ".config/qaf/demande").exists()
    assert argv[-1] == "workspace-write"  # pas de texte, pas de reprise au premier lancement


def test_connexion_les_23_ids_efface_le_resultat(tmp_path):
    m = str(tmp_path)
    for id in table.IDS:
        f4.noter_connexion(m, "echec")
        lances = []
        assert console.connexion(env_dom0(m, QREXEC_SERVICE_ARGUMENT=id), lanceur_note(lances)) == 0
        assert lances[0][0][-1] == id and f4.lire_connexion(m) == ""
        assert lances[0][0][0] == demande.TERMINAL


# --- Claude inchangé -------------------------------------------------------------------

def test_claude_actif_comportement_inchange(tmp_path):
    m = preparer_maison(tmp_path)
    lances = []
    assert demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                        fermer=lambda f: None) == 0
    assert lances[0][0] == demande.commande("R-004211", "liste mes qubes")
    assert not os.path.exists(f4.racine(m)) and not os.path.exists(os.path.join(m, ".config/qaf/demande"))
    assert not os.path.exists(os.path.join(m, ".claude/settings.json"))
    (tmp_path / ".claude/projects" / str(tmp_path / "agent").replace("/", "-").replace("_", "-")).mkdir(parents=True)
    next((tmp_path / ".claude/projects").iterdir()).joinpath("a.jsonl").write_text("{}\n")
    lances.clear()
    demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                 fermer=lambda f: None)
    assert lances[0][0][-4:] == [demande.CLAUDE, "--continue", "--", "liste mes qubes"]


def test_jetable_ignore_le_fournisseur_actif(tmp_path):
    m = tiers_pret(tmp_path)
    f4.ecrire_actif(m, "openrouter")
    lances = []
    assert demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=True, racine_verrou=str(tmp_path / "r")) == 0
    assert "--settings" not in lances[0][0] and not os.path.exists(os.path.join(f4.racine(m), "lancements.json"))


# --- conversation --------------------------------------------------------------------

def conv(tmp_path, m):
    d = tmp_path / ".claude/projects" / str(tmp_path / "agent").replace("/", "-").replace("_", "-")
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.jsonl").write_text("{}\n")


def lancer_demande(tmp_path, m):
    lances = []
    demande.main(entree(), env_dom0(m), lanceur_note(lances), jetable=False, racine_verrou=str(tmp_path / "r"),
                 fermer=lambda f: None)
    return lances[0][0]


def test_continue_seulement_dans_la_meme_famille(tmp_path):
    m = tiers_pret(tmp_path)
    conv(tmp_path, m)
    assert "--continue" in lancer_demande(tmp_path, m)  # Claude, comme avant
    f4.ecrire_actif(m, "openrouter")
    a = lancer_demande(tmp_path, m)
    assert "--settings" in a and "--continue" not in a  # nouvelle conversation
    assert list((tmp_path / ".claude/projects").iterdir())[0].joinpath("a.jsonl").exists()  # l'ancienne reste
    assert "--continue" in lancer_demande(tmp_path, m)  # même fournisseur : reprise
    f4.generer_settings(m, "openrouter", "https://openrouter.ai/api", "qwen/qwen3.5-coder", cle=True, essai=True)
    f4.noter_test(m, "openrouter", "https://openrouter.ai/api", "qwen/qwen3.5-coder")
    assert "--continue" not in lancer_demande(tmp_path, m)  # Anthropic -> essai : autre famille
    f4.ecrire_actif(m, "claude")
    a = lancer_demande(tmp_path, m)
    assert "--settings" not in a and "--continue" not in a  # retour : nouvelle conversation
    assert "--continue" in lancer_demande(tmp_path, m)


# --- MCP sans écraser l'utilisateur -------------------------------------------------------

@pytest.mark.parametrize("id", ["kimi", "muse", "copilot", "agy", "devin", "qwen", "kimchi", "cursor", "opencode"])
def test_mcp_json_preserve_l_utilisateur(tmp_path, id):
    f = table.PAR_ID[id]
    m = str(tmp_path)
    p = pathlib.Path(m, f.mcp.chemin)
    p.parent.mkdir(parents=True)
    avant = {"theme": "x", f.mcp.cles[0]: {"autre": {"command": "y"}}}
    p.write_text(json.dumps(avant))
    f4.poser_mcp(f, m)
    apres = json.loads(p.read_text())
    assert apres["theme"] == "x" and apres[f.mcp.cles[0]]["autre"] == {"command": "y"}
    assert f4.mcp_configure(f, m)
    f4.poser_mcp(f, m)  # idempotent
    f4.retirer_mcp(f, m)
    assert not f4.mcp_configure(f, m)
    apres = json.loads(p.read_text())
    assert apres["theme"] == "x" and apres[f.mcp.cles[0]].get("autre") == {"command": "y"}


@pytest.mark.parametrize("id", ["codex", "grok", "vibe"])
def test_mcp_toml_preserve_l_utilisateur(tmp_path, id):
    import tomllib
    f = table.PAR_ID[id]
    m = str(tmp_path)
    p = pathlib.Path(m, f.mcp.chemin)
    p.parent.mkdir(parents=True)
    texte = '# mon réglage\nmodel = "x"\n\n[tui]\ntheme = "y"\n'
    p.write_text(texte, encoding="utf-8")
    f4.poser_mcp(f, m)
    assert p.read_text(encoding="utf-8").startswith(texte)
    d = tomllib.loads(p.read_text(encoding="utf-8"))
    assert d["tui"]["theme"] == "y" and f4.mcp_configure(f, m)
    f4.poser_mcp(f, m)
    assert p.read_text(encoding="utf-8").count("qaf_mcp/lanceur.py") == 1
    f4.retirer_mcp(f, m)
    assert p.read_text(encoding="utf-8") == texte


def test_mcp_toml_parades_qaf_request(tmp_path):
    m = str(tmp_path)
    f4.poser_mcp(table.PAR_ID["codex"], m)
    f4.poser_mcp(table.PAR_ID["grok"], m)
    assert 'env_vars = ["QAF_REQUEST"]' in pathlib.Path(m, ".codex/config.toml").read_text()
    assert '"${QAF_REQUEST:-}"' in pathlib.Path(m, ".grok/config.toml").read_text()


@pytest.mark.parametrize("avant", ["", "model: x\n", "mcp_servers:\n    autre:\n        command: y\nmodel: x\n"])
def test_mcp_yaml_hermes(tmp_path, avant):
    f = table.PAR_ID["hermes"]
    m = str(tmp_path)
    p = pathlib.Path(m, ".hermes/config.yaml")
    p.parent.mkdir()
    p.write_text(avant)
    f4.poser_mcp(f, m)
    t = p.read_text()
    assert f4.mcp_configure(f, m) and "${QAF_REQUEST}" in t and table.LANCEUR in t
    if "autre" in avant:
        assert "    qaf:" in t and "        command: y" in t
    f4.poser_mcp(f, m)
    assert t == p.read_text()
    f4.retirer_mcp(f, m)
    assert p.read_text() == avant


def test_mcp_fichier_illisible_ou_autre_qaf_jamais_ecrase(tmp_path):
    m = str(tmp_path)
    p = pathlib.Path(m, ".kimi-code/mcp.json")
    p.parent.mkdir()
    p.write_text("{pas du json")
    with pytest.raises(f4.Refus):
        f4.poser_mcp(table.PAR_ID["kimi"], m)
    assert p.read_text() == "{pas du json"
    p.write_text(json.dumps({"mcpServers": {"qaf": {"command": "le-mien"}}}))
    with pytest.raises(f4.Refus):
        f4.poser_mcp(table.PAR_ID["kimi"], m)
    assert "le-mien" in p.read_text()
    assert not f4.mcp_configure(table.PAR_ID["kimi"], m)
    t = pathlib.Path(m, ".codex/config.toml")
    t.parent.mkdir()
    t.write_text("[[[pas du toml")
    with pytest.raises(f4.Refus):
        f4.poser_mcp(table.PAR_ID["codex"], m)
    assert t.read_text() == "[[[pas du toml"


def test_retrait_laisse_tout_propre(tmp_path):
    m = tiers_pret(tmp_path)
    f4.ecrire_actif(m, "openrouter")
    f4.retirer("openrouter", m)
    assert not os.path.exists(f4.dossier(m, "openrouter")) and f4.lire_actif(m) == "claude"
    m = cli_prete(tmp_path)
    (pathlib.Path(m, ".claude.json")).write_text("{}")
    f4.retirer_tout(m)
    assert not os.path.exists(f4.racine(m)) and not f4.mcp_configure(table.PAR_ID["codex"], m)


def test_liste_d_autorisation_ajoutee_et_retiree(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["devin"]
    p = pathlib.Path(m, ".config/devin/config.json")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    f4.poser_mcp(f, m)
    assert json.loads(p.read_text())["permissions"]["allow"] == ["Read", "mcp__qaf__*"]
    f4.retirer_mcp(f, m)
    assert json.loads(p.read_text())["permissions"]["allow"] == ["Read"]


# --- lanceur --------------------------------------------------------------------------

def test_lanceur_format_egal_au_schema():
    assert lanceur.FORMAT.pattern == _REQUEST_RE.pattern


def test_lanceur_variable_valide_gardee(tmp_path):
    env = {"QAF_REQUEST": "R-1234", "HOME": str(tmp_path)}
    lanceur.preparer(env)
    assert env["QAF_REQUEST"] == "R-1234"


@pytest.mark.parametrize("valeur", ["${QAF_REQUEST}", "${env:QAF_REQUEST}", ""])
def test_lanceur_litteral_non_resolu_retire(tmp_path, valeur):
    env = {"QAF_REQUEST": valeur, "HOME": str(tmp_path)}
    lanceur.preparer(env)
    assert "QAF_REQUEST" not in env
    f4.ecrire_demande(str(tmp_path), "R-004211")
    env = {"QAF_REQUEST": valeur, "HOME": str(tmp_path)}
    lanceur.preparer(env)
    assert env["QAF_REQUEST"] == "R-004211"


def test_lanceur_fichier_mal_forme_ignore(tmp_path):
    f4.ecrire_demande(str(tmp_path), "pirate")
    env = {"HOME": str(tmp_path)}
    lanceur.preparer(env)
    assert "QAF_REQUEST" not in env


# --- qaf.Usage v=2 ---------------------------------------------------------------------

class Rep(io.BytesIO):
    def __init__(self, corps=b"{}", status=200):
        super().__init__(corps)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_usage_v2_tiers_actif(tmp_path):
    m = tiers_pret(tmp_path)
    f4.ecrire_actif(m, "openrouter")
    f4.noter_connexion(m, "ok")
    vus = []

    def ouvrir(req, timeout):
        vus.append((req.full_url, timeout))
        return Rep(json.dumps({"data": {"limit_remaining": 3.5}}).encode())
    s = usage_sys.mesurer(m, time.time(), ouvrir)
    assert s["v"] == 2 and s["actif"] == "openrouter" and s["sante"] == "ok" and s["connexion"] == "ok"
    assert s["agents"] == ["claude", "openrouter"] and s["fournisseurs"]["openrouter"] == "connecte"
    assert ("https://openrouter.ai/api/v1/key", 2) in vus


def test_usage_sortie_bornee_avec_35_et_sans_chaine_libre(tmp_path):
    m = str(tmp_path)
    s = usage_sys.mesurer(m, time.time(), existe=lambda p: False)
    assert len(s["fournisseurs"]) == 34 and "claude" not in s["fournisseurs"]
    assert set(s["fournisseurs"].values()) <= set(f4.ETATS) and s["sante"] in f4.SANTES
    # pire cas : 34 états les plus longs, 35 agents, totaux pleins, 4 fenêtres
    s["fournisseurs"] = {i: "connexion_seule" for i in table.IDS_HORS_CLAUDE}
    s["agents"] = list(table.IDS)
    s["actif"] = "openrouter"
    s["fenetres"] = [{"nom": "semaine", "modele": "sonnet", "pourcent": 100, "remise": 2 ** 31}] * 4
    s["totaux"] = {"jetons_jour": 10 ** 12, "jetons_semaine": 10 ** 12, "invites_jour": 100000, "sessions_jour": 100000,
                   "modele": "sonnet", "jour_actif": 6, "partiel": True}
    texte = usage_sys.ligne(s)
    assert len(texte.encode()) <= 4096 and json.loads(texte)["v"] == 2 and "fournisseurs" in json.loads(texte)
    assert len(texte.encode()) < 2048  # marge : le plafond de dom0 reste loin


def test_dom0_accepte_v2(tmp_path):
    if tuple(getattr(dom0, "FOURNISSEURS", ())) == ("codex", "grok"):
        pytest.skip("qaf_ui/ pas encore au contrat v=2 (lot voisin)")
    m = tiers_pret(tmp_path)
    f4.ecrire_actif(m, "openrouter")
    s = usage_sys.mesurer(m, time.time(), lambda r, timeout: Rep(b'{"data":{}}'))
    assert dom0.lire_mesure(usage_sys.ligne(s).encode()) is not None


def test_usage_ne_rend_ni_modele_ni_adresse_ni_cle(tmp_path):
    m = tiers_pret(tmp_path, "local", "modele-tres-prive:1b")
    f4.ecrire_actif(m, "local")
    t = usage_sys.ligne(usage_sys.mesurer(m, time.time(), lambda r, timeout: Rep()))
    for mot in ("modele-tres-prive", "10.0.0", CLE):
        assert mot not in t


@pytest.mark.parametrize("code,attendu", [(401, "cle_refusee"), (403, "cle_refusee"), (402, "credit_epuise"),
                                          (500, "inconnue"), (200, "credit_epuise")])
def test_sante_openrouter(tmp_path, code, attendu):
    import urllib.error
    m = tiers_pret(tmp_path)

    def ouvrir(req, timeout):
        assert timeout <= 2
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
        return Rep(b'{"data":{"limit_remaining":0}}')
    assert f4.sante("openrouter", m, ouvrir) == attendu


def test_sante_bornee_meme_si_le_serveur_ne_rend_jamais_la_main(tmp_path, monkeypatch):
    m = tiers_pret(tmp_path)
    monkeypatch.setattr(f4, "DELAI_SANTE", 0.1)
    arret = threading.Event()
    debut = time.monotonic()

    def jamais(req, timeout):
        arret.wait(10)
        raise OSError
    assert f4.sante("openrouter", m, jamais) == "injoignable"
    arret.set()
    assert time.monotonic() - debut < 2


def test_sante_injoignable_et_cli(tmp_path):
    import urllib.error
    m = tiers_pret(tmp_path, "deepseek", "deepseek-flash")

    def ouvrir(req, timeout):
        assert req.full_url == "https://api.deepseek.com/anthropic/v1/models"
        raise urllib.error.URLError("x")
    assert f4.sante("deepseek", m, ouvrir) == "injoignable"
    assert f4.sante("codex", m, ouvrir) == "inconnue" and f4.sante("claude", m, ouvrir) == "inconnue"


def test_sante_lieu_autre_que_le_reseau_jamais_sonde(tmp_path):
    m = preparer_maison(tmp_path)
    f4.generer_settings(m, "local", "http://127.0.0.1:11434", "x", lieu="voisin")
    assert f4.sante("local", m, lambda *a, **k: 1 / 0) == "inconnue"


def test_totaux_sans_les_tours_d_un_tiers(tmp_path):
    from datetime import datetime
    m = str(tmp_path)
    d = tmp_path / ".claude/projects/-home-user-agent"
    d.mkdir(parents=True)
    t = int(datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp())

    def ligne(heure, i):
        return json.dumps({"type": "assistant", "timestamp": heure, "requestId": f"r{i}",
                           "message": {"id": f"m{i}", "model": "claude-opus-4", "usage": {"input_tokens": 100}}})
    (d / "a.jsonl").write_text("\n".join(ligne(t + k, k) for k in (0, 200, 400)) + "\n")
    f4.noter_lancement(m, "claude", t)  # rien d'écrit : Claude seul
    assert usage_sys.totaux(m, t + 1000)["jetons_jour"] == 300
    pathlib.Path(f4.racine(m)).mkdir(parents=True)
    pathlib.Path(f4.racine(m), "lancements.json").write_text(json.dumps([[t + 100, "openrouter"], [t + 300, "claude"]]))
    assert usage_sys.totaux(m, t + 1000)["jetons_jour"] == 200


# --- assistant, faux serveurs -----------------------------------------------------------

class Faux(http.server.BaseHTTPRequestHandler):
    mode = "anthropic"
    vus = []

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        Faux.vus.append((self.path, dict(self.headers)))
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": "qwen3:4b"}, {"id": "\x1b[31mevil"}]})
        return self._json(404, {})

    def do_POST(self):
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Faux.vus.append((self.path, dict(self.headers), corps))
        if self.mode == "openai" or self.path != "/v1/messages":
            return self._json(404, {"error": {"message": "x"}})
        if self.headers.get("x-api-key") == "mauvaise":
            return self._json(401, {"type": "error"})
        if not corps.get("stream"):
            return self._json(200, {"type": "message", "content": []})
        evts = [{"type": "message_start", "message": {}}]
        if self.mode == "anthropic":
            evts.append({"type": "content_block_start", "index": 0,
                         "content_block": {"type": "tool_use", "id": "t", "name": "qaf_sonde", "input": {}}})
        else:
            evts.append({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        evts.append({"type": "message_stop"})
        b = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in evts).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def serveur():
    Faux.mode, Faux.vus = "anthropic", []
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Faux)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def faire(id, m, reponses, cles=(), env=None, ouvrir=None, executer=None):
    sortie, rep, sec = [], iter(reponses), iter(cles)
    a = assistant.Assistant(id, m, env or {"HOME": m, "PATH": "/usr/bin", "LANG": "fr_FR.UTF-8"},
                            entree=lambda p="": (sortie.append(p), next(rep))[1],
                            secret=lambda p="": (sortie.append(p), next(sec))[1],
                            ecrire=lambda t="": sortie.append(t), ouvrir=ouvrir,
                            **({"executer": executer} if executer else {}))
    return a.executer_tout(), "\n".join(sortie)


def test_assistant_local_sans_cle_serveur_anthropic(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, [serveur + "/v1/", "1"], [""])
    assert ok and "[1/5]" in t and "[5/5]" in t and "jeton fictif" in t and "Rien n'a changé" in t
    assert "Ton serveur verra tout" in t and "\x1b" not in t
    assert reglages(m, "local")["env"]["ANTHROPIC_MODEL"] == "qwen3:4b"
    assert f4.activable("local", m) and f4.lire_actif(m) == "claude"  # rien ne s'active ici
    envoi = next(v for v in Faux.vus if len(v) == 3 and v[2].get("stream"))
    assert envoi[2]["tools"][0]["name"] == "qaf_sonde" and envoi[1]["Authorization"] == f"Bearer {f4.JETON_REMPLISSAGE}"
    assert all(JETON_OAUTH not in repr(v) for v in Faux.vus)


def test_assistant_serveur_openai_seul_message_clair(tmp_path, serveur):
    Faux.mode = "openai"
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, [serveur, "1", "q"], [""])
    assert not ok and "ne parle que le protocole OpenAI" in t and "non enregistrée" in t
    assert not os.path.exists(f4.dossier(m, "local"))


def test_assistant_modele_sans_outils_refuse(tmp_path, serveur):
    Faux.mode = "sans_outil"
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, [serveur, "1", "q"], [""])
    assert not ok and "n'appelle pas d'outils" in t and not f4.activable("local", m)


def test_assistant_serveur_muet(tmp_path):
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, ["http://127.0.0.1:9/", "qwen3:4b", "q"], [""])
    assert not ok and "Pas de réponse" in t


def test_assistant_adresse_en_clair_demande_accord(tmp_path):
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, ["http://10.0.0.20:8080", "n", "q"])
    assert not ok and "En clair" in t


def test_assistant_adresse_invalide(tmp_path):
    m = preparer_maison(tmp_path)
    for a in ("file:///etc/passwd", "http://x y/", "http://u:p@h:1", "http://h:99999"):
        with pytest.raises(f4.Refus):
            f4.nettoyer_url(a)
    ok, t = faire("local", m, ["pas une adresse", "q"])
    assert not ok and "Adresse non valable" in t


def ouvrir_openrouter(serveur, cle_ok=True):
    """OpenRouter redirigé vers le faux serveur, sans réseau."""
    import urllib.error
    import urllib.request

    def ouvrir(req, timeout):
        u = req.full_url
        if u.endswith("/api/v1/key"):
            if not cle_ok:
                raise urllib.error.HTTPError(u, 401, "x", {}, io.BytesIO(b""))
            return Rep(b'{"data":{}}')
        if u.endswith("/api/v1/models"):
            return Rep(json.dumps({"data": [
                {"id": "~anthropic/claude-sonnet-latest", "supported_parameters": ["tools"], "context_length": 200000},
                {"id": "qwen/qwen3.5-coder", "supported_parameters": ["tools"]},
                {"id": "sans/outils", "supported_parameters": []}]}).encode())
        r = urllib.request.Request(u.replace("https://openrouter.ai/api", serveur), data=req.data,
                                   headers=dict(req.header_items()))
        return urllib.request.urlopen(r, timeout=timeout)
    return ouvrir


def test_assistant_openrouter_parcours_complet(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    ok, t = faire("openrouter", m, ["2"], [CLE], ouvrir=ouvrir_openrouter(serveur))
    assert ok, t
    assert "plafond" in t and "lire" in t  # la clé reste lisible par l'agent
    assert "Clé reçue" in t and "Ce fournisseur verra tout" in t and "pris en charge" in t and "essai" in t
    assert "sans/outils" not in t and CLE not in t
    assert f4.lire_cle(m, "openrouter") == CLE and f4.activable("openrouter", m)
    assert stat.S_IMODE(os.stat(f4.chemin_cle(m, "openrouter")).st_mode) == 0o600


def test_assistant_modele_a_l_essai_marque(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    ok, _ = faire("openrouter", m, ["3"], [CLE], ouvrir=ouvrir_openrouter(serveur))
    assert ok and f4.config(m, "openrouter")["essai"] is True
    assert f4.famille_conversation("openrouter", m) == "openrouter:essai"


def test_assistant_cle_refusee_rien_enregistre(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    ok, t = faire("openrouter", m, ["q"], [CLE], ouvrir=ouvrir_openrouter(serveur, cle_ok=False))
    assert not ok and "Clé refusée par OpenRouter" in t and not os.path.exists(f4.dossier(m, "openrouter"))


def test_assistant_menu_deja_configure_retrait(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    faire("local", m, [serveur, "1"], [""])
    ok, t = faire("local", m, ["3"])
    assert ok is None and "déjà configuré" in t and "retiré" in t and not os.path.exists(f4.dossier(m, "local"))


def test_assistant_menu_refaire_le_test(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    faire("local", m, [serveur, "1"], [""])
    ok, t = faire("local", m, ["2"])
    assert ok and "Appel d'outil" in t


def test_assistant_trois_langues_memes_champs():
    import re
    for cle, v in TEXTES_ITEMS():
        assert len(v) == 3, cle
        champs = [sorted(re.findall(r"\{(\w+)\}", x)) for x in v]
        assert champs[0] == champs[1] == champs[2], cle


def TEXTES_ITEMS():
    return assistant.TEXTES.items()


@pytest.mark.parametrize("env,attendu", [({"LANG": "fr_FR.UTF-8"}, "fr"), ({"LANG": "en_US.UTF-8"}, "en"),
                                         ({"LC_ALL": "zh_CN.UTF-8"}, "zh"), ({"LANG": "C"}, "fr"), ({}, "fr"),
                                         ({"LANGUAGE": "en:fr", "LANG": "fr_FR"}, "en"),
                                         ({"LANGUAGE": "en", "LC_ALL": "C"}, "fr")])
def test_langue_de_l_assistant(env, attendu):
    assert assistant.langue(env) == attendu


def test_assistant_en_anglais(tmp_path, serveur):
    m = preparer_maison(tmp_path)
    ok, t = faire("local", m, [serveur, "1"], [""], env={"HOME": m, "LANG": "en_GB.UTF-8"})
    assert ok and "Your key stays stored" in t and "Test passed" in t


class Exe:
    def __init__(self, faire=None, code=0):
        self.argv, self.faire, self.code = [], faire, code

    def __call__(self, argv, **k):
        self.argv.append(argv)
        self.env = k.get("env")
        if self.faire:
            self.faire(argv)

        class R:
            returncode = self.code
            stdout = b"v22.23.4\n"
        return R


def test_assistant_cli_installe_connecte_declare(tmp_path):
    m = preparer_maison(tmp_path)

    def effet(argv):
        if argv[:3] == [*f4.SANS_PRIVILEGE, "npm"]:
            b = pathlib.Path(m, ".local/bin/kimi")
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_text("x")
        if argv[-1] == "login":
            p = pathlib.Path(m, ".kimi-code/credentials/kimi-code.json")
            p.parent.mkdir(parents=True)
            p.write_text("{}")
    exe = Exe(effet)
    ok, t = faire("kimi", m, ["o"], executer=exe)
    assert ok and "[1/5]" in t and "@moonshot-ai/kimi-code@2.4.1" in t and "[5/5]" in t, t
    assert exe.argv[0] == ["node", "--version"]
    assert exe.argv[1] == [*f4.SANS_PRIVILEGE, "npm", "install", "-g", "--prefix", m + "/.local", "--no-fund",
                           "--no-audit", "--before=" + table.GEL_DEPENDANCES, "@moonshot-ai/kimi-code@2.4.1"]
    assert f4.activable("kimi", m) and "sudo" not in repr(exe.argv)


def test_assistant_cli_annonce_avant_telechargement_refus(tmp_path):
    m = preparer_maison(tmp_path)
    exe = Exe()
    ok, t = faire("devin", m, ["n"], executer=exe, ouvrir=lambda *a, **k: 1 / 0)
    assert not ok and "static.devin.ai" in t and exe.argv == []


def test_assistant_cli_sans_preuve_jamais_activable(tmp_path):
    m = cli_prete(tmp_path, "qwen")
    os.unlink(pathlib.Path(m, ".qwen/settings.json"))
    ok, t = faire("qwen", m, [], executer=Exe())
    assert not ok and "/auth" in t and "non vue" in t and not f4.activable("qwen", m)


def test_assistant_menu_cli_retrait(tmp_path):
    m = cli_prete(tmp_path, "devin")
    ok, t = faire("devin", m, ["2"], executer=Exe())
    assert ok is None and "retiré" in t and not f4.mcp_configure(table.PAR_ID["devin"], m)


def test_retrait_et_quitter_ne_sont_pas_un_resultat(tmp_path):
    """Aucun test n'a tourné : la carte ne doit pas dire « prêt » ni « test réussi »."""
    m = cli_prete(tmp_path, "devin")
    env = {"HOME": m, "LANG": "fr_FR"}
    for choix in ("3", "2"):
        f4.noter_connexion(m, "ok")
        rep = iter([choix, ""])
        assert assistant.main(["devin"], env, lambda p="": next(rep)) == 0
        assert f4.lire_connexion(m) == ""
    (tmp_path / "t").mkdir()
    m = tiers_pret(tmp_path / "t")
    env = {"HOME": m, "LANG": "fr_FR"}
    for choix in ("4", "3"):
        f4.noter_connexion(m, "ok")
        rep = iter([choix, ""])
        assert assistant.main(["openrouter"], env, lambda p="": next(rep)) == 0
        assert f4.lire_connexion(m) == ""


def test_retrait_possible_apres_deconnexion(tmp_path):
    m = cli_prete(tmp_path, "devin")
    os.unlink(pathlib.Path(m, table.PAR_ID["devin"].preuves[0].chemin))
    assert not f4.activable("devin", m)
    ok, t = faire("devin", m, ["2"], executer=Exe())
    assert ok is None and "retiré" in t and not f4.mcp_configure(table.PAR_ID["devin"], m)


def test_assistant_grokbot_jamais_d_activation(tmp_path):
    m = preparer_maison(tmp_path)
    ok, t = faire("grokbot", m, [], executer=Exe())
    assert not ok and "jamais" in t and "Cursor" in t
    assert not f4.activable("grokbot", m) and f4.etat("grokbot", m) == "connexion_seule"


def test_assistant_claude_chemin_d_origine(tmp_path):
    m = preparer_maison(tmp_path)
    exe = Exe()
    ok, _ = faire("claude", m, [], executer=exe)
    assert ok and exe.argv == [[demande.CLAUDE, "auth", "login"]]


def test_assistant_main_note_le_resultat(tmp_path):
    m = str(tmp_path)
    env = {"HOME": m, "LANG": "fr_FR"}
    assert assistant.main(["pirate"], env) == 2
    rep = iter(["n", ""])
    assert assistant.main(["devin"], env, lambda p="": next(rep)) == 1
    assert f4.lire_connexion(m) == "echec"


# --- installation -------------------------------------------------------------------

class Tel(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def archive(nom="bin/kimchi", tete=""):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        donnees = b"#!/bin/sh\n"
        i = tarfile.TarInfo(tete + nom)
        i.size = len(donnees)
        t.addfile(i, io.BytesIO(donnees))
    return b.getvalue()


def table_avec(monkeypatch, id, **k):
    import dataclasses
    f = table.PAR_ID[id]
    return dataclasses.replace(f, install=dataclasses.replace(f.install, **k))


def test_archive_verifiee_puis_liee(tmp_path, monkeypatch):
    a = archive()
    f = table_avec(monkeypatch, "kimchi", sha256=hashlib.sha256(a).hexdigest())
    f4.installer(f, str(tmp_path), lambda r, timeout: Tel(a), Exe(), {"PATH": "/usr/bin"})
    b = tmp_path / ".local/bin/kimchi"
    assert b.is_symlink() and os.access(b, os.X_OK) and f4.binaire(f, str(tmp_path))


def test_empreinte_differente_rien_installe(tmp_path, monkeypatch):
    f = table.PAR_ID["kimchi"]
    with pytest.raises(f4.Refus) as e:
        f4.installer(f, str(tmp_path), lambda r, timeout: Tel(archive()), Exe(), {"PATH": "/usr/bin"})
    assert e.value.args[0] == "empreinte_differente" and f4.binaire(f, str(tmp_path)) is None


def test_archive_hostile_ne_sort_pas(tmp_path, monkeypatch):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        i = tarfile.TarInfo("../../evade")
        i.size = 0
        t.addfile(i, io.BytesIO())
    f = table_avec(monkeypatch, "kimchi", sha256=hashlib.sha256(b.getvalue()).hexdigest())
    with pytest.raises(f4.Refus):
        f4.installer(f, str(tmp_path / "m"), lambda r, timeout: Tel(b.getvalue()), Exe(), {"PATH": "/usr/bin"})
    assert not list(tmp_path.rglob("evade"))


def test_telechargement_https_seulement(tmp_path):
    with pytest.raises(f4.Refus):
        f4.telecharger("http://x/y", str(tmp_path / "f"), "0" * 64)


def test_redirection_hors_https_refusee():
    h = f4._HttpsSeul()
    req = urllib.request.Request("https://exemple.test/a")
    with pytest.raises(f4.Refus):
        h.redirect_request(req, None, 302, "", {}, "http://exemple.test/b")
    assert h.redirect_request(req, None, 302, "", {}, "https://exemple.test/b").full_url == "https://exemple.test/b"


def test_pas_d_empreinte_pas_de_telechargement(tmp_path):
    with pytest.raises(f4.Refus):
        f4.telecharger("https://x/y", str(tmp_path / "f"), "", lambda *a, **k: 1 / 0)
    assert not (tmp_path / "f").exists()


def test_tout_fichier_execute_a_son_empreinte():
    for f in table.FOURNISSEURS:
        if f.install is not None and f.install.mode in ("archive", "binaire", "script"):
            assert re.fullmatch("[0-9a-f]{64}", f.install.sha256), f.id
    assert "/bf0203a794a07aac3ff764972ab9087baa4e4f4e/" in table.PAR_ID["hermes"].install.url


def test_annonce_d_empreinte_avant_la_question(tmp_path):
    m = preparer_maison(tmp_path)
    _, t = faire("devin", m, ["n"], executer=Exe())
    assert t.index("Empreinte attendue") < t.index("Continuer")
    _, t = faire("kimi", m, ["n"], executer=Exe())
    assert t.index("Pas d'empreinte") < t.index("Continuer") and "dépendances gelées" in t


def test_installations_sans_privilege(tmp_path):
    exe = Exe()
    f4.installer(table.PAR_ID["codex"], str(tmp_path), None, exe, {"PATH": "/usr/bin"})
    assert exe.argv[-1][:2] == list(f4.SANS_PRIVILEGE) and "--before=" + table.GEL_DEPENDANCES in exe.argv[-1]


def test_setpriv_interdit_vraiment_de_gagner_des_droits():
    """La preuve par le noyau : no_new_privs est posé et hérité."""
    if not os.path.exists(f4.SANS_PRIVILEGE[0]):
        pytest.skip("setpriv absent")
    r = subprocess.run([*f4.SANS_PRIVILEGE, "/bin/sh", "-c", "grep NoNewPrivs /proc/self/status"],
                       capture_output=True, text=True)
    assert r.stdout.split() == ["NoNewPrivs:", "1"]


def test_kimchi_archive_reelle_et_preuve():
    k = table.PAR_ID["kimchi"]
    assert k.install.bin_dans == "bin/kimchi" and k.login == ("setup",)


def test_noms_de_la_table_egaux_a_la_carte():
    """L'assistant dit « carte de l'agent, ligne X » : X doit y être."""
    for i in table.IDS_HORS_CLAUDE:
        if i != "local":  # traduit par la carte
            assert table.PAR_ID[i].nom == dom0.NOMS[i], i


def test_le_test_envoie_l_id_de_claude_code():
    corps = json.loads(assistant._message("glm-5.3[1m]", True))
    assert corps["model"] == "glm-5.3"


def test_reponse_non_http_vaut_injoignable():
    import http.client

    def casse(*a, **k):
        raise http.client.BadStatusLine("SSH-2.0-OpenSSH")
    assert f4.appeler("http://127.0.0.1:1/v1/models", ouvrir=casse) == (0, b"")


# --- essais réels du 01/11 : chaque défaut vu a son test ------------------------------------

SEULES = ("ori", "mcode", "kiro-cli")


@pytest.mark.parametrize("id", SEULES)
def test_validation_absente_ou_non_vue_jamais_l_agent(tmp_path, id):
    """ori : write sans question, même hors dossier. mcode : rm hors dossier sans question.
    kiro-cli : ni validation ni serveur vus sans compte."""
    m = str(tmp_path)
    f = table.PAR_ID[id]
    assert f.famille == "connexion_seule" and f.mcp is None and f.note and id in dom0.JAMAIS_AGENT
    assert len(assistant.TEXTES[f.note]) == 3
    cli_posee(m, id)
    preuve_de(m, f)
    assert not f4.activable(id, m) and id not in f4.agents(m)
    with pytest.raises(f4.Refus):
        f4.lancement(id, m, "x")
    f4.ecrire_actif(m, id)
    assert f4.actif_effectif(m) == "claude"


def test_mcode_n_ecrit_plus_rien_dans_son_config_yaml(tmp_path):
    """Sa commande provider add écrivait dans notre bloc, et le retrait emportait tout."""
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "mcode")
    conf = pathlib.Path(m, ".minimax/config.yaml")
    conf.parent.mkdir()
    conf.write_text("defaultModel: a\n")
    preuve_de(m, f)
    assert faire("mcode", m, [], executer=Exe())[0] is True
    f4.retirer("mcode", m)
    assert conf.read_text() == "defaultModel: a\n" and not pathlib.Path(m, ".minimax/mcp.json").exists()


def test_kiro_coupe_la_mise_a_jour_avant_tout_lancement_et_la_preuve_sqlite_ne_suffit_pas(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "kiro-cli")
    p = pathlib.Path(m, f.preuves[0].chemin)
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE auth_kv (k TEXT)")
    c.execute("INSERT INTO auth_kv VALUES ('kirocli:auth:last-forced-refresh-at')")
    c.commit()
    c.close()
    assert f4.preuve(f, m)  # le faux positif vu : une fausse clé laisse cette ligne

    class Pas(Exe):
        def __call__(self, argv, **k):
            r = super().__call__(argv, **k)
            r.returncode = 1 if argv[1:] == ["whoami"] else 0
            return r

    exe = Pas()
    ok, t = faire("kiro-cli", m, [], executer=exe)
    assert ok is False and "non vue" in t and "prêt" not in t
    assert exe.argv[0][1:] == ["settings", "app.disableAutoupdates", "true"]
    exe = Exe(code=1)
    ok, t = faire("kiro-cli", m, [], executer=exe)
    assert ok is False and "n'a pas accepté" in t and len(exe.argv) == 1  # rien d'autre n'a été lancé


@pytest.mark.parametrize("id,fin", [("kilo", ["--prompt=x"]), ("commandcode", ["--permission-mode", "default", "--", "x"])])
def test_reprise_avec_texte_ne_perd_pas_la_demande(tmp_path, id, fin):
    """--continue avec un texte : la CLI n'envoie rien. Avec un texte, nouvelle conversation."""
    m = preparer_maison(tmp_path)
    f, b = cli_posee(m, id)
    f4.poser_mcp(f, m)
    assert f4.lancement(id, m, "x", suite=True).argv == [str(b), *fin]
    assert "--continue" in f4.lancement(id, m, None, suite=True).argv  # la console reprend toujours


def test_commandcode_exige_sa_commande_d_etat(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "commandcode")
    preuve_de(m, f)
    ok, t = faire("commandcode", m, [], executer=Exe(code=1))
    assert ok is False and "non vue" in t and not f4.mcp_declare(f, m)
    exe = Exe()
    assert faire("commandcode", m, [], executer=exe)[0] is True and exe.argv[-1][1:] == ["status"]


@pytest.mark.parametrize("nom", ["crushrc", ".crushrc"])
def test_crushrc_du_dossier_de_l_agent_interdit_le_lancement(tmp_path, nom):
    """Crush l'exécute sans question au démarrage (vu)."""
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "crush")
    f4.poser_mcp(f, m)
    assert f4.mcp_configure(f, m)
    rc = pathlib.Path(m, "agent", nom)
    rc.write_text("touch x\n")
    assert not f4.mcp_configure(f, m) and not f4.activable("crush", m)
    with pytest.raises(f4.Refus):
        f4.lancement("crush", m)
    f4.retirer("crush", m)
    with pytest.raises(f4.Refus) as e:
        f4.poser_mcp(f, m)
    assert e.value.args[0] == "fichier_execute" and "fichier_execute" in assistant.TEXTES


def test_preuve_de_dsh_le_fichier_nait_sans_cle(tmp_path):
    m = str(tmp_path)
    f = table.PAR_ID["dsh"]
    p = pathlib.Path(m, ".dsh/.credentials.yaml")
    p.parent.mkdir()
    p.write_text("version: 1\nrefs:\nrecords:\n  client-connection/browser-session:\n    kind: grant\n"
                 "    payload:\n      secret: abc\n")
    assert not f4.preuve(f, m)
    p.write_text("version: 1\nrecords:\n  llm/deepseek:\n    kind: api-key\n    key: sk-x\n")
    assert f4.preuve(f, m)
    p.write_text("version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-x\n")
    assert f4.preuve(f, m)


def test_ctrl_c_de_la_page_web_revient_dans_l_assistant(tmp_path):
    """Ctrl+C touche tout le groupe du terminal : l'assistant ne doit pas partir avec dsh."""
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "dsh")

    class Interrompu(Exe):
        def __call__(self, argv, **k):
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
            return super().__call__(argv, **k)

    ok, t = faire("dsh", m, [], executer=Interrompu())
    assert ok is False and "[3/5]" in t and "non vue" in t and not f4.mcp_declare(f, m)
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_le_gestionnaire_de_ctrl_c_ne_passe_pas_a_la_cli(tmp_path):
    """Un SIG_IGN serait hérité, et dsh n'entendrait plus Ctrl+C."""
    m = preparer_maison(tmp_path)
    a = assistant.Assistant("dsh", m, {"PATH": "/usr/bin:/bin"})
    argv = [sys.executable, "-c", "import signal,sys; sys.exit(signal.getsignal(signal.SIGINT) is signal.SIG_IGN)"]
    assert a.lancer_connexion(argv) == 0


def test_sortie_brutale_retire_la_declaration_posee_avant_la_connexion(tmp_path, monkeypatch):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "dsh")
    monkeypatch.setattr(f4, "noter_connexion", lambda *a: None)
    vue = {}

    def coupe(*a, **k):
        vue["pose"] = f4.mcp_declare(f, m)
        raise KeyboardInterrupt

    monkeypatch.setattr(assistant.Assistant, "lancer_connexion", coupe)
    assistant.main(["dsh"], {"HOME": m, "PATH": "/usr/bin"}, entree=lambda p="": "")
    assert vue["pose"] and not f4.mcp_declare(f, m)


def test_une_declaration_deja_confirmee_survit_a_une_sortie_brutale(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "dsh")
    f4.poser_mcp(f, m)
    a = assistant.Assistant("dsh", m, {"PATH": "/usr/bin"}, ecrire=lambda *x: None, executer=Exe())
    a.poser_tot()
    a.abandon()
    assert f4.mcp_declare(f, m)


def test_openclaw_refait_onboard_quand_sa_base_existe_sans_connexion(tmp_path):
    """Base présente, models status en échec : il ne faut pas dire « Déjà connecté »."""
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "openclaw")
    preuve_de(m, f)
    exe = Exe(code=1)
    ok, t = faire("openclaw", m, [], executer=exe)
    assert ok is False and "Déjà connecté" not in t
    assert [str(pathlib.Path(m, ".local/bin/openclaw")), *f.login] in exe.argv


def test_openclaw_coupe_sa_mise_a_jour_avant_onboard(tmp_path):
    m = preparer_maison(tmp_path)
    f, _ = cli_posee(m, "openclaw")
    c = pathlib.Path(m, ".openclaw/openclaw.json")
    vu = {}

    class Onboard(Exe):
        def __call__(self, argv, **k):
            if argv[1:] == list(f.login):
                vu["conf"] = json.loads(c.read_text())
                preuve_de(m, f)
            return super().__call__(argv, **k)

    assert faire("openclaw", m, [], executer=Onboard())[0] is True
    assert vu["conf"]["update"]["checkOnStart"] is False


@pytest.mark.parametrize("id,var,val", [("pi", "PI_SKIP_VERSION_CHECK", "1"), ("pi", "PI_TELEMETRY", "0"),
                                        ("amp", "AMP_SKIP_UPDATE_CHECK", "1")])
def test_terminal_de_connexion_coupe_mise_a_jour(tmp_path, id, var, val):
    m = preparer_maison(tmp_path)
    assert assistant.Assistant(id, m, {"PATH": "/usr/bin"}).env()[var] == val


def test_freebuff_dit_sa_telemetrie_avant_l_installation():
    for n, mot in enumerate(("statistiques", "statistics", "统计")):
        assert mot in assistant.TEXTES["note_freebuff"][n]


def test_page_web_previent_que_le_navigateur_reste_ouvert():
    for n, mot in enumerate(("reste ouvert", "stays open", "保持打开")):
        assert mot in assistant.TEXTES["page_web"][n]


def test_npm_tient_sur_un_lien_charge(tmp_path):
    exe = Exe()
    f4.installer(table.PAR_ID["dsh"], str(tmp_path), None, exe, {"PATH": "/usr/bin"})
    assert exe.env["npm_config_maxsockets"] == "3" and exe.env["npm_config_fetch_retries"] == "8"


class Coupe(io.BytesIO):
    """Réponse HTTP qui s'arrête en route."""

    def __init__(self, donnees, statut=200, annonce=None):
        super().__init__(donnees)
        self.status, self.headers = statut, {"Content-Length": str(annonce if annonce is not None else len(donnees))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_telechargement_repris_apres_une_coupure(tmp_path, monkeypatch):
    monkeypatch.setattr(f4, "PAUSE_REPRISE", 0)
    corps = os.urandom(3000)
    vus = []

    def ouvrir(req, timeout):
        vus.append(req.get_header("Range"))
        if len(vus) == 1:
            return Coupe(corps[:1000], annonce=3000)
        if len(vus) == 2:
            raise TimeoutError
        return Coupe(corps[1000:], 206)

    d = tmp_path / "f"
    f4.telecharger("https://x/y", str(d), hashlib.sha256(corps).hexdigest(), ouvrir)
    assert d.read_bytes() == corps and vus == [None, "bytes=1000-", "bytes=1000-"]


def test_serveur_sans_reprise_repart_de_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(f4, "PAUSE_REPRISE", 0)
    corps = os.urandom(3000)
    r = iter([Coupe(corps[:1000], annonce=3000), Coupe(corps)])
    d = tmp_path / "f"
    f4.telecharger("https://x/y", str(d), hashlib.sha256(corps).hexdigest(), lambda req, timeout: next(r))
    assert d.read_bytes() == corps


def test_partiel_refuse_par_le_serveur_repart_de_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(f4, "PAUSE_REPRISE", 0)
    corps = os.urandom(3000)
    etapes = []

    def ouvrir(req, timeout):
        etapes.append(req.get_header("Range"))
        if len(etapes) == 1:
            return Coupe(corps[:1000], annonce=3000)
        if len(etapes) == 2:
            raise urllib.error.HTTPError("https://x/y", 416, "non", {}, None)
        return Coupe(corps)

    d = tmp_path / "f"
    f4.telecharger("https://x/y", str(d), hashlib.sha256(corps).hexdigest(), ouvrir)
    assert d.read_bytes() == corps and etapes[-1] is None


def test_telechargement_abandonne_ne_laisse_rien(tmp_path, monkeypatch):
    monkeypatch.setattr(f4, "PAUSE_REPRISE", 0)
    vus = []

    def ouvrir(req, timeout):
        vus.append(1)
        return Coupe(b"x", annonce=10)

    d = tmp_path / "f"
    with pytest.raises(f4.Refus) as e:
        f4.telecharger("https://x/y", str(d), "0" * 64, ouvrir)
    assert e.value.args[0] == "installation_echec" and not d.exists() and len(vus) == f4.TENTATIVES


def test_erreur_inattendue_notee_et_terminal_garde(tmp_path, monkeypatch):
    m = str(tmp_path)
    monkeypatch.setattr(assistant.Assistant, "executer_tout", lambda self: 1 / 0)
    vus = []
    assert assistant.main(["devin"], {"HOME": m, "LANG": "fr_FR"}, lambda p="": vus.append(p) or "") == 1
    assert f4.lire_connexion(m) == "echec" and vus


def test_liste_de_modeles_d_un_type_inattendu(serveur):
    f = table.PAR_ID["deepseek"]

    def ouvrir(req, timeout):
        class R(Tel):
            status = 200
        return R(b'{"data": 3}')
    assert [m[0] for m in assistant.liste_modeles(f, "", "x", ouvrir)] == list(f.modeles_fixes)


def test_liste_openrouter_sans_en_tetes_anthropic(serveur):
    """Avec anthropic-version, OpenRouter rend le format Anthropic, sans supported_parameters :
    le tri par outils vidait la liste, seul le modèle fixe restait."""
    f = table.PAR_ID["openrouter"]
    vus = []

    def ouvrir(req, timeout):
        vus.append(req)

        class R(Tel):
            status = 200
        if req.has_header("Anthropic-version"):
            return R(json.dumps({"data": [{"id": "anthropic/claude-haiku-5.5", "type": "model"}]}).encode())
        return R(json.dumps({"data": [{"id": "anthropic/claude-haiku-5.5", "context_length": 1000000,
                                       "supported_parameters": ["tools"]}]}).encode())
    ids = [m[0] for m in assistant.liste_modeles(f, f.base_url, "sk-essai", ouvrir)]
    assert ids == [*f.modeles_fixes, "anthropic/claude-haiku-5.5"]
    assert len(vus) == 1 and not vus[0].has_header("Authorization") and not vus[0].has_header("X-api-key")


def test_poser_mcp_tout_ou_rien(tmp_path):
    m = cli_prete(tmp_path, "codex")
    f = table.PAR_ID["cursor"]
    p = pathlib.Path(m, ".cursor/cli-config.json")
    p.parent.mkdir(parents=True)
    p.write_text("pas du json")
    with pytest.raises(f4.Refus):
        f4.poser_mcp(f, m)
    assert not pathlib.Path(m, f.mcp.chemin).exists() and not f4.mcp_configure(f, m)
    p.write_text(json.dumps({"permissions": 3}))
    with pytest.raises(f4.Refus):
        f4.poser_mcp(f, m)
    assert not pathlib.Path(m, f.mcp.chemin).exists()


def test_opencode_sans_mise_a_jour_auto_sans_ecraser(tmp_path):
    m = preparer_maison(tmp_path)
    f = table.PAR_ID["opencode"]
    p = pathlib.Path(m, f.mcp.chemin)
    f4.poser_mcp(f, m)
    assert json.loads(p.read_text())["autoupdate"] is False and f4.mcp_configure(f, m)
    f4.retirer_mcp(f, m)
    assert "autoupdate" not in json.loads(p.read_text())
    p.write_text(json.dumps({"autoupdate": True}))
    f4.poser_mcp(f, m)
    assert json.loads(p.read_text())["autoupdate"] is True
    f4.retirer_mcp(f, m)
    assert json.loads(p.read_text())["autoupdate"] is True


def test_retirer_tout_garde_l_isolement_des_conversations(tmp_path):
    m = tiers_pret(tmp_path)
    f4.noter_lancement(m, "openrouter", 1000)
    assert not f4.reprise(m, "claude", True)
    f4.retirer_tout(m)
    assert not f4.reprise(m, "claude", True) and f4.tour_tiers(f4.periodes_tiers(m), 2000)
    assert os.listdir(f4.racine(m)) == ["lancements.json"] and f4.lire_actif(m) == "claude"
    assert stat.S_IMODE(os.stat(f4.racine(m)).st_mode) == 0o700


def test_dossiers_de_fournisseur_resserres(tmp_path):
    m = str(tmp_path)
    d = pathlib.Path(f4.racine(m))
    d.mkdir(parents=True, mode=0o755)
    f4.ecrire_actif(m, "claude")
    f4.ecrire_cle(m, "openrouter", CLE)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE(os.stat(f4.dossier(m, "openrouter")).st_mode) == 0o700


def test_niveaux_manquants_crees_en_0700(tmp_path):
    m = str(tmp_path)
    f4.ecrire_actif(m, "claude")
    assert stat.S_IMODE(os.stat(pathlib.Path(m, ".config/qaf")).st_mode) == 0o700


def test_approbations_limitees_a_qaf():
    for f in table.FOURNISSEURS:
        for _, _, v in f.mcp.allow if f.mcp else ():
            assert "qaf" in v, f.id


def test_node_trop_vieux(tmp_path):
    class R:
        returncode = 0
        stdout = b"v22.18.0\n"
    with pytest.raises(f4.Refus) as e:
        f4.installer(table.PAR_ID["kimi"], str(tmp_path), None, lambda *a, **k: R, {"PATH": "/usr/bin"})
    assert e.value.args[0] == "node_trop_vieux"


def test_hermes_script_fige_sans_sudo(tmp_path, monkeypatch):
    monkeypatch.setattr(f4, "libatomic_presente", lambda: True)
    vus = {}

    def exe(argv, env=None, **k):
        vus.update(argv=argv)

        class R:
            returncode = 0
        return R
    script = b"#!/bin/bash\n"
    f = table_avec(monkeypatch, "hermes", sha256=hashlib.sha256(script).hexdigest())
    f4.installer(f, str(tmp_path), lambda r, timeout: Tel(script), exe, {"PATH": "/usr/bin"})
    assert vus["argv"][:3] == [*f4.SANS_PRIVILEGE, "/bin/bash"]
    assert "--commit" in vus["argv"] and "--skip-setup" in vus["argv"]
    with pytest.raises(f4.Refus) as e:  # script changé : rien ne s'exécute
        vus.clear()
        f4.installer(table.PAR_ID["hermes"], str(tmp_path), lambda r, timeout: Tel(script), exe, {"PATH": "/usr/bin"})
    assert e.value.args[0] == "empreinte_differente" and not vus
    monkeypatch.setattr(f4, "libatomic_presente", lambda: False)
    with pytest.raises(f4.Refus) as e:
        f4.installer(table.PAR_ID["hermes"], str(tmp_path), None, exe, {"PATH": "/usr/bin"})
    assert e.value.args[0] == "libatomic_absente"


def test_uv_epingle_avant_vibe(tmp_path, monkeypatch):
    a = archive("uv", "uv-x86_64-unknown-linux-gnu/")
    monkeypatch.setattr(f4, "UV", type(table.UV)("archive", url=table.UV.url, sha256=hashlib.sha256(a).hexdigest(),
                                                 bin_dans="uv", retirer=1))
    exe = Exe()
    f4.installer(table.PAR_ID["vibe"], str(tmp_path), lambda r, timeout: Tel(a), exe, {"PATH": "/usr/bin"})
    assert exe.argv[0][:2] == list(f4.SANS_PRIVILEGE)
    assert exe.argv[0][3:] == ["tool", "install", "--exclude-newer", table.GEL_DEPENDANCES, "mistral-vibe==3.0.4"]
    assert exe.env["UV_TOOL_BIN_DIR"].endswith(".local/bin")


# --- livraison ----------------------------------------------------------------------

def test_service_livre_et_pose():
    s = (RACINE / "tools/setup-sys-agent.sh").read_text(encoding="utf-8")
    assert "qaf.Fournisseur" in s and (RACINE / "qaf_mcp/etc/qubes-rpc/qaf.Fournisseur").is_file()
    assert "rm -f /etc/skel/.mcp.json" in s
    p = (RACINE / "policy/30-qaf-usage.policy").read_text(encoding="utf-8")
    assert "qaf.Fournisseur *  @adminvm  sys-agent  allow" in p and "qaf.Fournisseur *  @anyvm    @anyvm     deny" in p
    assert "fournisseurs" not in (RACINE / "qaf_gate/schema.py").read_text(encoding="utf-8").split("CLIS =")[1][:40]


def test_table_sans_gtk_ni_dependance():
    src = (RACINE / "qaf_mcp/fournisseurs_table.py").read_text(encoding="utf-8")
    assert "import gi" not in src and "qaf_ui" not in src.replace("qaf_ui/usage.py", "")


def test_fichier_mcp_vide_de_la_cli_complete(tmp_path):
    """agy crée mcp_config.json vide à son premier lancement, avant la pose (vu le 31/10)."""
    m = str(tmp_path)
    f = table.PAR_ID["agy"]
    p = pathlib.Path(m, f.mcp.chemin)
    p.parent.mkdir(parents=True)
    p.write_text(" " * 3)
    f4.poser_mcp(f, m)
    assert f4.mcp_configure(f, m)


def test_muse_garde_son_schema(tmp_path):
    """Sans schema_version, muse refuse tout le fichier, et il faut qu'il reste au retrait."""
    m = str(tmp_path)
    f = table.PAR_ID["muse"]
    f4.poser_mcp(f, m)
    p = pathlib.Path(m, f.mcp.chemin)
    assert json.loads(p.read_text())["schema_version"] == 1 and f4.mcp_configure(f, m)
    f4.retirer_mcp(f, m)
    assert json.loads(p.read_text())["schema_version"] == 1 and not f4.mcp_configure(f, m)


def test_opencode_sans_service_d_arriere_plan():
    """Son service garderait QAF_REQUEST d'une ancienne demande (vu le 31/10)."""
    f = table.PAR_ID["opencode"]
    assert "--standalone" in f.lancer.args and "--standalone" in f.login and f.login_tui
