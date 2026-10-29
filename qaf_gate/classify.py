"""Classement d'un plan : gravité (N0-N4) × provenance × mode -> décision.

Tableau de docs/PLAN-v2.md §2, avec les corrections :
- C1 : « créé par l'agent » = présent dans le registre du Doorman (jamais un tag) ;
- C2 : vm.create ne donne jamais de réseau ; donner le réseau à un qube de
  l'agent = N2 ; le donner à un qube de l'utilisateur (changer son netvm) = N3.
- Coffres (vault…) : liste tenue par l'utilisateur ; toute modification d'un coffre est
  au moins N3 et toujours confirmée, même en mode Pilote (« tout sauf Coffre »).
- C13 : avec une portée (qubes nommés dans la demande de l'utilisateur ou créés pendant
  elle), toute opération N1+ sur un qube hors portée « ne découle pas de ta
  demande » : au moins une confirmation renforcée, même en mode Pilote.
  Sont comparés TOUS les qubes nommés par l'opération (Op.qubes() : champs
  de schema.CHAMPS_QUBE, et « value » de type qube), sauf ce que crée
  vm.create : le nom du nouveau qube et son modèle. Choisir le modèle d'un
  nouvel espace sans réseau n'expose aucune donnée (un modèle ne contient pas
  de données de l'utilisateur, et Qubes n'accepte qu'un TemplateVM) ; un coffre comme
  modèle reste N4.
- C13 bis (PARCOURS-1, docs/PLAN-v2.md) : un qube impliqué seulement de façon
  implicite n'a pas à être nommé par l'utilisateur : la route par défaut (default_netvm)
  posée par vm.set_netvm, le modèle par défaut ou un modèle de l'agent posé
  par vm.set_template, le modèle copié par vm.clone (sauf s'il a, à une
  profondeur quelconque, un dépendant qui n'est ni A ni T). Sauf s'il est protégé, coffre ou qube système
  de la configuration (sys-usb, sys-whonix…) ; ce que l'opération modifie reste
  toujours comparé à la portée.
- vm.clone : copie d'un TemplateVM en modèle de l'agent (registre, classe A).
  Un coffre, un qube protégé ou autre chose qu'un TemplateVM : N4. Modèle d'un
  coffre ou d'un qube système : N3 confirmé (l'agent lirait son contenu, revue
  du 25/10). Les dépendants sont suivis à toute profondeur (Contexte.descendance :
  template, puis modèle de jetables et default_dispvm) ; un seul qui n'est ni A
  ni T (P, S, C ou, dans le doute, M) suffit (revue de sécurité du 25/10).
- H1 : un jetable ne part que d'un modèle de la liste blanche de dom0
  (qaf-dvm, plus ceux listés par l'utilisateur) ; tout autre modèle = N4. Modifier
  qaf-dvm ou tpl-qaf-hand (infrastructure des jetables) = N4.
- Agent jetable (docs/AGENT-JETABLE.md) : qaf-agent-dvm, sys-agent et les
  jetables d'agent vivants sont P, donc tpl-agent aussi (R-2, en toute
  configuration) ; jetable ou clone d'une source étiquetée = N4 (R-6) ; la
  portée de C13 ne pose de plancher qu'à partir de N2 (n2), sauf une opération N1
  sans retour sur un qube d'un autre jetable : confirmation simple (R-9) ;
  n1 : le modèle de l'agent d'un qube de la portée y entre (modeles_agent).

OPS-v3 (docs/OPS-v3.md §3.2 et §3.2 bis) : chaque qube nommé reçoit une classe
P (protégé), C (coffre), S (système), M (modèle), A (agent) ou T (l'utilisateur),
calculée à partir de qubesd, du registre et de la configuration dom0
(Contexte). Règles de revue appliquées ici : R-A (tous les champs qube,
valeurs comprises), R-B (via Op.qubes), R-C (contagion : un qube de l'agent
dont un autre qube dépend n'est plus A), R-D (faire dépendre l'utilisateur de
l'agent : N3 renforcé, N4 vers un coffre), R-E (valeur globale héritée),
R-F (retirer un rôle dont d'autres dépendent : N4), R-G (ne_jamais_demarrer).
vm.reset_prop d'une propriété de type qube est classé avec la valeur EFFECTIVE
qu'il produira (valeur par défaut de qubesd, Contexte.defaut), avec les mêmes
contrôles que vm.set_prop (revue du 25/10).
"""

import json
import os
import re
from dataclasses import dataclass, field
from functools import cached_property

from . import proto, schema

N0, N1, N2, N3, N4 = range(5)

AUTO = "auto"
CONFIRMER = "confirmer"
CONFIRMER_RENFORCE = "confirmer_renforce"
REFUSER = "refuser"
_ORDRE = (AUTO, CONFIRMER, CONFIRMER_RENFORCE, REFUSER)

NORMAL = "normal"
PILOTE = "pilote"

