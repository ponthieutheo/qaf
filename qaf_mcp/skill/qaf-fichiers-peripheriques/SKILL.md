---
name: qaf-fichiers-peripheriques
description: Copier ou déplacer un fichier d'un qube à un autre, ouvrir un fichier dans un jetable, et brancher ou débrancher une clé USB, un disque ou le micro sur un qube Qubes OS, avec les outils MCP « qaf ». Utilise-le quand l'utilisateur veut passer un fichier entre qubes, ouvrir un fichier douteux sans risque, ou utiliser un périphérique.
---

# Fichiers et périphériques

Lis d'abord le skill `qaf` (règles, confirmations, refus).

## Fichiers entre qubes

`file_copy` / `file_move` (`source`, `path`, `dest`) lancent l'outil de Qubes
(« Copier vers un autre qube ») dans le qube source :

- la source doit être **démarrée** (`qube_start` d'abord) ; la destination est
  démarrée par Qubes si besoin ;
- `path` est relatif au dossier personnel de la source (`Documents/devis.pdf`),
  fichier ou dossier : ni `..`, ni fichier caché (`.ssh`…), ni `-` en tête ;
- **Qubes affiche aussi sa propre fenêtre** : l'utilisateur confirme deux fois (la nôtre
  puis celle de Qubes) ;
- le fichier arrive dans `~/QubesIncoming/<source>/` de la destination ;
- `file_move` l'efface de la source une fois copié.

Ces outils ne te montrent pas le contenu. Pour connaître le nom exact d'un
fichier, il faut entrer dans la source (`qaf-dans-un-qube`, `ls`).
Automatique entre deux qubes à toi ; sinon confirmé.
**Refusé :** dom0, sys-agent, qubes protégés ; déplacer depuis un coffre
(copier depuis un coffre demande une confirmation renforcée).

Ton propre qube ne peut rien envoyer à un autre qube. Pour mettre un fichier
que tu as préparé dans un de tes espaces, écris-le directement là-bas
(`qube_exec`).

## Ouvrir un fichier dans un jetable

`file_open_disposable` (`name`, `path`) ouvre le fichier dans le jetable par
défaut du qube, en lecture seule (le jetable est détruit quand l'utilisateur ferme la
fenêtre). Bon réflexe pour une pièce jointe douteuse. Le qube doit être démarré
et avoir un jetable par défaut (`qube_settings`, `default_dispvm`), sinon
erreur. Confirmé sur un qube de l'utilisateur. **Refusé :** un fichier de coffre si ce
jetable a Internet.

## Périphériques

1. `devices_list` (`class` facultatif : `usb`, `block`, `mic`) : chaque
   périphérique avec `backend` (le qube qui le voit), `port`, `device_id` et
   `attached_to`. Seuls les qubes démarrés montrent leurs périphériques : rien
   n'est démarré pour les lister.
2. `device_attach` : `class`, `backend`, `port` et `device_id` **exactement**
   comme `devices_list` les a donnés (jamais `*` ni `_`), `target`, et
   `read_only` pour un disque (vrai par défaut : n'écrire que si l'utilisateur le veut).
   Toujours confirmé ; l'attache est temporaire (perdue au redémarrage du qube).
   `peripherique_change` : le périphérique n'est plus le même, relis la liste.
3. `device_detach` (`class`, `backend`, `port`, `target`) : débrancher. Fais-le
   avant que l'utilisateur retire une clé.

**Refusé :** brancher sur un coffre, un qube système ou un qube protégé, depuis
un coffre, et un périphérique dont le qube qui le voit est protégé (dom0 sauf
pour le micro). Brancher un disque de l'utilisateur sur un de tes qubes demande une
confirmation renforcée.

**Sans sys-usb, ou sys-usb arrêté :** les clés USB ne sont pas visibles par
ces outils (quand les ports USB sont dans dom0, le brancher est refusé à
l'agent). Ne démarre sys-usb que si l'utilisateur le demande (c'est confirmé, et refusé
s'il a interdit de le démarrer). Sinon, dis-lui de brancher le périphérique
lui-même avec l'icône des périphériques de la barre de Qubes.
