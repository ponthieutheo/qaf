"""Scripts dom0 : syntaxe, et install/uninstall idempotents dans un dossier
d'essai (QAF_RACINE : ni systemd, ni comptes)."""

import os
import stat
import pathlib
import shutil
import subprocess

import pytest

RACINE = pathlib.Path(__file__).parent.parent
SCRIPTS = ["packaging/install.sh", "packaging/uninstall.sh", "tools/qaf-update",
           "tools/make-dist.sh", "tools/s1-dom0-probes.sh", "tools/qaf-installer",
           "tools/setup-sys-agent.sh", "tools/setup-qaf-dvm.sh", "tools/install-qaf-hand-modeles.sh"]
# Extensions du dépôt (dossiers de la racine avec extension.conf, archivés à part par make-dist.sh).
EXTENSIONS = sorted(p.parent.name for p in RACINE.glob("*/extension.conf"))

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")


@pytest.mark.parametrize("script", SCRIPTS)
def test_syntaxe(script):
    if script.startswith("tools/s1-") and not (RACINE / script).exists():
        pytest.skip("outil du dépôt privé seulement")
    subprocess.run(["bash", "-n", str(RACINE / script)], check=True)


def arbre(d):
    return sorted(str(p.relative_to(d)) for p in d.rglob("*") if "__pycache__" not in p.parts)


def lancer(script, racine, *args, check=True):
    env = {**os.environ, "QAF_RACINE": str(racine)}
    return subprocess.run(["bash", str(RACINE / script), *args], env=env,
                          check=check, capture_output=True, text=True)


# Menu QAF et ses applis, retirés le 29/10 (« garder que la brique agent »).
RETIRES = ("lanceur", "espaces", "reglages", "terminal", "capture", "theme")


