"""tools/qaf-installer dans un dossier d'essai (QAF_RACINE) : faux dom0 (qvm-*
factices), scripts qui parlent à qubesd ou à la session remplacés par un
lanceur d'essai qui note les appels ; le Doorman (install.sh) est installé
pour de vrai dans le dossier."""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tarfile

import pytest

RACINE = pathlib.Path(__file__).parent.parent
LIVRES = ("qaf_gate", "qaf_mcp", "qaf_hand", "qaf_ui", "packaging", "policy", "tools", "profil", "materiel",
          "LICENCES-TIERS")  # notice des icônes reprises de Lucide
# Sur le Legion, la couche matériel suit le produit (AGENT-JETABLE §13.6).
# portier.json en premier : install.sh y lit agent_long (agent permanent, D28).
PRODUIT = ["portier-json", "portier", "sys-agent", "agent-dvm", "qaf-dvm", "qaf-hand-modeles", "securite", "bureau",
           "materiel"]
# Xfce n'est plus personnalisé (26/10) : aucun script de profil visuel n'est lancé.
SCRIPTS_PRODUIT = ["setup-sys-agent.sh", "setup-agent-dvm.sh", "setup-qaf-dvm.sh", "install-qaf-hand-modeles.sh",
                   "jetable-defaut.sh", "peripheriques.sh", "politique-fichiers.sh", "securite-qubes.sh"]
# Extension d'essai (qaf-installer --extension=…) : deux étapes, un qube
# d'administration, un avertissement et un signal pour --verifier.
EXTENSION_CONF = """# extension d'essai
admin qube-admin
avertissement Essai : extension à ne pas garder.
etape ext-un root un.sh Extension : première étape
etape ext-deux session deux.sh Extension : deuxième étape
signal /etc/essai-extension ATTENTION extension d'essai présente
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="bash Linux requis")

FAUX_QVM = r"""#!/bin/bash
F=@FAUX@
echo "$(basename "$0") $*" >> "$F/appels"
case "$(basename "$0")" in
qvm-check)
    nom=${@: -1}
    if [[ " $* " == *" --running "* ]]; then [ -e "$F/running/$nom" ]; else [ -e "$F/qubes/$nom" ]; fi ;;
qvm-prefs)
    case "$2" in
        klass) cat "$F/qubes/$1" 2>/dev/null || exit 1 ;;
        autostart) echo False ;;
        *) exit 1 ;;
    esac ;;
