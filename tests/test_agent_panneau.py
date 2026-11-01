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


def mesure(etat="ok", pourcent=34, fournisseurs=(("codex", "deconnecte"), ("grok", "non_installe")), **v2):
    return u.Mesure(etat, "max_5x", (u.Fenetre("5h", "", pourcent, T + 8000),) if etat == "ok" else (),
                    None, fournisseurs, **v2)


def v2(**k):
    k.setdefault("fournisseurs", (("codex", "connecte"), ("grok", "non_installe"), ("grokbot", "connexion_seule"),
                                  ("agy", "connecte"), ("zai", "deconnecte"), ("openrouter", "connecte"),
                                  ("local", "connecte")))
    k.setdefault("agents", ("claude", "agy", "openrouter", "local"))
    return mesure(v=2, **k)


def test_retrait_ferme_l_attente_sans_mentir():
    avant = ("claude", "openrouter")
    assert p.connexion_retiree(v2(agents=("claude",)), "openrouter", avant)
    assert not p.connexion_retiree(v2(agents=avant), "openrouter", avant)  # encore là
    assert not p.connexion_retiree(v2(agents=("claude",)), "zai", avant)  # première connexion
    assert not p.connexion_retiree(v2(agents=("claude",), connexion="echec"), "openrouter", avant)
    assert not p.connexion_retiree(v2(agents=(), connexion=""), "claude", avant)


def test_clics_comme_omarchy():
    assert p.geste_du_clic(1) == "carte"
    assert p.geste_du_clic(2) == "rafraichir"
    assert p.geste_du_clic(3) == "console"
    assert p.geste_du_clic(8) is None


def test_argv_fixes():
    assert p.ARGV_CONSOLE == ["qvm-run", "--quiet", "--service", "sys-agent", "qaf.Console"]
    assert p.argv_connexion("codex")[-1] == "qaf.Connexion+codex"
    assert [p.argv_connexion(k)[-1] for k in u.IDS] == [f"qaf.Connexion+{k}" for k in u.IDS]  # tous
    assert "--no-gui" not in p.ARGV_CONSOLE + p.argv_connexion("claude")  # le service attend la session
    assert p.ARGV_ETAT == ["qvm-check", "--quiet", "--running", "sys-agent"]
    with pytest.raises(ValueError):
        p.argv_connexion("openai")


def test_memes_ids_des_deux_cotes():
    """Test croisé du contrat : dom0 a sa table, sys-agent la sienne, jamais importée par dom0."""
    table = pytest.importorskip("qaf_mcp.fournisseurs_table")
    ids = getattr(table, "IDS", None) or tuple(getattr(table, "FOURNISSEURS"))
    assert tuple(getattr(f, "id", f["id"] if isinstance(f, dict) else f) for f in ids) == u.IDS
    assert tuple(console.CONNEXIONS) == u.IDS  # qaf.Connexion+ID accepte les 35


def test_dom0_n_importe_pas_la_table_de_sys_agent():
    import pathlib
    racine = pathlib.Path(__file__).parent.parent
    for f in racine.joinpath("qaf_ui").rglob("*.py"):
        assert "qaf_mcp" not in f.read_text(encoding="utf-8").replace("qaf_mcp/", ""), f


def test_sections_dans_l_ordre_de_la_table():
    assert sum((ids for _, ids in p.SECTIONS), ()) == u.IDS
    assert [cle for cle, _ in p.SECTIONS] == ["carte.abonnements", "carte.autres_cli", "carte.cles_api",
                                              "carte.sec_locale"]
    assert p.SECTIONS[0][1][-1] == "opencode"
    assert p.SECTIONS[1][1] == ("pi", "omp", "ori", "crush", "openclaw", "dsh", "mcode", "kiro-cli", "freebuff",
                                "kilo", "amp", "commandcode")
    assert p.SECTIONS[2][1] == ("zai", "ollama", "minimax", "openrouter", "deepseek", "fireworks", "synthetic")
    assert p.SECTIONS[2][1] == p.CLES_API
    assert p.SECTIONS[3][1] == ("local",)
    assert {"plus", "terminal", "fermer", "coche"} <= set(icones.TRACES) and "chevron" not in icones.TRACES


