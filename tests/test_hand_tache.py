"""qaf_hand : recadrage XWD, service qaf.Task et coureur factice."""

import io
import json

import pytest

from qaf_hand import coureur, service, tache, xwd
from faux_x import faux_xwd, lire_png
from test_hand import ECRAN, FauxSysteme


# --------------------------------------------------------------------- XWD

@pytest.mark.parametrize("bpp,pas,lsb", [(24, 64 * 4, True), (24, 64 * 3, True), (32, 64 * 4, True),
                                         (32, 64 * 4 + 8, False), (24, 64 * 3, False)])
def test_xwd_formats(bpp, pas, lsb):
    donnees = faux_xwd(64, 48, ECRAN, bpp=bpp, pas=pas, lsb=lsb)
    image, zone = xwd.recadrer_png(donnees, (5, 7, 20, 10))
    larg, haut, px = lire_png(image)
    assert zone == (5, 7, 20, 10) and (larg, haut) == (20, 10)
    assert all(px[j][i] == ECRAN(5 + i, 7 + j) for j in range(10) for i in range(20))


def test_xwd_masques_bgr():
    donnees = faux_xwd(8, 4, ECRAN, bpp=32, masques=(0xFF, 0xFF00, 0xFF0000))
    _, _, px = lire_png(xwd.recadrer_png(donnees)[0])
    assert px[3][7] == ECRAN(7, 3)


@pytest.mark.parametrize("modif,message", [
    (dict(version=6), "version 7"),
    (dict(fmt=1), "ZPixmap"),
    (dict(bpp=16), "non pris en charge"),
    (dict(pas=10), "non pris en charge"),
    (dict(masques=(0xF800, 0x7E0, 0x1F)), "masque"),
])
def test_xwd_refuses(modif, message):
    with pytest.raises(xwd.XwdError, match=message):
        xwd.lire(faux_xwd(8, 4, ECRAN, **modif))


def test_xwd_tronque_et_dimensions():
    d = faux_xwd(8, 4, ECRAN)
    for coupe in (d[:50], d[:-1]):
        with pytest.raises(xwd.XwdError, match="tronqué"):
            xwd.lire(coupe)
    with pytest.raises(xwd.XwdError, match="dimensions"):
        xwd.lire(faux_xwd(0, 4, ECRAN))


def test_borner():
    assert xwd.borner(-5, -5, 10, 10, 64, 48) == (0, 0, 5, 5)
    assert xwd.borner(60, 40, 10, 10, 64, 48) == (60, 40, 4, 8)
    assert xwd.borner(64, 0, 10, 10, 64, 48) is None
    assert xwd.borner(0, 0, 0, 10, 64, 48) is None


# ----------------------------------------------------------------- qaf.Task

def test_tache_acceptee(tmp_path):
    lances = []
    rep = tache.accepter({"tache": "lister\nexec uname -n"}, demarrer=lances.append, racine=tmp_path)
    assert rep["accepte"] is True and rep["coureur"] == "factice"
    dossier = tmp_path / rep["id"]
    assert lances == [dossier]
    assert json.loads((dossier / "tache.json").read_text("utf-8")) == {"tache": "lister\nexec uname -n"}


def test_une_seule_tache_par_jetable(tmp_path):
    tache.accepter({"tache": "lister"}, demarrer=lambda d: None, racine=tmp_path)
    with pytest.raises(service.Refus, match="déjà"):
        tache.accepter({"tache": "lister"}, demarrer=lambda d: None, racine=tmp_path)


@pytest.mark.parametrize("r", [{}, {"tache": ""}, {"tache": "x" * 1001}, {"tache": 3},
                               {"tache": "a\x1bb"}, {"tache": "a‮b"}, {"tache": "a b"},
                               {"tache": "x", "stdin": "y"}, ["x"]])
def test_tache_invalide(tmp_path, r):
    lances = []
    with pytest.raises(service.Refus):
        tache.accepter(r, demarrer=lances.append, racine=tmp_path)
    assert lances == [] and not (tmp_path / "verrou").exists()


def test_service_task_par_servir(monkeypatch, tmp_path):
    monkeypatch.setattr(tache, "racine_par_defaut", lambda: tmp_path)
    lances = []
    monkeypatch.setattr(tache, "demarrer_coureur", lances.append)
    rep, code = service.servir("task", io.BytesIO(b'{"tache": "lister"}'), FauxSysteme())
    assert code == 0 and rep["ok"] and rep["accepte"] and lances == [tmp_path / rep["id"]]
    rep, code = service.servir("task", io.BytesIO(b'{"tache": "lister", "x": 1}'), FauxSysteme())
    assert code == 1 and "champ inconnu" in rep["erreur"]


# --------------------------------------------------------- coureur factice

def test_analyser():
    assert coureur.analyser("# commentaire\n\nlister\n  taper  bonjour \n") == [
        ("lister", ""), ("taper", "bonjour")]
    for mauvais in ("ouvre exemple.fr et inscris-moi", "", "# rien", "lister\n" * 51):
        with pytest.raises(coureur.TacheInvalide):
            coureur.analyser(mauvais)


class Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def un_coureur(tmp_path, systeme=None):
    h, lances = Horloge(), []
    c = coureur.Coureur(tmp_path, lanceur=systeme or FauxSysteme(), demarrer=lances.append,
                        dormir=h.dormir, horloge=h)
    return c, lances


def test_coureur_bout_en_bout(tmp_path):
    c, lances = un_coureur(tmp_path)
    bilan = c.courir("lancer xterm -T 'qaf demo' -e sh -c 'cat > f'\nattendre ^Fire 5\n"
                     "taper bonjour\ntouche Return\ncliquer 3 4\ncapturer\nexec uname -n\npause 1")
    assert bilan == {"statut": "fini", "pas_faits": 8, "message": "", "captures": 1, "coureur": "factice"}
    assert lances == [["xterm", "-T", "qaf demo", "-e", "sh", "-c", "cat > f"]]  # argv, jamais de shell
    appels = c.lanceur.appels
    assert ["xdotool", "windowfocus", "--sync", "27262990"] in appels
    assert ["xdotool", "type", "--delay", "12", "--", "bonjour"] in appels
    assert ["xdotool", "mousemove", "--window", "27262990", "3", "4"] in appels
    assert ["uname", "-n"] in appels
    _, _, px = lire_png((tmp_path / "capture-1.png").read_bytes())
    assert px[0][0] == ECRAN(10, 20)  # la fenêtre « Firefox », recadrée
    journal = [json.loads(l) for l in (tmp_path / "journal.jsonl").read_text("utf-8").splitlines()]
    assert [j["verbe"] for j in journal][:3] == ["lancer", "attendre", "taper"]
    assert "image" not in json.dumps(journal)  # jamais les pixels dans le journal
    assert json.loads((tmp_path / "rapport.json").read_text("utf-8"))["statut"] == "fini"


def test_coureur_langage_naturel(tmp_path):
    c, lances = un_coureur(tmp_path)
    bilan = c.courir("ouvre exemple.fr et inscris-moi")
    assert bilan["statut"] == "non_interpretable" and "vrai coureur" in bilan["message"]
    assert lances == [] and c.lanceur.appels == []


def test_coureur_attendre_expire(tmp_path):
    c, _ = un_coureur(tmp_path)
    bilan = c.courir("attendre ^absente$ 3\ntaper x")
    assert bilan["statut"] == "echec" and bilan["pas_faits"] == 0
    assert c.horloge() >= 3
    assert not any(a[:2] == ["xdotool", "type"] for a in c.lanceur.appels)


def test_coureur_refus_d_qaf_hand(tmp_path):
    c, _ = un_coureur(tmp_path)
    bilan = c.courir("taper a\x1bb")
    assert bilan["statut"] == "echec" and "contrôle" in bilan["message"]
    c, _ = un_coureur(tmp_path)
    assert c.courir("cliquer x y")["statut"] == "echec"


def test_coureur_duree_max(tmp_path):
    c, _ = un_coureur(tmp_path)
    c.horloge.t = 0
    bilan = c.courir("pause 10\n" * 40)
    assert bilan["statut"] == "delai_depasse"


def test_est_jetable():
    vus = []

    def faux(sortie, code=0):
        return lambda argv, entree=None, delai=15: (vus.append(argv), (code, sortie, b""))[1]
    assert coureur.est_jetable(faux(b"DispVM\n"))
    assert not coureur.est_jetable(faux(b"AppVM\n"))
    assert not coureur.est_jetable(faux(b"", 1))
    # /type (classe) : /qubes-vm-type vaut « AppVM » dans un jetable (Qubes 4.3.2).
    assert vus[0] == ["qubesdb-read", "/type"]

    def absent(argv, entree=None, delai=15):
        raise service.Refus("commande absente")
    assert not coureur.est_jetable(absent)


def test_coureur_main(tmp_path, monkeypatch, capsys):
    assert coureur.main([]) == 2
    (tmp_path / "tache.json").write_text('{"tache": "ouvre un site"}', "utf-8")
    eteint = []
    monkeypatch.setattr(coureur, "est_jetable", lambda: False)
    monkeypatch.setattr(coureur.subprocess, "run", lambda *a, **k: eteint.append(a))
    assert coureur.main(["--eteindre", str(tmp_path)]) == 1
    assert eteint == []  # pas un jetable : jamais d'extinction
    monkeypatch.setattr(coureur, "est_jetable", lambda: True)
    coureur.main(["--eteindre", str(tmp_path)])
    assert eteint == [(["sudo", "-n", "/usr/bin/systemctl", "poweroff"],)]
    (tmp_path / "tache.json").write_text("pas du json", "utf-8")
    assert coureur.main([str(tmp_path)]) == 1
    assert json.loads((tmp_path / "rapport.json").read_text("utf-8"))["statut"] == "erreur"
