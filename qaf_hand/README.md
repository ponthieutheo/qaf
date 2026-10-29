# qaf_hand — la main sans LLM du qube cible

Quatre services qrexec. Chacun lit **une** requête JSON (16 Kio max, schéma fermé)
sur stdin et répond **une** ligne JSON (`{"ok": true, …}` ou `{"ok": false, "erreur": …}`).
Jamais de shell : uniquement des listes d'arguments. Bibliothèque standard + `xdotool`,
`xwd` (Debian : `x11-apps`), `xwininfo` (`x11-utils`). ImageMagick n'est plus nécessaire.

| Service | Requêtes |
|---|---|
| `qaf.Screen` | `{"action":"lister","masquees":false}` · `{"action":"capturer","fenetre":"0x1a00003"\|"racine"}` → PNG en base64 + `x`, `y`, `largeur`, `hauteur` du cadre |
| `qaf.Input` | `taper` (texte ≤ 2000 car., sans contrôle) · `touche` (`ctrl+l`) · `cliquer` / `deplacer` (x, y, fenêtre facultative) · `defiler` (haut/bas, 1-20 crans) |
| `qaf.Exec` | `{"argv":["cmd","arg"],"delai":30}` — 64 arguments, 8 Kio, 120 s, sortie tronquée à 64 Kio |
| `qaf.Task` | **jetable seulement** : `{"tache":"…"}` (1 000 car.) → confie la tâche au coureur, répond aussitôt |

## Capture : la racine, recadrée

Dans un qube, `import -window ID` / `xwd -id ID` donnent une image **noire**
(mesuré sur Qubes 4.3). `qaf.Screen` capture donc la racine (`xwd -root`, ~60 ms),
lit la géométrie de la fenêtre (`xwininfo -id` : position absolue, taille,
`Map State`) et découpe le cadre en Python (`xwd.py`, XWD → PNG, stdlib seule).
Le X de Qubes sert du 24 bits par pixel **packé** avec un pas de ligne de 4 octets
par pixel (complété de zéros) : `xwd.py` lit les pixels selon `bits_per_pixel` et
les lignes selon `bytes_per_line`. Vérifié dans un qube Debian le 25/10 : identique au
pixel près à `import -window root -crop`.
Limites : une fenêtre non affichée est refusée ; une fenêtre recouverte par une
autre fenêtre du même qube montre ce qui la recouvre ; le cadre est borné à l'écran.

## Installation (template du qube cible, ou du modèle de jetable QAF)

`sudo sh tools/install-qaf-hand.sh [DÉPÔT]` (retrait : `--desinstaller`) :

- `qaf_hand/*.py` dans le site-packages **système** (`/usr/lib/python3/dist-packages/`
  sur Debian) : les services lancent `python3 -I -m qaf_hand …`, et `-I` ignore
  PYTHONPATH et le dossier courant ;
- `etc/qubes-rpc/qaf.*` → `/etc/qubes-rpc/` (0755), `etc/qubes/rpc-config/qaf.*` →
  `/etc/qubes/rpc-config/` (`wait-for-session=true` : le service attend la session
  graphique et hérite de `DISPLAY`, cf. `qubes-run-xorg`).

Dans une AppVM, `/usr` et `/etc` reviennent au template à chaque démarrage :
installer dans le **template**. Le modèle de jetable dédié du Doorman
(`qaf-dvm` sur `tpl-qaf-hand`) se crée avec `tools/setup-qaf-dvm.sh` (dom0, l'utilisateur).
Qui peut appeler : depuis un qube, personne pour l'instant (`policy/00-qaf.policy`
refuse explicitement `qaf.Task`, `qaf.Exec`, `qaf.Screen`, `qaf.Input` à `@anyvm`).
`qaf.Task` n'est appelé que depuis dom0, par `qaf-gate-taches` (sans politique).

## dispvm.run_task de bout en bout

