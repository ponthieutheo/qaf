"""Palette (qaf_ui, dom0) et service qaf.Request (qaf_mcp, sys-agent), sans GTK."""

import io
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from qaf_mcp import demande as service
from qaf_mcp.server import Serveur, FakeTransport
from qaf_ui import palette

RACINE = pathlib.Path(__file__).parent.parent


class Appel:
    def __init__(self, returncode=0):
        self.argv = self.entree = None
        self.returncode = returncode

    def __call__(self, argv, input=None, **kw):
        self.argv, self.entree, self.kw = argv, input, kw
        return subprocess.CompletedProcess(argv, self.returncode, b"", b"")


def portier(reponse):
    messages = []

    def appeler(m):
        messages.append(m)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse
    appeler.messages = messages
    return appeler


# --- palette : logique ---------------------------------------------------

def test_import_sans_gtk():
    code = "import sys; import qaf_ui.palette; print('gi' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], cwd=RACINE, capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "False"


def test_demande_transmise():
    # AGENT-JETABLE §1 : le Doorman lance un agent neuf et rend son nom ; la palette relaie.
    p, ex = portier({"statut": "fait", "request": "R-004211", "qubes": ["work"], "agent": "disp42"}), Appel()
    r = palette.demander("  démarre work\n", p, ex)
    assert r == palette.Resultat(True, "Demande R-004211 transmise à un agent neuf (disp42).", "R-004211")
    assert p.messages == [{"type": "demande", "texte": "démarre work"}]
    assert ex.argv == ["/usr/bin/qvm-run", "--pass-io", "--service", "disp42", "qaf.Request"]
    assert "--no-gui" not in ex.argv  # wait-for-session doit jouer
    assert json.loads(ex.entree) == {"request": "R-004211", "texte": "démarre work"}
    assert ex.kw["timeout"] == palette.DELAI_AGENT


def test_texte_vide_ou_trop_long_rien_n_est_envoye():
    p, ex = portier({"statut": "fait", "request": "R-000001"}), Appel()
    assert not palette.demander("   ", p, ex).ok
    assert not palette.demander("a" * (palette.MAX_TEXTE + 1), p, ex).ok
    assert p.messages == [] and ex.argv is None


@pytest.mark.parametrize("reponse,mot", [
    (OSError("absent"), "ne répond pas"),
    (ValueError("trame"), "ne répond pas"),
    ({"statut": "refuse", "raison": "caractère de contrôle interdit dans la demande"}, "contrôle"),
    ({"statut": "fait"}, "inattendue"),
    ({"statut": "fait", "request": "R-0001 vm.remove work"}, "inattendue"),
    ({"statut": "fait", "request": 7}, "inattendue"),
    ({"statut": "fait", "request": "R-000001"}, "inattendue"),  # sans agent
    ({"statut": "fait", "request": "R-000001", "agent": "sys-agent2"}, "inattendue"),
    ({"statut": "fait", "request": "R-000001", "agent": "disp1 --pass-io dom0"}, "inattendue"),
    ([], "inattendue"),
])
def test_portier_en_echec_rien_vers_l_agent(reponse, mot):
    ex = Appel()
    r = palette.demander("démarre work", portier(reponse), ex)
    assert not r.ok and mot in r.message and ex.argv is None


def test_agent_injoignable():
    p = portier({"statut": "fait", "request": "R-000042", "agent": "disp7"})
    r = palette.demander("x", p, Appel(returncode=1))
    assert not r.ok and r.request == "R-000042" and "code 1" in r.message

    def expire(*a, **k):
        raise subprocess.TimeoutExpired("qvm-run", 1)
    r = palette.demander("x", p, expire)
    assert not r.ok and "ne répond pas" in r.message


def test_aucun_raccourci_pose():
    # Décision du 25/10 : la palette se lance par la commande qaf-palette ;
    # ni la palette ni install.sh ne posent de raccourci clavier.
    assert not hasattr(palette, "poser_raccourci")
    assert palette.main(["--raccourci"]) == 2
    install = (RACINE / "packaging/install.sh").read_text(encoding="utf-8")
    assert "xfconf-query" not in install and "--raccourci" not in install
    assert 'lanceur "$R/usr/bin/qaf-palette" qaf_ui.palette' in install


def test_main_usage(capsys):
    assert palette.main(["--inconnu"]) == 2