# Qubes que l'agent ne peut jamais viser par une opération qui modifie.
PROTEGES = frozenset({"dom0"})
# Coffres par défaut ; la liste réelle vient de la configuration dom0 de l'utilisateur.
COFFRES_DEFAUT = frozenset({"vault"})
# Modèles de jetable admis ; la liste réelle viendra de la configuration dom0.
JETABLES_DEFAUT = frozenset({schema.MODELE_JETABLE})
# Infrastructure des jetables du Doorman (tools/setup-qaf-dvm.sh).
INFRA_JETABLES = frozenset({schema.MODELE_JETABLE, "tpl-qaf-hand", *schema.MODELES_AGENT})
SYSTEME_DEFAUT = frozenset({"sys-usb", "sys-audio", "sys-whonix"})
MINUTES_COFFRE = 5  # D15 : bail sur un coffre entrable ramené à 5 min (exécuteur et fenêtre)
CONFIG = "/etc/qaf/portier.json"

P, C, S, M, A, T = "P", "C", "S", "M", "A", "T"
LECTURES = frozenset({"vm.list", "vm.info", "vm.settings", "update.status", "sys.status", "prefs.get",
                      "fw.list", "dev.list", "template.available", "job.status", "enter.close", "backup.profiles"})
ENTRER = frozenset({"qube.exec", "qube.windows", "qube.screenshot", "qube.type", "qube.key", "qube.mouse",
                    "qube.scroll"})
_CONTENU = ENTRER | {"pkg.install", "pkg.remove"}  # ce qui est fait dans un modèle se retrouve dans ses qubes
# pkg.remove : paquets dont le retrait casserait le qube ou Qubes (OPS-v3 §4).
_CRITIQUES = re.compile(r"\A(qubes|python3-qubes|xen|linux-image|kernel|grub)|\A(systemd|apt|dnf|libc6|glibc)\Z")
_TAGS_N4 = ("created-by-", "guivm-", "audiovm-", "qaf-")
# Champ qui désigne le qube visé, quand ce n'est pas « name ».
_CIBLE = {"dev.attach": "target", "dev.detach": "target", "file.copy": "source", "file.move": "source",
          "template.remove": "template", "vm.clone": "source"}
_COFFRE_N4 = ENTRER | {"vm.remove"}
_GLOBAUX_P = ("management_dispvm", "default_guivm", "default_audiovm")
_GLOBAUX_S = ("clockvm", "updatevm", "default_netvm")
_GLOBAUX_QUBE = _GLOBAUX_P + _GLOBAUX_S + ("default_template", "default_dispvm")
_LIENS = ("template", "netvm", "default_dispvm")
# (niveau sur un qube de l'agent, niveau sur un qube de l'utilisateur).
_BASE = {"vm.start": (N1, N2), "vm.shutdown": (N1, N2), "vm.restart": (N1, N2), "vm.remove": (N1, N3),
         "vm.set_service": (N1, N2), "vm.set_template": (N1, N3), "pkg.install": (N1, N2),
         "pkg.remove": (N1, N3), "vm.grow_private": (N1, N2), "dev.detach": (N1, N2), "job.cancel": (N2, N2),
         **{o: (N1, N3) for o in ENTRER}}
_PROPS = {"label": (N1, N3), "default_dispvm": (N2, N3), "template_for_dispvms": (N2, N3),
          "provides_network": (N2, N3)}  # les autres : (N1, N2)
# Ce qu'une opération crée (nouveau qube, modèle de départ ou copié) : hors portée C13.
_CREES = {"vm.create": ("name", "template"), "vm.clone": ("name",)}
MOTIFS_N4 = {"template.install": "l'installation d'un modèle se fait par l'utilisateur avec l'outil Qubes Template Manager "
                                 "(dis-lui quel modèle installer)"}
_CLES_CONFIG = ("coffres", "jetables", "systeme", "admin_qubes", "ne_jamais_demarrer", "coffres_entrables")
# Opérations sans annulation (OPS-v3) : R-9 les garde confirmées hors portée
# sur un qube créé par un autre jetable d'agent.
SANS_RETOUR = frozenset({"vm.remove", "template.remove", "pkg.remove", "file.move"})
# Étiquettes que Qubes pose lui-même sur ses qubes (admin.vm.Create,
# CreateDisposable, guivm, audiovm) : sans effet pour R-6.
_TAGS_QUBES = ("created-by-", "disp-created-by-", "guivm-", "audiovm-", "relayvm-")


def _hotes(v, champ):
    if not isinstance(v, list) or not 1 <= len(v) <= 8 or len(set(map(str, v))) != len(v):
        raise ValueError(f"configuration du Doorman : {champ} doit être une liste de 1 à 8 hôtes distincts")
    return tuple(schema._hote(h, champ) for h in v)


