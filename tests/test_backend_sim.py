import pytest

from qaf_gate import backend as B
from backend_sim import SimBackend


def code(fn, *args):
    with pytest.raises(B.BackendError) as e:
        fn(*args)
    return e.value.code


def test_etat_initial_realiste():
    s = SimBackend()
    noms = [q.name for q in s.list_qubes()]
    assert {"dom0", "sys-net", "sys-firewall", "vault", "debian-13-xfce"} <= set(noms)
    assert noms == sorted(noms)
    assert s.info("sys-firewall").netvm == "sys-net"
    assert s.info("vault").netvm is None


def test_creation_sans_reseau():
    s = SimBackend()
    q = s.create_appvm("dev", "debian-13-xfce", "blue")
    assert q.netvm is None and q.state == "Halted" and q.klass == "AppVM" and q.uuid


def test_erreurs_creation():
    s = SimBackend()
    assert code(s.create_appvm, "work", "debian-13-xfce", "red") == B.NOM_PRIS
    assert code(s.create_appvm, "x", "absent", "red") == B.TEMPLATE_ABSENT
    assert code(s.create_appvm, "x", "work", "red") == B.PAS_UN_TEMPLATE
    assert code(s.create_appvm, "x", "debian-13-xfce", "rose") == B.LABEL_INCONNU


def test_cycle_de_vie_et_erreurs():
    s = SimBackend()
    s.create_appvm("dev", "debian-13-xfce", "blue")
    assert code(s.shutdown, "dev") == B.PAS_DEMARRE
    s.start("dev")
    assert code(s.start, "dev") == B.DEJA_DEMARRE
    assert code(s.remove, "dev") == B.PAS_ARRETE
    s.shutdown("dev")
    s.remove("dev")
    assert code(s.info, "dev") == B.INTROUVABLE


def test_en_usage():
    s = SimBackend()
    assert code(s.remove, "dom0") == B.EN_USAGE
    assert code(s.remove, "debian-13-xfce") == B.EN_USAGE  # template utilisé
    assert code(s.shutdown, "sys-net") == B.EN_USAGE  # sys-firewall en dépend
    assert code(s.remove, "default-dvm") == B.EN_USAGE  # default_dispvm


def test_set_netvm():
    s = SimBackend()
    s.create_appvm("dev", "debian-13-xfce", "blue")
    assert code(s.set_netvm, "dev", "vault") == B.PAS_UN_NETVM
    assert code(s.set_netvm, "dev", "absent") == B.INTROUVABLE
    s.set_netvm("dev", "sys-firewall")
    assert s.info("dev").netvm == "sys-firewall"
    s.set_netvm("dev", None)
    assert s.info("dev").netvm is None


def test_netvm_arretee():
    s = SimBackend()
    s.shutdown("work")
    s.shutdown("sys-firewall")
    s.create_appvm("dev", "debian-13-xfce", "blue")
    s.start("dev")
    assert code(s.set_netvm, "dev", "sys-firewall") == B.NETVM_ARRETEE


def test_demarrer_demarre_la_netvm():
    s = SimBackend()
    s.shutdown("work")
    s.shutdown("sys-firewall")
    s.start("personal")
    assert s.info("sys-firewall").state == "Running"


def test_dispvm():
    s = SimBackend()
    nom = s.run_dispvm_task(None, "tâche")
    assert s.info(nom).klass == "DispVM" and s.info(nom).template == "default-dvm"
    assert s.taches == [(nom, "tâche")]
    assert code(s.run_dispvm_task, "debian-13-xfce", "x") == B.PAS_UN_TEMPLATE


def test_backend_qubes_import_paresseux():
    # Le module se charge sans qubesadmin ; seule l'instanciation l'importe.
    import qaf_gate.backend_qubes as bq

    try:
        import qubesadmin  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            bq.QubesBackend()
