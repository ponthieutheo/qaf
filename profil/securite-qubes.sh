#!/bin/bash
# profil/securite-qubes.sh — audit UI U17 (décision du 25/10) : dans le
# menu, les qubes de service (sys-net, sys-usb, sys-firewall) et les modèles ne
# proposent plus que leur terminal. Navigateur, explorateur de fichiers et autres
# entrées disparaissent du menu (ils restent lançables par qvm-run). Le terminal
# reste : celui d'un modèle sert à installer des logiciels.
#
# La nouvelle liste est calculée à partir de la fonction menu-items actuelle de
# chaque qube, en ne gardant que les entrées dont le nom contient « terminal »
# (qubes-run-terminal, xfce4-terminal, qterminal…). Seule la fonction menu-items
# change (qvm-features) ; sys-usb n'est pas démarré.
#
# La valeur d'origine est sauvegardée dans /var/lib/qaf-profil/menu-items/<qube>
# (« SET<tab>valeur » ou « UNSET ») ; rejouable ; --retour la remet.
#
# Non traités ici : U31 (profil/peripheriques.sh), U46 (profil/jetable-defaut.sh),
# U58 (profil/politique-fichiers.sh).
set -e
SAUVE=/var/lib/qaf-profil/menu-items

if [ "${1:-}" = "--retour" ]; then
    for f in "$SAUVE"/*; do
        [ -f "$f" ] || continue
        q=$(basename "$f")
        qvm-check -q "$q" 2>/dev/null || continue
        ligne=$(sudo -n cat "$f")
        case "$ligne" in
            SET$'\t'*) qvm-features "$q" menu-items "${ligne#SET$'\t'}" ;;
            UNSET) qvm-features --unset "$q" menu-items ;;
        esac
    done
    sudo -n rm -rf "$SAUVE"
    echo "menu-items d'origine remis."
    exit 0
fi

# Les modèles Whonix sont exclus : Sysmaint Panel, systemcheck et les assistants
# Whonix font partie du parcours normal de maintenance de Whonix (règle n° 1).
# sys-whonix n'a jamais été concerné.
EXCLUS='^whonix-'
QUBES="sys-net sys-usb sys-firewall $(qvm-ls --raw-data --fields NAME,CLASS | awk -F'|' '$2=="TemplateVM"{print $1}' | grep -Ev "$EXCLUS")"
sudo -n install -d -m 0755 "$SAUVE"
# Qube exclu après coup (version précédente du script) : menu d'origine remis.
for f in "$SAUVE"/*; do
    [ -f "$f" ] || continue
    q=$(basename "$f")
    echo "$q" | grep -Eq "$EXCLUS" || continue
    ligne=$(sudo -n cat "$f")
    case "$ligne" in
        SET$'	'*) qvm-features "$q" menu-items "${ligne#SET$'	'}" ;;
        UNSET) qvm-features --unset "$q" menu-items ;;
    esac
    sudo -n rm -f "$f"
    echo "$q : menu d'origine remis (exclu)"
done
for q in $QUBES; do
    qvm-check -q "$q" 2>/dev/null || continue
    if [ ! -f "$SAUVE/$q" ]; then
        if v=$(qvm-features "$q" menu-items 2>/dev/null); then
            printf 'SET\t%s\n' "$v" | sudo -n tee "$SAUVE/$q" >/dev/null
        else
            echo UNSET | sudo -n tee "$SAUVE/$q" >/dev/null
        fi
    fi
    # Calcul à partir de la valeur sauvegardée (rejeu identique).
    ligne=$(sudo -n cat "$SAUVE/$q")
    [ "$ligne" = UNSET ] && avant="" || avant=${ligne#SET$'\t'}
    garde=$(for e in $avant; do case "$e" in *terminal*) printf '%s ' "$e" ;; esac; done)
    garde=${garde% }
    qvm-features "$q" menu-items "$garde"
    echo "$q : $garde"
done
