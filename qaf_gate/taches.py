"""qaf-gate-taches : le seul processus du Doorman qui lance qrexec-client (H3).

L'exécuteur crée et démarre le jetable par qubesd, puis demande ici une seule
chose : envoyer une tâche bornée au service qaf.Task du jetable qu'il désigne
et lire l'accusé, borné lui aussi. Ce service ne parle pas à qubesd, ne
choisit aucune cible et ne renvoie rien d'autre que {"accepte": bool}.

Pourquoi un processus à part : qrexec-client ouvre un vchan (périphériques Xen
/dev/xen/*, xenstore). Les donner à l'exécuteur, qui tient déjà qubesd,
élargirait le processus le plus puissant du Doorman. Ici, le compte
qaf-taches n'a que ces périphériques et le groupe qubes (sockets des
qrexec-daemon), pas qubesd (packaging/qaf-gate-taches.service).

Seul client admis : l'exécuteur (uid qaf-exec, vérifié par SO_PEERCRED, en plus
du mode du socket).
  {"type": "tache", "jetable": "dispNNNN", "tache": t, "delai": s}
  -> {"accepte": true|false, "raison": r}

OPS-v3 (§3.1, §3.4) : relais qrexec du Doorman, liste fermée de messages.
  {"type": "hand", "qube": q, "service": "qaf.Exec"|"qaf.Screen"|"qaf.Input",
   "requete": {...}, "delai": s}
  -> {"ok", "code", "octets", "b64"} : sortie de qaf_hand lue jusqu'à une borne
     fixée ICI par service, renvoyée en blob opaque, jamais analysée dans dom0.
  {"type": "vmexec", "qube": q, "action": a, "delai": s, ...champs de VMEXEC[a]}
  -> {"ok", "codes"} : message typé, sans argv (V10). Les champs sont revalidés
     ici avec les validateurs du schéma, l'argv est construit ici, passé à
     qubes.VMExec (livré par Qubes), en root seulement pour pkg.*. Les actions
     file.* lancent l'outil de Qubes DANS le qube source, en utilisateur : la
     copie passe par qubes.Filecopy et sa fenêtre native (D17), jamais par dom0.
     P-4 : au-delà de 4 096 octets, l'appel tue le qrexec-daemon du qube ; on
     refuse donc toute commande de plus de MAX_VMEXEC octets, sans rien envoyer.
"""

import base64
import json
import os
import pwd
import re
import select
import socket
import socketserver
import struct
import subprocess
import sys
import time

from . import proto, schema

