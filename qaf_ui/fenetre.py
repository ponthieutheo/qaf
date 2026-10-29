"""Rendu GTK3 de la fenêtre de confirmation (design/project/Confirmation.dc.html).

Rendu seulement : chaque frappe, clic, prise de focus et tic d'horloge est
remis à confirmation.Etat, qui seul décide. Importé par serveur.main(),
jamais par les tests (pas de GTK sur la machine de test).

Points GTK (vérifiés dans les sources Qubes 4.3) :
- CSS par Gtk.CssProvider sur l'écran, comme qubes_menu/appmenu.py ;
- set_keep_above + délai de focus, comme qrexec_policy_agent.py ;
- périphérique d'origine d'une frappe : Gdk.Event.get_source_device()
  (périphérique « esclave » XInput2, p. ex. « AT Raw Set 2 keyboard »),
  jamais get_device() qui renvoie le clavier virtuel maître.
Tous les textes passent par set_text (jamais de balisage Pango).
"""

import math
import threading
import time
from string import Template

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import confirmation as c  # noqa: E402
from .i18n import t  # noqa: E402
from .nuit import confiance as conf  # noqa: E402
from .nuit import jetons as j  # noqa: E402

TIC_MS = 200

# Direction B (refonte du 29/10, maquettes « confirmation-b ») :
# carte sombre aux jetons COEUR_* (figés : un thème ne change jamais le Cœur
# du système), bordure grise de 2 px (teinte d'aucun label : rien d'un liseré
# de qube), bande « CŒUR DU SYSTÈME » sur le noir de la barre avec le repère
# ◉ au violet SIGNATURE ; « Refuser » plein SIGNATURE (bouton principal de
# QAF, celui d'Entrée), « Autoriser » en contour neutre. Toute alerte (agent
# permanent, influence, sans demande, gravité forte, irréversible, mode dev)
# au rouge DANGER, jamais d'orange. Paires de contraste : jetons._PAIRES.
COULEURS = {
    "voile": j.COEUR_VOILE, "carte": j.COEUR_FOND, "carte_bord": j.COEUR_BORD,
    "bande": j.COEUR_VOILE, "bande_filet": j.COEUR_FILET, "bande_t1": j.COEUR_T1, "bande_t3": j.COEUR_T3,
    "t1": j.COEUR_T1, "t2": j.COEUR_T2, "t3": j.COEUR_T3,
    "champ": j.COEUR_CHAMP, "champ_bord": j.COEUR_FILET, "autoriser_bord": j.COEUR_FILET_FORT,
    "danger": j.DANGER, "repere": j.SIGNATURE,
    "refuser_fond": j.SIGNATURE, "refuser_survol": j.SIGNATURE_SURVOL, "refuser_texte": j.COEUR_VOILE,
}

