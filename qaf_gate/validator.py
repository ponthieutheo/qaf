"""Validateur qaf-gate : démon sous le compte dédié qaf-gate (C4).

Seul processus qui parse les octets venus de sys-agent. Sans accès X, sans
sudo, sans qubesd (confinement : packaging/qaf-gate-validator.service). Il
reçoit du talon (appelant, statut de lecture, octets), applique les limites
(C5 : appelants autorisés, pause, un plan à la fois par appelant ; plus de
plafond de débit ni de Pause automatique depuis le 28/10), parse, classe pour refuser tôt, puis transmet
le plan ré-sérialisé à l'exécuteur et renvoie sa réponse.
"""

import hashlib
import socketserver
import sys
import threading
import time
from dataclasses import dataclass

from . import classify, pause, proto, schema

LECTURES = {"trop_grand": "entrée trop grande", "delai": "délai de lecture dépassé"}


@dataclass(frozen=True)
class Config:
    appelants: frozenset = proto.APPELANTS
    pauses: tuple = pause.CHEMINS


class Validateur:
    def __init__(self, executeur, config=Config(), horloge=time.monotonic):
        self.executeur = executeur  # appeler(message) -> réponse (proto.client)
        self.config = config
        self.horloge = horloge
        self._verrou = threading.Lock()
        self._en_cours = set()

    def _refus(self, appelant, raison, plan=None, **extra):
        raison = raison[:200]  # = executor.MAX_RAISON
        ev = {"type": "refus", "appelant": appelant[:64], "raison": raison}
        if plan is not None:  # J1 : refus N4 tracé avec demande, opérations et empreinte
            ev.update(request=plan.request, ops=[o.op for o in plan.ops],
                      sha256=hashlib.sha256(schema.canonicalize(plan)).hexdigest())
        try:
            self.executeur({"type": "evenement", "event": ev})
        except (OSError, proto.ProtoError):
            pass  # l'exécuteur absent ne doit pas bloquer le refus
        return proto.reponse(proto.REFUSE, raison, **extra)

    def traiter(self, appelant: str, lecture: str, donnees: bytes) -> dict:
        # Filtre seulement (AGENT-JETABLE §5.3) : un nom de jetable sans nom
        # passe ; l'exécuteur seul juge (registre Agents, UUID, étiquette).
        if appelant not in self.config.appelants and not schema.AGENT_RE.match(appelant):
            return self._refus(appelant, "appelant non autorisé")
        r = pause.raison(self.config.pauses)
        if r:
            return self._refus(appelant, f"cœur du système en pause : {r}")
        with self._verrou:
            if appelant in self._en_cours:
                return self._refus(appelant, "un plan est déjà en cours pour cet appelant")
            self._en_cours.add(appelant)
        try:
            return self._valider(appelant, lecture, donnees)
        finally:
            with self._verrou:
                self._en_cours.discard(appelant)

    def _valider(self, appelant, lecture, donnees):
        if lecture != "ok":
            return self._refus(appelant, LECTURES.get(lecture, "lecture invalide"))
        try:
            plan = schema.parse_plan(donnees)
        except schema.PlanError as e:
            return self._refus(appelant, f"plan refusé : {e}")
        try:
            # Profils de sauvegarde du plan : lus par qaf-gate-outils pour l'exécuteur (V13).
            ctx = self.executeur({"type": "contexte", "profils": [o.get("profile") for o in plan.ops
                                                                  if o.op == "backup.run"]})
            # Même contexte que l'exécuteur (qubes, préférences, configuration
            # dom0) : le classement d'ici ne sert qu'à refuser tôt, celui de
            # l'exécuteur seul fait foi.
            contexte = classify.Contexte(ctx["qubes"], ctx["prefs"], classify.Config.de_json(ctx["config"]),
                                         frozenset(ctx["baux"]), ctx["profils"],
                                         None if ctx["tags_politique"] is None else frozenset(ctx["tags_politique"]),
                                         frozenset(ctx.get("agents", ())))
            cl = classify.classer(plan, agent_qubes=set(ctx["agent"]), existing=set(ctx["existants"]),
                                  influence=ctx["influence"], mode=ctx["mode"], ctx=contexte)
            if cl.decision == classify.REFUSER:
                return self._refus(appelant, classify.motif_n4(plan, contexte, set(ctx["agent"])), plan=plan,
                                   request=plan.request)
            canon = schema.canonicalize(plan).decode("utf-8")
            return self.executeur({"type": "plan", "appelant": appelant, "plan": canon})
        except (OSError, proto.ProtoError, KeyError, TypeError, ValueError, AttributeError):
            return proto.reponse(proto.ERREUR, "exécuteur indisponible")


class _Gestion(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        s.settimeout(15)
        try:
            appelant = proto.recevoir(s, 64).decode("ascii")
            lecture = proto.recevoir(s, 16).decode("ascii")
            donnees = proto.recevoir(s, schema.MAX_BYTES)
        except (OSError, ValueError):  # ProtoError et UnicodeDecodeError compris
            return
        s.settimeout(None)
        rep = self.server.validateur.traiter(appelant, lecture, donnees)
        try:
            proto.envoyer_json(s, rep)
        except OSError:
            pass


def serveur(validateur, chemin=None):
    return proto.serveur(_Gestion, chemin, validateur=validateur)


def main():
    v = Validateur(proto.client(proto.SOCK_EXECUTEUR, delai=proto.DELAI_VALIDATEUR))
    serveur(v, proto.SOCK_VALIDATEUR).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
