"""Niveau de confiance d'un espace, d'après son label Qubes (sans GTK).

Règle de docs/DA-NUIT.md, « jamais thémable » :
  rouge, orange -> Risqué ▲ ; jaune, gris -> Normal ● ;
  bleu, vert, violet -> Fiable ■ ; noir -> Coffre ◆.
Label inconnu ou absent -> Inconnu (trait), jamais un niveau rassurant.

La CLASSE passe avant le label (décision du 25/10) : un modèle (TemplateVM),
le cœur du système (dom0) et les qubes système (sys-*, provides_network) ne
sont pas des espaces de l'utilisateur. Leur label noir ou rouge ne dit rien de leur
confiance (un modèle noir n'est pas un Coffre) : ils prennent une « sorte »
écrite en clair (Modèle, Système, Cœur du système), dessinée en creux et en
gris neutre, jamais d'une couleur de label. Le niveau tiré du label ne vaut
que pour les espaces de l'utilisateur.

La couleur affichée garde la teinte du label : c'est un repère de sécurité.
Elle est seulement éclaircie pour rester visible (3:1) sur les surfaces
sombres ; le noir est dessiné noir avec un liseré clair (AUDIT-UI-1 U05).
"""

from dataclasses import dataclass

# Identifiants des niveaux et des sortes (jamais traduits : clés de FORMES, SYMBOLES…) ;
# le mot affiché vient de mot(), dans la langue de dom0 (qaf_ui/i18n.py).
RISQUE, NORMAL, FIABLE, COFFRE, INCONNU = "Risqué", "Normal", "Fiable", "Coffre", "Inconnu"
NIVEAUX = (RISQUE, NORMAL, FIABLE, COFFRE)  # ordre de tri : Risqué d'abord (AUDIT §6)
MODELE, SYSTEME, COEUR = "Modèle", "Système", "Cœur du système"
SORTES = (MODELE, SYSTEME, COEUR)
MOTS = {RISQUE: "niveau.risque", NORMAL: "niveau.normal", FIABLE: "niveau.fiable", COFFRE: "niveau.coffre",
        INCONNU: "niveau.inconnu", MODELE: "niveau.modele", SYSTEME: "niveau.systeme", COEUR: "niveau.coeur"}


def mot(niveau_ou_sorte):
    """Mot affiché d'un niveau ou d'une sorte (« Risqué » / « Risky »…)."""
    from ..i18n import t
    return t(MOTS[niveau_ou_sorte])

_NIVEAU_DU_LABEL = {
    "red": RISQUE, "orange": RISQUE,
    "yellow": NORMAL, "gray": NORMAL,
    "blue": FIABLE, "green": FIABLE, "purple": FIABLE,
    "black": COFFRE,
}
FORMES = {RISQUE: "triangle", NORMAL: "rond", FIABLE: "carre", COFFRE: "losange", INCONNU: "trait",
          # Sortes : formes en creux, distinctes des quatre niveaux pleins.
          MODELE: "carre-creux", SYSTEME: "rond-creux", COEUR: "cible-creux"}
SYMBOLES = {RISQUE: "▲", NORMAL: "●", FIABLE: "■", COFFRE: "◆", INCONNU: "–",
            MODELE: "□", SYSTEME: "○", COEUR: "◉"}

# Couleurs des labels Qubes 4.3 (qubes-core-admin, qubes/app.py), pour mémoire.
COULEURS_QUBES = {"red": "#cc0000", "orange": "#f57900", "yellow": "#edd400", "green": "#73d216",
                  "gray": "#555555", "blue": "#3465a4", "purple": "#75507b", "black": "#000000"}
# Même teinte, éclaircie pour les fonds Nuit (maquette Espaces : #EF5B4C,
# #E3B341, #5B9BFF). Vérifié à 3:1 sur les surfaces par les tests.
COULEURS_ECRAN = {"red": "#EF5B4C", "orange": "#F5924A", "yellow": "#E3B341", "green": "#6CCB5F",
                  "gray": "#9AA1AD", "blue": "#5B9BFF", "purple": "#B48ECF", "black": "#000000"}
LISERE_NOIR = "#E7E9EE"
COULEUR_INCONNUE = "#8F98A8"
COULEUR_SORTE = "#A3ABB9"  # gris du texte secondaire : neutre, ce n'est la teinte d'aucun label


def sorte(classe, nom="", fournit_reseau=False):
    """Sorte d'un qube qui n'est pas un espace de l'utilisateur, ou None (classe d'abord)."""
    if classe == "AdminVM" or nom == "dom0":
        return COEUR
    if classe == "TemplateVM":
        return MODELE
    if fournit_reseau or str(nom or "").startswith("sys-"):
        return SYSTEME
    return None


def niveau(label):
    """Niveau grand public d'un nom de label Qubes (« red », « blue »…)."""
    return _NIVEAU_DU_LABEL.get(label, INCONNU)


@dataclass(frozen=True)
class Pastille:
    niveau: str
    forme: str  # triangle, rond, carre, losange, trait
    symbole: str
    couleur: str  # remplissage, teinte du label
    lisere: str | None  # contour clair (label noir, inconnu)

    @property
    def mot(self):
        return mot(self.niveau)


def pastille(label, sorte_=None):
    """Pastille d'un espace de l'utilisateur (label), ou d'une sorte (MODELE, SYSTEME,
    COEUR) qui passe alors avant le label."""
    if sorte_ in SORTES:
        return Pastille(sorte_, FORMES[sorte_], SYMBOLES[sorte_], COULEUR_SORTE, None)
    n = niveau(label)
    if n == INCONNU:
        return Pastille(n, FORMES[n], SYMBOLES[n], COULEUR_INCONNUE, None)
    lisere = LISERE_NOIR if label == "black" else None
    return Pastille(n, FORMES[n], SYMBOLES[n], COULEURS_ECRAN[label], lisere)
