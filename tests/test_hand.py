import base64
import io
import json

import pytest

from qaf_hand import service
from qaf_hand.service import MAX_ENTREE, servir
from faux_x import faux_xwd, lire_png


def ECRAN(x, y):
    """Couleur du pixel (x, y) du faux écran : chaque pixel est reconnaissable."""
    return (x * 3 % 256, y * 5 % 256, (x + y) % 256)


ECRAN_XWD = faux_xwd(64, 48, ECRAN)


class FauxSysteme:
    """Simule xdotool, xwininfo, xwd et les commandes de qaf.Exec (écran 64×48)."""

    def __init__(self, fenetres=None, echec=None, xwd=ECRAN_XWD):
        # id décimal -> (titre, x, y, l, h, visible)
        self.fenetres = fenetres if fenetres is not None else {
            "27262979": ("xterm", 0, 0, 640, 480, True),
            "27262990": ("Firefox", 10, 20, 30, 15, True),
            "27263000": ("minimisée", 0, 0, 100, 100, False),
        }
        self.echec = echec or set()
        self.xwd = xwd
        self.appels = []

    def __call__(self, argv, entree=None, delai=15):
        self.appels.append(list(argv))
        prog = argv[0]
        if prog in self.echec:
            return 1, b"", b"Can't open display"
        if prog == "xdotool":
            cmd = argv[1]
            if cmd == "search":
                visibles = "--onlyvisible" in argv
                ids = [i for i, f in self.fenetres.items() if f[5] or not visibles]
                return (0 if ids else 1), "".join(i + "\n" for i in ids).encode(), b""
            if cmd == "getwindowgeometry":
                t, x, y, w, h, _ = self.fenetres[argv[-1]]
                return 0, f"WINDOW={argv[-1]}\nX={x}\nY={y}\nWIDTH={w}\nHEIGHT={h}\nSCREEN=0\n".encode(), b""
            if cmd == "getwindowname":
                return 0, (self.fenetres[argv[-1]][0] + "\n").encode(), b""
            return 0, b"", b""
        if prog == "xwininfo":
            t, x, y, w, h, vis = self.fenetres[argv[-1]]
            return 0, (f'\nxwininfo: Window id: {hex(int(argv[-1]))} "{t}"\n\n'
                       f"  Absolute upper-left X:  {x}\n  Absolute upper-left Y:  {y}\n"
                       f"  Relative upper-left X:  {x}\n  Relative upper-left Y:  {y}\n"
                       f"  Width: {w}\n  Height: {h}\n  Depth: 24\n"
                       f"  Map State: {'IsViewable' if vis else 'IsUnmapped'}\n").encode(), b""
        if prog == "xwd":
            return 0, self.xwd, b""
        return 0, b"sortie\n", b"erreur\n"


def req(service_nom, obj, systeme=None):
    systeme = systeme or FauxSysteme()
    brut = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    rep, code = servir(service_nom, io.BytesIO(brut), systeme)
    assert rep["ok"] is (code == 0)
    return rep, systeme


# ------------------------------------------------------------------ Screen

def test_lister_visibles():
    rep, s = req("screen", {"action": "lister"})
    assert [f["titre"] for f in rep["fenetres"]] == ["xterm", "Firefox"]
    assert rep["fenetres"][1] == {"id": "0x1a0000e", "titre": "Firefox", "x": 10, "y": 20,
                                  "width": 30, "height": 15}
    assert s.appels[0] == ["xdotool", "search", "--maxdepth", "1", "--onlyvisible", "--name", ""]


def test_lister_masquees_et_vide():
    rep, _ = req("screen", {"action": "lister", "masquees": True})
    assert len(rep["fenetres"]) == 3
    rep, _ = req("screen", {"action": "lister"}, FauxSysteme(fenetres={}))
    assert rep == {"ok": True, "fenetres": []}


def test_capturer_fenetre_recadre_la_racine():
    # RESULTATS-S1 D : la capture d'une fenêtre seule est noire dans un qube.
    # On capture la racine (xwd -root) et on découpe selon xwininfo.
    rep, s = req("screen", {"action": "capturer", "fenetre": "0x1a0000e"})
    assert s.appels == [["xwininfo", "-id", "27262990"], ["xwd", "-silent", "-root"]]
    assert (rep["format"], rep["x"], rep["y"], rep["largeur"], rep["hauteur"]) == ("png", 10, 20, 30, 15)
    larg, haut, px = lire_png(base64.b64decode(rep["image"]))
    assert (larg, haut) == (30, 15)
    assert px[0][0] == ECRAN(10, 20) and px[14][29] == ECRAN(39, 34)
    assert rep["octets"] == len(base64.b64decode(rep["image"]))