# Agent jetable (AGENT-JETABLE §3, §8, §11, D26) : clé -> validateur ;
# défauts dans Config. Durées en minutes. agent_hotes : seuls hôtes joints
# (TCP 443) par le pare-feu fermé du jetable (R-1, sonde J-5).
_CLES_AGENT = {"agent_long": schema._bool, "agents_max": schema._entier(1, 8),
               "agent_inactivite": schema._entier(5, 1440), "agent_duree_max": schema._entier(30, 1440),
               "agent_hotes": _hotes}


@dataclass(frozen=True)
class Classement:
    niveaux: tuple  # un niveau par opération, dans l'ordre du plan
    decision: str
    hors_portee: tuple = ()  # indices des opérations hors de la portée de la demande
    non_nommes: tuple = ()  # qubes de ces opérations que la demande ne couvre pas (affichés par la fenêtre)


@dataclass(frozen=True)
class Config:
    """Listes de la configuration dom0 de l'utilisateur (CONFIG, lue par l'exécuteur)."""
    coffres: frozenset = COFFRES_DEFAUT
    jetables: frozenset = JETABLES_DEFAUT
    systeme: frozenset = SYSTEME_DEFAUT
    admin_qubes: frozenset = frozenset()  # qubes à qui une politique hors QAF ouvre dom0
    ne_jamais_demarrer: frozenset = frozenset()  # R-G (Legion : sys-usb)
    coffres_entrables: frozenset = frozenset()  # D15 : vide par défaut, bail de 5 min renforcé
    # Appelant nommé admis : l'agent long, si portier.json dit agent_long
    # (§11, faux à la livraison) ; Config() sans fichier lu le garde (tests).
    appelants: frozenset = proto.APPELANTS
    agents_max: int = 4
    agent_inactivite: int = 60
    agent_duree_max: int = 480
    agent_hotes: tuple = ("api.anthropic.com",)

    def en_json(self):
        return {**{k: sorted(getattr(self, k)) for k in _CLES_CONFIG},
                **{k: list(v) if isinstance(v := getattr(self, k), tuple) else v for k in _CLES_AGENT}}

    @classmethod
    def de_json(cls, d):
        """Schéma fermé : clé inconnue ou valeur invalide = ValueError (fail-closed)."""
        if not isinstance(d, dict) or not set(d) <= set(_CLES_CONFIG) | set(_CLES_AGENT):
            raise ValueError("configuration du Doorman : clé inconnue")
        lus = {}
        for k, v in d.items():
            if k in _CLES_AGENT:
                lus[k] = _CLES_AGENT[k](v, k)
                continue
            if not isinstance(v, list):
                raise ValueError(f"configuration du Doorman : {k} doit être une liste")
            lus[k] = frozenset(schema._name(n, k) for n in v)
        long_ = lus.pop("agent_long", False)
        return cls(**lus, appelants=proto.APPELANTS if long_ else frozenset())

    @property
    def agent_long(self):
        return bool(self.appelants)


def lire_config(chemin=CONFIG):
    if not os.path.exists(chemin):
        return Config()
    with open(chemin, encoding="utf-8") as f:
        return Config.de_json(json.load(f))


