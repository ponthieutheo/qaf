"""qaf-suspension, partie GTK : publication de l'icône (qaf_ui.sni), sondage de
l'état, menu Gtk.Menu au clic. Logique et textes : suspension.py."""

import fcntl
import os
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from qaf_ui import sni  # noqa: E402
from qaf_ui.ecrans import suspension as logique  # noqa: E402
from qaf_ui.i18n import t  # noqa: E402


def images_alerte():
    """Cadenas ouvert ambre aux tailles courantes, en ARGB32 (IconPixmap)."""
    images = []
    for taille in logique.TAILLES:
        chargeur = GdkPixbuf.PixbufLoader.new_with_type("svg")
        chargeur.write(logique.cadenas_ouvert_svg(taille).encode())
        chargeur.close()
        pb = chargeur.get_pixbuf()
        images.append(sni.argb32(pb.get_width(), pb.get_height(), pb.get_pixels(),
                                 pb.get_rowstride(), pb.get_has_alpha()))
    return images


class Indicateur:
    def __init__(self, lire=logique.lire, lancer=logique.lancer):
        self.lire, self.lancer_commande = lire, lancer
        self.alerte = images_alerte()
        self.e = lire()
        self.element = sni.Element("qaf-suspension", t("susp.titre"), categorie="SystemServices")
        self.element.changer(**logique.apparence(self.e, self.alerte))  # avant publication : sans signal
        self.element.on_activer = self.clic_gauche
        self.element.on_menu = self.afficher_menu
        self.element.on_secondaire = self.afficher_menu
        self.en_cours = False
        self.menu = None
        self.publication = sni.publier(self.element)
        GLib.timeout_add_seconds(logique.SONDAGE_S, self.rafraichir)

    def rafraichir(self):
        self.e = self.lire()
        self.element.changer(**logique.apparence(self.e, self.alerte))
        return True

    def clic_gauche(self, x, y):
        self.rafraichir()
        if logique.clic_gauche(self.e) == "off":
            self.executer("off")
        else:
            self.afficher_menu(x, y)

    def executer(self, action):
        if self.en_cours:
            return
        self.en_cours = True

        def fil():
            ok, message = self.lancer_commande(action)
            GLib.idle_add(self.fini, ok, message)

        threading.Thread(target=fil, daemon=True).start()

    def fini(self, ok, message):
        self.en_cours = False
        self.rafraichir()
        if not ok:
            from qaf_ui.notif import notifier
            notifier(t("susp.notif"), message, "dialog-error")
        return False

    def afficher_menu(self, x, y):
        self.rafraichir()
        if self.menu is not None:
            self.menu.destroy()
        self.menu = m = Gtk.Menu()
        for libelle, action, actif in logique.menu(self.e):
            if libelle is None:
                m.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=libelle)
            item.set_sensitive(actif)
            if action is not None:
                item.connect("activate", lambda _i, a=action: self.executer(a))
            m.append(item)
        m.show_all()
        # Sans évènement déclencheur (le clic arrive par D-Bus), popup_at_rect
        # n'affiche rien : on place le menu nous-mêmes (ancienne API).
        m.popup(None, None, _position(x, y), None, 0, Gtk.get_current_event_time())


def _position(x, y):
    """Menu au point du clic, au-dessus quand le clic est dans la moitié basse
    de l'écran (barre en bas), jamais hors de l'écran. La taille est celle de
    la fenêtre du menu (marges du thème comprises), pas celle de la liste."""
    def placer(menu, _x, _y, _donnees=None):
        fenetre = menu.get_toplevel()
        largeur = max(fenetre.get_preferred_width()[1], menu.get_preferred_width()[1])
        hauteur = max(fenetre.get_preferred_height()[1], menu.get_preferred_height()[1])
        ecran = Gdk.Display.get_default()
        geo = (ecran.get_monitor_at_point(x, y) or ecran.get_primary_monitor()).get_geometry()
        px = min(max(x, geo.x), geo.x + geo.width - largeur)
        py = y - hauteur if y > geo.y + geo.height / 2 else y
        py = min(max(py, geo.y), geo.y + geo.height - hauteur)
        return px, py, False
    return placer


def _instance_unique():
    """Un seul indicateur par session : verrou dans XDG_RUNTIME_DIR."""
    dossier = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    fd = os.open(os.path.join(dossier, "qaf-suspension.verrou"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def lancer_indicateur(_args):
    verrou = _instance_unique()
    if verrou is None:
        print(t("susp.deja_lance"), file=sys.stderr)
        return 0
    # Menu en style Nuit pour ce processus seulement (thème GTK de la session inchangé).
    style = Gtk.CssProvider()
    style.load_from_data(logique.css_menu().encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Indicateur()
    Gtk.main()
    return 0
