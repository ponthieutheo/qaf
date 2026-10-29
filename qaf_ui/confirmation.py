"""Fenêtre de confirmation : toute la logique, sans GTK (testée par pytest).

Chemin d'une demande : exécuteur (qaf_gate.confirm.Fenetre) -> socket Unix ->
qaf-ui (serveur.py) -> lire_requete() -> rediger() -> Etat, piloté par le
rendu GTK (fenetre.py) jusqu'à une réponse ou l'expiration.

Règles :
- La phrase est rédigée ICI à partir du plan canonique, ré-analysé par le
  schéma fermé : aucun texte libre de l'agent n'est affiché, seuls les noms
  de qubes, les valeurs de champs fermés (validés par le schéma) et le nombre
  de caractères d'une tâche. Exceptions voulues par OPS-v3 §10 : la commande
  de qube.exec et le texte de qube.type, affichés EN ENTIER (Action.contenu,
  zone défilante), échappés par echapper() (caractères de contrôle, séquences
  bidi, tout ce qui n'est ni ASCII imprimable ni lettre accentuée — en NFD,
  lettre ASCII + diacritiques combinants — devient \\u{XXXX}), présentés comme texte de l'agent. Jamais tronqués : trop
  long pour être affiché (MAX_CONTENU) = demande refusée. Une sortie de qube
  n'est jamais affichée (la fenêtre ne reçoit que le plan, §3.4).
- Refuser est le bouton par défaut : Entrée et Échap refusent, un clic sur
  Refuser aussi, quel que soit le périphérique.
- Autoriser (C11) : touche A frappée au CLAVIER INTERNE, 1,5 s au moins après
  que la fenêtre a pris le focus ; pour une confirmation renforcée, le jeton
  à 6 chiffres tiré par l'exécuteur doit d'abord être tapé au même clavier.
  Tout clic sur Autoriser et toute frappe d'un autre périphérique sont
  ignorés. Le clavier interne est reconnu par son nom (Gdk source device),
  qui doit être unique dans /proc/bus/input/devices et branché à l'emplacement
  attendu (Phys) : un clavier USB qui usurperait le nom bloque l'autorisation.
  La liste des périphériques est relue à chaque appui sur A (C2) : si elle a
  changé depuis l'ouverture, la fenêtre se ferme en refus.
- La demande de l'utilisateur (texte du registre de dom0, jamais de l'agent) est
  affichée avec son âge ; chaque action hors de sa portée porte l'alerte
  « ne découle pas de ta demande » (F1, C13), et l'alerte nomme les qubes que
  la demande ne nomme pas. Sans demande : « Tu n'as rien demandé dans la
  palette pour cette action » (PARCOURS-1 M3).
- Aucun code (N0-N4, R-xxxx, « portier ») dans ce que voit l'utilisateur : gravité en
  mots, codes au journal seulement (PARCOURS-1 M8).
- La cible est toujours l'espace touché (le modèle mis à jour, la destination
  d'une sauvegarde), jamais « Cœur du système » (PARCOURS-1 M1).
- Un qube créé par l'agent (rôles nouveau, agent) n'est jamais montré mieux
  que « Risqué » : son label est choisi par l'agent (C1).
"""

import dataclasses
import re
import unicodedata
from dataclasses import dataclass

from qaf_gate import classify, proto, registry, schema

from . import i18n
from .i18n import t, tn
from .nuit import confiance as nuit

DELAI_AUTORISER = 1.5  # secondes de focus avant que A soit accepté
MAX_EXPIRE = 600
# Rôles fixés par l'exécuteur (registre, coffres, classes : confirm.qubes_du_plan).
ROLES = ("coffre", "agent", "agent-modele", "agent-systeme", "utilisateur", "utilisateur-modele", "utilisateur-systeme")
MAX_CONTENU = 100_000  # caractères échappés affichables ; au-delà : refus, jamais de troncature
_JETON_RE = re.compile(r"\A[0-9]{6}\Z")

CONFIG_CLAVIER = "/etc/qaf/clavier-de-confiance"
CLAVIER_XTEST = "Virtual core XTEST keyboard"  # clavier virtuel de X (xdotool) : jamais de confiance ici
CLAVIER_DEFAUT = "AT Raw Set 2 keyboard"  # contrôleur i8042 d'un portable PC
PHYS_DEFAUT = "isa0060/serio0"
PROC_DEVICES = "/proc/bus/input/devices"

# Couleurs des labels Qubes 4.3 (qubes-core-admin, qubes/app.py).
COULEURS = {"red": "#cc0000", "orange": "#f57900", "yellow": "#edd400", "green": "#73d216",
            "gray": "#555555", "blue": "#3465a4", "purple": "#75507b", "black": "#000000"}

# Vocabulaire débutant (PLAN-v2 §5) : la même règle que les écrans QAF
# (qaf_ui/nuit/confiance.py, DA-NUIT) : ▲ Risqué (rouge, orange), ● Normal
# (jaune, gris), ■ Fiable (bleu, vert, violet), ◆ Coffre (noir, ou un coffre
# de la liste de dom0). La CLASSE passe avant le label : un modèle, un qube
# système et le cœur du système ont leur sorte écrite en clair (Modèle,
# Système, Cœur du système), dessinée en creux et sans couleur de label.
FIABLE, NORMAL, RISQUE, COFFRE, INCONNU = nuit.FIABLE, nuit.NORMAL, nuit.RISQUE, nuit.COFFRE, nuit.INCONNU
MODELE, SYSTEME, COEUR = nuit.MODELE, nuit.SYSTEME, nuit.COEUR
FORMES = nuit.FORMES
COULEUR_SORTE = "#5E6470"  # gris neutre de la carte claire (5,45:1 sur #F4F5F7), teinte d'aucun label

