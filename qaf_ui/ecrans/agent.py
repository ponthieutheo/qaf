"""Panneau de l'agent (maquettes Agent et Pilote) : commande qaf-agent.

Ce que l'agent fait, en français simple, lu dans le journal du Doorman en
lecture seule (agent_etat.py, sans GTK), et les agents jetables ouverts
(registre du Doorman, par le socket des demandes comme la palette). Gestes
de l'utilisateur : pause d'urgence et reprise (qaf-pause), « Demander à l'agent… »
qui ouvre la palette qaf-palette (la demande y part au clavier interne).
Aucune action de l'agent ne passe ici. Tant que l'exception de développement
est posée (agent_etat.POLITIQUE_DEV), une ligne rouge le dit sous l'état.

Mise en page (docs/DESIGN-SESSION.md §6 et §11) : en-tête de 48 px avec la
tête de robot et son badge d'état (la forme change, pas seulement la
couleur), titre 20 px et le seul bouton principal ; l'état en une ligne
(titre 15 px et une phrase de conséquence en T2, comme une confirmation) ;
puis, à défilement, les agents ouverts (l'influence dite une seule fois, par
le badge et le mot sur la ligne de l'agent, et une phrase s'il y en a un) et
le fil, sections à en-tête 11 px T3 et lignes de 32 px qu'aucun bord ne
coupe au repos ; pied à filet avec la pause d'urgence à part. Ni pilule, ni
fond violet, ni étincelle : le violet n'existe que dans le badge « au
travail » de la tête.

Tout texte venu du journal ou des qubes est posé par libelle() / set_text :
jamais de Pango. Un processus à lui, Gtk.Application à instance unique
(docs/ECRANS-QAF.md §2).
"""

import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import donnees as d  # noqa: E402
from ..i18n import t  # noqa: E402
from ..nuit import composants as c  # noqa: E402
from ..nuit import css as style  # noqa: E402
from ..nuit import icones  # noqa: E402
from ..nuit import jetons as j  # noqa: E402
from . import agent_etat as ae  # noqa: E402

ID_APPLICATION = "fr.qaf.Agent"
LARGEUR, HAUTEUR = 560, 720
RAFRAICHIR_MS = 2000
COL_HEURE = 40  # « 21:08 » en chiffres tabulaires

# État du panneau -> badge de la tête (§6). Prêt : aucun badge ; au travail : disque violet.
_TETE = {ae.ACTIF: icones.TRAVAIL, ae.INFLUENCE: icones.INFLUENCE, ae.ATTENTE: icones.CONFIRMER,
         ae.PAUSE: icones.PAUSE, ae.ARRETE: icones.ARRETE}
# Signe d'une ligne du fil : icône au trait 16 px du jeu QAF (§13), recolorée par sa classe.
_SIGNE = {ae.FAIT: ("coche", "qaf-icone"), ae.REFUS: ("fermer", "qaf-signe-refus"),
          ae.DEMANDE: ("question", "qaf-signe-demande"), ae.INFO: ("point", "qaf-icone")}


def css():
    """CSS propre à l'écran (classe qaf-agent-panneau sur sa fenêtre), depuis les jetons."""
    e = "window.qaf-agent-panneau"
    return "\n".join(style.accents_ecran(e) + [
        f"{e} .qaf-etat {{ padding: 16px {j.MARGE_ECRAN}px; border-bottom: 1px solid {j.FILET}; }}",
        f"{e} label.qaf-influence {{ color: {j.DANGER}; }}",
        f"{e} label.qaf-dev {{ color: {j.DANGER}; }}",
        f"{e} .qaf-signe-refus {{ color: {j.DANGER}; }}",
        f"{e} .qaf-signe-demande {{ color: {j.T1}; }}",
        f"{e} list.qaf-liste row {{ min-height: {j.LIGNE}px; }}",
        f"{e} .qaf-reprise {{ padding: 16px {j.MARGE_ECRAN}px 0 {j.MARGE_ECRAN}px; "
        f"border-top: 1px solid {j.FILET}; }}",
    ]) + "\n"


_fournisseur = None


