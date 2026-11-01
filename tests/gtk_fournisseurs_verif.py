"""Vérifications GTK réelles de la liste des fournisseurs, lancées par test_agent_gtk.py
sous xvfb-run avec le Python du système (python3-gi). Imprime « OK » à la fin."""

import dataclasses
import os
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

from qaf_ui import usage as u  # noqa: E402
from qaf_ui.ecrans import agent_panneau as p  # noqa: E402
from qaf_ui.ecrans import agent_panneau_gtk as g  # noqa: E402

g.c.installer_style()


def pomper(duree=0.3):
    fin = time.monotonic() + duree
    while time.monotonic() < fin:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


def libelles(widget):
    if isinstance(widget, Gtk.Label):
        yield widget
    if isinstance(widget, Gtk.Container):
        enfants = []
        widget.forall(enfants.append)
        for e in enfants:
            yield from libelles(e)


def textes(carte):
    return [lab.get_text() for lab in libelles(carte)]


def montrer(carte):
    carte.show_all()
    pomper()
    return carte


def touche(carte, valeur):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = valeur
    ev.window = carte.get_window()
    carte._clavier(carte, ev)
    pomper(0.05)


def lanceur(pn):
    lances = []
    pn._lancer = lambda argv: lances.append(argv) or True
    return lances


# --- liste bornée dans la carte de 412 px, 35 lignes -----------------------------------------
carte = montrer(g._scene("ajout"))
assert carte.get_size()[0] == p.LARGEUR
assert carte.get_size()[1] < 500, carte.get_size()
adj = carte.defil.get_vadjustment()
assert adj.get_upper() > adj.get_page_size() > 200  # tout ne tient pas, la liste défile
visibles = textes(carte)
for cle in u.IDS:
    assert u.nom_fournisseur(cle) in visibles, cle
for section in ("ABONNEMENTS", "AUTRES CLI", "CLÉS API", "AUTO-HÉBERGÉ"):
    assert section in visibles

# Clavier : les flèches descendent toute la liste, et la zone suit le focus.
carte.set_focus(None)
carte._chercher("f-codex").grab_focus()
pomper(0.05)
parcourus = []
for _ in range(len(u.IDS) - 2):
    touche(carte, Gdk.KEY_j)
    parcourus.append(getattr(carte.get_focus(), "qaf_cle", None))
# Chaque ligne, dans l'ordre, sans jamais remonter, même avec le pointeur posé sur la liste.
assert parcourus == [f"f-{k}" for k in u.IDS[2:]], parcourus
assert adj.get_value() > 0, "la liste ne suit pas le focus"
assert carte.defil.get_vadjustment().get_value() + adj.get_page_size() > adj.get_upper() - 40
touche(carte, Gdk.KEY_k)
assert getattr(carte.get_focus(), "qaf_cle", None) == "f-synthetic"
assert textes(carte).count("Grok Bot") == 1

# Le tic de 5 s, et même une mesure identique, ne reconstruit rien.
adj.set_value(180)
pomper(0.05)
enfants, focus = carte.boite.get_children(), carte.get_focus()
carte.remplir()
carte.panneau._redessiner()
pomper(0.1)
assert carte.boite.get_children() == enfants and carte.get_focus() is focus and adj.get_value() == 180

# Un vrai changement reconstruit, sans perdre la position ni le focus clavier.
carte._chercher("f-zai").grab_focus()
pomper(0.05)
adj.set_value(240)
pomper(0.05)
pn = carte.panneau
pn.derniere = dataclasses.replace(pn.derniere, sante="inconnue")
pn._redessiner()
pomper(0.2)
assert carte.boite.get_children() != enfants
assert carte.get_focus() is not None and carte.get_focus().qaf_cle == "f-zai"
nouvelle = carte.defil.get_vadjustment()
assert nouvelle.get_value() > 0
carte.destroy()

# Un vrai geste de la souris déplace toujours le curseur.
carte = montrer(g._scene("ajout"))
carte.set_focus(None)
pomper(0.05)
cible = carte._chercher("f-kimi")
x, y = cible.translate_coordinates(carte, 20, 10)
ox, oy = carte.get_window().get_origin()[1:]
aff = Gdk.Display.get_default()
aff.get_default_seat().get_pointer().warp(aff.get_default_screen(), ox + x, oy + y)
pomper(0.2)
assert carte.get_focus() is cible
carte.destroy()

