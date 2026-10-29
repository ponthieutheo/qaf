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

from qaf_ui import sni  # noqa: E402
from qaf_ui import zone  # noqa: E402
from qaf_ui import usage as u  # noqa: E402
from qaf_ui.ecrans import agent_panneau as p  # noqa: E402
from qaf_ui.nuit import composants as c  # noqa: E402
from qaf_ui.nuit import contraste, icones, jetons as j  # noqa: E402
from qaf_ui import couleurs_plasma  # noqa: E402
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
.qaf-panneau-agent button.pa-grand {{ padding: 16px 8px 12px 8px;
    background-color: {k.CHAMP}; border: none; border-radius: {j.RAYON}px; }}
.qaf-panneau-agent button.pa-carre:hover, .qaf-panneau-agent button.pa-carre:focus {{
    border-color: {j.SIGNATURE}; }}
.qaf-panneau-agent button.pa-grand:hover, .qaf-panneau-agent button.pa-grand:focus {{
    background-color: {k.CHOISI}; }}
.qaf-panneau-agent button.pa-grand:disabled {{ background-color: {k.CHAMP}; }}
.qaf-panneau-agent button.pa-grand label {{ font-size: {j.TAILLE_CORPS}px; font-weight: {j.GRAISSE_ACCENT}; color: {k.T1}; }}
.qaf-panneau-agent button.pa-grand label.pa-note {{ font-size: {j.TAILLE_PETITE}px; font-weight: {j.GRAISSE}; color: {k.T3}; }}
.qaf-panneau-agent button.pa-grand:disabled label {{ color: {k.T3}; }}
.qaf-panneau-agent button.pa-lien {{ padding: 0; min-height: 0; background-color: transparent; border: none; }}
.qaf-panneau-agent button.pa-lien label {{ font-size: {j.TAILLE_CORPS}px; color: {k.T2};
    text-decoration-line: underline; }}
.qaf-panneau-agent button.pa-lien.pa-alerte label {{ color: {k.ATTENTION}; }}
.qaf-panneau-agent button.pa-lien:hover label, .qaf-panneau-agent button.pa-lien:focus label {{ color: {k.T1}; }}
.qaf-panneau-agent button.pa-grand:focus {{ border-color: {k.FILET_FORT}; }}
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


