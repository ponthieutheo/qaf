"""Icône de la zone de notification : StatusNotifierItem (org.kde.StatusNotifierItem).

Brique commune aux icônes QAF de la barre de Plasma (suspension des
confirmations, puis d'autres). Deux moitiés :

- `Element` : l'état de l'icône (nom d'icône du thème et/ou images ARGB32,
  infobulle, statut) et les rappels Python des clics. Sans D-Bus ni GTK :
  testé tel quel (tests/test_sni.py). Chaque changement dit quels signaux
  partent (NewIcon, NewAttentionIcon, NewToolTip, NewStatus, NewTitle).
- `publier(element)` : la publication sur le bus de session avec Gio (gi
  importé à l'appel seulement) : un nom org.kde.StatusNotifierItem-PID-N,
  l'objet /StatusNotifierItem, l'enregistrement auprès
  d'org.kde.StatusNotifierWatcher, refait à chaque (ré)apparition du watcher
  (plasmashell relancé). Il faut une boucle GLib (Gtk.main) qui tourne.

Pas de dbusmenu : la propriété Menu n'est pas publiée (facultative dans la
spécification), si bien que Plasma appelle ContextMenu(x, y) au clic droit ;
l'appli affiche son propre menu (Gtk.Menu). Pas de Menu = "/NO_DBUSMENU" (la
convention de KStatusNotifierItem) : le systemtray de Plasma 6 écrit alors
« DBusMenu disabled for this application » dans le journal à CHAQUE relecture
des propriétés, soit à chaque NewTitle/NewToolTip/NewIcon (toutes les 6 s
avec l'infobulle Wi-Fi de nm-applet republiée par qaf-zone, vu le 28/10).
Sans Menu, le chemin lu est vide : rien n'est écrit, et le clic droit
appelle ContextMenu (un seul avertissement, au clic).
"""

import os

INTERFACE = "org.kde.StatusNotifierItem"
CHEMIN = "/StatusNotifierItem"
WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_CHEMIN = "/StatusNotifierWatcher"
STATUTS = ("Passive", "Active", "NeedsAttention")
CATEGORIES = ("ApplicationStatus", "Communications", "SystemServices", "Hardware")

XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/>
<property name="IconThemePath" type="s" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="OverlayIconName" type="s" access="read"/>
<property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionIconName" type="s" access="read"/>
<property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionMovieName" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
<signal name="NewTitle"/>
<signal name="NewIcon"/>
<signal name="NewAttentionIcon"/>
<signal name="NewOverlayIcon"/>
<signal name="NewToolTip"/>
<signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

# Signature D-Bus de chaque propriété (XML ci-dessus).
SIGNATURES = {
    "Category": "s", "Id": "s", "Title": "s", "Status": "s", "WindowId": "i",
    "IconThemePath": "s", "IconName": "s", "IconPixmap": "a(iiay)",
    "OverlayIconName": "s", "OverlayIconPixmap": "a(iiay)",
    "AttentionIconName": "s", "AttentionIconPixmap": "a(iiay)", "AttentionMovieName": "s",
    "ToolTip": "(sa(iiay)ss)", "ItemIsMenu": "b",
}

# Champ d'Element -> (propriétés, signal émis quand il change).
_CHAMPS = {
    "titre": (("Title",), "NewTitle"),
    "icone": (("IconName",), "NewIcon"),
    "images": (("IconPixmap",), "NewIcon"),
    "attention_icone": (("AttentionIconName",), "NewAttentionIcon"),
    "attention_images": (("AttentionIconPixmap",), "NewAttentionIcon"),
    "infobulle": (("ToolTip",), "NewToolTip"),
    "statut": (("Status",), "NewStatus"),
}


def argb32(largeur, hauteur, pixels, rowstride=None, alpha=True):
    """Image du format IconPixmap : (largeur, hauteur, octets ARGB32 en ordre
    réseau, soit A R G B par pixel), depuis des pixels RGBA ou RGB par lignes
    (GdkPixbuf.get_pixels(), rowstride en octets)."""
    n = 4 if alpha else 3
    rowstride = rowstride or largeur * n
    if largeur <= 0 or hauteur <= 0 or len(pixels) < rowstride * (hauteur - 1) + largeur * n:
        raise ValueError("image vide ou tronquée")
    sortie = bytearray(largeur * hauteur * 4)
    o = 0
    for y in range(hauteur):
        ligne = pixels[y * rowstride:y * rowstride + largeur * n]
        for x in range(0, largeur * n, n):
            sortie[o] = ligne[x + 3] if alpha else 255
            sortie[o + 1:o + 4] = ligne[x:x + 3]
            o += 4
    return (largeur, hauteur, bytes(sortie))


