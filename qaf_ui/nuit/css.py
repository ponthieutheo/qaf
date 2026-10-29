"""CSS GTK 3 des écrans QAF, généré à partir des jetons (sans GTK).

Tout est limité aux fenêtres de classe « qaf-fenetre » (et aux infobulles de
nos processus) : rien ne déborde sur d'autres fenêtres, et le thème de Qubes
n'est jamais modifié (le fournisseur est chargé dans notre processus
seulement, règle n°1). Propriétés GTK 3.24 uniquement : une propriété inconnue
fait échouer tout le chargement (vérifié par le test GTK sous xvfb).

Vocabulaire de classes (docs/DESIGN-SESSION.md §3, §4, §11) ; les composants
de composants.py les posent, un écran peut aussi les poser lui-même :

  fenêtres   window.qaf-fenetre          fond SURFACE, texte T1 13 px
             window.qaf-fenetre.qaf-surface  + bordure 2 px BORD (carte de l'agent : POPUP)
  texte      .qaf-c3 .qaf-tnum            couleur T3, chiffres tabulaires
             .qaf-titre .qaf-sous-titre   20 px Inter Display 500 SIGNATURE ; 15 px 500
             .qaf-secondaire              T2 13 px
             .qaf-section                en-tête de section 11 px 500 T3, 28 px dont 10 au-dessus
             .qaf-nom .qaf-erreur         nom en 500 ; message d'erreur DANGER
  structure  .qaf-entete .qaf-pied        bandeau de 48 px à filet bas ; pied à filet haut
  listes     list.qaf-liste              lignes 32 px, rayon 4, qu'on lit sans y choisir
             .qaf-ligne .qaf-detail       contenu d'une ligne ; détail 11 px T3 à droite
  contrôles  button.qaf-bouton + .qaf-principal / .qaf-secondaire / .qaf-danger
             .qaf-icone                  icône symbolique (ICONE ; T3 désactivée)
"""

from . import jetons as j


def _px(n):
    return f"{n}px"


def approche(taille):
    """letter-spacing en px pour une des quatre tailles (§3)."""
    return f"{round(j.APPROCHE[taille] * taille, 3)}px"


def _texte(taille, graisse=None):
    g = f" font-weight: {graisse};" if graisse else ""
    return f"font-size: {_px(taille)}; letter-spacing: {approche(taille)};{g}"


def accents_ecran(e):
    """Graisse des accents d'un écran QAF (panneau de l'agent),
    limitée à sa fenêtre `e` : libellés de bouton, titres de 15 px (confirmation,
    en-tête de fiche) et titre d'écran de 20 px en Inter (pas Display, trop
    maigre sans compositeur), tous en GRAISSE_ACCENT (jetons.py)."""
    g = j.GRAISSE_ACCENT
    return [
        f"{e} button.qaf-bouton label, {e} label.qaf-sous-titre {{ font-weight: {g}; }}",
        f"{e} label.qaf-titre {{ font-family: {j.POLICE}; font-weight: {g}; }}",
    ]


