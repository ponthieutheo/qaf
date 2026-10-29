"""Panneau de l'agent : état et fil d'activité, sans GTK.

Tout vient du Doorman, en LECTURE SEULE :
- pause : os.lstat des deux fichiers de pause (qaf_gate.pause.raison), comme
  le validateur ; le contenu n'est jamais lu ;
- fil, attente de confirmation, influence : le journal chaîné de l'exécuteur
  (/var/lib/qaf-exec/journal.jsonl), lu par la fin et borné ; il faut le droit
  de lecture décrit dans docs/ECRANS-QAF.md §9 ; sans lui, le panneau le dit ;
- portier en marche : `systemctl show` (lecture, sans droit particulier) ;
- mode développement : os.lstat de la politique de l'exception de dev
  (POLITIQUE_DEV), contenu jamais lu ;
- agents jetables ouverts (nom, âge, influence) : le registre Agents du
  portier, demandé par le socket des demandes comme le fait la palette
  (palette.lire_agents_ouverts) : aucun droit nouveau.

Les gestes de l'utilisateur (pause d'urgence, reprise) passent par la commande
qaf-pause, comme à la main dans un terminal : argv fixe, jamais de shell.

Le journal contient des valeurs que l'agent a pu choisir (noms de qubes,
motifs). Elles ne sont jamais interprétées : le texte est fabriqué ici, puis
posé tel quel par l'écran (jamais de Pango). Les codes N0-N4 et R-xxxx restent
au journal (PARCOURS-1 M8) : aucune phrase ne les contient.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field

from qaf_gate import classify, pause
from qaf_gate.journal import GENESE, _hash
from qaf_gate.proto import APPELANTS, DELAI_CONFIRMATION

from .. import donnees as d
from .. import palette
from ..i18n import t

# Exception de développement (politique posée hors du produit) : un qube peut ouvrir un shell dans
# dom0. Jamais livrée par le produit ; tant qu'elle est là, le panneau le dit (os.lstat seul).
POLITIQUE_DEV = "/etc/qubes/policy.d/20-qaf-dev-dom0.policy"
JOURNAL = "/var/lib/qaf-exec/journal.jsonl"  # = qaf_gate.executor.JOURNAL (vérifié par les tests)
QUBE_AGENT = sorted(APPELANTS)[0]  # sys-agent
TERMINAL = "xfce4-terminal"  # identifiant .desktop dans tpl-agent (debian-13-xfce)
UNITES = ("qaf-gate-executor.service", "qaf-gate-executor.socket")
MAX_OCTETS = 512 * 1024  # fin du journal lue à chaque rafraîchissement
MAX_FIL = 60
# Opérations qui rendent à l'agent un contenu écrit par un qube (= executor.INFLUENCANTES,
# vérifié par les tests ; recopié pour ne pas charger l'exécuteur dans la session).
INFLUENCANTES = frozenset({"dev.list", "qube.exec", "qube.windows", "qube.screenshot", "template.available"})
NOM_RE = re.compile(r"\A[a-zA-Z][a-zA-Z0-9_.-]{0,30}\Z")
AGENT_RE = palette._AGENT_RE  # dispNNNN : jetable d'agent (= qaf_gate.schema.AGENT_RE)
# Raisons de fermeture écrites au journal par le Doorman (valeurs, jamais traduites) -> clé du texte.
FERMETURES = {"hors registre": "fil.ferme.hors_registre", "arrêt d'urgence": "fil.ferme.urgence",
              "durée maximale": "fil.ferme.duree", "inactif": "fil.ferme.inactif"}

# États affichés
PAUSE, ARRETE, ATTENTE, INFLUENCE, ACTIF = "pause", "arrete", "attente", "influence", "actif"
# Genres d'une ligne du fil
FAIT, REFUS, DEMANDE, INFO = "fait", "refus", "demande", "info"

# Phrases du fil, par opération : clés « fil.<op>.passe » (passé composé) et
# « fil.<op>.inf » (infinitif) des catalogues (qaf_ui/langues). [avec @|sans] : la
# première variante, où @ est le qube visé, s'il est connu ; sinon la seconde.
OPS_FIL = ("vm.list", "vm.info", "vm.settings", "vm.create", "vm.clone", "vm.start", "vm.shutdown", "vm.restart",
           "vm.remove", "vm.set_netvm", "vm.set_prop", "vm.reset_prop", "vm.set_service", "vm.set_template",
           "vm.tag", "vm.grow_private", "dispvm.create", "dispvm.run_task", "update.status", "update.run",
           "sys.status", "prefs.get", "prefs.set", "fw.list", "fw.add", "fw.remove", "dev.list", "dev.attach",
           "dev.detach", "pkg.install", "pkg.remove", "qube.exec", "qube.windows", "qube.screenshot", "qube.type",
           "qube.key", "qube.mouse", "qube.scroll", "template.available", "template.install", "template.remove",
           "job.status", "job.cancel", "enter.open", "enter.close", "file.copy", "file.move", "file.open_dispvm",
           "backup.profiles", "backup.run")
PHRASES = {op: (f"fil.{op}.passe", f"fil.{op}.inf") for op in OPS_FIL}
_INCONNUE = ("fil.inconnue.passe", "fil.inconnue.inf")
_VARIANTE = re.compile(r"\[([^|\]]*)\|([^\]]*)\]")


def _phrase(op, cible, forme):
    """forme 0 = passé composé, 1 = infinitif. cible : nom validé ou None."""
    modele = t(PHRASES.get(op, _INCONNUE)[forme] if isinstance(op, str) else _INCONNUE[forme])
    if isinstance(cible, str) and NOM_RE.match(cible):
        return _VARIANTE.sub(lambda m: m.group(1).replace("@", cible), modele)
    return _VARIANTE.sub(lambda m: m.group(2), modele)


def _ops(entree):
    """[(op, cible)] d'une entrée « plan » ou d'un refus du validateur.
    « cibles » (facultatif) : noms alignés sur « ops », voir ECRANS-QAF.md §9."""
    ops = entree.get("ops")
    if not isinstance(ops, list):
        return []
    cibles = entree.get("cibles")
    cibles = cibles if isinstance(cibles, list) and len(cibles) == len(ops) else [None] * len(ops)
    return list(zip(ops, cibles))[:16]


def _liste(ops, forme):
    morceaux = list(dict.fromkeys(_phrase(o, c, forme) for o, c in ops))
    if not morceaux:
        return t("fil.une_action")
    if len(morceaux) > 3:
        morceaux = morceaux[:3] + [t("fil.et_autres", n=len(morceaux) - 3)]
    return t("fil.puis").join(morceaux)


def heure(ts):
    """« 21:08 » : heures et minutes toujours sur deux chiffres (jamais « 21:8 »)."""
    if not ts:
        return "--:--"
    lt = time.localtime(ts)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


@dataclass(frozen=True)
class LigneFil:
    ts: int
    texte: str
    genre: str  # FAIT, REFUS, DEMANDE, INFO
    agent: str = None  # jetable d'agent (dispNNNN) à l'origine de la ligne, s'il est connu

    @property
    def heure(self):
        return heure(self.ts)


@dataclass
class Instantane:
    entrees: list = field(default_factory=list)  # entrées du journal, dans l'ordre
    erreur: str = None  # journal illisible : pourquoi (texte fixe)
    integre: bool = True  # chaîne vérifiée sur la partie lue
    pause: str = None  # qaf_gate.pause.raison()
    portier: bool = None  # True : socket ou service actif ; None : inconnu
    debut: float = None  # démarrage de l'exécuteur (heure murale), None si pas lancé ou inconnu
    maintenant: float = 0.0
    agents: list = None  # palette.AgentOuvert selon le Doorman ; None : portier muet (inconnu)
    permanent: bool = False  # agent permanent sys-agent (portier.json agent_long, D28)
    mode_dev: bool = False  # exception de développement posée (POLITIQUE_DEV)


@dataclass(frozen=True)
class Etat:
    code: str
    titre: str
    explication: str
    influence: bool  # None : inconnu (journal illisible)
    influence_texte: str
    attente: str = None  # ce qui attend ta réponse


# --- lecture -----------------------------------------------------------------

def lire_journal(chemin=JOURNAL, max_octets=MAX_OCTETS):
    """(entrées, erreur, intègre). Lit la fin du fichier, ignore la première
    ligne coupée, vérifie la chaîne sur ce qui est lu. Jamais d'écriture."""
    try:
        with open(chemin, "rb") as f:
            taille = os.fstat(f.fileno()).st_size
            debut = max(0, taille - max_octets)
            f.seek(debut)
            brut = f.read(max_octets)
    except FileNotFoundError:
        return [], None, True  # l'agent n'a encore rien fait
    except PermissionError:
        return [], "droit de lecture manquant", True
    except OSError:
        return [], "journal illisible", True
    lignes = brut.split(b"\n")
    if debut > 0:
        lignes = lignes[1:]  # première ligne coupée
    entrees, integre, prec = [], True, None
    for ligne in lignes:
        if not ligne.strip():
            continue
        try:
            e = json.loads(ligne.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            integre = False
            continue
        if not isinstance(e, dict) or set(e) != {"seq", "ts", "prev", "event", "hash"} \
                or not isinstance(e["event"], dict):
            integre = False
            continue
        try:
            bon = _hash(e) == e["hash"]
        except (TypeError, ValueError):
            bon = False
        if prec is None:
            lien = e["seq"] != 0 or e["prev"] == GENESE
        else:
            lien = e["prev"] == prec["hash"] and e["seq"] == prec["seq"] + 1
        integre = integre and bon and lien
        entrees.append(e)
        prec = e
    return entrees, None, integre


def lire_portier(lancer=subprocess.run, horloge=time.time, monotone=time.monotonic):
    """(portier_actif, debut). Le socket suffit : l'exécuteur démarre au
    premier plan ; un exécuteur pas encore lancé n'a pas d'influence."""
    try:
        r = lancer(["systemctl", "show", "-p", "ActiveState", "-p", "ExecMainStartTimestampMonotonic",
                    *UNITES], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if r.returncode != 0:
        return None, None
    blocs = [dict(l.split("=", 1) for l in b.splitlines() if "=" in l) for b in r.stdout.strip().split("\n\n")]
    if len(blocs) != 2:
        return None, None
    service, socket = blocs
    actif = service.get("ActiveState") == "active" or socket.get("ActiveState") == "active"
    debut = None
    if service.get("ActiveState") == "active":
        try:
            mono = int(service.get("ExecMainStartTimestampMonotonic", "0")) / 1e6
        except ValueError:
            mono = 0
        if mono > 0:
            debut = horloge() - (monotone() - mono)
    return actif, debut


def mode_dev(chemin=POLITIQUE_DEV):
    """Vrai si la politique de l'exception de développement existe. os.lstat seul, contenu
    jamais lu ; illisible pour une autre raison qu'absente : on la suppose là (prudence)."""
    try:
        os.lstat(chemin)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def instantane(chemin=JOURNAL, pauses=pause.CHEMINS, portier=lire_portier, horloge=time.time,
               agents=None, permanent=None, dev=POLITIQUE_DEV):
    entrees, erreur, integre = lire_journal(chemin)
    actif, debut = portier()
    ouverts = (agents or palette.lire_agents_ouverts)() if actif is not False else None
    return Instantane(entrees=entrees, erreur=erreur, integre=integre, pause=pause.raison(pauses),
                      portier=actif, debut=debut, maintenant=horloge(), agents=ouverts,
                      permanent=(permanent or palette.agent_permanent)(), mode_dev=mode_dev(dev))


# --- interprétation ----------------------------------------------------------

def _depuis_debut(inst):
    """Entrées écrites par l'exécuteur en cours (toutes si le début est inconnu :
    on se trompe alors du côté prudent)."""
    if inst.debut is None:
        return [] if inst.portier is True else inst.entrees
    seuil = int(inst.debut) - 1
    return [e for e in inst.entrees if isinstance(e.get("ts"), int) and e["ts"] >= seuil]


def _influence_journal(inst, seulement_long):
    return any(ev.get("type") == "op" and ev.get("ok") is True and ev.get("op") in INFLUENCANTES
               and (not seulement_long or ev.get("appelant") in APPELANTS)
               for ev in (e["event"] for e in _depuis_debut(inst)))


def influence(inst):
    """Vraie si un agent ouvert a lu du contenu de qube. Agents jetables : le
    registre du Doorman fait foi (un agent fermé n'influence plus rien) ;
    agent long (sys-agent) : le journal depuis le démarrage de l'exécuteur.
    Sans registre lisible, tout le journal compte (prudence). Agent permanent
    (D28) : influencé en permanence, comme le compte l'exécuteur."""
    if inst.permanent:
        return True
    if inst.agents is None:
        return None if inst.erreur else _influence_journal(inst, False)
    jetables = any(a.influence for a in inst.agents)
    return jetables or (not inst.erreur and _influence_journal(inst, True))


def attente(inst):
    """Phrase de ce qui attend ta confirmation, ou None."""
    if inst.erreur:
        return None
    repondus = set()
    for e in reversed(_depuis_debut(inst)):
        ev = e["event"]
        cle = (ev.get("sha256"), ev.get("request"))
        if ev.get("type") == "confirmation":
            repondus.add(cle)
        elif ev.get("type") == "plan" and ev.get("decision") in (classify.CONFIRMER, classify.CONFIRMER_RENFORCE):
            if cle in repondus:
                return None  # la dernière demande a eu sa réponse
            if isinstance(e.get("ts"), int) and inst.maintenant - e["ts"] <= DELAI_CONFIRMATION + 5:
                return _liste(_ops(ev), 1)
            return None
    return None


def texte_influence(inst, inf):
    """Une phrase, seulement s'il y a quelque chose à dire : qui est influencé et
    ce que ça change (vide quand tout est propre : rien n'est répété)."""
    if inf is None:
        return t("etat.influence.inconnue")
    if inst.permanent:
        return t("etat.influence.permanent")
    noms = [a.nom for a in inst.agents or [] if a.influence]
    if noms:
        return t("etat.influence.un" if len(noms) == 1 else "etat.influence.plusieurs", noms=", ".join(noms))
    if inf:  # agent long (sys-agent), d'après le journal
        return t("etat.influence.long")
    return ""


def etat(inst):
    """Titre et UNE phrase de conséquence (comme une confirmation, §7.6)."""
    inf = influence(inst)
    att = attente(inst) if not inst.pause else None
    if inst.pause:
        code, titre = PAUSE, t("etat.pause")
        # « automatique » : valeur écrite par le Doorman (pause.raison), jamais traduite.
        expl = t("etat.pause.auto" if "automatique" in inst.pause else "etat.pause.utilisateur")
    elif inst.portier is False:
        code, titre = ARRETE, t("etat.arrete")
        expl = t("etat.arrete.expl")
    elif att:
        code, titre = ATTENTE, t("etat.attente")
        # Une ligne de 512 px, sans mot orphelin (ECRANS-QAF §7 bis).
        expl = t("etat.attente.expl", quoi=att)
    elif inf:
        code, titre = INFLUENCE, t("etat.influence")
        expl = t("etat.influence.expl")
    elif inst.agents == [] and inst.portier is not False:
        code, titre = ACTIF, t("etat.pret")
        expl = t("etat.pret.expl")
    else:
        code, titre = ACTIF, t("etat.actif")
        expl = t("etat.actif.expl")
    return Etat(code, titre, expl, inf, texte_influence(inst, inf), att)


def ligne_agent(a):
    """« depuis 14 min · « texte » » (agent ouvert, données du Doorman) ; l'influence
    est la puce à côté."""
    debut = " ".join(a.texte.split())[:56]
    return t("etat.depuis", min=a.age // 60) + (t("etat.debut_demande", texte=debut) if debut else "")


def _agent_de(ev):
    a = ev.get("appelant") or ev.get("agent")  # « agent » : demande liée à un jetable
    return a if isinstance(a, str) and AGENT_RE.match(a) else None


def fil(inst, maximum=MAX_FIL):
    """Lignes du fil, la plus récente d'abord."""
    plans, lignes = {}, []
    for e in inst.entrees:
        ev, ts = e["event"], e.get("ts") if isinstance(e.get("ts"), int) else 0
        t_ = ev.get("type")
        n0 = len(lignes)
        if t_ == "agent_debut" and isinstance(ev.get("agent"), str) and AGENT_RE.match(ev["agent"]):
            lignes.append(LigneFil(ts, t("fil.agent_ouvert"), INFO, ev["agent"]))
        elif t_ == "agent_tue" and isinstance(ev.get("agent"), str) and AGENT_RE.match(ev["agent"]):
            pourquoi = t(FERMETURES.get(ev.get("raison"), "fil.ferme.inconnue"))
            lignes.append(LigneFil(ts, t("fil.agent_ferme", raison=pourquoi), INFO, ev["agent"]))
        elif t_ == "plan":
            plans[ev.get("sha256")] = ev
            d = ev.get("decision")
            if d == classify.REFUSER:
                lignes.append(LigneFil(ts, t("fil.refuse", quoi=_liste(_ops(ev), 1)), REFUS))
            elif d in (classify.CONFIRMER, classify.CONFIRMER_RENFORCE):
                lignes.append(LigneFil(ts, t("fil.demande", quoi=_liste(_ops(ev), 1)), DEMANDE))
        elif t_ == "confirmation":
            p = plans.get(ev.get("sha256"), {})
            quoi = _liste(_ops(p), 1)
            if ev.get("expiree"):
                lignes.append(LigneFil(ts, t("fil.sans_reponse", quoi=quoi), REFUS))
            elif ev.get("accordee") is True:
                lignes.append(LigneFil(ts, t("fil.accepte", quoi=quoi), INFO))
            else:
                lignes.append(LigneFil(ts, t("fil.refuse_par_toi", quoi=quoi), REFUS))
        elif t_ == "op":
            p = plans.get(ev.get("sha256"), {})
            ops = _ops(p)
            i = ev.get("i")
            cible = ops[i][1] if isinstance(i, int) and 0 <= i < len(ops) and ops[i][0] == ev.get("op") else None
            if ev.get("ok") is True:
                lignes.append(LigneFil(ts, _phrase(ev.get("op"), cible, 0), FAIT))
            else:
                lignes.append(LigneFil(ts, t("fil.echec", quoi=_phrase(ev.get("op"), cible, 1)), REFUS))
        elif t_ == "interruption":
            r = ev.get("raison")
            pourquoi = t("fil.interrompu.pause" if isinstance(r, str) and "pause" in r else "fil.interrompu.temps")
            lignes.append(LigneFil(ts, t("fil.interrompu", raison=pourquoi), INFO))
        elif t_ == "refus":
            lignes.append(LigneFil(ts, t("fil.refus_temps"), REFUS))
        elif t_ == "demande":
            lignes.append(LigneFil(ts, t("fil.demande_recue"), INFO))
        elif t_ == "evenements_ignores":
            lignes.append(LigneFil(ts, t("fil.trop_de_refus"), REFUS))
        elif ev.get("source") == "validateur" and isinstance(ev.get("event"), dict):
            lignes.append(LigneFil(ts, t("fil.refuse", quoi=_liste(_ops(ev["event"]), 1) if ev["event"].get("ops")
                                         else t("fil.mal_formee")), REFUS))
        # « fin », « registre_purge » : rien à dire à l'utilisateur
        agent = _agent_de(ev)
        if agent and len(lignes) > n0 and lignes[-1].agent is None:
            lignes[-1] = LigneFil(lignes[-1].ts, lignes[-1].texte, lignes[-1].genre, agent)
    return list(reversed(lignes))[:maximum]


# --- gestes de l'utilisateur ------------------------------------------------------------

Resultat = d.Resultat
PAUSE_RAISON = "pause d'urgence depuis le panneau de l'agent"


def basculer_pause(mettre, lancer=subprocess.run):
    """Pose ou lève la pause (donnees.pause_portier). Bloquant : à lancer dans un fil."""
    return d.pause_portier(mettre, PAUSE_RAISON if mettre else "", lancer)