def installer_style(ecran=None):
    """Socle + CSS de l'écran, dans CE processus seulement (règle n°1)."""
    global _fournisseur
    erreurs = c.installer_style(ecran)
    if _fournisseur is None:
        fournisseur = Gtk.CssProvider()
        fournisseur.connect("parsing-error", lambda _p, _s, err: erreurs.append(err.message))
        try:
            fournisseur.load_from_data(css().encode("utf-8"))
        except GLib.Error as err:
            erreurs.append(err.message)
            return erreurs
        Gtk.StyleContext.add_provider_for_screen(ecran or Gdk.Screen.get_default(), fournisseur,
                                                 c.PRIORITE_STYLE)
        _fournisseur = fournisseur
    return erreurs


def _genre(bouton, genre):
    """Change le genre d'un c.Bouton (principal, secondaire, danger) à chaud."""
    ctx = bouton.get_style_context()
    for g in (c.PRINCIPAL, c.SECONDAIRE, c.DANGER):
        ctx.remove_class(f"qaf-{g}")
    ctx.add_class(f"qaf-{genre}")


def _ligne_fil(ligne):
    """Heure, signe (icône au trait 16 px), phrase sur une ligne, et le jetable en fin
    de ligne (« portier » pour un refus sans agent connu, sinon « — ») : colonnes
    fixes, lignes de 32 px exactement."""
    boite = c._classes(Gtk.Box(spacing=j.ICONE_ECART, valign=Gtk.Align.CENTER), "qaf-ligne")
    boite.set_size_request(-1, j.LIGNE)
    boite.pack_start(c.libelle(ligne.heure, "qaf-c3", "qaf-tnum", width_chars=5, valign=Gtk.Align.CENTER,
                               xalign=0), False, False, 0)
    signe, classe = _SIGNE.get(ligne.genre, ("point", "qaf-icone"))
    boite.pack_start(c.Icone(signe, classe=classe), False, False, 0)
    texte = c.libelle(ligne.texte, ellipsize=Pango.EllipsizeMode.END, valign=Gtk.Align.CENTER)
    texte.set_tooltip_text(ligne.texte)
    boite.pack_start(texte, True, True, 0)
    qui = ligne.agent or (t("agent.doorman") if ligne.genre == ae.REFUS else "—")
    boite.pack_end(c.libelle(qui, "qaf-detail", valign=Gtk.Align.CENTER), False, False, 0)
    return boite


def _ligne_agent(agent):
    """Agent ouvert, sur une ligne : sa tête, le nom du jetable, son âge et le
    début de sa demande ; à droite, s'il est influencé, le badge de §6 (disque
    percé d'un « ! ») et le mot. Un agent propre n'a rien à droite."""
    boite = c._classes(Gtk.Box(spacing=j.ICONE_ECART), "qaf-ligne")
    boite.set_size_request(-1, j.LIGNE)
    boite.pack_start(c.TeteAgent(icones.PRET), False, False, 0)
    boite.pack_start(c.libelle(agent.nom, "qaf-nom", valign=Gtk.Align.CENTER), False, False, 0)
    boite.pack_start(c.libelle(ae.ligne_agent(agent), "qaf-detail", ellipsize=Pango.EllipsizeMode.END,
                               valign=Gtk.Align.CENTER), True, True, 0)
    if agent.influence:
        mot = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        mot.pack_start(c.BadgeInfluence(), False, False, 0)
        mot.pack_start(c.libelle(t("agent.influence"), "qaf-influence", valign=Gtk.Align.CENTER), False, False, 0)
        boite.pack_end(mot, False, False, 0)
    elif agent.influence is None:
        boite.pack_end(c.libelle(t("agent.influence_inconnue"), "qaf-detail", valign=Gtk.Align.CENTER),
                       False, False, 0)
    return boite


