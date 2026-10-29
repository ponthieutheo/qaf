"""Zone de notification de Plasma (qaf_ui/zone.py) : images sans X ni D-Bus."""

import pytest

from qaf_ui import zone
from qaf_ui.nuit import contraste


def image(lignes, alpha=None):
    """Image BGRX/BGRA (XGetImage ZPixmap) depuis une grille de (r, g, b)."""
    h, l = len(lignes), len(lignes[0])
    o = bytearray()
    for y, ligne in enumerate(lignes):
        for x, (r, g, b) in enumerate(ligne):
            o += bytes((b, g, r, 255 if alpha is None else alpha[y][x]))
    return l, h, l * 4, bytes(o)


BLANC, NOIR = (255, 255, 255), (0, 0, 0)


def icone_wifi():
    """22 x 22 : fond blanc, barres teintées rouge (comme le démon d'affichage)."""
    g = [[BLANC] * 22 for _ in range(22)]
    for x in range(4, 18):
        for y in range(10, 20):
            g[y][x] = (0x80, 0, 0) if x < 10 else (0xFF, 0x99, 0x99)
    return g


def pixel(argb, l, x, y):
    i = (y * l + x) * 4
    return tuple(argb[i:i + 4])


def test_fond_blanc_transparent_et_encre_a_la_couleur_du_label():
    l, h, pas, o = image(icone_wifi())
    w, hh, argb = zone.icone_qube(l, h, pas, o, 0xCC0000)
    assert (w, hh) == (zone.TAILLE, zone.TAILLE) and len(argb) == w * hh * 4
    assert pixel(argb, w, 0, 0)[0] == 0  # coin : fond retiré
    assert pixel(argb, w, 21, 5)[0] == 0
    couleur = zone.couleur_encre(0xCC0000)
    pleins = [pixel(argb, w, x, y) for y in range(w - 4) for x in range(w) if pixel(argb, w, x, y)[0] == 255]
    assert pleins and all(p[1:] == couleur for p in pleins)
    # Encre partielle : alpha intermédiaire, jamais un autre ton.
    partiels = {pixel(argb, w, x, y)[1:] for y in range(w) for x in range(w) if 0 < pixel(argb, w, x, y)[0] < 255}
    assert partiels <= {couleur}


def test_encre_lisible_sur_la_barre():
    for label in (0xCC0000, 0xF57900, 0xEDD400, 0x73D216, 0x555753, 0x3465A4, 0x75507B, 0x000000):
        c = zone.couleur_encre(label)
        assert contraste.ratio(c, zone.FOND_BARRE) >= contraste.GRAPHIQUE_MIN, hex(label)


def test_labels_noir_et_gris_prennent_les_encres_neutres():
    from qaf_ui.nuit import jetons as j
    assert zone.couleur_encre(0x000000) == contraste.rgb(j.ENCRE_NOIR)
    assert zone.couleur_encre(0x555753) == contraste.rgb(j.ENCRE_GRIS)


def test_trait_de_label_toujours_present_meme_icone_vide():
    """Repère de sécurité : toute icône de qube porte le trait, jamais une de dom0."""
    l, h, pas, o = image([[BLANC] * 22 for _ in range(22)])
    w, hh, argb = zone.icone_qube(l, h, pas, o, 0x3465A4)
    th = zone.TRAIT[1]
    milieu = w // 2
    assert pixel(argb, w, milieu, hh - 1)[0] == 255 and pixel(argb, w, milieu, hh - th)[0] == 255
    assert pixel(argb, w, milieu, hh - 1)[1:] == zone.couleur_encre(0x3465A4)
    assert all(pixel(argb, w, x, y)[0] == 0 for y in range(hh - th) for x in range(w))  # rien d'autre


