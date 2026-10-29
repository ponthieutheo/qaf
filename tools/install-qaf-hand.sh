#!/bin/sh
# install-qaf-hand.sh — installe qaf_hand dans un qube (en root), là où les
# services qrexec le cherchent : « /usr/bin/python3 -I -m qaf_hand … ».
# -I ignore PYTHONPATH et le dossier courant : le paquet doit être dans le
# chemin SYSTÈME de Python (cause de « No module named qaf_hand », RESULTATS-S1 D).
#
# Usage (dans le template du qube cible, ou du modèle de jetable QAF) :
#   sudo sh tools/install-qaf-hand.sh [DÉPÔT]            # installe
#   sudo sh tools/install-qaf-hand.sh --desinstaller     # retire
# DÉPÔT : dossier qui contient qaf_hand/ (par défaut : le dépôt de ce script).
#
# Ce qui est posé :
#   <site système>/qaf_hand/*.py              (Debian : /usr/lib/python3/dist-packages)
#   /etc/qubes-rpc/qaf.{Screen,Input,Exec,Task}            (0755)
#   /etc/qubes/rpc-config/qaf.{Screen,Input,Exec,Task}     (0644, wait-for-session=true)
# Dans une AppVM, /usr et /etc reviennent à l'état du template au redémarrage :
# installer dans le TEMPLATE pour un usage durable (une AppVM : essai seulement).
# Dépendances : python3, xdotool, xwd (Debian : x11-apps), xwininfo (x11-utils).
set -eu

SERVICES="qaf.Screen qaf.Input qaf.Exec qaf.Task"

[ "$(id -u)" = 0 ] || { echo "à lancer en root (sudo)" >&2; exit 1; }

# Dossier site-packages système, hors /usr/local et hors lib64 :
# Debian /usr/lib/python3/dist-packages, Fedora /usr/lib/python3.X/site-packages.
SITE=$(/usr/bin/python3 -I -c '
import re, sys
for p in sys.path:
    if re.fullmatch(r"/usr/lib/python3(\.[0-9]+)?/(dist|site)-packages", p):
        print(p); break
')
[ -n "$SITE" ] && [ -d "$SITE" ] || { echo "site-packages système introuvable" >&2; exit 1; }

if [ "${1:-}" = "--desinstaller" ]; then
    rm -rf "$SITE/qaf_hand"
    for s in $SERVICES; do rm -f "/etc/qubes-rpc/$s" "/etc/qubes/rpc-config/$s"; done
    echo "qaf_hand retiré ($SITE/qaf_hand, /etc/qubes-rpc/qaf.*, /etc/qubes/rpc-config/qaf.*)"
    exit 0
fi

DEPOT=${1:-$(dirname "$0")/..}
SRC="$DEPOT/qaf_hand"
[ -f "$SRC/service.py" ] && [ -f "$SRC/__main__.py" ] || { echo "qaf_hand introuvable dans $DEPOT" >&2; exit 1; }

# Copie propre : uniquement les .py du paquet (ni __pycache__, ni fichiers
# étrangers), propriétaire root, lecture seule pour les autres.
TMP="$SITE/.qaf_hand.nouveau"
rm -rf "$TMP"
install -d -m 0755 "$TMP"
for f in "$SRC"/*.py; do install -m 0644 "$f" "$TMP/"; done
rm -rf "$SITE/qaf_hand"
mv "$TMP" "$SITE/qaf_hand"
/usr/bin/python3 -I -m compileall -q "$SITE/qaf_hand" >/dev/null || true

install -d -m 0755 /etc/qubes-rpc /etc/qubes/rpc-config
for s in $SERVICES; do
    install -m 0755 "$SRC/etc/qubes-rpc/$s" "/etc/qubes-rpc/$s"
    install -m 0644 "$SRC/etc/qubes/rpc-config/$s" "/etc/qubes/rpc-config/$s"
done

# Vérification : le module se charge comme le lancera qrexec.
(cd / && /usr/bin/python3 -I -c 'import qaf_hand.service, qaf_hand.tache, qaf_hand.coureur, qaf_hand.xwd') \
    || { echo "ÉCHEC : python3 -I ne trouve pas qaf_hand" >&2; exit 1; }
echo "qaf_hand installé dans $SITE/qaf_hand ; services : $SERVICES"
for t in xdotool xwd xwininfo; do
    command -v "$t" >/dev/null 2>&1 || echo "ATTENTION : $t absent (Debian : apt install xdotool x11-apps x11-utils)"
done
