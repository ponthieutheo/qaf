"""Zone de notification de Plasma : logique pure des icônes (sans X ni D-Bus).

Sous Plasma, les icônes XEmbed (widgets qui-* de dom0, icônes des qubes
relayées par le démon d'affichage) passent par un mandataire qui les republie
en StatusNotifierItem. Celui de KDE (xembedsniproxy) copie l'image telle
quelle : l'agent GUI de chaque qube peint le fond de ses icônes en blanc (codé
en dur, qubes-gui-agent-linux vmside.c, XCreateSimpleWindow(…, WhitePixel)),
le démon d'affichage de dom0 les teinte à la couleur du label, sans canal
alpha : chaque icône de qube arrive en carré blanc sur la barre sombre
(issue Qubes #2846).

qaf-zone (zone_x.py) remplace xembedsniproxy pour la session et republie
chaque icône avec une image propre, calculée ici :
- icône d'un qube (fenêtre portant _QUBES_VMNAME, posé par le démon
  d'affichage de dom0, jamais par le qube) : le fond blanc disparaît, l'encre
  (ce qui n'est pas blanc) est redessinée dans la couleur du label lue sur la
  fenêtre de dom0 (_QUBES_LABEL_COLOR), éclaircie jusqu'à 3:1 sur la barre,
  et un trait de label est posé dessous. Un qube ne choisit ni la couleur de
  son encre ni l'absence du trait : son icône ne peut pas imiter celle d'un
  autre qube ni un widget de dom0 (jamais de trait) ;
- icône de dom0 : inchangée (seule la prémultiplication de X est défaite).

Format de sortie : celui d'IconPixmap (StatusNotifierItem) : ARGB 32 bits,
octets dans l'ordre réseau (A, R, G, B), non prémultiplié.
Format d'entrée : celui de XGetImage en ZPixmap sur un serveur petit-boutiste
(B, G, R, X ou A), pas = octets par ligne.
"""

import math
import os

from .nuit import contraste
from .nuit import jetons as j

# Fond de la barre de Plasma (Brise sombre, mesuré sur Qubes 4.3.2 le 28/10) :
# l'encre d'un label sombre est éclaircie jusqu'à 3:1 sur ce fond.
FOND_BARRE = "#24292C"
# Labels sans teinte : jetons figés (V2 §3.5, règle 3), jamais le gris des
# widgets de dom0.
ENCRES_NEUTRES = {0x000000: "ENCRE_NOIR", 0x555753: "ENCRE_GRIS"}
TAILLE = 22          # côté de l'image publiée pour une icône de qube
TRAIT = (12, 2)      # trait de label : largeur x hauteur, collé au bas
ECART_TRAIT = 1      # px libres entre l'encre et le trait
SEUIL_ENCRE = 10     # encre (0-255) en dessous de laquelle un pixel compte comme fond
# Taille acceptée d'une icône ancrée ; hors de ces bornes elle est mise à TAILLE.
COTE_MIN, COTE_MAX = 8, 128


def couleur_encre(label, fond=FOND_BARRE):
    """(r, g, b) de l'encre d'une icône de qube : la couleur du label (entier
    0xRRGGBB), mélangée au blanc par pas de 5 % jusqu'à 3:1 sur fond.
    Label noir ou gris : ENCRE_NOIR ou ENCRE_GRIS, même règle des 3:1."""
    neutre = ENCRES_NEUTRES.get(label & 0xFFFFFF)
    base = contraste.rgb(getattr(j, neutre)) if neutre else ((label >> 16) & 255, (label >> 8) & 255, label & 255)
    for pas in range(21):
        t = pas / 20
        c = tuple(round(v + (255 - v) * t) for v in base)
        if contraste.ratio(c, fond) >= contraste.GRAPHIQUE_MIN:
            return c
    return (255, 255, 255)


def _valide(largeur, hauteur, pas, octets):
    return 0 < largeur <= COTE_MAX and 0 < hauteur <= COTE_MAX and pas >= largeur * 4 \
        and len(octets) >= pas * hauteur


def encre(largeur, hauteur, pas, octets):
    """Carte d'encre (liste de lignes, 0.0 à 1.0) d'une icône de qube : 255 -
    min(r, g, b) (blanc = rien), ramenée à 1 au plus foncé de l'icône (un label
    gris ou noir garde tout son trait). None si l'image est invalide."""
    if not _valide(largeur, hauteur, pas, octets):
        return None
    carte = [[255 - min(octets[y * pas + x * 4:y * pas + x * 4 + 3]) for x in range(largeur)]
             for y in range(hauteur)]
    haut = max(max(ligne) for ligne in carte)
    if haut < SEUIL_ENCRE:
        return [[0.0] * largeur for _ in range(hauteur)]
    return [[(v / haut if v >= SEUIL_ENCRE else 0.0) for v in ligne] for ligne in carte]


