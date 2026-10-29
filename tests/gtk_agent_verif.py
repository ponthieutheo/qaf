"""Vérifications GTK réelles du panneau agent, lancées par test_agent_gtk.py
sous xvfb-run avec le Python du système (python3-gi). Imprime « OK » à la fin."""

import os
import sys
import time

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [RACINE, os.path.join(RACINE, "tests")]

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import agent_demo as demo  # noqa: E402
from qaf_ui.ecrans import agent  # noqa: E402
from qaf_ui.ecrans import agent_etat as ae  # noqa: E402
from qaf_ui.nuit import composants as c  # noqa: E402


def pomper(duree=0.8):
    """Laisse finir les fils (gestes, lecture) et leurs GLib.idle_add."""
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


assert c.installer_style() == []

# 1. État actif : Pause d'urgence visible, Reprendre caché, fil en phrases.
gestes = []
hostile = '<span foreground="red">vault</span> & <b>'
ev = demo.scenario_actif() + [
    (200, {"source": "validateur", "event": {"type": "refus", "appelant": "sys-agent", "raison": hostile}})]
w = agent.construire(source=demo.source(ev), basculer=lambda m: gestes.append(m) or ae.Resultat(True, hostile))
w.show_all()
pomper()
p = w.panneau
assert p.b_pause.get_visible() and not p.b_reprendre.get_visible()
textes = [lab.get_text() for lab in libelles(w)]
assert "a créé l'espace dev" in textes and "refusé : une demande mal formée" in textes
assert not any(lab.get_use_markup() for lab in libelles(w))
assert not any("N4" in t or "R-" in t or "<span" in t for t in textes), textes
# L'étiquette Agent ne dépend d'aucun caractère (✦ absent d'Inter) : tête de
# robot dessinée en Cairo (DESIGN-SESSION §6), plus d'étincelle.
assert not any("✦" in t for t in textes), textes
assert "Agent" in textes


