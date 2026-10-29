"""Mesure d'usage côté dom0 (qaf_ui/usage.py) : schéma fermé, mise en forme, rythme, cache."""

import json
import os
import stat

from qaf_ui import usage as u

T = 1_793_000_000


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
        bon(inconnu=1), {"etat": "ok"}, bon(v=2), bon(v=True), bon(etat="bizarre"), bon(offre="max_7x"),
        bon(fenetres=[fenetre(pourcent=101)]), bon(fenetres=[fenetre(pourcent=True)]),
        bon(fenetres=[fenetre(pourcent=3.5)]), bon(fenetres=[fenetre(remise=T + 9 * 86400)]),
        bon(fenetres=[fenetre(remise=T - 2 * 86400)]), bon(fenetres=[fenetre(nom="mois")]),
        bon(fenetres=[fenetre(modele="gpt")]), bon(fenetres=[fenetre(x=1)]),
        bon(fenetres=[fenetre()] * 5), bon(etat="refuse"),  # fenêtres sans état ok/limite
        bon(totaux=totaux(jetons_jour=5, jetons_semaine=4)), bon(totaux=totaux(jetons_semaine=10 ** 12 + 1)),
        bon(totaux=totaux(modele="gpt")), bon(totaux=totaux(jour_actif=7)), bon(totaux=totaux(partiel=0)),
        bon(totaux=totaux(invites_jour=False)), bon(totaux={"jetons_jour": 1}),
        bon(fournisseurs={"codex": "connecte"}), bon(fournisseurs={"codex": "oui", "grok": "connecte"}),
        [1, 2], "texte",
    ]
    for d in rejets:
        assert lire(d) is None, d


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
    class R:
        returncode, stdout = 0, b"x" * 10000

    vus = []
    code, octets = u.appeler(lambda argv, **k: vus.append((argv, k)) or R())
    assert code == 0 and len(octets) == u.MAX_OCTETS + 1
    assert vus[0][0] == u.ARGV_USAGE and vus[0][1]["timeout"] == u.DELAI_S

    def casse(*a, **k):
        raise OSError

    assert u.appeler(casse) == (None, b"")


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
    assert u.chemin_cache({"XDG_STATE_HOME": "/x"}) == "/x/qaf/usage.json"
    assert u.chemin_cache({"HOME": "/h"}) == "/h/.local/state/qaf/usage.json"
