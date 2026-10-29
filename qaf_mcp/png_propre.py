"""Réencodage d'une capture d'écran écrite par un qube, avant de l'enregistrer
dans sys-agent ou de l'ouvrir dans une visionneuse (OPS-v3 §19, revue M5).

Le PNG vient d'un qube : il peut être piégé pour viser la visionneuse (eog,
ristretto) et, par elle, sys-agent. On ne lui laisse donc jamais atteindre une
bibliothèque d'images : décodeur minimal en Python (struct + zlib, formats
fermés, tailles bornées avant toute allocation), puis réécriture d'un PNG RVB
neuf, construit ici, sans aucun bloc annexe. Tout écart = None (rien n'est
écrit, rien n'est ouvert).

qvm-convert-img (qubes-img-converter) n'est pas utilisable dans sys-agent :
il convertit dans un jetable, et sys-agent n'a pas de jetable par défaut (C3).

Travail borné : pixels bruts au plus 3840×2160×3 octets, taux de compression
au plus MAX_TAUX (qaf_hand compresse au niveau 3 : un écran tout noir donne
~230), et le décodage tourne dans un sous-processus limité à DELAI secondes de
CPU (RLIMIT_CPU) et de temps réel, avec RLIMIT_AS. Dépassement = refus.
Bibliothèque standard seulement.
"""

import os
import struct
import subprocess
import sys
import zlib

try:
    import resource
except ImportError:  # pas sous Windows ; sys-agent est sous Linux
    resource = None

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_OCTETS = 640 * 1024  # = borne du Doorman pour qube.screenshot (OPS-v3 §3.4)
MAX_COTE = 8192
MAX_BRUT = 3840 * 2160 * 3  # octets de pixels décodés, toutes couleurs confondues
MAX_TAUX = 400  # taille décompressée / taille compressée
DELAI = 5  # secondes de CPU et de temps réel pour le sous-processus
MAX_MEMOIRE = 512 * 1024 * 1024
# type de couleur (8 bits) -> octets par pixel : gris, RVB, gris+alpha, RVBA
OCTETS_PAR_PIXEL = {0: 1, 2: 3, 4: 2, 6: 4}
_ANNEXES_PERMIS = frozenset({b"gAMA", b"sRGB", b"pHYs", b"tEXt", b"tIME", b"cHRM", b"iCCP", b"zTXt", b"iTXt",
                             b"bKGD"})  # ignorés (jamais recopiés)


class PngRefuse(ValueError):
    pass


def _blocs(donnees):
    if len(donnees) > MAX_OCTETS:
        raise PngRefuse("image trop grande")
    if not donnees.startswith(SIGNATURE):
        raise PngRefuse("pas un PNG")
    i, n = len(SIGNATURE), len(donnees)
    while i + 12 <= n:
        (longueur,) = struct.unpack(">I", donnees[i:i + 4])
        type_ = donnees[i + 4:i + 8]
        fin = i + 12 + longueur
        if fin > n:
            raise PngRefuse("bloc tronqué")
        contenu = donnees[i + 8:i + 8 + longueur]
        if struct.unpack(">I", donnees[fin - 4:fin])[0] != zlib.crc32(type_ + contenu) & 0xFFFFFFFF:
            raise PngRefuse("CRC faux")
        yield type_, contenu
        if type_ == b"IEND":
            return
        i = fin
    raise PngRefuse("IEND manquant")