def test_install_idempotent_puis_desinstalle(tmp_path):
    lancer("packaging/install.sh", tmp_path)
    premier = arbre(tmp_path)
    lancer("packaging/install.sh", tmp_path)
    assert arbre(tmp_path) == premier
    for f in ["etc/qubes-rpc/qaf.Plan", "etc/qubes/policy.d/00-qaf.policy",
              "usr/libexec/qaf/qaf-gate-validator", "usr/libexec/qaf/qaf-gate-executor",
              "usr/bin/qaf-pause", "usr/lib/qaf/qaf_gate/stub.py",
              "etc/systemd/system/qaf-gate-validator.service",
              "etc/systemd/system/qaf-gate-demande.socket", "usr/bin/qaf-palette",
              "usr/lib/qaf/qaf_ui/palette.py", "usr/lib/qaf/qaf_ui/palette_gtk.py",
              "usr/lib/qaf/qaf_ui/donnees.py", "usr/lib/qaf/qaf_ui/nuit/__init__.py",
              "usr/lib/qaf/qaf_ui/i18n.py", "usr/lib/qaf/qaf_ui/langues/__init__.py",
              "usr/lib/qaf/qaf_ui/langues/fr.py", "usr/lib/qaf/qaf_ui/langues/en.py",
              "usr/lib/qaf/qaf_ui/nuit/composants.py", "usr/lib/qaf/qaf_ui/nuit/css.py",
              "usr/bin/qaf-agent", "usr/share/applications/qaf-agent.desktop",
              "usr/lib/qaf/qaf_ui/ecrans/__init__.py", "usr/lib/qaf/qaf_ui/ecrans/agent.py",
              "usr/libexec/qaf/qaf-gate-taches", "usr/lib/qaf/qaf_gate/taches.py",
              "etc/systemd/system/qaf-gate-taches.socket", "etc/systemd/system/qaf-gate-taches.service"]:
        assert (tmp_path / f).is_file(), f
    assert os.access(tmp_path / "etc/qubes-rpc/qaf.Plan", os.X_OK)
    assert not (tmp_path / "usr/lib/qaf/qaf_gate/backend_sim.py").exists()  # code de test (P2-9)
    assert not (tmp_path / "etc/qubes/policy.d/00-qaf.policy.tmp").exists()
    assert "from qaf_gate.validator import main" in (tmp_path / "usr/libexec/qaf/qaf-gate-validator").read_text()
    assert "from qaf_ui.palette import main" in (tmp_path / "usr/bin/qaf-palette").read_text()
    # Panneau de l'agent : une commande et une entrée de menu à nous, sans raccourci.
    assert "from qaf_ui.ecrans.agent import main" in (tmp_path / "usr/bin/qaf-agent").read_text()
    entree = (tmp_path / "usr/share/applications/qaf-agent.desktop").read_text(encoding="utf-8")
    assert "Exec=qaf-agent" in entree.splitlines() and "X-QAF" in entree
    assert (tmp_path / "usr/lib/qaf/qaf_ui/ecrans/agent_etat.py").is_file()
    assert "from qaf_gate.taches import main" in (tmp_path / "usr/libexec/qaf/qaf-gate-taches").read_text()
    # Menu QAF et ses applis : ni commande, ni entrée de menu, ni code, ni thèmes.
    for e in RETIRES:
        assert not (tmp_path / f"usr/bin/qaf-{e}").exists(), e
        assert not (tmp_path / f"usr/share/applications/qaf-{e}.desktop").exists(), e
    partis = {"menu.json", "applis.py", "capture.py", "terminal.py", "theme.py", "clavier.py", "console.py",
              "console_i3.py", "session.py", "plasma.py", "i3ipc.py", "fond.py",
              "ecrans/lanceur.py", "ecrans/lanceur_gtk.py", "ecrans/menu.py", "ecrans/menu_gtk.py",
              "ecrans/espaces.py", "ecrans/espaces_gtk.py", "ecrans/reglages.py", "ecrans/reglages_gtk.py",
              "ecrans/barre.py", "ecrans/barre_gtk.py", "nuit/themes.py", "nuit/galerie.py"}
    for f in partis:
        assert not (tmp_path / "usr/lib/qaf/qaf_ui" / f).exists(), f
    assert not (tmp_path / "usr/share/qaf/themes").exists()
    assert (tmp_path / "usr/lib/qaf/qaf_ui/x11.py").is_file()
    applis = sorted(p.name for p in (tmp_path / "usr/share/applications").iterdir())
    assert applis == ["qaf-agent.desktop"], applis
    (tmp_path / "var/lib/qaf-exec").mkdir(parents=True)
    lancer("packaging/uninstall.sh", tmp_path)
    lancer("packaging/uninstall.sh", tmp_path)
    reste = arbre(tmp_path)
    assert "var/lib/qaf-exec" in reste and not any("qaf.Plan" in r or "-qaf.policy" in r for r in reste)
    assert not any(f"qaf-{e}" in r for e in RETIRES + ("agent",) for r in reste)  # --retour : rien ne reste
    assert not any("qaf-palette" in r or "qaf_ui" in r or "demande.socket" in r or "taches" in r for r in reste)
    assert not any("qaf-barre" in r for r in reste)
    lancer("packaging/uninstall.sh", tmp_path, "--purge")
    assert not (tmp_path / "var/lib/qaf-exec").exists()


def test_install_sans_commandes_de_la_session_i3(tmp_path):
    """qaf-menu, qaf-session, qaf-barre et qaf-console servaient les sessions QAF sous
    i3 et sous Plasma (plus livrées depuis le 27/10, code retiré le 29/10 avec le menu)."""
    lancer("packaging/install.sh", tmp_path)
    for cmd in ("qaf-menu", "qaf-session", "qaf-barre", "qaf-console", "qaf-session-plasma"):
        assert not (tmp_path / "usr/bin" / cmd).exists(), cmd
    assert not (tmp_path / "usr/lib/qaf/qaf_ui/menu.json").exists()


def test_install_sans_menu_qaf_ni_toucher_qubes_et_kde(tmp_path):
    """29/10 : menu QAF retiré, seule la brique agent reste. install.sh et uninstall.sh
    ne posent ni ne touchent rien d'autre (fichiers de Qubes, de KDE, étape bureau)."""
    autres = ["usr/share/applications/qubes-qube-manager.desktop", "usr/share/applications/org.kde.konsole.desktop",
              "usr/share/qaf/xsessions/plasmax11.desktop", "usr/bin/qubes-app-menu",
              "usr/share/kglobalaccel/org.kde.krunner.desktop"]
    for f in autres:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(f"# {f}\n", encoding="utf-8")
    lancer("packaging/install.sh", tmp_path)
    for e in RETIRES:
        assert not (tmp_path / "usr/bin" / f"qaf-{e}").exists(), e
    assert not (tmp_path / "usr/share/qaf/themes").exists()
    assert not (tmp_path / "usr/lib/qaf/qaf_ui/menu.json").exists()
    premier = arbre(tmp_path)
    lancer("packaging/install.sh", tmp_path)  # rejouable
    assert arbre(tmp_path) == premier
    lancer("packaging/uninstall.sh", tmp_path)
    for f in autres:
        assert (tmp_path / f).read_text(encoding="utf-8") == f"# {f}\n", f


