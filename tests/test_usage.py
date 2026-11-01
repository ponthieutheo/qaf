"""Mesure d'usage côté dom0 (qaf_ui/usage.py) : schéma fermé, mise en forme, rythme, cache."""

import json
import os
import stat
import subprocess
import sys
import time

from qaf_ui import usage as u

T = 1_793_000_000


def faux_service(code):
    """Un vrai processus à la place de qvm-run : la lecture bornée se juge sur un vrai tube."""
    return lambda argv, **k: subprocess.Popen([sys.executable, "-c", code], **k)


def bon(**autres):
    d = {"v": 1, "etat": "ok", "offre": "max_5x",
         "fenetres": [{"nom": "5h", "modele": "", "pourcent": 34, "remise": T + 8000},
                      {"nom": "semaine", "modele": "", "pourcent": 61, "remise": T + 280000}],
         "totaux": {"jetons_jour": 812000, "jetons_semaine": 4200000, "invites_jour": 23,
                    "sessions_jour": 3, "modele": "opus", "jour_actif": 1, "partiel": False},
         "fournisseurs": {"codex": "deconnecte", "grok": "non_installe"}}
    d.update(autres)
    return d


def lire(d):
    return u.lire_mesure(json.dumps(d).encode(), T)


def test_mesure_valide():
    m = lire(bon())
    assert m.etat == "ok" and m.offre == "max_5x" and len(m.fenetres) == 2
    assert m.totaux.jetons_semaine == 4200000
    assert m.fournisseurs == (("codex", "deconnecte"), ("grok", "non_installe"))


def test_minimum_et_optionnels():
    assert lire({"v": 1, "etat": "sans_jeton"}).fenetres == ()
    m = lire({"v": 1, "etat": "erreur"})
    assert m.totaux is None and m.fournisseurs == ()


def fenetre(**k):
    f = {"nom": "5h", "modele": "", "pourcent": 34, "remise": T + 8000}
    f.update(k)
    return f


def totaux(**k):
    t = dict(bon()["totaux"])
    t.update(k)
    return t


def test_schema_ferme_rejets():
    rejets = [
        bon(inconnu=1), {"etat": "ok"}, bon(v=3), bon(v=0), bon(v=True), bon(etat="bizarre"), bon(offre="max_7x"),
        bon(fenetres=[fenetre(pourcent=101)]), bon(fenetres=[fenetre(pourcent=True)]),
        bon(fenetres=[fenetre(pourcent=3.5)]), bon(fenetres=[fenetre(remise=T + 9 * 86400)]),
        bon(fenetres=[fenetre(remise=T - 2 * 86400)]), bon(fenetres=[fenetre(nom="mois")]),
        bon(fenetres=[fenetre(modele="gpt")]), bon(fenetres=[fenetre(x=1)]),
        bon(fenetres=[fenetre()] * 5), bon(etat="refuse"),  # fenêtres sans état ok/limite
        bon(totaux=totaux(jetons_jour=5, jetons_semaine=4)), bon(totaux=totaux(jetons_semaine=10 ** 12 + 1)),
        bon(totaux=totaux(modele="gpt")), bon(totaux=totaux(jour_actif=7)), bon(totaux=totaux(partiel=0)),
        bon(totaux=totaux(invites_jour=False)), bon(totaux={"jetons_jour": 1}),
        bon(fournisseurs={"codex": "connecte"}), bon(fournisseurs={"codex": "oui", "grok": "connecte"}),
        bon(fournisseurs={"codex": "connecte", "grok": "connexion_seule"}),  # valeur de v2
        bon(actif="codex"), bon(sante="ok"), bon(connexion="ok"), bon(agents=[]),  # clés de v2 en v1
        [1, 2], "texte",
    ]
    for d in rejets:
        assert lire(d) is None, d


def bon2(**autres):
    d = bon(v=2, fournisseurs={"kimi": "non_installe", "grokbot": "connexion_seule", "openrouter": "connecte",
                               "local": "deconnecte"},
            actif="openrouter", sante="ok", connexion="", agents=["openrouter", "claude"])
    d.update(autres)
    return d


