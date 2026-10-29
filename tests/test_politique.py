"""Politiques policy/00-qaf.policy et 00-qaf-agent-long.policy (C3, P1-1 à P1-3, AGENT-JETABLE §5.2).

Les tests d'évaluation utilisent le VRAI parseur de qubes-core-qrexec :
- s'il est importable tel quel (dom0), ou depuis QAF_QREXEC_SRC (copie du dépôt) ;
- avec 90-default.policy pris dans QAF_QUBES_ADMIN_SRC (qubes-core-admin) ou
  dans /etc/qubes/policy.d.
Sinon ils sont sautés proprement.
"""

import importlib
import os
import pathlib
import shutil
import sys

import pytest

POLITIQUE = pathlib.Path(__file__).parent.parent / "policy" / "00-qaf.policy"


def regles():
    for ligne in POLITIQUE.read_text(encoding="utf-8").splitlines():
        if ligne.strip() and not ligne.lstrip().startswith("#"):
            yield tuple(ligne.split())


LONG = POLITIQUE.parent / "00-qaf-agent-long.policy"


def test_forme_et_ordre():
    r = list(regles())
    assert all(len(x) >= 5 and x[4] in ("allow", "deny", "ask") for x in r)
    allows = [x for x in r if x[4] == "allow"]
    assert allows == [("qaf.Plan", "*", "@tag:qaf-agent", "@adminvm", "allow"),
                      ("qubes.GetDate", "*", "@tag:qaf-agent", "@default", "allow", "target=dom0"),
                      # services que 90-default permet à tout qube vers dom0 (règle n°1)
                      *[(s, "*", "@tag:qaf-agent", "dom0", "allow") for s in (
                          "qubes.WindowIconUpdater", "qubes.FeaturesRequest", "qubes.NotifyTools",
                          "qubes.NotifyUpdates", "qubes.GetRandomizedTime", "qubes.SyncAppMenus")]]
    # Liste blanche des jetables puis bloc 4 (R-4) : les quatre refus terminent le fichier.
    assert r[-4:] == [("*", "*", "@tag:qaf-agent", "@adminvm", "deny"), ("*", "*", "@tag:qaf-agent", "@anyvm", "deny"),
                      ("*", "*", "@anyvm", "@tag:qaf-agent", "deny"),
                      ("*", "*", "@anyvm", "@dispvm:qaf-agent-dvm", "deny")]
    assert all(r.index(a) < len(r) - 4 for a in allows)
    # sys-agent n'a plus aucune ligne ici (AGENT-JETABLE §11).
    assert not any("sys-agent" in x for x in r)


def test_aucun_allow_ni_ask_vers_un_jetable_d_agent():
    # R-4, pour tous les fichiers QAF livrés.
    for f in list(POLITIQUE.parent.glob("*.policy")) + list((POLITIQUE.parent.parent / "profil").glob("*.policy")):
        for ligne in f.read_text(encoding="utf-8").splitlines():
            x = ligne.split()
            if x and not x[0].startswith("#") and len(x) >= 5 and "qaf-agent" in x[3]:
                assert x[4] == "deny", (f.name, ligne)


def test_qaf_agent_cite_seulement_dans_00_qaf():
    autres = [f for f in POLITIQUE.parent.parent.glob("**/*.policy") if f.name != POLITIQUE.name
              and ".claude" not in f.parts]
    for f in autres:
        assert "qaf-agent" not in "".join(
            l for l in f.read_text(encoding="utf-8").splitlines(True) if not l.lstrip().startswith("#")), f


def test_agent_long_fichier_a_part_trie_avant():
    assert LONG.name < POLITIQUE.name  # passe devant les refus de qaf.Plan et de qaf.Request
    r = [tuple(l.split()) for l in LONG.read_text(encoding="utf-8").splitlines()
         if l.strip() and not l.lstrip().startswith("#")]
    assert ("qaf.Plan", "*", "sys-agent", "@adminvm", "allow") in r
    assert r[-2:] == [("*", "*", "sys-agent", "@adminvm", "deny"), ("*", "*", "sys-agent", "@anyvm", "deny")]
    assert all("sys-agent" in x for x in r) and not any("@tag:" in " ".join(x) for x in r)