# --- la bascule : la coche suit la réponse de qaf.Fournisseur -----------------------------------
carte = montrer(g._scene("mesure"))
pn = carte.panneau
appels = []
vrai_basculer = u.basculer
u.basculer = lambda cle, *a, **k: appels.append(cle) or True
pn._console_ouverte = lambda: True
carte._chercher("f-openrouter").clicked()
assert pn.bascule == "openrouter" and pn.derniere.actif == "claude"
pomper(0.3)
assert appels == ["openrouter"] and pn.bascule is None and pn.derniere.actif == "openrouter"
assert pn.notes == ("carte.note_bascule", "carte.note_console")
vus = textes(carte)
assert "OpenRouter" in vus and "S'applique à la prochaine demande." in vus
assert "La console ouverte garde l'ancien fournisseur : referme-la." in vus
# Une mesure partie avant la bascule ne remet pas l'ancien actif.
ancienne = dataclasses.replace(pn.derniere, actif="claude")
pn._resultat(True, True, ancienne, 0, pn.heure_bascule - 1)
assert pn.derniere.actif == "openrouter"
# Réponse négative : la coche ne bouge pas, ligne en DANGER.
u.basculer = lambda cle, *a, **k: False
carte._chercher("f-claude").clicked()
pomper(0.3)
assert pn.derniere.actif == "openrouter" and pn.bascule_echec
assert "Bascule impossible : ton fournisseur n'a pas changé." in textes(carte)
carte.destroy()

# --- grokbot jamais activable, même si le qube le prétend -----------------------------------------
m = g._mesure_scene(fournisseurs=(("grokbot", "connecte"),), agents=("claude",))
carte = montrer(g._scene("ajout", m=dataclasses.replace(m, agents=("claude", "grokbot"))))
pn = carte.panneau
lances, appels = lanceur(pn), []
u.basculer = lambda cle, *a, **k: appels.append(cle) or True
pn.choisir("grokbot")
pn.basculer("grokbot")
pomper(0.1)
assert appels == [] and lances == [p.argv_connexion("grokbot")]
carte.destroy()

# --- « à configurer » et « non installé » ouvrent le terminal : l'installation s'y annonce ---------
carte = montrer(g._scene("ajout"))
pn = carte.panneau
lances = lanceur(pn)
carte._chercher("f-kimi").clicked()
assert lances == [p.argv_connexion("kimi")] and pn.connexion[0] == "kimi"
carte.destroy()

# --- connexion : un fournisseur déjà connecté ne ferme pas l'écran avant le résultat -------------
carte = montrer(g._scene("mesure", connexion="openrouter"))
pn = carte.panneau
assert pn.etat_carte() == "connexion"
apres = int(time.time()) + 60
pn._resultat(True, True, pn.derniere, 0, apres)
assert pn.connexion is not None and p.ecran_connexion(pn.derniere) == "attente"
pn._resultat(True, True, dataclasses.replace(pn.derniere, connexion="ok"), 0, apres)
pomper(0.1)
assert "OpenRouter est prêt" in textes(carte) and "Utiliser" in textes(carte)
assert "Test réussi. Ce fournisseur verra tout ce que lit l'agent." in textes(carte)
u.basculer = lambda cle, *a, **k: True
carte._chercher("utiliser").clicked()
pomper(0.3)
assert pn.connexion is None and pn.derniere.actif == "openrouter" and pn.etat_carte() == "mesure"
carte.destroy()

# Un ancien échec ne s'affiche pas sur le nouveau terminal.
carte = montrer(g._scene("mesure", connexion="openrouter", connexion_resultat="echec"))
pn = carte.panneau
assert "Test échoué" in textes(carte)
lanceur(pn)
carte._chercher("rouvrir").clicked()
pomper(0.05)
assert "Test échoué" not in textes(carte) and pn.etat_carte() == "connexion"
retard = pn.connexion[1] + 1  # mesure partie juste après le clic, avant l'effacement
pn._resultat(True, True, dataclasses.replace(pn.derniere, connexion="echec"), 0, retard)
pomper(0.05)
assert "Test échoué" not in textes(carte)
carte.destroy()

# --- sans compte Claude : la carte reste celle de l'actif --------------------------------------
carte = montrer(g._scene("vide", actif="openrouter"))
assert carte.panneau.etat_carte() == "mesure" and "OpenRouter" in textes(carte)
assert "FOURNISSEUR : OPENROUTER" in textes(carte)
carte.destroy()

# --- sys-agent arrêté : rien ne s'active ni ne s'ouvre -----------------------------------------
carte = montrer(g._scene("ajout", allume=False))
pn = carte.panneau
lances = lanceur(pn)
assert not any(carte._chercher(f"f-{k}") for k in ("codex", "agy", "kimi"))  # insensibles
pn.choisir("agy")
pn.connecter("kimi")
assert lances == []
carte.destroy()

u.basculer = vrai_basculer
print("OK")
