"""Backend réel, opérations d'OPS-v3 : qubesadmin remplacé par un faux qui
reproduit les appels utilisés (setattr/delattr, property_is_default, features,
firewall.rules + Rule, devices, volumes, pools). Les appels dans un qube
passent par qaf-gate-taches (message enregistré)."""

import sys
import types

import pytest

from qaf_gate import backend as B
from qaf_gate.backend import BackendError
from test_backend_qubes import faux_exc


class Rule:
    def __init__(self, rule, **champs):
        self.champs = champs
        self.rule = " ".join(f"{k}={v}" for k, v in sorted(champs.items()))
        for k in ("action", "dsthost", "proto", "dstports", "specialtarget", "icmptype", "expire", "comment"):
            setattr(self, k, champs.get(k))


class Pare_feu:
    def __init__(self):
        self.rules, self.ecritures = [Rule(None, action="accept")], 0

    def __setattr__(self, k, v):
        if k == "rules" and hasattr(self, "ecritures"):
            self.__dict__["ecritures"] += 1
        self.__dict__[k] = v


class Coll:
    def __init__(self, exposes=(), attaches=()):
        self.exposes, self.attaches = list(exposes), list(attaches)

    def get_exposed_devices(self):
        return self.exposes

    def get_attached_devices(self):
        return self.attaches


class Dev:
    def __init__(self, backend, port, device_id=""):
        self.backend_domain, self.port_id, self.device_id = types.SimpleNamespace(name=backend), port, device_id


class VM:
    def __init__(self, app, name, klass="AppVM", etat="Halted", **props):
        d = self.__dict__
        d.update(app=app, name=name, klass=klass, etat=etat, features={}, tags=set(), defauts=set(),
                 volumes={"private": types.SimpleNamespace(size=2 * 2**30)}, firewall=Pare_feu(),
                 devices={c: Coll() for c in ("usb", "block", "mic")}, appels=[])
        d.update(memory=400, label=types.SimpleNamespace(name="blue"), **props)

    def __setattr__(self, k, v):
        self.appels.append(("set", k, v))
        self.__dict__[k] = v
        self.defauts.discard(k)

    def __delattr__(self, k):
        self.appels.append(("reset", k))
        self.__dict__[k] = 400 if k == "memory" else None
        self.defauts.add(k)

    def property_is_default(self, k):
        return k in self.defauts

    def get_power_state(self):
        return self.etat

    def get_mem(self):
        return 409600

    def start(self):
        self.appels.append(("start",))
        self.__dict__["etat"] = "Running"

    def shutdown(self):
        self.appels.append(("shutdown",))
        self.__dict__["etat"] = "Halted"


class Domaines(dict):
    def __iter__(self):
        return iter(list(self.values()))

    def clear_cache(self):
        pass


class App:
    def __init__(self):
        self.domains = Domaines()
        for vm in (VM(self, "work", etat="Running"), VM(self, "debian-13-xfce", "TemplateVM"),
                   VM(self, "sys-usb", etat="Running"), VM(self, "sys-net", "AppVM", "Halted")):
            self.domains[vm.name] = vm
        self.domains["debian-13-xfce"].features["os-distribution"] = "debian"
        self.pools = {"vm-pool": types.SimpleNamespace(size=100, usage=40)}
        self.messages, self.reponse = [], {"ok": True, "codes": [0, 0]}
        self.default_template = self.domains["debian-13-xfce"]

    def taches(self, msg, delai):
        self.messages.append(msg)
        return self.reponse


@pytest.fixture
def qubes(monkeypatch):
    exc = faux_exc()
    mod = types.ModuleType("qubesadmin")
    mod.exc = exc
    pf = types.ModuleType("qubesadmin.firewall")
    pf.Rule = Rule
    mod.firewall = pf
    for nom, m in (("qubesadmin", mod), ("qubesadmin.exc", exc), ("qubesadmin.firewall", pf)):
        monkeypatch.setitem(sys.modules, nom, m)
    from qaf_gate.backend_qubes import QubesBackend
    app = App()
    return QubesBackend(app=app, taches=app.taches), app


