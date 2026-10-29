"""qaf-hand : la « main » sans LLM installée dans un qube cible.

Quatre services qrexec, chacun lit UNE requête JSON bornée sur stdin et répond
UNE ligne JSON sur stdout ({"ok": true, ...} ou {"ok": false, "erreur": ...}) :

  qaf.Screen  {"action": "lister", "masquees": false}
             {"action": "capturer", "fenetre": "0x1a00003" | "racine"}
                 -> PNG de la racine recadré sur la fenêtre (x, y, largeur, hauteur) ;
                    « racine » : cadre des fenêtres visibles (n9)
  qaf.Input   {"action": "taper", "texte": "...", "fenetre": "0x..."}
             {"action": "touche", "touches": "ctrl+l", "fenetre": "0x..."}
             {"action": "cliquer", "x": 10, "y": 20, "bouton": 1, "fenetre": "0x..."}
             {"action": "deplacer", "x": 10, "y": 20, "fenetre": "0x..."}
             {"action": "defiler", "sens": "bas", "crans": 3}
  qaf.Exec    {"argv": ["ls", "-l"], "delai": 10}
  qaf.Task    {"tache": "..."}  (jetable seulement, cf. qaf_hand/tache.py)

Tout passe par des listes d'arguments (jamais de shell). Les commandes système
passent par un « lanceur » injectable : les tests le remplacent.
Qui a le droit d'appeler ces services est décidé par la politique qrexec de
dom0, pas ici. Bibliothèque standard uniquement.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

from qaf_hand import xwd

MAX_ENTREE = 16 * 1024
MAX_TEXTE = 2000
MAX_FENETRES = 64
MAX_CAPTURE = 24 * 1024 * 1024
# Le Doorman lit au plus 640 Kio de réponse de qaf.Screen (qaf_gate/taches.py,
# BORNES) : la capture est réduite (un pixel sur k) jusqu'à tenir, en base64.
MAX_PNG_PORTIER = 460 * 1024
MAX_REDUCTION = 8
MIN_FENETRE = 8  # px : les petites fenêtres techniques (1×1) n'élargissent pas le cadre
MAX_LECTURE = 64 * 1024 * 1024  # sortie lue d'une commande (xwd d'un grand écran compris)
MAX_SORTIE_EXEC = 64 * 1024
MAX_JSON_EXEC = 120 * 1024  # sous les 128 Kio lus par le Doorman pour qaf.Exec
MAX_ARGV, MAX_ARG, MAX_ARGV_TOTAL = 64, 4096, 8192
DELAI_X = 15  # secondes pour une commande X (capture, xdotool)
DELAI_EXEC_MAX = 120
_TOUCHES_RE = re.compile(r"\A[A-Za-z0-9_]+(\+[A-Za-z0-9_]+){0,4}\Z")
_FENETRE_RE = re.compile(r"\A(0x[0-9a-fA-F]{1,8}|[0-9]{1,10})\Z")


class Refus(Exception):
    """Requête refusée (entrée invalide ou commande en échec)."""


def lancer(argv, entree=None, delai=DELAI_X):
    """Lanceur réel : (code, stdout, stderr). Remplacé dans les tests.

    Sorties dans des fichiers temporaires, pas dans des tubes (n18,
    PARCOURS-3) : on attend la fin de la COMMANDE, pas la fermeture de sa
    sortie. Une appli lancée en arrière-plan (« xfce4-terminal & ») garde sa
    sortie ouverte : avec des tubes, l'appel attendait jusqu'au délai et
    l'agent relançait l'appli. Ce qu'un tel fils écrit ensuite est ignoré."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")  # X du qube (qubes-gui-agent-linux, profile.d/qubes-gui.sh)
    with tempfile.TemporaryFile() as sortie, tempfile.TemporaryFile() as erreurs:
        try:
            p = subprocess.Popen(argv, stdin=subprocess.PIPE if entree else subprocess.DEVNULL,
                                 stdout=sortie, stderr=erreurs, env=env)
        except FileNotFoundError:
            raise Refus(f"commande absente : {argv[0]}") from None
        try:
            p.communicate(entree or None, timeout=delai)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise Refus(f"délai dépassé ({delai} s) : {argv[0]}") from None
        lus = []
        for f in (sortie, erreurs):
            f.seek(0)
            lus.append(f.read(MAX_LECTURE))
    return p.returncode, lus[0], lus[1]


