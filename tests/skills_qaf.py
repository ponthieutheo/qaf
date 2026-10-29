"""Skills livrés à l'agent (qaf_mcp/skill/<nom>/SKILL.md), pour les tests."""

import pathlib

DOSSIER = pathlib.Path(__file__).resolve().parent.parent / "qaf_mcp" / "skill"


def skills():
    """Nom du dossier -> texte du SKILL.md, « qaf » (le skill de base) d'abord."""
    return {p.parent.name: p.read_text(encoding="utf-8")
            for p in sorted(DOSSIER.glob("*/SKILL.md"), key=lambda p: p.parent.name)}


def tous():
    """Tous les skills bout à bout : une consigne peut vivre dans n'importe lequel."""
    return "\n".join(skills().values())