def test_aucun_raccourci_clavier_pose():
    """Meta et Meta+Espace gardent leur rôle de Plasma (menu de Qubes) : ni install.sh,
    ni uninstall.sh, ni qaf-installer ne posent de raccourci global (kglobalaccel,
    kglobalshortcutsrc, khotkeys) ni ne touchent à la configuration KDE de l'utilisateur."""
    for script in ("packaging/install.sh", "packaging/uninstall.sh", "tools/qaf-installer"):
        actions = [ligne.strip() for ligne in (RACINE / script).read_text(encoding="utf-8").splitlines()
                   if not ligne.lstrip().startswith(("#", "echo ", "dire "))]
        for motif in ("kglobalaccel", "khotkeys", "kwriteconfig", "X-KDE-Shortcuts", ".config/kglobalshortcutsrc"):
            assert not any(motif in ligne for ligne in actions), (script, motif)
        assert not any("kglobalshortcutsrc" in ligne for ligne in actions), script
    assert not (RACINE / "packaging" / "plasma").exists()


@pytest.mark.parametrize("dossier,nom,contenu", [
    ("etc", "00-a.policy", "qubes.VMShell * sys-agent @dispvm allow\n"),
    ("run", "-x.policy", "* * @anyvm @anyvm allow\n"),
    ("etc", "00-0.policy", "!include include/autre\n"),
    ("run", "00-qaf.policy", "# remplace la nôtre\n"),
])
def test_install_refuse_une_politique_qui_passe_avant(tmp_path, dossier, nom, contenu):
    # P1-3 : premier match gagne, fichiers triés par nom sur /run + /etc.
    d = tmp_path / dossier / "qubes" / "policy.d"
    d.mkdir(parents=True)
    (d / nom).write_text(contenu)
    r = lancer("packaging/install.sh", tmp_path, check=False)
    assert r.returncode == 1 and "rien n'a été installé" in r.stderr
    assert not (tmp_path / "usr").exists()


def test_install_accepte_les_fichiers_de_qubes(tmp_path):
    d = tmp_path / "etc" / "qubes" / "policy.d"
    d.mkdir(parents=True)
    (d / "00-commentaire.policy").write_text("# sys-agent cité en commentaire seulement\n")
    (d / "30-user.policy").write_text("qubes.VMShell * sys-agent @dispvm allow\n")  # passe après
    lancer("packaging/install.sh", tmp_path)
    assert (d / "00-qaf.policy").is_file() and (d / "30-user.policy").is_file()


def test_install_ne_rechmode_pas_les_dossiers_de_qubes(tmp_path):
    """Parcours 4 : install -d -m 0755 passait /etc/qubes/policy.d de 0775
    (paquet de Qubes) à 0755. Un dossier qui existe garde son mode."""
    dossiers = ["etc/qubes/policy.d", "etc/qubes-rpc", "usr/share/applications",
                "etc/xdg/autostart", "etc/systemd/system"]
    for d in dossiers:
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d).chmod(0o775)
    lancer("packaging/install.sh", tmp_path)
    for d in dossiers:
        assert stat.S_IMODE((tmp_path / d).stat().st_mode) == 0o775, d
    assert (tmp_path / "etc/qubes/policy.d/00-qaf.policy").is_file()


def test_aucun_cr_dans_les_fichiers_livres():
    # .gitattributes met -text sur les dossiers livrés : rien ne convertit plus
    # les fins de ligne, un CR ne doit donc jamais y entrer (P3-4).
    for dossier in ("qaf_gate", "packaging", "policy", "tools", "qaf_mcp", "qaf_hand", "qaf_ui",
                    "profil", "materiel", *EXTENSIONS):
        for p in (RACINE / dossier).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                if p.suffix == ".png":  # binaire (*.png binary) : la signature PNG contient \r\n
                    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", p
                    continue
                assert b"\r" not in p.read_bytes(), p


