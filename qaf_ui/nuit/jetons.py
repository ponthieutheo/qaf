"""Jetons des écrans QAF (docs/DESIGN-SESSION.md §2 à §4), sans GTK.

Seule source des couleurs, tailles, rayons et épaisseurs des écrans QAF : le
CSS GTK est généré à partir d'ici (css.py), les dessins Cairo (tête de
l'agent, icônes) lisent « jetons.X » au moment de dessiner, et les tests
mesurent les contrastes des paires déclarées dans _PAIRES. Ajouter une couleur
ici, jamais en dur dans un écran.

Direction B « affirmée / signature » (29/10, maquette
« B-affirme ») : neutres de Breeze sombre et
un seul accent, le violet SIGNATURE, posé partout où QAF parle (titre d'un
écran, bouton principal, contour de la carte de l'agent, tête de l'agent,
jauges, curseur de saisie, logo). Trois violets, trois rôles, jamais échangés :
SIGNATURE (#9D7BF4) accent et « à confirmer » ; AGENT (lavande #C4B5FD)
« au travail » seulement ; LABEL_AGENT (#75507B, le label violet de Qubes)
en aplat sous un texte clair, jamais en texte ni en trait fin sur fond sombre
(2,4:1). « À confirmer » et « au travail » se distinguent par la forme
(anneau / disque) et le mot, pas seulement par la teinte.

Correspondance avec les anciens noms (DESIGN-SESSION §2.1) : FOND -> MUR (le
fond des écrans QAF est SURFACE), SURFACE_1 -> SURFACE, SURFACE_2 et CHAMP_FOND
-> CHAMP, SELECTION et ACTIF -> CHOISI, BORDURE et FILET_BARRE -> FILET,
BORDURE_CHAMP -> FILET_FORT, BORDURE_FENETRE -> BORD, TEXTE/TEXTE_2/TEXTE_3 ->
T1/T2/T3, TEXTE_BOUTON -> T1, FOCUS -> T2, PRINCIPAL_FOND -> T1,
PRINCIPAL_TEXTE -> BARRE. Supprimés : AGENT_FOND, SUCCES_FOND, DANGER_FOND,
DANGER_BORDURE, BORDURE_TOUCHE (plus de fonds teintés, de pilules ni de
touches dessinées).
"""

# --- couleurs neutres (§2) -----------------------------------------------------
# 28/10 (« les mêmes couleurs que Plasma, plus d'orange ») : neutres de
# Breeze sombre (le style Plasma de la barre), bordure grise au lieu d'ivoire,
# « à confirmer » au bleu d'accent de Plasma au lieu de l'ambre.
MUR = "#232326"          # fond derrière une fenêtre dans les rendus hors écran (tools/rendu_ui.py)
BARRE = "#0E0E10"        # texte du bouton principal (sur SIGNATURE)
SURFACE = "#202326"      # écrans QAF, carte de l'agent, infobulles
CHAMP = "#292C30"        # boutons, champs, piste du curseur, panneau latéral
SURVOL = "#2E3236"       # survol d'un élément non sélectionnable (bouton, case de barre)
CHOISI = "#3B4045"       # ligne sélectionnée (clavier et souris), case ouverte, appuyé
FILET = "#33373B"        # séparateurs 1 px, filet de la barre, contour des boutons
FILET_FORT = "#4D5257"   # contour 1 px d'un champ de saisie
BORD = "#BCC2C8"         # bordure 2 px d'une surface QAF de premier niveau
BULLE_BORD = "#4D5257"   # contour 1 px d'une infobulle (§5)
T1 = "#FCFCFC"           # texte principal, coche, curseur : seul accent d'interface
T2 = "#C8CDD2"           # texte secondaire, anneau de focus, remplissage du curseur
T3 = "#A1A9B1"           # tertiaire : chevrons, invites, en-têtes de section (sur CHOISI : T2)
ICONE = "#BDC3C7"        # icônes symboliques au repos