# Résultats renvoyés à l'exécuteur.
AUTORISE, REFUSE, EXPIRE, OCCUPE, INVALIDE, ERREUR = "autorise", "refuse", "expire", "occupe", "invalide", "erreur"
PERIPHERIQUES = "peripheriques"  # liste des périphériques changée pendant la fenêtre (C2)
# Textes (qaf_ui/langues) : la langue est celle de dom0 (qaf_ui/i18n.py), jamais un
# champ de la demande. En anglais, chaque avertissement garde exactement sa force.
# - ALERTE_PORTEE : « ne découle pas de ta demande » ;
# - ALERTE_PERMANENT (sys-agent, AGENT-JETABLE D28) : influencé en permanence, ce n'est pas
#   un événement ; le bandeau le dit sans laisser croire qu'il vient de lire quelque chose ;
# - SUITE_NON_VERIFIEE (n19) : reformulée plutôt que cachée. Le Doorman ne voit pas ce qui
#   est tapé dans le terminal : il ne peut pas savoir s'il y a eu une suite, la note reste
#   sous chaque demande.
__getattr__ = i18n.constantes({
    "ALERTE_PORTEE": "conf.alerte_portee", "SANS_DEMANDE": "conf.sans_demande",
    "ALERTE_INFLUENCE": "conf.alerte_influence", "ALERTE_PERMANENT": "conf.alerte_permanent",
    "SUITE_NON_VERIFIEE": "conf.suite_non_verifiee", "AUTRE_SESSION": "conf.autre_session",
    "MESSAGE_DEV": "conf.message_dev"})
GRAVITES = ("conf.gravite.faible", "conf.gravite.faible", "conf.gravite.moyenne", "conf.gravite.forte",
            "conf.gravite.interdite")  # N0 à N4, en mots (clés de texte)


class DemandeInvalide(ValueError):
    pass


# --- lecture de la demande de l'exécuteur ---------------------------------

@dataclass(frozen=True)
class Requete:
    appelant: str
    plan: schema.Plan
    niveaux: tuple
    renforce: bool
    jeton: str
    influence: bool
    qubes: dict  # nom -> (label, rôle)
    expire_dans: int
    texte_demande: str | None = None  # registre des demandes de dom0 ; None = inconnue ou expirée
    age_demande: int | None = None  # secondes
    hors_portee: frozenset = frozenset()  # indices des opérations hors portée
    non_nommes: tuple = ()  # qubes que la demande ne nomme pas (classement de l'exécuteur)
    sauvegardes: dict = dataclasses.field(default_factory=dict)  # profil -> (destination_vm, chemin|None)
    autre_session: bool = False  # aide d'affichage : la demande a d'abord servi à une autre session
    suite: bool = False  # accepté, sans effet (n19) : la note sur la suite reste affichée sous toute demande


def _nom(v):
    try:
        return schema._name(v, "qubes")
    except schema.PlanError:
        raise DemandeInvalide("nom de qube invalide") from None


def lire_requete(msg) -> Requete:
    """Schéma fermé, comme le Doorman : tout écart = DemandeInvalide."""
    cles = {"type", "appelant", "canonique", "niveaux", "decision", "jeton", "influence", "qubes", "expire_dans",
            "texte_demande", "age_demande", "hors_portee"}
    if (not isinstance(msg, dict) or not cles <= set(msg) <= cles | {"sauvegardes", "non_nommes", "autre_session",
                                                                        "suite"}
            or msg["type"] != "confirmer"):
        raise DemandeInvalide("message inconnu")
    # Agent long ou jetable d'agent (AGENT-JETABLE §5.3 : l'exécuteur a vérifié son identité).
    if not isinstance(msg["appelant"], str) or (msg["appelant"] not in proto.APPELANTS
                                                and not schema.AGENT_RE.match(msg["appelant"])):
        raise DemandeInvalide("appelant inconnu")
    if not isinstance(msg["canonique"], str):
        raise DemandeInvalide("plan absent")
    donnees = msg["canonique"].encode("utf-8")
    try:
        plan = schema.parse_plan(donnees)
    except schema.PlanError as e:
        raise DemandeInvalide(f"plan refusé : {e}") from None
    if schema.canonicalize(plan) != donnees:
        raise DemandeInvalide("plan non canonique")
    niv = msg["niveaux"]
    if (not isinstance(niv, list) or len(niv) != len(plan.ops)
            or any(type(n) is not int or not classify.N0 <= n <= classify.N4 for n in niv)):
        raise DemandeInvalide("niveaux invalides")
    if msg["decision"] not in (classify.CONFIRMER, classify.CONFIRMER_RENFORCE):
        raise DemandeInvalide("décision invalide")
    if not isinstance(msg["jeton"], str) or not _JETON_RE.match(msg["jeton"]):
        raise DemandeInvalide("jeton invalide")
    if type(msg["influence"]) is not bool:
        raise DemandeInvalide("influence invalide")
    qubes = {}
    if not isinstance(msg["qubes"], list) or len(msg["qubes"]) > 3 * schema.MAX_OPS:
        raise DemandeInvalide("qubes invalides")
    for q in msg["qubes"]:
        if not isinstance(q, list) or len(q) != 3 or q[1] not in schema.LABELS or q[2] not in ROLES:
            raise DemandeInvalide("qube invalide")
        if _nom(q[0]) in qubes:
            raise DemandeInvalide("qube en double")
        qubes[q[0]] = (q[1], q[2])
    e = msg["expire_dans"]
    if type(e) is not int or not 1 <= e <= MAX_EXPIRE:
        raise DemandeInvalide("délai invalide")
    texte, age = msg["texte_demande"], msg["age_demande"]
    if texte is None:
        if age is not None:
            raise DemandeInvalide("âge sans demande")
    elif (not isinstance(texte, str) or not texte.strip() or len(texte) > registry.MAX_TEXTE
          or any(c != "\n" and unicodedata.category(c) in schema.TASK_INTERDITS for c in texte)):
        raise DemandeInvalide("texte de demande invalide")
    elif type(age) is not int or not 0 <= age <= registry.DUREE_DEMANDE:
        raise DemandeInvalide("âge de demande invalide")
    hors = msg["hors_portee"]
    if (not isinstance(hors, list) or len(set(hors)) != len(hors)
            or any(type(i) is not int or not 0 <= i < len(plan.ops) for i in hors)):
        raise DemandeInvalide("hors portée invalide")
    nn = msg.get("non_nommes", [])
    if not isinstance(nn, list) or len(nn) > 3 * schema.MAX_OPS or len(set(nn)) != len(nn):
        raise DemandeInvalide("qubes non nommés invalides")
    if type(msg.get("autre_session", False)) is not bool:
        raise DemandeInvalide("session invalide")
    if type(msg.get("suite", False)) is not bool:
        raise DemandeInvalide("suite invalide")
    return Requete(msg["appelant"], plan, tuple(niv), msg["decision"] == classify.CONFIRMER_RENFORCE,
                   msg["jeton"], msg["influence"], qubes, e, texte, age, frozenset(hors),
                   tuple(_nom(q) for q in nn), _sauvegardes(msg.get("sauvegardes", [])),
                   msg.get("autre_session", False), msg.get("suite", False))


