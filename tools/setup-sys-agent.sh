#!/bin/bash
# setup-sys-agent.sh — crée tpl-agent et sys-agent (à lancer dans dom0).
# Reprend l'installation faite à la main le 25/10/2026, avec Node 22
# (docs/AGENT-JETABLE.md §13.7). tpl-agent sert aussi de modèle à qaf-agent-dvm,
# le modèle des agents jetables (tools/setup-agent-dvm.sh) ; sys-agent est
# L'AGENT PERMANENT (D28, défaut depuis le 27/10) : chaque demande de la palette lui
# est transmise, sa mémoire ~/.claude survit. Réseau : sys-firewall, pare-feu de
# Qubes laissé ouvert (D28 : il est influencé en permanence, le Web ne change
# rien à ses confirmations).
# Prérequis : template debian-13-xfce et l'archive construite par
# tools/make-dist.sh : QAF_ARCHIVE=/chemin/qaf.tar.gz (déjà dans dom0 :
# tools/qaf-installer), ou QAF_SOURCE_QUBE=QUBE, le qube qui l'a construite
# (QAF_SOURCE_ARCHIVE, défaut qaf/dist/qaf.tar.gz dans son dossier personnel).
# Usage : bash setup-sys-agent.sh <sha256 attendu de dist/qaf.tar.gz>
# L'empreinte doit venir d'une source indépendante de ce qube (dépôt de
# référence, ou reconstruction sur une autre machine), comme pour le Doorman (C12).
# L'archive est toujours vérifiée contre l'empreinte avant usage.
set -e
ATTENDU=${1:?usage : setup-sys-agent.sh <sha256 attendu de dist/qaf.tar.gz>}
ARCH=$(mktemp /tmp/qaf-agent.XXXXXX.tar.gz)
trap 'rm -f "$ARCH"' EXIT
# Archive : QAF_ARCHIVE (déjà dans dom0 : tools/qaf-installer), sinon copiée
# depuis le qube QAF_SOURCE_QUBE qui l'a construite (QAF_SOURCE_ARCHIVE, relatif
# à son dossier personnel, défaut qaf/dist/qaf.tar.gz). Vérifiée ci-dessous.
if [ -n "${QAF_ARCHIVE:-}" ]; then cp -- "$QAF_ARCHIVE" "$ARCH"
elif [[ "${QAF_SOURCE_QUBE:-}" =~ ^[a-zA-Z][a-zA-Z0-9_.-]{0,30}$ ]] && [[ "${QAF_SOURCE_ARCHIVE:-qaf/dist/qaf.tar.gz}" =~ ^[A-Za-z0-9_][A-Za-z0-9_./-]*$ ]]; then
    qvm-run --pass-io --no-gui "$QAF_SOURCE_QUBE" "cat -- \"\$HOME/${QAF_SOURCE_ARCHIVE:-qaf/dist/qaf.tar.gz}\"" </dev/null > "$ARCH"
else echo "Archive : QAF_ARCHIVE=/chemin/qaf.tar.gz (dans dom0), ou QAF_SOURCE_QUBE=QUBE (le qube qui l'a construite)." >&2; exit 2; fi
OBTENU=$(sha256sum "$ARCH" | cut -d' ' -f1)
if [ "$OBTENU" != "$ATTENDU" ]; then
    echo "EMPREINTE DIFFÉRENTE : attendu $ATTENDU, obtenu $OBTENU. Rien n'est installé." >&2
    exit 1
fi
QAF_ARCHIVE=$ARCH