class CaleLignes(Gtk.Box):
    """Tient la zone qui défile à une hauteur où aucune ligne du fil n'est coupée
    au repos : le haut fixe (agents, en-têtes) plus un nombre entier de lignes de
    32 px. Le reste de la hauteur, sous la zone, est le fond de la fenêtre."""

    def __init__(self, enfant, premiere_ligne):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(enfant, True, True, 0)
        self._premiere = premiere_ligne  # () -> y de la première ligne du fil dans la zone, ou None
        self._y = None

    def do_size_allocate(self, alloc):
        y = self._premiere()
        if y is not None and 0 <= y < alloc.height:
            alloc.height = y + ((alloc.height - y) // j.LIGNE) * j.LIGNE
        if y != self._y:  # le haut a changé (agents ouverts, alerte) : recaler au prochain tour
            self._y = y
            GLib.idle_add(lambda: self.queue_resize() and False)
        Gtk.Box.do_size_allocate(self, alloc)


def _section(titre, haut=0):
    """En-tête 11 px T3, aligné sur le texte des lignes (8 px de leur marge)."""
    entete = c.EnTeteSection(titre)
    entete.set_margin_start(j.MARGE_LISTE)
    entete.set_margin_top(haut)
    return entete


def _liste_lecture(nom):
    """Liste qu'on lit sans y choisir : ni survol ni sélection (rien à activer)."""
    return c.Liste(nom_accessible=nom)


class Reprise(Gtk.Box):
    """« Lever la pause ? » posé au-dessus du pied, à plat : la question en
    15 px, sa conséquence en T2, Annuler (focus initial) puis l'action, à
    droite. Ni la confirmation du Doorman, ni celle de Qubes."""

    def __init__(self, rappel_oui, rappel_non):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        c._classes(self, "qaf-reprise", "qaf-confirmation")
        self.pack_start(c.libelle(t("agent.reprise.titre"), "qaf-sous-titre"), False, False, 0)
        self.pack_start(c.libelle(t("agent.reprise.question"), "qaf-secondaire", wrap=True, margin_top=4),
                        False, False, 0)
        boutons = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_top=16)
        self.non = c.Bouton(t("agent.reprise.annuler"), c.SECONDAIRE, rappel_non)
        self.oui = c.Bouton(t("agent.reprise.lever"), c.DANGER, rappel_oui)
        boutons.pack_start(self.non, False, False, 0)
        boutons.pack_start(self.oui, False, False, 0)
        self.pack_start(boutons, False, False, 0)
        c._nom_accessible(self, t("agent.reprise.titre"))