@dataclass(frozen=True)
class Contexte:
    """Ce que le classement sait de Qubes : qubes (nom -> champs de
    backend.QubeInfo), propriétés globales, configuration de dom0."""
    qubes: dict
    prefs: dict = field(default_factory=dict)
    config: Config = Config()
    baux: frozenset = frozenset()  # qubes sous bail valide (§3.6), tenu par l'exécuteur
    profils: dict = field(default_factory=dict)  # profil -> destination_vm, passphrase_vm (qaf-gate-outils)
    tags_politique: frozenset | None = frozenset()  # tags cités en @tag: des politiques ; None = inconnus
    agents: frozenset = frozenset()  # jetables d'agent vivants (registre Agents) : P (§6, point 3)

    def info(self, nom, champ):
        return self.qubes.get(nom, {}).get(champ)

    @cached_property
    def noyau(self):
        """P par rôle : dom0, appelants (sys-agent même sans agent long),
        jetables d'agent vivants, infrastructure des jetables (qaf-agent-dvm
        compris), admin_qubes, qubes graphique, audio et Salt, jetable par
        défaut d'un appelant (C3)."""
        c, appelants = self.config, {*self.config.appelants, proto.AGENT_LONG, *self.agents}
        p = {"dom0", *appelants, *INFRA_JETABLES, *c.admin_qubes, *(self.prefs.get(k) for k in _GLOBAUX_P)}
        p |= {i.get(k) for i in self.qubes.values() for k in ("guivm", "audiovm", "management_dispvm")}
        p |= {self.info(a, "default_dispvm") for a in appelants}
        p.discard(None)
        return frozenset(p)

    @cached_property
    def proteges(self):
        """P : le noyau et le modèle de chacun de ses qubes."""
        return self.noyau | {t for n in self.noyau if (t := self.info(n, "template"))}

    @cached_property
    def dependants(self):
        """nom -> qubes qui l'ont pour template, netvm ou default_dispvm."""
        d = {}
        for n, i in self.qubes.items():
            for k in _LIENS:
                if i.get(k):
                    d.setdefault(i[k], set()).add(n)
        return d

    def descendance(self, nom):
        """Qubes qui s'appuient sur nom à toute profondeur : par template (un
        DispVM a pour template son modèle de jetables), puis par default_dispvm."""
        vus, file = set(), [nom]
        while file:
            n = file.pop()
            for d in self.dependants.get(n, ()):
                if d not in vus and n in (self.info(d, "template"), self.info(d, "default_dispvm")):
                    vus.add(d)
                    file.append(d)
        return vus - {nom}

    def global_(self, nom):
        return any(self.prefs.get(k) == nom for k in _GLOBAUX_QUBE)

    def classe(self, nom, agent=frozenset()):
        if nom in self.proteges:
            return P
        if nom in self.config.coffres:
            return C
        if (self.info(nom, "provides_network") or nom in self.config.systeme
                or any(self.prefs.get(k) == nom for k in _GLOBAUX_S)):
            return S
        # R-C : un qube de l'agent dont un qube de l'utilisateur (ou un réglage global) dépend n'est plus A.
        if nom in agent and self.dependants.get(nom, set()) <= set(agent) and not self.global_(nom) and not (
                self.info(nom, "livre")):  # n17 : un modèle livré par Qubes reste à l'utilisateur
            return A
        if self.info(nom, "klass") == "TemplateVM" or self.info(nom, "template_for_dispvms"):
            return M
        return T

    def appuis(self, nom, agent):
        """Classes des qubes qui ont ce qube pour modèle ou pour modèle de jetables."""
        return {self.classe(d, agent) for d in self.dependants.get(nom, ())
                if nom in (self.info(d, "template"), self.info(d, "default_dispvm"))}

    def chaine(self, nom):
        """Le qube et sa chaîne de netvm : ce que qubesd démarre avec lui."""
        vus = []
        while nom and nom not in vus and len(vus) < 8:
            vus.append(nom)
            nom = self.info(nom, "netvm")
        return set(vus)

    def heritiers(self, agent):
        """Classes des qubes dont default_dispvm suit la valeur globale (R-E).
        Un jetable a pour défaut son propre modèle, pas la valeur globale."""
        return {self.classe(n, agent) for n, i in self.qubes.items()
                if i.get("default_dispvm_herite") and i.get("klass") != "DispVM"}

    def jetable_risque(self, v, agent):
        """Un jetable parti de v hérite de toute sa chaîne : v, son modèle, le
        modèle de ce modèle. Vrai si un seul maillon est à l'agent (registre) ou
        a le réseau. Seul juge d'une valeur de jetable : R-E, coffre (set_prop,
        reset_prop), file.open_dispvm."""
        for _ in range(3):
            if v and (v in agent or self.info(v, "netvm")):
                return True
            v = v and self.info(v, "template")
        return False

    def defaut(self, nom, prop):
        """Valeur que prendrait la propriété (de type qube) après vm.reset_prop :
        default_dispvm d'un jetable = son modèle, sinon la valeur globale
        (qubes/vm/qubesvm.py et dispvm.py)."""
        if prop != "default_dispvm":
            return None
        return self.info(nom, "template") if self.info(nom, "klass") == "DispVM" else self.prefs.get(prop)


def champs_qube(op, ctx):
    """Op.champs_qube(), plus la valeur effective d'un vm.reset_prop de type qube."""
    ch = op.champs_qube()
    if op.op == "vm.reset_prop" and schema.valeur_qube("vm.set_prop", op.get("prop")):
        v = ctx.defaut(op.target, op.get("prop"))
        ch += (("value", v),) if v else ()
    return ch


def _valeur(op, ctx):
    """Valeur écrite par l'opération : celle du plan, ou l'effective d'un reset."""
    if op.op == "vm.reset_prop":
        return dict(champs_qube(op, ctx)).get("value")
    return op.get("value")


def _plus_strict(a, b):
    return max(a, b, key=_ORDRE.index)


def _protege_admis(op, champ, q, ctx):
    """Exceptions à « un qube P nommé = N4 » (R-A)."""
    if champ == "template" and op.op in ("dispvm.run_task", "dispvm.create"):
        return q in ctx.config.jetables  # qaf-dvm : le modèle de jetable du Doorman
    # Le modèle d'un qube protégé, posé comme modèle d'un autre qube, n'est pas
    # touché : seul le noyau P reste interdit comme valeur.
    modele = champ == "template" or (champ == "value" and op.get("prop") == "default_template")
    if champ == "backend" and op.get("class") == "mic":  # seule exception P : le micro (dom0 ou l'audiovm)
        return q in ("dom0", ctx.info(op.get("target"), "audiovm"))
    return modele and op.op in ("vm.create", "vm.set_template", "prefs.set") and q not in ctx.noyau


