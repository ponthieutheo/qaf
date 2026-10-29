"""OPS-v3, lots 2 et 3 (D13 à D18, sondes du §13.1) : schéma, classement,
exécution sur le backend simulé, bail d'entrée, qaf-gate-taches (vmexec typé et
borne P-4), qaf-gate-outils (argv construits ici, travaux, profils lus
strictement), backend réel avec un faux qubesadmin, outils MCP, fenêtre, script
d'installation de qaf_hand dans les modèles."""

import dataclasses
import json
import pathlib
import shutil
import subprocess
import sys
import types

import pytest

from qaf_gate import classify as C
from qaf_gate import confirm, executor, outils, proto, schema, taches
from qaf_gate.backend import BackendError
from qaf_gate.journal import Journal
from qaf_gate.registry import Registry
from qaf_gate.schema import PlanError
from qaf_gate.validator import Config as ConfigValidateur
from qaf_gate.validator import Validateur
from qaf_mcp.server import OUTILS, OPS_OUTILS, construire_op
from qaf_ui import confirmation as ui
from backend_sim import SimBackend, _q, qubes_par_defaut
from exemples_ops import EXEMPLES, op
from test_ops_v3_classement import AGENT, CONFIG, classer, contexte, installation, niveau, plan

RACINE = pathlib.Path(__file__).resolve().parent.parent
NOUVELLES = ("update.run", "template.available", "template.install", "job.status", "job.cancel", "enter.open",
             "enter.close", "file.copy", "file.open_dispvm", "dev.attach", "dev.detach", "backup.profiles",
             "backup.run", "pkg.remove", "template.remove", "vm.tag", "vm.grow_private", "file.move")


# --- schéma ----------------------------------------------------------------

def test_quarante_neuf_operations():
    # OPS-v3 §11 : 41 + 8, plus vm.clone (PARCOURS-1 B2).
    assert set(NOUVELLES) <= set(schema.OPS) and len(schema.OPS) == 50


@pytest.mark.parametrize("chemin", ["", "/etc/passwd", "../x", "a/../b", ".ssh/id", "a/.x", "-x", "a/-b", "a//b",
                                    "a/", "a\nb", "a\0b", "x" * 256, "a‮b", ".", "~/x", 3, None])
def test_chemin_refuse(chemin):
    with pytest.raises(PlanError):
        plan(op("file.copy", path=chemin))


@pytest.mark.parametrize("chemin", ["rapport.pdf", "Documents/été 2026/facture n°3.pdf", "_x/y", "1/2"])
def test_chemin_admis(chemin):
    assert plan(op("file.copy", path=chemin))


@pytest.mark.parametrize("champ,valeur", [("device_id", "*"), ("device_id", "_"), ("device_id", ""),
                                          ("port", "*"), ("port", "_"), ("port", ""), ("port", "2-1\n"),
                                          ("device_id", "a" * 257), ("class", "webcam"), ("class", "pci")])
def test_peripherique_sans_joker(champ, valeur):
    # P-6/P-13 : « * », « _ » seul et le vide sont des jokers ; pas de classe webcam.
    with pytest.raises(PlanError):
        plan(op("dev.attach", **{champ: valeur}))


def test_peripherique_identifiant_obligatoire():
    o = op("dev.attach")
    del o["device_id"]
    with pytest.raises(PlanError):
        plan(o)
    assert plan(op("dev.attach", **{"class": "mic", "backend": "dom0", "port": "mic",
                                    "device_id": "dom0:mic::m000000"}))


@pytest.mark.parametrize("o", [op("job.status", job="T-12345"), op("job.cancel", job="t-123456"),
                               op("backup.run", profile="../x"), op("backup.run", profile="a b"),
                               op("template.install", repo="testing"), op("update.run", targets=[]),
                               op("update.run", apply="reboot"), op("vm.tag", tag="Projet"),
                               op("vm.grow_private", size_mib=100), op("enter.open", minutes=61),
                               op("update.run", targets=["a"] * 2), op("dev.attach", read_only="yes")])
def test_champs_fermes(o):
    with pytest.raises(PlanError):
        plan(o)


def test_lourdes():
    for a, b in (("file.copy", "pkg.remove"), ("template.available", "qube.exec"), ("file.move", "file.copy")):
        with pytest.raises(PlanError):
            plan(op(a), op(b))


# --- classement ----------------------------------------------------------------

def ctx_profils(**profils):
    ctx = contexte()
    return dataclasses.replace(ctx, profils=profils)