def test_un_qube_ne_choisit_pas_sa_couleur():
    """Une icône multicolore sort d'une seule couleur, celle du label."""
    g = [[BLANC] * 22 for _ in range(22)]
    for x in range(22):
        for y in range(5, 15):
            g[y][x] = [(0, 0xAA, 0), (0, 0, 0xFF), (0x11, 0x11, 0x11)][x % 3]
    l, h, pas, o = image(g)
    _, _, argb = zone.icone_qube(l, h, pas, o, 0xCC0000)
    tons = {argb[i + 1:i + 4] for i in range(0, len(argb), 4) if argb[i]}
    assert tons == {bytes(zone.couleur_encre(0xCC0000))}


def test_encre_tient_au_dessus_du_trait():
    g = [[NOIR] * 22 for _ in range(22)]  # icône pleine : réduite, jamais sur le trait
    l, h, pas, o = image(g)
    w, hh, argb = zone.icone_qube(l, h, pas, o, 0x73D216)
    ligne_libre = hh - zone.TRAIT[1] - 1
    assert all(pixel(argb, w, x, ligne_libre)[0] == 0 for x in range(w))


def test_dom0_inchangee_et_deprémultipliee():
    rgb = [[(0x52, 0x52, 0x52), (0xA3, 0xA3, 0xA3)], [(0, 0, 0), (0x40, 0x20, 0x10)]]
    alpha = [[255, 255], [0, 0x80]]
    l, h, pas, o = image(rgb, alpha)
    w, hh, argb = zone.icone_dom0(l, h, pas, o, 32)
    assert pixel(argb, w, 0, 0) == (255, 0x52, 0x52, 0x52)
    assert pixel(argb, w, 0, 1)[0] == 0
    assert pixel(argb, w, 1, 1) == (0x80, 0x80, 0x40, 0x20)  # 0x40 * 255 / 128 = 0x80
    w, hh, argb = zone.icone_dom0(l, h, pas, o, 24)
    assert pixel(argb, w, 0, 1) == (255, 0, 0, 0)  # 24 bits : opaque


@pytest.mark.parametrize("args", [(0, 22, 88, b""), (22, 22, 88, b"\0" * 10), (500, 2, 2000, b"\0" * 4000),
                                  (22, 22, 40, b"\0" * 2000)])
def test_images_invalides_refusees(args):
    assert zone.icone_qube(*args, 0xCC0000) is None
    assert zone.icone_dom0(*args, 32) is None


def test_taille_ancrage():
    assert zone.taille_ancrage(22, 22) == (22, 22)
    assert zone.taille_ancrage(16, 16) == (16, 16)
    assert zone.taille_ancrage(1, 1) == (zone.TAILLE, zone.TAILLE)
    assert zone.taille_ancrage(3000, 22) == (zone.TAILLE, zone.TAILLE)


def test_fenetre_geante_bornee():
    """Une icône (d'un qube, via le démon d'affichage) qui se déclare
    10000 x 10000 est remise à TAILLE ; aucune lecture ne dépasse COTE_MAX."""
    assert zone.taille_ancrage(10000, 10000) == (zone.TAILLE, zone.TAILLE)
    assert zone.taille_ancrage(10000, 22) == (zone.TAILLE, zone.TAILLE)
    assert zone.taille_lue(10000, 10000) == (zone.COTE_MAX, zone.COTE_MAX)
    assert zone.taille_lue(0, -5) == (1, 1)
    assert zone.icone_qube(10000, 10000, 40000, b"", 0xCC0000) is None


