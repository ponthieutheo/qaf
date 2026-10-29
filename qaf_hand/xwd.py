"""Recadrage d'une capture XWD de la racine et conversion en PNG (stdlib seule).

Pourquoi : dans un qube, la capture fenêtre par fenêtre (`import -window ID`,
`xwd -id ID`) donne une image NOIRE (docs/RESULTATS-S1.md, D). La racine, elle,
contient bien les fenêtres du qube. On capture donc la racine (`xwd -root`) et
on découpe le rectangle de la fenêtre ici, sans ImageMagick.

Format XWD (X11 XWDFile.h) : en-tête de 25 champs CARD32 gros-boutistes, nom
de fenêtre, table de couleurs (ncolors × 12 octets), puis les pixels en
ZPixmap. Seuls les visuels TrueColor 24/32 bits à canaux d'un octet sont pris
en charge : c'est ce que sert le X de Qubes (mesuré dans un qube Debian le 25/10 :
profondeur 24, 24 bits par pixel, pas de 7680 octets pour 1920 pixels,
LSBFirst, masques 0xff0000/0xff00/0xff).
"""

import struct
import zlib

ENTETE = struct.Struct(">25I")
ZPIXMAP = 2
MAX_COTE = 16384


class XwdError(ValueError):
    """Capture XWD illisible ou non prise en charge."""


def _octet_du_masque(masque, octets_par_pixel, lsb):
    """Indice, dans un pixel, de l'octet qui porte ce canal (masque 0xff << 8k)."""
    for k in range(octets_par_pixel):
        if masque == 0xFF << (8 * k):
            return k if lsb else octets_par_pixel - 1 - k
    raise XwdError(f"masque de couleur non pris en charge : {masque:#x}")


def lire(donnees):
    """Analyse un XWD ZPixmap -> (largeur, hauteur, pixels, pas, octets/pixel, (iR, iG, iB))."""
    if len(donnees) < ENTETE.size:
        raise XwdError("XWD tronqué (en-tête)")
    h = ENTETE.unpack_from(donnees)
    taille_entete, version, fmt, _prof, larg, haut = h[:6]
    lsb = h[7] == 0  # byte_order : 0 = LSBFirst
    bpp, pas = h[11], h[12]
    masques = h[14:17]
    ncouleurs = h[19]
    if version != 7 or fmt != ZPIXMAP:
        raise XwdError("XWD : version 7 en ZPixmap attendue")
    if not (0 < larg <= MAX_COTE and 0 < haut <= MAX_COTE):
        raise XwdError("XWD : dimensions hors limites")
    # Constaté dans un qube Debian : 24 bits par pixel PACKÉS (3 octets) mais un pas
    # de ligne de 4 octets par pixel, complété de zéros. Les pixels se lisent
    # donc avec bits_per_pixel, les lignes avec bytes_per_line.
    opp = bpp // 8
    if bpp not in (24, 32) or pas < larg * opp:
        raise XwdError(f"XWD : {bpp} bits par pixel / pas {pas} non pris en charge")
    debut = taille_entete + 12 * ncouleurs
    if taille_entete < ENTETE.size or len(donnees) < debut + pas * haut:
        raise XwdError("XWD tronqué (pixels)")
    indices = tuple(_octet_du_masque(m, opp, lsb) for m in masques)
    return larg, haut, memoryview(donnees)[debut:debut + pas * haut], pas, opp, indices


def borner(x, y, l, h, larg, haut):
    """Intersection du rectangle (x, y, l, h) avec l'écran ; None si vide."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(larg, x + l), min(haut, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def png(largeur, hauteur, rgb):
    """PNG RVB 8 bits minimal (signature, IHDR, IDAT, IEND)."""
    def bloc(type_, contenu):
        return (struct.pack(">I", len(contenu)) + type_ + contenu
                + struct.pack(">I", zlib.crc32(type_ + contenu) & 0xFFFFFFFF))

    ligne = largeur * 3
    brut = b"".join(b"\x00" + bytes(rgb[i * ligne:(i + 1) * ligne]) for i in range(hauteur))
    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut, 3))
            + bloc(b"IEND", b""))


def recadrer_png(donnees, rect=None, reduction=1):
    """XWD de la racine -> (PNG, (x, y, largeur, hauteur) effectivement découpés).

    rect = (x, y, l, h) en coordonnées de la racine, ou None pour l'écran entier.
    Le rectangle est borné à l'écran ; XwdError s'il n'en reste rien.
    reduction = k : un pixel sur k en largeur et en hauteur (sous-échantillonnage)."""
    larg, haut, pix, pas, opp, (ir, ig, ib) = lire(donnees)
    zone = borner(*rect, larg, haut) if rect else (0, 0, larg, haut)
    if zone is None:
        raise XwdError("fenêtre entièrement hors de l'écran")
    x, y, l, h = zone
    k = reduction
    if k == 1:
        lignes = b"".join(pix[(y + i) * pas + x * opp:(y + i) * pas + (x + l) * opp] for i in range(h))
        rgb = bytearray(l * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = lignes[ir::opp], lignes[ig::opp], lignes[ib::opp]
        return png(l, h, rgb), zone
    l2, rgb = (l + k - 1) // k, bytearray()
    for i in range(0, h, k):
        ligne, r = pix[(y + i) * pas + x * opp:(y + i) * pas + (x + l) * opp], bytearray(l2 * 3)
        r[0::3], r[1::3], r[2::3] = ligne[ir::opp * k], ligne[ig::opp * k], ligne[ib::opp * k]
        rgb += r
    return png(l2, (h + k - 1) // k, rgb), zone
