"""Demande de confirmation à l'utilisateur.

L'exécuteur ne montre que le plan CANONIQUE ré-sérialisé (C4) : jamais les
octets envoyés par sys-agent. Implémentations :
- Fenetre : défaut du démon, la fenêtre qaf_ui (session graphique de l'utilisateur,
  processus séparé joint par un socket Unix). Absente, muette ou illisible :
  refus. Validation au clavier interne seulement (C11, voir qaf_ui) ;
- ToujoursRefuser : défaut d'Executeur hors démon ;
- Console : terminal (tests manuels) ;
- Scripte : réponses écrites d'avance (tests).
"""

import hmac
import os
import socket
import struct
import sys
from dataclasses import dataclass

from . import classify, proto
from .schema import Plan

# Dossier posé par tmpfiles (packaging/tmpfiles-qaf-ui.conf) : propriétaire =
# l'utilisateur de la session, groupe qaf-exec, mode 2750.
SOCK_UI = "/run/qaf-ui/confirm.sock"


@dataclass(frozen=True)
class Demande:
    appelant: str
    plan: Plan
    canonique: bytes  # seul contenu affiché
    niveaux: tuple
    decision: str  # classify.CONFIRMER ou classify.CONFIRMER_RENFORCE
    jeton: str  # tiré au hasard dans dom0 : à retaper pour une confirmation renforcée
    influence: bool = True  # état de la session tenu par dom0, jamais dit par l'agent
    qubes: tuple = ()  # (nom, label, rôle) des qubes existants nommés dans le plan
    texte_demande: str | None = None  # texte du registre des demandes, None = inconnue ou expirée
    hors_portee: tuple = ()  # indices des opérations qui ne découlent pas de la demande (C13)
    non_nommes: tuple = ()  # qubes de ces opérations que la demande ne nomme pas
    autre_session: bool = False  # affichage seulement : citée d'abord par une autre session de l'agent
    age_demande: int | None = None  # secondes depuis la demande (registre de dom0)
    sauvegardes: tuple = ()  # (profil, destination_vm, destination_path) de chaque backup.run (U2)


def qubes_du_plan(plan, infos, agent, coffres, ctx=None):
    """Contexte de la fenêtre : label et rôle de chaque qube existant nommé
    dans le plan (coffre ; agent ou utilisateur, suivi de -modele pour un modèle ou
    un modèle de jetables, de -systeme pour la classe S). Tout vient de
    qubesd, du registre et de la configuration dom0."""
    noms = {q for o in plan.ops for q in o.qubes()} | {s[1] for s in sauvegardes_du_plan(plan, ctx)}

    def role(q):
        if q.name in coffres:
            return "coffre"
        genre = ("-systeme" if ctx is not None and ctx.classe(q.name, agent) == classify.S else
                 "-modele" if q.klass == "TemplateVM" or q.template_for_dispvms else "")
        return ("agent" if q.name in agent else "utilisateur") + genre
    return tuple(sorted((q.name, q.label, role(q)) for q in infos if q.name in noms))


def sauvegardes_du_plan(plan, ctx):
    """(profil, destination_vm, destination_path) de chaque backup.run, lus par
    qaf-gate-outils (U2 : la fenêtre nomme la destination et le chemin)."""
    profils = ctx.profils if ctx is not None else {}
    return tuple((p, profils[p]["destination_vm"], profils[p].get("destination_path"))
                 for p in dict.fromkeys(o.get("profile") for o in plan.ops if o.op == "backup.run") if p in profils)


def resume(d: Demande) -> str:
    # Demande de l'utilisateur (texte du registre de dom0) et actions côte à côte (C13),
    # sans code (N0-N4, R-xxxx) : ils restent au journal (PARCOURS-1 M8).
    if d.texte_demande is None:
        lignes = ["Tu n'as rien demandé dans la palette pour cette action.", "L'agent veut :"]
    else:
        lignes = [f"Ta demande (il y a {(d.age_demande or 0) // 60} min) : « {d.texte_demande} »", "L'agent veut :"]
    for i, op in enumerate(d.plan.ops):
        args = " ".join(f"{k}={v!r}" for k, v in op.args)
        alerte = "   ⚠ ne découle pas de ta demande" if i in d.hors_portee and d.texte_demande else ""
        lignes.append(f"  {i + 1}. {op.op} {args}".rstrip() + alerte)
    return "\n".join(lignes)


class Confirmateur:
    def demander(self, demande: Demande) -> bool:
        raise NotImplementedError


class ToujoursRefuser(Confirmateur):
    def demander(self, demande):
        return False


class Console(Confirmateur):
    """Confirmation normale : taper « oui ». Renforcée : retaper le jeton tiré
    dans dom0 (jamais un identifiant choisi par l'agent)."""

    def __init__(self, entree=None, sortie=None):
        self.entree = entree or sys.stdin
        self.sortie = sortie or sys.stdout

    def demander(self, demande):
        renforce = demande.decision == classify.CONFIRMER_RENFORCE
        attendu = demande.jeton if renforce else "oui"
        print(resume(demande), file=self.sortie)
        print(f"Pour autoriser, tape « {attendu} » : ", end="", file=self.sortie, flush=True)
        return self.entree.readline().strip() == attendu


class Scripte(Confirmateur):
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.demandes = []

    def demander(self, demande):
        self.demandes.append(demande)
        return self.reponses.pop(0) if self.reponses else False


class Fenetre(Confirmateur):
    """Client de la fenêtre qaf_ui. Envoie le plan CANONIQUE et le contexte tenu
    par dom0 (la fenêtre rédige elle-même la phrase). N'accepte que
    {"accorde": true} venu du propriétaire du dossier du socket et revérifie
    ici le jeton retapé d'une confirmation renforcée. Tout écart = refus."""

    def __init__(self, chemin=SOCK_UI, delai=proto.DELAI_CONFIRMATION):
        self.chemin, self.delai = chemin, delai

    def demander(self, d):
        """True ou False ; None si la fenêtre s'est fermée seule à l'échéance
        (motif « expire » : n4, rapporté comme expiré, jamais comme un refus)."""
        msg = {"type": "confirmer", "appelant": d.appelant, "canonique": d.canonique.decode("utf-8"),
               "niveaux": list(d.niveaux), "decision": d.decision, "jeton": d.jeton,
               "influence": bool(d.influence), "qubes": [list(q) for q in d.qubes],
               # F1 : demande de l'utilisateur (registre de dom0) et opérations hors portée.
               "texte_demande": d.texte_demande, "age_demande": d.age_demande,
               "hors_portee": list(d.hors_portee), "non_nommes": list(d.non_nommes),
               "autre_session": bool(d.autre_session), "sauvegardes": [list(s) for s in d.sauvegardes],
               "expire_dans": max(1, int(self.delai) - 2)}  # fermée avant l'expiration de l'exécuteur
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.delai + 5)
                s.connect(self.chemin)
                cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                if struct.unpack("3i", cred)[1] != os.stat(os.path.dirname(self.chemin)).st_uid:
                    return False  # ce n'est pas l'utilisateur de la session qui écoute
                proto.envoyer_json(s, msg)
                rep = proto.recevoir_json(s, 4096)
        except (OSError, ValueError):  # ProtoError hérite de ValueError
            return False
        if rep.get("accorde") is not True:
            return None if rep.get("motif") == "expire" else False
        tape = rep.get("jeton")
        return d.decision != classify.CONFIRMER_RENFORCE or (
            isinstance(tape, str) and hmac.compare_digest(tape.encode(), d.jeton.encode()))