@pytest.mark.parametrize("o,n", [
    (op("update.run", targets=["debian-13-xfce"]), C.N2),  # M(S) 2 : même chemin que Qubes Update
    (op("update.run", targets=["tpl-a"]), C.N1),
    (op("update.run", apply="sys", targets=["fedora-42-xfce"]), C.N3),
    (op("update.run", targets=["dom0"]), C.N4),
    (op("update.run", targets=["tpl-agent"]), C.N4),
    (op("update.run", apply="sys", targets=["debian-13-xfce"]), C.N4),  # redémarrerait sys-usb (R-G)
    (op("template.install"), C.N4), (op("template.install", repo="community"), C.N4),  # sudo (RESULTATS-V2)
    (op("template.remove", template="debian-13-xfce"), C.N4),  # modèle de sys-net
    (op("template.remove", template="fedora-42-xfce"), C.N3), (op("template.remove", template="tpl-a"), C.N1),
    (op("template.remove", template="tpl-agent"), C.N4),
    (op("job.status"), C.N0), (op("job.cancel"), C.N2), (op("backup.profiles"), C.N0),
    (op("enter.open"), C.N3), (op("enter.open", name="a-dev"), C.N1), (op("enter.open", name="sys-firewall"), C.N4),
    (op("enter.open", name="debian-13-xfce"), C.N4), (op("enter.open", name="fedora-42-xfce"), C.N3),
    (op("enter.open", name="vault"), C.N4), (op("enter.open", name="qube-admin"), C.N4),
    (op("enter.open", name="sys-agent"), C.N4), (op("enter.close"), C.N0),
    (op("file.copy", source="a-dev", dest="disp9"), C.N1), (op("file.copy"), C.N3),
    (op("file.copy", source="work", dest="a-dev"), C.N3), (op("file.copy", dest="sys-agent"), C.N4),
    (op("file.copy", dest="sys-usb"), C.N4), (op("file.copy", source="vault"), C.N3),
    (op("file.move", source="vault"), C.N4), (op("file.copy", source="dom0"), C.N4),
    (op("file.open_dispvm"), C.N3), (op("file.open_dispvm", name="a-dev"), C.N1),
    (op("file.open_dispvm", name="vault"), C.N4),
    (op("dev.attach"), C.N4),  # backend sys-usb : ne_jamais_demarrer (R-G)
    (op("dev.attach", backend="sys-net", port="xvdi"), C.N3),
    (op("dev.attach", target="vault", backend="sys-net"), C.N4),
    (op("dev.attach", target="sys-firewall", backend="sys-net"), C.N4),
    (op("dev.attach", **{"class": "mic", "backend": "dom0", "port": "mic", "device_id": "dom0:mic::m000000"}), C.N3),
    (op("dev.attach", **{"class": "usb", "backend": "dom0", "port": "1-1", "device_id": "x"}), C.N4),
    (op("dev.attach", **{"class": "block", "backend": "dom0", "port": "nvme0n1", "device_id": "x"}), C.N4),
    (op("dev.attach", target="sys-agent", backend="sys-net"), C.N4),
    (op("dev.detach"), C.N2), (op("dev.detach", target="a-dev"), C.N1), (op("dev.detach", target="vault"), C.N3),
    (op("vm.tag"), C.N3), (op("vm.tag", name="a-dev"), C.N3), (op("vm.tag", tag="qaf-x"), C.N4),
    (op("vm.tag", tag="created-by-x"), C.N4), (op("vm.tag", name="sys-agent"), C.N4),
    (op("pkg.remove"), C.N3), (op("pkg.remove", name="tpl-a"), C.N1),
    (op("pkg.remove", name="tpl-a", packages=["qubes-core-agent"]), C.N4),
    (op("pkg.remove", name="tpl-a", packages=["systemd"]), C.N4),
    (op("pkg.remove", name="tpl-a", packages=["libc6"]), C.N4),
    (op("vm.grow_private"), C.N2), (op("vm.grow_private", name="a-dev"), C.N1),
    (op("vm.grow_private", name="sys-net"), C.N3),
])
def test_niveaux_lots_2_3(o, n):
    assert niveau(o) == n


def test_tags_des_politiques_interdits():
    ctx = dataclasses.replace(contexte(), tags_politique=frozenset({"projet-x"}))
    assert niveau(op("vm.tag"), ctx=ctx) == C.N4


@pytest.mark.parametrize("profil,attendu", [
    (None, C.REFUSER), ({"destination_vm": "a-dev", "passphrase_vm": None}, C.REFUSER),
    ({"destination_vm": "sys-agent", "passphrase_vm": None}, C.REFUSER),
    ({"destination_vm": "work", "passphrase_vm": "a-dev"}, C.REFUSER),
    ({"destination_vm": "work", "passphrase_vm": "qube-admin"}, C.REFUSER),
    ({"destination_vm": "sys-usb", "passphrase_vm": None}, C.REFUSER),  # R-G : la démarrerait
    ({"destination_vm": "dom0", "passphrase_vm": None}, C.CONFIRMER),  # disque externe dans dom0
    ({"destination_vm": "personal", "passphrase_vm": "vault"}, C.CONFIRMER),
])
def test_sauvegarde(profil, attendu):
    # V13 : jamais l'archive ni la phrase de passe chez l'agent ; toujours confirmé, même en Pilote.
    ctx = ctx_profils(**({"hebdo": profil} if profil else {}))
    assert classer(op("backup.run"), ctx=ctx, mode=C.PILOTE).decision == attendu


def test_bail_entrer_chez_utilisateur_toujours_confirme():
    # V6 : même en Pilote non influencé, l'écran part chez le fournisseur.
    assert classer(op("enter.open"), mode=C.PILOTE).decision == C.CONFIRMER


