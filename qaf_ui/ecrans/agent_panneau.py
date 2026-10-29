"""Panneau de l'agent dans la barre de KDE Plasma : commande qaf-agent-panneau.

D'après Omarchy (basecamp/omarchy, shell/plugins/agents/Panel.qml), idée
reprise, code réécrit. Design et architecture : docs/AGENT-PANNEAU.md, écrit
pour l'ancienne barre i3 et transposé ici à la zone de notification de Plasma
(StatusNotifierItem, qaf_ui/sni.py), comme l'icône qaf-suspension.

- Icône : la tête de l'agent de QAF au style des icônes de qubes de qaf-zone
  (qaf_ui/zone.py) : tête au violet SIGNATURE de QAF (direction B, 29/10),
  trait dessous à la couleur du label de sys-agent (lu par qubesadmin),
  éclaircie jusqu'à 3:1 sur la barre comme pour tout qube. Arrêté : yeux fermés, encre atténuée (le trait reste entier) ;
  alerte (fenêtre la plus pleine à 90 % et plus) : pastille ATTENTION en haut
  à droite. Infobulle : « Prêt · Claude : 34 % sur 5 heures ».
- Clic gauche : la carte (fenêtre POPUP de dom0 sans décoration, au-dessus de
  l'icône, près du pointeur ; Échap ou clic dehors la ferment). Clic droit :
  la console de l'agent (terminal « Agent » de sys-agent). Clic du milieu :
  rafraîchir la mesure (jamais de démarrage de sys-agent pour une mesure).
- Carte (§2, §3) : héros (tête 24, « Agent », ligne en capitales qui tourne
  toutes les 2,8 s, boutons « + » et « >_ »), une section par abonnement avec
  ses jauges (5 heures, semaine) et le temps avant remise à zéro, lien
  « Journal de l'agent ». Pas de tuiles « LANCER UNE TÂCHE » (28/10 :
  il n'en veut pas).
- « + » : choix du fournisseur (Claude, Codex, Grok, comme Omarchy), puis un
  terminal DANS sys-agent avec la CLI de connexion du fournisseur
  (qaf.Connexion, D3).
- Données : qaf.Usage dans sys-agent, sans jamais le démarrer (qaf_ui/usage.py).

Ce module-ci est sans GTK (tests/test_agent_panneau.py) ; l'icône et la carte
sont dans agent_panneau_gtk.py.
"""

import os
import signal
import sys

from qaf_ui import i18n
from qaf_ui import usage as u
from qaf_ui.i18n import t

IDENT = "qaf-agent-panneau"
# Icône (qaf-zone) : label lu sur sys-agent ; sans label lisible, encre neutre (noir),
# jamais celle d'un widget de dom0. Arrêté : encre à 45 % ; alerte : pastille ATTENTION.
LABEL_DEFAUT = 0x000000
ATTENUATION_ARRETE = 0.45
PASTILLE_RAYON = 3.5
VERROU = "qaf-agent-panneau.verrou"  # sous XDG_RUNTIME_DIR : un seul panneau par session

# Mesures de la carte (§2.1, §2.2) : 412 hors tout, bordure 2, marge 16, contenu 376.
LARGEUR = 412
MARGE = 16
CONTENU = LARGEUR - 2 * 2 - 2 * MARGE
ECART_BLOCS = 16
ECART_JAUGES = 12
JAUGE = 4
BOUTON = 32
ECART_FOURNISSEURS = 10
TITRE_JAUGE = round(CONTENU * 0.30)
AVANT_DUREE = 20
TETE_HEROS = 24
ECART_POINTEUR = 8  # entre la carte et la barre

ROTATION_MS = 2800
FONDU_SORTIE_MS = 150
FONDU_ENTREE_MS = 200
SONDAGE_ETAT_S = 5
MINUTERIE_DUREES_S = 30
CONNEXION_SONDAGE_S = 5
CONNEXION_MAX_S = 15 * 60

# Fournisseurs du « + » (la liste d'Omarchy) : (clé de qaf.Connexion, nom, commande).
FOURNISSEURS = (
    ("claude", "Claude", "claude auth login"),
    ("codex", "Codex", "codex login"),
    ("grok", "Grok", "grok login"),
)
NOMS = {cle: nom for cle, nom, _ in FOURNISSEURS}

