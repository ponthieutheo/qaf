"""Exécuteur qaf-gate : le SEUL processus qui appelle le Backend (qubesd).

Il ne reçoit que des messages du validateur, sur un socket réservé au compte
qaf-gate. Il ne fait pas confiance au validateur (processus qui a parsé du
contenu non fiable) : il exige un plan déjà canonique (octet pour octet) et
refait lui-même le classement, qui seul fait foi. Il écrit le journal et le
registre, demande les confirmations, s'arrête à la première erreur et entre
deux opérations si le Doorman passe en pause.

Messages (JSON) :
  {"type": "contexte"}                        -> état utile au classement
  {"type": "evenement", "event": {"type": "refus", "appelant": a, "raison": r}}
                                              -> refus noté au journal (débit plafonné)
  {"type": "plan", "appelant": n, "plan": s}  -> exécution
Socket de la palette (AGENT-JETABLE §1, §8) :
  {"type": "demande", "texte": t}             -> agent neuf dans un jetable,
                                                 identifiant (C13) et nom du jetable
  {"type": "demande", "texte": t, "agent": a} -> nouvelle demande pour un agent ouvert
  {"type": "agents"}                          -> agents ouverts (âge, influence)
  "cli" facultatif dans une demande (schema.CLIS).
"""

# C13 : le registre des demandes (registry.Demandes) est tenu ici, pas par
# l'interface, pour que l'exécuteur reste seul juge : il tire l'identifiant,
# calcule la portée (qubes nommés dans le texte, d'après sa propre liste) et la
# confronte au plan. La palette ne fait que transmettre le texte, sur un socket
# distinct de celui du validateur (qui ne peut donc pas créer de demande).
# Une demande inconnue ou expirée est classée comme une session influencée,
# avec une portée vide ; la fenêtre, elle, dit seulement « tu n'as rien
# demandé » (l'influence affichée est la vraie : PARCOURS-1 M2). La demande est
# liée à la première session de l'agent qui la cite (registry.Demandes).
# Sans registre (demandes=None, anciens tests) : ni portée ni identifiant
# vérifié ; main() en passe toujours un.
#
# Agent jetable (docs/AGENT-JETABLE.md) : chaque demande de la palette lance un
# agent neuf dans un jetable de qaf-agent-dvm, créé, inscrit (registre Agents,
# avant l'étiquette : R-5), étiqueté qaf-agent puis démarré ici, hors du verrou
# des plans. Un jetable n'est admis comme appelant qu'au registre, avec le même
# UUID, la classe DispVM, ce modèle, l'étiquette et sans default_dispvm (§5.3) ;
# sa demande est celle que dom0 lui a liée (§7). Son influence est tenue par
# jetable, fausse à la création seulement (R-7), vraie aussi si son pare-feu
# n'est plus le pare-feu fermé attendu (R-1). Sans registre Agents (agents=None,
# anciens tests) : la demande va à l'agent long, comme avant.
# Agent permanent (AGENT-JETABLE D28, défaut depuis le 27/10) : si portier.json
# dit agent_long, une demande neuve va à sys-agent (démarré par la palette, dans
# dom0) au lieu d'un jetable ; elle est liée à sys-agent dans le registre des
# demandes, et seul l'appelant qrexec sys-agent peut la citer (Demandes.valide).
#
# Influence de l'agent long (§3.3) : fausse au départ (vraie d'office si
# une configuration lue active agent_long : AGENT-JETABLE §11) ; vraie dès qu'une
# opération rend à l'agent un contenu écrit par un qube (INFLUENCANTES). Rien
# de ce que dit sys-agent ne la réduit (revue du 25/10 : l'identifiant de
# session vient de l'agent). Elle est tenue deux fois :
# - par demande (R-xxxxxx, tiré par dom0) : une demande qui a lu du contenu de
#   qube reste influencée, quelle que soit la session ;
# - drapeau commun : vrai après toute lecture de contenu de qube.
# Ni l'un ni l'autre ne retombe tant que l'exécuteur tourne (revues du 25/10,
# PLAN-v2 C13 bis) : le temps n'efface pas le contexte d'un claude, et un
# redémarrage de sys-agent n'efface pas ~/.claude (instructions, mémoire,
# historique), qu'une session influencée a pu écrire.

import collections
import dataclasses
import hashlib
import json
import os
import re
import secrets
import socketserver
import stat
import sys
import threading
import time

from . import classify, confirm, pause, proto, schema
from .backend import AUTRE, BackendError

# Ce qu'écrit un qube passe par des types fermés avant d'aller à l'agent (V9).
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?\Z")
_TAG = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")
_ETAT_SERVICE = {"1": "on", "": "off", None: "default"}
MAX_JETABLES = 4  # jetables de l'agent en même temps (dispvm.create)
MAX_BAUX = 3  # baux d'entrée ouverts en même temps (§3.6)
_ETATS_TRAVAIL = ("en_cours", "fini", "annule")
# Politiques lues par qrexec, parcourues récursivement (include/ compris) ; ancien format compris.
POLITIQUES = ("/etc/qubes/policy.d", "/run/qubes/policy.d", "/etc/qubes-rpc/policy")
MAX_POLITIQUE = 1 << 20
_TAG_POLITIQUE = re.compile(r"@tag:([A-Za-z0-9_.-]+)")
# Opérations qui renvoient du contenu écrit par un qube : la session devient influencée (§3.3).
# Toutes les autres ne renvoient que du qubesd, des types fermés (V9) ou un code
# de sortie entier (qaf.Input, pkg.install) : vérifié par tests/test_revue_vague1.py.
INFLUENCANTES = frozenset({"dev.list", "qube.exec", "qube.windows", "qube.screenshot", "template.available"})
# Réponse à l'agent : sous proto.MAX_MESSAGE, que le validateur et le talon
# appliquent aussi ; au-delà, les plus gros résultats sont remplacés par leur
# taille et leur empreinte (le compte rendu op, ok, code reste entier).
MAX_REPONSE = proto.MAX_MESSAGE - 4096
_EMPREINTE = re.compile(r"\A[0-9a-f]{64}\Z")

