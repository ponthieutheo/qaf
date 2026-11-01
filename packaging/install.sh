#!/bin/bash
# Installe le Doorman QAF dans dom0. Idempotent, sans réseau.
# Lancé par tools/qaf-update (après vérification de l'empreinte), en root :
#   sudo bash qaf/packaging/install.sh
# QAF_RACINE=/chemin : installation d'essai dans un dossier (pas de systemd,
# pas de comptes), pour tester le script hors de dom0.
set -euo pipefail

export LC_ALL=C  # tri des noms de fichiers par octet, comme parser.py
SRC=$(cd "$(dirname "$0")/.." && pwd)
R=${QAF_RACINE:-}
POL=00-qaf.policy
# Dossiers qui ne sont pas à nous (livrés par Qubes ou le système) : créés
# s'ils manquent, jamais rechmodés. « install -d -m » sur un dossier existant
# change son mode : /etc/qubes/policy.d (0775 dans le paquet de Qubes) passait
# en 0755, vu par rpm -V sur une vraie machine. Règle n°1 : on n'y touche pas.
dossier_systeme() {
    local d
    for d in "$@"; do [ -d "$d" ] || install -d -m 0755 "$d"; done
}
LONG=00-qaf-agent-long.policy  # agent permanent (sys-agent, D28), seulement si portier.json dit agent_long
VOISIN=00-qaf-0-voisin.policy  # qube voisin : écrit par qaf-voisin, jamais livré (qaf_ui/voisin.py)
USAGE_POL=30-qaf-usage.policy  # panneau de l'agent (qaf.Usage, qaf.Console, qaf.Connexion, qaf.Fournisseur), posé avec $LONG

if [ -z "$R" ]; then
    [ "$(id -u)" = 0 ] || { echo "À lancer en root (sudo)." >&2; exit 1; }
    [ -e /etc/qubes-release ] || { echo "Ce n'est pas un dom0 Qubes." >&2; exit 1; }
fi

# qrexec fusionne /run/qubes/policy.d et /etc/qubes/policy.d, trie par nom de
# fichier et garde le premier match (qrexec/policy/parser.py, load_policy_dirs).
# Un fichier trié avant $POL qui parle de sys-agent, des jetables de l'agent
# (qaf-agent, étiquette ou modèle, ou un jetable dispN précis) ou d'un groupe
# qui peut les inclure passerait avant notre liste blanche ; un $POL dans /run
# la remplacerait. Dans ces cas on n'installe rien. Deux exceptions : notre
# $LONG, identique à celui du dépôt (AGENT-JETABLE §5.2, §11), et $VOISIN de forme
# exacte (voisin_valide). Contrôle fait à l'installation ; qaf-installer --verifier
# le refait.
MOTIF_AVANT='sys-agent|qaf-agent|disp[0-9]|@anyvm|@dispvm|@tag:|@type:|^[[:space:]]*!include'
# Seul fichier de l'utilisateur admis avant $POL : l'autorisation d'un qube voisin
# (qaf_ui/voisin.py, qaf-voisin, geste dans dom0). Dans /etc, une seule règle, de cette
# forme exacte, ni lien symbolique : elle n'ouvre qu'un port d'un qube, à sys-agent seul.
# Toute autre forme retombe sur le contrôle ordinaire : elle cite sys-agent, donc refus.
voisin_valide() {
    local l n=0 c="" LC_ALL=C
    local re='^qubes\.ConnectTCP \+([1-9][0-9]{0,4}) sys-agent @default allow target=([a-zA-Z][a-zA-Z0-9_.-]{0,30})$'
    [ ! -L "$1" ] || return 1
    # qrexec coupe aussi sur le retour chariot et autres fins de ligne : rien d'autre que l'ASCII imprimable.
    [ "$(LC_ALL=C tr -d '\n -~' < "$1" | wc -c)" = 0 ] || return 1
    while IFS= read -r l || [ -n "$l" ]; do
        case "$l" in ''|\#*) continue ;; esac
        [[ "$l" =~ $re ]] || return 1
        [ "${BASH_REMATCH[1]}" -le 65535 ] || return 1
        c=${BASH_REMATCH[2]}
        case "$c" in dom0|sys-*|qaf-*|disp[0-9]*|vault) return 1 ;; esac
        n=$((n + 1))
    done < "$1"
    [ "$n" = 1 ] || return 1
    # Coffres et qubes réglés dans portier.json ($2) : mêmes listes que voisin.noms_interdits.
    # Fichier absent ou illisible : refus.
    [ -z "${2:-}" ] || python3 -I -c 'import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
