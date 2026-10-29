#!/usr/bin/env bash
# Compte les lignes Python de qaf_gate/ (le TCB, tout ce qui est installé dans
# dom0) et échoue au-delà d'un plafond.
# Usage : tools/loc.sh [plafond]   (défaut 3420, ou variable QAF_LOC_PLAFOND)
# Sortie 0 = sous le plafond, 1 = dépassé, 2 = erreur d'usage.
#
# Le plafond porte sur les lignes de code : ni les lignes vides ni les lignes
# de commentaire seul (#) ne comptent, les docstrings comptent. Le total brut
# est affiché à côté. Plafond décidé le 25/10 (PLAN-v2 §9) : 1 500 (D11),
# porté à 3 000 pour la vague 1 d'OPS-v3 (D12), revue de chaque diff maintenue.
# porté à 3 200 après PARCOURS-1 (D19, 25/10), revue de chaque diff maintenue.
# porté à 3 400 pour l'agent jetable (D20, docs/AGENT-JETABLE.md §14-15) : le
# lot J1 et les correctifs de la revue (R-1 à R-11) ajoutent environ 300
# lignes, sans compresser le code ; revue de chaque diff maintenue. Accepté
# (D20, décision déléguée au coordinateur, 25/10).
# porté à 3 420 (D29, 28/10) : suspension des confirmations demandée par l'utilisateur
# (« sudoless » d'Omarchy), dont le contrôle est forcément dans l'exécuteur.
set -euo pipefail

plafond="${1:-${QAF_LOC_PLAFOND:-3420}}"
case "$plafond" in
    ''|*[!0-9]*) echo "plafond invalide : $plafond" >&2; exit 2 ;;
esac

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$racine"
[ -d qaf_gate ] || { echo "qaf_gate/ introuvable dans $racine" >&2; exit 2; }

total=0
brut=0
printf '%6s %6s  %s\n' code brut fichier
while IFS= read -r -d '' f; do
    n=$(grep -cEv '^[[:space:]]*(#|$)' "$f" || true)
    b=$(wc -l < "$f")
    printf '%6d %6d  %s\n' "$n" "$b" "$f"
    total=$((total + n))
    brut=$((brut + b))
done < <(find qaf_gate -name '*.py' -not -path '*/__pycache__/*' -print0 | sort -z)

printf '%6d %6d  total (plafond %d lignes de code)\n' "$total" "$brut" "$plafond"
if [ "$total" -gt "$plafond" ]; then
    echo "ÉCHEC : qaf_gate/ dépasse le plafond de $plafond lignes de code" >&2
    exit 1
fi