MAX_EVENEMENTS_MINUTE = 60  # au-delà, les refus du validateur ne sont plus journalisés
MAX_RAISON = 200
REGISTRE = "/var/lib/qaf-exec/registre.json"
DEMANDES = "/var/lib/qaf-exec/demandes.json"
AGENTS = "/var/lib/qaf-exec/agents.json"
ENTRETIEN = 15  # secondes entre deux passages de entretenir() (§8, R-5)
JOURNAL = "/var/lib/qaf-exec/journal.jsonl"


def refus(raison, **extra):
    return proto.reponse(proto.REFUSE, raison, **extra)


class Executeur:
    def __init__(self, backend, registre, journal, confirmateur=None, pauses=pause.CHEMINS,
                 pause_auto=pause.PAUSE_AUTO, delai=proto.DELAI_EXECUTEUR,
                 delai_confirmation=proto.DELAI_CONFIRMATION, horloge=time.monotonic,
                 appelants=proto.APPELANTS, coffres=classify.COFFRES_DEFAUT, demandes=None,
                 jetables=classify.JETABLES_DEFAUT, config=None, agents=None, suspension=pause.SUSPENSION):
        self.backend = backend
        self.registre = registre
        self.journal = journal
        self.demandes = demandes
        self.agents = agents  # registry.Agents, jetables d'agent ; None = anciens tests (agent long seul)
        self.confirmateur = confirmateur or confirm.ToujoursRefuser()
        self.pauses, self.pause_auto, self.suspension = pauses, pause_auto, suspension
        # Revérifiés : le validateur n'est pas de confiance. Configuration lue :
        # l'agent long n'est appelant que si portier.json le dit (§11).
        self.appelants = frozenset(config.appelants if config else appelants)
        # Configuration dom0 de l'utilisateur (classify.CONFIG) : coffres, jetables admis
        # (H1), système, admin_qubes, ne_jamais_demarrer ; transmise au validateur.
        self.config = config or classify.Config(coffres=frozenset(coffres), jetables=frozenset(jetables),
                                                appelants=self.appelants)
        self.delai, self.delai_confirmation, self.horloge = delai, delai_confirmation, horloge
        # Mode : TODO, fixé par l'UI de confiance (mode Pilote).
        self.etat = {"influence": bool(config and config.agent_long), "mode": classify.NORMAL}  # §11, D28
        self.demandes_influencees = set()  # R-xxxxxx qui ont lu du contenu de qube
        self._plan = threading.Lock()  # un plan à la fois, tous appelants confondus
        self._lancements = threading.Lock()  # créations de jetables d'agent, une à la fois, hors de _plan
        self._ecriture = threading.Lock()  # journal et registre
        self._evenements = collections.deque()  # dates des refus journalisés (fenêtre de 60 s)
        self._evenements_ignores = False
        # §3.6 : baux d'entrée, en mémoire seulement (un redémarrage les coupe).
        self.baux = {}  # qube -> {"uuid", "fin", "request"}

    def noter(self, event):
        with self._ecriture:
            self.journal.append(event)

    def traiter(self, msg: dict) -> dict:
        try:
            t = msg.get("type")
            if t == "contexte":
                infos, ctx = self._contexte(msg.get("profils") or [])
                return {
                    "existants": sorted(q.name for q in infos),
                    "agent": sorted(self.registre.agent_qubes(infos)),
                    "coffres": sorted(self.config.coffres),
                    "jetables": sorted(self.config.jetables),
                    "qubes": ctx.qubes, "prefs": ctx.prefs, "config": self.config.en_json(),
                    "baux": sorted(ctx.baux), "profils": ctx.profils,
                    "tags_politique": None if ctx.tags_politique is None else sorted(ctx.tags_politique),
                    "agents": sorted(ctx.agents), **self.etat,
                }
            if t == "evenement":
                return self._evenement(msg.get("event"))
            if t == "plan":
                fin = self.horloge() + self.delai  # tout compte : attente du verrou, confirmation
                if not self._plan.acquire(timeout=self.delai):
                    return refus("cœur du système occupé par une autre action")
                try:
                    return self._traiter_plan(msg.get("appelant"), msg.get("plan"), fin)
                finally:
                    self._plan.release()
            return proto.reponse(proto.ERREUR, "message inconnu")
        except BackendError as e:
            return proto.reponse(proto.ERREUR, e.code)

    def _contexte(self, profils=()):
        infos = self.backend.list_qubes()
        prefs = {k: v for k, v in self.backend.prefs().items() if v is None or isinstance(v, (str, bool, int))}
        lus = {}  # profils de sauvegarde nommés par le plan, lus par qaf-gate-outils (jamais ici)
        for p in profils[:schema.MAX_OPS] if isinstance(profils, list) else ():
            try:
                lus[p] = self.backend.backup_info(schema._profil(p, "profil"))
            except (BackendError, schema.PlanError):
                pass  # profil illisible : backup.run sera refusé (N4)
        return infos, classify.Contexte({q.name: dataclasses.asdict(q) for q in infos}, prefs, self.config,
                                        frozenset(self._baux(infos)), lus, _tags_politique(),
                                        frozenset(self.agents.agents if self.agents is not None else ()))

    def _baux(self, infos):
        """Baux encore valides : coupés par l'échéance, la Pause et un changement d'UUID."""
        uuids = {q.name: q.uuid for q in infos}
        with self._ecriture:
            if pause.raison(self.pauses):
                self.baux.clear()
            for n in [n for n, b in self.baux.items() if b["fin"] <= self.horloge() or uuids.get(n) != b["uuid"]]:
                del self.baux[n]
            return set(self.baux)

    def traiter_demande(self, msg: dict) -> dict:
        # Message du socket de la palette : seul chemin qui crée une demande et
        # qui lance un agent. Schéma fermé (tests/test_fuzz_schema.py).
        if self.demandes is None or not isinstance(msg, dict):
            return proto.reponse(proto.ERREUR, "message inconnu")
        try:
            if msg == {"type": "agents"} and self.agents is not None:
                return self._liste_agents()
            libres = {"agent", "cli"} if self.agents is not None else set()
            if not {"type", "texte"} <= set(msg) <= {"type", "texte"} | libres or msg["type"] != "demande":
                return proto.reponse(proto.ERREUR, "message inconnu")
            ouvert, cli = msg.get("agent"), msg.get("cli", schema.CLIS[0])
            if not isinstance(cli, str) or cli not in schema.CLIS or (
                    "agent" in msg and not (isinstance(ouvert, str) and schema.AGENT_RE.match(ouvert))):
                return refus("agent ou CLI inconnu")
            self.demandes.verifier(msg["texte"])
            infos = self.backend.list_qubes()
            if self.agents is None or (ouvert is None and self.config.agent_long):
                agent = proto.AGENT_LONG  # agent permanent (D28) : jamais de jetable
            elif ouvert is None:
                agent = self._lancer_agent(infos, cli)
            elif self._identite(ouvert, infos) is None:  # « Continuer avec cet agent » (§8)
                return refus("cet agent n'est plus ouvert : tape une nouvelle demande")
            else:
                agent = ouvert
            with self._ecriture:
                rid, e = self.demandes.ajouter(msg["texte"], {q.name for q in infos}, agent=agent)
                if agent in (self.agents.agents if self.agents is not None else ()):
                    self.agents.maj(agent, request=rid)  # liée par dom0 ; l'influence est gardée (R-7)
        except ValueError as err:
            return refus(str(err))
        except BackendError as err:
            return proto.reponse(proto.ERREUR, err.code)
        empreinte = hashlib.sha256(e["texte"].encode("utf-8")).hexdigest()
        self.noter({"type": "demande", "request": rid, "qubes": e["qubes"], "sha256_texte": empreinte,
                    "agent": agent})
        return proto.reponse(proto.FAIT, request=rid, qubes=e["qubes"], agent=agent)

    def _lancer_agent(self, infos, cli):
        """§1 : jetable créé sur qaf-agent-dvm, inscrit AVANT l'étiquette et le
        démarrage (R-5), étiqueté puis démarré ; tout échec le tue. Refusé si
        le modèle est aussi un jetable ordinaire ou un jetable par défaut
        (R-3), s'il porte une étiquette qui passerait au jetable (S-J2), ou
        au-delà d'agents_max."""
        modele, (_, ctx) = schema.MODELE_AGENT, self._contexte()
        if modele in self.config.jetables or modele in (ctx.prefs.get("default_dispvm"), *(
                q.default_dispvm for q in infos)) or classify.etiquette_heritee(modele, ctx):
            raise ValueError(f"{modele} est mal réglé (jetable par défaut, liste des jetables ou étiquette) : "
                             "qaf-installer --verifier")
        with self._lancements:
            self._purger_agents(self.backend.list_qubes())
            if len(self.agents.agents) >= self.config.agents_max:
                raise ValueError(f"déjà {self.config.agents_max} agents ouverts : ferme-en un d'abord")
            q = self.backend.creer_agent(modele)
            try:
                if not schema.AGENT_RE.match(q.name):
                    raise BackendError(AUTRE, "nom de jetable inattendu")
                with self._ecriture:
                    self.agents.add(q.name, q.uuid, cli)
                self.backend.tag(q.name, schema.TAG_AGENT, "add")
                self.backend.start(q.name)
            except BaseException:
                self.backend.tuer(q.name)
                with self._ecriture:
                    if self.agents.agents.get(q.name, {}).get("uuid") == q.uuid:
                        self.agents.remove(q.name)
                raise
        self.noter({"type": "agent_debut", "agent": q.name, "uuid": q.uuid, "cli": cli})
        return q.name

    def _liste_agents(self):
        """Agents ouverts pour la palette (« Continuer avec cet agent »)."""
        self._purger_agents(self.backend.list_qubes())
        maintenant = int(self.agents.horloge())
        with self._ecriture:
            return proto.reponse(proto.FAIT, agents=[
                {"agent": n, "age": maintenant - e["debut"], "influence": e["influence"], "cli": e["cli"],
                 "texte": self.demandes.demandes.get(e["request"], {}).get("texte", "")[:80]}
                for n, e in sorted(self.agents.agents.items())])

    def _purger_agents(self, infos):
        """§8 : jetable disparu (fin de tâche, kill) ou nom repris par un
        autre UUID (S-J4) : entrée et demande retirées, agent_fin journalisé."""
        if self.agents is None:
            return
        uuids = {q.name: q.uuid for q in infos}
        with self._ecriture:
            for n in [n for n, e in self.agents.agents.items() if uuids.get(n) != e["uuid"]]:
                self.agents.remove(n)
                self.demandes.retirer_agent(n)
                self.journal.append({"type": "agent_fin", "agent": n})

    def _identite(self, nom, infos):
        """§5.3 : entrée du registre Agents si qubesd confirme, maintenant, même
        UUID, DispVM d'un modèle d'agent, étiquette qaf-agent, sans
        default_dispvm effectif ; sinon None (« agent inconnu »)."""
        e = self.agents.agents.get(nom) if self.agents is not None else None
        q = next((q for q in infos if q.name == nom), None)
        if (e is None or q is None or q.uuid != e["uuid"] or q.klass != "DispVM" or q.template not in schema.MODELES_AGENT
                or schema.TAG_AGENT not in q.tags or q.default_dispvm):
            return None
        return e

    def _pare_feu_ouvert(self, nom):
        """R-1 : le pare-feu du jetable diffère du pare-feu fermé attendu
        (illisible compris) ; il faut alors le compter influencé."""
        try:
            return self.backend.fw_list(nom) != regles_agent(self.config.agent_hotes)
        except BackendError:
            return True

    def entretenir(self):
        """§8 et R-5, au démarrage puis toutes les ENTRETIEN secondes : jetables
        disparus purgés ; jetable d'un modèle d'agent hors registre (hors
        préchargés en attente), au-delà d'agent_duree_max, inactif depuis
        agent_inactivite sans demande vivante, ou arrêt d'urgence (qaf-pause) :
        tué. La Pause automatique, elle, garde les jetables."""
        if self.agents is None:
            return
        infos = self.backend.list_qubes()
        self._purger_agents(infos)
        maintenant, urgence = int(self.agents.horloge()), pause.raison(self.pauses[:1])
        for q in infos:
            if q.klass != "DispVM" or q.template not in schema.MODELES_AGENT:
                continue
            e = self.agents.agents.get(q.name)
            if e is None or e["uuid"] != q.uuid:
                raison = None if self.backend.precharge(q.name) else "hors registre"
            elif urgence:
                raison = "arrêt d'urgence"
            elif maintenant - e["debut"] >= 60 * self.config.agent_duree_max:
                raison = "durée maximale"
            elif maintenant - e["dernier_plan"] >= 60 * self.config.agent_inactivite and (
                    self.demandes.vivante(e["request"], q.name) is None):
                raison = "inactif"
            else:
                continue
            if raison:
                self.backend.tuer(q.name)
                self.noter({"type": "agent_tue", "agent": q.name, "raison": raison})

    def _evenement(self, ev):
        """Refus signalé par le validateur : schéma fermé, longueurs bornées,
        contenu imbriqué (jamais fusionné avec les champs de l'exécuteur),
        débit plafonné pour ne pas remplir le disque de dom0."""
        if not (isinstance(ev, dict) and {"type", "appelant", "raison"} <= set(ev) <= _CLES_EVENEMENT
                and ev["type"] == "refus" and isinstance(ev["appelant"], str) and len(ev["appelant"]) <= 64
                and isinstance(ev["raison"], str) and len(ev["raison"]) <= MAX_RAISON and _plan_refuse_ok(ev)):
            return proto.reponse(proto.ERREUR, "événement invalide")
        with self._ecriture:
            maintenant, file = self.horloge(), self._evenements
            while file and file[0] <= maintenant - 60:
                file.popleft()
            if len(file) >= MAX_EVENEMENTS_MINUTE:
                if not self._evenements_ignores:
                    self.journal.append({"source": "executeur", "type": "evenements_ignores",
                                         "raison": "débit des refus du validateur dépassé"})
                self._evenements_ignores = True
                return proto.reponse(proto.ERREUR, "débit d'événements dépassé")
            file.append(maintenant)
            self._evenements_ignores = False
            self.journal.append({"source": "validateur", "event": dict(ev)})
        return proto.reponse(proto.FAIT)

    def _purger(self, infos):
        """J3 : retire du registre les qubes que Qubes a supprimés (jetables
        effacés à l'arrêt). Un nom recréé par l'utilisateur n'y revient jamais (UUID)."""
        with self._ecriture:
            partis = self.registre.purger(infos)
            if partis:
                self.journal.append({"type": "registre_purge", "qubes": partis})

    def _confirmer(self, d):
        """True/False selon l'utilisateur, None si le délai est dépassé : la réponse
        tardive est ignorée, le plan n'est jamais exécuté après expiration."""
        reponse, debut = [], self.horloge()
        t = threading.Thread(target=lambda: reponse.append(self.confirmateur.demander(d)), daemon=True)
        t.start()
        t.join(self.delai_confirmation)
        if t.is_alive() or self.horloge() - debut > self.delai_confirmation or (reponse and reponse[0] is None):
            return None  # n4 : fenêtre fermée seule à l'échéance (confirm.Fenetre)
        return bool(reponse and reponse[0])  # exception du confirmateur = refus

    def _traiter_plan(self, appelant, texte, fin):
        if not isinstance(appelant, str) or not isinstance(texte, str):
            return refus("message de plan mal formé")
        jetable = appelant not in self.appelants
        if jetable and (self.agents is None or not schema.AGENT_RE.match(appelant)):
            return refus("appelant non autorisé")
        r = pause.raison(self.pauses)
        if r:
            return refus(f"cœur du système en pause : {r}")
        donnees = texte.encode("utf-8")
        try:
            plan = schema.parse_plan(donnees)
        except schema.PlanError as e:
            return refus(f"plan refusé par l'exécuteur : {e}")
        if schema.canonicalize(plan) != donnees:
            return refus("plan non canonique")
        empreinte = hashlib.sha256(donnees).hexdigest()
        infos, ctx = self._contexte([o.get("profile") for o in plan.ops if o.op == "backup.run"])
        self._purger(infos)
        base = {"appelant": appelant, "request": plan.request, "sha256": empreinte}
        ag = None
        if jetable:  # §5.3 et §7 : identité revérifiée, demande liée par dom0
            self._purger_agents(infos)
            ag = self._identite(appelant, infos)
            raison = ("agent inconnu" if ag is None else
                      "demande d'un autre agent, ou remplacée par une nouvelle demande" if plan.request != ag["request"]
                      else None)
            if raison:
                self.noter({"type": "refus", **base, "raison": raison})
                return refus(raison, request=plan.request)
            with self._ecriture:
                self.agents.maj(appelant, dernier_plan=int(self.agents.horloge()))
            if not ag["influence"] and self._pare_feu_ouvert(appelant):  # R-1
                with self._ecriture:
                    self.agents.maj(appelant, influence=True)
                self.noter({"type": "pare_feu_ecart", "agent": appelant})
        agent = self.registre.agent_qubes(infos)
        moi = ag["uuid"] if ag else None  # R-9 : qubes A créés par un autre jetable
        etrangers = {n for n in agent if (self.registre.qubes[n].get("agent") or {}).get("uuid") not in (None, moi)}
        etat, portee, dem = dict(self.etat), None, None
        influence = (ag["influence"] if ag else self.etat["influence"]) or plan.request in self.demandes_influencees
        etat["influence"] = influence
        if self.demandes is not None:  # C13 : la demande existe-t-elle, que couvre-t-elle ?
            with self._ecriture:  # dernière activité ; session notée pour l'affichage seulement
                dem = self.demandes.valide(plan.request, plan.session, agent=appelant)
            etat["influence"] = influence or dem is None
            portee = set(dem["qubes"]) if dem else set()
            if dem:  # qubes créés pendant cette demande (registre du Doorman, UUID vérifié), puis n1
                portee |= {n for n in agent if self.registre.qubes[n].get("request") == plan.request}
                portee |= classify.modeles_agent(portee, ctx, agent)
        cl = classify.classer(plan, agent_qubes=agent, existing={q.name for q in infos},
                              portee=portee, ctx=ctx, etrangers=etrangers, **etat)
        self.noter({"type": "plan", **base, "ops": [o.op for o in plan.ops], "niveaux": list(cl.niveaux),
                    "cibles": [o.target or o.get("template") for o in plan.ops],  # noms validés (ECRANS-QAF §9.2)
                    "decision": cl.decision,
                    "demande_connue": dem is not None, "hors_portee": list(cl.hors_portee)})
        if cl.decision == classify.REFUSER:
            return refus(classify.motif_n4(plan, ctx, agent), request=plan.request)
        if cl.decision in (classify.CONFIRMER, classify.CONFIRMER_RENFORCE) and pause.suspendue(self.suspension):
            self.noter({"type": "confirmation", **base, "accordee": True, "expiree": False, "suspension": True})
        elif cl.decision in (classify.CONFIRMER, classify.CONFIRMER_RENFORCE):
            jeton = f"{secrets.randbelow(10**6):06d}"  # tiré dans dom0, jamais choisi par l'agent
            # La fenêtre reçoit la vraie influence (PARCOURS-1 M2) ; l'absence de
            # demande y a sa propre ligne (texte_demande None).
            d = confirm.Demande(appelant, plan, donnees, cl.niveaux, cl.decision, jeton,
                                influence=influence,
                                qubes=confirm.qubes_du_plan(plan, infos, agent, self.config.coffres, ctx),
                                texte_demande=dem["texte"] if dem else None, hors_portee=cl.hors_portee,
                                non_nommes=cl.non_nommes,
                                autre_session=bool(not jetable and dem and dem.get("session") not in (
                                    None, plan.session)),
                                age_demande=self.demandes.age(dem) if dem else None,
                                sauvegardes=confirm.sauvegardes_du_plan(plan, ctx))
            ok = self._confirmer(d)
            self.noter({"type": "confirmation", **base, "accordee": bool(ok), "expiree": ok is None})
            if ok is None:  # n4 : jamais rapporté comme un refus de l'utilisateur
                return proto.reponse(proto.EXPIRE, "confirmation expirée : personne n'a répondu à la fenêtre "
                                     f"({int(self.delai_confirmation)} s), rien n'a été fait", request=plan.request)
            if not ok:
                return refus("non confirmé par l'utilisateur", request=plan.request)
        # Ne rien commencer qui ne pourrait pas finir avant l'échéance : en amont,
        # personne n'attendrait plus le résultat (proto.DUREE_MAX_OP).
        if self.horloge() + sum(proto.DUREE_MAX_OP[o.op] for o in plan.ops) >= fin:
            self.noter({"type": "refus", **base, "raison": "délai insuffisant"})
            return refus("délai insuffisant : rien n'a été exécuté", request=plan.request)
        return self._executer(plan, base, fin)

    def _executer(self, plan, base, fin):
        resultats, statut = [], proto.FAIT
        a = base["appelant"]
        jetable = self.agents is not None and a in self.agents.agents and a not in self.appelants
        auteur = {"nom": a, "uuid": self.agents.agents[a]["uuid"]} if jetable else None  # registre, R-9
        for i, op in enumerate(plan.ops):
            r = pause.raison(self.pauses) or (
                self.horloge() + proto.DUREE_MAX_OP[op.op] >= fin and "délai insuffisant")
            if r:
                statut = proto.INTERROMPU
                self.noter({"type": "interruption", **base, "op": i, "raison": r})
                break
            try:
                res = {"op": op.op, "ok": True, "resultat": self._op(op, plan.request, auteur)}
            except BackendError as e:
                res = {"op": op.op, "ok": False, "code": e.code, "message": e.message}
            except Exception as e:  # erreur imprévue : on s'arrête aussi
                res = {"op": op.op, "ok": False, "code": AUTRE, "message": type(e).__name__}
            self.noter({"type": "op", **base, "i": i, "op": op.op, "ok": res["ok"], "code": res.get("code"),
                        **_pour_journal(res.get("resultat"))})
            if res["ok"] and op.op in INFLUENCANTES:  # jamais remise à faux (C7, PLAN-v2 C13 bis, R-7)
                if jetable:  # influence de CE jetable seulement (§6)
                    with self._ecriture:
                        self.agents.maj(a, influence=True)
                else:
                    self.etat["influence"] = True
                self.demandes_influencees.add(plan.request)
            resultats.append(res)
            if not res["ok"]:
                statut = proto.ERREUR  # arrêt à la première erreur (cf. classify.classer)
                break
        rep = _borner(proto.reponse(statut, request=plan.request, resultats=resultats,
                                    non_executees=len(plan.ops) - len(resultats)))
        self.noter({"type": "fin", **base, "statut": statut, **({"reponse_tronquee": True}
                                                                 if rep.get("reponse_tronquee") else {})})
        return rep

    def _op(self, op, request, auteur=None):
        b, nom, a = self.backend, op.target, dict(op.args)
        o = op.op
        if o == "vm.list":
            return [dataclasses.asdict(q) for q in b.list_qubes()]
        if o == "vm.info":
            return dataclasses.asdict(b.info(nom))
        if o == "vm.create":
            q = b.create_appvm(nom, a["template"], a["label"])
            with self._ecriture:
                self.registre.add(nom, q.uuid, request, auteur)
            return dataclasses.asdict(q)
        if o == "vm.clone":
            q = b.clone_template(a["source"], nom, a["label"])
            with self._ecriture:
                self.registre.add(nom, q.uuid, request, auteur)
            return dataclasses.asdict(q)
        if o == "vm.remove":
            b.remove(nom)
            with self._ecriture:
                self.registre.remove(nom)
            return None
        if o == "dispvm.run_task":
            return {"dispvm": b.run_dispvm_task(a.get("template"), a["task"])}
        if o == "dispvm.create":
            infos = b.list_qubes()
            agent = self.registre.agent_qubes(infos)
            if sum(q.klass == "DispVM" and q.name in agent for q in infos) >= MAX_JETABLES:
                raise BackendError(AUTRE, f"déjà {MAX_JETABLES} jetables de l'agent : ferme-en un d'abord")
            q = b.create_dispvm(a.get("template"))
            with self._ecriture:
                self.registre.add(q.name, q.uuid, request, auteur)
            return {"dispvm": q.name}
        if o in MAINS:
            service, requete = MAINS[o](a)
            r = b.hand(nom, service, requete, proto.DUREE_MAX_OP[o] - 5)
            # qaf.Input : statut seul (§3.4) ; sa sortie, écrite par le qube, ne
            # part pas vers l'agent (ces opérations ne rendent pas influencé).
            c = r.get("code")
            return {"code": c if type(c) is int else None} if o not in INFLUENCANTES else r
        if o == "vm.settings":
            r = b.settings(nom)
            return {**r, "services": {k: _ETAT_SERVICE.get(v, "default") for k, v in r["services"].items()},
                    "tags": [t for t in r["tags"] if isinstance(t, str) and _TAG.match(t)]}
        if o == "update.status":
            return [_maj(e) for e in b.update_status(a.get("targets") or [])]
        if o in ("enter.open", "enter.close", "job.status") or o in LANCEURS:
            return self._op_v3(op, request)
        if o == "dev.list":
            return [d for d in map(_dev, b.dev_list(a.get("class"))) if d]
        appels = {
            "vm.start": lambda: b.start(nom), "vm.shutdown": lambda: b.shutdown(nom),
            "vm.restart": lambda: b.restart(nom), "vm.set_netvm": lambda: b.set_netvm(nom, a["netvm"]),
            "sys.status": b.sys_status, "prefs.get": b.prefs, "fw.list": lambda: b.fw_list(nom),
            "vm.set_prop": lambda: b.set_prop(nom, a["prop"], a["value"]),
            "vm.reset_prop": lambda: b.reset_prop(nom, a["prop"]),
            "vm.set_service": lambda: b.set_service(nom, a["service"], a["state"]),
            "vm.set_template": lambda: b.set_template(nom, a["template"]),
            "pkg.install": lambda: b.pkg_install(nom, a["packages"]),
            "prefs.set": lambda: b.set_pref(a["prop"], a["value"]),
            "fw.add": lambda: b.fw_add(nom, a["position"], a["rule"]),
            "fw.remove": lambda: b.fw_remove(nom, a["position"], a["rule"]),
            "template.available": lambda: b.template_available(a["repo"]),
            "job.cancel": lambda: b.job_cancel(a["job"]), "backup.profiles": b.backup_profiles,
            "file.copy": lambda: b.file_copy(a["source"], a["path"], a["dest"], False),
            "file.move": lambda: b.file_copy(a["source"], a["path"], a["dest"], True),
            "file.open_dispvm": lambda: b.file_open_dispvm(nom, a["path"]),
            "dev.attach": lambda: b.dev_attach(a["class"], a["backend"], a["port"], a["device_id"], a["target"],
                                               a.get("read_only", True)),
            "dev.detach": lambda: b.dev_detach(a["class"], a["backend"], a["port"], a["target"]),
            "pkg.remove": lambda: b.pkg_remove(nom, a["packages"]),
            "vm.tag": lambda: b.tag(nom, a["tag"], a["action"]),
            "vm.grow_private": lambda: b.grow_private(nom, a["size_mib"]),
        }
        if o in appels:
            return appels[o]()
        raise BackendError(AUTRE, f"opération sans exécution : {op.op}")


    def _op_v3(self, op, request):
        """Bail d'entrée (§3.6) et travaux longs de qaf-gate-outils (§3.5)."""
        b, nom, a, o = self.backend, op.target, dict(op.args), op.op
        if o == "enter.open":
            q = b.info(nom)
            if len(set(self.baux) - {nom}) >= MAX_BAUX:
                raise BackendError(AUTRE, f"déjà {MAX_BAUX} baux ouverts : ferme-en un d'abord")
            # qaf_hand doit répondre ; sa sortie n'est pas lue (rien ne part vers l'agent).
            b.hand(nom, "qaf.Screen", {"action": "lister", "masquees": False}, proto.DUREE_MAX_OP[o] - 5)
            minutes = min(a["minutes"], classify.MINUTES_COFFRE) if nom in self.config.coffres else a["minutes"]
            with self._ecriture:
                self.baux[nom] = {"uuid": q.uuid, "fin": self.horloge() + 60 * minutes, "request": request}
            return {"minutes": minutes}
        if o == "enter.close":
            with self._ecriture:
                return {"ferme": self.baux.pop(nom, None) is not None}
        if o == "job.status":
            r = b.job_status(a["job"])
            etat = r.get("etat") if r.get("etat") in _ETATS_TRAVAIL else None
            return {"travail": a["job"], "etat": etat,
                    **{k: r.get(k) if type(r.get(k)) is int else None for k in ("code", "duree")}}
        return LANCEURS[o](b, a)


