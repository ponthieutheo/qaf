#!/bin/bash
# setup-agent-dvm.sh — crée et règle qaf-agent-dvm, le modèle de jetables des
# agents (à lancer dans dom0, en root, après setup-sys-agent.sh qui prépare
# tpl-agent). Contrat : docs/AGENT-JETABLE.md §3, §4, §13.5. Rejouable.
#
# Chaque demande de la palette lance un agent neuf dans un jetable de ce
# modèle ; le Doorman le crée, l'étiquette qaf-agent et le démarre. Ce script :
#  - crée l'AppVM sur tpl-agent (template_for_dispvms, netvm sys-firewall,
#    default_dispvm '' explicite : S-J6 ; 800/3000 Mo ; label purple) ;
#  - refuse de continuer si le modèle est jetable par défaut (global ou d'un
#    qube), s'il est dans « jetables » de portier.json (R-3), ou s'il porte
#    l'étiquette qaf-agent (S-J2 : elle passerait à tous ses jetables) ;
#  - pose le pare-feu FERMÉ (R-1) : DNS, TCP 443 vers les seuls hôtes de
#    portier.json (agent_hotes), puis drop ; recopié par Qubes dans chaque
#    jetable, revérifié par le Doorman à chaque plan (même liste :
#    qaf_gate/executor.py, regles_agent) ;
#  - remplit son volume privé de la liste fermée du §3 (accords de claude,
#    réglages, skill, serveur MCP, ~/agent vide, ~/.config/qaf) et contrôle
#    qu'il n'y a ni historique, ni sessions, ni identifiants de connexion
#    interactive (Risque 3 du §4.1) ;
#  - l'éteint. Personne ne le démarre en usage normal (P pour le Doorman).
#
# Identifiants (D21, §4.3) : PAS posés ici. L'utilisateur démarre lui-même le modèle
# (« qvm-run qaf-agent-dvm xfce4-terminal »), lance « claude setup-token »,
# colle le jeton dans ~/.config/qaf/jeton-claude (chmod 600) puis l'éteint.
#
# Permissions de claude (D23, [à décider]) : par défaut, seuls les outils du
# serveur MCP qaf sont permis sans question (permissions.allow mcp__qaf__*).
# QAF_PERMISSIONS=boite pose bypassPermissions (« tout dans la boîte ») ; ce
# n'est acceptable qu'avec le pare-feu fermé ci-dessus et le bloc 4 de
# 00-qaf.policy (R-10), que ce script vérifie avant.
set -euo pipefail
M=qaf-agent-dvm
PORTIER=/etc/qaf/portier.json
DEFAUT_HOTES="api.anthropic.com"

echo "== $M : modèle des agents jetables"
qvm-check --quiet tpl-agent 2>/dev/null || { echo "tpl-agent absent : lance d'abord setup-sys-agent.sh." >&2; exit 1; }

# Hôtes du pare-feu, lus dans portier.json (agent_hotes), sinon la valeur du produit.
HOTES=$(python3 - "$PORTIER" "$DEFAUT_HOTES" <<'PY'
import json, re, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except FileNotFoundError:
    d = {}
if "qaf-agent-dvm" in d.get("jetables", []):
    sys.exit("portier.json : qaf-agent-dvm est dans « jetables » (interdit, R-3)")
h = d.get("agent_hotes", sys.argv[2].split())
if not isinstance(h, list) or not h or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9.:/-]{1,253}", x) for x in h):
    sys.exit("portier.json : agent_hotes invalide")
print(" ".join(h))
PY
)

# R-3 : jamais jetable par défaut (global ou d'un qube).
if [ "$(qubes-prefs default_dispvm 2>/dev/null || true)" = "$M" ]; then
    echo "$M est le jetable par défaut global : refusé (R-3). Change-le d'abord." >&2; exit 1
fi
q=$(qvm-ls --raw-data --fields NAME,DEFAULT_DISPVM 2>/dev/null | awk -F'|' -v m="$M" '$2==m && $1!=m{print $1}' | tr '\n' ' ')
if [ -n "$q" ]; then echo "$M est le jetable par défaut de : $q- refusé (R-3)." >&2; exit 1; fi