def _sert_de(nom, ctx, lien, globaux):
    return any(ctx.info(d, lien) == nom for d in ctx.dependants.get(nom, ())) or any(
        ctx.prefs.get(g) == nom for g in globaux)


def _niveau_prop(prop, v, nom, c, ctx, agent):
    if prop == "include_in_backups":
        return N1 if v is True else N4 if c == C else N1 if c == A else N3
    if prop == "provides_network" and (c == C or (v is False and _sert_de(nom, ctx, "netvm", ("default_netvm",)))):
        return N4  # R-F : couperait le réseau de ses clients
    if prop == "template_for_dispvms" and ((v is True and c in (S, C)) or (v is False and (
            _sert_de(nom, ctx, "default_dispvm", ("default_dispvm",))
            or any(ctx.info(d, "klass") == "DispVM" for d in ctx.dependants.get(nom, ()))))):
        return N4  # R-F : casserait les jetables qui en partent
    if prop == "default_dispvm" and c == C and ctx.jetable_risque(v, agent):
        return N4
    a, t = _PROPS.get(prop, (N1, N2))
    n = a if c == A else t
    return max(n, N3) if c == S else n


def _niveau_v3(op, ctx, agent, c):
    """Lots 2 et 3 (OPS-v3 §4, §7 à §10) ; None = règle générale (_BASE)."""
    o, nom = op.op, _cible(op)
    if o == "update.run":
        cs = {ctx.classe(t, agent) for t in op.get("targets")}
        if op.get("apply") != "none" or C in cs:
            return N3  # redémarre des qubes ; coffre StandaloneVM
        return N1 if cs == {A} else N2  # M(S) 2 : même chemin et mêmes dépôts que Qubes Update
    if o == "template.install":
        return N4  # qvm-template importe par sudo, qaf-outils n'en a pas (RESULTATS-V2) : l'utilisateur l'installe
    if o == "template.remove":
        return N4 if {S, C} & ctx.appuis(nom, agent) else N1 if c == A else N3
    if o == "backup.run":
        # V13 : jamais l'archive ni la phrase de passe chez l'agent (destination
        # dom0 permise : l'agent ne lit pas dom0). Profil illisible : refus.
        p = ctx.profils.get(op.get("profile"))
        if not p or ctx.classe(p.get("destination_vm"), agent) in (P, A) and p.get("destination_vm") != "dom0" or (
                p.get("passphrase_vm") and ctx.classe(p["passphrase_vm"], agent) in (P, A)):
            return N4
        return N3
    if o == "enter.open":
        if c == A:
            return N1
        # V7 : pas de bail sur S, M(S/C) ; D15 : coffre seulement s'il est entrable.
        if c == S or (c == M and {S, C} & ctx.appuis(nom, agent)) or (
                c == C and nom not in ctx.config.coffres_entrables):
            return N4
        return N3
    if o in ("file.copy", "file.move"):
        if c == C and o == "file.move":
            return N4
        return N1 if c == A and ctx.classe(op.get("dest"), agent) == A else N3
    if o == "file.open_dispvm":
        reseau = ctx.jetable_risque(ctx.info(nom, "default_dispvm"), agent)
        if c == C and reseau:
            return N4
        return N1 if c == A else N3 if reseau or c in (S, C) else N2
    if o == "dev.attach":
        be = op.get("backend")
        if c == C or ctx.classe(be, agent) == C or (c == S and nom != be):
            return N4
        return N3  # USB, micro, caméra, bloc : jamais selon le type annoncé
    if o == "vm.tag":
        t = op.get("tag")  # politiques illisibles (None) : fail-closed
        return N4 if ctx.tags_politique is None or t.startswith(_TAGS_N4) or t in ctx.tags_politique else N3
    if o == "pkg.remove" and any(_CRITIQUES.match(p) for p in op.get("packages")):
        return N4
    return None


def _cible(op):
    return op.get(_CIBLE.get(op.op, "name"))