def test_coffre_entrable_renforce_meme_en_pilote():
    cfg = dataclasses.replace(CONFIG, coffres_entrables=frozenset({"vault"}))
    cl = classer(op("enter.open", name="vault"), ctx=contexte(config=cfg), mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER_RENFORCE


def test_config_coffres_entrables():
    cfg = C.Config.de_json({"coffres_entrables": ["vault"]})
    assert cfg.coffres_entrables == {"vault"} and cfg.en_json()["coffres_entrables"] == ["vault"]
    livree = json.loads((RACINE / "packaging/portier.json").read_text())
    assert livree["coffres_entrables"] == []  # D15 : vide par défaut
    with pytest.raises(ValueError):
        C.Config.de_json({"coffres_entrables": ["-x"]})


@pytest.mark.parametrize("nom", sorted(C.ENTRER))
def test_sous_bail_automatique_meme_influence_et_hors_portee(nom):
    ctx = dataclasses.replace(contexte(), baux=frozenset({"work"}))
    cl = classer(op(nom), ctx=ctx, influence=True, portee=set())
    assert cl.decision == C.AUTO and cl.hors_portee == ()
    # Le bail ne vaut que pour ce qube : un autre reste confirmé.
    assert classer(op(nom, name="personal"), ctx=ctx, influence=True).decision != C.AUTO
    # Jamais un qube protégé, même sous un bail inscrit par erreur.
    assert classer(op(nom, name="vault"), ctx=dataclasses.replace(ctx, baux=frozenset())).decision == C.REFUSER


def test_copie_depuis_un_coffre_renforcee():
    cl = classer(op("file.copy", source="vault"), mode=C.PILOTE)
    assert cl.decision == C.CONFIRMER_RENFORCE
    assert classer(op("file.copy", dest="vault"), mode=C.PILOTE).decision == C.CONFIRMER


def test_bloc_de_utilisateur_vers_l_agent_renforce():
    cl = classer(op("dev.attach", backend="sys-net", port="xvdi", target="a-dev"), mode=C.PILOTE, influence=True)
    assert cl.decision == C.CONFIRMER_RENFORCE




def test_liste_des_modeles_ne_demarre_pas_un_updatevm_interdit():
    sim = installation()
    sim.globaux["updatevm"] = "sys-usb"
    assert niveau(op("template.available"), ctx=contexte(sim)) == C.N4


def test_maj_d_un_modele_de_coffre_confirmee():
    sim = installation()
    sim.qubes["tpl-v"] = _q("tpl-v", "TemplateVM", "black")
    sim.qubes["vault"] = dataclasses.replace(sim.qubes["vault"], template="tpl-v")
    cl = classer(op("update.run", targets=["tpl-v"]), ctx=contexte(sim), mode=C.PILOTE)
    assert cl.decision == C.CONFIRMER


# --- exécution sur le backend simulé ------------------------------------------------

def faire(tmp_path, reponses=(True,) * 8, influence=False, mode=C.NORMAL, horloge=None, config=None):
    qs = qubes_par_defaut() + [_q("tpl-qaf-hand", "TemplateVM", "black"),
                               _q("qaf-dvm", "AppVM", "red", "tpl-qaf-hand", None, template_for_dispvms=True)]
    kw = {"horloge": horloge} if horloge else {}
    ex = executor.Executeur(SimBackend(qs), Registry(f"{tmp_path}/registre.json"),
                            Journal(f"{tmp_path}/journal.jsonl"), confirm.Scripte(list(reponses)),
                            pauses=(f"{tmp_path}/pause",), pause_auto=f"{tmp_path}/pause", config=config, **kw)
    ex.etat = {"influence": influence, "mode": mode}
    return ex


def lancer(ex, *ops):
    texte = json.dumps({"version": 1, "request": "R-0001", "ops": list(ops)}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False)
    return ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": texte})


def journal(tmp_path):
    with open(f"{tmp_path}/journal.jsonl", encoding="utf-8") as f:
        return [e.get("event", e) for e in map(json.loads, f)]


class Horloge:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_bail_ouvert_puis_actions_automatiques(tmp_path):
    h = Horloge()
    ex = faire(tmp_path, reponses=[True], influence=True, horloge=h)
    r = lancer(ex, op("enter.open", minutes=10))
    assert r["statut"] == proto.FAIT and r["resultats"][0]["resultat"] == {"minutes": 10}
    assert ex.backend.appels[-1][:3] == ("hand", "work", "qaf.Screen")  # qaf_hand répond
    assert len(ex.confirmateur.demandes) == 1
    # Sous bail : plus de fenêtre (le Scripte n'a plus de « oui »), session influencée ou non.
    assert lancer(ex, op("qube.key"))["statut"] == proto.FAIT
    assert lancer(ex, op("qube.exec"))["statut"] == proto.FAIT
    assert len(ex.confirmateur.demandes) == 1
    # Un autre qube reste confirmé (ici : refusé, plus de réponse scriptée).
    assert lancer(ex, op("qube.key", name="personal"))["statut"] == proto.REFUSE
    # Échéance : le bail tombe.
    h.t += 601
    assert lancer(ex, op("qube.key"))["statut"] == proto.REFUSE


def test_bail_coupe_par_la_pause_l_uuid_et_enter_close(tmp_path):
    ex = faire(tmp_path, reponses=[True] * 3)
    lancer(ex, op("enter.open"))
    assert lancer(ex, op("enter.close"))["resultats"][0]["resultat"] == {"ferme": True}
    assert ex.baux == {}
    lancer(ex, op("enter.open"))
    ex.backend.qubes["work"] = dataclasses.replace(ex.backend.qubes["work"], uuid="autre")  # recréé par l'utilisateur
    assert ex._baux(ex.backend.list_qubes()) == set()
    lancer(ex, op("enter.open"))
    open(f"{tmp_path}/pause", "w").write("arrêt d'urgence")
    assert ex._baux(ex.backend.list_qubes()) == set()


def test_bail_coffre_cinq_minutes_et_trois_baux_au_plus(tmp_path):
    cfg = C.Config(coffres_entrables=frozenset({"vault"}))
    ex = faire(tmp_path, reponses=[True] * 8, config=cfg)
    ex.backend.qubes["vault"] = dataclasses.replace(ex.backend.qubes["vault"], state="Running")
    assert lancer(ex, op("enter.open", name="vault", minutes=60))["resultats"][0]["resultat"] == {"minutes": 5}
    for n in ("personal", "untrusted"):
        ex.backend.qubes[n] = dataclasses.replace(ex.backend.qubes[n], state="Running")
        assert lancer(ex, op("enter.open", name=n))["statut"] == proto.FAIT
    r = lancer(ex, op("enter.open", name="work"))
    assert r["statut"] == proto.ERREUR and "3 baux" in r["resultats"][0]["message"]


def test_bail_exige_qaf_hand(tmp_path, monkeypatch):
    ex = faire(tmp_path)

    def absent(*a):
        raise BackendError("autre", "qaf.Screen a échoué")
    monkeypatch.setattr(ex.backend, "hand", absent)
    assert lancer(ex, op("enter.open"))["statut"] == proto.ERREUR and ex.baux == {}


def test_travail_long_suivi_puis_fini(tmp_path):
    ex = faire(tmp_path)
    tid = lancer(ex, op("update.run", targets=["fedora-42-xfce"]))["resultats"][0]["resultat"]["travail"]
    assert lancer(ex, op("job.status", job=tid))["resultats"][0]["resultat"]["etat"] == "en_cours"
    ex.backend.finir(tid)
    res = lancer(ex, op("job.status", job=tid))["resultats"][0]["resultat"]
    assert res == {"travail": tid, "etat": "fini", "code": 0, "duree": 12}
    ops = [e for e in journal(tmp_path) if e.get("type") == "op"]
    assert ops[0]["travail"] == tid and ops[-1]["etat"] == "fini" and ops[-1]["code"] == 0


def test_maj_signale_les_cibles_prohibit_start(tmp_path):
    ex = faire(tmp_path)
    ex.backend.features["fedora-42-xfce"] = {"prohibit-start": "1"}
    r = lancer(ex, op("update.run", targets=["debian-13-xfce", "fedora-42-xfce"]))
    assert r["resultats"][0]["resultat"]["ignorees_prohibit_start"] == ["fedora-42-xfce"]
    tid = r["resultats"][0]["resultat"]["travail"]
    assert ex.backend.travaux[tid]["args"]["cibles"] == ["debian-13-xfce"]
    assert lancer(ex, op("job.cancel", job=tid))["statut"] == proto.FAIT
    assert lancer(ex, op("job.status", job=tid))["resultats"][0]["resultat"]["etat"] == "annule"


def test_maj_refuse_une_appvm(tmp_path):
    r = lancer(faire(tmp_path), op("update.run", targets=["work"]))
    assert r["statut"] == proto.ERREUR and r["resultats"][0]["code"] == "pas_un_template"


def test_sauvegarde_par_le_validateur(tmp_path):
    ex = faire(tmp_path)
    v = Validateur(ex.traiter, ConfigValidateur(pauses=(f"{tmp_path}/pause",)))
    donnees = json.dumps({"version": 1, "request": "R-0001", "ops": [op("backup.run")]}).encode()
    assert v.traiter("sys-agent", "ok", donnees)["statut"] == proto.REFUSE  # profil inconnu : N4
    ex.backend.profils["hebdo"] = {"destination_vm": "personal", "passphrase_vm": None}
    r = v.traiter("sys-agent", "ok", donnees)
    assert r["statut"] == proto.FAIT and r["resultats"][0]["resultat"]["travail"].startswith("T-")
    ex.backend.profils["hebdo"] = {"destination_vm": "personal", "passphrase_vm": "sys-agent"}
    assert v.traiter("sys-agent", "ok", donnees)["statut"] == proto.REFUSE


def test_copie_lancee_dans_la_source(tmp_path):
    ex = faire(tmp_path)
    ex.backend.qubes["personal"] = dataclasses.replace(ex.backend.qubes["personal"], state="Running")
    assert lancer(ex, op("file.copy", source="personal", dest="work"))["statut"] == proto.FAIT
    assert ex.backend.appels[-1] == ("vmexec", "personal", "file.copy",
                                     {"dest": "work", "chemin": "Documents/rapport.pdf"})
    assert ex.etat["influence"] is False  # rien du fichier ne revient à l'agent
    r = lancer(ex, op("file.copy", source="untrusted", dest="work"))
    assert r["statut"] == proto.ERREUR and r["resultats"][0]["code"] == "pas_demarre"


def test_ouvrir_dans_un_jetable(tmp_path):
    ex = faire(tmp_path)
    assert lancer(ex, op("file.open_dispvm"))["statut"] == proto.FAIT
    assert ex.backend.appels[-1][2] == "file.open_dispvm"


def test_attache_liee_au_peripherique_vu(tmp_path):
    ex = faire(tmp_path)
    ex.backend.peripheriques = [{"class": "block", "backend": "sys-net", "port": "xvdi", "device_id": "d1",
                                 "attached_to": None}]
    assert lancer(ex, op("dev.attach", backend="sys-net", port="xvdi", device_id="d1"))["statut"] == proto.FAIT
    assert ex.backend.attaches == [("block", "sys-net", "xvdi", "d1", "work", {"read-only": "yes"})]
    r = lancer(ex, op("dev.attach", backend="sys-net", port="xvdi", device_id="autre"))
    assert r["resultats"][0]["code"] == "peripherique_change"
    assert lancer(ex, op("dev.detach", backend="sys-net", port="xvdi"))["statut"] == proto.FAIT
    assert ex.backend.attaches == []


def test_tag_disque_paquets(tmp_path):
    ex = faire(tmp_path)
    assert lancer(ex, op("vm.tag"))["resultats"][0]["resultat"] == {"avant": False}
    assert lancer(ex, op("vm.grow_private"))["resultats"][0]["resultat"] == {"avant": 2048}
    assert lancer(ex, op("vm.grow_private", size_mib=2048))["statut"] == proto.ERREUR
    assert lancer(ex, op("pkg.remove"))["statut"] == proto.FAIT
    assert ex.backend.appels[-1] == ("vmexec", "debian-13-xfce", "debian", ["cups"], "pkg.remove")
    assert lancer(ex, op("backup.profiles"))["statut"] == proto.FAIT
    assert lancer(ex, op("template.available"))["statut"] == proto.FAIT and ex.etat["influence"] is True


def test_tags_lus_dans_les_politiques(tmp_path, monkeypatch):
    d = tmp_path / "policy.d"
    d.mkdir()
    (d / "30-user.policy").write_text("qubes.Filecopy * @tag:projet-x @anyvm ask\n")
    monkeypatch.setattr(executor, "POLITIQUES", (str(d), str(tmp_path / "absent")))
    assert executor._tags_politique() == {"projet-x"}
    assert lancer(faire(tmp_path), op("vm.tag"))["statut"] == proto.REFUSE


# --- qaf-gate-taches : vmexec typé, borne P-4 -----------------------------------------

def vmexec_msg(**champs):
    base = {"type": "vmexec", "qube": "work", "action": "file.copy", "dest": "personal", "utilisateur": "user",
            "chemin": "Documents/rapport.pdf", "delai": 290}
    return {**base, **champs}


def test_copie_en_utilisateur_dans_la_source():
    vu = []
    assert taches.traiter(vmexec_msg(), execvm=lambda *a: vu.append(a) or {"ok": True, "codes": [0]})["ok"]
    qube, cmds, _ = vu[0]
    assert qube == "work" and cmds == [taches.commande_vmexec(
        ["qvm-copy-to-vm", "--", "personal", "/home/user/Documents/rapport.pdf"], False)]
    assert cmds[0].startswith("DEFAULT:QUBESRPC qubes.VMExec+qvm--copy--to--vm+----+personal+")


def test_deplacer_et_ouvrir():
    vu = []
    taches.traiter(vmexec_msg(action="file.move"), execvm=lambda *a: vu.append(a) or {})
    assert "qvm--move--to--vm" in vu[-1][1][0]
    m = vmexec_msg(action="file.open_dispvm")
    del m["dest"]
    taches.traiter(m, execvm=lambda *a: vu.append(a) or {})
    assert vu[-1][1] == [taches.commande_vmexec(
        ["setsid", "-f", "qvm-open-in-dvm", "--view-only", "--", "/home/user/Documents/rapport.pdf"], False)]


def test_retrait_de_paquets_en_root_sans_cascade():
    vu = []
    for distro, outil in (("debian", "dpkg+----remove+----+cups"), ("fedora", "rpm+--e+----+cups")):
        taches.traiter({"type": "vmexec", "qube": "tpl", "action": "pkg.remove", "distro": distro,
                        "paquets": ["cups"], "delai": 300}, execvm=lambda *a: vu.append(a) or {})
        assert vu[-1][1] == [f"root:QUBESRPC qubes.VMExec+{outil} dom0"]


@pytest.mark.parametrize("modif", [{"chemin": "../etc"}, {"chemin": ".ssh/id"}, {"utilisateur": "a b"},
                                   {"utilisateur": "../x"}, {"dest": "dom0"}, {"qube": "dom0"}, {"argv": ["x"]},
                                   {"action": "file.cat"}, {"distro": "debian"}, {"dest": "-x"}])
def test_vmexec_fichier_refuse(modif):
    vu = []
    assert taches.traiter(vmexec_msg(**modif), execvm=lambda *a: vu.append(a))["ok"] is False and vu == []


def test_borne_p4_refusee_sans_rien_envoyer(monkeypatch):
    # P-4 : 4 097 octets tuent le qrexec-daemon du qube ; refus au-delà de 3 500.
    vu = []
    pire = "a" + "\U0001F600" * 254  # 4 octets UTF-8, 12 caractères encodés chacun : le pire chemin admis
    cmd = taches.commande_vmexec(["qvm-copy-to-vm", "--", "personal", f"/home/user/{pire}"], False)
    assert len(cmd.encode()) < taches.MAX_VMEXEC  # le schéma tient déjà sous la borne
    monkeypatch.setattr(taches, "MAX_VMEXEC", 1000)
    r = taches.traiter(vmexec_msg(chemin=pire), execvm=lambda *a: vu.append(a) or {})
    assert r["ok"] is False and "P-4" in r["raison"] and vu == []
    monkeypatch.undo()
    # Les pires cas admis tiennent sous la borne (§13.1).
    paquets = [f"p{i}" + "a" * 61 for i in range(8)]
    assert taches.traiter({"type": "vmexec", "qube": "tpl", "action": "pkg.install", "distro": "debian",
                           "paquets": paquets, "delai": 300}, execvm=lambda *a: vu.append(a) or {"ok": 1})
    assert max(len(c.encode()) for c in vu[-1][1]) < taches.MAX_VMEXEC
    court = "/".join(["é" * 20] * 12)[:150]
    taches.traiter(vmexec_msg(chemin=court), execvm=lambda *a: vu.append(a) or {})
    assert len(vu) == 2


# --- qaf-gate-outils -----------------------------------------------------------------

class FauxProc:
    def __init__(self, sortie=b"", code=None):
        import os
        r, w = os.pipe()
        os.write(w, sortie)
        os.close(w)
        self.stdout = open(r, "rb")
        self.pid, self.code, self.tue = 4242, code, False

    def wait(self, timeout=None):
        import time
        while self.code is None:
            time.sleep(0.01)
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def outil():
    lances, signaux, annulees = [], [], []
    procs = []

    def lancer(argv, **kw):
        lances.append((argv, kw))
        p = FauxProc(LISTE if "list" in argv else b"", 0 if "list" in argv else None)
        procs.append(p)
        return p
    o = outils.Outils(lancer=lancer, annuler=annulees.append, tuer=lambda pid, sig: signaux.append((pid, sig)))
    return o, lances, signaux, annulees, procs


LISTE = ("available|debian-13-minimal|0:4.3.0-202510232142|qubes-templates-itl\n"
         "available|ignore-tout|0:1|qubes-templates-itl-testing\n"
         "available|-x|0:1|qubes-templates-itl\n"
         "extra|whonix-17|0:1|qubes-templates-community\n"
         "available|kali|0:2024.1|qubes-templates-community\n"
         "available|x‮|0:1|qubes-templates-itl\n").encode()


def test_outils_argv_construits_ici(outil):
    o, lances, *_ = outil
    r = o.traiter({"type": "update", "cibles": ["debian-13-xfce", "fedora-44-xfce"], "apply": "none"})
    assert r["ok"] and r["travail"].startswith("T-")
    argv, kw = lances[-1]
    assert argv == [outils.QVU, "--quiet", "--no-progress", "--no-apply", "--targets", "debian-13-xfce,fedora-44-xfce"]
    assert kw["cwd"] == outils.HOME and kw["env"]["HOME"] == outils.HOME and kw["start_new_session"]
    assert kw["stdout"] == subprocess.DEVNULL  # aucune sortie lue ni gardée


def test_outils_modele(outil):
    o, lances, *_ = outil
    assert o.traiter({"type": "template.install", "nom": "kali", "repo": "community"})["ok"] is False
    assert lances == []  # plus d'installation : qvm-template importe par sudo (N4)
    o.traiter({"type": "template.remove", "nom": "kali"})
    assert lances[-1][0] == [outils.QVT, "--yes", "remove", "--", "kali"]
    assert not any("--nogpgcheck" in a or a in ("purge", "--disassoc") for argv, _ in lances for a in argv)


def test_outils_liste_filtree(outil):
    o, lances, *_ = outil
    r = o.traiter({"type": "template.list", "repo": "official"})
    assert r["modeles"] == [{"nom": "debian-13-minimal", "version": "0:4.3.0-202510232142",
                             "depot": "qubes-templates-itl"},
                            {"nom": "kali", "version": "0:2024.1", "depot": "qubes-templates-community"}]
    assert lances[-1][0] == [outils.QVT, "list", "--available", "--machine-readable"]


def test_outils_un_travail_a_la_fois_puis_annulation(outil):
    o, lances, signaux, annulees, procs = outil
    t = o.traiter({"type": "update", "cibles": ["tpl"], "apply": "sys"})["travail"]
    r = o.traiter({"type": "update", "cibles": ["tpl"], "apply": "none"})
    assert r["ok"] is False and "déjà en cours" in r["raison"]
    assert o.traiter({"type": "job.status", "travail": t})["etat"] == "en_cours"
    assert o.traiter({"type": "job.cancel", "travail": t})["ok"]
    assert signaux[0][1] == outils.signal.SIGTERM and annulees == []
    procs[-1].code = -15
    import time
    time.sleep(0.05)
    assert o.traiter({"type": "job.status", "travail": t})["etat"] == "annule"
    assert o.traiter({"type": "job.status", "travail": "T-000000"})["ok"] is False


def test_outils_sauvegarde_annulee_par_qubesd(outil, tmp_path, monkeypatch):
    o, lances, signaux, annulees, _ = outil
    monkeypatch.setattr(outils, "PROFILS", str(tmp_path))
    (tmp_path / "hebdo.conf").write_text("destination_path: /mnt\ndestination_vm: dom0\ninclude: null\n"
                                         "passphrase_text: 's3cret*&'\n")
    t = o.traiter({"type": "backup", "profil": "hebdo"})["travail"]
    assert lances[-1][0] == [outils.QVB, "--yes", "--profile", "hebdo"]
    o.traiter({"type": "job.cancel", "travail": t})
    assert annulees == ["hebdo"]
    assert o.traiter({"type": "backup.info", "profil": "hebdo"}) == {"ok": True, "destination_vm": "dom0",
                                                                     "destination_path": "/mnt",
                                                                     "passphrase_vm": None}
    assert o.traiter({"type": "backup.list"})["profils"] == [{"profil": "hebdo", "destination_vm": "dom0"}]
    assert "s3cret" not in json.dumps(o.traiter({"type": "backup.list"}))


@pytest.mark.parametrize("msg", [
    {"type": "update", "cibles": ["dom0"], "apply": "none"}, {"type": "update", "cibles": ["-x"], "apply": "none"},
    {"type": "update", "cibles": ["tpl"], "apply": "none", "argv": ["sh"]},
    {"type": "update", "cibles": "tpl", "apply": "none"}, {"type": "template.install", "nom": "x", "repo": "testing"},
    {"type": "template.install", "nom": "--nogpgcheck", "repo": "official"}, {"type": "backup", "profil": "../x"},
    {"type": "shell"}, {"type": "job.status", "travail": "1"}, [], None, {"type": ["update"]},
])
def test_outils_messages_refuses(outil, msg):
    o, lances, *_ = outil
    assert o.traiter(msg)["ok"] is False and lances == []


@pytest.mark.parametrize("texte", [
    "destination_vm: a\ndestination_vm: b\n", "\"destination_vm\": sys-agent\ndestination_vm: dom0\n",
    "destination_vm: &x dom0\n", "<<: *base\ndestination_vm: dom0\n", "{destination_vm: dom0}\n",
    "destination_vm: dom0 # commentaire\n", "destination_vm: |\n  dom0\n", "destination_path: /x\n",
    "  destination_vm: dom0\ndestination_vm: dom0\n", "destination_vm: !!str dom0\n",
    "? destination_vm\n: dom0\n", "destination_vm: dom0\npassphrase_vm: 'a-dev'\n",
    "x: *alias\ndestination_vm: dom0\n",
])
def test_profil_lu_strictement(texte):
    with pytest.raises(ValueError):
        outils.lire_profil(texte)


def test_profil_admis():
    t = ("compression: true\ndestination_path: /home/user/sauvegardes\ndestination_vm: sys-usb\ninclude:\n"
         "- vault\n- work\npassphrase_vm: vault\n")
    assert outils.lire_profil(t) == {"destination_vm": "sys-usb", "destination_path": "/home/user/sauvegardes",
                                     "passphrase_vm": "vault"}


def test_outils_reserve_a_l_executeur(tmp_path):
    import socket
    srv = outils.serveur(str(tmp_path / "o.sock"), autorises={-1}, outils=outils.Outils(lancer=None))
    import threading
    threading.Thread(target=srv.handle_request, daemon=True).start()
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(tmp_path / "o.sock"))
        proto.envoyer_json(s, {"type": "backup.list"})
        s.settimeout(5)
        try:
            assert s.recv(10) == b""  # pas l'exécuteur : fermé sans réponse
        except ConnectionResetError:
            pass
    srv.server_close()


