"""Qube voisin (qaf_ui/voisin.py) : validation, écriture root à argv fixe, retrait ; contrôles
d'install.sh et de qaf-installer --verifier ; effet réel de la règle ; tunnel de sys-agent."""

import ast
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

from qaf_mcp import fournisseurs as f4
from qaf_ui import usage as u
from qaf_ui import voisin as v
from qaf_ui.ecrans import agent_panneau as p
from test_fournisseurs import faire, preparer_maison, serveur  # noqa: F401
from test_installateur import Dom0
from test_politique import DEFAUT, EXC, INFO, LONG, PARSER, POLITIQUE, avec_qrexec

RACINE = pathlib.Path(__file__).parent.parent
linux = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="bash Linux requis")
REGLE = "qubes.ConnectTCP +11434 sys-agent @default allow target=ia-locale\n"


def q(nom="ia-locale", classe="AppVM", **k):
    return v.Qube(nom, classe, "0x3465a4", False, **k)


# --- validation --------------------------------------------------------------------------

@pytest.mark.parametrize("nom", ["ia-locale", "gpu", "a", "A1_b.c-d", "x" * 31])
def test_noms_admis(nom):
    assert v.nom_valide(nom)


@pytest.mark.parametrize("nom", ["", "dom0", "sys-agent", "sys-net", "sys-ollama", "qaf-agent-dvm", "qaf-dvm", "disp12",
                                 "1abc", "-x", "a b", "a\n", "a/b", "@default", "@tag:x", "*", "x" * 32, "é", None, 3])
def test_noms_refuses(nom):
    assert not v.nom_valide(nom)


@pytest.mark.parametrize("texte,attendu", [("1", 1), ("11434", 11434), ("65535", 65535), ("0", None), ("65536", None),
                                           ("011", None), ("+1", None), ("1 ", None), ("1\n", None), ("", None),
                                           ("٣", None), ("1e3", None), (5, None), (None, None)])
def test_port(texte, attendu):
    assert v.port_valide(texte) == attendu


def test_filtre_de_la_liste():
    interdits = {"dom0", "sys-agent", "vault", "coffre"}
    assert v.admissible(q(), interdits) and v.admissible(q("gpu", "StandaloneVM"), interdits)
    assert v.admissible(q("sans-reseau"), interdits)  # un qube sans réseau est le cas utile
    for refus in (q("vault"), q("coffre"), q("sys-ia"), q("dom0"), q("sys-agent"), q("tpl", "TemplateVM"),
                  q("dvm", modele_jetables=True), q("netvm", fournit_reseau=True), q("disp3", "DispVM"),
                  q("jetable-nomme", "DispVM"), q("t", etiquettes=("qaf-agent",))):
        assert not v.admissible(refus, interdits), refus


def test_noms_interdits_portier(tmp_path):
    f = tmp_path / "portier.json"
    f.write_text(json.dumps({"coffres": ["a"], "systeme": ["b"], "ne_jamais_demarrer": ["c"], "admin_qubes": ["d"],
                             "jetables": ["e"]}))
    assert {"dom0", "sys-agent", "vault", "a", "b", "c", "d", "e"} == v.noms_interdits(f)
    f.write_text("[]")
    assert v.noms_interdits(f) is None
    f.write_text('{"coffres": "a"}')
    assert v.noms_interdits(f) is None
    assert v.noms_interdits(tmp_path / "absent") is None


class Vm:
    def __init__(self, name, klass="AppVM", marche=False, casse=False, **k):
        self.name, self.klass, self.marche, self.casse = name, klass, marche, casse
        self.tags, self.label = (), type("L", (), {"color": "0x3465a4"})()
        self.__dict__.update(k)

    def is_running(self):
        if self.casse:
            raise RuntimeError("refus")
        return self.marche


