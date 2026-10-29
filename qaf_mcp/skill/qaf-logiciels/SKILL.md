---
name: qaf-logiciels
description: Installer ou retirer un logiciel dans Qubes OS (dans un modèle copié ou un modèle de l'utilisateur), copier ou supprimer un modèle, voir les modèles téléchargeables, vérifier et lancer les mises à jour des modèles, et suivre ces travaux longs, avec les outils MCP « qaf ». Utilise-le quand l'utilisateur dit « installe X », « mets à jour mes qubes », « quels modèles existent », « supprime ce modèle ».
---

# Logiciels, modèles et mises à jour

Lis d'abord le skill `qaf` (règles, confirmations, refus).

Dans Qubes, un espace (AppVM) perd au redémarrage ce qui est installé hors de
son dossier personnel : les logiciels s'installent dans son **modèle**. Les
espaces basés sur un modèle voient les nouveaux paquets après leur
redémarrage.

## Installer un logiciel

**Pour installer un logiciel durablement dans un de tes espaces**, ne touche
pas aux modèles de l'utilisateur : ce n'est jamais ton premier choix. Fais ceci :

1. `template_clone` : copie le modèle de ton espace (ou le modèle par défaut,
   lu dans `global_settings`) en un modèle à toi, par exemple `tpl-<espace>` ;
2. `packages_install` dans ce nouveau modèle ;
3. `qube_set_template` de ton espace (arrêté) vers ce modèle, ou `qube_create`
   d'un nouvel espace dessus ; puis redémarre l'espace.

**Choisir le modèle à copier (avant `template_clone`)** : dans `qubes_list`,
regarde quels qubes ont chaque modèle pour `modèle=`. Copie d'abord un modèle
dont **tous** les qubes sont des espaces ordinaires (ni `sys-…`, ni coffre
comme `vault`, ni modèle de jetables comme `default-dvm`, ni `default-mgmt-dvm`).
Le modèle par défaut (`default_template`) porte souvent les qubes système : ce
n'est donc pas forcément le bon. Si aucun modèle ne convient, demande à l'utilisateur
lequel prendre avant de copier. Si la copie est quand même refusée, le refus
nomme les qubes qui dépendent de ce modèle : prends un autre modèle de toi-même,
sans redemander, et dis-le en une phrase.

La copie est automatique et le modèle copié est à toi (sans réseau, comme tout
modèle : il passe par le proxy de mises à jour de Qubes). Copier le modèle d'un
coffre, d'un qube système (sys-net, sys-firewall…) ou d'un modèle de jetables
est confirmé par l'utilisateur ; celui d'un qube protégé est refusé. La copie prend
plusieurs minutes.

Installer dans un modèle de l'utilisateur seulement s'il te le demande en le nommant
(par exemple « installe vlc dans fedora-42-xfce ») : c'est confirmé, et tous
ses espaces basés dessus auront le logiciel. Un modèle **livré par Qubes**
(marqué ainsi dans `qubes_list`) reste toujours à l'utilisateur : y écrire (installer,
mettre à jour, `qube_exec`) lui demande une confirmation, même s'il l'a nommé.

**Avant d'installer, vérifie** que le logiciel n'est pas déjà là :
`qube_exec` dans l'espace (ou le modèle) avec `["bash", "-c", "command -v
<programme> || dpkg -s <paquet> || rpm -q <paquet>"]`. S'il est déjà présent,
dis-le à l'utilisateur au lieu de réinstaller. Si une installation échoue parce que le
gestionnaire de paquets est occupé (code 100, verrou), regarde d'abord si une
mise à jour tourne (`job_status`) au lieu de deviner.