# --- backend réel (faux qubesadmin) -----------------------------------------------

@pytest.fixture
def reel(monkeypatch):
    from test_backend_qubes_v3 import App, Dev, faux_exc
    exc = faux_exc()
    mod = types.ModuleType("qubesadmin")
    mod.exc = exc
    dp = types.ModuleType("qubesadmin.device_protocol")
    dp.Port = lambda vm, port, classe: ("port", vm.name, port, classe)
    dp.VirtualDevice = lambda port, device_id: ("dev", port, device_id)
    dp.DeviceAssignment = lambda dev, options, mode: ("assign", dev, tuple(sorted(options.items())), mode)
    for nom, m in (("qubesadmin", mod), ("qubesadmin.exc", exc), ("qubesadmin.device_protocol", dp)):
        monkeypatch.setitem(sys.modules, nom, m)
    from qaf_gate.backend_qubes import QubesBackend
    app = App()
    app.outils_msgs = []
    app.outils_rep = {"ok": True, "travail": "T-000001"}

    def outils_(msg, delai):
        app.outils_msgs.append(msg)
        return app.outils_rep
    b = QubesBackend(app=app, taches=app.taches, outils=outils_)
    return b, app, Dev


def test_reel_maj_prohibit_start_et_outils(reel):
    b, app, _ = reel
    app.domains["debian-13-xfce"].features["prohibit-start"] = "1"
    with pytest.raises(BackendError):
        b.update_run(["debian-13-xfce"], "none")
    with pytest.raises(BackendError):
        b.update_run(["work"], "none")  # AppVM
    del app.domains["debian-13-xfce"].features["prohibit-start"]
    assert b.update_run(["debian-13-xfce"], "sys") == {"travail": "T-000001", "ignorees_prohibit_start": []}
    assert app.outils_msgs[-1] == {"type": "update", "cibles": ["debian-13-xfce"], "apply": "sys"}
    app.outils_rep = {"ok": False, "raison": "un travail est déjà en cours"}
    with pytest.raises(BackendError, match="déjà en cours"):
        b.backup_run("hebdo")


