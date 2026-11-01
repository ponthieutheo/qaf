"""qaf-voisin : l'IA locale dans un autre qube. Autorise sys-agent à joindre UN port
d'UN qube choisi par l'utilisateur dans dom0, et le retire. À lancer en root (sudo),
comme qaf-suspendre : seul l'utilisateur de dom0 y a la main, l'agent n'y a aucun chemin.
Le geste est dans la carte de l'agent (agent_panneau_gtk.py) ; rien ne vient de qaf.Usage.

Une seule règle à la fois, dans un fichier à nous, jamais livré et jamais copié de
sys-agent :

    /etc/qubes/policy.d/00-qaf-0-voisin.policy
    qubes.ConnectTCP +PORT sys-agent @default allow target=QUBE

- Trié AVANT 00-qaf-agent-long.policy (« 0 » avant « a ») : sinon la ligne
  `* * sys-agent @anyvm deny` de la liste blanche passe devant et la règle ne sert à rien
  (essai réel). Ce fichier-là n'est pas modifié : install.sh ne l'accepte qu'identique.
- Forme exacte : install.sh et qaf-installer --verifier n'admettent avant 00-qaf.policy
  que ce nom, dans /etc, avec une seule ligne de cette forme (voisin_valide des deux
  scripts ; même motif que REGLE ci-dessous, vérifié par les tests). Toute autre ligne qui
  cite sys-agent reste refusée, et la mise à jour avec.
- @default et non le nom du qube : c'est la forme essayée pour de vrai. La cible est
  fixée ici, dom0 seul la connaît, et un autre port ou une cible nommée retombent sur la
  liste blanche. Une connexion démarre le qube s'il est éteint.

Aucun fichier de Qubes n'est touché ; `qaf-voisin retirer` ou la désinstallation effacent
le fichier. dom0 ne reçoit ni clé ni réseau : seuls un nom de qube et un port passent ici.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from .i18n import t

AGENT = "sys-agent"
NOM_FICHIER = "00-qaf-0-voisin.policy"
FICHIER = "/etc/qubes/policy.d/" + NOM_FICHIER
LONG = "/etc/qubes/policy.d/00-qaf-agent-long.policy"
PORTIER = "/etc/qaf/portier.json"
QAF_VOISIN = "/usr/bin/qaf-voisin"
CLASSES = ("AppVM", "StandaloneVM")
ENTETE = "## QAF : qube voisin, ecrit par qaf-voisin. Retirer : sudo qaf-voisin retirer\n"
# Mêmes bornes que voisin_valide de packaging/install.sh et de tools/qaf-installer.
REGLE = re.compile(r"\Aqubes\.ConnectTCP \+([1-9][0-9]{0,4}) sys-agent @default allow "
                   r"target=([a-zA-Z][a-zA-Z0-9_.-]{0,30})\Z")
_NOM = re.compile(r"\A[a-zA-Z][a-zA-Z0-9_.-]{0,30}\Z")
_PORT = re.compile(r"\A[1-9][0-9]{0,4}\Z")
_INTERDIT = re.compile(r"\A(dom0\Z|sys-|qaf-|disp[0-9])")


@dataclass(frozen=True)
class Regle:
    qube: str
    port: int


@dataclass(frozen=True)
class Qube:
    nom: str
    classe: str
    couleur: str
    en_marche: bool
    modele_jetables: bool = False
    fournit_reseau: bool = False
    etiquettes: tuple = ()


def nom_valide(nom):
    return isinstance(nom, str) and bool(_NOM.match(nom)) and not _INTERDIT.match(nom)


def port_valide(texte):
    """Entier de 1 à 65535 écrit sans rien autour, sinon None."""
    if not isinstance(texte, str) or not _PORT.match(texte) or int(texte) > 65535:
        return None
    return int(texte)


def noms_interdits(chemin=PORTIER):
    """Coffres, qubes système et réglages du Doorman (portier.json, lu ici seulement pour
    filtrer). None si illisible : échec fermé, aucun qube proposé."""
    try:
        with open(chemin, encoding="utf-8") as f:
            r = json.loads(f.read(64 * 1024))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(r, dict):
        return None
    noms = {"dom0", AGENT, "vault"}
    for cle in ("coffres", "systeme", "admin_qubes", "ne_jamais_demarrer", "coffres_entrables", "jetables"):
        v = r.get(cle, [])
        if not isinstance(v, list):
            return None
        noms |= {x for x in v if isinstance(x, str)}
    return noms


def admissible(q, interdits):
    """Ni dom0, ni sys-agent, ni modèle, ni jetable, ni sys-*, ni qube réseau, ni coffre."""
    return (nom_valide(q.nom) and q.nom not in interdits and q.classe in CLASSES and not q.modele_jetables
            and not q.fournit_reseau and "qaf-agent" not in q.etiquettes)


def _qube(vm):
    return Qube(vm.name, vm.klass, str(vm.label.color), bool(vm.is_running()),
                bool(getattr(vm, "template_for_dispvms", False)), bool(getattr(vm, "provides_network", False)),
                tuple(vm.tags))


def lister(app=None, portier=PORTIER):
    """Qubes proposés, triés par nom, ou None si qubesd ou portier.json ne répondent pas.
    Un qube illisible n'est pas proposé."""
    interdits = noms_interdits(portier)
    if interdits is None:
        return None
    try:
        if app is None:
            import qubesadmin
            app = qubesadmin.Qubes()
        qubes = []
        for vm in app.domains:
            try:
                qubes.append(_qube(vm))
            except Exception:  # noqa: BLE001 (qube en cours de suppression, propriété refusée)
                continue
    except Exception:  # noqa: BLE001 (qubesadmin absent, qubesd arrêté)
        return None
    return sorted((q for q in qubes if admissible(q, interdits)), key=lambda q: q.nom)


