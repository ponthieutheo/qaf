"""Socle des écrans QAF, sans GTK : jetons, contrastes, niveaux de confiance, CSS."""

import pathlib
import re
import subprocess
import sys

import pytest

from qaf_ui.nuit import confiance, contraste, css, icones, jetons

RACINE = pathlib.Path(__file__).parent.parent

# Bordures et titres que i3-settings-qubes donne aux fenêtres des qubes (hors dom0 :
# focus, focus inactif, sans focus). Avec les couleurs Tango des labels (qubes.xml),
# aucune ne doit approcher BORD ni COEUR_BORD : une surface QAF ne peut pas être
# imitée par un qube.
COULEURS_I3_LABELS = (
    "#bd2727", "#971f1f", "#841b1b",   # red
    "#e79e27", "#b87e1f", "#a16e1b",   # orange
    "#e7e532", "#b8b728", "#a1a023",   # yellow
    "#5ad840", "#48ac33", "#3e972c",   # green
    "#8e8e95", "#717177", "#636368",   # gray
    "#3874d8", "#2c5cac", "#275197",   # blue
    "#9f389f", "#7f2c7f", "#6f276f",   # purple
    "#333333", "#1e1e1e", "#141414",   # black
)
LABELS_TEINTES = ("red", "orange", "yellow", "green", "blue", "purple")
# Fonds d'un écran QAF : aucun ne doit ressembler à une couleur de label à teinte franche.
FONDS = ("MUR", "BARRE", "SURFACE", "CHAMP", "SURVOL", "CHOISI", "T1")


def distance_rvb(a, b):
    return sum((p - q) ** 2 for p, q in zip(contraste.rgb(a), contraste.rgb(b))) ** 0.5


def _lab(hexa):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in contraste.rgb(hexa))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ecart(a, b):
    """Écart de couleur ΔE CIE76 (0 = identiques ; ~2 à peine visible)."""
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** 0.5


def test_import_sans_gtk():
    code = ("import sys; import qaf_ui.nuit, qaf_ui.nuit.css, qaf_ui.nuit.confiance, qaf_ui.donnees; "
            "print('gi' in sys.modules, 'qubesadmin' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", code], cwd=RACINE, capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "False False"


def test_contraste_formule():
    assert contraste.ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contraste.ratio("#777777", "#777777") == pytest.approx(1.0)
    assert contraste.ratio("#ECEAE6", "#18181A") == pytest.approx(14.8, abs=0.05)  # mesure de référence (DESIGN-SESSION §2)
    with pytest.raises(ValueError):
        contraste.rgb("#12345")


@pytest.mark.parametrize("nom,texte,fond", jetons.PAIRES_TEXTE)
def test_paires_texte_au_moins_4_5(nom, texte, fond):
    assert contraste.ratio(texte, fond) >= contraste.TEXTE_MIN, nom


@pytest.mark.parametrize("nom,trait,fond", jetons.PAIRES_GRAPHIQUES)
def test_focus_au_moins_3(nom, trait, fond):
    assert contraste.ratio(trait, fond) >= contraste.GRAPHIQUE_MIN, nom


def test_couleurs_du_css_sont_des_jetons():
    # Toute couleur du CSS vient de jetons.py, sans exception.
    valeurs = {v.upper() for v in jetons.valeurs().values()}
    for couleur in set(re.findall(r"#[0-9A-Fa-f]{6}", css.CSS)):
        assert couleur.upper() in valeurs, couleur


def test_paires_declarees_couvrent_le_css():
    # Chaque (color, background-color) posé ensemble dans une règle est une paire mesurée.
    paires = {(t.upper(), f.upper()) for _n, t, f in jetons.PAIRES_TEXTE}
    for corps in re.findall(r"\{([^}]*)\}", css.CSS):
        c = re.search(r"(?<![-\w])color:\s*(#[0-9A-Fa-f]{6})", corps)
        b = re.search(r"background-color:\s*(#[0-9A-Fa-f]{6})", corps)
        if c and b:
            assert (c.group(1).upper(), b.group(1).upper()) in paires, corps


# --- niveaux de confiance (DA-NUIT, jamais thémables) ---------------------

