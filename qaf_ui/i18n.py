"""Langue des écrans QAF : français, anglais ou chinois simplifié (29/10 : « international »).

Mécanisme volontairement simple, sans dépendance : trois dictionnaires de
chaînes (qaf_ui/langues/fr.py, en.py et zh.py, mêmes clés, mêmes champs {nom}),
vérifiés par tests/test_i18n.py (clés identiques dans les trois langues, champs
identiques, aucune clé orpheline, aucune chaîne française en dur dans les
écrans).

La langue est celle de la locale de la session de dom0, lue UNE fois dans
l'environnement du processus, dans l'ordre de gettext (celui que suit KDE pour
la langue de Plasma) : LANGUAGE d'abord (liste « en:fr », la première langue
connue l'emporte), sauf si la locale est C ou POSIX ; puis LC_ALL, LC_MESSAGES
et LANG, comme POSIX. « en… » donne l'anglais, « zh… » le chinois simplifié
(zh_CN, zh_SG ; zh_TW et zh_HK aussi, faute de catalogue traditionnel), tout le
reste (fr, C, POSIX, vide, autre langue) le français, langue de repli. Les services --user
(qaf-ui, qaf-agent-panneau, qaf-suspension) reçoivent ces variables de la
session par l'import-environment de leur .desktop, pour parler la même langue
que la palette. Rien d'autre ne la choisit : ni un
fichier écrit par un qube, ni un message reçu (la fenêtre de confirmation lit
la demande de l'exécuteur par un schéma fermé, sans champ de langue). Les
rendus hors écran et les tests changent de langue par choisir().
"""

import os
import string

LANGUES = ("fr", "en", "zh")
REPLI = "fr"
VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")
PRIORITAIRE = "LANGUAGE"  # gettext : ignorée quand la locale est C ou POSIX

_langue = None


def _code(valeur) -> str:
    return valeur.split(".")[0].split("@")[0].split("_")[0].lower()


def langue_de(environ) -> str:
    """Langue d'après les variables de locale, dans l'ordre de gettext."""
    locale = ""
    for nom in VARIABLES:
        locale = environ.get(nom) or ""
        if locale:
            break
    if _code(locale) not in ("", "c", "posix"):
        for element in (environ.get(PRIORITAIRE) or "").split(":"):
            if _code(element) in LANGUES:
                return _code(element)
    code = _code(locale)
    return code if code in LANGUES else REPLI


def langue() -> str:
    """Langue de ce processus, fixée au premier appel (locale de dom0)."""
    global _langue
    if _langue is None:
        _langue = langue_de(os.environ)
    return _langue


def choisir(code):
    """Rendus hors écran et tests seulement : fixe la langue (None = relire la locale)."""
    global _langue
    if code is not None and code not in LANGUES:
        raise ValueError(f"langue inconnue : {code}")
    _langue = code


def catalogue(code=None) -> dict:
    code = code or langue()
    if code == "en":
        from .langues import en as module
    elif code == "zh":
        from .langues import zh as module
    else:
        from .langues import fr as module
    return module.TEXTES


def t(cle, **valeurs) -> str:
    """Chaîne de la clé dans la langue du processus, champs {nom} remplis."""
    texte = catalogue()[cle]
    return texte.format(**valeurs) if valeurs else texte


def tn(cle, n, **valeurs) -> str:
    """Pluriel : clé « cle.1 » au singulier, « cle.n » au pluriel (français :
    0 et 1 au singulier ; anglais : 1 seulement ; chinois : pas de pluriel, ses
    deux formes sont identiques). {n} est toujours fourni."""
    un = n <= 1 if langue() == "fr" else n == 1
    return t(f"{cle}.1" if un else f"{cle}.n", n=n, **valeurs)


def guillemets(nom) -> str:
    """Nom cité, jamais coupé de ses guillemets : « nom » (espaces insécables) ou “nom”
    (anglais et chinois)."""
    return f"“{nom}”" if langue() != "fr" else f"« {nom} »"


def phrases(liste) -> str:
    """Phrases mises bout à bout : séparées d'une espace (français, anglais), collées
    en chinois, où chacune finit déjà par « 。 »."""
    return ("" if langue() == "zh" else " ").join(liste)


def champs(texte) -> set:
    """Noms des champs {nom} d'une chaîne (tests)."""
    return {nom for _, nom, _, _ in string.Formatter().parse(texte) if nom is not None}


def constantes(table):
    """__getattr__ de module (PEP 562) : les anciens noms de constantes de texte
    (confirmation.ALERTE_PORTEE…) rendent la chaîne dans la langue du moment."""
    def __getattr__(nom):
        if nom in table:
            return t(table[nom])
        raise AttributeError(nom)
    return __getattr__