def test_table_des_35():
    assert len(u.IDS) == 35 and len(set(u.IDS)) == 35 and u.IDS[0] == "claude" and u.FOURNISSEURS == u.IDS[1:]
    assert set(u.NOMS) == set(u.IDS) - {"local"}  # « local » est traduit
    assert "ollama" in u.IDS and "local" in u.IDS and "agy" in u.IDS and u.NOMS["agy"] == "Google"
    assert u.JAMAIS_AGENT == ("grokbot", "pi", "ori", "mcode", "kiro-cli", "freebuff", "amp")
    assert u.nom_fournisseur("local") == "IA locale"


def test_v2_valide():
    m = lire(bon2())
    assert m.v == 2 and m.actif == "openrouter" and m.sante == "ok" and m.connexion == ""
    assert m.agents == ("claude", "openrouter")  # ordre de la table, pas celui du qube
    assert dict(m.fournisseurs)["grokbot"] == "connexion_seule"
    assert [c for c, _ in m.fournisseurs] == ["kimi", "grokbot", "openrouter", "local"]


def test_v2_minimum_et_defauts():
    m = lire({"v": 2, "etat": "ok"})
    assert m.actif == "claude" and m.sante == "inconnue" and m.connexion == "" and m.agents == ()
    # Absent = claude, y compris sur un fichier v1.
    assert lire(bon()).actif == "claude" and lire(bon()).v == 1


def test_v2_schema_ferme():
    rejets = [
        bon2(inconnu=1), bon2(actif="gemini"), bon2(actif="grokbot"), bon2(actif=1), bon2(actif=["claude"]),
        bon2(sante="malade"), bon2(sante=None), bon2(connexion="terminal"), bon2(connexion=1),
        bon2(agents=["inconnu"]), bon2(agents=["grokbot"]), bon2(agents="claude"),
        bon2(agents=["claude", "claude"]), bon2(agents=[1]), bon2(agents=[["claude"]]),
        bon2(fournisseurs={"claude": "connecte"}), bon2(fournisseurs={"gemini": "connecte"}),
        bon2(fournisseurs={"kimi": "peut-etre"}), bon2(fournisseurs={"kimi": 1}), bon2(fournisseurs=[]),
        bon2(port_demande=11434), bon2(lieu_local="voisin"),
    ]
    for d in rejets:
        assert lire(d) is None, d


def test_v2_aucune_chaine_libre():
    m = lire(bon2())
    chaines = [m.actif, m.sante, m.connexion, *m.agents] + [x for paire in m.fournisseurs for x in paire]
    permis = set(u.IDS) | set(u.ETATS_FOURNISSEUR) | set(u.SANTES) | set(u.CONNEXIONS)
    assert all(c in permis for c in chaines)


def test_v2_tient_dans_la_borne():
    tous = {k: "connexion_seule" for k in u.FOURNISSEURS}
    octets = json.dumps(bon2(fournisseurs=tous, agents=[k for k in u.IDS if k not in u.JAMAIS_AGENT]),
                        separators=(",", ":")).encode()
    assert len(octets) <= u.MAX_OCTETS and u.lire_mesure(octets, T) is not None


def test_sante_affichee():
    assert u.sante_affichee(None) is None
    assert u.sante_affichee(lire(bon2(sante="ok"))) is None and u.sante_affichee(lire(bon2(sante="inconnue"))) is None
    assert u.sante_affichee(lire(bon2(sante="credit_epuise"))) == "carte.sante_credit"


def test_bascule_argv_et_reponse():
    assert u.argv_bascule("local") == ["qvm-run", "--pass-io", "--no-gui", "--no-autostart", "--service",
                                       "sys-agent", "qaf.Fournisseur+local"]
    for mauvais in ("grokbot", "inconnu", "claude;id", ""):
        try:
            u.argv_bascule(mauvais)
        except ValueError:
            continue
        raise AssertionError(mauvais)

    assert u.basculer("zai", faux_service("print('ok')"))
    assert not u.basculer("zai", faux_service("print('non')"))
    assert not u.basculer("zai", faux_service("print('ok'); raise SystemExit(1)"))
    assert not u.basculer("zai", faux_service("print('ok ' * 500)"))

    def jamais(*a, **k):
        raise AssertionError("parti")

    assert not u.basculer("grokbot", jamais)  # ne part même pas

    def casse(*a, **k):
        raise OSError

    assert not u.basculer("zai", casse)