_GABARIT = """
.qaf-fond { background-color: $voile; }
.qaf-fond.translucide { background-color: alpha($voile, 0.88); }
.qaf-carte { background-color: $carte; color: $t1; border-radius: 10px; border: 2px solid $carte_bord;
            font-family: $police; font-size: 13px; }
.qaf-entete { background-color: $bande; border-bottom: 1px solid $bande_filet; padding: 14px 20px;
             border-radius: 8px 8px 0 0; }
.qaf-entete-titre { color: $bande_t1; font-size: 12px; font-weight: 700; }
.qaf-entete-sous { color: $bande_t3; font-size: 12px; }
.qaf-corps { padding: 22px 28px 4px 28px; }
.qaf-alerte { background-color: $champ; border-left: 3px solid $danger; border-radius: 4px; padding: 10px 14px; }
.qaf-alerte-texte { color: $danger; font-size: 13px; font-weight: 600; }
.qaf-titre { font-family: $police_titre; font-size: 20px; font-weight: 600; color: $t1; }
.qaf-action { color: $t2; }
.qaf-flux { background-color: $champ; border: 1px solid $champ_bord; border-radius: 6px; padding: 12px 14px; }
.qaf-espace { font-weight: 600; color: $t1; }
.qaf-espace.qaf-nom-agent { font-style: italic; font-weight: 400; color: $t2; }
.qaf-confiance { font-size: 11px; color: $t3; }
.qaf-demande { font-size: 15px; color: $t1; background-color: $champ; border: 1px solid $champ_bord;
              border-radius: 6px; padding: 10px 12px; }
.qaf-sous-titre { font-weight: 600; color: $t2; }
.qaf-note-demande { font-size: 11px; color: $t3; }
.qaf-detail-cle { color: $t3; }
.qaf-detail { color: $t1; }
.qaf-portee, .qaf-message, .qaf-detail.alerte { color: $danger; font-weight: 600; }
.qaf-code-bloc { background-color: $champ; border: 1px solid $champ_bord; border-radius: 6px; padding: 12px 16px; }
.qaf-chiffre { font-family: $police_code; font-size: 34px; font-weight: 700; color: $t1;
              background-color: $carte; border: 1px solid $champ_bord; border-radius: 6px; padding: 2px 10px; }
.qaf-tape { font-family: $police_code; font-size: 22px; color: $t2; }
.qaf-contenu-cadre { border: 1px solid $champ_bord; border-radius: 6px; }
.qaf-contenu { font-family: $police_code; color: $t1; background-color: $champ; padding: 8px 10px; }
.qaf-boutons { padding: 18px 28px 12px 28px; }
.qaf-refuser, .qaf-autoriser { background-image: none; box-shadow: none; text-shadow: none; border-radius: 6px;
                              min-height: 44px; font-weight: 600; font-size: 15px; }
.qaf-refuser { background-color: $refuser_fond; color: $refuser_texte; border: 0; }
.qaf-refuser:hover { background-color: $refuser_survol; }
.qaf-autoriser { background-color: transparent; color: $t3; border: 1px solid $autoriser_bord; }
.qaf-autoriser.pret { color: $t1; border: 2px solid $t2; }
.qaf-pied { padding: 0 28px 16px 28px; }
.qaf-pied-texte { font-size: 11px; color: $t3; }
.qaf-minuteur { font-size: 11px; color: $t2; }
"""


def css(couleurs):
    """Feuille de style de la fenêtre pour un jeu de couleurs (COULEURS)."""
    valeurs = dict(couleurs, police=j.POLICE, police_titre=j.POLICE_TITRE, police_code=j.POLICE_CODE)
    return Template(_GABARIT).substitute(valeurs).encode()


CSS = css(COULEURS)


def appareil(event):
    """Nom du périphérique physique qui a produit l'événement, ou None."""
    try:
        dev = event.get_source_device()
    except (AttributeError, TypeError):
        dev = Gdk.Event.get_source_device(event)
    return dev.get_name() if dev is not None else None


