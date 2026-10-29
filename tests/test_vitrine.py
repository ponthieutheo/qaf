"""Vitrine : les trois README et SECURITY.md pointent vers des fichiers qui existent,
sans emplacement « à venir » ; les captures 02, 03 et 04 se refont à partir du vrai code
(tests/rendu_vitrine.py, sous xvfb, contrastes exigés). La vitrine parle de la brique
agent seule : jamais de l'outillage de développement (consigne du 29/10)."""

import re
import subprocess

import pytest

from test_nuit_gtk import RACINE, _env, _gtk_disponible

DOCS = ("README.md", "README.en.md", "README.zh-CN.md", "SECURITY.md")


@pytest.mark.parametrize("nom", DOCS)
def test_liens_locaux_existent(nom):
    texte = (RACINE / nom).read_text(encoding="utf-8")
    liens = re.findall(r'(?:src|href)="([^"#:]+)"', texte) + re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", texte)
    assert liens
    manquants = [l for l in liens if not (RACINE / l).exists()]
    assert not manquants, manquants


@pytest.mark.parametrize("nom", DOCS)
def test_aucun_emplacement_a_venir(nom):
    texte = (RACINE / nom).read_text(encoding="utf-8").lower()
    for mot in ("à venir", "restent à remplir", "still to be filled", "coming soon"):
        assert mot not in texte


VITRINE = DOCS + tuple(f"docs/vitrine/{p.name}" for p in (RACINE / "docs" / "vitrine").glob("*.svg"))

try:  # mots du dépôt privé seulement (le dépôt public ne les porte pas)
    from mots_prives import MOTS_VITRINE
except ImportError:
    MOTS_VITRINE = ()


@pytest.mark.parametrize("nom", VITRINE)
def test_vitrine_sans_outillage_de_developpement(nom):
    texte = (RACINE / nom).read_text(encoding="utf-8").lower()
    for mot in ("--dev", "qaf-dev", "mode développement", "development mode", "开发模式", *MOTS_VITRINE):
        assert mot not in texte, mot


def test_readme_anglais_utilise_les_schemas_et_captures_anglais():
    texte = (RACINE / "README.en.md").read_text(encoding="utf-8")
    for svg in re.findall(r"docs/vitrine/(schema-[a-z]+)(-en)?\.svg", texte):
        assert svg[1] == "-en", svg
    for capture in ("02-demande-palette-en.png", "03-confirmation-simple-en.png", "04-confirmation-renforcee-en.png"):
        assert capture in texte


@pytest.mark.skipif(not _gtk_disponible(), reason="xvfb ou python3-gi absents")
@pytest.mark.parametrize("ecran", ["palette_demande", "confirmation_simple", "confirmation_renforcee"])
@pytest.mark.parametrize("langue", ["fr", "en"])
def test_captures_02_03_04_rendues_par_le_vrai_code(tmp_path, ecran, langue):
    png = tmp_path / f"{ecran}-{langue}.png"
    r = subprocess.run(["bash", str(RACINE / "tools" / "rendu-ui.sh"), f"rendu_vitrine:{ecran}", str(png),
                        "--langue", langue, "--exiger"],
                       capture_output=True, text=True, env=dict(_env(), QAF_RENDU_ECRAN="1920x1200"), timeout=120)
    assert r.returncode == 0 and png.stat().st_size > 10_000, r.stdout + r.stderr