cles = ("coffres", "systeme", "admin_qubes", "ne_jamais_demarrer", "coffres_entrables", "jetables")
sys.exit(any(sys.argv[2] in r.get(k, []) for k in cles))' "$2" "$c" 2>/dev/null
}
verifier_ordre() {
    local d f nom bloque=0
    for d in "$R/run/qubes/policy.d" "$R/etc/qubes/policy.d"; do
        [ -d "$d" ] || continue
        for f in "$d"/*.policy; do
            [ -f "$f" ] || continue
            nom=$(basename "$f")
            if [ "$nom" = "$POL" ] && [ "$d" = "$R/run/qubes/policy.d" ]; then
                echo "  $f remplacerait la politique QAF (même nom, /run passe avant /etc)" >&2
                bloque=1
            elif [ "$nom" = "$LONG" ] && [ "$d" = "$R/etc/qubes/policy.d" ] && cmp -s "$f" "$SRC/policy/$LONG"; then
                :  # le nôtre, inchangé
            elif [ "$nom" = "$VOISIN" ] && [ "$d" = "$R/etc/qubes/policy.d" ] && voisin_valide "$f" "$R/etc/qaf/portier.json"; then
                :  # autorisation posée par l'utilisateur : gardée par la mise à jour
            elif [[ "$nom" < "$POL" ]] && grep -Ev '^[[:space:]]*(#|$)' "$f" | grep -Eq "$MOTIF_AVANT"; then
                echo "  $f passe avant $POL et peut concerner l'agent (sys-agent ou ses jetables)" >&2
                bloque=1
            fi
        done
    done
    return "$bloque"
}
if ! verifier_ordre; then
    echo "Politique qrexec incompatible (voir ci-dessus) : rien n'a été installé." >&2
    exit 1
fi

# Utilisateur de la session graphique de dom0, propriétaire du dossier du
# socket de qaf-ui : QAF_UTILISATEUR, sinon celui qui a lancé sudo, sinon le
# premier membre du groupe qubes. Vérifié avant d'installer quoi que ce soit.
UI_UTIL=${QAF_UTILISATEUR:-${SUDO_USER:-}}
if [ -z "$UI_UTIL" ] || [ "$UI_UTIL" = root ]; then
    UI_UTIL=$( (getent group qubes || true) | cut -d: -f4 | cut -d, -f1)
fi
[ -n "$R" ] && UI_UTIL=${UI_UTIL:-utilisateur-essai}
if ! [[ "$UI_UTIL" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || [ "$UI_UTIL" = root ]; then
    echo "Utilisateur de session introuvable ou invalide (« $UI_UTIL ») : QAF_UTILISATEUR=nom." >&2
    echo "Rien n'a été installé." >&2
    exit 1
fi
# C5 : le compte doit exister AVANT d'installer quoi que ce soit (sinon arrêt
# à mi-chemin, avant la politique).
if [ -z "$R" ] && ! id -u "$UI_UTIL" >/dev/null 2>&1; then
    echo "Utilisateur de session « $UI_UTIL » inconnu : QAF_UTILISATEUR=nom. Rien n'a été installé." >&2
    exit 1
fi

for paquet in qaf_gate qaf_ui; do
    echo "== Code $paquet -> $R/usr/lib/qaf"
    rm -rf "$R/usr/lib/qaf/$paquet.nouveau"
    install -d -m 0755 "$R/usr/lib/qaf/$paquet.nouveau"
    install -m 0644 "$SRC/$paquet"/*.py "$R/usr/lib/qaf/$paquet.nouveau/"
    for sous in "$SRC/$paquet"/*/; do  # sous-paquets (qaf_ui/nuit : style des écrans)
        [ -f "$sous/__init__.py" ] || continue
        install -d -m 0755 "$R/usr/lib/qaf/$paquet.nouveau/$(basename "$sous")"
        install -m 0644 "$sous"*.py "$R/usr/lib/qaf/$paquet.nouveau/$(basename "$sous")/"
    done
    python3 -m compileall -q "$R/usr/lib/qaf/$paquet.nouveau" >/dev/null || true
    rm -rf "$R/usr/lib/qaf/$paquet"
    mv "$R/usr/lib/qaf/$paquet.nouveau" "$R/usr/lib/qaf/$paquet"
done

echo "== Lanceurs"
install -d -m 0755 "$R/usr/libexec/qaf" "$R/usr/bin"
lanceur() {  # lanceur <fichier> <paquet.module>
    cat > "$1.tmp" <<EOF
#!/usr/bin/python3 -I
import sys
sys.path.insert(0, "/usr/lib/qaf")
from $2 import main
sys.exit(main())
EOF
    chmod 0755 "$1.tmp"
    mv "$1.tmp" "$1"
}
lanceur "$R/usr/libexec/qaf/qaf-gate-validator" qaf_gate.validator
lanceur "$R/usr/libexec/qaf/qaf-gate-executor" qaf_gate.executor
lanceur "$R/usr/libexec/qaf/qaf-gate-taches" qaf_gate.taches
lanceur "$R/usr/libexec/qaf/qaf-gate-outils" qaf_gate.outils
lanceur "$R/usr/libexec/qaf/qaf-ui" qaf_ui.serveur
lanceur "$R/usr/bin/qaf-pause" qaf_gate.pause
# Suspension des confirmations (« sudoless » d'Omarchy, 28/10) : root seulement.
lanceur "$R/usr/bin/qaf-suspendre" qaf_ui.suspension
# Autorisation d'un qube voisin (root, argv fixe) : même schéma, aucun droit pour l'agent.
lanceur "$R/usr/bin/qaf-voisin" qaf_ui.voisin
# Son icône dans la barre de Plasma (StatusNotifierItem, session de l'utilisateur ;
# unité --user et autostart posés plus bas avec ceux de qaf-ui).
lanceur "$R/usr/bin/qaf-suspension" qaf_ui.ecrans.suspension
# Panneau de l'agent dans la barre de Plasma (usage de l'abonnement, console, connexion ;
# docs/AGENT-PANNEAU.md) : même schéma que qaf-suspension.
lanceur "$R/usr/bin/qaf-agent-panneau" qaf_ui.ecrans.agent_panneau
# Filet contre le gel du pointeur (docs/GEL-BUREAU.md) : service --user, session Plasma.
lanceur "$R/usr/libexec/qaf/qaf-degel" qaf_ui.degel
# Palette « Demander à l'agent » : commande qaf-palette, ouverte depuis le panneau
# de l'agent (qaf-agent) ou à la main. Aucun raccourci clavier n'est posé
# (décision du 25/10).
lanceur "$R/usr/bin/qaf-palette" qaf_ui.palette
# Panneau de l'agent (docs/ECRANS-QAF.md) : sa commande et une entrée de menu à
# nous, posée à côté de celles de Qubes (aucune n'est modifiée), sans raccourci.
dossier_systeme "$R/usr/share/applications"
lanceur "$R/usr/bin/qaf-agent" qaf_ui.ecrans.agent
install -m 0644 "$SRC/packaging/qaf-agent.desktop" "$R/usr/share/applications/qaf-agent.desktop"
install -m 0755 "$SRC/tools/qaf-update" "$R/usr/bin/qaf-update"
# Icônes au trait reprises de Lucide (ISC, qaf_ui/nuit/icones.py) : la notice
# voyage avec elles (à la racine de l'archive, mise par tools/make-dist.sh).
if [ -f "$SRC/LICENCES-TIERS" ]; then
    install -d -m 0755 "$R/usr/share/qaf"
    install -m 0644 "$SRC/LICENCES-TIERS" "$R/usr/share/qaf/LICENCES-TIERS"
fi

echo "== Talon qrexec -> $R/etc/qubes-rpc/qaf.Plan"
dossier_systeme "$R/etc/qubes-rpc"
install -m 0755 "$SRC/qaf_gate/stub.py" "$R/etc/qubes-rpc/qaf.Plan"

echo "== Comptes et unités systemd"
dossier_systeme "$R/etc/sysusers.d" "$R/etc/systemd/system"
install -d -m 0755 "$R/var/lib/qaf"
install -m 0644 "$SRC/packaging/sysusers-qaf.conf" "$R/etc/sysusers.d/qaf.conf"
for u in qaf-gate-validator.socket qaf-gate-validator.service \
         qaf-gate-executor.socket qaf-gate-demande.socket qaf-gate-executor.service \
         qaf-gate-taches.socket qaf-gate-taches.service qaf-gate-outils.socket qaf-gate-outils.service; do
    install -m 0644 "$SRC/packaging/$u" "$R/etc/systemd/system/$u"
done
# Configuration du Doorman (coffres, qubes système, admin_qubes,
# ne_jamais_demarrer : OPS-v3 §3.2, R-G) : réglage de l'utilisateur, posé une fois,
# jamais écrasé. Valeurs livrées : packaging/portier.json (listes à régler).
install -d -m 0755 "$R/etc/qaf"
[ -e "$R/etc/qaf/portier.json" ] || install -m 0644 "$SRC/packaging/portier.json" "$R/etc/qaf/portier.json"
if [ -z "$R" ]; then
    systemd-sysusers /etc/sysusers.d/qaf.conf
    systemctl daemon-reload
    # Un service activé par plusieurs sockets refuse qu'on ouvre un nouveau
    # socket tant qu'il tourne (« Socket service … already active, refusing »,
    # vu sur le vrai dom0 lors d'une mise à jour) : on arrête les services
    # d'abord ; qaf.Plan répond « portier indisponible » pendant ces secondes.
    systemctl stop qaf-gate-validator.service qaf-gate-executor.service qaf-gate-taches.service 2>/dev/null || true
    # qaf-gate-outils n'est pas arrêté avant : try-restart ci-dessous le relance,
    # ce qui interrompt un travail long en cours comme à la main (OPS-v3 §14).
    systemctl enable --now qaf-gate-taches.socket qaf-gate-outils.socket qaf-gate-executor.socket \
        qaf-gate-demande.socket qaf-gate-validator.socket
    # mise à jour : redémarre les démons déjà lancés (sinon, activés au 1er appel)
    systemctl try-restart qaf-gate-taches.service qaf-gate-outils.service qaf-gate-executor.service \
        qaf-gate-validator.service
fi

echo "== Fenêtre de confirmation (qaf-ui, session de $UI_UTIL)"
dossier_systeme "$R/etc/tmpfiles.d" "$R/etc/systemd/user" "$R/etc/xdg/autostart"
install -d -m 0755 "$R/etc/qaf"
sed "s/@UTILISATEUR@/$UI_UTIL/" "$SRC/packaging/tmpfiles-qaf-ui.conf" > "$R/etc/tmpfiles.d/qaf-ui.conf.tmp"
chmod 0644 "$R/etc/tmpfiles.d/qaf-ui.conf.tmp"
mv "$R/etc/tmpfiles.d/qaf-ui.conf.tmp" "$R/etc/tmpfiles.d/qaf-ui.conf"
install -m 0644 "$SRC/packaging/qaf-ui.service" "$R/etc/systemd/user/qaf-ui.service"
install -m 0644 "$SRC/packaging/qaf-ui.desktop" "$R/etc/xdg/autostart/qaf-ui.desktop"
# Icône de suspension des confirmations : session Plasma seulement (OnlyShowIn=KDE ;
# la session Xfce reste celle de Qubes d'origine).
install -m 0644 "$SRC/packaging/qaf-suspension.service" "$R/etc/systemd/user/qaf-suspension.service"
install -m 0644 "$SRC/packaging/qaf-suspension.desktop" "$R/etc/xdg/autostart/qaf-suspension.desktop"
install -m 0644 "$SRC/packaging/qaf-agent-panneau.service" "$R/etc/systemd/user/qaf-agent-panneau.service"
install -m 0644 "$SRC/packaging/qaf-agent-panneau.desktop" "$R/etc/xdg/autostart/qaf-agent-panneau.desktop"
install -m 0644 "$SRC/packaging/qaf-degel.service" "$R/etc/systemd/user/qaf-degel.service"
install -m 0644 "$SRC/packaging/qaf-degel.desktop" "$R/etc/xdg/autostart/qaf-degel.desktop"
# Réglage de l'utilisateur : posé une fois, jamais écrasé par une mise à jour.
[ -e "$R/etc/qaf/clavier-de-confiance" ] || \
    install -m 0644 "$SRC/packaging/clavier-de-confiance" "$R/etc/qaf/clavier-de-confiance"
# Panneau de l'agent : lecture seule du journal du Doorman par $UI_UTIL (ACL,
# docs/ECRANS-QAF.md §9.1). Fichier à nous ; ni le mode 0600 ni le propriétaire
# ne changent, et rien d'autre dans /var/lib/qaf-exec n'est ouvert.
sed "s/@UTILISATEUR@/$UI_UTIL/" "$SRC/packaging/tmpfiles-qaf-journal.conf" > "$R/etc/tmpfiles.d/qaf-journal-lecture.conf.tmp"
chmod 0644 "$R/etc/tmpfiles.d/qaf-journal-lecture.conf.tmp"
mv "$R/etc/tmpfiles.d/qaf-journal-lecture.conf.tmp" "$R/etc/tmpfiles.d/qaf-journal-lecture.conf"
if [ -z "$R" ]; then
    systemd-tmpfiles --create /etc/tmpfiles.d/qaf-ui.conf  # après sysusers : groupe qaf-exec
    systemd-tmpfiles --create /etc/tmpfiles.d/qaf-journal-lecture.conf || \
        echo "   ACL de lecture du journal non posée : le panneau de l'agent montrera l'état sans le fil." >&2
    uid=$(id -u "$UI_UTIL")
    if [ -S "/run/user/$uid/bus" ]; then  # session ouverte : recharge et relance si lancé
        runuser -u "$UI_UTIL" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
            sh -c 'systemctl --user daemon-reload; systemctl --user try-restart qaf-ui.service qaf-suspension.service qaf-agent-panneau.service qaf-degel.service' || true
    fi
    echo "   qaf-ui démarre à l'ouverture de session. Pour le lancer tout de suite, dans la session :"
    echo "   systemctl --user import-environment DISPLAY XAUTHORITY LANG LANGUAGE LC_ALL LC_MESSAGES; systemctl --user start qaf-ui"
fi

echo "== Zone de notification de Plasma (qaf-zone, icônes des qubes sans carré blanc)"
# qaf-zone remplace xembedsniproxy par un drop-in à nous sur l'unité --user de
# KDE (jamais modifiée) ; repli sur xembedsniproxy si qaf-zone manque ou échoue.
# Pris à la prochaine ouverture de session (aucune relance de la zone ici : une
# relance à chaud peut faire perdre son icône à un programme Qt d'un qube).
lanceur "$R/usr/libexec/qaf/qaf-zone" qaf_ui.zone_x
ZONE_D="$R/etc/systemd/user/plasma-xembedsniproxy.service.d"
install -d -m 0755 "$ZONE_D"
install -m 0644 "$SRC/packaging/qaf-zone.conf" "$ZONE_D/50-qaf-zone.conf.tmp"
mv "$ZONE_D/50-qaf-zone.conf.tmp" "$ZONE_D/50-qaf-zone.conf"
if [ -z "$R" ]; then
    uid=$(id -u "$UI_UTIL")
    if [ -S "/run/user/$uid/bus" ]; then
        runuser -u "$UI_UTIL" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" systemctl --user daemon-reload || true
    fi
    echo "   Prise à la prochaine session ; tout de suite : systemctl --user restart plasma-xembedsniproxy"
fi

echo "== Politique qrexec (en dernier : qaf.Plan n'est ouvert qu'une fois tout en place)"
dossier_systeme "$R/etc/qubes/policy.d"
if command -v qubes-policy-lint >/dev/null 2>&1; then
    qubes-policy-lint "$SRC/policy/$POL" "$SRC/policy/$LONG" "$SRC/policy/$USAGE_POL"
fi
# Agent permanent (AGENT-JETABLE D28) : activé par défaut (agent_long vrai). Sans $LONG, la
# politique ne donne plus rien à sys-agent (et le Doorman le refuse aussi).
# ORDRE : aucune fenêtre où sys-agent ne serait visé par aucune ligne. La politique
# en place (l'ancienne 00-qaf.policy) contient
# l'enfermement de sys-agent. $LONG (trié avant $POL, mêmes refus pour sys-agent) est
# donc posé AVANT $POL quand agent_long est vrai, et retiré seulement APRÈS $POL
# quand il est faux (la nouvelle $POL enferme alors sys-agent à elle seule).
if python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("agent_long") is True else 1)' \
        "$R/etc/qaf/portier.json" 2>/dev/null; then
    AGENT_LONG=1
