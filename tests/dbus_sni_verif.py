"""Publication réelle de qaf_ui.sni sur un bus de session privé, lancée par
test_sni.py sous dbus-run-session avec le Python du système (python3-gi).
Un faux org.kde.StatusNotifierWatcher, sur une autre connexion, note les
enregistrements, lit les propriétés, appelle Activate, écoute NewStatus, puis
« redémarre » (rend son nom et le reprend) : l'icône doit se réenregistrer.
Imprime « OK » à la fin."""

import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [RACINE, os.path.join(RACINE, "tests")]

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from qaf_ui import sni  # noqa: E402

XML_WATCHER = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
</interface></node>"""

boucle = GLib.MainLoop()
inscrits = []


def attendre(condition, delai=5.0):
    fin = GLib.get_monotonic_time() + int(delai * 1e6)
    ctx = boucle.get_context()
    while not condition() and GLib.get_monotonic_time() < fin:
        ctx.iteration(False)
    assert condition(), "délai dépassé"


def appel(service, chemin, iface, methode, params):
    """Appel depuis le watcher, sans bloquer la boucle : l'icône est dans ce
    même processus et répond depuis le contexte principal."""
    res = []
    watcher.call(service, chemin, iface, methode, params, None, 0, 5000, None,
                 lambda c, r: res.append(c.call_finish(r)))
    attendre(lambda: res)
    return res[0].unpack()


adresse = os.environ["DBUS_SESSION_BUS_ADDRESS"]
watcher = Gio.DBusConnection.new_for_address_sync(
    adresse, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None, None)


def methode_watcher(_c, expediteur, _p, _i, nom, params, invocation):
    inscrits.append((expediteur, params.unpack()[0]))
    invocation.return_value(None)


watcher.register_object(sni.WATCHER_CHEMIN, Gio.DBusNodeInfo.new_for_xml(XML_WATCHER).interfaces[0],
                        methode_watcher, None, None)


def prendre_nom():
    r = watcher.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "RequestName", GLib.Variant("(su)", (sni.WATCHER, 0)), None, 0, -1, None)
    assert r.unpack()[0] == 1


def rendre_nom():
    watcher.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "ReleaseName", GLib.Variant("(s)", (sni.WATCHER,)), None, 0, -1, None)


prendre_nom()

clics = []
e = sni.Element("essai", "Essai", icone="object-locked", infobulle=("Actives", "Clic : suspendre"))
e.on_activer = lambda x, y: clics.append(("gauche", x, y))
e.on_menu = lambda x, y: clics.append(("droit", x, y))
pub = sni.publier(e)

# 1. Enregistrement auprès du watcher, sous notre nom de service (déjà pris).
attendre(lambda: len(inscrits) == 1)
assert inscrits[0][1] == pub.nom and pub.nom.startswith("org.kde.StatusNotifierItem-"), inscrits

# 2. Propriétés lues par l'hôte.
props = appel(pub.nom, sni.CHEMIN, "org.freedesktop.DBus.Properties", "GetAll",
              GLib.Variant("(s)", (sni.INTERFACE,)))[0]
assert props["Status"] == "Active" and props["IconName"] == "object-locked", props
assert props["ToolTip"][2:] == ("Actives", "Clic : suspendre") and "Menu" not in props

# 3. Clics de l'hôte -> rappels Python.
for m, xy in (("Activate", (10, 20)), ("ContextMenu", (30, 40))):
    watcher.call(pub.nom, sni.CHEMIN, sni.INTERFACE, m, GLib.Variant("(ii)", xy), None, 0, -1, None, None, None)
attendre(lambda: len(clics) == 2)
assert clics == [("gauche", 10, 20), ("droit", 30, 40)], clics

# 4. Signaux : NewStatus avec le nouveau statut, NewIcon ; images ARGB32 relues.
recus = []
watcher.signal_subscribe(None, sni.INTERFACE, None, sni.CHEMIN, None, Gio.DBusSignalFlags.NONE,
                         lambda _c, _s, _p, _i, nom, params, *_: recus.append((nom, params.unpack())))
img = sni.argb32(1, 1, bytes([1, 2, 3, 4]))
e.changer(statut="NeedsAttention", icone="", images=[img])
attendre(lambda: len(recus) >= 2)
assert ("NewStatus", ("NeedsAttention",)) in recus and ("NewIcon", ()) in recus, recus
pix = appel(pub.nom, sni.CHEMIN, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (sni.INTERFACE, "IconPixmap")))[0]
assert [(w, h, bytes(o)) for w, h, o in pix] == [(1, 1, bytes([4, 1, 2, 3]))], pix

# 5. Le watcher redémarre (plasmashell relancé) : nouvel enregistrement.
rendre_nom()
prendre_nom()
attendre(lambda: len(inscrits) == 2)
assert inscrits[1][1] == pub.nom

# 6. Retrait : le nom de service disparaît du bus.
pub.retirer()
attendre(lambda: not watcher.call_sync(
    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
    GLib.Variant("(s)", (pub.nom,)), None, 0, -1, None).unpack()[0])
print("OK")
