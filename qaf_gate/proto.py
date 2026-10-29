"""Protocole du Doorman : contrat de réponse de qaf.Plan, délais, et trames
préfixées par leur longueur sur des sockets Unix (talon -> validateur ->
exécuteur), avec les serveurs associés.

Trame = 4 octets big-endian (longueur) + contenu. Toute longueur au-delà du
maximum annoncé par le lecteur est refusée avant lecture (ProtoError).
Le talon (stub.py) a sa propre copie de envoyer/recevoir et de DELAI_TALON :
il ne doit rien importer de qaf_gate (les tests vérifient les copies).
"""

import json
import os
import socket
import socketserver
import struct
import threading

from . import schema

SOCK_VALIDATEUR = "/run/qaf-gate/validator.sock"  # créé par qaf-gate-validator.socket
SOCK_EXECUTEUR = "/run/qaf-exec/executor.sock"  # créé par qaf-gate-executor.socket
# Palette (utilisateur de bureau de dom0, groupe qubes) -> exécuteur : enregistre
# une demande de l'utilisateur. Socket distinct : le validateur ne peut pas y créer de demande.
SOCK_DEMANDES = "/run/qaf-exec/demande.sock"  # créé par qaf-gate-demande.socket
MAX_MESSAGE = 1024 * 1024  # messages internes et réponses renvoyées à sys-agent

# Agent long (AGENT-JETABLE §11) : seul appelant nommé, admis par l'exécuteur
# seulement si portier.json dit « agent_long » ; les jetables d'agent
# (schema.AGENT_RE) sont admis par le registre Agents de l'exécuteur.
AGENT_LONG = "sys-agent"
APPELANTS = frozenset({AGENT_LONG})

# Contrat de réponse de qaf.Plan, le seul, importé par l'exécuteur, le validateur
# et qaf_mcp : un objet JSON {"statut": un de STATUTS, ...} avec, selon le cas,
# "raison", "request", "resultats", "non_executees".
FAIT = "fait"  # toutes les opérations ont réussi
REFUSE = "refuse"  # rien n'a été exécuté (schéma, N4, pause, non confirmé…)
ERREUR = "erreur"  # arrêt à la première erreur, ou portier indisponible
INTERROMPU = "interrompu"  # arrêt entre deux opérations (pause, délai dépassé)
EXPIRE = "expire"  # confirmation sans réponse dans le délai : rien n'est exécuté
STATUTS = (FAIT, REFUSE, ERREUR, INTERROMPU, EXPIRE)


def reponse(statut, raison=None, **extra):
    if statut not in STATUTS:
        raise ValueError(f"statut hors contrat : {statut!r}")
    return {"statut": statut, **({} if raison is None else {"raison": raison}), **extra}


# Durée maximale supposée de chaque opération (secondes). L'exécuteur ne lance
# une opération que si elle peut finir avant son échéance. Hypothèse sur
# qubesd, pas une garantie : à mesurer sur une vraie machine.
DUREE_MAX_OP = {"vm.list": 10, "vm.info": 10, "vm.create": 120, "vm.clone": 600, "vm.start": 120, "vm.shutdown": 30,
                "vm.remove": 60, "vm.set_netvm": 30, "dispvm.run_task": 120,
                "vm.settings": 10, "update.status": 30, "sys.status": 30, "prefs.get": 10, "fw.list": 10,
                "dev.list": 60, "vm.set_prop": 30, "vm.reset_prop": 30, "vm.set_service": 10, "vm.restart": 120,
                "vm.set_template": 30, "pkg.install": 600, "dispvm.create": 120, "qube.exec": 130,
                "qube.windows": 30, "qube.screenshot": 30, "qube.type": 40, "qube.key": 20, "qube.mouse": 20,
                "qube.scroll": 20, "prefs.set": 10, "fw.add": 10, "fw.remove": 10,
                # Lot 2 et 3. Travaux longs : seul le lancement compte (§3.5). Copie :
                # la fenêtre native de Qubes attend l'utilisateur dans le qube source (D17).
                "update.run": 30, "template.available": 90, "template.install": 20, "template.remove": 20,
                "job.status": 10, "job.cancel": 10, "enter.open": 30, "enter.close": 5, "file.copy": 300,
                "file.move": 300, "file.open_dispvm": 60, "dev.attach": 30, "dev.detach": 30,
                "backup.profiles": 10, "backup.run": 20, "pkg.remove": 600, "vm.tag": 10, "vm.grow_private": 30}

