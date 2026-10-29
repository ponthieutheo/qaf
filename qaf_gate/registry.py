"""Registres du Doorman, écrits par l'exécuteur seul (JSON réécrit de façon
atomique : fichier temporaire + fsync + rename)."""

# - Registry : seule source de vérité de « créé par l'agent » (C1). Chaque entrée
#   garde l'UUID du qube : si l'utilisateur supprime puis recrée un qube du même nom, le
#   nouveau n'est PAS un qube de l'agent. Son champ « request » est l'identifiant
#   porté par le plan qui l'a créé.
# - Demandes : demandes tapées par l'utilisateur dans la palette (C13). L'identifiant
#   R-xxxxxx est tiré ici, jamais choisi par l'agent ni par l'interface ; la
#   portée (qubes nommés dans le texte) est calculée ici aussi. Chaque demande
#   est liée par dom0 à UN agent (jetable lancé pour elle, ou l'agent long) ;
#   seule la DERNIÈRE demande de cet agent est valide (P1 par agent,
#   AGENT-JETABLE §7, n3) : il ne peut pas citer une ancienne demande plus
#   large, ni celle d'un autre agent.
# - Agents : jetables d'agent lancés par l'exécuteur (AGENT-JETABLE §6).
#   PARCOURS-1 M3 : la demande expire 10 min après le dernier plan qui la
#   cite (session finie ou abandonnée), et au plus tard au bout de
#   DUREE_DEMANDE. La première session de l'agent qui la cite (identifiant tiré
#   par qaf_mcp, donc choisi par sys-agent) est notée pour l'AFFICHAGE seulement
#   (« vient d'une autre session ») : elle ne décide jamais ni de la validité,
#   ni de la portée, ni de l'influence (revue du 25/10, PLAN-v2 C13 bis).

import json
import os
import re
import secrets
import tempfile
import time
import unicodedata

from .proto import AGENT_LONG
from .schema import TASK_INTERDITS

VERSION = 1
DUREE_DEMANDE = 2 * 3600  # au-delà, la demande est expirée (secondes)
INACTIVITE = 10 * 60  # sans plan qui la cite depuis 10 min : expirée
MAX_TEXTE = 2000
_MOT = re.compile(r"[A-Za-z0-9_.-]+")  # caractères possibles d'un nom de qube


