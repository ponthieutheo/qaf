"""Panneau de l'agent, logique sans GTK : lecture du journal en lecture seule,
état, phrases du fil (sans codes N/R), gestes par qaf-pause."""

import json
import os
import re
import subprocess
import time
import types

import pytest

import agent_demo as demo
from qaf_gate import executor, pause, schema
from qaf_gate.journal import Journal, verify
from qaf_ui.ecrans import agent_etat as ae


def inst(evenements, **kw):
    chemin = demo.ecrire(evenements)
    entrees, erreur, integre = ae.lire_journal(chemin)
    base = dict(entrees=entrees, erreur=erreur, integre=integre, portier=True,
                debut=time.time() - 3600, maintenant=time.time())
    base.update(kw)
    return ae.Instantane(**base)


def textes(i):
    return [l.texte for l in ae.fil(i)]


# --- constantes alignées sur le Doorman ----------------------------------------

def test_constantes_alignees_sur_le_portier():
    assert ae.JOURNAL == executor.JOURNAL
    assert ae.INFLUENCANTES == executor.INFLUENCANTES
    assert ae.QUBE_AGENT == "sys-agent"


@pytest.mark.parametrize("langue", ["fr", "en", "zh"])
def test_chaque_operation_du_schema_a_sa_phrase(langue):
    from qaf_ui import i18n
    i18n.choisir(langue)
    assert set(schema.OPS) <= set(ae.PHRASES)
    if langue == "fr":
        for passe, inf in ae.PHRASES.values():
            assert i18n.t(passe).startswith("a ") and not i18n.t(inf).startswith("a ")
    for op in ae.PHRASES:
        for forme in (0, 1):
            for cible in ("monqube", None):
                x = ae._phrase(op, cible, forme)
                assert not re.search(r"[{}|@\[\]]|de d'|l'espace un|of of|the qube a qube", x), x
                assert ("monqube" in x) == (cible is not None and "[" in i18n.t(ae.PHRASES[op][forme])), x


# --- lecture du journal ---------------------------------------------------------

def test_lecture_ne_modifie_rien(tmp_path):
    chemin = demo.ecrire(demo.scenario_actif(), str(tmp_path))
    avant = (open(chemin, "rb").read(), os.stat(chemin).st_mtime_ns)
    entrees, erreur, integre = ae.lire_journal(chemin)
    assert erreur is None and integre and len(entrees) == verify(chemin)
    assert (open(chemin, "rb").read(), os.stat(chemin).st_mtime_ns) == avant


def test_journal_absent_droit_manquant(tmp_path):
    assert ae.lire_journal(str(tmp_path / "rien.jsonl")) == ([], None, True)
    chemin = demo.ecrire(demo.scenario_actif(), str(tmp_path))
    os.chmod(chemin, 0)
    try:
        if os.access(chemin, os.R_OK):
            pytest.skip("root lit tout")
        entrees, erreur, _ = ae.lire_journal(chemin)
        assert entrees == [] and erreur == "droit de lecture manquant"
    finally:
        os.chmod(chemin, 0o600)
    i = ae.Instantane(erreur="droit de lecture manquant", portier=True)
    e = ae.etat(i)
    assert e.influence is None and "inconnue" in e.influence_texte and ae.fil(i) == []


def test_chaine_alteree_detectee(tmp_path):
    chemin = demo.ecrire(demo.scenario_actif(), str(tmp_path))
    lignes = open(chemin, encoding="utf-8").read().splitlines()
    e = json.loads(lignes[2])
    e["event"]["op"] = "vm.remove"
    lignes[2] = json.dumps(e)
    open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
    _, _, integre = ae.lire_journal(chemin)
    assert integre is False


def test_lecture_bornee_par_la_fin(tmp_path):
    chemin = os.path.join(tmp_path, "journal.jsonl")
    j = Journal(chemin)
    for n in range(400):
        j.append({"type": "demande", "request": f"R-{1000 + n}", "qubes": [], "sha256_texte": "0" * 64})
    entrees, erreur, integre = ae.lire_journal(chemin, max_octets=4096)
    assert erreur is None and integre and 0 < len(entrees) < 400
    assert entrees[-1]["seq"] == 399  # la fin, sans la ligne coupée


# --- phrases du fil ----------------------------------------------------------------

