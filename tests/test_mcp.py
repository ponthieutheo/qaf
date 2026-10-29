import io
import json

import pytest

from qaf_gate.schema import parse_plan
from qaf_mcp.server import FakeTransport, QrexecTransport, Serveur, rendre_reponse


def appel(serveur, nom, args=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": nom}}
    if args is not None:
        msg["params"]["arguments"] = args
    return serveur.traiter(msg)


@pytest.fixture
def demande():
    return {"QAF_REQUEST": "R-0142"}


def srv(transport, environ, **kw):
    return Serveur(transport, environ, session="S-0123456789abcdef", **kw)


def test_initialize_et_liste(demande):
    s = srv(FakeTransport(), demande)
    r = s.traiter({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "t", "version": "1"}}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in r["result"]["capabilities"]
    assert s.traiter({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    noms = [o["name"] for o in s.traiter({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
    assert noms[:9] == ["qubes_list", "qube_info", "qube_create", "template_clone", "qube_start", "qube_stop",
                        "qube_remove", "qube_set_network", "disposable_task"]
    assert len(noms) == 9 + 23 + 18  # OPS-v3 : lots 1, 2 et 3, plus template_clone (PARCOURS-1)


def test_version_inconnue_renvoie_la_notre(demande):
    r = srv(FakeTransport(), demande).traiter(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == "2025-11-25"


CAS = [
    ("qubes_list", {}, {"op": "vm.list"}),
    ("qube_info", {"name": "work"}, {"op": "vm.info", "name": "work"}),
    ("qube_create", {"name": "dev", "template": "debian-13"},
     {"op": "vm.create", "name": "dev", "template": "debian-13", "label": "gray"}),
    ("qube_create", {"name": "dev", "template": "debian-13", "label": "red"},
     {"op": "vm.create", "name": "dev", "template": "debian-13", "label": "red"}),
    ("qube_start", {"name": "dev"}, {"op": "vm.start", "name": "dev"}),
    ("qube_stop", {"name": "dev"}, {"op": "vm.shutdown", "name": "dev"}),
    ("qube_remove", {"name": "dev"}, {"op": "vm.remove", "name": "dev"}),
    ("qube_set_network", {"name": "dev", "netvm": "sys-firewall"},
     {"op": "vm.set_netvm", "name": "dev", "netvm": "sys-firewall"}),
    ("qube_set_network", {"name": "dev", "netvm": None}, {"op": "vm.set_netvm", "name": "dev", "netvm": None}),
    ("disposable_task", {"task": "ouvre exemple.fr"}, {"op": "dispvm.run_task", "task": "ouvre exemple.fr"}),
    ("disposable_task", {"task": "x", "template": "default-dvm"},
     {"op": "dispvm.run_task", "task": "x", "template": "default-dvm"}),
    ("template_clone", {"name": "tpl-dev", "source": "debian-13-xfce"},
     {"op": "vm.clone", "name": "tpl-dev", "source": "debian-13-xfce", "label": "gray"}),
]


@pytest.mark.parametrize("nom,args,op", CAS)
def test_outils_construisent_un_plan_valide(nom, args, op, demande):
    t = FakeTransport()
    r = appel(srv(t, demande), nom, args)
    assert r["result"]["isError"] is False
    (plan,) = t.plans
    assert plan == {"version": 1, "request": "R-0142", "session": "S-0123456789abcdef", "ops": [op]}
    parse_plan(json.dumps(plan).encode())  # le Doorman l'accepterait


INVALIDES = [
    ("qube_info", {}),
    ("qube_info", {"name": "work", "x": 1}),
    ("qube_info", {"name": "1abc"}),
    ("qube_info", {"name": "dom0-dm"}),
    ("qube_create", {"name": "dev", "template": "t", "label": "rose"}),
    ("qube_create", {"name": "dev", "template": "t", "netvm": "sys-net"}),  # jamais de réseau à la création
    ("qube_set_network", {"name": "dev"}),
    ("disposable_task", {"task": ""}),
    ("disposable_task", {"task": "a" * 1001}),
    ("disposable_task", {"task": "abc‮"}),
    ("qubes_list", {"tout": True}),
    ("qubes_list", "pas un objet"),
]


@pytest.mark.parametrize("nom,args", INVALIDES)
def test_arguments_invalides_rien_n_est_envoye(nom, args, demande):
    t = FakeTransport()
    r = appel(srv(t, demande), nom, args)
    assert r["result"]["isError"] is True
    assert r["result"]["content"][0]["text"].startswith("statut: plan_invalide")
    assert t.plans == []


def test_outil_inconnu_erreur_protocole(demande):
    r = appel(srv(FakeTransport(), demande), "qvm_run", {"cmd": "id"})
    assert r["error"]["code"] == -32602


def test_sans_demande_r0000(tmp_path):
    # PARCOURS-1 M3 : plus de fichier commun ; sans QAF_REQUEST ici ni chez le parent : R-0000.
    t = FakeTransport()
    appel(srv(t, {}, parent=str(tmp_path / "absent")), "qubes_list", {})
    assert t.plans[0]["request"] == "R-0000"


def test_demande_lue_chez_le_parent(tmp_path):
    # claude peut ne pas transmettre son environnement au serveur MCP : celui du parent fait foi.
    f = tmp_path / "environ"
    f.write_bytes(b"HOME=/home/user\0QAF_REQUEST=R-505548\0PATH=/usr/bin\0")
    t = FakeTransport()
    appel(srv(t, {}, parent=str(f)), "qubes_list", {})
    assert t.plans[0]["request"] == "R-505548"


def test_session_tiree_une_fois_par_serveur():
    t = FakeTransport()
    s = Serveur(t, {"QAF_REQUEST": "R-0142"})
    appel(s, "qubes_list", {})
    appel(s, "qubes_list", {})
    a, b = t.plans
    assert a["session"] == b["session"] != Serveur(t, {}).session
    assert len(a["session"]) == 18 and a["session"].startswith("S-")


def test_demande_forgee_refusee():
    t = FakeTransport()
    r = appel(srv(t, {"QAF_REQUEST": 'R-1", "ops": []'}), "qubes_list", {})
    assert r["result"]["isError"] and t.plans == []


def test_jamais_d_identifiant_r_dans_la_reponse_a_l_agent():
    # PARCOURS-1 M8 : l'agent répète ce qu'il lit ; R-xxxx reste au journal.
    texte, _ = rendre_reponse(0, b'{"request":"R-505548","statut":"refuse","raison":"non"}', b"")
    assert "R-505548" not in texte and "request" not in texte


def test_rendu_reponse_json():
    texte, err = rendre_reponse(0, b'{"request":"R-0001","statut":"fait","resultats":[{"ok":true}]}', b"")
    assert texte.splitlines()[0] == "statut: fait"
    assert 'resultats: [{"ok":true}]' in texte and not err
    for statut in (b"refuse", b"erreur", b"interrompu", b"expire"):
        assert rendre_reponse(0, b'{"statut":"' + statut + b'","raison":"x"}', b"")[1]


def test_rendu_contrat_unique_du_portier():
    # Un succès du vrai exécuteur est un succès pour l'agent (P2-7).
    from qaf_gate import proto
    succes = proto.reponse(proto.FAIT, request="R-0001", resultats=[], non_executees=0)
    assert rendre_reponse(0, json.dumps(succes).encode(), b"")[1] is False
    for hors_contrat in (b'{"statut":"execute"}', b'{"statut":"ok"}', b'{"x":1}', b"fait"):
        assert rendre_reponse(0, hors_contrat, b"")[1], hors_contrat


def test_rendu_codes_qrexec():
    assert "refuse_par_qrexec" in rendre_reponse(126, b"", b"Request refused\n")[0]
    assert "service_absent" in rendre_reponse(127, b"", b"")[0]
    texte, err = rendre_reponse(1, b"", b"boum")
    assert err and "boum" in texte


def test_rendu_nettoie_et_tronque():
    texte, _ = rendre_reponse(0, b"\x1b[31mrouge\x1b[0m" + b"a" * 20000, b"")
    assert "\x1b" not in texte and texte.endswith("[… tronqué]") and len(texte) < 8100


def test_boucle_stdio(demande):
    entree = io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        "\n"
        "pas du json\n"
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"qube_start","arguments":{"name":"dev"}}}\n'
        '{"jsonrpc":"2.0","id":3,"method":"inconnue"}\n'
    )
    sortie = io.StringIO()
    t = FakeTransport(reponse=b'{"statut":"fait","request":"R-0001"}')
    srv(t, demande).boucle(entree, sortie)
    reps = [json.loads(l) for l in sortie.getvalue().splitlines()]
    assert [r.get("id") for r in reps] == [1, None, 2, 3]
    assert reps[1]["error"]["code"] == -32700
    assert reps[2]["result"]["content"][0]["text"] == "statut: fait"
    assert reps[3]["error"]["code"] == -32601


def test_transport_qrexec_commande(monkeypatch):
    vu = {}

    class R:
        returncode, stdout, stderr = 0, b"{}", b""

    def faux_run(argv, **kw):
        vu["argv"], vu["input"] = argv, kw["input"]
        return R()

    monkeypatch.setattr("qaf_mcp.server.subprocess.run", faux_run)
    assert QrexecTransport().envoyer(b"plan") == (0, b"{}", b"")
    assert vu == {"argv": ["/usr/bin/qrexec-client-vm", "dom0", "qaf.Plan"], "input": b"plan"}


def test_transport_qrexec_absent():
    code, _, err = QrexecTransport(binaire="/nexiste/pas").envoyer(b"x")
    assert code == 125 and b"introuvable" in err
