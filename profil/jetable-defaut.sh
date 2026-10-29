#!/bin/bash
# profil/jetable-defaut.sh — audit UI U46 (décision du 25/10) : jetable
# par défaut = default-dvm (qubes-prefs default_dispvm), pour qu'« ouvrir dans
# un jetable » marche depuis les espaces ordinaires.
#
# Exceptions, gardées sans changement de comportement :
#  - sys-agent et vault restent sans jetable par défaut ;
#  - les qubes sys-* gardent leur valeur d'origine.
# Pour chacun, une valeur héritée est d'abord posée explicitement (même valeur
# qu'avant), puis seulement la valeur globale change.
#
# Avant tout changement, l'état d'origine (valeur globale et, pour chaque qube,
# valeur explicite ou héritée) est sauvegardé dans /var/lib/qaf-profil/default_dispvm.
# Rejouable ; --retour remet exactement l'état sauvegardé.
# N'édite aucun fichier livré par Qubes : seulement qubes-prefs et qvm-prefs.
set -e
SAUVE=/var/lib/qaf-profil/default_dispvm
MODELE=default-dvm

if [ "${1:-}" = "--retour" ]; then
    sudo -n test -f "$SAUVE/etat.json" || { echo "Rien à remettre ($SAUVE/etat.json absent)."; exit 0; }
    sudo -n cat "$SAUVE/etat.json" > /tmp/qaf-etat.json
    python3 - /tmp/qaf-etat.json <<'PY'
import json, sys, qubesadmin
etat = json.load(open(sys.argv[1]))
app = qubesadmin.Qubes()
g = etat["global"]
if g["defaut"]:
    del app.default_dispvm
else:
    app.default_dispvm = g["valeur"] or ""
for nom, e in etat["qubes"].items():
    if nom not in app.domains:
        continue
    vm = app.domains[nom]
    if e["defaut"]:
        del vm.default_dispvm
    else:
        vm.default_dispvm = e["valeur"] or ""
print("default_dispvm d'origine remis (global et", len(etat["qubes"]), "qubes).")
PY
    sudo -n rm -rf "$SAUVE"; rm -f /tmp/qaf-etat.json
    exit 0
fi

# 1. Le modèle de jetable doit exister et en être un.
python3 - "$MODELE" <<'PY'
import sys, qubesadmin
app = qubesadmin.Qubes()
nom = sys.argv[1]
if nom not in app.domains:
    sys.exit(f"{nom} n'existe pas : rien changé.")
vm = app.domains[nom]
if vm.klass != "AppVM" or not vm.template_for_dispvms:
    sys.exit(f"{nom} n'est pas un modèle de jetable (classe {vm.klass}, template_for_dispvms {vm.template_for_dispvms}) : rien changé.")
PY

# 2. Sauvegarde de l'état d'origine (une seule fois : un rejeu ne l'écrase pas).
if ! sudo -n test -f "$SAUVE/etat.json"; then
    sudo -n install -d -m 0755 "$SAUVE"
    python3 - <<'PY' | sudo -n tee "$SAUVE/etat.json" >/dev/null
import json, qubesadmin
app = qubesadmin.Qubes()
def val(x):
    return str(x) if x else ""
etat = {"global": {"defaut": app.property_is_default("default_dispvm"),
                   "valeur": val(app.default_dispvm)}, "qubes": {}}
for vm in app.domains:
    if vm.klass == "AdminVM":
        continue
    etat["qubes"][vm.name] = {"defaut": vm.property_is_default("default_dispvm"),
                              "valeur": val(vm.default_dispvm)}
print(json.dumps(etat, indent=1, sort_keys=True))
PY
fi

# 3. Exceptions figées, puis 4. valeur globale.
python3 - "$MODELE" <<'PY'
import sys, qubesadmin
app = qubesadmin.Qubes()
figes = []
for vm in app.domains:
    if vm.klass == "AdminVM":
        continue
    if vm.name in ("sys-agent", "vault") or vm.name.startswith("sys-"):
        if vm.name in ("sys-agent", "vault"):
            cible = ""          # toujours sans jetable par défaut
        else:
            cible = str(vm.default_dispvm) if vm.default_dispvm else ""
        if vm.property_is_default("default_dispvm") or (vm.name in ("sys-agent", "vault") and vm.default_dispvm):
            vm.default_dispvm = cible
        figes.append(f"{vm.name}={cible or '(aucun)'}")
app.default_dispvm = sys.argv[1]
print("Jetable par défaut :", app.default_dispvm, "; valeurs figées :", ", ".join(figes))
PY
