"""qaf-gate-taches (H3) : seul processus qui lance qrexec-client pour qaf.Task.
qrexec-client est remplacé par un petit programme Python lancé pour de vrai :
la lecture bornée et l'échéance portent sur de vrais tubes."""

import os
import subprocess
import sys
import threading
import time

import pytest

from qaf_gate import proto, taches

ACCUSE = '{"ok": true, "accepte": true, "id": "x", "coureur": "factice"}'


def faux_qrexec(script):
    """lancer() qui exécute `script` à la place de qrexec-client, en gardant argv."""
    vus = []

    def lancer(argv, **kw):
        vus.append(argv)
        return subprocess.Popen([sys.executable, "-c", script], **kw)

    return lancer, vus


def lit_puis_ecrit(sortie, code=0):
    return f"import sys; sys.stdin.buffer.read(); sys.stdout.write({sortie!r}); sys.stdout.flush(); sys.exit({code})"


def test_accepte_et_argv():
    lancer, vus = faux_qrexec(lit_puis_ecrit(ACCUSE + "\n"))
    assert taches.envoyer("disp42", "lister", 10, lancer=lancer) == {"accepte": True, "raison": ""}
    assert vus == [[taches.QREXEC_CLIENT, "-d", "disp42", "-w", "10", "DEFAULT:QUBESRPC qaf.Task dom0"]]


def test_entree_transmise():
    script = ("import sys, json; t = json.loads(sys.stdin.buffer.read())['tache']; "
              f"sys.stdout.write({ACCUSE!r} if t == 'é\\nx' else '{{}}')")
    lancer, _ = faux_qrexec(script)
    assert taches.envoyer("disp1", "é\nx", 10, lancer=lancer)["accepte"] is True


@pytest.mark.parametrize("script", [
    lit_puis_ecrit('{"ok": false, "erreur": "x"}', code=1),
    lit_puis_ecrit("pas du json"),
    lit_puis_ecrit('{"ok": true}'),
    lit_puis_ecrit(ACCUSE, code=126),  # service absent ou refusé
    "import sys; sys.stdout.buffer.write(b'\\xff' * 10)",
])
def test_refus(script):
    lancer, _ = faux_qrexec(script)
    assert taches.envoyer("disp1", "x", 10, lancer=lancer)["accepte"] is False


def test_reponse_trop_longue_lue_bornee_et_tuee():
    # Un jetable qui inonde stdout : au plus MAX_ACCUSE + 1 octets lus, processus tué.
    lancer, _ = faux_qrexec("import sys\nwhile True: sys.stdout.write('x' * 65536)")
    debut = time.monotonic()
    r = taches.envoyer("disp1", "x", 20, lancer=lancer)
    assert r == {"accepte": False, "raison": "réponse de qaf.Task trop longue ou en retard"}
    assert time.monotonic() - debut < 10


def test_lire_borne_ne_garde_pas_plus_que_maxi():
    p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdout.write('y' * 100000)"],
                         stdout=subprocess.PIPE)
    try:
        assert taches.lire_borne(p, time.monotonic() + 10, maxi=10) is None
    finally:
        p.kill()
        p.wait()


def test_echeance_respectee():
    lancer, _ = faux_qrexec("import time; time.sleep(30)")
    debut = time.monotonic()
    r = taches.envoyer("disp1", "x", 1, lancer=lancer)
    assert r["accepte"] is False and time.monotonic() - debut < 5


def test_processus_qui_ne_finit_pas_apres_l_accuse():
    lancer, _ = faux_qrexec(f"import sys, time; sys.stdout.write({ACCUSE!r}); sys.stdout.close(); time.sleep(30)")
    debut = time.monotonic()
    assert taches.envoyer("disp1", "x", 2, lancer=lancer)["accepte"] is False
    assert time.monotonic() - debut < 6


def test_qrexec_client_absent():
    def lancer(argv, **kw):
        raise FileNotFoundError(argv[0])
    assert taches.envoyer("disp1", "x", 5, lancer=lancer)["accepte"] is False


