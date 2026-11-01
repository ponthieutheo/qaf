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
  à droite (fenêtres de Claude, donc seulement si Claude est l'actif). Infobulle :
  « Prêt · Claude : 34 % sur 5 heures », ou le fournisseur actif.
- Clic gauche : la carte (fenêtre POPUP de dom0 sans décoration, au-dessus de
  l'icône, près du pointeur ; Échap ou clic dehors la ferment). Clic droit :
  la console de l'agent (terminal « Agent » de sys-agent). Clic du milieu :
  rafraîchir la mesure (jamais de démarrage de sys-agent pour une mesure).
- Carte (§2, §3) : héros (tête 24, « Agent », ligne en capitales qui tourne
  toutes les 2,8 s, boutons « + » et « >_ »), le fournisseur actif coché (jauges
  pour Claude, 5 heures et semaine, avec le temps avant remise à zéro), puis les
  autres fournisseurs utilisables avec « Utiliser » toujours visible.
  Pas de tuiles « LANCER UNE TÂCHE » (28/10 : il n'en veut pas).
- « + » : les 35 fournisseurs d'Omarchy, liste à hauteur bornée, par section. Une
  ligne activable bascule (qaf.Fournisseur) ; une autre ouvre le terminal de
  l'assistant DANS sys-agent (qaf.Connexion, D3). Clés et adresses ne se saisissent
  que là, jamais dans dom0.
- « Autoriser un qube voisin » : écran de dom0, seul endroit où s'ouvre le passage vers
  l'IA locale d'un autre qube (qaf_ui/voisin.py). Le qube vient de qubesd, le port du
  clavier interne (P2), rien de qaf.Usage. La carte n'y est jamais reconstruite par le
  tic de la mesure, sinon le champ s'effacerait sous la frappe.
- Rien de ce qui s'affiche ne vient du qube : énumérations et ids de la table de
  dom0 seulement ; modèle et adresse ne sont jamais montrés ici.
- Données : qaf.Usage dans sys-agent, sans jamais le démarrer (qaf_ui/usage.py).

Ce module-ci est sans GTK (tests/test_agent_panneau.py) ; l'icône et la carte
sont dans agent_panneau_gtk.py.
"""

import os
import signal
import sys

from qaf_ui import i18n
from qaf_ui import usage as u
from qaf_ui import voisin as v
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
COLONNE = 36  # tête 24 + 12 : coche sous la tête, noms sous le mot « Agent »
RETRAIT_COCHE = 4
LIGNE = 32
ICONE_LIGNE = 16
ECART_CORPS = 8
SECTION = 20
LISTE_MAX = 324  # hauteur bornée du « + » : la dernière ligne visible est coupée, le défilement se voit
AUTRES_MAX = 160  # carte normale : 5 lignes, puis défilement
TITRE_JAUGE = 88
DUREE = 56
DUREE_ZH = 72  # « 2 小时 14 分 » déborde à 56 et les deux jauges n'auraient plus la même longueur
AVANT_DUREE = 20
TETE_HEROS = 24
ECART_POINTEUR = 8  # entre la carte et la barre
CARRE = 12  # carré de label d'un qube
VOISINS_MAX = 160  # écran du qube voisin : 5 lignes, puis défilement
PORT_EXEMPLE = "11434"  # indication seulement : le port est toujours tapé (P2)
# Seules touches du champ de port, au nom Gdk : chiffres et déplacement, rien pour coller.
TOUCHES_PORT = frozenset({*"0123456789", *(f"KP_{i}" for i in range(10)), "BackSpace", "Delete", "KP_Delete",
                          "Left", "Right", "Home", "End", "KP_Left", "KP_Right", "Tab", "ISO_Left_Tab"})

ROTATION_MS = 2800
FONDU_SORTIE_MS = 150
FONDU_ENTREE_MS = 200
SONDAGE_ETAT_S = 5
MINUTERIE_DUREES_S = 30
CONNEXION_SONDAGE_S = 5
CONNEXION_MAX_S = 15 * 60
# Une mesure partie avant que qaf.Connexion ait effacé le dernier résultat dirait encore
# l'ancien : elle ne compte pas pendant ces quelques secondes.
CONNEXION_MARGE_S = 5

# Sections du « + », l'ordre de la table (Omarchy) restant celui de la lecture. « local » est
# aujourd'hui une adresse réseau : sa section est le point d'extension pour tout autre lieu.
SECTIONS = (("carte.abonnements", u.IDS[:15]), ("carte.autres_cli", u.IDS[15:27]), ("carte.cles_api", u.IDS[27:34]),
            ("carte.sec_locale", u.IDS[34:]))
AUTRES_CLI = u.IDS[15:27]
CLES_API = u.IDS[27:34]

# Textes de la carte (qaf_ui/langues), lus dans la langue de dom0 à chaque usage.
__getattr__ = i18n.constantes({
    "TITRE": "carte.titre", "INTRO_VIDE": "carte.intro_vide",
    "NON_INSTALLE": "carte.non_installe", "CONNECTE": "carte.connecte",
    "ATTENTE": "carte.attente", "INCONNU": "carte.inconnu", "DEMARRER": "carte.demarrer",
    "ROUVRIR": "carte.rouvrir", "INFOBULLE_PLUS": "carte.infobulle_plus", "INFOBULLE_RETOUR": "carte.infobulle_retour",
    "INFOBULLE_CONSOLE": "carte.infobulle_console", "DEMARRAGE": "carte.demarrage", "USAGE": "carte.usage"})


# Console de l'agent (qaf.Console). Sans --no-gui : le service attend la session
# graphique. Démarre sys-agent s'il est éteint : c'est un geste de l'utilisateur.
ARGV_CONSOLE = ["qvm-run", "--quiet", "--service", u.AGENT, "qaf.Console"]


def argv_connexion(fournisseur):
    if fournisseur not in u.IDS:
        raise ValueError(f"fournisseur inconnu : {fournisseur}")
    return ["qvm-run", "--quiet", "--service", u.AGENT, f"qaf.Connexion+{fournisseur}"]


ARGV_ETAT = ["qvm-check", "--quiet", "--running", u.AGENT]


class EcranVoisin:
    """État de l'écran « qube voisin », tenu hors des widgets. clavier : confirmation.Clavier
    lu à l'ouverture ; qubes : None pendant la lecture, sinon liste, vide si rien ne convient,
    False si illisible."""

    def __init__(self, clavier, regle):
        self.clavier, self.regle = clavier, regle
        self.qubes, self.choix, self.port = None, None, ""
        self.message, self.en_cours = "", False

    def autorisable(self):
        """Un qube choisi dans la liste de dom0 et un port tapé. Jamais si l'identité du clavier
        interne manque : le champ est alors fermé."""
        return (self.regle is None and not self.en_cours and self.clavier.ok and v.port_valide(self.port) is not None
                and bool(self.qubes) and self.choix in {q.nom for q in self.qubes})

    def qube(self, nom):
        return next((q for q in self.qubes or () if q.nom == nom), None)

    def vue(self):
        """Ce qui reconstruit l'écran. Ni le port ni la mesure : le champ survit à la frappe."""
        return self.qubes, self.choix, self.message, self.en_cours, self.regle


def touche_du_port(nom):
    return nom in TOUCHES_PORT


def notes_voisin(mesure, regle, marche):
    """Sous l'IA locale active quand un voisin est autorisé : sa santé n'est jamais sondée, le
    tunnel réveillerait le qube (la mesure dit `inconnue`). dom0 dit l'état réel du qube."""
    if regle is None or mesure is None or mesure.sante != "inconnue":
        return ()
    notes = [t("carte.note_sante_voisin")]
    if marche is not None:
        notes.append(t("carte.voisin_etat", qube=regle.qube, etat=t("carte.en_marche" if marche else "carte.eteint")))
    return tuple(notes)


def geste_du_clic(bouton):
    """Bouton de souris (1, 2, 3) -> geste : carte, rafraichir, console."""
    return {1: "carte", 2: "rafraichir", 3: "console"}.get(bouton)


def etat_fournisseur(mesure, cle):
    """« connecte », « deconnecte », « non_installe » ou « connexion_seule » ; Claude est
    toujours installé (claude est la CLI de l'agent)."""
    if cle == "claude":
        if mesure is None:
            return "deconnecte"
        return "deconnecte" if mesure.etat == "sans_jeton" else "connecte"
    if mesure is None:
        return "non_installe"
    # Une clé ou une adresse ne s'installe pas : sans nouvelle de sys-agent (v2), reste à configurer.
    sans_cli = mesure.v >= 2 and (cle in CLES_API or cle == "local")
    return dict(mesure.fournisseurs).get(cle, "deconnecte" if sans_cli else "non_installe")


def connectable(mesure, cle):
    """Le terminal de connexion peut-il s'ouvrir pour ce fournisseur ? En v1, sys-agent ne
    connaît que Claude, Codex et Grok, sans installation à la demande."""
    if cle == "claude":
        return True
    if mesure is None:
        return False
    return mesure.v >= 2 or (cle in u.FOURNISSEURS_V1 and etat_fournisseur(mesure, cle) != "non_installe")


def activable(mesure, cle):
    """Peut-il devenir l'agent : connecté, et son serveur MCP qaf configuré (agents). grokbot
    jamais, quoi que dise le qube."""
    return (mesure is not None and cle not in u.JAMAIS_AGENT and cle in mesure.agents
            and etat_fournisseur(mesure, cle) == "connecte")


def statut_ligne(mesure, cle):
    """Statut d'une ligne : actif, connecte, a_configurer, non_installe ou connexion_seule.
    Connecté mais sans serveur MCP qaf = connexion seule, jamais un agent."""
    e = etat_fournisseur(mesure, cle)
    if u.actif_de(mesure) == cle and (cle != "claude" or e == "connecte"):
        return "actif"
    if e == "connecte":
        return "connecte" if activable(mesure, cle) else "connexion_seule"
    return {"deconnecte": "a_configurer"}.get(e, e)


def geste_ligne(mesure, cle, allume):
    """Effet d'un clic : basculer, terminal, ou None (désactivée). sys-agent arrêté : rien, le
    terminal ne le démarrerait pas sans un geste exprès."""
    if not allume:
        return None
    statut = statut_ligne(mesure, cle)
    if statut == "actif":
        return None
    if statut == "connecte":
        return "basculer"
    return "terminal" if connectable(mesure, cle) else None


def icone_terminal(mesure, cle, allume):
    """L'icône du terminal n'apparaît que là où la ligne fait autre chose que l'ouvrir."""
    return allume and connectable(mesure, cle) and statut_ligne(mesure, cle) in ("actif", "connecte")


def utilisables(mesure):
    """Carte normale : les fournisseurs qui peuvent devenir l'agent, sauf l'actif."""
    return [cle for cle in u.IDS if cle != u.actif_de(mesure) and activable(mesure, cle)]


def largeur_duree():
    return DUREE_ZH if i18n.langue() == "zh" else DUREE


def meta_fournisseur(mesure, cle, regle=None):
    """Ce que l'on sait dire d'un fournisseur sans rien lire du qube. regle : l'autorisation
    d'un qube voisin, lue par dom0 dans son propre fichier."""
    if cle == "claude":
        return u.OFFRES.get(mesure.offre, "") if mesure is not None else ""
    if cle == "local":
        return t("carte.reseau" if regle is None else "carte.qube_voisin")
    return t("carte.cle_api" if cle in CLES_API else "carte.cli" if cle in AUTRES_CLI else "carte.abonnement")


def note_actif(cle):
    """Rappel permanent sous un fournisseur actif autre que Claude."""
    if cle == "local":
        return "carte.note_local"
    return "carte.note_or" if cle in CLES_API else "carte.note_cli" if cle in AUTRES_CLI else "carte.note_abonnement"


def notes_bascule(cle, console_ouverte):
    """Clés des notes après une bascule. Une console ouverte garde son processus, sur l'ancien
    fournisseur : elle n'est jamais fermée d'autorité, seulement signalée."""
    notes = ["carte.note_bascule"]
    if console_ouverte:
        notes.append("carte.note_console")
    if cle == "agy":
        notes.append("carte.note_google")
    return tuple(notes)


def etapes_connexion(cle):
    """Trois lignes constantes, sans coches vivantes : la mesure arrive au mieux toutes les 15 s
    et aucun écran ne doit mentir pendant ce temps."""
    if cle == "local":
        milieu = "carte.et_lieu"
    else:
        milieu = "carte.et_cle" if cle in CLES_API else "carte.et_compte"
    return ("carte.et_ouvert", milieu, "carte.et_test")


def ecran_connexion(mesure):
    """Résultat du dernier assistant : ok, echec, ou attente."""
    r = mesure.connexion if mesure is not None else ""
    return r or "attente"


def connexion_finie_v1(mesure, cle, etat):
    """sys-agent en v1 n'a pas de résultat d'assistant : l'écran se ferme quand la connexion se
    voit. En v2, seul un résultat le fait passer, un fournisseur déjà connecté ne le ferme pas."""
    return (mesure.v == 1 and etat_fournisseur(mesure, cle) == "connecte"
            and (cle != "claude" or etat == "ok"))


def connexion_retiree(mesure, cle, avant):
    """Le fournisseur vient d'être retiré dans le terminal : aucun test n'a tourné, donc aucun
    résultat n'arrivera. avant : les ids activables à l'ouverture du terminal."""
    return (cle in avant and cle != "claude" and mesure.v >= 2 and not mesure.connexion
            and cle not in mesure.agents)


def titre_pret(cle):
    return t("carte.pret_f" if cle == "local" else "carte.pret", nom=u.nom_fournisseur(cle))


def phrase_pret(cle):
    return t("carte.pret_local" if cle == "local" else "carte.pret_tiers")


def etat_carte(allume, derniere, statut, ajout=False, connexion=None, voisin=False):
    """État de la carte : voisin, arrete, connexion, ajout, vide, attente ou mesure.
    derniere : dernière Mesure gardée (avec ses fenêtres) ; statut : état de la
    toute dernière réponse de qaf.Usage (None s'il n'y en a pas eu). L'écran du voisin passe
    devant tout, et marche sys-agent éteint : retirer une autorisation ne dépend pas de lui."""
    if voisin:
        return "voisin"
    if connexion:
        return "connexion"
    if ajout:
        return "ajout"
    if not allume:
        return "arrete"
    if statut == "sans_jeton" and u.actif_de(derniere) in ("claude", None):
        return "vide"  # un autre actif : sans compte Claude, la carte reste celle de cet actif
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