SOCK_TACHES = "/run/qaf-taches/taches.sock"  # créé par qaf-gate-taches.socket
QREXEC_CLIENT = "/usr/lib/qubes/qrexec-client"  # = qubesadmin.config.QREXEC_CLIENT
COMPTE_EXECUTEUR = "qaf-exec"
MAX_ACCUSE = 4096  # octets lus au plus dans la réponse de qaf.Task
DELAI_MAX = proto.DUREE_MAX_OP["dispvm.run_task"]
MAX_MESSAGE = 64 * 1024  # tâche de 1 000 caractères, ou requête qaf_hand (16 Kio encodée)
MAX_REQUETE = 16 * 1024  # = qaf_hand.service.MAX_ENTREE
# Octets lus au plus dans la réponse de qaf_hand (§3.4), par service et action.
BORNES = {"qaf.Exec": 128 * 1024, "qaf.Screen": 640 * 1024, "qaf.Input": 4096}
BORNE_LISTE = 16 * 1024  # qaf.Screen « lister »
DELAI_HAND = proto.DUREE_MAX_OP["qube.exec"]
DELAI_VMEXEC = proto.DUREE_MAX_OP["pkg.install"]
# Commandes fixes de pkg.install. Debian : --no-remove, jamais de retrait en cascade.
PKG_INSTALL = {
    "debian": (["apt-get", "-q", "update"],
               ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-q", "-y", "--no-remove", "install", "--"]),
    "fedora": (["dnf", "-q", "-y", "install", "--"],),
}
PKG_REMOVE = {"debian": (["dpkg", "--remove", "--"],), "fedora": (["rpm", "-e", "--"],)}  # pas de cascade
MAX_VMEXEC = 3500  # octets de « user:QUBESRPC qubes.VMExec+… dom0 » (P-4 : 4 097 tue le qrexec du qube)
# Action -> (champs propres, en root ?).
VMEXEC = {"pkg.install": ({"distro", "paquets"}, True), "pkg.remove": ({"distro", "paquets"}, True),
          "file.copy": ({"dest", "utilisateur", "chemin"}, False), "file.move": ({"dest", "utilisateur", "chemin"}, False),
          "file.open_dispvm": ({"utilisateur", "chemin"}, False)}
_UTILISATEUR = re.compile(r"\A[a-z_][a-z0-9_-]{0,31}\Z")
# Noms donnés par qubesd aux jetables (qubes/vm/dispvm.py : "disp" + dispid).
_JETABLE_RE = re.compile(r"\Adisp[0-9]{1,6}\Z")


def _qube(v):
    schema._name(v, "qube")
    if v == "dom0":  # qrexec-client -d dom0 lancerait la commande dans dom0
        raise ValueError("dom0 n'est jamais une cible")
    return v


def _delai(d, maxi):
    if type(d) is not int or not 1 <= d <= maxi:
        raise ValueError("délai invalide")
    return d


_SURS = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")


def encoder_vmexec(argv):
    """Arguments de qubes.VMExec, comme qubesadmin.utils.encode_for_vmexec : sur
    les octets UTF-8, « - » devient « -- », tout octet hors [a-zA-Z0-9_.] -XX."""
    return "+".join("".join(chr(b) if b in _SURS else "--" if b == 0x2D else f"-{b:02X}" for b in a.encode("utf-8"))
                    for a in argv)


def valider(msg):
    """(jetable, tache, delai) ou ValueError. Schéma fermé."""
    if not isinstance(msg, dict) or set(msg) != {"type", "jetable", "tache", "delai"} or msg["type"] != "tache":
        raise ValueError("message inconnu")
    if not isinstance(msg["jetable"], str) or not _JETABLE_RE.match(msg["jetable"]):
        raise ValueError("nom de jetable invalide")
    schema._task(msg["tache"], "tache")  # mêmes bornes que le schéma des plans
    d = msg["delai"]
    if type(d) is not int or not 1 <= d <= DELAI_MAX:
        raise ValueError("délai invalide")
    return msg["jetable"], msg["tache"], d


def lire_borne(p, fin, maxi=MAX_ACCUSE, horloge=time.monotonic):
    """Lit au plus maxi + 1 octets de p.stdout avant l'échéance `fin`.
    Renvoie les octets lus jusqu'à la fin du flux, ou None si la réponse est
    trop longue ou en retard. Ne retient jamais plus de maxi + 1 octets."""
    fd, lus = p.stdout.fileno(), b""
    while len(lus) <= maxi:
        reste = fin - horloge()
        if reste <= 0:
            return None
        prets, _, _ = select.select([fd], [], [], reste)
        if prets:
            bloc = os.read(fd, maxi + 1 - len(lus))
            if not bloc:
                return lus
            lus += bloc
    return None


def envoyer(jetable, tache, delai, lancer=subprocess.Popen, horloge=time.monotonic):
    """Confie la tâche à qaf.Task dans `jetable` ; une seule échéance pour tout."""
    fin = horloge() + delai
    argv = [QREXEC_CLIENT, "-d", jetable, "-w", str(delai), "DEFAULT:QUBESRPC qaf.Task dom0"]
    entree = json.dumps({"tache": tache}, ensure_ascii=False).encode("utf-8")
    try:
        p = lancer(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"accepte": False, "raison": f"qrexec-client : {type(e).__name__}"}
    try:
        try:  # < 8 Kio : tient dans le tampon du tube, n'attend pas
            p.stdin.write(entree)
            p.stdin.close()
        except BrokenPipeError:
            pass
        sortie = lire_borne(p, fin, horloge=horloge)
        if sortie is None:
            return {"accepte": False, "raison": "réponse de qaf.Task trop longue ou en retard"}
        try:
            code = p.wait(timeout=max(0.0, fin - horloge()))
        except subprocess.TimeoutExpired:
            return {"accepte": False, "raison": "qrexec-client ne s'est pas terminé à temps"}
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    try:
        accuse = json.loads(sortie.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        accuse = None
    if code != 0 or not isinstance(accuse, dict) or accuse.get("accepte") is not True:
        return {"accepte": False, "raison": f"qaf.Task n'a pas accepté la tâche (code {code})"}
    return {"accepte": True, "raison": ""}


def valider_hand(msg):
    if (set(msg) != {"type", "qube", "service", "requete", "delai"} or not isinstance(msg["service"], str)
            or msg["service"] not in BORNES or not isinstance(msg["requete"], dict)):
        raise ValueError("message hand inconnu")
    requete = json.dumps(msg["requete"], ensure_ascii=False).encode("utf-8")
    if len(requete) > MAX_REQUETE:
        raise ValueError("requête qaf_hand invalide ou trop grande")
    lister = msg["service"] == "qaf.Screen" and msg["requete"].get("action") == "lister"
    return (_qube(msg["qube"]), msg["service"], requete, BORNE_LISTE if lister else BORNES[msg["service"]],
            _delai(msg["delai"], DELAI_HAND))


def main_qube(qube, service, requete, borne, delai, lancer=subprocess.Popen, horloge=time.monotonic):
    """Appelle qaf_hand dans `qube` (utilisateur par défaut) ; blob borné."""
    fin = horloge() + delai
    argv = [QREXEC_CLIENT, "-d", qube, "-w", str(delai), f"DEFAULT:QUBESRPC {service} dom0"]
    echec = {"ok": False, "code": -1, "octets": 0, "b64": ""}
    try:
        p = lancer(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return echec
    try:
        try:
            p.stdin.write(requete)
            p.stdin.close()
        except BrokenPipeError:
            pass
        sortie = lire_borne(p, fin, maxi=borne, horloge=horloge)
        if sortie is None:
            return echec  # trop longue ou en retard
        code = p.wait(timeout=max(0.0, fin - horloge()))
    except subprocess.TimeoutExpired:
        return echec
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    return {"ok": code == 0, "code": code, "octets": len(sortie), "b64": base64.b64encode(sortie).decode("ascii")}


def valider_vmexec(msg):
    a = msg.get("action")
    if not isinstance(a, str) or a not in VMEXEC or set(msg) != {"type", "qube", "action", "delai", *VMEXEC[a][0]}:
        raise ValueError("message vmexec inconnu")
    if a.startswith("pkg."):
        cmds = (PKG_INSTALL if a == "pkg.install" else PKG_REMOVE).get(msg["distro"]) if isinstance(
            msg["distro"], str) else None
        if cmds is None:
            raise ValueError("distribution inconnue")
        argvs = [*cmds[:-1], cmds[-1] + schema._paquets(msg["paquets"], "paquets")]
    else:
        u = msg["utilisateur"]
        if not isinstance(u, str) or not _UTILISATEUR.match(u):
            raise ValueError("utilisateur invalide")
        fichier = f"/home/{u}/{schema._chemin(msg['chemin'], 'chemin')}"
        argvs = [["setsid", "-f", "qvm-open-in-dvm", "--view-only", "--", fichier]] if a == "file.open_dispvm" else [
            ["qvm-copy-to-vm" if a == "file.copy" else "qvm-move-to-vm", "--", _qube(msg["dest"]), fichier]]
    cmds = [commande_vmexec(argv, VMEXEC[a][1]) for argv in argvs]
    if any(len(c.encode("utf-8")) > MAX_VMEXEC for c in cmds):
        raise ValueError(f"commande qubes.VMExec de plus de {MAX_VMEXEC} octets : refusée (P-4)")
    return _qube(msg["qube"]), cmds, _delai(msg["delai"], DELAI_VMEXEC)


def commande_vmexec(argv, root):
    return f"{'root' if root else 'DEFAULT'}:QUBESRPC qubes.VMExec+{encoder_vmexec(argv)} dom0"


def vmexec(qube, commandes, delai, lancer=subprocess.Popen, horloge=time.monotonic):
    """Lance chaque commande qubes.VMExec (construite par valider_vmexec), sans
    rien lire de la sortie ; s'arrête au premier code non nul. Une seule échéance."""
    fin, codes = horloge() + delai, []
    for commande in commandes:
        reste = int(fin - horloge())
        if reste < 1:
            return {"ok": False, "codes": codes}
        cmd = [QREXEC_CLIENT, "-d", qube, "-w", str(reste), commande]
        try:
            p = lancer(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return {"ok": False, "codes": codes}
        try:
            codes.append(p.wait(timeout=max(0.0, fin - horloge())))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            return {"ok": False, "codes": codes}
        if codes[-1] != 0:
            break
    return {"ok": len(codes) == len(commandes) and not any(codes), "codes": codes}


def traiter(msg, envoi=envoyer, main=main_qube, execvm=vmexec):
    t = msg.get("type") if isinstance(msg, dict) else None
    try:
        if t == "hand":
            return main(*valider_hand(msg))
        if t == "vmexec":
            return execvm(*valider_vmexec(msg))
        jetable, tache, delai = valider(msg)
    except (ValueError, schema.PlanError) as e:
        return {"accepte": False, "ok": False, "raison": str(e)[:200]}
    return envoi(jetable, tache, delai)


def uid_pair(s):
    cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", cred)[1]


class _Gestion(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        try:
            if uid_pair(s) not in self.server.autorises:
                return  # pas l'exécuteur : connexion fermée sans réponse
            s.settimeout(10)
            msg = proto.recevoir_json(s, MAX_MESSAGE)
        except (OSError, proto.ProtoError):
            return
        rep = self.server.traiter(msg)
        try:
            s.settimeout(10)
            proto.envoyer_json(s, rep)
        except OSError:
            pass


def serveur(chemin=None, autorises=None, envoi=envoyer):
    if autorises is None:
        autorises = {pwd.getpwnam(COMPTE_EXECUTEUR).pw_uid}
    srv = proto.serveur(_Gestion, chemin, autorises=frozenset(autorises),
                        traiter=lambda msg: traiter(msg, envoi))
    srv.max_connexions = 2  # l'exécuteur traite un plan à la fois
    return srv


def main():
    serveur(SOCK_TACHES).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
