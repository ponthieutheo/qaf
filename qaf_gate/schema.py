"""Format FERMÉ des plans envoyés par sys-agent à qaf.Plan.

Un plan est un objet JSON :
    {"version": 1, "request": "R-0142", "ops": [{"op": "vm.start", "name": "dev"}]}
avec, facultatif, "session": "S-<16 hexadécimaux>", identifiant de la session
de l'agent tiré par qaf_mcp (C13 : la demande de la palette est liée à la
première session qui la cite). Il n'autorise rien : il ne fait que restreindre.

Tout champ inconnu, manquant, en double ou mal typé = refus (PlanError).
Le Doorman ne travaille ensuite que sur le plan ré-sérialisé par canonicalize().
"""

import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass

VERSION = 1
MAX_BYTES = 64 * 1024  # C5 : entrée bornée
MAX_OPS = 16
MAX_TASK_CHARS = 1000  # C2 : champ texte court, pas de stdin libre

# Labels par défaut de Qubes (qubes-core-admin, qubes/app.py, load_initial_values).
LABELS = ("red", "orange", "yellow", "green", "gray", "blue", "purple", "black")

# Règles de qubes-core-admin, qubes/vm/__init__.py, validate_name().
_NAME_RE = re.compile(r"\A[a-zA-Z][a-zA-Z0-9_.-]*\Z")
_RESERVED = ("Domain-0", "Dom0", "none", "default")
_REQUEST_RE = re.compile(r"\AR-[0-9]{4,8}\Z")
SESSION_RE = re.compile(r"\AS-[0-9a-f]{16}\Z")
# Catégories Unicode refusées dans un texte (sauf « \n ») : Cc contrôle, Cf
# format (dont inversions bidi), Cs surrogats isolés, Zl/Zp séparateurs de
# ligne et de paragraphe (U+2028/U+2029), Co usage privé, Cn non assigné.
TASK_INTERDITS = ("Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn")
_CLE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _court(k):
    """Clé choisie par l'agent, réduite pour un message d'erreur : 32 caractères
    ASCII sûrs au plus (les messages partent au journal et vers l'affichage)."""
    return repr(_CLE_RE.sub("?", str(k)[:32]))


class PlanError(ValueError):
    """Plan refusé par le schéma."""


def _qube(f):
    """Marque un validateur de champ de type qube : il entre dans CHAMPS_QUBE
    (portée C13, contexte de la fenêtre, classes de cible : R-A)."""
    f.qube = True
    return f


@_qube
def _name(v, champ):
    if not isinstance(v, str) or len(v) > 31 or not _NAME_RE.match(v):
        raise PlanError(f"{champ} : nom de qube invalide")
    if v in _RESERVED or v.endswith("-dm"):
        raise PlanError(f"{champ} : nom de qube réservé")
    return v


@_qube
def _name_or_none(v, champ):
    return None if v is None else _name(v, champ)


def _label(v, champ):
    if v not in LABELS:
        raise PlanError(f"{champ} : label inconnu")
    return v


def _texte(n):
    def f(v, champ):
        if not isinstance(v, str) or not 1 <= len(v) <= n:
            raise PlanError(f"{champ} : texte absent ou trop long")
        if any(c != "\n" and unicodedata.category(c) in TASK_INTERDITS for c in v):
            raise PlanError(f"{champ} : caractère de contrôle interdit")
        return v
    return f


_task = _texte(MAX_TASK_CHARS)


def _bool(v, champ):
    if type(v) is not bool:
        raise PlanError(f"{champ} : booléen attendu")
    return v


def _entier(a, b):
    def f(v, champ):
        if type(v) is not int or not a <= v <= b:
            raise PlanError(f"{champ} : entier de {a} à {b} attendu")
        return v
    return f


def _choix(*valeurs):
    def f(v, champ):
        if not isinstance(v, str) or v not in valeurs:
            raise PlanError(f"{champ} : valeur non admise")
        return v
    return f


def _motif(regex, quoi):
    r = re.compile(regex)

    def f(v, champ):
        if not isinstance(v, str) or not r.match(v):
            raise PlanError(f"{champ} : {quoi} invalide")
        return v
    return f


def _noms(a, b):
    @_qube
    def f(v, champ):
        if not isinstance(v, list) or not a <= len(v) <= b:
            raise PlanError(f"{champ} : liste de {a} à {b} noms attendue")
        noms = [_name(x, champ) for x in v]
        if len(set(noms)) != len(noms):
            raise PlanError(f"{champ} : nom en double")
        return noms
    return f