USAGE = POLITIQUE.parent / "30-qaf-usage.policy"


def test_politique_du_panneau_de_l_agent():
    # docs/AGENT-PANNEAU.md §7.2 : allow seulement de @adminvm vers sys-agent, refus partout ailleurs ;
    # triée après 00-qaf.policy (ne passe jamais devant la liste blanche).
    assert USAGE.name > POLITIQUE.name and USAGE.name < "90"
    r = [tuple(l.split()) for l in USAGE.read_text(encoding="utf-8").splitlines()
         if l.strip() and not l.lstrip().startswith("#")]
    services = ("qaf.Usage", "qaf.Connexion", "qaf.Console")
    assert {x for x in r if x[4] == "allow"} == {(s, "*", "@adminvm", "sys-agent", "allow") for s in services}
    assert {x for x in r if x[4] == "deny"} == {(s, "*", "@anyvm", "@anyvm", "deny") for s in services}
    assert len(r) == 6 and not any("@tag:" in " ".join(x) for x in r)


def test_nom_trie_en_tete():
    # Trié par nom avec les fichiers de Qubes (parser.py) : doit passer avant
    # 30-user.policy, 35-compat.policy, 50-config-*, 80-whonix, 90-default…
    assert POLITIQUE.name < "05"


def _parseur():
    src = os.environ.get("QAF_QREXEC_SRC")
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        return importlib.import_module("qrexec.policy.parser"), importlib.import_module("qrexec.exc")
    except ImportError:
        return None, None


def _defaut():
    admin = os.environ.get("QAF_QUBES_ADMIN_SRC")
    for p in ([pathlib.Path(admin) / "qubes-rpc-policy" / "90-default.policy"] if admin else []) + [
            pathlib.Path("/etc/qubes/policy.d/90-default.policy")]:
        if p.is_file():
            return p
    return None


PARSER, EXC = _parseur()
DEFAUT = _defaut()
avec_qrexec = pytest.mark.skipif(PARSER is None or DEFAUT is None,
                                 reason="qubes-core-qrexec non importable ou 90-default.policy introuvable")


def _dom(t, dvm=None, tpl=False):
    return {"tags": [], "type": t, "template_for_dispvms": tpl, "default_dispvm": dvm,
            "icon": "red", "label": "red", "guivm": None, "power_state": "Running", "uuid": "x"}


INFO = {"domains": {"dom0": _dom("AdminVM"), "sys-agent": _dom("AppVM", "default-dvm"),
                    "work": _dom("AppVM", "default-dvm"), "default-dvm": _dom("AppVM", None, True),
                    "qaf-agent-dvm": _dom("AppVM", None, True),
                    "disp42": {**_dom("DispVM", None), "tags": ["qaf-agent"], "template": "qaf-agent-dvm"},
                    "piege": _dom("AppVM", "qaf-agent-dvm")}}
AGENT = "disp42"


@pytest.fixture(params=[False, True], ids=["sans-agent-long", "agent-long"])
def evaluer(tmp_path, request):
    shutil.copy(POLITIQUE, tmp_path / POLITIQUE.name)
    if request.param:
        shutil.copy(LONG, tmp_path / LONG.name)
    shutil.copy(DEFAUT, tmp_path / "90-default.policy")
    pol = PARSER.FilePolicy(policy_path=tmp_path)

    def evaluer(svc, src, cible, arg="+"):
        """Cible effective si autorisé, None si refusé."""
        try:
            r = pol.evaluate(PARSER.Request(svc, arg, src, cible, system_info=INFO))
        except EXC.AccessDenied:
            return None
        return r.target if isinstance(r, PARSER.AllowResolution) else None

    return evaluer


@avec_qrexec
def test_qaf_plan_seul_vers_dom0(evaluer):
    assert evaluer("qaf.Plan", AGENT, "dom0") == "dom0"
    assert evaluer("qaf.Plan", AGENT, "@adminvm") is not None
    assert evaluer("qaf.Plan", "work", "dom0") is None
    for src in (AGENT, "sys-agent"):
        for svc in ("admin.vm.List", "admin.vm.property.Set", "admin.vm.Create.AppVM", "policy.Replace"):
            for cible in ("dom0", "@adminvm", "uuid:00000000-0000-0000-0000-000000000000"):
                assert evaluer(svc, src, cible) is None, (svc, src, cible)


