"""OPS-v3, lot 0 et lot 1 : schéma fermé des nouvelles opérations, classes de
cible (P, C, S, M, A, T) et règles de revue R-A à R-G (docs/OPS-v3.md §3.2 bis,
constats V1 à V15 du §15)."""

import dataclasses
import json

import pytest

from qaf_gate import classify as C
from qaf_gate import schema
from qaf_gate.backend import QubeInfo
from qaf_gate.schema import PlanError, canonicalize, parse_plan
from backend_sim import SimBackend, _q, qubes_par_defaut
from exemples_ops import EXEMPLES, op


def plan(*ops):
    return parse_plan(json.dumps({"version": 1, "request": "R-0001", "ops": list(ops)}).encode())


# --- schéma ----------------------------------------------------------------

@pytest.mark.parametrize("nom", sorted(EXEMPLES))
def test_exemple_valide_et_canonique(nom):
    p = plan(op(nom))
    assert parse_plan(canonicalize(p)) == p


@pytest.mark.parametrize("paquets", [["git-"], ["foo+"], ["-x"], ["a/b"], ["a=1"], ["*"], ["git", "git"], [],
                                     ["a" * 64], ["git"] * 9, ["vim:amd64"], ["x@1"], "git"])
def test_paquets_refuses(paquets):
    # V11 : « nom- » retirerait le paquet sous apt-get ; ni version, ni joker, ni fichier.
    with pytest.raises(PlanError):
        plan(op("pkg.install", packages=paquets))


def test_paquets_admis():
    assert plan(op("pkg.install", packages=["g++", "libstdc++6", "x", "python3.12", "gcc-c++"]))


@pytest.mark.parametrize("regle", [
    {"action": "accept", "host": "exemple.org", "port": "443"},  # clé inconnue
    {"action": "accept", "dns": True, "host": "x.org"},
    {"action": "accept", "dns": False},
    {"action": "accept", "ports": "443"},  # ports sans tcp/udp
    {"action": "accept", "proto": "icmp", "ports": "1"},
    {"action": "accept", "proto": "tcp", "ports": "0"},
    {"action": "accept", "proto": "tcp", "ports": "70000"},
    {"action": "accept", "proto": "tcp", "ports": "443-80"},
    {"action": "accept", "host": "10.0.0.1/8"},  # strict=True : bits d'hôte posés
    {"action": "accept", "host": "-x.org"},
    {"action": "accept", "host": "x.org."},
    {"action": "allow"},
    {"action": "accept", "proto": "TCP"},
    ["accept"],
])
def test_regle_pf_fermee(regle):
    with pytest.raises(PlanError):
        plan(op("fw.add", rule=regle))


@pytest.mark.parametrize("regle", [
    {"action": "drop"}, {"action": "accept", "dns": True}, {"action": "accept", "host": "2001:db8::/32"},
    {"action": "drop", "host": "10.42.0.0/24", "proto": "udp", "ports": "53-54"},
])
def test_regle_pf_admise(regle):
    assert plan(op("fw.add", rule=regle))


@pytest.mark.parametrize("prop,valeur", [
    ("memory", "4096"), ("memory", True), ("memory", 100), ("label", "pink"), ("autostart", 1),
    ("default_dispvm", 3), ("default_dispvm", "dom0 "), ("keyboard_layout", "fr;rm"),
    ("netvm", "sys-net"), ("kernel", "x"), ("template", "x"), ("guivm", "dom0"), ("virt_mode", "pv"),
])
def test_valeur_choisie_par_prop(prop, valeur):
    # R-B : le validateur de value est celui de prop ; prop hors liste blanche = refus.
    with pytest.raises(PlanError):
        plan(op("vm.set_prop", prop=prop, value=valeur))