class Element:
    """Une icône de la zone de notification, sans D-Bus.

    icone : nom d'icône du thème (le thème de Plasma la colore) ; images :
    liste de (largeur, hauteur, ARGB32) (voir argb32), prises quand le nom est
    vide ou introuvable. infobulle : (titre, texte). Rappels : on_activer(x, y)
    clic gauche, on_secondaire(x, y) clic du milieu, on_menu(x, y) clic droit,
    on_defiler(delta, orientation). Coordonnées : écran, en pixels.
    """

    def __init__(self, ident, titre, icone="", images=(), statut="Active",
                 categorie="ApplicationStatus", infobulle=("", "")):
        if categorie not in CATEGORIES:
            raise ValueError(f"catégorie inconnue : {categorie}")
        self.ident = ident
        self.categorie = categorie
        self.titre = titre
        self.icone = icone
        self.images = list(images)
        self.attention_icone = ""
        self.attention_images = []
        self.infobulle = tuple(infobulle)
        self._verifier_statut(statut)
        self.statut = statut
        self.on_activer = self.on_secondaire = self.on_menu = self.on_defiler = None
        self.emettre = lambda signal, args: None  # posé par publier()

    @staticmethod
    def _verifier_statut(statut):
        if statut not in STATUTS:
            raise ValueError(f"statut inconnu : {statut}")

    def changer(self, **champs):
        """Change des champs (titre, icone, images, attention_icone,
        attention_images, infobulle, statut) ; émet une fois chaque signal
        concerné, seulement si la valeur a changé. Renvoie les signaux émis."""
        signaux = []
        for nom, valeur in champs.items():
            if nom not in _CHAMPS:
                raise TypeError(f"champ inconnu : {nom}")
            if nom == "statut":
                self._verifier_statut(valeur)
            elif nom in ("images", "attention_images"):
                valeur = list(valeur)
            elif nom == "infobulle":
                valeur = tuple(valeur)
            if getattr(self, nom) == valeur:
                continue
            setattr(self, nom, valeur)
            signal = _CHAMPS[nom][1]
            if signal not in signaux:
                signaux.append(signal)
        for signal in signaux:
            self.emettre(signal, (self.statut,) if signal == "NewStatus" else None)
        return signaux

    def valeur(self, propriete):
        """Valeur Python d'une propriété, prête pour GLib.Variant(SIGNATURES[p], v)."""
        titre_ib, texte_ib = self.infobulle
        valeurs = {
            "Category": self.categorie, "Id": self.ident, "Title": self.titre,
            "Status": self.statut, "WindowId": 0, "IconThemePath": "",
            "IconName": self.icone, "IconPixmap": list(self.images),
            "OverlayIconName": "", "OverlayIconPixmap": [],
            "AttentionIconName": self.attention_icone, "AttentionIconPixmap": list(self.attention_images),
            "AttentionMovieName": "",
            "ToolTip": (self.icone, list(self.images), titre_ib, texte_ib),
            "ItemIsMenu": False,
        }
        if propriete not in valeurs:
            raise KeyError(propriete)
        return valeurs[propriete]

    def appeler(self, methode, args):
        """Méthode D-Bus reçue -> rappel Python. Faux si la méthode est inconnue."""
        rappels = {"Activate": self.on_activer, "SecondaryActivate": self.on_secondaire,
                   "ContextMenu": self.on_menu, "Scroll": self.on_defiler}
        if methode not in rappels:
            return False
        if rappels[methode] is not None:
            rappels[methode](*args)
        return True


def nom_de_service(pid=None, numero=1):
    """Nom de bus à nous, comme KStatusNotifierItem : org.kde.StatusNotifierItem-PID-N."""
    return f"{INTERFACE}-{os.getpid() if pid is None else pid}-{numero}"


_numero = 0


def publier(element, bus=None):
    """Publie l'élément sur le bus de session (Gio). Renvoie un objet avec
    retirer(). Le nom de bus est pris, l'objet exporté, puis l'élément
    s'enregistre auprès du watcher dès qu'il est là (et à chaque retour).
    Les rappels de l'élément tournent dans la boucle GLib."""
    import gi
    gi.require_version("Gtk", "3.0")  # jamais GTK 4 dans ce processus (test_nuit)
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    global _numero
    _numero += 1
    bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    service = nom_de_service(numero=_numero)
    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]

    def methode(_conn, _exp, _chemin, _iface, nom, params, invocation):
        try:
            connu = element.appeler(nom, tuple(params.unpack()))
        except Exception as ex:  # un rappel qui casse ne fait pas tomber l'icône
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(ex)[:200])
            return
        if connu:
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", nom)

    def propriete(_conn, _exp, _chemin, _iface, nom):
        return GLib.Variant(SIGNATURES[nom], element.valeur(nom))

    ident_objet = bus.register_object(CHEMIN, info, methode, propriete, None)

    def emettre(signal, args):
        params = GLib.Variant("(s)", args) if args else None
        try:
            bus.emit_signal(None, CHEMIN, INTERFACE, signal, params)
        except GLib.Error:
            pass

    element.emettre = emettre

    def enregistrer(_conn=None, _nom=None, _proprio=None):
        bus.call(WATCHER, WATCHER_CHEMIN, WATCHER, "RegisterStatusNotifierItem",
                 GLib.Variant("(s)", (service,)), None, Gio.DBusCallFlags.NONE, 5000, None, None, None)

    # Le watcher de KDE ignore un nom pas encore pris : on ne guette le watcher
    # qu'une fois le nom acquis ; il rappelle enregistrer() à chaque apparition.
    veille = []

    def nom_acquis(_conn, _nom):
        if not veille:
            veille.append(Gio.bus_watch_name_on_connection(
                bus, WATCHER, Gio.BusNameWatcherFlags.NONE, enregistrer, None))

    ident_nom = Gio.bus_own_name_on_connection(bus, service, Gio.BusNameOwnerFlags.NONE, nom_acquis, None)

    class Publication:
        nom = service

        @staticmethod
        def retirer():
            for v in veille:
                Gio.bus_unwatch_name(v)
            bus.unregister_object(ident_objet)
            Gio.bus_unown_name(ident_nom)
            element.emettre = lambda signal, args: None

    return Publication()