# Opérations qui lancent un travail long dans qaf-gate-outils : {"travail": "T-xxxxxx", …}.
# Pas template.install : qvm-template importe par sudo, que qaf-outils n'a pas (N4).
LANCEURS = {
    "update.run": lambda b, a: b.update_run(a["targets"], a["apply"]),
    "template.remove": lambda b, a: b.template_remove(a["template"]),
    "backup.run": lambda b, a: b.backup_run(a["profile"]),
}


def regles_agent(hotes):
    """Pare-feu fermé de qaf-agent-dvm, recopié dans chaque jetable (R-1, §3) :
    DNS, puis TCP 443 vers les seuls hôtes de portier.json, puis drop. Même
    liste que tools/setup-agent-dvm.sh (vérifié par les tests)."""
    return ([{"action": "accept", "dns": True}]
            + [{"action": "accept", "host": h, "proto": "tcp", "ports": "443"} for h in hotes] + [{"action": "drop"}])


def _tags_politique():
    """Tags cités en @tag: dans les politiques qrexec (vm.tag : N4), lecture
    seule et récursive. Fail-closed : un dossier ou un fichier illisible (ou
    trop gros, ou trop profond) rend None = inconnu, et vm.tag passe en N4.
    Un emplacement absent (l'ancien /etc/qubes-rpc/policy) n'est pas une erreur."""
    tags = set()

    def lire(chemin, profondeur):
        try:
            st = os.stat(chemin)
        except FileNotFoundError:
            return  # absent, ou lien cassé : qrexec ne le lit pas non plus
        if stat.S_ISDIR(st.st_mode):
            if profondeur > 8:
                raise OSError("politiques trop profondes")
            for n in sorted(os.listdir(chemin)):
                lire(os.path.join(chemin, n), profondeur + 1)
        elif stat.S_ISREG(st.st_mode):
            with open(chemin, encoding="utf-8", errors="replace") as fh:
                texte = fh.read(MAX_POLITIQUE + 1)
            if len(texte) > MAX_POLITIQUE:
                raise OSError("politique trop grosse")
            tags.update(_TAG_POLITIQUE.findall(texte))
    try:
        for d in POLITIQUES:
            lire(d, 0)
    except OSError:
        return None
    return frozenset(tags)


