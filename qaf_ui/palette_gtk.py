"""Rendu GTK3 de la palette (dom0). Toute la logique est dans palette.py.

Façon de faire reprise du menu Qubes 4.3 (qubes-desktop-linux-menu,
qubes_menu/appmenu.py) : Gtk.Application à instance unique (un second
lancement de qaf-palette referme la palette), CSS chargé pour l'écran avec
STYLE_PROVIDER_PRIORITY_APPLICATION, Échap et perte du focus ferment.
Pas de webview, aucune dépendance hors de PyGObject (livré dans dom0).
P2 : frappes filtrées par périphérique source (clavier interne seulement),
coller, clic du milieu et glisser-déposer désactivés dans le champ.
Agent jetable (docs/AGENT-JETABLE.md §8) : Entrée lance un agent neuf ; les
flèches haut/bas (clavier interne) choisissent un agent ouvert pour
« Continuer avec cet agent » (âge et influence affichés, tenus par dom0).
"""

import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import confirmation, palette  # noqa: E402
from .fenetre import appareil  # noqa: E402
from .i18n import t  # noqa: E402
from .nuit import icones, jetons  # noqa: E402

ID_APPLICATION = "fr.qaf.Palette"
LARGEUR = 720


def _style():
    fournisseur = Gtk.CssProvider()
    try:
        fournisseur.load_from_data(palette.CSS.encode("utf-8"))
    except GLib.Error:
        return  # thème par défaut plutôt que pas de palette du tout
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), fournisseur, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)  # devant le gtk.css de session


def _label(texte, classe, **kw):
    kw.setdefault("xalign", 0)
    lab = Gtk.Label(label=texte, **kw)
    lab.get_style_context().add_class(classe)
    return lab


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _logo_agent(taille=32):
    """Logo de l'agent (direction B) : carré arrondi à l'aplat LABEL_AGENT,
    filet SIGNATURE, tête de robot T1 au trait. Remplace l'étincelle « ✦ »."""
    zone = Gtk.DrawingArea()
    zone.set_size_request(taille, taille)
    zone.set_valign(Gtk.Align.CENTER)

    def dessiner(_w, cr):
        r, d = 8, taille - 1
        cr.new_sub_path()
        for cx, cy, a in ((d - r, r + 1, -1.5708), (d - r, d - r, 0), (r + 1, d - r, 1.5708), (r + 1, r + 1, 3.1416)):
            cr.arc(cx, cy, r, a, a + 1.5708)
        cr.close_path()
        cr.set_source_rgb(*_rgb(jetons.LABEL_AGENT))
        cr.fill_preserve()
        cr.set_source_rgb(*_rgb(jetons.SIGNATURE))
        cr.set_line_width(1.5)
        cr.stroke()
        cr.translate(taille * 0.2, taille * 0.2)
        cr.scale(taille * 0.6 / 16, taille * 0.6 / 16)
        icones.dessiner_tete_agent(cr, icones.PRET, jetons.T1, jetons.LABEL_AGENT)
        return False

    zone.connect("draw", dessiner)
    return zone


