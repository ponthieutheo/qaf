# Installer QAF sur un Qubes OS 4.3

Une seule commande transforme un Qubes OS 4.3 fraîchement installé en QAF :
`qaf-installer`. Elle ajoute le Doorman, les qubes de l'agent, les écrans QAF
et les réglages de sécurité, sans modifier le fonctionnement normal de Qubes.
Le bureau de QAF est **KDE Plasma (X11)**, le bureau KDE officiel de Qubes ; la
session Xfce reste exactement celle de Qubes. Tout se retire avec `qaf-installer --retour`.

## Avant de commencer

- Qubes OS **4.3**, installé normalement, avec le modèle **debian-13-xfce**
  (présent après une installation par défaut).
- Une session ouverte dans dom0, et un terminal de dom0
  (menu : Terminal du Cœur du système, ou `xfce4-terminal`).
- Le réseau qui marche (sys-net, sys-firewall) : l'installation télécharge des
  paquets dans les modèles.
- Au moins 20 Gio libres pour les qubes et 2 Gio dans dom0.
- Pour le bureau : KDE Plasma, paquet officiel de Qubes, à installer toi-même
  dans dom0 (QAF ne l'installe pas) :

  ```bash
  sudo qubes-dom0-update kde-settings-qubes
  ```

  Il ajoute la session « Plasma (X11) » à l'écran de connexion. LightDM reste
  l'écran de connexion : si le paquet a activé SDDM, garde LightDM
  (`sudo systemctl disable sddm` puis `sudo systemctl enable lightdm`). Sans Plasma, QAF s'installe quand même et ses
  écrans marchent sous Xfce.
- Compter 20 à 40 minutes.

## 1. Récupérer l'archive dans un qube

Dans un qube qui a Internet (par exemple `personal`, ou un jetable),
télécharge `qaf.tar.gz` (page de la version dans les *Releases* du dépôt, <https://github.com/ponthieutheo/qaf/releases>). Note où il est
rangé, par exemple `~/Downloads/qaf.tar.gz`.

## 2. Noter l'empreinte de référence, ailleurs

L'empreinte (sha256, 64 caractères) se lit sur une source **indépendante** du
qube qui a téléchargé l'archive : la page de la version ouverte sur
un autre appareil, ou la sortie de `tools/make-dist.sh` sur une autre machine. Jamais
dans le qube qui a fourni l'archive : s'il était piégé, il mentirait aussi sur
l'empreinte.

## 3. Amener l'archive dans dom0 et la vérifier

Dans le terminal de dom0 (remplace `personal` et le chemin si besoin) :

```bash
qvm-run --pass-io personal 'cat ~/Downloads/qaf.tar.gz' > ~/qaf.tar.gz
echo "EMPREINTE  qaf.tar.gz" | sha256sum -c
```

Remplace `EMPREINTE` par l'empreinte de référence (deux espaces avant
`qaf.tar.gz`). La réponse doit être `qaf.tar.gz: OK` (ou `Réussi`).
**Si elle dit `FAILED` ou `ÉCHEC` : n'ouvre pas l'archive.** Supprime-la
(`rm ~/qaf.tar.gz`) et recommence l'étape 1.

## 4. La commande unique

```bash
cd ~ && tar -xzf qaf.tar.gz
sudo bash qaf/tools/qaf-installer ~/qaf.tar.gz EMPREINTE
```

Si tu ne mets pas l'empreinte sur la ligne, elle t'est demandée. L'installateur
la revérifie, puis :

1. contrôle les prérequis (vrai dom0, Qubes 4.3, place, modèle debian-13-xfce,
   session ouverte) : s'il manque quelque chose, il s'arrête **avant** de rien
   changer ;
2. affiche son plan (aucun paquet n'est ajouté dans dom0) ;
3. enchaîne les étapes : configuration du Doorman (`/etc/qaf/portier.json`,
   agent permanent par défaut) ; portier ; tpl-agent (Node 22 de NodeSource,
   Claude Code épinglé) et sys-agent, l'agent ; qaf-agent-dvm, le modèle des
   agents jetables, gardé mais inactif avec l'agent permanent
   (`tools/setup-agent-dvm.sh`, pare-feu fermé : DNS et API seulement) ;
   tpl-qaf-hand et qaf-dvm ; qaf_hand dans les modèles Debian et Fedora ;
   réglages de sécurité (jetable par défaut, périphériques refusés à sys-agent,
   sys-net et sys-firewall, copie de fichiers depuis untrusted). Aucune session
   n'est posée par QAF : le bureau est le KDE Plasma classique de Qubes ; la
   session Xfce n'est pas touchée. QAF n'y
   ajoute que la brique agent : l'icône de l'agent et celle de la suspension dans
   la barre de Plasma, les icônes des qubes sans carré blanc (`qaf-zone`), le
   filet contre le gel du pointeur (`qaf-degel`), le panneau de l'agent
   (`qaf-agent`, menu de Plasma › Système) et la palette « Demander à l'agent »
   (`qaf-palette`, ouverte depuis ce panneau). Aucun raccourci clavier n'est
   posé : la touche Meta et Meta+Espace gardent leur rôle de Plasma (menu de
   Qubes) ;
4. termine par un contrôle de non-régression du Qubes d'origine.

Sur un modèle de machine connu (par exemple le Lenovo Legion 82JU), un profil
matériel est appliqué en plus ; sur le Legion, il garantit que sys-usb n'est
jamais démarré et retire les disques internes (dont le SSD Windows) de la liste
des périphériques proposés aux qubes. Une machine inconnue n'a aucun profil
matériel.

À la fin :

- déconnecte-toi, puis connecte-toi : avec Plasma installé, l'écran de connexion
  ne propose plus que la session **Plasma (X11)** (Xfce reste installée, inchangée,
  et `--retour` la propose de nouveau) ;
- connecte le compte de l'agent (voir « Compte de l'agent » ci-dessous).

## Compte de l'agent (une seule étape)

L'agent est **permanent** :
c'est le qube `sys-agent`. Ses fichiers (`~/.claude` : mémoire, historique,
identifiants) survivent d'une demande à l'autre. Chaque demande tapée dans la
palette (`qaf-palette`, bouton « Demander à l'agent… » du panneau `qaf-agent`)
lui est transmise : dom0 démarre sys-agent
s'il est arrêté, sa fenêtre « Agent » s'ouvre et la conversation reprend avec ta
nouvelle demande.
Il compte comme influencé en permanence : toute action de gravité moyenne ou
plus t'est demandée (touche A au clavier interne), les lectures restent
automatiques.

Une seule chose à faire, une fois : **te connecter à Claude dans sys-agent.**

1. clic sur l'icône de l'agent dans la barre de Plasma, puis **Claude** (ou
   « + » puis Claude si un abonnement est déjà connecté) : un terminal s'ouvre
   dans sys-agent sur `claude auth login` ;
2. suis la connexion habituelle de Claude Code (lien à ouvrir, code à
   recopier), puis ferme le terminal.

Les identifiants sont dans `~/.claude/.credentials.json` de sys-agent ; claude
les rafraîchit tout seul, et un seul claude tourne à la fois dans sys-agent
(chaque demande remplace la fenêtre « Agent » précédente). Sans la barre :
`qvm-run sys-agent xfce4-terminal` dans un terminal de dom0.

**Autre possibilité : le jeton longue durée.** Dans le même terminal :
`claude setup-token`, colle le jeton dans `~/.config/qaf/jeton-claude`, puis
`chmod 600 ~/.config/qaf/jeton-claude`. Il est passé à chaque claude lancé par
une demande et passe avant la connexion habituelle.

**Si les identifiants ont pu fuir** (procédure d'incident) : révoque la
session ou le jeton depuis ton compte (claude.ai, réglages), mets l'agent en
pause (`sudo qaf-pause`), puis reconnecte-toi comme ci-dessus et lève la pause
(`sudo qaf-pause off`). Garde un plafond de dépense sur le compte.

**Réseau de sys-agent** : il passe par sys-firewall, avec le pare-feu de Qubes
ouvert (il peut lire le Web et la documentation). Comme il est déjà influencé
en permanence, le Web ne change rien à ce qui t'est demandé.

**Revenir aux agents jetables** (un agent neuf par demande, sans mémoire) :
`"agent_long": false` dans `/etc/qaf/portier.json`, puis relancer l'installation.
Leur compte se connecte alors avec un jeton longue durée rangé dans le modèle
`qaf-agent-dvm` : `qvm-run qaf-agent-dvm xfce4-terminal`, `claude setup-token`,
jeton dans `~/.config/qaf/jeton-claude` (0600), puis `qvm-shutdown qaf-agent-dvm`.
Si tu changes `agent_hotes`, relance
`sudo bash /usr/lib/qaf-installer/qaf/tools/setup-agent-dvm.sh` : tant que le
pare-feu du modèle diffère de la liste, le Doorman compte chaque agent jetable
comme influencé.

Tout est noté dans `/var/log/qaf-installer.log`.

## Si ça échoue

L'installateur s'arrête à la première étape qui échoue, avec un message
`ÉCHEC :` qui dit laquelle et une piste. Les étapes suivantes ne sont pas
faites ; celles d'avant restent en place.

1. Lis le message et les lignes juste au-dessus. Le détail complet est dans
   `/var/log/qaf-installer.log` (`less /var/log/qaf-installer.log`).
2. Corrige la cause. Les plus courantes :

   | Message | Que faire |
   |---|---|
   | `EMPREINTES DIFFÉRENTES` | L'archive n'est pas la bonne, ou l'empreinte est mal recopiée. Rien n'a été installé. Recommence à l'étape 1. |
   | `Le modèle debian-13-xfce est absent` | Installe-le (Qubes Template Manager), puis relance. |
   | `Place insuffisante` | Supprime des qubes ou des sauvegardes inutiles, puis relance. |
   | `La session graphique … n'est pas ouverte` | Lance la commande depuis un terminal de ta session. |
   | Échec à « tpl-agent », « qaf-dvm » ou « qaf_hand » | Souvent le réseau : vérifie que sys-net et sys-firewall marchent, puis relance. |
   | `« sys-usb » a démarré` (Legion) | Arrête-toi là et préviens : sur ce laptop sys-usb ne doit jamais démarrer. |

3. **Relance la même commande.** Chaque étape se rejoue sans dommage : les
   réglages déjà posés gardent leur sauvegarde d'origine.
4. Ou retire tout : `sudo qaf-installer --retour`.

## Vérifier que Qubes marche toujours

```bash
qaf-installer --verifier
```

Lecture seule : qvm-ls, qubesd actif et son journal, politiques QAF valides,
fichiers de politique de Qubes intacts, état du Doorman, qubes qui ne doivent
jamais démarrer. Essaie aussi à la main : ouvrir un jetable, copier un fichier
entre deux qubes (la confirmation native de Qubes doit s'afficher), ouvrir le
Gestionnaire des espaces.

## Tout retirer

```bash
sudo qaf-installer --retour
```

Retire tout dans l'ordre inverse et remet les valeurs d'origine sauvegardées.
Les qubes créés (tpl-agent, sys-agent, qaf-agent-dvm, tpl-qaf-hand, qaf-dvm) sont
**gardés**. KDE Plasma (`kde-settings-qubes`), installé à part, reste : c'est un
paquet de Qubes, QAF ne le retire pas.

**La couche matériel est gardée** : sur le Legion, les disques internes (dont
le SSD Windows) restent retirés de la liste des périphériques proposés aux
qubes. `--retour` seul ne rend donc pas la machine strictement identique,
volontairement : un retrait ne doit jamais réexposer le disque Windows par
surprise. Pour la retirer aussi, explicitement :

```bash
sudo qaf-installer --retour --materiel
```

Après un `--retour` seul, la commande `qaf-installer` n'existe plus : lance-la
depuis l'archive extraite, `sudo bash qaf/tools/qaf-installer --retour --materiel`.

Les disques internes redeviennent alors attachables à un qube (au prochain
démarrage). La valeur d'origine (aucune règle) est notée dans
`/var/lib/qaf-installer/materiel`.

```bash
sudo qaf-installer --retour --purge
```

Supprime en plus, définitivement, les qubes créés par l'installateur (jamais
ceux qui existaient avant), le journal et le registre du Doorman. Il faut
taper `PURGER` pour confirmer. `--purge` n'implique pas `--materiel` et le dit
dans son récapitulatif.