def test_set_prop_garde_la_valeur_avant(qubes):
    b, app = qubes
    work = app.domains["work"]
    work.defauts.add("memory")
    assert b.set_prop("work", "memory", 1024) == {"avant": {"valeur": 400, "defaut": True}}
    assert ("set", "memory", 1024) in work.appels
    assert b.reset_prop("work", "memory") == {"avant": {"valeur": 1024, "defaut": False}}
    assert work.appels[-1] == ("reset", "memory")


def test_services_comme_qvm_service(qubes):
    b, app = qubes
    f = app.domains["work"].features
    assert b.set_service("work", "cups", "on") == {"avant": None} and f["service.cups"] == "1"
    b.set_service("work", "cups", "off")
    assert f["service.cups"] == ""
    assert b.set_service("work", "cups", "default") == {"avant": ""} and "service.cups" not in f


def test_settings(qubes):
    b, app = qubes
    app.domains["work"].tags.add("projet")
    r = b.settings("work")
    assert r["proprietes"]["label"] == "blue" and r["prive_mio"] == 2048 and r["tags"] == ["projet"]


def test_pare_feu_conserve_les_regles_et_compare_exactement(qubes):
    b, app = qubes
    fw = app.domains["work"].firewall
    fw.rules = [Rule(None, action="accept", comment="de l'utilisateur")]
    b.fw_add("work", 0, {"action": "drop", "host": "10.0.0.0/8"})
    assert [r.rule for r in fw.rules] == ["action=drop dsthost=10.0.0.0/8", "action=accept comment=de l'utilisateur"]
    assert b.fw_list("work")[1] == {"action": "accept", "autres_champs": True}
    with pytest.raises(BackendError) as e:
        b.fw_remove("work", 0, {"action": "drop", "host": "10.0.0.0/16"})
    assert e.value.code == B.REGLE_CHANGEE
    b.fw_remove("work", 0, {"action": "drop", "host": "10.0.0.0/8"})
    assert [r.rule for r in fw.rules] == ["action=accept comment=de l'utilisateur"]


def test_pkg_install_demarre_puis_arrete_le_modele(qubes):
    b, app = qubes
    tpl = app.domains["debian-13-xfce"]
    assert b.pkg_install("debian-13-xfce", ["git"]) == {"codes": [0, 0]}
    # Délai = ce qui reste après le démarrage du modèle (échéance unique, 540 s au plus).
    assert 530 <= app.messages[0].pop("delai") <= 540
    assert app.messages == [{"type": "vmexec", "qube": "debian-13-xfce", "action": "pkg.install",
                             "distro": "debian", "paquets": ["git"]}]
    assert tpl.appels == [("start",), ("shutdown",)]
    app.reponse = {"ok": False, "codes": [100]}
    with pytest.raises(BackendError):
        b.pkg_install("debian-13-xfce", ["git"])
    tpl.features["os-distribution"] = "arch"
    with pytest.raises(BackendError) as e:
        b.pkg_install("debian-13-xfce", ["git"])
    assert e.value.code == B.DISTRIBUTION
    with pytest.raises(BackendError) as e:
        b.pkg_install("work", ["git"])
    assert e.value.code == B.PAS_UN_TEMPLATE


def test_hand_par_le_relais_et_qube_demarre(qubes):
    b, app = qubes
    app.reponse = {"ok": True, "code": 0, "octets": 2, "b64": "e30="}
    assert b.hand("work", "qaf.Exec", {"argv": ["ls"], "delai": 5}, 10) == {"code": 0, "octets": 2, "b64": "e30="}
    assert app.messages[-1] == {"type": "hand", "qube": "work", "service": "qaf.Exec",
                                "requete": {"argv": ["ls"], "delai": 5}, "delai": 10}
    with pytest.raises(BackendError) as e:
        b.hand("sys-net", "qaf.Exec", {}, 10)
    assert e.value.code == B.PAS_DEMARRE


