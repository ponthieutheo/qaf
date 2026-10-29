"""Contraste WCAG 2.x entre deux couleurs « #RRGGBB » (sans GTK).

Sert à trois endroits : les tests vérifient les paires de jetons déclarées
dans jetons.PAIRES_TEXTE, la pastille de confiance est vérifiée à 3:1
(élément non textuel, WCAG 1.4.11), et tools/rendu_ui.py mesure les pixels
d'un rendu hors écran avec la même formule.
"""

TEXTE_MIN = 4.5  # texte, y compris les petits libellés (DA-NUIT)
GRAPHIQUE_MIN = 3.0  # formes et contours porteurs de sens


def rgb(hexa):
    h = hexa.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"couleur invalide : {hexa!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(couleur):
    """Luminance relative (WCAG) d'une couleur hexadécimale ou d'un triplet 0-255."""
    r, g, b = rgb(couleur) if isinstance(couleur, str) else couleur

    def canal(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
