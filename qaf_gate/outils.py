"""qaf-gate-outils : lance les outils de Qubes à argv fixe (D13, OPS-v3 §3.1, §3.5).

Pourquoi un processus à part : qubes-vm-update, qvm-template et qvm-backup
demandent à la fois qubesd (groupe qubes) et le vchan (qrexec-client vers les
qubes et l'updatevm). Aucun des deux autres processus n'a les deux (H3). On
réutilise les outils de Qubes, mêmes chemins que Qubes Update (Règle n°1),
plutôt que de les réimplémenter. Compte qaf-outils, sans X, sans sudo, sans
root (P-1, P-2), sandbox de packaging/qaf-gate-outils.service.

Seul client admis : l'exécuteur (uid qaf-exec, SO_PEERCRED). Il n'envoie que des
messages d'un tuple fermé (MESSAGES), aux champs typés et revalidés ici ;
l'argv est construit ICI, jamais reçu. Aucune sortie d'outil n'est lue, sauf
celle de « qvm-template list », filtrée ligne par ligne (regex stricte, 256
lignes au plus) : elle vient de l'updatevm (C6), l'opération est influençante.

Un seul travail à la fois, tenu en mémoire : {"travail": "T-xxxxxx"} tiré ici.
Un arrêt du service envoie SIGTERM au travail en cours, comme une interruption
à la main (OPS-v3 §14). Les profils de sauvegarde (/etc/qubes/backup, en clair,
phrase de passe comprise) ne sont ouverts que par ce processus, et seules les
lignes destination_vm, destination_path et passphrase_vm en sortent (P-3).
"""

import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

from . import proto, schema, taches