def widgets(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        enfants = []
        widget.forall(enfants.append)
        for e in enfants:
            yield from widgets(e)


tetes = [x for x in widgets(w) if isinstance(x, c.TeteAgent)]
assert tetes and all(x.get_allocated_width() >= 16 for x in tetes), tetes

# 1 bis. Tab atteint les trois gestes visibles ; chacun a un nom accessible.
for b in (p.b_demander, p.b_pause):
    assert b.get_can_focus() and b.get_accessible().get_name()
vus = set()
w.set_focus(None)
for _ in range(200):
    w.child_focus(Gtk.DirectionType.TAB_FORWARD)
    vus.add(w.get_focus())
assert p.b_demander in vus and p.b_pause in vus, [type(x).__name__ for x in vus]

# 2. Pause d'urgence : geste par basculer(True), message posé tel quel.
p.b_pause.clicked()
pomper()
assert gestes == [True], gestes
assert p.message.get_text() == hostile and not p.message.get_use_markup()

# 3. « Demander à l'agent… » : ouvre la palette (argv fixe), aucune demande d'ici.
lances = []
p.lancer = lambda argv, **k: lances.append(argv)
p.b_demander.clicked()
pomper()
assert lances == [["qaf-palette"]] and p.message.get_text() == "qaf-palette ouvert.", (lances, p.message.get_text())

# 3 bis. Agents ouverts (registre du Doorman) : une ligne par agent, influence écrite.
w5 = agent.construire(source=demo.source(demo.scenario_attente(), agents=demo.INFLUENCE))
w5.show_all()
pomper()
t5 = [lab.get_text() for lab in libelles(w5)]
assert w5.panneau.liste_agents.get_visible() and "disp6000" in t5 and "disp5859" in t5, t5
assert "2 agents ouverts, chacun dans un jetable" in t5, t5
assert any(t.startswith("disp6000 a lu du contenu") for t in t5), t5
# L'influence se dit une fois : le mot sur la ligne de l'agent influencé, jamais « session propre ».
assert t5.count("influencé") == 1 and "session propre" not in t5 and not any("▲" in t for t in t5), t5
assert w5.panneau.b_pause.get_sensitive()
# Filet entre « Agents ouverts » et « Ce qu'il a fait », aligné sur le texte des lignes
# (36 px du bord de la liste).
filet = w5.panneau.filet_agents
assert filet.get_mapped()
x_filet = filet.translate_coordinates(w5, 0, 0)[0]
x_liste = w5.panneau.liste_agents.translate_coordinates(w5, 0, 0)[0]
assert x_filet - x_liste == agent.j.RETRAIT_SEPARATEUR, (x_filet, x_liste)
w6 = agent.construire(source=demo.source(demo.scenario_actif(), agents=[]))
w6.show_all()
pomper()
t6 = [lab.get_text() for lab in libelles(w6)]
assert not w6.panneau.liste_agents.get_visible() and "Aucun agent ouvert" in t6, t6
assert not w6.panneau.filet_agents.get_visible()  # pas de filet orphelin
# Prêt : aucun agent ne tourne, la pause d'urgence est éteinte ; rien à dire de l'influence.
assert not w6.panneau.b_pause.get_sensitive() and not w6.panneau.influence.get_visible()

# 4. En pause : Reprendre visible, geste basculer(False).
w2 = agent.construire(source=demo.source(demo.scenario_pause(), en_pause=True),
                      basculer=lambda m: gestes.append(m) or ae.Resultat(False, "refusé"))
w2.show_all()
pomper()
p2 = w2.panneau
assert p2.b_reprendre.get_visible() and not p2.b_pause.get_visible()
# En pause : « Reprendre » est le seul principal, « Demander » est éteint.
assert "qaf-principal" in p2.b_reprendre.get_style_context().list_classes()
assert "qaf-principal" not in p2.b_demander.get_style_context().list_classes()
assert not p2.b_demander.get_sensitive()
assert p2.b_reprendre.libelle.get_mapped(), "bouton sans texte"
p2.b_reprendre.clicked()
pomper()
assert gestes[-1] is True, "la reprise ne doit rien lancer avant confirmation"
assert p2.confirmation.get_visible() and not p2.b_reprendre.get_visible()
p2.confirmation.non.clicked()
pomper()
assert gestes[-1] is True and p2.b_reprendre.get_visible() and not p2.confirmation.get_visible()
p2.b_reprendre.clicked()
p2.confirmation.oui.clicked()
pomper()
assert gestes[-1] is False and "qaf-erreur" in p2.message.get_style_context().list_classes()

# 5. Journal illisible : le panneau le dit, sans planter.
w3 = agent.construire(source=lambda: ae.Instantane(erreur="droit de lecture manquant", portier=True))
w3.show_all()
pomper()
assert w3.panneau.alerte.get_visible() and "relance" in w3.panneau.alerte.get_text()

# 5 bis. Mode développement : la ligne rouge n'apparaît que si l'exception de dev est posée.
assert not w3.panneau.dev.get_visible()
w5 = demo.permanent_dev()
w5.show_all()
pomper()
assert w5.panneau.dev.get_visible() and "qaf-dev" in w5.panneau.dev.get_style_context().list_classes()
assert w5.panneau.dev.get_text().startswith(("Mode développement", "Development mode"))
w5.panneau.appliquer(ae.Instantane(portier=True, agents=[], permanent=True))
assert not w5.panneau.dev.get_visible()
w5.destroy()

# 6. Rafraîchissement dans un fil : une source qui lève ne casse rien.
w3.panneau.source = lambda: 1 / 0
w3.panneau.rafraichir()
pomper()
assert w3.panneau.lecture_en_cours is False

# 8. Le fil change : la liste est refaite, Tab en sort toujours. (Sous xvfb sans
# gestionnaire de fenêtres, seule la dernière fenêtre montrée suit Tab.)
w4 = agent.construire(source=demo.source(demo.scenario_actif()))
w4.show_all()
pomper()
p4 = w4.panneau
p4.source = demo.source(demo.scenario_attente())
p4.rafraichir()
pomper()
assert "t'a demandé : changer l'accès Internet de loisirs" in [lab.get_text() for lab in libelles(w4)]
vus = set()
w4.set_focus(None)
for _ in range(200):
    w4.child_focus(Gtk.DirectionType.TAB_FORWARD)
    vus.add(w4.get_focus())
assert p4.b_demander in vus and p4.b_pause in vus, [type(x).__name__ for x in vus]
avant = p4.liste.get_children()
p4.appliquer(p4.source())  # rien de neuf : la liste n'est pas refaite (focus gardé)
assert p4.liste.get_children() == avant

print("OK")