# Textes de la carte (qaf_ui/langues), lus dans la langue de dom0 à chaque usage.
__getattr__ = i18n.constantes({
    "TITRE": "carte.titre", "INTRO_VIDE": "carte.intro_vide", "INTRO_AJOUT": "carte.intro_ajout",
    "NON_INSTALLE": "carte.non_installe", "CONNECTE": "carte.connecte", "SANS_MESURE": "carte.sans_mesure",
    "ATTENTE": "carte.attente", "INCONNU": "carte.inconnu", "DEMARRER": "carte.demarrer",
    "ROUVRIR": "carte.rouvrir", "INFOBULLE_PLUS": "carte.infobulle_plus", "INFOBULLE_RETOUR": "carte.infobulle_retour",
    "INFOBULLE_CONSOLE": "carte.infobulle_console", "DEMARRAGE": "carte.demarrage", "USAGE": "carte.usage"})


# Console de l'agent (qaf.Console). Sans --no-gui : le service attend la session
# graphique. Démarre sys-agent s'il est éteint : c'est un geste de l'utilisateur.
ARGV_CONSOLE = ["qvm-run", "--quiet", "--service", u.AGENT, "qaf.Console"]


def argv_connexion(fournisseur):
    if fournisseur not in NOMS:
        raise ValueError(f"fournisseur inconnu : {fournisseur}")
    return ["qvm-run", "--quiet", "--service", u.AGENT, f"qaf.Connexion+{fournisseur}"]


ARGV_ETAT = ["qvm-check", "--quiet", "--running", u.AGENT]


def geste_du_clic(bouton):
    """Bouton de souris (1, 2, 3) -> geste : carte, rafraichir, console."""
    return {1: "carte", 2: "rafraichir", 3: "console"}.get(bouton)


def etat_fournisseur(mesure, cle):
    """« connecte », « deconnecte » ou « non_installe » ; Claude est toujours
    installé (claude est la CLI de l'agent)."""
    if cle == "claude":
        if mesure is None:
            return "deconnecte"
        return "deconnecte" if mesure.etat == "sans_jeton" else "connecte"
    return dict(mesure.fournisseurs).get(cle, "non_installe") if mesure is not None else "non_installe"


def etat_carte(allume, derniere, statut, ajout=False, connexion=None):
    """État de la carte : arrete, connexion, ajout, vide, attente ou mesure.
    derniere : dernière Mesure gardée (avec ses fenêtres) ; statut : état de la
    toute dernière réponse de qaf.Usage (None s'il n'y en a pas eu)."""
    if connexion:
        return "connexion"
    if ajout:
        return "ajout"
    if not allume:
        return "arrete"
    if statut == "sans_jeton":
        return "vide"
    if derniere is None and statut is None:
        return "attente"
    return "mesure"


def couleur_jauge(pourcent, allume):
    """Couleur du plein de la jauge (palette Plasma) : T3 si la mesure est
    gardée (agent arrêté), ATTENTION (rouge de Plasma) à 90 % et plus, sinon
    ACCENT (violet SIGNATURE)."""
    if not allume:
        return "T3"
    return "ATTENTION" if pourcent >= u.ALARME else "ACCENT"


def apparence_icone(allume, mesure, maintenant):
    """(clé d'images, infobulle) de l'icône : arrete, alerte ou pret."""
    if not allume:
        cle = "arrete"
    elif u.alarme(mesure, maintenant):
        cle = "alerte"
    else:
        cle = "pret"
    return cle, u.infobulle_icone(allume, mesure, maintenant)


def label_de(couleur):
    """Couleur du label lue par qubesadmin (« 0x9f389f », « #9f389f ») -> entier
    0xRRGGBB, ou LABEL_DEFAUT si elle est illisible."""
    if not isinstance(couleur, str):
        return LABEL_DEFAUT
    c = couleur.strip().lower().removeprefix("0x").removeprefix("#")
    if len(c) != 6 or any(x not in "0123456789abcdef" for x in c):
        return LABEL_DEFAUT
    return int(c, 16)


def attenuer(image, facteur, hauteur_trait):
    """Image ARGB (largeur, hauteur, octets A R G B) dont l'encre est atténuée
    (alpha x facteur), sauf les `hauteur_trait` dernières lignes (le trait de
    label reste entier : c'est le repère que seul dom0 pose)."""
    largeur, hauteur, octets = image
    sortie = bytearray(octets)
    for y in range(hauteur - hauteur_trait):
        for x in range(largeur):
            i = (y * largeur + x) * 4
            sortie[i] = round(sortie[i] * facteur)
    return largeur, hauteur, bytes(sortie)