def _sauvegardes(liste):
    """Destination de chaque sauvegarde du plan, lue par qaf-gate-outils dans le
    profil de l'utilisateur (U2) : profil, qube de destination, chemin (affiché échappé)."""
    if not isinstance(liste, list) or len(liste) > schema.MAX_OPS:
        raise DemandeInvalide("sauvegardes invalides")
    lues = {}
    for s in liste:
        if not isinstance(s, list) or len(s) != 3 or not (s[2] is None or isinstance(s[2], str) and 0 < len(s[2]) <= 256):
            raise DemandeInvalide("sauvegarde invalide")
        try:
            p = schema._profil(s[0], "profil")
        except schema.PlanError:
            raise DemandeInvalide("profil invalide") from None
        if p in lues:
            raise DemandeInvalide("sauvegarde en double")
        lues[p] = (_nom(s[1]), s[2])
    return lues


# --- rédaction ------------------------------------------------------------

@dataclass(frozen=True)
class Espace:
    nom: str  # nom validé par le schéma, ou libellé du système
    label: str | None
    role: str  # coffre, agent, utilisateur, nouveau, introuvable, systeme

    @property
    def par_agent(self):
        """Nom et label choisis par l'agent (affichés autrement, C1)."""
        return self.role == "nouveau" or self.role.startswith("agent")

    @property
    def confiance(self):
        """Niveau ou sorte affiché (classe d'abord, puis label ; jamais mieux que Risqué pour l'agent)."""
        if self.role == "coffre":
            return COFFRE
        if self.par_agent:  # C1 : label choisi par l'agent, jamais rassurant
            return INCONNU if nuit.niveau(self.label) == INCONNU else RISQUE
        if self.role == "utilisateur-modele":
            return MODELE
        if self.role == "utilisateur-systeme":
            return COEUR if self.nom == "dom0" else SYSTEME
        if self.role == "introuvable":
            return INCONNU
        return nuit.niveau(self.label)

    @property
    def sorte(self):
        return self.confiance in nuit.SORTES

    @property
    def mention(self):
        """Ligne sous le nom : confiance, et « créé par l'agent » le cas échéant."""
        mot = nuit.mot(self.confiance)
        return t("conf.mention_agent", niveau=mot) if self.par_agent else mot

    @property
    def forme(self):
        return FORMES[self.confiance]

    @property
    def couleur(self):
        if self.sorte:
            return COULEUR_SORTE
        return COULEURS.get(self.label, "#8A909C")


@dataclass(frozen=True)
class Action:
    phrase: str
    niveau: int
    source: Espace | None
    destination: Espace | None
    reversible: bool
    nuance: str  # précision affichée après « Réversible : oui/non »
    hors_portee: bool = False  # « ne découle pas de ta demande » (C13)
    contenu: str = ""  # texte de l'agent échappé, affiché EN ENTIER dans une zone défilante
    mais: bool = False  # nuance d'une action réversible : « oui, mais … » (sinon « oui »)


@dataclass(frozen=True)
class Vue:
    titre: str
    actions: tuple
    reversible: str  # « oui », « oui, mais … », « non : … »
    influence: bool
    alerte: str | None
    renforce: bool
    jeton: str
    demande: str  # origine et âge, ou SANS_DEMANDE
    texte_demande: str | None = None  # texte tapé par l'utilisateur dans la palette, affiché par set_text
    age_demande: str = ""  # « il y a 4 min », pour « Ta demande (il y a 4 min) : »
    gravite: str = ""  # en mots (GRAVITES), jamais N0-N4
    entete: str = dataclasses.field(default_factory=lambda: t("conf.entete"))
    sous_entete: str = dataclasses.field(default_factory=lambda: t("conf.sous_entete"))
    pied: str = dataclasses.field(default_factory=lambda: t("conf.pied"))
    suite: bool = False  # n19 : SUITE_NON_VERIFIEE affichée dès qu'une demande est citée
    irreversible: bool = False  # « Réversible : non » (au rouge)
    gravite_forte: bool = False  # gravité forte ou interdite (au rouge)