# --------------------------------------------------------------- validation

def _champs(req, requis, facultatifs=()):
    if not isinstance(req, dict):
        raise Refus("objet JSON attendu")
    manquants = set(requis) - set(req)
    inconnus = set(req) - set(requis) - set(facultatifs)
    if manquants:
        raise Refus(f"champ manquant : {sorted(manquants)}")
    if inconnus:
        raise Refus(f"champ inconnu : {sorted(inconnus)}")


def _entier(v, champ, mini, maxi):
    if type(v) is not int or not mini <= v <= maxi:
        raise Refus(f"{champ} : entier entre {mini} et {maxi} attendu")
    return v


def _fenetre(v, racine_ok=False):
    """Identifiant X de fenêtre -> chaîne décimale (forme acceptée par xdotool)."""
    if racine_ok and v == "racine":
        return "root"
    if isinstance(v, int) and type(v) is int:
        v = str(v)
    if not isinstance(v, str) or not _FENETRE_RE.match(v):
        raise Refus("fenetre : identifiant X attendu (0x… ou décimal)")
    n = int(v, 0)
    if not 0 < n < 2**32:
        raise Refus("fenetre : identifiant hors limites")
    return str(n)


def _texte(v):
    if not isinstance(v, str) or not 1 <= len(v) <= MAX_TEXTE:
        raise Refus(f"texte : 1 à {MAX_TEXTE} caractères")
    if any(c not in "\n\t" and unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in v):
        raise Refus("texte : caractère de contrôle interdit")
    return v


def lire_requete(flux):
    brut = flux.read(MAX_ENTREE + 1)
    if len(brut) > MAX_ENTREE:
        raise Refus("requête trop grande")
    try:
        return json.loads(brut.decode("utf-8"), parse_constant=_pas_de_constante)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refus("JSON invalide") from None


def _pas_de_constante(c):
    raise ValueError(c)


# ---------------------------------------------------------------- qaf.Screen

def _xdotool(lanceur, *args):
    code, out, err = lanceur(["xdotool", *args])
    if code != 0:
        raise Refus(f"xdotool {args[0]} : {err.decode('utf-8', 'replace').strip()[:200] or code}")
    return out.decode("utf-8", "replace")


def lister_fenetres(lanceur, masquees=False):
    """Fenêtres de premier niveau du X du qube (pas de gestionnaire de
    fenêtres dans un qube : ce sont les enfants directs de la racine)."""
    args = ["search", "--maxdepth", "1"] + ([] if masquees else ["--onlyvisible"]) + ["--name", ""]
    code, out, err = lanceur(["xdotool", *args])
    if code not in (0, 1):  # 1 = aucune fenêtre trouvée
        raise Refus(f"xdotool search : {err.decode('utf-8', 'replace').strip()[:200]}")
    ids = [l for l in out.decode().split() if l.isdigit()][:MAX_FENETRES]
    fenetres = []
    for wid in ids:
        geo = {}
        for ligne in _xdotool(lanceur, "getwindowgeometry", "--shell", wid).splitlines():
            k, _, v = ligne.partition("=")
            if k in ("X", "Y", "WIDTH", "HEIGHT") and v.lstrip("-").isdigit():
                geo[k.lower()] = int(v)
        try:
            titre = _xdotool(lanceur, "getwindowname", wid).strip()
        except Refus:
            titre = ""
        fenetres.append({"id": hex(int(wid)), "titre": titre[:200], **geo})
    return fenetres


def geometrie(lanceur, wid):
    """Position absolue, taille et état d'affichage d'une fenêtre (xwininfo)."""
    code, out, err = lanceur(["xwininfo", "-id", wid])
    if code != 0:
        raise Refus(f"xwininfo : {err.decode('utf-8', 'replace').strip()[:200] or code}")
    cles = {"Absolute upper-left X": "x", "Absolute upper-left Y": "y",
            "Width": "largeur", "Height": "hauteur", "Map State": "etat"}
    geo = {}
    for ligne in out.decode("utf-8", "replace").splitlines():
        k, _, v = ligne.strip().partition(":")
        if k in cles:
            v = v.strip()
            geo[cles[k]] = int(v) if v.lstrip("-").isdigit() else v
    if not all(type(geo.get(k)) is int for k in ("x", "y", "largeur", "hauteur")):
        raise Refus("xwininfo : géométrie illisible")
    return geo