def test_etat_de_la_carte():
    assert p.etat_carte(False, mesure(), "ok") == "arrete"
    assert p.etat_carte(True, None, None) == "attente"
    assert p.etat_carte(True, None, "sans_jeton") == "vide"
    assert p.etat_carte(True, mesure(), "ok") == "mesure"
    assert p.etat_carte(True, mesure(), "refuse") == "mesure"
    assert p.etat_carte(True, mesure(), "ok", ajout=True) == "ajout"
    assert p.etat_carte(False, None, None, connexion=("claude", T)) == "connexion"
    # Sans compte Claude, un autre actif sort de l'écran « connecte-toi ».
    assert p.etat_carte(True, v2(etat="sans_jeton", actif="openrouter"), "sans_jeton") == "mesure"
    assert p.etat_carte(True, v2(etat="sans_jeton"), "sans_jeton") == "vide"


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


def test_statuts_des_lignes():
    m = v2()
    assert p.statut_ligne(m, "claude") == "actif"
    assert p.statut_ligne(m, "agy") == "connecte"
    assert p.statut_ligne(m, "codex") == "connexion_seule"  # connecté, sans serveur MCP qaf
    assert p.statut_ligne(m, "grokbot") == "connexion_seule"
    assert p.statut_ligne(m, "zai") == "a_configurer" and p.statut_ligne(m, "grok") == "non_installe"
    assert p.statut_ligne(v2(actif="local"), "local") == "actif" and p.statut_ligne(v2(actif="local"), "claude") == "connecte"
    # Aucune coche sur un Claude sans compte.
    assert p.statut_ligne(v2(etat="sans_jeton"), "claude") == "a_configurer"
    assert p.statut_ligne(None, "claude") == "a_configurer"


def test_cle_ou_adresse_sans_nouvelle_reste_a_configurer():
    m = v2(fournisseurs=())
    assert p.statut_ligne(m, "zai") == "a_configurer" and p.statut_ligne(m, "local") == "a_configurer"
    assert p.statut_ligne(m, "kimi") == "non_installe"
    assert p.statut_ligne(mesure(), "zai") == "non_installe"  # v1 : sys-agent ne le connaît pas


def test_politique_de_la_bascule():
    import pathlib
    lignes = [" ".join(x.split()) for x in pathlib.Path(__file__).parent.parent.joinpath(
        "policy/30-qaf-usage.policy").read_text(encoding="utf-8").splitlines()]
    assert "qaf.Fournisseur * @adminvm sys-agent allow" in lignes
    assert "qaf.Fournisseur * @anyvm @anyvm deny" in lignes


def test_activable():
    assert p.activable(v2(), "openrouter") and p.activable(v2(), "claude")
    assert not p.activable(v2(), "codex")  # pas dans agents : ne devient jamais l'agent
    assert not p.activable(v2(agents=("claude", "grokbot")), "grokbot")
    assert not p.activable(v2(agents=("zai",)), "zai")  # non connecté
    assert not p.activable(mesure(), "codex") and not p.activable(None, "claude")


def test_gestes_des_lignes():
    m = v2()
    assert p.geste_ligne(m, "openrouter", True) == "basculer"
    assert p.geste_ligne(m, "zai", True) == "terminal"
    assert p.geste_ligne(m, "grok", True) == "terminal"  # installation à la demande
    assert p.geste_ligne(m, "grokbot", True) == "terminal"
    assert p.geste_ligne(m, "claude", True) is None  # déjà l'actif
    assert all(p.geste_ligne(m, k, False) is None for k in u.IDS)  # jamais de démarrage implicite
    # sys-agent en v1 : seuls Claude et Codex répondent à qaf.Connexion.
    assert p.geste_ligne(mesure(fournisseurs=(("codex", "deconnecte"), ("grok", "non_installe"))), "kimi", True) is None
    assert p.geste_ligne(mesure(), "grok", True) is None and p.geste_ligne(mesure(), "codex", True) == "terminal"
    assert p.geste_ligne(None, "kimi", True) is None and p.geste_ligne(None, "claude", True) == "terminal"


def test_icone_du_terminal_seulement_si_la_ligne_fait_autre_chose():
    m = v2()
    assert p.icone_terminal(m, "claude", True) and p.icone_terminal(m, "openrouter", True)
    assert not p.icone_terminal(m, "zai", True) and not p.icone_terminal(m, "grokbot", True)
    assert not p.icone_terminal(m, "claude", False)