def _espace(req, nom):
    if nom in req.qubes:
        label, role = req.qubes[nom]
        return Espace(nom, label, role)
    return Espace(nom, None, "introuvable")


def _g(nom):
    """Nom entre guillemets, espaces insécables : jamais coupé de ses « » (ou “ ”)."""
    return i18n.guillemets(nom)


DESIGNATIONS = {"coffre": "conf.des.coffre", "agent": "conf.des.agent", "agent-modele": "conf.des.agent_modele",
                "agent-systeme": "conf.des.agent_systeme", "utilisateur": "conf.des.utilisateur",
                "utilisateur-modele": "conf.des.utilisateur_modele", "utilisateur-systeme": "conf.des.utilisateur_systeme",
                "nouveau": "conf.des.nouveau"}


def designer(e: Espace) -> str:
    return t(DESIGNATIONS.get(e.role, "conf.des.introuvable"), nom=_g(e.nom))


def _action(req, op, niveau) -> Action:
    cible = _espace(req, op.target) if op.target else None
    modele = _espace(req, op.get("template")) if op.get("template") else None
    if op.op == "vm.list":
        return Action(t("conf.op.vm_list"), niveau, None, None, True, "")
    if op.op == "vm.info":
        return Action(t("conf.op.vm_info", ici=designer(cible)), niveau, None, cible, True, "")
    if op.op == "vm.create":
        neuf = Espace(op.target, op.get("label"), "nouveau")
        return Action(t("conf.op.vm_create", neuf=designer(neuf), modele=_g(modele.nom)),
                      niveau, modele, neuf, True, t("conf.nuance.supprimable"))
    if op.op == "vm.clone":
        src, neuf = _espace(req, op.get("source")), Espace(op.target, op.get("label"), "nouveau")
        return Action(t("conf.op.vm_clone", source=designer(src), neuf=_g(neuf.nom)), niveau, src,
                      neuf, True, t("conf.nuance.copie_supprimable"))
    if op.op == "vm.start":
        return Action(t("conf.op.vm_start", ici=designer(cible)), niveau, None, cible, True, "")
    if op.op == "vm.shutdown":
        return Action(t("conf.op.vm_shutdown", ici=designer(cible)), niveau, None, cible, True,
                      t("conf.nuance.travail_perdu"))
    if op.op == "vm.remove":
        return Action(t("conf.op.vm_remove", ici=designer(cible)), niveau, cible, None, False,
                      t("conf.nuance.efface"))
    if op.op == "vm.set_netvm" and op.get("netvm") is None:
        return Action(t("conf.op.netvm_couper", ici=designer(cible)), niveau, cible,
                      Espace(t("conf.sys.aucun_reseau"), None, "systeme"), True, "")
    if op.op == "vm.set_netvm":
        net = _espace(req, op.get("netvm"))
        return Action(t("conf.op.netvm_connecter", ici=designer(cible), net=_g(net.nom)), niveau, cible, net,
                      True, t("conf.nuance.donnees_sortir"), mais=True)
    if op.op == "dispvm.run_task":
        n = len(op.get("task"))
        return Action(tn("conf.op.run_task", n, modele=_g(modele.nom if modele else schema.MODELE_JETABLE)),
                      niveau, modele, Espace(t("conf.sys.jetable"), "red", "systeme"), False,
                      t("conf.nuance.tache"))
    action = _action_v3(req, op, niveau, cible)
    if action is None:
        raise DemandeInvalide("opération sans rédaction")  # le schéma l'aurait refusée
    return action


PROPRIETES = {"label": "conf.prop.label", "memory": "conf.prop.memory", "maxmem": "conf.prop.maxmem",
              "vcpus": "conf.prop.vcpus", "autostart": "conf.prop.autostart",
              "include_in_backups": "conf.prop.include_in_backups", "keyboard_layout": "conf.prop.keyboard_layout",
              "shutdown_timeout": "conf.prop.shutdown_timeout", "qrexec_timeout": "conf.prop.qrexec_timeout",
              "default_dispvm": "conf.prop.default_dispvm", "template_for_dispvms": "conf.prop.template_for_dispvms",
              "provides_network": "conf.prop.provides_network", "default_template": "conf.prop.default_template",
              "check_updates_vm": "conf.prop.check_updates_vm"}
SERVICES = {"on": "conf.service.on", "off": "conf.service.off", "default": "conf.service.default"}


def _valeur(op):
    v = op.get("value")
    if isinstance(v, bool):
        return t("conf.oui") if v else t("conf.non")
    if v is None:
        return t("conf.aucun")
    return _g(v) if schema.valeur_qube(op.op, op.get("prop")) else str(v)


def _regle(r):
    """Règle de pare-feu (champs fermés du schéma) en mots."""
    if r.get("dns"):
        return t("conf.fw.dns")
    quoi = t("conf.fw.hote", hote=r["host"]) if "host" in r else t("conf.fw.tout")
    if "proto" in r:
        quoi += t("conf.fw.proto", proto=r["proto"].upper())
    return quoi + (t("conf.fw.ports", ports=r["ports"]) if "ports" in r else "")


_VISIBLES = {"\\": "\\\\", "\n": "\\n", "\t": "\\t"}


def _lettre_accentuee(ch):
    """Vrai si la décomposition NFD de ch est une lettre ASCII suivie
    seulement de diacritiques combinants (é, à, ç, ñ…). Tout le reste, même
    d'allure latine (ı sans point, ĸ, ſ, œ, ǀ ǁ ǂ ǃ, cyrillique, grec…), est
    échappé : un homoglyphe ne doit jamais passer pour une lettre ASCII."""
    d = unicodedata.normalize("NFD", ch)
    return (len(d) > 1 and d[0].isascii() and d[0].isalpha()
            and all(unicodedata.combining(c) for c in d[1:]))


