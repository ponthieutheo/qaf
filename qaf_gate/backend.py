"""Interface Backend : tout ce qui touche qubesd passe par ici.

Implémentations : backend_qubes.QubesBackend (dom0, via qubesadmin) et
SimBackend dans tests/backend_sim.py (tests seulement, jamais installé).
"""

from dataclasses import dataclass

# Propriétés globales lues par prefs.get et par le classement (qubes-prefs).
GLOBAUX = ("default_netvm", "default_template", "default_dispvm", "clockvm", "updatevm", "check_updates_vm",
           "management_dispvm", "default_guivm", "default_audiovm", "default_kernel", "default_pool")

# États renvoyés par qubesadmin QubesVM.get_power_state().
ETATS = ("Halted", "Transient", "Running", "Paused", "Suspended", "Halting", "Dying", "Crashed", "NA")

# Codes d'erreur stables (le message, lui, est libre).
INTROUVABLE = "introuvable"
NOM_PRIS = "nom_pris"
TEMPLATE_ABSENT = "template_absent"
PAS_UN_TEMPLATE = "pas_un_template"
PAS_UN_NETVM = "pas_un_netvm"
NETVM_ARRETEE = "netvm_arretee"
DEJA_DEMARRE = "deja_demarre"
PAS_DEMARRE = "pas_demarre"
PAS_ARRETE = "pas_arrete"
EN_USAGE = "en_usage"
LABEL_INCONNU = "label_inconnu"
NON_IMPLEMENTE = "non_implemente"
REGLE_CHANGEE = "regle_changee"  # fw.remove : la règle à cette position n'est plus celle vue
PERIPHERIQUE_CHANGE = "peripherique_change"  # dev.attach : plus ce périphérique à ce port
DISTRIBUTION = "distribution_inconnue"
AUTRE = "autre"


class BackendError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code} : {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QubeInfo:
    name: str
    uuid: str
    klass: str  # AdminVM, AppVM, TemplateVM, DispVM, StandaloneVM
    state: str  # voir ETATS
    label: str
    template: str | None  # None si la classe n'a pas de template
    netvm: str | None  # None = aucun réseau
    provides_network: bool
    # OPS-v3, classes de cible (§3.2) et dépendances (R-C, R-E).
    template_for_dispvms: bool = False
    default_dispvm: str | None = None
    default_dispvm_herite: bool = True  # propriété laissée à la valeur globale
    guivm: str | None = None
    audiovm: str | None = None
    management_dispvm: str | None = None
    # Étiquettes (qubesd, jamais le qube) : identité d'un jetable d'agent et
    # héritage par dispvm.create (AGENT-JETABLE §5.3, R-6).
    tags: tuple = ()
    livre: bool = False  # n17 : modèle livré par Qubes (paquet ou qvm-template), toujours à l'utilisateur


class Backend:
    """Méthodes minimales pour les opérations du MVP. Erreurs : BackendError."""

    def list_qubes(self) -> list[QubeInfo]:
        raise NotImplementedError

    def info(self, name: str) -> QubeInfo:
        raise NotImplementedError

    def create_appvm(self, name: str, template: str, label: str) -> QubeInfo:
        """Crée une AppVM SANS réseau (netvm = None, correction C2)."""
        raise NotImplementedError

    def start(self, name: str) -> None:
        raise NotImplementedError

    def shutdown(self, name: str) -> None:
        raise NotImplementedError

    def remove(self, name: str) -> None:
        raise NotImplementedError

    def set_netvm(self, name: str, netvm: str | None) -> None:
        raise NotImplementedError

    def run_dispvm_task(self, template: str | None, task: str) -> str:
        """Lance une disposable (template None = modèle dédié du Doorman, jamais
        default_dispvm) et y confie la tâche. Renvoie le nom de la disposable."""
        raise NotImplementedError

    # --- OPS-v3, lot 1 et lot 2 sans décision en attente ---------------------
    # Écritures : renvoient {"avant": valeur lue dans qubesd juste avant} (§3.7).

    def prefs(self) -> dict:
        """Propriétés globales GLOBAUX ; un qube est donné par son nom."""
        raise NotImplementedError

    def settings(self, name: str) -> dict:
        """{"proprietes", "services" (valeurs brutes des fonctions), "tags", "prive_mio"}."""
        raise NotImplementedError

