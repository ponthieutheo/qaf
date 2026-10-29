"""Captures 02 et 03 de la vitrine (docs/vitrine/captures/), rendues hors écran
à partir du VRAI code de l'interface (tools/rendu-ui.sh rendu_vitrine:FONCTION),
sans rien taper dans une vraie session. Outil de la machine de développement, jamais installé dans
dom0. L'assemblage sur le fond des captures réelles se fait à part.

Le scénario est celui du README : l'utilisateur tape « crée-moi un espace dev Debian
avec git » ; l'agent permanent crée « dev » (N1, sans réseau), puis demande à
lui donner Internet par sys-firewall (N2 : confirmation simple)."""

import rendu_confirmation as rc
from qaf_gate import classify
from qaf_ui import fenetre

DEMANDE = ("crée-moi un espace dev Debian avec git", "make me a Debian dev qube with git")
QUBES = [["dev", "purple", "agent"], ["sys-firewall", "green", "utilisateur-systeme"], ["sys-net", "red", "utilisateur-systeme"],
         ["vault", "black", "coffre"], ["debian-13-xfce", "black", "utilisateur-modele"]]


def palette_demande():
    """La palette ouverte, la demande tapée, comme sur une vraie machine (agent permanent, aucun agent ouvert)."""
    from qaf_ui import palette
    from qaf_ui import palette_gtk as pg
    palette.agents_ouverts = lambda: []  # aucun Doorman ici : ce que dit celui d'une vraie machine
    palette.agent_permanent = lambda: True
    app = pg.Palette()
    app.clavier = rc.CLAVIER  # clavier interne (aucun sous xvfb)
    app.register(None)
    pg._style()
    app._construire()
    app._ouverts([])
    app.champ.set_text(rc.demande(*DEMANDE))
    app.champ.grab_focus()
    app.champ.set_position(-1)  # curseur en fin de texte, rien de sélectionné
    return app.fenetre


def confirmation_simple():
    """Donner Internet à « dev », créé par l'agent pour cette demande : N2, confirmation simple."""
    req = rc._requete([{"op": "vm.set_netvm", "name": "dev", "netvm": "sys-firewall"}], [classify.N2],
                      classify.CONFIRMER, True, qubes=QUBES, texte_demande=rc.demande(*DEMANDE), age_demande=38)
    fenetre.preparer()
    return fenetre.FenetreConfirmation(req, rc.CLAVIER, lambda: 1000.0, lambda _r: None).win


def confirmation_renforcee():
    """Démarrer « untrusted », que l'utilisateur n'a pas demandé : agent permanent influencé, aucune
    demande dans la palette, confirmation renforcée avec code. Clavier interne du produit."""
    qubes = [["untrusted", "red", "utilisateur"], ["work", "blue", "utilisateur"], ["vault", "black", "coffre"],
             ["sys-net", "red", "utilisateur-systeme"], ["debian-13-xfce", "black", "utilisateur-modele"]]
    req = rc._requete([{"op": "vm.start", "name": "untrusted"}], [classify.N2], classify.CONFIRMER_RENFORCE, True,
                      qubes=qubes)
    fenetre.preparer()
    return fenetre.FenetreConfirmation(req, rc.CLAVIER, lambda: 1000.0, lambda _r: None).win