def boite(carte):
    """(x0, y0, x1, y1) exclusif de l'encre, None si l'icône est vide."""
    ys = [y for y, ligne in enumerate(carte) if any(ligne)]
    if not ys:
        return None
    xs = [x for x in range(len(carte[0])) if any(ligne[x] for ligne in carte)]
    return xs[0], ys[0], xs[-1] + 1, ys[-1] + 1


def reduire(carte, b, largeur, hauteur):
    """Partie b de la carte réduite à largeur x hauteur par moyenne des surfaces
    (pas de crénelage). Jamais agrandie : l'appelant choisit une taille <= b."""
    x0, y0, x1, y1 = b
    fx, fy = (x1 - x0) / largeur, (y1 - y0) / hauteur
    sortie = []
    for cy in range(hauteur):
        a0, a1 = y0 + cy * fy, y0 + (cy + 1) * fy
        ligne = []
        for cx in range(largeur):
            b0, b1 = x0 + cx * fx, x0 + (cx + 1) * fx
            somme = aire = 0.0
            for y in range(int(a0), min(y1, math.ceil(a1))):
                hy = min(y + 1, a1) - max(y, a0)
                for x in range(int(b0), min(x1, math.ceil(b1))):
                    a = hy * (min(x + 1, b1) - max(x, b0))
                    somme += carte[y][x] * a
                    aire += a
            ligne.append(somme / aire if aire else 0.0)
        sortie.append(ligne)
    return sortie


def icone_qube(largeur, hauteur, pas, octets, label, fond=FOND_BARRE, taille=TAILLE, dessin=None):
    """(taille, taille, octets ARGB) d'une icône de qube : encre dans la
    couleur du label sur fond transparent, centrée au-dessus du trait de label
    (toujours présent, même si l'icône est vide). None si l'image est invalide.
    dessin : (r, g, b) du dessin seul, pour les icônes de dom0 qui ont leur
    propre couleur (tête de l'agent, violet SIGNATURE) ; le trait garde
    toujours la couleur du label."""
    carte = encre(largeur, hauteur, pas, octets)
    if carte is None:
        return None
    trait_rgb = couleur_encre(label, fond)
    dessin_rgb = dessin or trait_rgb
    sortie = bytearray(taille * taille * 4)

    def poser(x, y, a, rgb=dessin_rgb):
        if a <= 0:
            return
        i = (y * taille + x) * 4
        sortie[i:i + 4] = bytes((min(255, round(a * 255)), *rgb))

    tl, th = TRAIT
    zone_h = taille - th - ECART_TRAIT  # hauteur disponible pour l'encre
    b = boite(carte)
    if b is not None:
        bw, bh = b[2] - b[0], b[3] - b[1]
        echelle = min(1.0, taille / bw, zone_h / bh)
        w, h = max(1, round(bw * echelle)), max(1, round(bh * echelle))
        petite = reduire(carte, b, w, h) if (w, h) != (bw, bh) else \
            [ligne[b[0]:b[2]] for ligne in carte[b[1]:b[3]]]
        ox, oy = (taille - w) // 2, (zone_h - h) // 2
        for y in range(h):
            for x in range(w):
                poser(ox + x, oy + y, petite[y][x])
    tx = (taille - tl) // 2
    for y in range(taille - th, taille):
        for x in range(tx, tx + tl):
            # Bouts arrondis : les coins du trait à moitié couverts.
            bout = x in (tx, tx + tl - 1)
            poser(x, y, 0.55 if bout else 1.0, trait_rgb)
    return taille, taille, bytes(sortie)


def icone_dom0(largeur, hauteur, pas, octets, profondeur):
    """(largeur, hauteur, octets ARGB) d'une icône de dom0, inchangée : en 32
    bits, la prémultiplication de X est défaite ; en 24 bits, opaque."""
    if not _valide(largeur, hauteur, pas, octets):
        return None
    sortie = bytearray(largeur * hauteur * 4)
    for y in range(hauteur):
        for x in range(largeur):
            i = y * pas + x * 4
            bb, gg, rr = octets[i], octets[i + 1], octets[i + 2]
            a = octets[i + 3] if profondeur == 32 else 255
            if 0 < a < 255:
                rr, gg, bb = (min(255, round(c * 255 / a)) for c in (rr, gg, bb))
            elif a == 0:
                rr = gg = bb = 0
            o = (y * largeur + x) * 4
            sortie[o:o + 4] = bytes((a, rr, gg, bb))
    return largeur, hauteur, bytes(sortie)


