"""Icône « suspension des confirmations » dans la barre de Plasma : commande qaf-suspension.

L'équivalent de l'icône « Passwordless Sudo » d'Omarchy (28/10). Elle
montre seulement l'état écrit par qaf-suspendre (/var/lib/qaf/suspension, lu
sans droits ; l'échéance est tenue par le Doorman, qaf_gate.pause.suspendue)
et lance `sudo -n qaf-suspendre …` aux clics :

- confirmations actives : cadenas fermé du thème, discret (statut Active,
  jamais caché dans les icônes masquées) ; clic gauche ou droit : menu ;
- suspension en cours : cadenas ouvert ambre (image à nous, statut
  NeedsAttention), infobulle « Confirmations de l'agent suspendues — reste
  42 min » ou « … en permanence » ; clic gauche : rétablir tout de suite ;
  clic droit : menu.

Ce module-ci est sans GTK (tests/test_suspension_icone.py) ; la publication
(qaf_ui.sni) et le menu (Gtk.Menu) sont dans suspension_gtk.py.
"""

import math
import subprocess
import sys

from qaf_ui import i18n
from qaf_ui import suspension as s
from qaf_ui.i18n import t
from qaf_ui.nuit import jetons as j

QAF_SUSPENDRE = "/usr/bin/qaf-suspendre"
SONDAGE_S = 2
__getattr__ = i18n.constantes({"TITRE": "susp.titre"})  # titre de l'icône, dans la langue de dom0
ICONE_ACTIVE = "object-locked"  # Breeze : cadenas fermé, couleur du texte de la barre
AMBRE = j.AGENT  # violet de l'agent (28/10 : plus d'orange) ; nom gardé pour les appelants
TAILLES = (16, 22, 24, 32, 48, 64)

# (clé du libellé, action de qaf-suspendre) ; None = séparateur.
DUREES = (("susp.menu.15m", "15m"), ("susp.menu.1h", "1h"),
          ("susp.menu.1j", "1j"), ("susp.menu.permanent", "permanent"))
RETABLIR = ("susp.menu.retablir", "off")
ACTIONS = tuple(a for _, a in DUREES) + (RETABLIR[1],)


def cadenas_ouvert_svg(taille):
    """Cadenas ouvert ambre, plein, trou de serrure sombre : lisible sur une
    barre sombre comme claire, à 16 px comme à 64 px."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{taille}" height="{taille}">'
            f'<path d="M7.5 11.5V6.5a4.5 4.5 0 0 1 9 0V8" fill="none" stroke="{AMBRE}" '
            'stroke-width="2.4" stroke-linecap="round"/>'
            f'<rect x="4" y="11" width="16" height="11" rx="2.6" fill="{AMBRE}"/>'
            '<circle cx="12" cy="15.6" r="1.7" fill="#1c1b19"/>'
            '<rect x="11.15" y="15.8" width="1.7" height="3.4" rx=".85" fill="#1c1b19"/></svg>')


def css_menu():
    """Menu en style Nuit (jetons des écrans QAF) : sombre comme les menus de la
    barre de Plasma, quel que soit le thème GTK de la session."""
    return f"""
window.popup, menu, .menu {{ background-color: {j.SURFACE}; }}
menu, .menu {{ border: 1px solid {j.FILET}; padding: 6px 0; font-family: {j.POLICE}; }}
menuitem {{ color: {j.T1}; padding: 5px 12px; margin: 0 5px; min-height: 18px;
            border: none; border-radius: {j.RAYON}px; background-image: none; box-shadow: none; outline-style: none; }}
menuitem:hover {{ background-color: {j.CHOISI}; border: none; box-shadow: none; }}
menuitem:disabled, menuitem:disabled label {{ color: {j.T3}; background-color: transparent; }}
separator {{ background-color: {j.FILET}; min-height: 1px; margin: 5px 0; border: none; }}
"""


def suspendue(e):
    return e is not None


def restant(secondes):
    """« 42 min », « 1 h 05 », « 24 h » : minutes arrondies au-dessus, espaces
    insécables (l'infobulle de Plasma ne coupe pas « 15 | min »)."""
    m = max(1, math.ceil(secondes / 60))
    if m < 60:
        return t("susp.restant.min", m=m)
    h, m = divmod(m, 60)
    return t("susp.restant.h", h=h) if m == 0 else t("susp.restant.h_min", h=h, m=f"{m:02d}")


def duree(e):
    """« reste 42 min » ou « en permanence »."""
    return t("susp.permanence") if e == "permanent" else t("susp.reste", duree=restant(e))


def phrase(e):
    """État en une phrase (en-tête du menu)."""
    if e is None:
        return t("susp.actives")
    return t("susp.suspendues_duree", duree=duree(e))


def infobulle(e):
    """(titre, texte) de l'infobulle : l'état, puis la durée et le geste."""
    if e is None:
        return (t("susp.actives"), t("susp.bulle.suspendre"))
    d = duree(e)
    return (t("susp.suspendues"), t("susp.bulle.retablir", duree=f"{d[0].upper()}{d[1:]}"))


def apparence(e, alerte=()):
    """Champs de qaf_ui.sni.Element.changer() pour l'échéance e (voir
    qaf_ui.suspension.echeance). alerte : images ARGB32 du cadenas ouvert."""
    if e is None:
        return dict(statut="Active", icone=ICONE_ACTIVE, images=[], attention_icone="",
                    attention_images=[], infobulle=infobulle(e))
    return dict(statut="NeedsAttention", icone="", images=list(alerte), attention_icone="",
                attention_images=list(alerte), infobulle=infobulle(e))


def clic_gauche(e):
    """« off » quand une suspension court (couper tout de suite), sinon « menu »."""
    return "off" if suspendue(e) else "menu"


def menu(e):
    """Lignes du menu : (libellé, action ou None, actif). En-tête = l'état,
    jamais cliquable ; (None, None, False) = séparateur."""
    lignes = [(phrase(e), None, False), (None, None, False)]
    lignes += [(t(libelle), action, True) for libelle, action in DUREES]
    lignes += [(None, None, False), (t(RETABLIR[0]), RETABLIR[1], suspendue(e))]
    return lignes


def commande(action):
    if action not in ACTIONS:
        raise ValueError(f"action inconnue : {action}")
    return ["sudo", "-n", QAF_SUSPENDRE, action]


def lancer(action, executer=subprocess.run):
    """sudo -n qaf-suspendre ACTION. Bloquant : hors du fil GTK. Renvoie
    (ok, message pour l'utilisateur si ça a échoué)."""
    try:
        r = executer(commande(action), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as ex:
        return False, t("susp.lancement", erreur=ex)
    if r.returncode != 0:
        detail = (r.stderr or "").strip()[:160]
        return False, t("susp.refuse") + (t("donnees.detail", detail=detail) if detail else ".")
    return True, ""


def lire(chemin=None):
    """Échéance actuelle (None, « permanent » ou secondes) ; illisible = None,
    comme le Doorman (échec fermé : confirmations)."""
    try:
        return s.echeance() if chemin is None else s.echeance(chemin)
    except Exception:
        return None


def main():
    from qaf_ui.ecrans import suspension_gtk
    return suspension_gtk.lancer_indicateur(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