def test_value_de_type_qube_compte_comme_qube():
    # R-B (V2) : value entre dans Op.qubes() quand la propriété désigne un qube.
    assert plan(op("vm.set_prop", prop="default_dispvm", value="default-dvm")).ops[0].qubes() == (
        "work", "default-dvm")
    assert plan(op("prefs.set", prop="default_template", value="fedora-42-xfce")).ops[0].qubes() == (
        "fedora-42-xfce",)
    assert plan(op("vm.set_prop")).ops[0].qubes() == ("work",)
    assert plan(op("update.status")).ops[0].qubes() == ("work", "debian-13-xfce")


def test_une_seule_operation_lourde_par_plan():
    with pytest.raises(PlanError):
        plan(op("qube.exec"), op("qube.screenshot"))
    with pytest.raises(PlanError):
        plan(op("pkg.install"), op("dev.list"))
    assert plan(op("qube.exec"), op("qube.type"), op("qube.key"))


@pytest.mark.parametrize("champs", [{"keys": "ctrl l"}, {"window": "0x1G"}, {"window": "root"},
                                    {"text": "a\x1bb"}, {"x": -1}, {"button": 4}])
def test_champs_fermes_entrer(champs):
    nom = "qube.type" if "text" in champs else "qube.mouse" if {"x", "button"} & set(champs) else "qube.key"
    with pytest.raises(PlanError):
        plan(op(nom, **champs))


def test_argv_borne():
    with pytest.raises(PlanError):
        plan(op("qube.exec", argv=["a\0b"]))
    with pytest.raises(PlanError):
        plan(op("qube.exec", argv=["x" * 4097]))
    with pytest.raises(PlanError):
        plan(op("qube.exec", argv=[""]))


# --- contexte réaliste --------------------------------------------------------

def installation():
    """Qubes 4.3 type + sys-agent, son modèle, l'infrastructure des jetables, le
    qube Salt, qube-admin, et des qubes de l'agent (registre)."""
    qs = qubes_par_defaut() + [
        _q("tpl-agent", "TemplateVM", "black"),
        _q("sys-agent", "AppVM", "purple", "tpl-agent", "sys-firewall", state="Running",
           default_dispvm="agent-dvm", default_dispvm_herite=False),
        _q("agent-dvm", "AppVM", "gray", "tpl-agent", None, template_for_dispvms=True),
        _q("tpl-qaf-hand", "TemplateVM", "black"),
        _q("qaf-dvm", "AppVM", "red", "tpl-qaf-hand", None, template_for_dispvms=True),
        _q("fedora-42-mgmt", "TemplateVM", "black"),
        _q("default-mgmt-dvm", "AppVM", "black", "fedora-42-mgmt", None, template_for_dispvms=True),
        _q("qube-admin", "StandaloneVM", "orange", None, "sys-firewall", state="Running"),
        _q("tpl-a", "TemplateVM", "gray"),  # modèle installé par l'agent
        _q("a-dev", "AppVM", "gray", "tpl-a", None, state="Running"),
        _q("a-net", "AppVM", "gray", "debian-13-xfce", None, True),
        _q("a-dvm", "AppVM", "gray", "debian-13-xfce", None, template_for_dispvms=True),
        _q("disp9", "DispVM", "red", "qaf-dvm", None, state="Running"),
    ]
    sim = SimBackend(qs)
    sim.globaux["management_dispvm"] = "default-mgmt-dvm"
    for n in list(sim.qubes):
        if n not in ("dom0",):
            sim.qubes[n] = dataclasses.replace(sim.qubes[n], management_dispvm="default-mgmt-dvm")
    return sim


AGENT = {"tpl-a", "a-dev", "a-net", "a-dvm", "disp9"}
CONFIG = C.Config(admin_qubes=frozenset({"qube-admin"}), ne_jamais_demarrer=frozenset({"sys-usb"}))


def contexte(sim=None, config=CONFIG):
    sim = sim or installation()
    return C.Contexte({q.name: dataclasses.asdict(q) for q in sim.list_qubes()}, sim.prefs(), config)


def classer(*ops, ctx=None, influence=False, mode=C.NORMAL, agent=AGENT, portee=None):
    ctx = ctx or contexte()
    return C.classer(plan(*ops), agent_qubes=set(agent), existing=set(ctx.qubes), influence=influence,
                     mode=mode, ctx=ctx, portee=portee)