# Paquet : ni fichier local, ni version, ni groupe, ni joker, et rien d'autre
# qu'un alphanumérique en fin de nom (V11 : pour apt-get, « nom- » veut dire
# retirer et « nom+ » installer). Noms finissant par + : seulement ceux-ci.
_PAQUET_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9+._-]{0,61}[A-Za-z0-9])?\Z")
PAQUETS_PLUS = ("g++", "c++", "gcc-c++")


def _paquets(v, champ):
    if not isinstance(v, list) or not 1 <= len(v) <= 8:
        raise PlanError(f"{champ} : liste de 1 à 8 paquets attendue")
    for p in v:
        if not isinstance(p, str) or not (_PAQUET_RE.match(p) or p in PAQUETS_PLUS):
            raise PlanError(f"{champ} : nom de paquet invalide")
    if len(set(v)) != len(v):
        raise PlanError(f"{champ} : paquet en double")
    return list(v)


def _argv(v, champ):
    # Mêmes bornes que qaf_hand (service.executer).
    if (not isinstance(v, list) or not 1 <= len(v) <= 64
            or not all(isinstance(a, str) and len(a) <= 4096 and "\0" not in a for a in v)
            or not v[0] or sum(len(a) for a in v) > 8192):
        raise PlanError(f"{champ} : liste de 1 à 64 arguments, 8 Kio au plus, sans octet nul")
    return list(v)


_fenetre = _motif(r"\A(racine|0x[0-9a-fA-F]{1,8})\Z", "fenêtre")


def _chemin(v, champ):
    """Relatif au dossier personnel : composants non vides qui commencent par
    une lettre, un chiffre ou _ (ni .., ni - en tête, ni fichier caché)."""
    if (not isinstance(v, str) or not 1 <= len(v) <= 255
            or any(unicodedata.category(c) in TASK_INTERDITS for c in v)  # NUL et saut de ligne compris
            or not all(p and (p[0].isalnum() or p[0] == "_") for p in v.split("/"))):
        raise PlanError(f"{champ} : chemin relatif au dossier personnel invalide")
    return v


def _dev(n, quoi):
    # P-6/P-13 : « * », « _ » seul et le vide sont des jokers de l'API des
    # périphériques de 4.3 ; ils délieraient la confirmation du périphérique vu.
    r = re.compile(r"\A[A-Za-z0-9_.:-]{1,%d}\Z" % n)

    def f(v, champ):
        if not isinstance(v, str) or v == "_" or not r.match(v):
            raise PlanError(f"{champ} : {quoi} invalide")
        return v
    return f


