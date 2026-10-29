"""Accès X11 minimal par ctypes (libX11 de dom0), sans GTK et sans paquet.

Pour le panneau de l'agent dans la barre (qaf_ui/ecrans/agent_panneau*.py) :
lire les propriétés posées par le gestionnaire de fenêtres (_NET_CLIENT_LIST,
_NET_WM_NAME) et par le démon d'affichage de Qubes (_QUBES_VMNAME), et
demander l'activation d'une fenêtre (_NET_ACTIVE_WINDOW).

Une connexion X à part, distincte de celle de GDK, ouverte pour un geste puis
refermée.

Erreurs X : le gestionnaire d'erreurs de Xlib est global au processus. Celui
installé ici ne traite que NOTRE connexion (erreur notée, jamais de sortie du
processus) et passe toutes les autres au gestionnaire précédent, celui de GDK.
Il faut donc ouvrir GDK avant Affichage() (import de Gtk).

Rien ici n'interprète une valeur lue : ce sont des octets ou des entiers, que
la logique (agent_panneau.py) valide avant usage.
"""

import ctypes
import ctypes.util

CLIENT_MESSAGE = 33
ANY_PROPERTY_TYPE = 0
MAX_LONGS = 1 << 16  # au plus 256 Kio lus par propriété


class _Any(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong)]


class _Message(ctypes.Structure):
    _fields_ = _Any._fields_ + [("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                                ("data", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xany", _Any), ("xclient", _Message), ("pad", ctypes.c_long * 24)]


class _ErreurX(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
                ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_GESTIONNAIRE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_ErreurX))
_x = None
_nos_connexions = {}  # adresse Display* -> Affichage
_precedent = None
_gestionnaire = None  # gardé en vie (sinon ctypes libère le rappel)


def _charger():
    global _x
    if _x is not None:
        return _x
    chemin = ctypes.util.find_library("X11") or "libX11.so.6"
    x = ctypes.CDLL(chemin)
    vp, ul, ci, cl = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long
    x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [vp]
    x.XDefaultScreen.argtypes = [vp]
    x.XRootWindow.restype, x.XRootWindow.argtypes = ul, [vp, ci]
    x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, ci]
    x.XGetWindowProperty.argtypes = [vp, ul, ul, cl, cl, ci, ul, ctypes.POINTER(ul), ctypes.POINTER(ci),
                                     ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(vp)]
    x.XFree.argtypes = [vp]
    x.XSendEvent.argtypes = [vp, ul, ci, cl, ctypes.POINTER(_XEvent)]
    x.XFlush.argtypes = [vp]
    x.XSetErrorHandler.restype, x.XSetErrorHandler.argtypes = _GESTIONNAIRE, [_GESTIONNAIRE]
    _x = x
    return x


def _sur_erreur(affichage, erreur):
    cible = _nos_connexions.get(affichage)
    if cible is not None:
        cible.erreurs += 1
        return 0
    if _precedent:
        return _precedent(affichage, erreur)
    return 0


class Affichage:
    """Une connexion X à nous. nom=None : $DISPLAY."""

    def __init__(self, nom=None):
        global _precedent, _gestionnaire
        x = _charger()
        self._x = x
        self.dpy = x.XOpenDisplay(nom.encode() if nom else None)
        if not self.dpy:
            raise OSError("affichage X introuvable")
        self.erreurs = 0
        _nos_connexions[self.dpy] = self
        if _gestionnaire is None:
            _gestionnaire = _GESTIONNAIRE(_sur_erreur)
            _precedent = x.XSetErrorHandler(_gestionnaire)
        self.racine = x.XRootWindow(self.dpy, x.XDefaultScreen(self.dpy))
        self._atomes = {}

    def fermer(self):
        if self.dpy:
            _nos_connexions.pop(self.dpy, None)
            self._x.XCloseDisplay(self.dpy)
            self.dpy = None

    # --- atomes et propriétés ---------------------------------------------

    def atome(self, nom):
        a = self._atomes.get(nom)
        if a is None:
            a = self._atomes[nom] = self._x.XInternAtom(self.dpy, nom.encode("ascii"), 0)
        return a

    def _lire(self, fenetre, nom):
        """(type, format, octets bruts, nombre) ou None (absente, fenêtre disparue)."""
        x = self._x
        type_, fmt = ctypes.c_ulong(), ctypes.c_int()
        nb, reste, p = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        avant = self.erreurs
        r = x.XGetWindowProperty(self.dpy, fenetre, self.atome(nom), 0, MAX_LONGS, 0, ANY_PROPERTY_TYPE,
                                 ctypes.byref(type_), ctypes.byref(fmt), ctypes.byref(nb),
                                 ctypes.byref(reste), ctypes.byref(p))
        if r != 0 or self.erreurs != avant or not type_.value:
            if p.value:
                x.XFree(p)
            return None
        try:
            n = nb.value
            if fmt.value == 32:  # côté client, format 32 = tableau de long C
                brut = ctypes.string_at(p, n * ctypes.sizeof(ctypes.c_long)) if n else b""
            else:
                brut = ctypes.string_at(p, n * (fmt.value // 8)) if n else b""
            return type_.value, fmt.value, brut, n
        finally:
            if p.value:
                x.XFree(p)

    def lire_entiers(self, fenetre, nom):
        """Liste d'entiers non signés d'une propriété (format 8, 16 ou 32), ou None.
        Le démon d'affichage de Qubes pose _QUBES_LABEL en format 8 selon les
        versions : les trois formats sont acceptés."""
        lu = self._lire(fenetre, nom)
        if lu is None:
            return None
        type_c = {8: ctypes.c_ubyte, 16: ctypes.c_ushort, 32: ctypes.c_ulong}.get(lu[1])
        if type_c is None:
            return None
        tableau = (type_c * lu[3]).from_buffer_copy(lu[2]) if lu[3] else []
        return [v & 0xFFFFFFFF for v in tableau]

    def lire_octets(self, fenetre, nom):
        """Octets d'une propriété de format 8 (STRING, UTF8_STRING), ou None."""
        lu = self._lire(fenetre, nom)
        if lu is None or lu[1] != 8:
            return None
        return lu[2]

    # --- messages -----------------------------------------------------------

    def envoyer(self, destination, fenetre, type_nom, donnees, masque, propager=False):
        """ClientMessage de format 32 (5 entiers au plus)."""
        ev = _XEvent()
        m = ev.xclient
        m.type, m.send_event, m.display, m.window = CLIENT_MESSAGE, 1, self.dpy, fenetre
        m.message_type, m.format = self.atome(type_nom), 32
        for i, v in enumerate(list(donnees)[:5]):
            m.data[i] = v
        self._x.XSendEvent(self.dpy, destination, 1 if propager else 0, masque, ctypes.byref(ev))

    def vider(self):
        self._x.XFlush(self.dpy)

