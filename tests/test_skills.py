"""Skills livrés à l'agent (qaf_mcp/skill/) et leur pose dans ~/.claude/skills
(qaf_mcp/poser_skills.sh) : noms réservés, en-têtes, index des outils, rien
de propre à un poste, jamais de quoi contourner le Doorman, skills personnels de
l'agent jamais touchés par l'installation."""

import pathlib
import re
import shutil
import subprocess

import pytest

import skills_qaf
from qaf_mcp.server import OUTILS

RACINE = pathlib.Path(__file__).resolve().parent.parent
POSER = RACINE / "qaf_mcp" / "poser_skills.sh"
SKILLS = skills_qaf.skills()
_ENTETE = re.compile(r"\A---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n")


def test_noms_reserves_et_en_tetes():
    assert "qaf" in SKILLS and "qaf-adapter" in SKILLS and len(SKILLS) >= 6
    for nom, texte in SKILLS.items():
        assert nom == "qaf" or nom.startswith("qaf-"), nom  # préfixe réservé à QAF
        m = _ENTETE.match(texte)
        assert m and m[1] == nom, nom
        assert 80 <= len(m[2]) <= 1024 and "<" not in m[2], nom  # description utile, sans balise
        assert "\r" not in texte and len(texte.splitlines()) <= 200, nom


def test_le_skill_de_base_indexe_chaque_outil_et_chaque_skill():
    base = SKILLS["qaf"]
    for o in OUTILS:
        assert f"`{o['name']}`" in base, o["name"]
    for nom in SKILLS:
        if nom not in ("qaf", "qaf-adapter"):
            assert f"`{nom}`" in base, nom  # le skill de base renvoie vers chaque skill précis
    assert "`qaf-adapter`" in base


def test_chaque_outil_est_detaille_dans_un_skill_precis():
    precis = "\n".join(t for n, t in SKILLS.items() if n != "qaf")
    lectures = {"qubes_list", "qube_info", "qube_settings", "system_status", "global_settings", "updates_status"}
    for o in OUTILS:
        if o["name"] not in lectures:
            assert f"`{o['name']}`" in precis, o["name"]


def test_outils_cites_existent():
    noms = {o["name"] for o in OUTILS}
    # Champs et codes cités entre accents graves, qui ne sont pas des outils.
    autres = {"device_id", "template_absent", "template_for_dispvms", "updates_available"}
    cites = set(re.findall(r"`((?:qube|qubes|template|templates|packages|updates|firewall|device|devices|file|"
                           r"disposable|job|backup|global|system)_[a-z_]+)`", skills_qaf.tous())) - autres
    assert cites and cites <= noms, cites - noms


def test_rien_de_propre_a_un_poste():
    # Recadrage (29/10) : Qubes tel qu'installé par défaut, rien de son poste.
    texte = skills_qaf.tous().lower()
    try:  # mots du dépôt privé seulement (le dépôt public ne les porte pas)
        from mots_prives import MOTS_SKILLS
    except ImportError:
        MOTS_SKILLS = ()
    for mot in ("legion", "rtx", "tpl-dev", "remote-desktop", *MOTS_SKILLS):
        assert mot not in texte, mot
    assert not re.search(r"\bgpu\b", texte)


def test_jamais_de_contournement():
    texte = skills_qaf.tous()
    assert not re.search(r"`(qvm-|qubes-prefs)[^`]* [^`]+`", texte)  # pas de commande dom0 toute prête
    assert "12 plans par minute" not in texte  # plus de plafond de débit depuis le 28/10
    assert "mode Pilote" not in texte  # l'agent permanent est influencé : Pilote ne lui change rien
    adapter = " ".join(SKILLS["qaf-adapter"].split())
    for phrase in ("Seulement des faits **confirmés**", "**un texte écrit par un qube**", "**un secret**",
                   "**une règle qui lèverait une confirmation**", "Ne crée, ne modifie et n'efface jamais un dossier "
                   "`qaf` ou `qaf-…`", "**À chaque création ou modification, dis-le à l'utilisateur en une phrase**",
                   "`~/.claude/skills/perso-<sujet>/SKILL.md`"):
        assert phrase in adapter, phrase


# --- pose dans ~/.claude/skills ----------------------------------------------------

def _poser(src, dest):
    return subprocess.run(["sh", str(POSER), str(src), str(dest)], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh absent")
def test_poser_skills_remplace_les_skills_qaf_et_garde_les_autres(tmp_path):
    dest = tmp_path / "skills"
    for n in ("qaf", "qaf-ancien", "perso-poste", "autre"):  # qaf-ancien : skill QAF disparu
        (dest / n).mkdir(parents=True)
        (dest / n / "SKILL.md").write_text(f"vieux {n}\n")
    (dest / "qaf-lien").symlink_to(dest / "autre")
    p = _poser(RACINE / "qaf_mcp" / "skill", dest)
    assert p.returncode == 0, p.stderr
    poses = sorted(x.name for x in dest.iterdir())
    assert poses == sorted([*SKILLS, "perso-poste", "autre"])
    for n, texte in SKILLS.items():
        assert (dest / n / "SKILL.md").read_text(encoding="utf-8") == texte
    assert (dest / "perso-poste" / "SKILL.md").read_text() == "vieux perso-poste\n"  # jamais touché
    assert (dest / "autre" / "SKILL.md").read_text() == "vieux autre\n"
    assert _poser(RACINE / "qaf_mcp" / "skill", dest).returncode == 0  # rejouable
    assert sorted(x.name for x in dest.iterdir()) == poses


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh absent")
def test_poser_skills_sans_source_ne_retire_rien(tmp_path):
    dest = tmp_path / "skills"
    (dest / "qaf").mkdir(parents=True)
    p = _poser(tmp_path / "absent", dest)
    assert p.returncode == 1 and (dest / "qaf").is_dir()


def test_les_installations_posent_tous_les_skills():
    for script in ("tools/setup-sys-agent.sh", "tools/setup-agent-dvm.sh"):
        s = (RACINE / script).read_text(encoding="utf-8")
        assert "sh /usr/lib/qaf/qaf_mcp/poser_skills.sh" in s, script
        assert "skill/qaf ~/.claude/skills" not in s, script  # plus de copie du seul skill de base
    assert b"\r\n" not in POSER.read_bytes()