class Palette(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=ID_APPLICATION)
        self.fenetre = None
        self.envoi = False  # pendant l'envoi, ni Échap ni perte du focus ne ferment
        self.clavier = confirmation.clavier_systeme()  # clavier de confiance (P2)
        self.agents = []  # palette.AgentOuvert, lus auprès du Doorman à l'ouverture
        self.choix = -1  # -1 : agent neuf ; sinon indice dans self.agents

    def do_activate(self):
        if self.fenetre is not None and self.fenetre.is_visible():
            if not self.envoi:
                self.quit()  # second lancement : referme
            return
        if self.fenetre is None:
            _style()
            self._construire()
        self._placer()
        self.fenetre.show_all()
        self.fenetre.present()
        self.champ.grab_focus()

    def _construire(self):
        w = Gtk.ApplicationWindow(application=self, title=t("pal.titre"))
        w.set_decorated(False)
        w.set_resizable(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        w.set_default_size(LARGEUR, -1)
        ecran = w.get_screen()
        if ecran.get_rgba_visual() is not None and ecran.is_composited():
            w.set_visual(ecran.get_rgba_visual())  # coins arrondis transparents
        w.get_style_context().add_class("qaf-palette")
        w.connect("key-press-event", self._touche)
        w.connect("focus-out-event", lambda *_: self.envoi or self.quit())

        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ligne = Gtk.Box(spacing=12)
        ligne.get_style_context().add_class("qaf-champ")
        ligne.pack_start(Gtk.Image.new_from_icon_name("system-search-symbolic", Gtk.IconSize.MENU), False, False, 0)
        self.champ = Gtk.Entry(placeholder_text=t("pal.invite"), hexpand=True)
        self.champ.set_max_length(palette.MAX_TEXTE)
        self.champ.connect("activate", self._envoyer)
        # P2 : rien n'entre dans le champ sans frappe du clavier interne.
        self.champ.connect("paste-clipboard", lambda e: e.stop_emission_by_name("paste-clipboard"))
        self.champ.connect("button-press-event", lambda _e, ev: ev.button == 2)
        self.champ.drag_dest_unset()
        ligne.pack_start(self.champ, True, True, 0)
        ligne.pack_start(_label(t("pal.touche_echap"), "qaf-touche"), False, False, 0)
        boite.pack_start(ligne, False, False, 0)

        boite.pack_start(_label(t("pal.section_agent"), "qaf-section", margin_start=10, margin_top=10),
                         False, False, 0)
        agent = Gtk.Box(spacing=12)
        agent.get_style_context().add_class("qaf-agent")
        agent.pack_start(_logo_agent(), False, False, 0)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        textes.pack_start(_label(t("pal.demander"), "qaf-agent-titre"), False, False, 0)
        textes.pack_start(_label(t("pal.sous_titre"), "qaf-agent-sous-titre"), False, False, 0)
        agent.pack_start(textes, True, True, 0)
        agent.pack_start(_label(t("pal.touche_entree"), "qaf-touche", valign=Gtk.Align.CENTER), False, False, 0)
        boite.pack_start(agent, False, False, 0)

        self.titre_ouverts = _label(t("pal.agents_ouverts"), "qaf-section", margin_start=10, margin_top=10)
        self.liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        boite.pack_start(self.titre_ouverts, False, False, 0)
        boite.pack_start(self.liste, False, False, 0)
        self.titre_ouverts.set_no_show_all(True)
        threading.Thread(target=lambda: GLib.idle_add(self._ouverts, palette.agents_ouverts()), daemon=True).start()

        self.etat = _label("", "qaf-etat", wrap=True)
        boite.pack_start(self.etat, False, False, 0)
        if not self.clavier.ok:
            self.etat.set_text(t("pal.saisie_impossible", raison=self.clavier.raison))
            self.etat.get_style_context().add_class("erreur")

        pied = Gtk.Box(spacing=18)
        pied.get_style_context().add_class("qaf-pied")
        pied.pack_start(_label(t("pal.pied_entree"), "qaf-pied-texte"), False, False, 0)
        pied.pack_start(_label(t("pal.pied_echap"), "qaf-pied-texte"), False, False, 0)
        pied.pack_end(_label(t("pal.pied_coeur"), "qaf-pied-texte"), False, False, 0)
        boite.pack_start(pied, False, False, 0)
        w.add(boite)
        self.fenetre = w

    def _placer(self):
        affichage = Gdk.Display.get_default()
        moniteur = affichage.get_primary_monitor() or affichage.get_monitor(0)
        if moniteur is None:
            self.fenetre.set_position(Gtk.WindowPosition.CENTER)
            return
        g = moniteur.get_geometry()
        self.fenetre.move(g.x + max(0, (g.width - LARGEUR) // 2), g.y + int(g.height * 0.15))

    def _ouverts(self, agents):
        self.agents, self.choix = agents, -1
        self.titre_ouverts.set_visible(bool(agents))
        self._dessiner()
        return False

    def _dessiner(self):
        for enfant in self.liste.get_children():
            self.liste.remove(enfant)
        for i, a in enumerate(self.agents):
            lab = _label(("▸ " if i == self.choix else "  ") + a.ligne(), "qaf-agent-sous-titre", margin_start=22)
            self.liste.pack_start(lab, False, False, 0)
        self.liste.show_all()
        if self.choix >= 0:  # n14 : la suite reprend la conversation de cet agent
            consigne = t("pal.entree_suite", nom=self.agents[self.choix].nom)
        else:
            consigne = t("pal.entree_permanent") if palette.agent_permanent() else t("pal.entree_neuf")
        if self.clavier.ok:
            self._montrer(consigne, False)

    def _touche(self, _w, evenement):
        # Appelé avant le champ (signal de la fenêtre) : True arrête la frappe.
        if evenement.keyval == Gdk.KEY_Escape and not self.envoi:
            self.quit()
            return True
        if not palette.frappe_admise(self.clavier, Gdk.keyval_name(evenement.keyval) or "", appareil(evenement)):
            self._montrer(t("pal.frappe_ignoree"), True)
            return True
        if evenement.keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and self.agents and not self.envoi:
            pas = 1 if evenement.keyval == Gdk.KEY_Down else -1
            self.choix = (self.choix + 1 + pas) % (len(self.agents) + 1) - 1
            self._dessiner()
            return True
        return False

    def _montrer(self, texte, erreur):
        ctx = self.etat.get_style_context()
        (ctx.add_class if erreur else ctx.remove_class)("erreur")
        self.etat.set_text(texte)

    def _envoyer(self, _champ):
        if self.envoi:
            return
        texte = self.champ.get_text()
        refus = palette.envoi_admis(self.clavier, confirmation.clavier_systeme)
        if refus:
            self._montrer(refus, True)
            return
        self.envoi = True
        self.champ.set_sensitive(False)
        agent = self.agents[self.choix].nom if self.choix >= 0 else None
        self._montrer(t("pal.preparation") if agent is None else t("pal.envoi_agent", nom=agent), False)

        def travail():
            r = palette.demander(texte, agent=agent)
            GLib.idle_add(self._fini, r)

        threading.Thread(target=travail, daemon=True).start()

    def _fini(self, r):
        self.envoi = False
        self._montrer(r.message, not r.ok)
        if r.ok:
            GLib.timeout_add(1200, lambda: self.quit() or False)
        else:
            self.champ.set_sensitive(True)
            self.champ.grab_focus()
        return False


def lancer():
    return Palette().run([])