def _fenetre(a):
    return {"fenetre": a["window"]} if a.get("window", "racine") != "racine" else {}


def _souris(a):
    if a["button"] == 0:
        return {"action": "deplacer", "x": a["x"], "y": a["y"], **_fenetre(a)}
    return {"action": "cliquer", "x": a["x"], "y": a["y"], "bouton": a["button"], **_fenetre(a)}


# Opérations d'entrée -> (service qaf_hand, requête construite dans dom0 à partir
# des champs validés ; seul l'argv de qube.exec part tel quel, vers le qube).
MAINS = {
    "qube.exec": lambda a: ("qaf.Exec", {"argv": a["argv"], "delai": a["timeout"]}),
    "qube.windows": lambda a: ("qaf.Screen", {"action": "lister", "masquees": a["hidden"]}),
    "qube.screenshot": lambda a: ("qaf.Screen", {"action": "capturer", "fenetre": a["window"]}),
    "qube.type": lambda a: ("qaf.Input", {"action": "taper", "texte": a["text"], **_fenetre(a)}),
    "qube.key": lambda a: ("qaf.Input", {"action": "touche", "touches": a["keys"], **_fenetre(a)}),
    "qube.mouse": lambda a: ("qaf.Input", _souris(a)),
    "qube.scroll": lambda a: ("qaf.Input", {"action": "defiler", "sens": a["direction"], "crans": a["steps"]}),
}


