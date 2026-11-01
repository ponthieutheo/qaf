"""Fournisseurs de l'agent, côté sys-agent : fournisseur actif, réglages 0600,
lancement, état, santé, installation à la demande, déclaration du serveur MCP qaf.
Table : qaf_mcp/fournisseurs_table.py. Service qrexec qaf.Fournisseur+ID : main().

Tout tient sous ~/.config/qaf/fournisseurs/ (dossiers 0700, fichiers 0600) :
  actif, connexion, lancements.json
  <id>/settings.json, <id>/cle, <id>/config.json, <id>/test.json
~/.claude/settings.json n'est jamais touché, jamais de /logout. Le retrait complet
(retirer_tout) ne garde que lancements.json s'il a servi : sans lui, Claude reprendrait
par --continue la conversation d'un tiers et compterait ses tours dans ses totaux.

Le fournisseur actif n'est qu'un choix d'affichage et de lancement : sys-agent est
influencé en permanence, l'agent peut réécrire ces fichiers, et il n'y gagne aucun
droit (le Doorman reste seul juge). Un id illisible, ou un fournisseur qui n'est
plus activable, retombe sur Claude.

FUITE À EMPÊCHER (prouvée par essai) : avec une connexion OAuth présente et une base
URL tierce sans identifiant explicite, Claude Code envoie le jeton OAuth chez le
tiers. D'où : aucun lancement vers un tiers sans reglages_valides(), qui exige un
apiKeyHelper ou un jeton explicite, ANTHROPIC_API_KEY vide et un dossier de travail
approuvé (sans quoi claude n'exécute pas le helper). Les variables d'identifiants
héritées sont retirées de l'environnement. Cela protège l'ENVOI automatique du jeton,
pas sa LECTURE : une CLI tierce ou un Claude Code branché sur un tiers tourne sous le
même utilisateur, et l'agent peut lire ~/.claude si on l'y pousse, puis en renvoyer le
contenu au fournisseur. Mémoire partagée oblige : c'est une limite documentée.

Une clé n'est jamais dans un argv ni dans dom0. Bibliothèque standard seulement.
"""

import bisect
import glob
import http.client
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from urllib.parse import quote

from .fournisseurs_table import (COMMANDE_MCP, GEL_DEPENDANCES, IDS, IDS_HORS_CLAUDE, INTERDITS,  # noqa: F401
                                 LANCEUR, NODE, NODE_VERSION, PAR_ID, UV)

RACINE = (".config", "qaf", "fournisseurs")
DEMANDE = (".config", "qaf", "demande")
PROFIL = ".claude.json"
DOSSIER_AGENT = "agent"
SH = "/bin/sh"
# Installations : no_new_privs, donc sudo refuse même appelé par son chemin absolu.
SANS_PRIVILEGE = ("/usr/bin/setpriv", "--no-new-privs")
CLAUDE = PAR_ID["claude"].binaires[0]

JETON_REMPLISSAGE = "qaf-local"  # serveur sans clé : jamais le jeton OAuth (FUITE, voir plus haut)
MAX_FICHIER = 8 * 1024 * 1024
MAX_REGLAGES = 256 * 1024
MAX_TELECHARGE = 1024 * 1024 * 1024
TENTATIVES = 6
PAUSE_REPRISE = 5
# npm : un seul paquet en panne fait tout échouer (vu sur un lien chargé, passé avec ceci).
ENV_NPM = (("npm_config_maxsockets", "3"), ("npm_config_fetch_retries", "8"), ("npm_config_fetch_timeout", "600000"))
MAX_LANCEMENTS = 64
DELAI_SANTE = 2
DELAI_RESEAU = 15
ETATS = ("connecte", "deconnecte", "non_installe", "connexion_seule")
SANTES = ("ok", "injoignable", "cle_refusee", "credit_epuise", "inconnue")
CONNEXIONS = ("ok", "echec", "")
ID_MODELE = re.compile(r"\A[A-Za-z0-9._:/~@\[\]+-]{1,128}\Z")
# Identifiants qui ne doivent jamais suivre un lancement hors Claude d'origine.
ENV_RETIRE = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
              "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CONFIG_DIR", "QAF_REQUEST")
# QAF_REQUEST y figure parce que demande.py la pose lui-même sur la commande.
ENV_ANTHROPIC = {
    "local": (("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"), ("ENABLE_TOOL_SEARCH", "false"),
              ("API_TIMEOUT_MS", "1800000")),
}
AFFICHE = 'printf "%s\\n\\n" "$1"; shift; exec "$@"'
DEBUT, FIN = "# >>> qaf (posé par QAF, retiré avec le fournisseur)", "# <<< qaf"


class Refus(Exception):
    """Refus expliqué : args[0] = clé de texte de l'assistant."""


# --- fichiers -----------------------------------------------------------------------

def racine(maison):
    return os.path.join(maison, *RACINE)


def dossier(maison, id):
    return os.path.join(racine(maison), id)


def _creer_dossier(d):
    """Chaque niveau manquant en 0700 (os.makedirs ne l'applique qu'au dernier)."""
    if not os.path.isdir(d):
        _creer_dossier(os.path.dirname(d))
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            pass


def _ecrire(chemin, texte, mode=0o600):
    """Écriture atomique ; 0600 par défaut, jamais de fenêtre plus ouverte. Un dossier
    de fournisseur déjà là est resserré ; les dossiers des CLI ne sont pas à nous."""
    d = os.path.dirname(chemin)
    _creer_dossier(d)
    if os.path.join(*RACINE) in d:
        os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".qaf-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            f.write(texte)
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lire(chemin, plafond=MAX_FICHIER):
    try:
        with open(chemin, "rb") as f:
            brut = f.read(plafond + 1)
    except OSError:
        return None
    return None if len(brut) > plafond else brut


def _lire_json(chemin, plafond=MAX_FICHIER):
    brut = _lire(chemin, plafond)
    if brut is None:
        return None
    try:
        return json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def _dans(obj, cles):
    for c in cles:
        if not isinstance(obj, dict) or c not in obj:
            return None
        obj = obj[c]
    return obj


# --- fournisseur actif --------------------------------------------------------------

