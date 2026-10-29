"""Corrections du parcours utilisateur réel du 25/10 (docs/PARCOURS-1.md) :
B2 (clone de modèle), M1 (libellé de la cible), M2 (fatigue d'alerte : influence
réelle, C13 bis), M3 (demande liée à la session) et M8 (aucun jargon vu par
l'utilisateur). Côté exécuteur et registre : tests/test_demandes.py (fin du fichier)."""

import json
import re

import pytest

from qaf_gate import classify as C
from qaf_gate import confirm, schema
from qaf_ui import confirmation as c
from exemples_ops import EXEMPLES, op
from backend_sim import _q
from test_ops_v3_classement import AGENT, CONFIG, classer, contexte, installation, niveau

JARGON = re.compile(r"\bN[0-4]\b|\bR-\d{4,8}\b|portier", re.IGNORECASE)


# --- B2 : vm.clone ----------------------------------------------------------------

def clone(source="fedora-42-xfce", name="tpl-neuf"):
    return op("vm.clone", source=source, name=name)


def test_clone_d_un_modele_de_utilisateur_automatique():
    cl = classer(clone())  # fedora-42-xfce : modèle de work seulement
    assert cl.niveaux == (C.N1,) and cl.decision == C.AUTO
    assert classer(clone(), influence=True).decision == C.AUTO


def test_clone_d_un_modele_de_qube_systeme_ou_de_coffre_confirme():
    # Revue du 25/10 (MEDIUM) : debian-13-xfce porte sys-net et vault ; l'agent lirait leur système.
    cl = classer(clone("debian-13-xfce"))
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER
    assert classer(clone("debian-13-xfce"), mode=C.PILOTE).decision == C.CONFIRMER  # plancher
    # Et la source n'est plus implicite pour C13 : à nommer dans la demande.
    cl = classer(clone("debian-13-xfce"), portee=set())
    assert cl.non_nommes == ("debian-13-xfce",) and cl.decision == C.CONFIRMER_RENFORCE
    assert classer(clone("debian-13-xfce"), portee={"debian-13-xfce"}).hors_portee == ()


@pytest.mark.parametrize("source", [
    "vault",  # coffre
    "tpl-agent",  # modèle de sys-agent (protégé)
    "tpl-qaf-hand",  # infrastructure des jetables
    "work",  # pas un TemplateVM : contient des données de l'utilisateur
    "default-dvm",  # modèle de jetables (AppVM)
    "dom0",
    "inconnu",
])
def test_clone_refuse(source):
    assert classer(clone(source)).decision == C.REFUSER


# Revue de sécurité du 25/10 : dépendants de la source à toute profondeur.

def ctx_avec(*qubes, systeme=C.SYSTEME_DEFAUT):
    sim = installation()
    sim.qubes["tpl-x"] = _q("tpl-x", "TemplateVM", "black")
    for q in qubes:
        sim.qubes[q.name] = q
    return contexte(sim, C.Config(admin_qubes=CONFIG.admin_qubes, systeme=frozenset(systeme)))


def dvm(nom="x-dvm", template="tpl-x", **kw):
    return _q(nom, "AppVM", "red", template, None, template_for_dispvms=True, default_dispvm=nom,
              default_dispvm_herite=False, **kw)


def confirme_et_a_nommer(ctx):
    cl = classer(clone("tpl-x"), ctx=ctx)
    assert cl.niveaux == (C.N3,) and cl.decision == C.CONFIRMER
    assert classer(clone("tpl-x"), ctx=ctx, mode=C.PILOTE).decision == C.CONFIRMER  # plancher
    cl = classer(clone("tpl-x"), ctx=ctx, portee=set())
    assert "tpl-x" in cl.non_nommes and cl.decision == C.CONFIRMER_RENFORCE


def test_clone_qube_systeme_jetable_d_un_modele_de_jetables():
    # Legion : sys-usb est un DispVM de default-dvm, lui-même sur le modèle copié.
    confirme_et_a_nommer(ctx_avec(dvm(), _q("usb-disp", "DispVM", "red", "x-dvm"), systeme={"usb-disp"}))