```text
sys-agent ─qaf.Plan─▶ portier (dom0) ── classement N2/N3, confirmation si besoin
  exécuteur : modèle dans la liste blanche (qaf-dvm, sans réseau) ; échéance unique de 110 s
              qubesd_call(qaf-dvm, admin.vm.CreateDisposable) → dispNNNN (auto_cleanup)
              vm.start() par qubesd, puis socket /run/qaf-taches/taches.sock :
  qaf-gate-taches : qrexec-client -d dispNNNN -w <reste> qaf.Task, envoie {"tache": …},
              lit au plus 4 097 octets d'accusé avant l'échéance, sinon tue qrexec-client
  dispNNNN  : qaf.Task vérifie la tâche, l'écrit dans ~/qaf-taches/<id>/, lance le coureur
              détaché, répond {"ok": true, "accepte": true}
              coureur → qaf_hand (capture, clavier, exec) → journal.jsonl, rapport.json
              fin : sudo -n systemctl poweroff (seulement si qubesdb /type = DispVM)
  qubesd    : jetable arrêté → supprimé avec son disque (auto_cleanup)
```

Le modèle est **toujours nommé** : `template` du plan, sinon `qaf-dvm`
(`schema.MODELE_JETABLE`), et toujours dans la liste blanche de dom0
(`classify.JETABLES_DEFAUT` ; tout autre modèle = N4, H1). Jamais
`CreateDisposable` sur dom0, qui prendrait `default_dispvm` (vide sur
une installation mesurée). Si l'accusé manque, est négatif ou arrive trop tard : `kill` du
jetable (ou suppression s'il n'a jamais démarré).

Pourquoi `qaf-gate-taches` (H3) : `qrexec-client` ouvre un vchan (périphériques
`/dev/xen/*`, xenstore). Plutôt que d'ouvrir ces périphériques à l'exécuteur,
qui tient déjà qubesd, un petit service séparé (compte `qaf-taches`, groupe
`qubes`, pas d'accès à qubesd, `AF_UNIX` seulement) fait cet appel et rien
d'autre. Seul l'exécuteur peut lui parler (socket en groupe `qaf-exec`, uid
revérifié par `SO_PEERCRED`). Voir `qaf_gate/taches.py` et
`packaging/qaf-gate-taches.service`.

### Coureur factice (`coureur.py`)

Pour la démonstration technique, sans LLM : la tâche est une suite de pas, un par
ligne (`lancer`, `attendre <titre>`, `fenetre`, `taper`, `touche`, `cliquer`,
`capturer`, `lister`, `exec`, `pause`). Une tâche en langage naturel est notée
« non interprétable » et le jetable s'éteint. 50 pas, 300 s au plus.

### Ce qui manque pour le vrai coureur

1. **Le coureur LLM** lui-même, à la place de `qaf_hand.coureur` (même contrat :
   lit `tache.json`, pilote qaf_hand, écrit `rapport.json`, éteint le jetable).
   Boucle « capture → décision → action » avec l'outil computer use du fournisseur.
2. **Une clé API plafonnée, jamais d'OAuth** (C9) : le jetable ne contient aucun
   secret. Le coureur parle à un **relais LLM de son niveau de confiance** (C8),
   qui ajoute la clé de l'utilisateur, plafonne la dépense en euros (par tâche et par jour)
   et journalise. Le relais vit dans un qube à part (`sys-llm-<niveau>`), joint
   par `qubes.ConnectTCP` (politique : `qaf-dvm` → relais seulement), pas par une
   netvm : `qaf-dvm` reste sans réseau.
3. **Une tâche web** (« ouvre ce site ») demande un jetable avec réseau : second
   modèle nommé (ex. `qaf-dvm-web`), à décider par l'utilisateur ; le plan devra le nommer
   (classé N3, donc confirmé).
4. **Le retour du résultat** : aujourd'hui `rapport.json` meurt avec le jetable.
   Il faudra un canal fermé (schéma court, texte borné, jamais de pixels) vers
   sys-agent, via le Doorman, et la provenance « influencée » posée sur la session
   dès qu'un coureur a lu du contenu web (C6, C13).
5. **Arrêt d'urgence et durée** : le Doorman doit pouvoir tuer tous les jetables
   QAF (tag `created-by-dom0` + registre) et imposer une durée maximale même si le
   coureur ne s'éteint pas (minuterie côté dom0).
6. **Indicateur « agent dans X »** dans dom0 (S4) et quotas de jetables simultanés.

## Choix et points vérifiés sur la vraie machine

- Pas de gestionnaire de fenêtres dans un qube : les fenêtres sont listées comme
  enfants directs de la racine (`xdotool search --maxdepth 1`), le focus est donné
  par `windowfocus` (XSetInputFocus). Vérifié le 25/10.
- La saisie passe par XTEST (`xdotool type` sans `--window`), accents compris :
  xterm et beaucoup d'applis ignorent les événements XSendEvent. Vérifié le 25/10.
