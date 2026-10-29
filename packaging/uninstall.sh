#!/bin/bash
# Désinstalle le Doorman QAF de dom0. Idempotent, sans réseau.
#   sudo bash uninstall.sh           garde journal, registre et comptes
#   sudo bash uninstall.sh --purge   efface aussi /var/lib/qaf* et les comptes
# QAF_RACINE=/chemin : essai dans un dossier (voir install.sh).
set -euo pipefail

R=${QAF_RACINE:-}
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

if [ -z "$R" ]; then
    [ "$(id -u)" = 0 ] || { echo "À lancer en root (sudo)." >&2; exit 1; }
fi

# Utilisateur de la session graphique, noté par install.sh dans qaf-ui.conf (lu
# avant que ce fichier parte) : pour ses fichiers de la session QAF.
UI_UTIL=$(sed -n 's|^d /run/qaf-ui [0-7]* \([a-z_][a-z0-9_-]*\) qaf-exec.*|\1|p' \
    "$R/etc/tmpfiles.d/qaf-ui.conf" 2>/dev/null | head -n 1 || true)

echo "== Politique d'abord : plus aucun appel qaf.Plan accepté"
rm -f "$R/etc/qubes/policy.d/00-qaf.policy" "$R/etc/qubes/policy.d/00-qaf.policy.tmp" \
    "$R/etc/qubes/policy.d/00-qaf-agent-long.policy" "$R/etc/qubes/policy.d/00-qaf-agent-long.policy.tmp" \
    "$R/etc/qubes/policy.d/30-qaf-usage.policy" "$R/etc/qubes/policy.d/30-qaf-usage.policy.tmp"
rm -f "$R/etc/qubes-rpc/qaf.Plan"

UNITES="qaf-gate-validator.socket qaf-gate-validator.service qaf-gate-executor.socket qaf-gate-demande.socket qaf-gate-executor.service qaf-gate-taches.socket qaf-gate-taches.service qaf-gate-outils.socket qaf-gate-outils.service"
if [ -z "$R" ]; then
    # shellcheck disable=SC2086
    systemctl disable --now $UNITES 2>/dev/null || true
fi
for u in $UNITES; do rm -f "$R/etc/systemd/system/$u"; done
[ -z "$R" ] && systemctl daemon-reload

# Un qaf-ui déjà lancé tourne jusqu'à la fin de la session, sans socket.
echo "== Fenêtre de confirmation"
rm -f "$R/etc/xdg/autostart/qaf-suspension.desktop" "$R/etc/systemd/user/qaf-suspension.service" \
    "$R/etc/xdg/autostart/qaf-agent-panneau.desktop" "$R/etc/systemd/user/qaf-agent-panneau.service" \
    "$R/etc/xdg/autostart/qaf-degel.desktop" "$R/etc/systemd/user/qaf-degel.service"
if [ -z "$R" ] && [ -n "$UI_UTIL" ] && uid=$(id -u "$UI_UTIL" 2>/dev/null) && [ -S "/run/user/$uid/bus" ]; then
    # L'icône de suspension quitte la barre tout de suite (qaf-suspendre part aussi).
    runuser -u "$UI_UTIL" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        sh -c 'systemctl --user stop qaf-suspension.service qaf-agent-panneau.service qaf-degel.service; systemctl --user daemon-reload' || true
fi
rm -f "$R/etc/xdg/autostart/qaf-ui.desktop" "$R/etc/systemd/user/qaf-ui.service" \
    "$R/etc/tmpfiles.d/qaf-ui.conf" "$R/etc/tmpfiles.d/qaf-ui.conf.tmp"
rm -rf "$R/run/qaf-ui"

echo "== Zone de notification de Plasma : xembedsniproxy d'origine"
# Notre drop-in parti, l'unité de KDE relance xembedsniproxy à la prochaine
# session (ou tout de suite : systemctl --user restart plasma-xembedsniproxy).
# Un qaf-zone lancé tourne jusque-là ; son lanceur parti, une relance par
# systemd retombe sur xembedsniproxy.
rm -f "$R/etc/systemd/user/plasma-xembedsniproxy.service.d/50-qaf-zone.conf" \
    "$R/etc/systemd/user/plasma-xembedsniproxy.service.d/50-qaf-zone.conf.tmp"
rmdir "$R/etc/systemd/user/plasma-xembedsniproxy.service.d" 2>/dev/null || true
if [ -z "$R" ] && [ -n "$UI_UTIL" ]; then
    uid=$(id -u "$UI_UTIL" 2>/dev/null || true)
    if [ -n "$uid" ] && [ -S "/run/user/$uid/bus" ]; then
        runuser -u "$UI_UTIL" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" systemctl --user daemon-reload || true
    fi
fi

echo "== Lecture du journal par la session (panneau de l'agent)"
LECTURE="$R/etc/tmpfiles.d/qaf-journal-lecture.conf"
if [ -f "$LECTURE" ]; then
    util=$(sed -n 's/^a+ .*u:\([a-z_][a-z0-9_-]*\):r$/\1/p' "$LECTURE" | head -n 1)
    if [ -z "$R" ] && [ -n "$util" ] && [ -f /var/lib/qaf-exec/journal.jsonl ]; then
        setfacl -x "u:$util" /var/lib/qaf-exec/journal.jsonl 2>/dev/null || true
    fi
fi
rm -f "$LECTURE" "$LECTURE.tmp"

rm -rf "$R/usr/lib/qaf" "$R/usr/libexec/qaf"
rm -f "$R/usr/bin/qaf-pause" "$R/usr/bin/qaf-suspendre" "$R/usr/bin/qaf-suspension" "$R/usr/bin/qaf-agent-panneau" "$R/usr/bin/qaf-update" "$R/usr/bin/qaf-palette"
rm -f "$R/usr/bin/qaf-agent" "$R/usr/share/applications/qaf-agent.desktop"
rm -f "$R/usr/share/qaf/LICENCES-TIERS"  # notice des icônes Lucide
rmdir "$R/usr/share/qaf" 2>/dev/null || true

if [ "$PURGE" = 1 ]; then
    echo "== Purge : journal, registre, pause, comptes"
    rm -rf "$R/var/lib/qaf" "$R/var/lib/qaf-gate" "$R/var/lib/qaf-exec" "$R/var/lib/qaf-outils"
    rm -f "$R/etc/sysusers.d/qaf.conf" "$R/etc/qaf/clavier-de-confiance" "$R/etc/qaf/portier.json"
    rmdir "$R/etc/qaf" 2>/dev/null || true
    if [ -z "$R" ]; then
        for u in qaf-gate qaf-exec qaf-taches qaf-outils; do
            id -u "$u" >/dev/null 2>&1 && userdel "$u"
        done
        getent group qaf-gate >/dev/null && groupdel qaf-gate || true
        getent group qaf-exec >/dev/null && groupdel qaf-exec || true
        getent group qaf-taches >/dev/null && groupdel qaf-taches || true
        getent group qaf-outils >/dev/null && groupdel qaf-outils || true
    fi
else
    echo "Journal et registre gardés dans /var/lib/qaf-exec (--purge pour effacer)."
fi
echo "QAF désinstallé."
