"""Backend réel : qubesd via la bibliothèque officielle qubesadmin (dom0 seulement).

API vérifiée dans qubes-core-admin-client : Qubes(), app.domains[nom],
`nom in app.domains`, `del app.domains[nom]` (admin.vm.Remove),
app.add_new_vm(cls, name, label, template), vm.start(), vm.shutdown(),
vm.get_power_state(), vm.klass, vm.label.name, vm.netvm = None (envoie '' :
aucun réseau), exceptions qubesadmin.exc reconstruites depuis qubesd.
Jetables : app.qubesd_call(modèle, "admin.vm.CreateDisposable") -> nom,
app.domains.get_blind(nom), vm.start(), vm.kill(). Cache des noms :
app.domains.clear_cache() (qubesadmin/app.py, VMCollection).
L'appel qrexec à qaf.Task ne se fait PAS ici : qaf-gate-taches (taches.py).

OPS-v3 (mêmes appels que qvm-prefs, qvm-service, qvm-firewall, qubes-prefs,
qvm-device) : setattr/delattr sur vm et app (admin.vm.property.Set/Reset,
admin.property.Set), vm.property_is_default, vm.features (admin.vm.feature.*),
vm.tags, vm.volumes["private"].size, vm.firewall.rules et
qubesadmin.firewall.Rule (admin.vm.firewall.Get/Set), vm.devices[classe]
(get_exposed_devices / get_attached_devices en 4.3, available / attached
avant : sonde P-6), vm.get_mem (admin.vm.CurrentState, sonde P-7),
app.pools[nom].size/usage (admin.pool.UsageData). Aucun fichier de Qubes
n'est lu ni écrit. Les appels dans un qube (qaf_hand, qubes.VMExec) passent
par qaf-gate-taches.
"""

import hashlib
import re
import time

from . import backend as B
from . import classify, proto, schema
from .backend import BackendError, QubeInfo

# Modèle de jetable DÉDIÉ au Doorman (tools/setup-qaf-dvm.sh) : AppVM avec
# template_for_dispvms=True et qaf_hand installé dans son template. Jamais
# default_dispvm (C3 ; souvent vide sur un Qubes installé).
MODELE_JETABLE = schema.MODELE_JETABLE
# H2 : échéance unique (création, démarrage, accusé) sous proto.DUREE_MAX_OP (120 s).
DELAI_TACHE = proto.DUREE_MAX_OP["dispvm.run_task"] - 10
SOCK_TACHES = "/run/qaf-taches/taches.sock"  # = taches.SOCK_TACHES (vérifié par les tests)
SOCK_OUTILS = "/run/qaf-outils/outils.sock"  # = outils.SOCK_OUTILS (vérifié par les tests)
DELAI_OUTILS = proto.DUREE_MAX_OP["template.available"] - 5
DELAI_FICHIER = proto.DUREE_MAX_OP["file.copy"] - 10
DELAI_PKG = proto.DUREE_MAX_OP["pkg.install"] - 60  # démarrage et arrêt du modèle compris
# Fonction os-distribution (écrite par le qube, C6) : ne fait que choisir
# entre les deux argv fixes de qaf-gate-taches ; inconnue = refus.
DISTROS = {"debian": "debian", "whonix": "debian", "ubuntu": "debian", "kali": "debian", "fedora": "fedora"}
QUBES_ROLES = ("guivm", "audiovm", "management_dispvm")
_NOM_RE = re.compile(r"\A[a-zA-Z][a-zA-Z0-9_.-]{0,30}\Z")


# Propriétés remises à la valeur par défaut de Qubes sur un clone (vm.clone).
CLONE_DEFAUTS = ("default_dispvm", "management_dispvm", "guivm", "audiovm", "autostart")


def _nom(v):
    """Qube -> nom ; label -> nom du label ; autre valeur telle quelle."""
    return getattr(v, "name", v) if v is not None and not isinstance(v, (str, int, bool)) else v


def _prop(vm, nom, defaut=None):
    # AdminVM/StandaloneVM n'ont pas de template, dom0 pas de netvm :
    # qubesadmin lève alors une sous-classe d'AttributeError.
    try:
        return getattr(vm, nom)
    except AttributeError:
        return defaut


