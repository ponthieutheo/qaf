"""Écrans GTK réels, hors écran : xvfb-run + Python du système (python3-gi).

Ignoré si la machine n'a pas les paquets de rendu (tools/rendu-ui.sh --installer).
Jamais de fenêtre sur l'écran réel : DISPLAY et WAYLAND_DISPLAY sont retirés.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

RACINE = pathlib.Path(__file__).parent.parent
PY = "/usr/bin/python3"


def _gtk_disponible():
    if not shutil.which("xvfb-run") or not os.path.exists(PY):
        return False
    code = 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'
    return subprocess.run([PY, "-c", code], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _gtk_disponible(), reason="xvfb ou python3-gi absents")


def _env():
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONPATH")}
    env.update(GDK_BACKEND="x11", NO_AT_BRIDGE="1")
    return env


def test_rendu_refuse_lecran_reel(tmp_path):
    env = _env() | {"DISPLAY": "", "WAYLAND_DISPLAY": "wayland-0"}
    r = subprocess.run([PY, "-I", str(RACINE / "tools" / "rendu_ui.py"), "agent_demo:actif", str(tmp_path / "x.png")],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "xvfb-run" in r.stderr


@pytest.mark.parametrize("ecran", ["agent_demo:actif", "rendu_confirmation:renforcee"])
def test_ecrans_tiennent_sur_un_petit_ecran(tmp_path, ecran):
    # AUDIT-ECRANS-1 : à 1366x768, Agent (780 px) sortait de l'écran ; la fenêtre est
    # maintenant bornée et son contenu défile.
    r = subprocess.run(["bash", str(RACINE / "tools" / "rendu-ui.sh"), ecran, str(tmp_path / "e.png"),
                        "--plein", "--exiger"],
                       capture_output=True, text=True, env=_env() | {"QAF_RENDU_ECRAN": "1366x768"}, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sur écran 1366x768" in r.stdout and "DÉBORDE" not in r.stdout, r.stdout