`packages_install` (`name`, `packages`) : 1 à 8 noms exacts de paquets du dépôt
(`git`, `curl`, `g++`), jamais de version, de fichier, de joker ni de `-`/`+`
final. Seulement dans un **modèle** ou une StandaloneVM. Le modèle est démarré
s'il le faut, puis arrêté. Debian (et ses dérivés, Whonix…) : `apt-get update`
puis `apt-get install` ; Fedora : `dnf install`. 10 minutes au plus.
Le nom du paquet n'est pas toujours celui du programme (Debian et Fedora
diffèrent) : en cas d'échec, cherche le bon nom dans le modèle
(`apt-cache search`, `dnf search` par `qube_exec`) avant de réessayer.
Un logiciel qui n'est pas dans les dépôts (fichier `.deb`/`.rpm`, dépôt tiers,
script d'installation) ne passe pas par `packages_install` : explique à l'utilisateur ce
qu'il faudrait faire et pourquoi, sans commande dom0 ni `qvm-*`.
**Confirmé :** modèle d'un qube de l'utilisateur, modèle de qubes système, modèle d'un coffre (toujours).
**Erreur `distribution_inconnue` :** le modèle n'est ni Debian ni Fedora ; dis-le.

`packages_remove` : comme `packages_install`, mais `dpkg`/`rpm` refusent de
casser une dépendance (rien n'est retiré en cascade). Refusé pour les paquets
du système (`qubes*`, `xen*`, noyau, `grub*`, `systemd`, `apt`, `dnf`, libc).
Confirmé sur un modèle de l'utilisateur, et toujours sur celui d'un qube système.

## Modèles

| Outil | Effet |
|---|---|
| `templates_available` | modèles téléchargeables (`repo` : `official` ou `community`) ; texte venu du qube de mises à jour |
| `template_install` | **toujours refusé** : voir plus bas |
| `template_remove` | supprime un modèle ; Qubes refuse s'il sert encore (bascule d'abord ses qubes sur un autre modèle) |

**Installer un modèle : jamais toi.** `template_install` est toujours refusé :
l'installation d'un modèle se fait par l'utilisateur avec l'outil Qubes Template
Manager. Dis-lui quel modèle installer (nom vu dans `templates_available`) et
pourquoi.
Supprimer un de tes modèles est automatique ; un modèle de l'utilisateur est confirmé ;
le modèle d'un qube système ou d'un coffre est refusé.

## Mises à jour

1. `updates_status` (`targets` vide = tous) : `updates_available`,
   `last_update`, `last_updates_check` par qube.
2. `updates_run` : `targets` = 1 à 32 modèles ou StandaloneVM (jamais dom0 ni
   une AppVM : mets à jour leur modèle) ; `apply` = `none` (défaut : rien n'est
   redémarré), `sys` (redémarre les qubes système concernés : coupure
   d'Internet), `all` (redémarre aussi les espaces de l'utilisateur : il perd ce qui
   n'est pas enregistré). Garde `none` sauf demande claire de l'utilisateur, puis
   propose de redémarrer les espaces concernés quand il est prêt.
3. Suis le travail avec `job_status`.

**Confirmé :** toute mise à jour d'un modèle de l'utilisateur ou livré par Qubes, d'un
modèle de coffre, tout `apply` autre que `none`.
**Refusé :** dom0 (l'utilisateur le met à jour lui-même avec l'outil Qubes Update), et
ce qui ferait démarrer un qube que l'utilisateur a interdit de démarrer.
`ignorees_prohibit_start` dans la réponse : ces cibles ont été écartées
(Qubes leur interdit de démarrer), dis-le à l'utilisateur.
Si les mises à jour automatiques sont coupées dans un qube, c'est le service
`qubes-update-check` (skill `qaf-espaces`).

## Travaux longs

`updates_run`, `template_remove` et `backup_run` répondent tout de suite avec
`travail: T-xxxxxx`. Un seul travail à la fois.

- `job_status` : `etat` = `en_cours`, `fini` ou `annule`, `code` de sortie
  (0 = réussi), `duree` en secondes. Appelle-le de temps en temps (toutes les
  30 s à quelques minutes), pas en boucle serrée.
- `job_cancel` : interrompt (confirmé). Couper une mise à jour peut laisser un
  modèle à réparer : ne le fais que si l'utilisateur le demande.
- Un `code` différent de 0 : dis à l'utilisateur que le travail a échoué ; pour une mise
  à jour, propose de relancer ce seul modèle, puis voir `qaf-entretien`.