def pastille(image, couleur, rayon=PASTILLE_RAYON, marge=0.5):
    """Pose une pastille pleine (disque anticrénelé) en haut à droite de
    l'image ARGB, dans `couleur` (« #RRGGBB »)."""
    largeur, hauteur, octets = image
    r, g, b = (int(couleur[k:k + 2], 16) for k in (1, 3, 5))
    cx, cy = largeur - rayon - marge, rayon + marge
    sortie = bytearray(octets)
    for y in range(hauteur):
        for x in range(largeur):
            # couverture approchée : 4 x 4 échantillons par pixel
            n = sum(1 for sy in range(4) for sx in range(4)
                    if (x + (sx + 0.5) / 4 - cx) ** 2 + (y + (sy + 0.5) / 4 - cy) ** 2 <= rayon ** 2)
            if n:
                a = n / 16
                i = (y * largeur + x) * 4
                fond_a = sortie[i] / 255
                tot = a + fond_a * (1 - a)
                mel = [round((c * a + sortie[i + 1 + k] * fond_a * (1 - a)) / tot) for k, c in enumerate((r, g, b))]
                sortie[i:i + 4] = bytes((round(tot * 255), *mel))
    return largeur, hauteur, bytes(sortie)


def placement(x, y, largeur, hauteur, zone):
    """Coin haut-gauche de la carte : au-dessus de la barre si le clic est dans
    la moitié basse de l'écran (barre en bas, défaut de Plasma), dessous
    sinon, centrée sur le pointeur, jamais hors de la zone de travail
    (zone : (x, y, largeur, hauteur), sans la barre)."""
    zx, zy, zl, zh = zone
    px = min(max(x - largeur // 2, zx + ECART_POINTEUR), zx + zl - largeur - ECART_POINTEUR)
    if y > zy + zh / 2:
        py = zy + zh - hauteur - ECART_POINTEUR
    else:
        py = zy + ECART_POINTEUR
    return max(px, zx), max(py, zy)


# --- console : la fenêtre « Agent » déjà ouverte (dom0 l'amène devant) ------------------------

def nom_du_qube(brut):
    try:
        return bytes(brut).rstrip(b"\0").decode("ascii") if brut is not None else None
    except (UnicodeDecodeError, TypeError, ValueError):
        return None


def fenetre_agent(aff):
    """xid de la fenêtre la plus récente de sys-agent titrée « Agent » (le
    nom du qube vient de _QUBES_VMNAME, posée par dom0 ; le titre, choisi par
    le qube, ne sert qu'à choisir parmi SES fenêtres), ou None."""
    try:
        clients = aff.lire_entiers(aff.racine, "_NET_CLIENT_LIST") or []
    except OSError:
        return None
    for xid in reversed(clients):
        if type(xid) is not int or not 0 < xid < 1 << 32:
            continue
        try:
            if nom_du_qube(aff.lire_octets(xid, "_QUBES_VMNAME")) != u.AGENT:
                continue
            titre = aff.lire_octets(xid, "_NET_WM_NAME") or b""
        except OSError:
            continue
        if bytes(titre).decode("utf-8", "replace").rstrip("\0").endswith("] Agent"):
            return xid
    return None


def amener_devant(aff, xid):
    """_NET_ACTIVE_WINDOW (source « pager » : KWin obéit et la sort de la réduction)."""
    masque = (1 << 20) | (1 << 19)
    aff.envoyer(aff.racine, xid, "_NET_ACTIVE_WINDOW", [2, 0, 0], masque)
    aff.vider()


# --- commandes à soi (tests sur une vraie machine, sans clic) --------------------------------

def fichier_verrou(environ=None):
    environ = os.environ if environ is None else environ
    return os.path.join(environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", VERROU)


SIGNAUX = {"--ouvrir": signal.SIGUSR1, "--fermer": signal.SIGUSR2}


def envoyer_ordre(option, environ=None, tuer=os.kill):
    """qaf-agent-panneau --ouvrir | --fermer : signal au panneau de la session
    (pid noté dans le verrou). Rend un code de sortie."""
    try:
        with open(fichier_verrou(environ), encoding="ascii") as f:
            pid = int(f.read(16).strip())
        tuer(pid, SIGNAUX[option])
    except (OSError, ValueError):
        print(t("carte.pas_lance"), file=sys.stderr)
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if args and (len(args) != 1 or args[0] not in SIGNAUX):
        print(t("carte.usage"), file=sys.stderr)
        return 2
    if args:
        return envoyer_ordre(args[0])
    from qaf_ui.ecrans import agent_panneau_gtk
    return agent_panneau_gtk.lancer()


if __name__ == "__main__":
    sys.exit(main())