def decoder(donnees):
    """PNG 8 bits non entrelacé -> (largeur, hauteur, pixels RVB bruts)."""
    entete, idat = None, []
    for type_, contenu in _blocs(donnees):
        if type_ == b"IHDR":
            if entete is not None or len(contenu) != 13:
                raise PngRefuse("IHDR invalide")
            entete = struct.unpack(">IIBBBBB", contenu)
        elif type_ == b"IDAT":
            if entete is None:
                raise PngRefuse("IDAT avant IHDR")
            idat.append(contenu)
        elif type_ != b"IEND" and type_ not in _ANNEXES_PERMIS:
            raise PngRefuse("bloc non permis")
    if entete is None or not idat:
        raise PngRefuse("IHDR ou IDAT manquant")
    larg, haut, prof, couleur, compression, filtre, entrelace = entete
    if (prof != 8 or couleur not in OCTETS_PAR_PIXEL or compression or filtre or entrelace
            or not 0 < larg <= MAX_COTE or not 0 < haut <= MAX_COTE
            or larg * haut * max(3, OCTETS_PAR_PIXEL.get(couleur, 3)) > MAX_BRUT):
        raise PngRefuse("format non permis")
    bpp = OCTETS_PAR_PIXEL[couleur]
    ligne = larg * bpp
    attendu = (ligne + 1) * haut
    if attendu > MAX_TAUX * sum(len(b) for b in idat):
        raise PngRefuse("compression anormale")
    d = zlib.decompressobj()
    try:
        brut = d.decompress(b"".join(idat), attendu + 1)  # jamais plus que l'attendu (bombe zlib)
    except zlib.error:
        raise PngRefuse("données compressées invalides") from None
    if len(brut) != attendu or d.unconsumed_tail:
        raise PngRefuse("taille des pixels incohérente")
    pixels, prec = bytearray(), bytearray(ligne)
    for y in range(haut):
        f = brut[y * (ligne + 1)]
        cur = bytearray(brut[y * (ligne + 1) + 1:(y + 1) * (ligne + 1)])
        if f == 1:
            for x in range(bpp, ligne):
                cur[x] = (cur[x] + cur[x - bpp]) & 255
        elif f == 2:
            for x in range(ligne):
                cur[x] = (cur[x] + prec[x]) & 255
        elif f == 3:
            for x in range(ligne):
                cur[x] = (cur[x] + ((cur[x - bpp] if x >= bpp else 0) + prec[x]) // 2) & 255
        elif f == 4:
            for x in range(ligne):
                a = cur[x - bpp] if x >= bpp else 0
                b = prec[x]
                c = prec[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise PngRefuse("filtre inconnu")
        pixels += cur
        prec = cur
    return larg, haut, _en_rvb(pixels, couleur)


def _en_rvb(px, couleur):
    if couleur == 2:
        return bytes(px)
    if couleur == 6:
        out = bytearray(len(px) // 4 * 3)
        out[0::3], out[1::3], out[2::3] = px[0::4], px[1::4], px[2::4]  # alpha ignoré
        return bytes(out)
    gris = px if couleur == 0 else px[0::2]
    out = bytearray(len(gris) * 3)
    out[0::3] = out[1::3] = out[2::3] = gris
    return bytes(out)


def encoder(larg, haut, rgb):
    """PNG RVB 8 bits minimal (signature, IHDR, IDAT, IEND), comme qaf_hand/xwd.py."""
    def bloc(type_, contenu):
        return (struct.pack(">I", len(contenu)) + type_ + contenu
                + struct.pack(">I", zlib.crc32(type_ + contenu) & 0xFFFFFFFF))

    ligne = larg * 3
    brut = b"".join(b"\x00" + rgb[i * ligne:(i + 1) * ligne] for i in range(haut))
    return (SIGNATURE + bloc(b"IHDR", struct.pack(">IIBBBBB", larg, haut, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut, 6)) + bloc(b"IEND", b""))


def reencoder_ici(donnees):
    """Réencodage dans ce processus, sans limite de temps (sous-processus et tests)."""
    try:
        return encoder(*decoder(donnees))
    except PngRefuse:
        return None


def _limiter():
    resource.setrlimit(resource.RLIMIT_CPU, (DELAI, DELAI))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMOIRE, MAX_MEMOIRE))


_ENFANT = ("import sys; sys.path.insert(0, sys.argv[1]); from qaf_mcp.png_propre import reencoder_ici; "
           "d = sys.stdin.buffer.read(); r = reencoder_ici(d); sys.stdout.buffer.write(r or b''); "
           "sys.exit(0 if r else 3)")


def reencoder(donnees):
    """Octets venus d'un qube -> PNG propre, ou None si refusé ou trop long.
    Décodage dans un sous-processus borné (CPU, mémoire, temps réel)."""
    if len(donnees) > MAX_OCTETS or not donnees.startswith(SIGNATURE):
        return None  # refus immédiat, sans sous-processus
    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _ENFANT, racine], input=donnees, capture_output=True,
                           timeout=DELAI + 1, preexec_fn=_limiter if resource else None)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.startswith(SIGNATURE):
        return None
    return r.stdout
