# Gel du bureau de dom0 (pointeur qui bouge, clics morts)

Enquête du 28/10 (gel de 16:59, Plasma X11, Xorg 21.1.23, kwin-x11 6.4.4).

## Symptôme

« Je peux bouger ma souris, mais je ne peux pas cliquer. Plus rien ne bouge. »
Le clavier marche encore. Seul un redémarrage (ou un aller-retour de VT) en sort.

## Cause prouvée : le pointeur maître est GELÉ par une saisie synchrone jamais libérée

Une saisie X en mode `GrabModeSync` gèle le périphérique : le serveur met ses
événements en file (`EnqueueEvent`) jusqu'à ce que le propriétaire de la saisie
envoie `AllowEvents`. S'il ne l'envoie jamais, tout reste en file : le curseur
bouge (il est déplacé par le pilote, avant la file), mais aucun clic ni
relâchement n'est livré.

Preuves, point par point :

1. **Relevé pendant le gel** : `query_pointer().mask` = 272 (bouton 1 tenu +
   NumLock) alors qu'aucun périphérique evdev n'avait de bouton enfoncé ;
   `grab_pointer` → AlreadyGrabbed, `grab_keyboard` → réussi ; un ButtonRelease
   XTest ne change rien. C'est exactement l'état d'un maître gelé : le
   relâchement est en file.
