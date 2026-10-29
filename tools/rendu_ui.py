"""Rendu hors écran d'un écran QAF vers PNG, et mesure des contrastes sur les pixels.

Outil de la machine de développement (jamais installé dans dom0). À lancer sous xvfb-run, par
tools/rendu-ui.sh : jamais de fenêtre sur l'écran réel (pas de WSLg).

  python3 tools/rendu_ui.py ECRAN sortie.png [--contraste rapport.json] [--exiger] [--langue fr|en|zh]

ECRAN : « module:fonction », p. ex. « agent_demo:actif » ou
« rendu_confirmation:renforcee » (modules de tests/) ; la fonction, sans
argument, renvoie une fenêtre GTK.

Contraste : pour chaque Gtk.Label visible, le fond est la couleur la plus
fréquente du rectangle du libellé, le texte le pixel le plus contrasté ;
ratio WCAG (qaf_ui.nuit.contraste). Un libellé coupé par une zone qui défile
n'est pas mesuré. --exiger : code 1 si un libellé est sous
4,5:1. --langue : langue des écrans (qaf_ui.i18n) ; sans elle, celle de la
locale, comme dans dom0.
"""

import argparse
import collections
import importlib
import json
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [RACINE, os.path.join(RACINE, "tests")]

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from qaf_ui.nuit import contraste, jetons  # noqa: E402

BARRE = 28  # hauteur de la barre du haut de dom0 (Xfce), retirée de la place utile


