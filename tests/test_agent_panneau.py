"""Panneau de l'agent dans la barre de Plasma : logique sans GTK
(qaf_ui/ecrans/agent_panneau.py) et services qaf.Console / qaf.Connexion de
sys-agent (qaf_mcp/console.py)."""

import os
import signal

import pytest

from qaf_mcp import console, demande
from qaf_ui import usage as u
from qaf_ui import zone
from qaf_ui.ecrans import agent_panneau as p
from qaf_ui.nuit import contraste, icones, jetons

T = 1_793_000_000


def mesure(etat="ok", pourcent=34, fournisseurs=(("codex", "deconnecte"), ("grok", "non_installe"))):
    return u.Mesure(etat, "max_5x", (u.Fenetre("5h", "", pourcent, T + 8000),) if etat == "ok" else (),
                    None, fournisseurs)


def test_clics_comme_omarchy():
    assert p.geste_du_clic(1) == "carte"
    assert p.geste_du_clic(2) == "rafraichir"
    assert p.geste_du_clic(3) == "console"
    assert p.geste_du_clic(8) is None


def test_argv_fixes():
    assert p.ARGV_CONSOLE == ["qvm-run", "--quiet", "--service", "sys-agent", "qaf.Console"]
    assert p.argv_connexion("codex")[-1] == "qaf.Connexion+codex"
    assert "--no-gui" not in p.ARGV_CONSOLE + p.argv_connexion("claude")  # le service attend la session
    assert p.ARGV_ETAT == ["qvm-check", "--quiet", "--running", "sys-agent"]
    with pytest.raises(ValueError):
        p.argv_connexion("openai")


def test_memes_cles_des_deux_cotes():
    assert {f[0] for f in p.FOURNISSEURS} == set(console.CONNEXIONS)
    assert [f[1] for f in p.FOURNISSEURS] == ["Claude", "Codex", "Grok"]  # la liste d'Omarchy
    assert {"plus", "terminal", "fermer"} <= set(icones.TRACES)


def test_etat_de_la_carte():
    assert p.etat_carte(False, mesure(), "ok") == "arrete"
    assert p.etat_carte(True, None, None) == "attente"
    assert p.etat_carte(True, None, "sans_jeton") == "vide"
    assert p.etat_carte(True, mesure(), "ok") == "mesure"
    assert p.etat_carte(True, mesure(), "refuse") == "mesure"
    assert p.etat_carte(True, mesure(), "ok", ajout=True) == "ajout"
    assert p.etat_carte(False, None, None, connexion=("claude", T)) == "connexion"


def test_arret_jamais_de_mesure_sans_agent():
    # Carte sur un agent éteint : jauges gardées en T3, aucune demande de mesure
    # n'est faite (le fil de fond ne lance qaf.Usage que si qvm-check dit « en marche »).
    assert p.couleur_jauge(95, allume=False) == "T3"
    assert p.couleur_jauge(95, allume=True) == "ATTENTION"
    assert p.couleur_jauge(89, allume=True) == "ACCENT"
    assert "--no-autostart" in u.ARGV_USAGE


def test_etat_des_fournisseurs():
    assert p.etat_fournisseur(None, "claude") == "deconnecte"
    assert p.etat_fournisseur(mesure(), "claude") == "connecte"
    assert p.etat_fournisseur(mesure("sans_jeton"), "claude") == "deconnecte"
    assert p.etat_fournisseur(mesure(), "codex") == "deconnecte"
    assert p.etat_fournisseur(mesure(), "grok") == "non_installe"
    assert p.etat_fournisseur(None, "codex") == "non_installe"


def test_apparence_de_l_icone():
    assert p.apparence_icone(False, mesure(), T)[0] == "arrete"
    assert p.apparence_icone(True, mesure(pourcent=93), T)[0] == "alerte"
    assert p.apparence_icone(True, mesure(pourcent=50), T)[0] == "pret"
    # fenêtre échue : plus d'alerte
    assert p.apparence_icone(True, mesure(pourcent=93), T + 9000)[0] == "pret"


