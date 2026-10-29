# materiel/lenovo-82ju.sh — profil matériel du Lenovo Legion 5 15ACH6H (82JU).
# Lu (source) par tools/qaf-installer quand le DMI vaut sys_vendor=LENOVO,
# product_name=82JU. Couche « matériel » : rien ici n'est livré aux autres
# machines.
# Particularités relevées sur ce modèle :
#  - sys-usb fait dérailler ce laptop : ne jamais le démarrer, ne pas activer son
#    démarrage automatique, ne toucher à aucune attache PCI ni USB (contrôleurs
#    de sys-usb et de sys-net) ;
#  - les réglages de GRUB, de Xen et de qmemman posés pour que la machine marche
#    ne se modifient pas sans l'accord de l'utilisateur ;
#  - deux disques internes : nvme0n1 = SSD Windows (476,9 G), nvme1n1 = /boot
#    de dom0. QAF ne les monte, n'attache ni n'écrit jamais : il les retire
#    seulement de la liste des périphériques proposés aux qubes (U01,
#    docs/AGENT-JETABLE.md §13.6).
# Variables lues par l'installateur :
#   MATERIEL_NOM                 nom affiché
#   MATERIEL_NE_JAMAIS_DEMARRER  ajouté à ne_jamais_demarrer de /etc/qaf/portier.json ;
#                                l'installateur s'arrête si l'un d'eux démarre
# Fonctions appelées par l'installateur (en root) :
#   materiel_verifier RACINE     lecture seule, avertit sans corriger
#   materiel_appliquer RACINE    masque les disques internes (règle udev à part)
#   materiel_retirer RACINE      retire ce masquage : seulement par
#                                « qaf-installer --retour --materiel » (explicite)
# shellcheck shell=bash disable=SC2034

MATERIEL_NOM="Lenovo Legion 5 15ACH6H (82JU)"
MATERIEL_NE_JAMAIS_DEMARRER="sys-usb"
MATERIEL_UDEV=etc/udev/rules.d/90-qaf-disque-interne.rules
# Clavier interne : sur ce laptop, les touches passent par le contrôleur ITE
# branché sur un port USB INTERNE (relevé du 27/10 : flèches, menu), pas par le
# clavier PS/2 « AT Raw Set 2 keyboard » seul déclaré par défaut. Ajouté comme
# second clavier de confiance (qaf_ui/confirmation.py), verrouillé sur son nom et
# son emplacement : un clavier USB branché sur un port externe a un autre
# emplacement (Phys attribué par le noyau) et reste refusé.
MATERIEL_CLAVIER=etc/qaf/clavier-de-confiance
MATERIEL_CLAVIER_ITE="ITE Tech. Inc. ITE Device(8910) Keyboard"
MATERIEL_CLAVIER_PHYS="usb-0000:06:00.3-4/input0"
MATERIEL_CLAVIER_DEBUT="# --- QAF matériel lenovo-82ju : début ---"
MATERIEL_CLAVIER_FIN="# --- QAF matériel lenovo-82ju : fin ---"

materiel_clavier_poser() {  # ajoute le bloc ITE (une fois) au fichier de confiance
    local f="${1:-}/$MATERIEL_CLAVIER"
    if [ ! -f "$f" ]; then   # absent = clavier PS/2 par défaut (confirmation.py) : écrit tel quel
        install -d -m 0755 "$(dirname "$f")"
        printf 'AT Raw Set 2 keyboard\nphys=isa0060/serio0\n' > "$f"
        chmod 0644 "$f"
    fi
    grep -qxF "$MATERIEL_CLAVIER_DEBUT" "$f" && return 0
    printf '%s\n%s\nphys=%s\n%s\n' "$MATERIEL_CLAVIER_DEBUT" "$MATERIEL_CLAVIER_ITE" \
        "$MATERIEL_CLAVIER_PHYS" "$MATERIEL_CLAVIER_FIN" >> "$f"
    echo "   Clavier interne ITE ajouté aux claviers de confiance ($MATERIEL_CLAVIER_PHYS)."
}

materiel_clavier_retirer() {
    local f="${1:-}/$MATERIEL_CLAVIER"
    [ -f "$f" ] && grep -qxF "$MATERIEL_CLAVIER_DEBUT" "$f" || return 0
    sed -i "/^$MATERIEL_CLAVIER_DEBUT\$/,/^$MATERIEL_CLAVIER_FIN\$/d" "$f"
    echo "   Clavier interne ITE retiré des claviers de confiance."
}