def test_reel_fonctions_de_mise_a_jour_p8(reel):
    b, app, _ = reel
    app.domains["work"].features.update({"updates-available": "1", "last-update": "2026-10-20 10:00:00",
                                         "last-updates-check": "2026-10-25 10:53:17", "last-updated": "x"})
    assert b.update_status(["work"]) == [{"name": "work", "updates_available": "1",
                                          "last_update": "2026-10-20 10:00:00",
                                          "last_updates_check": "2026-10-25 10:53:17", "skip_update": None,
                                          "prohibit_start": None}]


def test_reel_copie_et_utilisateur(reel):
    b, app, _ = reel
    app.domains["work"].__dict__["default_user"] = "user"
    app.reponse = {"ok": True, "codes": [0]}
    assert b.file_copy("work", "a.txt", "sys-net", False) == {"code": 0}
    m = app.messages[-1]
    assert (m["type"], m["qube"], m["action"], m["utilisateur"], m["dest"], m["chemin"]) == (
        "vmexec", "work", "file.copy", "user", "sys-net", "a.txt")
    with pytest.raises(BackendError):
        b.file_copy("sys-net", "a.txt", "work", False)  # source arrêtée


def test_reel_attache_temporaire_lecture_seule(reel):
    b, app, Dev = reel
    app.domains["sys-usb"].devices["block"].exposes = [Dev("sys-usb", "sda", "d1")]
    attaches = []
    app.domains["work"].devices["block"].attach = attaches.append
    b.dev_attach("block", "sys-usb", "sda", "d1", "work", True)
    assert attaches == [("assign", ("dev", ("port", "sys-usb", "sda", "block"), "d1"),
                         (("read-only", "yes"),), "manual")]
    with pytest.raises(BackendError, match="n'expose plus"):
        b.dev_attach("block", "sys-usb", "sda", "autre", "work", True)