def lire_actif(maison):
    brut = _lire(os.path.join(racine(maison), "actif"), 64)
    try:
        v = brut.decode("ascii").strip() if brut else ""
    except UnicodeDecodeError:
        return "claude"
    return v if v in IDS else "claude"


def ecrire_actif(maison, id):
    if id not in IDS:
        raise ValueError(id)
    _ecrire(os.path.join(racine(maison), "actif"), id + "\n")


def actif_effectif(maison, existe=os.path.exists):
    """Le fournisseur qui sera vraiment lancé : l'actif s'il est encore activable,
    sinon Claude, chemin d'origine."""
    a = lire_actif(maison)
    return a if a != "claude" and activable(a, maison, existe) else "claude"


def activable(id, maison, existe=os.path.exists):
    f = PAR_ID.get(id)
    if f is None or f.famille == "connexion_seule":
        return False
    if id == "claude":
        return True  # le retour à Claude ne se refuse jamais
    if f.famille in ("cle", "local"):
        return reglages_valides(id, maison) and test_valide(id, maison) and serveur_claude(maison)
    return binaire(f, maison, existe) is not None and preuve(f, maison, existe) and mcp_configure(f, maison)


def agents(maison, existe=os.path.exists):
    """Ids dont le serveur MCP qaf est configuré, donc activables."""
    return [i for i in IDS if activable(i, maison, existe)]


def etat(id, maison, existe=os.path.exists):
    f = PAR_ID[id]
    if f.famille == "connexion_seule":
        if f.install is None:
            return "connexion_seule"
        if binaire(f, maison, existe) is None:
            return "non_installe"
        return "connexion_seule" if preuve(f, maison, existe) else "deconnecte"
    if f.famille in ("cle", "local"):
        return "connecte" if reglages_valides(id, maison) and test_valide(id, maison) else "deconnecte"
    if binaire(f, maison, existe) is None:
        return "non_installe"
    return "connecte" if preuve(f, maison, existe) else "deconnecte"


def etats(maison, existe=os.path.exists):
    return {i: etat(i, maison, existe) for i in IDS_HORS_CLAUDE}


def binaire(f, maison, existe=os.path.exists):
    for b in f.binaires:
        p = b.replace("~", maison, 1)
        if existe(p):
            return p
    return None


def preuve(f, maison, existe=os.path.exists):
    """Connexion prouvée sans jamais rendre le secret."""
    for p in f.preuves:
        chemin = os.path.join(maison, p.chemin)
        if p.genre == "fichier" and existe(chemin):
            return True
        if p.genre == "motif" and glob.glob(chemin):
            return True
        if p.genre == "ligne":
            brut = _lire(chemin, 256 * 1024) or b""
            for ligne in brut.splitlines():
                nom, _, val = ligne.partition(b"=")
                if nom.strip().decode("ascii", "replace") == p.cle[0] and val.strip():
                    return True
        if p.genre == "json" and _dans(_lire_json(chemin), p.cle):
            return True
        if p.genre == "json_non_vide" and _objet_non_vide(_lire_json(chemin)):
            return True
        if p.genre == "sqlite" and _table_non_vide(chemin, p.cle[0]):
            return True
        if p.genre == "texte" and re.search(p.cle[0].encode(), _lire(chemin, 256 * 1024) or b"", re.M):
            return True
    return False


def _objet_non_vide(d):
    return isinstance(d, dict) and bool(d)


def _table_non_vide(chemin, table):
    """Au moins une ligne dans cette table : lecture seule, aucune valeur rendue. Une base
    verrouillée, abîmée ou sans cette table n'est pas une preuve."""
    if not os.path.isfile(chemin):
        return False
    try:
        import sqlite3
    except ImportError:
        return False
    try:
        c = sqlite3.connect("file:" + quote(chemin) + "?mode=ro", uri=True, timeout=0.2)
        try:
            return c.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
        finally:
            c.close()
    except (sqlite3.Error, OSError, ValueError):
        return False


def serveur_claude(maison):
    s = _dans(_lire_json(os.path.join(maison, PROFIL)), ("mcpServers", "qaf"))
    return isinstance(s, dict)


def dossier_agent(maison):
    return os.path.join(maison, DOSSIER_AGENT)


def dossier_approuve(maison):
    """claude n'exécute un apiKeyHelper que dans un dossier approuvé."""
    p = _dans(_lire_json(os.path.join(maison, PROFIL)), ("projects", dossier_agent(maison), "hasTrustDialogAccepted"))
    return p is True and os.path.isdir(dossier_agent(maison))


# --- réglages des lancements vers un tiers ------------------------------------------

def chemin_settings(maison, id):
    return os.path.join(dossier(maison, id), "settings.json")


def chemin_cle(maison, id):
    return os.path.join(dossier(maison, id), "cle")


def ecrire_cle(maison, id, cle):
    if not cle or len(cle) > 4096 or not all("!" <= c <= "~" for c in cle):
        raise Refus("cle_invalide")
    _ecrire(chemin_cle(maison, id), cle + "\n")


def lire_cle(maison, id):
    """Clé du fournisseur, pour l'en-tête d'un appel de contrôle ; jamais affichée."""
    brut = _lire(chemin_cle(maison, id), 8192)
    try:
        return brut.decode("ascii").strip() if brut else ""
    except UnicodeDecodeError:
        return ""


def nettoyer_url(url):
    """Adresse de base : http(s), sans /v1 final (Claude Code l'ajoute), sans identifiants."""
    url = url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    m = re.fullmatch(r"(https?)://([A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?", url)
    if not m:
        raise Refus("adresse_invalide")
    if m.group(3) and not 1 <= int(m.group(3)[1:]) <= 65535:
        raise Refus("adresse_invalide")
    return url


def adresse_en_clair(url):
    hote = re.sub(r"\Ahttps?://", "", url).split("/")[0].rsplit(":", 1)[0]
    return url.startswith("http://") and hote not in ("127.0.0.1", "localhost", "[::1]")


# --- qube voisin : tunnel vers qubes.ConnectTCP -------------------------------------------
# L'autorisation se pose dans dom0 (qaf-voisin) : ici, rien n'ouvre le passage. Sans elle,
# le tunnel écoute et chaque connexion revient vide. Écoute sur 127.0.0.1 (qvm-connect-tcp
# écouterait sur 0.0.0.0), service persistant : forme essayée pour de vrai. « @default »
# laisse dom0 seul choisir le qube ; le tunnel ne le nomme pas, et le socket seul ne
# réveille rien : qrexec n'est appelé qu'à une connexion.