def cadre_des_fenetres(lanceur):
    """n9 (PARCOURS-2 et 3) : rectangle qui englobe les fenêtres visibles du
    qube, ou None (aucune fenêtre, liste illisible). La racine d'un qube n'est
    jamais affichée chez l'utilisateur : hors des fenêtres, ce n'est qu'un fond vide."""
    try:
        f = [w for w in lister_fenetres(lanceur) if {"x", "y"} <= set(w)
             and min(w.get("width", 0), w.get("height", 0)) >= MIN_FENETRE]
    except (Refus, ValueError):  # sortie illisible : l'écran entier, comme avant
        return None
    if not f:
        return None
    x, y = min(w["x"] for w in f), min(w["y"] for w in f)
    return x, y, max(w["x"] + w["width"] for w in f) - x, max(w["y"] + w["height"] for w in f) - y


def capturer(lanceur, fenetre):
    """Capture la racine (xwd) puis découpe la fenêtre demandée -> PNG.
    « racine » : le cadre des fenêtres visibles (n9), l'écran entier sans fenêtre.

    Dans un qube, la capture d'une fenêtre seule est NOIRE (RESULTATS-S1, D) :
    seule la racine porte les pixels. Conséquence assumée : si une autre
    fenêtre du qube recouvre la cible, c'est elle qu'on voit dans le cadre."""
    cible = _fenetre(fenetre, racine_ok=True)
    rect = cadre_des_fenetres(lanceur) if cible == "root" else None
    if cible != "root":
        geo = geometrie(lanceur, cible)
        if geo.get("etat") != "IsViewable":
            raise Refus(f"fenêtre non affichée ({str(geo.get('etat', '?'))[:32]}) : rien à capturer")
        rect = (geo["x"], geo["y"], geo["largeur"], geo["hauteur"])
    code, out, err = lanceur(["xwd", "-silent", "-root"])
    if code != 0 or not out:
        raise Refus(f"capture impossible : {err.decode('utf-8', 'replace').strip()[:200] or code}")
    for k in range(1, MAX_REDUCTION + 1):
        try:
            image, (x, y, l, h) = xwd.recadrer_png(out, rect, k)
        except xwd.XwdError as e:
            raise Refus(f"capture : {e}") from None
        if len(image) <= MAX_PNG_PORTIER:
            break
    if len(image) > min(MAX_CAPTURE, MAX_PNG_PORTIER):
        raise Refus("capture trop grande")
    return {"format": "png", "octets": len(image), "x": x, "y": y, "largeur": l, "hauteur": h, "reduction": k,
            "image": base64.b64encode(image).decode("ascii")}


def screen(req, lanceur):
    if not isinstance(req, dict) or req.get("action") not in ("lister", "capturer"):
        raise Refus("action : lister ou capturer")
    if req["action"] == "lister":
        _champs(req, ("action",), ("masquees",))
        masquees = req.get("masquees", False)
        if type(masquees) is not bool:
            raise Refus("masquees : booléen attendu")
        return {"fenetres": lister_fenetres(lanceur, masquees)}
    _champs(req, ("action", "fenetre"))
    return capturer(lanceur, req["fenetre"])


# ----------------------------------------------------------------- qaf.Input

def _focaliser(lanceur, req):
    # Pas de gestionnaire de fenêtres dans le qube : windowactivate (EWMH)
    # échouerait ; windowfocus fait un XSetInputFocus. À valider sur une vraie machine.
    if "fenetre" in req:
        _xdotool(lanceur, "windowfocus", "--sync", _fenetre(req["fenetre"]))


def _position(lanceur, req):
    x = _entier(req["x"], "x", 0, 16384)
    y = _entier(req["y"], "y", 0, 16384)
    if "fenetre" in req:  # coordonnées relatives à la fenêtre
        _xdotool(lanceur, "mousemove", "--window", _fenetre(req["fenetre"]), str(x), str(y))
    else:
        _xdotool(lanceur, "mousemove", str(x), str(y))


