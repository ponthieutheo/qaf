"""Mesure d'usage de l'abonnement de l'agent, côté dom0 : validation, mise en
forme, rythme et cache (docs/AGENT-PANNEAU.md §5 et §7.1). Sans GTK.

La mesure vient du service qrexec qaf.Usage de sys-agent (qaf_mcp/usage.py),
appelé par dom0 SANS JAMAIS DÉMARRER sys-agent (--no-autostart). sys-agent est
influencé en permanence : tout ce qu'il rend peut être faux. D'où :
- lecture bornée (4 096 octets), json.loads dans un try qui attrape tout ;
- schéma FERMÉ : clé inconnue, type faux (bool pour un entier compris), valeur
  hors bornes = mesure entière rejetée (None), cache inchangé ;
- seuls des entiers bornés et des énumérations passent ; tout texte affiché
  est une constante d'ici, choisie d'après eux ;
- aucune décision ne s'appuie sur la mesure (ni plan, ni confirmation, ni
  pause) : elle ne sert qu'à l'affichage (D2).

Deux versions du schéma sont lues : v=1 (sys-agent pas encore mis à jour, clés
codex et grok exactement) et v=2 (fournisseurs de la table IDS, plus actif,
sante, connexion, agents). Le cache écrit en v=1 reste lisible. La bascule de
fournisseur (qaf.Fournisseur) est un geste de l'utilisateur, pas une décision
fondée sur la mesure : elle ne fait que transmettre un id de la table de dom0.
"""

import json
import math
import os
import time
from dataclasses import dataclass

from .i18n import langue, t, tn

AGENT = "sys-agent"  # = palette.AGENT_PERMANENT
MAX_OCTETS = 4096
DELAI_S = 20
ARGV_USAGE = ["qvm-run", "--pass-io", "--no-gui", "--no-autostart", "--service", AGENT, "qaf.Usage"]
ARGV_DEMARRER = ["qvm-start", AGENT]
DELAI_BASCULE_S = 5
MAX_REPONSE_BASCULE = 16

ETATS = ("ok", "sans_jeton", "en_pause", "expire", "refuse", "limite", "injoignable", "erreur")
OFFRES = {"": "", "pro": "Pro", "max_5x": "Max 5x", "max_20x": "Max 20x", "team": "Team",
          "team_5x": "Team 5x", "enterprise": "Enterprise"}
# Valeurs de qaf.Usage -> clé de texte (qaf_ui/langues) ou nom propre.
FENETRES = {"5h": "usage.fenetre.5h", "semaine": "usage.fenetre.semaine"}
# Infobulle de l'icône : « 61 % sur semaine » / « 61% of the weekly limit ».
LIMITES = {"5h": "usage.limite.5h", "semaine": "usage.limite.semaine"}
MODELES = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable"}
MODELES_TOTAUX = {"": "", "autre": "usage.autres_modeles", **{k: v for k, v in MODELES.items()}}
JOURS = ("usage.jour.lundi", "usage.jour.mardi", "usage.jour.mercredi", "usage.jour.jeudi", "usage.jour.vendredi", "usage.jour.samedi",
         "usage.jour.dimanche")
# Les 35 fournisseurs, ceux du plugin agents d'Omarchy dans son ordre, avec après ses CLI les
# 12 que seul son lanceur connaît : table de dom0, écrite à part de celle de sys-agent (qaf_mcp/fournisseurs_table.py, jamais importée ici) ; un test
# croisé vérifie l'égalité des deux listes.
IDS = ("claude", "codex", "grok", "kimi", "cursor", "grokbot", "copilot", "muse", "agy", "kimchi", "hermes",
       "devin", "vibe", "qwen", "opencode", "pi", "omp", "ori", "crush", "openclaw", "dsh", "mcode", "kiro-cli",
       "freebuff", "kilo", "amp", "commandcode", "zai", "ollama", "minimax", "openrouter", "deepseek", "fireworks",
       "synthetic", "local")