def taille_ancrage(largeur, hauteur):
    """Taille à donner à une icône ancrée : la sienne si elle est raisonnable
    (le démon d'affichage garde celle du qube, 16 ou 22), sinon TAILLE."""
    if COTE_MIN <= largeur <= COTE_MAX and COTE_MIN <= hauteur <= COTE_MAX:
        return largeur, hauteur
    return TAILLE, TAILLE


def taille_lue(largeur, hauteur):
    """Taille de la lecture d'image : bornée à COTE_MAX de côté, au moins 1."""
    return max(1, min(COTE_MAX, int(largeur))), max(1, min(COTE_MAX, int(hauteur)))


def bouton_defilement(delta, orientation):
    """(bouton X, nombre de crans) d'un Scroll(delta, orientation) de SNI :
    4/5 vertical, 6/7 horizontal, un cran par 120 (au moins un, au plus 10)."""
    horizontal = str(orientation).lower().startswith("h")
    if delta == 0:
        return None, 0
    bouton = (7 if delta > 0 else 6) if horizontal else (4 if delta > 0 else 5)
    return bouton, max(1, min(10, abs(int(delta)) // 120))


BOUTONS_TENUS = 0x1F00  # Button1Mask à Button5Mask (X.h)
ATTENTE_PAS_MS, ATTENTE_MAX_MS = 25, 1500


def attendre_boutons(masque, deja_ms):
    """Délai (ms) avant de rejouer un clic : 0 si aucun bouton n'est tenu (ou
    si l'on a assez attendu, le clic part alors quand même), sinon un pas."""
    if masque & BOUTONS_TENUS and deja_ms < ATTENTE_MAX_MS:
        return ATTENTE_PAS_MS
    return 0


def textes(nom_fenetre, classe, vm):
    """(Id, Title) publiés pour une icône. Id stable (classe X, préfixée du qube
    pour une icône de qube) ; Title = nom de la fenêtre (le démon d'affichage y
    met déjà « [qube] »), préfixé du qube s'il manque. Texte nettoyé et borné."""
    def propre(t, n=200):
        t = "".join(c if c.isprintable() else " " for c in (t or ""))
        return " ".join(t.split())[:n]
    nom, cl, vm = propre(nom_fenetre), propre(classe, 80), propre(vm, 40)
    if vm:
        if not nom.startswith(f"[{vm}]"):
            nom = f"[{vm}] {nom}".strip()
        ident = cl if cl.startswith(f"{vm}:") else f"{vm}:{cl or 'icone'}"
    else:
        ident = cl or nom or "icone"
    return ident, nom or ident


# --- icône prise par le gestionnaire de fenêtres -------------------------------
# Vu le 28/10 au redémarrage de qaf-zone : les icônes des qubes rendues à la
# racine sont réancrées par qubes-guid, mais KWin traite en retard la demande
# d'affichage faite pendant qu'elles étaient à la racine et les met dans un
# cadre : trois petites fenêtres sur le bureau, plus d'icône dans la barre.
# Réancrer l'icône (la sortir du cadre) suffit : KWin la relâche.
REPRISES_MAX = 3   # par fenêtre, pour toute la vie de qaf-zone (jamais de ping-pong)
REPRISE_MS = 300


def reprendre(parent, racine, conteneur, reprises):
    """Vrai si une icône ancrée, reparentée sous `parent`, doit être réancrée :
    seulement si elle est passée sous une fenêtre étrangère (un cadre du
    gestionnaire de fenêtres), jamais rendue à la racine (le programme la
    retire lui-même) ni restée chez nous, et au plus REPRISES_MAX fois."""
    return parent not in (racine, conteneur) and reprises < REPRISES_MAX


# --- repli : xembedsniproxy d'origine après des échecs répétés ---------------
ECHECS_MAX = 3
XEMBEDSNIPROXY = "/usr/bin/xembedsniproxy"


def fichier_echecs():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "qaf-zone-echecs")


def lire_echecs(chemin=None):
    try:
        with open(chemin or fichier_echecs(), encoding="ascii") as f:
            return max(0, int(f.read(16).strip() or 0))
    except (OSError, ValueError):
        return 0


def noter_echec(chemin=None):
    n = lire_echecs(chemin) + 1
    try:
        with open(chemin or fichier_echecs(), "w", encoding="ascii") as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def oublier_echecs(chemin=None):
    try:
        os.unlink(chemin or fichier_echecs())
    except OSError:
        pass