def test_phrases_du_fil_sans_codes():
    t = textes(inst(demo.scenario_actif()))
    assert "a créé l'espace dev" in t
    assert "refusé : changer l'accès Internet de vault" in t
    assert "a installé des logiciels dans dev" in t
    assert "refusé : changer un réglage général" in t  # refus du validateur : motif caché
    for x in t:
        assert not re.search(r"\bN[0-4]\b|R-\d{4}|<b>|vm\.|\bop\b", x), x


def test_sans_cibles_la_phrase_reste_generique():
    ev = [(0, {k: v for k, v in demo._plan("1" * 64, ["vm.create"], "auto").items()}),
          (1, demo._op("1" * 64, 0, "vm.create"))]
    assert textes(inst(ev)) == ["a créé un espace"]


def test_modele_retire_nomme():
    ev = [(0, demo._plan("1" * 64, ["template.remove"], "auto", ["tpl-essai"])),
          (1, demo._op("1" * 64, 0, "template.remove"))]
    assert textes(inst(ev)) == ["a retiré le modèle tpl-essai"]
    ev = [(0, demo._plan("1" * 64, ["template.remove"], "auto")), (1, demo._op("1" * 64, 0, "template.remove"))]
    assert textes(inst(ev)) == ["a retiré un modèle"]


def test_cible_hostile_jamais_reprise():
    ev = [(0, demo._plan("1" * 64, ["vm.start"], "auto", ['<span foreground="red">x</span>'])),
          (1, demo._op("1" * 64, 0, "vm.start"))]
    assert textes(inst(ev)) == ["a démarré un espace"]


def test_confirmations_et_interruptions():
    ev = demo.scenario_attente() + [
        (510, {"type": "confirmation", "appelant": "sys-agent", "request": "R-1042", "sha256": "5" * 64,
               "accordee": False, "expiree": True})]
    t = textes(inst(ev))
    assert t[0] == "sans réponse, donc refusé : changer l'accès Internet de loisirs"
    t = textes(inst(demo.scenario_pause()))
    assert t[0] == "arrêté en cours de route (mise en pause)"


# --- état -----------------------------------------------------------------------------

def test_etat_actif_propre():
    e = ae.etat(inst(demo.scenario_actif()))
    assert (e.code, e.influence, e.attente) == (ae.ACTIF, False, None)


def test_etat_attente_et_influence():
    e = ae.etat(inst(demo.scenario_attente()))
    assert e.code == ae.ATTENTE and e.influence is True
    assert e.attente == "changer l'accès Internet de loisirs"
    assert "tout ce qui touche tes espaces t'est demandé" in e.influence_texte
    assert e.explication == "Il veut changer l'accès Internet de loisirs : réponds dans Cœur du système."


def test_attente_expiree_ou_repondue():
    i = inst(demo.scenario_attente())
    i.maintenant += 3600
    assert ae.etat(i).code == ae.INFLUENCE
    ev = demo.scenario_attente() + [(510, {"type": "confirmation", "appelant": "sys-agent",
                                           "request": "R-1042", "sha256": "5" * 64,
                                           "accordee": True, "expiree": False})]
    assert ae.etat(inst(ev)).code == ae.INFLUENCE


def test_influence_retombe_au_redemarrage_du_portier():
    i = inst(demo.scenario_attente(), debut=time.time() + 10)
    assert ae.etat(i).influence is False
    i = inst(demo.scenario_attente(), debut=None, portier=True)  # socket seul : exécuteur pas lancé
    assert ae.etat(i).influence is False
    i = inst(demo.scenario_attente(), debut=None, portier=None)  # inconnu : prudence
    assert ae.etat(i).influence is True


def test_pause_prime_et_portier_arrete(tmp_path):
    chemins = (str(tmp_path / "pause"), str(tmp_path / "pause-auto"))
    open(chemins[0], "w").close()
    i = ae.instantane(demo.ecrire(demo.scenario_attente()), chemins, portier=lambda: (True, None))
    e = ae.etat(i)
    assert e.code == ae.PAUSE and "Tu as mis" in e.explication and e.attente is None
    os.unlink(chemins[0])
    open(chemins[1], "w").close()
    assert "trop de demandes refusées" in ae.etat(ae.instantane(str(tmp_path / "x"), chemins,
                                                                portier=lambda: (True, None))).explication
    assert ae.etat(ae.Instantane(portier=False)).code == ae.ARRETE