UNITE_VOISIN = "qaf-voisin.service"
_ADRESSE_VOISIN = re.compile(r"\A\s*(?:voisin|neighbou?r|相邻)\s+([1-9][0-9]{0,4})\s*\Z")


def port_voisin(brut):
    """« voisin 11434 » -> 11434 ; autre chose (une adresse) -> None."""
    m = _ADRESSE_VOISIN.match(brut)
    if m is None:
        return None
    if int(m.group(1)) > 65535:
        raise Refus("adresse_invalide")
    return int(m.group(1))


def chemin_unite(maison):
    return os.path.join(maison, ".config", "systemd", "user", UNITE_VOISIN)


def texte_unite(port):
    port = int(port)
    return ("[Unit]\nDescription=QAF : tunnel vers le serveur du qube voisin\n\n[Service]\n"
            f"ExecStart=/usr/bin/socat TCP-LISTEN:{port},bind=127.0.0.1,reuseaddr,fork "
            f'EXEC:"/usr/bin/qrexec-client-vm @default qubes.ConnectTCP+{port}"\n'
            "Restart=on-failure\nRestartSec=2\n\n[Install]\nWantedBy=default.target\n")


def _systemctl(executer, *args):
    try:
        executer(["systemctl", "--user", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def port_tunnel(maison):
    """Port du tunnel en place, ou None."""
    try:
        with open(chemin_unite(maison), encoding="utf-8") as f:
            m = re.search(r"TCP-LISTEN:([0-9]+),", f.read(4096))
    except (OSError, ValueError):
        return None
    return int(m.group(1)) if m else None


def poser_tunnel(maison, port, executer=subprocess.run):
    _ecrire(chemin_unite(maison), texte_unite(port), 0o644)
    _systemctl(executer, "daemon-reload")
    _systemctl(executer, "enable", UNITE_VOISIN)
    _systemctl(executer, "restart", UNITE_VOISIN)


def retirer_tunnel(maison, executer=subprocess.run):
    if not os.path.lexists(chemin_unite(maison)):
        return
    _systemctl(executer, "disable", "--now", UNITE_VOISIN)
    os.unlink(chemin_unite(maison))
    _systemctl(executer, "daemon-reload")


def generer_settings(maison, id, base, modele, cle=False, contexte=0, essai=False, lieu="reseau"):
    """Écrit <id>/settings.json et <id>/config.json (0600). cle : une clé a été
    enregistrée (apiKeyHelper) ; sinon, pour le local seulement, jeton de
    remplissage : jamais de lancement tiers sans identifiant explicite."""
    f = PAR_ID[id]
    if f.famille not in ("cle", "local") or not ID_MODELE.match(modele):
        raise Refus("reglage_invalide")
    if not cle and f.famille != "local":
        raise Refus("cle_invalide")
    base = nettoyer_url(base)
    env = {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": modele,
           "ANTHROPIC_DEFAULT_OPUS_MODEL": modele, "ANTHROPIC_DEFAULT_SONNET_MODEL": modele,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": modele, "CLAUDE_CODE_SUBAGENT_MODEL": modele,
           "DISABLE_AUTOUPDATER": "1", **dict(ENV_ANTHROPIC.get(f.famille, ())), **dict(f.env)}
    reglages = {"env": env}
    if cle:
        reglages["apiKeyHelper"] = "cat " + shlex.quote(chemin_cle(maison, id))
    else:
        env["ANTHROPIC_AUTH_TOKEN"] = JETON_REMPLISSAGE
        try:
            os.unlink(chemin_cle(maison, id))
        except OSError:
            pass
    if f.famille == "local":
        reglages["skipWebFetchPreflight"] = True
        if contexte:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(int(contexte))
    _ecrire(chemin_settings(maison, id), json.dumps(reglages, indent=1) + "\n")
    _ecrire(os.path.join(dossier(maison, id), "config.json"),
            json.dumps({"base": base, "modele": modele, "essai": bool(essai), "lieu": lieu}) + "\n")


def config(maison, id):
    c = _lire_json(os.path.join(dossier(maison, id), "config.json"), 4096)
    return c if isinstance(c, dict) else {}


def reglages_valides(id, maison):
    """Vrai seulement si le lancement ne peut pas emporter le jeton OAuth : fichier
    régulier 0600, base URL, ANTHROPIC_API_KEY vide, et un identifiant explicite
    (apiKeyHelper dont le fichier existe, ou jeton non vide). Échec fermé."""
    chemin = chemin_settings(maison, id)
    try:
        st = os.lstat(chemin)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
        return False
    r = _lire_json(chemin, MAX_REGLAGES)
    env = r.get("env") if isinstance(r, dict) else None
    if not isinstance(env, dict) or not env.get("ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_API_KEY") != "":
        return False
    aide, jeton = r.get("apiKeyHelper"), env.get("ANTHROPIC_AUTH_TOKEN")
    if isinstance(jeton, str) and jeton:
        return True
    return isinstance(aide, str) and bool(aide) and bool(lire_cle(maison, id)) and dossier_approuve(maison)


def noter_test(maison, id, base, modele):
    _ecrire(os.path.join(dossier(maison, id), "test.json"), json.dumps({"ok": True, "base": base, "modele": modele}))


def test_valide(id, maison):
    """L'aller-retour avec outil a réussi pour CETTE adresse et CE modèle."""
    t = _lire_json(os.path.join(dossier(maison, id), "test.json"), 4096)
    c = config(maison, id)
    return isinstance(t, dict) and t.get("ok") is True and bool(c) \
        and (t.get("base"), t.get("modele")) == (c.get("base"), c.get("modele"))


# --- lancement ----------------------------------------------------------------------

class Lancement:
    """argv du CLI, paires VAR=valeur (sans secret), variables à retirer."""

    def __init__(self, argv, env=(), retire=ENV_RETIRE):
        self.argv, self.env, self.retire = list(argv), list(env), tuple(retire)


def lancement(id, maison, texte=None, suite=False, chemin=None):
    """Lancement de `id`, hors Claude d'origine. Refus (exception) si le fournisseur
    ne peut pas être lancé sans risque : aucun lancement tiers sans identifiant."""
    f = PAR_ID.get(id)
    if f is None or id == "claude" or f.famille == "connexion_seule":
        raise Refus("non_activable")
    if f.famille in ("cle", "local"):
        if not reglages_valides(id, maison):
            raise Refus("non_activable")
        argv = [CLAUDE, "--settings", chemin_settings(maison, id), *(("--continue",) if suite else ())]
        return Lancement(argv + (["--", texte] if texte is not None else []))
    b = binaire(f, maison)
    if b is None or not mcp_configure(f, maison):
        raise Refus("non_activable")
    lc = f.lancer
    argv = [b, *lc.args, *(lc.suite if suite and not (lc.suite_sans_texte and texte is not None) else ())]
    if texte is not None:
        if lc.texte == "tiret":
            argv += ["--", texte]
        elif lc.texte == "egal":
            argv.append(lc.option + texte)
        elif lc.texte == "option":  # l'espace : le texte ne peut pas être pris pour une option
            argv += [lc.option, " " + texte]
        elif lc.texte == "seul":
            argv.append(" " + texte)
        else:
            argv = [SH, "-c", AFFICHE, "qaf-agent", texte, *argv]
    env = [f"PATH={chemin_de(f, maison)}:{chemin or '/usr/local/bin:/usr/bin:/bin'}", *(f"{k}={v}" for k, v in lc.env)]
    return Lancement(argv, env, ENV_RETIRE + lc.retire)


def dossier_vide(maison, id):
    """Dossier de lancement sans rien à charger, pour une CLI qui n'a pas de validation."""
    d = os.path.join(dossier(maison, id), "vide")
    _creer_dossier(d)
    return d


def chemin_de(f, maison):
    """Dossiers à mettre en tête du PATH pour cette CLI : ~/.local/bin, précédé du Node
    privé pour celle qui en a besoin."""
    local = os.path.join(maison, ".local", "bin")
    if f.install is not None and f.install.node_prive:
        return os.path.join(dossier_node(maison), "bin") + ":" + local
    return local


def famille_conversation(id, maison):
    """Une conversation ne se reprend qu'entre modèles de la même famille. OpenRouter :
    un modèle Anthropic et un modèle à l'essai ne sont pas de la même famille."""
    if id == "openrouter" and config(maison, id).get("essai"):
        return "openrouter:essai"
    return id


def lancements(maison):
    """[[heure, famille], …] triés ; le dernier dit la famille de la conversation."""
    j = _lire_json(os.path.join(racine(maison), "lancements.json"), 64 * 1024)
    if not isinstance(j, list):
        return []
    out = [x for x in j if isinstance(x, list) and len(x) == 2 and type(x[0]) is int and isinstance(x[1], str)]
    return sorted(out, key=lambda x: x[0])


def noter_lancement(maison, id, heure):
    """Noté dans sys-agent à chaque lancement : la conversation suivante, et les
    totaux Claude, en dépendent. Rien n'est écrit tant que Claude seul a tourné."""
    fam = famille_conversation(id, maison)
    deja = lancements(maison)
    if not deja and fam == "claude":
        return
    if deja and deja[-1][1] == fam:
        return
    try:
        _ecrire(os.path.join(racine(maison), "lancements.json"),
                json.dumps((deja + [[int(heure), fam]])[-MAX_LANCEMENTS:]))
    except OSError:
        pass


def reprise(maison, id, conversation):
    """Faut-il reprendre la conversation ? Oui seulement dans la même famille ;
    après un changement, nouvelle conversation, l'ancienne reste sur disque."""
    deja = lancements(maison)
    if (deja[-1][1] if deja else "claude") != famille_conversation(id, maison):
        return False
    f = PAR_ID[id]
    return bool(conversation) if f.famille in ("cle", "local") or id == "claude" else bool(f.lancer.suite)


def periodes_tiers(maison):
    """(heures, familles) pour tour_tiers : lecture une fois par mesure."""
    deja = lancements(maison)
    return [x[0] for x in deja], [x[1] for x in deja]


def tour_tiers(periodes, heure):
    """Vrai si l'heure tombe après un lancement vers un autre fournisseur que Claude."""
    heures, familles = periodes
    i = bisect.bisect_right(heures, heure) - 1
    return i >= 0 and familles[i] != "claude"


# --- demande courante (lanceur MCP) --------------------------------------------------

def ecrire_demande(maison, rid):
    _ecrire(os.path.join(maison, *DEMANDE), rid + "\n")


def effacer_demande(maison):
    try:
        os.unlink(os.path.join(maison, *DEMANDE))
    except OSError:
        pass


# --- résultat du dernier assistant --------------------------------------------------

def lire_connexion(maison):
    brut = _lire(os.path.join(racine(maison), "connexion"), 16)
    v = brut.decode("ascii", "replace").strip() if brut else ""
    return v if v in CONNEXIONS else ""


def noter_connexion(maison, resultat):
    _ecrire(os.path.join(racine(maison), "connexion"), resultat + "\n")


def effacer_connexion(maison):
    try:
        os.unlink(os.path.join(racine(maison), "connexion"))
    except OSError:
        pass


# --- santé du fournisseur actif ------------------------------------------------------

class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def entetes(cle):
    return {"Authorization": f"Bearer {cle}", "x-api-key": cle, "anthropic-version": "2023-06-01",
            "User-Agent": "qaf", "Accept": "application/json"}


def appeler(url, cle="", delai=DELAI_RESEAU, corps=None, ouvrir=None, plafond=MAX_FICHIER):
    """(code, octets). Code 0 : injoignable. Sans redirection : une clé ne suit
    jamais un 30x vers un autre hôte."""
    ouvrir = ouvrir or urllib.request.build_opener(_SansRedirection).open
    h = entetes(cle) if cle else {"User-Agent": "qaf", "Accept": "application/json"}
    if corps is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=corps, headers=h)
    try:
        with ouvrir(req, timeout=delai) as r:
            return r.status, r.read(plafond + 1)[:plafond]
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(64 * 1024)
        except (OSError, http.client.HTTPException):
            return e.code, b""
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return 0, b""  # un service qui ne parle pas HTTP n'est qu'injoignable


def sante(id, maison, ouvrir=None):
    """Énumération pour la carte. Sonde de 2 s au plus, sans dépense ; jamais pour une
    CLI ni pour un lieu autre que le réseau (une sonde réveillerait un qube voisin).
    Le délai d'une socket ne couvre ni le DNS ni un serveur qui goutte : fil à part."""
    f = PAR_ID.get(id)
    if f is None or f.famille not in ("cle", "local"):
        return "inconnue"
    resultat = []
    fil = threading.Thread(target=lambda: resultat.append(_sonde(id, maison, ouvrir)), daemon=True)
    fil.start()
    fil.join(DELAI_SANTE)
    return resultat[0] if resultat else "injoignable"


def _sonde(id, maison, ouvrir):
    f = PAR_ID[id]
    c = config(maison, id)
    base = c.get("base")
    if f.famille == "local" and c.get("lieu", "reseau") != "reseau":
        return "inconnue"
    if not isinstance(base, str) or not base:
        return "inconnue"
    code, corps = appeler(f.verif_url or base + "/v1/models", lire_cle(maison, id) or JETON_REMPLISSAGE,
                          DELAI_SANTE, ouvrir=ouvrir, plafond=64 * 1024)
    if code == 0:
        return "injoignable"
    if code in (401, 403):
        return "cle_refusee"
    if code == 402:
        return "credit_epuise"
    if code == 200:
        if f.verif_url:
            try:
                d = json.loads(corps.decode("utf-8")).get("data")
                reste = d.get("limit_remaining") if isinstance(d, dict) else None
            except (UnicodeDecodeError, ValueError, AttributeError, RecursionError):
                reste = None
            if type(reste) in (int, float) and reste <= 0:
                return "credit_epuise"
        return "ok"
    return "inconnue"


# --- déclaration du serveur MCP qaf ----------------------------------------------------

def entree_mcp(f):
    cmd, *args = COMMANDE_MCP
    base = {"command": list(COMMANDE_MCP)} if f.mcp.commande_liste else {"command": cmd, "args": args}
    return {**f.mcp.entree, **base}


def _notre(e):
    return LANCEUR in json.dumps(e)


def _toml_valeur(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_valeur(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_valeur(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def _toml(chemin):
    brut = _lire(chemin)
    if brut is None:
        return "" if not os.path.exists(chemin) else None, {}
    try:
        t = brut.decode("utf-8")
        return t, tomllib.loads(t)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, {}


def _toml_entree(f, d):
    if f.id == "vibe":
        l = d.get("mcp_servers")
        return next((e for e in l if isinstance(e, dict) and e.get("name") == "qaf"), None) if isinstance(l, list) else None
    return _dans(d, f.mcp.cles)


def mcp_declare(f, maison):
    """Vrai si le fichier de la CLI déclare bien NOTRE serveur."""
    if f.mcp is None:
        return False
    chemin = os.path.join(maison, f.mcp.chemin)
    if f.mcp.format == "json":
        return _notre(_dans(_lire_json(chemin), f.mcp.cles))
    if f.mcp.format == "toml":
        t, d = _toml(chemin)
        return t is not None and _notre(_toml_entree(f, d))
    brut = _lire(chemin) or b""
    if f.mcp.format == "cordis":
        return LANCEUR.encode() in brut and DEBUT.encode() in brut
    return LANCEUR.encode() in brut and re.search(rb"^\s+qaf:", brut, re.M) is not None


def _exige_tenu(maison, fichier, cles, valeur):
    return _dans(_lire_json(_fichier_utilisateur(os.path.join(maison, fichier))), cles) == valeur


def _present(obj, cles):
    """Le chemin existe ; « * » vaut pour tout membre d'un objet."""
    if not cles:
        return True
    if not isinstance(obj, dict):
        return False
    if cles[0] == "*":
        return any(_present(v, cles[1:]) for v in obj.values())
    return cles[0] in obj and _present(obj[cles[0]], cles[1:])


def _refus_present(maison, fichier, cles):
    """Le fichier défait la validation, ou on ne peut pas dire qu'il ne la défait pas :
    illisible, commentaires compris, vaut présence. Absent ou vide : rien."""
    chemin = _fichier_utilisateur(os.path.join(maison, fichier))
    if not os.path.exists(chemin):
        return False
    d = _json_a_completer(chemin)
    return not isinstance(d, dict) or _present(d, cles)


def mcp_configure(f, maison):
    """Déclaré, et chaque réglage qui tient lieu de validation est bien là : c'est ce qui
    sépare une CLI qui demande d'une CLI qui laisse tout passer. La déclaration seule ne
    suffit pas à devenir l'agent. Garde-fou contre la dérive, pas une barrière : l'agent
    écrit dans ces fichiers, le contrôle ne vaut qu'au moment où il tourne."""
    return mcp_declare(f, maison) and all(_exige_tenu(maison, *e) for e in f.mcp.exige) \
        and not any(_refus_present(maison, *r) for r in f.mcp.refuse) and not _absent_present(f, maison)


def _absent_present(f, maison):
    return any(os.path.lexists(os.path.join(maison, x)) for x in f.mcp.absents)


def _fichier_utilisateur(chemin):
    return os.path.realpath(chemin)


def _mode(chemin):
    try:
        return stat.S_IMODE(os.stat(chemin).st_mode)
    except OSError:
        return 0o600


def _json_a_completer(chemin):
    """Document à compléter : absent ou vide = {} (agy crée son fichier vide au premier
    lancement, donc avant la pose). Rien d'autre n'est deviné : illisible = None."""
    brut = _lire(chemin)
    if brut is None and not os.path.exists(chemin):
        return {}
    return {} if brut is not None and not brut.strip() else _lire_json(chemin)


def _poser_json(chemin, cles, entree):
    d = _json_a_completer(chemin)
    if not isinstance(d, dict):
        raise Refus("config_illisible")
    o = d
    for c in cles[:-1]:
        o = o.setdefault(c, {})
        if not isinstance(o, dict):
            raise Refus("config_illisible")
    deja = o.get(cles[-1])
    if deja is not None and not _notre(deja):
        raise Refus("config_autre_qaf")
    o[cles[-1]] = entree
    _ecrire(chemin, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(chemin))


def _retirer_json(chemin, cles):
    d = _lire_json(chemin)
    o = _dans(d, cles[:-1]) if isinstance(d, dict) else None
    if isinstance(o, dict) and _notre(o.get(cles[-1])):
        del o[cles[-1]]
        _ecrire(chemin, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(chemin))


def _poser_toml(f, chemin):
    t, d = _toml(chemin)
    if t is None:
        raise Refus("config_illisible")
    deja = _toml_entree(f, d)
    if deja is not None:
        if _notre(deja):
            return
        raise Refus("config_autre_qaf")
    e = entree_mcp(f)
    titre = "[[mcp_servers]]" if f.id == "vibe" else "[" + ".".join(f.mcp.cles) + "]"
    lignes = [DEBUT, titre, *(f"{k} = {_toml_valeur(v)}" for k, v in e.items()), FIN]
    nouveau = t + ("" if not t or t.endswith("\n") else "\n") + "\n".join(lignes) + "\n"
    try:
        if not _notre(_toml_entree(f, tomllib.loads(nouveau))):
            raise Refus("config_illisible")
    except tomllib.TOMLDecodeError:
        raise Refus("config_illisible") from None
    _ecrire(chemin, nouveau, _mode(chemin))


def _retirer_bloc(chemin):
    brut = _lire(chemin)
    if brut is None:
        return
    t = brut.decode("utf-8", "replace")
    n = re.sub(r"^[ \t]*" + re.escape(DEBUT) + r"\n.*?^[ \t]*" + re.escape(FIN) + r"\n", "", t, flags=re.M | re.S)
    if n != t:
        _ecrire(chemin, n, _mode(chemin))


def _poser_yaml(f, chemin):
    brut = _lire(chemin)
    t = brut.decode("utf-8", "replace") if brut is not None else ""
    if DEBUT in t:
        return
    if re.search(r"^mcp_servers:(?!\s*(#.*)?$)", t, re.M):
        raise Refus("config_illisible")
    m = re.search(r"^mcp_servers:[ \t]*(#.*)?\n?", t, re.M)
    cmd, *args = COMMANDE_MCP
    env = f.mcp.entree.get("env", {})

    def bloc(i):
        lignes = [f"{i}qaf:", f"{i}{i}command: {json.dumps(cmd)}", f"{i}{i}args: {json.dumps(args)}", f"{i}{i}env:"]
        return lignes + [f"{i}{i}{i}{k}: {json.dumps(v)}" for k, v in env.items()]

    if m:
        suite = next((x for x in t[m.end():].splitlines() if x.strip() and not x.lstrip().startswith("#")), "")
        n = len(suite) - len(suite.lstrip(" "))
        i = " " * (n or 2)
        if re.search(r"^\s*qaf:", t[m.end():], re.M):
            raise Refus("config_autre_qaf")
        insere = "\n".join([i + DEBUT, *bloc(i), i + FIN]) + "\n"
        tete = t[:m.end()] + ("" if t[:m.end()].endswith("\n") else "\n")
        nouveau = tete + insere + t[m.end():]
    else:
        nouveau = t + ("" if not t or t.endswith("\n") else "\n") + \
            "\n".join([DEBUT, "mcp_servers:", *bloc("  "), FIN]) + "\n"
    _ecrire(chemin, nouveau, _mode(chemin))


def _texte_cordis(f):
    """Patch dsh : l'ajout du serveur, puis une ligne par plugin du socle à désactiver."""
    cmd, *args = COMMANDE_MCP
    lignes = [DEBUT, "- insert:", "    - id: qaf", "      name: '@deepseek-ai/dsh-mcp-client'", "      config:",
              "        serverName: qaf", "        transport: stdio", f"        command: {json.dumps(cmd)}",
              f"        args: {json.dumps(args)}"]
    for ligne in f.mcp.couper:
        lignes += [f"- id: {ligne}", "  disabled: true"]
    return "\n".join([*lignes, FIN]) + "\n"


def _poser_cordis(f, chemin):
    """Un patch est une liste YAML : le bloc s'ajoute à la fin. Toute autre forme, ou un
    autre serveur qaf : refus. Rien n'est analysé d'autre."""
    brut = _lire(chemin)
    t = brut.decode("utf-8", "replace") if brut is not None else ""
    if DEBUT in t:
        return
    utile = [x for x in t.splitlines() if x.strip() and not x.lstrip().startswith("#")]
    if utile and not utile[0].startswith("- "):
        raise Refus("config_illisible")
    if re.search(r"^\s*-?\s*id:\s*[\"']?qaf[\"']?\s*$", t, re.M):
        raise Refus("config_autre_qaf")
    _ecrire(chemin, t + ("" if not t or t.endswith("\n") else "\n") + _texte_cordis(f), _mode(chemin))


def _exige_contraire(maison, fichier, cles, valeur):
    """Un autre choix est déjà écrit à cet endroit. QAF ne l'écrase pas, mais il ne laisse
    pas croire que la CLI est prête."""
    d = _json_a_completer(_fichier_utilisateur(os.path.join(maison, fichier)))
    if not isinstance(d, dict):
        return False  # illisible : _verifier_cles refuse juste après
    parent = _dans(d, cles[:-1])
    return isinstance(parent, dict) and cles[-1] in parent and parent[cles[-1]] != valeur


def poser_mcp(f, maison):
    """Ajoute le serveur qaf sans toucher au reste du fichier. Un fichier illisible,
    un autre serveur nommé qaf ou un réglage exigé contredit : refus, rien n'est écrit,
    dans aucun des fichiers."""
    chemin = _fichier_utilisateur(os.path.join(maison, f.mcp.chemin))
    allow = [(_fichier_utilisateur(os.path.join(maison, x)), c, v) for x, c, v in f.mcp.allow]
    reglages = [(_fichier_utilisateur(os.path.join(maison, x)), c, v)
                for x, c, v in f.mcp.reglages + f.mcp.socle + f.mcp.exige]
    if any(_exige_contraire(maison, *e) for e in f.mcp.exige) \
            or any(_refus_present(maison, *r) for r in f.mcp.refuse):
        raise Refus("validation_contredite")
    if _absent_present(f, maison):
        raise Refus("fichier_execute")
    for p, cles, _ in allow:
        _verifier_cles(p, cles, liste=True)
    for p, cles, _ in reglages:
        _verifier_cles(p, cles)
    if f.mcp.format == "json":
        _poser_json(chemin, f.mcp.cles, entree_mcp(f))
    elif f.mcp.format == "toml":
        _poser_toml(f, chemin)
    elif f.mcp.format == "cordis":
        _poser_cordis(f, chemin)
    else:
        _poser_yaml(f, chemin)
    for p, cles, valeur in allow:
        d, o = _prendre_cles(p, cles)
        liste = o.setdefault(cles[-1], [])
        if valeur not in liste:
            liste.append(valeur)
            _ecrire(p, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(p))
    for p, cles, valeur in reglages:  # un choix déjà là appartient à l'utilisateur
        d, o = _prendre_cles(p, cles)
        if cles[-1] not in o:
            o[cles[-1]] = valeur
            _ecrire(p, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(p))


def _verifier_cles(chemin, cles, liste=False):
    """Lecture seule : le chemin de clés peut-il être écrit dans ce fichier JSON ?"""
    d = _json_a_completer(chemin)
    for c in cles[:-1]:
        if not isinstance(d, dict):
            raise Refus("config_illisible")
        d = d.get(c, {})
    if not isinstance(d, dict) or (liste and not isinstance(d.get(cles[-1], []), list)):
        raise Refus("config_illisible")


def _prendre_cles(chemin, cles):
    """(document, objet parent) d'un fichier déjà passé par _verifier_cles."""
    d = _json_a_completer(chemin)
    o = d
    for c in cles[:-1]:
        o = o.setdefault(c, {})
    return d, o


def retirer_mcp(f, maison):
    if f.mcp is None:
        return
    chemin = _fichier_utilisateur(os.path.join(maison, f.mcp.chemin))
    if f.mcp.format == "json":
        _retirer_json(chemin, f.mcp.cles)
    else:
        _retirer_bloc(chemin)
    for fichier, cles, valeur in f.mcp.allow:
        p = _fichier_utilisateur(os.path.join(maison, fichier))
        d = _lire_json(p)
        liste = _dans(d, cles)
        if isinstance(liste, list) and valeur in liste:
            liste.remove(valeur)
            _ecrire(p, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(p))
    for fichier, cles, valeur in f.mcp.reglages + f.mcp.exige:
        p = _fichier_utilisateur(os.path.join(maison, fichier))
        d = _lire_json(p)
        o = _dans(d, cles[:-1]) if isinstance(d, dict) else None
        if isinstance(o, dict) and cles[-1] in o and o[cles[-1]] == valeur:
            del o[cles[-1]]
            _ecrire(p, json.dumps(d, indent=2, ensure_ascii=False) + "\n", _mode(p))


def retirer(id, maison):
    """Retire un fournisseur : ses réglages, sa clé, son serveur qaf. La CLI et sa
    connexion restent à l'utilisateur. S'il était actif, retour à Claude."""
    f = PAR_ID[id]
    if id == "claude":
        return
    if f.mcp is not None:
        try:
            retirer_mcp(f, maison)
        except (OSError, Refus):
            pass
    if f.famille == "local":
        retirer_tunnel(maison)
    shutil.rmtree(dossier(maison, id), ignore_errors=True)
    if lire_actif(maison) == id:
        ecrire_actif(maison, "claude")


def retirer_tout(maison):
    """Désinstallation : Claude reste tel quel. Seul lancements.json survit s'il a servi :
    il sépare les conversations et les totaux de Claude de ceux des tiers."""
    for i in IDS_HORS_CLAUDE:
        retirer(i, maison)
    deja = lancements(maison)
    shutil.rmtree(racine(maison), ignore_errors=True)
    if deja:
        _ecrire(os.path.join(racine(maison), "lancements.json"), json.dumps(deja))
    effacer_demande(maison)


# --- installation à la demande (sans sudo, dans ~/.local) --------------------------------

class _HttpsSeul(urllib.request.HTTPRedirectHandler):
    """urllib suivrait de https vers http : chaque étape reste en https."""

    def redirect_request(self, req, fp, code, msg, headers, nouvelle):
        if not nouvelle.startswith("https://"):
            raise Refus("installation_echec")
        return super().redirect_request(req, fp, code, msg, headers, nouvelle)


def _ouvrir_https(url, ouvrir=None, depuis=0):
    if not url.startswith("https://"):
        raise Refus("installation_echec")
    req = urllib.request.Request(url, headers={"User-Agent": "qaf", **({"Range": f"bytes={depuis}-"} if depuis else {})})
    return (ouvrir or urllib.request.build_opener(_HttpsSeul).open)(req, timeout=60)


def _recevoir(url, dest, ouvrir):
    """Une passe : reprend le fichier partiel si le serveur répond 206, sinon repart de
    zéro. Une réponse plus courte que Content-Length est une coupure, pas une fin."""
    deja = os.path.getsize(dest) if os.path.exists(dest) else 0
    with _ouvrir_https(url, ouvrir, deja) as r:
        n = deja if deja and getattr(r, "status", 200) == 206 else 0
        en_tetes = getattr(r, "headers", None)
        taille = en_tetes.get("Content-Length") if en_tetes is not None else None
        attendu = n + int(taille) if taille and taille.isdigit() else None
        with open(dest, "ab" if n else "wb") as f:
            while True:
                bloc = r.read(1 << 20)
                if not bloc:
                    break
                n += len(bloc)
                if n > MAX_TELECHARGE:
                    raise Refus("installation_echec")
                f.write(bloc)
    if attendu is not None and n < attendu:
        raise ConnectionError("réponse tronquée")


def _empreinte(chemin):
    import hashlib
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def telecharger(url, dest, sha256, ouvrir=None):
    """Téléchargement borné, repris après une coupure. Ce fichier sera exécuté : pas
    d'empreinte, pas de téléchargement, et il est contrôlé avant tout usage."""
    if not sha256:
        raise Refus("installation_echec")
    if os.path.exists(dest):
        os.unlink(dest)
    for essai in range(TENTATIVES):
        if essai:
            time.sleep(PAUSE_REPRISE)
        try:
            _recevoir(url, dest, ouvrir)
            break
        except urllib.error.HTTPError as e:
            if e.code == 416 and os.path.exists(dest):  # partiel que le serveur ne reprend pas
                os.unlink(dest)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
    else:
        if os.path.exists(dest):
            os.unlink(dest)
        raise Refus("installation_echec")
    if _empreinte(dest) != sha256:
        os.unlink(dest)
        raise Refus("empreinte_differente")


def _deplier(archive, dest, retirer_n):
    os.makedirs(dest, mode=0o700, exist_ok=True)
    with tarfile.open(archive, "r:gz") as t:
        membres = []
        for m in t.getmembers():
            parties = m.name.split("/")[retirer_n:]
            if parties:
                m.name = "/".join(parties)
                membres.append(m)
        t.extractall(dest, members=membres, filter="data")


def _lier(f, maison, cible):
    os.chmod(cible, 0o755)
    bin_ = os.path.join(maison, ".local", "bin")
    os.makedirs(bin_, exist_ok=True)
    for b in f.binaires:
        if b.startswith("~/.local/bin/"):
            lien = b.replace("~", maison, 1)
            if os.path.lexists(lien):
                os.unlink(lien)
            os.symlink(cible, lien)
    for nom in f.install.freres:
        frere = os.path.join(os.path.dirname(cible), nom)
        if not os.path.isfile(frere):
            raise Refus("installation_echec")
        os.chmod(frere, 0o755)
        lien = os.path.join(bin_, nom)
        if os.path.lexists(lien):
            os.unlink(lien)
        os.symlink(frere, lien)


def dossier_node(maison):
    return os.path.join(maison, ".local", "share", "qaf-cli", "node", NODE_VERSION)


def _poser_outil(outil, d, ouvrir):
    """Outil privé épinglé (uv, Node), une seule fois, sous son empreinte."""
    cible = os.path.join(d, outil.bin_dans)
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.path.isfile(cible):
        return cible
    with tempfile.TemporaryDirectory(dir=d) as tmp:
        a = os.path.join(tmp, "outil.tar.gz")
        telecharger(outil.url, a, outil.sha256, ouvrir)
        try:
            _deplier(a, d, outil.retirer)
        except (tarfile.TarError, OSError):
            raise Refus("installation_echec") from None
    if not os.path.isfile(cible):
        raise Refus("installation_echec")
    os.chmod(cible, 0o755)
    return cible


def version_node(executer):
    r = executer(["node", "--version"], capture_output=True, timeout=10)
    m = re.match(rb"v(\d+)\.(\d+)", r.stdout or b"")
    return (int(m.group(1)), int(m.group(2))) if m else ()


def libatomic_presente():
    return any(os.path.exists(os.path.join(d, "libatomic.so.1"))
               for d in ("/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/lib64"))


def installer(f, maison, ouvrir=None, executer=subprocess.run, env=None):
    """Pose la CLI, version épinglée de la table. Un échec lève Refus."""
    i = f.install
    if i is None:
        raise Refus("installation_echec")
    env = dict(os.environ if env is None else env)
    for v in ENV_RETIRE:
        env.pop(v, None)
    local = os.path.join(maison, ".local")
    env["PATH"] = os.path.join(local, "bin") + ":" + env.get("PATH", "/usr/bin:/bin")
    env.update(ENV_NPM)
    base = os.path.join(local, "share", "qaf-cli", f.id, f.version)
    os.makedirs(base, mode=0o700, exist_ok=True)

    def lancer(argv, **k):
        try:
            r = executer([*SANS_PRIVILEGE, *argv], env=k.pop("env", env), timeout=1800, **k)
        except (OSError, subprocess.SubprocessError):
            raise Refus("installation_echec") from None
        if r.returncode != 0:
            raise Refus("installation_echec")

    if i.mode == "npm":
        if i.node_prive:
            _poser_outil(NODE, dossier_node(maison), ouvrir)
            env["PATH"] = chemin_de(f, maison) + ":" + env.get("PATH", "/usr/bin:/bin")
        if i.node_min:
            try:
                n = version_node(executer)
            except (OSError, subprocess.SubprocessError):
                n = ()
            if not n or n < tuple(i.node_min):
                raise Refus("node_trop_vieux")
        lancer(["npm", "install", "-g", "--prefix", local, "--no-fund", "--no-audit",
                f"--before={GEL_DEPENDANCES}", i.paquet])
    elif i.mode == "archive":
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            a = os.path.join(tmp, "archive.tar.gz")
            telecharger(i.url, a, i.sha256, ouvrir)
            try:
                _deplier(a, base, i.retirer)
            except (tarfile.TarError, OSError):
                raise Refus("installation_echec") from None
        cible = os.path.join(base, i.bin_dans)
        if not os.path.isfile(cible):
            raise Refus("installation_echec")
        _lier(f, maison, cible)
    elif i.mode == "binaire":
        cible = os.path.join(base, f.id)
        telecharger(i.url, cible, i.sha256, ouvrir)
        _lier(f, maison, cible)
    elif i.mode == "uv":
        uv = _poser_outil(UV, os.path.join(local, "share", "qaf-cli", "uv", "0.13.6"), ouvrir)
        lancer([uv, "tool", "install", "--exclude-newer", GEL_DEPENDANCES, i.paquet],
               env={**env, "UV_TOOL_BIN_DIR": os.path.join(local, "bin")})
    elif i.mode == "script":
        if not libatomic_presente():
            raise Refus("libatomic_absente")
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            script = os.path.join(tmp, "install.sh")
            telecharger(i.url, script, i.sha256, ouvrir)
            lancer(["/bin/bash", script, *i.args])
    else:
        raise Refus("installation_echec")


# --- service qaf.Fournisseur+ID --------------------------------------------------------

def bascule(environ=None, sortie=None, existe=os.path.exists):
    """Appelé par dom0 seulement. Refuse tout id non activable ; écrit l'actif de
    façon atomique ; une ligne, « ok » ou « non »."""
    environ = os.environ if environ is None else environ
    sortie = sortie or sys.stdout
    id = environ.get("QREXEC_SERVICE_ARGUMENT", "")
    maison = environ.get("HOME") or os.path.expanduser("~")
    ok = environ.get("QREXEC_REMOTE_DOMAIN") == "dom0" and id in IDS and activable(id, maison, existe)
    if ok:
        try:
            ecrire_actif(maison, id)
        except OSError:
            ok = False
    sortie.write("ok\n" if ok else "non\n")
    sortie.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(bascule())
