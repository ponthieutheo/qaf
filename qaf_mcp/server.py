"""Serveur MCP (stdio) de sys-agent : traduit les outils de l'agent en plans.

Chaque appel d'outil construit UN plan au format fermé de qaf_gate/schema.py,
le valide localement (retour immédiat à l'agent), puis l'envoie au Doorman :

    qrexec-client-vm dom0 qaf.Plan      (plan sur stdin, réponse sur stdout)

Le serveur n'a aucun droit : c'est le Doorman qui décide. Il ne fait que
mettre en forme. Transport injectable pour les tests (voir FakeTransport).

Protocole : JSON-RPC 2.0, un message JSON par ligne (transport stdio de MCP),
méthodes initialize, ping, tools/list, tools/call.
"""

import base64
import binascii
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time
import unicodedata

from qaf_gate.proto import DELAI_AGENT, FAIT, STATUTS
from qaf_gate.schema import LABELS, OPS, PREFS, PROPS, SERVICES, PlanError, canonicalize, parse_plan

from . import png_propre

NOM_SERVEUR = "qaf"
VERSION_SERVEUR = "0.1.0"
# Versions du protocole MCP connues ; on renvoie celle du client si on la connaît.
VERSIONS_MCP = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

QREXEC_CLIENT_VM = "/usr/bin/qrexec-client-vm"  # qubesadmin/config.py
# Codes de sortie de qrexec-client-vm (qubes-core-qrexec, libqrexec/qrexec.h).
EXIT_REFUSE, EXIT_INTROUVABLE, EXIT_PROBLEME = 126, 127, 125

MAX_LIGNE = 256 * 1024  # message JSON-RPC entrant
MAX_REPONSE = 8000  # caractères de réponse du Doorman rendus à l'agent
MAX_SORTIE_QUBE = 64000  # idem quand la réponse porte une sortie de qube (qube_exec…)
# Une confirmation attend l'utilisateur. Jamais moins que DELAI_AGENT : abandonner avant
# le Doorman laisserait un plan s'exécuter sans que personne en lise le résultat.
DELAI = max(float(os.environ.get("QAF_DELAI", DELAI_AGENT)), DELAI_AGENT)

# Identifiant de la demande courante (C13) : le service qaf.Request
# (qaf_mcp/demande.py) lance claude avec QAF_REQUEST=R-xxxxxx quand la palette de
# dom0 transmet une demande. Lu dans l'environnement de ce processus, sinon
# dans celui de son parent (claude ne transmet pas forcément tout son
# environnement aux serveurs MCP). Plus de fichier commun : une session
# ouverte sans la palette n'hérite jamais de la demande d'une autre
# (PARCOURS-1 M3). Sans demande : R-0000, qu'aucune demande ne porte.
VARIABLE_DEMANDE = "QAF_REQUEST"
SANS_DEMANDE = "R-0000"
# Copie des captures d'écran dans sys-agent, pour les ouvrir dans une
# visionneuse quand l'utilisateur veut les voir (PARCOURS-1 M5).
VARIABLE_CAPTURES = "QAF_CAPTURES"
MAX_CAPTURES = 50  # les plus anciennes sont effacées au-delà


# ---------------------------------------------------------------- transports

class QrexecTransport:
    """Envoie le plan à dom0 par qrexec. Renvoie (code, stdout, stderr)."""

    def __init__(self, binaire=QREXEC_CLIENT_VM, delai=DELAI):
        self.binaire = binaire
        self.delai = delai

    def envoyer(self, plan: bytes):
        try:
            r = subprocess.run(
                [self.binaire, "dom0", "qaf.Plan"],
                input=plan, capture_output=True, timeout=self.delai,
            )
        except FileNotFoundError:
            return EXIT_PROBLEME, b"", b"qrexec-client-vm introuvable (pas dans un qube ?)"
        except subprocess.TimeoutExpired:
            return EXIT_PROBLEME, b"", f"pas de reponse du coeur du systeme en {self.delai:.0f} s".encode()
        return r.returncode, r.stdout, r.stderr


class FakeTransport:
    """Transport de test : enregistre les plans et renvoie une réponse fixe."""

    def __init__(self, reponse=b'{"statut":"fait"}', code=0, stderr=b""):
        self.plans = []
        self.reponse, self.code, self.stderr = reponse, code, stderr

    def envoyer(self, plan: bytes):
        self.plans.append(json.loads(plan))
        rep = self.reponse(plan) if callable(self.reponse) else self.reponse
        return self.code, rep, self.stderr


# ------------------------------------------------------------------- outils

_NOM = {"type": "string", "description": "Nom du qube (lettre puis lettres, chiffres, _ . -)", "maxLength": 31}

