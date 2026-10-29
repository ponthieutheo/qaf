# qaf_mcp — serveur MCP de sys-agent

Serveur MCP en stdio (bibliothèque standard Python ≥ 3.11). Il donne à l'agent
8 outils et traduit chacun en **un plan** au format fermé de `qaf_gate/schema.py`,
validé localement puis envoyé au Doorman :

```
agent (Claude Code) ──stdio/JSON-RPC──▶ qaf_mcp ──qrexec-client-vm dom0 qaf.Plan──▶ qaf-gate (dom0)
```

| Outil | Opération du plan |
|---|---|
| `qubes_list` | `vm.list` |
| `qube_info` | `vm.info` |
| `qube_create` | `vm.create` (label `gray` par défaut, jamais de réseau) |
| `qube_start` / `qube_stop` | `vm.start` / `vm.shutdown` |
| `qube_remove` | `vm.remove` |
| `qube_set_network` | `vm.set_netvm` (`null` = aucun réseau) |
| `disposable_task` | `dispvm.run_task` |

## Installation dans tpl-agent (à faire, non testé sur Qubes)

1. Copier `qaf_mcp/` et `qaf_gate/` (seul `schema.py` sert) dans `/usr/lib/qaf/`.
2. Claude Code : `qaf_mcp/mcp.json.exemple` → `.mcp.json` du projet de travail de
   l'agent (ou `claude mcp add`), skills `skill/qaf*/` → `~/.claude/skills/` par
   `poser_skills.sh` (remplace les `qaf`/`qaf-…`, garde les skills `perso-…` de l'agent).
3. Politique dom0 (format v5, comme `qubes.NotifyTools` dans `90-default.policy`) :
   `qaf.Plan  *  sys-agent  dom0  allow` — à livrer dans `policy/`, pas ici.

## Réponses

- Exit 126 de `qrexec-client-vm` = politique refusée, 127 = service absent
  (`libqrexec/qrexec.h`). Rendus en `statut: refuse_par_qrexec` / `service_absent`.
- Sinon la sortie du Doorman, au contrat unique de `qaf_gate/proto.py` :
  `statut` parmi `fait`, `refuse`, `erreur`, `interrompu`, `expire` (seul `fait`
  est un succès), plus `raison`, `request`, `resultats`, `non_executees` selon
  le cas. Rendue en lignes `clé: valeur` (`statut` d'abord) ; tout autre texte
  est une erreur, nettoyée (caractères de contrôle) et tronquée à 8000 caractères.
- Délai : `QAF_DELAI`, jamais moins que `proto.DELAI_AGENT` (360 s), plus long que
  tous les délais du Doorman. `qaf_gate/schema.py` et `qaf_gate/proto.py` sont
  donc aussi installés dans sys-agent.

## Demandes de l'utilisateur (C13)

```
palette (dom0) ──socket demande.sock──▶ exécuteur : tire R-xxxxxx, note texte + qubes nommés
palette (dom0) ──qvm-run --pass-io --service sys-agent qaf.Request──▶ qaf_mcp/demande.py
      ouvre xfce4-terminal --disable-server -x env QAF_REQUEST=R-xxxxxx claude -- <texte>
qaf_mcp ──plan {"request": R-xxxxxx, "session": S-…, ...}──▶ portier : demande connue ? liée à cette session ?
                                                          opération dans sa portée ?
```

- Fichiers pour `tpl-agent` (posés par `tools/setup-sys-agent.sh`) :
  `etc/qubes-rpc/qaf.Request` → `/etc/qubes-rpc/`, `etc/qubes/rpc-config/qaf.Request`
  (`wait-for-session=true`).
- Le service refuse tout appelant autre que dom0 (`QREXEC_REMOTE_DOMAIN`) ; la
  politique `00-qaf.policy` refuse `qaf.Request` depuis tout qube.
- L'identifiant de la demande n'est donné qu'au claude lancé pour elle
  (`QAF_REQUEST`, lu par qaf_mcp dans son environnement ou celui de son parent).
  Plus de fichier `/run/qaf/demande` commun à toutes les sessions (PARCOURS-1 M3).
  Sans demande : `R-0000`, que le Doorman traite comme une demande absente.
- qaf_mcp tire un identifiant de session (`S-` + 16 hexadécimaux) à son
  démarrage. Le Doorman lie la demande à la première session qui la cite et
  la fait expirer 10 min après le dernier plan de cette session. Cet
  identifiant n'autorise rien : il empêche seulement une autre session
  d'hériter de la demande.

## TODO

- Un plan = une opération. Les plans à plusieurs étapes (créer + donner le réseau)
  sont possibles côté portier mais pas encore exposés.

## Test manuel

```
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -m qaf_mcp
```
