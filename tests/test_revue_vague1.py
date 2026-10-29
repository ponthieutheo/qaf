"""Revue automatique de la vague 1 (25/10) et remarques de l'essai réel
(docs/RESULTATS-V1.md) : non-régression de chaque correction.

- vm.reset_prop classé avec la valeur EFFECTIVE qu'il produira ;
- R-E sur la valeur effective (explicite ou héritée), plus de refus par principe ;
- fenêtre : commande de qube.exec et texte de qube.type affichés en entier,
  échappés, jamais tronqués ;
- INFLUENCANTES : toute opération qui renvoie un contenu écrit par un qube ;
- J1 (refus N4 du validateur tracé), J3 (registre purgé), U1 (phrases),
  réponse à l'agent bornée sous 1 Mio.
"""

import dataclasses
import json

import pytest

from qaf_gate import classify as C
from qaf_gate import confirm, executor, proto, schema
from qaf_gate.journal import Journal
from qaf_gate.registry import Registry
from qaf_gate.validator import Config as ConfigValidateur
from qaf_gate.validator import Validateur
from qaf_ui import confirmation as ui
from backend_sim import SimBackend, _q, qubes_par_defaut
from exemples_ops import EXEMPLES, op
from test_ops_v3_classement import AGENT, classer, contexte, installation, niveau


def avec_global(valeur, sim=None):
    sim = sim or installation()
    sim.globaux["default_dispvm"] = valeur
    return contexte(sim)


# --- a. vm.reset_prop : valeur effective ------------------------------------

