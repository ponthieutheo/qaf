"""Couleurs du thème Plasma de la session, pour que les fenêtres QAF ouvertes
depuis la barre (carte de l'agent…) aient l'air des fenêtres de Plasma
(28/10 : « les mêmes couleurs, que ça aille ensemble »).

Lues dans le fichier `colors` du style Plasma choisi (~/.config/plasmarc,
[Theme] name), comme Plasma lui-même ; le style « default » n'en a pas et
suit Breeze sombre (la barre de Qubes sous Plasma) : on lit alors celui de
breeze-dark. Fichier absent ou illisible : valeurs de Breeze sombre écrites
ici. Seulement lu, jamais écrit ; aucun texte venu d'un qube.

Direction B (29/10) : l'ACCENT n'est plus lu dans Plasma, c'est le
violet SIGNATURE de QAF, fixe (jauges, contour de la carte de l'agent) ; les
neutres restent ceux du style Plasma.
"""

import configparser
import os
from dataclasses import dataclass

from .nuit import jetons

THEMES = "/usr/share/plasma/desktoptheme"
REPLI = "breeze-dark"


@dataclass(frozen=True)
class Palette:
    SURFACE: str = "#202326"     # fond des fenêtres (Window)
    VUE: str = "#141618"         # fond des listes (View)
    T1: str = "#FCFCFC"          # texte
    T3: str = "#A1A9B1"          # texte inactif
    ACCENT: str = jetons.SIGNATURE  # violet de QAF, fixe (jamais lu dans Plasma)
    ATTENTION: str = "#DA4453"   # négatif de Plasma (alerte ; plus d'orange, 28/10)
    POLICE: str = '"Noto Sans", "Noto Sans CJK SC", sans-serif'

    def _mix(self, a, poids):
        """Couleur a mélangée au fond SURFACE (poids de a entre 0 et 1)."""
        ca, cf = _rgb(a), _rgb(self.SURFACE)
        return "#" + "".join(f"{round(x * poids + y * (1 - poids)):02X}" for x, y in zip(ca, cf))

    @property
    def T2(self):
        return self._mix(self.T1, 0.82)

    @property
    def CHAMP(self):
        return self._mix(self.T1, 0.07)

    @property
    def CHOISI(self):
        return self._mix(self.T1, 0.14)

    @property
    def FILET(self):
        return self._mix(self.T1, 0.12)

    @property
    def FILET_FORT(self):
        return self._mix(self.ACCENT, 0.7)


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _hex(valeur):
    """« 32,35,38 » -> « #202326 » ; autre chose : None."""
    try:
        r, g, b = (int(x) for x in valeur.split(",")[:3])
    except (ValueError, AttributeError):
        return None
    if not all(0 <= x <= 255 for x in (r, g, b)):
        return None
    return f"#{r:02X}{g:02X}{b:02X}"


def _lire(chemin):
    c = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(chemin, encoding="utf-8") as f:
            c.read_string(f.read(65536))
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    return c


def theme_choisi(config=os.path.expanduser("~/.config/plasmarc")):
    c = _lire(config)
    nom = c.get("Theme", "name", fallback="default") if c else "default"
    return nom if nom and "/" not in nom and not nom.startswith(".") else "default"


def palette(config=os.path.expanduser("~/.config/plasmarc"), themes=THEMES):
    nom = theme_choisi(config)
    c = _lire(os.path.join(themes, nom, "colors")) or _lire(os.path.join(themes, REPLI, "colors"))
    if c is None:
        return Palette()
    d = Palette()

    def lu(groupe, cle, defaut):
        return _hex(c.get(groupe, cle, fallback="")) or defaut
    return Palette(SURFACE=lu("Colors:Window", "BackgroundNormal", d.SURFACE),
                   VUE=lu("Colors:View", "BackgroundNormal", d.VUE),
                   T1=lu("Colors:Window", "ForegroundNormal", d.T1),
                   T3=lu("Colors:Window", "ForegroundInactive", d.T3),
                   ATTENTION=lu("Colors:Window", "ForegroundNegative", d.ATTENTION))