def test_label_vers_niveau_regle_da_nuit():
    attendu = {"red": "Risqué", "orange": "Risqué", "yellow": "Normal", "gray": "Normal",
               "blue": "Fiable", "green": "Fiable", "purple": "Fiable", "black": "Coffre"}
    assert {label: confiance.niveau(label) for label in attendu} == attendu
    for inconnu in (None, "", "rose", "RED", "<b>red</b>"):
        assert confiance.niveau(inconnu) == confiance.INCONNU


def test_formes_et_symboles():
    assert [confiance.pastille(x).symbole for x in ("red", "yellow", "blue", "black")] == ["▲", "●", "■", "◆"]
    assert [confiance.pastille(x).forme for x in ("orange", "gray", "green", "black", None)] == \
        ["triangle", "rond", "carre", "losange", "trait"]
    assert confiance.pastille("blue").mot == "Fiable"


@pytest.mark.parametrize("label", sorted(confiance.COULEURS_ECRAN))
def test_pastille_visible_sur_toutes_les_surfaces(label):
    p = confiance.pastille(label)
    trait = p.lisere or p.couleur  # le noir se lit par son liseré clair
    for fond in (jetons.SURFACE, jetons.CHAMP, jetons.SURVOL, jetons.CHOISI):
        assert contraste.ratio(trait, fond) >= contraste.GRAPHIQUE_MIN, (label, fond)


def test_teinte_du_label_conservee():
    # La couleur affichée garde la teinte Qubes (canal dominant identique).
    for label, qubes in confiance.COULEURS_QUBES.items():
        if label in ("black", "gray"):
            continue
        a, b = contraste.rgb(qubes), contraste.rgb(confiance.COULEURS_ECRAN[label])
        assert a.index(max(a)) == b.index(max(b)), label


# --- CSS GTK 3 --------------------------------------------------------------

PROPRIETES_GTK3 = {
    "background-color", "background-image", "border", "border-top", "border-bottom", "border-color",
    "border-radius", "box-shadow", "text-shadow", "color", "caret-color", "-gtk-secondary-caret-color",
    "font-family", "font-size", "font-weight", "font-feature-settings", "letter-spacing", "padding", "margin",
    "margin-bottom", "min-width", "min-height", "outline-color", "outline-style", "outline-width",
    "outline-offset", "-gtk-outline-radius", "-gtk-icon-style", "transition", "opacity",
}


def test_css_gtk3_et_limite_aux_fenetres_qaf():
    texte = css.CSS
    assert texte.count("{") == texte.count("}")
    for selecteurs, corps in re.findall(r"([^{}]+)\{([^}]*)\}", texte):
        for s in selecteurs.split(","):
            s = s.strip()
            assert s.startswith((".qaf-fenetre", "window.qaf-fenetre", "tooltip")), s
        proprietes = {d.split(":")[0].strip() for d in corps.split(";") if d.strip()}
        assert proprietes <= PROPRIETES_GTK3, proprietes - PROPRIETES_GTK3
    assert "Inter" in texte and jetons.SURFACE in texte and "Inter Display" in texte
    # Le violet ne sert qu'au badge « au travail » de la barre (dessin Cairo) : jamais dans le CSS.
    assert jetons.AGENT not in texte
    assert "*:focus" in texte and jetons.T2 in texte


def test_css_sans_classe_des_ecrans_retires():
    # Menu QAF, Espaces, Réglages, barre et thèmes retirés (29/10) : leurs règles aussi.
    for classe in ("qaf-requete", "qaf-ariane", "qaf-curseur", "qaf-badge", "qaf-constat", "qaf-choix",
                   "qaf-bascule", "qaf-repli", "qaf-champ", "qaf-compteur", "qaf-ligne-riche", "row:selected"):
        assert classe not in css.CSS, classe


def test_fonds_loin_des_labels_de_qubes():
    # Un écran QAF ne doit pas passer pour une fenêtre de qube : aucun fond n'approche
    # (ΔE < 20) une couleur de label à teinte franche.
    for fond in FONDS:
        for label in LABELS_TEINTES:
            assert ecart(getattr(jetons, fond), confiance.COULEURS_QUBES[label]) >= 20, (fond, label)