def _booleen(v):
    return {"1": True, "": False, True: True, False: False}.get(v) if isinstance(v, (str, bool)) else None


def _maj(e):
    """Fonctions écrites par le qube lui-même (qubes.NotifyUpdates) : types fermés."""
    def date(k):
        return e.get(k) if isinstance(e.get(k), str) and _DATE.match(e.get(k)) else None
    return {"name": e.get("name"), "updates_available": _booleen(e.get("updates_available")),
            "last_update": date("last_update"), "last_updates_check": date("last_updates_check"),
            "skip_update": _booleen(e.get("skip_update")), "prohibit_start": _booleen(e.get("prohibit_start"))}


def _dev(e):
    """Entrée annoncée par un backend : écartée si non conforme (V9)."""
    try:
        ok = (e["class"] in ("usb", "block", "mic") and schema._name(e["backend"], "backend")
              and schema._port_dev(e["port"], "port") and schema._id_dev(e["device_id"], "device_id")
              and schema._name_or_none(e.get("attached_to"), "attached_to") == e.get("attached_to"))
    except (KeyError, TypeError, schema.PlanError):
        return None
    return {k: e.get(k) for k in ("class", "backend", "port", "device_id", "attached_to")} if ok else None


_CLES_EVENEMENT = {"type", "appelant", "raison", "request", "ops", "sha256"}