def test_clone_coffre_qui_ouvre_ses_jetables_sur_un_modele_de_jetables():
    coffre = _q("vault", "AppVM", "black", "debian-13-xfce", None, default_dispvm="x-dvm",
                default_dispvm_herite=False)
    confirme_et_a_nommer(ctx_avec(dvm(), coffre))


def test_clone_qube_systeme_a_trois_niveaux_par_default_dispvm():
    # tpl-x <- x-dvm (template) <- w-dvm (default_dispvm) <- sys-x (default_dispvm, réseau : S).
    w = _q("w-dvm", "AppVM", "red", "debian-13-xfce", None, template_for_dispvms=True, default_dispvm="x-dvm",
           default_dispvm_herite=False)
    sx = _q("sys-x", "AppVM", "red", "debian-13-xfce", None, True, default_dispvm="w-dvm",
            default_dispvm_herite=False)
    ctx = ctx_avec(dvm(), w, sx)
    assert ctx.descendance("tpl-x") == {"x-dvm", "w-dvm", "sys-x"}
    confirme_et_a_nommer(ctx)


def test_clone_dans_le_doute_modele_de_jetables_confirme():
    # Un dépendant ni A ni T (ici M, modèle de jetables de l'utilisateur) : confirmation.
    confirme_et_a_nommer(ctx_avec(dvm()))


def test_clone_d_un_modele_d_espaces_ordinaires_reste_automatique():
    # Relevé du 25/10 : debian-13-xfce ne sert qu'à dev-demo (T) ; plus un espace de l'agent.
    ctx = ctx_avec(_q("dev-demo", "AppVM", "blue", "tpl-x", None),
                   _q("a-x", "AppVM", "gray", "tpl-x", None))
    cl = classer(clone("tpl-x"), ctx=ctx, agent=AGENT | {"a-x"}, portee=set())
    assert cl.niveaux == (C.N1,) and cl.decision == C.AUTO and cl.hors_portee == ()
    assert classer(clone("tpl-x"), ctx=ctx, influence=True).decision == C.AUTO


def test_clone_d_un_coffre_modele_refuse():
    cfg = C.Config(coffres=frozenset({"vault", "fedora-42-xfce"}), admin_qubes=CONFIG.admin_qubes)
    assert classer(clone("fedora-42-xfce"), ctx=contexte(config=cfg)).decision == C.REFUSER


def test_le_clone_est_de_l_agent_dans_le_meme_plan():
    # Créé dans le plan : classe A, donc paquets et espace dessus automatiques, même hors demande.
    cl = classer(clone(), op("vm.create", name="a-neuf", template="tpl-neuf", label="gray"),
                 op("vm.start", name="tpl-neuf"), portee=set())
    assert cl.decision == C.AUTO and cl.hors_portee == ()


def test_modele_source_du_clone_hors_controle_de_portee():
    assert classer(clone(), portee=set()).hors_portee == ()


def test_set_template_vers_un_modele_de_l_agent_sur_un_qube_de_l_agent():
    assert niveau(op("vm.set_template", name="a-dev", template="tpl-a")) == C.N1
    assert classer(op("vm.set_template", name="a-dev", template="tpl-a"), portee={"a-dev"}).decision == C.AUTO
    # Mais faire dépendre un qube de l'utilisateur d'un modèle de l'agent reste renforcé (R-D).
    assert classer(op("vm.set_template", name="work", template="tpl-a")).decision == C.CONFIRMER_RENFORCE


def test_installation_dans_un_modele_de_l_agent_automatique():
    assert classer(op("pkg.install", name="tpl-a", packages=["tree"])).decision == C.AUTO
    assert classer(op("pkg.install", name="debian-13-xfce", packages=["tree"])).decision != C.AUTO


# --- M2 : C13 bis --------------------------------------------------------------------

def test_c13_bis_reseau_par_defaut_implicite():
    net = op("vm.set_netvm", name="a-dev", netvm="sys-firewall")  # default_netvm
    assert classer(net, portee={"a-dev"}).hors_portee == ()
    assert classer(net, portee={"a-dev"}).decision == C.AUTO  # N2 non influencé


