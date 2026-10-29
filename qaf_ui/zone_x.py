"""qaf-zone : remplace xembedsniproxy dans la session Plasma (X11) de dom0.

Tient la zone de notification XEmbed (_NET_SYSTEM_TRAY_S0) comme le fait
xembedsniproxy, et republie chaque icône ancrée en StatusNotifierItem, avec
l'image propre calculée par zone.py (fond blanc des icônes de qube retiré,
encre à la couleur du label, trait de label ; icônes de dom0 inchangées).

Mécanique reprise de xembedsniproxy (plasma-workspace) :
- chaque icône est reparentée dans un conteneur à nous (override-redirect,
  redirigé par Composite, opacité 0 : jamais visible), ajoutée au save-set
  (si qaf-zone meurt, X la rend à la racine et son programme se réancre) ;
- l'image est relue à chaque Damage (et toutes les 3 s, au cas où) ;
- un clic de Plasma (Activate, ContextMenu, SecondaryActivate, Scroll) place
  le conteneur sous le pointeur, au-dessus de tout, et rejoue le bouton par
  XTest, puis le renvoie dessous : l'icône d'origine reçoit un vrai clic ;
- une icône que le gestionnaire de fenêtres met dans un cadre (course au
  redémarrage de qaf-zone, vue le 28/10) est réancrée (zone.reprendre).

Lancé par le drop-in /etc/systemd/user/plasma-xembedsniproxy.service.d/
50-qaf-zone.conf (packaging/qaf-zone.conf), à la place de xembedsniproxy, avec
les mêmes dépendances et le même cycle de vie. Repli : si l'environnement
manque (pas d'extension, pas de bus) ou après ECHECS_MAX plantages dans la
session, qaf-zone se remplace par /usr/bin/xembedsniproxy (exec) : la zone de
notification n'est jamais perdue.

python-xlib (python3-xlib, dépendance de python3-qubesadmin, toujours là dans
dom0) et gi ne sont importés qu'au lancement.
"""

import os
import sys
import time

from . import sni, zone

OPCODE_DOCK = 0
XEMBED_EMBEDDED_NOTIFY = 0
RELECTURE_MS = 3000


class Icone:
    """Une icône ancrée : sa fenêtre, notre conteneur, son objet SNI."""

    def __init__(self, client, conteneur, vm, label):
        self.client, self.conteneur = client, conteneur
        self.vm, self.label = vm, label
        self.taille = (zone.TAILLE, zone.TAILLE)
        self.profondeur = 24
        self.lu = None
        self.pixmap = None  # (l, h, octets ARGB)
        self.ident, self.titre = "icone", ""
        self.element = None      # sni.Element
        self.publication = None  # sni.publier(...)
        self.connexion = None    # connexion D-Bus à elle (un nom de bus par icône)
        self.degats = None