# 1. Modèle : Node 22 + Claude Code installés sous /usr (dans une AppVM,
#    /usr/local vient du disque privé du qube, pas du modèle : un CLI installé
#    en /usr/local dans le modèle est invisible dans sys-agent et les jetables).
#    Claude Code 2.1.310 exige Node >= 22, absent de trixie et trixie-security
#    (S-J10) : dépôt NodeSource node_22.x (nodistro), par le proxy de mises à
#    jour de Qubes (qubes.UpdatesProxy, permis aux modèles par 90-default).
#    La clé est comparée à une empreinte écrite ici, relevée sur une source
#    indépendante (comme l'empreinte de l'archive, C12) ; le script s'arrête si
#    elle diffère. Un pin apt ne laisse venir de NodeSource que nodejs.
#    Version de Claude Code ÉPINGLÉE (C7) ; mise à jour = changer CLAUDE_VERSION.
#    [À faire] : NODESOURCE_EMPREINTE à confirmer sur une source indépendante
#    (nodesource/distributions, README) avant le premier lancement.
NODESOURCE_EMPREINTE=${QAF_NODESOURCE_EMPREINTE:-6F71F525282841EEDAF851B42F59B5F99B1BE0B4}
CLAUDE_VERSION=${QAF_CLAUDE_VERSION:-2.1.310}
#    Codex (OpenAI), deuxième fournisseur du « + » du panneau de l'agent
#    (docs/AGENT-PANNEAU.md) : paquet npm officiel @openai/codex (éditeur
#    openai-publisher), ÉPINGLÉ comme Claude Code, sans script d'installation ;
#    son binaire vient de la dépendance de plateforme du même paquet.
#    Grok n'est PAS installé : @xai-official/grok pose son binaire dans le
#    dossier personnel de l'installeur (~/.grok/bin, script postinstall) et se
#    met à jour lui-même, ce qui ne passe pas d'un modèle à ses AppVM ; le
#    panneau le montre « non installé ».
CODEX_VERSION=${QAF_CODEX_VERSION:-0.163.0}
qvm-ls --raw-list | grep -qx tpl-agent || qvm-clone debian-13-xfce tpl-agent
qvm-prefs tpl-agent label black
qvm-start --skip-if-running tpl-agent
qvm-run -u root --pass-io --no-gui tpl-agent "set -e
   P='--proxy http://127.0.0.1:8082'
   apt-get update -q
   command -v gpg >/dev/null && command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q gnupg curl >/dev/null
   curl -fsS \$P https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /tmp/qaf-nodesource.key
   E=\$(gpg --show-keys --with-colons /tmp/qaf-nodesource.key | awk -F: '\$1==\"fpr\"{print \$10; exit}')
   if [ \"\$E\" != '$NODESOURCE_EMPREINTE' ]; then echo \"EMPREINTE NODESOURCE DIFFÉRENTE : \$E\" >&2; exit 1; fi
   gpg --dearmor < /tmp/qaf-nodesource.key > /usr/share/keyrings/qaf-nodesource.gpg
   echo 'deb [signed-by=/usr/share/keyrings/qaf-nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' \
     > /etc/apt/sources.list.d/qaf-nodesource.list
   printf 'Package: *\nPin: origin deb.nodesource.com\nPin-Priority: -1\n\nPackage: nodejs\nPin: origin deb.nodesource.com\nPin-Priority: 600\n' \
     > /etc/apt/preferences.d/qaf-nodesource
   apt-get update -q
   DEBIAN_FRONTEND=noninteractive apt-get remove -y -q npm >/dev/null 2>&1 || true
   DEBIAN_FRONTEND=noninteractive apt-get install -y -q nodejs git python3 jq >/dev/null
   node --version | grep -q '^v22\.'
   npm install -g --prefix /usr --no-fund --no-audit \
     --proxy http://127.0.0.1:8082 --https-proxy http://127.0.0.1:8082 @anthropic-ai/claude-code@$CLAUDE_VERSION @openai/codex@$CODEX_VERSION
   claude --version
   codex --version" </dev/null

# 2. Serveur MCP qaf (+ schéma et contrat du Doorman) dans /usr/lib/qaf du modèle,
#    et les services qrexec de l'agent : qaf.Request (demandes de la palette),
#    qaf.Usage, qaf.Console, qaf.Connexion et qaf.Fournisseur (panneau de l'agent
#    de la barre de Plasma, docs/AGENT-PANNEAU.md ; appelés par dom0 seulement).
#    Les autres fournisseurs n'ajoutent rien ici : leurs CLI s'installent à la
#    demande dans ~/.local de sys-agent (qaf_mcp/fournisseurs_table.py).
#    /etc/skel/.mcp.json : résidu d'un ancien serveur, hérité par tout nouveau qube
#    de ce modèle ; QAF n'y pose rien, il est retiré.
MCP="qaf_mcp qaf_gate/__init__.py qaf_gate/schema.py qaf_gate/proto.py"
rm -rf /tmp/qafmcp && mkdir /tmp/qafmcp
tar -xzf "$QAF_ARCHIVE" -C /tmp/qafmcp --no-same-owner qaf/qaf_mcp qaf/qaf_gate/__init__.py \
    qaf/qaf_gate/schema.py qaf/qaf_gate/proto.py
# shellcheck disable=SC2086
tar -C /tmp/qafmcp/qaf -cz --exclude=__pycache__ $MCP > /tmp/qafmcp.tgz
rm -rf /tmp/qafmcp
# Une seule ligne : qubes.VMShell lit la commande sur stdin ligne par ligne,
# tar avalerait les lignes suivantes avant l'archive.
qvm-run -u root --pass-io --no-gui tpl-agent \
  'rm -rf /usr/lib/qaf && mkdir -p /usr/lib/qaf && tar -C /usr/lib/qaf -xz && E=/usr/lib/qaf/qaf_mcp/etc && install -m 0755 $E/qubes-rpc/qaf.Request /etc/qubes-rpc/qaf.Request && install -D -m 0644 $E/qubes/rpc-config/qaf.Request /etc/qubes/rpc-config/qaf.Request && for s in qaf.Usage qaf.Console qaf.Connexion qaf.Fournisseur; do install -m 0755 $E/qubes-rpc/$s /etc/qubes-rpc/$s; done && for s in qaf.Console qaf.Connexion; do install -D -m 0644 $E/qubes/rpc-config/$s /etc/qubes/rpc-config/$s; done && rm -f /etc/skel/.mcp.json' < /tmp/qafmcp.tgz
qvm-shutdown --wait tpl-agent

# 3. sys-agent : aucun droit admin.* (politique 00-qaf.policy), pas de jetable
#    par défaut (C3), réseau pour l'API du fournisseur.
#    Dossier de travail ~/agent, approuvé d'avance dans ~/.claude.json
#    (projects.<dossier>.hasTrustDialogAccepted) : qaf.Request y lance claude,
#    qui ne pose plus la question « trust this folder » (PARCOURS-1 M4).
#    Un ancien ~/.mcp.json (qaf déclaré par projet) est mis de côté : sinon claude
#    demande « New MCP server found in this project » à chaque lancement.
#    Skills : tous ceux de qaf_mcp/skill (qaf, qaf-…) posés par poser_skills.sh,
#    qui retire les qaf-… disparus sans toucher aux skills perso-… de l'agent.
#    Les outils deviennent mcp__qaf__* ; /usr/lib/qaf n'arrive dans sys-agent
#    qu'à son prochain démarrage (jusque-là, qaf.Request y échoue : fermé).
qvm-ls --raw-list | grep -qx sys-agent || \
  qvm-create --class AppVM --template tpl-agent --label purple --prop netvm=sys-firewall sys-agent
qvm-prefs sys-agent default_dispvm ''
qvm-prefs sys-agent memory 2000
qvm-prefs sys-agent maxmem 6000
qvm-start --skip-if-running sys-agent
qvm-run --pass-io --no-gui sys-agent \
  'sh /usr/lib/qaf/qaf_mcp/poser_skills.sh
   claude mcp add --scope user qaf -e PYTHONPATH=/usr/lib/qaf -- /usr/bin/python3 -m qaf_mcp
   if [ -e ~/.mcp.json ]; then mv ~/.mcp.json ~/.mcp.json.qaf-ancien; fi
   mkdir -p ~/agent && C=~/.claude.json && { [ -s "$C" ] || echo "{}" > "$C"; } && jq --arg d "$HOME/agent" ".projects[\$d] = ((.projects[\$d] // {}) + {hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true})" "$C" > "$C.qaf" && mv "$C.qaf" "$C"
   cd ~/agent && claude mcp list' </dev/null
echo "sys-agent prêt. Connexion du compte (une fois) : icône de l'agent dans la barre de Plasma, puis Claude (terminal sur « claude auth login »)."