def test_capturer_racine_entiere():
    # Une fenêtre visible couvre tout l'écran (xterm 640×480) : écran entier.
    rep, s = req("screen", {"action": "capturer", "fenetre": "racine"})
    assert s.appels[-1] == ["xwd", "-silent", "-root"]
    larg, haut, px = lire_png(base64.b64decode(rep["image"]))
    assert (larg, haut, rep["x"], rep["y"]) == (64, 48, 0, 0) and px[47][63] == ECRAN(63, 47)
    # Aucune fenêtre visible : écran entier aussi.
    rep, _ = req("screen", {"action": "capturer", "fenetre": "racine"}, FauxSysteme(fenetres={}))
    assert (rep["largeur"], rep["hauteur"]) == (64, 48)


def test_capturer_racine_prend_le_cadre_des_fenetres():
    # n9 (PARCOURS-2 et 3) : « racine » donnait 1920×1080 presque blancs autour
    # d'un petit terminal ; c'est maintenant le cadre des fenêtres visibles.
    s = FauxSysteme(fenetres={"1": ("terminal", 10, 20, 30, 15, True), "2": ("dialogue", 30, 5, 10, 10, True),
                              "3": ("technique", 0, 0, 1, 1, True), "4": ("cachée", 0, 0, 64, 48, False)})
    rep, _ = req("screen", {"action": "capturer", "fenetre": "racine"}, s)
    assert (rep["x"], rep["y"], rep["largeur"], rep["hauteur"]) == (10, 5, 30, 30)
    larg, haut, px = lire_png(base64.b64decode(rep["image"]))
    assert (larg, haut) == (30, 30) and px[0][0] == ECRAN(10, 5)
    # Liste des fenêtres illisible : écran entier, la capture ne casse pas.
    rep, _ = req("screen", {"action": "capturer", "fenetre": "racine"}, FauxSysteme(echec={"xdotool"}))
    assert rep["ok"] and (rep["largeur"], rep["hauteur"]) == (64, 48)


def test_lanceur_reel_n_attend_pas_un_fils_detache():
    # n18 (PARCOURS-3) : « nohup xfce4-terminal & » gardait stdout ouvert, qube_exec
    # attendait le délai (20 s), puis l'agent relançait : deux terminaux.
    import time
    debut = time.monotonic()
    code, out, err = service.lancer(["sh", "-c", "sleep 30 & echo pret; echo ko >&2"], b"", 10)
    assert time.monotonic() - debut < 5
    assert (code, out, err) == (0, b"pret\n", b"ko\n")
    code, out, _ = service.lancer(["sh", "-c", "read l; echo \"$l\""], b"bonjour\n", 5)
    assert out == b"bonjour\n"
    with pytest.raises(service.Refus, match="délai dépassé"):
        service.lancer(["sleep", "5"], None, 1)
    with pytest.raises(service.Refus, match="commande absente"):
        service.lancer(["/nexiste/pas"], None, 1)


def test_capturer_fenetre_debordante_bornee_a_l_ecran():
    s = FauxSysteme(fenetres={"5": ("à cheval", -10, 40, 30, 30, True)})
    rep, _ = req("screen", {"action": "capturer", "fenetre": "5"}, s)
    assert (rep["x"], rep["y"], rep["largeur"], rep["hauteur"]) == (0, 40, 20, 8)
    s = FauxSysteme(fenetres={"5": ("dehors", 100, 100, 30, 30, True)})
    rep, _ = req("screen", {"action": "capturer", "fenetre": "5"}, s)
    assert "hors de l'écran" in rep["erreur"]


def test_capturer_fenetre_non_affichee():
    rep, s = req("screen", {"action": "capturer", "fenetre": "27263000"})
    assert "non affichée (IsUnmapped)" in rep["erreur"]
    assert ["xwd", "-silent", "-root"] not in s.appels


def test_capture_echoue():
    rep, _ = req("screen", {"action": "capturer", "fenetre": "racine"}, FauxSysteme(echec={"xwd"}))
    assert "capture impossible" in rep["erreur"]
    rep, _ = req("screen", {"action": "capturer", "fenetre": "27262979"}, FauxSysteme(echec={"xwininfo"}))
    assert "xwininfo" in rep["erreur"]
    rep, _ = req("screen", {"action": "capturer", "fenetre": "racine"}, FauxSysteme(xwd=b"pas un xwd"))
    assert "XWD tronqué" in rep["erreur"]


@pytest.mark.parametrize("fenetre", ["root", "0x", "0x123456789", "-1", "1; rm -rf /", 0, True, None, "0x0", 2**32])
def test_fenetre_invalide(fenetre):
    rep, s = req("screen", {"action": "capturer", "fenetre": fenetre})
    assert not rep["ok"] and s.appels == []


# ------------------------------------------------------------------- Input

def test_taper_dans_une_fenetre():
    rep, s = req("input", {"action": "taper", "texte": "-n bonjour\n", "fenetre": "0x1a00003"})
    assert rep == {"ok": True, "tape": 11}
    assert s.appels == [["xdotool", "windowfocus", "--sync", "27262979"],
                        ["xdotool", "type", "--delay", "12", "--", "-n bonjour\n"]]


