"""Icônes QAF au trait et tête de l'agent (docs/DESIGN-SESSION.md §6, §13), sans GTK.

Un seul jeu d'icônes pour les écrans de la brique agent (panneau de l'agent,
carte de l'agent dans la barre) : symbolique monochrome, 16 px, trait 1,5 px
(2,25 dans la grille de 24), bouts ronds (DESIGN-SESSION-V2 §3.1 : un trait de
1,33 tombait sur deux pixels à moitié allumés). La couleur n'est jamais dans le
tracé : elle vient du CSS au moment de dessiner (composants.Icone lit la couleur
de son style, donc ICONE au repos, T3 désactivé).

Tracés repris de Lucide (https://lucide.dev, licence ISC, texte dans
LICENCES-TIERS) ; aucun fichier n'est installé : le SVG est construit en
mémoire (svg()) et rendu par GdkPixbuf (librsvg, déjà dans dom0 pour les
icônes Papirus).

La tête de l'agent n'est pas un tracé Lucide : elle est dessinée en Cairo
(dessiner_tete_agent), avec un badge d'état dont la FORME change (§6).
"""

import math
from xml.sax.saxutils import quoteattr

# nom -> contenu SVG (grille 24 x 24, sans couleur ni épaisseur)
TRACES = {
    "coche": '<path d="M20 6 9 17l-5-5"/>',
    "fermer": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "terminal": '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    # États écrits du fil de l'agent : jamais une forme des niveaux de confiance
    # (triangle, rond plein, carré, losange) ni le disque du badge de l'agent.
    "question": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
                '<path d="M12 17h.01"/>',  # help-circle
    "point": '<circle cx="12.1" cy="12.1" r="1"/>',  # dot
}
NOMS = tuple(sorted(TRACES))
EPAISSEUR = 2.25  # dans la grille de 24 : 1,5 px à 16 px (DESIGN-SESSION-V2 §3.1)


def svg(nom, couleur, taille=16):
    """Document SVG de l'icône `nom`, tracée en `couleur` ("#RRGGBB"), à `taille` px.
    Lève KeyError pour un nom inconnu (jamais de tracé venu d'ailleurs)."""
    trace = TRACES[nom]
    if not (isinstance(couleur, str) and len(couleur) == 7 and couleur[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in couleur[1:])):
        raise ValueError(f"couleur invalide : {couleur!r}")
    c = quoteattr(couleur)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(taille)}" height="{int(taille)}" '
            f'viewBox="0 0 24 24" fill="none" stroke={c} color={c} stroke-width="{EPAISSEUR}" '
            f'stroke-linecap="round" stroke-linejoin="round">{trace}</svg>')


# --- tête de l'agent (§6) ------------------------------------------------------------
# États du badge : la forme change, pas seulement la couleur (six silhouettes).
PRET, TRAVAIL, CONFIRMER, INFLUENCE, PAUSE, ARRETE = (
    "pret", "travail", "confirmer", "influence", "pause", "arrete")
ETATS_AGENT = (PRET, TRAVAIL, CONFIRMER, INFLUENCE, PAUSE, ARRETE)
# Jeton de couleur du badge (lu dans jetons au moment de dessiner).
COULEUR_BADGE = {TRAVAIL: "AGENT", CONFIRMER: "ATTENTION", INFLUENCE: "DANGER", PAUSE: "ICONE"}


# Badge (DESIGN-SESSION-V2 §3.4) : disque r 3,5 (7 px), anneau r 2,75 trait 1,5
# (7 px hors tout), détourage r 5. Centre (13,5 ; 13,5) du carré de 16.
RAYON_BADGE, RAYON_ANNEAU, RAYON_DETOURAGE = 3.5, 2.75, 5


def _rgb(hexa):
    return tuple(int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))


def dessiner_tete_agent(cr, etat, tete, fond, badge=None):
    """Dessine la tête de robot dans le carré 16 x 16 courant de `cr` (Cairo ;
    mettre cr.scale(t / 16, t / 16) avant pour une autre taille).

    etat : un de ETATS_AGENT ; tete : couleur du trait ("#RRGGBB", ICONE en
    général, T3 à l'arrêt) ; fond : couleur du fond de la case (détourage du
    badge par un disque r = 5 ; DESIGN-SESSION-V2 §3.4 : badges agrandis pour
    se voir du coin de l'œil, ils débordent de 1 px du carré, jamais rognés) ; badge : couleur du badge (sinon tete)."""
    if etat not in ETATS_AGENT:
        etat = ARRETE
    cr.save()
    cr.set_line_width(1.5)
    cr.set_line_cap(1)  # cairo.LINE_CAP_ROUND
    cr.set_source_rgb(*_rgb(tete))
    # Corps : rectangle 11,5 x 8,5 à (2,25 ; 5,25), rayon 2.
    x, y, w, h, r = 2.25, 5.25, 11.5, 8.5, 2
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.stroke()
    # Antenne.
    cr.move_to(8, 2.25)
    cr.line_to(8, 5.25)
    cr.stroke()
    # Yeux : disques r = 1, ou fermés (deux traits de 2 px) à l'arrêt.
    if etat == ARRETE:
        for x0 in (4.75, 9.25):
            cr.move_to(x0, 9.5)
            cr.line_to(x0 + 2, 9.5)
            cr.stroke()
    else:
        for cx in (5.75, 10.25):
            cr.arc(cx, 9.5, 1, 0, 2 * math.pi)
            cr.fill()
    if etat in (PRET, ARRETE):
        cr.restore()
        return
    # Détourage : disque de la couleur du fond de la case.
    cr.set_source_rgb(*_rgb(fond))
    cr.arc(13.5, 13.5, RAYON_DETOURAGE, 0, 2 * math.pi)
    cr.fill()
    couleur = _rgb(badge or tete)
    cr.set_source_rgb(*couleur)
    if etat == TRAVAIL:
        cr.arc(13.5, 13.5, RAYON_BADGE, 0, 2 * math.pi)
        cr.fill()
    elif etat == CONFIRMER:
        cr.set_line_width(1.5)
        cr.arc(13.5, 13.5, RAYON_ANNEAU, 0, 2 * math.pi)
        cr.stroke()
    elif etat == INFLUENCE:
        cr.arc(13.5, 13.5, RAYON_BADGE, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*_rgb(fond))
        cr.set_line_width(1.1)
        cr.move_to(13.5, 11.4)
        cr.line_to(13.5, 13.8)
        cr.stroke()
        cr.arc(13.5, 15.5, 0.65, 0, 2 * math.pi)
        cr.fill()
    elif etat == PAUSE:
        cr.rectangle(11.4, 11, 1.6, 5)
        cr.rectangle(14, 11, 1.6, 5)
        cr.fill()
    cr.restore()


def dessiner_badge_influence(cr, cx, cy, r, couleur, fond):
    """Badge « influencé » seul (§6 : disque percé d'un « ! »), centré en (cx, cy),
    de rayon r (3 sur la tête de 16 px) ; le « ! » prend la couleur du fond.
    Pour écrire « influencé » ailleurs que sur la tête (panneau de l'agent)."""
    k = r / 3
    cr.save()
    cr.set_source_rgb(*_rgb(couleur))
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*_rgb(fond))
    cr.set_line_width(1.1 * k)
    cr.set_line_cap(1)
    cr.move_to(cx, cy - 1.9 * k)
    cr.line_to(cx, cy + 0.2 * k)
    cr.stroke()
    cr.arc(cx, cy + 1.75 * k, 0.6 * k, 0, 2 * math.pi)
    cr.fill()
    cr.restore()
