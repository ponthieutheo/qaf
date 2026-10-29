"""Outils de test : fabrique des captures XWD et relit des PNG (code de test seulement)."""

import struct
import zlib


def faux_xwd(larg, haut, couleur, bpp=24, pas=None, lsb=True, ncouleurs=256, nom=b"xwdump\0",
             masques=(0xFF0000, 0xFF00, 0xFF), version=7, fmt=2):
    """XWD ZPixmap comme celui du X de Qubes. couleur(x, y) -> (r, v, b).
    Par défaut : 24 bits par pixel packés, pas de 4 octets par pixel (mesuré dans un qube Debian)."""
    opp = bpp // 8
    pas = pas if pas is not None else larg * 4
    entete = [0] * 25
    entete[0] = 100 + len(nom)
    entete[1], entete[2], entete[3], entete[4], entete[5] = version, fmt, 24, larg, haut
    entete[7] = 0 if lsb else 1
    entete[8], entete[10], entete[11], entete[12], entete[13] = 32, 8, bpp, pas, 5
    entete[14:17] = masques
    entete[17], entete[18], entete[19] = 8, ncouleurs, ncouleurs
    entete[20], entete[21] = larg, haut
    pixels = bytearray()
    for y in range(haut):
        ligne = bytearray()
        for x in range(larg):
            r, v, b = couleur(x, y)
            val = 0
            for c, m in zip((r, v, b), masques):
                val |= c << ((m & -m).bit_length() - 1)
            val &= (1 << 8 * opp) - 1  # formats refusés (16 bits…) : contenu sans importance
            ligne += val.to_bytes(opp, "little" if lsb else "big")
        pixels += ligne + b"\0" * (pas - len(ligne))
    return struct.pack(">25I", *entete) + nom + b"\0" * (12 * ncouleurs) + bytes(pixels)


def lire_png(donnees):
    """PNG RVB 8 bits sans filtre (celui de qaf_hand.xwd.png) -> (largeur, hauteur, pixels[y][x])."""
    assert donnees[:8] == b"\x89PNG\r\n\x1a\n"
    pos, blocs = 8, {}
    while pos < len(donnees):
        n, = struct.unpack_from(">I", donnees, pos)
        type_, contenu = donnees[pos + 4:pos + 8], donnees[pos + 8:pos + 8 + n]
        crc, = struct.unpack_from(">I", donnees, pos + 8 + n)
        assert crc == zlib.crc32(type_ + contenu) & 0xFFFFFFFF
        blocs.setdefault(type_, b"")
        blocs[type_] += contenu
        pos += 12 + n
    larg, haut, prof, typ = struct.unpack_from(">IIBB", blocs[b"IHDR"])
    assert (prof, typ) == (8, 2) and b"IEND" in blocs
    brut = zlib.decompress(blocs[b"IDAT"])
    lignes = [brut[y * (1 + 3 * larg):(y + 1) * (1 + 3 * larg)] for y in range(haut)]
    assert all(l[0] == 0 for l in lignes)
    return larg, haut, [[tuple(l[1 + 3 * x:4 + 3 * x]) for x in range(larg)] for l in lignes]