def test_unites_ferment_les_sockets_qubesd_inutiles():
    # P2-1 : internal et misc sont aussi en groupe qubes.
    ex = (RACINE / "packaging/qaf-gate-executor.service").read_text()
    va = (RACINE / "packaging/qaf-gate-validator.service").read_text()
    chemins = lambda u: next(l for l in u.splitlines() if l.startswith("InaccessiblePaths=")).split()
    assert {"-/run/qubesd.internal.sock", "-/run/qubesd.misc.sock"} <= set(chemins(ex))
    assert "-/run/qubesd.sock" not in chemins(ex)  # le seul dont l'exécuteur a besoin
    assert {"-/run/qubesd.sock", "-/run/qubesd.internal.sock", "-/run/qubesd.misc.sock"} <= set(chemins(va))
    assert "StateDirectory" not in va  # le validateur n'écrit nulle part


@pytest.fixture
def faux_dom0(tmp_path):
    """qvm-run et sudo factices ; l'archive servie est qaf/packaging/install.sh."""
    import tarfile
    src = tmp_path / "src" / "qaf" / "packaging"
    src.mkdir(parents=True)
    (src / "install.sh").write_text("echo installé\n")
    archive = tmp_path / "qaf.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(tmp_path / "src" / "qaf", arcname="qaf")
    binaires = tmp_path / "bin"
    binaires.mkdir()
    journal = tmp_path / "appels"
    (binaires / "qvm-run").write_text(f'#!/bin/sh\necho qvm-run >> "{journal}"\ncat "{archive}"\n')
    (binaires / "sudo").write_text(f'#!/bin/sh\necho "sudo $*" >> "{journal}"\n')
    for f in binaires.iterdir():
        f.chmod(0o755)
    import hashlib
    env = {**os.environ, "PATH": f"{binaires}:{os.environ['PATH']}", "QAF_SOURCE_QUBE": "qube-source"}
    return env, hashlib.sha256(archive.read_bytes()).hexdigest(), journal


def qaf_update(env, *args, entree=""):
    return subprocess.run(["bash", str(RACINE / "tools/qaf-update"), *args], env=env, input=entree,
                          capture_output=True, text=True)


def test_qaf_update_demande_l_empreinte_avant_de_recuperer(faux_dom0):
    # P2-10 : sans empreinte de référence, rien n'est récupéré ni affiché.
    env, bonne, journal = faux_dom0
    r = qaf_update(env, entree="\n")
    assert r.returncode == 1 and not journal.exists()
    assert bonne not in r.stdout + r.stderr


def test_qaf_update_exige_le_qube_source(faux_dom0):
    # Aucun qube source par défaut : ni deviné, ni demandé ; rien n'est récupéré.
    env, bonne, journal = faux_dom0
    env = {k: v for k, v in env.items() if k != "QAF_SOURCE_QUBE"}
    r = qaf_update(env, bonne)
    assert r.returncode == 2 and "--qube=QUBE" in r.stderr and not journal.exists()
    for mauvais in ("--qube=a;b", "--archive=../x", "--archive=/etc/shadow"):
        r = qaf_update(env, "--qube=src", mauvais, bonne)
        assert r.returncode == 2 and not journal.exists(), mauvais
    r = qaf_update(env, "--qube=src", "--archive=depot/dist/qaf.tar.gz", bonne)
    assert r.returncode == 0, r.stderr
    assert "depuis src" in r.stdout and "depot/dist/qaf.tar.gz" in r.stdout


def test_qaf_update_n_affiche_la_recue_qu_en_cas_d_ecart(faux_dom0):
    env, bonne, journal = faux_dom0
    r = qaf_update(env, "0" * 64)
    assert r.returncode == 1 and bonne in r.stderr and bonne not in r.stdout
    assert "sudo" not in journal.read_text()
    r = qaf_update(env, entree=bonne + "\n")
    assert r.returncode == 0, r.stderr
    assert bonne not in r.stdout + r.stderr
    assert "sudo bash" in journal.read_text() and "install.sh" in journal.read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