# --- couleurs d'accent et d'état ------------------------------------------------
# Direction B (29/10) : violet vif partout, états gardés distincts par la forme.
SIGNATURE = "#9D7BF4"    # accent de QAF : titres, bouton principal, contour de la carte de l'agent
SIGNATURE_SURVOL = "#B195F6"  # bouton principal survolé (SIGNATURE + 20 % de blanc)
SIGNATURE_FOND = "#2A2438"    # fond des boutons carrés de la carte de l'agent (« + », « >_ »)
LABEL_AGENT = "#75507B"  # label violet de Qubes (sys-agent) : aplat sous T1 seulement, jamais texte
AGENT = "#C4B5FD"        # lavande : « au travail » (disque), rien d'autre ; jamais dans le CSS
ATTENTION = "#9D7BF4"    # « à confirmer » (anneau) : SIGNATURE (bleu de Plasma le 28/10, ambre avant)
DANGER = "#F09A8A"       # badge « influencé », texte d'un bouton destructeur
SUCCES = "#8FD3A3"       # texte de réussite dans les écrans QAF (jamais un fond)
# Encre des icônes de qubes à label sans teinte, dans la barre (DESIGN-SESSION-V2 §3.5) :
# jamais ICONE (distance RVB ≥ 60), sinon une icône de vault passerait pour un widget de dom0.
ENCRE_NOIR = "#7A7873"   # label noir
ENCRE_GRIS = "#8E8E95"   # label gris : le gris de son label (i3 de Qubes)
# Fenêtre de confirmation du Cœur du système (qaf_ui/fenetre.py, direction B du
# 29/10) : neutres de Nuit recopiés ici, à part, pour qu'elle reste figée. Alerte
# = DANGER ; bouton par défaut (Refuser) = SIGNATURE ; bord gris COEUR_BORD,
# teinte d'aucun label (tests/test_nuit.py).
COEUR_VOILE = "#0E0E10"      # voile plein écran et bande « CŒUR DU SYSTÈME »
COEUR_FOND = "#202326"       # carte
COEUR_CHAMP = "#292C30"      # blocs (flux, demande, code, alerte)
COEUR_FILET = "#33373B"
COEUR_FILET_FORT = "#4D5257"  # contour d'« Autoriser » non armé (son libellé porte le sens)
COEUR_BORD = "#BCC2C8"       # bordure 2 px de la carte
COEUR_T1 = "#FCFCFC"
COEUR_T2 = "#C8CDD2"
COEUR_T3 = "#A1A9B1"
# Fond d'une appli sombre typique d'un qube (#1e1e1e) : BORD doit s'y détacher (3:1).
APPLI_SOMBRE = "#1E1E1E"

# --- typographie (§3) ------------------------------------------------------------
# Inter seule dans dom0 ; Inter Display pour le titre d'un écran (un par écran).
# Repli CJK (chinois) : Noto Sans CJK SC, présente dans dom0 ; Inter n'a aucun
# idéogramme, Pango prend chaque glyphe manquant dans la police suivante de la liste
# (sans elle, fontconfig choisirait au hasard, ou des carrés).
POLICE = 'Inter, "Noto Sans", "Noto Sans CJK SC", Cantarell, sans-serif'
POLICE_TITRE = '"Inter Display", Inter, "Noto Sans", "Noto Sans CJK SC", sans-serif'
# JetBrains Mono seulement dans tpl-agent (§14).
POLICE_CODE = '"DejaVu Sans Mono", "Noto Sans Mono CJK SC", "Noto Sans CJK SC", monospace'
# Quatre tailles : la hiérarchie se fait par la couleur (T1/T2/T3) et la graisse.
TAILLE_PETITE = 11   # en-têtes de section, détails à droite
TAILLE_CORPS = 13    # tout le reste
TAILLE_GRANDE = 15   # sous-titre, titre d'une confirmation
TAILLE_TITRE = 20    # titre d'un écran QAF
TAILLES = (TAILLE_PETITE, TAILLE_CORPS, TAILLE_GRANDE, TAILLE_TITRE)
# Approche (letter-spacing), en em (§3) ; css.approche(taille) la donne en px.
APPROCHE = {11: 0.005, 13: -0.003, 15: -0.011, 20: -0.017}
GRAISSE = 400
GRAISSE_FORTE = 500  # 600 et 700 ne servent plus
# Libellés de bouton, titres d'écran et de confirmation des écrans QAF : le Medium
# (500) de la maquette, rendu par Chrome, porte autant d'encre que le 600 d'Inter
# rendu par FreeType et cairo sans compositeur (mesuré sur « Annuler » et
# « Éteindre l'ordinateur ? », DESIGN-SESSION, annexe des écrans). Même intention
# (« Medium »), graisse de rendu calibrée.
GRAISSE_ACCENT = 600
THEME_ICONES = "Papirus-Dark"  # applis des qubes ; icônes QAF : qaf_ui/nuit/icones.py