def test_lire_portier_systemctl():
    sortie = ("ActiveState=active\nExecMainStartTimestampMonotonic=5000000\n\n"
              "ActiveState=active\nExecMainStartTimestampMonotonic=0\n")
    lancer = lambda argv, **kw: types.SimpleNamespace(returncode=0, stdout=sortie)
    actif, debut = ae.lire_portier(lancer, horloge=lambda: 1000.0, monotone=lambda: 15.0)
    assert actif is True and debut == pytest.approx(990.0)
    sortie = "ActiveState=inactive\nExecMainStartTimestampMonotonic=0\n\nActiveState=active\n"
    assert ae.lire_portier(lancer) == (True, None)
    sortie = "ActiveState=inactive\n\nActiveState=inactive\n"
    assert ae.lire_portier(lancer) == (False, None)

    def casse(*a, **k):
        raise OSError("pas de systemctl")
    assert ae.lire_portier(casse) == (None, None)


# --- gestes ----------------------------------------------------------------------------

def test_pause_par_qaf_pause_argv_fixe():
    vus = []

    def lancer(argv, **kw):
        vus.append((argv, kw))
        return types.SimpleNamespace(returncode=0)
    assert ae.basculer_pause(True, lancer).ok and ae.basculer_pause(False, lancer).ok
    assert [v[0] for v in vus] == [["sudo", "-n", "/usr/bin/qaf-pause", "on", ae.PAUSE_RAISON],
                                   ["sudo", "-n", "/usr/bin/qaf-pause", "off"]]
    assert all("shell" not in kw and kw["stdin"] is subprocess.DEVNULL for _a, kw in vus)
    refus = ae.basculer_pause(True, lambda a, **k: types.SimpleNamespace(returncode=1))
    assert not refus.ok and "sudo qaf-pause" in refus.message


def test_demander_ouvre_la_palette_argv_fixe():
    # « Demander à l'agent… » du panneau : la palette (clavier interne, P2), plus le
    # lanceur retiré le 29/10 ; rien d'autre n'est lancé, jamais de shell.
    from qaf_ui import donnees
    vus = []
    r = donnees.ouvrir_palette(lambda argv, **kw: vus.append((argv, kw)))
    assert r.ok and vus == [(["qaf-palette"], {"stdin": subprocess.DEVNULL, "start_new_session": True})]

    def absent(argv, **kw):
        raise FileNotFoundError(argv[0])
    assert not donnees.ouvrir_palette(absent).ok


def test_pause_reelle_reconnue(tmp_path):
    chemins = (str(tmp_path / "pause"), str(tmp_path / "auto"))
    pause.main(["on"], chemins)
    assert ae.instantane(str(tmp_path / "j"), chemins, portier=lambda: (True, None)).pause
    pause.main(["off"], chemins)
    assert ae.instantane(str(tmp_path / "j"), chemins, portier=lambda: (True, None)).pause is None


def test_module_sans_gtk_ni_pango():
    import pathlib
    racine = pathlib.Path(__file__).parent.parent / "qaf_ui" / "ecrans"
    assert "import gi" not in (racine / "agent_etat.py").read_text(encoding="utf-8")
    for f in ("agent.py", "agent_etat.py"):
        src = (racine / f).read_text(encoding="utf-8")
        assert "set_markup" not in src and "use_markup=True" not in src and "shell=True" not in src


# --- agents jetables (registre du Doorman) et heure -------------------------------

def test_heure_toujours_sur_deux_chiffres():
    # « 21:8 » vu dans le fil : les minutes et les heures gardent leur zéro.
    ts = int(time.mktime((2026, 10, 25, 21, 8, 0, 0, 0, -1)))
    assert ae.heure(ts) == "21:08" and ae.LigneFil(ts, "x", ae.INFO).heure == "21:08"
    ts = int(time.mktime((2026, 10, 25, 7, 5, 0, 0, 0, -1)))
    assert ae.heure(ts) == "07:05" and ae.heure(0) == "--:--"