@pytest.mark.parametrize("msg", [
    {"type": "tache", "jetable": "work", "tache": "x", "delai": 10},  # pas un jetable
    {"type": "tache", "jetable": "dom0", "tache": "x", "delai": 10},
    {"type": "tache", "jetable": "disp1;x", "tache": "x", "delai": 10},
    {"type": "tache", "jetable": "disp1", "tache": "", "delai": 10},
    {"type": "tache", "jetable": "disp1", "tache": "a" * 1001, "delai": 10},
    {"type": "tache", "jetable": "disp1", "tache": "abc‮", "delai": 10},
    {"type": "tache", "jetable": "disp1", "tache": "x", "delai": 0},
    {"type": "tache", "jetable": "disp1", "tache": "x", "delai": 10_000},
    {"type": "tache", "jetable": "disp1", "tache": "x", "delai": True},
    {"type": "tache", "jetable": "disp1", "tache": "x", "delai": 10, "shell": "sh"},
    {"type": "autre", "jetable": "disp1", "tache": "x", "delai": 10},
    ["pas", "un", "objet"],
])
def test_schema_ferme(msg):
    appels = []
    r = taches.traiter(msg, envoi=lambda *a: appels.append(a))
    assert r["accepte"] is False and appels == []


def test_message_valide_transmis():
    appels = []
    taches.traiter({"type": "tache", "jetable": "disp7", "tache": "t", "delai": 30},
                   envoi=lambda *a: appels.append(a) or {"accepte": True, "raison": ""})
    assert appels == [("disp7", "t", 30)]


def _serveur(tmp_path, autorises):
    chemin = str(tmp_path / "t.sock")
    srv = taches.serveur(chemin, autorises=autorises, envoi=lambda j, t, d: {"accepte": True, "raison": j})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, chemin


def test_socket_de_bout_en_bout(tmp_path):
    srv, chemin = _serveur(tmp_path, {os.getuid()})
    try:
        rep = proto.client(chemin, 5)({"type": "tache", "jetable": "disp3", "tache": "t", "delai": 9})
        assert rep == {"accepte": True, "raison": "disp3"}
    finally:
        srv.shutdown()
        srv.server_close()


def test_socket_refuse_un_autre_compte(tmp_path):
    srv, chemin = _serveur(tmp_path, {os.getuid() + 12345})
    try:
        with pytest.raises((proto.ProtoError, OSError)):
            proto.client(chemin, 5)({"type": "tache", "jetable": "disp3", "tache": "t", "delai": 9})
    finally:
        srv.shutdown()
        srv.server_close()


def test_unites_systemd():
    from pathlib import Path
    racine = Path(__file__).resolve().parent.parent / "packaging"
    so = (racine / "qaf-gate-taches.socket").read_text(encoding="utf-8").splitlines()
    assert f"ListenStream={taches.SOCK_TACHES}" in so
    assert "SocketGroup=qaf-exec" in so and "SocketMode=0660" in so
    se = (racine / "qaf-gate-taches.service").read_text(encoding="utf-8")
    lignes = se.splitlines()
    assert "User=qaf-taches" in lignes and "SupplementaryGroups=qubes" in lignes
    assert "DevicePolicy=closed" in lignes and "RestrictAddressFamilies=AF_UNIX" in lignes
    assert "-/run/qubesd.sock" in se  # ne joint jamais qubesd
    # qrexec-client (libxencall) ouvre /dev/xen/hypercall : sans lui, code 125 (Qubes 4.3.2, 25/10)
    assert "DeviceAllow=/dev/xen/hypercall rw" in lignes
    ex = (racine / "qaf-gate-executor.service").read_text(encoding="utf-8").splitlines()
    assert "PrivateDevices=yes" in ex  # H3 : le confinement de l'exécuteur reste entier
    assert not any(ligne.startswith("DeviceAllow") for ligne in ex)


def test_taches_n_importe_pas_qubesadmin():
    code = "import sys; import qaf_gate.taches; sys.exit('qubesadmin' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], cwd=str(__import__("pathlib").Path(__file__).parent.parent)
                          ).returncode == 0