_port_dev, _id_dev = _dev(64, "port"), _dev(256, "identifiant de périphérique")
_job = _motif(r"\AT-[0-9]{6}\Z", "travail")
_profil = _motif(r"\A[A-Za-z0-9_-]{1,64}\Z", "profil")
_REPO = _choix("official", "community")
_CLASSE_DEV = _choix("usb", "block", "mic")
_LABEL_DNS = re.compile(r"\A[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_PORTS_RE = re.compile(r"\A([0-9]{1,5})(?:-([0-9]{1,5}))?\Z")


def _hote(v, champ):
    if isinstance(v, str) and len(v) <= 253:
        try:
            ipaddress.ip_network(v, strict=True)
            return v
        except ValueError:
            pass
        if all(_LABEL_DNS.match(p) for p in v.split(".")):
            return v
    raise PlanError(f"{champ} : adresse, réseau ou nom DNS invalide")


def _regle_pf(v, champ):
    """Règle de pare-feu FERMÉE (OPS-v3 §6, V11) : clé inconnue = refus."""
    if not isinstance(v, dict) or v.get("action") not in ("accept", "drop"):
        raise PlanError(f"{champ} : règle invalide (action accept ou drop)")
    cles = set(v) - {"action"}
    if "dns" in v:
        if cles != {"dns"} or v["dns"] is not True:
            raise PlanError(f"{champ} : dns: true exclut host, proto et ports")
        return dict(v)
    if not cles <= {"host", "proto", "ports"}:
        raise PlanError(f"{champ} : champ de règle inconnu")
    if "host" in v:
        _hote(v["host"], champ)
    if "proto" in v and v["proto"] not in ("tcp", "udp", "icmp"):
        raise PlanError(f"{champ} : proto tcp, udp ou icmp")
    if "ports" in v:
        m = _PORTS_RE.match(v["ports"]) if isinstance(v["ports"], str) else None
        bas, haut = (int(m[1]), int(m[2] or m[1])) if m else (0, 0)
        if v.get("proto") not in ("tcp", "udp") or not 1 <= bas <= haut <= 65535:
            raise PlanError(f"{champ} : ports de 1 à 65535, avec tcp ou udp seulement")
    return dict(v)


# Propriétés réglables d'un qube (liste blanche fermée, OPS-v3 §5.2) et
# globales (§9). Le validateur de « value » est choisi par « prop » (R-B).
PROPS = {
    "label": _label,
    "memory": _entier(256, 65536),
    "maxmem": _entier(0, 65536),
    "vcpus": _entier(1, 64),
    "autostart": _bool,
    "include_in_backups": _bool,
    "keyboard_layout": _motif(r"\A[a-z]{2,8}\+[A-Za-z0-9_-]{0,16}\+[A-Za-z0-9_,:-]{0,32}\Z", "disposition"),
    "shutdown_timeout": _entier(10, 3600),
    "qrexec_timeout": _entier(10, 3600),
    "default_dispvm": _name_or_none,
    "template_for_dispvms": _bool,
    "provides_network": _bool,
}
PREFS = {"default_template": _name, "default_dispvm": _name_or_none, "check_updates_vm": _bool}
SELON_PROP = None  # validateur de « value » lu dans VALEURS[op][prop]
VALEURS = {"vm.set_prop": PROPS, "prefs.set": PREFS}
SERVICES = ("crond", "cups", "qubes-update-check", "network-manager")


def valeur_qube(op, prop):
    """Vrai si « value » de cette opération désigne un qube (R-B)."""
    return getattr(VALEURS.get(op, {}).get(prop), "qube", False)


# Opération -> (champs obligatoires, champs facultatifs), chacun avec son validateur.
# vm.create n'a PAS de champ netvm : un qube créé par l'agent n'a jamais de
# réseau (C2) ; le donner est une opération séparée vm.set_netvm.
# vm.set_netvm : netvm null = aucun réseau.
# dispvm.run_task : template absent = modèle de jetable dédié du Doorman
# (MODELE_JETABLE), jamais default_dispvm.
MODELE_JETABLE = "qaf-dvm"  # créé par tools/setup-qaf-dvm.sh, sans réseau
# Agent jetable (docs/AGENT-JETABLE.md) : modèle de jetables des agents, créé par
# tools/setup-agent-dvm.sh ; jamais modèle de dispvm.create ni default_dispvm (R-3).
MODELE_AGENT = "qaf-agent-dvm"
MODELES_AGENT = frozenset({MODELE_AGENT})
TAG_AGENT = "qaf-agent"  # posée par l'exécuteur seul, après la création, avant le démarrage
AGENT_RE = re.compile(r"\Adisp[0-9]{1,4}\Z")  # nom d'un jetable sans nom (qubes/app.py)
CLIS = ("claude",)  # CLI lancé par qaf.Request (§12), argv fixe dans qaf_mcp/demande.py
_NOM = {"name": _name}
_PF = {"name": _name, "position": _entier(0, 63), "rule": _regle_pf}
_FICHIER = {"source": _name, "path": _chemin, "dest": _name}
OPS = {
    "vm.list": ({}, {}),
    "vm.info": (_NOM, {}),
    "vm.create": ({"name": _name, "template": _name, "label": _label}, {}),
    # Copie d'un modèle (TemplateVM) en modèle de l'agent, sans réseau : name =
    # le nouveau modèle, inscrit au registre (classe A, PARCOURS-1 B2).
    "vm.clone": ({"name": _name, "source": _name, "label": _label}, {}),
    "vm.start": (_NOM, {}),
    "vm.shutdown": (_NOM, {}),
    "vm.remove": (_NOM, {}),
    "vm.set_netvm": ({"name": _name, "netvm": _name_or_none}, {}),
    "dispvm.run_task": ({"task": _task}, {"template": _name}),
    # OPS-v3, lot 1 : voir, régler, entrer chez soi.
    "vm.settings": (_NOM, {}),
    "update.status": ({}, {"targets": _noms(0, 64)}),
    "sys.status": ({}, {}),
    "prefs.get": ({}, {}),
    "fw.list": (_NOM, {}),
    "dev.list": ({}, {"class": _CLASSE_DEV}),
    "vm.set_prop": ({"name": _name, "prop": _choix(*PROPS), "value": SELON_PROP}, {}),
    "vm.reset_prop": ({"name": _name, "prop": _choix(*PROPS)}, {}),
    "vm.set_service": ({"name": _name, "service": _choix(*SERVICES),
                        "state": _choix("on", "off", "default")}, {}),
    "vm.restart": (_NOM, {}),
    "vm.set_template": ({"name": _name, "template": _name}, {}),
    "pkg.install": ({"name": _name, "packages": _paquets}, {}),
    "dispvm.create": ({}, {"template": _name}),
    "qube.exec": ({"name": _name, "argv": _argv, "timeout": _entier(1, 120)}, {}),
    "qube.windows": ({"name": _name, "hidden": _bool}, {}),
    "qube.screenshot": ({"name": _name, "window": _fenetre}, {}),
    "qube.type": ({"name": _name, "text": _texte(2000)}, {"window": _fenetre}),
    "qube.key": ({"name": _name, "keys": _motif(r"\A[A-Za-z0-9_]{1,24}(\+[A-Za-z0-9_]{1,24}){0,4}\Z", "touches")},
                 {"window": _fenetre}),
    "qube.mouse": ({"name": _name, "x": _entier(0, 16383), "y": _entier(0, 16383), "button": _entier(0, 3)},
                   {"window": _fenetre}),
    "qube.scroll": ({"name": _name, "direction": _choix("haut", "bas"), "steps": _entier(1, 20)}, {}),
    # Lot 2, sans décision en attente : préférences globales, pare-feu.
    "prefs.set": ({"prop": _choix(*PREFS), "value": SELON_PROP}, {}),
    "fw.add": (_PF, {}),
    "fw.remove": (_PF, {}),
    # Lot 2 (D13 à D17) : outils de Qubes (qaf-gate-outils), bail, fichiers.
    "update.run": ({"targets": _noms(1, 32), "apply": _choix("none", "sys", "all")}, {}),
    "template.available": ({"repo": _REPO}, {}),
    "template.install": ({"template": _name, "repo": _REPO}, {}),
    "job.status": ({"job": _job}, {}),
    "job.cancel": ({"job": _job}, {}),
    "enter.open": ({"name": _name, "minutes": _entier(1, 60)}, {}),
    "enter.close": (_NOM, {}),
    "file.copy": (_FICHIER, {}),
    "file.open_dispvm": ({"name": _name, "path": _chemin}, {}),
    # Lot 3 : isolation forte et irréversible.
    "dev.attach": ({"class": _CLASSE_DEV, "backend": _name, "port": _port_dev, "device_id": _id_dev,
                    "target": _name}, {"read_only": _bool}),
    "dev.detach": ({"class": _CLASSE_DEV, "backend": _name, "port": _port_dev, "target": _name}, {}),
    "backup.profiles": ({}, {}),
    "backup.run": ({"profile": _profil}, {}),
    "pkg.remove": ({"name": _name, "packages": _paquets}, {}),
    "template.remove": ({"template": _name}, {}),
    "vm.tag": ({"name": _name, "tag": _motif(r"\A[a-z][a-z0-9_-]{0,30}\Z", "étiquette"),
                "action": _choix("add", "remove")}, {}),
    "vm.grow_private": ({"name": _name, "size_mib": _entier(1024, 1048576)}, {}),
    "file.move": (_FICHIER, {}),
}
# Au plus une de ces opérations par plan (§3.4) : la réponse reste sous
# proto.MAX_MESSAGE (sorties de qube) et la durée du plan bornée (pkg.install).
LOURDES = frozenset({"dev.list", "qube.exec", "qube.windows", "qube.screenshot", "pkg.install", "pkg.remove",
                     "template.available", "file.copy", "file.move", "vm.clone"})

# Champs de type qube de chaque opération, tirés des validateurs ci-dessus :
# une opération future est couverte d'office (portée C13, contexte de la fenêtre).
CHAMPS_QUBE = {
    op: tuple(sorted(k for k, f in {**req, **opt}.items() if getattr(f, "qube", False)))
    for op, (req, opt) in OPS.items()
}


@dataclass(frozen=True)
class Op:
    op: str
    args: tuple  # paires (champ, valeur) triées : immuable et comparable

    def get(self, champ, defaut=None):
        return dict(self.args).get(champ, defaut)

    def champs_qube(self):
        """Paires (champ, qube) de tous les champs qui désignent un qube : listes
        aplaties, « value » compris quand la propriété est de type qube (R-A, R-B)."""
        d = dict(self.args)
        champs = CHAMPS_QUBE[self.op] + (("value",) if valeur_qube(self.op, d.get("prop")) else ())
        return tuple((k, q) for k in champs for q in (d[k] if isinstance(d.get(k), list) else [d.get(k)])
                     if q is not None)

    def qubes(self):
        """Qubes nommés par l'opération (champs de type qube non nuls)."""
        return tuple(q for _, q in self.champs_qube())

    @property
    def target(self):
        """Qube visé par l'opération (None pour vm.list et dispvm.run_task)."""
        return self.get("name")


@dataclass(frozen=True)
class Plan:
    version: int
    request: str
    ops: tuple
    session: str | None = None  # session de l'agent (qaf_mcp), None = non transmise


def _no_dup(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise PlanError(f"clé en double : {_court(k)}")
        d[k] = v
    return d


def _no_const(c):
    raise PlanError(f"constante JSON interdite : {c}")


def _exact_keys(obj, required, optional, ou):
    if not isinstance(obj, dict):
        raise PlanError(f"{ou} : objet attendu")
    keys = set(obj)
    missing = set(required) - keys
    unknown = keys - set(required) - set(optional)
    if missing:
        raise PlanError(f"{ou} : champ manquant {sorted(missing)}")
    if unknown:
        noms = ", ".join(_court(k) for k in sorted(unknown)[:3])
        raise PlanError(f"{ou} : champ inconnu {noms}{' …' if len(unknown) > 3 else ''}")


def _parse_op(raw, i):
    # isinstance d'abord : « [] in OPS » lèverait TypeError (trouvé par le fuzzing).
    if not isinstance(raw, dict) or not isinstance(raw.get("op"), str) or raw["op"] not in OPS:
        raise PlanError(f"ops[{i}] : opération inconnue")
    required, optional = OPS[raw["op"]]
    _exact_keys(raw, {"op", *required}, optional, f"ops[{i}]")
    checks = {**required, **optional}
    if raw["op"] in VALEURS:  # R-B : le type de value dépend de prop, validé d'abord
        checks["value"] = VALEURS[raw["op"]][checks["prop"](raw["prop"], f"ops[{i}].prop")]
    args = tuple(sorted(
        (k, checks[k](v, f"ops[{i}].{k}")) for k, v in raw.items() if k != "op"
    ))
    return Op(raw["op"], args)


def parse_plan(data: bytes) -> Plan:
    """Valide des octets bruts et renvoie un Plan, ou lève PlanError."""
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise PlanError("entrée absente ou trop grande")
    try:
        text = data.decode("utf-8")
        raw = json.loads(text, object_pairs_hook=_no_dup, parse_constant=_no_const)
    except PlanError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise PlanError(f"JSON invalide : {type(e).__name__}") from None
    _exact_keys(raw, {"version", "request", "ops"}, ("session",), "plan")
    if type(raw["version"]) is not int or raw["version"] != VERSION:
        raise PlanError("version non prise en charge")
    if not isinstance(raw["request"], str) or not _REQUEST_RE.match(raw["request"]):
        raise PlanError("identifiant de demande invalide (R-xxxx)")
    session = raw.get("session")
    if session is not None and (not isinstance(session, str) or not SESSION_RE.match(session)):
        raise PlanError("identifiant de session invalide (S- et 16 hexadécimaux)")
    ops = raw["ops"]
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
        raise PlanError(f"ops : liste de 1 à {MAX_OPS} opérations attendue")
    plan = Plan(VERSION, raw["request"], tuple(_parse_op(o, i) for i, o in enumerate(ops)), session)
    if sum(o.op in LOURDES for o in plan.ops) > 1:
        raise PlanError("une seule opération lourde par plan (lecture d'un qube, paquets, copie, clone de modèle)")
    return plan


def to_dict(plan: Plan) -> dict:
    d = {
        "version": plan.version,
        "request": plan.request,
        "ops": [{"op": o.op, **dict(o.args)} for o in plan.ops],
    }
    if plan.session is not None:
        d["session"] = plan.session
    return d


def canonicalize(plan: Plan) -> bytes:
    """Ré-sérialisation déterministe (clés triées, sans espaces, UTF-8)."""
    return json.dumps(
        to_dict(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