materiel_verifier() {
    local r=${1:-} a
    if a=$(qvm-prefs sys-usb autostart 2>/dev/null) && [ "$a" = True ]; then
        echo "  ATTENTION : sys-usb démarre automatiquement (autostart True). Sur ce laptop il fait"
        echo "  dérailler la machine. Rien n'est changé : à décider par l'utilisateur (qvm-prefs sys-usb autostart False)."
    fi
    if grep -q 'rd.qubes.hide_all_usb' "$r/proc/cmdline" 2>/dev/null; then
        echo "  ATTENTION : rd.qubes.hide_all_usb est actif ; sur la machine de référence il est absent."
    fi
    if ! grep -q 'nouveau' "$r/proc/cmdline" 2>/dev/null; then
        echo "  Remarque : nouveau n'est pas en liste noire (RTX 3060) ; sur la machine de référence il l'est."
    fi
    echo "  Aucune attache PCI ni USB n'est touchée ; sys-usb n'est jamais démarré."
}

materiel_disques() {  # disques NVMe/SATA non amovibles et hors USB (noms du noyau)
    local r=${1:-} b
    for b in "$r"/sys/block/nvme* "$r"/sys/block/sd*; do
        [ -e "$b" ] || continue
        [ "$(cat "$b/removable" 2>/dev/null)" = "0" ] || continue
        case "$(readlink -f "$b/device" 2>/dev/null)" in *usb*) continue ;; esac
        basename "$b"
    done
}

# Règle passée avant 99-qubes-block.rules (QUBES_EXPORT_BLOCK_DEVICE=0 : ignoré
# par udev-block-add-change) et après 60-persistent-storage (qui pose ID_WWN,
# sur le disque ET ses partitions : sonde J-6). Les disques sont désignés par
# ID_WWN, stable d'un démarrage à l'autre (l'ordre nvme0/nvme1 peut
# s'inverser), relevé ici sur la machine (jamais écrit dans le dépôt), et en
# plus par nom de noyau, partitions comprises, par prudence. Ni montage ni
# /boot touchés. Aucun « udevadm trigger » : la liste de Qubes est nettoyée
# par qubesdb-rm, comme udev-block-remove, sans événement sur les disques.
materiel_appliquer() {
    local r=${1:-} f d w disques
    f="$r/$MATERIEL_UDEV"
    disques=$(materiel_disques "$r")
    install -d -m 0755 "$(dirname "$f")"
    {
        echo "# QAF (materiel/lenovo-82ju.sh) : disques internes, dont le SSD Windows, jamais proposés aux qubes."
        echo "# Retrait : sudo qaf-installer --retour --materiel"
        for d in $disques; do
            w=""
            [ -z "$r" ] && w=$(udevadm info --query=property --name="/dev/$d" 2>/dev/null | sed -n 's/^ID_WWN=//p')
            if [[ "$w" =~ ^[A-Za-z0-9._:-]+$ ]]; then
                echo "SUBSYSTEM==\"block\", ENV{ID_WWN}==\"$w\", ENV{QUBES_EXPORT_BLOCK_DEVICE}=\"0\""
            fi
            echo "SUBSYSTEM==\"block\", KERNEL==\"${d}*\", ENV{QUBES_EXPORT_BLOCK_DEVICE}=\"0\""
        done
    } > "$f.tmp"
    chmod 0644 "$f.tmp"
    mv "$f.tmp" "$f"
    materiel_clavier_poser "$r"
    [ -n "$r" ] && { echo "   [essai] règle udev écrite (disques :${disques:+ }$(echo $disques))"; return 0; }
    udevadm control --reload
    for d in $disques; do
        for n in $(qubesdb-list /qubes-block-devices/ 2>/dev/null | cut -d/ -f1 | sort -u | grep "^$d" || true); do
            qubesdb-rm "/qubes-block-devices/$n/"
        done
    done
    qubesdb-write /qubes-block-devices ''
    echo "   Disques internes retirés de la liste des périphériques :" $disques
}

materiel_retirer() {
    local r=${1:-}
    rm -f "$r/$MATERIEL_UDEV" "$r/$MATERIEL_UDEV.tmp"
    materiel_clavier_retirer "$r"
    [ -z "$r" ] && udevadm control --reload
    echo "   Masquage des disques internes retiré : ils redeviendront attachables à un qube"
    echo "   au prochain démarrage (aucun événement udev n'est relancé maintenant)."
}

materiel_controler() {  # lecture seule, pour --verifier ; code 1 si écart
    local r=${1:-} ecart=0
    if [ -f "$r/$MATERIEL_UDEV" ]; then echo "  OK       règle de masquage des disques internes présente"
    else echo "  ÉCHEC    règle $MATERIEL_UDEV absente : le SSD Windows peut être proposé à un qube"; ecart=1; fi
    if grep -qxF "$MATERIEL_CLAVIER_ITE" "$r/$MATERIEL_CLAVIER" 2>/dev/null; then
        echo "  OK       clavier interne ITE parmi les claviers de confiance"
    else echo "  ÉCHEC    clavier interne ITE absent de $MATERIEL_CLAVIER : ni menu ni autorisation au clavier"; ecart=1; fi
    return "$ecart"
}