@pytest.mark.parametrize("netvm", ["sys-net", "sys-whonix"])
def test_c13_bis_autre_route_a_nommer(netvm):
    sim = installation()
    sim.create_appvm("sys-whonix", "debian-13-xfce", "black")
    cl = classer(op("vm.set_netvm", name="a-dev", netvm=netvm), ctx=contexte(sim), portee={"a-dev"})
    assert cl.hors_portee == (0,) and cl.non_nommes == (netvm,)


def test_c13_bis_route_par_defaut_mais_systeme_configure():
    sim = installation()
    sim.globaux["default_netvm"] = "sys-whonix"
    sim.create_appvm("sys-whonix", "debian-13-xfce", "black")
    cl = classer(op("vm.set_netvm", name="a-dev", netvm="sys-whonix"), ctx=contexte(sim), portee={"a-dev"})
    assert cl.hors_portee == (0,)  # sys-whonix est dans la liste système de l'utilisateur


def test_c13_bis_le_qube_modifie_reste_a_nommer():
    # La cible (modifiée) n'est jamais implicite, et le modèle d'update.run est modifié.
    assert classer(op("vm.set_netvm", name="work", netvm="sys-firewall"), portee=set()).non_nommes == ("work",)
    assert classer(op("update.run", targets=["debian-13-xfce"]), portee={"a-dev"}).hors_portee == (0,)
    assert classer(op("pkg.install", name="debian-13-xfce", packages=["tree"]), portee=set()).hors_portee == (0,)


def test_c13_bis_modele_par_defaut_ou_de_l_agent():
    assert classer(op("vm.set_template", name="a-dev", template="debian-13-xfce"), portee={"a-dev"}).hors_portee == ()
    assert classer(op("vm.set_template", name="a-dev", template="fedora-42-xfce"),
                   portee={"a-dev"}).non_nommes == ("fedora-42-xfce",)


def test_c13_bis_n_affaiblit_jamais_un_refus():
    for o in (op("vm.set_netvm", name="vault", netvm="sys-firewall"), clone("vault")):
        assert classer(o, portee=set()).decision in (C.REFUSER, C.CONFIRMER_RENFORCE)
    # N1 sur un qube de l'agent : hors portée journalisé, automatique (n2 (a), AGENT-JETABLE §13.2).
    cl = classer(op("vm.set_template", name="a-dev", template="tpl-agent"), portee=set())
    assert (cl.decision, cl.hors_portee) == (C.AUTO, (0,))


# --- M1 : libellé de la cible ---------------------------------------------------------

QUBES = [["debian-13-xfce", "black", "utilisateur-modele"], ["work", "blue", "utilisateur"], ["personal", "yellow", "utilisateur"],
         ["sys-usb", "red", "utilisateur-systeme"], ["vault", "black", "coffre"], ["tpl-neuf", "gray", "agent-modele"],
         ["sys-net", "red", "utilisateur-systeme"], ["fedora-42-xfce", "black", "utilisateur-modele"]]


def requete(*ops, niveaux=None, **extra):
    p = schema.parse_plan(json.dumps({"version": 1, "request": "R-0147", "ops": list(ops)}).encode())
    msg = {"type": "confirmer", "appelant": "sys-agent", "canonique": schema.canonicalize(p).decode(),
           "niveaux": niveaux or [C.N2] * len(p.ops), "decision": C.CONFIRMER, "jeton": "482913",
           "influence": False, "qubes": QUBES, "expire_dans": 60, "texte_demande": None, "age_demande": None,
           "hors_portee": [], **extra}
    return c.lire_requete(msg)


def test_mise_a_jour_d_un_modele_nomme_le_modele():
    v = c.rediger(requete(op("update.run", targets=["debian-13-xfce"])))
    a = v.actions[0]
    assert a.destination.nom == "debian-13-xfce" and a.destination.role == "utilisateur-modele"
    assert "ton modèle « debian-13-xfce »" in a.phrase.replace(" ", " ")
    assert "qube de mises à jour" in a.phrase