def _bouton(classe, contenu, rappel, infobulle=None, cle=None):
    b = Gtk.Button()
    b.get_style_context().add_class(classe)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.add(contenu)
    b.set_can_focus(True)
    if infobulle:
        b.set_tooltip_text(infobulle)
    b.connect("clicked", lambda *_: rappel())
    b.connect("enter-notify-event", lambda w, _e: w.grab_focus() or False)
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
        self._minuteries = [GLib.timeout_add(p.ROTATION_MS, self._tourner),
                            GLib.timeout_add_seconds(p.MINUTERIE_DUREES_S, self._a_jour)]
        self.connect("key-press-event", self._clavier)
        self.connect("destroy", self._fin)

    def _fin(self, *_):
        for m in self._minuteries:
            GLib.source_remove(m)
        self._minuteries = []

    def _a_jour(self):
        self.remplir()
        return True

    # Clavier : flèches et hjkl promènent le focus (le « curseur » unique, que la
    # souris déplace aussi) ; Entrée agit, ou rafraîchit si rien n'a le focus ; r
    # rafraîchit ; Échap ferme (SurfaceAF).
    def _clavier(self, _w, ev):
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
        focus = getattr(self.get_focus(), "qaf_cle", None)
        for enfant in self.boite.get_children():
            self.boite.remove(enfant)
            enfant.destroy()
        pn = self.panneau
        maintenant = time.time()
        etat = pn.etat_carte()
        m = pn.derniere
        self.boite.pack_start(self._heros(etat, m), False, False, 0)
        self.boite.pack_start(_filet(), False, False, 0)
        if etat == "connexion":
            self._connexion()
        elif etat in ("vide", "ajout"):
            self._fournisseurs(etat)
        else:
            self._abonnement(etat, m, maintenant)
            for cle, nom, _ in p.FOURNISSEURS[1:]:
                if p.etat_fournisseur(m, cle) == "connecte":
                    self.boite.pack_start(_filet(), False, False, 0)
                    self.boite.pack_start(self._autre(nom), False, False, 0)
        self.boite.show_all()
        cible = self._chercher(focus) if focus else None
        if cible is not None:
            cible.grab_focus()
        elif self.get_focus() is not None and not self.get_focus().get_mapped():
            self.set_focus(None)
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
        boite = Gtk.Box(spacing=14)
        tete = _tete(pn, icones.ARRETE if etat == "arrete" else icones.PRET, p.TETE_HEROS)
        boite.pack_start(tete, False, False, 0)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        textes.pack_start(_lab(t("carte.titre"), "pa-titre"), False, False, 0)
        if etat == "connexion":
            phrases = [t("carte.heros_connexion", nom=p.NOMS[pn.connexion[0]].upper())]
        else:
            phrases = u.phrases_heros(m, pn.allume, etat)
        if phrases != self.phrases:
            self.phrases, self.rang = phrases, 0
        self.ligne = _lab(self.phrases[self.rang % len(self.phrases)], "pa-heros")
        self.ligne.set_ellipsize(3)  # Pango.EllipsizeMode.END
        textes.pack_start(self.ligne, False, False, 0)
        boite.pack_start(textes, True, True, 0)
        boutons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if etat in ("ajout", "connexion"):
            boutons.pack_start(_bouton("pa-carre", c.Icone("fermer"), pn.retour, t("carte.infobulle_retour"),
                                       "retour"), False, False, 0)
        elif etat != "vide":
            boutons.pack_start(_bouton("pa-carre", c.Icone("plus"), pn.ajouter, t("carte.infobulle_plus"), "plus"),
                               False, False, 0)
            boutons.pack_start(_bouton("pa-carre", c.Icone("terminal"), pn.console, t("carte.infobulle_console"),
                                       "console"), False, False, 0)
        boite.pack_end(boutons, False, False, 0)
        return boite

    def _entete_fournisseur(self, nom, offre=""):
        boite = Gtk.Box(spacing=10)
        boite.pack_start(_tete(self.panneau, icones.PRET, 16), False, False, 0)
        boite.pack_start(_lab(nom, "pa-nom"), False, False, 0)
        if offre:
            boite.pack_end(_lab(offre, "pa-offre"), False, False, 0)
        return boite

    def _abonnement(self, etat, m, maintenant):
        pn = self.panneau
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_JAUGES)
        section.pack_start(self._entete_fournisseur("Claude", u.OFFRES.get(m.offre, "") if m else ""),
                           False, False, 0)
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
                         "pa-duree", xalign=1.0, largeur=56)
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
        self.boite.pack_start(section, False, False, 0)

    def _autre(self, nom):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=p.ECART_JAUGES)
        section.pack_start(self._entete_fournisseur(nom), False, False, 0)
        section.pack_start(_lab(t("carte.sans_mesure"), "pa-note"), False, False, 0)
        return section

    def _fournisseurs(self, etat):
        pn = self.panneau
        self.boite.pack_start(_lab(t("carte.intro_vide") if etat == "vide" else t("carte.intro_ajout"), "pa-texte",
                                   enrouler=True), False, False, 0)
        rangee = Gtk.Box(spacing=p.ECART_FOURNISSEURS, homogeneous=True)
        for cle, nom, commande in p.FOURNISSEURS:
            e = p.etat_fournisseur(pn.derniere, cle)
            contenu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            contenu.pack_start(c.TeteAgent(icones.PRET, taille=32), False, False, 0)
            contenu.pack_start(_lab(nom, xalign=0.5), False, False, 0)
            note = {"connecte": t("carte.connecte"), "non_installe": t("carte.non_installe")}.get(e, "")
            contenu.pack_start(_lab(note or " ", "pa-note", xalign=0.5), False, False, 0)
            b = _bouton("pa-grand", contenu, lambda k=cle: pn.connecter(k),
                        t("carte.bulle_terminal", commande=commande)
                        if e != "non_installe" else t("carte.bulle_absent", nom=nom), f"f-{cle}")
            b.set_sensitive(e != "non_installe")
            rangee.pack_start(b, True, True, 0)
        self.boite.pack_start(rangee, False, False, 0)

    def _connexion(self):
        pn = self.panneau
        cle = pn.connexion[0]
        commande = dict((k, cmd) for k, _n, cmd in p.FOURNISSEURS)[cle]
        self.boite.pack_start(_lab(t("carte.terminal_ouvert"), "pa-fort"), False, False, 0)
        etapes = Gtk.Grid(column_spacing=12, row_spacing=10)
        lignes = ((commande, t("carte.etape_lien")),
                  (None, t("carte.etape_finir")),
                  (None, t("carte.etape_mesure") if cle == "claude" else t("carte.etape_voit")))
        for i, (code, texte) in enumerate(lignes):
            etapes.attach(_lab(str(i + 1), "pa-num"), 0, i, 1, 1)
            boite = Gtk.Box()
            if code:
                boite.pack_start(_lab(code, "pa-code"), False, False, 0)
            boite.pack_start(_lab(texte, "pa-texte", enrouler=True), False, False, 0)
            etapes.attach(boite, 1, i, 1, 1)
        self.boite.pack_start(etapes, False, False, 0)
        self.boite.pack_start(_lien(t("carte.rouvrir"), lambda: pn.connecter(cle), cle="rouvrir"), False, False, 0)