FOURNISSEURS = IDS[1:]
FOURNISSEURS_V1 = ("codex", "grok")
# grokbot : une limite d'usage d'un compte Cursor, aucun Doorman au milieu. Les trois autres :
# CLI sans validation prouvée, que QAF ne lance jamais comme agent.
JAMAIS_AGENT = ("grokbot", "pi", "ori", "mcode", "kiro-cli", "freebuff", "amp")
# Noms propres de dom0, jamais lus dans le qube ; « local » est traduit (nom_fournisseur).
NOMS = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "kimi": "Kimi", "cursor": "Cursor",
        "grokbot": "Grok Bot", "copilot": "Copilot", "muse": "Muse", "agy": "Google", "kimchi": "Kimchi",
        "hermes": "Hermes", "devin": "Devin", "vibe": "Mistral Vibe", "qwen": "Qwen", "opencode": "OpenCode",
        "pi": "Pi", "omp": "Oh My Pi", "ori": "Ori", "crush": "Crush", "openclaw": "OpenClaw",
        "dsh": "DeepSeek Harness", "mcode": "MiniMax Code", "kiro-cli": "Kiro CLI", "freebuff": "Freebuff",
        "kilo": "Kilo", "amp": "Amp", "commandcode": "Command Code",
        "zai": "Z.ai", "ollama": "Ollama Cloud", "minimax": "MiniMax", "openrouter": "OpenRouter",
        "deepseek": "DeepSeek", "fireworks": "Fireworks", "synthetic": "Synthetic"}
ETATS_FOURNISSEUR_V1 = ("connecte", "deconnecte", "non_installe")
ETATS_FOURNISSEUR = ETATS_FOURNISSEUR_V1 + ("connexion_seule",)
SANTES = ("ok", "injoignable", "cle_refusee", "credit_epuise", "inconnue")
SANTES_TEXTES = {"injoignable": "carte.sante_injoignable", "cle_refusee": "carte.sante_cle",
                 "credit_epuise": "carte.sante_credit"}
CONNEXIONS = ("ok", "echec", "")
CLES_V1 = {"v", "etat", "offre", "fenetres", "totaux", "fournisseurs"}
CLES = CLES_V1 | {"actif", "sante", "connexion", "agents"}
CLES_FENETRE = {"nom", "modele", "pourcent", "remise"}
CLES_TOTAUX = {"jetons_jour", "jetons_semaine", "invites_jour", "sessions_jour", "modele", "jour_actif", "partiel"}
MAX_JETONS = 10 ** 12
MAX_COMPTE = 100_000

# Rythme (Omarchy : refreshIntervalSec, réutilisation de 15 s, 3 min près d'un seuil).
INTERVALLE_S = 900
INTERVALLE_PROCHE_S = 180
SEUIL_PROCHE = 75
REUTILISATION_S = 15
REESSAI_LIMITE_S = 300
ALARME = 90
TROP_VIEUX_S = 7 * 86400


@dataclass(frozen=True)
class Fenetre:
    nom: str        # clé de FENETRES
    modele: str     # "" ou clé de MODELES (limite propre à un modèle)
    pourcent: int
    remise: int     # époque, secondes


@dataclass(frozen=True)
class Totaux:
    jetons_jour: int
    jetons_semaine: int
    invites_jour: int
    sessions_jour: int
    modele: str
    jour_actif: int
    partiel: bool


@dataclass(frozen=True)
class Mesure:
    etat: str
    offre: str
    fenetres: tuple
    totaux: Totaux | None
    fournisseurs: tuple  # ((id, état), …) dans l'ordre de IDS, ou ()
    v: int = 1
    actif: str = "claude"     # v1, ou clé absente : Claude, seul agent possible
    sante: str = "inconnue"
    connexion: str = ""       # résultat du dernier assistant : ok, echec, "" (aucun)
    agents: tuple = ()        # ids activables, dans l'ordre de IDS


def _entier(x, bas, haut):
    return type(x) is int and bas <= x <= haut


