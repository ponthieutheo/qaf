"""qaf-agent-panneau, partie GTK : icône de la zone de notification de Plasma
(qaf_ui.sni), mesure en fil de fond (qaf.Usage), et la carte (SurfaceAF).
Logique, textes et argv : agent_panneau.py et qaf_ui/usage.py.

D'après Omarchy (basecamp/omarchy, shell/plugins/agents/Panel.qml), idée
reprise, code réécrit.
"""

import dataclasses
import fcntl
import os
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import cairo  # noqa: E402

from qaf_ui import confirmation, palette  # noqa: E402
from qaf_ui import sni  # noqa: E402
from qaf_ui import zone  # noqa: E402
from qaf_ui import usage as u  # noqa: E402
from qaf_ui import voisin as v  # noqa: E402
from qaf_ui.ecrans import agent_panneau as p  # noqa: E402
from qaf_ui.nuit import composants as c  # noqa: E402
from qaf_ui.nuit import contraste, icones, jetons as j  # noqa: E402
from qaf_ui import couleurs_plasma  # noqa: E402
from qaf_ui.fenetre import appareil  # noqa: E402
from qaf_ui.i18n import t  # noqa: E402

# Couleurs du thème Plasma (28/10 : la carte doit aller avec la barre) ;
# tailles, police de code et graisses restent celles des écrans QAF.
k = couleurs_plasma.palette()

LABEL_RELU_S = 60
DESSIN = 64  # côté du dessin de la tête, réduit ensuite par qaf-zone (moyenne des surfaces)


# --- icône de la barre : style des icônes de qubes de qaf-zone -------------------------------

def _dessin_tete(etat):
    """Tête de l'agent noire sur fond blanc, au format d'une image XGetImage
    (B, G, R, X ; pas = octets par ligne) : ce que qaf-zone reçoit d'une icône de
    qube. (largeur, hauteur, pas, octets)."""
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, DESSIN, DESSIN)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(DESSIN / 16, DESSIN / 16)
    icones.dessiner_tete_agent(cr, etat, "#000000", "#FFFFFF")
    surface.flush()
    return DESSIN, DESSIN, surface.get_stride(), bytes(surface.get_data())


def images_tete(cle, label):
    """Images IconPixmap de l'icône : pret, alerte (pastille ATTENTION) ou
    arrete (yeux fermés, encre atténuée). Tête au violet SIGNATURE (direction
    B), trait dessous à la couleur du label de sys-agent, exactement comme
    qaf-zone pose celui des icônes des qubes."""
    image = zone.icone_qube(*_dessin_tete(icones.ARRETE if cle == "arrete" else icones.PRET), label,
                            dessin=contraste.rgb(j.SIGNATURE))
    if cle == "arrete":
        image = p.attenuer(image, p.ATTENUATION_ARRETE, zone.TRAIT[1])
    elif cle == "alerte":
        image = p.pastille(image, k.ATTENTION)
    return [image]


def lire_label():
    """Couleur du label de sys-agent par qubesadmin (jamais codée en dur)."""
    try:
        import qubesadmin
        return p.label_de(qubesadmin.Qubes().domains[u.AGENT].label.color)
    except Exception:  # noqa: BLE001 (qubesadmin absent, qube inconnu : encre neutre)
        return p.LABEL_DEFAUT


# --- style de la carte (jetons Nuit, ce processus seulement) ---------------------------------
# Direction B : contour 2 px SIGNATURE, titre « Agent » en SIGNATURE, boutons
# carrés sur SIGNATURE_FOND à icône SIGNATURE, jauges SIGNATURE (k.ACCENT).

def css():
    a = 0.06 * j.TAILLE_PETITE  # espacement des capitales : 0,06 em
    return f"""
.qaf-panneau-agent {{ font-family: {k.POLICE}; }}
window.qaf-surface.qaf-panneau-agent {{ background-color: {k.SURFACE}; border: 2px solid {j.SIGNATURE}; }}
.qaf-panneau-agent .pa-titre {{ font-size: {j.TAILLE_GRANDE}px; font-weight: {j.GRAISSE_ACCENT}; color: {j.SIGNATURE}; }}
.qaf-panneau-agent .pa-heros {{ font-size: {j.TAILLE_PETITE}px; font-weight: {j.GRAISSE_FORTE};
    color: {k.T3}; letter-spacing: {a:.2f}px; }}
.qaf-panneau-agent .pa-section {{ font-size: {j.TAILLE_PETITE}px; font-weight: {j.GRAISSE_FORTE};
    color: {k.T3}; letter-spacing: {a:.2f}px; }}
.qaf-panneau-agent .pa-nom {{ font-size: {j.TAILLE_CORPS}px; font-weight: {j.GRAISSE_ACCENT}; color: {k.T1}; }}
.qaf-panneau-agent .pa-offre, .qaf-panneau-agent .pa-note {{ font-size: {j.TAILLE_PETITE}px; color: {k.T3}; }}
.qaf-panneau-agent .pa-jauge-titre {{ font-size: {j.TAILLE_CORPS}px; color: {k.T1}; }}
.qaf-panneau-agent .pa-jauge-titre.pa-gardee {{ color: {k.T2}; }}
.qaf-panneau-agent .pa-duree {{ font-size: {j.TAILLE_CORPS}px; color: {k.T2}; font-feature-settings: "tnum"; }}
.qaf-panneau-agent .pa-duree.pa-gardee {{ color: {k.T3}; }}
.qaf-panneau-agent .pa-texte {{ font-size: {j.TAILLE_CORPS}px; color: {k.T2}; }}
.qaf-panneau-agent .pa-fort {{ font-size: {j.TAILLE_CORPS}px; color: {k.T1}; font-weight: {j.GRAISSE_FORTE}; }}
.qaf-panneau-agent .pa-attention {{ font-size: {j.TAILLE_CORPS}px; color: {k.ATTENTION}; }}
.qaf-panneau-agent .pa-num {{ font-size: {j.TAILLE_CORPS}px; color: {k.T3}; font-feature-settings: "tnum"; }}
.qaf-panneau-agent .pa-code {{ font-family: {j.POLICE_CODE}; font-size: {j.TAILLE_CORPS}px; color: {k.T1}; }}
.qaf-panneau-agent separator.pa-filet {{ background-color: {k.FILET}; min-height: 1px; margin: 0; }}
.qaf-panneau-agent button {{ background-image: none; box-shadow: none; text-shadow: none;
    -gtk-icon-shadow: none; outline-style: none; transition: background-color {j.TRANSITION_MS}ms; }}
.qaf-panneau-agent button.pa-carre {{ min-width: {p.BOUTON}px; min-height: {p.BOUTON}px; padding: 0;
    background-color: {j.SIGNATURE_FOND}; border: 1px solid {j.SIGNATURE_FOND}; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-carre .qaf-icone {{ color: {j.SIGNATURE}; }}
.qaf-panneau-agent button.pa-carre:hover, .qaf-panneau-agent button.pa-carre:focus {{
    border-color: {j.SIGNATURE}; }}
.qaf-panneau-agent button.pa-lien {{ padding: 0; min-height: 0; background-color: transparent; border: none; }}
.qaf-panneau-agent button.pa-lien label {{ font-size: {j.TAILLE_CORPS}px; color: {k.T2};
    text-decoration-line: underline; }}
.qaf-panneau-agent button.pa-lien.pa-alerte label {{ color: {j.DANGER}; }}
.qaf-panneau-agent button.pa-lien:hover label, .qaf-panneau-agent button.pa-lien:focus label {{ color: {k.T1}; }}
.qaf-panneau-agent .qaf-icone.pa-c {{ color: {j.SIGNATURE}; }}
.qaf-panneau-agent button.pa-rang {{ min-height: {p.LIGNE}px; padding: 0; background-color: transparent;
    border: none; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-rang:hover, .qaf-panneau-agent button.pa-rang:focus {{ background-color: {k.CHAMP}; }}
.qaf-panneau-agent button.pa-rang:active {{ background-color: {k.CHOISI}; }}
.qaf-panneau-agent button.pa-rang:disabled {{ background-color: transparent; }}
.qaf-panneau-agent button.pa-det {{ min-width: {p.LIGNE}px; min-height: {p.LIGNE}px; padding: 0;
    background-color: transparent; border: none; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-det .qaf-icone {{ color: {k.T3}; }}
.qaf-panneau-agent button.pa-det:hover, .qaf-panneau-agent button.pa-det:focus {{ background-color: {k.CHAMP}; }}
.qaf-panneau-agent button.pa-det:hover .qaf-icone, .qaf-panneau-agent button.pa-det:focus .qaf-icone {{ color: {k.T1}; }}
.qaf-panneau-agent .pa-rnom {{ font-size: {j.TAILLE_CORPS}px; color: {k.T1}; }}
.qaf-panneau-agent .pa-rnom.pa-actif {{ font-weight: {j.GRAISSE_ACCENT}; }}
.qaf-panneau-agent .pa-rnom.pa-eteint {{ color: {k.T3}; }}
.qaf-panneau-agent .pa-statut {{ font-size: {j.TAILLE_PETITE}px; color: {k.T3}; }}
.qaf-panneau-agent .pa-statut.pa-ok {{ color: {k.T2}; }}
.qaf-panneau-agent .pa-statut.pa-actif {{ color: {k.T1}; font-weight: {j.GRAISSE_FORTE}; }}
.qaf-panneau-agent .pa-statut.pa-danger {{ color: {j.DANGER}; font-weight: {j.GRAISSE_FORTE}; }}
.qaf-panneau-agent .pa-use {{ font-size: {j.TAILLE_CORPS}px; color: {k.T1}; text-decoration-line: underline; }}
.qaf-panneau-agent button.pa-rang:disabled .pa-use {{ color: {k.T3}; }}
.qaf-panneau-agent .pa-echec {{ font-size: {j.TAILLE_CORPS}px; color: {j.DANGER}; }}
.qaf-panneau-agent .pa-echec.pa-fort {{ font-weight: {j.GRAISSE_FORTE}; }}
.qaf-panneau-agent button.pa-principal {{ min-height: {p.LIGNE}px; padding: 0 16px; background-color: {j.SIGNATURE};
    border: none; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-principal:hover, .qaf-panneau-agent button.pa-principal:focus {{
    background-color: {j.SIGNATURE_SURVOL}; }}
.qaf-panneau-agent button.pa-principal label {{ font-size: {j.TAILLE_CORPS}px; font-weight: {j.GRAISSE_ACCENT};
    color: {j.BARRE}; }}
.qaf-panneau-agent button.pa-contour {{ min-height: {p.LIGNE}px; padding: 0 16px; background-color: transparent;
    border: 1px solid {k.T2}; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-contour label {{ font-size: {j.TAILLE_CORPS}px; color: {k.T1}; }}
.qaf-panneau-agent button.pa-contour:hover, .qaf-panneau-agent button.pa-contour:focus {{ border-color: {k.T1};
    background-color: {k.CHAMP}; }}
.qaf-panneau-agent button.pa-contour:disabled {{ border-color: {k.FILET}; background-color: transparent; }}
.qaf-panneau-agent button.pa-contour:disabled label {{ color: {k.T3}; }}
.qaf-panneau-agent entry.pa-port {{ min-height: 0; padding: 4px 8px; font-size: {j.TAILLE_CORPS}px; color: {k.T1};
    background-color: {k.CHAMP}; background-image: none; border: 1px solid {k.FILET}; border-radius: {j.RAYON}px;
    box-shadow: none; caret-color: {j.SIGNATURE}; font-feature-settings: "tnum"; }}
.qaf-panneau-agent entry.pa-port:focus {{ border-color: {j.SIGNATURE}; }}
.qaf-panneau-agent entry.pa-port:disabled {{ color: {k.T3}; }}
.qaf-panneau-agent scrolledwindow.pa-liste overshoot, .qaf-panneau-agent scrolledwindow.pa-liste undershoot {{
    background: none; border: none; box-shadow: none; }}
.qaf-panneau-agent scrolledwindow.pa-liste scrollbar {{ background-color: transparent; border: none; }}
.qaf-panneau-agent scrolledwindow.pa-liste scrollbar slider {{ min-width: 4px; min-height: 24px; margin: 0;
    border: none; border-radius: 2px; background-color: {k.T3}; }}
"""


