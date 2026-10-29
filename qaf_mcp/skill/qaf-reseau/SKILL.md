---
name: qaf-reseau
description: Internet et réseau dans Qubes OS avec les outils MCP « qaf » - donner ou couper Internet à un qube, choisir par quel qube réseau il sort, lire et changer ses règles de pare-feu (n'autoriser que certains sites), et monter un qube réseau intermédiaire comme un VPN. Utilise-le quand l'utilisateur parle d'Internet, de réseau, de pare-feu, de VPN ou d'un qube qui n'a pas de connexion.
---

# Réseau et pare-feu

Lis d'abord le skill `qaf` (règles, confirmations, refus).

Dans Qubes, un qube sort sur Internet par son **qube réseau** (`netvm`) :
d'habitude `sys-firewall`, qui sort par `sys-net` (la carte réseau). Le réseau
par défaut est `default_netvm` dans `global_settings`. Les qubes qui donnent le
réseau sont marqués « donne le réseau » dans `qubes_list`.

## Donner ou couper Internet

`qube_set_network` (`name`, `netvm`) : `netvm` = un qube qui donne le réseau
(en général le réseau par défaut), ou `null` pour couper.

- Donner Internet : toujours confirmé (plus fortement sur un qube de l'utilisateur).
  Un qube que tu crées n'en a jamais : demande-le seulement si le travail en a
  besoin, et dis pourquoi.
- Couper Internet : automatique sur tes qubes, confirmé sur ceux de l'utilisateur.
- **Refusé :** donner Internet à un coffre (`vault`…), à dom0, à sys-agent.
- Le changement vaut tout de suite, même qube démarré. `netvm_arretee` : le
  qube réseau visé est arrêté alors que le qube tourne ; démarre-le d'abord
  (`qube_start`).

## Pare-feu

Les règles d'un qube sont lues dans l'ordre ; la première qui correspond
décide. Elles ne s'appliquent que s'il a un qube réseau.

- `firewall_list` : les règles, numérotées à partir de 0 (`autres_champs` :
  une règle a des réglages que tu ne vois pas ; ne la retire pas).
- `firewall_add` (`name`, `position`, `rule`) : insère la règle à cette
  position (0 = avant toutes).
- `firewall_remove` (`name`, `position`, `rule`) : retire la règle à cette
  position **seulement** si c'est exactement celle donnée, sinon
  `regle_changee` : relis et recommence. Lis `firewall_list` juste avant.

`rule` est fermée, clé inconnue = refus :
`{"action": "accept"|"drop", "host": "exemple.org" ou "10.0.0.0/8",
"proto": "tcp"|"udp"|"icmp", "ports": "443" ou "8000-8100"}` (chaque clé sauf
`action` facultative, `ports` seulement avec tcp ou udp), ou
`{"action": "accept", "dns": true}` pour le DNS.

**N'autoriser que certains sites** (par exemple « work ne doit joindre que
exemple.org ») : `firewall_list`, puis, à partir de la position 0 :
`{"action": "accept", "dns": true}` (sans DNS, aucun nom ne se résout), une
règle `accept` par site (`host`, `proto` tcp, `ports` 443), puis
`{"action": "drop"}` juste après. Relis `firewall_list` et montre le résultat à
l'utilisateur. Pour tout rouvrir : retire ces règles.

**Confirmé :** ce qui ouvre (ajouter un `accept`, retirer un `drop`) sur un
qube de l'utilisateur ou sur un de tes qubes ; tout sur un qube système ou un coffre.
Ce qui ferme est automatique sur tes qubes.

## Qube réseau intermédiaire (VPN, proxy)

Si l'utilisateur veut que des qubes sortent par un VPN, et qu'aucun qube ne le fait
déjà (`qubes_list`, « donne le réseau ») :

1. Modèle à toi avec le logiciel du VPN (skill `qaf-logiciels`).
2. `qube_create` du qube VPN sur ce modèle, `qube_set_network` vers le réseau
   par défaut (confirmé), `qube_start`.
3. Configure et teste le VPN dedans **tant qu'il est encore un simple espace à
   toi** (skill `qaf-dans-un-qube`, automatique). Les identifiants et fichiers
   de configuration viennent de l'utilisateur : laisse-le les taper ou les copier
   lui-même, ne les écris jamais dans ta mémoire.
4. `qube_set_property` `provides_network` = `true` (confirmé ; si Qubes refuse
   pendant qu'il tourne, arrête-le d'abord). Dès ce moment, c'est un qube
   système : toute action sur lui est confirmée et le bail y est refusé.
5. Quand l'utilisateur le demande en les nommant, `qube_set_network` de ses qubes vers
   le qube VPN (confirmation renforcée : ses qubes dépendraient d'un qube à
   toi). Vérifie la sortie depuis un de tes qubes branché dessus.

Ne change jamais `sys-net` ni `sys-firewall` pour ça. Si ce n'est pas possible
avec ces outils (réglage du réseau par défaut, qube réseau de démarrage),
explique à l'utilisateur ce qu'il peut faire lui-même dans les réglages de Qubes.

## Un qube n'a pas Internet

1. `qube_info` : a-t-il un qube réseau ? Sinon, `qube_set_network`.
2. Ce qube réseau et sa chaîne (`sys-firewall`, `sys-net`) tournent-ils
   (`qubes_list`) ?
3. Règles de pare-feu (`firewall_list`) : un `drop` avant les `accept` ?
   Le DNS est-il autorisé ?
4. Dans le qube (`qaf-dans-un-qube`) :
   `["bash", "-c", "getent hosts exemple.org; curl -sSI https://exemple.org | head -1"]`.
5. Si `sys-net` n'a pas de connexion (Wi-Fi non connecté…), c'est à l'utilisateur de
   choisir son réseau dans l'icône réseau de la barre ; tu ne peux pas entrer
   dans un qube système.
