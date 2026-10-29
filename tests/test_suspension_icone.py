"""Icône qaf-suspension (qaf_ui/ecrans/suspension.py), logique sans GTK : textes,
apparence, clics, menu, commande sudo ; et qaf-suspendre sous un umask strict."""

import json
import os
import subprocess
import types

import pytest

from qaf_ui import sni
from qaf_ui import suspension as S
from qaf_ui.ecrans import suspension as I
from qaf_ui.nuit import jetons
from test_nuit_gtk import PY, _gtk_disponible

NBSP = " "


def test_restant_arrondi_a_la_minute_au_dessus():
    assert I.restant(42 * 60) == f"42{NBSP}min"
    assert I.restant(41 * 60 + 1) == f"42{NBSP}min"
    assert I.restant(5) == I.restant(0) == I.restant(-3) == f"1{NBSP}min"
    assert I.restant(3600) == f"1{NBSP}h"
    assert I.restant(3600 + 5 * 60) == f"1{NBSP}h{NBSP}05"
    assert I.restant(86400) == f"24{NBSP}h"


def test_phrases_et_infobulle():
    assert I.phrase(None) == "Confirmations de l'agent actives"
    assert I.phrase(42 * 60) == f"Confirmations de l'agent suspendues — reste 42{NBSP}min"
    assert I.phrase("permanent") == "Confirmations de l'agent suspendues — en permanence"
    assert I.infobulle(None) == ("Confirmations de l'agent actives", "Clic : suspendre les confirmations")
    assert I.infobulle(600) == ("Confirmations de l'agent suspendues", f"Reste 10{NBSP}min · clic pour les rétablir")
    assert I.infobulle("permanent")[1] == "En permanence · clic pour les rétablir"


def test_apparence_discrete_puis_alerte():
    alerte = [(1, 1, b"\xff\0\0\0")]
    a = I.apparence(None, alerte)
    assert a["statut"] == "Active" and a["icone"] == "object-locked" and a["images"] == []
    for e in (900, "permanent"):
        a = I.apparence(e, alerte)
        assert a["statut"] == "NeedsAttention" and a["icone"] == ""  # l'image ambre, pas l'icône du thème
        assert a["images"] == alerte and a["attention_images"] == alerte
    el = sni.Element("qaf-suspension", I.TITRE)
    for e in (None, 900, "permanent", None):  # chaque apparence est acceptée telle quelle par Element
        el.changer(**I.apparence(e, alerte))
    assert el.statut == "Active"


def test_clic_gauche_coupe_seulement_quand_ca_court():
    assert I.clic_gauche(None) == "menu"
    assert I.clic_gauche(1) == I.clic_gauche("permanent") == "off"


def test_menu():
    lignes = I.menu(None)
    assert lignes[0] == ("Confirmations de l'agent actives", None, False)  # en-tête, jamais cliquable
    actions = [a for _, a, actif in lignes if a and actif]
    assert actions == ["15m", "1h", "1j", "permanent"]  # rien à rétablir
    assert ("Rétablir les confirmations", "off", False) in lignes
    lignes = I.menu(600)
    assert [a for _, a, actif in lignes if a and actif] == ["15m", "1h", "1j", "permanent", "off"]
    assert sum(1 for l in lignes if l == (None, None, False)) == 2  # deux séparateurs
    assert set(I.ACTIONS) == set(S.DUREES) | {"off"}  # alignées sur qaf-suspendre


def test_commande_et_lancement():
    assert I.commande("1h") == ["sudo", "-n", "/usr/bin/qaf-suspendre", "1h"]
    with pytest.raises(ValueError):
        I.commande("1h; rm -rf /")
    vus = []

    def executer(argv, **kw):
        vus.append((argv, kw))
        return types.SimpleNamespace(returncode=0, stderr="")
    assert I.lancer("off", executer) == (True, "")
    assert vus[0][0][-1] == "off" and vus[0][1]["stdin"] == subprocess.DEVNULL and vus[0][1]["timeout"]
    ok, msg = I.lancer("1j", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="sudo: a password is required\n"))
    assert not ok and "refusé" in msg and "password" in msg

    def absent(*a, **k):
        raise FileNotFoundError("sudo")
    ok, msg = I.lancer("1j", absent)
    assert not ok and "pas pu être lancé" in msg


def test_lire_echec_ferme(tmp_path):
    assert I.lire(str(tmp_path / "absent")) is None
    p = tmp_path / "s"
    p.write_text("pas du json")
    assert I.lire(str(p)) is None


def test_qaf_suspendre_fichier_lisible_meme_sous_umask_strict(tmp_path):
    # Vu sur une vraie machine : sudo garde l'umask 0077 de la session, le fichier
    # sortait en 0600, illisible par qaf-exec (portier) et par l'icône.
    chemin, moi = str(tmp_path / "suspension"), os.getuid()
    ancien = os.umask(0o077)
    try:
        assert S.main(["15m"], chemin, maintenant=lambda: 1000, proprietaire=moi) == 0
        assert os.stat(chemin).st_mode & 0o777 == 0o644
        os.chmod(chemin, 0o600)  # un fichier déjà là, resté en 0600, est refait en 0644
        assert S.main(["permanent"], chemin, proprietaire=moi) == 0
        assert os.stat(chemin).st_mode & 0o777 == 0o644
        assert json.loads(open(chemin, encoding="ascii").read()) == {"jusqua": None}
    finally:
        assert os.umask(ancien) == 0o077  # umask du processus rendu


def test_cadenas_et_css_du_menu():
    svg = I.cadenas_ouvert_svg(22)
    assert 'width="22"' in svg and jetons.AGENT in svg and I.AMBRE == jetons.AGENT
    css = I.css_menu()
    for c in (jetons.SURFACE, jetons.T1, jetons.T3, jetons.CHOISI, jetons.FILET):
        assert c in css
    assert "{" in css and "{{" not in css


@pytest.mark.skipif(not _gtk_disponible(), reason="python3-gi absent")
def test_cadenas_rendu_et_css_accepte_par_gtk():
    """Le SVG se charge à chaque taille (images ARGB32 non vides) et GTK accepte le CSS."""
    code = r"""
import sys; sys.path.insert(0, ".")
import gi; gi.require_version("Gtk", "3.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk
from qaf_ui.ecrans import suspension as I
from qaf_ui.ecrans import suspension_gtk as G
imgs = G.images_alerte()
assert [(w, h) for w, h, _ in imgs] == [(t, t) for t in I.TAILLES]
assert all(len(o) == w * h * 4 and any(o[0::4]) for w, h, o in imgs)
Gtk.CssProvider().load_from_data(I.css_menu().encode())
print("OK")
"""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONPATH")}
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(["xvfb-run", "-a", PY, "-I", "-c", code], capture_output=True, text=True,
                       env=env, cwd=racine, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr
