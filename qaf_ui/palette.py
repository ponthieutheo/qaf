"""Palette (dom0) : logique, sans GTK. Se lance par la commande qaf-palette
(bouton « Demander à l'agent… » du panneau de l'agent, ou à la main) :
l'installation ne pose aucun raccourci clavier (décision du 25/10).

Seul endroit où une demande est « certifiée utilisateur » (PLAN-v2 §5) : elle est
rendue par dom0 et tapée au CLAVIER INTERNE (P2, même filtre que la fenêtre
de confirmation : toute frappe d'un autre périphérique est ignorée, coller
est impossible). Entrée = « Demander à l'agent » :
1. le texte part à l'exécuteur du Doorman (socket des demandes, réservé à
   l'utilisateur de bureau) qui l'enregistre, tire l'identifiant R-xxxxxx,
   calcule la portée (C13) et le LIE à un agent qu'il nomme : l'agent
   permanent sys-agent (agent_long, défaut depuis le 27/10 : AGENT-JETABLE
   D28), ou, si agent_long est faux, un agent neuf dans un jetable (dispNNNN,
   §1) qu'il crée, étiquette et démarre lui-même. La palette n'est juge de
   rien, elle relaie le texte et le nom rendu ;
2. la palette transmet {request, texte} à CET agent par le service qrexec
   qaf.Request (qvm-run --service), qui ouvre un terminal avec « claude » sur
   le texte. qvm-run démarre sys-agent s'il est arrêté : c'est dom0 qui le
   démarre, jamais l'agent. Dans sys-agent, le terminal de la demande
   précédente est remplacé et claude --continue reprend la conversation ;
   dans un jetable, fermer le terminal ferme l'agent.
« Continuer avec cet agent » (§8) : demander(texte, agent=nom) crée une
nouvelle demande liée au même jetable (son influence est gardée), où
qaf.Request lance claude --continue : la suite reprend la conversation (n14) ;
lire_agents_ouverts() liste les agents ouverts (panneau de l'agent).

Le rendu GTK est dans palette_gtk.py, importé seulement par main() : ce module
se teste sans GTK. Plus tard la palette listera aussi les applis.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass

from . import i18n
from .i18n import t
from .nuit import jetons as _j

SOCK_DEMANDES = "/run/qaf-exec/demande.sock"  # = qaf_gate.proto.SOCK_DEMANDES (vérifié par les tests)
QVM_RUN = "/usr/bin/qvm-run"
MAX_TEXTE = 2000  # = qaf_gate.registry.MAX_TEXTE
DELAI_PORTIER = 60  # secondes ; création et démarrage du jetable compris (5 à 15 s, §1)
DELAI_AGENT = 180  # attente de la session graphique du jetable comprise
_REQUEST_RE = re.compile(r"\AR-[0-9]{4,8}\Z")  # = qaf_gate.schema
_AGENT_RE = re.compile(r"\Adisp[0-9]{1,4}\Z")  # = qaf_gate.schema.AGENT_RE
AGENT_PERMANENT = "sys-agent"  # = qaf_gate.proto.AGENT_LONG (vérifié par les tests)
PORTIER_JSON = "/etc/qaf/portier.json"  # réglage de l'utilisateur (lu, jamais écrit ici)
# Textes (qaf_ui/langues) : PREPARATION (sys-agent démarre, ou un jetable est créé),
# FRAPPE_IGNOREE (une ligne dans la carte de 360 px), SUITE (n14 : une suite lance
# claude --continue dans le jetable).
__getattr__ = i18n.constantes({"PREPARATION": "pal.preparation", "FRAPPE_IGNOREE": "pal.frappe_ignoree",
                               "SUITE": "pal.suite"})
TOUCHES_LIBRES = ("Escape",)  # fermer reste possible depuis n'importe quel clavier


@dataclass(frozen=True)
class Resultat:
    ok: bool
    message: str  # une phrase pour l'utilisateur
    request: str | None = None


def appeler_portier(message, chemin=SOCK_DEMANDES, delai=DELAI_PORTIER):
    """Client du socket des demandes (trames du Doorman, qaf_gate.proto)."""
    from qaf_gate import proto  # /usr/lib/qaf, mis dans sys.path par le lanceur qaf-palette
    return proto.client(chemin, delai)(message)


def agent_permanent(chemin=PORTIER_JSON):
    """Vrai si l'agent est l'agent permanent sys-agent (D28) : comme le Doorman
    (classify.lire_config), fichier absent = agent_long vrai ; fichier
    illisible ou hors format = faux (le Doorman refuse alors de démarrer).
    Sert à l'affichage et à la console, jamais à une décision du Doorman."""
    try:
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return isinstance(brut, dict) and brut.get("agent_long", False) is True