def _plan_refuse_ok(ev):
    """J1 : champs facultatifs d'un refus N4 du validateur, tous fermés."""
    ops, req, emp = ev.get("ops", []), ev.get("request", "R-0000"), ev.get("sha256", "0" * 64)
    return (isinstance(req, str) and bool(schema._REQUEST_RE.match(req)) and isinstance(emp, str)
            and bool(_EMPREINTE.match(emp)) and isinstance(ops, list) and len(ops) <= schema.MAX_OPS
            and all(isinstance(o, str) and o in schema.OPS for o in ops))


def _taille(obj):
    return len(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _borner(rep):
    """Réponse sous MAX_REPONSE : du plus gros au plus petit, un résultat est
    remplacé par {"octets", "sha256"} ; l'opération a bien été exécutée."""
    if _taille(rep) <= MAX_REPONSE:
        return rep
    res = rep["resultats"]
    for i in sorted((i for i, r in enumerate(res) if "resultat" in r), key=lambda i: -_taille(res[i]["resultat"])):
        brut = json.dumps(res[i].get("resultat"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        res[i] = {**res[i], "resultat": None, "tronque": {"octets": len(brut),
                                                           "sha256": hashlib.sha256(brut).hexdigest()}}
        if _taille(rep) <= MAX_REPONSE:
            break
    rep["reponse_tronquee"] = True
    return rep


def _pour_journal(r):
    """Valeur avant l'écriture (§3.7) ; d'un blob venu d'un qube, seulement
    sa longueur et son empreinte (§3.4)."""
    if not isinstance(r, dict):
        return {}
    if "b64" in r:
        return {"octets": r.get("octets"), "sha256": hashlib.sha256(str(r["b64"]).encode()).hexdigest()}
    if "travail" in r:  # lancement et fin d'un travail long (§3.5)
        return {k: r[k] for k in ("travail", "etat", "code", "duree") if k in r}
    return {"avant": r["avant"]} if "avant" in r else {}


class _Gestion(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            msg = proto.recevoir_json(self.request)
        except (OSError, proto.ProtoError):
            return
        self.request.settimeout(None)  # le plan peut attendre une confirmation
        rep = self.server.traiter(msg)  # selon le socket : plans ou demandes
        try:
            proto.envoyer_json(self.request, rep)
        except OSError:
            pass


def serveur(executeur, chemin=None, nom=None):
    return proto.serveur(_Gestion, chemin, nom, executeur=executeur, traiter=executeur.traiter)


def serveur_demandes(executeur, chemin=None, nom=None):
    return proto.serveur(_Gestion, chemin, nom, executeur=executeur, traiter=executeur.traiter_demande)


def main():
    from .backend_qubes import QubesBackend
    from .journal import Journal
    from .registry import Agents, Demandes, Registry

    # Fenêtre qaf_ui dans la session de l'utilisateur ; absente = refus (fail-closed).
    config = classify.lire_config()  # illisible : le Doorman ne démarre pas (fail-closed)
    ex = Executeur(QubesBackend(modeles_admis=config.jetables), Registry(REGISTRE), Journal(JOURNAL),
                   confirm.Fenetre(), demandes=Demandes(DEMANDES), config=config, agents=Agents(AGENTS))

    def entretien():
        while True:
            try:
                ex.entretenir()
            except Exception as e:  # qubesd indisponible : on réessaie au tour suivant
                ex.noter({"type": "entretien_erreur", "erreur": type(e).__name__})
            time.sleep(ENTRETIEN)
    threading.Thread(target=entretien, daemon=True).start()
    palette = serveur_demandes(ex, proto.SOCK_DEMANDES, "demande")  # noms : FileDescriptorName
    threading.Thread(target=palette.serve_forever, daemon=True).start()
    serveur(ex, proto.SOCK_EXECUTEUR, "plan").serve_forever()


if __name__ == "__main__":
    sys.exit(main())