class Zone:
    def __init__(self):
        from Xlib import X, display
        from Xlib.ext.damage import DamageNotify
        self.DamageNotify = DamageNotify
        import gi
        gi.require_version("Gtk", "3.0")  # jamais GTK 4 dans ce processus (test_nuit)
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.X, self.GLib, self.Gio = X, GLib, Gio
        self.d = display.Display()
        for ext in ("Composite", "DAMAGE", "XTEST"):
            if not self.d.has_extension(ext):
                raise RuntimeError(f"extension X absente : {ext}")
        # DAMAGE refuse toute requête d'un client qui n'a pas d'abord demandé
        # sa version (erreur que python-xlib décode mal : blocage, vu le 28/10).
        self.d.damage_query_version()
        self.d.composite_query_version()
        self.ecran = self.d.screen()
        self.racine = self.ecran.root
        self.a = {n: self.d.intern_atom(n) for n in (
            "_NET_SYSTEM_TRAY_S%d" % self.d.get_default_screen(), "_NET_SYSTEM_TRAY_OPCODE",
            "_NET_SYSTEM_TRAY_VISUAL", "_NET_SYSTEM_TRAY_ORIENTATION", "MANAGER", "_XEMBED",
            "_NET_WM_WINDOW_OPACITY", "_NET_WM_NAME", "UTF8_STRING", "_QUBES_VMNAME",
            "_QUBES_LABEL_COLOR", "WM_NAME", "WM_CLASS", "_XEMBED_INFO")}
        self.selection = self.a["_NET_SYSTEM_TRAY_S%d" % self.d.get_default_screen()]
        self.adresse_bus = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.icones = {}  # id client -> Icone
        self.par_conteneur = {}
        self.reprises = {}  # id client -> réancrages après prise par le WM (zone.reprendre)
        self.boucle = GLib.MainLoop()
        self.d.set_error_handler(self._erreur_x)

    # --- X ------------------------------------------------------------------
    def _erreur_x(self, erreur, requete):
        # Fenêtre partie entre-temps : normal pour une icône qui se ferme.
        print(f"qaf-zone : erreur X ignorée ({erreur.__class__.__name__})", file=sys.stderr)

    def demarrer(self):
        from Xlib import Xatom
        X = self.X
        self.gestionnaire = self.racine.create_window(-1, -1, 1, 1, 0, X.CopyFromParent,
                                                      override_redirect=1, event_mask=X.StructureNotifyMask)
        visuel = next((v.visual_id for p in self.ecran.allowed_depths if p.depth == 32 for v in p.visuals), None)
        if visuel is not None:
            self.gestionnaire.change_property(self.a["_NET_SYSTEM_TRAY_VISUAL"], Xatom.VISUALID, 32, [visuel])
        self.gestionnaire.change_property(self.a["_NET_SYSTEM_TRAY_ORIENTATION"], Xatom.CARDINAL, 32, [0])  # horizontale
        self.gestionnaire.set_selection_owner(self.selection, X.CurrentTime)
        self.d.sync()
        if self.d.get_selection_owner(self.selection) != self.gestionnaire:
            raise RuntimeError("sélection _NET_SYSTEM_TRAY_S0 non obtenue")
        from Xlib.protocol import event
        ev = event.ClientMessage(window=self.racine, client_type=self.a["MANAGER"],
                                 data=(32, [X.CurrentTime, self.selection, self.gestionnaire.id, 0, 0]))
        self.racine.send_event(ev, event_mask=X.StructureNotifyMask)
        self.d.flush()
        self.GLib.io_add_watch(self.d.fileno(), self.GLib.PRIORITY_DEFAULT, self.GLib.IO_IN, self._x_pret)
        self.GLib.timeout_add(RELECTURE_MS, self._relire_tout)
        self._pomper()

    def _x_pret(self, *_):
        self._pomper()
        return True

    def _pomper(self):
        """Traite les événements X en file (python-xlib en lit aussi pendant
        l'attente d'une réponse : la file est vidée après chaque action)."""
        while self.d.pending_events():
            try:
                self._evenement(self.d.next_event())
            except Exception as e:  # une icône fautive ne fait pas tomber la zone
                print(f"qaf-zone : événement ignoré ({e!r})", file=sys.stderr)
        self.d.flush()
        return False

    def _evenement(self, ev):
        X = self.X
        t = ev.type
        if t == X.ClientMessage and ev.client_type == self.a["_NET_SYSTEM_TRAY_OPCODE"]:
            _, donnees = ev.data
            if donnees[1] == OPCODE_DOCK:
                self.ancrer(donnees[2])
        elif t == X.SelectionClear and ev.atom == self.selection:
            print("qaf-zone : zone de notification reprise par un autre programme, fin.", file=sys.stderr)
            self.arreter()
        elif t == X.DestroyNotify:
            self.retirer(ev.window.id)
        elif t == X.ReparentNotify:
            wid = ev.window.id
            ic = self.icones.get(wid)
            if ic is not None and ev.parent.id != ic.conteneur.id:
                self.retirer(wid)
                n = self.reprises.get(wid, 0)
                if zone.reprendre(ev.parent.id, self.racine.id, ic.conteneur.id, n):
                    self.reprises[wid] = n + 1
                    print(f"qaf-zone : icône {wid:#x} prise par le gestionnaire de fenêtres, réancrée", file=sys.stderr)
                    self.GLib.timeout_add(zone.REPRISE_MS, self._reprendre, wid)
        elif t == X.ConfigureNotify:
            ic = self.icones.get(ev.window.id)
            if ic is not None and (ev.width, ev.height) != ic.taille:
                self._dimensionner(ic, ev.width, ev.height)
        elif t == X.PropertyNotify:
            ic = self.icones.get(ev.window.id)
            if ic is not None and ev.atom in (self.a["WM_NAME"], self.a["_NET_WM_NAME"]):
                self._textes(ic)
        elif isinstance(ev, self.DamageNotify):
            self.d.damage_subtract(ev.damage, 0, 0)
            ic = self.icones.get(ev.drawable.id)
            if ic is not None:
                self._relire(ic)

    def _reprendre(self, wid):
        """Réancre une icône sortie de notre conteneur par le gestionnaire de
        fenêtres (zone.reprendre), si elle existe encore et reste une icône
        XEmbed. Comme une demande d'ancrage reçue de son programme."""
        if wid not in self.icones:
            fenetre = self.d.create_resource_object("window", wid)
            if self._prop(fenetre, "_XEMBED_INFO") is not None:
                self.ancrer(wid)
        self._pomper()
        return False

    def _prop(self, fenetre, nom):
        try:
            p = fenetre.get_full_property(self.a[nom], 0)
        except Exception:
            return None
        return p.value if p is not None else None

    def ancrer(self, wid):
        from Xlib import Xatom
        X = self.X
        if wid in self.icones or wid == 0:
            return
        client = self.d.create_resource_object("window", wid)
        try:
            g = client.get_geometry()
        except Exception:
            return
        vm = self._prop(client, "_QUBES_VMNAME")
        vm = bytes(vm).decode("utf-8", "replace") if vm is not None else ""
        couleur = self._prop(client, "_QUBES_LABEL_COLOR")
        label = int(couleur[0]) & 0xFFFFFF if vm and couleur is not None and len(couleur) else None
        if vm and label is None:
            label = 0x000000  # qube sans couleur lue : encre neutre, jamais celle de dom0
        l, h = zone.taille_ancrage(g.width, g.height)
        conteneur = self.racine.create_window(0, 0, l, h, 0, X.CopyFromParent, override_redirect=1,
                                              event_mask=X.SubstructureNotifyMask)
        conteneur.change_property(self.a["_NET_WM_WINDOW_OPACITY"], Xatom.CARDINAL, 32, [0])
        conteneur.composite_redirect_window(1, onerror=self._ignorer())  # manuel : jamais peint
        conteneur.map()
        conteneur.configure(stack_mode=X.Below)
        client.change_save_set(X.SetModeInsert, onerror=self._ignorer())
        client.change_attributes(event_mask=X.StructureNotifyMask | X.PropertyChangeMask, onerror=self._ignorer())
        client.reparent(conteneur, 0, 0, onerror=self._ignorer())
        if (l, h) != (g.width, g.height):
            client.configure(width=l, height=h, onerror=self._ignorer())
        from Xlib.protocol import event
        client.send_event(event.ClientMessage(window=client, client_type=self.a["_XEMBED"],
                                              data=(32, [X.CurrentTime, XEMBED_EMBEDDED_NOTIFY, 0,
                                                         conteneur.id, 0])), onerror=self._ignorer())
        client.map(onerror=self._ignorer())
        ic = Icone(client, conteneur, vm, label)
        ic.taille, ic.profondeur = (l, h), g.depth
        ic.degats = client.damage_create(3)  # DamageReportNonEmpty
        self.icones[wid] = ic
        self.par_conteneur[conteneur.id] = ic
        self._textes(ic)
        self.d.sync()
        self._relire(ic)
        self._publier(ic)

    def _ignorer(self):
        from Xlib import error
        return error.CatchError()

    def _dimensionner(self, ic, l, h):
        """Nouvelle taille demandée par l'icône (un qube la choisit, via le démon
        d'affichage) : bornée par zone.taille_ancrage avant tout usage, et
        l'icône est remise à la taille bornée."""
        bornee = zone.taille_ancrage(l, h)
        if bornee != (l, h):
            ic.client.configure(width=bornee[0], height=bornee[1], onerror=self._ignorer())
        if bornee == ic.taille:
            return
        ic.taille = bornee
        ic.conteneur.configure(width=bornee[0], height=bornee[1], onerror=self._ignorer())
        self._relire(ic)

    def _textes(self, ic):
        nom = self._prop(ic.client, "_NET_WM_NAME") or self._prop(ic.client, "WM_NAME") or b""
        classe = self._prop(ic.client, "WM_CLASS") or b""
        parts = bytes(classe).split(b"\0")
        classe = parts[0] or (parts[1] if len(parts) > 1 else b"")  # nom d'instance : « sys-net:nm-applet »
        ic.ident, ic.titre = zone.textes(bytes(nom).decode("utf-8", "replace"),
                                         classe.decode("utf-8", "replace"), ic.vm)
        if ic.element is not None:  # l'Id reste celui de la publication
            ic.element.changer(titre=ic.titre, infobulle=(ic.titre, ""))

    def _relire(self, ic):
        l, h = zone.taille_lue(*ic.taille)  # jamais plus de COTE_MAX x COTE_MAX
        try:
            im = ic.client.get_image(0, 0, l, h, self.X.ZPixmap, 0xFFFFFFFF)
        except Exception:
            return
        octets = bytes(im.data)
        if octets == ic.lu:
            return
        ic.lu = octets
        pas = len(octets) // h if h else 0
        if ic.vm:
            img = zone.icone_qube(l, h, pas, octets, ic.label)
        else:
            img = zone.icone_dom0(l, h, pas, octets, ic.profondeur)
        if img is not None:
            ic.pixmap = img
            if ic.element is not None:
                ic.element.changer(images=[img])  # NewIcon

    def _relire_tout(self):
        for ic in list(self.icones.values()):
            self._relire(ic)
        self._pomper()
        return True

    def retirer(self, wid):
        ic = self.icones.pop(wid, None)
        if ic is None:
            return
        self.par_conteneur.pop(ic.conteneur.id, None)
        self._depublier(ic)
        ic.conteneur.destroy(onerror=self._ignorer())
        self.d.flush()

    def rendre(self, ic):
        """Fin propre : l'icône repart à la racine, cachée, sans être fermée
        (son programme se réancre au prochain gestionnaire)."""
        ic.client.unmap(onerror=self._ignorer())
        ic.client.reparent(self.racine, 0, 0, onerror=self._ignorer())
        ic.conteneur.destroy(onerror=self._ignorer())

    def cliquer(self, ic, bouton, fois=1, attente=0):
        """Clic XTest sur l'icône d'origine. Le bouton rejoué par XTest va à la
        fenêtre sous le pointeur réel : le conteneur est placé sous celui-ci
        (les coordonnées passées par Plasma ne servent pas). Plasma appelle
        ContextMenu bouton encore enfoncé : tant qu'un bouton est tenu, sa
        saisie implicite prendrait notre clic ; on attend qu'il soit relâché
        (zone.attendre_boutons)."""
        X = self.X
        if self.icones.get(ic.client.id) is not ic:
            return False
        p = self.racine.query_pointer()
        delai = zone.attendre_boutons(p.mask, attente)
        if delai:
            self.GLib.timeout_add(delai, self.cliquer, ic, bouton, fois, attente + delai)
            return False
        x, y = p.root_x, p.root_y
        if os.environ.get("QAF_ZONE_TRACE"):
            print(f"qaf-zone : bouton {bouton} x{fois} en {x},{y} sur {ic.client.id:#x}", file=sys.stderr)
        l, h = ic.taille
        ic.conteneur.configure(x=x - l // 2, y=y - h // 2, stack_mode=X.Above, onerror=self._ignorer())
        self.d.sync()
        for _ in range(fois):
            self.d.xtest_fake_input(X.ButtonPress, bouton)
            self.d.xtest_fake_input(X.ButtonRelease, bouton)
        self.d.sync()
        ic.conteneur.configure(stack_mode=X.Below, onerror=self._ignorer())
        self._pomper()
        return False

    # --- D-Bus : un StatusNotifierItem par icône (qaf_ui/sni.py) ------------
    def _publier(self, ic):
        """Publie l'icône avec sni.publier, sur une connexion à elle : un nom de
        bus et un objet /StatusNotifierItem par icône, comme xembedsniproxy."""
        Gio = self.Gio
        e = sni.Element(ic.ident, ic.titre, images=[ic.pixmap] if ic.pixmap else (),
                        infobulle=(ic.titre, ""))
        e.on_activer = lambda x, y: self.cliquer(ic, 1)
        e.on_secondaire = lambda x, y: self.cliquer(ic, 2)
        e.on_menu = lambda x, y: self.cliquer(ic, 3)

        def defiler(delta, orientation):
            bouton, fois = zone.bouton_defilement(delta, orientation)
            if bouton:
                self.cliquer(ic, bouton, fois)
        e.on_defiler = defiler
        drapeaux = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        ic.connexion = Gio.DBusConnection.new_for_address_sync(self.adresse_bus, drapeaux, None, None)
        ic.element = e
        ic.publication = sni.publier(e, bus=ic.connexion)

    def _depublier(self, ic):
        if ic.publication is not None:
            ic.publication.retirer()
            ic.publication = None
        if ic.connexion is not None:
            ic.connexion.close(None, None, None)
            ic.connexion = None

    # --- vie du processus ---------------------------------------------------
    def tourner(self):
        import signal
        for s in (signal.SIGTERM, signal.SIGINT):
            self.GLib.unix_signal_add(self.GLib.PRIORITY_HIGH, s, self._signal_fin)
        self.boucle.run()

    def _signal_fin(self):
        self.arreter()
        return False

    def arreter(self):
        for ic in list(self.icones.values()):
            self._depublier(ic)
            self.rendre(ic)
        self.icones.clear()
        try:
            self.d.sync()
        except Exception:
            pass
        self.boucle.quit()


def _repli(raison):
    print(f"qaf-zone : {raison} ; repli sur xembedsniproxy.", file=sys.stderr)
    sys.stderr.flush()
    if os.access(zone.XEMBEDSNIPROXY, os.X_OK):
        os.execv(zone.XEMBEDSNIPROXY, [zone.XEMBEDSNIPROXY])
    return 1


def main():
    import faulthandler
    import signal
    faulthandler.register(signal.SIGUSR1)  # pile de chaque fil sur stderr (diagnostic)
    if zone.lire_echecs() >= zone.ECHECS_MAX:
        return _repli(f"{zone.ECHECS_MAX} échecs dans cette session")
    try:
        z = Zone()
        z.demarrer()
    except Exception as e:
        zone.noter_echec()
        return _repli(f"démarrage impossible ({e!r})")
    debut = time.monotonic()
    try:
        z.tourner()
    except Exception as e:
        zone.noter_echec()
        print(f"qaf-zone : arrêt sur erreur ({e!r})", file=sys.stderr)
        return 1
    if time.monotonic() - debut > 600:
        zone.oublier_echecs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