def _niveau(op, ctx, agent, c):
    o = op.op
    if o == "vm.create":
        return N1  # créer sans réseau
    if o == "vm.clone":  # copier un modèle ne le modifie pas ; le clone est sans réseau
        src = op.get("source")
        return N1 if ctx.info(src, "klass") == "TemplateVM" and src not in ctx.config.coffres and not (
            etiquette_heritee(src, ctx)) else N4
    if o in ("dispvm.run_task", "dispvm.create"):
        # qaf-dvm (sans réseau, rien de l'utilisateur) ; un autre modèle de la liste
        # blanche peut contenir des données de l'utilisateur ou avoir le réseau : N3.
        modele = op.get("template") or schema.MODELE_JETABLE
        if modele not in ctx.config.jetables or etiquette_heritee(modele, ctx):
            return N4
        if modele != schema.MODELE_JETABLE:
            return N3
        return N2 if o == "dispvm.run_task" else N1
    if o == "prefs.set":
        # R-E, valeur effective : un qube P (sys-agent : C3) ou un coffre qui
        # hérite du réglage global recevrait ce jetable ; N4 si un maillon de sa
        # chaîne (jetable_risque) a le réseau ou est à l'agent. Les qubes à
        # valeur explicite ne comptent pas.
        if op.get("prop") == "default_dispvm" and {P, C} & ctx.heritiers(agent) and ctx.jetable_risque(
                op.get("value"), agent):
            return N4
        return N3 if op.get("prop") == "default_dispvm" else N2
    n = _niveau_v3(op, ctx, agent, c)
    if n is not None:
        return n
    if c == C and o in _COFFRE_N4:  # sans bail (coffres_entrables, D15) : jamais
        return N4
    if o == "vm.set_netvm":
        if op.get("netvm") is None:  # couper le réseau : réduit l'exposition
            return N1 if c == A else N3 if c == S else N2
        return N4 if c == C else N2 if c == A else N3  # un coffre n'a jamais de réseau
    if o in ("vm.set_prop", "vm.reset_prop"):
        prop = op.get("prop")
        bool_ = schema.PROPS[prop] is schema._bool
        # Reset : valeur effective (qube) ; booléen : les deux valeurs (prudent).
        valeurs = [True, False] if o == "vm.reset_prop" and bool_ else [_valeur(op, ctx)]
        return max(_niveau_prop(prop, v, op.target, c, ctx, agent) for v in valeurs)
    if o in ("fw.add", "fw.remove"):
        # Ajouter un accept ou retirer un drop ouvre ; l'inverse ferme.
        ouvre = (op.get("rule")["action"] == "accept") == (o == "fw.add")
        a, t = (N2, N3) if ouvre else (N1, N2)
    else:
        a, t = _BASE[o]
    n = a if c == A else t
    return max(n, N3) if c == S and o != "vm.start" else n  # C15 : arrêter sys-net = N3


def _fait_dependre(op, ctx, agent, c):
    """R-D : un qube qui n'est pas A, ou un réglage global, s'appuierait sur un
    qube du registre (modèle, netvm, jetable par défaut)."""
    o, prop = op.op, op.get("prop")
    v = {"vm.set_template": op.get("template"), "vm.set_netvm": op.get("netvm")}.get(o)
    if (o in ("vm.set_prop", "vm.reset_prop") and prop == "default_dispvm") or (
            o == "prefs.set" and prop != "check_updates_vm"):
        v = _valeur(op, ctx)
    return v is not None and v in agent and (o == "prefs.set" or c != A)


def _demarre(op, ctx):
    """Qubes que l'opération ferait démarrer (R-G)."""
    o = op.op
    if o in ("vm.start", "vm.restart", "pkg.install", "pkg.remove"):
        return ctx.chaine(op.target)
    if o == "update.run":  # cibles démarrées ; -r/-R redémarrent les qubes qui en dépendent
        t = set(op.get("targets"))
        for _ in range(2 if op.get("apply") != "none" else 0):  # 2 : DispVM d'un modèle de jetable (sys-usb du Legion)
            t |= {n for n, i in ctx.qubes.items() if i.get("template") in t}
        return t
    if o == "template.available":  # téléchargement par l'updatevm
        return ctx.chaine(ctx.prefs.get("updatevm")) | {op.get("template")}
    if o in ("file.copy", "file.move"):
        return ctx.chaine(op.get("dest"))  # qrexec démarre la destination
    if o == "file.open_dispvm":
        return ctx.chaine(ctx.info(op.target, "default_dispvm"))
    if o == "dev.attach":
        return ctx.chaine(op.get("backend")) | ctx.chaine(op.get("target"))
    if o == "backup.run":
        p = ctx.profils.get(op.get("profile")) or {}
        return ctx.chaine(p.get("destination_vm")) | ctx.chaine(p.get("passphrase_vm"))
    if o in ("dispvm.run_task", "dispvm.create"):
        return ctx.chaine(op.get("template") or schema.MODELE_JETABLE)
    if o == "vm.set_prop" and op.get("prop") == "autostart" and op.get("value") is True:
        return ctx.chaine(op.target)  # au démarrage de Qubes, sa netvm démarre avec lui
    return set()


def _bail_admis(nom, ctx, agent):
    """Un bail ne vaut que si enter.open serait encore admis, classes et
    configuration relues à chaque plan (V7, D15, R-G) : un qube devenu P, S,
    M(S/C), interdit de démarrage ou coffre non entrable perd l'automatisme."""
    c = ctx.classe(nom, agent)
    return not (c in (P, S) or nom in ctx.config.ne_jamais_demarrer or (c == M and {S, C} & ctx.appuis(nom, agent))
                or (c == C and nom not in ctx.config.coffres_entrables))