def test_lister_trie_filtre_et_ferme_si_illisible(tmp_path):
    f = tmp_path / "portier.json"
    f.write_text(json.dumps(json.loads((RACINE / "packaging/portier.json").read_text())))
    app = type("A", (), {"domains": [Vm("work", marche=True), Vm("dom0", "AdminVM"), Vm("sys-agent"), Vm("gpu", "StandaloneVM"),
                                     Vm("vault"), Vm("sys-net", provides_network=True), Vm("casse", casse=True),
                                     Vm("debian-13", "TemplateVM")]})()
    assert [(x.nom, x.en_marche, x.couleur) for x in v.lister(app, f)] == [("gpu", False, "0x3465a4"),
                                                                           ("work", True, "0x3465a4")]
    assert v.lister(app, tmp_path / "absent") is None

    class Cassee:
        @property
        def domains(self):
            raise OSError("qubesd")
    assert v.lister(Cassee(), f) is None


# --- écriture ----------------------------------------------------------------------------

def essai_main(tmp_path, args, qubes=lambda: [q(), q("gpu")], long_=True):
    f, lg = tmp_path / v.NOM_FICHIER, tmp_path / "long"
    if long_:
        lg.write_text("x")
    return v.main(args, chemin=str(f), long_=str(lg), qubes=qubes), f


def test_autoriser_forme_exacte(tmp_path, capsys):
    ancien = os.umask(0o077)  # l'umask de sudo, vu sur la vraie machine pour qaf-suspendre
    try:
        code, f = essai_main(tmp_path, ["autoriser", "ia-locale", "11434"])
    finally:
        os.umask(ancien)
    assert code == 0 and "ia-locale, port 11434" in capsys.readouterr().out
    assert [x for x in f.read_text().splitlines() if not x.startswith("#")] == [REGLE.strip()]
    assert stat.S_IMODE(f.stat().st_mode) == 0o644
    assert v.lire_regle(str(f)) == v.Regle("ia-locale", 11434)
    assert not pathlib.Path(str(f) + ".tmp").exists()


def test_une_seule_regle_remplacee(tmp_path):
    essai_main(tmp_path, ["autoriser", "ia-locale", "11434"])
    _, f = essai_main(tmp_path, ["autoriser", "gpu", "8080"])
    assert v.lire_regle(str(f)) == v.Regle("gpu", 8080)
    assert f.read_text().count("qubes.ConnectTCP") == 1


@pytest.mark.parametrize("args", [["autoriser", "dom0", "1"], ["autoriser", "sys-net", "1"], ["autoriser", "x y", "1"],
                                  ["autoriser", "ia-locale", "0"], ["autoriser", "ia-locale", "65536"],
                                  ["autoriser", "ia-locale"], ["autoriser", "ia-locale", "1", "x"], ["n'importe"],
                                  ["autoriser", "ia-locale\nqubes.VMShell * sys-agent @anyvm allow", "1"],
                                  ["autoriser", "ia-locale", "1\n* * sys-agent @anyvm allow"]])
def test_argv_refuse_sans_rien_ecrire(tmp_path, args):
    code, f = essai_main(tmp_path, args)
    assert code == 2 and not f.exists()


def test_qube_non_propose_par_qubesd_refuse(tmp_path):
    code, f = essai_main(tmp_path, ["autoriser", "autre", "1"])
    assert code == 1 and not f.exists()
    code, f = essai_main(tmp_path, ["autoriser", "ia-locale", "1"], qubes=lambda: None)
    assert code == 1 and not f.exists()


def test_refus_sans_liste_blanche(tmp_path):
    code, f = essai_main(tmp_path, ["autoriser", "ia-locale", "1"], long_=False)
    assert code == 1 and not f.exists()


def test_retirer_idempotent(tmp_path, capsys):
    _, f = essai_main(tmp_path, ["autoriser", "ia-locale", "1"])
    assert essai_main(tmp_path, ["retirer"])[0] == 0 and not f.exists()
    assert essai_main(tmp_path, ["retirer"])[0] == 0
    assert essai_main(tmp_path, [])[0] == 0 and "Aucun" in capsys.readouterr().out


def test_ecriture_ne_suit_pas_un_lien(tmp_path):
    cible = tmp_path / "cible"
    cible.write_text("intacte")
    f = tmp_path / v.NOM_FICHIER
    pathlib.Path(str(f) + ".tmp").symlink_to(cible)
    v.ecrire("ia-locale", 1, str(f))
    assert cible.read_text() == "intacte" and v.lire_regle(str(f)) == v.Regle("ia-locale", 1)


