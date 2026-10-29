"""Faux Qubes en mémoire pour les tests. Reproduit les refus de qubesd utiles
au Doorman (messages inspirés de qubes-core-admin, qubes/vm et qubes/app.py).
OPS-v3 : propriétés, services, tags, pare-feu, périphériques, préférences
globales, faux relais qrexec (qaf_hand, qubes.VMExec) enregistré dans `appels`."""

import base64
import json
import uuid as _uuid
from dataclasses import replace

from qaf_gate import schema

from qaf_gate import backend as B
from qaf_gate.backend import BackendError, QubeInfo
from qaf_gate.schema import LABELS

ACTIF = ("Running", "Transient", "Paused", "Suspended", "Halting")


def _q(name, klass, label, template=None, netvm=None, net=False, state="Halted", **autres):
    return QubeInfo(name, str(_uuid.uuid4()), klass, state, label, template, netvm, net, **autres)


def qubes_par_defaut():
    """Installation type de Qubes 4.3 (noms des qubes créés à l'installation)."""
    return [
        _q("dom0", "AdminVM", "black", state="Running"),
        _q("debian-13-xfce", "TemplateVM", "black"),
        _q("fedora-42-xfce", "TemplateVM", "black"),
        _q("sys-net", "AppVM", "red", "debian-13-xfce", None, True, "Running"),
        _q("sys-firewall", "AppVM", "green", "debian-13-xfce", "sys-net", True, "Running"),
        _q("sys-usb", "AppVM", "red", "debian-13-xfce", None, False, "Running"),
        _q("default-dvm", "AppVM", "red", "debian-13-xfce", "sys-firewall", template_for_dispvms=True),
        _q("vault", "AppVM", "black", "debian-13-xfce", None),
        _q("work", "AppVM", "blue", "fedora-42-xfce", "sys-firewall", state="Running"),
        _q("personal", "AppVM", "yellow", "debian-13-xfce", "sys-firewall"),
        _q("untrusted", "AppVM", "red", "debian-13-xfce", "sys-firewall"),
    ]