_style = None


def installer_css():
    global _style
    if _style is None:
        _style = Gtk.CssProvider()
        _style.load_from_data(css().encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), _style, c.PRIORITE_STYLE + 1)


# --- briques de la carte ------------------------------------------------------------------

def _lab(texte, *classes, xalign=0.0, largeur=None, enrouler=False):
    lab = Gtk.Label(label=texte, xalign=xalign)
    for k in classes:
        lab.get_style_context().add_class(k)
    if largeur:
        lab.set_size_request(largeur, -1)
    if enrouler:
        lab.set_line_wrap(True)
        lab.set_max_width_chars(48)
    return lab


def _filet():
    s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    s.get_style_context().add_class("pa-filet")
    return s


class Jauge(Gtk.DrawingArea):
    """Jauge de 4 px, rayon 2 : piste CHOISI, plein T1 (ATTENTION à 90 %, T3
    gardée) ; traits verticaux 2 x 10 px pour les limites propres à un modèle."""

    def __init__(self, pourcent, couleur, traits=()):
        super().__init__(hexpand=True)
        self.pourcent, self.couleur, self.traits = pourcent, couleur, list(traits)
        self.set_size_request(-1, 12)
        self.connect("draw", self._dessiner)

    @staticmethod
    def _rgb(h):
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def _barre(self, cr, x, y, w, h):
        r = h / 2
        if w <= 0:
            return
        r = min(r, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 1.5708)
        cr.arc(x + r, y + r, r, 1.5708, 4.7124)
        cr.close_path()
        cr.fill()

    def _dessiner(self, _w, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        y = (h - p.JAUGE) / 2
        cr.set_source_rgb(*self._rgb(k.CHOISI))
        self._barre(cr, 0, y, w, p.JAUGE)
        cr.set_source_rgb(*self._rgb(getattr(k, self.couleur)))
        self._barre(cr, 0, y, w * max(0, min(100, self.pourcent)) / 100, p.JAUGE)
        cr.set_source_rgb(*self._rgb(k.T2))
        for trait in self.traits:
            x = max(1, min(w - 1, w * trait / 100))
            cr.rectangle(x - 1, (h - 10) / 2, 2, 10)
            cr.fill()
        return False


def _entree(bouton, ev):
    """Le survol déplace le curseur unique, s'il y a eu un geste. Un défilement ou une
    reconstruction glisse aussi une ligne sous un pointeur immobile : GTK annonce alors une
    entrée, qui volerait le focus du clavier en pleine descente."""
    carte = bouton.get_toplevel()
    position = (ev.x_root, ev.y_root)
    if getattr(carte, "pointeur", None) != position:
        carte.pointeur = position
        bouton.grab_focus()
    return False


def _bouton(classe, contenu, rappel, infobulle=None, cle=None):
    b = Gtk.Button()
    b.get_style_context().add_class(classe)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.add(contenu)
    b.set_can_focus(True)
    if infobulle:
        b.set_tooltip_text(infobulle)
    b.connect("clicked", lambda *_: rappel())
    b.connect("enter-notify-event", _entree)
    b.qaf_cle = cle
    return b


def _lien(texte, rappel, *classes, cle=None, infobulle=None):
    b = _bouton("pa-lien", _lab(texte), rappel, infobulle, cle)
    for k in classes:
        b.get_style_context().add_class(k)
    b.set_halign(Gtk.Align.START)
    return b


def _tete(panneau, etat, taille):
    """Tête de l'agent dans la carte : trait au violet SIGNATURE (direction B),
    comme l'icône de la barre ; à l'arrêt, T3. Détourage sur le fond Plasma de
    la carte."""
    tete = c.TeteAgent(etat, taille=taille)
    tete.couleur = (lambda: k.T3) if etat == icones.ARRETE else (lambda: j.SIGNATURE)
    tete._fond = lambda: k.SURFACE
    return tete


def _gouttiere(enfant=None):
    """Colonne de 36 px : la coche, centrée sous la tête, ou rien. Le nom tombe sous « Agent »."""
    b = Gtk.Box()
    b.set_size_request(p.COLONNE, -1)
    if enfant is not None:
        enfant.set_margin_start(p.RETRAIT_COCHE)
        enfant.set_halign(Gtk.Align.START)
        enfant.set_valign(Gtk.Align.CENTER)
        b.pack_start(enfant, False, False, 0)
    return b


def _coche():
    i = c.Icone("coche", taille=p.ICONE_LIGNE)
    i.get_style_context().add_class("pa-c")
    return i


def _carre(couleur):
    """Carré du label d'un qube, lu par qubesadmin : repère de dom0, pas du qube. Sans label
    lisible, T3."""
    v_label = p.label_de(couleur) if couleur else p.LABEL_DEFAUT
    rgb = Jauge._rgb(f"#{v_label:06x}" if couleur and v_label != p.LABEL_DEFAUT else k.T3)
    z = Gtk.DrawingArea()
    z.set_size_request(p.CARRE, p.CARRE)
    z.set_valign(Gtk.Align.CENTER)
    z.set_margin_end(8)

    def dessiner(_w, cr):
        cr.set_source_rgb(*rgb)
        cr.rectangle(0, 0, p.CARRE, p.CARRE)
        cr.fill()
        return False
    z.connect("draw", dessiner)
    return z


def _classe(widget, *classes):
    for nom in classes:
        widget.get_style_context().add_class(nom)
    return widget


# --- la carte ---------------------------------------------------------------------------------

class Carte(c.SurfaceAF):
    def __init__(self, panneau, saisir=True):
        super().__init__(p.LARGEUR, rappel_fermer=panneau.fermer_carte, titre=t("carte.titre"))
        installer_css()
        self.panneau, self.avec_saisie = panneau, saisir
        self.get_style_context().add_class("qaf-panneau-agent")
        # Contour 2 px SIGNATURE (direction B) : la place de 2 px de SurfaceAF est gardée.
        self.boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_BLOCS)
        for cote in ("top", "bottom", "start", "end"):
            getattr(self.boite, f"set_margin_{cote}")(p.MARGE)
        self.add(self.boite)
        self.phrases, self.rang, self.ligne = [], 0, None
        self.defil, self._vue, self.pointeur = None, object(), _pointeur()
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("motion-notify-event", self._mouvement)
        self._minuteries = [GLib.timeout_add(p.ROTATION_MS, self._tourner),
                            GLib.timeout_add_seconds(p.MINUTERIE_DUREES_S, self._a_jour)]
        self.connect("key-press-event", self._clavier)
        self.connect("destroy", self._fin)

    def _fin(self, *_):
        for m in self._minuteries:
            GLib.source_remove(m)
        self._minuteries = []

    def _mouvement(self, _w, ev):
        self.pointeur = (ev.x_root, ev.y_root)
        return False

    def _a_jour(self):
        self.remplir()
        return True

    # Clavier : flèches et hjkl promènent le focus (le « curseur » unique, que la
    # souris déplace aussi) ; Entrée agit, ou rafraîchit si rien n'a le focus ; r
    # rafraîchit ; Échap ferme (SurfaceAF).
    def _clavier(self, _w, ev):
        if self.panneau.voisin is not None:  # P2 : sur cet écran, le clavier interne seul agit
            nom = Gdk.keyval_name(ev.keyval) or ""
            if not palette.frappe_admise(self.panneau.voisin.clavier, nom, appareil(ev)):
                return True
            if isinstance(self.get_focus(), Gtk.Entry):
                return not p.touche_du_port(nom)
        pas = {Gdk.KEY_j: 1, Gdk.KEY_Down: 1, Gdk.KEY_k: -1, Gdk.KEY_Up: -1}.get(ev.keyval)
        voisine = self._ligne_voisine(pas) if pas else None
        if voisine is not None:
            voisine.grab_focus()
            return True
        touches = {Gdk.KEY_h: Gtk.DirectionType.LEFT, Gdk.KEY_j: Gtk.DirectionType.DOWN,
                   Gdk.KEY_k: Gtk.DirectionType.UP, Gdk.KEY_l: Gtk.DirectionType.RIGHT}
        if ev.keyval in touches:
            self.emit("move-focus", touches[ev.keyval])
            return True
        if ev.keyval == Gdk.KEY_r:
            self.panneau.rafraichir(force=True)
            return True
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not isinstance(self.get_focus(), Gtk.Button):
            self.panneau.rafraichir(force=True)
            return True
        return False

    def _lignes(self):
        """Un bouton par ligne de la zone défilante, dans l'ordre de lecture."""
        if self.defil is None:
            return []
        lignes = []
        for w in self.defil.get_child().get_child().get_children():
            boutons = [w] if isinstance(w, Gtk.Button) else w.get_children() if isinstance(w, Gtk.Box) else []
            premier = next((b for b in boutons if isinstance(b, Gtk.Button) and b.get_sensitive()), None)
            if premier is not None:
                lignes.append(premier)
        return lignes

    def _ligne_voisine(self, pas):
        """Ligne au-dessus ou au-dessous du focus, sur toute la liste, visible ou non."""
        focus, lignes = self.get_focus(), self._lignes()
        rang = next((i for i, b in enumerate(lignes)
                     if focus is not None and focus.get_parent() in (b, b.get_parent()) or focus is b), None)
        if rang is None or not 0 <= rang + pas < len(lignes):
            return None
        return lignes[rang + pas]

    def _tourner(self):
        if self.ligne is None or len(self.phrases) < 2:
            return True
        self.rang = (self.rang + 1) % len(self.phrases)
        lab, texte, debut = self.ligne, self.phrases[self.rang], time.monotonic()

        def pas():
            ms = (time.monotonic() - debut) * 1000
            if lab is not self.ligne:
                return False
            if ms < p.FONDU_SORTIE_MS:
                lab.set_opacity(1 - ms / p.FONDU_SORTIE_MS)
                return True
            if lab.get_text() != texte:
                lab.set_text(texte)
            e = (ms - p.FONDU_SORTIE_MS) / p.FONDU_ENTREE_MS
            lab.set_opacity(min(1.0, e))
            return e < 1
        GLib.timeout_add(16, pas)
        return True

    # --- construction ------------------------------------------------------------------
    def remplir(self):
        """Reconstruit la carte seulement si ce qu'elle montre a changé : le tic de 5 s ne doit
        ni perdre le défilement, ni le survol, ni l'ordre du clavier."""
        pn = self.panneau
        vue = pn.empreinte()
        if vue == self._vue:
            return
        self._vue = vue
        focus = getattr(self.get_focus(), "qaf_cle", None)
        hauteur = self.defil.get_vadjustment().get_value() if self.defil is not None else 0
        self.defil = None
        for enfant in self.boite.get_children():
            self.boite.remove(enfant)
            enfant.destroy()
        maintenant = time.time()
        etat = pn.etat_carte()
        m = pn.derniere
        self.boite.pack_start(self._heros(etat, m), False, False, 0)
        self.boite.pack_start(_filet(), False, False, 0)
        if etat == "voisin":
            self._voisin()
        elif etat == "connexion":
            self._connexion()
        elif etat in ("vide", "ajout"):
            self._fournisseurs(etat)
        else:
            self._carte(etat, m, maintenant)
        self.boite.show_all()
        cible = self._chercher(focus) if focus else None
        if cible is not None:
            cible.grab_focus()
        elif self.get_focus() is not None and not self.get_focus().get_mapped():
            self.set_focus(None)
        if self.defil is not None and hauteur:
            # Après l'allocation : avant, la nouvelle zone n'a pas encore sa hauteur.
            defil = self.defil
            GLib.idle_add(lambda: defil is self.defil and defil.get_vadjustment().set_value(hauteur) and False,
                          priority=GLib.PRIORITY_LOW)
        self.resize(p.LARGEUR, 1)
        self.panneau.replacer()

    def _chercher(self, cle):
        pile = list(self.boite.get_children())
        while pile:
            w = pile.pop()
            if getattr(w, "qaf_cle", None) == cle and w.get_sensitive():
                return w
            if isinstance(w, Gtk.Container) and not isinstance(w, Gtk.Button):
                pile.extend(w.get_children())
        return None

    def _heros(self, etat, m):
        pn = self.panneau
        boite = Gtk.Box(spacing=p.COLONNE - p.TETE_HEROS)
        tete = _tete(pn, icones.ARRETE if etat == "arrete" else icones.PRET, p.TETE_HEROS)
        boite.pack_start(tete, False, False, 0)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        textes.pack_start(_lab(t("carte.titre"), "pa-titre"), False, False, 0)
        if etat == "voisin":
            phrases = [t("carte.hero_voisin")]
        elif etat == "connexion":
            phrases = [t("carte.heros_connexion", nom=u.nom_fournisseur(pn.connexion[0]).upper())]
        else:
            phrases = u.phrases_heros(m, pn.allume, etat)
        if phrases != self.phrases:
            self.phrases, self.rang = phrases, 0
        self.ligne = _lab(self.phrases[self.rang % len(self.phrases)], "pa-heros")
        self.ligne.set_ellipsize(3)  # Pango.EllipsizeMode.END
        textes.pack_start(self.ligne, False, False, 0)
        boite.pack_start(textes, True, True, 0)
        boutons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if etat in ("ajout", "connexion", "voisin"):
            boutons.pack_start(_bouton("pa-carre", c.Icone("fermer"), pn.retour, t("carte.infobulle_retour"),
                                       "retour"), False, False, 0)
        elif etat != "vide":
            boutons.pack_start(_bouton("pa-carre", c.Icone("plus"), pn.ajouter, t("carte.infobulle_plus"), "plus"),
                               False, False, 0)
            boutons.pack_start(_bouton("pa-carre", c.Icone("terminal"), pn.console, t("carte.infobulle_console"),
                                       "console"), False, False, 0)
        boite.pack_end(boutons, False, False, 0)
        return boite

    def _defilante(self, contenu, maxi):
        """Zone à hauteur bornée, sans barre sous le texte quand tout tient. Le focus du clavier
        la fait suivre : une ligne atteinte aux flèches n'est jamais sous le bord."""
        zone_liste = Gtk.ScrolledWindow()
        zone_liste.get_style_context().add_class("pa-liste")
        zone_liste.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        zone_liste.set_propagate_natural_height(True)
        zone_liste.set_max_content_height(maxi)
        zone_liste.set_shadow_type(Gtk.ShadowType.NONE)
        zone_liste.add(contenu)
        zone_liste.get_child().set_shadow_type(Gtk.ShadowType.NONE)
        contenu.set_focus_vadjustment(zone_liste.get_vadjustment())
        self.defil = zone_liste
        return zone_liste

    def _note(self, texte, *classes):
        lab = _lab(texte, *(classes or ("pa-note",)))
        # Largeur fixée à la colonne : sinon un libellé enroulé réclame sa largeur naturelle
        # et élargit la carte de 412 px.
        lab.set_line_wrap(True)
        lab.set_max_width_chars(1)
        lab.set_size_request(p.CONTENU - p.COLONNE, -1)
        lab.set_margin_start(p.COLONNE)
        return lab

    def _texte_statut(self, m, cle, statut):
        """(texte, classes) à droite d'une ligne."""
        pn = self.panneau
        if pn.bascule == cle:
            return t("carte.bascule"), ("pa-statut", "pa-ok")
        if statut == "actif":
            sante = u.sante_affichee(m) if pn.allume else None
            return (t(sante), ("pa-statut", "pa-danger")) if sante else (t("carte.actif"), ("pa-statut", "pa-actif"))
        if statut == "connecte" and cle == "local" and pn.regle() is not None:
            return t("carte.voisin_connecte"), ("pa-statut", "pa-ok")
        if statut == "connecte":
            return t("carte.connectee" if cle == "local" else "carte.connecte"), ("pa-statut", "pa-ok")
        if statut == "connexion_seule":
            return t("carte.connexion_seule"), ("pa-statut", "pa-ok")
        return t("carte.non_installe" if statut == "non_installe" else "carte.a_configurer"), ("pa-statut",)

    def _rang(self, cle):
        """Ligne du « + ». Clic : basculer si elle est activable, sinon ouvrir le terminal. La
        ligne active n'a pas de survol : elle n'a plus rien à faire."""
        pn, m = self.panneau, self.panneau.derniere
        statut, geste = p.statut_ligne(m, cle), p.geste_ligne(m, cle, pn.allume)
        actif = statut == "actif"
        interieur = Gtk.Box()
        interieur.set_size_request(-1, p.LIGNE)
        interieur.pack_start(_gouttiere(_coche() if actif else None), False, False, 0)
        nom = _lab(u.nom_fournisseur(cle), "pa-rnom")
        if actif:
            nom.get_style_context().add_class("pa-actif")
        elif geste is None:
            nom.get_style_context().add_class("pa-eteint")
        interieur.pack_start(nom, False, False, 0)
        if cle == "local" and statut == "a_configurer":
            meta = _lab(t("carte.meta_local"), "pa-statut")
            meta.set_margin_start(8)
            interieur.pack_start(meta, False, False, 0)
        texte, classes = self._texte_statut(m, cle, statut)
        droite = _lab(texte, *classes, xalign=1.0)
        droite.set_margin_end(4)
        interieur.pack_end(droite, False, False, 0)
        ligne = Gtk.Box()
        if actif:
            ligne.pack_start(interieur, True, True, 0)
        else:
            bulle = t("carte.bulle_terminal") if geste == "terminal" else None
            if geste is None and pn.allume:
                bulle = t("carte.bulle_maj", nom=u.nom_fournisseur(cle))
            b = _bouton("pa-rang", interieur, lambda k=cle: pn.choisir(k), bulle, f"f-{cle}")
            b.set_sensitive(geste is not None and pn.bascule is None)
            ligne.pack_start(b, True, True, 0)
        if p.icone_terminal(m, cle, pn.allume):
            ligne.pack_start(_bouton("pa-det", c.Icone("terminal", taille=p.ICONE_LIGNE),
                                     lambda k=cle: pn.connecter(k), t("carte.bulle_reglages"), f"t-{cle}"),
                             False, False, 0)
        else:
            vide = Gtk.Box()
            vide.set_size_request(p.LIGNE, p.LIGNE)
            ligne.pack_start(vide, False, False, 0)
        return ligne

    def _rang_autre(self, cle):
        """Carte normale : un autre fournisseur, avec son geste visible en permanence."""
        pn, m = self.panneau, self.panneau.derniere
        interieur = Gtk.Box()
        interieur.set_size_request(-1, p.LIGNE)
        interieur.pack_start(_gouttiere(), False, False, 0)
        interieur.pack_start(_lab(u.nom_fournisseur(cle), "pa-rnom"), False, False, 0)
        meta = _lab(p.meta_fournisseur(m, cle, pn.regle()), "pa-statut")
        meta.set_margin_start(8)
        interieur.pack_start(meta, False, False, 0)
        droite = _lab(t("carte.bascule"), "pa-statut", "pa-ok", xalign=1.0) if pn.bascule == cle \
            else _lab(t("carte.utiliser"), "pa-use", xalign=1.0)
        droite.set_margin_end(4)
        interieur.pack_end(droite, False, False, 0)
        b = _bouton("pa-rang", interieur, lambda k=cle: pn.choisir(k), None, f"f-{cle}")
        b.set_sensitive(pn.allume and pn.bascule is None)
        return b

    def _entete_actif(self, cle, meta, sante=None):
        boite = Gtk.Box()
        boite.set_size_request(-1, p.LIGNE)
        boite.pack_start(_gouttiere(_coche()), False, False, 0)
        boite.pack_start(_lab(u.nom_fournisseur(cle), "pa-rnom", "pa-actif"), False, False, 0)
        droite = _lab(t(sante), "pa-statut", "pa-danger", xalign=1.0) if sante else _lab(meta, "pa-statut", xalign=1.0)
        droite.set_margin_end(4)
        boite.pack_end(droite, False, False, 0)
        return boite

    def _carte(self, etat, m, maintenant):
        pn = self.panneau
        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_CORPS)
        actif = u.actif_de(m)
        allume = etat != "arrete"
        sante = u.sante_affichee(m) if allume else None
        if actif in ("claude", None):
            self._abonnement(corps, etat, m, maintenant, sante)
        else:
            regle = pn.regle() if actif == "local" else None
            corps.pack_start(self._entete_actif(actif, p.meta_fournisseur(m, actif, regle), sante), False, False, 0)
            if not allume:
                corps.pack_start(self._lien_retrait(t("carte.demarrer"), pn.demarrer, "demarrer"), False, False, 0)
            elif sante:
                corps.pack_start(self._lien_retrait(t("carte.rouvrir"), lambda: pn.connecter(actif), "reconnexion",
                                                    "pa-alerte"), False, False, 0)
            corps.pack_start(self._note(t(p.note_actif(actif))), False, False, 0)
            for note in p.notes_voisin(m, regle, pn.voisin_marche):
                corps.pack_start(self._note(note), False, False, 0)
        autres = p.utilisables(m)
        if autres:
            corps.pack_start(_filet(), False, False, 0)
            liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for cle in autres:
                liste.pack_start(self._rang_autre(cle), False, False, 0)
            corps.pack_start(self._defilante(liste, p.AUTRES_MAX), False, False, 0)
        self._notes_bascule(corps)
        self.boite.pack_start(corps, False, False, 0)

    def _lien_retrait(self, texte, rappel, cle, *classes):
        lien = _lien(texte, rappel, *classes, cle=cle)
        lien.set_margin_start(p.COLONNE)
        return lien

    def _notes_bascule(self, boite):
        pn = self.panneau
        if pn.bascule_echec:
            boite.pack_start(self._note(t("carte.note_bascule_echec"), "pa-echec"), False, False, 0)
        for cle in pn.notes:
            boite.pack_start(self._note(t(cle)), False, False, 0)

    def _abonnement(self, corps, etat, m, maintenant, sante):
        """Claude actif, ou rien de lu encore (sans mesure, aucun nom n'est présumé)."""
        pn = self.panneau
        if m is not None:
            e = None if u.probleme(pn.statut)[0] else sante
            corps.pack_start(self._entete_actif("claude", u.OFFRES.get(m.offre, ""), e), False, False, 0)
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_JAUGES)
        section.set_margin_start(p.COLONNE)
        allume = etat != "arrete"
        if not allume:
            section.pack_start(_lien(t("carte.demarrer"), pn.demarrer, cle="demarrer"), False, False, 0)
        elif etat == "attente":
            section.pack_start(_lab(t("carte.attente"), "pa-texte"), False, False, 0)
        else:
            texte, geste = u.probleme(pn.statut)
            if texte and geste:
                section.pack_start(_lien(texte, lambda: pn.connecter("claude"), "pa-alerte", cle="reconnexion"),
                                   False, False, 0)
            elif texte:
                section.pack_start(_lab(texte, "pa-note", enrouler=True), False, False, 0)
            elif sante:
                section.pack_start(_lien(t("carte.rouvrir"), lambda: pn.connecter("claude"), "pa-alerte",
                                         cle="reconnexion"), False, False, 0)
        age = None if pn.heure is None else maintenant - pn.heure
        fenetres = u.principales(m) if m is not None and (age is None or age <= u.TROP_VIEUX_S) else []
        if not fenetres and etat != "attente" and (not allume or pn.statut in ("ok", None)):
            # Sans mesure gardée (ou plus vieille que 7 jours) : « Usage inconnu ».
            section.pack_start(_lab(t("carte.inconnu") if not allume else u.probleme("erreur")[0], "pa-note"),
                               False, False, 0)
        grille = Gtk.Grid(column_spacing=0, row_spacing=p.ECART_JAUGES)
        for i, f in enumerate(fenetres):
            pc = u.pourcent_affiche(f, maintenant)
            gardee = not allume
            titre = _lab(t(u.FENETRES[f.nom]), "pa-jauge-titre", largeur=p.TITRE_JAUGE)
            jauge = Jauge(pc, p.couleur_jauge(pc, allume),
                          [u.pourcent_affiche(trait, maintenant) for trait in u.traits(m, f.nom)])
            jauge.set_margin_end(p.AVANT_DUREE)
            duree = _lab(u.duree(f.remise - maintenant) if f.remise > maintenant else "",
                         "pa-duree", xalign=1.0, largeur=p.largeur_duree())
            if gardee:
                titre.get_style_context().add_class("pa-gardee")
                duree.get_style_context().add_class("pa-gardee")
            bulle = u.infobulle_fenetre(f, maintenant, age, allume)
            for trait in u.traits(m, f.nom):
                bulle += "\n" + u.infobulle_fenetre(trait, maintenant)
            ligne = Gtk.EventBox()
            rangee = Gtk.Box()
            rangee.pack_start(titre, False, False, 0)
            rangee.pack_start(jauge, True, True, 0)
            rangee.pack_start(duree, False, False, 0)
            ligne.add(rangee)
            ligne.set_tooltip_text(bulle)
            grille.attach(ligne, 0, i, 1, 1)
            ligne.set_hexpand(True)
        if fenetres:
            section.pack_start(grille, False, False, 0)
        corps.pack_start(section, False, False, 0)

    def _fournisseurs(self, etat):
        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_CORPS)
        if etat == "vide":
            corps.pack_start(_lab(t("carte.intro_vide"), "pa-texte", enrouler=True), False, False, 0)
        liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for rang, (cle_titre, ids) in enumerate(p.SECTIONS):
            titre = _lab(t(cle_titre), "pa-section")
            titre.set_size_request(-1, p.SECTION)
            titre.set_margin_start(p.COLONNE)
            if rang:
                titre.set_margin_top(p.ECART_CORPS)
            liste.pack_start(titre, False, False, 0)
            for cle in ids:
                liste.pack_start(self._rang(cle), False, False, 0)
        # Toujours là, sys-agent éteint compris : l'autorisation se retire sans lui.
        liste.pack_start(self._lien_voisin(), False, False, 0)
        corps.pack_start(self._defilante(liste, p.LISTE_MAX), False, False, 0)
        self._notes_bascule(corps)
        corps.pack_start(self._note(t("carte.note_saisie")), False, False, 0)
        self.boite.pack_start(corps, False, False, 0)

    def _connexion(self):
        pn = self.panneau
        cle = pn.connexion[0]
        ecran = p.ecran_connexion(pn.derniere)
        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_CORPS)
        if ecran == "attente":
            etapes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for i, texte in enumerate(p.etapes_connexion(cle)):
                ligne = Gtk.Box()
                ligne.set_size_request(-1, 24)
                numero = _lab(str(i + 1), "pa-num")
                numero.set_size_request(p.COLONNE, -1)
                numero.set_margin_start(p.RETRAIT_COCHE)
                ligne.pack_start(numero, False, False, 0)
                ligne.pack_start(_lab(t(texte), "pa-texte"), False, False, 0)
                etapes.pack_start(ligne, False, False, 0)
            corps.pack_start(etapes, False, False, 0)
            corps.pack_start(self._note(t("carte.note_cnx")), False, False, 0)
        elif ecran == "echec":
            corps.pack_start(self._note(t("carte.echec"), "pa-echec", "pa-fort"), False, False, 0)
            corps.pack_start(self._note(t("carte.note_echec")), False, False, 0)
        else:
            self._pret(corps, cle)
        corps.pack_start(self._lien_retrait(t("carte.rouvrir"), lambda: pn.connecter(cle), "rouvrir"), False, False, 0)
        if cle == "local":
            corps.pack_start(self._lien_voisin(), False, False, 0)
        self.boite.pack_start(corps, False, False, 0)

    def _lien_voisin(self):
        return self._lien_retrait(t("carte.lien_voisin"), self.panneau.ouvrir_voisin, "voisin")

    def _voisin(self):
        """Direction A-liste : une liste, un champ, un bouton en contour (jamais plein, jamais
        par défaut). Ouvrir un port à sys-agent n'est pas le geste que la carte met en avant."""
        e = self.panneau.voisin
        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_CORPS)
        if e.regle is not None:
            self._voisin_autorise(corps, e)
        else:
            self._voisin_choix(corps, e)
        if e.message:
            corps.pack_start(self._note(e.message, "pa-echec"), False, False, 0)
        self.boite.pack_start(corps, False, False, 0)

    def _voisin_autorise(self, corps, e):
        q = e.qube(e.regle.qube)
        ligne = Gtk.Box()
        ligne.set_size_request(-1, p.LIGNE)
        ligne.pack_start(_gouttiere(_coche()), False, False, 0)
        ligne.pack_start(_carre(q.couleur if q else None), False, False, 0)
        ligne.pack_start(_lab(t("carte.autorise", qube=e.regle.qube, port=e.regle.port), "pa-rnom", "pa-actif"),
                         False, False, 0)
        if q is not None:
            marche = self.panneau.voisin_marche  # relu à chaque sondage ; la liste date de l'ouverture
            etat = _lab(t("carte.en_marche" if (q.en_marche if marche is None else marche) else "carte.eteint"),
                        "pa-statut", xalign=1.0)
            etat.set_margin_end(4)
            ligne.pack_end(etat, False, False, 0)
        corps.pack_start(ligne, False, False, 0)
        retirer = self._lien_retrait(t("carte.retirer"), self.panneau.voisin_retirer, "retirer")
        retirer.set_sensitive(not e.en_cours)
        corps.pack_start(retirer, False, False, 0)
        corps.pack_start(self._note(t("carte.note_voisin")), False, False, 0)
        corps.pack_start(self._note(t("carte.voisin_une_seule")), False, False, 0)

    def _voisin_choix(self, corps, e):
        pn = self.panneau
        titre = _lab(t("carte.sec_voisin"), "pa-section")
        titre.set_size_request(-1, p.SECTION)
        titre.set_margin_start(p.COLONNE)
        corps.pack_start(titre, False, False, 0)
        if e.qubes is None:
            corps.pack_start(self._note(t("carte.voisin_lecture")), False, False, 0)
        elif e.qubes is False:
            corps.pack_start(self._note(t("carte.voisin_illisible"), "pa-echec"), False, False, 0)
        elif not e.qubes:
            corps.pack_start(self._note(t("carte.voisin_aucun")), False, False, 0)
        else:
            liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for q in e.qubes:
                liste.pack_start(self._rang_qube(q, q.nom == e.choix), False, False, 0)
            corps.pack_start(self._defilante(liste, p.VOISINS_MAX), False, False, 0)
        ligne = Gtk.Box()
        ligne.pack_start(_gouttiere(), False, False, 0)
        ligne.pack_start(_lab(t("carte.port"), "pa-rnom"), False, False, 0)
        champ = self._champ_port(e)
        champ.set_margin_start(12)
        ligne.pack_start(champ, False, False, 0)
        corps.pack_start(ligne, False, False, 0)
        if e.clavier.ok:
            corps.pack_start(self._note(t("carte.voisin_port_aide")), False, False, 0)
        else:
            corps.pack_start(self._note(t("pal.saisie_impossible", raison=e.clavier.raison), "pa-echec"), False, False, 0)
        corps.pack_start(self._note(t("carte.note_voisin"), "pa-texte"), False, False, 0)
        self.b_autoriser = _bouton("pa-contour", _lab(t("carte.autoriser"), xalign=0.5), pn.voisin_autoriser, None,
                                   "autoriser")
        self.b_autoriser.set_halign(Gtk.Align.START)
        self.b_autoriser.set_margin_start(p.COLONNE)
        self.b_autoriser.set_sensitive(e.autorisable())
        corps.pack_start(self.b_autoriser, False, False, 0)

    def _rang_qube(self, q, choisi):
        interieur = Gtk.Box()
        interieur.set_size_request(-1, p.LIGNE)
        interieur.pack_start(_gouttiere(_coche() if choisi else None), False, False, 0)
        interieur.pack_start(_carre(q.couleur), False, False, 0)
        nom = _lab(q.nom, "pa-rnom")  # texte brut : un nom de qube n'est jamais du balisage
        if choisi:
            nom.get_style_context().add_class("pa-actif")
        interieur.pack_start(nom, False, False, 0)
        etat = _lab(t("carte.en_marche" if q.en_marche else "carte.eteint"), "pa-statut", xalign=1.0)
        etat.set_margin_end(14)  # la barre de défilement passe par-dessus la marge, pas par-dessus l'état
        interieur.pack_end(etat, False, False, 0)
        return _bouton("pa-rang", interieur, lambda n=q.nom: self.panneau.voisin_choisir(n), None, f"v-{q.nom}")

    def _champ_port(self, e):
        """P2, comme la palette : ni collage, ni clic du milieu, ni glisser-déposer. Les touches
        sont filtrées par _clavier. Vide au départ : un port présent a été tapé ici."""
        champ = Gtk.Entry()
        champ.get_style_context().add_class("pa-port")
        champ.set_max_length(5)
        champ.set_width_chars(7)
        champ.set_placeholder_text(p.PORT_EXEMPLE)
        champ.set_input_purpose(Gtk.InputPurpose.DIGITS)
        champ.set_text(e.port)
        champ.set_sensitive(e.clavier.ok and not e.en_cours)
        champ.set_tooltip_text(t("carte.voisin_port_aide"))
        champ.connect("paste-clipboard", lambda w: w.stop_emission_by_name("paste-clipboard"))
        champ.connect("button-press-event", lambda _w, ev: ev.button != 1)
        champ.drag_dest_unset()
        champ.connect("changed", self._port_change)
        champ.qaf_cle = "port"
        return champ

    def _port_change(self, champ):
        e = self.panneau.voisin
        if e is None:
            return
        if not champ.get_text().isascii() or not champ.get_text().isdigit() and champ.get_text():
            champ.set_text("")  # filet : rien d'autre que des chiffres ne reste jamais ici
            return
        e.port = champ.get_text()
        self.b_autoriser.set_sensitive(e.autorisable())

    def _pret(self, corps, cle):
        """Test réussi : le consentement de la première activation. La phrase dit qui verra tout
        ce que lit l'agent ; seul « Utiliser » bascule, l'assistant n'active rien."""
        pn, m = self.panneau, self.panneau.derniere
        corps.pack_start(self._note(p.titre_pret(cle), "pa-fort"), False, False, 0)
        if u.actif_de(m) == cle:
            return
        if not p.activable(m, cle):
            corps.pack_start(self._note(t("carte.pret_seule")), False, False, 0)
            return
        corps.pack_start(self._note(p.phrase_pret(cle), "pa-texte"), False, False, 0)
        b = _bouton("pa-principal", _lab(t("carte.utiliser"), xalign=0.5), lambda: pn.basculer(cle), None, "utiliser")
        b.set_halign(Gtk.Align.START)
        b.set_margin_start(p.COLONNE)
        b.set_sensitive(pn.allume)
        corps.pack_start(b, False, False, 0)