def marche(nom, executer=subprocess.run):
    """État réel du qube, lu dans dom0 : c'est tout ce qu'on sait de lui sans le réveiller.
    None si on ne sait pas."""
    if not nom_valide(nom):
        return None
    try:
        r = executer(["qvm-check", "--quiet", "--running", nom], stdin=subprocess.DEVNULL, capture_output=True,
                     timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def ligne_regle(qube, port):
    return f"qubes.ConnectTCP +{port} {AGENT} @default allow target={qube}"


def lire_regle(chemin=FICHIER):
    """La règle en place (lecture sans droits), ou None : absente, ou de forme inattendue."""
    try:
        with open(chemin, encoding="ascii", newline="") as f:  # pas de traduction des fins de ligne
            texte = f.read(4097)
    except (OSError, ValueError):
        return None
    # Comme voisin_valide : qrexec coupe aussi sur \r et autres fins de ligne.
    if len(texte) > 4096 or not texte.replace("\n", "").isprintable():
        return None
    lignes = [x for x in texte.split("\n") if x and not x.startswith("#")]
    m = REGLE.match(lignes[0]) if len(lignes) == 1 else None
    if m is None or port_valide(m.group(1)) is None or not nom_valide(m.group(2)):
        return None
    return Regle(m.group(2), int(m.group(1)))


def ecrire(qube, port, chemin=FICHIER):
    """Écriture atomique, 0644 quel que soit l'umask hérité de sudo. Le nom temporaire ne
    finit pas par .policy : qrexec ne le lit pas."""
    tmp = chemin + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(ENTETE + ligne_regle(qube, port) + "\n")
        f.flush()
        os.fchmod(f.fileno(), 0o644)
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def retirer(chemin=FICHIER):
    for c in (chemin, chemin + ".tmp"):
        try:
            os.unlink(c)
        except FileNotFoundError:
            pass


def commande(action, qube=None, port=None):
    """argv fixe de la carte. Les valeurs sont validées ici, puis de nouveau par root."""
    if action == "retirer":
        return ["sudo", "-n", QAF_VOISIN, "retirer"]
    if action != "autoriser" or not nom_valide(qube) or port_valide(str(port)) is None:
        raise ValueError(f"demande invalide : {action}")
    return ["sudo", "-n", QAF_VOISIN, "autoriser", qube, str(port)]


def lancer(action, qube=None, port=None, executer=subprocess.run):
    """Bloquant : hors du fil GTK. Renvoie True si la commande a réussi."""
    try:
        r = executer(commande(action, qube, port), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return r.returncode == 0


def main(argv=None, chemin=FICHIER, long_=LONG, qubes=lister):
    """qaf-voisin [etat | autoriser QUBE PORT | retirer] — root, dans dom0."""
    args = sys.argv[1:] if argv is None else argv
    args = args or ["etat"]
    try:
        return _executer(args, chemin, long_, qubes)
    except PermissionError:
        print(t("voisin.root"), file=sys.stderr)
        return 1


def _executer(args, chemin, long_, qubes):
    if args == ["etat"]:
        r = lire_regle(chemin)
        print(t("carte.autorise", qube=r.qube, port=r.port) if r else t("voisin.aucune"))
        return 0
    if args == ["retirer"]:
        retirer(chemin)
        print(t("voisin.retire"))
        return 0
    if len(args) == 3 and args[0] == "autoriser" and nom_valide(args[1]) and port_valide(args[2]) is not None:
        if not os.path.isfile(long_):  # sans la liste blanche, une règle seule ne serait pas bornée
            print(t("voisin.sans_agent"), file=sys.stderr)
            return 1
        proposes = qubes()
        if proposes is None or args[1] not in {q.nom for q in proposes}:
            print(t("voisin.qube_refuse", qube=args[1]), file=sys.stderr)
            return 1
        ecrire(args[1], port_valide(args[2]), chemin)
        print(t("carte.autorise", qube=args[1], port=args[2]))
        return 0
    print(t("voisin.usage"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