def niveau_op(op, ctx, agent=frozenset()):
    """(niveau, plancher) d'une opération ; le plancher est la décision minimale,
    quels que soient la provenance et le mode."""
    if op.op in LECTURES:  # R-G vaut aussi pour une lecture qui démarre l'updatevm
        return (N4, REFUSER) if _demarre(op, ctx) & ctx.config.ne_jamais_demarrer else (N0, AUTO)
    if op.op in ENTRER and op.target in ctx.baux and _bail_admis(op.target, ctx, agent):
        return N0, AUTO  # §3.6 : sous bail, automatique sur ce seul qube (portée comprise)
    champs = champs_qube(op, ctx)
    if any(q in ctx.proteges and not _protege_admis(op, k, q, ctx) for k, q in champs):
        return N4, REFUSER  # R-A : un qube P nommé n'importe où, valeur comprise
    if any(k in ("template", "netvm", "value") and q in ctx.config.coffres for k, q in champs):
        return N4, REFUSER  # un coffre n'est ni modèle, ni réseau, ni jetable d'un autre qube
    cible = _cible(op)
    c = ctx.classe(cible, agent) if cible else None
    n, plancher = _niveau(op, ctx, agent, c), AUTO
    appuis = ctx.appuis(cible, agent) if cible else set()
    if c == M and op.op not in ("vm.start", "update.run") and S in appuis:
        n = max(n, N3)  # §3.2 : modèle d'un qube système (sys-net…) = N3
    if (any(q in ctx.config.coffres for _, q in champs)  # R-A : coffre nommé n'importe où
            or (op.op in ENTRER | {"enter.open"} and c != A)  # V6 : l'écran part chez le fournisseur (D14)
            or (op.op in _CONTENU and C in appuis)  # modèle d'un coffre
            or (op.op == "pkg.remove" and S in appuis)
            or op.op == "backup.run"  # lit tous les qubes du profil, coffres compris : jamais levé par Pilote
            or (op.op == "prefs.set" and op.get("prop") == "default_dispvm" and {P, C} & ctx.heritiers(agent))):
        n, plancher = max(n, N3), CONFIRMER
    if ((op.op in ("enter.open", "file.copy", "file.move") and c == C)  # D15, source coffre
            or (op.op == "dev.attach" and op.get("class") == "block" and c == A
                and ctx.classe(op.get("backend"), agent) != A)):  # les données de l'utilisateur vont chez l'agent
        plancher = CONFIRMER_RENFORCE
    if (op.op == "update.run" and any(C in ctx.appuis(t, agent) or ctx.info(t, "livre") for t in op.get("targets"))) or (
            op.op in _CONTENU and ctx.info(cible, "livre")):  # n17 : écrire dans un modèle livré par Qubes
        plancher = _plus_strict(plancher, CONFIRMER)  # M(C) 2✓ : modèle d'un coffre, toujours confirmé
    if op.op == "vm.clone" and _clone_sensible(cible, ctx, agent):
        n, plancher = max(n, N3), _plus_strict(plancher, CONFIRMER)  # dépendants de la source (revue du 25/10)
    if _fait_dependre(op, ctx, agent, c):
        n, plancher = (N4 if c == C else max(n, N3)), CONFIRMER_RENFORCE
    if _demarre(op, ctx) & ctx.config.ne_jamais_demarrer:
        n = N4
    return n, REFUSER if n == N4 else plancher


def motif_n4(plan, ctx=None, agent=frozenset()):
    """Message de refus N4 pour l'agent (validateur et exécuteur), avec la
    raison connue d'une opération toujours refusée. Sans code (N4) : l'agent
    le répète à l'utilisateur (PARCOURS-1 M8) ; le code reste au journal. n16 : pour
    une copie de modèle, les qubes qui en dépendent, pour qu'il en choisisse un autre."""
    motifs = [MOTIFS_N4[o.op] for o in plan.ops if o.op in MOTIFS_N4]
    for o in (o for o in plan.ops if o.op == "vm.clone" and ctx is not None):
        d = sorted((q for q in ctx.descendance(o.get("source")) if ctx.classe(q, agent) not in (A, T)),
                   key=lambda q: (ctx.info(q, "template") != o.get("source"), q))  # basés dessus d'abord
        if d:
            motifs.append(f"le modèle {o.get('source')} sert à {', '.join(d[:6])}{'…' if len(d) > 6 else ''} : "
                          "copie un autre modèle, dont ne dépendent que des espaces ordinaires")
    return ("le cœur du système refuse cette action à l'agent, dans tous les modes : "
            + (" ; ".join(dict.fromkeys(motifs)) or "explique à l'utilisateur ce qu'il faudrait faire et pourquoi"))


def etiquette_heritee(nom, ctx):
    """R-6 (S-J2) : un jetable hérite des étiquettes de son modèle, et un clone
    de celles de sa source avant nettoyage. Vrai si nom porte qaf-agent, ou une
    étiquette (hors celles que Qubes pose partout) citée par une politique ;
    politiques illisibles : toute étiquette compte (fail-closed)."""
    tags = {t for t in ctx.info(nom, "tags") or () if not t.startswith(_TAGS_QUBES)}
    return schema.TAG_AGENT in (ctx.info(nom, "tags") or ()) or bool(
        tags and (ctx.tags_politique is None or tags & ctx.tags_politique))


