"""Composants GTK 3 communs aux écrans QAF de la brique agent (docs/DESIGN-SESSION.md §3 à §13).

Importé seulement par les écrans au lancement (gi n'est jamais chargé par la
logique ni par les tests sans GTK). Règles tenues ici pour tous les écrans :
- tout texte passe par set_text / Gtk.Label(label=…) sans balisage : un nom
  de qube, une note ou un message d'erreur n'est jamais du Pango ;
- chaque élément interactif prend le focus clavier et a un nom accessible ;
- aucune couleur ici : tout vient du CSS généré (css.py) ou des jetons lus au
  moment de dessiner.

Briques (à réutiliser telles quelles, sans réinventer) :

  Style et fenêtres
  installer_style()                    CSS des écrans QAF + Papirus-Dark, une fois par processus
  FenetreAF(titre, largeur, hauteur)   écran QAF (panneau de l'agent) : fond SURFACE, Échap ferme
  SurfaceAF(largeur, x, y, rappel_fermer)  carte flottante (carte de l'agent) : POPUP, bordure
                                       2 px BORD, Échap et clic dehors ferment ; .saisir() prend
                                       clavier et souris (Gdk.Seat.grab), .fermer()
  Listes
  Liste(nom_accessible)                Gtk.ListBox au clavier ; .ajouter(widget), .vider()
  EnTeteSection(texte)                 11 px 500 T3, casse normale, 28 px dont 10 au-dessus
  Contrôles (§11)
  Bouton(texte, genre, rappel, icone)  32 px, rayon 4 ; genre : PRINCIPAL (un par écran),
                                       SECONDAIRE, DANGER (CHAMP + texte corail)
  Icônes et marques
  Icone(nom, taille, classe)           icône QAF au trait (icones.py), couleur du CSS
  TeteAgent(etat, taille)              tête de robot + badge d'état (§6)
  BadgeInfluence(taille)               badge « influencé » seul (disque percé d'un « ! »), 12 px
  libelle(texte, *classes)             Gtk.Label en texte brut
"""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from . import css, icones, jetons  # noqa: E402
from ..i18n import t  # noqa: E402

# Priorité de notre CSS : juste au-dessus de celle du gtk.css de l'utilisateur.
PRIORITE_STYLE = Gtk.STYLE_PROVIDER_PRIORITY_USER + 1

PRINCIPAL, SECONDAIRE, DANGER = "principal", "secondaire", "danger"
_style_installe = False


def installer_style(ecran=None):
    """Charge le CSS des écrans QAF pour l'écran, dans CE processus seulement
    (aucun fichier de thème de Qubes, de KDE ni de GTK touché). Renvoie la
    liste des erreurs de CSS."""
    global _style_installe
    erreurs = []
    if _style_installe:
        return erreurs
    reglages = Gtk.Settings.get_default()
    if reglages is not None:
        reglages.set_property("gtk-application-prefer-dark-theme", True)
        if any(os.path.isdir(os.path.join(d, "icons", jetons.THEME_ICONES))
               for d in GLib.get_system_data_dirs() + [GLib.get_user_data_dir()]):
            reglages.set_property("gtk-icon-theme-name", jetons.THEME_ICONES)
    fournisseur = Gtk.CssProvider()
    fournisseur.connect("parsing-error", lambda _p, _s, err: erreurs.append(err.message))
    try:
        fournisseur.load_from_data(css.generer().encode("utf-8"))
    except GLib.Error as e:
        erreurs.append(e.message)
        return erreurs  # thème par défaut plutôt que pas d'écran du tout
    # Au-dessus du gtk.css de la session (priorité USER) : sinon « entry:focus » et
    # « list » de la session passaient devant nos règles (GTK 3 compare la priorité
    # avant la spécificité). Nos règles restent limitées à .qaf-fenetre.
    Gtk.StyleContext.add_provider_for_screen(ecran or Gdk.Screen.get_default(), fournisseur, PRIORITE_STYLE)
    _style_installe = True
    return erreurs



def _classes(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        if c:
            ctx.add_class(c)
    return widget


def _nom_accessible(widget, nom):
    acc = widget.get_accessible()
    if acc is not None:
        acc.set_name(nom)


def libelle(texte, *classes, **kw):
    """Gtk.Label en texte brut (jamais de balisage Pango)."""
    kw.setdefault("xalign", 0)
    lab = Gtk.Label(**kw)
    lab.set_use_markup(False)
    lab.set_text("" if texte is None else str(texte))
    return _classes(lab, *classes)


def _hexa(rgba):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in (rgba.red, rgba.green, rgba.blue))