def test_reel_disque_et_tags(reel):
    b, app, _ = reel
    vol = app.domains["work"].volumes["private"]
    vol.pool, tailles = "vm-pool", []
    vol.resize = tailles.append
    app.pools["vm-pool"] = types.SimpleNamespace(size=100 * 2**30, usage=10 * 2**30)
    assert b.grow_private("work", 4096) == {"avant": 2048} and tailles == [4096 * 2**20]
    with pytest.raises(BackendError):
        b.grow_private("work", 1024)
    with pytest.raises(BackendError, match="10 %"):
        b.grow_private("work", 100 * 1024)
    assert b.tag("work", "x", "add") == {"avant": False} and "x" in app.domains["work"].tags


# --- MCP, fenêtre, packaging ----------------------------------------------------------

def test_outils_mcp_couvrent_les_nouvelles_operations():
    assert {o for o, _ in OPS_OUTILS.values()} >= set(NOUVELLES)
    noms = {o["name"] for o in OUTILS}
    assert set(OPS_OUTILS) <= noms
    assert construire_op("qube_enter", {"name": "work"}) == {"op": "enter.open", "name": "work", "minutes": 30}
    assert construire_op("updates_run", {"targets": ["t"]})["apply"] == "none"
    with pytest.raises(Exception):
        construire_op("file_copy", {"source": "a", "path": "b", "dest": "c", "argv": ["x"]})