def test_reset_du_jetable_d_un_coffre_vers_un_jetable_avec_reseau():
    # Exemple de la revue : vault, global default-dvm (réseau) -> N4.
    assert niveau(op("vm.reset_prop", name="vault", prop="default_dispvm"), ctx=avec_global("default-dvm")) == C.N4
    # Même si vault a aujourd'hui une valeur explicite vide (U46 appliqué sur une installation).
    sim = installation()
    sim.qubes["vault"] = dataclasses.replace(sim.qubes["vault"], default_dispvm=None, default_dispvm_herite=False)
    assert niveau(op("vm.reset_prop", name="vault", prop="default_dispvm"), ctx=avec_global("default-dvm", sim)) == C.N4
    # Global vide : le reset retire le jetable du coffre, toujours confirmé.
    cl = classer(op("vm.reset_prop", name="vault", prop="default_dispvm"), ctx=avec_global(None), mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER


CIBLES = ["work", "vault", "a-dev", "sys-firewall", "debian-13-xfce", "disp9", "sys-agent", "personal"]
GLOBAUX = ["default-dvm", "a-dvm", None, "qaf-dvm", "vault", "agent-dvm"]


@pytest.mark.parametrize("cible", CIBLES)
@pytest.mark.parametrize("glob", GLOBAUX)
def test_reset_memes_controles_que_set_prop(cible, glob):
    """Reset = set_prop de la valeur que qubesd prendra : niveau, décision
    (Normal et Pilote, influencée ou non) et portée identiques."""
    ctx = avec_global(glob)
    effectif = ctx.defaut(cible, "default_dispvm")
    assert effectif == (ctx.info(cible, "template") if cible == "disp9" else glob)
    reset = op("vm.reset_prop", name=cible, prop="default_dispvm")
    pose = op("vm.set_prop", name=cible, prop="default_dispvm", value=effectif)
    for kw in ({}, {"mode": C.PILOTE}, {"influence": True}, {"portee": {cible}}):
        a, b = classer(reset, ctx=ctx, **kw), classer(pose, ctx=ctx, **kw)
        assert (a.niveaux, a.decision, a.hors_portee) == (b.niveaux, b.decision, b.hors_portee), kw


def test_reset_vers_un_jetable_de_l_agent_renforce():
    # R-D : remettre par défaut quand le global est un qube de l'agent.
    cl = classer(op("vm.reset_prop", name="work", prop="default_dispvm"), ctx=avec_global("a-dvm"), mode=C.PILOTE)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER_RENFORCE


def test_reset_booleen_reste_prudent():
    assert niveau(op("vm.reset_prop", name="sys-firewall", prop="provides_network")) == C.N4
    assert niveau(op("vm.reset_prop", prop="include_in_backups")) == C.N3


def test_un_jetable_n_herite_pas_du_global():
    # Le défaut d'un jetable est son modèle : il ne compte pas dans R-E.
    sim = installation()
    for n in sim.qubes:
        sim.qubes[n] = dataclasses.replace(sim.qubes[n], default_dispvm_herite=(n == "disp9"))
    assert contexte(sim).heritiers(AGENT) == set()
    sim.qubes["work"] = dataclasses.replace(sim.qubes["work"], default_dispvm_herite=True)
    assert contexte(sim).heritiers(AGENT) == {C.T}


# --- R-E : valeur effective, plus de refus par principe -------------------------

def _p_herite():
    sim = installation()
    sim.qubes["dom0"] = dataclasses.replace(sim.qubes["dom0"], default_dispvm_herite=True)
    sim.qubes["t-dvm"] = _q("t-dvm", "AppVM", "red", "debian-13-xfce", None, template_for_dispvms=True)
    return sim


@pytest.mark.parametrize("valeur,attendu", [(None, C.N3), ("t-dvm", C.N3), ("default-dvm", C.N4), ("a-dvm", C.N4)])
def test_r_e_valeur_effective(valeur, attendu):
    # dom0 (P) hérite : un jetable sans réseau et hors de l'agent est permis, toujours confirmé.
    cl = classer(op("prefs.set", prop="default_dispvm", value=valeur), ctx=contexte(_p_herite()), mode=C.PILOTE)
    assert cl.niveaux == (attendu,)
    assert cl.decision == (C.REFUSER if attendu == C.N4 else C.CONFIRMER)


# --- b. fenêtre : texte de l'agent en entier, échappé ------------------------------

def vue(*ops, qubes=(("work", "blue", "utilisateur"),)):
    canon = json.dumps({"version": 1, "request": "R-0001", "ops": list(ops)}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False)
    msg = {"type": "confirmer", "appelant": "sys-agent", "canonique": canon, "niveaux": [3] * len(ops),
           "decision": "confirmer", "jeton": "123456", "influence": False, "qubes": [list(q) for q in qubes],
           "expire_dans": 60, "texte_demande": None, "age_demande": None, "hors_portee": []}
    return ui.rediger(ui.lire_requete(msg))


@pytest.mark.parametrize("brut,visible", [
    ("\x1b[31m", "\\u{001B}[31m"), ("a\nb", "a\\nb"), ("\t", "\\t"), ("‮evil‬", "\\u{202E}evil\\u{202C}"),
    ("⁦x⁩", "\\u{2066}x\\u{2069}"), ("​", "\\u{200B}"), (" ", "\\u{00A0}"),
    ("é", "e\\u{0301}"), ("а", "\\u{0430}"), ("\\", "\\\\"), ("éàçÉœ", "éàçÉ\\u{0153}"), ("\x7f", "\\u{007F}"),
    ("\U0001F600", "\\u{1F600}"),
])
def test_echapper(brut, visible):
    assert ui.echapper(brut) == visible


def test_commande_affichee_en_entier():
    argv = ["bash", "-c", 'echo "a b" ; ' + "x" * 3000 + " ‮"]
    a = vue(op("qube.exec", argv=argv)).actions[0]
    assert a.contenu == '"bash" "-c" "echo \\"a b\\" ; ' + "x" * 3000 + ' \\u{202E}"'
    assert "‮" not in a.contenu and "…" not in a.contenu
    assert "3 arguments" in a.phrase and a.contenu not in a.phrase


def test_texte_tape_affiche():
    a = vue(op("qube.type", text="rm -rf ~\n")).actions[0]
    assert a.contenu == "rm -rf ~\\n" and "taper 9 caractères" in a.phrase
    assert vue(op("qube.key")).actions[0].contenu == ""


def test_le_pire_argv_admis_par_le_schema_s_affiche_en_entier():
    argv = ["\U0010FFFF" * 4096, "\U0010FFFF" * 4096]
    a = vue(op("qube.exec", argv=argv)).actions[0]
    assert a.contenu.count("\\u{10FFFF}") == 8192


def test_trop_long_pour_etre_affiche_refuse(monkeypatch):
    monkeypatch.setattr(ui, "MAX_CONTENU", 100)
    with pytest.raises(ui.DemandeInvalide):
        vue(op("qube.exec", argv=["x" * 200]))
    with pytest.raises(ui.DemandeInvalide):
        vue(op("qube.type", text="‮" * 20))


# --- U1 : phrases de la fenêtre ---------------------------------------------------

def test_phrases_modele_et_qube_devenu_systeme():
    assert "ton modèle «\xa0debian-13-xfce\xa0»" in vue(op("pkg.install"),
                                                       qubes=(("debian-13-xfce", "black", "utilisateur-modele"),)).titre
    t = vue(op("vm.set_prop", name="a-net", prop="provides_network", value=False),
            qubes=(("a-net", "gray", "agent-systeme"),)).titre
    assert "qu'il a créé, devenu un qube système" in t
    e = ui.Espace("a-net", "gray", "agent-systeme")
    assert e.par_agent and e.confiance == ui.RISQUE


def test_roles_donnes_par_l_executeur():
    sim, ctx = installation(), contexte()
    p = schema.parse_plan(json.dumps({"version": 1, "request": "R-0001", "ops": [
        op("vm.set_prop", name="a-net", prop="provides_network", value=False), op("pkg.install"),
        op("vm.set_service", name="sys-firewall"), op("vm.set_service", name="tpl-a"), op("vm.start")]}).encode())
    roles = {n: r for n, _, r in confirm.qubes_du_plan(p, sim.list_qubes(), AGENT, {"vault"}, ctx)}
    assert roles == {"a-net": "agent-systeme", "debian-13-xfce": "utilisateur-modele", "sys-firewall": "utilisateur-systeme",
                     "tpl-a": "agent-modele", "work": "utilisateur"}
    assert set(roles.values()) <= set(ui.ROLES)


# --- c. INFLUENCANTES ---------------------------------------------------------------

MARQUE = "MARQUE-ECRITE-PAR-LE-QUBE"


def faire(tmp_path, **kw):
    qs = qubes_par_defaut() + [_q("tpl-qaf-hand", "TemplateVM", "black"),
                               _q("qaf-dvm", "AppVM", "red", "tpl-qaf-hand", None, template_for_dispvms=True)]
    ex = executor.Executeur(SimBackend(qs), Registry(f"{tmp_path}/registre.json"),
                            Journal(f"{tmp_path}/journal.jsonl"), confirm.Scripte((True,) * 16),
                            pauses=(f"{tmp_path}/pause",), pause_auto=f"{tmp_path}/pause", **kw)
    ex.etat = {"influence": False, "mode": C.NORMAL}
    return ex


def lancer(ex, *ops):
    texte = json.dumps({"version": 1, "request": "R-0001", "ops": list(ops)}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False)
    return ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": texte})


def journal(tmp_path):
    with open(f"{tmp_path}/journal.jsonl", encoding="utf-8") as f:
        return [json.loads(ligne)["event"] for ligne in f]


@pytest.mark.parametrize("nom", sorted(EXEMPLES))
def test_tout_contenu_de_qube_rend_la_session_influencee(tmp_path, nom):
    ex = faire(tmp_path)
    b = ex.backend
    for s in ("qaf.Exec", "qaf.Screen", "qaf.Input"):
        b.reponses_main[s] = {"ok": True, "stdout": MARQUE, "titres": [MARQUE]}
    b.features = {n: {"updates-available": MARQUE, "last-update": MARQUE, "last-updates-check": MARQUE,
                      "skip-update": MARQUE, "prohibit-start": MARQUE, "os-distribution": "debian"} for n in b.qubes}
    b.disponibles = [{"nom": MARQUE, "version": "0:1", "depot": "qubes-templates-itl"}]
    b.profils = {"hebdo": {"destination_vm": "personal", "passphrase_vm": None}}
    b.peripheriques = [{"class": "usb", "backend": "sys-usb", "port": "2-1", "device_id": MARQUE,
                        "attached_to": None}]
    r = lancer(ex, op(nom))
    sortie = json.dumps(r)
    if r.get("statut") == proto.FAIT and nom in executor.INFLUENCANTES:
        assert ex.etat["influence"] is True
    if '"b64"' in sortie or MARQUE in sortie:
        assert nom in executor.INFLUENCANTES, (nom, sortie[:300])
        assert ex.etat["influence"] is True
    if nom not in executor.INFLUENCANTES:
        assert ex.etat["influence"] is False and MARQUE not in sortie and '"b64"' not in sortie  # clé, pas un UUID qui contiendrait b64


def test_influencantes_toutes_couvertes(tmp_path):
    # Chaque opération qui renvoie un blob ou des entrées annoncées par un qube.
    assert executor.INFLUENCANTES == {"dev.list", "qube.exec", "qube.windows", "qube.screenshot",
                                      "template.available"}  # lignes venues de l'updatevm (C6)
    assert {o for o, f in executor.MAINS.items() if f(dict(op(o)))[0] != "qaf.Input"} <= executor.INFLUENCANTES


@pytest.mark.parametrize("code", ["0", None, 1.5, [0]])
def test_qaf_input_code_entier_seulement(tmp_path, code, monkeypatch):
    ex = faire(tmp_path)
    monkeypatch.setattr(ex.backend, "hand", lambda *a: {"code": code, "octets": 3, "b64": "eHh4"})
    r = lancer(ex, op("qube.key"))
    assert r["resultats"][0]["resultat"] == {"code": None} and ex.etat["influence"] is False


# --- J1 : refus N4 du validateur tracé -----------------------------------------------

def test_refus_n4_du_validateur_journalise(tmp_path):
    ex = faire(tmp_path)
    v = Validateur(ex.traiter, ConfigValidateur(pauses=(f"{tmp_path}/pause",)))
    o = op("qube.exec", name="vault")
    donnees = json.dumps({"version": 1, "request": "R-0042", "ops": [o]}).encode()
    r = v.traiter("sys-agent", "ok", donnees)
    assert r["statut"] == proto.REFUSE and r["request"] == "R-0042"
    ev = [e["event"] for e in journal(tmp_path) if e.get("source") == "validateur"][-1]
    attendu = __import__("hashlib").sha256(schema.canonicalize(schema.parse_plan(donnees))).hexdigest()
    assert ev["request"] == "R-0042" and ev["ops"] == ["qube.exec"] and ev["sha256"] == attendu


@pytest.mark.parametrize("extra", [{"request": "R-1"}, {"ops": ["shell"]}, {"ops": "vm.list"},
                                   {"sha256": "x" * 64}, {"ops": ["vm.list"] * 17}, {"autre": 1}])
def test_evenement_ferme(tmp_path, extra):
    ex = faire(tmp_path)
    ev = {"type": "refus", "appelant": "sys-agent", "raison": "r", **extra}
    assert ex.traiter({"type": "evenement", "event": ev})["statut"] == proto.ERREUR


def test_plan_refuse_par_l_executeur_garde_les_operations(tmp_path):
    ex = faire(tmp_path)
    assert lancer(ex, op("qube.exec", name="vault"))["statut"] == proto.REFUSE
    plan = [e for e in journal(tmp_path) if e.get("type") == "plan"][-1]
    assert plan["ops"] == ["qube.exec"] and plan["decision"] == C.REFUSER and len(plan["sha256"]) == 64


# --- J3 : registre purgé --------------------------------------------------------------

def test_jetable_supprime_par_qubes_purge_du_registre(tmp_path):
    ex = faire(tmp_path)
    jetable = lancer(ex, op("dispvm.create"))["resultats"][0]["resultat"]["dispvm"]
    assert jetable in ex.registre.qubes
    del ex.backend.qubes[jetable]  # arrêté : qubesd l'a supprimé (auto_cleanup)
    assert lancer(ex, op("vm.list"))["statut"] == proto.FAIT
    assert jetable not in ex.registre.qubes and jetable not in Registry(f"{tmp_path}/registre.json").qubes
    assert {"type": "registre_purge", "qubes": [jetable]} in journal(tmp_path)


def test_qube_recree_sous_le_meme_nom_reste_hors_registre(tmp_path):
    ex = faire(tmp_path)
    lancer(ex, op("vm.create"))
    ex.backend.qubes["neuf"] = dataclasses.replace(ex.backend.qubes["neuf"], uuid="autre")
    assert lancer(ex, op("vm.list"))["statut"] == proto.FAIT
    assert "neuf" not in ex.registre.agent_qubes(ex.backend.list_qubes())


# --- réponse à l'agent bornée sous 1 Mio ---------------------------------------------

def test_reponse_bornee_sans_perdre_le_compte_rendu(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "MAX_REPONSE", 3000)
    ex = faire(tmp_path)
    r = lancer(ex, op("vm.list"), op("vm.settings"), op("vm.start", name="personal"))
    assert r["statut"] == proto.FAIT and r["reponse_tronquee"] is True and r["non_executees"] == 0
    assert [x["op"] for x in r["resultats"]] == ["vm.list", "vm.settings", "vm.start"]
    assert all(x["ok"] for x in r["resultats"])
    assert r["resultats"][0]["resultat"] is None and len(r["resultats"][0]["tronque"]["sha256"]) == 64
    assert executor._taille(r) <= 3000
    assert [e for e in journal(tmp_path) if e.get("type") == "fin"][-1]["reponse_tronquee"] is True


def test_reponse_reelle_sous_le_mio(tmp_path):
    ex = faire(tmp_path)
    ex.backend.reponses_main["qaf.Screen"] = {"ok": True, "image": "A" * (640 * 1024)}
    ops = [op("qube.screenshot")] + [op("vm.list")] * (schema.MAX_OPS - 1)
    for i in range(30):
        ex.backend.qubes[f"q{i:02d}"] = _q(f"q{i:02d}", "AppVM", "blue", "debian-13-xfce", None)
    r = lancer(ex, *ops)
    assert r["statut"] == proto.FAIT and len(r["resultats"]) == schema.MAX_OPS
    assert executor._taille(r) <= proto.MAX_MESSAGE
    assert executor._taille(r) <= executor.MAX_REPONSE


def test_zone_du_texte_de_l_agent_sans_trait_d_union_ajoute():
    # Essai réel du 25/10 : un TextView GTK 3 coupe « rester » en « r-ester » ;
    # la fenêtre doit montrer le texte exact : Label sans césure Pango.
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "qaf_ui" / "fenetre.py").read_text(encoding="utf-8")
    corps = src[src.index("def _contenu("):src.index("def _flux(")]
    assert "attr_insert_hyphens_new(False)" in corps and "set_text(texte)" in corps
    assert "Gtk.TextView" not in corps and "PolicyType.NEVER" in corps
