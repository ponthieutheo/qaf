#!/usr/bin/env bash
# Rendu hors écran d'un écran QAF vers PNG, avec mesure des contrastes.
# Sur la machine de développement (Linux ou WSL), jamais dans dom0 ; jamais sur l'écran réel : xvfb-run
# seulement, WSLg neutralisé (WAYLAND_DISPLAY vidé).
#
# Usage : tools/rendu-ui.sh ECRAN sortie.png [--contraste rapport.json] [--exiger] [--plein] [--langue fr|en|zh]
#         tools/rendu-ui.sh --installer   (paquets de rendu dans WSL, sudo)
# ECRAN : « module:fonction » (voir tools/rendu_ui.py).
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAQUETS="xvfb xauth gir1.2-gtk-3.0 python3-gi python3-gi-cairo fonts-inter papirus-icon-theme"

if [ "${1:-}" = "--installer" ]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PAQUETS
    exit 0
fi
[ $# -ge 2 ] || { sed -n '5,7p' "$0" >&2; exit 2; }

manque=""
command -v xvfb-run >/dev/null || manque="xvfb"
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null \
    || manque="$manque gir1.2-gtk-3.0 python3-gi"
if [ -n "$manque" ]; then
    echo "manque :$manque — lancer : tools/rendu-ui.sh --installer" >&2
    exit 3
fi

# Python du système (celui qui a python3-gi), pas celui d'uv. Écran virtuel
# 1440x900 comme les maquettes, ou QAF_RENDU_ECRAN=LxH (p. ex. 1366x768 ou
# 1920x1080) ; aucune variable de l'écran réel ne passe.
ecran="${QAF_RENDU_ECRAN:-1440x900}"
[[ "$ecran" =~ ^[0-9]{3,4}x[0-9]{3,4}$ ]] || { echo "QAF_RENDU_ECRAN : LxH attendu" >&2; exit 2; }
exec env -u WAYLAND_DISPLAY -u DISPLAY GDK_BACKEND=x11 NO_AT_BRIDGE=1 \
    xvfb-run -a -s "-screen 0 ${ecran}x24" \
    /usr/bin/python3 -I "$racine/tools/rendu_ui.py" "$@"