# --- grille, rayons, épaisseurs (§4) ----------------------------------------------
GRILLE = 4               # toute mesure est un multiple de 4 ; écarts usuels 4, 8, 12, 16, 24
RAYON = 4                # ligne choisie, bouton, champ, curseur : aucun autre rayon
RAYON_FENETRE = 0        # fenêtre de premier niveau (sans compositeur, un coin arrondi laisse un carré noir)
RAYON_SURFACE_COMPOSEE = 8  # carte de l'agent : seulement quand un compositeur tourne (KWin)
BORD_LARGEUR = 2         # bordure des surfaces QAF
FILET_LARGEUR = 1
FOCUS_LARGEUR = 2        # anneau de focus T2…
FOCUS_DECALAGE = 2       # …décalé de 2 px
TRANSITION_MS = 80       # fond des lignes et des cases ; rien d'autre ne s'anime
# Mesures communes des surfaces (§7, §8, §11).
LIGNE = 32               # ligne de liste, bouton
ENTETE = 48              # en-tête d'une surface ou d'un écran
SECTION = 28             # en-tête de section, dont 10 au-dessus
MARGE_LISTE = 8          # marge intérieure d'une liste et d'une ligne
MARGE_ECRAN = 24         # marges d'un écran QAF
ICONE_TAILLE = 16        # icône symbolique ; 32 pour le meilleur résultat
ICONE_ECART = 12         # entre l'icône et le libellé
RETRAIT_SEPARATEUR = 36  # filet aligné sur le texte : 8 + 16 + 12

# Paires (nom, texte, fond) réellement utilisées par css.py, les composants et
# les écrans : chacune doit atteindre 4,5:1 (tests/test_nuit.py). Écrites avec
# les NOMS des jetons. Toute nouvelle paire va ici.
_PAIRES = [
    ("texte sur surface", "T1", "SURFACE"),
    ("texte sur champ", "T1", "CHAMP"),
    ("texte sur survol", "T1", "SURVOL"),
    ("texte sur ligne choisie", "T1", "CHOISI"),
    ("texte sur barre", "T1", "BARRE"),
    ("secondaire sur surface", "T2", "SURFACE"),
    ("secondaire sur champ", "T2", "CHAMP"),
    ("secondaire sur survol", "T2", "SURVOL"),
    ("secondaire sur ligne choisie", "T2", "CHOISI"),
    ("secondaire sur barre", "T2", "BARRE"),
    ("tertiaire sur surface", "T3", "SURFACE"),
    ("tertiaire sur champ", "T3", "CHAMP"),
    ("tertiaire sur survol", "T3", "SURVOL"),
    ("tertiaire sur barre", "T3", "BARRE"),
    ("bouton principal", "BARRE", "SIGNATURE"),
    ("bouton principal survolé", "BARRE", "SIGNATURE_SURVOL"),
    ("titre signature sur surface", "SIGNATURE", "SURFACE"),
    ("titre signature sur barre", "SIGNATURE", "BARRE"),
    ("titre de l'agent sur son bloc", "SIGNATURE", "SIGNATURE_FOND"),
    ("secondaire sur le bloc de l'agent", "T2", "SIGNATURE_FOND"),
    ("bouton danger", "DANGER", "CHAMP"),
    ("danger sur surface", "DANGER", "SURFACE"),
    ("danger sur ligne choisie", "DANGER", "CHOISI"),
    ("danger sur barre", "DANGER", "BARRE"),
    ("en marche sur surface", "SUCCES", "SURFACE"),
    ("en marche sur champ", "SUCCES", "CHAMP"),
    ("en marche sur ligne choisie", "SUCCES", "CHOISI"),
    # Fenêtre de confirmation (qaf_ui/fenetre.py).
    ("cœur : texte", "COEUR_T1", "COEUR_FOND"),
    ("cœur : secondaire", "COEUR_T2", "COEUR_FOND"),
    ("cœur : tertiaire", "COEUR_T3", "COEUR_FOND"),
    ("cœur : texte sur bloc", "COEUR_T1", "COEUR_CHAMP"),
    ("cœur : secondaire sur bloc", "COEUR_T2", "COEUR_CHAMP"),
    ("cœur : tertiaire sur bloc", "COEUR_T3", "COEUR_CHAMP"),
    ("cœur : bande", "COEUR_T1", "COEUR_VOILE"),
    ("cœur : sous-titre de la bande", "COEUR_T3", "COEUR_VOILE"),
    ("cœur : alerte", "DANGER", "COEUR_FOND"),
    ("cœur : alerte sur bloc", "DANGER", "COEUR_CHAMP"),
    ("cœur : refuser", "COEUR_VOILE", "SIGNATURE"),
    ("cœur : refuser survolé", "COEUR_VOILE", "SIGNATURE_SURVOL"),
]
# Éléments non textuels : 3:1 au moins (WCAG 1.4.11).
_PAIRES_GRAPHIQUES = [
    ("focus sur surface", "T2", "SURFACE"),
    ("focus sur champ", "T2", "CHAMP"),
    ("focus sur ligne choisie", "T2", "CHOISI"),
    ("bord sur appli sombre", "BORD", "APPLI_SOMBRE"),
    ("icône sur surface", "ICONE", "SURFACE"),
    ("icône sur barre", "ICONE", "BARRE"),
    ("icône sur survol", "ICONE", "SURVOL"),
    ("icône choisie", "T1", "CHOISI"),
    # Barre v2 (fond SURFACE) : encres des icônes de qubes à label noir ou gris.
    ("encre de label noir sur barre", "ENCRE_NOIR", "SURFACE"),
    ("encre de label gris sur barre", "ENCRE_GRIS", "SURFACE"),
    # Direction B : contour de la carte de l'agent, curseur de saisie, remplissage du
    # curseur, bord d'un choix coché, icônes des boutons carrés de la carte.
    ("contour signature sur surface", "SIGNATURE", "SURFACE"),
    ("signature sur champ", "SIGNATURE", "CHAMP"),
    ("signature sur survol", "SIGNATURE", "SURVOL"),
    ("signature sur ligne choisie", "SIGNATURE", "CHOISI"),
    ("signature sur son bouton", "SIGNATURE", "SIGNATURE_FOND"),
    ("icône du curseur sur signature", "BARRE", "SIGNATURE"),
    ("tête du logo sur l'aplat de l'agent", "T1", "LABEL_AGENT"),
    # Fenêtre de confirmation : bord de la carte sur le voile, repère ◉ de la bande,
    # « Autoriser » armé, flèche d'un flux (neutre ou alerte), signe « ! ».
    ("cœur : bord de la carte", "COEUR_BORD", "COEUR_VOILE"),
    ("cœur : repère de la bande", "SIGNATURE", "COEUR_VOILE"),
    ("cœur : autoriser armé", "COEUR_T2", "COEUR_FOND"),
    ("cœur : flèche", "COEUR_T3", "COEUR_CHAMP"),
    ("cœur : flèche en alerte", "DANGER", "COEUR_CHAMP"),
    ("cœur : point du signe d'alerte", "COEUR_CHAMP", "DANGER"),
] + [(f"badge {etat} sur {nom_fond}", jeton, fond)
     for etat, jeton in (("au travail", "AGENT"), ("à confirmer", "ATTENTION"),
                         ("influencé", "DANGER"), ("en pause", "ICONE"), ("arrêté", "T3"))
     for nom_fond, fond in (("barre", "BARRE"), ("barre v2", "SURFACE"), ("survol", "SURVOL"),
                            ("case ouverte", "CHOISI"))]