# --- le panneau (icône, mesure, gestes) ---------------------------------------------------

class Panneau:
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
        return p.etat_carte(self.allume, self.derniere, self.statut, self.ajout, self.connexion)

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
        if self.connexion and time.time() - self.connexion[1] > p.CONNEXION_MAX_S:
            self.connexion = None
            self._redessiner()
        self.rafraichir(force=bool(self.connexion))
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
            self.statut = mesure.etat
            self.reessai = u.REESSAI_LIMITE_S if mesure.etat == "limite" else None
            if mesure.etat == "ok":
                self.derniere, self.heure = mesure, int(maintenant)
                u.cache_ecrire(mesure, maintenant)
            elif self.derniere is not None:
                self.derniere = dataclasses.replace(
                    self.derniere, totaux=mesure.totaux or self.derniere.totaux,
                    fournisseurs=mesure.fournisseurs or self.derniere.fournisseurs)
            else:
                self.derniere = dataclasses.replace(mesure, fenetres=())
            if self.connexion and p.etat_fournisseur(self.derniere, self.connexion[0]) == "connecte" \
                    and (self.connexion[0] != "claude" or mesure.etat == "ok"):
                self.connexion = None
        elif appel and allume:
            self.statut = "erreur"
        self._apparence()
        self._redessiner()
        if self.forcer:
            self.forcer = False
            self.rafraichir(force=True)
        return False

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
        self.ajout = False
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
        self.ajout = False

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
        self.ajout, self.connexion = False, None
        self._redessiner()

    def connecter(self, fournisseur):
        if p.etat_fournisseur(self.derniere, fournisseur) == "non_installe":
            return
        if self._lancer(p.argv_connexion(fournisseur)):
            self.ajout, self.connexion = False, (fournisseur, time.time())
            self._redessiner()

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

def _scene(etat, allume=True):
    maintenant = int(time.time())
    m = u.Mesure("ok", "max_20x", (
        u.Fenetre("5h", "", 93 if etat == "limite" else 34, maintenant + 2 * 3600 + 14 * 60),
        u.Fenetre("semaine", "", 61, maintenant + 3 * 86400 + 7 * 3600),
        u.Fenetre("semaine", "fable", 20, maintenant + 3 * 86400 + 7 * 3600)),
        u.Totaux(812000, 4200000, 23, 3, "opus", 1, False),
        (("codex", "connecte" if etat == "ajout" else "deconnecte"), ("grok", "non_installe")))
    pn = Panneau.__new__(Panneau)
    pn.allume, pn.derniere, pn.heure = allume, m, maintenant - (3 * 3600 if not allume else 60)
    pn.statut = "sans_jeton" if etat == "vide" else "ok"
    pn.ajout, pn.connexion = etat == "ajout", ("claude", maintenant) if etat == "connexion" else None
    pn.carte, pn.position, pn.en_cours = None, (900, 860), False
    pn.rafraichir = lambda force=False: None
    carte = Carte(pn, saisir=False)
    pn.carte = carte
    carte.remplir()
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
    return _scene("connexion")