def modeles_agent(portee, ctx, agent):
    """n1 : le modèle de chaque qube de la portée, s'il est A et que toute sa
    descendance (à toute profondeur) est A ; un modèle qui porte un qube de
    l'utilisateur reste à nommer."""
    return {t for n in portee if (t := ctx.info(n, "template")) and ctx.classe(t, agent) == A
            and all(ctx.classe(d, agent) == A for d in ctx.descendance(t))}


def _clone_sensible(source, ctx, agent):
    """vm.clone : la source a, à une profondeur quelconque, un dépendant qui
    n'est ni A ni T (P, S, C, ou M dans le doute) : l'agent lirait son système."""
    return bool({ctx.classe(d, agent) for d in ctx.descendance(source)} - {A, T})


def _implicite(op, champ, q, ctx, agent):
    """C13 bis : vrai si ce qube n'a pas à être nommé dans la demande."""
    if champ in _CREES.get(op.op, ()):
        return True
    if q in ctx.proteges or q in ctx.config.coffres or q in ctx.config.systeme:
        return False
    if (op.op, champ) == ("vm.set_netvm", "netvm"):
        return q == ctx.prefs.get("default_netvm")
    if (op.op, champ) == ("vm.clone", "source"):
        return not _clone_sensible(q, ctx, agent)
    if (op.op, champ) == ("vm.set_template", "template"):
        return q in agent or q == ctx.prefs.get("default_template")
    return False


def decision(niveau, influence, mode):
    """Décision pour un niveau, selon la provenance et le mode."""
    if niveau == N4:
        return REFUSER  # l'agent rédige un diff, l'utilisateur l'applique
    if niveau <= N1:
        return AUTO  # TODO quotas en session influencée (§2, ligne N1)
    if mode == PILOTE:
        # Le mode Pilote lève les confirmations de ce qui vient des demandes de
        # l'utilisateur, jamais celles d'une session influencée (§2 point 6, D5) : un
        # N2 influencé peut donner le réseau à un qube de l'agent (C2).
        if not influence:
            return AUTO
        return CONFIRMER_RENFORCE if niveau == N3 else CONFIRMER
    if niveau == N2:
        return CONFIRMER if influence else AUTO
    return CONFIRMER_RENFORCE if influence else CONFIRMER


def classer(plan, *, agent_qubes, existing, influence, mode=NORMAL, coffres=COFFRES_DEFAUT, portee=None,
            jetables=JETABLES_DEFAUT, ctx=None, etrangers=frozenset()):
    """Classe un plan entier ; la décision est la plus stricte des opérations.

    Un qube créé plus tôt dans le même plan compte comme qube de l'agent (et
    entre dans la portée), seulement si son nom n'existait pas déjà (sinon la
    création échouera et l'exécuteur doit s'arrêter à la première erreur).
    Sans ctx (tests anciens) : contexte réduit aux noms existants.
    """
    # portee : noms de qubes couverts par la demande, None = pas de contrôle.
    # etrangers : qubes A créés par un AUTRE jetable d'agent (registre, R-9).
    if mode not in (NORMAL, PILOTE):
        raise ValueError(f"mode inconnu : {mode!r}")
    if ctx is None:
        ctx = Contexte({n: {} for n in existing}, {}, Config(coffres=frozenset(coffres), jetables=frozenset(jetables)))
    a_agent = set(agent_qubes)
    portee = None if portee is None else set(portee)
    niveaux, decisions, hors, non_nommes = [], [], [], {}
    for i, op in enumerate(plan.ops):
        n, plancher = niveau_op(op, ctx, a_agent)
        d = _plus_strict(decision(n, influence, mode), plancher)
        vises = [q for k, q in champs_qube(op, ctx) if not _implicite(op, k, q, ctx, a_agent)]
        manquants = [q for q in vises if portee is not None and q not in portee]
        if n > N0 and manquants:  # journalisé (hors_portee) dès N1
            hors.append(i)
            non_nommes.update(dict.fromkeys(manquants))
            if n >= N2:  # n2 (a) : le plancher de portée ne vaut qu'à partir de N2
                d = _plus_strict(d, CONFIRMER_RENFORCE)
            elif op.op in SANS_RETOUR and _cible(op) in etrangers:
                d = _plus_strict(d, CONFIRMER)  # R-9 : l'utilisateur a pu y mettre ses fichiers
        niveaux.append(n)
        decisions.append(d)
        if op.op in _CREES and op.target not in existing:
            a_agent.add(op.target)
            if portee is not None:
                portee.add(op.target)
    pire = max(decisions, key=_ORDRE.index)
    return Classement(tuple(niveaux), pire, tuple(hors), tuple(non_nommes))
