"""Backend réel, dispvm.run_task : qubesadmin remplacé par un faux (API vérifiée
dans qubes-core-admin-client : qubesd_call, domains.get_blind, clear_cache,
start, kill). L'appel qrexec lui-même passe par qaf-gate-taches (test_taches.py)."""

import sys
import types

import pytest

from qaf_gate import backend as B
from qaf_gate.backend import BackendError


def faux_exc():
    m = types.ModuleType("qubesadmin.exc")

    class QubesException(Exception):
        pass

    m.QubesException = QubesException
    for nom in ("QubesVMNotFoundError", "QubesVMNotHaltedError", "QubesVMInUseError",
                "QubesNoTemplateError", "QubesLabelNotFoundError", "QubesVMNotStartedError"):
        setattr(m, nom, type(nom, (QubesException,), {}))
    return m


class FauxVM:
    def __init__(self, app, nom, klass="AppVM", dvm=False, netvm=None):
        self.app, self.name, self.klass, self.template_for_dispvms = app, nom, klass, dvm
        self.netvm = netvm
        self.appels = []

    def start(self):
        self.appels.append(("start",))
        self.app.horloge_val += self.app.duree_demarrage
        if self.app.echec_demarrage:
            raise self.app.exc.QubesException("démarrage impossible")

    def kill(self):
        self.appels.append(("kill",))
        if self.app.jamais_demarre:
            raise self.app.exc.QubesVMNotStartedError("pas démarré")


class FauxDomaines:
    def __init__(self, app):
        self.app, self.vms, self.vidages = app, {}, 0
        self.cache = None  # noms figés comme le cache de qubesadmin ; None = liste à jour

    def __contains__(self, nom):
        return nom in (self.vms if self.cache is None else self.cache)

    def __getitem__(self, nom):
        return self.vms[nom]

    def __iter__(self):
        return iter(list(self.vms.values()))

    def get_blind(self, nom):
        return self.vms[nom]

    def clear_cache(self):
        self.vidages += 1
        self.cache = None

    def __delitem__(self, nom):
        self.app.supprimes.append(nom)
        del self.vms[nom]


class FauxApp:
    def __init__(self, exc):
        self.exc = exc
        self.domains = FauxDomaines(self)
        for vm in (FauxVM(self, "qaf-dvm", dvm=True), FauxVM(self, "work"),
                   FauxVM(self, "debian-13-xfce", klass="TemplateVM"),
                   FauxVM(self, "default-dvm", dvm=True, netvm="sys-firewall")):
            self.domains.vms[vm.name] = vm
        self.appels, self.supprimes, self.messages = [], [], []
        self.echec_demarrage = self.jamais_demarre = False
        self.nom_rendu = b"disp4242"
        self.horloge_val, self.duree_demarrage = 1000.0, 20.0
        self.reponse = {"accepte": True, "raison": ""}

    def qubesd_call(self, dest, methode, arg=None, payload=None):
        self.appels.append((dest, methode))
        nom = self.nom_rendu.decode("ascii", "replace")
        self.domains.vms[nom] = FauxVM(self, nom, klass="DispVM")
        return self.nom_rendu

    def taches(self, msg, delai):
        self.messages.append((msg, delai))
        if isinstance(self.reponse, Exception):
            raise self.reponse
        return self.reponse


@pytest.fixture
def qubes(monkeypatch):
    exc = faux_exc()
    mod = types.ModuleType("qubesadmin")
    mod.exc = exc
    monkeypatch.setitem(sys.modules, "qubesadmin", mod)
    monkeypatch.setitem(sys.modules, "qubesadmin.exc", exc)
    from qaf_gate.backend_qubes import QubesBackend
    app = FauxApp(exc)
    b = QubesBackend(app=app, modeles_admis={"qaf-dvm", "default-dvm", "work", "debian-13-xfce", "absent"},
                     taches=app.taches, horloge=lambda: app.horloge_val)
    return b, app


def code(fn, *args):
    with pytest.raises(BackendError) as e:
        fn(*args)
    return e.value.code


def test_jetable_depuis_le_modele_dedie(qubes):
    b, app = qubes
    assert b.run_dispvm_task(None, "lister\nexec uname -n") == "disp4242"
    # CreateDisposable vise le modèle NOMMÉ : jamais dom0 (qui prendrait default_dispvm).
    assert app.appels == [("qaf-dvm", "admin.vm.CreateDisposable")]
    assert app.domains.vms["disp4242"].appels == [("start",)]
    # H3 : la tâche part par qaf-gate-taches ; H2 : avec le temps RESTANT (110 - 20 s de démarrage).
    msg, delai = app.messages[0]
    assert msg == {"type": "tache", "jetable": "disp4242", "tache": "lister\nexec uname -n", "delai": 90}
    assert delai == 95
    assert app.supprimes == []  # le coureur l'éteindra ; auto_cleanup le supprime


def test_modele_explicite_de_la_liste(qubes):
    b, app = qubes
    b.run_dispvm_task("default-dvm", "x")
    assert app.appels == [("default-dvm", "admin.vm.CreateDisposable")]