OUTILS = [
    {
        "name": "qubes_list",
        "description": ("Liste TOUS les qubes, un par ligne : nom, état, sorte (AppVM, TemplateVM…), couleur, "
                        "modèle et réseau. Lecture seule. La liste est complète : montre-les tous à l'utilisateur."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qube_info",
        "description": "Détails d'un qube. Lecture seule.",
        "inputSchema": {"type": "object", "properties": {"name": _NOM},
                        "required": ["name"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qube_create",
        "description": ("Crée une AppVM SANS réseau à partir d'un template. Pour lui donner "
                        "Internet ensuite : qube_set_network (action séparée, parfois confirmée)."),
        "inputSchema": {"type": "object", "properties": {
            "name": _NOM,
            "template": {**_NOM, "description": "Template de base, ex. debian-13-xfce"},
            "label": {"type": "string", "enum": list(LABELS), "default": "gray",
                      "description": "Couleur (niveau de confiance) ; gray par défaut"},
        }, "required": ["name", "template"], "additionalProperties": False},
    },
    {
        "name": "template_clone",
        "description": ("Copie un modèle (TemplateVM) en un nouveau modèle À TOI, sans réseau. Chemin par défaut "
                        "pour installer durablement un logiciel dans un espace que tu crées : clone le modèle, "
                        "installe dans le clone (packages_install, automatique), puis crée l'espace dessus "
                        "(qube_create) ou bascule ton espace dessus (qube_set_template). C'est le premier choix : "
                        "ne modifie pas un modèle de l'utilisateur sans qu'il le demande. Refusé pour un coffre ou un "
                        "modèle protégé."),
        "inputSchema": {"type": "object", "properties": {
            "name": {**_NOM, "description": "Nom du nouveau modèle, ex. tpl-agent-dev"},
            "source": {**_NOM, "description": "Modèle à copier ; de préférence le modèle par défaut de Qubes"},
            "label": {"type": "string", "enum": list(LABELS), "default": "gray"},
        }, "required": ["name", "source"], "additionalProperties": False},
    },
    {
        "name": "qube_start",
        "description": "Démarre un qube.",
        "inputSchema": {"type": "object", "properties": {"name": _NOM},
                        "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "qube_stop",
        "description": "Arrête proprement un qube (sans attendre la fin de l'arrêt).",
        "inputSchema": {"type": "object", "properties": {"name": _NOM},
                        "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "qube_remove",
        "description": "Supprime un qube arrêté. Un qube de l'utilisateur demande sa confirmation.",
        "inputSchema": {"type": "object", "properties": {"name": _NOM},
                        "required": ["name"], "additionalProperties": False},
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "qube_set_network",
        "description": ("Change le qube réseau (netvm) d'un qube. netvm = null coupe le réseau. "
                        "Donner le réseau est une action d'isolation : souvent confirmée par l'utilisateur."),
        "inputSchema": {"type": "object", "properties": {
            "name": _NOM,
            "netvm": {"type": ["string", "null"], "maxLength": 31,
                      "description": "ex. sys-firewall, ou null pour aucun réseau"},
        }, "required": ["name", "netvm"], "additionalProperties": False},
    },
    {
        "name": "disposable_task",
        "description": ("Confie une tâche courte (texte, 1000 caractères max) à un agent qui "
                        "tourne dans un qube jetable. Tu ne reçois qu'un compte rendu, jamais "
                        "les pixels ni le contenu brut."),
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "maxLength": 1000},
            "template": {**_NOM, "description": ("Modèle de jetable ; absent = qaf-dvm (sans réseau). "
                                                 "Un modèle hors de la liste de l'utilisateur est refusé.")},
        }, "required": ["task"], "additionalProperties": False},
    },
]


def _outil(nom, description, proprietes=None, requis=(), lecture=False):
    schema_ = {"type": "object", "properties": proprietes or {}, "additionalProperties": False}
    if requis:
        schema_["required"] = list(requis)
    o = {"name": nom, "description": description, "inputSchema": schema_}
    if lecture:
        o["annotations"] = {"readOnlyHint": True}
    return o


_QUBE = {"name": _NOM}
_ENTIER = {"type": "integer"}
_FENETRE = {"type": "string", "description": "« racine » (écran entier) ou identifiant 0x… lu par qube_windows"}
_REGLE = {"type": "object", "description": ("Règle fermée : {action: accept|drop, host?: adresse, réseau ou nom DNS, "
                                            "proto?: tcp|udp|icmp, ports?: \"443\" ou \"8000-8100\" (tcp/udp)} "
                                            "ou {action, dns: true}")}
_POSITION = {"type": "integer", "minimum": 0, "maximum": 63, "description": "0 = avant toutes les autres règles"}

# Outils d'OPS-v3 -> (opération, valeurs par défaut des champs facultatifs côté MCP).
OPS_OUTILS = {
    "qube_settings": ("vm.settings", {}), "updates_status": ("update.status", {}),
    "system_status": ("sys.status", {}), "global_settings": ("prefs.get", {}), "firewall_list": ("fw.list", {}),
    "devices_list": ("dev.list", {}), "qube_set_property": ("vm.set_prop", {}),
    "qube_reset_property": ("vm.reset_prop", {}), "qube_set_service": ("vm.set_service", {}),
    "qube_restart": ("vm.restart", {}), "qube_set_template": ("vm.set_template", {}),
    "packages_install": ("pkg.install", {}), "disposable_create": ("dispvm.create", {}),
    "qube_exec": ("qube.exec", {"timeout": 30}), "qube_windows": ("qube.windows", {"hidden": False}),
    "qube_screenshot": ("qube.screenshot", {"window": "racine"}), "qube_type": ("qube.type", {}),
    "qube_key": ("qube.key", {}), "qube_mouse": ("qube.mouse", {"button": 1}), "qube_scroll": ("qube.scroll", {}),
    "global_set_setting": ("prefs.set", {}), "firewall_add": ("fw.add", {}), "firewall_remove": ("fw.remove", {}),
    # Lots 2 et 3.
    "updates_run": ("update.run", {"apply": "none"}), "templates_available": ("template.available",
                                                                               {"repo": "official"}),
    "template_install": ("template.install", {"repo": "official"}), "template_remove": ("template.remove", {}),
    "job_status": ("job.status", {}), "job_cancel": ("job.cancel", {}),
    "qube_enter": ("enter.open", {"minutes": 30}), "qube_leave": ("enter.close", {}),
    "file_copy": ("file.copy", {}), "file_move": ("file.move", {}), "file_open_disposable": ("file.open_dispvm", {}),
    "device_attach": ("dev.attach", {}), "device_detach": ("dev.detach", {}),
    "backup_profiles": ("backup.profiles", {}), "backup_run": ("backup.run", {}),
    "packages_remove": ("pkg.remove", {}), "qube_tag": ("vm.tag", {}), "qube_grow_disk": ("vm.grow_private", {}),
}

OUTILS += [
    _outil("qube_settings", "Réglages d'un qube : propriétés de la liste blanche, services, étiquettes, taille du "
           "disque privé. Lecture seule.", _QUBE, ["name"], True),
    _outil("updates_status", "Mises à jour disponibles et date de la dernière, par qube (vide = tous, dom0 compris). "
           "Lecture seule.", {"targets": {"type": "array", "items": _NOM, "maxItems": 64}}, lecture=True),
    _outil("system_status", "Qubes en marche, leur mémoire, et l'occupation des disques. Lecture seule.",
           lecture=True),
    _outil("global_settings", "Réglages globaux de Qubes (modèle, jetable et réseau par défaut, clockvm, "
           "updatevm…). Lecture seule.", lecture=True),
    _outil("firewall_list", "Règles de pare-feu d'un qube, dans l'ordre. Lecture seule.", _QUBE, ["name"], True),
    _outil("devices_list", "Périphériques USB, disques et micro disponibles, et à quel qube chacun est branché. "
           "Lecture seule, mais la liste est écrite par les qubes : elle rend la session influencée.",
           {"class": {"type": "string", "enum": ["usb", "block", "mic"]}}, lecture=True),
    _outil("qube_set_property", "Change une propriété d'un qube. Propriétés permises : " + ", ".join(PROPS)
           + ". Toute autre (netvm, template, kernel, guivm…) est refusée : utilise l'outil dédié, ou dis à l'utilisateur qu'il peut la "
           "changer lui-même dans les réglages du qube.", {"name": _NOM, "prop": {"type": "string", "enum": list(PROPS)},
                                   "value": {"type": ["string", "integer", "boolean", "null"]}},
           ["name", "prop", "value"]),
    _outil("qube_reset_property", "Remet une propriété d'un qube à la valeur par défaut de Qubes.",
           {"name": _NOM, "prop": {"type": "string", "enum": list(PROPS)}}, ["name", "prop"]),
    _outil("qube_set_service", "Active, désactive ou remet par défaut un service Qubes dans un qube (comme "
           "qvm-service).", {"name": _NOM, "service": {"type": "string", "enum": list(SERVICES)},
                             "state": {"type": "string", "enum": ["on", "off", "default"]}},
           ["name", "service", "state"]),
    _outil("qube_restart", "Redémarre un qube (arrêt puis démarrage). Un qube système coupe le réseau ou l'USB "
           "un moment : confirmé par l'utilisateur.", _QUBE, ["name"]),
    _outil("qube_set_template", "Change le modèle d'un qube arrêté.", {"name": _NOM, "template": _NOM},
           ["name", "template"]),
    _outil("packages_install", "Installe 1 à 8 paquets dans un modèle ou une StandaloneVM (apt ou dnf, par le "
           "proxy de mises à jour de Qubes). Le modèle est démarré puis arrêté s'il l'était. Pour un de tes "
           "espaces : installe dans un modèle copié avec template_clone, pas dans un modèle de l'utilisateur.",
           {"name": _NOM, "packages": {"type": "array", "items": {"type": "string", "maxLength": 63},
                                       "minItems": 1, "maxItems": 8}}, ["name", "packages"]),
    _outil("disposable_create", "Ouvre un qube jetable sans réseau (modèle qaf-dvm) dans lequel tu peux ensuite "
           "travailler avec qube_exec, qube_screenshot, qube_type…", {"template": {**_NOM, "description":
                                                                            "absent = qaf-dvm"}}),
    _outil("qube_exec", "Lance une commande (liste d'arguments, sans shell : pour un shell, [\"bash\", \"-c\", "
           "\"…\"]) DANS un qube démarré. Renvoie code, stdout et stderr (64 Kio au plus). Sur un qube de l'utilisateur, "
           "chaque appel est confirmé.", {"name": _NOM, "argv": {"type": "array", "items": {"type": "string"},
                                                                 "minItems": 1, "maxItems": 64},
                                          "timeout": {**_ENTIER, "minimum": 1, "maximum": 120}},
           ["name", "argv"]),
    _outil("qube_windows", "Liste les fenêtres d'un qube démarré (identifiant, titre, position).",
           {"name": _NOM, "hidden": {"type": "boolean"}}, ["name"]),
    _outil("qube_screenshot", "Capture l'écran d'un qube démarré (ou une seule fenêtre). L'image t'est renvoyée "
           "(peut être réduite pour tenir sous 640 Kio) : décris-la à l'utilisateur. Une copie est enregistrée dans "
           "le qube où tu tournes (fichier: dans la réponse) ; s'il veut la voir, ouvre-la avec ristretto (ou eog). "
           "« racine » : le cadre des fenêtres ouvertes.", {"name": _NOM, "window": _FENETRE}, ["name"]),
    _outil("qube_type", "Tape un texte (2000 caractères au plus) au clavier dans un qube.",
           {"name": _NOM, "text": {"type": "string", "maxLength": 2000}, "window": _FENETRE}, ["name", "text"]),
    _outil("qube_key", "Appuie sur une touche ou une combinaison (ex. Return, ctrl+l) dans un qube.",
           {"name": _NOM, "keys": {"type": "string"}, "window": _FENETRE}, ["name", "keys"]),
    _outil("qube_mouse", "Déplace la souris (button 0) ou clique (1 gauche, 2 milieu, 3 droit) dans un qube ; x, y "
           "relatifs à la fenêtre si window est donné.", {"name": _NOM, "x": _ENTIER, "y": _ENTIER,
                                                          "button": {**_ENTIER, "minimum": 0, "maximum": 3},
                                                          "window": _FENETRE}, ["name", "x", "y"]),
    _outil("qube_scroll", "Fait défiler (haut ou bas, 1 à 20 crans) dans un qube.",
           {"name": _NOM, "direction": {"type": "string", "enum": ["haut", "bas"]},
            "steps": {**_ENTIER, "minimum": 1, "maximum": 20}}, ["name", "direction", "steps"]),
    _outil("global_set_setting", "Change un réglage global de Qubes : " + ", ".join(PREFS) + ". default_netvm, "
           "clockvm, updatevm et les autres sont réservés à l'utilisateur.",
           {"prop": {"type": "string", "enum": list(PREFS)}, "value": {"type": ["string", "boolean", "null"]}},
           ["prop", "value"]),
    _outil("firewall_add", "Ajoute une règle de pare-feu à un qube, à la position donnée.",
           {"name": _NOM, "position": _POSITION, "rule": _REGLE}, ["name", "position", "rule"]),
    _outil("firewall_remove", "Supprime la règle de pare-feu d'un qube à cette position, seulement si c'est "
           "exactement celle donnée (relis firewall_list d'abord).",
           {"name": _NOM, "position": _POSITION, "rule": _REGLE}, ["name", "position", "rule"]),
]

_TRAVAIL = {"job": {"type": "string", "description": "identifiant T-xxxxxx renvoyé au lancement"}}
_CHEMIN = {"type": "string", "maxLength": 255, "description": ("chemin RELATIF au dossier personnel, ex. "
                                                               "Documents/rapport.pdf (pas de fichier caché)")}
_FICHIER = {"source": _NOM, "path": _CHEMIN, "dest": _NOM}
_CLASSE_DEV = {"type": "string", "enum": ["usb", "block", "mic"]}
_PAQUETS = {"type": "array", "items": {"type": "string", "maxLength": 63}, "minItems": 1, "maxItems": 8}

OUTILS += [
    _outil("updates_run", "Lance la mise à jour de modèles ou StandaloneVM (qubes-vm-update, comme Qubes Update). "
           "Travail long : renvoie un identifiant à suivre avec job_status. apply : none (défaut, ne redémarre "
           "rien), sys (redémarre les qubes système concernés), all.",
           {"targets": {"type": "array", "items": _NOM, "minItems": 1, "maxItems": 32},
            "apply": {"type": "string", "enum": ["none", "sys", "all"]}}, ["targets"]),
    _outil("templates_available", "Modèles disponibles au téléchargement (dépôt official ou community). La liste "
           "vient du qube de mises à jour : elle rend la session influencée.",
           {"repo": {"type": "string", "enum": ["official", "community"]}}, lecture=True),
    _outil("template_install", "Toujours refusé : l'installation d'un modèle se fait par l'utilisateur avec l'outil "
           "Qubes Template Manager. N'appelle pas cet outil : dis à l'utilisateur quel modèle installer et pourquoi.",
           {"template": _NOM, "repo": {"type": "string", "enum": ["official", "community"]}}, ["template"]),
    _outil("template_remove", "Supprime définitivement un modèle (Qubes refuse s'il sert encore). Travail long.",
           {"template": _NOM}, ["template"]),
    _outil("job_status", "État d'un travail long (en_cours, fini, annule), code de sortie et durée.", _TRAVAIL,
           ["job"], True),
    _outil("job_cancel", "Interrompt un travail long. Couper une mise à jour peut laisser un modèle à réparer.",
           _TRAVAIL, ["job"]),
    _outil("qube_enter", "Ouvre un bail d'entrée sur un qube de l'utilisateur (1 à 60 min, 30 par défaut) : une seule "
           "confirmation, puis qube_exec, qube_screenshot, qube_type… y sont automatiques jusqu'à la fin.",
           {"name": _NOM, "minutes": {**_ENTIER, "minimum": 1, "maximum": 60}}, ["name"]),
    _outil("qube_leave", "Ferme le bail d'entrée d'un qube.", _QUBE, ["name"]),
    _outil("file_copy", "Copie un fichier d'un qube à un autre (outil de Qubes lancé dans la source ; Qubes "
           "demande aussi sa confirmation). Arrive dans ~/QubesIncoming/<source>/.", _FICHIER,
           ["source", "path", "dest"]),
    _outil("file_move", "Déplace un fichier d'un qube à un autre (disparaît de la source).", _FICHIER,
           ["source", "path", "dest"]),
    _outil("file_open_disposable", "Ouvre un fichier d'un qube dans un jetable, en lecture seule.",
           {"name": _NOM, "path": _CHEMIN}, ["name", "path"]),
    _outil("device_attach", "Branche un périphérique (lu par devices_list) sur un qube, jusqu'à son redémarrage. "
           "device_id est obligatoire : il lie la confirmation au périphérique vu.",
           {"class": _CLASSE_DEV, "backend": _NOM, "port": {"type": "string"}, "device_id": {"type": "string"},
            "target": _NOM, "read_only": {"type": "boolean"}}, ["class", "backend", "port", "device_id", "target"]),
    _outil("device_detach", "Débranche un périphérique d'un qube.",
           {"class": _CLASSE_DEV, "backend": _NOM, "port": {"type": "string"}, "target": _NOM},
           ["class", "backend", "port", "target"]),
    _outil("backup_profiles", "Profils de sauvegarde créés par l'utilisateur, et leur qube de destination.", lecture=True),
    _outil("backup_run", "Lance une sauvegarde selon un profil de l'utilisateur. Travail long (job_status).",
           {"profile": {"type": "string", "maxLength": 64}}, ["profile"]),
    _outil("packages_remove", "Retire 1 à 8 paquets d'un modèle (dpkg/rpm : refuse si un autre paquet en dépend).",
           {"name": _NOM, "packages": _PAQUETS}, ["name", "packages"]),
    _outil("qube_tag", "Ajoute ou retire une étiquette sur un qube (toujours confirmé).",
           {"name": _NOM, "tag": {"type": "string", "maxLength": 31}, "action": {"type": "string",
                                                                              "enum": ["add", "remove"]}},
           ["name", "tag", "action"]),
    _outil("qube_grow_disk", "Agrandit le disque privé d'un qube (taille totale en Mio ; ne se réduit jamais).",
           {"name": _NOM, "size_mib": {**_ENTIER, "minimum": 1024, "maximum": 1048576}}, ["name", "size_mib"]),
]


class ArgumentsInvalides(Exception):
    pass


def _exactement(args, requis, facultatifs=()):
    if not isinstance(args, dict):
        raise ArgumentsInvalides("arguments : objet attendu")
    manquants = set(requis) - set(args)
    inconnus = set(args) - set(requis) - set(facultatifs)
    if manquants:
        raise ArgumentsInvalides(f"argument manquant : {', '.join(sorted(manquants))}")
    if inconnus:
        raise ArgumentsInvalides(f"argument inconnu : {', '.join(sorted(inconnus))}")


def construire_op(outil, args):
    """Outil MCP + arguments -> une opération de plan (dict), sans validation fine."""
    args = {} if args is None else args
    if outil == "qubes_list":
        _exactement(args, ())
        return {"op": "vm.list"}
    if outil == "template_clone":
        _exactement(args, ("name", "source"), ("label",))
        return {"op": "vm.clone", "name": args["name"], "source": args["source"], "label": args.get("label", "gray")}
    if outil == "qube_create":
        _exactement(args, ("name", "template"), ("label",))
        return {"op": "vm.create", "name": args["name"], "template": args["template"],
                "label": args.get("label", "gray")}
    simples = {"qube_info": "vm.info", "qube_start": "vm.start",
               "qube_stop": "vm.shutdown", "qube_remove": "vm.remove"}
    if outil in simples:
        _exactement(args, ("name",))
        return {"op": simples[outil], "name": args["name"]}
    if outil == "qube_set_network":
        _exactement(args, ("name", "netvm"))
        return {"op": "vm.set_netvm", "name": args["name"], "netvm": args["netvm"]}
    if outil == "disposable_task":
        _exactement(args, ("task",), ("template",))
        op = {"op": "dispvm.run_task", "task": args["task"]}
        if "template" in args:
            op["template"] = args["template"]
        return op
    if outil in OPS_OUTILS:
        nom_op, defauts = OPS_OUTILS[outil]
        requis, facultatifs = OPS[nom_op]
        _exactement(args, set(requis) - set(defauts), set(facultatifs) | set(defauts))
        return {"op": nom_op, **defauts, **args}
    raise KeyError(outil)


def demande_courante(environ=None, parent=None):
    """Identifiant R-xxxx de la demande de l'utilisateur transmise à CETTE session, ou R-0000."""
    environ = os.environ if environ is None else environ
    if environ.get(VARIABLE_DEMANDE):
        return environ[VARIABLE_DEMANDE].strip()[:16]  # validé ensuite par parse_plan
    try:
        with open(parent or f"/proc/{os.getppid()}/environ", "rb") as f:
            brut = f.read(1 << 20)
    except OSError:
        return SANS_DEMANDE
    cle = VARIABLE_DEMANDE.encode() + b"="
    for v in brut.split(b"\0"):
        if v.startswith(cle):
            return v[len(cle):].decode("ascii", "replace").strip()[:16] or SANS_DEMANDE
    return SANS_DEMANDE


def nouvelle_session():
    """Identifiant de session de l'agent (C13) : tiré une fois par serveur MCP,
    donc par session de claude. Il ne donne aucun droit : le Doorman lie la
    demande à la première session qui la cite, pour qu'aucune autre n'en hérite."""
    return "S-" + secrets.token_hex(8)


def liste_compacte(stdout):
    """Réponse de vm.list -> texte d'une ligne par qube (~60 caractères), ou
    None si ce n'est pas une liste réussie. Le détail complet (uuid, guivm…)
    faisait dépasser MAX_REPONSE et coupait la liste (PARCOURS-1 M6)."""
    try:
        obj = json.loads(stdout.decode("utf-8"))
        res = obj["resultats"][0]
        qubes = res["resultat"]
        if obj.get("statut") != FAIT or res.get("op") != "vm.list" or not isinstance(qubes, list):
            return None
        lignes = []
        for q in qubes:
            extra = (" | donne le réseau" if q.get("provides_network") else "") + (
                " | modèle de jetables" if q.get("template_for_dispvms") else "") + (
                " | livré par Qubes" if q.get("livre") else "")
            lignes.append(f"{q['name']} | {q['state']} | {q['klass']} | {q['label']} | "
                          f"modèle={q.get('template') or '-'} | réseau={q.get('netvm') or 'aucun'}{extra}")
    except (AttributeError, IndexError, KeyError, TypeError, UnicodeDecodeError, ValueError):
        return None
    return (f"statut: fait\nqubes: {len(lignes)} (liste complète)\n"
            "nom | état | sorte | couleur | modèle | réseau\n" + "\n".join(lignes))


def enregistrer_capture(donnees_b64, dossier):
    """Copie une capture dans sys-agent ; renvoie (chemin, image propre en
    base64), ou (None, None). Le PNG vient d'un qube : il n'est écrit (et donc
    proposé à une visionneuse) qu'après réencodage par png_propre (signature,
    taille, format fermé, PNG neuf construit ici). Revue de M5, OPS-v3 §19."""
    try:
        propre = png_propre.reencoder(base64.b64decode(donnees_b64, validate=True))
    except (binascii.Error, ValueError):
        return None, None
    if propre is None:
        return None, None
    try:
        d = pathlib.Path(dossier)
        d.mkdir(parents=True, exist_ok=True)
        base = time.strftime("capture-%Y%m%d-%H%M%S")
        chemin, n = d / f"{base}.png", 1
        while chemin.exists():
            n += 1
            chemin = d / f"{base}-{n}.png"
        chemin.write_bytes(propre)
        for vieux in sorted(d.glob("capture-*.png"), key=lambda p: p.stat().st_mtime)[:-MAX_CAPTURES]:
            vieux.unlink()
        return str(chemin), base64.b64encode(propre).decode("ascii")
    except OSError:
        return None, base64.b64encode(propre).decode("ascii")


def _propre(texte, maxi=MAX_REPONSE):
    """Retire les caractères de contrôle/format (sauf saut de ligne) et borne."""
    texte = "".join(c for c in texte if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf", "Cs"))
    return texte if len(texte) <= maxi else texte[:maxi] + "\n[… tronqué]"


def deballer(stdout, captures=None):
    """Sorties de qube (blob base64 opaque, jamais lu par dom0) : décodées ICI,
    dans sys-agent. Renvoie (réponse réécrite, blocs image MCP). Avec
    captures (dossier), chaque image y est aussi copiée (sortie["fichier"])."""
    try:
        obj = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return stdout, []
    images, vus = [], 0
    for res in (obj.get("resultats") if isinstance(obj, dict) and isinstance(obj.get("resultats"), list) else []):
        r = res.get("resultat") if isinstance(res, dict) else None
        if not isinstance(r, dict) or not isinstance(r.get("b64"), str):
            continue
        vus += 1
        try:
            sortie = json.loads(base64.b64decode(r.pop("b64"), validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            sortie = "sortie illisible"
        if isinstance(sortie, dict) and isinstance(sortie.get("image"), str):
            data = sortie.pop("image")
            sortie["image"] = "(jointe)"
            # Seule une capture (qube.screenshot) est enregistrée, et seulement
            # réencodée ; sinon l'image ne va qu'au modèle, jamais sur le disque.
            if captures and res.get("op") == "qube.screenshot":
                chemin, propre = enregistrer_capture(data, captures)
                data = propre or data
                if chemin:
                    sortie["fichier"] = chemin
            images.append({"type": "image", "data": data, "mimeType": "image/png"})
        r["sortie"] = sortie
    if not vus:
        return stdout, []
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), images


def rendre_reponse(code, stdout, stderr, maxi=MAX_REPONSE):
    """Réponse du Doorman -> (texte court et structuré, est_une_erreur).

    Contrat unique : qaf_gate/proto.py, un objet JSON {"statut": un de
    proto.STATUTS, ...}. Seul « fait » est un succès. Tout autre contenu est
    hors contrat : rendu comme une erreur (nettoyé, tronqué).
    """
    if code == EXIT_REFUSE:
        return ("statut: refuse_par_qrexec\nmessage: la politique qrexec refuse qaf.Plan "
                "depuis ce qube (règle manquante dans dom0)."), True
    if code == EXIT_INTROUVABLE:
        return "statut: service_absent\nmessage: qaf.Plan n'est pas installé dans dom0.", True
    texte = stdout.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(texte)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        lignes = []
        # Pas d'identifiant R-xxxx pour l'agent : il le répéterait à l'utilisateur (PARCOURS-1 M8).
        for k in sorted((k for k in obj if k != "request"), key=lambda k: (k != "statut", k)):
            v = obj[k]
            v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            lignes.append(f"{k}: {v}")
        statut = obj.get("statut")
        if statut not in STATUTS:
            lignes.insert(0, "statut_hors_contrat: oui")
        return _propre("\n".join(lignes), maxi), code != 0 or statut != FAIT
    if code != 0:
        err = stderr.decode("utf-8", "replace").strip()
        return _propre(f"statut: erreur\ncode: {code}\nmessage: {err or texte or 'sans détail'}"), True
    return _propre(f"statut: erreur\nmessage: réponse hors contrat du cœur du système\n{texte}"), True


# ------------------------------------------------------------- serveur MCP

class Serveur:
    def __init__(self, transport=None, environ=None, session=None, parent=None):
        self.transport = transport or QrexecTransport()
        self.environ, self.parent = environ, parent
        self.session = session or nouvelle_session()
        env = os.environ if environ is None else environ
        maison = env.get("HOME")
        self.captures = env.get(VARIABLE_CAPTURES) or (os.path.join(maison, "captures") if maison else None)

    def appeler_outil(self, nom, args):
        """Renvoie le résultat MCP {content, isError}."""
        try:
            op = construire_op(nom, args)
            brut = json.dumps({"version": 1, "request": demande_courante(self.environ, self.parent),
                               "session": self.session, "ops": [op]}, ensure_ascii=False).encode("utf-8")
            plan = canonicalize(parse_plan(brut))  # même validation que le Doorman
        except (ArgumentsInvalides, PlanError) as e:
            return _resultat(f"statut: plan_invalide\nmessage: {e}", True)
        code, sortie, err = self.transport.envoyer(plan)
        liste = liste_compacte(sortie) if nom == "qubes_list" and code == 0 else None
        if liste is not None:
            return _resultat(_propre(liste, MAX_SORTIE_QUBE), False)
        sortie2, images = deballer(sortie, self.captures)
        maxi = MAX_SORTIE_QUBE if sortie2 is not sortie else MAX_REPONSE
        texte, erreur = rendre_reponse(code, sortie2, err, maxi)
        resultat = _resultat(texte, erreur)
        resultat["content"] += images
        return resultat

    def traiter(self, msg):
        """Un message JSON-RPC décodé -> réponse (dict) ou None (notification)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _erreur(msg.get("id") if isinstance(msg, dict) else None, -32600, "requête invalide")
        if "id" not in msg:
            return None  # notification (notifications/initialized, cancelled…) : pas de réponse
        mid, methode, params = msg["id"], msg["method"], msg.get("params") or {}
        if not isinstance(params, dict):
            return _erreur(mid, -32602, "params : objet attendu")
        if methode == "initialize":
            demandee = params.get("protocolVersion")
            return _ok(mid, {
                "protocolVersion": demandee if demandee in VERSIONS_MCP else VERSIONS_MCP[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": NOM_SERVEUR, "version": VERSION_SERVEUR},
                "instructions": ("Tu pilotes Qubes OS sans droits : chaque outil demande l'action au "
                                 "cœur du système (dom0), qui vérifie, demande parfois confirmation "
                                 "à l'utilisateur, puis exécute. Un refus est définitif : ne contourne pas, "
                                 "explique pourquoi en une phrase, dis que l'utilisateur peut le faire lui-même "
                                 "dans les réglages de Qubes, et ne donne jamais de commande dom0 ou "
                                 "qvm-* toute prête. Parle à l'utilisateur en français simple, sans jargon."),
            })
        if methode == "ping":
            return _ok(mid, {})
        if methode == "tools/list":
            return _ok(mid, {"tools": OUTILS})
        if methode == "tools/call":
            nom = params.get("name")
            if nom not in {o["name"] for o in OUTILS}:
                return _erreur(mid, -32602, f"outil inconnu : {nom!r}")
            return _ok(mid, self.appeler_outil(nom, params.get("arguments")))
        return _erreur(mid, -32601, f"méthode inconnue : {methode}")

    def boucle(self, entree, sortie):
        for ligne in entree:
            if not ligne.strip():
                continue
            if len(ligne) > MAX_LIGNE:
                rep = _erreur(None, -32600, "message trop long")
            else:
                try:
                    rep = self.traiter(json.loads(ligne))
                except ValueError:
                    rep = _erreur(None, -32700, "JSON invalide")
            if rep is not None:
                sortie.write(json.dumps(rep, ensure_ascii=False) + "\n")
                sortie.flush()


def _resultat(texte, erreur):
    return {"content": [{"type": "text", "text": texte}], "isError": erreur}


def _ok(mid, resultat):
    return {"jsonrpc": "2.0", "id": mid, "result": resultat}


def _erreur(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main():
    entree = open(sys.stdin.fileno(), "r", encoding="utf-8", errors="replace", closefd=False)
    sortie = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)
    Serveur().boucle(entree, sortie)


if __name__ == "__main__":
    main()
