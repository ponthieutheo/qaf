#!/bin/bash
# install-qaf-hand-modeles.sh — pose qaf_hand dans des modèles d'origine de
# l'utilisateur (debian-13-xfce, fedora-44-xfce…) pour que l'agent puisse y entrer
# sous bail (D16, OPS-v3 §10). À lancer dans dom0, par l'utilisateur.
#
#   bash install-qaf-hand-modeles.sh <sha256 de dist/qaf.tar.gz> MODÈLE…   installe
#   bash install-qaf-hand-modeles.sh --retour MODÈLE…                      retire
#
# Ce qui est ajouté dans chaque modèle, et rien d'autre (règle n°1, D16) :
#   - qaf_hand et ses services qrexec (tools/install-qaf-hand.sh : site-packages
#     système, /etc/qubes-rpc/qaf.*, /etc/qubes/rpc-config/qaf.*) ;
#   - xdotool, xwd, xwininfo (Debian : xdotool x11-apps x11-utils ; Fedora :
#     xdotool xwd xwininfo), par le proxy de mises à jour habituel du modèle ;
#   - /var/lib/qaf-hand/paquets-ajoutes : paquets absents AVANT, dépendances
#     comprises (état d'origine, écrit une seule fois), pour --retour.
# Les services qaf.* restent refusés à tout qube par 00-qaf.policy : seul dom0
# (qaf-gate-taches) les appelle.
#
# --retour : retire qaf_hand, ses services, et les seuls paquets que ce script a
# ajoutés (dpkg --remove / rpm -e : refusent si un paquet installé depuis en
# dépend ; dans ce cas ils restent, et le script le dit).
#
# Seuls les TemplateVM sont acceptés. Aucun autre qube n'est démarré (ni sys-usb,
# ni sys-net : les paquets passent par le proxy de mises à jour du modèle). Un
# modèle arrêté avant est arrêté après. L'empreinte de l'archive doit venir
# d'une source indépendante du qube qui l'a construite (C12). Archive :
# QAF_ARCHIVE ou QAF_SOURCE_QUBE, comme setup-qaf-dvm.sh.
set -euo pipefail

