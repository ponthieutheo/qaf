#!/bin/bash
# setup-qaf-dvm.sh — crée le modèle de jetable DÉDIÉ du Doorman (à lancer dans dom0, par l'utilisateur).
#
#   tpl-qaf-hand : clone de debian-13-xfce + xdotool/xwd/xwininfo + qaf_hand
#                 (tools/install-qaf-hand.sh : site-packages système, /etc/qubes-rpc/qaf.*)
#   qaf-dvm      : AppVM sur tpl-qaf-hand, template_for_dispvms=True, SANS réseau
#                 (C3), label red. C'est backend_qubes.MODELE_JETABLE :
#                 dispvm.run_task sans « template » crée ses jetables ici,
#                 jamais sur default_dispvm.
#
# Prérequis : l'archive dist/qaf.tar.gz construite par tools/make-dist.sh, dans
# dom0 (QAF_ARCHIVE) ou dans le qube qui l'a construite (QAF_SOURCE_QUBE).
# Usage : bash setup-qaf-dvm.sh <sha256 attendu de dist/qaf.tar.gz>
# L'empreinte doit venir d'une source indépendante de ce qube (C12).
#
# Réseau : qaf-dvm n'en a pas. Le futur coureur (LLM) parlera à son relais par
# qubes.ConnectTCP (C8), pas par une netvm. Une tâche web (« ouvre ce site »)
# demandera un second modèle avec réseau, nommé explicitement dans le plan
# (classé N3 par le Doorman) : à décider par l'utilisateur, rien n'est créé ici.
set -euo pipefail
ATTENDU=${1:?usage : setup-qaf-dvm.sh <sha256 attendu de dist/qaf.tar.gz>}
BASE=${QAF_TPL_BASE:-debian-13-xfce}
TPL=tpl-qaf-hand
DVM=qaf-dvm
ARCH=$(mktemp /tmp/qaf-dvm.XXXXXX.tar.gz)
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

# 1. Modèle : outils X et qaf_hand dans le système (persistant dans un template).
qvm-ls --raw-list | grep -qx "$TPL" || qvm-clone "$BASE" "$TPL"
qvm-prefs "$TPL" label black
qvm-start --skip-if-running "$TPL"
qvm-run -u root --pass-io --no-gui "$TPL" \
  'apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q xdotool x11-apps x11-utils python3 >/dev/null' \
  </dev/null
qvm-run -u root --pass-io --no-gui "$TPL" \
  'rm -rf /tmp/qaf-dvm && mkdir /tmp/qaf-dvm && tar -xz -C /tmp/qaf-dvm \
   && sh /tmp/qaf-dvm/qaf/tools/install-qaf-hand.sh /tmp/qaf-dvm/qaf && rm -rf /tmp/qaf-dvm' < "$ARCH"
qvm-shutdown --wait "$TPL"

# 2. Modèle de jetable dédié.
qvm-ls --raw-list | grep -qx "$DVM" || qvm-create --class AppVM --template "$TPL" --label red "$DVM"
qvm-prefs "$DVM" template "$TPL"
qvm-prefs "$DVM" netvm ''
qvm-prefs "$DVM" template_for_dispvms True
qvm-prefs "$DVM" default_dispvm ''
echo "$DVM prêt : $(qvm-prefs "$DVM" template_for_dispvms) / netvm='$(qvm-prefs "$DVM" netvm)'"
# Pas « qvm-run --dispvm=… --service qaf.Task » pour essayer : dans dom0, qvm-run
# tue le jetable dès la fin de l'appel (DispVMWrapper.cleanup), donc le coureur
# détaché avec lui. Essai par le vrai chemin du backend :
echo "Essai (le jetable s'éteint seul à la fin de la tâche, puis qubesd le supprime) :"
echo "  sudo -n -u qaf-exec env PYTHONPATH=/usr/lib/qaf python3 -c 'from qaf_gate.backend_qubes import QubesBackend as Q; print(Q().run_dispvm_task(None, \"lister\\nexec uname -n\"))'"