def entree(req, lanceur):
    action = req.get("action") if isinstance(req, dict) else None
    if action == "taper":
        _champs(req, ("action", "texte"), ("fenetre",))
        texte = _texte(req["texte"])
        _focaliser(lanceur, req)
        _xdotool(lanceur, "type", "--delay", "12", "--", texte)
        return {"tape": len(texte)}
    if action == "touche":
        _champs(req, ("action", "touches"), ("fenetre",))
        t = req["touches"]
        if not isinstance(t, str) or len(t) > 64 or not _TOUCHES_RE.match(t):
            raise Refus("touches : ex. Return, ctrl+l, ctrl+shift+t")
        _focaliser(lanceur, req)
        _xdotool(lanceur, "key", "--", t)
        return {"touches": t}
    if action in ("cliquer", "deplacer"):
        _champs(req, ("action", "x", "y"), ("fenetre", "bouton") if action == "cliquer" else ("fenetre",))
        bouton = _entier(req.get("bouton", 1), "bouton", 1, 3)
        _position(lanceur, req)
        if action == "cliquer":
            _xdotool(lanceur, "click", str(bouton))
        return {"x": req["x"], "y": req["y"]}
    if action == "defiler":
        _champs(req, ("action", "sens", "crans"))
        if req["sens"] not in ("haut", "bas"):
            raise Refus("sens : haut ou bas")
        crans = _entier(req["crans"], "crans", 1, 20)
        _xdotool(lanceur, "click", "--repeat", str(crans), "4" if req["sens"] == "haut" else "5")
        return {"crans": crans}
    raise Refus("action : taper, touche, cliquer, deplacer ou defiler")


# ------------------------------------------------------------------ qaf.Exec

def executer(req, lanceur):
    _champs(req, ("argv",), ("delai",))
    argv = req["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGV:
        raise Refus(f"argv : liste de 1 à {MAX_ARGV} chaînes")
    if not all(isinstance(a, str) and len(a) <= MAX_ARG and "\0" not in a for a in argv):
        raise Refus("argv : chaînes sans octet nul, 4096 caractères max")
    if sum(len(a) for a in argv) > MAX_ARGV_TOTAL or not argv[0]:
        raise Refus("argv : trop long ou commande vide")
    delai = _entier(req.get("delai", 30), "delai", 1, DELAI_EXEC_MAX)
    code, out, err = lanceur(argv, b"", delai)
    # Le Doorman lit au plus 128 Kio de réponse (qaf_gate/taches.py, BORNES) :
    # une sortie binaire grossit une fois encodée en JSON, on la réduit
    # jusqu'à tenir plutôt que de faire échouer toute l'opération.
    borne = MAX_SORTIE_EXEC
    while True:
        rep = {"code": code, "stdout": out[:borne].decode("utf-8", "replace"),
               "stderr": err[:borne].decode("utf-8", "replace"), "tronque": len(out) > borne or len(err) > borne}
        if borne <= 1024 or len(json.dumps({"ok": True, **rep}, ensure_ascii=False).encode("utf-8")) <= MAX_JSON_EXEC:
            return rep
        borne //= 2


def tache(req, lanceur):
    """qaf.Task (jetable seulement) : voir qaf_hand/tache.py."""
    from qaf_hand import tache as t  # import paresseux : t importe ce module
    return t.accepter(req)


SERVICES = {"screen": screen, "input": entree, "exec": executer, "task": tache}


def servir(nom, flux_entree, lanceur=lancer):
    """Traite une requête ; renvoie (réponse dict, code de sortie)."""
    try:
        return {"ok": True, **SERVICES[nom](lire_requete(flux_entree), lanceur)}, 0
    except Refus as e:
        return {"ok": False, "erreur": str(e)}, 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in SERVICES:
        print(f"usage : python3 -m qaf_hand {{{'|'.join(SERVICES)}}}", file=sys.stderr)
        return 2
    rep, code = servir(argv[0], sys.stdin.buffer)
    sys.stdout.write(json.dumps(rep, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code