def test_modele_hors_liste_blanche_refuse(qubes):
    # H1 : en plus du classement (N4), le backend refuse tout modèle non admis.
    b, app = qubes
    b.modeles_admis = frozenset({"qaf-dvm"})
    assert code(b.run_dispvm_task, "default-dvm", "x") == B.PAS_UN_TEMPLATE
    assert app.appels == []


def test_qaf_dvm_avec_reseau_refuse(qubes):
    b, app = qubes
    app.domains.vms["qaf-dvm"].netvm = "sys-firewall"
    assert code(b.run_dispvm_task, None, "x") == B.AUTRE
    assert app.appels == []


@pytest.mark.parametrize("modele,attendu", [("absent", B.TEMPLATE_ABSENT), ("work", B.PAS_UN_TEMPLATE),
                                            ("debian-13-xfce", B.PAS_UN_TEMPLATE)])
def test_modele_refuse(qubes, modele, attendu):
    b, app = qubes
    assert code(b.run_dispvm_task, modele, "x") == attendu
    assert app.appels == []


def test_modele_dedie_absent(qubes):
    b, app = qubes
    del app.domains.vms["qaf-dvm"]
    assert code(b.run_dispvm_task, None, "x") == B.TEMPLATE_ABSENT
    assert app.appels == []  # pas de repli sur default_dispvm


@pytest.mark.parametrize("reponse", [
    {"accepte": False, "raison": "qaf.Task n'a pas accepté la tâche (code 1)"},
    {"raison": "x"},
    {"accepte": "oui"},
    ConnectionRefusedError(),  # qaf-gate-taches absent
])
def test_tache_non_acceptee_tue_le_jetable(qubes, reponse):
    b, app = qubes
    app.reponse = reponse
    assert code(b.run_dispvm_task, None, "x") == B.AUTRE
    assert ("kill",) in app.domains.vms["disp4242"].appels


def test_echeance_unique_demarrage_trop_long(qubes):
    # H2 : le démarrage a mangé tout le budget : rien n'est envoyé, jetable tué.
    b, app = qubes
    app.duree_demarrage = 200.0
    with pytest.raises(BackendError, match="délai épuisé"):
        b.run_dispvm_task(None, "x")
    assert app.messages == [] and ("kill",) in app.domains.vms["disp4242"].appels


def test_demarrage_impossible(qubes):
    b, app = qubes
    app.echec_demarrage = app.jamais_demarre = True
    assert code(b.run_dispvm_task, None, "x") == B.AUTRE
    assert app.supprimes == ["disp4242"]  # jamais démarré : retiré directement


def test_nom_de_jetable_inattendu(qubes):
    b, app = qubes
    app.nom_rendu = b"../../x"
    assert code(b.run_dispvm_task, None, "x") == B.AUTRE


def test_list_qubes_vide_le_cache(qubes):
    # H4 : qubesadmin ne rafraîchit sa liste que par clear_cache().
    b, app = qubes
    app.domains.vms = {}
    assert b.list_qubes() == []
    assert app.domains.vidages == 1


def test_socket_des_taches_identique():
    from qaf_gate import backend_qubes, taches
    assert backend_qubes.SOCK_TACHES == taches.SOCK_TACHES


# --- agent jetable (docs/AGENT-JETABLE.md §1) -------------------------------------

def test_creer_agent_sans_demarrer_sur_le_seul_modele_d_agent(qubes, monkeypatch):
    b, app = qubes
    assert code(b.creer_agent, "qaf-dvm") == B.PAS_UN_TEMPLATE  # pas un modèle d'agent
    assert app.appels == []
    app.domains.vms["qaf-agent-dvm"] = FauxVM(app, "qaf-agent-dvm", dvm=True, netvm="sys-firewall")
    monkeypatch.setattr(b, "_info", lambda vm: vm.name)
    assert b.creer_agent("qaf-agent-dvm") == "disp4242"
    # CreateDisposable sur le modèle NOMMÉ ; ni start ni étiquette ici (l'exécuteur le fait, R-5).
    assert app.appels == [("qaf-agent-dvm", "admin.vm.CreateDisposable")]
    assert app.domains.vms["disp4242"].appels == []
    b.tuer("disp4242")
    assert app.domains.vms["disp4242"].appels == [("kill",)]


def test_creer_agent_vide_le_cache_des_noms(qubes, monkeypatch):
    # qubesadmin garde la liste des noms : le jetable créé doit être trouvable
    # par son nom aussitôt (tag puis start, par l'exécuteur).
    b, app = qubes
    app.domains.vms["qaf-agent-dvm"] = FauxVM(app, "qaf-agent-dvm", dvm=True, netvm="sys-firewall")
    app.domains.cache = set(app.domains.vms)
    monkeypatch.setattr(b, "_info", lambda vm: vm.name)
    assert b.creer_agent("qaf-agent-dvm") == "disp4242"
    assert "disp4242" in app.domains and b._vm("disp4242") is app.domains.vms["disp4242"]
