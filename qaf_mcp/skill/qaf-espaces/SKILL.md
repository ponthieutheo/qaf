---
name: qaf-espaces
description: Créer, démarrer, arrêter, redémarrer, régler (couleur, mémoire, processeurs, démarrage automatique, sauvegarde, disposition du clavier, jetable par défaut, services), agrandir le disque, étiqueter ou supprimer un qube Qubes OS, changer son modèle, changer les réglages globaux, et ouvrir ou fermer des qubes jetables, avec les outils MCP « qaf ». Utilise-le quand l'utilisateur veut un nouvel espace (par exemple « crée-moi un espace pour développer »), changer un réglage d'un qube, ou un jetable.
---

# Espaces, réglages et jetables

Lis d'abord le skill `qaf` (règles, confirmations, refus).

## Créer un espace

`qube_create` (`name`, `template`, `label` = `gray` par défaut) crée une AppVM
**sans réseau**, arrêtée. Automatique.

- Modèle : celui que l'utilisateur nomme, sinon `default_template` de `global_settings`.
  Pour un espace qui a besoin de logiciels en plus, crée d'abord un modèle à
  toi (skill `qaf-logiciels`), puis l'espace dessus.
- Couleur : demande-la seulement si l'utilisateur y tient ; elle dit à quel point il
  fait confiance à l'espace.
- Internet : action séparée, `qube_set_network` (skill `qaf-reseau`), toujours
  confirmée par l'utilisateur.
- Puis `qube_start`. Pour ouvrir une appli dedans, voir `qaf-dans-un-qube`.

Recette « espace pour développer » : `global_settings` → modèle à toi avec
les outils (`template_clone` puis `packages_install`, skill `qaf-logiciels`) →
`qube_create` sur ce modèle → `qube_set_network` vers le réseau par défaut si
le travail a besoin d'Internet → `qube_start`. Dis à l'utilisateur à chaque étape ce qui
lui sera demandé.

## Démarrer, arrêter, supprimer

| Outil | Effet |
|---|---|
| `qube_start` | démarre (et démarre son qube réseau avec lui). `deja_demarre` : il tourne déjà |
| `qube_stop` | demande l'arrêt, **sans attendre** : relis l'état avec `qube_info` |
| `qube_restart` | arrête, attend, redémarre (60 s au plus pour l'arrêt) ; un qube réseau avec des qubes branchés dessus refuse de s'arrêter (`en_usage`) |
| `qube_remove` | supprime un qube **arrêté** ; Qubes refuse s'il sert encore (modèle, réseau, jetable d'un autre) |

Automatique sur tes qubes ; confirmé sur ceux de l'utilisateur. Arrêter ou redémarrer un
qube système (`sys-net`, `sys-firewall`…) coupe Internet un moment : préviens.
`qube_remove` sur un qube de l'utilisateur demande une confirmation renforcée : ne le
fais que s'il l'a demandé en le nommant. Quand tu supprimes un espace à toi,
supprime aussi le modèle que tu avais créé pour lui (`template_remove`), s'il
ne sert plus à rien.

## Régler un qube

- `qube_settings` d'abord : l'utilisateur voit l'ancienne et la nouvelle valeur.
- `qube_set_property` : `label`, `memory` (Mio, 256 à 65536), `maxmem` (Mio,
  0 = pas de mémoire dynamique), `vcpus`, `autostart`, `include_in_backups`,
  `keyboard_layout` (`disposition+variante+options`, par exemple `fr++`),
  `shutdown_timeout`, `qrexec_timeout` (secondes), `default_dispvm` (nom ou
  `null`), `template_for_dispvms`, `provides_network`. `value` doit avoir le
  bon type (entier, booléen, nom de qube ou `null`). La plupart prennent effet
  au prochain démarrage du qube.
- `qube_reset_property` : retour à la valeur par défaut de Qubes.
- `qube_set_service` : `crond`, `cups`, `qubes-update-check`,
  `network-manager` ; `on`, `off` ou `default`.
- `qube_set_template` : change le modèle d'un qube **arrêté**.
- `qube_grow_disk` : taille totale du disque privé en Mio (1024 au moins), plus
  grande que l'actuelle ; ne se réduit jamais ; refusé par Qubes si la réserve
  de disque passerait sous 10 % de place libre (voir `system_status`).
- `qube_tag` : ajoute ou retire une étiquette (minuscules, chiffres, `_ -`).
  Toujours confirmé ; refusé pour les étiquettes que Qubes ou QAF posent
  (`created-by-…`, `guivm-…`, `audiovm-…`, `qaf-…`) et pour toute étiquette
  utilisée par une règle de sécurité.

**Confirmé :** sur tes qubes, presque rien (`default_dispvm`,
`template_for_dispvms`, `provides_network` le sont). Sur ceux de l'utilisateur : tout,
avec plus d'insistance pour la couleur, le modèle,
`include_in_backups=false`, `default_dispvm`, les rôles réseau et jetable, et
tout ce qui touche un qube système ou un coffre. Faire dépendre un qube de
l'utilisateur d'un qube que tu as créé (modèle, réseau ou jetable) demande une
**confirmation renforcée**.
**Refusé :** toute autre propriété (`netvm` et `template` ont leurs outils ;
`kernel`, `guivm`, `virt_mode`…), retirer un rôle réseau ou jetable dont
d'autres qubes dépendent, sortir un coffre des sauvegardes, `autostart` d'un
qube que l'utilisateur a interdit de démarrer. Dans ces cas, dis pourquoi en une phrase
et que l'utilisateur peut le changer lui-même dans les réglages du qube (Qube Manager)
s'il y tient, sans commande toute prête.

## Réglages globaux

`global_set_setting` : `default_template`, `default_dispvm` (nom ou `null`),
`check_updates_vm` (booléen). Toujours confirmé. Le réseau par défaut,
`updatevm`, `clockvm` et les autres sont réservés à l'utilisateur (réglages globaux de
Qubes). Changer le jetable par défaut est refusé si sys-agent, dom0 ou un
coffre en hériterait avec un jetable qui a Internet ou qui est à toi.

## Jetables

- `disposable_create` ouvre un jetable sans réseau, à toi (4 au plus en même
  temps), sur le modèle `qaf-dvm` (laisse `template` vide). Automatique. Tu y
  travailles avec les outils de `qaf-dans-un-qube`. Quand tu as fini, arrête-le
  avec `qube_stop` : Qubes le supprime tout seul.
- `disposable_task` confie une tâche texte (1000 caractères au plus) à un autre
  agent dans un jetable `qaf-dvm` sans réseau ; tu ne reçois qu'un court compte
  rendu. Toujours confirmé. Laisse `template` vide : un autre modèle de la
  liste de l'utilisateur est confirmé avec plus d'insistance, tout modèle hors liste est
  refusé.
- Les jetables de l'utilisateur (« Ouvrir dans un jetable », Firefox jetable…) se lancent
  depuis son menu ; tu n'en crées pas pour lui. Pour ouvrir un de ses fichiers
  dans un jetable, voir `qaf-fichiers-peripheriques`.