@avec_qrexec
def test_sys_agent_seulement_avec_l_agent_long(evaluer, request):
    long_ = request.node.callspec.params.get("evaluer")
    assert (evaluer("qaf.Plan", "sys-agent", "dom0") == "dom0") is bool(long_)
    assert (evaluer("qaf.Request", "dom0", "sys-agent") == "sys-agent") is bool(long_)


@avec_qrexec
def test_default_redirige_vers_dom0_ferme(evaluer):
    # P1-1 : 90-default redirige ces appels vers dom0 par target=dom0.
    for svc in ("qubes.Notifications", "admin.vm.List", "qaf.Plan", "qubes.Filecopy"):
        assert evaluer(svc, AGENT, "@default") is None, svc
    assert evaluer("qubes.Notifications", "work", "@default") == "dom0"  # autres qubes : inchangé


@avec_qrexec
def test_getdate_exception_explicite(evaluer):
    assert evaluer("qubes.GetDate", AGENT, "@default", "+nanoseconds") == "dom0"
    assert evaluer("qubes.GetDate", AGENT, "work") is None


@avec_qrexec
def test_aucune_disposable_ni_qube_nomme(evaluer):
    # P1-2 : qubes.GetImageRGBA vers @dispvm était resté ouvert.
    for svc in ("qubes.VMShell", "qubes.VMExec", "qubes.VMExecGUI", "qubes.OpenURL", "qubes.OpenInVM",
                "qubes.StartApp", "qubes.GetImageRGBA", "qubes.VMRootShell"):
        for cible in ("@dispvm", "@dispvm:default-dvm", "work", "default-dvm"):
            assert evaluer(svc, AGENT, cible) is None, (svc, cible)
    for svc in ("qubes.Filecopy", "qubes.ClipboardPaste", "qubes.ConnectTCP", "qubes.UpdatesProxy"):
        assert evaluer(svc, AGENT, "work") is None, svc
    assert evaluer("qubes.VMShell", "work", "@dispvm") is not None  # politique Qubes inchangée pour les autres


@avec_qrexec
def test_rien_vers_un_jetable_d_agent(evaluer):
    # R-4 : ni copie (ask de 90-default), ni shell, ni @dispvm d'un qube réglé sur qaf-agent-dvm (R-3).
    for svc in ("qubes.Filecopy", "qubes.VMShell", "qubes.ClipboardPaste", "qubes.OpenInVM"):
        assert evaluer(svc, "work", AGENT) is None, svc
        assert evaluer(svc, "piege", "@dispvm") is None, svc
        assert evaluer(svc, "work", "@dispvm:qaf-agent-dvm") is None, svc


@avec_qrexec
def test_qaf_request_aucun_qube_ne_peut_l_appeler(evaluer):
    for src in ("work", "sys-agent", "default-dvm", AGENT):
        for cible in ("sys-agent", "@default", "@dispvm", "dom0", AGENT):
            assert evaluer("qaf.Request", src, cible) is None, (src, cible)


def test_services_qaf_hand_refus_explicites():
    # H5 : qaf.Task, qaf.Exec, qaf.Screen, qaf.Input refusés explicitement à tout qube.
    r = list(regles())
    for svc in ("qaf.Task", "qaf.Exec", "qaf.Screen", "qaf.Input"):
        assert (svc, "*", "@anyvm", "@anyvm", "deny") in r, svc
        assert not any(x[0] == svc and x[4] != "deny" for x in r), svc


@avec_qrexec
def test_services_qaf_hand_aucun_qube_ne_peut_les_appeler(evaluer):
    for svc in ("qaf.Task", "qaf.Exec", "qaf.Screen", "qaf.Input"):
        for src in ("work", "sys-agent", "disp1234"):
            for cible in ("work", "@dispvm", "@default", "disp4242"):
                assert evaluer(svc, src, cible) is None, (svc, src, cible)