2. **Le message `[dix] EventToCore: Not implemented yet` est l'empreinte du gel.**
   Source de xserver 21.1.23 (`dix/eventconvert.c`) : les événements de geste
   (XI 2.4), tactiles et bruts rendent `BadMatch` *sans* message ; le message
   ne sort que pour les autres types, en pratique `ET_DeviceChanged` (changement
   d'esclave du maître : pavé tactile ↔ XTest ↔ souris). Or un DeviceChanged
   n'atteint `EventToCore` que par `EnqueueEvent` (`dix/events.c`), c'est-à-dire
   **quand le maître est gelé**, et seulement si un client RECORD écoute (KDE en
   a un ; l'extension RECORD est active). Ailleurs, `UpdateDeviceState` le
   consomme avant (`DONT_PROCESS`).
3. **Reproduit à l'identique sous Xvfb 21.1** (qube Debian) : un client pose une
   saisie passive SYNC sur le bouton 1 et ne fait jamais `AllowEvents` ; un clic
   XTest gèle le maître → masque 256, AlreadyGrabbed, clavier libre, relâchement
   XTest sans effet ; un client RECORD + un changement d'esclave pendant le gel
   écrivent **exactement** `[dix] EventToCore: Not implemented yet`.
4. **Corrélation dans le temps** (horodatages Xorg = secondes depuis le démarrage
   du noyau, sans veille sur ces sessions) :
   - session du 28/10 : `32972.754` = 16:59:14 → début du gel signalé ;
     `33132.152` = 17:01:54 → le ButtonRelease XTest de l'enquête (changement
     d'esclave pendant le gel) ; puis `chvt 2` à 33141 ;
   - session précédente (`Xorg.0.log.old`) : `29779.232` = 07:44 le 28/10, 35 s
     après le passage au thème Breeze sombre (xsettingsd rechargé à 07:43:28,
     KWin redécore toutes les fenêtres) ; l'utilisateur redémarre à 07:49 (6 min après).
   Une seule occurrence par session, chaque fois au moment d'un gel.
5. **Qui tient la saisie** : la fenêtre sous le pointeur au gel, `0x016000cb`, est
   le cadre KWin de la barre Plasma (client `0x1600000` = `kwin_x11`, X-Resource),
   avec dedans l'enveloppe `0x016000cc`, puis la fenêtre de plasmashell
   `0x180003c`. Sonde grab/ungrab : seule l'enveloppe porte des saisies de
   boutons (1 et 3, toutes combinaisons) ; ni la racine, ni le cadre, ni la
   fenêtre de plasmashell. Dans kwin-x11 6.4.4 (`src/events.cpp`,
   `updateMouseGrab`/`establishCommandWindowGrab`), ce sont des saisies
   `XCB_GRAB_MODE_SYNC` posées sur toute fenêtre inactive — un dock ne l'est
   jamais — et libérées par `xcb_allow_events(REPLAY_POINTER)` dans
   `buttonPressEvent`. Le menu Qubes avait le focus, la barre était inactive :
   chaque clic sur la barre passe par ce gel « normal » de quelques ms ; une
   fois, l'AllowEvents n'est pas venu.

Niveau de preuve : le **mécanisme** (maître gelé par une saisie synchrone non
libérée) est prouvé (source, reproduction exacte, relevé, corrélation). Le
**propriétaire** est KWin avec une très forte probabilité (seule saisie
synchrone possible sur ce chemin), sans trace directe du serveur pour le gel
du 28/10. Le **déclencheur** dans KWin (pourquoi l'AllowEvents manque, par
exemple pendant une redécoration) n'est pas identifié : aucun bogue public
correspondant trouvé (KDE 495169 ressemble, sans analyse).

Pistes écartées :
- **Gestes du pavé tactile (XI 2.4)** : ils ne produisent jamais ce message
  (`BadMatch` explicite) et ne peuvent pas activer une saisie de bouton ; ils
  sont seulement mis en file pendant le gel comme le reste.
- **qaf-zone** (`zone_x.py`, `cliquer`) : chaque ButtonPress XTest part avec son
  ButtonRelease, dans le même appel ; un clic rejoué pendant un gel est mis en
  file puis rejoué, il ne crée ni bouton bloqué ni saisie. Son conteneur est
  override-redirect, sans saisie. Au plus, un clic XTest pendant le gel est le
  changement d'esclave qui écrit le message (empreinte, pas cause).
- **Menu Qubes (GTK)** : ses saisies GDK sont asynchrones, elles ne gèlent rien ;
  son focus rend seulement la barre « inactive », donc couverte par la saisie
  SYNC de KWin.

## Pourquoi `chvt` débloquait

L'X de LightDM est sur le **VT1** (`loginctl` : session 2, VTNr=1). Quitter le
VT désactive tous les périphériques ; X défait alors leurs saisies et vide la
file : au retour sur le VT1, tout repart. **Ne jamais `chvt` vers un autre VT
sans revenir par `chvt 1`.**

## Correctif : le filet `qaf-degel`

KWin ne peut pas être modifié (paquet KDE). `qaf-degel` (`qaf_ui/degel.py`,
service `--user` `qaf-degel.service`, lancé par `/etc/xdg/autostart/qaf-degel.desktop`
dans Plasma seulement) détecte et défait le gel :

- **Détection** : il lit les événements bruts XI2 des périphériques *esclaves*,
  qui arrivent même quand le maître est gelé. Un bouton relâché sur tous les
  esclaves depuis 3 s, mais encore tenu par le maître (`query_pointer`) = gel
  (un relâchement normal met le maître à jour dans la même milliseconde). Aucun
  faux positif possible sur un bouton tenu longtemps : l'esclave le tient aussi.
- **Dégel** : action serveur XKB « Ungrab » (`XF86Ungrab`, option
  `grab:break_actions` de xkeyboard-config) : `setxkbmap` ajoute l'option, un
  keycode libre reçoit `XF86Ungrab`, une frappe XTest de ce keycode, puis keycode
  et options sont remis comme avant. Rien n'est tapé hors d'un gel détecté ; au
  plus un dégel toutes les 30 s.
- **Preuve automatique du coupable** : le serveur écrit alors dans
  `/var/log/Xorg.0.log` le bloc « Ungrabbing all devices; grabs listed below »
  avec le pid et la commande du propriétaire, « from passive grab », « device
  frozen », le type et le bouton de la saisie. `journalctl --user -u qaf-degel`
  note la fenêtre sous le pointeur et son client. Une notification « Bureau
  débloqué » s'affiche.

Vérifié sous Xvfb (qube Debian) : gel provoqué, dégel en 3,5 s, bloc « Ungrabbing »
avec le bon pid, options XKB remises à vide ; clics normaux et bouton tenu 5 s
sans aucune action.

## Si ça regèle

1. Attendre 3 à 5 secondes : `qaf-degel` débloque seul (notification « Bureau
   débloqué »).
2. Sinon : **Ctrl+Alt+F2**, puis **Ctrl+Alt+F1** (le bureau est sur le VT1).
   Vérifié dans cet X : F1/F2 portent `XF86Switch_VT_1/2` au niveau Ctrl+Alt,
   aucun `DontVTSwitch`. Si les touches F du Legion sont en mode multimédia,
   ajouter **Fn**.
3. Ensuite, pour la preuve : `grep -A8 Ungrabbing /var/log/Xorg.0.log` et
   `journalctl --user -u qaf-degel`.

## Retrait

`packaging/uninstall.sh` retire l'unité, l'autostart et le lanceur. Aucun fichier
de KDE, de Qubes ni de X n'est modifié ; l'option XKB n'est posée que le temps
d'un dégel.