@pytest.mark.skipif(not (RACINE / ".git").exists(), reason="pas un dépôt git (arbre exporté sans historique)")
def test_make_dist_independant_de_la_config_git(tmp_path):
    # P3-4 : même empreinte avec une configuration git hostile (umask, CRLF).
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(RACINE), str(clone)], check=True)
    empreintes = []
    for nom, conf in (("neutre", ""), ("hostile", "[tar]\n\tumask = 0077\n[core]\n\tautocrlf = true\n")):
        home = tmp_path / nom
        home.mkdir()
        (home / ".gitconfig").write_text(conf + "[safe]\n\tdirectory = *\n")
        env = {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["sh", str(clone / "tools/make-dist.sh")], env=env, check=True, capture_output=True)
        empreintes.append((clone / "dist/qaf.tar.gz.sha256").read_text().split()[0])
    assert empreintes[0] == empreintes[1]


def test_sysusers_gecos_sans_deux_points():
    # systemd-sysusers refuse un champ GECOS contenant « : » (vu sur le vrai dom0).
    import shlex
    from pathlib import Path
    for ligne in Path("packaging/sysusers-qaf.conf").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("u "):
            assert ":" not in shlex.split(ligne)[3], ligne


def lancer_ui(racine, utilisateur, check=True):
    env = {**os.environ, "QAF_RACINE": str(racine), "QAF_UTILISATEUR": utilisateur}
    return subprocess.run(["bash", str(RACINE / "packaging/install.sh")], env=env,
                          check=check, capture_output=True, text=True)


def test_install_fenetre_de_confirmation(tmp_path):
    r = lancer_ui(tmp_path, "user")
    for f in ["usr/lib/qaf/qaf_ui/confirmation.py", "usr/lib/qaf/qaf_ui/fenetre.py", "usr/libexec/qaf/qaf-ui",
              "etc/systemd/user/qaf-ui.service", "etc/xdg/autostart/qaf-ui.desktop", "etc/qaf/clavier-de-confiance",
              "etc/qaf/portier.json"]:
        assert (tmp_path / f).is_file(), f
    assert "from qaf_ui.serveur import main" in (tmp_path / "usr/libexec/qaf/qaf-ui").read_text()
    assert "from qaf_gate.executor import main" in (tmp_path / "usr/libexec/qaf/qaf-gate-executor").read_text()
    tmpfiles = (tmp_path / "etc/tmpfiles.d/qaf-ui.conf").read_text()
    assert "d /run/qaf-ui 2750 user qaf-exec -" in tmpfiles.splitlines()
    # Panneau de l'agent : ACL en lecture seule sur le seul journal, pour la seule session (§9.1).
    lecture = [ligne.split() for ligne in (tmp_path / "etc/tmpfiles.d/qaf-journal-lecture.conf").read_text().splitlines()
               if ligne.strip() and not ligne.startswith("#")]
    assert lecture == [["d", "/var/lib/qaf-exec", "0711", "qaf-exec", "qaf-exec", "-", "-"],
                       ["f", "/var/lib/qaf-exec/journal.jsonl", "0600", "qaf-exec", "qaf-exec", "-", "-"],
                       ["a+", "/var/lib/qaf-exec/journal.jsonl", "-", "-", "-", "-", "u:user:r,m::r"]]
    # La politique reste la dernière étape : qaf.Plan n'ouvre qu'une fois la fenêtre en place.
    etapes = [ligne for ligne in r.stdout.splitlines() if ligne.startswith("== ")]
    assert etapes[-1].startswith("== Politique qrexec") and any("qaf-ui" in e for e in etapes)
    # Le réglage de l'utilisateur n'est jamais écrasé ; le reste est idempotent.
    (tmp_path / "etc/qaf/clavier-de-confiance").write_text("Mon clavier\n")
    premier = arbre(tmp_path)
    lancer_ui(tmp_path, "user")
    assert arbre(tmp_path) == premier
    assert (tmp_path / "etc/qaf/clavier-de-confiance").read_text() == "Mon clavier\n"
    lancer("packaging/uninstall.sh", tmp_path)
    reste = arbre(tmp_path)
    assert not any("qaf-ui" in p or "qaf_ui" in p or "qaf-journal-lecture" in p for p in reste)
    assert "etc/qaf/clavier-de-confiance" in reste  # gardé sans --purge
    assert "etc/qaf/portier.json" in reste
    lancer("packaging/uninstall.sh", tmp_path, "--purge")
    assert not (tmp_path / "etc/qaf").exists()