def commande_agent(rid, texte, agent):
    """argv et entrée de l'appel qaf.Request vers le jetable de l'agent.
    Jamais --no-gui : le service attend la session graphique (wait-for-session).
    Appel parti de dom0 : aucune politique consultée."""
    argv = [QVM_RUN, "--pass-io", "--service", agent, "qaf.Request"]
    entree = json.dumps({"request": rid, "texte": texte}, ensure_ascii=False).encode("utf-8")
    return argv, entree


def demander(texte, appeler=None, executer=subprocess.run, agent=None):
    """Enregistre la demande dans dom0, qui lance un agent neuf (ou la lie à
    l'agent ouvert `agent`), puis la transmet à ce jetable."""
    texte = (texte or "").strip()
    if not texte:
        return Resultat(False, t("pal.vide"))
    if len(texte) > MAX_TEXTE:
        return Resultat(False, t("pal.trop_longue", max=MAX_TEXTE))
    if agent is not None and not (isinstance(agent, str) and _AGENT_RE.match(agent)):
        return Resultat(False, t("pal.agent_inconnu"))
    message = {"type": "demande", "texte": texte, **({"agent": agent} if agent else {})}
    try:
        rep = (appeler or appeler_portier)(message)
    except (OSError, ValueError):  # ProtoError est un ValueError
        return Resultat(False, t("pal.doorman_muet"))
    rid = rep.get("request") if isinstance(rep, dict) else None
    nom = rep.get("agent") if isinstance(rep, dict) else None
    # Nom rendu par le Doorman : un jetable, ou l'agent permanent pour une demande neuve (D28).
    nom_ok = isinstance(nom, str) and (_AGENT_RE.match(nom) or (agent is None and nom == AGENT_PERMANENT))
    if not isinstance(rep, dict) or rep.get("statut") != "fait" or not isinstance(rid, str) \
            or not _REQUEST_RE.match(rid) or not nom_ok or agent not in (None, nom):
        raison = rep.get("raison") if isinstance(rep, dict) else None
        # La raison est un texte du Doorman (qaf_gate, en français) : affichée telle quelle.
        return Resultat(False, t("pal.refusee", raison=raison or t("pal.reponse_inattendue")))
    argv, entree = commande_agent(rid, texte, nom)
    try:
        r = executer(argv, input=entree, capture_output=True, timeout=DELAI_AGENT)
    except (OSError, subprocess.TimeoutExpired):
        return Resultat(False, t("pal.agent_muet", rid=rid, nom=nom), rid)
    if r.returncode != 0:
        return Resultat(False, t("pal.agent_refuse", rid=rid, nom=nom, code=r.returncode), rid)
    if nom == AGENT_PERMANENT:
        return Resultat(True, t("pal.transmise_permanent", rid=rid, nom=nom), rid)
    return Resultat(True, t("pal.transmise_neuf", rid=rid, nom=nom) if agent is None
                    else t("pal.transmise_suite", rid=rid, nom=nom, suite=t("pal.suite")), rid)