def generer():
    f = ".qaf-fenetre"
    r = j.RAYON
    focus = (f"outline-color: {j.T2}; outline-style: solid; outline-width: {_px(j.FOCUS_LARGEUR)}; "
             f"outline-offset: {_px(j.FOCUS_DECALAGE)}; -gtk-outline-radius: {_px(r + j.FOCUS_DECALAGE)};")
    sans_relief = "background-image: none; box-shadow: none; text-shadow: none;"
    transition = f"transition: background-color {j.TRANSITION_MS}ms ease-out;"
    corps_ligne = j.LIGNE - 2 * j.FILET_LARGEUR  # min-height GTK 3 = boîte de contenu, sans le contour
    regles = [
        # --- fenêtres (§4 : rayon 0, ni ombre ni transparence) ---------------------
        f"window{f} {{ background-color: {j.SURFACE}; color: {j.T1}; border: none; "
        f"border-radius: {_px(j.RAYON_FENETRE)}; box-shadow: none; }}",
        f"window{f}.qaf-surface {{ border: {_px(j.BORD_LARGEUR)} solid {j.BORD}; }}",
        # Avec un compositeur (KWin) : coins de 8 px dessinés par GTK (la bordure suit le
        # coin), seulement si la surface a un visuel RGBA (composants.SurfaceAF) ; sinon
        # coins droits.
        f"window{f}.qaf-surface.qaf-arrondi {{ border-radius: {_px(j.RAYON_SURFACE_COMPOSEE)}; }}",
        f"{f}, {f} * {{ font-family: {j.POLICE}; }}",
        f"{f} label {{ color: {j.T1}; {_texte(j.TAILLE_CORPS, j.GRAISSE)} text-shadow: none; }}",
        # Focus visible partout : anneau 2 px T2, décalé de 2 px.
        f"{f} *:focus {{ {focus} }}",
        # --- typographie (§3) ----------------------------------------------------------
        f"{f} label.qaf-c3 {{ color: {j.T3}; }}",
        f"{f} label.qaf-tnum {{ font-feature-settings: \"tnum\"; }}",
        # Direction B : le titre d'un écran est la signature violette (4,9:1 sur SURFACE).
        f"{f} label.qaf-titre {{ {_texte(j.TAILLE_TITRE, j.GRAISSE_FORTE)} font-family: {j.POLICE_TITRE}; "
        f"color: {j.SIGNATURE}; }}",
        f"{f} label.qaf-sous-titre {{ {_texte(j.TAILLE_GRANDE, j.GRAISSE_FORTE)} }}",
        f"{f} label.qaf-secondaire {{ color: {j.T2}; }}",
        f"{f} label.qaf-section {{ color: {j.T3}; {_texte(j.TAILLE_PETITE, j.GRAISSE_FORTE)} "
        f"padding: 10px 0 2px 0; min-height: 16px; }}",
        f"{f} label.qaf-nom {{ font-weight: {j.GRAISSE_FORTE}; }}",
        f"{f} label.qaf-erreur {{ color: {j.DANGER}; }}",
        # --- structure ---------------------------------------------------------------------
        f"{f} .qaf-entete {{ min-height: {_px(j.ENTETE - j.FILET_LARGEUR)}; padding: 0 {_px(j.MARGE_ECRAN)}; "
        f"border-bottom: 1px solid {j.FILET}; }}",
        f"{f} .qaf-pied {{ padding: 12px {_px(j.MARGE_ECRAN)}; border-top: 1px solid {j.FILET}; }}",
        f"{f} .qaf-pied label {{ color: {j.T3}; }}",
        f"{f} separator {{ background-color: {j.FILET}; min-height: 1px; min-width: 1px; }}",
        f"{f} scrolledwindow, {f} viewport {{ background-color: transparent; border: none; }}",
        # --- listes (§4, §7.3) : qu'on lit sans y choisir ----------------------------------
        # Marges posées en entier : adw-gtk3 (thème de dom0) met « scrolledwindow list row
        # { margin: 0 6px } » et « list { padding: 6px 0 } ».
        f"{f} list.qaf-liste {{ background-color: transparent; padding: 0; }}",
        f"{f} list.qaf-liste row {{ background-color: transparent; border-radius: {_px(r)}; "
        f"padding: 0 {_px(j.MARGE_LISTE)}; margin: 0; min-height: {_px(j.LIGNE)}; outline-style: none; "
        f"{transition} }}",
        f"{f} list.qaf-liste row:hover {{ background-color: transparent; }}",
        f"{f} .qaf-ligne label.qaf-detail {{ color: {j.T3}; {_texte(j.TAILLE_PETITE)} }}",
        f"{f} .qaf-icone {{ color: {j.ICONE}; }}",
        f"{f} image.qaf-icone {{ color: {j.ICONE}; -gtk-icon-style: symbolic; }}",
        f"{f} *:disabled .qaf-icone {{ color: {j.T3}; }}",
        # --- boutons (§11) : 32 px, rayon 4, jamais de dégradé ni d'ombre ------------------
        f"{f} button.qaf-bouton {{ {sans_relief} border-radius: {_px(r)}; min-height: {_px(corps_ligne)}; "
        f"padding: 0 16px; border: 1px solid {j.FILET}; background-color: {j.CHAMP}; color: {j.T1}; "
        f"{transition} }}",
        f"{f} button.qaf-bouton label {{ color: inherit; font-weight: {j.GRAISSE_FORTE}; }}",
        f"{f} button.qaf-bouton .qaf-icone {{ color: inherit; }}",
        f"{f} button.qaf-secondaire {{ background-color: {j.CHAMP}; color: {j.T1}; }}",
        f"{f} button.qaf-secondaire:hover {{ background-color: {j.SURVOL}; }}",
        f"{f} button.qaf-bouton:active, {f} button.qaf-bouton:checked {{ background-color: {j.CHOISI}; }}",
        # Bouton principal violet (direction B), texte BARRE (6:1).
        f"{f} button.qaf-principal {{ background-color: {j.SIGNATURE}; color: {j.BARRE}; "
        f"border-color: {j.SIGNATURE}; }}",
        f"{f} button.qaf-principal:hover {{ background-color: {j.SIGNATURE_SURVOL}; color: {j.BARRE}; "
        f"border-color: {j.SIGNATURE_SURVOL}; }}",
        f"{f} button.qaf-principal:active {{ background-color: {j.SIGNATURE}; color: {j.BARRE}; }}",
        f"{f} button.qaf-danger {{ background-color: {j.CHAMP}; color: {j.DANGER}; }}",
        f"{f} button.qaf-danger:hover {{ background-color: {j.SURVOL}; }}",
        f"{f} button.qaf-bouton:disabled {{ background-color: {j.CHAMP}; color: {j.T3}; border-color: {j.FILET}; }}",
        f"{f} button.qaf-bouton:disabled label {{ color: {j.T3}; }}",
        # --- divers ----------------------------------------------------------------------------
        f"{f} .qaf-confirmation {{ background-color: transparent; border: none; padding: 16px 0; }}",
        # Infobulles de nos processus (§5) : SURFACE, contour 1 px, marges 4 × 8, une ligne.
        # GtkTooltipWindow pose sa boîte à 6 px du bord quel que soit le CSS (marges
        # fixées dans son code, mesuré sous Xvfb) : le libellé complète (6 + 3 = contour
        # 1 + 8 en largeur ; 6 + 1 + ligne de 17 + 6 = 30 px de haut, comme la maquette).
        f"tooltip, tooltip.qaf-bulle {{ background-color: {j.SURFACE}; color: {j.T1}; "
        f"border: 1px solid {j.BULLE_BORD}; border-radius: 0; box-shadow: none; padding: 0; }}",
        # Le thème GTK pose « tooltip * { padding: 4px } » (Adwaita) : la boîte et le
        # libellé intérieurs s'ajoutaient aux marges (bulle de 44 px de haut, mesurée).
        f"tooltip * {{ padding: 0; margin: 0; background-color: transparent; }}",
        f"tooltip label {{ color: {j.T1}; font-family: {j.POLICE}; {_texte(j.TAILLE_CORPS)} "
        f"padding: 1px 3px 0 3px; }}",
    ]
    return "\n".join(regles) + "\n"


CSS = generer()