@pytest.mark.parametrize("nom", sorted(EXEMPLES))
def test_aucune_cible_cœur_du_systeme(nom):
    if nom == "backup.run":
        r = requete(op(nom), sauvegardes=[["hebdo", "personal", None]])
    else:
        r = requete(op(nom))
    for a in c.rediger(r).actions:
        for e in (a.source, a.destination):
            assert e is None or "Cœur du système" not in e.nom, (nom, e)


def test_sauvegarde_vise_sa_destination():
    a = c.rediger(requete(op("backup.run"), sauvegardes=[["hebdo", "personal", None]])).actions[0]
    assert a.destination.nom == "personal"


def test_clone_phrase_et_cible():
    a = c.rediger(requete(clone("debian-13-xfce", name="tpl-neuf"))).actions[0]
    assert a.source.nom == "debian-13-xfce" and a.destination.nom == "tpl-neuf" and a.destination.par_agent
    assert "nouveau modèle" in a.phrase and "sans Internet" in a.phrase


# --- M8 : aucun jargon vu par l'utilisateur ---------------------------------------------------

def _textes(v):
    out = [v.titre, v.reversible, v.alerte or "", v.demande, v.age_demande, v.gravite, v.entete, v.sous_entete,
           v.pied, c.SUITE_NON_VERIFIEE]
    for a in v.actions:
        out += [a.phrase, a.nuance] + [e.nom for e in (a.source, a.destination) if e]
    return out


@pytest.mark.parametrize("nom", sorted(EXEMPLES))
@pytest.mark.parametrize("demande", [None, "fais-le"])
def test_fenetre_sans_jargon(nom, demande):
    extra = {"sauvegardes": [["hebdo", "personal", None]]} if nom == "backup.run" else {}
    if demande:
        extra.update(texte_demande=demande, age_demande=240, hors_portee=[0], non_nommes=["work"])
    for niv in (0, 2, 3):
        v = c.rediger(requete(op(nom), niveaux=[niv], influence=True, **extra))
        for t in _textes(v):
            assert not JARGON.search(t), (nom, t)
        assert v.gravite in ("faible", "moyenne", "forte")


def test_ta_demande_avec_son_age():
    v = c.rediger(requete(op("vm.start", name="work"), texte_demande="démarre work", age_demande=240))
    assert v.age_demande == "il y a 4 min" and v.demande == "tapée dans la palette, il y a 4 min"


def test_messages_a_l_agent_sans_jargon():
    from qaf_mcp.server import OUTILS
    for o in OUTILS:
        assert not JARGON.search(o["description"]), o["name"]
    p = schema.parse_plan(b'{"version":1,"request":"R-0001","ops":[{"op":"vm.shutdown","name":"dom0"}]}')
    assert not JARGON.search(C.motif_n4(p))


def test_console_sans_code():
    p = schema.parse_plan(b'{"version":1,"request":"R-0042","ops":[{"op":"vm.remove","name":"work"}]}')
    d = confirm.Demande("sys-agent", p, schema.canonicalize(p), (C.N3,), C.CONFIRMER_RENFORCE, "731904",
                        texte_demande="supprime work", age_demande=60)
    assert not JARGON.search(confirm.resume(d))


# --- M3 : schéma de la session ---------------------------------------------------------

@pytest.mark.parametrize("session", ["S-0123", "S-0123456789ABCDEF", "R-0123456789abcdef", 12, None, ""])
def test_session_invalide(session):
    d = {"version": 1, "request": "R-0001", "session": session, "ops": [{"op": "vm.list"}]}
    if session is None:
        assert schema.parse_plan(json.dumps(d).encode()).session is None
        return
    with pytest.raises(schema.PlanError):
        schema.parse_plan(json.dumps(d).encode())


def test_session_canonique():
    d = {"version": 1, "request": "R-0001", "session": "S-0123456789abcdef", "ops": [{"op": "vm.list"}]}
    p = schema.parse_plan(json.dumps(d).encode())
    assert p.session == "S-0123456789abcdef" and schema.parse_plan(schema.canonicalize(p)) == p
