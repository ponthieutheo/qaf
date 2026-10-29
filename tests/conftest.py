"""Réglages communs des tests : les écrans parlent français (langue de repli),
quelle que soit la locale de la machine de test ; tests/test_i18n.py passe en
anglais explicitement (qaf_ui.i18n.choisir)."""

import os

import pytest

from qaf_ui import i18n

# Les sous-processus (rendus xvfb, scripts bash) héritent de l'environnement :
# locale neutre, présente partout, pour qu'ils parlent français eux aussi et que
# bash n'affiche pas d'avertissement setlocale (machine sans la locale de l'hôte).
for _nom in [n for n in os.environ if n == "LANGUAGE" or n.startswith("LC_")]:
    del os.environ[_nom]
os.environ["LANG"] = "C.UTF-8"


@pytest.fixture(autouse=True)
def _langue_francaise():
    i18n.choisir("fr")
    yield
    i18n.choisir("fr")