def ecrire_atomique(path, obj):
    dossier = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".registre-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, sort_keys=True, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if hasattr(os, "O_DIRECTORY"):  # rend le rename durable (POSIX)
        dfd = os.open(dossier, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _charger(path, cle):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != VERSION or not isinstance(data.get(cle), dict):
        raise ValueError("registre illisible ou de version inconnue")
    return data[cle]


class Registry:
    def __init__(self, path):
        self.path = path
        self.qubes = _charger(path, "qubes")  # nom -> {"uuid", "request", "created"}

    def _save(self):
        ecrire_atomique(self.path, {"version": VERSION, "qubes": self.qubes})

    def add(self, name, uuid, request, agent=None):
        # agent : {"nom", "uuid"} du jetable créateur (affichage, journal, R-9).
        self.qubes[name] = {"uuid": uuid, "request": request, "created": int(time.time()),
                            **({"agent": agent} if agent else {})}
        self._save()

    def remove(self, name):
        if self.qubes.pop(name, None) is not None:
            self._save()

    def purger(self, infos):
        """Retire les qubes qui n'existent plus (jetables supprimés par Qubes à
        l'arrêt) ; renvoie leurs noms. Un qube recréé sous le même nom reste
        exclu par l'UUID, purgé ou non."""
        vus = {q.name for q in infos}
        partis = sorted(n for n in self.qubes if n not in vus)
        for n in partis:
            del self.qubes[n]
        if partis:
            self._save()
        return partis

    def is_agent(self, name, uuid):
        e = self.qubes.get(name)
        return e is not None and bool(uuid) and e["uuid"] == uuid

    def agent_qubes(self, infos):
        """Noms des qubes existants (QubeInfo) qui appartiennent à l'agent."""
        return {q.name for q in infos if self.is_agent(q.name, q.uuid)}


def noms_cites(texte, existants):
    # Qubes existants nommés dans le texte : mots entiers, casse ignorée,
    # ponctuation finale ôtée (« démarre work. » nomme work, « network » non).
    mots = {m.lower() for mot in _MOT.findall(texte) for m in (mot, mot.rstrip("._-"))}
    return {n for n in existants if n.lower() in mots}


class Demandes:
    def __init__(self, path, horloge=time.time):
        self.path, self.horloge = path, horloge
        self.demandes = _charger(path, "demandes")  # id -> {"texte", "ts", "qubes"}

    @staticmethod
    def verifier(texte):
        # ValueError si le texte est vide, trop long ou contient un caractère de contrôle.
        if not isinstance(texte, str) or not texte.strip() or len(texte) > MAX_TEXTE:
            raise ValueError(f"demande vide ou trop longue ({MAX_TEXTE} caractères au plus)")
        if any(c != "\n" and unicodedata.category(c) in TASK_INTERDITS for c in texte):
            raise ValueError("caractère de contrôle interdit dans la demande")

    def _save(self):
        ecrire_atomique(self.path, {"version": VERSION, "demandes": self.demandes})

    def ajouter(self, texte, existants, agent=AGENT_LONG):
        # Renvoie (identifiant, entrée) ; ValueError comme verifier().
        self.verifier(texte)
        rid = None
        while rid is None or rid in self.demandes:  # jamais l'identifiant d'une autre
            rid = f"R-{secrets.randbelow(10**6):06d}"
        e = {"texte": texte, "ts": int(self.horloge()), "qubes": sorted(noms_cites(texte, existants)), "agent": agent}
        # P1 par agent : sa demande précédente ne vaut plus rien ; celles des
        # autres agents restent (n3). Une entrée sans agent (ancien format) part.
        self.demandes = {k: v for k, v in self.demandes.items() if v.get("agent") not in (None, agent)}
        self.demandes[rid] = e
        self._save()
        return rid, e

    def retirer_agent(self, agent):
        """Demandes d'un agent disparu : purgées avec lui (§7)."""
        avant = len(self.demandes)
        self.demandes = {k: v for k, v in self.demandes.items() if v.get("agent") != agent}
        if len(self.demandes) != avant:
            self._save()

    def vivante(self, rid, agent=AGENT_LONG):
        """Entrée si la demande est la dernière de cet agent et n'a pas expiré,
        sans rien noter ; sinon None (une entrée sans agent ne vaut rien)."""
        e = self.demandes.get(rid)
        if e is None or e.get("agent") != agent or not 0 <= self.age(e) < DUREE_DEMANDE or (
                int(self.horloge()) - e.get("vu", e["ts"]) >= INACTIVITE):
            return None
        return e

    def valide(self, rid, session=None, agent=AGENT_LONG):
        # Comme vivante(), en notant la dernière activité et la première
        # session qui la cite (affichage seulement, jamais une décision).
        e = self.vivante(rid, agent)
        if e is None:
            return None
        e["vu"] = int(self.horloge())
        if session and not e.get("session"):
            e["session"] = session
        self._save()
        return e

    def age(self, e):
        # Secondes écoulées depuis la demande (affichées dans la fenêtre).
        return int(self.horloge()) - e["ts"]


class Agents:
    """Jetables d'agent lancés par l'exécuteur : nom -> {"uuid", "request",
    "debut", "dernier_plan", "influence", "cli"}. L'influence n'est écrite
    fausse qu'à la création d'une entrée (R-7) ; relue au démarrage de
    l'exécuteur, elle repart à vrai (§8 : l'inconnu compte comme influencé)."""

    def __init__(self, path, horloge=time.time):
        self.path, self.horloge = path, horloge
        self.agents = _charger(path, "agents")
        for e in self.agents.values():
            e["influence"] = True

    def _save(self):
        ecrire_atomique(self.path, {"version": VERSION, "agents": self.agents})

    def add(self, nom, uuid, cli):
        if nom in self.agents:  # jamais réattribuée à un autre UUID (R-7)
            raise ValueError(f"agent {nom} déjà inscrit")
        t = int(self.horloge())
        self.agents[nom] = {"uuid": uuid, "request": None, "debut": t, "dernier_plan": t, "influence": False,
                            "cli": cli}
        self._save()

    def maj(self, nom, **champs):
        """request, dernier_plan, ou influence=True : elle ne retombe jamais."""
        if not set(champs) <= {"request", "dernier_plan", "influence"} or champs.get("influence", True) is not True:
            raise ValueError("mise à jour d'agent interdite")
        self.agents[nom].update(champs)
        self._save()

    def remove(self, nom):
        if self.agents.pop(nom, None) is not None:
            self._save()