def test_skill_cite_chaque_outil():
    texte = (RACINE / "qaf_mcp/skill/qaf/SKILL.md").read_text(encoding="utf-8")
    for nom in OPS_OUTILS:
        assert f"`{nom}`" in texte, nom


def vue(o, qubes=(("work", "blue", "utilisateur"), ("personal", "yellow", "utilisateur"), ("vault", "black", "coffre"))):
    canon = json.dumps({"version": 1, "request": "R-0001", "ops": [o]}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
    msg = {"type": "confirmer", "appelant": "sys-agent", "canonique": canon, "niveaux": [3], "decision": "confirmer",
           "jeton": "123456", "influence": False, "qubes": [list(q) for q in qubes], "expire_dans": 60,
           "texte_demande": None, "age_demande": None, "hors_portee": []}
    return ui.rediger(ui.lire_requete(msg))


@pytest.mark.parametrize("o,mots", [
    (op("enter.open"), ["entrer dans ton espace « work » pendant 30 minutes", "fournisseur"]),
    (op("file.copy"), ["copier le fichier", "vers ton espace « personal »", "Qubes te demandera aussi"]),
    (op("update.run"), ["mettre à jour le modèle « debian-13-xfce », sans redémarrer"]),
    (op("template.install", repo="community"), ["communautaire"]),
    (op("dev.attach"), ["en lecture seule", "annoncé par le périphérique"]),
    (op("dev.attach", **{"class": "mic", "backend": "dom0", "port": "mic", "device_id": "m"}), ["t'entendre"]),
    (op("job.cancel"), ["interrompre"]), (op("vm.grow_private"), ["20 Go"]),
    (op("backup.run"), ["sauvegarde « hebdo »"]), (op("file.copy", path="été/x.pdf"), ["« été/x.pdf »"]),
])
def test_phrases(o, mots):
    titre = vue(o).titre.replace(" ", " ")
    for m in mots:
        assert m in titre, (m, titre)


def test_irreversibles():
    for o in (op("template.remove"), op("vm.grow_private"), op("file.move"), op("enter.open")):
        assert vue(o).reversible.startswith("non"), o["op"]


def test_unites_qaf_gate_outils():
    u = (RACINE / "packaging/qaf-gate-outils.service").read_text()
    for ligne in ("User=qaf-outils", "SupplementaryGroups=qubes", "DeviceAllow=/dev/xen/hypercall rw",
                  "NoNewPrivileges=yes", "CapabilityBoundingSet=", "PrivateNetwork=yes", "ProtectSystem=strict",
                  "Environment=HOME=/var/lib/qaf-outils", "WorkingDirectory=/var/lib/qaf-outils",
                  "ReadWritePaths=/var/log/qubes", "RestrictAddressFamilies=AF_UNIX"):
        assert ligne in u.splitlines(), ligne
    chemins = next(ligne for ligne in u.splitlines() if ligne.startswith("InaccessiblePaths="))[18:].split()
    assert {"-/tmp/.X11-unix", "-/run/qubesd.internal.sock", "-/etc/sudoers"} <= set(chemins)
    s = (RACINE / "packaging/qaf-gate-outils.socket").read_text()
    assert "SocketGroup=qaf-exec" in s and "SocketMode=0660" in s and outils.SOCK_OUTILS in s
    from qaf_gate import backend_qubes
    assert backend_qubes.SOCK_OUTILS == outils.SOCK_OUTILS
    assert "qaf-outils" in (RACINE / "packaging/sysusers-qaf.conf").read_text()


pytestmark_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")


@pytestmark_bash
def test_install_pose_et_retire_qaf_gate_outils(tmp_path):
    import os
    env = {**os.environ, "QAF_RACINE": str(tmp_path)}
    subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env=env, check=True, capture_output=True)
    for f in ("usr/libexec/qaf/qaf-gate-outils", "usr/lib/qaf/qaf_gate/outils.py",
              "etc/systemd/system/qaf-gate-outils.service", "etc/systemd/system/qaf-gate-outils.socket"):
        assert (tmp_path / f).is_file(), f
    assert "from qaf_gate.outils import main" in (tmp_path / "usr/libexec/qaf/qaf-gate-outils").read_text()
    subprocess.run(["bash", str(RACINE / "packaging/uninstall.sh")], env=env, check=True, capture_output=True)
    assert not (tmp_path / "etc/systemd/system/qaf-gate-outils.service").exists()


@pytestmark_bash
def test_script_qaf_hand_modeles():
    script = RACINE / "tools/install-qaf-hand-modeles.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    t = script.read_text(encoding="utf-8")
    assert "--retour" in t and "sha256sum" in t and "TemplateVM" in t and "paquets-ajoutes" in t
    assert "sys-usb" not in t.replace("ni sys-usb", "")  # ne démarre ni ne vise sys-usb
    assert "dpkg --remove" in t and "rpm -e" in t  # retour sans cascade
    # Essai réel du 25/10 : apt-get avalait l'archive lue sur stdin. Elle est copiée d'abord.
    assert t.index('cat > "$ARCH"') < t.index("apt-get update") and 'tar -xzf "$ARCH"' in t
    # qubes.VMShell lit la commande sur la même entrée que l'archive : commande sur une ligne.
    assert '"$(une_ligne "$DANS_MODELE_INSTALLER")" < "$ARCH"' in t and 'exit "$ECHECS"' in t
    assert subprocess.run(["bash", str(script)], capture_output=True).returncode == 2
    assert subprocess.run(["bash", str(script), "pas-une-empreinte", "x"], capture_output=True).returncode == 2