def bureau():
    """Fond plein écran couleur du bureau, pour situer la fenêtre (--plein).
    Sans gestionnaire de fenêtres, la zone de travail est l'écran entier : la
    barre du haut n'est pas simulée, son débordement est compté à part (BARRE)."""
    w = Gtk.Window()
    w.set_decorated(False)
    geo = w.get_screen().get_display().get_monitor(0).get_geometry()
    w.set_default_size(geo.width, geo.height)
    w.move(0, 0)
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(f"window.qaf-bureau {{ background-color: {jetons.MUR}; }}".encode())
    w.get_style_context().add_provider(fournisseur, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    w.get_style_context().add_class("qaf-bureau")
    w.show_all()
    attendre(100)
    return w


def construire(ecran):
    module, _, fonction = ecran.partition(":")
    return getattr(importlib.import_module(module), fonction or "construire")()


def attendre(ms):
    fini = []
    GLib.timeout_add(ms, lambda: fini.append(1) or False)
    while not fini:
        Gtk.main_iteration_do(True)


def libelles(widget):
    if isinstance(widget, Gtk.Label):
        yield widget
    if isinstance(widget, Gtk.Container):
        enfants = []
        widget.forall(enfants.append)
        for e in enfants:
            yield from libelles(e)


def partie_visible(lab, fenetre, x0, y0, larg, haut):
    """Rectangle du libellé réellement visible : coupé par chaque zone de
    défilement parente. None si moins des 3/4 de sa hauteur se voient (un
    libellé à moitié sorti d'un défilement n'a pas de fond mesurable)."""
    x1, y1 = x0 + larg, y0 + haut
    parent = lab.get_parent()
    while parent is not None and parent is not fenetre:
        if isinstance(parent, Gtk.ScrolledWindow):
            pa = parent.get_allocation()
            px, py = parent.translate_coordinates(fenetre, 0, 0) or (0, 0)
            x0, y0 = max(x0, px), max(y0, py)
            x1, y1 = min(x1, px + pa.width), min(y1, py + pa.height)
        parent = parent.get_parent()
    if x1 <= x0 or (y1 - y0) < 0.75 * haut:
        return None
    return x0, y0, x1, y1


def mesurer(fenetre, pixbuf):
    px, pas, n = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
    larg, haut = pixbuf.get_width(), pixbuf.get_height()
    rapport = []
    for lab in libelles(fenetre):
        if not lab.get_mapped() or not lab.get_text().strip():
            continue
        a = lab.get_allocation()
        x0, y0 = lab.translate_coordinates(fenetre, 0, 0) or (0, 0)
        visible = partie_visible(lab, fenetre, x0, y0, a.width, a.height)
        if visible is None:
            continue  # libellé caché sous un défilement : il n'est pas à l'écran
        vx0, vy0, vx1, vy1 = visible
        couleurs = collections.Counter()
        for y in range(max(0, vy0), min(haut, vy1)):
            base = y * pas
            for x in range(max(0, vx0), min(larg, vx1)):
                i = base + x * n
                couleurs[(px[i], px[i + 1], px[i + 2])] += 1
        if not couleurs:
            continue
        fond = couleurs.most_common(1)[0][0]
        texte = max(couleurs, key=lambda c: contraste.ratio(c, fond))
        r = contraste.ratio(texte, fond)
        rapport.append({"texte": lab.get_text()[:60], "ratio": round(r, 2),
                        "fond": "#%02X%02X%02X" % fond, "encre": "#%02X%02X%02X" % texte,
                        "ok": r >= contraste.TEXTE_MIN})
    return rapport


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("ecran")
    p.add_argument("sortie")
    p.add_argument("--contraste")
    p.add_argument("--exiger", action="store_true")
    p.add_argument("--plein", action="store_true",
                   help="capture tout l'écran virtuel (fond du bureau derrière la fenêtre)")
    p.add_argument("--langue", choices=("fr", "en", "zh"), help="langue des écrans (défaut : locale)")
    a = p.parse_args(argv)
    if a.langue:
        from qaf_ui import i18n
        i18n.choisir(a.langue)
    if not os.environ.get("DISPLAY", "").startswith(":") or os.environ.get("WAYLAND_DISPLAY"):
        print("refus : à lancer sous xvfb-run (tools/rendu-ui.sh), jamais sur l'écran réel", file=sys.stderr)
        return 2
    fond = bureau() if a.plein else None
    fenetre = construire(a.ecran)
    if fond is not None:
        fenetre.set_transient_for(fond)
    fenetre.show_all()
    fenetre.set_focus_visible(True)
    attendre(100)
    # Pointeur sur le coin haut gauche de la fenêtre (en-tête, sans survol) : sinon Xvfb le
    # laisse au centre de l'écran et la ligne dessous prend l'aspect « survolé ». Il reste
    # sur la fenêtre, qui garde donc le focus clavier (sans gestionnaire de fenêtres).
    try:
        ox, oy = fenetre.get_window().get_origin()[1:]
        dsp = Gdk.Display.get_default()
        dsp.get_default_seat().get_pointer().warp(dsp.get_default_screen(), ox + 2, oy + 2)
    except (AttributeError, TypeError):
        pass
    attendre(500)
    larg, haut = fenetre.get_size()
    pixbuf = Gdk.pixbuf_get_from_window(fenetre.get_window(), 0, 0, larg, haut)
    ecran = fenetre.get_screen()
    geo = ecran.get_display().get_monitor(0).get_geometry()
    el, eh = geo.width, geo.height
    deborde = larg > el or haut > eh - BARRE
    if fond is not None:
        Gdk.pixbuf_get_from_window(ecran.get_root_window(), 0, 0, el, eh).savev(a.sortie, "png", [], [])
    else:
        pixbuf.savev(a.sortie, "png", [], [])
    print(f"{a.sortie} : fenêtre {larg}x{haut} sur écran {el}x{eh}"
          + (f" — DÉBORDE (barre du haut {BARRE} px comprise)" if deborde else ""))
    code = 0
    if a.contraste or a.exiger:
        rapport = mesurer(fenetre, pixbuf)
        faibles = [r for r in rapport if not r["ok"]]
        if a.contraste:
            with open(a.contraste, "w", encoding="utf-8") as f:
                json.dump(rapport, f, ensure_ascii=False, indent=1)
        mini = min((r["ratio"] for r in rapport), default=0)
        print(f"contraste : {len(rapport)} libellés mesurés, minimum {mini}:1, {len(faibles)} sous 4,5:1")
        for r in faibles:
            print(f"  {r['ratio']}:1  {r['encre']} sur {r['fond']}  « {r['texte']} »")
        code = 1 if a.exiger and faibles else 0
    return code


if __name__ == "__main__":
    sys.exit(main())
