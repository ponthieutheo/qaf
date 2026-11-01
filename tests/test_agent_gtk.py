"""Panneau agent réel, hors écran : xvfb-run + Python du système (python3-gi).
Ignoré si la machine n'a pas les paquets de rendu (tools/rendu-ui.sh --installer).
Jamais de fenêtre sur l'écran réel : DISPLAY et WAYLAND_DISPLAY sont retirés."""

import json
import shutil
import subprocess

import pytest

from test_nuit_gtk import PY, RACINE, _env, _gtk_disponible

pytestmark = pytest.mark.skipif(not _gtk_disponible(), reason="xvfb ou python3-gi absents")


def test_panneau_agent_sous_xvfb():
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1440x900x24", PY, "-I",
                        str(RACINE / "tests" / "gtk_agent_verif.py")],
                       capture_output=True, text=True, env=_env(), timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr


def test_fournisseurs_sous_xvfb():
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1440x900x24", PY, "-I",
                        str(RACINE / "tests" / "gtk_fournisseurs_verif.py")],
                       capture_output=True, text=True, env=_env(), timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr


def test_qube_voisin_sous_xvfb():
    if shutil.which("xdotool") is None:
        pytest.skip("xdotool absent")
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1440x900x24 +extension XInputExtension +extension XTEST", PY, "-I",
                        str(RACINE / "tests" / "gtk_voisin_verif.py")],
                       capture_output=True, text=True, env=_env(), timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr


@pytest.mark.parametrize("etat", ["actif", "attente", "pause", "permanent_dev"])
def test_rendus_agent_contrastes(tmp_path, etat):
    png, rapport = tmp_path / f"agent-{etat}.png", tmp_path / "c.json"
    r = subprocess.run(["bash", str(RACINE / "tools" / "rendu-ui.sh"), f"agent_demo:{etat}", str(png),
                        "--contraste", str(rapport), "--exiger"],
                       capture_output=True, text=True, env=_env(), timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    mesures = json.loads(rapport.read_text(encoding="utf-8"))
    assert len(mesures) >= 20 and all(m["ratio"] >= 4.5 for m in mesures)
