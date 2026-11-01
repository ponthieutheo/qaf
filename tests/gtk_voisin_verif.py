"""Vérifications GTK réelles de l'écran « qube voisin », lancées par test_agent_gtk.py sous
xvfb-run avec le Python du système (python3-gi). Le champ de port est tapé pour de vrai
par xdotool (XTEST) dans une fenêtre POPUP avec saisie du siège, comme dans dom0 mais
sans KWin. Imprime « OK » à la fin."""

import os
import subprocess
import sys
import tempfile
import time

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()  # cache d'usage : jamais celui de la machine
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [RACINE, os.path.join(RACINE, "tests")]

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from qaf_ui import voisin as v  # noqa: E402
from qaf_ui.ecrans import agent_panneau_gtk as g  # noqa: E402

g.c.installer_style()


class Clavier:
    ok, raison = True, ""

    def __init__(self, confiance=True):
        self.confiance = confiance

    def est_de_confiance(self, _appareil):
        return self.confiance

    def __eq__(self, autre):
        return isinstance(autre, Clavier)


def pomper(duree=0.3):
    fin = time.monotonic() + duree
    while time.monotonic() < fin:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def touches(*noms):
    subprocess.run(["xdotool", "key", *noms], check=True)
    pomper(0.4)


def textes(widget):
    if isinstance(widget, Gtk.Label):
        yield widget.get_text()
    if isinstance(widget, Gtk.Container):
        enfants = []
        widget.forall(enfants.append)
        for e in enfants:
            yield from textes(e)


def cherche(racine, cle):
    """Comme Carte._chercher, éteints compris."""
    if getattr(racine, "qaf_cle", None) == cle:
        return racine
    if isinstance(racine, Gtk.Container):
        enfants = []
        racine.forall(enfants.append)
        for e in enfants:
            w = cherche(e, cle)
            if w is not None:
                return w
    return None


def ecran(clavier, **k):
    carte = g._scene_voisin(clavier=clavier, **k)
    carte.move(100, 100)
    carte.show_all()
    pomper()
    assert carte.saisir(), "saisie du siège refusée"
    pomper(0.1)
    return carte, carte.panneau.voisin, cherche(carte, "port")


# --- la frappe arrive dans le champ, sous POPUP + saisie -------------------------------------
carte, e, champ = ecran(Clavier(), choix="ia-locale")
assert carte.get_size()[0] == g.p.LARGEUR
assert cherche(carte, "autoriser").get_sensitive() is False  # port vide
champ.grab_focus()
pomper(0.1)
touches("1", "1", "a", "v", "4", "minus", "3", "r", "4")
assert champ.get_text() == "11434", champ.get_text()  # lettres et signes ignorés, r ne rafraîchit pas
assert e.port == "11434"
assert cherche(carte, "autoriser").get_sensitive() is True

# --- le tic de la mesure ne touche pas le champ --------------------------------------------
pn = carte.panneau
pn.derniere = g._mesure_scene(actif="openrouter")
pn.allume, pn.statut = False, "erreur"
for _ in range(3):
    pn._redessiner()
    carte._a_jour()
pomper(0.1)
assert cherche(carte, "port") is champ and champ.get_text() == "11434" and champ.has_focus()
touches("BackSpace", "BackSpace")
assert e.port == "114"
assert cherche(carte, "autoriser").get_sensitive() is True  # 114 reste un port

# --- P2 : ni collage, ni clic du milieu, ni glisser-déposer ------------------------------------
Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("22", -1)
pomper(0.1)
champ.emit("paste-clipboard")
pomper(0.1)
assert champ.get_text() == "114"
touches("ctrl+v")
assert champ.get_text() == "114"
assert champ.drag_dest_get_target_list() is None
carte.relacher()
carte.destroy()

# --- clavier qui n'est pas l'interne : rien n'entre, le bouton reste éteint -----------------------
carte, e, champ = ecran(Clavier(confiance=False), choix="ia-locale")
champ.grab_focus()
pomper(0.1)
touches("1", "1", "4", "3", "4")
assert champ.get_text() == "" and cherche(carte, "autoriser").get_sensitive() is False
carte.relacher()
carte.destroy()

# --- clavier interne non identifié : champ fermé, raison dite ---------------------------------


class SansClavier(Clavier):
    ok, raison = False, "raison d'essai"


carte, e, champ = ecran(SansClavier(), choix="ia-locale")
assert not champ.get_sensitive() and any("raison d'essai" in x for x in textes(carte))
assert not e.autorisable()
carte.relacher()
carte.destroy()

# --- autoriser : la commande reçoit le qube et le port, le bouton est en contour, sans défaut ---
carte, e, champ = ecran(Clavier(), choix="ia-locale", port="11434")
b = cherche(carte, "autoriser")
assert b.get_style_context().has_class("pa-contour") and not b.get_style_context().has_class("pa-principal")
assert not b.get_can_default() and not champ.get_activates_default()
appels = []
vrai_lancer, vraie_regle = v.lancer, carte.panneau.regle
v.lancer = lambda *a: appels.append(a) or True
g.confirmation.clavier_systeme = lambda: e.clavier
carte.panneau.rafraichir = lambda force=False: None
carte.panneau.regle = lambda: v.Regle("ia-locale", 11434)
b.clicked()
pomper(0.3)
assert appels == [("autoriser", "ia-locale", "11434")], appels
assert any("ia-locale, port 11434" in x for x in textes(carte)) and e.regle is not None
assert cherche(carte, "port") is None  # plus de champ tant que la règle existe

# --- retirer ------------------------------------------------------------------------------------
appels.clear()
carte.panneau.regle = lambda: None
carte._chercher("retirer").clicked()
pomper(0.3)
assert appels == [("retirer",)] and e.regle is None and e.port == ""
assert cherche(carte, "port") is not None
carte.panneau.retour()
assert carte.panneau.voisin is None
v.lancer = vrai_lancer
carte.relacher()
carte.destroy()

# --- échec de l'écriture : dit, rien n'est prétendu -----------------------------------------------
carte, e, champ = ecran(Clavier(), choix="work", port="8080")
v.lancer = lambda *a: False
carte.panneau.rafraichir = lambda force=False: None
carte.panneau.regle = lambda: None
cherche(carte, "autoriser").clicked()
pomper(0.3)
assert any("rien n'a été écrit" in x for x in textes(carte)) and e.regle is None
v.lancer = vrai_lancer
carte.relacher()
carte.destroy()

# --- l'état du qube autorisé suit dom0, pas la liste lue à l'ouverture (essai réel) -----------------
for marche, mot in ((True, "en marche"), (False, "éteint")):
    g.Panneau.voisin_marche = marche
    assert mot in list(textes(g.scene_voisin_autorise())), mot
g.Panneau.voisin_marche = None

# --- chemins d'accès et note de santé ---------------------------------------------------------
connexion = g._scene("mesure", connexion="local")
assert connexion._chercher("voisin") is not None
assert g._scene("mesure", connexion="openrouter")._chercher("voisin") is None
assert g._scene("ajout")._chercher("voisin") is not None
assert g._scene("ajout", allume=False)._chercher("voisin") is not None  # retrait sans sys-agent
assert any("réveillerait" in x for x in textes(g.scene_carte_local_voisin()))
assert not any("réveillerait" in x for x in textes(g.scene_carte_local()))
print("OK")
