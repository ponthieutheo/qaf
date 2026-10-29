#!/bin/bash
# profil/peripheriques.sh — audit UI U31 (décision du 25/10) : plus
# d'attache de périphériques (USB, bloc, micro, caméra) vers sys-agent, sys-net
# et sys-firewall, que ce soit depuis le widget, Qube Manager ou qvm-device.
#
# Mécanisme de Qubes 4.3 : la propriété devices_denied de la cible (liste de
# codes d'interface de 7 caractères, collés sans séparateur ; « * » = joker).
# qubesd la vérifie dans ext/admin.py (on_device_attach) pour toute attache
# manuelle reçue par l'API admin.vm.device.<classe>.Attach. Les widgets de dom0
# passent par cette API : une politique qrexec sur admin.vm.device.* ne les
# verrait pas (dom0 parle à qubesd directement), devices_denied si.
# Codes posés : u****** (tout l'USB, dont caméras u06/u0e), b****** (bloc),
# m****** (micro). Aucun code p (PCI).
#
# Ne touche ni sys-usb ni le PCI (laptop Legion, voir materiel/lenovo-82ju.sh) :
#  - aucune propriété de sys-usb n'est lue ou changée, il n'est pas démarré ;
#  - le refus ne vise que les attaches « manual » : les attaches persistantes
#    (required, auto-attach), comme les cartes réseau PCI de sys-net, ne passent
#    pas par ce contrôle, et le code PCI n'est de toute façon pas refusé.
#    Une clé réseau USB attachée en persistant à sys-net (usage documenté)
#    reste donc possible.
# U31 prévoyait aussi le démarrage automatique de sys-usb : NON appliqué
# (sys-usb fait dérailler le Legion, consigne du 25/10).
#
# Valeur d'origine sauvegardée dans /var/lib/qaf-profil/devices_denied ; rejouable ;
# --retour la remet.
set -e
SAUVE=/var/lib/qaf-profil/devices_denied
QUBES="sys-agent sys-net sys-firewall"
REFUS="u******b******m******"

if [ "${1:-}" = "--retour" ]; then
    sudo -n test -f "$SAUVE/etat.json" || { echo "Rien à remettre ($SAUVE/etat.json absent)."; exit 0; }
    sudo -n cat "$SAUVE/etat.json" > /tmp/qaf-etat.json
    python3 - /tmp/qaf-etat.json <<'PY'
import json, sys, qubesadmin
app = qubesadmin.Qubes()
for nom, e in json.load(open(sys.argv[1])).items():
    if nom not in app.domains:
        continue
    vm = app.domains[nom]
    if e["defaut"]:
        del vm.devices_denied
    else:
        vm.devices_denied = e["valeur"]
    print(nom, "devices_denied remis à", repr(e["valeur"]))
PY
    sudo -n rm -rf "$SAUVE"; rm -f /tmp/qaf-etat.json
    exit 0
fi

if ! sudo -n test -f "$SAUVE/etat.json"; then
    sudo -n install -d -m 0755 "$SAUVE"
    python3 - $QUBES <<'PY' | sudo -n tee "$SAUVE/etat.json" >/dev/null
import json, sys, qubesadmin
app = qubesadmin.Qubes()
etat = {}
for nom in sys.argv[1:]:
    if nom in app.domains:
        vm = app.domains[nom]
        etat[nom] = {"defaut": vm.property_is_default("devices_denied"),
                     "valeur": vm.devices_denied or ""}
print(json.dumps(etat, indent=1, sort_keys=True))
PY
fi

python3 - "$REFUS" $QUBES <<'PY'
import sys, qubesadmin
app = qubesadmin.Qubes()
refus = sys.argv[1]
for nom in sys.argv[2:]:
    if nom not in app.domains:
        continue
    vm = app.domains[nom]
    avant = vm.devices_denied or ""
    # On ajoute nos codes à ceux déjà posés (qubesd dédoublonne).
    vm.devices_denied = avant + refus
    print(nom, "devices_denied :", repr(vm.devices_denied))
PY