def _rgb(hexa):
    return tuple(int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _teinte(espace: c.Espace):
    """(remplissage, liseré) de la pastille sur la carte sombre : teinte du
    label éclaircie (confiance.COULEURS_ECRAN, 3:1 sur les surfaces Nuit), noir
    avec liseré clair, gris neutre pour une sorte (Modèle, Système, Cœur)."""
    if espace.sorte:
        return conf.COULEUR_SORTE, None
    if espace.forme == "trait" or espace.label not in conf.COULEURS_ECRAN:
        return conf.COULEUR_INCONNUE, None
    return conf.COULEURS_ECRAN[espace.label], (conf.LISERE_NOIR if espace.label == "black" else None)


def forme(espace: c.Espace, taille=16):
    """Pastille : couleur du label Qubes ET forme du niveau de confiance."""
    zone = Gtk.DrawingArea()
    zone.set_size_request(taille, taille)

    def dessiner(_w, cr):
        s, f = taille, espace.forme
        teinte, lisere = _teinte(espace)
        cr.set_source_rgb(*_rgb(teinte))
        if f.endswith("-creux"):  # Modèle, Système, Cœur du système : en creux, gris neutre
            cr.set_line_width(1.5)
            if f == "carre-creux":
                cr.rectangle(2.5, 2.5, s - 5, s - 5)
            elif f == "rond-creux":
                cr.arc(s / 2, s / 2, s / 2 - 2.25, 0, 2 * math.pi)
            else:  # cœur du système : anneau et point central (◉)
                cr.arc(s / 2, s / 2, s / 2 - 2.25, 0, 2 * math.pi)
                cr.stroke()
                cr.arc(s / 2, s / 2, max(1.5, s / 6), 0, 2 * math.pi)
                cr.fill()
                return False
            cr.stroke()
            return False
        if f == "carre":
            cr.rectangle(2, 2, s - 4, s - 4)
        elif f == "rond":
            cr.arc(s / 2, s / 2, s / 2 - 2, 0, 2 * math.pi)
        elif f == "triangle":
            cr.move_to(s / 2, 1.5)
            cr.line_to(s - 1.5, s - 2)
            cr.line_to(1.5, s - 2)
            cr.close_path()
        elif f == "losange":
            cr.move_to(s / 2, 1)
            cr.line_to(s - 1, s / 2)
            cr.line_to(s / 2, s - 1)
            cr.line_to(1, s / 2)
            cr.close_path()
        else:
            cr.rectangle(2, s / 2 - 1, s - 4, 2)
        cr.fill_preserve()
        if lisere:
            cr.set_source_rgb(*_rgb(lisere))
            cr.set_line_width(1)
            cr.stroke()
        else:
            cr.new_path()
        return False

    zone.connect("draw", dessiner)
    return zone


def _dessin(taille, peindre, valign=Gtk.Align.CENTER):
    zone = Gtk.DrawingArea()
    zone.set_size_request(taille, taille)
    zone.set_valign(valign)
    zone.connect("draw", lambda _w, cr: peindre(cr, taille) or False)
    return zone


def _repere(cr, s):
    """◉ du Cœur du système dans la bande : posé par dom0, jamais par un qube."""
    cr.set_source_rgb(*_rgb(COULEURS["repere"]))
    cr.set_line_width(1.6)
    cr.arc(s / 2, s / 2, s / 2 - 1.5, 0, 2 * math.pi)
    cr.stroke()
    cr.arc(s / 2, s / 2, s / 5, 0, 2 * math.pi)
    cr.fill()


def _signe_alerte(cr, s):
    """Disque « ! » de l'état influencé (le même que dans la barre)."""
    cr.set_source_rgb(*_rgb(COULEURS["danger"]))
    cr.arc(s / 2, s / 2, s / 2, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*_rgb(COULEURS["champ"]))
    cr.rectangle(s / 2 - 1, s * 0.22, 2, s * 0.36)
    cr.fill()
    cr.arc(s / 2, s * 0.74, 1.3, 0, 2 * math.pi)
    cr.fill()


def _label(texte, classe, wrap=False, xalign=0.0):
    lab = Gtk.Label()
    lab.set_text(texte)  # jamais set_markup
    lab.set_xalign(xalign)
    if wrap:
        lab.set_line_wrap(True)
        lab.set_max_width_chars(60)
    for k in classe.split():
        lab.get_style_context().add_class(k)
    return lab


def _bloc_espace(e: c.Espace):
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    b.pack_start(forme(e), False, False, 0)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    nom = _label(e.nom, "qaf-espace")
    if e.par_agent:  # nom choisi par l'agent : mis en forme autrement (C1)
        nom.get_style_context().add_class("qaf-nom-agent")
    v.pack_start(nom, False, False, 0)
    if e.role != "systeme" or e.confiance != c.INCONNU:
        v.pack_start(_label(e.mention, "qaf-confiance"), False, False, 0)
    b.pack_start(v, False, False, 0)
    return b


def _contenu(texte):
    """Texte de l'agent (commande, texte à taper), déjà échappé par
    confirmation.echapper : affiché EN ENTIER dans une zone défilante,
    jamais interprété (set_text), jamais focalisable (les touches restent à
    la fenêtre : Entrée et Échap refusent). Retour à la ligne par caractère,
    SANS trait d'union ajouté par Pango (mesuré le 25/10 avec un TextView :
    « r-ester » pour « rester »), et aucun défilement horizontal : rien de
    caché à droite."""
    lab = Gtk.Label()
    lab.set_text(texte)
    lab.set_xalign(0.0)
    lab.set_yalign(0.0)
    lab.set_line_wrap(True)
    lab.set_line_wrap_mode(Pango.WrapMode.CHAR)
    lab.set_width_chars(68)
    lab.set_max_width_chars(68)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_insert_hyphens_new(False))
    attrs.insert(Pango.attr_family_new("DejaVu Sans Mono, Noto Sans Mono CJK SC, Noto Sans CJK SC, monospace"))
    lab.set_attributes(attrs)
    lab.get_style_context().add_class("qaf-contenu")
    zone = Gtk.ScrolledWindow()
    zone.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    zone.set_min_content_height(40)
    zone.set_max_content_height(240)
    zone.set_propagate_natural_height(True)
    zone.get_style_context().add_class("qaf-contenu-cadre")
    zone.add(lab)
    return zone