@pytest.mark.parametrize("nom", NOUVELLES)
def test_duree_et_journal(nom):
    assert nom in proto.DUREE_MAX_OP
    assert proto.DELAI_EXECUTEUR == 2520  # la chaîne des délais ne bouge pas


# --- fuzzing des nouveaux lecteurs du TCB ----------------------------------------------

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from test_fuzz_schema import reglages  # noqa: E402

LIGNES_YAML = st.sampled_from(["destination_vm: dom0", "destination_vm: sys-agent", "passphrase_vm: vault",
                               "include:", "- vault", "<<: *a", "\"destination_vm\": x", "destination_vm: &a b",
                               "  destination_vm: c", "passphrase_text: 'x*&'", "? destination_vm", "---"])


@reglages(300)
@given(st.lists(LIGNES_YAML | st.text(max_size=40), max_size=8))
def test_fuzz_profil(lignes):
    try:
        r = outils.lire_profil("\n".join(lignes))
    except ValueError:
        return
    assert set(r) == {"destination_vm", "destination_path", "passphrase_vm"} and schema._name(r["destination_vm"], "d")
    assert sum(ligne.startswith("destination_vm") for ligne in lignes) == 1


CHAMPS_OUTILS = st.fixed_dictionaries({}, optional={
    "type": st.sampled_from(sorted(outils.MESSAGES) + ["shell", ""]), "cibles": st.lists(st.text(max_size=8)),
    "apply": st.text(max_size=5), "repo": st.sampled_from(["official", "testing", ""]), "nom": st.text(max_size=8),
    "profil": st.text(max_size=8), "travail": st.text(max_size=9), "argv": st.just(["sh"])})


@reglages(300)
@given(CHAMPS_OUTILS)
def test_fuzz_outils_aucun_argv_hors_forme(msg):
    lances = []

    def lancer(argv, **kw):
        lances.append(argv)
        return FauxProc(b"", 0)
    outils.Outils(lancer=lancer, annuler=lambda p: None, tuer=lambda *a: None).traiter(msg)
    for argv in lances:
        assert argv[0] in (outils.QVU, outils.QVT, outils.QVB)
        assert "dom0" not in argv and not any(a.startswith("--nogpg") for a in argv)
        if argv[0] == outils.QVT and "--" in argv:
            assert schema._name(argv[-1], "nom") == argv[-1]


# --- Revue défensive du diff lots 2 et 3 (25/10) --------------------------------

def test_maj_apply_sys_voit_sys_usb_jetable():
    # Legion : sys-usb est un DispVM de default-dvm ; mettre à jour le modèle de
    # default-dvm avec -r le redémarrerait (R-G), à deux niveaux de modèle.
    ctx = contexte()
    qubes = {**ctx.qubes, "sys-usb": {**ctx.qubes["sys-usb"], "klass": "DispVM", "template": "default-dvm"},
             "default-dvm": {**ctx.qubes["default-dvm"], "template": "fedora-42-xfce"}}
    ctx = dataclasses.replace(ctx, qubes=qubes)
    assert niveau(op("update.run", apply="sys", targets=["fedora-42-xfce"]), ctx=ctx) == C.N4
    assert niveau(op("update.run", apply="all", targets=["fedora-42-xfce"]), ctx=ctx) == C.N4
    assert niveau(op("update.run", targets=["fedora-42-xfce"]), ctx=ctx) == C.N2  # sans -r : rien ne redémarre


@pytest.mark.parametrize("qube,config", [
    ("sys-firewall", CONFIG),  # devenu (ou resté) S : pas de bail (V7)
    ("vault", CONFIG),  # coffre non entrable (D15)
    ("debian-13-xfce", CONFIG),  # M(S)
    ("work", dataclasses.replace(CONFIG, ne_jamais_demarrer=frozenset({"sys-usb", "work"}))),  # R-G
    ("work", dataclasses.replace(CONFIG, coffres=frozenset({"work"}))),  # devenu coffre non entrable
])
def test_bail_relu_a_chaque_plan(qube, config):
    # Un bail inscrit ne rend rien automatique si enter.open ne serait plus admis.
    ctx = dataclasses.replace(contexte(config=config), baux=frozenset({qube}))
    for nom in sorted(C.ENTRER):
        assert classer(op(nom, name=qube), ctx=ctx, mode=C.PILOTE).decision != C.AUTO, nom


def test_bail_coffre_entrable_reste_automatique():
    cfg = dataclasses.replace(CONFIG, coffres_entrables=frozenset({"vault"}))
    ctx = dataclasses.replace(contexte(config=cfg), baux=frozenset({"vault"}))
    assert classer(op("qube.exec", name="vault"), ctx=ctx, influence=True).decision == C.AUTO


def test_tags_lus_dans_include_et_ancien_format(tmp_path, monkeypatch):
    assert {"/etc/qubes/policy.d", "/run/qubes/policy.d", "/etc/qubes-rpc/policy"} <= set(executor.POLITIQUES)
    d = tmp_path / "policy.d"
    (d / "include").mkdir(parents=True)
    (d / "include" / "admin-local-rwx").write_text("@tag:projet-y @adminvm allow\n")
    ancien = tmp_path / "qubes-rpc-policy"
    ancien.mkdir()
    (ancien / "qubes.Filecopy").write_text("@tag:projet-z @anyvm ask\n")
    monkeypatch.setattr(executor, "POLITIQUES", (str(d), str(d / "include"), str(ancien)))
    assert executor._tags_politique() == {"projet-y", "projet-z"}


def test_outils_politiques_d_execution_en_lecture_seule():
    u = (RACINE / "packaging/qaf-gate-outils.service").read_text().splitlines()
    # /run/qubes entier (policy.d compris) en lecture seule : plus aucun ReadWritePaths dessus (§18).
    assert not any(ligne.startswith("ReadWritePaths=") and "/run/qubes" in ligne for ligne in u)
    assert "ProtectSystem=strict" in u and "SystemCallErrorNumber=EPERM" in u
