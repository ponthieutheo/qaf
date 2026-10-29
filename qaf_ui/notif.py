"""Notification de bureau (org.freedesktop.Notifications) depuis dom0.

Best effort : sans démon de notifications ou sans bus, le message part
seulement sur la sortie d'erreur. Gio est importé à l'appel, jamais au
chargement du module (tests sans GTK).
"""

import sys

APPLI = "QAF"


def notifier(titre, texte, icone="dialog-information"):
    print(f"{titre} : {texte}", file=sys.stderr)
    try:
        import gi
        gi.require_version("Gtk", "3.0")  # jamais GTK 4 dans ce processus (test_nuit)
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (APPLI, 0, icone, titre, texte, [], {}, 8000)),
            None, Gio.DBusCallFlags.NONE, 3000, None)
        return True
    except Exception:  # pas de bus, pas de démon : la sortie d'erreur suffit
        return False