CLAVIER = "AT Raw Set 2 keyboard"
PROC = 'N: Name="AT Raw Set 2 keyboard"\nP: Phys=isa0060/serio0/input0\n\n'
PROC_USB = PROC + 'N: Name="Clavier USB"\nP: Phys=usb-0000:00:14.0-1/input0\n\n'


def test_frappe_du_clavier_interne_seulement():
    # P2 : même filtre que la fenêtre de confirmation.
    from qaf_ui import confirmation as c
    k = c.verifier_clavier(c.lire_config_clavier(None), PROC)
    assert palette.frappe_admise(k, "a", CLAVIER)
    for appareil in ("Clavier USB", "Virtual core XTEST keyboard", None):
        assert not palette.frappe_admise(k, "a", appareil)
        assert not palette.frappe_admise(k, "Return", appareil)
        assert palette.frappe_admise(k, "Escape", appareil)  # fermer reste possible
    usurpe = c.verifier_clavier(c.lire_config_clavier(None), PROC + PROC.replace("isa0060/serio0", "usb-1"))
    assert not palette.frappe_admise(usurpe, "a", CLAVIER)


def test_envoi_refuse_si_les_peripheriques_ont_change():
    from qaf_ui import confirmation as c
    conf = c.lire_config_clavier(None)
    k = c.verifier_clavier(conf, PROC)
    assert palette.envoi_admis(k, lambda: c.verifier_clavier(conf, PROC)) is None
    assert "branché" in palette.envoi_admis(k, lambda: c.verifier_clavier(conf, PROC_USB))
    sans = c.verifier_clavier(c.ConfigClavier("Autre", None), PROC)
    assert "impossible" in palette.envoi_admis(sans, lambda: sans)


# Propriétés CSS reconnues par GTK 3.24 (gtk/gtkcssstylepropertyimpl.c et
# gtkcssshorthandpropertyimpl.c) : une propriété inconnue fait échouer
# Gtk.CssProvider.load_from_data en entier.
PROPRIETES_GTK3 = {
    "background", "background-color", "border", "border-top", "border-bottom", "border-radius",
    "box-shadow", "color", "caret-color", "font-family", "font-size", "font-weight", "padding",
    "margin", "margin-top", "min-width", "min-height",
}


def test_css_proprietes_gtk3_seulement():
    corps = re.findall(r"\{([^}]*)\}", palette.CSS)
    proprietes = {d.split(":")[0].strip() for b in corps for d in b.split(";") if d.strip()}
    assert proprietes and proprietes <= PROPRIETES_GTK3, proprietes - PROPRIETES_GTK3
    assert palette.CSS.count("{") == palette.CSS.count("}")
    # Direction B : jetons Nuit, contour violet SIGNATURE ; le label violet seulement en aplat.
    from qaf_ui.nuit import jetons
    assert jetons.SURFACE in palette.CSS and jetons.SIGNATURE in palette.CSS and "Inter" in palette.CSS
    assert jetons.LABEL_AGENT not in palette.CSS and "#12151B" not in palette.CSS


def test_rendu_gtk_separe():
    source = (RACINE / "qaf_ui" / "palette.py").read_text(encoding="utf-8")
    assert "import gi" not in source and "from gi" not in source
    gtk = (RACINE / "qaf_ui" / "palette_gtk.py").read_text(encoding="utf-8")
    assert 'require_version("Gtk", "3.0")' in gtk and "WebKit" not in gtk


# --- service qaf.Request (sys-agent) --------------------------------------

def entree(obj):
    return io.BytesIO(json.dumps(obj).encode() if not isinstance(obj, bytes) else obj)


def test_service_lance_claude_avec_la_demande_dans_son_environnement(tmp_path, capsys):
    # PARCOURS-1 M3 : l'identifiant va à CE claude (QAF_REQUEST), jamais dans un fichier commun.
    lances = []
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    texte = "-crée un espace dev ; rm -rf ~"  # commence par « - », contient du shell
    code = service.main(entree({"request": "R-004211", "texte": texte}), env,
                        lambda argv, **kw: lances.append((argv, kw)), jetable=False)
    assert code == 0 and list(tmp_path.iterdir()) == []
    assert "ok R-004211" in capsys.readouterr().out
    ((argv, kw),) = lances
    assert argv == ["/usr/bin/xfce4-terminal", "--disable-server", "--geometry=120x36",
                    "--title=Agent",
                    "-x", "/usr/bin/env", "QAF_REQUEST=R-004211", "DISABLE_AUTOUPDATER=1",
                    "/usr/bin/claude", "--", texte]
    assert kw["start_new_session"] and kw["cwd"] == str(tmp_path) and "shell" not in kw
    # qaf_mcp lit ensuite cet identifiant dans chaque plan (variable posée par env)
    t = FakeTransport()
    Serveur(t, {argv[6].split("=")[0]: argv[6].split("=")[1]}).appeler_outil("qubes_list", {})
    assert t.plans[0]["request"] == "R-004211"