usage() { echo "usage : $0 <sha256> MODÈLE… | $0 --retour MODÈLE…" >&2; exit 2; }
[ $# -ge 2 ] || usage
MODE=installer
if [ "$1" = "--retour" ]; then MODE=retour; shift
else ATTENDU=$1; shift
     [[ "$ATTENDU" =~ ^[0-9a-f]{64}$ ]] || usage
fi

for M in "$@"; do
    [[ "$M" =~ ^[a-zA-Z][a-zA-Z0-9_.-]{0,30}$ ]] || { echo "nom de modèle invalide : $M" >&2; exit 1; }
    [ "$(qvm-prefs "$M" klass 2>/dev/null)" = TemplateVM ] || { echo "$M n'est pas un TemplateVM : rien n'est fait." >&2; exit 1; }
done

if [ "$MODE" = installer ]; then
    ARCH=$(mktemp /tmp/qaf-hand.XXXXXX.tar.gz)
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
    tar -tzf "$ARCH" qaf/qaf_hand/service.py qaf/tools/install-qaf-hand.sh >/dev/null
fi

# Script lancé en root dans le modèle. Liste des paquets avant/après pour ne
# retirer au retour que ce qui a été ajouté ici.
read -r -d '' DANS_MODELE_INSTALLER <<'FIN' || true
set -eu
ETAT=/var/lib/qaf-hand
# L'archive arrive sur l'entrée standard : la lire d'abord, car apt-get et les
# scripts des paquets lisent eux aussi stdin (essai réel du 25/10 : archive
# avalée, « not in gzip format »).
ARCH=$(mktemp /tmp/qaf-hand.XXXXXX.tar.gz)
cat > "$ARCH"
paquets() { if command -v dpkg-query >/dev/null; then dpkg-query -W -f '${Package}\n'; else rpm -qa --qf '%{NAME}\n'; fi | sort -u; }
mkdir -p "$ETAT"
paquets > "$ETAT/.avant"
if command -v apt-get >/dev/null; then
    apt-get update -q </dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-remove xdotool x11-apps x11-utils </dev/null >/dev/null
else
    dnf -y -q install xdotool xwd xwininfo </dev/null >/dev/null
fi
# État d'origine : écrit une seule fois (une réinstallation ne l'écrase pas).
[ -e "$ETAT/paquets-ajoutes" ] || paquets | comm -13 "$ETAT/.avant" - > "$ETAT/paquets-ajoutes"
rm -f "$ETAT/.avant"
rm -rf /tmp/qaf-hand && mkdir /tmp/qaf-hand && tar -xzf "$ARCH" -C /tmp/qaf-hand && rm -f "$ARCH"
sh /tmp/qaf-hand/qaf/tools/install-qaf-hand.sh /tmp/qaf-hand/qaf
rm -rf /tmp/qaf-hand
FIN

read -r -d '' DANS_MODELE_RETOUR <<'FIN' || true
set -eu
ETAT=/var/lib/qaf-hand
SITE=$(/usr/bin/python3 -I -c 'import re, sys
for p in sys.path:
    if re.fullmatch(r"/usr/lib/python3(\.[0-9]+)?/(dist|site)-packages", p):
        print(p); break')
[ -n "$SITE" ] && rm -rf "$SITE/qaf_hand"
for s in qaf.Screen qaf.Input qaf.Exec qaf.Task; do rm -f "/etc/qubes-rpc/$s" "/etc/qubes/rpc-config/$s"; done
if [ -s "$ETAT/paquets-ajoutes" ]; then
    if command -v dpkg >/dev/null; then dpkg --remove $(cat "$ETAT/paquets-ajoutes") || echo "ATTENTION : paquets gardés (un autre en dépend)"
    else rpm -e $(cat "$ETAT/paquets-ajoutes") || echo "ATTENTION : paquets gardés (un autre en dépend)"; fi
fi
rm -rf "$ETAT"
echo "qaf_hand retiré"
FIN

# qvm-run passe par qubes.VMShell : le shell du modèle lit la commande sur la
# MÊME entrée standard que l'archive, ligne par ligne. Un script de plusieurs
# lignes y serait donc avalé par le premier « cat » (essai réel du 25/10 : rien
# installé, code 0). La commande tient donc sur une ligne : le script est posé
# en base64 dans un fichier, puis lancé avec l'archive pour seule entrée.
une_ligne() { printf 'echo %s | base64 -d > /tmp/qaf-hand-modele.sh && bash /tmp/qaf-hand-modele.sh; R=$?; rm -f /tmp/qaf-hand-modele.sh; exit $R' \
    "$(printf '%s\n' "$1" | base64 -w0)"; }

ECHECS=0
for M in "$@"; do
    ETAIT_ARRETE=0
    qvm-check --quiet --running "$M" 2>/dev/null || ETAIT_ARRETE=1
    [ "$ETAIT_ARRETE" = 1 ] && qvm-start "$M"
    R=0
    if [ "$MODE" = installer ]; then
        qvm-run -u root --pass-io --no-gui "$M" "$(une_ligne "$DANS_MODELE_INSTALLER")" < "$ARCH" || R=$?
    else
        qvm-run -u root --pass-io --no-gui "$M" "$(une_ligne "$DANS_MODELE_RETOUR")" </dev/null || R=$?
    fi
    # Arrêté avant, arrêté après, même en cas d'échec.
    [ "$ETAIT_ARRETE" = 1 ] && qvm-shutdown --wait "$M"
    if [ "$R" = 0 ]; then echo "$M : $MODE fait (les espaces basés dessus l'auront après leur redémarrage)"
    else echo "$M : ÉCHEC de $MODE (code $R)" >&2; ECHECS=1; fi
done
exit "$ECHECS"