def test_placement_au_dessus_de_la_barre_du_bas():
    zone = (0, 0, 1920, 1040)  # barre de Plasma en bas (40 px)
    x, y = p.placement(1800, 1060, 412, 300, zone)
    assert y + 300 == 1040 - p.ECART_POINTEUR and x + 412 <= 1920 - p.ECART_POINTEUR
    x, y = p.placement(10, 20, 412, 300, (0, 40, 1920, 1040))  # barre en haut
    assert y == 40 + p.ECART_POINTEUR and x == p.ECART_POINTEUR


def test_mesures_de_la_carte():
    assert p.LARGEUR == 412 and p.CONTENU == 376 and p.JAUGE == 4
    assert p.ECART_FOURNISSEURS == 10 and p.ECART_JAUGES == 12 and p.ROTATION_MS == 2800


def test_plus_de_taches_pre_remplies():
    # 28/10 : ni « LANCER UNE TÂCHE » ni tuiles, ni amorce dans la console.
    import pathlib
    racine = pathlib.Path(__file__).parent.parent
    for f in ("qaf_ui/ecrans/agent_panneau.py", "qaf_ui/ecrans/agent_panneau_gtk.py", "qaf_mcp/console.py"):
        texte = (racine / f).read_text(encoding="utf-8")
        assert "LANCER UNE TÂCHE" not in texte.replace("Pas de tuiles « LANCER UNE TÂCHE »", "")
        assert "AMORCE" not in texte and "tuile" not in texte.lower().replace("pas de tuiles", "")
    assert not (racine / "qaf_mcp/amorce.py").exists()
    assert not hasattr(p, "TUILES") and not hasattr(console, "AMORCES")


# --- icône : style des icônes de qubes de qaf-zone ------------------------------------------

VIOLET = 0x9F389F  # label « purple » de Qubes


def tete_xgetimage(cote=32):
    """Fausse tête noire sur blanc au format XGetImage (B, G, R, X)."""
    octets = bytearray(b"\xff" * cote * cote * 4)
    for y in range(8, 24):
        for x in range(6, 26):
            if y in (8, 23) or x in (6, 25) or (y == 14 and x in (11, 12, 19, 20)):
                octets[(y * cote + x) * 4:(y * cote + x) * 4 + 3] = b"\x00\x00\x00"
    return cote, cote, cote * 4, bytes(octets)


def pixels(image):
    largeur, hauteur, o = image
    return [(o[i], o[i + 1], o[i + 2], o[i + 3]) for i in range(0, largeur * hauteur * 4, 4)]


def test_label_lu_jamais_code_en_dur():
    assert p.label_de("0x9f389f") == VIOLET
    assert p.label_de("#9F389F") == VIOLET
    for illisible in (None, "", "violet", "0x12345", 42):
        assert p.label_de(illisible) == p.LABEL_DEFAUT


def test_icone_au_style_qaf_zone():
    image = zone.icone_qube(*tete_xgetimage(), VIOLET)
    encre = zone.couleur_encre(VIOLET)
    assert contraste.ratio(encre, zone.FOND_BARRE) >= 3
    vus = {(r, g, b) for a, r, g, b in pixels(image) if a}
    assert vus == {encre}  # encre et trait, tout à la couleur du label éclaircie
    largeur, hauteur, o = image
    # trait de label collé au bas
    bas = [pixels(image)[(hauteur - 1) * largeur + x][0] for x in range(largeur)]
    assert max(bas) == 255


def test_tete_violette_trait_au_label():
    # Direction B : la tête de l'agent au violet SIGNATURE, le trait dessous garde
    # la couleur du label de sys-agent (repère que seul dom0 pose).
    from qaf_ui.nuit import jetons
    signature = contraste.rgb(jetons.SIGNATURE)
    image = zone.icone_qube(*tete_xgetimage(), VIOLET, dessin=signature)
    largeur, hauteur, _ = image
    px = pixels(image)
    corps = {(r, g, b) for a, r, g, b in px[:(hauteur - zone.TRAIT[1]) * largeur] if a}
    trait = {(r, g, b) for a, r, g, b in px[(hauteur - zone.TRAIT[1]) * largeur:] if a}
    assert corps == {signature} and trait == {zone.couleur_encre(VIOLET)}
    assert contraste.ratio(signature, zone.FOND_BARRE) >= 3
    from qaf_ui import couleurs_plasma
    assert couleurs_plasma.Palette().ACCENT == jetons.SIGNATURE  # jauges : plus le bleu de Plasma


