"""États de la fenêtre de confirmation pour le rendu hors écran
(tools/rendu-ui.sh rendu_confirmation:simple|renforcee|influencee). Outil de la machine de développement,
jamais installé dans dom0 : la fenêtre réelle (qaf_ui/fenetre.py) avec une
demande fabriquée comme dans tests/test_confirmation.py."""

import json

from qaf_gate import classify, schema
from qaf_ui import confirmation as c
from qaf_ui import fenetre

QUBES = [["vault", "black", "coffre"], ["sys-net", "red", "utilisateur-systeme"], ["work", "blue", "utilisateur"],
         ["dev", "purple", "agent"], ["debian-13-xfce", "black", "utilisateur-modele"]]
CLAVIER = c.Clavier(nom="AT Raw Set 2 keyboard", ok=True, raison="")


def demande(fr, en, zh=None):
    """Texte tapé par l'utilisateur dans la palette, dans la langue du rendu (--langue)."""
    from qaf_ui import i18n
    return {"en": en, "zh": zh or en}.get(i18n.langue(), fr)


def _requete(ops, niveaux, decision, influence, **extra):
    p = schema.parse_plan(json.dumps({"version": 1, "request": "R-0147", "ops": ops}).encode())
    msg = {"type": "confirmer", "appelant": "sys-agent", "canonique": schema.canonicalize(p).decode(),
           "niveaux": niveaux, "decision": decision, "jeton": "482913", "influence": influence,
           "qubes": QUBES, "expire_dans": 118, "texte_demande": None, "age_demande": None,
           "hors_portee": []}
    msg.update(extra)
    return c.lire_requete(msg)


def _fenetre(req):
    fenetre.preparer()
    f = fenetre.FenetreConfirmation(req, CLAVIER, lambda: 1000.0, lambda _r: None)
    return f.win


def simple():
    return _fenetre(_requete([{"op": "vm.start", "name": "work"}], [classify.N2], classify.CONFIRMER, False,
                             texte_demande=demande("démarre work", "start work", "启动 work"), age_demande=5))


def modele():
    """Modèle livré par Qubes (classe d'abord : « Modèle », pas « Coffre ») ; la session a reçu une suite (n19)."""
    return _fenetre(_requete([{"op": "pkg.install", "name": "debian-13-xfce", "packages": ["tree"]}],
                             [classify.N2], classify.CONFIRMER, False, suite=True,
                             texte_demande=demande("installe tree dans le modèle debian-13-xfce",
                                                   "install tree in the debian-13-xfce template",
                                                   "在模板 debian-13-xfce 中安装 tree"), age_demande=40))


def influencee():
    """Agent permanent (influencé), aucune demande de l'utilisateur, vérification du clavier désactivée (MODE DÉVELOPPEMENT)."""
    req = _requete([{"op": "vm.start", "name": "work"}], [classify.N2], classify.CONFIRMER, True)
    fenetre.preparer()
    clavier = c.Clavier(nom="AT Raw Set 2 keyboard", ok=True, raison="", dev=True)
    return fenetre.FenetreConfirmation(req, clavier, lambda: 1000.0, lambda _r: None).win


def renforcee():
    return _fenetre(_requete([{"op": "vm.set_netvm", "name": "vault", "netvm": "sys-net"}], [classify.N3],
                             classify.CONFIRMER_RENFORCE, True))
