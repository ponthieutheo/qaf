"""Aperçus de la direction B « affirmée / signature » (tools/rendu-ui.sh
rendu_theme_b:FONCTION sortie.png). Outil de la machine de développement, jamais installé dans
dom0 : les vraies fenêtres (carte de l'agent, palette, confirmation armée)
et l'icône de l'agent dans la barre, sur des données fabriquées."""

import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import cairo  # noqa: E402

from qaf_ui import usage as u  # noqa: E402
from qaf_ui.nuit import icones, jetons as j  # noqa: E402


class _FauxPanneau:
    """Ce que la carte lit du panneau : agent allumé, Claude mesuré."""

    def __init__(self, mesure, allume=True, statut="ok"):
        self.allume, self.derniere, self.statut = allume, mesure, statut
        self.heure = time.time()
        self.connexion, self.ajout, self.label = None, False, 0x75507B

    def etat_carte(self):
        from qaf_ui.ecrans import agent_panneau as p
        return p.etat_carte(self.allume, self.derniere, self.statut, self.ajout, self.connexion)

    def __getattr__(self, nom):  # gestes (fermer_carte, replacer, console…) : rien
        return lambda *a, **k: None


def _mesure(pc5=61, pcsem=74):
    t = int(time.time())
    return u.Mesure("ok", "max_5x", (u.Fenetre("5h", "", pc5, t + 3 * 3600 + 1200),
                                     u.Fenetre("semaine", "", pcsem, t + 4 * 86400 + 6 * 3600)),
                    None, (("codex", "connecte"), ("grok", "non_installe")))


def carte():
    from qaf_ui.ecrans import agent_panneau_gtk as g
    k = g.Carte(_FauxPanneau(_mesure()), saisir=False)
    k.remplir()
    return k


def carte_alerte():
    from qaf_ui.ecrans import agent_panneau_gtk as g
    k = g.Carte(_FauxPanneau(_mesure(93, 81)), saisir=False)
    k.remplir()
    return k


def palette():
    from qaf_ui import palette_gtk as pg
    app = pg.Palette()
    app.register(None)
    pg._style()
    app._construire()
    return app.fenetre


def confirmation_armee():
    import rendu_confirmation as rc
    from qaf_gate import classify
    from qaf_ui import fenetre
    req = rc._requete([{"op": "vm.start", "name": "work"}], [classify.N2], classify.CONFIRMER, False,
                      texte_demande=rc.demande("démarre work", "start work", "启动 work"), age_demande=5)
    fenetre.preparer()
    f = fenetre.FenetreConfirmation(req, rc.CLAVIER, lambda: 1000.0, lambda _r: None)
    # « Autoriser » armé (touche A admise) : texte clair, contour T2 (le violet reste à Refuser).
    f.etat.autorisable = lambda: True
    f._actualiser()
    f.win.unfullscreen()
    f.win.resize(900, 760)
    return f.win


def barre():
    """Icône de l'agent dans la barre de Plasma (prêt, alerte, arrêté), agrandie
    x4 sans lissage, puis le sémaphore des états (au travail = disque lavande,
    à confirmer = anneau violet, influencé, en pause) sur le fond de la barre."""
    from qaf_ui import zone
    from qaf_ui.ecrans import agent_panneau_gtk as g
    w = Gtk.Window()
    w.set_decorated(False)
    w.set_default_size(560, 200)
    zone_dessin = Gtk.DrawingArea()
    w.add(zone_dessin)
    images = [g.images_tete(cle, 0x75507B)[0] for cle in ("pret", "alerte", "arrete")]

    def rgb(h):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def dessiner(_w, cr):
        cr.set_source_rgb(*rgb(zone.FOND_BARRE))
        cr.paint()
        for n, (larg, haut, octets) in enumerate(images):
            pas = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, larg)
            donnees = bytearray(pas * haut)
            for y in range(haut):
                for x in range(larg):
                    a, r, gg, b = octets[(y * larg + x) * 4:(y * larg + x) * 4 + 4]
                    i = y * pas + x * 4
                    donnees[i:i + 4] = bytes((b * a // 255, gg * a // 255, r * a // 255, a))
            s = cairo.ImageSurface.create_for_data(donnees, cairo.FORMAT_ARGB32, larg, haut, pas)
            cr.save()
            cr.translate(24 + n * 120, 16)
            cr.scale(4, 4)
            cr.set_source_surface(s, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint()
            cr.restore()
        etats = ((icones.TRAVAIL, "AGENT"), (icones.CONFIRMER, "ATTENTION"), (icones.INFLUENCE, "DANGER"),
                 (icones.PAUSE, "ICONE"))
        for n, (etat, jeton) in enumerate(etats):
            cr.save()
            cr.translate(24 + n * 120, 128)
            cr.scale(3, 3)
            icones.dessiner_tete_agent(cr, etat, j.SIGNATURE, zone.FOND_BARRE, getattr(j, jeton))
            cr.restore()
        return False

    zone_dessin.connect("draw", dessiner)
    return w