def echapper(texte):
    """Rend visible tout ce qui pourrait tromper l'œil : seuls l'ASCII
    imprimable et les lettres latines accentuées (_lettre_accentuee) restent tels quels ;
    antislash, saut de ligne, tabulation, contrôles, formats bidi (U+202E…),
    espaces spéciaux, diacritiques isolés et autres écritures deviennent
    des échappements visibles (\\n, \\u{202E}…)."""
    out = []
    for ch in texte:
        o = ord(ch)
        if ch in _VISIBLES:
            out.append(_VISIBLES[ch])
        elif 0x20 <= o < 0x7F or _lettre_accentuee(ch):
            out.append(ch)
        else:
            out.append(f"\\u{{{o:04X}}}")
    return "".join(out)


def _contenu(texte):
    if len(texte) > MAX_CONTENU:
        raise DemandeInvalide("texte de l'agent trop long pour être affiché en entier : refus")
    return texte


def _commande(argv):
    """argv entier, chaque argument entre guillemets droits, échappé."""
    return _contenu(" ".join('"' + echapper(a).replace('"', '\\"') + '"' for a in argv))


def _action_v3(req, op, niveau, cible):
    """Phrases des opérations d'OPS-v3 (lot 1, et lot 2 sans décision en attente)."""
    o, ici = op.op, designer(cible) if cible else ""
    lectures = {"vm.settings": "conf.op.vm_settings", "update.status": "conf.op.update_status",
                "sys.status": "conf.op.sys_status", "prefs.get": "conf.op.prefs_get",
                "fw.list": "conf.op.fw_list", "dev.list": "conf.op.dev_list"}
    if o in lectures:
        return Action(t(lectures[o], ici=ici), niveau, None, cible, True, "")
    if o == "vm.set_prop":
        sauve = op.get("prop") == "include_in_backups" and op.get("value") is False
        return Action(t("conf.op.set_prop", prop=t(PROPRIETES[op.get("prop")]), ici=ici, valeur=_valeur(op)),
                      niveau, None, cible, True, t("conf.nuance.pas_sauvegarde") if sauve else "", mais=sauve)
    if o == "vm.reset_prop":
        return Action(t("conf.op.reset_prop", prop=t(PROPRIETES[op.get("prop")]), ici=ici),
                      niveau, None, cible, True, "")
    if o == "prefs.set":
        return Action(t("conf.op.prefs_set", prop=t(PROPRIETES[op.get("prop")]), valeur=_valeur(op)),
                      niveau, None, Espace(t("conf.sys.reglages"), None, "systeme"), True, "")
    if o == "vm.set_service":
        return Action(t(SERVICES[op.get("state")], service=op.get("service"), ici=ici),
                      niveau, None, cible, True, "")
    if o == "vm.restart":
        return Action(t("conf.op.vm_restart", ici=ici), niveau, None, cible, True, t("conf.nuance.coupes"))
    if o == "vm.set_template":
        modele = _espace(req, op.get("template"))
        return Action(t("conf.op.set_template", ici=ici, modele=designer(modele)), niveau, modele, cible, True, "")
    if o == "pkg.install":
        p = op.get("packages")
        return Action(tn("conf.op.pkg_install", len(p), paquets=", ".join(p), ici=ici), niveau, None, cible, True,
                      t("conf.nuance.retirer"))
    if o == "dispvm.create":
        modele = op.get("template") or schema.MODELE_JETABLE
        return Action(t("conf.op.dispvm_create", modele=_g(modele)), niveau,
                      _espace(req, modele), Espace(t("conf.sys.jetable"), "red", "systeme"), True,
                      t("conf.nuance.efface_arret"))
    contenu, n = "", len(op.get("argv") or op.get("text") or "")
    if o == "qube.exec":
        contenu = _commande(op.get("argv"))
    elif o == "qube.type":
        contenu = _contenu(echapper(op.get("text")))
    entrer = {"qube.windows": "conf.op.qube_windows", "qube.screenshot": "conf.op.qube_screenshot",
              "qube.key": "conf.op.qube_key", "qube.scroll": "conf.op.qube_scroll",
              "qube.mouse": "conf.op.qube_deplacer" if op.get("button") == 0 else "conf.op.qube_cliquer"}
    if o in ("qube.exec", "qube.type"):
        phrase = tn("conf.op.qube_exec" if o == "qube.exec" else "conf.op.qube_type", n, ici=ici)
    elif o in entrer:
        phrase = t(entrer[o], ici=ici, touches=op.get("keys"))
    else:
        phrase = None
    if phrase is not None:
        return Action(phrase, niveau, None, cible, False, t("conf.nuance.reste_fait"), contenu=contenu)
    if o in ("fw.add", "fw.remove"):
        r = op.get("rule")
        accepte = r["action"] == "accept"
        ouvre = accepte == (o == "fw.add")
        if o == "fw.add":
            phrase = t("conf.op.fw_add_autoriser" if accepte else "conf.op.fw_add_bloquer", regle=_regle(r),
                       ici=ici, rang=op.get("position") + 1)
        else:
            phrase = t("conf.op.fw_remove_autoriser" if accepte else "conf.op.fw_remove_bloquer", regle=_regle(r),
                       ici=ici)
        return Action(phrase, niveau, None, cible, True, t("conf.nuance.donnees_sortir") if ouvre else "", mais=ouvre)
    return _action_lot23(req, op, niveau, cible)