def test_arrete_attenue_l_encre_pas_le_trait():
    image = zone.icone_qube(*tete_xgetimage(), VIOLET)
    eteinte = p.attenuer(image, p.ATTENUATION_ARRETE, zone.TRAIT[1])
    largeur, hauteur, _ = image
    avant, apres = pixels(image), pixels(eteinte)
    corps = [i for i in range((hauteur - zone.TRAIT[1]) * largeur) if avant[i][0]]
    assert corps and all(apres[i][0] < avant[i][0] for i in corps if avant[i][0] > 4)
    trait = range((hauteur - zone.TRAIT[1]) * largeur, hauteur * largeur)
    assert all(apres[i] == avant[i] for i in trait)


def test_alerte_pastille_attention():
    image = zone.icone_qube(*tete_xgetimage(), VIOLET)
    from qaf_ui import couleurs_plasma
    alerte = p.pastille(image, couleurs_plasma.palette().ATTENTION)
    largeur, _, _ = alerte
    from qaf_ui import couleurs_plasma
    r, g, b = (int(couleurs_plasma.palette().ATTENTION[k:k + 2], 16) for k in (1, 3, 5))
    coin = pixels(alerte)[3 * largeur + largeur - 4]
    assert coin == (255, r, g, b)
    assert contraste.ratio((r, g, b), zone.FOND_BARRE) >= 3



def test_contrastes_de_la_carte():
    # Palette de Plasma (Breeze sombre, valeurs de repli comme dans dom0).
    from qaf_ui import couleurs_plasma
    k = couleurs_plasma.Palette()
    for texte, fond in (("T1", "SURFACE"), ("T2", "SURFACE"), ("T3", "SURFACE"), ("T1", "CHAMP"),
                        ("T1", "CHOISI"), ("T2", "CHOISI")):
        assert contraste.ratio(getattr(k, texte), getattr(k, fond)) >= 4.5, (texte, fond)
    # éléments graphiques : jauge pleine et gardée sur la piste, ATTENTION sur la carte
    assert contraste.ratio(k.ATTENTION, k.SURFACE) >= 3
    assert contraste.ratio(k.ACCENT, k.CHOISI) >= 3
    assert contraste.ratio(k.T3, k.CHOISI) >= 1.5  # gardée : volontairement éteinte


def test_palette_lue_dans_le_theme_plasma(tmp_path):
    from qaf_ui import couleurs_plasma as cp
    (tmp_path / "breeze-dark").mkdir()
    (tmp_path / "breeze-dark/colors").write_text(
        "[Colors:Window]\nBackgroundNormal=10,20,30\nForegroundNormal=250,250,250\n"
        "[Colors:Selection]\nBackgroundNormal=1,2,300\n")
    k = cp.palette(config=str(tmp_path / "absent"), themes=str(tmp_path))
    assert k.SURFACE == "#0A141E" and k.T1 == "#FAFAFA"
    assert k.ACCENT == cp.Palette().ACCENT  # 300 hors bornes : valeur de repli
    (tmp_path / "rc").write_text("[Theme]\nname=../../etc\n")
    assert cp.theme_choisi(str(tmp_path / "rc")) == "default"
    assert cp.palette(config=str(tmp_path / "rc"), themes=str(tmp_path / "rien")) == cp.Palette()


class FauxAffichage:
    racine = 1

    def __init__(self, fenetres):
        self.f = fenetres
        self.envois = []

    def lire_entiers(self, xid, nom):
        return list(self.f)

    def lire_octets(self, xid, nom):
        vm, titre = self.f[xid]
        return vm if nom == "_QUBES_VMNAME" else titre

    def envoyer(self, *a):
        self.envois.append(a)

    def vider(self):
        pass


