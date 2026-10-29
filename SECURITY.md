# Sécurité de QAF

*[English below](#english)*

QAF donne à un agent IA la main sur Qubes OS sans lui donner aucun droit. Une faille qui permettrait à l'agent de contourner le Doorman, la fenêtre de confirmation ou le journal est exactement ce que ce projet cherche à empêcher : merci de la signaler.

## Signaler une faille

- **Jamais dans un ticket public** (ni issue, ni discussion, ni pull request) : une faille publiée avant son correctif expose tous ceux qui ont installé QAF.
- Passe par le **signalement privé de GitHub** : [https://github.com/ponthieutheo/qaf/security/advisories/new](https://github.com/ponthieutheo/qaf/security/advisories/new) (onglet *Security*, puis *Report a vulnerability*). Seul le mainteneur lit le rapport ; le correctif se prépare en privé, puis l'avis est publié avec lui, en te citant si tu le souhaites.
- Dis ce qu'il faut pour reproduire : version (commit), opération ou plan en cause, ce que tu attendais, ce qui s'est passé. Un plan JSON ou un test pytest qui échoue est la meilleure preuve.
- Réponse visée sous 7 jours. QAF est un prototype maintenu par une seule personne, sans programme de prime.

## Ce qui compte comme faille

Le modèle de menace part d'un **agent manipulé** (injection de prompt par le Web, un fichier, une sortie de commande) qui fait tout ce qu'il peut depuis `sys-agent`. Est une faille, par exemple :

- une action N2 ou plus exécutée **sans** fenêtre de confirmation (hors suspension demandée par l'utilisateur) ; une action N4 exécutée tout court ;
- un plan qui passe le schéma fermé avec un champ, une valeur ou une opération qu'il aurait dû refuser ;
- une fenêtre de confirmation qui montre autre chose que ce qui sera exécuté, ou qu'un qube peut voir, cliquer ou imiter ; une autorisation obtenue sans la touche A du clavier interne ;
- `sys-agent` qui joint dom0 ou un autre qube par un autre chemin que `qaf.Plan` ;
- une ligne du journal chaîné modifiée, supprimée ou réordonnée sans que l'exécuteur le voie ;
- une installation ou un retrait (`qaf-installer`, `--retour`) qui affaiblit une protection d'origine de Qubes.

## Hors du périmètre

- Les failles de Qubes OS, Xen, qrexec ou du noyau : à signaler au [projet Qubes OS](https://www.qubes-os.org/security/).
- Ce que QAF assume et documente : `sys-agent` a le réseau ; les demandes partent chez le fournisseur du modèle ; la suspension des confirmations, quand l'utilisateur l'a demandée (voir [« Ce que QAF ne promet pas »](README.md#ce-que-qaf-ne-promet-pas)).

## Pour aller plus loin

- [README, section Sécurité](README.md#sécurité) : niveaux N0 à N4, confirmation, Doorman, journal, politiques.
- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) : ce que l'installateur pose, le contrôle de non-régression et le retrait complet (`--retour`).
- Aucun audit externe n'a encore été fait.

---

## English

QAF gives an AI agent control over Qubes OS without giving it any rights. A flaw that would let the agent get around the Doorman, the confirmation window or the log is exactly what this project exists to prevent: please report it.

**Reporting.** Never in a public issue, discussion or pull request: a flaw made public before its fix exposes everyone who installed QAF. Use GitHub's **private vulnerability reporting**: [https://github.com/ponthieutheo/qaf/security/advisories/new](https://github.com/ponthieutheo/qaf/security/advisories/new) (*Security* tab, then *Report a vulnerability*). Only the maintainer reads the report; the fix is prepared privately and the advisory is published with it, crediting you if you wish. Include the commit, the operation or plan involved, what you expected and what happened; a JSON plan or a failing pytest test is the best evidence. We aim to answer within 7 days. QAF is a prototype maintained by one person, with no bounty programme.

**In scope.** The threat model assumes a **manipulated agent** doing everything it can from `sys-agent`: an N2-or-higher action carried out without a confirmation window (outside a suspension the user asked for), any N4 action carried out, a plan that gets through the closed schema when it should not, a confirmation window that shows something other than what will run or that a qube can see, click or imitate, an approval without the A key of the internal keyboard, `sys-agent` reaching dom0 or another qube other than through `qaf.Plan`, an undetected change to the hash-chained log, an install or removal that weakens one of Qubes' own protections.

**Out of scope.** Flaws in Qubes OS, Xen, qrexec or the kernel (report them to the [Qubes OS project](https://www.qubes-os.org/security/)); what QAF documents and accepts (see ["What QAF does not promise"](README.en.md#what-qaf-does-not-promise)). 

More: [README, Security section](README.en.md#security), [`docs/INSTALLATION.md`](docs/INSTALLATION.md) (installation and full removal, in French). No external audit has been done yet.
