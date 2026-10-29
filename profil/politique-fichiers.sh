#!/bin/bash
# profil/politique-fichiers.sh — audit UI U58 (décision du 25/10) :
# installe profil/25-qaf-fichiers.policy dans /etc/qubes/policy.d/ (fichier QAF à
# part, aucun fichier de Qubes modifié), après passage à qubes-policy-lint.
# Un fichier du même nom déjà présent est sauvegardé dans
# /var/lib/qaf-profil/policy ; --retour le remet, ou retire le nôtre.
set -e
ICI=$(cd "$(dirname "$0")" && pwd)
NOM=25-qaf-fichiers.policy
DEST=/etc/qubes/policy.d/$NOM
SAUVE=/var/lib/qaf-profil/policy

if [ "${1:-}" = "--retour" ]; then
    if [ -f "$SAUVE/$NOM" ]; then
        sudo -n install -m 0644 "$SAUVE/$NOM" "$DEST"
    else
        sudo -n rm -f "$DEST"
    fi
    sudo -n rm -rf "$SAUVE"
    echo "$NOM retiré (état d'origine remis)."
    exit 0
fi

# Règle d'install.sh : avant 00-qaf.policy, rien sur sys-agent ni @anyvm.
# Ce fichier passe après ; il ne doit de toute façon nommer ni l'un ni l'autre en source.
if grep -Ev '^[[:space:]]*(#|$)' "$ICI/$NOM" | awk '{print $3}' | grep -Eq 'sys-agent|@anyvm'; then
    echo "$NOM ne doit pas viser sys-agent ni @anyvm en source." >&2
    exit 1
fi
qubes-policy-lint -s "$ICI/$NOM"
if [ -f "$DEST" ] && ! cmp -s "$ICI/$NOM" "$DEST" && [ ! -f "$SAUVE/$NOM" ]; then
    sudo -n install -d -m 0755 "$SAUVE"
    sudo -n cp -p "$DEST" "$SAUVE/$NOM"
fi
sudo -n install -m 0644 "$ICI/$NOM" "$DEST"
echo "$NOM installé :"
grep -Ev '^[[:space:]]*(#|$)' "$DEST"