def test_dimensionner_borne_avant_de_lire():
    """zone_x.Zone._dimensionner sur une fausse icône : taille bornée, icône
    remise à la taille bornée, lecture jamais plus grande que COTE_MAX."""
    from qaf_ui import zone_x

    class Fenetre:
        def __init__(self):
            self.configures, self.lectures = [], []

        def configure(self, **k):
            self.configures.append((k.get("width"), k.get("height")))

        def get_image(self, x, y, l, h, *_):
            self.lectures.append((l, h))
            raise RuntimeError("pas de X ici")

    z = zone_x.Zone.__new__(zone_x.Zone)
    z.X = type("X", (), {"ZPixmap": 2})
    z._ignorer = lambda: None
    ic = zone_x.Icone(Fenetre(), Fenetre(), "sys-net", 0xCC0000)
    ic.taille = (22, 22)
    z._dimensionner(ic, 10000, 10000)
    assert ic.taille == (22, 22)
    assert ic.client.configures == [(zone.TAILLE, zone.TAILLE)]  # remise à la taille bornée
    assert ic.client.lectures == []  # taille inchangée : rien relu
    z._dimensionner(ic, 64, 64)  # taille raisonnable : acceptée
    assert ic.taille == (64, 64) and ic.client.lectures == [(64, 64)]
    ic.taille = (5000, 5000)  # même si une taille énorme passait, la lecture reste bornée
    z._relire(ic)
    assert ic.client.lectures[-1] == (zone.COTE_MAX, zone.COTE_MAX)


def test_defilement():
    assert zone.bouton_defilement(120, "vertical") == (4, 1)
    assert zone.bouton_defilement(-360, "vertical") == (5, 3)
    assert zone.bouton_defilement(30, "horizontal") == (7, 1)
    assert zone.bouton_defilement(-99999, "Horizontal") == (6, 10)
    assert zone.bouton_defilement(0, "vertical") == (None, 0)


def test_attendre_les_boutons_relaches():
    assert zone.attendre_boutons(0, 0) == 0
    assert zone.attendre_boutons(1 << 10, 0) == zone.ATTENTE_PAS_MS  # bouton 3 tenu
    assert zone.attendre_boutons(1 << 8, zone.ATTENTE_MAX_MS) == 0  # on n'attend pas sans fin
    assert zone.attendre_boutons(1 << 0, 0) == 0  # Maj tenue : pas un bouton


def test_textes():
    assert zone.textes("[sys-net] Wi-Fi", "sys-net:nm-applet", "sys-net") == ("sys-net:nm-applet", "[sys-net] Wi-Fi")
    # Le nom du qube est toujours en tête, même si la fenêtre ne le dit pas.
    ident, titre = zone.textes("Réseau\x07\nfaux", "nm-applet", "sys-net")
    assert ident == "sys-net:nm-applet" and titre == "[sys-net] Réseau faux"
    assert zone.textes("qui-domains", "qui-domains", "") == ("qui-domains", "qui-domains")
    assert zone.textes("", "", "") == ("icone", "icone")
    assert len(zone.textes("x" * 5000, "y", "")[1]) <= 200


def test_echecs(tmp_path):
    f = str(tmp_path / "echecs")
    assert zone.lire_echecs(f) == 0
    assert zone.noter_echec(f) == 1 and zone.noter_echec(f) == 2
    assert zone.lire_echecs(f) == 2
    zone.oublier_echecs(f)
    assert zone.lire_echecs(f) == 0
    (tmp_path / "echecs").write_text("n'importe quoi")
    assert zone.lire_echecs(f) == 0


def test_zone_x_s_importe_sans_x_ni_gi():
    """gi et python-xlib ne sont chargés qu'au lancement."""
    import subprocess
    import sys
    code = ("import sys; from qaf_ui import zone_x; "
            "assert 'Xlib' not in sys.modules and 'gi' not in sys.modules; "
            "assert callable(zone_x.main)")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_reprendre_une_icone_prise_par_le_gestionnaire_de_fenetres():
    racine, conteneur, cadre = 0x3BF, 0x4400010, 0x16122E7
    assert zone.reprendre(cadre, racine, conteneur, 0)  # cadre de KWin : réancrée
    assert zone.reprendre(cadre, racine, conteneur, zone.REPRISES_MAX - 1)
    assert not zone.reprendre(cadre, racine, conteneur, zone.REPRISES_MAX)  # jamais de ping-pong
    assert not zone.reprendre(racine, racine, conteneur, 0)  # rendue à la racine : son programme la retire
    assert not zone.reprendre(conteneur, racine, conteneur, 0)