def test_console_existante_choisie_par_le_nom_du_qube():
    aff = FauxAffichage({10: (b"sys-agent", "[sys-agent] Agent".encode()),
                         11: (b"qube-admin", "[sys-agent] Agent".encode()),  # titre imité : ignoré
                         12: (b"sys-agent", "[sys-agent] Connexion Claude".encode())})
    assert p.fenetre_agent(aff) == 10
    p.amener_devant(aff, 10)
    assert aff.envois[0][1:3] == (10, "_NET_ACTIVE_WINDOW")
    assert p.fenetre_agent(FauxAffichage({11: (b"qube-admin", b"[sys-agent] Agent")})) is None


def test_ordres_a_soi(tmp_path):
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    vus = []
    assert p.envoyer_ordre("--ouvrir", env, lambda pid, sig: vus.append((pid, sig))) == 1
    (tmp_path / p.VERROU).write_text("4242\n")
    assert p.envoyer_ordre("--ouvrir", env, lambda pid, sig: vus.append((pid, sig))) == 0
    assert p.envoyer_ordre("--fermer", env, lambda pid, sig: vus.append((pid, sig))) == 0
    assert vus == [(4242, signal.SIGUSR1), (4242, signal.SIGUSR2)]


# --- services de sys-agent ---------------------------------------------------------------

class FauxProc:
    pid = 4321


def test_console_refuse_un_qube_et_tout_argument(tmp_path):
    lances = []
    lancer = lambda argv, **k: lances.append(argv) or FauxProc()  # noqa: E731
    assert console.console({"QREXEC_REMOTE_DOMAIN": "qube-admin"}, lancer, str(tmp_path)) == 1
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "QREXEC_SERVICE_ARGUMENT": "pirate", "HOME": str(tmp_path)}
    assert console.console(env, lancer, str(tmp_path)) == 1
    assert lances == []


def test_console_ouvre_le_terminal_agent(tmp_path):
    lances = []
    lancer = lambda argv, **k: lances.append((argv, k)) or FauxProc()  # noqa: E731
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path), "QAF_REQUEST": "R-1234",
           demande.VARIABLE_JETON: "fuite"}
    assert console.console(env, lancer, str(tmp_path)) == 0
    argv, k = lances[0]
    assert argv[:2] == [demande.TERMINAL, "--disable-server"] and f"--title={demande.TITRE}" in argv
    assert argv[-1] == demande.CLAUDE  # pas de conversation : pas de --continue
    assert "QAF_REQUEST" not in k["env"] and demande.VARIABLE_JETON not in k["env"]
    assert (tmp_path / "qaf-request" / "terminal").read_text() == "4321\n"


def test_console_deja_ouverte_rien_de_plus(tmp_path):
    proc = tmp_path / "proc" / "4321"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"\0".join([demande.TERMINAL.encode(), b"--title=Agent"]))
    (tmp_path / "qaf-request").mkdir()
    (tmp_path / "qaf-request" / "terminal").write_text("4321\n")
    lances = []
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "HOME": str(tmp_path)}
    assert console.console(env, lambda a, **k: lances.append(a), str(tmp_path), str(tmp_path / "proc")) == 0
    assert lances == []


def test_connexion(tmp_path):
    lances = []
    lancer = lambda argv, **k: lances.append(argv) or FauxProc()  # noqa: E731
    env = {"QREXEC_REMOTE_DOMAIN": "dom0", "QREXEC_SERVICE_ARGUMENT": "claude", "HOME": str(tmp_path)}
    assert console.connexion(env, lancer, existe=lambda c: True) == 0
    assert lances[0][-3:] == [demande.CLAUDE, "auth", "login"]
    assert console.connexion({**env, "QREXEC_SERVICE_ARGUMENT": "grok"}, lancer, existe=lambda c: False) == 2
    assert console.connexion({**env, "QREXEC_SERVICE_ARGUMENT": "x"}, lancer) == 1
    assert console.connexion({**env, "QREXEC_REMOTE_DOMAIN": "vault"}, lancer) == 1
    assert len(lances) == 1