class SimBackend(B.Backend):
    def __init__(self, qubes=None, default_dispvm="default-dvm"):
        self.qubes = {q.name: q for q in (qubes or qubes_par_defaut())}
        self.default_dispvm = default_dispvm
        self.globaux = {"default_netvm": "sys-firewall", "default_template": "debian-13-xfce",
                        "default_dispvm": default_dispvm, "clockvm": "sys-net", "updatevm": "sys-firewall",
                        "check_updates_vm": True, "management_dispvm": None, "default_guivm": None,
                        "default_audiovm": None, "default_kernel": "6.12", "default_pool": "vm-pool"}
        for q in list(self.qubes.values()):
            if q.klass != "AdminVM" and q.default_dispvm is None and q.default_dispvm_herite:
                self.qubes[q.name] = replace(q, default_dispvm=default_dispvm)
        self.props, self.services, self.tags = {}, {}, {}  # nom -> {prop: valeur explicite}…
        self.features = {}  # nom -> fonctions (updates-available, os-distribution…)
        self.regles = {}  # nom -> règles de pare-feu (dict de schema._regle_pf)
        self.peripheriques = []  # entrées de dev_list telles qu'annoncées
        self.appels = []  # relais qrexec simulé : ("hand"|"vmexec", qube, …)
        self.reponses_main = {}  # service -> dict renvoyé par qaf_hand
        self.taches = []  # (disposable, tâche) confiées, pour les tests
        self.tues = []  # jetables tués (tuer), pour les tests
        self._n_disp = 0
        # Faux qaf-gate-outils (lots 2 et 3) : travaux, profils de sauvegarde, modèles disponibles.
        self.travaux = {}  # T-xxxxxx -> {"type", "etat", "code", "args"}
        self.profils = {}  # nom -> {"destination_vm", "passphrase_vm"}
        self.disponibles = [{"nom": "debian-13-minimal", "version": "0:4.3.0", "depot": "qubes-templates-itl"}]
        self.attaches = []  # (classe, backend, port, device_id, cible, options)
        self.prive = {}  # nom -> taille du volume privé (Mio)

    def _get(self, name):
        if name not in self.qubes:
            raise BackendError(B.INTROUVABLE, f"qube inconnu : {name}")
        return self.qubes[name]

    def _set(self, q, **champs):
        self.qubes[q.name] = replace(q, **champs)

    def _avec_tags(self, q):
        return replace(q, tags=tuple(sorted(self.tags.get(q.name, ()))))

    def list_qubes(self):
        return [self._avec_tags(self.qubes[n]) for n in sorted(self.qubes)]

    def info(self, name):
        return self._avec_tags(self._get(name))

    def create_appvm(self, name, template, label):
        if name in self.qubes:
            raise BackendError(B.NOM_PRIS, f"le nom {name} est déjà utilisé")
        if label not in LABELS:
            raise BackendError(B.LABEL_INCONNU, f"label inconnu : {label}")
        if template not in self.qubes:
            raise BackendError(B.TEMPLATE_ABSENT, f"template introuvable : {template}")
        if self.qubes[template].klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un template")
        q = _q(name, "AppVM", label, template, None)
        self.qubes[name] = q
        return q

    def clone_template(self, source, name, label):
        if name in self.qubes:
            raise BackendError(B.NOM_PRIS, f"le nom {name} est déjà utilisé")
        src = self._get(source)
        if src.klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{source} n'est pas un template")
        q = _q(name, "TemplateVM", label)
        self.qubes[name] = q
        if source in self.features:
            self.features[name] = dict(self.features[source])
        return q

    def start(self, name):
        q = self._get(name)
        if q.klass == "AdminVM" or q.state in ACTIF:
            raise BackendError(B.DEJA_DEMARRE, f"{name} est déjà démarré")
        if q.netvm and self.qubes[q.netvm].state != "Running":
            self.start(q.netvm)  # qubesd démarre aussi la netvm
        self._set(q, state="Running")

    def shutdown(self, name):
        q = self._get(name)
        if q.klass == "AdminVM":
            raise BackendError(B.EN_USAGE, "dom0 ne s'arrête pas par l'Admin API")
        if q.state != "Running":
            raise BackendError(B.PAS_DEMARRE, f"{name} n'est pas démarré")
        clients = [c.name for c in self.qubes.values() if c.netvm == name and c.state in ACTIF]
        if clients:
            raise BackendError(B.EN_USAGE, f"{name} sert de netvm à {', '.join(clients)}")
        self._set(q, state="Halted")

    def remove(self, name):
        q = self._get(name)
        if q.klass == "AdminVM":
            raise BackendError(B.EN_USAGE, "'dom0' cannot be removed")
        if q.state != "Halted":
            raise BackendError(B.PAS_ARRETE, f"{name} doit être arrêté")
        deps = [d.name for d in self.qubes.values() if name in (d.template, d.netvm)]
        if name == self.default_dispvm:
            deps.append("@GLOBAL:default_dispvm")
        if deps:
            raise BackendError(B.EN_USAGE, f"{name} est utilisé par {', '.join(deps)}")
        del self.qubes[name]

    def set_netvm(self, name, netvm):
        q = self._get(name)
        if netvm is not None:
            n = self._get(netvm)
            if not n.provides_network:
                raise BackendError(B.PAS_UN_NETVM, f"{netvm} ne fournit pas de réseau")
            if netvm == name:
                raise BackendError(B.AUTRE, "boucle réseau interdite")
            if q.state in ACTIF and n.state != "Running":
                raise BackendError(B.NETVM_ARRETEE, f"{netvm} est arrêtée")
        self._set(q, netvm=netvm)

    def run_dispvm_task(self, template, task):
        modele = template or self.default_dispvm
        base = self._get(modele)
        if base.klass != "AppVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle de jetable")
        self._n_disp += 1
        nom = f"disp{self._n_disp}"
        self.qubes[nom] = _q(nom, "DispVM", base.label, modele, base.netvm, state="Running")
        self.taches.append((nom, task))
        return nom

    # --- OPS-v3 ----------------------------------------------------------------

    DEFAUTS = {"memory": 400, "maxmem": 4000, "vcpus": 2, "autostart": False, "include_in_backups": True,
               "keyboard_layout": "us++", "shutdown_timeout": 60, "qrexec_timeout": 60}

    def _valeur(self, q, prop):
        if prop in ("label", "default_dispvm", "template_for_dispvms", "provides_network"):
            return getattr(q, {"provides_network": "provides_network"}.get(prop, prop))
        return self.props.get(q.name, {}).get(prop, self.DEFAUTS.get(prop))

    def prefs(self):
        return dict(self.globaux)

    def settings(self, name):
        q = self._get(name)
        return {"proprietes": {p: self._valeur(q, p) for p in schema.PROPS},
                "services": {s: self.services.get(name, {}).get(s) for s in schema.SERVICES},
                "tags": sorted(self.tags.get(name, ())), "prive_mio": 2048}

    def set_prop(self, name, prop, value):
        q = self._get(name)
        avant = {"valeur": self._valeur(q, prop), "defaut": prop not in self.props.get(name, {})}
        if prop == "label":
            self._set(q, label=value)
        elif prop == "default_dispvm":
            if value is not None and not self._get(value).template_for_dispvms:
                raise BackendError(B.PAS_UN_TEMPLATE, f"{value} n'est pas un modèle de jetable")
            self._set(q, default_dispvm=value, default_dispvm_herite=False)
        elif prop in ("template_for_dispvms", "provides_network"):
            self._set(q, **{prop: value})
        elif prop == "memory" and value > self._valeur(q, "maxmem") > 0:
            raise BackendError(B.AUTRE, "memory doit rester inférieure à maxmem")
        self.props.setdefault(name, {})[prop] = value
        return {"avant": avant}

    def reset_prop(self, name, prop):
        q = self._get(name)
        avant = {"valeur": self._valeur(q, prop), "defaut": prop not in self.props.get(name, {})}
        self.props.get(name, {}).pop(prop, None)
        if prop == "default_dispvm":
            self._set(q, default_dispvm=self.globaux["default_dispvm"], default_dispvm_herite=True)
        return {"avant": avant}

    def set_service(self, name, service, state):
        self._get(name)
        svc = self.services.setdefault(name, {})
        avant = svc.get(service)
        if state == "default":
            svc.pop(service, None)
        else:
            svc[service] = "1" if state == "on" else ""
        return {"avant": avant}

    def set_pref(self, prop, value):
        if value is not None and not isinstance(value, bool):
            self._get(value)
        avant = {"valeur": self.globaux.get(prop), "defaut": False}
        self.globaux[prop] = value
        if prop == "default_dispvm":
            for q in list(self.qubes.values()):
                if q.default_dispvm_herite and q.klass != "AdminVM":
                    self._set(q, default_dispvm=value)
        return {"avant": avant}

    def update_status(self, targets):
        noms = targets or sorted(self.qubes)
        out = []
        for n in noms:
            self._get(n)
            f = self.features.get(n, {})
            out.append({"name": n, **{k.replace("-", "_"): f.get(k) for k in (
                "updates-available", "last-update", "last-updates-check", "skip-update", "prohibit-start")}})
        return out

    def sys_status(self):
        return {"qubes": [{"name": q.name, "memoire_kio": 409600} for q in self.list_qubes() if q.state == "Running"],
                "pools": [{"name": "vm-pool", "taille": 500 * 2**30, "utilise": 120 * 2**30}]}

    def fw_list(self, name):
        self._get(name)
        return [dict(r) for r in self.regles.get(name, [{"action": "accept"}])]

    def fw_add(self, name, position, rule):
        regles = self.fw_list(name)
        if len(regles) >= 64:
            raise BackendError(B.AUTRE, "64 règles au plus")
        avant = json.dumps(regles, sort_keys=True)
        regles.insert(position, dict(rule))
        self.regles[name] = regles
        return {"avant": avant}

    def fw_remove(self, name, position, rule):
        regles = self.fw_list(name)
        if position >= len(regles) or regles[position] != rule:
            raise BackendError(B.REGLE_CHANGEE, "la règle à cette position a changé")
        avant = json.dumps(regles, sort_keys=True)
        del regles[position]
        self.regles[name] = regles
        return {"avant": avant}

    def dev_list(self, classe):
        return [dict(e) for e in self.peripheriques if classe in (None, e.get("class"))]

    def restart(self, name):
        self.shutdown(name)
        self.start(name)

    def set_template(self, name, template):
        q = self._get(name)
        if self._get(template).klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un template")
        if q.state != "Halted":
            raise BackendError(B.PAS_ARRETE, f"{name} doit être arrêté")
        self._set(q, template=template)
        return {"avant": {"valeur": q.template, "defaut": False}}

    def pkg_install(self, name, packages, action="pkg.install"):
        q = self._get(name)
        if q.klass not in ("TemplateVM", "StandaloneVM"):
            raise BackendError(B.PAS_UN_TEMPLATE, f"{name} : modèle ou StandaloneVM attendu")
        distro = self.features.get(name, {}).get("os-distribution", "debian")
        if distro not in ("debian", "fedora"):
            raise BackendError(B.DISTRIBUTION, f"{name} : distribution inconnue")
        self.appels.append(("vmexec", name, distro, list(packages)) + ((action,) if action != "pkg.install" else ()))
        return {"codes": [0, 0] if distro == "debian" and action == "pkg.install" else [0]}

    def pkg_remove(self, name, packages):
        return self.pkg_install(name, packages, "pkg.remove")

    # --- lots 2 et 3 -------------------------------------------------------------

    def _travail(self, type_, **args):
        if any(t["etat"] == "en_cours" for t in self.travaux.values()):
            raise BackendError(B.AUTRE, "qaf-gate-outils : un travail est déjà en cours")
        tid = f"T-{len(self.travaux) + 100001}"
        self.travaux[tid] = {"type": type_, "etat": "en_cours", "code": None, "args": args}
        return tid

    def finir(self, tid, code=0):
        """Test : le travail se termine (comme l'outil de Qubes)."""
        t = self.travaux[tid]
        t["etat"], t["code"] = "fini", code
        if t["type"] == "template.remove" and code == 0:
            del self.qubes[t["args"]["nom"]]

    def update_run(self, targets, apply):
        ignorees = []
        for t in targets:
            if self._get(t).klass not in ("TemplateVM", "StandaloneVM"):
                raise BackendError(B.PAS_UN_TEMPLATE, f"{t} : modèle ou StandaloneVM attendu")
            if self.features.get(t, {}).get("prohibit-start"):
                ignorees.append(t)
        cibles = [t for t in targets if t not in ignorees]
        if not cibles:
            raise BackendError(B.AUTRE, "toutes les cibles ont prohibit-start")
        return {"travail": self._travail("update", cibles=cibles, apply=apply), "ignorees_prohibit_start": ignorees}

    def template_available(self, repo):
        return [dict(m) for m in self.disponibles]


    def template_remove(self, template):
        if self._get(template).klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un modèle")
        return {"travail": self._travail("template.remove", nom=template)}

    def backup_run(self, profile):
        if profile not in self.profils:
            raise BackendError(B.AUTRE, "profil inconnu")
        return {"travail": self._travail("backup", profil=profile)}

    def backup_info(self, profile):
        if profile not in self.profils:
            raise BackendError(B.AUTRE, "qaf-gate-outils : profil illisible")
        return dict(self.profils[profile])

    def backup_profiles(self):
        return [{"profil": n, "destination_vm": p["destination_vm"]} for n, p in sorted(self.profils.items())]

    def job_status(self, job):
        if job not in self.travaux:
            raise BackendError(B.AUTRE, "qaf-gate-outils : travail inconnu")
        t = self.travaux[job]
        return {"ok": True, "etat": t["etat"], "code": t["code"], "duree": 12}

    def job_cancel(self, job):
        self.job_status(job)
        if self.travaux[job]["etat"] == "en_cours":
            self.travaux[job].update(etat="annule", code=-15)
        return {"annulation": "demandée"}

    def _dans(self, name, action, **champs):
        if self._get(name).state != "Running":
            raise BackendError(B.PAS_DEMARRE, f"{name} n'est pas démarré")
        self.appels.append(("vmexec", name, action, champs))
        return {"code": 0}

    def file_copy(self, source, path, dest, move):
        self._get(dest)
        return self._dans(source, "file.move" if move else "file.copy", dest=dest, chemin=path)

    def file_open_dispvm(self, name, path):
        if self._get(name).default_dispvm is None:
            raise BackendError(B.AUTRE, f"{name} n'a pas de jetable par défaut")
        return self._dans(name, "file.open_dispvm", chemin=path)

    def dev_attach(self, classe, backend, port, device_id, target, read_only):
        self._get(target)
        vus = {(e["port"], e["device_id"]) for e in self.peripheriques
               if e.get("class") == classe and e.get("backend") == backend}
        if (port, device_id) not in vus:
            raise BackendError(B.PERIPHERIQUE_CHANGE, f"{backend}:{port} n'expose plus {device_id}")
        opts = {"read-only": "yes" if read_only else "no"} if classe == "block" else {}
        self.attaches.append((classe, backend, port, device_id, target, opts))

    def dev_detach(self, classe, backend, port, target):
        self._get(target)
        self.attaches = [a for a in self.attaches if a[:3] + a[4:5] != (classe, backend, port, target)]

    def tag(self, name, tag, action):
        self._get(name)
        tags = self.tags.setdefault(name, set())
        avant = tag in tags
        (tags.add if action == "add" else tags.discard)(tag)
        return {"avant": avant}

    def grow_private(self, name, size_mib):
        self._get(name)
        avant = self.prive.get(name, 2048)
        if size_mib <= avant:
            raise BackendError(B.AUTRE, f"{name} : agrandir seulement")
        self.prive[name] = size_mib
        return {"avant": avant}

    def create_dispvm(self, template):
        modele = template or schema.MODELE_JETABLE
        base = self._get(modele)
        if base.klass != "AppVM" or not base.template_for_dispvms:
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle de jetable")
        self._n_disp += 1
        q = _q(f"disp{self._n_disp}", "DispVM", base.label, modele, base.netvm, state="Running")
        self.qubes[q.name] = q
        return q

    # --- agent jetable (AGENT-JETABLE) ------------------------------------------

    def creer_agent(self, modele):
        """Comme DispVM.__init__ : étiquettes, pare-feu et default_dispvm du
        modèle recopiés (S-J2, S-J6), plus created-by-dom0 ; pas démarré."""
        if modele not in schema.MODELES_AGENT:
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle d'agent")
        base = self._get(modele)
        if base.klass != "AppVM" or not base.template_for_dispvms:
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle de jetable")
        self._n_disp += 1
        nom = f"disp{self._n_disp}"
        self.qubes[nom] = _q(nom, "DispVM", base.label, modele, base.netvm, default_dispvm=base.default_dispvm,
                             default_dispvm_herite=False)
        self.tags[nom] = set(self.tags.get(modele, ())) | {"created-by-dom0"}
        if modele in self.regles:
            self.regles[nom] = [dict(r) for r in self.regles[modele]]
        return self.info(nom)

    def tuer(self, name):
        self.tues.append(name)
        self.qubes.pop(name, None)  # auto_cleanup : Qubes le supprime

    def precharge(self, name):
        return bool(self.features.get(name, {}).get("preload-dispvm-completed"))

    def hand(self, name, service, requete, delai):
        q = self._get(name)
        if q.state != "Running":
            raise BackendError(B.PAS_DEMARRE, f"{name} n'est pas démarré")
        self.appels.append(("hand", name, service, requete))
        sortie = json.dumps(self.reponses_main.get(service, {"ok": True})).encode()
        return {"code": 0, "octets": len(sortie), "b64": base64.b64encode(sortie).decode()}