else
    AGENT_LONG=0
    rm -f "$R/etc/qubes/policy.d/$VOISIN"  # sans liste blanche, cette règle n'a plus de cadre
fi
if [ "$AGENT_LONG" = 1 ]; then
    install -m 0644 "$SRC/policy/$LONG" "$R/etc/qubes/policy.d/$LONG.tmp"
    mv "$R/etc/qubes/policy.d/$LONG.tmp" "$R/etc/qubes/policy.d/$LONG"
    install -m 0644 "$SRC/policy/$USAGE_POL" "$R/etc/qubes/policy.d/$USAGE_POL.tmp"
    mv "$R/etc/qubes/policy.d/$USAGE_POL.tmp" "$R/etc/qubes/policy.d/$USAGE_POL"
fi
install -m 0644 "$SRC/policy/$POL" "$R/etc/qubes/policy.d/$POL.tmp"
mv "$R/etc/qubes/policy.d/$POL.tmp" "$R/etc/qubes/policy.d/$POL"
if [ "$AGENT_LONG" = 1 ]; then
    echo "   Agent permanent (portier.json) : sys-agent garde qaf.Plan, influencé en permanence."
else
    rm -f "$R/etc/qubes/policy.d/$LONG" "$R/etc/qubes/policy.d/$USAGE_POL"
fi

echo "QAF installé. État : qaf-pause status ; arrêt d'urgence : sudo qaf-pause"
echo "Agent : icône dans la barre de Plasma ; panneau « qaf-agent » (menu Système), d'où s'ouvre la palette « qaf-palette »."
echo "Aucun raccourci clavier posé : Meta et Meta+Espace gardent leur rôle de Plasma."
echo "Bureau : session « Plasma (X11) » d'origine à l'écran de connexion (QAF n'ajoute aucune session)."
