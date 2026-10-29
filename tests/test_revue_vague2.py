"""Revue du 25/10 après les essais réels des lots 2 et 3 (docs/RESULTATS-V2.md,
OPS-v3 §18) : non-régression de chaque correction.

1. echapper() : seules restent telles quelles les lettres dont la NFD est une
   lettre ASCII suivie de diacritiques combinants (homoglyphes échappés) ;
2. R-E : la valeur de jetable est jugée sur toute sa chaîne (qube, modèle,
   modèle du modèle), par une seule fonction (Contexte.jetable_risque) ;
3. tags des politiques : lecture récursive et fail-closed (inconnu => vm.tag N4) ;
4. template.install : N4 (qvm-template importe par sudo), message clair ;
5. fenêtre de backup.run : qube de destination et chemin nommés ;
6. qaf-gate-outils : plus d'écriture dans /run/qubes ;
7. bail sur un coffre : la fenêtre affiche la durée réelle (5 min).
"""

import dataclasses
import json
import os
import pathlib
import sys
import unicodedata

import pytest
from backend_sim import _q
from exemples_ops import op
from test_ops_v3_classement import (
    AGENT,
    CONFIG,
    classer,
    contexte,
    installation,
    niveau,
    plan,
)
from test_ops_v3_lot2_3 import faire, lancer

from qaf_gate import classify as C
from qaf_gate import confirm, executor, outils, proto, schema
from qaf_gate.validator import Config as ConfigValidateur
from qaf_gate.validator import Validateur
from qaf_ui import confirmation as ui

RACINE = pathlib.Path(__file__).resolve().parent.parent


# --- 1. homoglyphes -----------------------------------------------------------

@pytest.mark.parametrize("ch", list("ıĸſœæßøðþǀǁǂǃаеорсхΑαοϲἀ") + ["ǅ", "ﬁ", "Ꭺ", "ℓ", "𝐚"])
def test_homoglyphes_echappes(ch):
    assert ui.echapper(ch) == f"\\u{{{ord(ch):04X}}}"


@pytest.mark.parametrize("ch", list("éèêëàâäçñÉÀÇÑÅůőşțÿ"))
def test_lettres_accentuees_gardees(ch):
    assert ui.echapper(ch) == ch


def test_toute_lettre_gardee_se_decompose_en_ascii():
    """Balayage du plan multilingue de base : tout caractère non ASCII affiché
    tel quel est une lettre ASCII + diacritiques combinants en NFD."""
    for o in range(0x80, 0x10000):
        ch = chr(o)
        if ui.echapper(ch) == ch:
            d = unicodedata.normalize("NFD", ch)
            assert d[0].isascii() and d[0].isalpha() and len(d) > 1, hex(o)
            assert all(unicodedata.combining(c) for c in d[1:]), hex(o)


def test_commande_avec_i_sans_point_echappee():
    a = ui.rediger(ui.lire_requete(_msg(op("qube.exec", argv=["curl", "evıl.example"])))).actions[0]
    assert "\\u{0131}" in a.contenu and "ı" not in a.contenu


# --- 2. R-E : chaîne du jetable -------------------------------------------------

def _chaine():
    """Jetables sans réseau eux-mêmes, mais dont un maillon est risqué."""
    sim = installation()
    sim.qubes["dom0"] = dataclasses.replace(sim.qubes["dom0"], default_dispvm_herite=True)  # P hérite (R-E)
    sim.qubes["vault"] = dataclasses.replace(sim.qubes["vault"], default_dispvm_herite=True)
    sim.qubes["tpl-reseau"] = _q("tpl-reseau", "TemplateVM", "black", None, "sys-firewall")
    sim.qubes["tpl-sur-reseau"] = _q("tpl-sur-reseau", "TemplateVM", "black", "tpl-reseau", None)
    sim.qubes["tpl-propre"] = _q("tpl-propre", "TemplateVM", "black")
    jetables = {"dvm-modele-agent": "tpl-a", "dvm-modele-reseau": "tpl-reseau",
                "dvm-modele-modele-reseau": "tpl-sur-reseau", "dvm-propre": "tpl-propre"}
    for n, t in jetables.items():
        sim.qubes[n] = _q(n, "AppVM", "red", t, None, template_for_dispvms=True)
    return contexte(sim)


RISQUES = ["dvm-modele-agent", "dvm-modele-reseau", "dvm-modele-modele-reseau", "a-dvm", "default-dvm"]