def niveau(*ops, **kw):
    return classer(*ops, **kw).niveaux[-1]


def test_classes_de_cible():
    ctx, a = contexte(), AGENT
    attendu = {"dom0": C.P, "sys-agent": C.P, "tpl-agent": C.P, "agent-dvm": C.P, "qaf-dvm": C.P,
               "tpl-qaf-hand": C.P, "default-mgmt-dvm": C.P, "fedora-42-mgmt": C.P, "qube-admin": C.P,
               "vault": C.C, "sys-net": C.S, "sys-firewall": C.S, "sys-usb": C.S, "a-net": C.S,
               "debian-13-xfce": C.M, "default-dvm": C.M, "a-dvm": C.A, "a-dev": C.A, "disp9": C.A,
               "tpl-a": C.A, "work": C.T, "personal": C.T}
    assert {n: ctx.classe(n, a) for n in attendu} == attendu


# V1 / R-A : un coffre nommé dans n'importe quel champ qube est toujours confirmé (ou refusé).
def _substitutions(qube):
    for nom in sorted(EXEMPLES):
        if nom in C.LECTURES:
            continue
        base = op(nom)
        champs = [k for k, _ in plan(base).ops[0].champs_qube()]
        if nom == "vm.set_prop":
            yield {**base, "prop": "default_dispvm", "value": qube}
        if nom == "prefs.set":
            yield {**base, "prop": "default_template", "value": qube}
        for k in champs:
            yield {**base, k: [qube] if isinstance(base[k], list) else qube}


@pytest.mark.parametrize("o", list(_substitutions("vault")), ids=lambda o: o["op"])
def test_coffre_dans_chaque_champ(o):
    for mode in (C.NORMAL, C.PILOTE):
        assert classer(o, mode=mode).decision != C.AUTO, o


@pytest.mark.parametrize("o", list(_substitutions("sys-agent")), ids=lambda o: o["op"])
def test_protege_dans_chaque_champ(o):
    assert classer(o).decision == C.REFUSER, o


def test_modele_d_un_protege_comme_valeur_de_modele():
    # Poser comme modèle le modèle d'un qube P ne touche pas ce qube : admis.
    assert niveau(op("vm.create", template="tpl-agent")) == C.N1
    assert niveau(op("vm.set_template", template="fedora-42-mgmt")) == C.N3
    # Le noyau P, lui, jamais : ni comme modèle, ni comme valeur.
    assert niveau(op("vm.create", template="tpl-qaf-hand")) == C.N4
    assert niveau(op("vm.set_netvm", netvm="sys-agent")) == C.N4


def test_portee_inclut_value():
    # V2 : default_dispvm non nommé par la demande -> hors portée.
    cl = classer(op("vm.set_prop", prop="default_dispvm", value="default-dvm"), portee={"work"})
    assert cl.hors_portee == (0,) and cl.decision == C.CONFIRMER_RENFORCE


# V3 / R-C : contagion.
def test_modele_de_l_agent_devient_m_des_qu_un_qube_de_utilisateur_en_depend():
    assert niveau(op("pkg.install", name="tpl-a")) == C.N1
    sim = installation()
    sim.qubes["work"] = dataclasses.replace(sim.qubes["work"], template="tpl-a")
    ctx = contexte(sim)
    assert ctx.classe("tpl-a", AGENT) == C.M
    assert niveau(op("pkg.install", name="tpl-a"), ctx=ctx) == C.N2
    assert niveau(op("qube.exec", name="tpl-a"), ctx=ctx) == C.N3


def test_qube_de_l_agent_devenu_jetable_par_defaut_n_est_plus_a():
    sim = installation()
    sim.qubes["work"] = dataclasses.replace(sim.qubes["work"], default_dispvm="a-dvm")
    assert contexte(sim).classe("a-dvm", AGENT) == C.M