qvm-ls)
    if [ "$1" = --raw-list ]; then echo dom0; ls "$F/qubes"
    else for q in "$F"/qubes/*; do echo "$(basename "$q")|$(cat "$q")"; done; fi ;;
qubes-prefs) echo vm-pool ;;
qvm-pool) echo "name vm-pool"; echo "size $(cat "$F/pool-taille")"; echo "usage 1073741824" ;;
qvm-remove) rm -f "$F/qubes/${@: -1}" ;;
qvm-shutdown) ;;
esac
"""

LANCEUR = r"""#!/bin/bash
F=@FAUX@
qui=$1; script=$(basename "$2"); shift 2
echo "$qui $script $*" >> "$F/lances"
[ -n "${ECHEC:-}" ] && [ "$script" = "$ECHEC" ] && { echo "échec simulé de $script" >&2; exit 7; }
[ -n "${DEMARRE_SYS_USB:-}" ] && [ "$script" = "$DEMARRE_SYS_USB" ] && touch "$F/running/sys-usb"
case "$script" in
    setup-sys-agent.sh) echo TemplateVM > "$F/qubes/tpl-agent"; echo AppVM > "$F/qubes/sys-agent" ;;
    setup-agent-dvm.sh) echo AppVM > "$F/qubes/qaf-agent-dvm" ;;
    setup-qaf-dvm.sh) echo TemplateVM > "$F/qubes/tpl-qaf-hand"; echo AppVM > "$F/qubes/qaf-dvm" ;;
esac
exit 0
"""


def extension_essai(dossier, conf=EXTENSION_CONF, nom="essai", scripts=("un.sh", "deux.sh")):
    """Archive d'extension : un seul dossier qaf/<nom>/ avec extension.conf et ses scripts."""
    d = dossier / nom
    d.mkdir(parents=True)
    (d / "extension.conf").write_text(conf, encoding="utf-8")
    for s in scripts:
        (d / s).write_text("#!/bin/bash\necho essai\n")
    archive = dossier / f"qaf-{nom}.tar.gz"
    return archive, archive_de(dossier, archive, dossiers=(nom,))


def archive_de(depot, chemin, extra=None, dossiers=LIVRES):
    with tarfile.open(chemin, "w:gz") as t:
        for d in dossiers:
            # Un fichier livré à la racine (LICENCES-TIERS) : rglob n'y trouve rien,
            # il était silencieusement absent de l'archive d'essai.
            chemins = [depot / d] if (depot / d).is_file() else sorted((depot / d).rglob("*"))
            for p in chemins:
                if p.is_file() and "__pycache__" not in p.parts:
                    t.add(p, arcname=f"qaf/{p.relative_to(depot).as_posix()}")
        if extra:
            t.add(extra, arcname="hors/x")
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


class Dom0:
    def __init__(self, tmp, vendeur="LENOVO", modele="82JU"):
        self.tmp = tmp
        self.r = tmp / "racine"
        self.faux = tmp / "faux"
        for d in ("qubes", "running"):
            (self.faux / d).mkdir(parents=True)
        (self.faux / "pool-taille").write_text(str(100 * 1024**3))
        for q, k in {"debian-13-xfce": "TemplateVM", "fedora-42-xfce": "TemplateVM", "whonix-workstation-18": "TemplateVM",
                     "default-dvm": "AppVM", "sys-usb": "DispVM", "sys-net": "AppVM", "vault": "AppVM"}.items():
            (self.faux / "qubes" / q).write_text(k + "\n")
        (self.r / "etc").mkdir(parents=True)
        (self.r / "etc/qubes-release").write_text("Qubes release 4.3.2 (R4.3)\n")
        (self.r / "run").mkdir()
        (self.r / "run/qubesd.sock").write_text("")
        dmi = self.r / "sys/class/dmi/id"
        dmi.mkdir(parents=True)
        (dmi / "sys_vendor").write_text(vendeur + "\n")
        (dmi / "product_name").write_text(modele + "\n")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for nom in ("qvm-check", "qvm-prefs", "qvm-ls", "qubes-prefs", "qvm-pool", "qvm-remove", "qvm-shutdown"):
            (self.bin / nom).write_text(FAUX_QVM.replace("@FAUX@", str(self.faux)))
        (self.bin / "lanceur").write_text(LANCEUR.replace("@FAUX@", str(self.faux)))
        for f in self.bin.iterdir():
            f.chmod(0o755)
        self.archive = tmp / "qaf.tar.gz"
        self.empreinte = archive_de(RACINE, self.archive)
        self.archive_ext, self.empreinte_ext = extension_essai(tmp / "ext")
        self.ext = [f"--extension={self.archive_ext}", f"--empreinte-extension={self.empreinte_ext}"]

    def env(self, **extra):
        return {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "QAF_RACINE": str(self.r),
                "QAF_ESSAI_LANCEUR": str(self.bin / "lanceur"), "QAF_UTILISATEUR": "user", "QAF_DOM0_MIN_MO": "1",
                **extra}

    def lancer(self, *args, entree="", **extra):
        return subprocess.run(["bash", str(RACINE / "tools/qaf-installer"), *args], env=self.env(**extra),
                              input=entree, capture_output=True, text=True)

    def installer(self, *args, **extra):
        return self.lancer(*args, str(self.archive), self.empreinte, **extra)

    def lances(self):
        f = self.faux / "lances"
        return [l.split() for l in f.read_text().splitlines()] if f.exists() else []

    def scripts(self):
        return [l[1] for l in self.lances()]

    def portier(self):
        return json.loads((self.r / "etc/qaf/portier.json").read_text())

    def etapes(self):
        f = self.r / "var/lib/qaf-installer/etapes"
        return f.read_text().split() if f.exists() else []

    def arbre(self):
        return sorted(str(p.relative_to(self.r)) for p in self.r.rglob("*")
                      if "__pycache__" not in p.parts and p.name != "qaf-installer.log")


@pytest.fixture
def dom0(tmp_path):
    return Dom0(tmp_path)


def test_syntaxe_et_aide():
    for s in ["tools/qaf-installer", "materiel/lenovo-82ju.sh"]:
        subprocess.run(["bash", "-n", str(RACINE / s)], check=True)


@pytest.mark.parametrize("args", [["--purge"], ["--extension"], ["--extension=x", "--retour"], ["--retour", "x.tar.gz"], [], ["--inconnu"]])
def test_options_incoherentes(dom0, args):
    r = dom0.lancer(*args)
    assert r.returncode == 2 and not (dom0.r / "var").exists()


def test_installation_complete_sur_le_legion(dom0):
    r = dom0.installer()
    sortie = r.stdout + r.stderr
    assert r.returncode == 0, sortie
    assert dom0.scripts() == SCRIPTS_PRODUIT  # ordre exact, aucune étape d'extension
    assert dom0.etapes() == PRODUIT
    # qaf_hand (D16) : modèles d'origine Debian et Fedora, ni Whonix ni ceux de QAF.
    hand = next(l for l in dom0.lances() if l[1] == "install-qaf-hand-modeles.sh")
    assert hand[2:] == [dom0.empreinte, "debian-13-xfce", "fedora-42-xfce"]
    assert all(l[0] == "root" for l in dom0.lances() if l[1].startswith(("setup-", "install-")))
    assert all(l[0] == "session" for l in dom0.lances() if l[1].endswith("-dom0.sh") or l[1] == "jetable-defaut.sh")
    # Couche matériel : sys-usb jamais démarré ; produit : aucun qube d'administration.
    p = dom0.portier()
    assert p["ne_jamais_demarrer"] == ["sys-usb"] and p["admin_qubes"] == [] and p["coffres_entrables"] == []
    assert "Lenovo Legion 5" in sortie and "materiel/lenovo-82ju.sh" in sortie
    # Doorman réellement installé dans le dossier, installateur copié pour --retour.
    for f in ["etc/qubes/policy.d/00-qaf.policy", "etc/qubes-rpc/qaf.Plan", "usr/bin/qaf-installer",
              "usr/lib/qaf-installer/qaf/tools/qaf-installer", "usr/lib/qaf-installer/qaf.tar.gz",
              "var/lib/qaf-installer/sauve/portier.json.absent",
              "usr/share/qaf/LICENCES-TIERS", "usr/bin/qaf-agent", "usr/bin/qaf-palette"]:
        assert (dom0.r / f).is_file(), f
    # Menu QAF retiré (29/10) : ni lanceur, ni ses applis, ni thèmes.
    for f in ["usr/bin/qaf-lanceur", "usr/bin/qaf-espaces", "usr/bin/qaf-reglages", "usr/bin/qaf-theme",
              "usr/bin/qaf-terminal", "usr/bin/qaf-capture", "usr/share/qaf/themes"]:
        assert not (dom0.r / f).exists(), f
    journal = (dom0.r / "var/log/qaf-installer.log").read_text()
    assert "empreinte vérifiée" in journal and "QAF installé" in journal
    crees = (dom0.r / "var/lib/qaf-installer/qubes-crees").read_text().split()
    assert sorted(crees) == ["qaf-agent-dvm", "qaf-dvm", "sys-agent", "tpl-agent", "tpl-qaf-hand"]
    assert "Non-régression" in sortie and "sys-usb arrêté" in sortie


def test_installation_idempotente(dom0):
    assert dom0.installer().returncode == 0
    arbre, lances = dom0.arbre(), dom0.lances()
    r = dom0.installer()
    assert r.returncode == 0, r.stdout + r.stderr
    assert dom0.arbre() == arbre and dom0.lances() == lances * 2
    assert dom0.etapes() == PRODUIT and dom0.portier()["ne_jamais_demarrer"] == ["sys-usb"]
    # Les qubes existaient au 2e passage : la liste des créés n'a pas changé.
    assert len((dom0.r / "var/lib/qaf-installer/qubes-crees").read_text().split()) == 5


def test_verrou_jamais_herite_par_les_etapes(dom0, tmp_path):
    # Un démon relancé par une étape ne doit pas garder le verrou de l'installateur.
    temoin = tmp_path / "fd9"
    lanceur = tmp_path / "lanceur-fd"
    base = dom0.bin / "lanceur"
    lanceur.write_text("#!/bin/bash\n"
                       f"[ -e /proc/$$/fd/9 ] && echo herite >> {temoin}\n"
                       f'exec "{base}" "$@"\n')
    lanceur.chmod(0o755)
    r = dom0.installer(QAF_ESSAI_LANCEUR=str(lanceur))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not temoin.exists()


def test_machine_inconnue_sans_profil_materiel(tmp_path):
    d = Dom0(tmp_path, "ACME Corp.", "X-1000")
    r = d.installer()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "aucun profil matériel" in r.stdout
    assert d.portier()["ne_jamais_demarrer"] == []


def test_echec_arrete_tout_puis_reprise(dom0):
    r = dom0.installer(ECHEC="setup-qaf-dvm.sh")
    sortie = r.stdout + r.stderr
    assert r.returncode == 1
    assert "ÉCHEC : étape « Modèle tpl-qaf-hand et jetable qaf-dvm » (code 7)" in sortie
    assert "les étapes suivantes n'ont pas été faites" in sortie and "qaf-installer --retour" in sortie
    assert dom0.scripts() == ["setup-sys-agent.sh", "setup-agent-dvm.sh", "setup-qaf-dvm.sh"]
    assert dom0.etapes() == ["portier-json", "portier", "sys-agent", "agent-dvm"]
    assert "ÉCHEC" in (dom0.r / "var/log/qaf-installer.log").read_text()
    r = dom0.installer()
    assert r.returncode == 0 and dom0.etapes() == PRODUIT


def test_sys_usb_demarre_pendant_une_etape(dom0):
    r = dom0.installer(DEMARRE_SYS_USB="securite-qubes.sh")
    assert r.returncode == 1 and "« sys-usb » a démarré pendant l'étape" in r.stdout
    assert "securite" not in dom0.etapes() and "materiel" not in dom0.etapes()


def test_empreinte_fausse_ou_invalide(dom0):
    r = dom0.lancer(str(dom0.archive), "0" * 64)
    assert r.returncode == 1 and "EMPREINTES DIFFÉRENTES" in r.stdout and dom0.empreinte in r.stdout
    r = dom0.lancer(str(dom0.archive), entree="pas-une-empreinte\n")
    assert r.returncode == 1 and "Empreinte invalide" in r.stdout
    assert not (dom0.r / "usr").exists() and not dom0.lances()
    r = dom0.lancer(str(dom0.archive), entree=dom0.empreinte + "\n")  # demandée si absente
    assert r.returncode == 0, r.stdout + r.stderr


def test_archive_hors_de_qaf_refusee(dom0, tmp_path):
    (tmp_path / "x").write_text("x")
    a = tmp_path / "mauvaise.tar.gz"
    e = archive_de(RACINE, a, extra=tmp_path / "x")
    r = dom0.lancer(str(a), e)
    assert r.returncode == 1 and "inattendue" in r.stdout and not (dom0.r / "usr").exists()


@pytest.mark.parametrize("panne,message", [
    ("version", "Version de Qubes non prise en charge"),
    ("modele", "Le modèle debian-13-xfce est absent"),
    ("pool", "Place insuffisante dans le pool"),
    ("dom0", "Ce n'est pas un dom0 Qubes"),
])
def test_prerequis(dom0, panne, message):
    if panne == "version":
        (dom0.r / "etc/qubes-release").write_text("Qubes release 4.2.4 (R4.2)\n")
    elif panne == "modele":
        (dom0.faux / "qubes/debian-13-xfce").unlink()
    elif panne == "pool":
        (dom0.faux / "pool-taille").write_text(str(5 * 1024**3))
    else:
        (dom0.r / "etc/qubes-release").unlink()
    r = dom0.installer()
    assert r.returncode == 1 and message in r.stdout, r.stdout
    assert not (dom0.r / "usr").exists() and not dom0.lances()


def test_purge_demande_confirmation_et_garde_les_qubes_d_avant(dom0):
    (dom0.faux / "qubes/sys-agent").write_text("AppVM\n")  # existait avant QAF
    assert dom0.installer().returncode == 0
    (dom0.r / "var/lib/qaf-exec").mkdir(parents=True)
    r = dom0.lancer("--retour", "--purge", entree="non\n")
    assert r.returncode == 1 and "rien n'a été fait" in r.stdout
    assert dom0.etapes() == PRODUIT and (dom0.faux / "qubes/qaf-dvm").exists()
    r = dom0.lancer("--retour", "--purge", entree="PURGER\n")
    assert r.returncode == 0, r.stdout + r.stderr
    retires = [l.split()[-1] for l in (dom0.faux / "appels").read_text().splitlines() if l.startswith("qvm-remove")]
    assert retires == ["qaf-agent-dvm", "qaf-dvm", "tpl-qaf-hand", "tpl-agent"]
    assert (dom0.faux / "qubes/sys-agent").exists()
    assert "Gardé : masquage des disques internes" in r.stdout  # --purge n'implique pas --materiel
    assert not (dom0.r / "var/lib/qaf-exec").exists()
    assert sorted(p.name for p in (dom0.r / "var/lib/qaf-installer").iterdir()) == ["materiel"]
    assert (dom0.r / "etc/udev/rules.d/90-qaf-disque-interne.rules").exists()
    assert not (dom0.r / "etc/qaf/portier.json").exists()  # absent avant l'installation
    r = dom0.lancer("--retour")
    assert r.returncode == 0 and "Rien à retirer" in r.stdout


def test_verifier_en_lecture_seule(dom0):
    assert dom0.installer().returncode == 0
    arbre = sorted(str(p.relative_to(dom0.r)) for p in dom0.r.rglob("*"))
    journal = (dom0.r / "var/log/qaf-installer.log").read_text()
    r = dom0.lancer("--verifier")
    assert r.returncode == 0, r.stdout
    assert "qvm-ls répond" in r.stdout and "sys-usb arrêté" in r.stdout
    assert sorted(str(p.relative_to(dom0.r)) for p in dom0.r.rglob("*")) == arbre
    assert (dom0.r / "var/log/qaf-installer.log").read_text() == journal
    (dom0.faux / "running/sys-usb").write_text("")
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "sys-usb est démarré" in r.stdout


def test_verifier_bureau_plasma_simple_information(dom0):
    # Session Plasma X11 (kde-settings-qubes, installé par l'utilisateur) : information, jamais un écart.
    r = dom0.installer()
    assert r.returncode == 0
    assert "sudo qubes-dom0-update kde-settings-qubes" in r.stdout
    assert "seule session proposée" in r.stdout
    assert not (dom0.r / "etc/lightdm/lightdm.conf.d/50-qaf-plasma-seul.conf").exists()  # sans Plasma : rien
    assert "session « QAF »" not in r.stdout
    assert "i3-settings-qubes" not in r.stdout and "picom" not in r.stdout
    sans = dom0.lancer("--verifier")
    assert "info     session Plasma X11 absente" in sans.stdout, sans.stdout
    assert "session QAF" not in sans.stdout
    x = dom0.r / "usr/share/xsessions/plasmax11.desktop"
    x.parent.mkdir(parents=True, exist_ok=True)
    x.write_text("[Desktop Entry]\n")
    avec = dom0.lancer("--verifier")
    assert "info     session Plasma X11 présente (/usr/share/xsessions/plasmax11.desktop)" in avec.stdout
    assert avec.returncode == sans.returncode
    assert x.read_text() == "[Desktop Entry]\n"  # lecture seule


def test_profil_materiel_nomme_par_le_dmi():
    # materiel/<fabricant>-<modèle>.sh, en minuscules ; ne fait que déclarer.
    noms = sorted(p.name for p in (RACINE / "materiel").glob("*.sh"))
    assert "lenovo-82ju.sh" in noms
    texte = (RACINE / "materiel/lenovo-82ju.sh").read_text(encoding="utf-8")
    assert 'MATERIEL_NE_JAMAIS_DEMARRER="sys-usb"' in texte
    for interdit in ("qvm-start", "qvm-device", "qvm-pci", "qvm-usb", "qvm-prefs sys-usb autostart True"):
        assert interdit not in texte


@pytest.mark.parametrize("sortie, attendu", [
    ("", "OK       fichiers du paquet de politique de Qubes intacts"),
    (".M.......    /var/run/qubes", "OK       fichiers du paquet de politique de Qubes intacts"),
    (".M.......    /run/qubes", "OK       fichiers du paquet de politique de Qubes intacts"),
    ("S.5....T.  c /etc/qubes/policy.d/90-default.policy", "ÉCHEC    fichiers de politique de Qubes modifiés"),
    (".M.......    /etc/qubes/policy.d", "ÉCHEC    fichiers de politique de Qubes modifiés"),
])
def test_verifier_rpm_ecarte_seulement_run(dom0, sortie, attendu):
    # /var/run/qubes (tmpfs recréé au démarrage) n'est pas un fichier de politique modifié.
    pol = dom0.r / "etc/qubes/policy.d"
    pol.mkdir(parents=True)
    (pol / "90-default.policy").write_text("")
    appels = dom0.r / "rpm-appels"
    (dom0.bin / "rpm").write_text(f"#!/bin/bash\necho \"$*\" >> '{appels}'\nprintf '%s' '{sortie}'\n[ -z '{sortie}' ]\n")
    (dom0.bin / "rpm").chmod(0o755)
    r = dom0.lancer("--verifier")
    assert attendu in r.stdout, r.stdout
    # Les deux paquets : celui de 90-default.policy et celui du dossier policy.d (paquet qrexec).
    assert any(l.rstrip().endswith("etc/qubes/policy.d") for l in appels.read_text().splitlines())


# --- agent jetable et couche matériel (docs/AGENT-JETABLE.md §3, §13.6, R-3, R-11) ---

def disques_internes(dom0):
    for d in ("nvme0n1", "nvme1n1"):
        b = dom0.r / "sys/block" / d
        b.mkdir(parents=True)
        (b / "removable").write_text("0\n")


def test_materiel_masque_les_disques_et_retour_les_garde(dom0):
    disques_internes(dom0)
    assert dom0.installer().returncode == 0
    regle = dom0.r / "etc/udev/rules.d/90-qaf-disque-interne.rules"
    texte = regle.read_text()
    assert 'KERNEL=="nvme0n1*"' in texte and 'KERNEL=="nvme1n1*"' in texte  # disque et partitions
    etat = (dom0.r / "var/lib/qaf-installer/materiel").read_text()
    assert "profil=lenovo-82ju" in etat and "origine=aucune règle" in etat
    r = dom0.lancer("--retour")
    assert r.returncode == 0, r.stdout + r.stderr
    assert regle.read_text() == texte  # --retour seul ne réexpose pas le SSD Windows
    assert "Profil matériel gardé : disques internes toujours masqués" in r.stdout
    assert "Rien à retirer" in dom0.lancer("--retour").stdout
    r = dom0.lancer("--retour", "--materiel")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "disque Windows, redeviendront attachables à un qube" in r.stdout
    assert not regle.exists()
    # Seule reste la liste des qubes créés (pour un --retour --purge plus tard).
    assert sorted(p.name for p in (dom0.r / "var/lib/qaf-installer").iterdir()) == ["qubes-crees"]


def test_produit_ne_relance_jamais_udevadm_trigger():
    for f in ["materiel/lenovo-82ju.sh", "tools/qaf-installer", "packaging/install.sh"]:
        lignes = [l for l in (RACINE / f).read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
        assert not any("udevadm trigger" in l for l in lignes), f


def test_materiel_seulement_avec_retour(dom0):
    assert dom0.lancer("--materiel").returncode == 2


def test_machine_inconnue_sans_couche_materiel(tmp_path):
    d = Dom0(tmp_path, "ACME Corp.", "X-1000")
    assert d.installer().returncode == 0
    assert "materiel" not in d.etapes() and not (d.r / "etc/udev/rules.d").exists()


def test_portier_json_garde_les_reglages_agent_et_refuse_r3(dom0):
    (dom0.r / "etc/qaf").mkdir(parents=True)
    (dom0.r / "etc/qaf/portier.json").write_text('{"coffres": ["vault"], "agents_max": 2, "agent_long": true}\n')
    assert dom0.installer().returncode == 0
    p = dom0.portier()
    assert p["agents_max"] == 2 and p["agent_long"] is True and p["agent_hotes"] == ["api.anthropic.com"]
    # agent_long vrai : la politique de l'agent long est posée (00-qaf-agent-long.policy).
    assert (dom0.r / "etc/qubes/policy.d/00-qaf-agent-long.policy").is_file()
    p["jetables"] = ["qaf-dvm", "qaf-agent-dvm"]
    (dom0.r / "etc/qaf/portier.json").write_text(json.dumps(p))
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "qaf-agent-dvm est dans « jetables »" in r.stdout
    r = dom0.installer()
    assert r.returncode == 1 and "R-3" in r.stdout + r.stderr


def test_agent_permanent_par_defaut(dom0):
    # D28 (27/10) : agent_long vrai à la livraison, sa politique posée dès
    # la première installation (portier.json écrit avant install.sh).
    r = dom0.installer()
    assert r.returncode == 0, r.stdout + r.stderr
    assert dom0.portier()["agent_long"] is True
    assert (dom0.r / "etc/qubes/policy.d/00-qaf-agent-long.policy").is_file()
    assert "agent permanent : politique de sys-agent posée" in r.stdout
    assert "icône de l'agent dans la barre de Plasma" in r.stdout and "setup-token" not in r.stdout
    assert "menu QAF" not in r.stdout  # menu retiré (29/10)


def test_agent_long_faux_de_l_utilisateur_garde(dom0):
    # Un agent_long faux posé par l'utilisateur n'est jamais changé : sans agent
    # permanent, sa politique n'est pas posée.
    (dom0.r / "etc/qaf").mkdir(parents=True)
    (dom0.r / "etc/qaf/portier.json").write_text('{"coffres": ["vault"], "agent_long": false}\n')
    r = dom0.installer()
    assert r.returncode == 0 and dom0.portier()["agent_long"] is False
    assert not (dom0.r / "etc/qubes/policy.d/00-qaf-agent-long.policy").exists()
    assert not (dom0.r / "var/lib/qaf-installer/agent-permanent").exists()


def test_verifier_agent_long_sans_sa_politique(dom0):
    assert dom0.installer().returncode == 0
    (dom0.r / "etc/qubes/policy.d/00-qaf-agent-long.policy").unlink()
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "00-qaf-agent-long.policy est absent" in r.stdout


def test_verifier_politique_triee_avant_qui_cite_l_agent(dom0):
    assert dom0.installer().returncode == 0
    (dom0.r / "etc/qubes/policy.d/00-a.policy").write_text("qubes.Filecopy * work @tag:qaf-agent allow\n")
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "00-a.policy triée avant 00-qaf.policy" in r.stdout


def test_verifier_politique_agent_long_modifiee_ou_sans_agent_long(dom0):
    # Revue du 25/10 : --verifier ne laisse passer 00-qaf-agent-long.policy avant
    # 00-qaf.policy que s'il est identique au fichier livré ET que agent_long est vrai.
    (dom0.r / "etc/qaf").mkdir(parents=True)
    (dom0.r / "etc/qaf/portier.json").write_text('{"agent_long": true}\n')
    assert dom0.installer().returncode == 0
    long_ = dom0.r / "etc/qubes/policy.d/00-qaf-agent-long.policy"
    r = dom0.lancer("--verifier")
    assert "00-qaf-agent-long.policy" not in r.stdout, r.stdout
    long_.write_text(long_.read_text() + "qaf.Plan * work @adminvm allow\n")
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "diffère de la politique livrée" in r.stdout
    shutil.copy(RACINE / "policy/00-qaf-agent-long.policy", long_)
    p = dom0.portier()
    p["agent_long"] = False
    (dom0.r / "etc/qaf/portier.json").write_text(json.dumps(p))
    r = dom0.lancer("--verifier")
    assert r.returncode == 1 and "ne dit pas agent_long" in r.stdout


@pytest.mark.parametrize("contenu", ["qubes.Filecopy * work disp12 allow\n", "qaf.Plan * qaf-agent-dvm @adminvm allow\n",
                                     "qaf.Plan * sys-agent @adminvm allow\n"])
def test_install_sh_refuse_une_politique_triee_avant(tmp_path, contenu):
    r = tmp_path / "r"
    (r / "etc/qubes/policy.d").mkdir(parents=True)
    (r / "etc/qubes/policy.d/00-a.policy").write_text(contenu)
    p = subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env={**os.environ, "QAF_RACINE": str(r)},
                       capture_output=True, text=True)
    assert p.returncode == 1 and "rien n'a été installé" in p.stderr


def test_install_sh_accepte_sa_politique_agent_long_identique(tmp_path):
    r = tmp_path / "r"
    (r / "etc/qubes/policy.d").mkdir(parents=True)
    shutil.copy(RACINE / "policy/00-qaf-agent-long.policy", r / "etc/qubes/policy.d/")
    env = {**os.environ, "QAF_RACINE": str(r)}
    p = subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    # agent_long vrai (portier.json livré, D28) : le fichier est gardé, identique.
    assert (r / "etc/qubes/policy.d/00-qaf-agent-long.policy").read_bytes() == \
        (RACINE / "policy/00-qaf-agent-long.policy").read_bytes()
    (r / "etc/qaf/portier.json").write_text('{"agent_long": false}\n')
    p = subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and not (r / "etc/qubes/policy.d/00-qaf-agent-long.policy").exists()  # retiré
    (r / "etc/qubes/policy.d/00-qaf-agent-long.policy").write_text("qaf.Plan * @anyvm @adminvm allow\n")
    p = subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env=env, capture_output=True, text=True)
    assert p.returncode == 1  # modifié : refusé


def test_setup_agent_dvm_contrat():
    s = (RACINE / "tools/setup-agent-dvm.sh").read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(RACINE / "tools/setup-agent-dvm.sh")], check=True)
    for attendu in ("template_for_dispvms True", "default_dispvm ''", "netvm sys-firewall", "memory 800",
                    "maxmem 3000", "specialtarget=dns", "dstports=443", "add drop", "del --rule-no 0",
                    "qvm-shutdown --wait", "hasTrustDialogAccepted", "mcp__qaf__*", ".credentials.json",
                    '--unset "$M" preload-dispvm-max'):
        assert attendu in s, attendu
    assert "qvm-tags \"$M\" add" not in s and "tags add" not in s  # jamais qaf-agent sur le modèle (S-J2)
    assert "sys-usb" not in s and "qvm-pci" not in s and "qvm-usb" not in s


def test_regles_du_pare_feu_alignees_avec_le_portier():
    # Même liste que qaf_gate.executor.regles_agent (R-1).
    from qaf_gate import executor
    s = (RACINE / "tools/setup-agent-dvm.sh").read_text(encoding="utf-8")
    r = executor.regles_agent(("api.anthropic.com",))
    assert r[0] == {"action": "accept", "dns": True} and r[-1] == {"action": "drop"}
    assert r[1] == {"action": "accept", "host": "api.anthropic.com", "proto": "tcp", "ports": "443"}
    assert s.index("specialtarget=dns") < s.index("dstports=443") < s.index("add drop")
    p = json.loads((RACINE / "packaging/portier.json").read_text())
    assert p["agent_hotes"] == ["api.anthropic.com"] and 'DEFAUT_HOTES="api.anthropic.com"' in s


def test_node_22_nodesource_epingle():
    s = (RACINE / "tools/setup-sys-agent.sh").read_text(encoding="utf-8")
    for attendu in ("node_22.x nodistro main", "signed-by=/usr/share/keyrings/qaf-nodesource.gpg", "Pin-Priority: -1",
                    "Pin-Priority: 600", "EMPREINTE NODESOURCE DIFFÉRENTE", "@anthropic-ai/claude-code@$CLAUDE_VERSION",
                    "http://127.0.0.1:8082"):
        assert attendu in s, attendu
    assert "forky" not in s


def test_installation_neuve_ne_touche_pas_xfce(dom0):
    assert dom0.installer().returncode == 0
    assert set(dom0.scripts()) == set(SCRIPTS_PRODUIT)  # aucun script de profil visuel
    assert "xfce-origine" not in dom0.etapes()
    # Bureau = KDE Plasma classique (paquet de Qubes, installé par l'utilisateur) : QAF n'ajoute
    # aucune session (ni sous i3, ni sous Plasma).
    assert not (dom0.r / "usr/share/xsessions/qaf.desktop").exists()
    assert not (dom0.r / "usr/bin/qaf-session-plasma").exists()
    assert not (dom0.r / "usr/share/qaf/xdg-data").exists()
    assert not (dom0.r / "usr/share/qaf/i3").exists()


# --- extension (qaf-installer --extension=…) ------------------------------------------

def test_extension_seulement_avec_l_option(dom0):
    assert dom0.installer().returncode == 0
    assert not {"un.sh", "deux.sh"} & set(dom0.scripts())
    r = dom0.installer(*dom0.ext)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ATTENTION : --extension" in r.stdout and "Essai : extension à ne pas garder." in r.stdout
    assert "Extension : première étape" in r.stdout
    assert dom0.lances()[-2:] == [["root", "un.sh"], ["session", "deux.sh"]]
    assert dom0.portier()["admin_qubes"] == ["qube-admin"]
    assert dom0.etapes()[-2:] == ["ext-un", "ext-deux"]
    assert (dom0.r / "usr/lib/qaf-installer/extension/qaf/essai/extension.conf").is_file()
    # Relancé sans --extension : l'extension n'est pas réinstallée, mais signalée et gardée.
    avant = len(dom0.lances())
    r = dom0.installer()
    assert r.returncode == 0, r.stdout + r.stderr
    assert not {"un.sh", "deux.sh"} & {l[1] for l in dom0.lances()[avant:]}
    assert "l'extension d'une installation précédente" in r.stdout
    assert (dom0.r / "usr/lib/qaf-installer/extension/qaf/essai/un.sh").is_file()
    assert not (dom0.r / "usr/lib/qaf-installer/qaf/essai").exists()
    # --verifier rappelle ce que l'extension a signalé.
    (dom0.r / "etc/essai-extension").write_text("")
    assert "ATTENTION extension d'essai présente" in dom0.lancer("--verifier").stdout


def test_extension_retiree_en_premier_avec_valeurs_d_origine(dom0):
    (dom0.r / "etc/qaf").mkdir(parents=True)
    origine = '{"coffres": ["vault", "loisirs"], "ne_jamais_demarrer": []}\n'
    (dom0.r / "etc/qaf/portier.json").write_text(origine)
    assert dom0.installer(*dom0.ext).returncode == 0
    assert dom0.portier()["admin_qubes"] == ["qube-admin"]
    avant = len(dom0.lances())
    r = dom0.lancer("--retour")
    assert r.returncode == 0, r.stdout + r.stderr
    retraits = dom0.lances()[avant:]
    assert all(l[2] == "--retour" for l in retraits)
    assert [l[1] for l in retraits][:3] == ["deux.sh", "un.sh", "securite-qubes.sh"]
    assert (dom0.r / "etc/qaf/portier.json").read_text() == origine
    assert not (dom0.r / "usr/lib/qaf-installer").exists()


def test_extension_installee_sans_archive_refusee(dom0):
    assert dom0.installer(*dom0.ext).returncode == 0
    import shutil as sh
    sh.rmtree(dom0.r / "usr/lib/qaf-installer/extension")
    avant, arbre = len(dom0.lances()), dom0.arbre()
    r = dom0.installer()
    assert r.returncode == 1 and "aucune archive d'extension" in r.stdout and "ext-un" in r.stdout
    assert len(dom0.lances()) == avant and dom0.arbre() == arbre
    r = dom0.lancer("--retour")
    assert r.returncode == 1 and "Rien n'a été retiré" in r.stdout
    assert dom0.etapes()[-2:] == ["ext-un", "ext-deux"]


@pytest.mark.parametrize("conf, message", [
    ("etape portier root un.sh Titre\n", "étape invalide"),        # nom d'une étape du produit
    ("etape ext-un admin un.sh Titre\n", "étape invalide"),        # ni root ni session
    ("etape ext-un root absent.sh Titre\n", "étape invalide"),     # script absent
    ("etape ext-un root ../un.sh Titre\n", "étape invalide"),
    ("admin pas/un-qube\n", "nom de qube invalide"),
    ("signal relatif/x Texte\n", "signal invalide"),
    ("commande rm -rf /\n", "déclaration inconnue"),
])
def test_extension_mal_formee_refusee_avant_tout(dom0, tmp_path, conf, message):
    a, e = extension_essai(tmp_path / "mauvaise", conf=conf)
    r = dom0.installer(f"--extension={a}", f"--empreinte-extension={e}")
    assert r.returncode == 1 and message in r.stdout, r.stdout
    assert not (dom0.r / "usr").exists() and not dom0.lances()


def test_extension_livree_a_part(dom0, tmp_path):
    # L'archive du produit ne contient jamais d'extension ; une extension tient dans un seul dossier.
    a = tmp_path / "avec-extension.tar.gz"
    shutil.copytree(dom0.tmp / "ext" / "essai", tmp_path / "depot" / "essai")
    for d in LIVRES:
        (tmp_path / "depot" / d).symlink_to(RACINE / d)
    e = archive_de(tmp_path / "depot", a, dossiers=LIVRES + ("essai",))
    r = dom0.lancer(str(a), e)
    assert r.returncode == 1 and "contient une extension" in r.stdout and not (dom0.r / "usr").exists()
    r = dom0.lancer(f"--extension={dom0.archive}", f"--empreinte-extension={dom0.empreinte}",
                    str(dom0.archive), dom0.empreinte)
    assert r.returncode == 1 and "inattendue" in r.stdout and not dom0.lances()
    r = dom0.lancer(f"--extension={dom0.archive_ext}", "--empreinte-extension=" + "0" * 64,
                    str(dom0.archive), dom0.empreinte)
    assert r.returncode == 1 and "EMPREINTES DIFFÉRENTES" in r.stdout and not (dom0.r / "usr").exists()