@pytest.mark.parametrize("utilisateur", ["User", "a b", "x;rm", "-user", "o/../etc"])
def test_install_refuse_un_utilisateur_invalide(tmp_path, utilisateur):
    r = lancer_ui(tmp_path, utilisateur, check=False)
    assert r.returncode == 1 and "Rien n'a été installé" in r.stderr
    assert not (tmp_path / "usr").exists()


def test_unite_qaf_ui():
    u = (RACINE / "packaging/qaf-ui.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/libexec/qaf/qaf-ui" in u and "ConditionPathIsDirectory=/run/qaf-ui" in u
    d = (RACINE / "packaging/qaf-ui.desktop").read_text(encoding="utf-8")
    assert "import-environment DISPLAY XAUTHORITY" in d and "qaf-ui.service" in d
    from qaf_gate import confirm
    from qaf_ui import serveur
    assert confirm.SOCK_UI == serveur.SOCK_UI and confirm.SOCK_UI.startswith("/run/qaf-ui/")


def test_install_icone_de_suspension_puis_retire(tmp_path):
    lancer_ui(tmp_path, "user")
    for f in ["usr/bin/qaf-suspendre", "usr/bin/qaf-suspension", "usr/lib/qaf/qaf_ui/sni.py",
              "usr/lib/qaf/qaf_ui/ecrans/suspension.py", "usr/lib/qaf/qaf_ui/ecrans/suspension_gtk.py",
              "etc/systemd/user/qaf-suspension.service", "etc/xdg/autostart/qaf-suspension.desktop"]:
        assert (tmp_path / f).is_file(), f
    assert "from qaf_ui.ecrans.suspension import main" in (tmp_path / "usr/bin/qaf-suspension").read_text()
    assert "from qaf_ui.suspension import main" in (tmp_path / "usr/bin/qaf-suspendre").read_text()
    u = (RACINE / "packaging/qaf-suspension.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/qaf-suspension" in u.splitlines()
    assert "NoNewPrivileges" not in u.replace("pas de NoNewPrivileges", "")  # sudo -n qaf-suspendre aux clics
    d = (RACINE / "packaging/qaf-suspension.desktop").read_text(encoding="utf-8").splitlines()
    assert "OnlyShowIn=KDE;" in d  # jamais dans la session Xfce de Qubes d'origine
    assert any("import-environment DISPLAY XAUTHORITY" in ligne and "qaf-suspension.service" in ligne for ligne in d)
    lancer("packaging/uninstall.sh", tmp_path)
    assert not any("suspension" in p or "suspendre" in p for p in arbre(tmp_path))


def test_install_panneau_de_l_agent_puis_retire(tmp_path):
    lancer_ui(tmp_path, "user")
    for f in ["usr/bin/qaf-agent-panneau", "usr/lib/qaf/qaf_ui/usage.py",
              "usr/lib/qaf/qaf_ui/ecrans/agent_panneau.py", "usr/lib/qaf/qaf_ui/ecrans/agent_panneau_gtk.py",
              "etc/systemd/user/qaf-agent-panneau.service", "etc/xdg/autostart/qaf-agent-panneau.desktop",
              "etc/qubes/policy.d/30-qaf-usage.policy"]:
        assert (tmp_path / f).is_file(), f
    assert "from qaf_ui.ecrans.agent_panneau import main" in (tmp_path / "usr/bin/qaf-agent-panneau").read_text()
    u = (RACINE / "packaging/qaf-agent-panneau.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/qaf-agent-panneau" in u.splitlines()
    d = (RACINE / "packaging/qaf-agent-panneau.desktop").read_text(encoding="utf-8").splitlines()
    assert "OnlyShowIn=KDE;" in d  # jamais dans la session Xfce de Qubes d'origine
    # Politique posée avec l'agent long seulement, retirée avec lui.
    (tmp_path / "etc/qaf/portier.json").write_text('{"agent_long": false}\n')
    lancer_ui(tmp_path, "user")
    assert not (tmp_path / "etc/qubes/policy.d/30-qaf-usage.policy").exists()
    lancer("packaging/uninstall.sh", tmp_path)
    assert not any("agent-panneau" in x or "usage" in x for x in arbre(tmp_path))


def test_services_du_panneau_dans_tpl_agent():
    s = (RACINE / "tools/setup-sys-agent.sh").read_text(encoding="utf-8")
    for svc in ("qaf.Usage", "qaf.Console", "qaf.Connexion"):
        assert (RACINE / "qaf_mcp/etc/qubes-rpc" / svc).is_file()
        assert svc in s
    for svc in ("qaf.Console", "qaf.Connexion"):  # ouvrent un terminal : attendent la session
        assert "wait-for-session=true" in (RACINE / "qaf_mcp/etc/qubes/rpc-config" / svc).read_text()
    assert "@openai/codex@$CODEX_VERSION" in s and "CODEX_VERSION=${QAF_CODEX_VERSION:-0.163.0}" in s
    assert "@xai-official/grok" not in s.replace("@xai-official/grok pose", "")


def test_executeur_recoit_ses_deux_sockets_nommes():
    # proto._fd_systemd reconnaît les sockets par FileDescriptorName (C13).
    ex = (RACINE / "packaging/qaf-gate-executor.service").read_text()
    assert "Sockets=qaf-gate-executor.socket qaf-gate-demande.socket" in ex.splitlines()
    plan = (RACINE / "packaging/qaf-gate-executor.socket").read_text().splitlines()
    dem = (RACINE / "packaging/qaf-gate-demande.socket").read_text().splitlines()
    assert "FileDescriptorName=plan" in plan and "SocketGroup=qaf-gate" in plan
    assert "FileDescriptorName=demande" in dem and "ListenStream=/run/qaf-exec/demande.sock" in dem
    # la palette (utilisateur de bureau, groupe qubes) seule ; pas le validateur
    assert "SocketGroup=qubes" in dem and "SocketMode=0660" in dem
    assert "Service=qaf-gate-executor.service" in dem
    install = (RACINE / "packaging/install.sh").read_text()
    assert "qaf-gate-demande.socket" in install


def _depot_neuf(tmp_path):
    """Ce que make-dist archive (sa liste LIVRES, plus les extensions et .gitattributes),
    copié dans un git neuf avec un seul commit : un worktree vu depuis WSL n'a
    pas de .git utilisable (git ls-files y échoue)."""
    depot = tmp_path / "depot"
    md = (RACINE / "tools/make-dist.sh").read_text(encoding="utf-8")
    livres = next(l for l in md.splitlines() if l.startswith("LIVRES=")).split("=", 1)[1].strip('"').split()
    for nom in livres + EXTENSIONS + [".gitattributes"]:
        source = RACINE / nom
        if source.is_dir():
            shutil.copytree(source, depot / nom, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif source.is_file():
            depot.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, depot / nom)
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "essai"]):
        subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=depot, env=env, check=True)
    return depot, env