def test_touche_clic_defiler():
    _, s = req("input", {"action": "touche", "touches": "ctrl+shift+t"})
    assert s.appels == [["xdotool", "key", "--", "ctrl+shift+t"]]
    _, s = req("input", {"action": "cliquer", "x": 5, "y": 6, "bouton": 3, "fenetre": "27262979"})
    assert s.appels == [["xdotool", "mousemove", "--window", "27262979", "5", "6"], ["xdotool", "click", "3"]]
    _, s = req("input", {"action": "deplacer", "x": 1, "y": 2})
    assert s.appels == [["xdotool", "mousemove", "1", "2"]]
    _, s = req("input", {"action": "defiler", "sens": "haut", "crans": 3})
    assert s.appels == [["xdotool", "click", "--repeat", "3", "4"]]


INPUT_INVALIDES = [
    {"action": "taper", "texte": ""},
    {"action": "taper", "texte": "a" * 2001},
    {"action": "taper", "texte": "\x1b[A"},
    {"action": "taper", "texte": "a‮b"},
    {"action": "taper", "texte": "a", "extra": 1},
    {"action": "touche", "touches": "ctrl+l type rm"},
    {"action": "touche", "touches": "--help"},
    {"action": "touche", "touches": "a+b+c+d+e+f"},
    {"action": "cliquer", "x": -1, "y": 0},
    {"action": "cliquer", "x": 1, "y": 1, "bouton": 4},
    {"action": "cliquer", "x": 1.5, "y": 1},
    {"action": "cliquer", "x": True, "y": 1},
    {"action": "deplacer", "x": 1, "y": 1, "bouton": 1},
    {"action": "defiler", "sens": "gauche", "crans": 1},
    {"action": "defiler", "sens": "bas", "crans": 21},
    {"action": "exec"},
    [],
]


@pytest.mark.parametrize("r", INPUT_INVALIDES)
def test_input_invalide(r):
    rep, s = req("input", r)
    assert not rep["ok"] and s.appels == []


def test_xdotool_en_echec():
    rep, _ = req("input", {"action": "touche", "touches": "Return"}, FauxSysteme(echec={"xdotool"}))
    assert not rep["ok"] and "Can't open display" in rep["erreur"]


# -------------------------------------------------------------------- Exec

def test_exec_borne():
    rep, s = req("exec", {"argv": ["ls", "-l", "; rm -rf /"], "delai": 5})
    assert rep == {"ok": True, "code": 0, "stdout": "sortie\n", "stderr": "erreur\n", "tronque": False}
    assert s.appels == [["ls", "-l", "; rm -rf /"]]  # liste d'arguments, jamais de shell


def test_exec_sortie_tronquee():
    class Bavard(FauxSysteme):
        def __call__(self, argv, entree=None, delai=15):
            return 0, b"x" * 100000, b""
    rep, _ = req("exec", {"argv": ["yes"]}, Bavard())
    assert rep["tronque"] and len(rep["stdout"]) == 64 * 1024


def test_exec_sortie_binaire_tient_sous_la_borne_du_portier():
    # Octets invalides ou de contrôle : la réponse JSON grossit ; elle est
    # réduite pour rester sous les 128 Kio lus par qaf-gate-taches.
    class Binaire(FauxSysteme):
        def __call__(self, argv, entree=None, delai=15):
            return 0, b"\xff\x00" * 60000, b"\x01" * 70000
    rep, _ = req("exec", {"argv": ["cat", "/bin/ls"]}, Binaire())
    assert rep["ok"] and rep["tronque"]
    assert len(json.dumps(rep, ensure_ascii=False).encode("utf-8")) <= 120 * 1024


@pytest.mark.parametrize("r", [
    {"argv": []}, {"argv": "ls -l"}, {"argv": [""]}, {"argv": ["ls", 1]}, {"argv": ["a\0b"]},
    {"argv": ["x"] * 65}, {"argv": ["a" * 4097]}, {"argv": ["a" * 4000, "b" * 4000, "c" * 400]},
    {"argv": ["ls"], "delai": 0}, {"argv": ["ls"], "delai": 121}, {"argv": ["ls"], "shell": True},
    {"argv": ["ls"], "stdin": "x"},
])
def test_exec_invalide(r):
    rep, s = req("exec", r)
    assert not rep["ok"] and s.appels == []


# ------------------------------------------------------------- entrée brute

@pytest.mark.parametrize("brut", [b"", b"{", b"\xff\xfe", b"NaN", b'{"argv": [NaN]}', b"[" * 100000,
                                  b" " * (MAX_ENTREE + 1)])
def test_entree_brute_refusee(brut):
    for nom in ("screen", "input", "exec"):
        rep, s = req(nom, brut)
        assert not rep["ok"] and s.appels == []


def test_main_usage(capsys):
    assert service.main([]) == 2
    assert service.main(["shell"]) == 2