APPLY = {"none": "conf.apply.none", "sys": "conf.apply.sys", "all": "conf.apply.all"}
DEPOTS = {"official": "conf.depot.official", "community": "conf.depot.community"}
CLASSES_DEV = {"usb": "conf.dev.usb", "block": "conf.dev.block", "mic": "conf.dev.mic"}


def _liste(noms):
    return ", ".join(_g(n) for n in noms)


def _action_lot23(req, op, niveau, cible):
    """Phrases des lots 2 et 3 d'OPS-v3 (outils de Qubes, bail, fichiers, périphériques…)."""
    o, ici = op.op, designer(cible) if cible else ""
    if o in ("job.status", "backup.profiles", "template.available", "enter.close"):
        cle = {"job.status": "conf.op.job_status", "backup.profiles": "conf.op.backup_profiles",
               "template.available": "conf.op.template_available", "enter.close": "conf.op.enter_close"}[o]
        depot = t(DEPOTS[op.get("repo")]) if op.get("repo") in DEPOTS else ""
        return Action(t(cle, travail=op.get("job"), depot=depot, ici=ici), niveau, None, cible, True, "")
    if o == "update.run":  # cible : le modèle lui-même, jamais « Cœur du système » (PARCOURS-1 M1)
        cibles = op.get("targets")
        e = _espace(req, cibles[0])
        quoi = (t("conf.le_modele", nom=_g(e.nom)) if e.role == "introuvable" else designer(e)) \
            if len(cibles) == 1 else t("conf.les_modeles", noms=_liste(cibles))
        return Action(t("conf.op.update_run", quoi=quoi, apply=t(APPLY[op.get("apply")])), niveau, None,
                      _espace(req, cibles[0]), True, t("conf.nuance.version_avant"))
    if o == "template.install":
        return Action(t("conf.op.template_install", depot=t(DEPOTS[op.get("repo")]), nom=_g(op.get("template"))),
                      niveau, None, Espace(op.get("template"), "black", "nouveau"), True,
                      t("conf.nuance.supprimable"))
    if o == "template.remove":
        return Action(t("conf.op.template_remove", nom=_g(op.get("template"))), niveau,
                      _espace(req, op.get("template")), None, False, t("conf.nuance.reinstaller"))
    if o == "job.cancel":
        return Action(t("conf.op.job_cancel", travail=op.get("job")), niveau, None, None, False,
                      t("conf.nuance.interrompre"))
    if o == "enter.open":  # durée réelle : l'exécuteur ramène le bail d'un coffre à 5 min (D15)
        m = min(op.get("minutes"), classify.MINUTES_COFFRE) if cible.role == "coffre" else op.get("minutes")
        return Action(tn("conf.op.enter_open", m, ici=ici), niveau, None, cible, False, t("conf.nuance.bail"))
    if o in ("file.copy", "file.move"):
        src, dst = _espace(req, op.get("source")), _espace(req, op.get("dest"))
        copie = o == "file.copy"
        return Action(t("conf.op.file_copy" if copie else "conf.op.file_move", chemin=_g(echapper(op.get("path"))),
                        source=designer(src), dest=designer(dst)), niveau, src, dst,
                      copie, t("conf.nuance.copie_fichier") if copie else t("conf.nuance.disparait"))
    if o == "file.open_dispvm":
        return Action(t("conf.op.file_open_dispvm", chemin=_g(echapper(op.get("path"))), ici=ici), niveau, cible,
                      Espace(t("conf.sys.jetable"), "red", "systeme"), True, t("conf.nuance.jetable_ferme"))
    if o in ("dev.attach", "dev.detach"):
        dst, be = _espace(req, op.get("target")), _espace(req, op.get("backend"))
        quoi = t(CLASSES_DEV[op.get("class")], port=f"{op.get('backend')}:{op.get('port')}")
        if o == "dev.detach":
            return Action(t("conf.op.dev_detach", quoi=quoi, dest=designer(dst)), niveau, be, dst, True, "")
        ro = t("conf.dev.lecture_seule") if op.get("class") == "block" and op.get("read_only", True) else ""
        oreille = t("conf.dev.entendre") if op.get("class") == "mic" else ""
        return Action(t("conf.op.dev_attach", quoi=quoi, ident=op.get("device_id"), dest=designer(dst), ro=ro,
                        oreille=oreille), niveau, be, dst, True, t("conf.nuance.peripherique"), mais=True)
    if o == "backup.run":
        dest, chemin = req.sauvegardes.get(op.get("profile"), (None, None))
        if dest:
            ou = _g(echapper(chemin)) if chemin else t("conf.backup.dossier_defaut")
            vers = t("conf.backup.vers", dest=t("conf.backup.dom0") if dest == "dom0"
                     else designer(_espace(req, dest)), dossier=ou)
        else:
            vers = t("conf.backup.illisible")
        cible = (_espace(req, dest) if dest and dest != "dom0" else Espace(t("conf.sys.dom0"), None, "systeme")
                 if dest else None)
        return Action(t("conf.op.backup_run", profil=_g(op.get("profile")), vers=vers), niveau, None, cible,
                      True, "")
    if o == "pkg.remove":
        p = op.get("packages")
        return Action(tn("conf.op.pkg_remove", len(p), paquets=", ".join(p), ici=ici),
                      niveau, None, cible, True, t("conf.nuance.reinstaller_paquets"))
    if o == "vm.tag":
        cle = "conf.op.tag_add" if op.get("action") == "add" else "conf.op.tag_remove"
        return Action(t(cle, etiquette=_g(op.get("tag")), ici=ici), niveau, None, cible, True, "")
    if o == "vm.grow_private":
        return Action(t("conf.op.grow_private", ici=ici, go=op.get("size_mib") // 1024), niveau, None,
                      cible, False, t("conf.nuance.reduire"))
    return None


def _age(secondes):
    m = secondes // 60
    if m < 1:
        return t("conf.age.instant")
    return t("conf.age.min", m=m) if m < 60 else t("conf.age.h", h=m // 60, m=f"{m % 60:02d}")


def rediger(req: Requete) -> Vue:
    actions = tuple(dataclasses.replace(_action(req, op, n), hors_portee=i in req.hors_portee)
                    for i, (op, n) in enumerate(zip(req.plan.ops, req.niveaux)))
    pire = max(actions, key=lambda a: a.niveau)  # la première des plus graves
    titre = pire.phrase
    if len(actions) > 1:
        titre += tn("conf.autres_actions", len(actions) - 1)
    irreversibles = [a for a in actions if not a.reversible]
    if irreversibles:
        reversible = t("conf.reversible.non", nuance=irreversibles[0].nuance)
    else:
        nuances = [a.nuance for a in actions if a.mais]
        reversible = t("conf.reversible.oui_mais", nuance=nuances[0]) if nuances else t("conf.reversible.oui")
    alertes = []
    if req.influence:  # contenu écrit par un qube (INFLUENCANTES), ou agent permanent (D28)
        alertes.append(t("conf.alerte_permanent" if req.appelant in proto.APPELANTS else "conf.alerte_influence"))
    if req.texte_demande is None:  # sans demande, tout est hors portée : une seule phrase, sans alerte de plus
        actions = tuple(dataclasses.replace(a, hors_portee=False) for a in actions)
        demande, age = t("conf.sans_demande"), ""
    else:
        demande, age = t("conf.demande_tapee", age=_age(req.age_demande)), _age(req.age_demande)
        if req.autre_session:
            alertes.append(t("conf.autre_session"))
        if req.non_nommes:
            alertes.append(t("conf.non_nommes", noms=_liste(req.non_nommes)))
        elif req.hors_portee:
            alertes.append(t("conf.hors_portee"))
    pire_niveau = max(req.niveaux)
    return Vue(titre, actions, reversible, req.influence, i18n.phrases(alertes) or None, req.renforce, req.jeton,
               demande, req.texte_demande, age, t(GRAVITES[pire_niveau]),
               suite=req.texte_demande is not None, irreversible=bool(irreversibles),
               gravite_forte=pire_niveau >= classify.N3)


# --- clavier de confiance (C11) -------------------------------------------

@dataclass(frozen=True)
class ConfigClavier:
    nom: str | None  # None : aucun clavier de confiance, autorisation impossible
    phys: str | None
    # Autres claviers INTERNES du même portable, (nom, phys) : sur le Legion 82JU,
    # les touches passent par le contrôleur ITE (USB interne), pas par le clavier
    # PS/2 (relevé du 27/10). Chacun est vérifié comme le premier.
    autres: tuple = ()


def lire_config_clavier(texte: str | None) -> ConfigClavier:
    """Fichier absent (None) : clavier interne i8042. Sinon, chaque ligne utile qui
    n'est pas « phys=… » ouvre un clavier (nom exact du périphérique) ; la ligne
    « phys=… » qui suit donne son emplacement (facultative). Le premier est le
    clavier interne principal, les suivants s'ajoutent (autres)."""
    if texte is None:
        return ConfigClavier(CLAVIER_DEFAUT, PHYS_DEFAUT)
    entrees = []
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne.startswith("phys="):
            if entrees:
                entrees[-1][1] = ligne[5:].strip() or None
        else:
            entrees.append([ligne, None])
    if not entrees:
        return ConfigClavier(None, None)
    return ConfigClavier(entrees[0][0], entrees[0][1], tuple((n, p) for n, p in entrees[1:]))


def peripheriques(texte: str) -> list:
    """(nom, phys) de chaque bloc de /proc/bus/input/devices."""
    res, nom, phys = [], None, ""
    for ligne in texte.splitlines() + [""]:
        if ligne.startswith("N: Name="):
            nom = ligne[8:].strip().removeprefix('"').removesuffix('"')
        elif ligne.startswith("P: Phys="):
            phys = ligne[8:].strip()
        elif not ligne.strip():
            if nom is not None:
                res.append((nom, phys))
            nom, phys = None, ""
    return res


@dataclass(frozen=True)
class Clavier:
    """Verdict sur le clavier de confiance, établi à l'ouverture de la fenêtre
    et refait à chaque appui sur A (C2)."""
    nom: str | None
    ok: bool
    raison: str
    peripheriques: tuple = ()  # (nom, phys) de tous les périphériques lus
    dev: bool = False  # jamais posé par le code livré
    autres: tuple = ()  # noms des autres claviers internes vérifiés (ConfigClavier.autres)

    def est_de_confiance(self, appareil) -> bool:
        return self.ok and isinstance(appareil, str) and (appareil == self.nom or appareil in self.autres)


def verifier_clavier(config: ConfigClavier, devices: str | None) -> Clavier:
    if not config.nom:
        return Clavier(None, False, t("conf.clavier.aucun"))
    if devices is None:
        return Clavier(config.nom, False, t("conf.clavier.illisible"))
    tous = tuple(peripheriques(devices))
    # Chaque clavier déclaré doit être là, seul de son nom, à son emplacement :
    # sinon AUCUN n'est de confiance (un clavier USB qui usurpe un nom bloque tout).
    for nom, phys in ((config.nom, config.phys), *config.autres):
        memes = [p for n, p in tous if n == nom]
        if not memes:
            return Clavier(config.nom, False, t("conf.clavier.introuvable"), tous)
        if len(memes) > 1:
            return Clavier(config.nom, False, t("conf.clavier.homonymes", n=len(memes)), tous)
        if phys and not memes[0].startswith(phys):
            return Clavier(config.nom, False, t("conf.clavier.deplace"), tous)
    return Clavier(config.nom, True, "", tous, autres=tuple(n for n, _ in config.autres))


def _lire(chemin):
    try:
        with open(chemin, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def clavier_systeme(config=CONFIG_CLAVIER, devices=PROC_DEVICES) -> Clavier:
    return verifier_clavier(lire_config_clavier(_lire(config)), _lire(devices))


# --- état de la fenêtre ---------------------------------------------------

# Rangée du haut d'un clavier AZERTY sans Maj (noms de keyval GDK) : sur un
# clavier français, les chiffres demandent sinon Maj.
_AZERTY = {"ampersand": "1", "eacute": "2", "quotedbl": "3", "apostrophe": "4", "parenleft": "5",
           "minus": "6", "egrave": "7", "underscore": "8", "ccedilla": "9", "agrave": "0"}
TOUCHES_REFUS = ("Return", "KP_Enter", "Escape")


def chiffre(touche: str) -> str | None:
    if len(touche) == 1 and touche.isdigit():
        return touche
    if touche.startswith("KP_") and len(touche) == 4 and touche[3].isdigit():
        return touche[3]
    return _AZERTY.get(touche)


@dataclass(frozen=True)
class Resultat:
    accorde: bool
    motif: str
    jeton: str = ""

    def reponse(self) -> dict:
        return {"accorde": self.accorde, "motif": self.motif, "jeton": self.jeton}


def refus(motif) -> dict:
    return Resultat(False, motif).reponse()


class Etat:
    """Machine d'état de la fenêtre. Le rendu lui passe des noms de touches
    (Gdk.keyval_name) et le nom du périphérique source ; elle seule décide."""

    BOUTON_PAR_DEFAUT = "refuser"

    def __init__(self, vue: Vue, clavier: Clavier, expire_dans, horloge, relire=None):
        # relire() -> Clavier : nouvelle lecture des périphériques (C2) ;
        # None = pas de relecture (tests de la machine d'état seulement).
        self.vue, self.clavier, self.horloge, self.relire = vue, clavier, horloge, relire
        self.fin = horloge() + expire_dans
        self.focus_depuis = None
        self.tape = ""
        self.message = "" if clavier.ok else t("conf.msg.impossible", raison=clavier.raison)
        self.resultat = None

    @property
    def message(self):
        # Bandeau du mode dev : préfixé à chaque message, jamais effaçable.
        if self.clavier.dev:
            return i18n.phrases([t("conf.message_dev")] + ([self._message] if self._message else []))
        return self._message

    @message.setter
    def message(self, texte):
        self._message = texte

    def _finir(self, accorde, motif):
        if self.resultat is None:
            self.resultat = Resultat(accorde, motif, self.tape if accorde and self.vue.renforce else "")
        return self.resultat

    def restant(self) -> float:
        return max(0.0, self.fin - self.horloge())

    def tic(self):
        """Appelé régulièrement : ferme sur expiration."""
        if self.resultat is None and self.restant() <= 0:
            self._finir(False, EXPIRE)
        return self.resultat

    def focus(self, actif: bool):
        # Comme FocusStealingHelper de qrexec-policy-agent : le délai repart à
        # chaque prise de focus, une frappe destinée à une autre fenêtre ne
        # peut pas autoriser.
        self.focus_depuis = self.horloge() if actif else None

    def jeton_complet(self) -> bool:
        return not self.vue.renforce or self.tape == self.vue.jeton

    def autorisable(self) -> bool:
        return (self.resultat is None and self.clavier.ok and self.restant() > 0 and self.jeton_complet()
                and self.focus_depuis is not None and self.horloge() - self.focus_depuis >= DELAI_AUTORISER)

    def touche(self, touche: str, appareil):
        """Frappe : renvoie le résultat si la fenêtre doit se fermer."""
        if self.tic() is not None:
            return self.resultat
        if touche in TOUCHES_REFUS:
            return self._finir(False, REFUSE)  # refuser reste possible partout
        if not self.clavier.est_de_confiance(appareil):
            self.message = t("conf.msg.frappe_ignoree")
            return None
        c = chiffre(touche)
        if self.vue.renforce and c is not None:
            if len(self.tape) < len(self.vue.jeton):
                self.tape += c
            self.message = ""
        elif self.vue.renforce and touche == "BackSpace":
            self.tape = self.tape[:-1]
        elif touche in ("a", "A"):
            if self.autorisable():
                if self.relire is not None and self.relire() != self.clavier:
                    return self._finir(False, PERIPHERIQUES)  # branchement pendant la fenêtre
                return self._finir(True, AUTORISE)
            if not self.clavier.ok:
                pass  # message déjà posé
            elif not self.jeton_complet():
                self.message = t("conf.msg.code_dabord")
            else:
                self.message = t("conf.msg.patiente")
        return None

    def clic(self, bouton: str, appareil=None):
        """Clic : seul Refuser agit. Autoriser ne se fait qu'au clavier interne."""
        if self.tic() is not None:
            return self.resultat
        if bouton == "refuser":
            return self._finir(False, REFUSE)
        self.message = t("conf.msg.clic_ignore")
        return None

    def fermeture(self):
        """Fenêtre fermée par le gestionnaire de fenêtres : refus."""
        return self._finir(False, REFUSE)