def test_dev_list_ne_demarre_rien(qubes):
    b, app = qubes
    app.domains["sys-usb"].devices["usb"] = Coll([Dev("sys-usb", "2-1", "1234:5678")])
    app.domains["work"].devices["usb"] = Coll(attaches=[Dev("sys-usb", "2-1")])
    app.domains["sys-net"].devices["usb"] = Coll([Dev("sys-net", "9-9")])  # arrêté : pas interrogé
    assert b.dev_list("usb") == [{"class": "usb", "backend": "sys-usb", "port": "2-1", "device_id": "1234:5678",
                                  "attached_to": "work"}]
    assert all(a[0] != "start" for vm in app.domains for a in vm.appels)


def test_prefs_et_sys_status(qubes):
    b, app = qubes
    assert b.prefs()["default_template"] == "debian-13-xfce"
    assert b.set_pref("default_template", "work")["avant"]["valeur"] == "debian-13-xfce"
    s = b.sys_status()
    assert {"name": "work", "memoire_kio": 409600} in s["qubes"] and s["pools"][0]["utilise"] == 40


def test_restart(qubes):
    b, app = qubes
    b.restart("work")
    assert app.domains["work"].appels[-2:] == [("shutdown",), ("start",)]


# --- vm.clone (PARCOURS-1, revue du 25/10) ------------------------------------------------

class VMClone(VM):
    def __setattr__(self, k, v):  # qubesadmin rend un objet Label pour un label posé par son nom
        super().__setattr__(k, types.SimpleNamespace(name=v) if k == "label" and isinstance(v, str) else v)


def faux_clone(app, echec=None):
    def clone_vm(src, name, ignore_errors=False, ignore_devices=False):
        app.clone_args = {"ignore_errors": ignore_errors, "ignore_devices": ignore_devices}
        vm = VMClone(app, name, src.klass)
        vm.__dict__.update(tags=set(src.tags) | {"created-by-dom0"}, features=dict(src.features),
                           defauts=set(), autostart=True, default_dispvm="default-dvm",
                           management_dispvm="mgmt", guivm=types.SimpleNamespace(name="sys-gui"), audiovm=None,
                           netvm=types.SimpleNamespace(name="sys-net"))
        vm.firewall.rules = [Rule(None, action="drop")]
        if echec:
            vm.__dict__["tags"] = echec
        app.domains[name] = vm
        return vm
    app.clone_vm = clone_vm


def test_clone_nettoye_privileges(qubes):
    b, app = qubes
    tpl = app.domains["debian-13-xfce"]
    tpl.tags.update({"qaf-relais", "qaf-agent", "vpn-autorise", "created-by-sys-agent"})
    faux_clone(app)
    q = b.clone_template("debian-13-xfce", "tpl-neuf", "gray")
    vm = app.domains["tpl-neuf"]
    assert app.clone_args == {"ignore_errors": True, "ignore_devices": True}
    assert vm.tags == {"created-by-dom0"}  # étiquettes de politique, qaf-*, created-by- recopiés : retirés
    for p in ("default_dispvm", "management_dispvm", "guivm", "audiovm", "autostart"):
        assert ("reset", p) in vm.appels, p
    assert vm.netvm is None and q.netvm is None and q.label == "gray"
    assert [r.action for r in vm.firewall.rules] == ["accept"]
    assert vm.features["os-distribution"] == "debian"  # nécessaire à pkg.install


def test_clone_retire_si_nettoyage_impossible(qubes):
    b, app = qubes

    class Etiquettes(set):
        def discard(self, t):
            raise app_exc()

    def app_exc():
        import qubesadmin.exc
        return qubesadmin.exc.QubesException("refus")

    faux_clone(app, echec=Etiquettes({"vpn-autorise"}))
    with pytest.raises(BackendError):
        b.clone_template("debian-13-xfce", "tpl-neuf", "gray")
    assert "tpl-neuf" not in app.domains


def test_clone_refuse_hors_template(qubes):
    b, app = qubes
    faux_clone(app)
    with pytest.raises(BackendError):
        b.clone_template("work", "tpl-neuf", "gray")
    assert "tpl-neuf" not in app.domains