# Fonds sombres sur lesquels LABEL_AGENT ne passe jamais 3:1 : il n'y sert
# qu'en aplat (tests/test_nuit.py le vérifie dans tout le CSS des écrans).
FONDS_SOMBRES = ("MUR", "BARRE", "SURFACE", "CHAMP", "SURVOL", "CHOISI")
# Seuils plus hauts que WCAG, tenus par la direction (§2, §15) : (texte, fond, ratio).
# §15 annonce aussi T2 ≥ 5:1 sur CHOISI, mais Nuit y mesure 4,93:1 :
# seul le 4,5:1 de WCAG (paire « secondaire sur ligne choisie ») est exigé.
SEUILS_DIRECTION = (("T3", "SURFACE", 5.2), ("BORD", "SURFACE", 7.0))

# Couleurs déclarées ici (tests : contrastes, distance aux labels de Qubes).
COULEURS = (
    "MUR", "BARRE", "SURFACE", "CHAMP", "SURVOL", "CHOISI", "FILET", "FILET_FORT", "BORD", "BULLE_BORD",
    "T1", "T2", "T3", "ICONE",
    "SIGNATURE", "SIGNATURE_SURVOL", "SIGNATURE_FOND", "LABEL_AGENT",
    "AGENT", "ATTENTION", "DANGER", "SUCCES", "ENCRE_NOIR", "ENCRE_GRIS",
    "APPLI_SOMBRE", "COEUR_VOILE", "COEUR_FOND", "COEUR_CHAMP",
    "COEUR_FILET", "COEUR_FILET_FORT", "COEUR_BORD", "COEUR_T1", "COEUR_T2", "COEUR_T3",
)


def valeurs():
    """Couleurs {nom du jeton: "#RRGGBB"}."""
    g = globals()
    return {k: g[k] for k in COULEURS}


def _paires(paires):
    v = valeurs()
    return [(n, v[t], v[f]) for n, t, f in paires]


PAIRES_TEXTE = _paires(_PAIRES)
PAIRES_GRAPHIQUES = _paires(_PAIRES_GRAPHIQUES)