@pytest.mark.parametrize("texte", ["", "# rien\n", REGLE + REGLE, REGLE.replace("+11434", "*"),
                                   REGLE.replace("sys-agent", "@anyvm"), REGLE.replace("ia-locale", "dom0"),
                                   REGLE.replace("11434", "70000"), " " + REGLE, REGLE.replace("allow", "ask"),
                                   REGLE + "qubes.VMShell * sys-agent @anyvm allow\n", "é" + REGLE])
def test_lire_regle_inattendue_vaut_absente(tmp_path, texte):
    f = tmp_path / "x"
    f.write_text(texte, encoding="utf-8")
    assert v.lire_regle(str(f)) is None


def test_commande_a_argv_fixe():
    assert v.commande("autoriser", "gpu", "8080") == ["sudo", "-n", "/usr/bin/qaf-voisin", "autoriser", "gpu", "8080"]
    assert v.commande("retirer") == ["sudo", "-n", "/usr/bin/qaf-voisin", "retirer"]
    for mauvais in (("autoriser", "dom0", "1"), ("autoriser", "gpu", "0"), ("sh", "gpu", "1"), ("autoriser", None, None)):
        with pytest.raises(ValueError):
            v.commande(*mauvais)


def test_lancer_rapporte_l_echec():
    class R:
        returncode = 1
    assert v.lancer("autoriser", "gpu", "1", executer=lambda *a, **k: R()) is False
    R.returncode = 0
    assert v.lancer("retirer", executer=lambda *a, **k: R()) is True

    def casse(*a, **k):
        raise OSError("sudo")
    assert v.lancer("retirer", executer=casse) is False
    assert v.lancer("autoriser", "dom0", "1") is False


def test_marche_ne_lit_que_l_etat():
    vus = []

    def faux(argv, **k):
        vus.append(argv)
        return type("R", (), {"returncode": 0})()
    assert v.marche("gpu", faux) is True
    assert vus == [["qvm-check", "--quiet", "--running", "gpu"]]
    assert v.marche("dom0", faux) is None and len(vus) == 1


def test_le_module_reste_sobre():
    # dom0 : ni réseau, ni dépendance du Doorman ; qubesadmin seulement au moment de lister.
    arbre = ast.parse((RACINE / "qaf_ui/voisin.py").read_text(encoding="utf-8"))
    haut = {a.name for n in arbre.body if isinstance(n, ast.Import) for a in n.names}
    assert haut == {"json", "os", "re", "subprocess", "sys"}
    source = (RACINE / "qaf_ui/voisin.py").read_text(encoding="utf-8")
    for mot in ("socket", "urllib", "http.client", "qvm-pci", "qvm-usb", "qaf_gate", "attach"):
        assert mot not in source, mot


# --- les deux contrôles ------------------------------------------------------------------

def bash_voisin(script, chemin):
    """Extrait voisin_valide du script et l'essaie sur un fichier."""
    texte = (RACINE / script).read_text(encoding="utf-8")
    fonction = re.search(r"^voisin_valide\(\) \{\n.*?^\}\n", texte, re.M | re.S).group(0)
    return subprocess.run(["bash", "-c", fonction + 'voisin_valide "$1"', "x", str(chemin)]).returncode == 0


FORMES_REFUSEES = [
    "", "## vide\n", REGLE + REGLE, REGLE + "qubes.VMShell * sys-agent @anyvm allow\n",
    REGLE.replace("+11434", "*"), REGLE.replace("+11434", "+0"), REGLE.replace("+11434", "+65536"),
    REGLE.replace("+11434", "+99999"), REGLE.replace("+11434", "+1 2"), REGLE.replace("sys-agent", "@anyvm"),
    REGLE.replace("sys-agent", "work"), REGLE.replace("@default", "@anyvm"), REGLE.replace("allow", "ask"),
    REGLE.replace("ia-locale", "dom0"), REGLE.replace("ia-locale", "sys-net"), REGLE.replace("ia-locale", "qaf-agent-dvm"),
    REGLE.replace("ia-locale", "disp12"), REGLE.replace("target=ia-locale", "target=@adminvm"),
    REGLE.replace("target=ia-locale", "target=ia-locale user=root"), REGLE.replace(" target=ia-locale", ""),
    REGLE.replace("qubes.ConnectTCP", "qubes.VMShell"), REGLE.replace("qubes.ConnectTCP", "qubesXConnectTCP"),
    " " + REGLE, REGLE.replace(" ", "  ", 1), REGLE.replace("\n", "\r\n"), "!include /etc/x\n" + REGLE, "é" + REGLE,
    "# x\rqubes.ConnectTCP * sys-agent @anyvm allow\n" + REGLE, "# x\x0b\n" + REGLE,
]