def _flux(action: c.Action, influence):
    boite = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    boite.get_style_context().add_class("qaf-flux")
    if action.source:
        boite.pack_start(_bloc_espace(action.source), False, False, 0)
    if action.source and action.destination:
        trait = Gtk.DrawingArea()
        trait.set_size_request(60, 16)
        rouge = influence or action.niveau >= 3

        def dessiner(w, cr):
            h, larg = w.get_allocated_height(), w.get_allocated_width()
            cr.set_source_rgb(*_rgb(COULEURS["danger"] if rouge else COULEURS["t3"]))
            cr.rectangle(0, h / 2 - 1, larg - 8, 2)
            cr.fill()
            cr.move_to(larg, h / 2)
            cr.line_to(larg - 9, h / 2 - 5)
            cr.line_to(larg - 9, h / 2 + 5)
            cr.close_path()
            cr.fill()
            return False

        trait.connect("draw", dessiner)
        boite.pack_start(trait, True, True, 0)
    if action.destination:
        boite.pack_start(_bloc_espace(action.destination), False, False, 0)
    return boite


def _details(vue):
    """(libellé, valeur, au rouge DANGER) des détails, dans la langue de dom0 :
    sans demande, irréversible, gravité forte ou interdite (affichage seulement)."""
    return [(t("conf.fen.demande"), vue.demande, vue.texte_demande is None),
            (t("conf.fen.reversible"), vue.reversible, vue.irreversible),
            (t("conf.fen.gravite"), vue.gravite, vue.gravite_forte)]