# --- le panneau (icône, mesure, gestes) ---------------------------------------------------

class Panneau:
    bascule = None            # id en cours de bascule : la coche ne bouge qu'à la réponse
    bascule_echec = False
    heure_bascule = 0.0       # une mesure partie avant cette heure ne dit pas le nouvel actif
    notes = ()                # clés de texte après une bascule, jusqu'à la fermeture de la carte
    voisin = None             # p.EcranVoisin tant que cet écran est ouvert
    voisin_marche = None      # état réel du qube autorisé, lu dans dom0 (None : pas de règle)
    agents_avant = ()         # ids activables à l'ouverture du terminal de connexion

    def __init__(self, publier=True):
        self.allume = False
        self.derniere, self.heure = u.cache_lire()
        self.statut = None
        self.dernier_appel = None
        self.reessai = None
        self.en_cours = False
        self.forcer = False
        self.ajout = False
        self.connexion = None  # (fournisseur, heure de lancement)
        self.carte = None
        self.position = None
        self.label, self.label_lu = None, 0.0
        self.images = {}
        self._poser_label(p.LABEL_DEFAUT)
        self.element = sni.Element(p.IDENT, t("carte.titre"), categorie="ApplicationStatus")
        self._apparence()
        self.element.on_activer = lambda x, y: self.basculer_carte(x, y)
        self.element.on_menu = lambda _x, _y: self.console()
        self.element.on_secondaire = lambda _x, _y: self.rafraichir(force=True)
        self.publication = sni.publier(self.element) if publier else None
        GLib.timeout_add_seconds(p.SONDAGE_ETAT_S, self._tic)
        self._tic()

    # --- état et mesure (fil de fond) ---------------------------------------------------
    def etat_carte(self):
        return p.etat_carte(self.allume, self.derniere, self.statut, self.ajout, self.connexion,
                            self.voisin is not None)

    def regle(self):
        return v.lire_regle()

    def _poser_label(self, label):
        if label != self.label:
            self.label = label
            self.images = {k: images_tete(k, label) for k in ("pret", "alerte", "arrete")}
            return True
        return False

    def _apparence(self):
        cle, bulle = p.apparence_icone(self.allume, self.derniere, time.time())
        self.element.changer(images=self.images[cle], infobulle=bulle,
                             statut="NeedsAttention" if cle == "alerte" else "Active",
                             attention_images=self.images["alerte"] if cle == "alerte" else [])

    def _tic(self):
        attente = bool(self.connexion) and p.ecran_connexion(self.derniere) == "attente"
        if attente and time.time() - self.connexion[1] > p.CONNEXION_MAX_S:
            self.connexion = None
            self._redessiner()
        self.rafraichir(force=attente)
        return True

    def rafraichir(self, force=False):
        if self.en_cours:
            self.forcer = self.forcer or force
            return
        self.en_cours = True
        ouverte = self.carte is not None
        threading.Thread(target=self._travail, args=(force, ouverte), daemon=True).start()

    def _travail(self, force, ouverte):
        try:
            allume = subprocess.run(p.ARGV_ETAT, stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            allume = False
        maintenant = time.time()
        label = None
        if maintenant - self.label_lu >= LABEL_RELU_S:  # le label change rarement : relu chaque minute
            self.label_lu, label = maintenant, lire_label()
        regle = self.regle()
        self.voisin_marche = v.marche(regle.qube) if regle is not None else None
        mesure, code, appel = None, None, False
        liante = u.fenetre_liante(self.derniere, maintenant)
        if allume and (not self.allume or u.doit_mesurer(
                self.dernier_appel, maintenant, force, ouverte,
                None if liante is None else u.pourcent_affiche(liante, maintenant),
                self.statut, self.reessai)):
            appel = True
            code, octets = u.appeler()
            mesure = u.lire_mesure(octets) if code == 0 else None
        GLib.idle_add(self._resultat, allume, appel, mesure, code, maintenant, label)

    def _resultat(self, allume, appel, mesure, code, maintenant, label=None):
        self.en_cours = False
        if label is not None:
            self._poser_label(label)
        if appel:
            self.dernier_appel = maintenant
            if code not in (0, None) and allume:
                allume = False  # --no-autostart : sys-agent s'est éteint entre-temps
        self.allume = allume
        if mesure is not None:
            mesure = self._sans_passe(mesure, maintenant)
            self.statut = mesure.etat
            self.reessai = u.REESSAI_LIMITE_S if mesure.etat == "limite" else None
            if mesure.etat == "ok":
                self.derniere, self.heure = mesure, int(maintenant)
                u.cache_ecrire(mesure, maintenant)
            elif self.derniere is not None:
                self.derniere = dataclasses.replace(
                    self.derniere, totaux=mesure.totaux or self.derniere.totaux,
                    fournisseurs=mesure.fournisseurs or self.derniere.fournisseurs,
                    v=mesure.v, actif=mesure.actif, sante=mesure.sante, connexion=mesure.connexion,
                    agents=mesure.agents)
            else:
                self.derniere = dataclasses.replace(mesure, fenetres=())
            if self.connexion and (p.connexion_finie_v1(self.derniere, self.connexion[0], mesure.etat)
                                   or p.connexion_retiree(self.derniere, self.connexion[0], self.agents_avant)):
                self.connexion = None
        elif appel and allume:
            self.statut = "erreur"
        self._apparence()
        self._redessiner()
        if self.forcer:
            self.forcer = False
            self.rafraichir(force=True)
        return False

    def _sans_passe(self, mesure, maintenant):
        """Une mesure partie avant un geste ne le défait pas : ni l'ancien actif après une bascule,
        ni l'ancien résultat d'assistant juste après l'ouverture du terminal."""
        changes = {}
        if self.connexion and maintenant <= self.connexion[1] + p.CONNEXION_MARGE_S:
            changes["connexion"] = ""
        if maintenant <= self.heure_bascule and self.derniere is not None:
            changes["actif"] = self.derniere.actif
        return dataclasses.replace(mesure, **changes) if changes else mesure

    def empreinte(self):
        """Ce que la carte montre. Les durées changent à la minute, seulement sur l'écran des
        jauges : sur la liste, rien ne reconstruit sans raison."""
        etat = self.etat_carte()
        if etat == "voisin":  # ni la mesure ni les 30 s des durées : le champ de port y survit
            return etat, self.voisin.vue(), self.voisin_marche
        minute = int(time.time() // 60) if etat in ("mesure", "arrete") else 0
        return (etat, self.allume, self.statut, self.derniere, self.connexion, self.bascule, self.bascule_echec,
                self.notes, minute, self.voisin_marche)

    def _redessiner(self):
        if self.carte is not None:
            self.carte.remplir()

    # --- la carte ------------------------------------------------------------------
    def basculer_carte(self, x, y, saisir=True):
        if self.carte is not None:
            self.fermer_carte()
            return
        self.ouvrir_carte(x, y, saisir)

    def ouvrir_carte(self, x, y, saisir=True):
        self.ajout, self.bascule_echec, self.notes, self.voisin = False, False, (), None
        self.position = (x, y)
        self.carte = Carte(self, saisir)
        self.carte.remplir()
        self.carte.show_all()
        self.replacer()
        if saisir:
            GLib.timeout_add(50, lambda: self.carte is not None and self.carte.saisir() and False)
        self.rafraichir()  # mesure à l'ouverture si la dernière a plus de 15 s

    def replacer(self):
        if self.carte is None or self.position is None:
            return
        x, y = self.position
        largeur, hauteur = p.LARGEUR, self.carte.get_preferred_height()[1]
        affichage = Gdk.Display.get_default()
        moniteur = affichage.get_monitor_at_point(x, y) or affichage.get_primary_monitor()
        z = moniteur.get_workarea()
        self.carte.move(*p.placement(x, y, largeur, hauteur, (z.x, z.y, z.width, z.height)))

    def fermer_carte(self):
        if self.carte is not None:
            carte, self.carte = self.carte, None
            carte.relacher()
            carte.destroy()
        self.ajout, self.voisin = False, None

    # --- gestes ------------------------------------------------------------------
    def _lancer(self, argv):
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            from qaf_ui.notif import notifier
            notifier(t("carte.titre"), t("carte.lancement_impossible", commande=argv[0], erreur=e), "dialog-error")
            return False
        threading.Thread(target=proc.wait, daemon=True).start()
        return True

    def ajouter(self):
        self.ajout = True
        self._redessiner()

    def retour(self):
        if self.voisin is not None:  # retour à l'écran d'où l'on vient
            self.voisin = None
        else:
            self.ajout, self.connexion = False, None
        self._redessiner()

    # --- qube voisin : gestes de dom0, à argv fixe, hors du fil GTK ------------------------
    def ouvrir_voisin(self):
        e = self.voisin = p.EcranVoisin(confirmation.clavier_systeme(), self.regle())
        self._redessiner()

        def lire():
            GLib.idle_add(self._voisin_liste, e, v.lister())
        threading.Thread(target=lire, daemon=True).start()

    def _voisin_liste(self, e, qubes):
        e.qubes = False if qubes is None else tuple(qubes)
        if self.voisin is e:
            self._redessiner()
        return False

    def voisin_choisir(self, nom):
        e = self.voisin
        if e is not None and not e.en_cours and e.qube(nom) is not None:
            e.choix, e.message = nom, ""
            self._redessiner()

    def _voisin_geste(self, e, action, *args, echec):
        e.en_cours, e.message = True, ""
        self._redessiner()

        def travail():
            GLib.idle_add(self._voisin_fin, e, v.lancer(action, *args), echec)
        threading.Thread(target=travail, daemon=True).start()

    def _voisin_fin(self, e, reussi, echec):
        e.en_cours, e.regle, e.port = False, self.regle(), ""
        e.message = "" if reussi else t(echec)
        if self.voisin is e:
            self._redessiner()
        self.rafraichir()
        return False

    def voisin_autoriser(self):
        e = self.voisin
        if e is None or not e.autorisable():
            return
        refus = palette.envoi_admis(e.clavier, confirmation.clavier_systeme)  # périphériques relus, comme la palette
        if refus:
            e.message = refus
            self._redessiner()
            return
        self._voisin_geste(e, "autoriser", e.choix, e.port, echec="carte.voisin_refus")

    def voisin_retirer(self):
        e = self.voisin
        if e is not None and not e.en_cours and e.regle is not None:
            self._voisin_geste(e, "retirer", echec="carte.voisin_retrait_echec")

    def connecter(self, fournisseur):
        if not self.allume or not p.connectable(self.derniere, fournisseur):
            return
        if self._lancer(p.argv_connexion(fournisseur)):
            if self.derniere is not None:  # l'ancien résultat, effacé là-bas, ne s'affiche pas ici
                self.derniere = dataclasses.replace(self.derniere, connexion="")
                self.agents_avant = self.derniere.agents
            self.ajout, self.connexion = False, (fournisseur, time.time())
            self._redessiner()

    def choisir(self, cle):
        geste = p.geste_ligne(self.derniere, cle, self.allume)
        if geste == "basculer":
            self.basculer(cle)
        elif geste == "terminal":
            self.connecter(cle)

    def basculer(self, cle):
        if self.bascule is not None or not self.allume or not p.activable(self.derniere, cle):
            return
        self.bascule, self.bascule_echec, self.notes, self.connexion = cle, False, (), None
        self._redessiner()
        threading.Thread(target=self._bascule_fil, args=(cle,), daemon=True).start()

    def _bascule_fil(self, cle):
        reussi = u.basculer(cle)
        GLib.idle_add(self._bascule_fin, cle, reussi, reussi and self._console_ouverte())

    def _bascule_fin(self, cle, reussi, console):
        self.bascule = None
        if reussi and self.derniere is not None:
            self.derniere = dataclasses.replace(self.derniere, actif=cle, sante="inconnue")
            self.heure_bascule = time.time()
            self.notes = p.notes_bascule(cle, console)
            self._apparence()
        else:
            self.bascule_echec = True
        self._redessiner()
        self.rafraichir(force=True)
        return False

    def _console_ouverte(self):
        """La fenêtre « Agent » existe-t-elle ? Sans réponse de X, on suppose que oui : la note
        en trop ne coûte rien, celle qui manque ferait croire à la bascule d'une console."""
        try:
            from qaf_ui import x11
            aff = x11.Affichage()
        except OSError:
            return True
        try:
            return p.fenetre_agent(aff) is not None
        finally:
            aff.fermer()

    def console(self):
        self.fermer_carte()
        if self.allume:
            try:
                from qaf_ui import x11
                aff = x11.Affichage()
            except OSError:
                aff = None
            if aff is not None:
                try:
                    xid = p.fenetre_agent(aff)
                    if xid is not None:
                        p.amener_devant(aff, xid)
                        return
                finally:
                    aff.fermer()
        if self._lancer(p.ARGV_CONSOLE) and not self.allume:
            from qaf_ui.notif import notifier
            notifier(t("carte.titre"), t("carte.demarrage"), "dialog-information")

    def demarrer(self):
        self.fermer_carte()
        self._lancer(u.ARGV_DEMARRER)



# --- lancement --------------------------------------------------------------------------

def _instance_unique():
    """Un seul panneau par session : verrou (et pid, pour --ouvrir/--fermer)."""
    chemin = p.fichier_verrou()
    fd = os.open(chemin, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def _pointeur():
    siege = Gdk.Display.get_default().get_default_seat()
    _ecran, x, y = siege.get_pointer().get_position()
    return x, y


def lancer():
    verrou = _instance_unique()
    if verrou is None:
        print(t("carte.deja_lance"), file=sys.stderr)
        return 0
    c.installer_style()
    installer_css()
    panneau = Panneau()

    # Commandes à soi (qaf-agent-panneau --ouvrir | --fermer) : carte ouverte près du
    # pointeur SANS prendre clavier ni souris (pour une capture pendant que l'utilisateur
    # travaille), puis refermée.
    def ouvrir(*_):
        if panneau.carte is None:
            panneau.ouvrir_carte(*_pointeur(), saisir=False)
        return True

    def fermer(*_):
        panneau.fermer_carte()
        return True

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, ouvrir)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, fermer)
    Gtk.main()
    return 0


# --- scènes de rendu hors écran (tools/rendu-ui.sh, jamais installé autrement) ------------------

def _mesure_scene(etat="mesure", actif="claude", fournisseurs=None, agents=None, sante="ok", connexion="", v=2):
    maintenant = int(time.time())
    fenetres = (u.Fenetre("5h", "", 93 if etat == "limite" else 34, maintenant + 2 * 3600 + 14 * 60),
                u.Fenetre("semaine", "", 61, maintenant + 3 * 86400 + 7 * 3600),
                u.Fenetre("semaine", "fable", 20, maintenant + 3 * 86400 + 7 * 3600))
    if fournisseurs is None:
        fournisseurs = (("codex", "connecte"), ("grok", "non_installe"), ("grokbot", "connexion_seule"),
                        ("agy", "connecte"), ("kimi", "non_installe"), ("zai", "deconnecte"),
                        ("openrouter", "connecte"), ("local", "connecte"))
    if agents is None:
        agents = ("claude", "agy", "openrouter", "local")
    return u.Mesure("sans_jeton" if etat == "vide" else "ok", "max_20x", () if etat == "vide" else fenetres,
                    u.Totaux(812000, 4200000, 23, 3, "opus", 1, False), fournisseurs, v, actif, sante, connexion,
                    agents)


def _scene(etat, allume=True, statut=None, connexion=None, connexion_resultat="", **mesure):
    maintenant = int(time.time())
    m = mesure.pop("m", None) or _mesure_scene(etat, connexion=connexion_resultat, **mesure)
    pn = Panneau.__new__(Panneau)
    pn.allume, pn.derniere, pn.heure = allume, m, maintenant - (3 * 3600 if not allume else 60)
    pn.statut = statut or ("sans_jeton" if etat == "vide" else "ok")
    pn.ajout, pn.connexion = etat == "ajout", (connexion, maintenant) if connexion else None
    pn.carte, pn.position, pn.en_cours, pn.forcer = None, (900, 860), False, False
    pn.rafraichir = pn._apparence = lambda force=False: None
    carte = Carte(pn, saisir=False)
    pn.carte = carte
    carte.remplir()
    return carte


def _avec(carte, **attributs):
    """Pose un état de la bascule, puis reconstruit."""
    for nom, valeur in attributs.items():
        setattr(carte.panneau, nom, valeur)
    carte.remplir()
    return carte


def _defiler(carte, position):
    """La liste descendue, une fois allouée (rendu seulement)."""
    GLib.timeout_add(60, lambda: carte.defil.get_vadjustment().set_value(position) and False)
    return carte


def _survoler(carte, cle):
    carte._chercher(cle).set_state_flags(Gtk.StateFlags.PRELIGHT, False)
    return carte


def scene_mesure():
    return _scene("mesure")


def scene_limite():
    return _scene("limite")


def scene_arrete():
    return _scene("arrete", allume=False)


def scene_vide():
    return _scene("vide")


def scene_ajout():
    return _scene("ajout")


def scene_connexion():
    return _scene("mesure", connexion="claude")


def scene_plus_vierge():
    return _scene("ajout", fournisseurs=(("grok", "non_installe"), ("grokbot", "connexion_seule")), agents=("claude",))


def scene_plus_configure():
    return _scene("ajout")


def scene_plus_bas():
    return _defiler(_scene("ajout"), 1000)


def scene_plus_survol():
    return _survoler(_scene("ajout"), "f-codex")


def scene_plus_survol_terminal():
    return _survoler(_scene("ajout"), "t-agy")


def scene_plus_tiers_actif():
    return _defiler(_scene("ajout", actif="openrouter"), 1000)


def scene_plus_arrete():
    return _scene("ajout", allume=False)


def scene_plus_v1():
    return _scene("ajout", m=dataclasses.replace(_mesure_scene(), v=1, actif="claude", agents=(),
                                                 fournisseurs=(("codex", "connecte"), ("grok", "non_installe"))))


def scene_carte_claude():
    return _scene("mesure")


def scene_carte_openrouter():
    return _scene("mesure", actif="openrouter")


def scene_carte_local():
    return _scene("mesure", actif="local")


def scene_carte_google():
    return _scene("mesure", actif="agy")


def scene_carte_tiers_arrete():
    return _scene("arrete", allume=False, actif="openrouter")


def scene_carte_sans_compte_claude():
    return _scene("vide", actif="openrouter")


def scene_carte_nombreux():
    tous = tuple((cle, "connecte") for cle in u.FOURNISSEURS if cle != "grokbot")
    return _scene("mesure", fournisseurs=tous, agents=tuple(k for k in u.IDS if k != "grokbot"))


def scene_sante_cle():
    return _scene("mesure", actif="openrouter", sante="cle_refusee")


def scene_sante_credit():
    return _scene("mesure", actif="openrouter", sante="credit_epuise")


def scene_sante_injoignable():
    return _scene("mesure", actif="local", sante="injoignable")


def scene_bascule_en_cours():
    return _avec(_scene("mesure"), bascule="openrouter")


def scene_bascule_faite():
    return _avec(_scene("mesure", actif="agy"), notes=p.notes_bascule("agy", True))


def scene_bascule_echec():
    return _avec(_scene("mesure"), bascule_echec=True)


def scene_connexion_cle():
    return _scene("mesure", connexion="openrouter")


def scene_connexion_abonnement():
    return _scene("mesure", connexion="codex")


def scene_connexion_local():
    return _scene("mesure", connexion="local")


def scene_connexion_echec():
    return _scene("mesure", connexion="openrouter", connexion_resultat="echec")


def scene_pret_tiers():
    return _scene("mesure", connexion="openrouter", connexion_resultat="ok")


def scene_pret_local():
    return _scene("mesure", connexion="local", connexion_resultat="ok")


def scene_pret_seule():
    return _scene("mesure", connexion="grokbot", connexion_resultat="ok")


class _ClavierScene:
    ok, raison = True, ""


_QUBES_SCENE = (v.Qube("gpu", "StandaloneVM", "0xcc0000", False), v.Qube("ia-locale", "AppVM", "0x3465a4", True),
                v.Qube("personal", "AppVM", "0xf5c211", False), v.Qube("work", "AppVM", "0x4e9a06", True))


def _scene_voisin(regle=None, choix=None, port="", qubes=_QUBES_SCENE, clavier=_ClavierScene, message=""):
    carte = _scene("mesure", connexion="local")
    e = p.EcranVoisin(clavier, regle)
    e.qubes, e.choix, e.port, e.message = qubes, choix, port, message
    carte.panneau.voisin = e
    carte.remplir()
    return carte


def scene_voisin_choix():
    return _scene_voisin(choix="ia-locale", port="11434")


def scene_voisin_vide():
    return _scene_voisin()


def scene_voisin_autorise():
    return _scene_voisin(regle=v.Regle("ia-locale", 11434))


def scene_voisin_aucun():
    return _scene_voisin(qubes=())


def scene_voisin_refus():
    return _scene_voisin(choix="work", port="8080", message=t("carte.voisin_refus"))


def scene_carte_local_voisin():
    carte = _scene("mesure", actif="local", sante="inconnue")
    carte.panneau.regle = lambda: v.Regle("ia-locale", 11434)
    carte.panneau.voisin_marche = False
    carte._vue = None
    carte.remplir()
    return carte