MARGE_ECRAN = jetons.MARGE_ECRAN  # px laissés autour d'une fenêtre QAF sur un petit écran


def zone_utile():
    """Zone de travail de l'écran principal (sans la barre du haut), ou None."""
    affichage = Gdk.Display.get_default()
    if affichage is None:
        return None
    moniteur = affichage.get_primary_monitor() or affichage.get_monitor(0)
    return None if moniteur is None else moniteur.get_workarea()


def borner(largeur, hauteur, zone):
    """Taille voulue ramenée à la zone de travail moins une marge : sur un écran
    1366x768, une fenêtre de 790 px ne passe plus sous la barre ni hors de
    l'écran (son contenu défile). -1 (taille naturelle) reste -1."""
    if zone is None:
        return largeur, hauteur
    if largeur > 0:
        largeur = min(largeur, zone.width - 2 * MARGE_ECRAN)
    if hauteur > 0:
        hauteur = min(hauteur, zone.height - 2 * MARGE_ECRAN)
    return largeur, hauteur


class FenetreAF(Gtk.ApplicationWindow):
    """Écran QAF de premier niveau (panneau de l'agent) : fond SURFACE, opaque
    et rectangulaire, centré sur l'écran principal, Échap ferme (sauf si
    fermable=False pendant une action)."""

    def __init__(self, titre, largeur=720, hauteur=-1, application=None):
        super().__init__(title=titre)
        if application is not None:
            self.set_application(application)
        installer_style(self.get_screen())
        self.fermable = True
        self.set_decorated(False)
        self.set_default_size(*borner(largeur, hauteur, zone_utile()))
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        _classes(self, "qaf-fenetre")
        self.connect("key-press-event", self._touche)
        self.connect("realize", lambda *_: self.centrer())

    def centrer(self):
        g = zone_utile()
        if g is None:
            return
        largeur, hauteur = self.get_size()
        self.move(g.x + max(0, (g.width - largeur) // 2), g.y + max(0, (g.height - hauteur) // 2))

    def _touche(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape and self.fermable:
            self.close()
            return True
        return False


class SurfaceAF(Gtk.Window):
    """Surface QAF flottante (carte de l'agent dans la barre) : fenêtre POPUP
    (override-redirect : aucun gestionnaire de fenêtres ne la décore), fond
    SURFACE, bordure 2 px BORD, hauteur = contenu. Échap ou un clic hors de la
    surface appelle fermer() ; rappel_fermer() remplace la destruction par
    défaut. Si un compositeur tourne (KWin, relu à chaque création), visuel
    RGBA et coins de 8 px dessinés par GTK (classe qaf-arrondi), pour que la
    bordure suive le coin ; sinon coins droits. app_paintable reste faux :
    GtkWindow ne dessine son fond et sa bordure CSS que dans ce cas."""

    def __init__(self, largeur, x=None, y=None, rappel_fermer=None, titre="QAF"):
        super().__init__(type=Gtk.WindowType.POPUP)
        installer_style(self.get_screen())
        self.set_title(titre)
        self.set_size_request(largeur, -1)
        self.set_resizable(False)
        _classes(self, "qaf-fenetre", "qaf-surface")
        ecran = self.get_screen()
        visuel = ecran.get_rgba_visual() if ecran.is_composited() else None
        if visuel is not None:
            self.set_visual(visuel)
            _classes(self, "qaf-arrondi")
        # La bordure CSS d'une fenêtre GTK 3 ne réserve pas de place : le contenu
        # passerait dessus. Le cadre de 2 px est réservé ici.
        self.set_border_width(jetons.BORD_LARGEUR)
        if x is not None and y is not None:
            self.move(x, y)
        self._rappel_fermer = rappel_fermer
        self._saisie = False
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event", self._touche)
        self.connect("button-press-event", self._clic)

    def saisir(self):
        """Prend clavier et souris (comme rofi) une fois la fenêtre montrée.
        Renvoie True si la saisie est obtenue."""
        fenetre = self.get_window()
        if fenetre is None:
            return False
        siege = Gdk.Display.get_default().get_default_seat()
        etat = siege.grab(fenetre, Gdk.SeatCapabilities.ALL, True, None, None, None, None)
        self._saisie = etat == Gdk.GrabStatus.SUCCESS
        return self._saisie

    def relacher(self):
        if self._saisie:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._saisie = False

    def fermer(self):
        self.relacher()
        if self._rappel_fermer is not None:
            self._rappel_fermer()
        else:
            self.destroy()

    def _touche(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.fermer()
            return True
        return False

    def _clic(self, _w, ev):
        a = self.get_allocation()
        if not (0 <= ev.x < a.width and 0 <= ev.y < a.height) or ev.window != self.get_window():
            self.fermer()
            return True
        return False


# --- icônes ---------------------------------------------------------------------------
_CACHE_ICONES = {}


def _pixbuf_icone(nom, couleur, taille):
    cle = (nom, couleur, taille)
    pb = _CACHE_ICONES.get(cle)
    if pb is None:
        chargeur = GdkPixbuf.PixbufLoader.new_with_type("svg")
        chargeur.set_size(taille, taille)
        try:
            chargeur.write(icones.svg(nom, couleur, taille).encode("utf-8"))
            chargeur.close()
        except GLib.Error as e:
            print(f"qaf : icône {nom} non rendue ({e.message})", file=sys.stderr)
            return None
        pb = chargeur.get_pixbuf()
        if len(_CACHE_ICONES) > 512:  # borne : on ne garde pas tout
            _CACHE_ICONES.clear()
        _CACHE_ICONES[cle] = pb
    return pb


class Icone(Gtk.DrawingArea):
    """Icône QAF au trait (qaf_ui/nuit/icones.py), dessinée dans la couleur que
    le CSS donne au widget : ICONE au repos (classe qaf-icone), T3 désactivé."""

    def __init__(self, nom, taille=jetons.ICONE_TAILLE, classe="qaf-icone"):
        if nom not in icones.TRACES:
            raise KeyError(f"icône inconnue : {nom}")
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.nom, self.taille = nom, taille
        self.set_size_request(taille, taille)
        _classes(self, classe)
        self.connect("draw", self._dessiner)
        self.connect("style-updated", lambda *_: self.queue_draw())
        self.connect("state-flags-changed", lambda *_: self.queue_draw())

    def couleur(self):
        ctx = self.get_style_context()
        return _hexa(ctx.get_color(ctx.get_state()))

    def _dessiner(self, _w, cr):
        pb = _pixbuf_icone(self.nom, self.couleur(), self.taille)
        if pb is not None:
            x = (self.get_allocated_width() - self.taille) // 2
            y = (self.get_allocated_height() - self.taille) // 2
            Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
            cr.paint()
        return False


class TeteAgent(Gtk.DrawingArea):
    """Tête de robot au trait + badge d'état (§6). etat : icones.PRET,
    TRAVAIL, CONFIRMER, INFLUENCE, PAUSE, ARRETE. Le trait prend la couleur du
    CSS (ICONE ; T3 à l'arrêt) ; le badge, la couleur figée de son état ; le
    détourage, le fond de la case (`fond` : nom d'un jeton)."""

    def __init__(self, etat=icones.PRET, taille=jetons.ICONE_TAILLE, fond="SURFACE", classe="qaf-icone"):
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.etat, self.taille, self.fond = etat, taille, fond
        self.set_size_request(taille, taille)
        _classes(self, classe)
        self.connect("draw", self._dessiner)
        self.connect("style-updated", lambda *_: self.queue_draw())

    def changer(self, etat):
        self.etat = etat
        self.queue_draw()

    def couleur(self):
        """Couleur du trait (celle du CSS, T3 à l'arrêt)."""
        if self.etat == icones.ARRETE:
            return jetons.T3
        ctx = self.get_style_context()
        return _hexa(ctx.get_color(ctx.get_state()))

    def _dessiner(self, _w, cr):
        jeton = icones.COULEUR_BADGE.get(self.etat)
        cr.translate((self.get_allocated_width() - self.taille) / 2,
                     (self.get_allocated_height() - self.taille) / 2)
        cr.scale(self.taille / 16, self.taille / 16)
        icones.dessiner_tete_agent(cr, self.etat, self.couleur(), getattr(jetons, self.fond),
                                   getattr(jetons, jeton) if jeton else None)
        return False


class BadgeInfluence(Gtk.DrawingArea):
    """Badge « influencé » de la tête de l'agent (§6), seul, en 12 px : disque
    DANGER percé d'un « ! » couleur du fond. Se pose devant le mot
    « influencé » (panneau de l'agent) ; jamais un triangle (forme de Risqué)."""

    def __init__(self, taille=12, fond="SURFACE"):
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.taille, self.fond = taille, fond
        self.set_size_request(taille, taille)
        self.connect("draw", self._dessiner)

    def _dessiner(self, _w, cr):
        x = (self.get_allocated_width() - self.taille) / 2
        y = (self.get_allocated_height() - self.taille) / 2
        r = self.taille * 4 / 12  # 4 px à 12 px : le badge de la tête (r = 3 sur 16), un peu grandi
        icones.dessiner_badge_influence(cr, x + self.taille / 2, y + self.taille / 2, r,
                                        jetons.DANGER, getattr(jetons, self.fond))
        return False


# --- listes -----------------------------------------------------------------------------------

class EnTeteSection(Gtk.Label):
    """En-tête de section : 11 px Medium T3, casse normale, 28 px dont 10 au-dessus."""

    def __init__(self, texte):
        super().__init__(xalign=0)
        self.set_use_markup(False)
        self.set_text(str(texte))
        _classes(self, "qaf-section")


class Liste(Gtk.ListBox):
    """Liste au clavier, qu'on lit sans y choisir : Tab entre et sort de la
    liste, les flèches défilent (comportement GTK)."""

    def __init__(self, nom_accessible=None):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_activate_on_single_click(False)
        _classes(self, "qaf-liste")
        _nom_accessible(self, nom_accessible or t("comp.liste"))

    def do_focus(self, direction):
        """Tab entre dans la liste sur sa première ligne et en ressort au Tab
        suivant ; les flèches gardent le comportement GTK. Sans cela,
        GtkListBox fait passer Tab de ligne en ligne."""
        if direction not in (Gtk.DirectionType.TAB_FORWARD, Gtk.DirectionType.TAB_BACKWARD):
            return Gtk.ListBox.do_focus(self, direction)
        haut = self.get_toplevel()
        focus = haut.get_focus() if isinstance(haut, Gtk.Window) else None
        if focus is not None and (focus is self or focus.is_ancestor(self)):
            return False  # déjà dedans : Tab sort
        ligne = next((r for r in self.get_children() if r.get_child_visible() and r.get_visible()), None)
        if ligne is None:
            return False
        ligne.grab_focus()
        if isinstance(haut, Gtk.Window) and haut.get_focus() is not ligne:
            return Gtk.ListBox.do_focus(self, direction)  # GTK a refusé : comportement d'origine
        return True

    def ajouter(self, widget):
        ligne = Gtk.ListBoxRow()
        ligne.set_can_focus(True)
        ligne.add(widget)
        self.add(ligne)
        return ligne

    def vider(self):
        for ligne in self.get_children():
            self.remove(ligne)


# --- contrôles (§11) ---------------------------------------------------------------------------

class Bouton(Gtk.Button):
    """32 px, rayon 4, 13 px Medium. PRINCIPAL : ivoire sur texte BARRE (un par
    écran au plus) ; SECONDAIRE : CHAMP + contour FILET ; DANGER : CHAMP +
    texte DANGER. Focus clavier : anneau 2 px T2 décalé de 2 px."""

    def __init__(self, texte, genre=SECONDAIRE, rappel=None, icone=None):
        if genre not in (PRINCIPAL, SECONDAIRE, DANGER):
            raise ValueError(f"genre de bouton inconnu : {genre}")
        super().__init__()
        boite = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        if icone:
            boite.pack_start(Icone(icone) if icone in icones.TRACES
                             else Gtk.Image.new_from_icon_name(icone, Gtk.IconSize.MENU), False, False, 0)
        self.libelle = libelle(texte, xalign=0.5)
        boite.pack_start(self.libelle, False, False, 0)
        self.add(boite)
        self.set_can_focus(True)
        _nom_accessible(self, str(texte))
        self.genre = genre
        _classes(self, "qaf-bouton", f"qaf-{genre}")
        if rappel is not None:
            self.connect("clicked", lambda _b: rappel())