def test_fournisseur_actif_partout():
    m = lire(bon2(fenetres=[fenetre(pourcent=95)]))
    # Héros, infobulle, pastille : plus aucun Claude présumé.
    assert u.phrases_heros(m, True) == ["FOURNISSEUR : OPENROUTER"]
    assert u.infobulle_icone(True, m, T)[1].startswith("Prêt · OpenRouter")
    assert "Claude" not in u.infobulle_icone(True, m, T)[1]
    assert not u.alarme(m, T)
    panne = lire(bon2(sante="cle_refusee"))
    assert "clé refusée" in u.infobulle_icone(True, panne, T)[1]
    claude = lire(bon2(actif="claude", fenetres=[fenetre(pourcent=95)]))
    assert u.alarme(claude, T) and "Claude" in u.infobulle_icone(True, claude, T)[1]
    assert u.phrases_heros(lire(bon2(actif="local")), True) == ["FOURNISSEUR : IA LOCALE"]
    assert u.actif_de(None) is None


def test_octets_hostiles():
    assert u.lire_mesure(b"{" + b" " * 4096 + b"}", T) is None  # 4 098 octets
    assert u.lire_mesure(b"[" * 3000 + b"]" * 3000, T) is None
    assert u.lire_mesure(b"\xff\xfe", T) is None
    assert u.lire_mesure(b"", T) is None
    assert u.lire_mesure("{}", T) is None  # pas des octets


def test_aucun_texte_du_qube_dans_une_mesure():
    m = lire(bon())
    chaines = [m.etat, m.offre] + [f.nom for f in m.fenetres] + [f.modele for f in m.fenetres] \
        + [m.totaux.modele] + [x for paire in m.fournisseurs for x in paire]
    permis = set(u.ETATS) | set(u.OFFRES) | set(u.FENETRES) | set(u.MODELES_TOTAUX) \
        | set(u.FOURNISSEURS) | set(u.ETATS_FOURNISSEUR)
    assert all(c in permis for c in chaines)


def test_durees_et_jetons():
    assert u.duree(2 * 3600 + 14 * 60) == "2 h 14"
    assert u.duree(3 * 86400 + 7 * 3600) == "3 j 7 h"
    assert u.duree(41 * 60) == "41 min"
    assert u.duree(0) == "maintenant" and u.duree(-5) == "maintenant"
    assert u.duree(3600) == "1 h" and u.duree(86400) == "1 j"
    assert u.jetons(812000) == "812 K"
    assert u.jetons(4_200_000) == "4,2 M"
    assert u.jetons(950) == "950" and u.jetons(1000) == "1 K" and u.jetons(10_000_000) == "10 M"


def test_fenetre_liante_alarme_et_echue():
    m = lire(bon(fenetres=[fenetre(pourcent=93), fenetre(nom="semaine", pourcent=61, remise=T + 99999)]))
    assert u.fenetre_liante(m, T).nom == "5h"
    assert u.alarme(m, T)
    assert not u.alarme(lire(bon()), T)
    # remise passée : la fenêtre est à 0 (règle d'Omarchy)
    assert u.pourcent_affiche(m.fenetres[0], T + 9000) == 0
    assert u.fenetre_liante(m, T + 9000).nom == "semaine" and not u.alarme(m, T + 9000)
    assert u.alarme(lire(bon(fenetres=[fenetre(pourcent=90)])), T)


def test_traits_par_modele():
    m = lire(bon(fenetres=[fenetre(nom="semaine", remise=T + 9999),
                           fenetre(nom="semaine", modele="fable", pourcent=20, remise=T + 9999)]))
    assert [f.modele for f in u.principales(m)] == [""]
    assert [f.modele for f in u.traits(m, "semaine")] == ["fable"]
    assert "Fable" in u.infobulle_fenetre(u.traits(m, "semaine")[0], T)


def test_infobulles():
    m = lire(bon())
    b = u.infobulle_fenetre(m.fenetres[1], T)
    assert b.startswith("Semaine : 61 % utilisés · remise à zéro dans 3 j 5 h")
    assert "mesuré par sys-agent" in b
    assert "agent arrêté" in u.infobulle_fenetre(m.fenetres[1], T, age_s=3 * 3600, allume=False)
    titre, texte = u.infobulle_icone(True, m, T)
    assert titre == "Agent" and texte.startswith("Prêt · Claude : 61 % sur semaine")
    assert u.infobulle_icone(False, m, T)[0] == "Agent arrêté"


