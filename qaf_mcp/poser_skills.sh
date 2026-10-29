#!/bin/sh
# poser_skills.sh — pose les skills de QAF dans ~/.claude/skills de l'agent
# (sys-agent, ou le modèle des agents jetables). Appelé par
# tools/setup-sys-agent.sh et tools/setup-agent-dvm.sh.
#
#   sh poser_skills.sh [SOURCE] [DESTINATION]
#   SOURCE      : /usr/lib/qaf/qaf_mcp/skill par défaut (un dossier par skill)
#   DESTINATION : ~/.claude/skills par défaut
#
# Les noms « qaf » et « qaf-… » sont réservés à QAF : tous ceux de DESTINATION
# sont retirés (skills disparus ou renommés compris), puis ceux de SOURCE sont
# recopiés. Tout autre dossier (skills « perso-… » que l'agent écrit lui-même
# pour s'adapter au poste, skills de l'utilisateur) n'est jamais touché.
set -eu
SRC=${1:-/usr/lib/qaf/qaf_mcp/skill}
DEST=${2:-$HOME/.claude/skills}
[ -f "$SRC/qaf/SKILL.md" ] || { echo "skills de QAF introuvables dans $SRC" >&2; exit 1; }
mkdir -p "$DEST"
for d in "$DEST"/qaf "$DEST"/qaf-*; do
    if [ -e "$d" ] || [ -L "$d" ]; then rm -rf -- "$d"; fi
done
for s in "$SRC"/*; do
    n=${s##*/}
    case $n in
        qaf|qaf-*) if [ -f "$s/SKILL.md" ]; then cp -R -- "$s" "$DEST/$n"; fi ;;
    esac
done
exit 0
