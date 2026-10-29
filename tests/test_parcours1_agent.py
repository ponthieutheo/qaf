"""PARCOURS-1, côté agent et palette : B1 (palette), M4 (dossier de confiance),
M5 (capture montrée), M6 (liste complète), M7 (aucune commande pour contourner
un refus), skill en français simple."""

import base64
import json
import pathlib
import re
import sys
import types
from unittest import mock

import skills_qaf
from qaf_mcp import demande
from qaf_mcp.server import OUTILS, FakeTransport, Serveur

RACINE = pathlib.Path(__file__).resolve().parent.parent
SKILL = skills_qaf.tous()  # tous les skills livrés : qaf, qaf-…


def qube(nom, i):
    return {"name": nom, "uuid": f"{i:08x}-1111-2222-3333-444455556666", "klass": "AppVM", "state": "Halted",
            "label": "blue", "template": "debian-13-xfce", "netvm": "sys-firewall", "provides_network": False,
            "template_for_dispvms": False, "default_dispvm": "default-dvm", "default_dispvm_herite": True,
            "guivm": "dom0", "audiovm": "dom0", "management_dispvm": "default-mgmt-dvm"}


# --- M6 : tous les qubes, sans troncature -------------------------------------------

def test_liste_complete_meme_avec_beaucoup_de_qubes():
    qubes = [qube(f"espace-{i:02d}", i) for i in range(60)]
    qubes[0].update(name="sys-usb", provides_network=False, netvm=None)
    qubes[1].update(name="sys-net", provides_network=True, netvm=None)
    rep = {"statut": "fait", "request": "R-0001", "resultats": [{"op": "vm.list", "ok": True, "resultat": qubes}]}
    assert len(json.dumps(rep)) > 8000  # l'ancien rendu coupait la liste ici
    r = Serveur(FakeTransport(reponse=json.dumps(rep).encode()), {}).appeler_outil("qubes_list", {})
    texte = r["content"][0]["text"]
    assert not r["isError"] and "tronqué" not in texte and "qubes: 60 (liste complète)" in texte
    for q in qubes:
        assert f"\n{q['name']} |" in texte
    assert "sys-net | Halted | AppVM | blue | modèle=debian-13-xfce | réseau=aucun | donne le réseau" in texte
    assert "uuid" not in texte and "R-0001" not in texte


def test_liste_en_erreur_garde_le_rendu_normal():
    r = Serveur(FakeTransport(reponse=b'{"statut":"refuse","raison":"pause"}'), {}).appeler_outil("qubes_list", {})
    assert r["isError"] and "statut: refuse" in r["content"][0]["text"]


# --- M5 : capture renvoyée en image ET copiée dans sys-agent ------------------------

def _capture(image):
    sortie = json.dumps({"ok": True, "format": "png", "image": image}).encode()
    return json.dumps({"statut": "fait", "resultats": [{"op": "qube.screenshot", "ok": True, "resultat": {
        "code": 0, "b64": base64.b64encode(sortie).decode()}}]}).encode()


def test_capture_en_image_mcp_et_copie_a_ouvrir(tmp_path):
    import qaf_hand.xwd as xwd
    png = xwd.png(2, 2, bytes(range(12)))
    image = base64.b64encode(png).decode()
    r = Serveur(FakeTransport(reponse=_capture(image)), {"HOME": str(tmp_path)}).appeler_outil(
        "qube_screenshot", {"name": "disp1"})
    assert r["content"][1]["type"] == "image" and r["content"][1]["mimeType"] == "image/png"
    (fichier,) = (tmp_path / "captures").iterdir()
    assert png_propre.decoder(fichier.read_bytes()) == png_propre.decoder(png)
    assert str(fichier) in r["content"][0]["text"]


def test_captures_bornees(tmp_path):
    srv = Serveur(FakeTransport(reponse=_capture(base64.b64encode(_png(2, 2)).decode())), {"QAF_CAPTURES": str(tmp_path)})
    for i in range(55):
        (tmp_path / f"capture-2020-{i:03d}.png").write_bytes(b"vieux")
    srv.appeler_outil("qube_screenshot", {"name": "disp1"})
    assert len(list(tmp_path.glob("capture-*.png"))) == 50


def test_capture_sans_dossier_ne_casse_rien():
    r = Serveur(FakeTransport(reponse=_capture(base64.b64encode(b"x").decode())), {}).appeler_outil(
        "qube_screenshot", {"name": "disp1"})
    assert not r["isError"] and len(r["content"]) == 2 and "fichier" not in r["content"][0]["text"]


# --- M4 : claude lancé dans le dossier approuvé -------------------------------------

def test_dossier_de_travail(tmp_path):
    assert demande.dossier_de_travail({"HOME": str(tmp_path)}) == str(tmp_path)
    (tmp_path / "agent").mkdir()
    assert demande.dossier_de_travail({"HOME": str(tmp_path)}) == str(tmp_path / "agent")