# --- direction de la session (DESIGN-SESSION §2 à §4) ------------------------

def test_valeurs_du_paragraphe_2():
    attendu = {"MUR": "#232326", "BARRE": "#0E0E10",
               "SURFACE": "#202326", "CHAMP": "#292C30", "SURVOL": "#2E3236", "CHOISI": "#3B4045",
               "FILET": "#33373B", "FILET_FORT": "#4D5257", "BORD": "#BCC2C8", "T1": "#FCFCFC",
               "T2": "#C8CDD2", "T3": "#A1A9B1", "ICONE": "#BDC3C7", "AGENT": "#C4B5FD",
               "ATTENTION": "#9D7BF4", "DANGER": "#F09A8A", "SUCCES": "#8FD3A3",
               # Direction B (29/10, B-affirme-maquette.svg).
               "SIGNATURE": "#9D7BF4", "SIGNATURE_SURVOL": "#B195F6", "SIGNATURE_FOND": "#2A2438",
               "LABEL_AGENT": "#75507B"}
    assert {k: getattr(jetons, k) for k in attendu} == attendu


def test_echelle_typographique_et_geometrie():
    # Quatre tailles, deux graisses ; grille de 4 ; un seul rayon intérieur, 0 dehors.
    assert jetons.TAILLES == (11, 13, 15, 20) and (jetons.GRAISSE, jetons.GRAISSE_FORTE) == (400, 500)
    assert (jetons.RAYON, jetons.RAYON_FENETRE, jetons.BORD_LARGEUR, jetons.FOCUS_LARGEUR,
            jetons.FOCUS_DECALAGE) == (4, 0, 2, 2, 2)
    for nom in ("LIGNE", "ENTETE", "SECTION", "MARGE_LISTE", "MARGE_ECRAN", "ICONE_TAILLE", "ICONE_ECART",
                "RETRAIT_SEPARATEUR"):
        assert getattr(jetons, nom) % jetons.GRILLE == 0, nom
    assert jetons.RETRAIT_SEPARATEUR == jetons.MARGE_LISTE + jetons.ICONE_TAILLE + jetons.ICONE_ECART
    texte = css.CSS
    tailles = {int(t) for t in re.findall(r"font-size: (\d+)px", texte)}
    assert tailles <= set(jetons.TAILLES), tailles
    graisses = {int(g) for g in re.findall(r"font-weight: (\d+)", texte)}
    assert graisses <= {400, 500}, graisses
    rayons = {int(r) for r in re.findall(r"border-radius: (\d+)px", texte)}
    # 8 : surface QAF avec compositeur seulement.
    assert rayons <= {0, 4, 8}, rayons
    assert re.findall(r"([^\n]*)border-radius: 8px", texte) == ["window.qaf-fenetre.qaf-surface.qaf-arrondi { "]
    assert "999px" not in texte and "letter-spacing: 1px" not in texte  # ni pilules ni capitales espacées


def test_seuils_de_la_direction():
    for texte, fond, mini in jetons.SEUILS_DIRECTION:
        assert contraste.ratio(getattr(jetons, texte), getattr(jetons, fond)) >= mini, (texte, fond)


def test_bord_loin_de_toute_couleur_de_label():
    # Une surface QAF à bord ivoire ne peut pas être imitée par un qube : BORD est à plus
    # de 60 (distance RVB) de chaque couleur de label (Tango de qubes.xml, bordures de
    # i3-settings-qubes hors dom0).
    for couleur in COULEURS_I3_LABELS + tuple(confiance.COULEURS_QUBES.values()):
        assert distance_rvb(jetons.BORD, couleur) > 60, couleur
    assert contraste.ratio(jetons.BORD, jetons.APPLI_SOMBRE) >= 3