def test_carte_normale_seulement_les_utilisables():
    assert p.utilisables(v2()) == ["agy", "openrouter", "local"]
    assert p.utilisables(v2(actif="openrouter")) == ["claude", "agy", "local"]
    assert p.utilisables(None) == [] and p.utilisables(mesure()) == []


def test_notes_et_ecrans():
    assert p.note_actif("openrouter") == "carte.note_or" and p.note_actif("local") == "carte.note_local"
    assert p.note_actif("codex") == "carte.note_abonnement"
    assert p.note_actif("ori") == "carte.note_cli" and p.note_actif("dsh") == "carte.note_cli"
    assert p.notes_bascule("zai", False) == ("carte.note_bascule",)
    assert p.notes_bascule("zai", True) == ("carte.note_bascule", "carte.note_console")
    assert "carte.note_google" in p.notes_bascule("agy", False)
    assert p.etapes_connexion("zai")[1] == "carte.et_cle" and p.etapes_connexion("local")[1] == "carte.et_lieu"
    assert p.etapes_connexion("codex")[1] == "carte.et_compte" and len(p.etapes_connexion("codex")) == 3
    assert p.ecran_connexion(None) == "attente" and p.ecran_connexion(v2()) == "attente"
    assert p.ecran_connexion(v2(connexion="ok")) == "ok" and p.ecran_connexion(v2(connexion="echec")) == "echec"
    assert p.titre_pret("local") == "IA locale est prête" and p.titre_pret("zai") == "Z.ai est prêt"
    assert "verra tout ce que lit l'agent" in p.phrase_pret("zai")
    assert "verra tout ce que lit l'agent" in p.phrase_pret("local")


def test_connexion_v1_se_ferme_v2_attend_le_resultat():
    fin = mesure(fournisseurs=(("codex", "connecte"), ("grok", "non_installe")))
    assert p.connexion_finie_v1(fin, "codex", "ok")
    assert not p.connexion_finie_v1(mesure(), "codex", "ok")
    assert not p.connexion_finie_v1(v2(), "openrouter", "ok")  # déjà connecté : ne ferme pas


def test_largeur_des_durees_en_chinois():
    from qaf_ui import i18n
    avant = i18n.langue()
    try:
        i18n.choisir("zh")
        assert p.largeur_duree() == 72
        i18n.choisir("fr")
        assert p.largeur_duree() == 56
    finally:
        i18n.choisir(avant)


def test_apparence_de_l_icone():
    assert p.apparence_icone(False, mesure(), T)[0] == "arrete"
    assert p.apparence_icone(True, mesure(pourcent=93), T)[0] == "alerte"
    assert p.apparence_icone(True, mesure(pourcent=50), T)[0] == "pret"
    # fenêtre échue : plus d'alerte
    assert p.apparence_icone(True, mesure(pourcent=93), T + 9000)[0] == "pret"
    # les 93 % sont ceux de Claude : pas de pastille s'il n'est pas l'actif
    assert p.apparence_icone(True, v2(pourcent=93, actif="zai"), T)[0] == "pret"


def test_placement_au_dessus_de_la_barre_du_bas():
    zone = (0, 0, 1920, 1040)  # barre de Plasma en bas (40 px)
    x, y = p.placement(1800, 1060, 412, 300, zone)
    assert y + 300 == 1040 - p.ECART_POINTEUR and x + 412 <= 1920 - p.ECART_POINTEUR
    x, y = p.placement(10, 20, 412, 300, (0, 40, 1920, 1040))  # barre en haut
    assert y == 40 + p.ECART_POINTEUR and x == p.ECART_POINTEUR


def test_mesures_de_la_carte():
    assert p.LARGEUR == 412 and p.CONTENU == 376 and p.JAUGE == 4
    assert p.ECART_JAUGES == 12 and p.ROTATION_MS == 2800 and p.COLONNE == 36 and p.LIGNE == jetons.LIGNE
    assert p.TETE_HEROS + 12 == p.COLONNE and p.COLONNE + p.TITRE_JAUGE + p.DUREE < p.CONTENU


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
    assert console.connexion(env, lancer) == 0
    assert lances[0][-1] == "claude" and "--title=Connexion Claude" in lances[0]  # l'assistant lance claude auth login
    assert console.connexion({**env, "QREXEC_SERVICE_ARGUMENT": "x"}, lancer) == 1
    assert console.connexion({**env, "QREXEC_REMOTE_DOMAIN": "vault"}, lancer) == 1
    assert len(lances) == 1