def test_setup_approuve_le_dossier_de_travail():
    s = (RACINE / "tools/setup-sys-agent.sh").read_bytes()
    assert b"\r\n" not in s
    assert b"hasTrustDialogAccepted: true" in s and b'"$HOME/agent"' in s and b"mkdir -p ~/agent" in s
    assert b"mv ~/.mcp.json ~/.mcp.json.qaf-ancien" in s


# --- B1 : la palette ne plante plus sur xalign --------------------------------------

def test_label_palette_accepte_xalign(monkeypatch):
    vus = []

    class Label:
        def __init__(self, **kw):
            vus.append(kw)

        def get_style_context(self):
            return types.SimpleNamespace(add_class=lambda c: None)

    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    rep = types.ModuleType("gi.repository")
    rep.Gtk, rep.Gdk, rep.GLib, rep.Pango = mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    rep.Gtk.Label = Label
    gi.repository = rep
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", rep)
    for m in ("qaf_ui.palette_gtk", "qaf_ui.fenetre"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    try:
        import qaf_ui.palette_gtk as pg
        pg._label("✦", "icone", xalign=0.5)
        pg._label("texte", "c")
    finally:  # pas de modules montés sur le faux gi pour les autres tests
        for m in ("qaf_ui.palette_gtk", "qaf_ui.fenetre"):
            sys.modules.pop(m, None)
    assert [v["xalign"] for v in vus] == [0.5, 0]


# --- skill et outils : refus sans commande toute prête, clone d'abord ---------------

COMMANDE = re.compile(r"`(qvm-|qubes-prefs)[^`]* [^`]+`")


def test_skill_aucune_commande_pour_contourner():
    assert not COMMANDE.search(SKILL)
    assert "rédige" not in SKILL.lower() and "Ne donne JAMAIS de commande dom0 ou `qvm-*` toute prête" in SKILL
    refus = SKILL[SKILL.index("## Réagir à un refus"):]
    assert "une phrase" in refus and "réglages de Qubes" in refus


def test_skill_clone_avant_les_modeles_de_utilisateur():
    s = SKILL.replace("\n", " ")
    i = s.index("Pour installer un logiciel durablement")
    assert "ce n'est jamais ton premier choix" in s[i:i + 200]
    assert s.index("`template_clone`", i) < s.index("`packages_install`", i) < s.index("`qube_set_template`", i)


def test_skill_capture_et_liste():
    s = SKILL.replace("\n", " ")
    assert "setsid ristretto <fichier> </dev/null >/dev/null 2>&1 &" in s and "Ne lui donne jamais un chemin de fichier" in s
    assert "montre **tous** les qubes" in s


def test_outils_sans_redaction_de_commande():
    for o in OUTILS:
        assert "rédige" not in o["description"], o["name"]
    d = {o["name"]: o["description"] for o in OUTILS}
    assert "TOUS" in d["qubes_list"] and "ristretto" in d["qube_screenshot"] and "premier choix" in d["template_clone"]


# --- revue de M5 : PNG venu d'un qube réencodé avant d'être écrit -----------------

import struct  # noqa: E402
import zlib  # noqa: E402

from qaf_mcp import png_propre  # noqa: E402


def _bloc(t, c):
    return struct.pack(">I", len(c)) + t + c + struct.pack(">I", zlib.crc32(t + c) & 0xFFFFFFFF)


def _png(larg, haut, couleur=2, filtre=0, extra=b"", ihdr=None):
    bpp = png_propre.OCTETS_PAR_PIXEL[couleur]
    brut = b"".join(bytes([filtre]) + bytes((x * 7 + y) & 255 for x in range(larg * bpp)) for y in range(haut))
    return (png_propre.SIGNATURE + _bloc(b"IHDR", ihdr or struct.pack(">IIBBBBB", larg, haut, 8, couleur, 0, 0, 0))
            + extra + _bloc(b"IDAT", zlib.compress(brut)) + _bloc(b"IEND", b""))


def test_png_reencode_tous_filtres_et_couleurs():
    for couleur in (0, 2, 4, 6):
        for filtre in range(5):
            propre = png_propre.reencoder(_png(5, 4, couleur, filtre, extra=_bloc(b"tEXt", b"a\0b")))
            assert propre and b"tEXt" not in propre, (couleur, filtre)
            assert png_propre.decoder(propre)[:2] == (5, 4)


def test_png_reencode_garde_les_pixels():
    import qaf_hand.xwd as xwd
    rgb = bytes(range(256)) * 9
    l, h, px = png_propre.decoder(xwd.png(16, 48, rgb))
    assert (l, h, px) == (16, 48, rgb)


def test_png_refuses():
    bon = _png(4, 4)
    refuses = [
        b"GIF89a" + bon[6:],                                     # mauvaise signature
        bon[:-5],                                                # tronqué
        bon[:30] + bytes([bon[30] ^ 1]) + bon[31:],              # CRC faux
        _png(4, 4, ihdr=struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 1)),   # entrelacé
        _png(4, 4, ihdr=struct.pack(">IIBBBBB", 4, 4, 16, 2, 0, 0, 0)),  # 16 bits
        _png(4, 4, ihdr=struct.pack(">IIBBBBB", 9000, 1, 8, 2, 0, 0, 0)),  # trop large
        _png(4, 4, ihdr=struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)),   # taille incohérente
        _png(4, 4, filtre=9),                                    # filtre inconnu
        _png(4, 4, extra=_bloc(b"exIf", b"x")),                  # bloc non permis
        png_propre.SIGNATURE + b"\0" * (png_propre.MAX_OCTETS),  # trop grand
    ]
    for i, d in enumerate(refuses):
        assert png_propre.reencoder(d) is None, i


