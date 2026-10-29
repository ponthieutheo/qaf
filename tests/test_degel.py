"""qaf-degel (qaf_ui/degel.py) : détection du gel du pointeur, sans X."""
import struct

from qaf_ui import degel
from qaf_ui.degel import Suivi, SEUIL_S

MAITRES = (2, 3)
PAVE, XTEST = 11, 4


def test_lire_brut_format_python_xlib():
    # octets vus sous Xvfb : deviceid 4, time, detail 1, sourceid 4, …
    donnees = bytes.fromhex("04003b420802010000000400020000000000000000000000")
    assert degel.lire_brut(donnees) == (4, 1)
    assert degel.lire_brut(b"\x00" * 4) is None
    assert degel.lire_brut(struct.pack("=HIIH", 11, 7, 3, 11)) == (11, 3)


def test_relachement_normal_aucun_gel():
    s = Suivi(MAITRES)
    s.brut(PAVE, 1, True, 0.0)
    s.brut(PAVE, 1, False, 0.1)
    assert s.prochaine() == 0.1 + SEUIL_S
    assert s.dues(1.0) == []
    assert s.dues(0.1 + SEUIL_S) == [1]
    assert not s.gele(1, masque=0)            # le maître a relâché aussi
    assert s.gele(1, masque=256 | 16)          # maître gelé (bouton 1 + NumLock, relevé du 28/10)


def test_bouton_tenu_longtemps_n_est_pas_un_gel():
    s = Suivi(MAITRES)
    s.brut(PAVE, 1, True, 0.0)
    assert s.prochaine() is None
    assert not s.gele(1, masque=256)


def test_un_autre_esclave_tient_encore_le_bouton():
    s = Suivi(MAITRES)
    s.brut(PAVE, 1, True, 0.0)
    s.brut(XTEST, 1, True, 0.0)
    s.brut(XTEST, 1, False, 0.1)
    assert s.prochaine() is None and not s.gele(1, masque=256)
    s.brut(PAVE, 1, False, 0.2)
    assert s.dues(0.2 + SEUIL_S) == [1]


def test_bruts_du_maitre_et_boutons_de_defilement_ignores():
    s = Suivi(MAITRES)
    s.brut(2, 1, False, 0.0)
    s.brut(PAVE, 4, False, 0.0)
    assert s.prochaine() is None


def test_nouvel_appui_annule_la_verification():
    s = Suivi(MAITRES)
    s.brut(PAVE, 1, False, 0.0)
    s.brut(PAVE, 1, True, 1.0)
    assert s.prochaine() is None


def test_noms_et_options_xkb():
    n = degel.noms_xkb(b"evdev\0pc104\0fr\0oss\0\0")
    assert n == {"rules": "evdev", "model": "pc104", "layout": "fr", "variant": "oss", "options": ""}
    assert degel.options(n) == []
    assert degel.argv_setxkbmap(n, ["grab:break_actions"]) == [
        "setxkbmap", "-rules", "evdev", "-model", "pc104", "-layout", "fr", "-variant", "oss",
        "-option", "", "-option", "grab:break_actions"]
    n2 = degel.noms_xkb("evdev\0pc105\0fr,us\0oss,\0ctrl:nocaps,compose:ralt\0")
    assert degel.options(n2) == ["ctrl:nocaps", "compose:ralt"]
    # remise exacte : on vide d'abord les options (« -option "" ») puis on remet les anciennes
    assert degel.argv_setxkbmap(n2, degel.options(n2))[-5:] == ["", "-option", "ctrl:nocaps", "-option", "compose:ralt"]
