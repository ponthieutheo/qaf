"""Gestes de l'utilisateur depuis les écrans de l'agent (dom0, sans GTK) : pause du
Doorman par qaf-pause et ouverture de la palette « Demander à l'agent », à
argv fixe, jamais de shell. Rien ne passe par le Doorman : ce sont les actions
de l'utilisateur. Les actions de l'agent, elles, restent dans le Doorman (qaf_gate) et
sa confirmation.
"""

import subprocess
from dataclasses import dataclass

from .i18n import t


@dataclass(frozen=True)
class Resultat:
    ok: bool
    message: str  # une phrase pour l'utilisateur, à poser avec set_text


QAF_PAUSE = "/usr/bin/qaf-pause"


def commande_pause(mettre, raison=""):
    """argv fixe de la pause du Doorman (sudo -n : jamais de mot de passe
    demandé dans un écran). La raison est un texte à nous, jamais une donnée
    venue d'un qube."""
    if mettre:
        return ["sudo", "-n", QAF_PAUSE, "on"] + ([raison] if raison else [])
    return ["sudo", "-n", QAF_PAUSE, "off"]


def pause_portier(mettre, raison="", executer=subprocess.run):
    """Pose ou lève la pause du Doorman par qaf-pause, depuis le panneau de
    l'agent. Bloquant : à lancer hors du fil GTK. Lever la pause
    ne s'appelle qu'après une confirmation dans l'écran."""
    secours = "sudo qaf-pause" if mettre else "sudo qaf-pause off"
    try:
        r = executer(commande_pause(mettre, raison), stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as ex:
        return Resultat(False, t("donnees.pause.lancement", erreur=ex, secours=secours))
    if r.returncode != 0:
        detail = (getattr(r, "stderr", "") or "").strip()[:160]
        return Resultat(False, t("donnees.pause.refusee") + (t("donnees.detail", detail=detail) if detail else "")
                        + t("donnees.pause.secours", secours=secours))
    return Resultat(True, t("donnees.pause.posee") if mettre else t("donnees.pause.levee"))


PALETTE = ["qaf-palette"]  # palette « Demander à l'agent », argv fixe


def ouvrir_palette(lancer=subprocess.Popen):
    """Ouvre la palette (qaf-palette) depuis le panneau de l'agent, argv fixe."""
    try:
        lancer(list(PALETTE), stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return Resultat(False, t("donnees.palette.introuvable", commande=PALETTE[0], erreur=e))
    return Resultat(True, t("donnees.palette.ouverte", commande=PALETTE[0]))