class FenetreConfirmation:
    def __init__(self, req: c.Requete, clavier: c.Clavier, horloge, fin, relire=None):
        self.vue = c.rediger(req)
        self.etat = c.Etat(self.vue, clavier, req.expire_dans, horloge, relire)
        self._fin, self._fini = fin, False

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title(t("conf.fen.titre"))
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        # Voile sombre plein écran (boîte, pas la fenêtre : certains thèmes
        # ne dessinent pas le fond d'une fenêtre), la carte au centre.
        # Translucide seulement si un compositeur le permet.
        self.fond = Gtk.EventBox()
        self.fond.get_style_context().add_class("qaf-fond")
        visuel = self.win.get_screen().get_rgba_visual()
        if visuel is not None and self.win.get_screen().is_composited():
            self.win.set_visual(visuel)
            self.win.set_app_paintable(True)
            self.fond.get_style_context().add_class("translucide")

        carte = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        carte.get_style_context().add_class("qaf-carte")
        if self.vue.influence:
            carte.get_style_context().add_class("influencee")
        carte.set_size_request(640, -1)
        carte.set_halign(Gtk.Align.CENTER)
        carte.set_valign(Gtk.Align.CENTER)

        # Bande du Cœur du système : repère ◉, nom, et ce que l'agent ne peut pas.
        entete = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        entete.get_style_context().add_class("qaf-entete")
        entete.pack_start(_dessin(14, _repere), False, False, 0)
        entete.pack_start(_label(self.vue.entete, "qaf-entete-titre"), False, False, 0)
        entete.pack_end(_label(self.vue.sous_entete, "qaf-entete-sous", xalign=1.0), False, False, 0)
        carte.pack_start(entete, False, False, 0)

        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        corps.get_style_context().add_class("qaf-corps")
        if self.vue.alerte:
            alerte = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            alerte.get_style_context().add_class("qaf-alerte")
            alerte.pack_start(_dessin(18, _signe_alerte, Gtk.Align.START), False, False, 0)
            alerte.pack_start(_label(self.vue.alerte, "qaf-alerte-texte", wrap=True), True, True, 0)
            corps.pack_start(alerte, False, False, 0)
        # Demande de l'utilisateur et actions côte à côte (C13) : texte du registre de dom0.
        if self.vue.texte_demande is not None:
            demande = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            demande.pack_start(_label(t("conf.fen.ta_demande", age=self.vue.age_demande), "qaf-sous-titre"),
                               False, False, 0)
            demande.pack_start(_label(self.vue.texte_demande, "qaf-demande", wrap=True), False, False, 0)
            if self.vue.suite:  # n19 : la fenêtre ne vérifie que la demande, jamais la suite tapée
                demande.pack_start(_label(t("conf.suite_non_verifiee"), "qaf-note-demande", wrap=True),
                                   False, False, 0)
            corps.pack_start(demande, False, False, 0)
        corps.pack_start(_label(self.vue.titre, "qaf-titre", wrap=True), False, False, 0)
        plusieurs = len(self.vue.actions) > 1
        for i, a in enumerate(self.vue.actions, 1):
            if plusieurs:
                corps.pack_start(_label(f"{i}. {a.phrase}", "qaf-action", wrap=True), False, False, 0)
            if a.hors_portee:
                corps.pack_start(_label("⚠ " + t("conf.alerte_portee"), "qaf-portee"), False, False, 0)
            if a.contenu:
                corps.pack_start(_label(t("conf.fen.texte_exact"), "qaf-sous-titre"), False, False, 0)
                corps.pack_start(_contenu(a.contenu), False, False, 0)
            if a.source or a.destination:
                corps.pack_start(_flux(a, self.vue.influence), False, False, 0)

        details = Gtk.Grid(column_spacing=12, row_spacing=6)
        for r, (cle, val, rouge) in enumerate(_details(self.vue)):
            details.attach(_label(cle, "qaf-detail-cle"), 0, r, 1, 1)
            classe = "qaf-detail alerte" if rouge else "qaf-detail"
            details.attach(_label(val, classe, wrap=True), 1, r, 1, 1)
        corps.pack_start(details, False, False, 0)

        self.tape = None
        if self.vue.renforce:
            bloc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            bloc.get_style_context().add_class("qaf-code-bloc")
            bloc.pack_start(_label(t("conf.fen.renforcee"), "qaf-sous-titre"), False, False, 0)
            # Un chiffre par case, deux groupes de trois (pas de letter-spacing :
            # GTK 3 double alors la hauteur du libellé).
            ligne = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for i, chiffre in enumerate(self.vue.jeton):
                case = _label(chiffre, "qaf-chiffre", xalign=0.5)
                case.set_margin_start(14 if i and i % 3 == 0 else 0)
                ligne.pack_start(case, False, False, 0)
            self.tape = _label("", "qaf-tape", xalign=1.0)
            self.tape.set_valign(Gtk.Align.CENTER)
            ligne.pack_end(self.tape, False, False, 0)
            bloc.pack_start(ligne, False, False, 0)
            corps.pack_start(bloc, False, False, 0)
        self.message = _label("", "qaf-message", wrap=True)
        self.message.set_no_show_all(True)  # visible seulement avec un message (mode dev compris)
        corps.pack_start(self.message, False, False, 0)
        carte.pack_start(corps, False, False, 0)

        boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True)
        boutons.get_style_context().add_class("qaf-boutons")
        self.refuser = Gtk.Button(label=t("conf.fen.refuser"))
        self.autoriser = Gtk.Button(label=t("conf.fen.autoriser"))
        for b, classe in ((self.refuser, "qaf-refuser"), (self.autoriser, "qaf-autoriser")):
            b.set_can_focus(False)  # aucune activation clavier par GTK : tout passe par Etat
            b.get_style_context().add_class(classe)
            boutons.pack_start(b, True, True, 0)
        self.refuser.connect("clicked", lambda _b: self._decider(self.etat.clic("refuser")))
        # Autoriser n'a pas de « clicked » : un clic, d'où qu'il vienne, est ignoré.
        self.autoriser.connect("button-press-event", self._clic_autoriser)
        carte.pack_start(boutons, False, False, 0)

        pied = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pied.get_style_context().add_class("qaf-pied")
        pied.pack_start(_label(self.vue.pied, "qaf-pied-texte"), False, False, 0)
        self.pied = _label("", "qaf-minuteur", xalign=1.0)
        pied.pack_end(self.pied, False, False, 0)
        carte.pack_start(pied, False, False, 0)

        self.fond.add(carte)
        self.win.add(self.fond)
        self.win.connect("key-press-event", self._touche)
        self.win.connect("focus-in-event", lambda *_: self.etat.focus(True))
        self.win.connect("focus-out-event", lambda *_: self.etat.focus(False))
        self.win.connect("delete-event", self._fermeture)
        self._actualiser()
        GLib.timeout_add(TIC_MS, self._tic)
        self.win.show_all()
        self.win.fullscreen()
        self.win.present()

    def _touche(self, _w, event):
        self._decider(self.etat.touche(Gdk.keyval_name(event.keyval) or "", appareil(event)))
        return True  # aucune touche n'atteint un autre widget

    def _clic_autoriser(self, _w, event):
        self._decider(self.etat.clic("autoriser", appareil(event)))
        return True

    def _fermeture(self, *_):
        self._decider(self.etat.fermeture())
        return True

    def _tic(self):
        if self._fini:
            return False
        self._decider(self.etat.tic())
        return not self._fini

    def _actualiser(self):
        restant = int(math.ceil(self.etat.restant()))
        self.pied.set_text(t("conf.fen.minuteur", duree=f"{restant // 60}:{restant % 60:02d}"))
        self.message.set_text(self.etat.message)
        self.message.set_visible(bool(self.etat.message))
        if self.tape is not None:
            saisi = self.etat.tape.ljust(len(self.vue.jeton), "_")
            self.tape.set_text(" ".join(saisi[:3]) + "   " + " ".join(saisi[3:]))
        ctx = self.autoriser.get_style_context()
        (ctx.add_class if self.etat.autorisable() else ctx.remove_class)("pret")

    def _decider(self, resultat):
        if self._fini:
            return
        if resultat is None:
            self._actualiser()
            return
        self._fini = True
        self.win.destroy()
        self._fin(resultat)

    def forcer(self):
        """Appelé si le serveur a cessé d'attendre : refus « expire »."""
        self._decider(self.etat._finir(False, c.EXPIRE))
        return False


def afficheur(clavier=c.clavier_systeme, horloge=time.monotonic):
    """Fonction afficher(req) -> réponse, appelée depuis le thread du serveur :
    la fenêtre est créée dans la boucle GTK (GLib.idle_add)."""

    def afficher(req):
        fini, boite, fen = threading.Event(), {}, {}

        def terminer(res):
            boite.setdefault("res", res)
            fini.set()

        def ouvrir():
            try:
                fen["f"] = FenetreConfirmation(req, clavier(), horloge, terminer, relire=clavier)
            except Exception:
                terminer(c.Resultat(False, c.ERREUR))
            return False

        GLib.idle_add(ouvrir)
        if not fini.wait(req.expire_dans + 2):
            GLib.idle_add(lambda: fen["f"].forcer() if "f" in fen else False)
            return c.refus(c.EXPIRE)
        return boite["res"].reponse()

    return afficher


def preparer() -> bool:
    """Ouvre l'affichage et charge le style. False : pas d'affichage."""
    if not Gtk.init_check(None)[0]:
        return False
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), fournisseur,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return True


def boucle():
    Gtk.main()
    return 0