def test_licences_tiers_dans_l_archive_et_installee(tmp_path):
    # Les icônes au trait reprises de Lucide (ISC) voyagent avec leur notice : make-dist
    # la met à la racine de dist/qaf.tar.gz, install.sh la pose dans /usr/share/qaf.
    import tarfile
    depot, env = _depot_neuf(tmp_path)
    subprocess.run(["sh", str(depot / "tools/make-dist.sh")], cwd=depot, env=env, check=True, capture_output=True)
    with tarfile.open(depot / "dist/qaf.tar.gz") as t:
        noms = set(t.getnames())
        notice = t.extractfile("qaf/LICENCES-TIERS").read().decode("utf-8")
    assert "qaf/qaf_ui/nuit/icones.py" in noms and not any("packaging/themes" in n for n in noms)
    assert "Lucide" in notice and "ISC License" in notice and "Omarchy" not in notice
    # Une archive par extension, à part, sans la notice du produit ; sans extension, aucune.
    assert sorted(p.name for p in (depot / "dist").glob("qaf-*.tar.gz")) == [f"qaf-{e}.tar.gz" for e in EXTENSIONS]
    for e in EXTENSIONS:
        with tarfile.open(depot / f"dist/qaf-{e}.tar.gz") as t:
            assert "qaf/LICENCES-TIERS" not in t.getnames()
            assert all(n in ("qaf", f"qaf/{e}") or n.startswith(f"qaf/{e}/") for n in t.getnames())
    r = tmp_path / "r"
    lancer("packaging/install.sh", r)
    assert (r / "usr/share/qaf/LICENCES-TIERS").read_bytes() == (RACINE / "LICENCES-TIERS").read_bytes()
    lancer("packaging/uninstall.sh", r)
    assert not (r / "usr/share/qaf").exists()