@linux
@pytest.mark.parametrize("script", ["packaging/install.sh", "tools/qaf-installer"])
def test_voisin_valide_des_deux_scripts(tmp_path, script):
    f = tmp_path / "f.policy"
    for bon in (REGLE, v.ENTETE + REGLE, REGLE.rstrip("\n"), REGLE.replace("11434", "65535"), REGLE.replace("11434", "1"),
                REGLE.replace("ia-locale", "A1_b.c-d")):
        f.write_text(bon, encoding="utf-8")
        assert bash_voisin(script, f), repr(bon)
    for mauvais in FORMES_REFUSEES:
        f.write_text(mauvais, encoding="utf-8", newline="")
        assert not bash_voisin(script, f), repr(mauvais)
        assert v.lire_regle(str(f)) is None, repr(mauvais)  # dom0 et les scripts disent la même chose


@linux
@pytest.mark.parametrize("script", ["packaging/install.sh", "tools/qaf-installer"])
def test_voisin_valide_refuse_les_coffres(tmp_path, script):
    """Essai réel : une règle posée à la main vers vault avait la forme exacte et passait
    les deux contrôles. Mêmes listes que noms_interdits, portier.json illisible = refus."""
    texte = (RACINE / script).read_text(encoding="utf-8")
    fonction = re.search(r"^voisin_valide\(\) \{\n.*?^\}\n", texte, re.M | re.S).group(0)

    def admis(regle, portier):
        f = tmp_path / "f.policy"
        f.write_text(regle, encoding="utf-8")
        return subprocess.run(["bash", "-c", fonction + 'voisin_valide "$1" "$2"', "x", str(f), str(portier)]).returncode == 0

    portier = tmp_path / "portier.json"
    reglages = {"coffres": ["coffre-a"], "systeme": ["sysb"], "admin_qubes": ["atelier"], "ne_jamais_demarrer": ["nd"],
                "coffres_entrables": ["ce"], "jetables": ["jet"]}
    portier.write_text(json.dumps(reglages), encoding="utf-8")
    assert v.noms_interdits(str(portier)) >= {x for noms in reglages.values() for x in noms}
    assert admis(REGLE, portier)
    assert not admis(REGLE.replace("ia-locale", "vault"), portier)
    for noms in reglages.values():
        assert not admis(REGLE.replace("ia-locale", noms[0]), portier), noms
    assert not admis(REGLE, tmp_path / "absent.json")
    portier.write_text("{", encoding="utf-8")
    assert not admis(REGLE, portier)


@linux
def test_voisin_valide_refuse_un_lien(tmp_path):
    f = tmp_path / "vrai"
    f.write_text(REGLE)
    lien = tmp_path / "lien"
    lien.symlink_to(f)
    assert not bash_voisin("packaging/install.sh", lien) and not bash_voisin("tools/qaf-installer", lien)


def test_meme_fonction_dans_les_deux_scripts():
    fonctions = [re.search(r"^voisin_valide\(\) \{\n.*?^\}\n", (RACINE / s).read_text(encoding="utf-8"), re.M | re.S).group(0)
                 for s in ("packaging/install.sh", "tools/qaf-installer")]
    assert fonctions[0] == fonctions[1]
    motif = re.search(r"local re='\^(.*)\$'", fonctions[0]).group(1)
    assert motif == v.REGLE.pattern[2:-2]  # sans \A ni \Z
    for s in ("packaging/install.sh", "tools/qaf-installer"):
        assert f"VOISIN={v.NOM_FICHIER}" in (RACINE / s).read_text(encoding="utf-8")


