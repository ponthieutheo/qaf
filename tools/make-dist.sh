#!/bin/sh
# make-dist.sh — construit dist/qaf.tar.gz et son sha256, dans le qube qui livre
# l'archive comme sur une autre machine (pour obtenir l'empreinte de référence).
#
# L'archive est reproductible : git archive d'un commit (dates = date du
# commit, propriétaire 0) puis gzip -n (sans nom ni date). Le même commit
# donne donc la même empreinte partout : c'est ce qui permet de vérifier
# l'archive contre une source indépendante (C12).
# Réserve : à confirmer que deux versions différentes de git/gzip produisent
# bien les mêmes octets (comparer une fois).
#
# qaf.tar.gz : le produit (dont profil/ et tools/qaf-installer) et les profils
# matériels (materiel/ : qaf-installer n'en pose un que sur la machine qu'il
# nomme). Une extension n'y est JAMAIS : chaque dossier de la racine qui porte
# un extension.conf part à part dans dist/qaf-<dossier>.tar.gz (sa propre
# empreinte), que seul « qaf-installer --extension=… » lit. Sans extension,
# seule qaf.tar.gz est construite.
set -eu

cd "$(dirname "$0")/.."
LIVRES="qaf_gate qaf_mcp qaf_hand qaf_ui packaging policy tools profil materiel LICENCES-TIERS"
COMMIT=$(git rev-parse HEAD)
# Extensions suivies par git dans ce commit (dossier de la racine avec extension.conf).
EXTENSIONS=$(git ls-tree -r --name-only "$COMMIT" | sed -n 's#^\([A-Za-z0-9_-]*\)/extension\.conf$#\1#p')
# shellcheck disable=SC2086
if [ -n "$(git status --porcelain -- $LIVRES $EXTENSIONS)" ]; then
    echo "Modifications non commitées dans $LIVRES $EXTENSIONS : committe d'abord." >&2
    exit 1
fi
mkdir -p dist
# Indépendant de la configuration git locale : umask des entrées fixé, aucune
# conversion de fin de ligne (et .gitattributes : -text sur les dossiers livrés).
archive() {  # archive NOM CHEMIN… -> dist/NOM.tar.gz et son .sha256
    nom=$1
    shift
    git -c tar.umask=0022 -c core.autocrlf=false -c core.eol=lf \
        archive --format=tar --prefix=qaf/ "$COMMIT" "$@" \
        | gzip -n -9 > "dist/$nom.tar.gz.tmp"
    mv "dist/$nom.tar.gz.tmp" "dist/$nom.tar.gz"
    (cd dist && sha256sum "$nom.tar.gz" > "$nom.tar.gz.sha256")
}
# shellcheck disable=SC2086
archive qaf $LIVRES
for e in $EXTENSIONS; do archive "qaf-$e" "$e/"; done

echo "commit      : $COMMIT"
echo "archive     : dist/qaf.tar.gz ($(wc -c < dist/qaf.tar.gz) octets)"
echo "sha256      : $(cut -d' ' -f1 dist/qaf.tar.gz.sha256)"
for e in $EXTENSIONS; do
    echo "extension   : dist/qaf-$e.tar.gz (qaf-installer --extension=…)"
    echo "sha256-$e  : $(cut -d' ' -f1 "dist/qaf-$e.tar.gz.sha256")"
done