# R-D : faire dépendre l'utilisateur de l'agent.
@pytest.mark.parametrize("o", [
    op("vm.set_template", template="tpl-a"),
    op("vm.set_netvm", netvm="a-net"),
    op("vm.set_prop", prop="default_dispvm", value="a-dvm"),
    op("prefs.set", prop="default_template", value="tpl-a"),
])
def test_dependance_envers_l_agent_renforcee_meme_en_pilote(o):
    cl = classer(o, mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER_RENFORCE


@pytest.mark.parametrize("o", [op("vm.set_template", template="vault"), op("vm.create", template="vault"),
                               op("vm.set_netvm", netvm="vault"), op("prefs.set", value="vault"),
                               op("vm.set_prop", prop="default_dispvm", value="vault")])
def test_coffre_jamais_pose_comme_valeur(o):
    assert niveau(o) == C.N4


def test_dependance_d_un_coffre_envers_l_agent_interdite():
    assert niveau(op("vm.set_template", name="vault", template="tpl-a")) == C.N4
    assert niveau(op("vm.set_prop", name="vault", prop="default_dispvm", value="a-dvm")) == C.N4
    assert niveau(op("vm.set_prop", name="vault", prop="default_dispvm", value="default-dvm")) == C.N4  # réseau


def test_l_agent_sur_ses_propres_qubes_reste_auto():
    assert classer(op("vm.set_template", name="a-dev", template="tpl-a")).decision == C.AUTO


# V4 : qube Salt, son modèle, admin_qubes.
@pytest.mark.parametrize("o", [op("qube.exec", name="qube-admin"), op("vm.start", name="qube-admin"),
                               op("pkg.install", name="fedora-42-mgmt"),
                               op("vm.set_prop", name="default-mgmt-dvm", prop="memory", value=512)])
def test_qubes_d_administration_proteges(o):
    assert niveau(o) == C.N4


# V5 / R-E : valeur globale héritée.
def test_jetable_global_herite_par_un_protege():
    sim = installation()
    # dom0 hérite (AdminVM a default_dispvm) : refus.
    sim.qubes["dom0"] = dataclasses.replace(sim.qubes["dom0"], default_dispvm_herite=True)
    assert niveau(op("prefs.set", prop="default_dispvm", value="default-dvm"), ctx=contexte(sim)) == C.N4
    # Si sys-agent héritait du réglage global (C3) : refus aussi.
    sim = installation()
    sim.qubes["sys-agent"] = dataclasses.replace(sim.qubes["sys-agent"], default_dispvm_herite=True)
    assert niveau(op("prefs.set", prop="default_dispvm", value="default-dvm"), ctx=contexte(sim)) == C.N4


def test_jetable_global_sans_heritier_protege():
    # Sur une installation réelle, qaf-dvm, tpl-agent… héritent aussi du réglage
    # global : l'opération n'est permise que si chaque qube P (et le coffre) a
    # sa propre valeur.
    sim, ctx = installation(), contexte()
    for n in [*ctx.proteges, "vault"]:
        if n in sim.qubes:
            sim.qubes[n] = dataclasses.replace(sim.qubes[n], default_dispvm_herite=False)
    cl = classer(op("prefs.set", prop="default_dispvm", value="default-dvm"), ctx=contexte(sim))
    assert cl.niveaux == (C.N3,)
    # Un coffre qui hérite : toujours confirmé ; vers un jetable avec réseau : refus.
    sim.qubes["vault"] = dataclasses.replace(sim.qubes["vault"], default_dispvm_herite=True)
    assert niveau(op("prefs.set", prop="default_dispvm", value="default-dvm"), ctx=contexte(sim)) == C.N4
    cl = classer(op("prefs.set", prop="default_dispvm", value=None), ctx=contexte(sim), mode=C.PILOTE)
    assert cl.decision == C.CONFIRMER


# V6 : entrer chez l'utilisateur, toujours confirmé ; chez l'agent, automatique.
@pytest.mark.parametrize("nom", ["qube.exec", "qube.windows", "qube.screenshot", "qube.type", "qube.key",
                                 "qube.mouse", "qube.scroll"])
def test_entrer_chez_utilisateur_toujours_confirme(nom):
    assert classer(op(nom), mode=C.PILOTE).decision == C.CONFIRMER
    assert classer(op(nom, name="disp9"), influence=True).decision == C.AUTO
    assert classer(op(nom, name="vault"), mode=C.PILOTE).decision == C.REFUSER


def test_entrer_dans_un_modele_de_coffre_toujours_confirme():
    # debian-13-xfce est le modèle de vault : M(C).
    cl = classer(op("pkg.install", name="debian-13-xfce"), mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER
    assert niveau(op("pkg.install", name="fedora-42-xfce")) == C.N2  # modèle de work seulement


# V12 / R-F : retirer un rôle dont d'autres dépendent.
def test_retirer_un_role_utilise():
    assert niveau(op("vm.set_prop", name="sys-firewall", prop="provides_network", value=False)) == C.N4
    assert niveau(op("vm.set_prop", name="a-net", prop="provides_network", value=False)) == C.N3  # S, sans client
    assert niveau(op("vm.set_prop", name="default-dvm", prop="template_for_dispvms", value=False)) == C.N4
    assert niveau(op("vm.set_prop", name="qaf-dvm", prop="template_for_dispvms", value=False)) == C.N4  # P
    assert niveau(op("vm.set_prop", name="a-dvm", prop="template_for_dispvms", value=False)) == C.N2
    assert niveau(op("vm.set_prop", name="vault", prop="template_for_dispvms", value=True)) == C.N4
    assert niveau(op("vm.reset_prop", name="sys-firewall", prop="provides_network")) == C.N4


# V14 / R-G : ne_jamais_demarrer.
@pytest.mark.parametrize("o", [op("vm.start", name="sys-usb"), op("vm.restart", name="sys-usb"),
                               op("vm.set_prop", name="sys-usb", prop="autostart", value=True)])
def test_ne_jamais_demarrer(o):
    assert niveau(o) == C.N4
    assert niveau(o, ctx=contexte(config=C.Config(admin_qubes=frozenset({"qube-admin"})))) < C.N4


def test_demarrer_un_qube_qui_demarrerait_sys_usb():
    sim = installation()
    sim.qubes["personal"] = dataclasses.replace(sim.qubes["personal"], netvm="sys-usb")
    assert niveau(op("vm.start", name="personal"), ctx=contexte(sim)) == C.N4
    assert niveau(op("vm.set_prop", name="sys-usb", prop="autostart", value=False)) == C.N3


# C15 et niveaux de base.
@pytest.mark.parametrize("o,n", [
    (op("vm.shutdown", name="sys-net"), C.N3),
    (op("vm.start", name="sys-net"), C.N2),
    (op("vm.shutdown", name="a-dev"), C.N1),
    (op("vm.set_prop", prop="label", value="black"), C.N3),
    (op("vm.set_prop", prop="memory", value=1024), C.N2),
    (op("vm.set_prop", name="a-dev", prop="label", value="red"), C.N1),
    (op("vm.set_prop", prop="include_in_backups", value=False), C.N3),
    (op("vm.set_prop", prop="include_in_backups", value=True), C.N1),
    (op("vm.set_prop", name="vault", prop="include_in_backups", value=False), C.N4),
    (op("vm.set_prop", name="sys-net", prop="memory", value=1024), C.N3),
    (op("vm.reset_prop", prop="include_in_backups"), C.N3),
    (op("vm.set_service"), C.N2),
    (op("vm.set_service", name="sys-firewall"), C.N3),
    (op("vm.set_template"), C.N3),
    (op("vm.restart"), C.N2),
    (op("vm.restart", name="sys-firewall"), C.N3),
    (op("pkg.install", name="work"), C.N2),
    (op("dispvm.create"), C.N1),
    (op("dispvm.create", template="default-dvm"), C.N4),  # hors liste blanche
    (op("fw.add"), C.N3),  # ajouter un accept chez l'utilisateur
    (op("fw.add", rule={"action": "drop"}), C.N2),
    (op("fw.remove"), C.N3),  # retirer un drop ouvre
    (op("fw.remove", rule={"action": "accept"}), C.N2),
    (op("fw.add", name="a-dev", rule={"action": "drop"}), C.N1),
    (op("fw.add", name="sys-firewall", rule={"action": "drop"}), C.N3),
    (op("prefs.set"), C.N2),
    (op("prefs.set", prop="check_updates_vm", value=False), C.N2),
    (op("vm.set_netvm", name="sys-firewall", netvm=None), C.N3),
] + [(op(n), C.N0) for n in sorted(C.LECTURES)])
def test_niveaux(o, n):
    assert niveau(o) == n


def test_coffre_toujours_confirme_en_pilote():
    cl = classer(op("fw.add", name="vault", rule={"action": "drop"}), mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER


def test_qube_cree_dans_le_plan_compte_comme_agent():
    cl = classer(op("vm.create"), op("vm.set_prop", name="neuf", prop="label", value="red"))
    assert cl.niveaux == (C.N1, C.N1)


def test_config_fermee(tmp_path):
    f = tmp_path / "portier.json"
    f.write_text('{"coffres": ["vault", "loisirs"], "ne_jamais_demarrer": ["sys-usb"]}')
    c = C.lire_config(str(f))
    assert c.coffres == {"vault", "loisirs"} and c.ne_jamais_demarrer == {"sys-usb"} and c.admin_qubes == set()
    for mauvais in ('{"coffre": ["vault"]}', '{"coffres": "vault"}', '{"coffres": ["a b"]}', "[]"):
        f.write_text(mauvais)
        with pytest.raises((ValueError, PlanError)):
            C.lire_config(str(f))
    assert C.lire_config(str(tmp_path / "absent.json")) == C.Config()


def test_config_livree_valide():
    from pathlib import Path
    c = C.lire_config(str(Path(__file__).resolve().parent.parent / "packaging" / "portier.json"))
    # Produit livré à tous : ni sys-usb (profil matériel du Legion, qaf-installer)
    # ni qube d'administration (admin_qubes vide).
    assert not c.ne_jamais_demarrer and not c.admin_qubes and "vault" in c.coffres


def test_qubeinfo_compatible():
    # Les champs ajoutés ont une valeur par défaut : les anciens appels marchent.
    q = QubeInfo("x", "u", "AppVM", "Halted", "red", None, None, False)
    assert q.default_dispvm_herite and q.default_dispvm is None


# Revue du 25/10 : modèle d'un qube système (M(S)) = N3, même pour son contenu.
def test_modele_d_un_qube_systeme_n3():
    sim = installation()
    sim.qubes["tpl-net"] = _q("tpl-net", "TemplateVM", "black")
    sim.qubes["sys-net"] = dataclasses.replace(sim.qubes["sys-net"], template="tpl-net")
    ctx = contexte(sim)
    assert ctx.classe("tpl-net", AGENT) == C.M
    assert niveau(op("pkg.install", name="tpl-net"), ctx=ctx) == C.N3
    assert niveau(op("vm.set_service", name="tpl-net", service="cups", state="on"), ctx=ctx) == C.N3
    assert niveau(op("vm.start", name="tpl-net"), ctx=ctx) == C.N2  # démarrer reste N2
    assert niveau(op("pkg.install", name="fedora-42-xfce"), ctx=ctx) == C.N2  # modèle de work seulement


# R-G : le démarrage automatique d'un qube démarre aussi sa netvm.
def test_autostart_d_un_qube_dont_la_netvm_est_interdite():
    sim = installation()
    sim.qubes["personal"] = dataclasses.replace(sim.qubes["personal"], netvm="sys-usb")
    assert niveau(op("vm.set_prop", name="personal", prop="autostart", value=True), ctx=contexte(sim)) == C.N4
    assert niveau(op("vm.set_prop", name="work", prop="autostart", value=True)) == C.N2