@dataclass(frozen=True)
class AgentOuvert:
    nom: str
    age: int  # secondes
    influence: bool
    texte: str  # début de sa dernière demande

    def ligne(self):
        """« disp1234 · 12 min · influencé · texte » (une ligne de la palette)."""
        etat = t("pal.influence") if self.influence else t("pal.non_influence")
        return t("pal.ligne_agent", nom=self.nom, min=self.age // 60, etat=etat, texte=self.texte[:60])


def agents_ouverts(appeler=None):
    """Agents ouverts selon le Doorman (« Continuer avec cet agent »), [] si
    le Doorman ne répond pas ou répond hors du format attendu."""
    return lire_agents_ouverts(appeler) or []


def lire_agents_ouverts(appeler=None):
    """Comme agents_ouverts, mais None quand le Doorman ne répond pas (le
    panneau de l'agent distingue « aucun agent » de « inconnu »). Même socket
    que la demande, réservé à l'utilisateur de bureau : aucun droit nouveau."""
    try:
        rep = (appeler or appeler_portier)({"type": "agents"})
    except (OSError, ValueError):
        return None
    if not isinstance(rep, dict) or rep.get("statut") != "fait" or not isinstance(rep.get("agents"), list):
        return None
    out = []
    for a in rep["agents"][:16]:
        if (isinstance(a, dict) and isinstance(a.get("agent"), str) and _AGENT_RE.match(a["agent"])
                and type(a.get("age")) is int and type(a.get("influence")) is bool and isinstance(a.get("texte"), str)):
            out.append(AgentOuvert(a["agent"], max(0, a["age"]), a["influence"], " ".join(a["texte"].split())))
    return out


def frappe_admise(clavier, touche, appareil) -> bool:
    """P2 : une frappe n'atteint le champ que si elle vient du clavier interne
    (confirmation.Clavier, vérifié à l'ouverture), sauf Échap."""
    return touche in TOUCHES_LIBRES or clavier.est_de_confiance(appareil)


def envoi_admis(clavier, relire) -> str | None:
    """Avant l'envoi, la liste des périphériques est relue (comme pour A dans la
    fenêtre de confirmation) : None si l'envoi est permis, sinon la raison."""
    if not clavier.ok:
        return t("pal.impossible", raison=clavier.raison)
    if relire() != clavier:
        return t("pal.peripherique_change")
    return None


# Style : jetons Nuit, direction B (29/10, maquettes « B-affirme ») :
# surface Breeze sombre, contour 2 px violet SIGNATURE, bloc de l'agent sur
# SIGNATURE_FOND, son logo (tête T1 sur l'aplat LABEL_AGENT, comme le logo QAF).
# Propriétés CSS de GTK3 uniquement (une propriété inconnue fait échouer tout le
# chargement : vérifié par les tests).
CSS = f"""
window.qaf-palette {{ background-color: {_j.SURFACE}; border: 2px solid {_j.SIGNATURE}; border-radius: 16px; }}
.qaf-palette * {{ font-family: {_j.POLICE}; color: {_j.T1}; }}
.qaf-champ {{ padding: 18px 20px; border-bottom: 1px solid {_j.FILET}; }}
.qaf-champ entry {{ background: transparent; border: none; box-shadow: none; color: {_j.T1};
                  font-size: 19px; font-weight: 500; padding: 0; caret-color: {_j.SIGNATURE}; }}
.qaf-section {{ color: {_j.T3}; font-size: 11px; font-weight: 600; padding: 8px 12px 6px 12px; }}
.qaf-agent {{ background-color: {_j.SIGNATURE_FOND}; border: 1px solid {_j.SIGNATURE}; border-radius: 10px;
            padding: 12px; margin: 0 10px; }}
.qaf-agent-titre {{ font-size: 15px; font-weight: 600; color: {_j.SIGNATURE}; }}
.qaf-agent-sous-titre {{ color: {_j.T2}; font-size: 12.5px; }}
.qaf-touche {{ color: {_j.T2}; font-size: 11px; border: 1px solid {_j.FILET_FORT}; border-radius: 6px; padding: 3px 7px; }}
.qaf-etat {{ color: {_j.T2}; font-size: 12.5px; padding: 6px 22px 0 22px; }}
.qaf-etat.erreur {{ color: {_j.DANGER}; }}
.qaf-pied {{ padding: 12px 20px; border-top: 1px solid {_j.FILET}; margin-top: 10px; }}
.qaf-pied label {{ color: {_j.T3}; font-size: 12px; }}
"""


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(t("pal.usage"), file=sys.stderr)
        return 2
    from . import palette_gtk  # GTK seulement ici
    return palette_gtk.lancer()


if __name__ == "__main__":
    sys.exit(main())