class Panneau:
    """Construit la fenêtre et la tient à jour. source() -> Instantane ;
    basculer(mettre) -> Resultat (bloquant) ; tout est injecté pour les tests."""

    def __init__(self, source=None, basculer=None, application=None, lancer=None):
        self.lancer = lancer  # rendus et tests : à la place de subprocess.Popen
        self.source = source or ae.instantane
        self.basculer = basculer or ae.basculer_pause
        self.lecture_en_cours = False
        self.inst = None
        self.lignes, self.erreur = None, None
        self._en_cours = False
        installer_style()
        w = self.fenetre = c.FenetreAF(t("agent.titre"), LARGEUR, HAUTEUR, application=application)
        c._classes(w, "qaf-agent-panneau")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        boite.pack_start(self._entete(), False, False, 0)
        boite.pack_start(self._etat(), False, False, 0)

        # Sous l'état, tout défile d'un bloc (agents, influence, fil) : la fenêtre tient
        # sur un écran de 1366x768 quel que soit le nombre d'agents ouverts.
        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, margin_start=j.MARGE_ECRAN - j.MARGE_LISTE,
                        margin_end=j.MARGE_ECRAN - j.MARGE_LISTE, margin_bottom=16)
        a_plat = dict(margin_start=j.MARGE_LISTE, margin_end=j.MARGE_LISTE)  # textes alignés sur les lignes

        # Agents jetables ouverts (registre du Doorman, lu par le socket des demandes).
        self.titre_agents = _section(t("agent.section_ouverts"))
        self.titre_agents.set_no_show_all(True)
        corps.pack_start(self.titre_agents, False, False, 0)
        self.liste_agents = _liste_lecture(t("agent.section_ouverts"))
        self.liste_agents.set_no_show_all(True)
        corps.pack_start(self.liste_agents, False, False, 0)
        self.agents = None

        # L'influence en une phrase, seulement si un agent l'est (le mot est sur sa ligne).
        self.influence = c.libelle("", "qaf-secondaire", wrap=True, max_width_chars=64, margin_top=4,
                                   margin_bottom=4, **a_plat)
        self.influence.set_no_show_all(True)
        corps.pack_start(self.influence, False, False, 0)

        # Filet entre les deux sections, aligné sur le texte des lignes (36 px du bord de la
        # liste) ; seulement quand « Agents ouverts » est là.
        self.filet_agents = Gtk.Separator(margin_start=j.RETRAIT_SEPARATEUR, margin_end=j.MARGE_LISTE,
                                          margin_top=8)
        self.filet_agents.set_no_show_all(True)
        corps.pack_start(self.filet_agents, False, False, 0)

        self.titre_fil = _section(t("agent.section_fil"))
        corps.pack_start(self.titre_fil, False, False, 0)
        self.alerte = c.libelle("", "qaf-erreur", wrap=True, max_width_chars=64, **a_plat)
        self.alerte.set_no_show_all(True)
        corps.pack_start(self.alerte, False, False, 0)
        self.liste = _liste_lecture(t("agent.liste_fil"))
        corps.pack_start(self.liste, False, False, 0)
        defile = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        defile.set_overlay_scrolling(True)
        defile.add(corps)
        self.corps = corps
        boite.pack_start(CaleLignes(defile, self._y_fil), True, True, 0)

        boite.pack_start(self._pied(), False, False, 0)
        w.add(boite)
        self.appliquer(self.source())
        # Focus initial sur le fil (une liste, sans anneau) : le bouton principal ne
        # s'allume pas à l'ouverture ; Tab y mène. Jamais sur une ligne : le fil est
        # refait quand il change.
        self.liste.set_can_focus(True)
        w.set_focus(self.liste)

    def _y_fil(self):
        """y de la première ligne du fil dans la zone qui défile (None avant la mise en place)."""
        if self.liste.get_allocated_height() <= 1:
            return None
        res = self.liste.translate_coordinates(self.corps, 0, 0)
        return res[1] if res else None

    def _entete(self):
        entete = Gtk.Box(spacing=j.ICONE_ECART)
        c._classes(entete, "qaf-entete")
        self.tete = c.TeteAgent(icones.PRET)
        entete.pack_start(self.tete, False, False, 0)
        entete.pack_start(c.libelle(t("agent.titre"), "qaf-titre", valign=Gtk.Align.CENTER), False, False, 0)
        # La demande se tape dans la palette (clavier interne, P2), jamais ici.
        self.b_demander = c.Bouton(t("agent.demander"), c.PRINCIPAL, self.ouvrir_palette)
        self.b_demander.set_valign(Gtk.Align.CENTER)
        entete.pack_end(self.b_demander, False, False, 0)
        return entete

    def _etat(self):
        bloc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        c._classes(bloc, "qaf-etat")
        # Comme une confirmation (§7.6) : le titre en 15 px, une phrase de conséquence en T2.
        self.titre = c.libelle("", "qaf-sous-titre")
        bloc.pack_start(self.titre, False, False, 0)
        self.explication = c.libelle("", "qaf-secondaire", wrap=True, max_width_chars=64, margin_top=4)
        bloc.pack_start(self.explication, False, False, 0)
        # Exception de développement posée (un qube peut ouvrir un shell dans dom0) : une ligne
        # au rouge DANGER sous l'état, tant qu'elle est là (ancienne alerte de Réglages).
        self.dev = c.libelle(t("agent.mode_dev"), "qaf-dev", wrap=True, max_width_chars=64, margin_top=8)
        self.dev.set_tooltip_text(t("agent.mode_dev_detail"))
        self.dev.set_no_show_all(True)
        bloc.pack_start(self.dev, False, False, 0)
        return bloc

    def _pied(self):
        bas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Hors du pied : sa couleur atténuée masquerait celle d'une erreur.
        self.message = c.libelle("", wrap=True, max_width_chars=64, margin_start=j.MARGE_ECRAN,
                                 margin_end=j.MARGE_ECRAN, margin_bottom=12)
        self.message.set_no_show_all(True)
        bas.pack_start(self.message, False, False, 0)
        # Lever la pause se confirme dans l'écran ; la poser, non.
        self.confirmation = Reprise(self.lever_pause, self.annuler_reprise)
        self.confirmation.show_all()
        self.confirmation.set_no_show_all(True)
        self.confirmation.hide()
        bas.pack_start(self.confirmation, False, False, 0)
        pied = Gtk.Box(spacing=8)
        c._classes(pied, "qaf-pied")
        # Où il vit : chaque agent dans un espace jetable neuf (AGENT-JETABLE), plus sys-agent.
        self.lieu = c.libelle("", valign=Gtk.Align.CENTER, ellipsize=Pango.EllipsizeMode.END)
        pied.pack_start(self.lieu, True, True, 0)
        self.b_reprendre = c.Bouton(t("agent.reprendre"), c.SECONDAIRE, self.demander_reprise)
        self.b_pause = c.Bouton(t("agent.pause"), c.DANGER, lambda: self.pause(True))
        for b in (self.b_reprendre, self.b_pause):  # un seul des deux visible
            b.get_child().show_all()
            b.set_no_show_all(True)
        pied.pack_end(self.b_pause, False, False, 0)  # le danger à part (U65)
        pied.pack_end(self.b_reprendre, False, False, 0)
        bas.pack_start(pied, False, False, 0)
        return bas

    # --- état -----------------------------------------------------------------

    def appliquer(self, inst):
        self.inst = inst
        et = ae.etat(inst)
        tete = _TETE.get(et.code, icones.PRET)
        if et.code == ae.ACTIF and inst.agents == []:
            tete = icones.PRET  # aucun agent ne tourne : la tête nue, comme dans la barre
        self.tete.changer(tete)
        self.tete.set_tooltip_text(t("agent.infobulle_tete", etat=et.titre.lower()))
        self.titre.set_text(et.titre)
        self.explication.set_text(et.explication)
        self.dev.set_visible(bool(inst.mode_dev))
        self.influence.set_text(et.influence_texte)
        self.influence.set_visible(bool(et.influence_texte))
        pause = et.code == ae.PAUSE
        self.b_pause.set_visible(not pause)
        self.b_reprendre.set_visible(pause and not self.confirmation.get_visible())
        if not pause:
            self.confirmation.hide()
        # Un seul bouton principal, et jamais un geste inutile : en pause, « Reprendre » est
        # le principal et « Demander » est éteint (tout serait refusé) ; prêt (aucun agent ne
        # tourne), la pause d'urgence est éteinte.
        _genre(self.b_demander, c.SECONDAIRE if pause else c.PRINCIPAL)
        _genre(self.b_reprendre, c.PRINCIPAL if pause else c.SECONDAIRE)
        if not self._en_cours:
            self.b_demander.set_sensitive(not pause)
            pret = et.code == ae.ACTIF and inst.agents == [] and inst.portier is not False
            self.b_pause.set_sensitive(not pret)
        self._appliquer_agents(inst.agents, inst.permanent)
        if inst.erreur:
            alerte = t("agent.alerte_droit")
        elif not inst.integre:
            alerte = t("agent.alerte_chaine")
        else:
            alerte = ""
        self.alerte.set_text(alerte)
        self.alerte.set_visible(bool(alerte))
        lignes = ae.fil(inst)
        if lignes == self.lignes and self.erreur == inst.erreur:
            return  # rien de neuf : ni focus ni défilement perdus
        self.lignes, self.erreur = lignes, inst.erreur
        self.liste.vider()
        for ligne in lignes:
            rang = self.liste.ajouter(_ligne_fil(ligne))
            acc = rang.get_accessible()
            if acc is not None:
                acc.set_name(f"{ligne.heure} {ligne.texte}")
        if not lignes and not inst.erreur:
            self.liste.ajouter(c.libelle(t("agent.rien"), "qaf-c3"))
        self.liste.show_all()

    def _appliquer_agents(self, agents, permanent=False):
        if agents is None:
            self.lieu.set_text(t("agent.lieu.inconnu"))
        elif permanent and not agents:  # sys-agent (AGENT-JETABLE D28) : pas un agent « ouvert »
            self.lieu.set_text(t("agent.lieu.permanent"))
        else:
            n = len(agents)
            self.lieu.set_text(t("agent.lieu.aucun") if n == 0 else t("agent.lieu.un") if n == 1
                               else t("agent.lieu.plusieurs", n=n))
        visible = bool(agents)
        self.titre_agents.set_visible(visible)
        self.liste_agents.set_visible(visible)
        self.filet_agents.set_visible(visible)
        if agents == self.agents:
            return
        self.agents = agents
        self.liste_agents.vider()
        for a in agents or []:
            rang = self.liste_agents.ajouter(_ligne_agent(a))
            mot = t({True: "agent.influence", False: "agent.propre", None: "agent.influence_inconnue"}[a.influence])
            c._nom_accessible(rang, f"{a.nom}, {ae.ligne_agent(a)}, {mot}")
        for rang in self.liste_agents.get_children():  # la liste est en no_show_all : ses lignes à la main
            rang.show_all()

    def rafraichir(self):
        if not self.lecture_en_cours:
            self.lecture_en_cours = True

            def lire():
                try:
                    inst = self.source()
                except Exception:  # un écran ne tombe pas pour une lecture ratée
                    inst = None
                GLib.idle_add(self._recu, inst)
            threading.Thread(target=lire, daemon=True).start()
        return True  # GLib.timeout_add : continuer

    def _recu(self, inst):
        self.lecture_en_cours = False
        if inst is not None and self.fenetre.get_window() is not None:
            self.appliquer(inst)
        return False

    # --- gestes de l'utilisateur ---------------------------------------------------------

    def _dire(self, texte, erreur=False):
        ctx = self.message.get_style_context()
        (ctx.add_class if erreur else ctx.remove_class)("qaf-erreur")
        self.message.set_text(texte)
        self.message.set_visible(bool(texte))

    def _en_fond(self, travail, bouton):
        bouton.set_sensitive(False)
        self.fenetre.fermable = False
        self._en_cours = True

        def fini(res):
            bouton.set_sensitive(True)
            self.fenetre.fermable = True
            self._en_cours = False
            self._dire(res.message, not res.ok)
            self.rafraichir()
            return False
        threading.Thread(target=lambda: GLib.idle_add(fini, travail()), daemon=True).start()

    def pause(self, mettre):
        self._en_fond(lambda: self.basculer(mettre), self.b_pause if mettre else self.b_reprendre)

    def demander_reprise(self):
        self.b_reprendre.hide()
        self.confirmation.show()
        self.confirmation.non.grab_focus()  # focus initial sur Annuler

    def annuler_reprise(self):
        self.confirmation.hide()
        self.b_reprendre.set_visible(self.inst is not None and ae.etat(self.inst).code == ae.PAUSE)

    def lever_pause(self):
        self.confirmation.hide()
        self.b_reprendre.show()
        self.pause(False)

    def ouvrir_palette(self):
        """Une demande part de la palette (clavier interne, P2) : jamais d'ici."""
        self._en_fond(lambda: d.ouvrir_palette(**({"lancer": self.lancer} if self.lancer else {})),
                      self.b_demander)


def construire(application=None, source=None, basculer=None, lancer=None):
    """Fenêtre du panneau (rendu hors écran, tests) ; pas de minuterie."""
    p = Panneau(source=source, basculer=basculer, application=application, lancer=lancer)
    p.fenetre.panneau = p
    return p.fenetre


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=ID_APPLICATION)
        self.fenetre = None

    def do_activate(self):
        if self.fenetre is not None:
            self.fenetre.present()  # second lancement : ramène le panneau
            return
        self.fenetre = construire(application=self)
        GLib.timeout_add(RAFRAICHIR_MS, self.fenetre.panneau.rafraichir)
        self.fenetre.show_all()


def main():
    return Application().run(sys.argv[:1])