def test_bombe_zlib_refusee():
    ihdr = struct.pack(">IIBBBBB", 10, 10, 8, 2, 0, 0, 0)
    d = (png_propre.SIGNATURE + _bloc(b"IHDR", ihdr) + _bloc(b"IDAT", zlib.compress(b"\0" * 50_000_000, 9))
         + _bloc(b"IEND", b""))
    assert len(d) < png_propre.MAX_OCTETS and png_propre.reencoder(d) is None


def _rep(op, image):
    sortie = json.dumps({"ok": True, "format": "png", "image": image}).encode()
    return json.dumps({"statut": "fait", "resultats": [{"op": op, "ok": True, "resultat": {
        "code": 0, "b64": base64.b64encode(sortie).decode()}}]}).encode()


def test_png_invalide_ni_ecrit_ni_propose(tmp_path):
    image = base64.b64encode(b"\x89PNG\r\n\x1a\npiege").decode()
    r = Serveur(FakeTransport(reponse=_rep("qube.screenshot", image)), {"HOME": str(tmp_path)}).appeler_outil(
        "qube_screenshot", {"name": "disp1"})
    assert not (tmp_path / "captures").exists() and "fichier" not in r["content"][0]["text"]
    assert r["content"][1]["data"] == image  # le modèle la reçoit quand même


def test_png_trop_grand_ni_ecrit(tmp_path):
    image = base64.b64encode(png_propre.SIGNATURE + b"\0" * png_propre.MAX_OCTETS).decode()
    Serveur(FakeTransport(reponse=_rep("qube.screenshot", image)), {"HOME": str(tmp_path)}).appeler_outil(
        "qube_screenshot", {"name": "disp1"})
    assert not (tmp_path / "captures").exists()


def test_image_d_une_autre_op_jamais_ecrite(tmp_path):
    image = base64.b64encode(_png(3, 3)).decode()
    r = Serveur(FakeTransport(reponse=_rep("qube.exec", image)), {"HOME": str(tmp_path)}).appeler_outil(
        "qube_exec", {"name": "disp1", "argv": ["ls"]})
    assert not (tmp_path / "captures").exists() and "fichier" not in r["content"][0]["text"]


def test_capture_valide_reencodee_ecrite_et_renvoyee(tmp_path):
    brut = _png(6, 2, couleur=6, extra=_bloc(b"tEXt", b"x\0y"))
    r = Serveur(FakeTransport(reponse=_rep("qube.screenshot", base64.b64encode(brut).decode())),
                {"HOME": str(tmp_path)}).appeler_outil("qube_screenshot", {"name": "disp1"})
    (f,) = (tmp_path / "captures").iterdir()
    propre = f.read_bytes()
    assert propre != brut and b"tEXt" not in propre and base64.b64decode(r["content"][1]["data"]) == propre


def test_taux_de_compression_anormal_refuse():
    # 3840x2160 tout noir compressé au maximum : taux ~1000, au-delà de MAX_TAUX.
    ihdr = struct.pack(">IIBBBBB", 3840, 2160, 8, 2, 0, 0, 0)
    brut = b"\0" * ((3840 * 3 + 1) * 2160)
    d = png_propre.SIGNATURE + _bloc(b"IHDR", ihdr) + _bloc(b"IDAT", zlib.compress(brut, 9)) + _bloc(b"IEND", b"")
    assert png_propre.reencoder_ici(d) is None


def test_ecran_noir_de_qaf_hand_accepte():
    import qaf_hand.xwd as xwd
    assert png_propre.reencoder_ici(xwd.png(1920, 1080, bytes(1920 * 1080 * 3))) is not None


def test_trop_de_pixels_refuse():
    assert png_propre.reencoder_ici(_png(4, 4, ihdr=struct.pack(">IIBBBBB", 3840, 2161, 8, 2, 0, 0, 0))) is None


def test_reencodage_en_sous_processus():
    assert png_propre.reencoder(_png(5, 4, 6, 4)) == png_propre.reencoder_ici(_png(5, 4, 6, 4))


def test_decodage_trop_long_coupe(monkeypatch):
    import time
    monkeypatch.setattr(png_propre, "DELAI", 1)
    monkeypatch.setattr(png_propre, "_ENFANT", "while True: pass")
    t0 = time.monotonic()
    assert png_propre.reencoder(_png(2, 2)) is None
    assert time.monotonic() - t0 < 2.5  # coupé par RLIMIT_CPU (ou le délai réel)