def test_direction_b_trois_violets_trois_roles():
    # SIGNATURE : accent (titre d'écran, bouton principal, curseur de saisie).
    texte = css.generer()
    assert re.search(r"label\.qaf-titre \{[^}]*color: " + jetons.SIGNATURE, texte)
    assert re.search(r"button\.qaf-principal \{ background-color: " + jetons.SIGNATURE
                     + r"; color: " + jetons.BARRE, texte)
    # « À confirmer » (SIGNATURE) et « au travail » (lavande) : teintes distinctes
    # ET formes distinctes (anneau / disque), avec leur mot.
    assert jetons.ATTENTION == jetons.SIGNATURE != jetons.AGENT
    assert distance_rvb(jetons.ATTENTION, jetons.AGENT) > 60
    assert icones.COULEUR_BADGE[icones.TRAVAIL] == "AGENT" and icones.COULEUR_BADGE[icones.CONFIRMER] == "ATTENTION"
    # SIGNATURE se tient sur tous les fonds sombres où il sert de trait (3:1), en texte sur SURFACE (4,5:1).
    for fond in ("SURFACE", "BARRE", "CHAMP", "SURVOL", "CHOISI", "SIGNATURE_FOND"):
        assert contraste.ratio(jetons.SIGNATURE, getattr(jetons, fond)) >= 3, fond
    assert contraste.ratio(jetons.SIGNATURE, jetons.SURFACE) >= 4.5


def test_label_violet_de_qubes_jamais_en_texte_ni_en_trait_sur_fond_sombre():
    # #75507B fait moins de 3:1 sur chaque fond sombre : seulement en aplat (logo,
    # sous un texte clair) ou sur la carte claire du Cœur du système.
    for fond in jetons.FONDS_SOMBRES:
        assert contraste.ratio(jetons.LABEL_AGENT, getattr(jetons, fond)) < 3, fond
    from pathlib import Path
    from qaf_ui import palette
    for feuille in (css.generer(), palette.CSS):
        assert jetons.LABEL_AGENT not in feuille
    # Aucune feuille de style ni dessin d'écran ne le pose en couleur de texte, de trait ou de bord
    # (les tables de labels de Qubes le gardent : c'est la couleur du label « purple »).
    usage = re.compile(r"(color|border[-a-z]*|caret-color|set_source_rgb)\W[^;\n]*(#75507B|LABEL_AGENT)", re.I)
    racine = Path(__file__).resolve().parent.parent / "qaf_ui"
    for f in [*racine.glob("*.py"), *racine.glob("ecrans/*.py"), *racine.glob("nuit/*.py")]:
        if f.name in ("jetons.py", "palette_gtk.py"):
            continue  # définition ; aplat du logo de la palette
        assert not usage.search(f.read_text(encoding="utf-8")), f.name


def test_fenetre_du_coeur_direction_b():
    """Fenêtre de confirmation du Doorman (qaf_ui/fenetre.py, lue comme texte : pas de GTK) :
    toutes ses couleurs viennent des jetons figés, sans orange ni rouge de label."""
    src = (RACINE / "qaf_ui" / "fenetre.py").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", src), "couleur en dur dans fenetre.py"
    assert "LABEL_AGENT" not in src and "COEUR_CARTE" not in src
    for jeton in ("COEUR_VOILE", "COEUR_FOND", "COEUR_BORD", "COEUR_CHAMP", "COEUR_T1", "COEUR_T2", "COEUR_T3",
                  "COEUR_FILET", "COEUR_FILET_FORT"):
        assert jeton in jetons.COULEURS and f"j.{jeton}" in src, jeton
    assert "j.DANGER" in src and "j.SIGNATURE" in src
    # Bord de la carte : teinte d'aucun label (comme BORD), donc pas un liseré de qube.
    for couleur in COULEURS_I3_LABELS + tuple(confiance.COULEURS_QUBES.values()):
        assert distance_rvb(jetons.COEUR_BORD, couleur) > 60, couleur
    # Alerte : DANGER, loin de l'orange des labels (plus d'orange).
    assert distance_rvb(jetons.DANGER, confiance.COULEURS_QUBES["orange"]) > 60
    # Repères de sécurité gardés à l'écran (textes posés par fenetre.py).
    for texte in ('t("conf.fen.refuser")', 't("conf.fen.autoriser")', "self.vue.entete", "self.vue.sous_entete",
                  "self.vue.alerte", "self.vue.jeton", "vue.gravite", "vue.reversible",
                  't("conf.fen.minuteur"', "self.etat.message"):
        assert texte in src, texte