def lire_mesure(octets, maintenant=None):
    """Octets rendus par qaf.Usage -> Mesure, ou None (rejetée en bloc)."""
    maintenant = time.time() if maintenant is None else maintenant
    if not isinstance(octets, (bytes, bytearray)) or len(octets) > MAX_OCTETS:
        return None
    try:
        brut = json.loads(bytes(octets).decode("utf-8"))
    except Exception:  # noqa: BLE001 (profondeur, encodage, tout : rejet)
        return None
    try:
        return _valider(brut, maintenant)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def _valider(b, maintenant):
    if not isinstance(b, dict) or not _entier(b.get("v"), 1, 2):
        return None
    v = b["v"]
    if not {"v", "etat"} <= set(b) <= (CLES_V1 if v == 1 else CLES) or b["etat"] not in ETATS:
        return None
    offre = b.get("offre", "")
    if not isinstance(offre, str) or offre not in OFFRES:
        return None
    brutes = b.get("fenetres", [])
    if not isinstance(brutes, list) or len(brutes) > 4:
        return None
    if brutes and b["etat"] not in ("ok", "limite"):
        return None
    fenetres = []
    for f in brutes:
        if not isinstance(f, dict) or set(f) != CLES_FENETRE:
            return None
        if not isinstance(f["nom"], str) or f["nom"] not in FENETRES:
            return None
        if not isinstance(f["modele"], str) or (f["modele"] and f["modele"] not in MODELES):
            return None
        if not _entier(f["pourcent"], 0, 100):
            return None
        if not _entier(f["remise"], int(maintenant) - 86400, int(maintenant) + 8 * 86400):
            return None
        fenetres.append(Fenetre(f["nom"], f["modele"], f["pourcent"], f["remise"]))
    totaux = None
    if "totaux" in b:
        t = b["totaux"]
        if not isinstance(t, dict) or set(t) != CLES_TOTAUX:
            return None
        if not all(_entier(t[k], 0, MAX_JETONS) for k in ("jetons_jour", "jetons_semaine")):
            return None
        if t["jetons_jour"] > t["jetons_semaine"]:
            return None
        if not all(_entier(t[k], 0, MAX_COMPTE) for k in ("invites_jour", "sessions_jour")):
            return None
        if not isinstance(t["modele"], str) or t["modele"] not in MODELES_TOTAUX:
            return None
        if not _entier(t["jour_actif"], -1, 6) or type(t["partiel"]) is not bool:
            return None
        totaux = Totaux(**{k: t[k] for k in CLES_TOTAUX})
    fournisseurs = ()
    if "fournisseurs" in b:
        f = b["fournisseurs"]
        if not isinstance(f, dict):
            return None
        if v == 1 and set(f) != set(FOURNISSEURS_V1):
            return None  # v1 : exactement codex et grok
        if not set(f) <= set(FOURNISSEURS):
            return None
        permis = ETATS_FOURNISSEUR_V1 if v == 1 else ETATS_FOURNISSEUR
        if not all(isinstance(x, str) and x in permis for x in f.values()):
            return None
        fournisseurs = tuple((k, f[k]) for k in IDS if k in f)
    if v == 1:
        return Mesure(b["etat"], offre, tuple(fenetres), totaux, fournisseurs)
    actif = b.get("actif", "claude")
    if not isinstance(actif, str) or actif not in IDS or actif in JAMAIS_AGENT:
        return None
    sante = b.get("sante", "inconnue")
    connexion = b.get("connexion", "")
    if not isinstance(sante, str) or sante not in SANTES:
        return None
    if not isinstance(connexion, str) or connexion not in CONNEXIONS:
        return None
    agents = b.get("agents", [])
    if not isinstance(agents, list) or len(agents) > len(IDS):
        return None
    if not all(isinstance(a, str) and a in IDS and a not in JAMAIS_AGENT for a in agents):
        return None
    if len(set(agents)) != len(agents):
        return None
    return Mesure(b["etat"], offre, tuple(fenetres), totaux, fournisseurs, 2, actif, sante, connexion,
                  tuple(k for k in IDS if k in agents))