def test_phrases_heros():
    m = lire(bon())
    p = u.phrases_heros(m, True)
    assert p[0] == "4,2 M JETONS CETTE SEMAINE" and "812 K JETONS AUJOURD'HUI" in p
    assert "SURTOUT OPUS" in p and "JOUR LE PLUS CHARGÉ : MARDI" in p
    assert "23 INVITES AUJOURD'HUI" in p and "3 SESSIONS AUJOURD'HUI" in p
    assert u.phrases_heros(m, False) == ["AGENT ARRÊTÉ"]
    assert u.phrases_heros(None, True) == ["ABONNEMENT"]
    assert u.phrases_heros(lire(bon(totaux=totaux(partiel=True))), True)[0].startswith("≈ ")


def test_doit_mesurer():
    assert u.doit_mesurer(None, T)
    assert not u.doit_mesurer(T - 10, T, force=True)  # plancher de 15 s même forcé
    assert u.doit_mesurer(T - 16, T, force=True)
    assert u.doit_mesurer(T - 16, T, ouverte=True)
    assert not u.doit_mesurer(T - 600, T)
    assert u.doit_mesurer(T - 900, T)
    assert u.doit_mesurer(T - 200, T, liante=80)  # 3 min au-delà de 75 %
    assert not u.doit_mesurer(T - 200, T, liante=50)
    assert not u.doit_mesurer(T - 200, T, etat="limite")
    assert u.doit_mesurer(T - 300, T, etat="limite")


def test_argv_fixes():
    assert u.ARGV_USAGE == ["qvm-run", "--pass-io", "--no-gui", "--no-autostart", "--service",
                            "sys-agent", "qaf.Usage"]
    assert u.ARGV_DEMARRER == ["qvm-start", "sys-agent"]


def test_appeler_borne_la_sortie():
    vus = []
    ouvrir = faux_service("import sys; sys.stdout.write('x' * 10000)")
    code, octets = u.appeler(lambda argv, **k: vus.append(argv) or ouvrir(argv, **k))
    assert len(octets) == u.MAX_OCTETS + 1 and vus == [u.ARGV_USAGE]

    def casse(*a, **k):
        raise OSError

    assert u.appeler(casse) == (None, b"")


def test_qube_qui_ecrit_sans_fin_est_coupe():
    """Rien n'est gardé au-delà de la limite, et le processus ne tourne plus."""
    procs = []
    ouvrir = faux_service("import sys\nwhile True: sys.stdout.write('x' * 65536)")
    debut = time.monotonic()
    code, octets = u.lire_borne(["x"], 16, 20, lambda a, **k: procs.append(ouvrir(a, **k)) or procs[-1])
    assert len(octets) == 17 and code not in (None, 0) and procs[0].poll() is not None
    assert time.monotonic() - debut < 10


def test_qube_muet_coupe_au_delai():
    assert u.lire_borne(["x"], 16, 0.3, faux_service("import time; time.sleep(30)")) == (None, b"")


def test_cache(tmp_path):
    chemin = str(tmp_path / "qaf" / "usage.json")
    m = lire(bon())
    assert u.cache_ecrire(m, T, chemin)
    assert stat.S_IMODE(os.stat(chemin).st_mode) == 0o600
    lu, heure = u.cache_lire(chemin)
    assert lu == m and heure == T
    with open(chemin, "w") as f:
        f.write('{"heure": 1, "mesure": {"v": 1, "etat": "ok", "pirate": 1}}')
    assert u.cache_lire(chemin) == (None, None)
    assert u.cache_lire(str(tmp_path / "absent")) == (None, None)
    # Un cache écrit avant la mise à jour (v=1 sans v2) reste lisible.
    with open(chemin, "w") as f:
        json.dump({"heure": T, "mesure": bon()}, f)
    ancien, _ = u.cache_lire(chemin)
    assert ancien is not None and ancien.v == 1 and ancien.actif == "claude"
    # Et une mesure v2 fait l'aller-retour entier.
    m2 = lire(bon2(connexion="echec", sante="injoignable"))
    assert u.cache_ecrire(m2, T, chemin) and u.cache_lire(chemin)[0] == m2
    assert u.chemin_cache({"XDG_STATE_HOME": "/x"}) == "/x/qaf/usage.json"
    assert u.chemin_cache({"HOME": "/h"}) == "/h/.local/state/qaf/usage.json"
