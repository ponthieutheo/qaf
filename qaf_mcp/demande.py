"""Service qrexec qaf.Request de l'agent : dom0 (la palette) transmet la
demande que l'utilisateur vient de taper.

Entrée (stdin, bornée) : {"request": "R-123456", "texte": "..."} en JSON,
avec "cli" facultatif (qaf_gate.schema.CLIS). Ouvre un terminal avec le CLI
lancé sur le texte, et l'identifiant dans SON environnement (QAF_REQUEST), lu
par qaf_mcp pour chaque plan : le Doorman le confronte à son registre des
demandes (C13), où dom0 l'a lié à CE qube (AGENT-JETABLE §7).

Agent permanent (docs/AGENT-JETABLE.md D28, défaut depuis le 27/10) : le
service tourne dans sys-agent, que la palette (dom0) a démarré s'il était
arrêté. Une seule fenêtre « Agent » : le terminal ouvert par la demande
précédente (pid noté sous /run/user/<uid>/qaf-request/terminal, revérifié dans
/proc : notre terminal, notre titre) est fermé, puis un nouveau terminal lance
claude --continue sur la nouvelle demande si une conversation existe déjà dans
~/agent : la conversation et ~/.claude (mémoire, identifiants) sont gardés, et
un seul claude tourne à la fois (un seul processus rafraîchit le jeton OAuth).
Le nouveau claude a le NOUVEL identifiant dans son environnement ; l'ancien,
remplacé dans le registre de dom0 (P1), ne couvrirait plus rien. Le jeton
longue durée (~/.config/qaf/jeton-claude) reste une alternative à la connexion
OAuth.

Agent jetable (mode inactif, agent_long faux) : le service tourne dans un
jetable de qaf-agent-dvm que le Doorman vient de lancer pour cette demande.
Dans un jetable :
- le jeton longue durée de Claude Code (~/.config/qaf/jeton-claude, posé par
  l'utilisateur dans le modèle, §4) est passé dans CLAUDE_CODE_OAUTH_TOKEN, à
  l'environnement du seul terminal lancé ici (jamais dans l'argv) ;
- le trafic non essentiel et les mises à jour automatiques de claude sont
  coupés (le pare-feu fermé du jetable ne joint que l'API, R-1) ;
- quand le DERNIER terminal d'agent du jetable se ferme, le jetable s'éteint
  (sudo -n systemctl poweroff) et Qubes le supprime (n13, PARCOURS-3) : chaque
  terminal tient un verrou partagé (flock) sur un fichier du jetable ; à sa
  fermeture, celui qui obtient le verrou exclusif est le dernier. Le noyau
  libère le verrou d'un processus tué : pas de compteur à dériver. Ce n'est
  qu'un confort (R-8) : les vraies garanties sont les minuteries du Doorman ;
- une demande qui arrive dans un jetable où un agent a déjà tourné est une
  suite (« Continuer avec cet agent ») : claude --continue reprend la
  conversation dans le même dossier (n14).

L'identifiant n'est qu'un renvoi : le Doorman ne croit que son propre registre.
Il est quand même validé ici (format du schéma), et l'appelant doit être dom0
(QREXEC_REMOTE_DOMAIN, fourni par qrexec, en plus de la politique).
Bibliothèque standard seulement. Lancé par /etc/qubes-rpc/qaf.Request.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata

from qaf_gate.schema import CLIS, TASK_INTERDITS, _REQUEST_RE

from .server import VARIABLE_DEMANDE

MAX_ENTREE = 16 * 1024
MAX_TEXTE = 2000  # = qaf_gate.registry.MAX_TEXTE (vérifié par les tests)
TERMINAL = "/usr/bin/xfce4-terminal"  # livré par debian-13-xfce (base de tpl-agent)
CLAUDE = "/usr/bin/claude"  # installé sous /usr par tools/setup-sys-agent.sh
ENV = "/usr/bin/env"
SH = "/bin/sh"
DOSSIER_TRAVAIL = "agent"  # ~/agent : dossier de travail du CLI
GEOMETRIE = "--geometry=120x36"  # M4 (PARCOURS-2 p3) : plus grand que 80x24
TITRE = "Agent"  # fixe, ASCII : jamais le texte de la demande dans le titre (DESIGN-SESSION §9)
# argv fixe par CLI (AGENT-JETABLE §12) : le texte vient toujours après « -- ».
ARGV_CLI = {"claude": [CLAUDE]}
# Environnement fixe du CLI dans un jetable (§3, §13.7).
ENV_JETABLE = ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", "DISABLE_AUTOUPDATER=1")
# sys-agent : claude vient du modèle (/usr, tpl-agent), en version épinglée mise à
# jour par qaf-installer ; sa mise à jour automatique échouerait (« no write
# permission to npm prefix », vu le 27/10).
ENV_SYS_AGENT = ("DISABLE_AUTOUPDATER=1",)
JETON = ".config/qaf/jeton-claude"  # dans le volume privé de qaf-agent-dvm (§4.1, piste A)
VARIABLE_JETON = "CLAUDE_CODE_OAUTH_TOKEN"
MAX_JETON = 1024
QUBESDB_TYPE = ["/usr/bin/qubesdb-read", "/type"]  # classe du qube ; /qubes-vm-type vaut « AppVM » dans un jetable (Qubes 4.3.2)
# Dans un jetable : prendre le verrou partagé ($1), attendre le terminal (au
# premier plan de ce sh détaché, sans le descripteur du verrou : une appli
# lancée en arrière-plan par l'agent ne le garde pas), puis éteindre si plus
# aucun autre terminal ne tient le verrou (n13). "$@" : l'argv du terminal,
# jamais interprété par le shell.
FLOCK = "/usr/bin/flock"  # util-linux, livré par debian-13-xfce
EXTINCTION = "sudo -n systemctl poweroff"
# « command exec » : un échec d'ouverture du verrou ne tue pas le sh (exec est un
# builtin spécial) ; le terminal se lance quand même, sans extinction à la fin.
APRES = (f'trap "" HUP; v="$1"; shift; command exec 9>>"$v"; {FLOCK} -s 9; "$@" 9>&-; '
         f'{FLOCK} -n -x 9 && {EXTINCTION}')
VERROU = ("qaf-request", "agents")  # sous /run/user/<uid> (tmpfs du jetable)
TERMINAL_PID = ("qaf-request", "terminal")  # sys-agent : pid du terminal « Agent » de la demande précédente
DELAI_FERMETURE = 5.0  # secondes laissées à l'ancien claude pour finir d'écrire sa conversation


class DemandeInvalide(ValueError):
    pass


def lire(donnees: bytes):
    """Octets reçus de dom0 -> (identifiant, texte, cli), ou DemandeInvalide."""
    if len(donnees) > MAX_ENTREE:
        raise DemandeInvalide("entrée trop grande")
    try:
        obj = json.loads(donnees.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise DemandeInvalide("JSON invalide") from None
    if not isinstance(obj, dict) or not {"request", "texte"} <= set(obj) <= {"request", "texte", "cli"}:
        raise DemandeInvalide("champs attendus : request, texte (cli facultatif)")
    rid, texte, cli = obj["request"], obj["texte"], obj.get("cli", "claude")
    if not isinstance(rid, str) or not _REQUEST_RE.match(rid):
        raise DemandeInvalide("identifiant de demande invalide")
    if not isinstance(texte, str) or not texte.strip() or len(texte) > MAX_TEXTE:
        raise DemandeInvalide("texte vide ou trop long")
    if any(c != "\n" and unicodedata.category(c) in TASK_INTERDITS for c in texte):
        raise DemandeInvalide("caractère de contrôle interdit")
    if not isinstance(cli, str) or cli not in CLIS or cli not in ARGV_CLI:
        raise DemandeInvalide("CLI inconnu")
    return rid, texte, cli


def commande_terminal(rid, texte, cli="claude", jetable=False, suite=False):
    """argv du terminal : pas de shell, le texte est un seul argument après
    « -- » (il ne peut pas être pris pour une option du CLI). env pose
    QAF_REQUEST pour ce seul CLI (et son serveur MCP) ; --disable-server :
    un terminal neuf, pas un onglet d'un terminal déjà lancé avec un autre
    environnement, et qui ne rend la main qu'à sa fermeture. suite :
    --continue, la conversation de l'agent est reprise (n14 dans un jetable ;
    dans sys-agent dès qu'une conversation existe, D28).
    Titre fixe « Agent » (Qubes écrit « [dispNNNN] Agent ») : la demande reste
    dans le contenu de la console, jamais dans la barre de titre violette."""
    return [TERMINAL, "--disable-server", GEOMETRIE, f"--title={TITRE}", "-x", ENV,
            f"{VARIABLE_DEMANDE}={rid}", *(ENV_JETABLE if jetable else ENV_SYS_AGENT), *ARGV_CLI[cli],
            *(("--continue",) if suite else ()), "--", texte]


def commande(rid, texte, cli="claude", jetable=False, verrou=None, suite=False):
    """Ce que lance le service : le terminal, suivi dans un jetable de
    l'extinction au dernier terminal (APRES, n13, R-8). Sans fichier de
    verrou, pas d'extinction : les minuteries de dom0 s'en chargent."""
    argv = commande_terminal(rid, texte, cli, jetable, suite)
    return [SH, "-c", APRES, "qaf-agent", verrou, *argv] if jetable and verrou else argv


def verrou_agents(racine=None):
    """(fichier de verrou des terminaux d'agent, suite) : suite est vrai si un
    agent a déjà tourné dans ce jetable (le fichier existait déjà). Créé sous
    /run/user/<uid> (tmpfs, perdu avec le jetable) ; (None, False) en cas
    d'échec."""
    racine = racine or f"/run/user/{os.getuid()}"
    dossier, fichier = os.path.join(racine, VERROU[0]), os.path.join(racine, *VERROU)
    try:
        os.makedirs(dossier, mode=0o700, exist_ok=True)
        suite = os.path.exists(fichier)
        with open(fichier, "a"):
            pass
    except OSError:
        return None, False
    return fichier, suite


def conversation_existe(environ, dossier):
    """Vrai si claude a déjà une conversation pour `dossier` (fichiers .jsonl
    sous ~/.claude/projects/<dossier, chaque caractère hors [A-Za-z0-9]
    remplacé par « - »>) : --continue a alors de quoi reprendre."""
    base = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(environ.get("HOME") or "/", ".claude")
    projet = os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", dossier))
    try:
        return any(n.endswith(".jsonl") for n in os.listdir(projet))
    except OSError:
        return False


def fichier_terminal(racine=None):
    """Fichier du pid du terminal « Agent » de sys-agent (tmpfs), ou None."""
    racine = racine or f"/run/user/{os.getuid()}"
    try:
        os.makedirs(os.path.join(racine, TERMINAL_PID[0]), mode=0o700, exist_ok=True)
    except OSError:
        return None
    return os.path.join(racine, *TERMINAL_PID)


def fermer_precedent(fichier, proc="/proc", tuer=os.kill, dormir=time.sleep, delai=DELAI_FERMETURE):
    """sys-agent (D28) : ferme le terminal « Agent » de la demande précédente,
    s'il tourne encore et si /proc confirme que c'est bien lui (notre
    terminal, notre titre) : un pid réutilisé par un autre processus n'est
    jamais tué. SIGTERM (claude finit d'écrire sa conversation), puis SIGKILL
    après `delai`. Rend True si un terminal a été fermé."""
    try:
        with open(fichier, encoding="ascii") as f:
            pid = int(f.read(16).strip())
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
            argv = f.read(1 << 16).split(b"\0")
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if pid <= 1 or argv[:1] != [TERMINAL.encode()] or f"--title={TITRE}".encode() not in argv:
        return False
    try:
        tuer(pid, signal.SIGTERM)
    except OSError:
        return False
    vivant = os.path.join(proc, str(pid))
    fin = time.monotonic() + delai
    while os.path.exists(vivant) and time.monotonic() < fin:
        dormir(0.1)
    if os.path.exists(vivant):
        try:
            tuer(pid, signal.SIGKILL)
        except OSError:
            pass
    return True


def noter_terminal(fichier, pid):
    """Note le pid du nouveau terminal « Agent » (fermé à la demande suivante)."""
    if not fichier or type(pid) is not int or pid <= 1:
        return
    try:
        with open(fichier + ".tmp", "w", encoding="ascii") as f:
            f.write(f"{pid}\n")
        os.replace(fichier + ".tmp", fichier)
    except OSError:
        pass


def dossier_de_travail(environ):
    """~/agent, déjà approuvé dans ~/.claude.json (setup-agent-dvm.sh,
    setup-sys-agent.sh) : claude ne pose plus la question « trust this folder »
    (M4). Repli sur le dossier personnel si ~/agent manque."""
    maison = environ.get("HOME") or "/"
    dossier = os.path.join(maison, DOSSIER_TRAVAIL)
    return dossier if os.path.isdir(dossier) else maison


def est_jetable(executer=subprocess.run):
    """Vrai dans un jetable (QubesDB /type = DispVM, écrit par dom0)."""
    try:
        r = executer(QUBESDB_TYPE, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == b"DispVM"


def jeton(environ):
    """Jeton longue durée de Claude Code, ou None (absent, illisible, mal formé)."""
    try:
        with open(os.path.join(environ.get("HOME") or "/", JETON), encoding="ascii") as f:
            j = f.read(MAX_JETON + 1).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return j if j and len(j) <= MAX_JETON and all("!" <= c <= "~" for c in j) else None


def main(entree=None, environ=None, lancer=subprocess.Popen, jetable=None, racine_verrou=None,
         fermer=fermer_precedent):
    environ = os.environ if environ is None else environ
    entree = sys.stdin.buffer if entree is None else entree
    if environ.get("QREXEC_REMOTE_DOMAIN") != "dom0":
        print("refusé : qaf.Request n'accepte que dom0", file=sys.stderr)
        return 1
    try:
        rid, texte, cli = lire(entree.read(MAX_ENTREE + 1))
    except DemandeInvalide as e:
        print(f"refusé : {e}", file=sys.stderr)
        return 1
    jetable = est_jetable() if jetable is None else jetable
    env = {k: v for k, v in environ.items() if k != VARIABLE_JETON}
    j = jeton(environ)  # jetable : seule connexion (§4.1) ; sys-agent : alternative à la connexion OAuth
    if j:
        env[VARIABLE_JETON] = j
    cwd, pid_terminal = dossier_de_travail(environ), None
    if jetable:
        verrou, suite = verrou_agents(racine_verrou)
    else:  # sys-agent (D28) : une seule fenêtre « Agent », conversation reprise
        verrou, pid_terminal = None, fichier_terminal(racine_verrou)
        if pid_terminal:
            fermer(pid_terminal)
        suite = conversation_existe(environ, cwd)
    # Détaché : qrexec rend la main à dom0 tout de suite, le terminal reste.
    p = lancer(commande(rid, texte, cli, jetable, verrou, suite), stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=cwd, env=env)
    noter_terminal(pid_terminal, getattr(p, "pid", None))
    print(f"ok {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
