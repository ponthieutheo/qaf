"""Brique StatusNotifierItem (qaf_ui/sni.py) : état, signaux et rappels sans
D-Bus ; puis la publication réelle sur un bus de session privé
(dbus-run-session + Python du système), avec un faux watcher qui redémarre."""

import os
import shutil
import subprocess

import pytest

from qaf_ui import sni
from test_nuit_gtk import PY, RACINE


def element(**kw):
    e = sni.Element("essai", "Essai", **kw)
    e.emis = []
    e.emettre = lambda signal, args: e.emis.append((signal, args))
    return e


def test_argb32_depuis_rgba_et_rgb():
    rgba = bytes([1, 2, 3, 4, 5, 6, 7, 8, 0, 0])  # 2 px + 2 octets de bourrage par ligne
    assert sni.argb32(2, 1, rgba, rowstride=10) == (2, 1, bytes([4, 1, 2, 3, 8, 5, 6, 7]))
    rgb = bytes([9, 8, 7, 6, 5, 4])
    assert sni.argb32(1, 2, rgb, rowstride=3, alpha=False) == (1, 2, bytes([255, 9, 8, 7, 255, 6, 5, 4]))
    with pytest.raises(ValueError):
        sni.argb32(2, 2, b"\0" * 8)  # tronqué
    with pytest.raises(ValueError):
        sni.argb32(0, 1, b"")


def test_valeurs_des_proprietes_et_signatures():
    img = sni.argb32(1, 1, bytes([10, 20, 30, 40]))
    e = element(icone="object-locked", images=[img], infobulle=("Titre", "Texte"), categorie="SystemServices")
    assert set(sni.SIGNATURES) == {
        "Category", "Id", "Title", "Status", "WindowId", "IconThemePath", "IconName", "IconPixmap",
        "OverlayIconName", "OverlayIconPixmap", "AttentionIconName", "AttentionIconPixmap",
        "AttentionMovieName", "ToolTip", "ItemIsMenu"}
    for p in sni.SIGNATURES:  # chaque propriété du XML a une valeur, et inversement
        e.valeur(p)
        assert f'name="{p}"' in sni.XML
    assert e.valeur("Category") == "SystemServices" and e.valeur("Id") == "essai"
    assert e.valeur("IconPixmap") == [(1, 1, bytes([40, 10, 20, 30]))]
    assert e.valeur("ToolTip") == ("object-locked", [img], "Titre", "Texte")
    assert e.valeur("ItemIsMenu") is False
    with pytest.raises(KeyError):  # pas de Menu : Plasma appelle ContextMenu sans rien journaliser
        e.valeur("Menu")
    with pytest.raises(KeyError):
        e.valeur("Inconnue")


def test_changer_emet_seulement_ce_qui_change():
    e = element(icone="a")
    assert e.changer(icone="a") == [] and e.emis == []
    assert e.changer(icone="b", images=[(1, 1, b"\0\0\0\0")]) == ["NewIcon"]  # un seul NewIcon
    assert e.emis == [("NewIcon", None)]
    e.emis.clear()
    assert e.changer(statut="NeedsAttention", infobulle=("x", "y"), attention_images=[]) == ["NewStatus", "NewToolTip"]
    assert ("NewStatus", ("NeedsAttention",)) in e.emis
    assert e.changer(infobulle=["x", "y"]) == []  # liste ou tuple : même valeur
    assert e.changer(titre="Autre", attention_icone="dialog-warning") == ["NewTitle", "NewAttentionIcon"]


def test_valeurs_refusees():
    with pytest.raises(ValueError):
        sni.Element("x", "x", statut="Clignote")
    with pytest.raises(ValueError):
        sni.Element("x", "x", categorie="Jeux")
    e = element()
    with pytest.raises(ValueError):
        e.changer(statut="Actif")
    with pytest.raises(TypeError):
        e.changer(couleur="rouge")


def test_methodes_vers_rappels():
    e = element()
    vus = []
    assert e.appeler("Activate", (3, 4))  # sans rappel : accepté, sans effet
    e.on_activer = lambda x, y: vus.append(("gauche", x, y))
    e.on_menu = lambda x, y: vus.append(("droit", x, y))
    e.on_secondaire = lambda x, y: vus.append(("milieu", x, y))
    e.on_defiler = lambda d, o: vus.append(("molette", d, o))
    for m, a in (("Activate", (1, 2)), ("ContextMenu", (5, 6)), ("SecondaryActivate", (7, 8)),
                 ("Scroll", (-120, "vertical"))):
        assert e.appeler(m, a)
    assert vus == [("gauche", 1, 2), ("droit", 5, 6), ("milieu", 7, 8), ("molette", -120, "vertical")]
    assert not e.appeler("ProvideXdgActivationToken", ("t",))


def test_nom_de_service():
    assert sni.nom_de_service(42, 3) == "org.kde.StatusNotifierItem-42-3"


def _bus_disponible():
    if not shutil.which("dbus-run-session") or not os.path.exists(PY):
        return False
    code = 'import gi; gi.require_version("Gio", "2.0"); from gi.repository import Gio'
    return subprocess.run([PY, "-c", code], capture_output=True).returncode == 0


@pytest.mark.skipif(not _bus_disponible(), reason="dbus-run-session ou python3-gi absents")
def test_publication_sur_un_vrai_bus():
    env = {k: v for k, v in os.environ.items() if k not in ("DBUS_SESSION_BUS_ADDRESS", "PYTHONPATH", "DISPLAY")}
    r = subprocess.run(["dbus-run-session", "--", PY, "-I", str(RACINE / "tests" / "dbus_sni_verif.py")],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr
