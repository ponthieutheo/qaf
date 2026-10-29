"""tools/loc.sh : le TCB reste sous son plafond de lignes."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "loc.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")


def lancer(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def test_sous_le_plafond():
    r = lancer()
    assert "total (plafond 3420 lignes de code)" in r.stdout  # D29 (suspension des confirmations, 28/10)
    assert r.returncode == 0, r.stdout + r.stderr


def test_jalon_du_lot_3():
    # Jalons : 2 650 après le lot 2, 3 000 après le lot 3 (D12), 3 200 (D19),
    # 3 400 avec l'agent jetable (D20) : 3 200 est dépassé, et c'est écrit.
    assert lancer("3420").returncode == 0
    assert lancer("3200").returncode == 1


def test_plafond_depasse():
    r = lancer("10")
    assert r.returncode == 1 and "ÉCHEC" in r.stderr


def test_plafond_invalide():
    assert lancer("abc").returncode == 2


def test_backend_simule_hors_du_tcb():
    # Code de test : ni compté dans le TCB ni installé dans dom0 (P2-9).
    racine = SCRIPT.parent.parent
    assert not (racine / "qaf_gate" / "backend_sim.py").exists()
    assert (racine / "tests" / "backend_sim.py").exists()