def test_influence_suit_les_agents_ouverts():
    # Le journal montre qu'un agent a lu une capture ; le registre dit s'il est encore ouvert.
    ev = demo.scenario_attente()[:-1]
    assert ae.influence(inst(ev)) is True  # registre inconnu : prudence, tout le journal compte
    assert ae.influence(inst(ev, agents=[])) is False  # fermé : plus d'influence
    e = ae.etat(inst(ev, agents=[]))
    # Rien à dire quand tout est propre : l'influence ne se dit qu'une fois, et seulement si elle est là.
    assert (e.code, e.titre) == (ae.ACTIF, "Prêt") and e.influence_texte == ""
    e = ae.etat(inst(ev, agents=demo.INFLUENCE))
    assert e.influence is True and e.influence_texte.startswith("disp6000 a lu du contenu")
    assert "fermeture de cet agent" in e.influence_texte
    e = ae.etat(inst(ev, agents=demo.PROPRE))
    assert e.influence is False and e.influence_texte == ""
    # Journal illisible mais registre lisible : l'influence reste connue.
    assert ae.influence(ae.Instantane(erreur="droit de lecture manquant", portier=True,
                                      agents=demo.INFLUENCE)) is True


def test_agent_long_compte_toujours_par_le_journal():
    ev = [(0, demo._plan("4" * 64, ["qube.screenshot"], "auto", ["untrusted"], appelant="sys-agent")),
          (1, demo._op("4" * 64, 0, "qube.screenshot", appelant="sys-agent"))]
    assert ae.influence(inst(ev, agents=[])) is True


def test_fil_nomme_l_agent_et_ses_ouvertures():
    ev = demo.scenario_actif() + [(400, {"type": "agent_tue", "agent": "disp6000", "raison": "inactif"}),
                                  (401, {"type": "agent_tue", "agent": "<b>x</b>", "raison": "inactif"})]
    lignes = ae.fil(inst(ev))
    assert (lignes[0].texte, lignes[0].agent) == ("agent fermé par le cœur du système (resté inactif)", "disp6000")
    assert (lignes[-2].texte, lignes[-2].agent) == ("agent neuf ouvert dans un espace jetable", "disp6000")
    assert (lignes[-1].texte, lignes[-1].agent) == ("a reçu une demande de ta part", "disp6000")
    assert all(l.agent == "disp6000" for l in lignes if l.texte.startswith("a créé"))
    assert not any("<b>" in (l.agent or "") + l.texte for l in lignes)


def test_ligne_agent_ouvert():
    from qaf_ui import palette
    a = palette.AgentOuvert("disp6000", 14 * 60 + 30, True, "va  dans\nessai-dev")
    assert ae.ligne_agent(a) == "depuis 14 min · « va dans essai-dev »"


def test_instantane_lit_les_agents_par_la_palette(tmp_path):
    chemin = demo.ecrire(demo.scenario_actif(), str(tmp_path))
    vus = []
    i = ae.instantane(chemin, (str(tmp_path / "p"), str(tmp_path / "q")), portier=lambda: (True, None),
                      agents=lambda: vus.append(1) or [])
    assert i.agents == [] and vus == [1]
    i = ae.instantane(chemin, (str(tmp_path / "p"), str(tmp_path / "q")), portier=lambda: (False, None),
                      agents=lambda: vus.append(2) or [])
    assert i.agents is None and vus == [1]  # portier arrêté : rien demandé


def test_mode_dev_lu_par_lstat_seul(tmp_path):
    politique = tmp_path / "20-qaf-dev-dom0.policy"
    assert ae.mode_dev(str(politique)) is False
    politique.write_text("qaf.Shell * qube-admin dom0 allow")
    politique.chmod(0)  # illisible : lstat suffit, le contenu n'est jamais lu
    assert ae.mode_dev(str(politique)) is True
    assert ae.POLITIQUE_DEV == "/etc/qubes/policy.d/20-qaf-dev-dom0.policy"


def test_mode_dev_dans_l_instantane(tmp_path):
    chemin = demo.ecrire(demo.scenario_actif(), str(tmp_path))
    pauses = (str(tmp_path / "p"), str(tmp_path / "q"))
    politique = tmp_path / "dev.policy"
    i = ae.instantane(chemin, pauses, portier=lambda: (True, None), agents=lambda: [], dev=str(politique))
    assert i.mode_dev is False
    politique.touch()
    i = ae.instantane(chemin, pauses, portier=lambda: (True, None), agents=lambda: [], dev=str(politique))
    assert i.mode_dev is True