# Délais en secondes, strictement croissants de l'intérieur vers l'agent :
# personne n'abandonne tant qu'en aval quelque chose peut encore agir.
DELAI_CONFIRMATION = 120  # sans réponse de l'utilisateur : refus « expirée », jamais exécuté ensuite
# Depuis la réception du plan : confirmation + le pire plan (une opération
# lourde, schema.LOURDES, et MAX_OPS - 1 des autres les plus longues). Plan qui
# ne tient plus dans le reste : refus « délai insuffisant ».
DELAI_EXECUTEUR = (DELAI_CONFIRMATION + max(DUREE_MAX_OP[o] for o in schema.LOURDES)
                   + (schema.MAX_OPS - 1) * max(d for o, d in DUREE_MAX_OP.items() if o not in schema.LOURDES))  # 2 520 s
DELAI_VALIDATEUR = DELAI_EXECUTEUR + 30  # attente de l'exécuteur
DELAI_TALON = DELAI_VALIDATEUR + 30  # = stub.DELAI_REPONSE
DELAI_AGENT = DELAI_TALON + 30  # qaf_mcp : attente de qrexec-client-vm


class ProtoError(ValueError):
    pass


def envoyer(s, data: bytes):
    s.sendall(struct.pack(">I", len(data)) + data)


def _exact(s, n):
    morceaux = []
    while n:
        bloc = s.recv(min(n, 65536))
        if not bloc:
            raise ProtoError("connexion fermée en cours de trame")
        morceaux.append(bloc)
        n -= len(bloc)
    return b"".join(morceaux)


def recevoir(s, maxi: int) -> bytes:
    (n,) = struct.unpack(">I", _exact(s, 4))
    if n > maxi:
        raise ProtoError(f"trame trop longue ({n} > {maxi})")
    return _exact(s, n)


def envoyer_json(s, obj: dict):
    envoyer(s, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def recevoir_json(s, maxi: int = MAX_MESSAGE) -> dict:
    try:
        obj = json.loads(recevoir(s, maxi).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ProtoError(f"message illisible : {type(e).__name__}") from None
    if not isinstance(obj, dict):
        raise ProtoError("objet JSON attendu")
    return obj


def client(chemin: str, delai: float):
    """Renvoie appeler(message) -> réponse, une connexion par message."""

    def appeler(message: dict) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(delai)
            s.connect(chemin)
            envoyer_json(s, message)
            return recevoir_json(s)

    return appeler


class Serveur(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Un thread par connexion, au plus max_connexions à la fois (TasksMax=32
    dans les unités) : au-delà, la connexion est fermée tout de suite."""

    daemon_threads = True
    max_connexions = 8
    _places = None

    def process_request(self, request, client_address):
        if self._places is None:  # boucle d'acceptation : un seul thread ici
            self._places = threading.BoundedSemaphore(self.max_connexions)
        if not self._places.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._places.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._places.release()


def _fd_systemd(nom):
    # Descripteur passé par systemd : celui nommé `nom` (FileDescriptorName),
    # ou le seul s'il n'y en a qu'un (et sans nom quand `nom` est donné).
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    n, noms = int(os.environ.get("LISTEN_FDS") or 0), os.environ.get("LISTEN_FDNAMES", "").split(":")
    if nom is not None and nom in noms[:n]:
        return 3 + noms.index(nom)
    return 3 if n == 1 and (nom is None or noms == [""]) else None


def serveur(gestionnaire, chemin=None, nom=None, **attributs):
    """Serveur Unix : socket passé par systemd (activation, fd 3 et suivants)
    s'il existe, sinon bind sur `chemin` (tests). `attributs` : sur le serveur."""
    fd = _fd_systemd(nom)
    if fd is not None:
        sock = socket.socket(fileno=fd)
        srv = Serveur(chemin or "", gestionnaire, bind_and_activate=False)
        srv.socket.close()
        srv.socket = sock
    else:
        if chemin is None:
            raise ValueError("ni socket systemd ni chemin")
        if os.path.exists(chemin):
            os.unlink(chemin)
        srv = Serveur(chemin, gestionnaire)
    for k, v in attributs.items():
        setattr(srv, k, v)
    return srv