SOCK_OUTILS = "/run/qaf-outils/outils.sock"  # créé par qaf-gate-outils.socket
HOME = "/var/lib/qaf-outils"  # HOME et dossier de travail à lui (P-2 : cache, --downloaddir .)
ENV = {"HOME": HOME, "PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "LANG": "C.UTF-8"}
PROFILS = "/etc/qubes/backup"
QVU, QVT, QVB = "/usr/bin/qubes-vm-update", "/usr/bin/qvm-template", "/usr/bin/qvm-backup"
APPLY = {"none": "--no-apply", "sys": "--apply-to-sys", "all": "--apply-to-all"}
DEPOTS = {"official": [], "community": ["--enablerepo=qubes-templates-community"]}  # jamais *-testing
MAX_LISTE, MAX_SORTIE_LISTE = 256, 256 * 1024
DELAI_LISTE = proto.DUREE_MAX_OP["template.available"] - 15
DELAI_KILL = 30  # job.cancel : SIGTERM, puis SIGKILL après 30 s
MAX_TRAVAUX = 16  # travaux finis gardés en mémoire pour job.status
# « état|nom|époque:version|dépôt » (P-2) ; dépôts testing jamais.
_LIGNE = re.compile(r"\Aavailable\|([a-zA-Z][a-zA-Z0-9_.-]{0,30})\|([0-9]{1,4}:[A-Za-z0-9._+~-]{1,64})"
                    r"\|(qubes-templates-itl|qubes-templates-community)\Z")
_CLE_PROFIL = re.compile(r"\A(destination_vm|passphrase_vm): ([a-zA-Z][a-zA-Z0-9_.-]{0,30})\Z")
# Chemin affiché dans la fenêtre (U2) : scalaire simple, sans commentaire ni caractère de contrôle.
_CHEMIN = re.compile(r"\A(destination_path): ([^\s\"'&*!|>{\[%@`#][^\x00-\x1f\x7f#]{0,255})(?<!\s)\Z")
_ANCRE = re.compile(r"[^\s#-][^:]*:\s*[&*!]")  # ancre, alias ou étiquette YAML en valeur
# Pas d'installation de modèle : qvm-template importe le volume par sudo, que qaf-outils
# n'a pas et n'aura pas (RESULTATS-V2) ; template.install est N4, l'utilisateur l'installe.
MESSAGES = {"update": ("cibles", "apply"), "template.list": ("repo",), "template.remove": ("nom",),
            "backup": ("profil",), "backup.info": ("profil",), "backup.list": (),
            "job.status": ("travail",), "job.cancel": ("travail",)}


def _cible(v):
    if schema._name(v, "cible") == "dom0":
        raise ValueError("dom0 n'est jamais une cible")  # mise à jour de dom0 : N4 (§2)
    return v


def _depot(v):
    return DEPOTS[schema._REPO(v, "repo")]


def lire_profil(texte):
    """destination_vm, destination_path et passphrase_vm d'un profil YAML de qvm-backup, lus en
    lecture stricte : toute autre forme (clé entre guillemets, ancre, alias,
    fusion, flux, valeur multiligne, doublon) qui pourrait les redéfinir = refus."""
    vus = {}
    for ligne in texte.splitlines():
        if (ligne[:1] and ligne[0] in "\"'?{[&*!%|>@`<") or _ANCRE.match(ligne):
            raise ValueError("profil : forme YAML non admise")
        if "destination_vm" in ligne or "passphrase_vm" in ligne or "destination_path" in ligne:
            m = _CLE_PROFIL.match(ligne) or _CHEMIN.match(ligne)
            if not m or m[1] in vus:
                raise ValueError("profil : ligne destination_vm, destination_path ou passphrase_vm non admise")
            vus[m[1]] = m[2]
    if "destination_vm" not in vus:
        raise ValueError("profil : destination_vm absente")
    return {"destination_vm": vus["destination_vm"], "destination_path": vus.get("destination_path"),
            "passphrase_vm": vus.get("passphrase_vm")}


def _profil(nom):
    with open(os.path.join(PROFILS, schema._profil(nom, "profil") + ".conf"), encoding="utf-8") as f:
        return lire_profil(f.read(64 * 1024))


def _annuler_sauvegarde(profil):
    import qubesadmin  # import paresseux : seulement pour annuler une sauvegarde
    import qubesadmin.exc
    try:
        qubesadmin.Qubes().qubesd_call("dom0", "admin.backup.Cancel", profil)
    except qubesadmin.exc.QubesException:
        pass  # plus rien en cours (P-3)


class Outils:
    def __init__(self, lancer=subprocess.Popen, horloge=time.monotonic, annuler=_annuler_sauvegarde,
                 tuer=os.killpg):
        self.lancer, self.horloge, self.annuler, self.tuer = lancer, horloge, annuler, tuer
        self.travaux = {}  # T-xxxxxx -> {"p", "type", "profil", "debut", "fin", "code", "annule"}
        self._verrou = threading.Lock()

    def traiter(self, msg):
        t = msg.get("type") if isinstance(msg, dict) else None
        try:
            if not isinstance(t, str) or t not in MESSAGES or set(msg) != {"type", *MESSAGES[t]}:
                raise ValueError("message inconnu")
            return {"ok": True, **self._traiter(t, msg)}
        except (ValueError, OSError, schema.PlanError) as e:
            return {"ok": False, "raison": str(e)[:200]}

    def _traiter(self, t, m):
        if t == "update":
            cibles = [_cible(c) for c in schema._noms(1, 32)(m["cibles"], "cibles")]
            apply = APPLY[schema._choix(*APPLY)(m["apply"], "apply")]
            return self._lancer(t, [QVU, "--quiet", "--no-progress", apply, "--targets", ",".join(cibles)])
        if t == "template.list":
            return {"modeles": self._liste(_depot(m["repo"]))}
        if t == "template.remove":  # jamais purge ni --disassoc : Qubes refuse s'il sert encore
            return self._lancer(t, [QVT, "--yes", "remove", "--", _cible(m["nom"])])
        if t == "backup":
            _profil(m["profil"])  # existe et se lit strictement
            return self._lancer(t, [QVB, "--yes", "--profile", m["profil"]], m["profil"])
        if t == "backup.info":
            return _profil(m["profil"])
        if t == "backup.list":
            noms = sorted(f[:-5] for f in os.listdir(PROFILS) if f.endswith(".conf"))
            return {"profils": [{"profil": n, "destination_vm": self._destination(n)} for n in noms
                                if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n)][:64]}
        travail = self.travaux.get(schema._job(m["travail"], "travail"))
        if travail is None:
            raise ValueError("travail inconnu")
        if t == "job.cancel":
            self._arreter(travail)
        fin = travail["fin"] or self.horloge()
        etat = "en_cours" if travail["code"] is None else "annule" if travail["annule"] else "fini"
        return {"etat": etat, "code": travail["code"], "duree": int(fin - travail["debut"])}

    @staticmethod
    def _destination(nom):
        try:
            return _profil(nom)["destination_vm"]
        except (ValueError, OSError):
            return None

    def _lancer(self, type_, argv, profil=None):
        with self._verrou:
            if any(t["code"] is None for t in self.travaux.values()):
                raise ValueError("un travail est déjà en cours : attends sa fin (job.status)")
            p = self.lancer(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=HOME, env=ENV, start_new_session=True)
            tid = None
            while tid is None or tid in self.travaux:
                tid = f"T-{secrets.randbelow(10**6):06d}"
            travail = {"p": p, "type": type_, "profil": profil, "debut": self.horloge(), "fin": None,
                       "code": None, "annule": False}
            for vieux in [k for k, t in self.travaux.items() if t["code"] is not None][:-MAX_TRAVAUX + 1]:
                del self.travaux[vieux]
            self.travaux[tid] = travail
        threading.Thread(target=self._attendre, args=(travail,), daemon=True).start()
        return {"travail": tid}

    def _attendre(self, travail):
        code = travail["p"].wait()
        travail["fin"], travail["code"] = self.horloge(), code

    def _arreter(self, travail):
        if travail["code"] is not None:
            return
        travail["annule"] = True
        if travail["type"] == "backup":
            self.annuler(travail["profil"])  # admin.backup.Cancel (P-3)
        pid = travail["p"].pid
        self._signal(pid, signal.SIGTERM)
        threading.Timer(DELAI_KILL, lambda: travail["code"] is None and self._signal(pid, signal.SIGKILL)).start()

    def _signal(self, pid, sig):
        try:
            self.tuer(pid, sig)  # tout le groupe : l'outil et ses enfants
        except ProcessLookupError:
            pass

    def _liste(self, depot):
        """qvm-template list, synchrone et borné ; seules les lignes conformes restent."""
        p = self.lancer([QVT, *depot, "list", "--available", "--machine-readable"], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=HOME, env=ENV, start_new_session=True)
        try:
            sortie = taches.lire_borne(p, self.horloge() + DELAI_LISTE, MAX_SORTIE_LISTE, self.horloge)
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
        if sortie is None:
            raise ValueError("liste des modèles trop longue ou en retard")
        lignes = (_LIGNE.match(ligne) for ligne in sortie.decode("utf-8", "replace").splitlines())
        return [{"nom": m[1], "version": m[2], "depot": m[3]} for m in lignes if m][:MAX_LISTE]


def serveur(chemin=None, autorises=None, outils=None):
    import pwd
    if autorises is None:
        autorises = {pwd.getpwnam(taches.COMPTE_EXECUTEUR).pw_uid}
    outils = outils or Outils()
    srv = proto.serveur(taches._Gestion, chemin, autorises=frozenset(autorises), traiter=outils.traiter)
    srv.max_connexions = 2  # l'exécuteur traite un plan à la fois
    return srv


def main():
    serveur(SOCK_OUTILS).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