    def set_prop(self, name, prop, value) -> dict:
        raise NotImplementedError

    def reset_prop(self, name, prop) -> dict:
        raise NotImplementedError

    def set_service(self, name, service, state) -> dict:
        raise NotImplementedError

    def set_pref(self, prop, value) -> dict:
        raise NotImplementedError

    def update_status(self, targets) -> list:
        """[{"name", "updates_available", "last_update", "last_updates_check",
        "skip_update", "prohibit_start"}], valeurs brutes des fonctions
        (normalisées par l'exécuteur, V9). targets vide = tous."""
        raise NotImplementedError

    def sys_status(self) -> dict:
        raise NotImplementedError

    def fw_list(self, name) -> list:
        raise NotImplementedError

    def fw_add(self, name, position, rule) -> dict:
        raise NotImplementedError

    def fw_remove(self, name, position, rule) -> dict:
        """Retire la règle seulement si celle à `position` est exactement `rule`."""
        raise NotImplementedError

    def dev_list(self, classe) -> list:
        """[{"class", "backend", "port", "device_id", "attached_to"}], tel
        qu'annoncé par les backends (filtré par l'exécuteur, V9)."""
        raise NotImplementedError

    def restart(self, name) -> None:
        raise NotImplementedError

    def set_template(self, name, template) -> dict:
        raise NotImplementedError

    def pkg_install(self, name, packages) -> dict:
        raise NotImplementedError

    def create_dispvm(self, template: str | None) -> QubeInfo:
        """Jetable démarré sur un modèle nommé et admis (template None = MODELE_JETABLE)."""
        raise NotImplementedError

    def creer_agent(self, modele: str) -> QubeInfo:
        """Jetable d'agent créé sur un modèle de schema.MODELES_AGENT, PAS
        démarré : l'exécuteur l'inscrit, l'étiquette puis le démarre (§1)."""
        raise NotImplementedError

    def tuer(self, name: str) -> None:
        """Jetable arrêté net (kill, puis supprimé par Qubes) ; sans erreur s'il a disparu."""
        raise NotImplementedError

    def precharge(self, name: str) -> bool:
        """Jetable préchargé en attente (fonction preload-dispvm-completed)."""
        raise NotImplementedError

    def hand(self, name, service, requete, delai) -> dict:
        """Appel de qaf_hand dans un qube démarré par qaf-gate-taches :
        {"ok", "code", "octets", "b64"}, blob opaque jamais lu dans dom0 (§3.4)."""
        raise NotImplementedError


# --- Lots 2 et 3 d'OPS-v3 ---------------------------------------------------
# Travaux longs (qaf-gate-outils, §3.5) : renvoient {"travail": "T-xxxxxx", …}.
#   update_run(targets, apply)          qubes-vm-update, cibles TemplateVM/StandaloneVM
#   template_available(repo) -> list    [{"nom", "version", "depot"}] filtré ligne par ligne
#   template_remove(template)           qvm-template --yes remove
#   backup_run(profile)                 qvm-backup --yes --profile
#   job_status(job) -> {"etat", "code", "duree"} ; job_cancel(job)
#   backup_profiles() -> [{"profil", "destination_vm"}]
#   backup_info(profile) -> {"destination_vm", "destination_path", "passphrase_vm"} (qaf-gate-outils seul)
# Dans un qube, par qaf-gate-taches (qubes.VMExec à argv fixe) :
#   file_copy(source, path, dest, move) ; file_open_dispvm(name, path) ; pkg_remove(name, packages)
# Admin API : dev_attach(classe, backend, port, device_id, target, read_only),
#   dev_detach(classe, backend, port, target), tag(name, tag, action) -> {"avant"},
#   grow_private(name, size_mib) -> {"avant"}.
# PARCOURS-1 : clone_template(source, name, label) -> QubeInfo, copie d'un
#   TemplateVM SANS réseau (C2), comme qvm-clone.
METHODES_V3 = ("clone_template", "update_run", "template_available", "template_remove", "backup_run",
               "job_status", "job_cancel", "backup_profiles", "backup_info", "file_copy", "file_open_dispvm",
               "pkg_remove", "dev_attach", "dev_detach", "tag", "grow_private")


def _non_implemente(self, *args):
    raise NotImplementedError


for _m in METHODES_V3:
    setattr(Backend, _m, _non_implemente)