class QubesBackend(B.Backend):
    def __init__(self, app=None, modele_jetable=MODELE_JETABLE, delai_tache=DELAI_TACHE,
                 modeles_admis=classify.JETABLES_DEFAUT, taches=None, horloge=time.monotonic, outils=None):
        import qubesadmin  # import paresseux : jamais au chargement du Doorman
        import qubesadmin.exc

        self.exc = qubesadmin.exc
        self.app = app if app is not None else qubesadmin.Qubes()
        self.modele_jetable, self.delai_tache = modele_jetable, delai_tache
        self.modeles_admis, self.horloge = frozenset(modeles_admis), horloge
        # taches(message, délai) -> réponse de qaf-gate-taches (socket réservé à qaf-exec).
        self.taches = taches or (lambda msg, delai: proto.client(SOCK_TACHES, delai)(msg))
        # outils(message, délai) -> réponse de qaf-gate-outils (D13, socket réservé à qaf-exec).
        self.outils = outils or (lambda msg, delai: proto.client(SOCK_OUTILS, delai)(msg))

    def _err(self, e, contexte=""):
        """Traduit une exception qubesadmin en BackendError."""
        x = self.exc
        table = [
            (x.QubesVMNotFoundError, B.INTROUVABLE),
            (x.QubesVMNotHaltedError, B.PAS_ARRETE),
            (x.QubesVMNotStartedError, B.PAS_DEMARRE),
            (x.QubesVMInUseError, B.EN_USAGE),
            (x.QubesNoTemplateError, B.TEMPLATE_ABSENT),
            (x.QubesLabelNotFoundError, B.LABEL_INCONNU),
        ]
        for cls, code in table:
            if isinstance(e, cls):
                return BackendError(code, f"{contexte}{e}")
        return BackendError(B.AUTRE, f"{contexte}{type(e).__name__}: {e}")

    def _vm(self, name):
        if name not in self.app.domains:
            raise BackendError(B.INTROUVABLE, f"qube inconnu : {name}")
        return self.app.domains[name]

    def _info(self, vm):
        def prop(nom, defaut=None):
            return _prop(vm, nom, defaut)

        tpl, net = prop("template"), prop("netvm")
        try:
            herite = bool(vm.property_is_default("default_dispvm"))
        except (AttributeError, self.exc.QubesException):
            herite = False
        noms = {k: _nom(prop(k)) for k in ("default_dispvm", *QUBES_ROLES)}
        return QubeInfo(
            name=vm.name,
            uuid=str(prop("uuid", "")),
            klass=vm.klass,
            state=vm.get_power_state(),
            label=vm.label.name,
            template=tpl.name if tpl is not None else None,
            netvm=net.name if net is not None else None,
            provides_network=bool(prop("provides_network", False)),
            template_for_dispvms=bool(prop("template_for_dispvms", False)),
            default_dispvm_herite=herite,
            tags=tuple(sorted(str(t) for t in prop("tags", ()))),
            livre=vm.klass == "TemplateVM" and self._livre(vm),
            **noms,
        )

    def _livre(self, vm):
        """n17 : installé par paquet (installed_by_rpm) ou par qvm-template (feature
        template-name égale au nom : un clone la garde sous un autre nom). Dans le
        doute, livré (à l'utilisateur)."""
        try:
            return bool(_prop(vm, "installed_by_rpm", False)) or vm.features.get("template-name", None) == vm.name
        except (AttributeError, TypeError, self.exc.QubesException):
            return True

    def _appel(self, fn, contexte):
        try:
            return fn()
        except BackendError:
            raise
        except self.exc.QubesException as e:
            raise self._err(e, contexte) from None

    def list_qubes(self):
        # H4 : l'exécuteur vit longtemps ; sans ceci, un qube créé ou supprimé
        # hors du Doorman resterait invisible (ou fantôme) jusqu'à son redémarrage.
        self.app.domains.clear_cache()
        return self._appel(lambda: [self._info(vm) for vm in self.app.domains], "")

    def info(self, name):
        return self._appel(lambda: self._info(self._vm(name)), f"{name} : ")

    def create_appvm(self, name, template, label):
        if name in self.app.domains:
            raise BackendError(B.NOM_PRIS, f"le nom {name} est déjà utilisé")
        if template not in self.app.domains:
            raise BackendError(B.TEMPLATE_ABSENT, f"template introuvable : {template}")
        if self.app.domains[template].klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un template")
        vm = self._appel(lambda: self.app.add_new_vm("AppVM", name, label, template), f"{name} : ")
        try:
            vm.netvm = None  # C2 : jamais de réseau à la création
        except self.exc.QubesException as e:
            # Qube encore arrêté donc sans réseau effectif : on le retire.
            self._appel(lambda: self.app.domains.__delitem__(name), f"{name} : ")
            raise self._err(e, f"{name} : netvm non retiré : ") from None
        return self._appel(lambda: self._info(vm), f"{name} : ")

    def clone_template(self, source, name, label):
        """Comme qvm-clone (admin.vm.volume.CloneFrom/CloneTo) ; le clone garde
        les fonctions et étiquettes du modèle, prend le label demandé et perd
        tout netvm explicite. Échec après la copie : le clone est retiré."""
        if name in self.app.domains:
            raise BackendError(B.NOM_PRIS, f"le nom {name} est déjà utilisé")
        src = self._vm(source)
        if src.klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{source} n'est pas un template")
        # Revue du 25/10 : qvm-clone recopie aussi les attaches de périphériques,
        # les étiquettes (que des politiques qrexec peuvent citer) et des
        # propriétés qui donnent des droits. Sans attaches, puis nettoyage ;
        # tout échec du nettoyage retire le clone (jamais de clone privilégié).
        vm = self._appel(lambda: self.app.clone_vm(src, name, ignore_errors=True, ignore_devices=True), f"{name} : ")
        try:
            vm.label = label
            for p in CLONE_DEFAUTS:
                if not vm.property_is_default(p):
                    delattr(vm, p)
            if _prop(vm, "netvm") is not None:
                vm.netvm = None
            import qubesadmin.firewall
            vm.firewall.rules = [qubesadmin.firewall.Rule(None, action="accept")]  # défaut de Qubes
            # Seules restent les étiquettes que qubesd pose lui-même pour ce
            # clone : created-by-<appelant> (absente de la source) et
            # guivm-/audiovm- de ses valeurs actuelles.
            gardees = {f"guivm-{_nom(_prop(vm, 'guivm'))}", f"audiovm-{_nom(_prop(vm, 'audiovm'))}"}
            for t in list(vm.tags):
                if not (t in gardees or (t.startswith("created-by-") and t not in src.tags)):
                    vm.tags.discard(t)
        except (self.exc.QubesException, AttributeError, TypeError) as e:
            self._appel(lambda: self.app.domains.__delitem__(name), f"{name} : ")
            raise BackendError(B.AUTRE, f"{name} : clone retiré, nettoyage impossible : {type(e).__name__}") from None
        return self._appel(lambda: self._info(vm), f"{name} : ")

    def start(self, name):
        vm = self._vm(name)
        if vm.get_power_state() != "Halted":
            raise BackendError(B.DEJA_DEMARRE, f"{name} est déjà démarré")
        self._appel(vm.start, f"{name} : ")

    def shutdown(self, name):
        # TODO : attendre l'arrêt (wait=True) ? À trancher avec l'exécuteur.
        self._appel(self._vm(name).shutdown, f"{name} : ")

    def remove(self, name):
        self._vm(name)
        self._appel(lambda: self.app.domains.__delitem__(name), f"{name} : ")

    def set_netvm(self, name, netvm):
        vm = self._vm(name)
        if netvm is not None:
            self._vm(netvm)
        try:
            vm.netvm = netvm
        except self.exc.QubesVMNotStartedError as e:
            raise BackendError(B.NETVM_ARRETEE, f"{name} : {e}") from None
        except self.exc.QubesException as e:
            raise self._err(e, f"{name} : ") from None

    def run_dispvm_task(self, template, task):
        """Jetable créé sur un modèle NOMMÉ et admis (admin.vm.CreateDisposable
        visant le modèle, jamais dom0 : qubesd prendrait alors default_dispvm),
        démarré par qubesd, puis qaf-gate-taches (H3) confie la tâche à qaf.Task,
        qui répond aussitôt. Une seule échéance pour tout (H2). Le coureur
        éteint le jetable à la fin ; qubesd le supprime (auto_cleanup=True,
        qubes/vm/dispvm.py gen_disposable)."""
        fin = self.horloge() + self.delai_tache
        nom, vm = self._jetable(template)
        try:
            vm.start()  # borné par qubesd (qrexec_timeout du jetable), pas par nous
            reste = int(fin - self.horloge())
            if reste < 1:
                raise BackendError(B.AUTRE, f"{nom} : délai épuisé avant de confier la tâche")
            rep = self.taches({"type": "tache", "jetable": nom, "tache": task, "delai": reste}, reste + 5)
            if rep.get("accepte") is not True:
                raison = str(rep.get("raison"))[:200]
                raise BackendError(B.AUTRE, f"{nom} : tâche non confiée ({raison})")
        except Exception as e:
            self._abandonner(nom, vm)
            if isinstance(e, BackendError):
                raise
            if isinstance(e, self.exc.QubesException):
                raise self._err(e, f"{nom} : ") from None
            raise BackendError(B.AUTRE, f"{nom} : qaf-gate-taches : {type(e).__name__}") from None
        return nom

    def _jetable(self, template):
        """Jetable créé (pas démarré) sur un modèle NOMMÉ et admis."""
        modele = template or self.modele_jetable
        if modele not in self.modeles_admis:  # H1 : liste blanche de dom0, en plus du classement
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle de jetable admis")
        return self._creer(modele, sans_reseau=modele == self.modele_jetable)

    def _creer(self, modele, sans_reseau=False):
        if modele not in self.app.domains:
            raise BackendError(B.TEMPLATE_ABSENT, f"modèle de jetable introuvable : {modele}")
        m = self.app.domains[modele]
        if m.klass != "AppVM" or not _prop(m, "template_for_dispvms", False):
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle de jetable")
        if sans_reseau and _prop(m, "netvm") is not None:
            raise BackendError(B.AUTRE, f"{modele} a un réseau : il doit rester sans réseau")
        brut = self._appel(lambda: self.app.qubesd_call(modele, "admin.vm.CreateDisposable"),
                           f"{modele} : ")
        nom = brut.decode("ascii", "replace")
        if not _NOM_RE.match(nom):
            raise BackendError(B.AUTRE, "nom de jetable inattendu")
        # get_blind : le cache de noms de qubesadmin ignore encore ce qube.
        return nom, self.app.domains.get_blind(nom)

    def _abandonner(self, nom, vm):
        """Jetable inutilisable : kill (auto_cleanup le supprime) ; s'il n'a
        jamais démarré, suppression directe."""
        try:
            vm.kill()
        except self.exc.QubesVMNotStartedError:
            try:
                del self.app.domains[nom]
            except self.exc.QubesException:
                pass  # déjà supprimé par qubesd (échec du démarrage, DispVM.start)
        except self.exc.QubesException:
            pass

    # --- OPS-v3 ----------------------------------------------------------------

    def _ecrire(self, obj, prop, ecrire, contexte):
        """Écrit une propriété et renvoie la valeur d'avant (§3.7)."""
        avant = {"valeur": _nom(_prop(obj, prop)), "defaut": None}
        try:
            avant["defaut"] = bool(obj.property_is_default(prop))
        except (AttributeError, self.exc.QubesException):
            pass
        self._appel(ecrire, contexte)
        return {"avant": avant}

    def prefs(self):
        return self._appel(lambda: {k: _nom(_prop(self.app, k)) for k in B.GLOBAUX}, "qubes-prefs : ")

    def settings(self, name):
        vm = self._vm(name)

        def lire():
            prive = vm.volumes["private"].size if "private" in vm.volumes else None
            return {"proprietes": {p: _nom(_prop(vm, p)) for p in schema.PROPS},
                    "services": {s: vm.features.get("service." + s, None) for s in schema.SERVICES},
                    "tags": sorted(str(t) for t in vm.tags),
                    "prive_mio": None if prive is None else int(prive) // (1024 * 1024)}
        return self._appel(lire, f"{name} : ")

    def set_prop(self, name, prop, value):
        vm = self._vm(name)
        return self._ecrire(vm, prop, lambda: setattr(vm, prop, value), f"{name} : ")

    def reset_prop(self, name, prop):
        vm = self._vm(name)
        return self._ecrire(vm, prop, lambda: delattr(vm, prop), f"{name} : ")

    def set_service(self, name, service, state):
        vm, cle = self._vm(name), "service." + service

        def ecrire():
            if state != "default":
                vm.features[cle] = "1" if state == "on" else ""  # comme qvm-service
            elif cle in vm.features:
                del vm.features[cle]
        avant = self._appel(lambda: vm.features.get(cle, None), f"{name} : ")
        self._appel(ecrire, f"{name} : ")
        return {"avant": avant}

    def set_pref(self, prop, value):
        return self._ecrire(self.app, prop, lambda: setattr(self.app, prop, value), "qubes-prefs : ")

    def update_status(self, targets):
        noms = targets or [vm.name for vm in self.app.domains]

        def lire(n):
            f = self._vm(n).features
            # P-8 : en 4.3, last-update (pas last-updated) et last-updates-check.
            return {"name": n, **{k.replace("-", "_"): f.get(k, None) for k in (
                "updates-available", "last-update", "last-updates-check", "skip-update", "prohibit-start")}}
        return self._appel(lambda: [lire(n) for n in noms], "")

    def sys_status(self):
        def lire():
            qubes = []
            for vm in self.app.domains:
                if vm.get_power_state() == "Running":
                    try:
                        mem = int(vm.get_mem())
                    except (AttributeError, ValueError, self.exc.QubesException):
                        mem = None
                    qubes.append({"name": vm.name, "memoire_kio": mem})
            pools = []
            for nom in self.app.pools:
                pool = self.app.pools[nom]
                try:
                    pools.append({"name": str(nom), "taille": int(pool.size), "utilise": int(pool.usage)})
                except (AttributeError, TypeError, ValueError, self.exc.QubesException):
                    pools.append({"name": str(nom), "taille": None, "utilise": None})
            return {"qubes": qubes, "pools": pools}
        return self._appel(lire, "")

    # Pare-feu : toujours sur les objets Rule de qubesadmin, pour garder intacts
    # les champs que l'agent ne voit pas (icmptype, expire, comment).
    def _regle(self, d):
        import qubesadmin.firewall
        champs = {"action": d["action"]}
        if d.get("dns"):
            champs["specialtarget"] = "dns"
        for cle, nom in (("host", "dsthost"), ("proto", "proto"), ("ports", "dstports")):
            if cle in d:
                champs[nom] = d[cle]
        return qubesadmin.firewall.Rule(None, **champs)

    @staticmethod
    def _en_dict(r):
        d = {"action": str(r.action)}
        for nom, cle in (("dsthost", "host"), ("proto", "proto"), ("dstports", "ports")):
            v = getattr(r, nom, None)
            if v is not None:
                d[cle] = str(getattr(v, "pretty_value", v))
        if str(getattr(r, "specialtarget", None) or "") == "dns":
            d["dns"] = True
        if any(getattr(r, k, None) is not None for k in ("icmptype", "expire", "comment")):
            d["autres_champs"] = True
        return d

    def fw_list(self, name):
        return self._appel(lambda: [self._en_dict(r) for r in self._vm(name).firewall.rules], f"{name} : ")

    def _fw_ecrire(self, name, changer):
        vm = self._vm(name)

        def faire():
            regles = list(vm.firewall.rules)
            avant = hashlib.sha256("\n".join(str(r.rule) for r in regles).encode()).hexdigest()
            changer(regles)
            vm.firewall.rules = regles  # admin.vm.firewall.Set, comme qvm-firewall
            return {"avant": avant}
        return self._appel(faire, f"{name} : ")

    def fw_add(self, name, position, rule):
        def inserer(regles):
            if len(regles) >= 64:
                raise BackendError(B.AUTRE, f"{name} : 64 règles au plus")
            regles.insert(position, self._regle(rule))
        return self._fw_ecrire(name, inserer)

    def fw_remove(self, name, position, rule):
        def retirer(regles):
            if position >= len(regles) or str(regles[position].rule) != str(self._regle(rule).rule):
                raise BackendError(B.REGLE_CHANGEE, f"{name} : la règle à la position {position} a changé")
            del regles[position]
        return self._fw_ecrire(name, retirer)

    def dev_list(self, classe):
        classes = [classe] if classe else ["usb", "block", "mic"]

        def port(d):
            return str(getattr(d, "port_id", None) or getattr(d, "ident", ""))

        def lire():
            vms = [vm for vm in self.app.domains if vm.name == "dom0" or vm.get_power_state() == "Running"]
            attaches = {}
            for vm in vms:
                for c in classes:
                    coll = vm.devices[c]
                    for d in (getattr(coll, "get_attached_devices", None) or coll.attached)():
                        attaches[(c, d.backend_domain.name, port(d))] = vm.name
            out = []
            for vm in vms:  # backends démarrés seulement : rien n'est démarré pour lister
                for c in classes:
                    coll = vm.devices[c]
                    for d in (getattr(coll, "get_exposed_devices", None) or coll.available)():
                        out.append({"class": c, "backend": vm.name, "port": port(d),
                                    "device_id": str(getattr(d, "device_id", "") or ""),
                                    "attached_to": attaches.get((c, vm.name, port(d)))})
            return out
        return self._appel(lire, "")

    def restart(self, name, delai=60):
        vm = self._vm(name)
        self._appel(vm.shutdown, f"{name} : ")  # netvm avec clients : en_usage, jamais --force
        fin = self.horloge() + delai
        while self._appel(vm.get_power_state, f"{name} : ") != "Halted":
            if self.horloge() > fin:
                raise BackendError(B.AUTRE, f"{name} ne s'est pas arrêté en {delai} s")
            time.sleep(0.5)
        self._appel(vm.start, f"{name} : ")

    def set_template(self, name, template):
        vm = self._vm(name)
        if self._vm(template).klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un template")
        return self._ecrire(vm, "template", lambda: setattr(vm, "template", template), f"{name} : ")

    def pkg_install(self, name, packages):
        return self._pkg(name, packages, "pkg.install")

    def pkg_remove(self, name, packages):
        return self._pkg(name, packages, "pkg.remove")

    def _pkg(self, name, packages, action):
        """Comme Qubes Update : démarre le modèle s'il est arrêté, installe (ou
        retire) par qubes.VMExec à argv fixe (qaf-gate-taches), puis l'arrête."""
        fin = self.horloge() + DELAI_PKG  # démarrage compris : l'opération tient dans DUREE_MAX_OP
        vm = self._vm(name)
        if vm.klass not in ("TemplateVM", "StandaloneVM"):
            raise BackendError(B.PAS_UN_TEMPLATE, f"{name} : modèle ou StandaloneVM attendu")
        distro = DISTROS.get(str(self._appel(lambda: vm.features.get("os-distribution", ""), f"{name} : ")))
        if distro is None:
            raise BackendError(B.DISTRIBUTION, f"{name} : distribution inconnue")
        demarre = vm.get_power_state() == "Halted"
        if demarre:
            self._appel(vm.start, f"{name} : ")
        try:
            reste = int(fin - self.horloge())
            if reste < 1:
                raise BackendError(B.AUTRE, f"{name} : délai épuisé avant l'installation")
            rep = self.taches({"type": "vmexec", "qube": name, "action": action, "distro": distro,
                               "paquets": list(packages), "delai": reste}, reste + 5)
        finally:
            if demarre:
                try:
                    vm.shutdown()
                except self.exc.QubesException:
                    pass
        codes = [c for c in rep.get("codes", []) if type(c) is int][:4]
        if rep.get("ok") is not True:
            raise BackendError(B.AUTRE, f"{name} : {action} non abouti (codes {codes})")
        return {"codes": codes}

    def create_dispvm(self, template):
        nom, vm = self._jetable(template)
        try:
            vm.start()
        except self.exc.QubesException as e:
            self._abandonner(nom, vm)
            raise self._err(e, f"{nom} : ") from None
        return self._appel(lambda: self._info(vm), f"{nom} : ")

    def creer_agent(self, modele):
        """CreateDisposable sur le modèle d'agent NOMMÉ, sans démarrer (S-J3 :
        l'étiquette ne se pose qu'après la création)."""
        if modele not in schema.MODELES_AGENT:
            raise BackendError(B.PAS_UN_TEMPLATE, f"{modele} n'est pas un modèle d'agent")
        nom, vm = self._creer(modele)
        # Le cache de noms de qubesadmin ignore ce qube : sans ceci, tag() et
        # start() par son nom répondent « introuvable » (Qubes 4.3.2, 25/10).
        self.app.domains.clear_cache()
        return self._appel(lambda: self._info(vm), f"{nom} : ")

    def tuer(self, name):
        self._abandonner(name, self.app.domains.get_blind(name))

    def precharge(self, name):
        return bool(self._appel(lambda: self._vm(name).features.get("preload-dispvm-completed", None),
                                f"{name} : "))

    def hand(self, name, service, requete, delai):
        vm = self._vm(name)
        if vm.get_power_state() != "Running":
            raise BackendError(B.PAS_DEMARRE, f"{name} n'est pas démarré")
        rep = self.taches({"type": "hand", "qube": name, "service": service, "requete": requete,
                           "delai": delai}, delai + 5)
        if rep.get("ok") is not True:
            raise BackendError(B.AUTRE, f"{name} : {service} a échoué (code {rep.get('code')})")
        return {"code": rep.get("code"), "octets": rep.get("octets"), "b64": rep.get("b64")}

    # --- Lots 2 et 3 -----------------------------------------------------------

    def _outil(self, msg, delai=15):
        """Message fermé à qaf-gate-outils, qui construit lui-même l'argv (D13)."""
        try:
            rep = self.outils(msg, delai)
        except (OSError, ValueError) as e:
            raise BackendError(B.AUTRE, f"qaf-gate-outils : {type(e).__name__}") from None
        if rep.get("ok") is not True:
            raise BackendError(B.AUTRE, f"qaf-gate-outils : {str(rep.get('raison'))[:200]}")
        return rep

    def update_run(self, targets, apply):
        ignorees = []
        for t in targets:
            vm = self._vm(t)
            if vm.klass not in ("TemplateVM", "StandaloneVM"):
                raise BackendError(B.PAS_UN_TEMPLATE, f"{t} : modèle ou StandaloneVM attendu")
            if self._appel(lambda: vm.features.get("prohibit-start", None), f"{t} : "):
                ignorees.append(t)  # P-1 : qubes-vm-update la sauterait sans le dire
        cibles = [t for t in targets if t not in ignorees]
        if not cibles:
            raise BackendError(B.AUTRE, "toutes les cibles ont prohibit-start : rien n'est lancé")
        r = self._outil({"type": "update", "cibles": cibles, "apply": apply})
        return {"travail": r["travail"], "ignorees_prohibit_start": ignorees}

    def template_available(self, repo):
        return self._outil({"type": "template.list", "repo": repo}, DELAI_OUTILS)["modeles"]

    def template_remove(self, template):
        if self._vm(template).klass != "TemplateVM":
            raise BackendError(B.PAS_UN_TEMPLATE, f"{template} n'est pas un modèle")
        return {"travail": self._outil({"type": "template.remove", "nom": template})["travail"]}

    def backup_run(self, profile):
        return {"travail": self._outil({"type": "backup", "profil": profile})["travail"]}

    def backup_info(self, profile):
        r = self._outil({"type": "backup.info", "profil": profile})
        return {k: r.get(k) for k in ("destination_vm", "destination_path", "passphrase_vm")}

    def backup_profiles(self):
        return self._outil({"type": "backup.list"})["profils"]

    def job_status(self, job):
        return self._outil({"type": "job.status", "travail": job})

    def job_cancel(self, job):
        self._outil({"type": "job.cancel", "travail": job})
        return {"annulation": "demandée"}

    def _dans(self, name, action, **champs):
        """Outil de Qubes lancé dans un qube démarré, en utilisateur (qaf-gate-taches)."""
        vm = self._vm(name)
        if vm.get_power_state() != "Running":
            raise BackendError(B.PAS_DEMARRE, f"{name} n'est pas démarré")
        user = str(self._appel(lambda: vm.default_user, f"{name} : "))
        rep = self.taches({"type": "vmexec", "qube": name, "action": action, "utilisateur": user,
                           "delai": DELAI_FICHIER, **champs}, DELAI_FICHIER + 5)
        codes = [c for c in rep.get("codes", []) if type(c) is int][:1]
        if rep.get("ok") is not True:
            raise BackendError(B.AUTRE, f"{name} : {action} non abouti (code {codes}, {str(rep.get('raison'))[:100]})")
        return {"code": codes[0] if codes else None}

    def file_copy(self, source, path, dest, move):
        self._vm(dest)
        return self._dans(source, "file.move" if move else "file.copy", dest=dest, chemin=path)

    def file_open_dispvm(self, name, path):
        if _prop(self._vm(name), "default_dispvm") is None:
            raise BackendError(B.AUTRE, f"{name} n'a pas de jetable par défaut (U46)")
        return self._dans(name, "file.open_dispvm", chemin=path)

    def _assignation(self, classe, backend, port, device_id, options=None):
        from qubesadmin.device_protocol import DeviceAssignment, Port, VirtualDevice
        dev = VirtualDevice(Port(self._vm(backend), port, classe), device_id=device_id)
        return DeviceAssignment(dev, options=options or {}, mode="manual")

    def dev_attach(self, classe, backend, port, device_id, target, read_only):
        vm, be = self._vm(target), self._vm(backend)

        def faire():
            vus = {(str(getattr(d, "port_id", "")), str(getattr(d, "device_id", "")))
                   for d in be.devices[classe].get_exposed_devices()}
            if (port, device_id) not in vus:  # V8 : la confirmation porte sur ce périphérique-là
                raise BackendError(B.PERIPHERIQUE_CHANGE, f"{backend}:{port} n'expose plus {device_id}")
            opts = {"read-only": "yes" if read_only else "no"} if classe == "block" else {}
            vm.devices[classe].attach(self._assignation(classe, backend, port, device_id, opts))
        self._appel(faire, f"{target} : ")  # attache temporaire, jamais assign (§2)

    def dev_detach(self, classe, backend, port, target):
        vm = self._vm(target)
        self._appel(lambda: vm.devices[classe].detach(self._assignation(classe, backend, port, "*")),
                    f"{target} : ")

    def tag(self, name, tag, action):
        vm = self._vm(name)
        avant = self._appel(lambda: tag in vm.tags, f"{name} : ")
        self._appel(lambda: vm.tags.add(tag) if action == "add" else vm.tags.discard(tag), f"{name} : ")
        return {"avant": avant}

    def grow_private(self, name, size_mib):
        vm, octets = self._vm(name), size_mib * 1024 * 1024

        def faire():
            vol = vm.volumes["private"]
            avant, pool = int(vol.size), self.app.pools[vol.pool]
            if octets <= avant:
                raise BackendError(B.AUTRE, f"{name} : agrandir seulement (actuel {avant // 2**20} Mio)")
            if int(pool.usage) + octets - avant > 0.9 * int(pool.size):
                raise BackendError(B.AUTRE, f"{name} : le pool {vol.pool} passerait sous 10 % d'espace libre")
            vol.resize(octets)
            return {"avant": avant // 2**20}
        return self._appel(faire, f"{name} : ")