@pytest.mark.parametrize("v", RISQUES)
def test_r_e_juge_toute_la_chaine(v):
    ctx = _chaine()
    assert ctx.jetable_risque(v, AGENT)
    assert niveau(op("prefs.set", prop="default_dispvm", value=v), ctx=ctx) == C.N4
    # Mêmes contrôles pour le coffre, par set_prop, reset_prop et file.open_dispvm.
    assert niveau(op("vm.set_prop", name="vault", prop="default_dispvm", value=v), ctx=ctx) == C.N4
    ctx_g = dataclasses.replace(ctx, prefs={**ctx.prefs, "default_dispvm": v})
    assert niveau(op("vm.reset_prop", name="vault", prop="default_dispvm"), ctx=ctx_g) == C.N4
    q = {**ctx.qubes, "vault": {**ctx.qubes["vault"], "default_dispvm": v}}
    assert niveau(op("file.open_dispvm", name="vault"), ctx=dataclasses.replace(ctx, qubes=q)) == C.N4


def test_r_e_chaine_propre_reste_confirmee():
    ctx = _chaine()
    assert not ctx.jetable_risque("dvm-propre", AGENT)
    cl = classer(op("prefs.set", prop="default_dispvm", value="dvm-propre"), ctx=ctx, mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER
    assert niveau(op("vm.set_prop", name="vault", prop="default_dispvm", value="dvm-propre"), ctx=ctx) == C.N3


def test_r_e_sans_heritier_p_ni_coffre():
    # Personne de P ou C n'hérite : la chaîne ne joue pas (N3 confirmé, comme avant).
    sim = installation()
    for n in sim.qubes:
        sim.qubes[n] = dataclasses.replace(sim.qubes[n], default_dispvm_herite=False)
    sim.qubes["dvm-x"] = _q("dvm-x", "AppVM", "red", "tpl-a", None, template_for_dispvms=True)
    assert niveau(op("prefs.set", prop="default_dispvm", value="dvm-x"), ctx=contexte(sim)) == C.N3


def test_chaine_bornee_et_sans_boucle():
    q = {"x": {"template": "y"}, "y": {"template": "x"}}
    assert C.Contexte(q).jetable_risque("x", set()) is False
    assert C.Contexte(q).jetable_risque(None, set()) is False


# --- 3. tags des politiques : récursif, fail-closed --------------------------------

def _politiques(tmp_path, monkeypatch):
    d = tmp_path / "policy.d"
    (d / "include" / "profond").mkdir(parents=True)
    (d / "30-user.policy").write_text("qubes.Filecopy * @tag:projet-a @anyvm ask\n")
    (d / "include" / "profond" / "x").write_text("@tag:projet-b @adminvm allow\n")
    run = tmp_path / "run-policy.d"
    run.mkdir()
    (run / "90-run.policy").write_text("qubes.VMShell * @tag:projet-c @anyvm deny\n")
    monkeypatch.setattr(executor, "POLITIQUES", (str(d), str(run), str(tmp_path / "ancien-absent")))
    return d


def test_tags_lus_recursivement(tmp_path, monkeypatch):
    _politiques(tmp_path, monkeypatch)
    assert executor._tags_politique() == {"projet-a", "projet-b", "projet-c"}  # ancien absent : pas d'erreur


def test_fichier_illisible_rend_inconnu(tmp_path, monkeypatch):
    d = _politiques(tmp_path, monkeypatch)
    vrai_open = open

    def open_(chemin, *a, **kw):
        if str(chemin).endswith("30-user.policy"):
            raise PermissionError(13, "refusé")
        return vrai_open(chemin, *a, **kw)
    monkeypatch.setattr("builtins.open", open_)
    assert executor._tags_politique() is None
    assert d.exists()


def test_dossier_illisible_rend_inconnu(tmp_path, monkeypatch):
    d = _politiques(tmp_path, monkeypatch)
    vrai = os.listdir
    monkeypatch.setattr(executor.os, "listdir",
                        lambda p: (_ for _ in ()).throw(PermissionError(13, "refusé")) if p == str(d / "include")
                        else vrai(p))
    assert executor._tags_politique() is None


def test_racine_illisible_rend_inconnu(tmp_path, monkeypatch):
    _politiques(tmp_path, monkeypatch)
    vrai = os.stat
    monkeypatch.setattr(executor.os, "stat", lambda p, *a, **kw: (_ for _ in ()).throw(PermissionError(13, "x"))
                        if "run-policy.d" in str(p) else vrai(p, *a, **kw))
    assert executor._tags_politique() is None


def test_politique_trop_grosse_rend_inconnu(tmp_path, monkeypatch):
    d = _politiques(tmp_path, monkeypatch)
    (d / "99-gros.policy").write_text("#" * (executor.MAX_POLITIQUE + 1))
    assert executor._tags_politique() is None


def test_tag_quelconque_n4_si_politiques_inconnues():
    ctx = dataclasses.replace(contexte(), tags_politique=None)
    assert niveau(op("vm.tag", tag="essai-libre"), ctx=ctx) == C.N4
    assert niveau(op("vm.tag", tag="essai-libre"), ctx=contexte()) == C.N3


def test_politiques_inconnues_refusees_de_bout_en_bout(tmp_path, monkeypatch):
    """Exécuteur et validateur (contexte JSON, None compris) refusent vm.tag."""
    _politiques(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, "_tags_politique", lambda: None)
    ex = faire(tmp_path)
    assert lancer(ex, op("vm.tag", tag="essai-libre"))["statut"] == proto.REFUSE
    assert ex.traiter({"type": "contexte"})["tags_politique"] is None
    v = Validateur(ex.traiter, ConfigValidateur(pauses=(f"{tmp_path}/pause",)))
    donnees = json.dumps({"version": 1, "request": "R-0042", "ops": [op("vm.tag", tag="essai-libre")]}).encode()
    r = v.traiter("sys-agent", "ok", donnees)
    assert r["statut"] == proto.REFUSE and "refuse cette action à l'agent" in r["raison"]


# --- 4. template.install : N4, message clair -------------------------------------

@pytest.mark.parametrize("repo", ["official", "community"])
@pytest.mark.parametrize("kw", [{}, {"mode": C.PILOTE}, {"influence": True}])
def test_installer_un_modele_toujours_refuse(repo, kw):
    assert classer(op("template.install", repo=repo), **kw).decision == C.REFUSER


def test_message_d_installation_pour_l_agent(tmp_path):
    m = C.motif_n4(plan(op("template.install")))
    assert "refuse cette action à l'agent" in m and "l'utilisateur" in m and "Qubes Template Manager" in m
    assert "N4" not in m and "portier" not in m and "Doorman" not in m  # PARCOURS-1 M8 : l'agent le répète à l'utilisateur
    assert C.motif_n4(plan(op("qube.exec", name="vault"))).endswith("ce qu'il faudrait faire et pourquoi")
    ex = faire(tmp_path)
    r = lancer(ex, op("template.install"))
    assert r["statut"] == proto.REFUSE and "Qubes Template Manager" in r["raison"]
    assert ex.confirmateur.demandes == [] and ex.backend.travaux == {}
    v = Validateur(ex.traiter, ConfigValidateur(pauses=(f"{tmp_path}/pause",)))
    donnees = json.dumps({"version": 1, "request": "R-0042", "ops": [op("template.install")]}).encode()
    assert "Qubes Template Manager" in v.traiter("sys-agent", "ok", donnees)["raison"]


def test_plus_aucun_chemin_d_installation():
    assert "template.install" not in outils.MESSAGES and "template.install" not in executor.LANCEURS
    assert outils.Outils(lancer=None).traiter({"type": "template.install", "nom": "x", "repo": "official"})[
        "ok"] is False
    from qaf_gate import backend
    assert "template_install" not in backend.METHODES_V3
    assert "template.remove" in executor.LANCEURS and "template.remove" in outils.MESSAGES  # remove reste


def test_skill_et_outil_mcp_disent_que_utilisateur_installe():
    from qaf_mcp.server import OUTILS
    d = next(o for o in OUTILS if o["name"] == "template_install")["description"]
    assert "Toujours refusé" in d and "N4" not in d and "Qubes Template Manager" in d
    import skills_qaf
    skill = skills_qaf.tous()
    assert "l'installation d'un modèle se fait par l'utilisateur avec l'outil Qubes Template" in skill.replace("\n", " ")


# --- 5. fenêtre de backup.run -------------------------------------------------------

def _msg(o, qubes=(("work", "blue", "utilisateur"),), **extra):
    canon = json.dumps({"version": 1, "request": "R-0001", "ops": [o]}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    return {"type": "confirmer", "appelant": "sys-agent", "canonique": canon, "niveaux": [3],
            "decision": "confirmer", "jeton": "123456", "influence": False, "qubes": [list(q) for q in qubes],
            "expire_dans": 60, "texte_demande": None, "age_demande": None, "hors_portee": [], **extra}


def _phrase(o, **kw):
    return ui.rediger(ui.lire_requete(_msg(o, **kw))).actions[0].phrase.replace(" ", " ")


def test_sauvegarde_nomme_destination_et_chemin():
    p = _phrase(op("backup.run"), qubes=(("personal", "yellow", "utilisateur"),),
                sauvegardes=[["hebdo", "personal", "/home/user/sauvegardes"]])
    assert "« personal »" in p and "« /home/user/sauvegardes »" in p and "« hebdo »" in p
    p = _phrase(op("backup.run"), qubes=(), sauvegardes=[["hebdo", "dom0", "/run/media/user/disque"]])
    assert "dom0" in p and "/run/media/user/disque" in p
    # Chemin trompeur : échappé.
    p = _phrase(op("backup.run"), qubes=(), sauvegardes=[["hebdo", "dom0", "/media/\u202egpj.exe"]])
    assert "\\u{202E}" in p and "\u202e" not in p
    assert "n'a pas pu lire" in _phrase(op("backup.run"))


@pytest.mark.parametrize("s", [["hebdo", "dom0"], ["../x", "dom0", "/a"], ["hebdo", "-x", "/a"],
                               ["hebdo", "dom0", ""], ["hebdo", "dom0", "x" * 257], ["hebdo", "dom0", 3]])
def test_sauvegarde_mal_formee_refusee(s):
    with pytest.raises(ui.DemandeInvalide):
        ui.lire_requete(_msg(op("backup.run"), sauvegardes=[s]))


def test_executeur_envoie_la_destination(tmp_path):
    ex = faire(tmp_path, reponses=(False,))
    ex.backend.profils["hebdo"] = {"destination_vm": "personal", "destination_path": "/home/user/s",
                                   "passphrase_vm": None}
    lancer(ex, op("backup.run"))
    d = ex.confirmateur.demandes[-1]
    assert d.sauvegardes == (("hebdo", "personal", "/home/user/s"),)
    assert "personal" in {q[0] for q in d.qubes}  # rôle et label de la destination pour la fenêtre


def test_fenetre_transmet_les_sauvegardes(tmp_path):
    import socket
    import threading
    os.chmod(tmp_path, 0o755)
    chemin = str(tmp_path / "c.sock")
    recu = {}
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(chemin)
    srv.listen(1)

    def servir():
        c, _ = srv.accept()
        recu.update(proto.recevoir_json(c, 1 << 20))
        proto.envoyer_json(c, {"accorde": False})
        c.close()
    threading.Thread(target=servir, daemon=True).start()
    p = plan(op("backup.run"))
    d = confirm.Demande("sys-agent", p, schema.canonicalize(p), (3,), C.CONFIRMER, "123456",
                        sauvegardes=(("hebdo", "dom0", "/mnt"),))
    assert confirm.Fenetre(chemin, delai=5).demander(d) is False
    srv.close()
    assert recu["sauvegardes"] == [["hebdo", "dom0", "/mnt"]]
    assert ui.lire_requete(recu).sauvegardes == {"hebdo": ("dom0", "/mnt")}


# --- 6. qaf-gate-outils : plus d'écriture dans /run/qubes -----------------------------

def test_outils_sans_ecriture_dans_run_qubes():
    u = (RACINE / "packaging/qaf-gate-outils.service").read_text().splitlines()
    rw = [ligne for ligne in u if ligne.startswith("ReadWritePaths=")]
    assert rw == ["ReadWritePaths=/var/log/qubes"]


# --- 7. bail sur un coffre : durée réelle ----------------------------------------------

def test_bail_coffre_affiche_cinq_minutes():
    p = _phrase(op("enter.open", name="vault", minutes=30), qubes=(("vault", "black", "coffre"),))
    assert "pendant 5 minutes" in p and "30" not in p
    p = _phrase(op("enter.open", name="work", minutes=30))
    assert "pendant 30 minutes" in p
    p = _phrase(op("enter.open", name="vault", minutes=1), qubes=(("vault", "black", "coffre"),))
    assert "pendant 1 minute " in p


def test_bail_coffre_meme_duree_que_l_executeur(tmp_path):
    cfg = dataclasses.replace(CONFIG, coffres_entrables=frozenset({"vault"}))
    ex = faire(tmp_path, config=cfg)
    ex.backend.qubes["vault"] = dataclasses.replace(ex.backend.qubes["vault"], state="Running")
    r = lancer(ex, op("enter.open", name="vault", minutes=30))
    assert r["resultats"][0]["resultat"]["minutes"] == C.MINUTES_COFFRE == 5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