def test_install_zone_de_notification_puis_retire(tmp_path):
    """qaf-zone remplace xembedsniproxy par un drop-in à nous sur l'unité --user
    de KDE (jamais livrée ni modifiée par QAF), retiré par uninstall.sh."""
    lancer("packaging/install.sh", tmp_path)
    d = tmp_path / "etc/systemd/user/plasma-xembedsniproxy.service.d/50-qaf-zone.conf"
    assert d.is_file()
    texte = d.read_text(encoding="utf-8")
    assert "ExecStart=\n" in texte and "/usr/libexec/qaf/qaf-zone" in texte
    assert "exec /usr/bin/xembedsniproxy" in texte  # repli si qaf-zone manque
    assert "from qaf_ui.zone_x import main" in (tmp_path / "usr/libexec/qaf/qaf-zone").read_text()
    for f in ("zone.py", "zone_x.py"):
        assert (tmp_path / "usr/lib/qaf/qaf_ui" / f).is_file(), f
    # Rien de KDE n'est posé : seulement notre drop-in.
    assert not (tmp_path / "etc/systemd/user/plasma-xembedsniproxy.service").exists()
    assert not (tmp_path / "usr/lib/systemd/user").exists()
    lancer("packaging/uninstall.sh", tmp_path)
    assert not (tmp_path / "etc/systemd/user/plasma-xembedsniproxy.service.d").exists()
    assert not (tmp_path / "usr/libexec/qaf/qaf-zone").exists()


def test_drop_in_zone_repli_sur_xembedsniproxy(tmp_path):
    """La ligne ExecStart lance qaf-zone s'il est là, sinon xembedsniproxy."""
    texte = (RACINE / "packaging/qaf-zone.conf").read_text(encoding="utf-8")
    ligne = [l for l in texte.splitlines() if l.startswith("ExecStart=/bin/sh -c ")][0]
    script = ligne[len("ExecStart=/bin/sh -c "):].strip("'")
    faux = tmp_path / "x"
    faux.mkdir()
    essai = script.replace("/usr/libexec/qaf/qaf-zone", str(faux / "qaf-zone")) \
                  .replace("/usr/bin/xembedsniproxy", str(faux / "xembed"))
    (faux / "xembed").write_text("#!/bin/sh\necho xembedsniproxy\n")
    (faux / "xembed").chmod(0o755)
    assert subprocess.run(["sh", "-c", essai], capture_output=True, text=True).stdout == "xembedsniproxy\n"
    (faux / "qaf-zone").write_text("#!/bin/sh\necho qaf-zone\n")
    (faux / "qaf-zone").chmod(0o755)
    assert subprocess.run(["sh", "-c", essai], capture_output=True, text=True).stdout == "qaf-zone\n"


def test_install_filet_degel_puis_retire(tmp_path):
    """qaf-degel (docs/GEL-BUREAU.md) : service --user lancé par l'autostart de Plasma."""
    lancer_ui(tmp_path, "user")
    for f in ["usr/libexec/qaf/qaf-degel", "usr/lib/qaf/qaf_ui/degel.py",
              "etc/systemd/user/qaf-degel.service", "etc/xdg/autostart/qaf-degel.desktop"]:
        assert (tmp_path / f).is_file(), f
    assert "from qaf_ui.degel import main" in (tmp_path / "usr/libexec/qaf/qaf-degel").read_text()
    u = (RACINE / "packaging/qaf-degel.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/libexec/qaf/qaf-degel" in u.splitlines()
    d = (RACINE / "packaging/qaf-degel.desktop").read_text(encoding="utf-8").splitlines()
    assert "OnlyShowIn=KDE;" in d
    assert any("import-environment DISPLAY XAUTHORITY" in ligne and "qaf-degel.service" in ligne for ligne in d)
    lancer("packaging/uninstall.sh", tmp_path)
    assert not any("degel" in p for p in arbre(tmp_path))