def test_titre_du_terminal_fixe_sans_la_demande():
    """Titre fixe « Agent » : la demande (texte de l'utilisateur) reste dans la console,
    jamais dans la barre de titre violette ; ni identifiant R-xxxx."""
    texte = "crée-moi un espace essai-dev et installe tree dedans"
    for jetable, suite in ((False, False), (True, False), (True, True)):
        argv = service.commande_terminal("R-004211", texte, jetable=jetable, suite=suite)
        titres = [a for a in argv if a.startswith("--title=")]
        assert titres == ["--title=Agent"]
        assert argv[-1] == texte and argv.count(texte) == 1  # la demande : le seul argument du CLI


@pytest.mark.parametrize("env", [{}, {"QREXEC_REMOTE_DOMAIN": "work"}, {"QREXEC_REMOTE_DOMAIN": "sys-agent"}])
def test_service_refuse_un_autre_appelant(tmp_path, env):
    lances = []
    code = service.main(entree({"request": "R-004211", "texte": "x"}), env, lances.append)
    assert code == 1 and lances == []


@pytest.mark.parametrize("donnees", [
    b"pas du json", b"[]", {"request": "R-0001"}, {"request": "R-0001", "texte": "x", "plus": 1},
    {"request": "R-1", "texte": "x"}, {"request": "R-0001\nR-0002", "texte": "x"},
    {"request": "R-0001", "texte": ""}, {"request": "R-0001", "texte": "a\x1bb"},
    {"request": "R-0001", "texte": "a" * (service.MAX_TEXTE + 1)}, b"x" * (service.MAX_ENTREE + 1),
])
def test_service_entree_invalide(tmp_path, donnees):
    lances = []
    code = service.main(entree(donnees), {"QREXEC_REMOTE_DOMAIN": "dom0"}, lances.append)
    assert code == 1 and lances == []


def test_fichiers_livres_pour_tpl_agent():
    rpc = RACINE / "qaf_mcp" / "etc" / "qubes-rpc" / "qaf.Request"
    assert rpc.read_text().startswith("#!/bin/sh\n")
    assert "python3 -I" in rpc.read_text() and "qaf_mcp.demande import main" in rpc.read_text()
    conf = (RACINE / "qaf_mcp" / "etc" / "qubes" / "rpc-config" / "qaf.Request").read_text()
    assert "wait-for-session=true" in conf.splitlines()
    # PARCOURS-1 M3 : plus de fichier de demande commun (/run/qaf/demande), donc plus de tmpfiles.d.
    assert not (RACINE / "qaf_mcp" / "etc" / "tmpfiles.d").exists() and not hasattr(service, "ecrire_demande")
    setup = (RACINE / "tools" / "setup-sys-agent.sh").read_text()
    assert "/etc/qubes-rpc/qaf.Request" in setup and "/etc/qubes/rpc-config/qaf.Request" in setup


def test_lanceur_du_service_trouve_le_module(tmp_path):
    # Le lanceur ajoute /usr/lib/qaf à sys.path malgré -I : on le simule en
    # remplaçant ce chemin par la racine du dépôt.
    rpc = (RACINE / "qaf_mcp" / "etc" / "qubes-rpc" / "qaf.Request").read_text()
    ligne = next(l for l in rpc.splitlines() if l.startswith("exec "))
    code = re.search(r"-c '([^']*)'", ligne).group(1).replace("/usr/lib/qaf", str(RACINE))
    env = {"QREXEC_REMOTE_DOMAIN": "work", "PATH": os.environ.get("PATH", "")}
    r = subprocess.run([sys.executable, "-I", "-c", code], input=b"{}", env=env, capture_output=True)
    assert r.returncode == 1 and "n'accepte que dom0" in r.stderr.decode()