def test_violet_jamais_un_fond_ni_un_bord():
    # Le violet de l'agent reste loin du label violet (#9f389f) et n'est jamais un jeton de fond.
    assert ecart(jetons.AGENT, "#9f389f") > 30
    assert "AGENT" not in FONDS and "AGENT_FOND" not in jetons.valeurs()


def test_icones_au_trait():
    # Seulement les icônes des écrans de la brique agent (fil, carte de l'agent).
    assert set(icones.TRACES) == {"coche", "fermer", "plus", "terminal", "question", "point"}
    doc = icones.svg("coche", "#B4B1AB")
    # Trait 1,5 px à 16 (2,25 dans la grille de 24, DESIGN-SESSION-V2 §3.1).
    assert 'stroke="#B4B1AB"' in doc and 'stroke-width="2.25"' in doc and 'width="16"' in doc
    with pytest.raises(KeyError):
        icones.svg("<script>", "#FFFFFF")
    with pytest.raises(ValueError):
        icones.svg("coche", 'red" onload="x')
    assert set(icones.ETATS_AGENT) == {"pret", "travail", "confirmer", "influence", "pause", "arrete"}
    assert icones.COULEUR_BADGE["travail"] == "AGENT"
    assert (icones.RAYON_BADGE, icones.RAYON_ANNEAU, icones.RAYON_DETOURAGE) == (3.5, 2.75, 5)


def test_composants_sans_balisage_pango():
    source = (RACINE / "qaf_ui" / "nuit" / "composants.py").read_text(encoding="utf-8")
    assert "set_markup" not in source and "use_markup=True" not in source
    assert "set_use_markup(True)" not in source
    assert 'require_version("Gtk", "3.0")' in source and "WebKit" not in source


def test_sortes_avant_le_label():
    # Classe d'abord : un modèle, un qube système, le cœur du système ne prennent pas le niveau du label.
    assert confiance.sorte("TemplateVM", "debian-13-xfce") == confiance.MODELE
    assert confiance.sorte("AdminVM", "dom0") == confiance.COEUR
    assert confiance.sorte("AppVM", "sys-net") == confiance.SYSTEME
    assert confiance.sorte("AppVM", "mon-vpn", fournit_reseau=True) == confiance.SYSTEME
    assert confiance.sorte("AppVM", "loisirs") is None and confiance.sorte("DispVM", "disp12") is None
    p = confiance.pastille("black", confiance.MODELE)
    assert (p.mot, p.forme, p.symbole, p.couleur, p.lisere) == ("Modèle", "carre-creux", "□", "#A3ABB9", None)
    assert confiance.pastille("black").mot == "Coffre"  # sans sorte : la règle du label
    formes = {confiance.pastille(None, s).forme for s in confiance.SORTES}
    assert len(formes) == 3 and not formes & {confiance.pastille(x).forme for x in ("red", "yellow", "blue", "black")}
    assert confiance.COULEUR_SORTE not in confiance.COULEURS_ECRAN.values()


def test_chaque_module_gtk_fixe_gtk3_avant_gi_repository():
    # Parcours 4 : un écran importait gi.repository sans require_version ; dom0 a
    # aussi GTK 4, que PyGObject chargeait, et l'écran tombait à l'ouverture.
    # Les tests sous xvfb n'ont que GTK 3 et ne le voyaient pas.
    fautifs = []
    for f in sorted((RACINE / "qaf_ui").rglob("*.py")):
        lignes = f.read_text(encoding="utf-8").splitlines()
        imports = [i for i, l in enumerate(lignes) if "from gi.repository import" in l]
        if not imports:
            continue
        version = [i for i, l in enumerate(lignes) if 'gi.require_version("Gtk", "3.0")' in l]
        if not version or version[0] > imports[0]:
            fautifs.append(str(f.relative_to(RACINE)))
    assert fautifs == []


def test_un_seul_anneau_de_focus_dans_le_socle():
    """ECRANS-QAF §7 bis : l'anneau (2 px T2, 2 px d'écart) n'est écrit que par le socle ;
    aucun écran n'a de règle :focus à lui."""
    assert css.generer().count(":focus") == 1
    assert ":focus" not in (RACINE / "qaf_ui" / "ecrans" / "agent.py").read_text(encoding="utf-8")