qvm-check --quiet "$M" 2>/dev/null || \
    qvm-create --class AppVM --template tpl-agent --label purple --prop netvm=sys-firewall "$M"
qvm-prefs "$M" template_for_dispvms True
qvm-prefs "$M" netvm sys-firewall
qvm-prefs "$M" default_dispvm ''
qvm-prefs "$M" memory 800
qvm-prefs "$M" maxmem 3000
qvm-prefs "$M" label purple
# Jamais de jetables préchargés : CreateDisposable rendrait un jetable déjà
# démarré, sans étiquette ni inscription au Doorman (qubes/vm/dispvm.py, from_appvm).
qvm-features --unset "$M" preload-dispvm-max 2>/dev/null || true
if qvm-tags "$M" list 2>/dev/null | grep -qx qaf-agent; then
    echo "$M porte l'étiquette qaf-agent : elle passerait à tous ses jetables (S-J2). Retire-la à la main." >&2
    exit 1
fi

echo "== Pare-feu fermé (R-1) : DNS, TCP 443 vers : $HOTES ; puis drop"
qvm-firewall "$M" reset
qvm-firewall "$M" add accept specialtarget=dns
for h in $HOTES; do qvm-firewall "$M" add accept dsthost="$h" proto=tcp dstports=443; done
qvm-firewall "$M" add drop
qvm-firewall "$M" del --rule-no 0   # la règle « accept » de reset, restée en tête
qvm-firewall "$M" list

PERMS='{"permissions": {"allow": ["mcp__qaf__*"]}}'
if [ "${QAF_PERMISSIONS:-}" = boite ]; then
    grep -q '@tag:qaf-agent[[:space:]]*deny' /etc/qubes/policy.d/00-qaf.policy 2>/dev/null || {
        echo "QAF_PERMISSIONS=boite sans le bloc 4 de 00-qaf.policy : refusé (R-10)." >&2; exit 1; }
    PERMS='{"permissions": {"defaultMode": "bypassPermissions", "allow": ["mcp__qaf__*"]}, "skipDangerousModePermissionPrompt": true}'
fi

PERMS_B64=$(printf '%s\n' "$PERMS" | base64 -w0)

echo "== Volume privé : liste fermée (§3)"
qvm-start --skip-if-running "$M"
qvm-run --pass-io --no-gui "$M" "set -e
  mkdir -p ~/agent ~/.config/qaf ~/.claude/skills && chmod 700 ~/.config/qaf
  sh /usr/lib/qaf/qaf_mcp/poser_skills.sh
  claude mcp remove --scope user qaf >/dev/null 2>&1 || true
  claude mcp add --scope user qaf -e PYTHONPATH=/usr/lib/qaf -- /usr/bin/python3 -m qaf_mcp >/dev/null
  rm -f ~/.mcp.json
  C=~/.claude.json
  jq --arg d \"\$HOME/agent\" '.hasCompletedOnboarding = true | .projects[\$d] = ((.projects[\$d] // {}) + {hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true})' \"\$C\" > \"\$C.qaf\" && mv \"\$C.qaf\" \"\$C\"
  echo '$PERMS_B64' | base64 -d > ~/.claude/settings.json
  ecart=''
  for f in .claude/projects .claude/sessions .claude/shell-snapshots .claude/todos .claude/history.jsonl \
           .claude/.credentials.json .claude/CLAUDE.md CLAUDE.md agent/CLAUDE.md .bash_history; do
    [ -e ~/\$f ] && ecart=\"\$ecart \$f\"
  done
  [ -z \"\$(ls -A ~/agent)\" ] || ecart=\"\$ecart agent/(non vide)\"
  if [ -n \"\$ecart\" ]; then echo \"ÉCART : fichiers en trop dans le volume privé :\$ecart\" >&2; exit 3; fi
  for f in ~/.claude/*; do case \${f##*/} in settings.json|skills) ;; *) echo \"À regarder : ~/.claude/\${f##*/}\" ;; esac; done
  [ -s ~/.config/qaf/jeton-claude ] && echo 'Jeton présent.' || echo 'Jeton absent : voir docs/INSTALLATION.md (claude setup-token).'
  claude mcp list" </dev/null
qvm-shutdown --wait "$M"
echo "$M prêt. Chaque demande de la palette lance un agent neuf dans un de ses jetables."