def vers_dict(m):
    """Mesure -> dict au format de qaf.Usage (pour le cache)."""
    d = {"v": m.v, "etat": m.etat, "offre": m.offre,
         "fenetres": [{"nom": f.nom, "modele": f.modele, "pourcent": f.pourcent, "remise": f.remise}
                      for f in m.fenetres]}
    if m.totaux is not None:
        d["totaux"] = {k: getattr(m.totaux, k) for k in CLES_TOTAUX}
    if m.fournisseurs:
        d["fournisseurs"] = dict(m.fournisseurs)
    if m.v >= 2:
        d.update(actif=m.actif, sante=m.sante, connexion=m.connexion, agents=list(m.agents))
    return d


# --- fournisseurs (dom0 ne connaît que sa table) -------------------------------------------

def nom_fournisseur(cle):
    return t("carte.ia_locale") if cle == "local" else NOMS[cle]


def actif_de(m):
    """Fournisseur actif, None sans mesure : rien ne présume Claude avant d'avoir lu sys-agent."""
    return None if m is None else m.actif


def sante_affichee(m):
    """Clé de texte du problème du fournisseur actif, ou None (ok, inconnue, sans mesure)."""
    return None if m is None else SANTES_TEXTES.get(m.sante)


def lire_borne(argv, limite, delai, ouvrir=None):
    """(code, octets), en ne gardant jamais plus de limite + 1 octets : capture_output
    garderait tout ce qu'écrit le qube avant la coupe. Au-delà, le processus est tué. Code
    None : lancement impossible, ou délai dépassé."""
    import subprocess
    import threading
    try:
        p = (ouvrir or subprocess.Popen)(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, b""
    sortie = []

    def lire():
        try:
            sortie.append(p.stdout.read(limite + 1))
        except (OSError, ValueError):
            sortie.append(b"")

    debut = time.monotonic()
    fil = threading.Thread(target=lire, daemon=True)
    fil.start()
    fil.join(delai)
    if fil.is_alive():
        p.kill()
        p.wait()
        return None, b""
    if len(sortie[0]) > limite:
        p.kill()
    try:
        code = p.wait(timeout=max(0.1, delai - (time.monotonic() - debut)))
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        return None, b""
    p.stdout.close()
    return code, sortie[0]


def argv_bascule(cle):
    """qaf.Fournisseur+ID, sans jamais démarrer sys-agent. Un id hors de la table de dom0, ou
    qui ne peut pas être l'agent, ne part pas : l'argument qrexec vient d'ici seulement."""
    if cle not in IDS or cle in JAMAIS_AGENT:
        raise ValueError(f"fournisseur non activable : {cle}")
    return ["qvm-run", "--pass-io", "--no-gui", "--no-autostart", "--service", AGENT, f"qaf.Fournisseur+{cle}"]


def basculer(cle, ouvrir=None, delai=DELAI_BASCULE_S):
    """Demande à sys-agent de changer d'actif. Vrai seulement sur le code 0 et la ligne « ok ».
    Bloquant : hors du fil GTK."""
    try:
        argv = argv_bascule(cle)
    except ValueError:
        return False
    code, octets = lire_borne(argv, MAX_REPONSE_BASCULE, delai, ouvrir)
    return code == 0 and octets.strip() == b"ok"


# --- mise en forme (textes de dom0) ------------------------------------------------------

def duree(secondes):
    """« 2 h 14 », « 3 j 7 h », « 41 min », « maintenant »."""
    s = int(secondes)
    if s <= 0:
        return t("usage.maintenant")
    m = math.ceil(s / 60)
    if m < 60:
        return t("usage.duree.min", m=m)
    h, m = divmod(m, 60)
    if h < 24:
        return t("usage.duree.h_min", h=h, m=f"{m:02d}") if m else t("usage.duree.h", h=h)
    j, h = divmod(h, 24)
    return t("usage.duree.j_h", j=j, h=h) if h else t("usage.duree.j", j=j)


def jetons(n):
    """« 812 K », « 4,2 M », « 950 », « 1,1 Md » (anglais et chinois : « 4.2 M », « 1.1 B »)."""
    if n < 1000:
        return str(n)
    virgule = "," if langue() == "fr" else "."
    for unite, base in ((t("usage.milliard"), 10 ** 9), ("M", 10 ** 6), ("K", 10 ** 3)):
        if n >= base:
            v = n / base
            texte = f"{v:.1f}" if v < 10 else f"{v:.0f}"
            return f"{texte.replace('.', virgule)} {unite}".replace(f"{virgule}0 ", " ")
    return str(n)


def age(secondes):
    """« à l'instant », « il y a 12 min », « il y a 3 h »."""
    if secondes < 60:
        return t("usage.instant")
    return t("usage.il_y_a", duree=duree(secondes))


def pourcent_affiche(f, maintenant):
    """Pourcentage à montrer : une fenêtre dont la remise est passée est à 0
    (règle d'Omarchy, limitWindow)."""
    return 0 if f.remise <= maintenant else f.pourcent


def principales(m):
    """Fenêtres de tout l'abonnement (sans modèle), dans l'ordre 5 h, semaine."""
    if m is None:
        return []
    ordre = {"5h": 0, "semaine": 1}
    return sorted((f for f in m.fenetres if not f.modele), key=lambda f: ordre[f.nom])


def traits(m, nom):
    """Limites propres à un modèle sur la même fenêtre (trait sur la jauge) ;
    une limite à 0 % n'a pas de trait (il se confondrait avec le début de la jauge)."""
    return [] if m is None else [f for f in m.fenetres if f.modele and f.nom == nom and f.pourcent > 0]


def fenetre_liante(m, maintenant):
    """La fenêtre la plus pleine (pourcentage affiché), ou None."""
    fs = [] if m is None else list(m.fenetres)
    if not fs:
        return None
    return max(fs, key=lambda f: (pourcent_affiche(f, maintenant), f.nom == "5h"))


def alarme(m, maintenant):
    """Les fenêtres mesurées sont celles de Claude : pas d'alarme s'il n'est pas l'actif."""
    if actif_de(m) not in ("claude", None):
        return False
    f = fenetre_liante(m, maintenant)
    return f is not None and pourcent_affiche(f, maintenant) >= ALARME


def infobulle_fenetre(f, maintenant, age_s=None, allume=True):
    nom = t(FENETRES[f.nom]) if not f.modele else f"{MODELES[f.modele]} · {t(FENETRES[f.nom]).lower()}"
    texte = t("usage.bulle.utilises", nom=nom, pc=pourcent_affiche(f, maintenant))
    if f.remise > maintenant:
        texte += t("usage.bulle.remise", duree=duree(f.remise - maintenant))
    if age_s is not None and (not allume or age_s >= 900):
        texte += t("usage.bulle.mesure", age=age(age_s)) + ("" if allume else t("usage.bulle.arrete"))
    return texte + t("usage.bulle.par_agent")


def phrases_heros(m, allume, etat_carte=None):
    """Lignes en capitales du héros (tournent toutes les 2,8 s)."""
    if not allume:
        return [t("usage.heros.arrete")]
    if etat_carte == "vide":
        return [t("usage.heros.connecter")]
    if etat_carte == "ajout":
        return [t("usage.heros.ajouter")]
    if actif_de(m) not in ("claude", None):
        # Les totaux sont ceux de Claude : ils ne diraient rien de l'actif.
        return [t("usage.heros.fournisseur", nom=nom_fournisseur(m.actif).upper())]
    tot = None if m is None else m.totaux
    if tot is None or tot.jetons_semaine == 0:
        return [t("usage.heros.abonnement")]
    environ = "≈ " if tot.partiel else ""
    lignes = [environ + t("usage.heros.semaine", jetons=jetons(tot.jetons_semaine))]
    if tot.jetons_jour:
        lignes.append(environ + t("usage.heros.jour", jetons=jetons(tot.jetons_jour)))
    if tot.modele and tot.modele != "autre":
        lignes.append(t("usage.heros.surtout", modele=MODELES[tot.modele].upper()))
    if tot.jour_actif >= 0:
        lignes.append(t("usage.heros.jour_charge", jour=t(JOURS[tot.jour_actif]).upper()))
    if tot.invites_jour:
        lignes.append(tn("usage.heros.invites", tot.invites_jour))
    if tot.sessions_jour:
        lignes.append(tn("usage.heros.sessions", tot.sessions_jour))
    return lignes


def probleme(etat):
    """(texte, geste) d'une mesure qui n'a pas abouti ; geste : « connexion » ou None."""
    cle, geste = {
        "refuse": ("usage.pb.refuse", "connexion"),
        "expire": ("usage.pb.expire", "connexion"),
        "en_pause": ("usage.pb.en_pause", None),
        "limite": ("usage.pb.limite", None),
        "injoignable": ("usage.pb.injoignable", None),
        "erreur": ("usage.pb.erreur", None),
    }.get(etat, (None, None))
    return (t(cle) if cle else None), geste


def infobulle_icone(allume, m, maintenant):
    """(titre, texte) de l'infobulle de l'icône dans la barre."""
    if not allume:
        return (t("usage.icone.arrete"), t("usage.icone.gestes"))
    texte = t("usage.icone.pret")
    if actif_de(m) not in ("claude", None):
        texte += t("usage.icone.fournisseur", nom=nom_fournisseur(m.actif))
        if sante_affichee(m):
            texte += " · " + t(sante_affichee(m))
        return (t("usage.icone.titre"), texte + "\n" + t("usage.icone.gestes"))
    f = fenetre_liante(m, maintenant)
    if f is not None:
        texte += t("usage.icone.claude", pc=pourcent_affiche(f, maintenant), fenetre=t(LIMITES[f.nom]))
    elif m is not None and m.etat == "sans_jeton":
        texte += t("usage.icone.sans_compte")
    return (t("usage.icone.titre"), texte + "\n" + t("usage.icone.gestes"))


# --- rythme -----------------------------------------------------------------------

def doit_mesurer(dernier, maintenant, force=False, ouverte=False, liante=None,
                 etat=None, reessai_apres=None):
    """Faut-il appeler qaf.Usage maintenant ? dernier : heure du dernier appel
    (réussi ou non), None s'il n'y en a pas eu. Jamais plus d'un appel par
    15 s, même forcé."""
    if dernier is None:
        return True
    ecart = maintenant - dernier
    if ecart < REUTILISATION_S:
        return False
    if force or ouverte:
        return True
    if etat == "limite":
        return ecart >= (reessai_apres or REESSAI_LIMITE_S)
    proche = liante is not None and liante >= SEUIL_PROCHE
    return ecart >= (INTERVALLE_PROCHE_S if proche else INTERVALLE_S)


# --- cache de dom0 ($XDG_STATE_HOME/qaf/usage.json) ------------------------------------------

def chemin_cache(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME") or os.path.join(environ.get("HOME") or "/", ".local", "state")
    return os.path.join(base, "qaf", "usage.json")


def cache_ecrire(m, heure, chemin=None):
    """Dernière mesure VALIDÉE et son heure ; écriture atomique, 0600."""
    chemin = chemin or chemin_cache()
    try:
        os.makedirs(os.path.dirname(chemin), mode=0o700, exist_ok=True)
        tmp = chemin + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"heure": int(heure), "mesure": vers_dict(m)}, f)
        os.replace(tmp, chemin)
        return True
    except OSError:
        return False


def cache_lire(chemin=None):
    """(Mesure, heure) ou (None, None). Revalidée comme à la réception (bornes
    des remises prises à l'heure de la mesure)."""
    chemin = chemin or chemin_cache()
    try:
        with open(chemin, "rb") as f:
            brut = json.loads(f.read(4 * MAX_OCTETS).decode("utf-8"))
        heure = brut["heure"]
        if not _entier(heure, 0, 2 ** 40):
            return None, None
        m = lire_mesure(json.dumps(brut["mesure"]).encode(), heure)
    except Exception:  # noqa: BLE001
        return None, None
    return (m, heure) if m is not None else (None, None)


def appeler(ouvrir=None, delai=DELAI_S):
    """Lance qaf.Usage (argv fixe) et rend (code, octets bornés). Bloquant :
    hors du fil GTK. Code None : qvm-run absent ou délai dépassé."""
    return lire_borne(ARGV_USAGE, MAX_OCTETS, delai, ouvrir)