def test_nom_trie_devant_la_liste_blanche():
    assert v.NOM_FICHIER < LONG.name < POLITIQUE.name
    assert sorted([POLITIQUE.name, LONG.name, v.NOM_FICHIER, "30-qaf-usage.policy", "90-default.policy"],
                  key=lambda n: n.encode())[0] == v.NOM_FICHIER


def test_aucun_fichier_de_regle_livre():
    assert not list(RACINE.glob("policy/*voisin*")) and not list(RACINE.glob("profil/*voisin*"))


def test_liste_blanche_inchangee():
    # install.sh n'accepte 00-qaf-agent-long.policy qu'identique : y toucher casserait la mise à jour.
    lignes = [x.split() for x in LONG.read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
    assert not any("ConnectTCP" in x[0] for x in lignes)
    assert lignes[-1] == ["*", "*", "sys-agent", "@anyvm", "deny"]


def dom0(tmp_path, avec=REGLE):
    d = Dom0(tmp_path)
    assert d.installer().returncode == 0
    f = d.r / "etc/qubes/policy.d" / v.NOM_FICHIER
    if avec is not None:
        f.write_text(avec, encoding="utf-8")
    return d, f


@linux
def test_verifier_accepte_la_forme_exacte_et_dit_la_regle(tmp_path):
    d, _ = dom0(tmp_path)
    r = d.lancer("--verifier")
    assert "triée avant" not in r.stdout and "qube voisin autorisé" in r.stdout, r.stdout
    assert "aucune politique ne passe avant 00-qaf.policy" in r.stdout


@linux
def test_verifier_refuse_une_ligne_en_plus(tmp_path):
    d, _ = dom0(tmp_path, REGLE + "qubes.VMShell * sys-agent @anyvm allow\n")
    r = d.lancer("--verifier")
    assert r.returncode == 1 and "triée avant 00-qaf.policy" in r.stdout


@linux
def test_verifier_refuse_la_regle_sans_agent_long(tmp_path):
    d, f = dom0(tmp_path)
    (d.r / "etc/qaf/portier.json").write_text(json.dumps({"agent_long": False}))
    r = d.lancer("--verifier")
    assert r.returncode == 1 and "ne dit pas agent_long" in r.stdout


@linux
def test_verifier_refuse_le_meme_nom_dans_run(tmp_path):
    d, f = dom0(tmp_path, None)
    run = d.r / "run/qubes/policy.d"
    run.mkdir(parents=True)
    (run / v.NOM_FICHIER).write_text(REGLE)
    assert "triée avant 00-qaf.policy" in d.lancer("--verifier").stdout


def _installer_seul(d):
    env = {**os.environ, "QAF_RACINE": str(d.r), "QAF_UTILISATEUR": "essai"}
    return subprocess.run(["bash", str(RACINE / "packaging/install.sh")], capture_output=True, text=True, env=env)


@linux
def test_mise_a_jour_garde_l_autorisation(tmp_path):
    d, f = dom0(tmp_path)
    r = _installer_seul(d)
    assert r.returncode == 0, r.stderr
    assert f.read_text() == REGLE
    assert (d.r / "usr/bin/qaf-voisin").is_file()
    assert "qaf_ui.voisin import main" in (d.r / "usr/bin/qaf-voisin").read_text()


@linux
def test_mise_a_jour_refuse_une_forme_inexacte(tmp_path):
    d, f = dom0(tmp_path, REGLE.replace("+11434", "*"))
    r = _installer_seul(d)
    assert r.returncode == 1 and "rien n'a été installé" in r.stderr


@linux
def test_sans_agent_long_l_installation_retire_la_regle(tmp_path):
    d, f = dom0(tmp_path)
    (d.r / "etc/qaf/portier.json").write_text(json.dumps({"agent_long": False}))
    assert _installer_seul(d).returncode == 0
    assert not f.exists()


@linux
def test_desinstallation_retire_tout(tmp_path):
    d, f = dom0(tmp_path)
    pathlib.Path(str(f) + ".tmp").write_text("x")
    env = {**os.environ, "QAF_RACINE": str(d.r)}
    r = subprocess.run(["bash", str(RACINE / "packaging/uninstall.sh")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not f.exists() and not pathlib.Path(str(f) + ".tmp").exists()
    assert not (d.r / "usr/bin/qaf-voisin").exists()


# --- effet réel, vrai parseur de qrexec ---------------------------------------------------

@pytest.fixture
def politique(tmp_path):
    shutil.copy(POLITIQUE, tmp_path / POLITIQUE.name)
    shutil.copy(LONG, tmp_path / LONG.name)
    shutil.copy(DEFAUT, tmp_path / "90-default.policy")
    v.ecrire("work", 11434, str(tmp_path / v.NOM_FICHIER))
    pol = PARSER.FilePolicy(policy_path=tmp_path)

    def evaluer(svc, src, cible, arg="+"):
        try:
            r = pol.evaluate(PARSER.Request(svc, arg, src, cible, system_info=INFO))
        except EXC.AccessDenied as ex:
            return str(ex) or "refus"
        return r.target if isinstance(r, PARSER.AllowResolution) else "ask"

    return evaluer


@avec_qrexec
def test_regle_prend_effet_devant_la_liste_blanche(politique):
    assert politique("qubes.ConnectTCP", "sys-agent", "@default", "+11434") == "work"


@avec_qrexec
def test_rien_de_plus_que_le_couple_choisi(politique):
    for arg in ("+22", "+1143", "+114340", "+", "+0"):
        assert politique("qubes.ConnectTCP", "sys-agent", "@default", arg) != "work", arg
    for cible in ("work", "default-dvm", "dom0", "@dispvm", "@adminvm"):
        assert politique("qubes.ConnectTCP", "sys-agent", cible, "+11434") not in ("work", "default-dvm", "dom0")
    for src in ("work", "piege", "default-dvm", "disp42"):
        assert politique("qubes.ConnectTCP", src, "@default", "+11434") not in ("work",)
    for svc in ("qubes.VMShell", "qubes.Filecopy", "qubes.OpenURL", "admin.vm.List"):
        assert politique(svc, "sys-agent", "@default", "+11434") not in ("work", "dom0"), svc
    assert politique("qaf.Plan", "sys-agent", "dom0") == "dom0"  # la liste blanche n'a pas bougé


@avec_qrexec
def test_sans_le_fichier_la_liste_blanche_masque(tmp_path):
    shutil.copy(POLITIQUE, tmp_path / POLITIQUE.name)
    shutil.copy(LONG, tmp_path / LONG.name)
    shutil.copy(DEFAUT, tmp_path / "90-default.policy")
    (tmp_path / "29-essai.policy").write_text(REGLE.replace("ia-locale", "work"))
    pol = PARSER.FilePolicy(policy_path=tmp_path)
    with pytest.raises(EXC.AccessDenied):
        pol.evaluate(PARSER.Request("qubes.ConnectTCP", "+11434", "sys-agent", "@default", system_info=INFO))


# --- la carte ----------------------------------------------------------------------------

class Clavier:
    ok, raison = True, ""


def test_ecran_passe_devant_tout():
    assert p.etat_carte(False, None, None, voisin=True) == "voisin"
    assert p.etat_carte(True, None, "ok", True, ("local", 1), True) == "voisin"
    assert p.etat_carte(True, None, "ok", True, ("local", 1)) == "connexion"


def test_autorisable_exige_tout():
    e = p.EcranVoisin(Clavier, None)
    assert not e.autorisable()
    e.qubes = (q(),)
    assert not e.autorisable()
    e.choix = "ia-locale"
    assert not e.autorisable()
    e.port = "0"
    assert not e.autorisable()
    e.port = "11434"
    assert e.autorisable()
    e.choix = "disparu"
    assert not e.autorisable()
    e.choix, e.en_cours = "ia-locale", True
    assert not e.autorisable()
    e.en_cours, e.regle = False, v.Regle("gpu", 1)
    assert not e.autorisable()

    class SansClavier:
        ok, raison = False, "x"
    e = p.EcranVoisin(SansClavier, None)
    e.qubes, e.choix, e.port = (q(),), "ia-locale", "11434"
    assert not e.autorisable()


def test_le_port_ne_reconstruit_pas_l_ecran():
    e = p.EcranVoisin(Clavier, None)
    avant = e.vue()
    e.port = "114"
    assert e.vue() == avant


def test_touches_du_port():
    for bon in ("0", "9", "KP_5", "BackSpace", "Delete", "Left", "Right", "Home", "End", "Tab"):
        assert p.touche_du_port(bon)
    for mauvais in ("a", "v", "minus", "Return", "KP_Enter", "Insert", "Up", "ampersand", ""):
        assert not p.touche_du_port(mauvais)


def mesure(sante):
    return u.Mesure("ok", "", (), None, (("local", "connecte"),), 2, "local", sante, "", ("claude", "local"))


def test_note_de_sante_du_voisin():
    r = v.Regle("gpu", 1)
    assert len(p.notes_voisin(mesure("inconnue"), r, None)) == 1
    notes = p.notes_voisin(mesure("inconnue"), r, False)
    assert "réveillerait" in notes[0] and notes[1] == "gpu : éteint"
    assert p.notes_voisin(mesure("inconnue"), r, True)[1] == "gpu : en marche"
    assert p.notes_voisin(mesure("ok"), r, True) == ()  # une adresse réseau sondée
    assert p.notes_voisin(mesure("inconnue"), None, None) == ()
    assert p.notes_voisin(None, r, None) == ()


def test_meta_du_local():
    assert p.meta_fournisseur(None, "local") == "réseau"
    assert p.meta_fournisseur(None, "local", v.Regle("gpu", 1)) == "qube voisin"


def test_schema_ferme_inchange():
    # Ni port, ni qube, ni lieu dans qaf.Usage : dom0 ne décide rien sur ce que dit sys-agent.
    source = (RACINE / "qaf_ui/usage.py").read_text(encoding="utf-8")
    for mot in ("voisin", "port_demande", "lieu_local"):
        assert mot not in source, mot
    bruit = {"v": 2, "etat": "ok", "offre": "", "fenetres": [], "actif": "local", "sante": "inconnue",
             "fournisseurs": {}, "connexion": "", "agents": ["claude"], "voisin": "gpu"}
    assert u.lire_mesure(json.dumps(bruit).encode()) is None
    assert v.__name__ not in sys.modules.get("qaf_ui.usage").__dict__


def test_dom0_jamais_de_clef_de_gate():
    # TCB : aucune ligne dans qaf_gate/, aucune mention du voisin.
    for f in (RACINE / "qaf_gate").glob("*.py"):
        assert "voisin" not in f.read_text(encoding="utf-8"), f


# --- côté sys-agent ----------------------------------------------------------------------

def test_port_voisin():
    assert f4.port_voisin("voisin 11434") == 11434 and f4.port_voisin("  neighbour 8080 ") == 8080
    assert f4.port_voisin("neighbor 1") == 1 and f4.port_voisin("相邻 1") == 1
    assert f4.port_voisin("http://127.0.0.1:11434") is None and f4.port_voisin("voisin") is None
    assert f4.port_voisin("voisin 0") is None and f4.port_voisin("voisin 11434 x") is None
    with pytest.raises(f4.Refus):
        f4.port_voisin("voisin 70000")


def test_unite_du_tunnel_forme_essayee():
    u_ = f4.texte_unite(11434)
    assert ('ExecStart=/usr/bin/socat TCP-LISTEN:11434,bind=127.0.0.1,reuseaddr,fork '
            'EXEC:"/usr/bin/qrexec-client-vm @default qubes.ConnectTCP+11434"\n') in u_
    assert "0.0.0.0" not in u_ and "WantedBy=default.target" in u_


def test_tunnel_pose_et_retire(tmp_path):
    m, vus = str(tmp_path), []
    ex = lambda argv, **k: vus.append(argv)  # noqa: E731
    f4.retirer_tunnel(m, ex)
    assert vus == []  # rien à retirer : systemctl n'est pas appelé
    f4.poser_tunnel(m, 8080, ex)
    assert pathlib.Path(f4.chemin_unite(m)).read_text() == f4.texte_unite(8080)
    assert ["systemctl", "--user", "enable", "qaf-voisin.service"] in vus
    assert ["systemctl", "--user", "restart", "qaf-voisin.service"] in vus
    vus.clear()
    f4.retirer_tunnel(m, ex)
    assert ["systemctl", "--user", "disable", "--now", "qaf-voisin.service"] in vus
    assert not os.path.exists(f4.chemin_unite(m))


def test_assistant_voisin_parcours_complet(tmp_path, serveur):  # noqa: F811
    m, vus = preparer_maison(tmp_path), []
    port = serveur.rsplit(":", 1)[1]
    ok, sortie = faire("local", m, [f"voisin {port}", "1"], [""], executer=lambda argv, **k: vus.append(argv))
    assert ok and "Autoriser un qube voisin" in sortie and "En clair" not in sortie
    assert f4.config(m, "local")["lieu"] == "voisin" and f4.config(m, "local")["base"] == serveur
    assert ["systemctl", "--user", "enable", "qaf-voisin.service"] in vus
    assert os.path.exists(f4.chemin_unite(m))
    # Santé : aucun appel, le tunnel réveillerait le qube.
    assert f4.sante("local", m, lambda *a, **k: 1 / 0) == "inconnue"


def test_assistant_voisin_tunnel_vide_explique_et_ne_laisse_rien(tmp_path):
    m = preparer_maison(tmp_path)
    ok, sortie = faire("local", m, ["voisin 9", "qwen3:4b", "q"], [""], executer=lambda argv, **k: None)
    assert not ok and "qube non autorisé" in sortie and "Pas de réponse" not in sortie
    assert not os.path.exists(f4.chemin_unite(m)) and not f4.activable("local", m)


def test_passer_a_une_adresse_retire_le_tunnel(tmp_path, serveur):  # noqa: F811
    m = preparer_maison(tmp_path)
    ex = lambda argv, **k: None  # noqa: E731
    port = serveur.rsplit(":", 1)[1]
    assert faire("local", m, [f"voisin {port}", "1"], [""], executer=ex)[0]
    assert faire("local", m, ["1", serveur, "1"], [""], executer=ex)[0]
    assert f4.config(m, "local")["lieu"] == "reseau" and not os.path.exists(f4.chemin_unite(m))


def test_refaire_le_test_garde_le_lieu(tmp_path, serveur):  # noqa: F811
    m = preparer_maison(tmp_path)
    ex = lambda argv, **k: None  # noqa: E731
    assert faire("local", m, [f"voisin {serveur.rsplit(':', 1)[1]}", "1"], [""], executer=ex)[0]
    assert faire("local", m, ["2"], executer=ex)[0]
    assert f4.config(m, "local")["lieu"] == "voisin" and os.path.exists(f4.chemin_unite(m))


def test_retirer_le_local_retire_le_tunnel(tmp_path, monkeypatch):
    m = preparer_maison(tmp_path)
    monkeypatch.setattr(f4, "_systemctl", lambda *a: None)
    f4.poser_tunnel(m, 8080)
    f4.retirer("local", m)
    assert not os.path.exists(f4.chemin_unite(m))


def test_sans_droits_message_propre(tmp_path, capsys, monkeypatch):
    def refus(*a):
        raise PermissionError
    monkeypatch.setattr(v, "retirer", refus)
    assert v.main(["retirer"], str(tmp_path / "f.policy")) == 1
    assert "sudo" in capsys.readouterr().err


def test_port_du_tunnel_en_place(tmp_path):
    m = str(tmp_path)
    ex = lambda *a, **k: None
    assert f4.port_tunnel(m) is None
    f4.poser_tunnel(m, 11434, ex)
    assert f4.port_tunnel(m) == 11434
    f4.retirer_tunnel(m, ex)
    assert f4.port_tunnel(m) is None


def test_lire_regle_refuse_un_fichier_trop_long(tmp_path):
    f = tmp_path / "f.policy"
    f.write_text("#" * 5000 + chr(10) + REGLE, encoding="utf-8")
    assert v.lire_regle(str(f)) is None
