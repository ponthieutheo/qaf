"""Service qrexec qaf.Usage (sys-agent, installé dans tpl-agent) : la mesure
d'usage de l'abonnement, pour le panneau de l'agent dans la barre de dom0
(docs/AGENT-PANNEAU.md §5.4). D'après Omarchy (basecamp/omarchy,
bin/omarchy-agent-usage-claude et shell/plugins/agents/collectors.py), idée
reprise, code réécrit.

Appelé par dom0 seulement (QREXEC_REMOTE_DOMAIN), entrée ignorée, aucun
argument. Rend UNE ligne JSON de 4 096 octets au plus, faite de nombres
bornés et d'énumérations : aucun texte venu du réseau ni des fichiers n'en
sort, et jamais le jeton ni rien qui en dérive. dom0 la revalide à schéma
fermé (qaf_ui/usage.py) : ce qui suit n'est qu'une politesse.

- Limites : jeton d'accès de la connexion interactive de claude
  (~/.claude/.credentials.json, claudeAiOauth.accessToken ; à défaut le jeton
  longue durée ~/.config/qaf/jeton-claude), envoyé au point d'usage OAuth
  d'Anthropic avec les en-têtes d'Omarchy. Jeton absent : « sans_jeton » ;
  échu : « en_pause » (ou « expire » si le jeton de rafraîchissement l'est
  aussi), sans appel réseau. Le jeton n'est JAMAIS rafraîchi ici (seul claude
  le fait : un rafraîchissement par nous ferait tourner le jeton de
  rafraîchissement sous ses pieds) et rien n'est écrit dans ~/.claude.
- Plancher de 15 s entre deux appels au point d'usage (Omarchy :
  « Anthropic rate-limits its usage endpoint readily ») : la dernière réponse
  est gardée dans ~/.cache/qaf-usage/limites.json (notre qube, perdu = refait).
- Totaux locaux : transcriptions ~/.claude/projects/**/*.jsonl modifiées
  depuis 8 jours, 64 Mio et 5 s au plus (au-delà : « partiel »).
- Fournisseurs de la carte « + » : Codex et Grok, installés ou non,
  connectés ou non (présence de leur auth.json, jamais lu).

Bibliothèque standard seulement. Lancé par /etc/qubes-rpc/qaf.Usage.
"""

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

URL = "https://api.anthropic.com/api/oauth/usage"
ENTETES = {"anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}
DELAI_HTTP = 10
PLANCHER_S = 15
MAX_REPONSE = 256 * 1024
MAX_SORTIE = 4096
JOURS_LUS = 8
MAX_OCTETS = 64 * 1024 * 1024
MAX_CALCUL_S = 5.0
MAX_LIGNE = 4 * 1024 * 1024
MAX_FICHIER_JETON = 1024
CREDENTIALS = (".claude", ".credentials.json")
PROFIL = (".claude.json",)
JETON_LONG = (".config", "qaf", "jeton-claude")
PROJETS = (".claude", "projects")
CACHE = (".cache", "qaf-usage", "limites.json")
ETATS = ("ok", "sans_jeton", "en_pause", "expire", "refuse", "limite", "injoignable", "erreur")
OFFRES = ("", "pro", "max_5x", "max_20x", "team", "team_5x", "enterprise")
MODELES = ("opus", "sonnet", "haiku", "fable")
CLIS = {"codex": (("/usr/bin/codex",), (".codex", "auth.json")),
        "grok": (("/usr/bin/grok", "~/.grok/bin/grok"), (".grok", "auth.json"))}
MAX_JETONS = 10 ** 12
MAX_COMPTE = 100_000


def _chemin(maison, parties):
    return os.path.join(maison, *parties)


def _lire_json(chemin, plafond=MAX_REPONSE):
    try:
        with open(chemin, "rb") as f:
            brut = f.read(plafond + 1)
    except OSError:
        return None
    if len(brut) > plafond:
        return None
    try:
        return json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


# --- identifiants (lus, jamais écrits) ----------------------------------------------

def identifiants(maison, maintenant):
    """(etat, jeton, offre) : etat None si le jeton est utilisable."""
    c = _lire_json(_chemin(maison, CREDENTIALS))
    o = c.get("claudeAiOauth") if isinstance(c, dict) else None
    jeton = o.get("accessToken") if isinstance(o, dict) else None
    if isinstance(jeton, str) and 0 < len(jeton) <= 4096:
        offre = offre_de(o.get("subscriptionType"), o.get("rateLimitTier"), maison)
        expire = o.get("expiresAt")
        if isinstance(expire, (int, float)) and not isinstance(expire, bool) and expire / 1000 <= maintenant:
            rafr = o.get("refreshTokenExpiresAt")
            perime = isinstance(rafr, (int, float)) and not isinstance(rafr, bool) and rafr / 1000 <= maintenant
            return ("expire" if perime else "en_pause"), None, offre
        return None, jeton, offre
    try:  # variante setup-token (docs/AGENT-JETABLE.md §4)
        with open(_chemin(maison, JETON_LONG), encoding="ascii") as f:
            j = f.read(MAX_FICHIER_JETON + 1).strip()
    except (OSError, UnicodeDecodeError):
        j = ""
    if j and len(j) <= MAX_FICHIER_JETON and all("!" <= ch <= "~" for ch in j):
        return None, j, offre_de(None, None, maison)
    return "sans_jeton", None, ""


def offre_de(abonnement, palier, maison=None):
    """Énumération de l'offre (plan_label d'Omarchy) : type d'abonnement plus
    multiplicateur « max_(5|20)x » du palier, ou du profil ~/.claude.json."""
    if not isinstance(palier, str) and maison:
        p = _lire_json(_chemin(maison, PROFIL), 8 * 1024 * 1024)
        compte = p.get("oauthAccount") if isinstance(p, dict) else None
        if isinstance(compte, dict):
            palier = compte.get("userRateLimitTier")
    abonnement = abonnement.lower() if isinstance(abonnement, str) else ""
    m = re.search(r"max_(5|20)x", palier.lower()) if isinstance(palier, str) else None
    mult = f"_{m.group(1)}x" if m else ""
    if abonnement == "max" or (not abonnement and m):
        return "max" + (mult or "_5x")
    if abonnement == "team":
        return "team_5x" if mult == "_5x" else "team"
    if abonnement in ("pro", "enterprise"):
        return abonnement
    return ""


# --- limites ------------------------------------------------------------------

def epoque(valeur):
    """resets_at (ISO 8601 ou époque) -> secondes, ou None."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)) and math.isfinite(valeur):
        return int(valeur / 1000 if valeur > 10 ** 11 else valeur)
    if isinstance(valeur, str) and len(valeur) <= 64:
        try:
            return int(datetime.fromisoformat(valeur.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _pourcent(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(0, min(100, int(math.floor(v))))


def famille(nom):
    nom = nom.lower() if isinstance(nom, str) else ""
    return next((m for m in MODELES if m in nom), None)


def fenetres(reponse, maintenant):
    """Réponse du point d'usage -> liste de fenêtres (4 au plus)."""
    out = []
    if not isinstance(reponse, dict):
        return out
    for cle, nom in (("five_hour", "5h"), ("seven_day", "semaine")):
        f = reponse.get(cle)
        if isinstance(f, dict):
            p, r = _pourcent(f.get("utilization")), epoque(f.get("resets_at"))
            if p is not None and r is not None:
                out.append({"nom": nom, "modele": "", "pourcent": p, "remise": r})
    limites = reponse.get("limits")
    for lim in limites if isinstance(limites, list) else ():
        if len(out) >= 4 or not isinstance(lim, dict):
            continue
        portee = lim.get("scope")
        modele = portee.get("model") if isinstance(portee, dict) else None
        fam = famille(modele.get("display_name") if isinstance(modele, dict) else None)
        groupe = lim.get("group")
        nom = {"session": "5h", "weekly": "semaine"}.get(groupe)
        p, r = _pourcent(lim.get("percent")), epoque(lim.get("resets_at"))
        if fam and nom and p is not None and r is not None \
                and not any(x["nom"] == nom and x["modele"] == fam for x in out):
            out.append({"nom": nom, "modele": fam, "pourcent": p, "remise": r})
    return [x for x in out if maintenant - 86400 <= x["remise"] <= maintenant + 8 * 86400]


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def interroger(jeton, ouvrir=None):
    """(code HTTP, corps JSON ou None). Code 0 : injoignable."""
    ouvrir = ouvrir or urllib.request.build_opener(_SansRedirection).open
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {jeton}", **ENTETES})
    try:
        with ouvrir(req, timeout=DELAI_HTTP) as r:
            brut = r.read(MAX_REPONSE + 1)
            code = r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None
    if len(brut) > MAX_REPONSE:
        return code, None
    try:
        return code, json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return code, None


def limites(maison, maintenant, ouvrir=None):
    """(etat, offre, fenetres), avec le plancher de 15 s (cache de notre qube)."""
    etat, jeton, offre = identifiants(maison, maintenant)
    if etat:
        return etat, offre, []
    cache = _chemin(maison, CACHE)
    c = _lire_json(cache, 64 * 1024)
    if isinstance(c, dict) and isinstance(c.get("heure"), (int, float)) \
            and 0 <= maintenant - c["heure"] < PLANCHER_S and c.get("etat") in ETATS \
            and isinstance(c.get("fenetres"), list):
        return c["etat"], offre, c["fenetres"]
    code, corps = interroger(jeton, ouvrir)
    if code == 200 and corps is not None:
        etat, f = "ok", fenetres(corps, maintenant)
    elif code in (401, 403):
        etat, f = "refuse", []
    elif code == 429:
        etat, f = "limite", []
    elif code == 0:
        etat, f = "injoignable", []
    else:
        etat, f = "erreur", []
    try:
        os.makedirs(os.path.dirname(cache), mode=0o700, exist_ok=True)
        with open(cache + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"heure": maintenant, "etat": etat, "fenetres": f}, fh)
        os.replace(cache + ".tmp", cache)
    except OSError:
        pass
    return etat, offre, f


# --- totaux locaux -----------------------------------------------------------------

def _debut_semaine(jour):
    return jour - timedelta(days=jour.weekday())


def totaux(maison, maintenant, horloge=time.monotonic):
    """Totaux du jour et de la semaine (lundi à aujourd'hui, heure locale)."""
    aujourdhui = datetime.fromtimestamp(maintenant).date()
    lundi = _debut_semaine(aujourdhui)
    racine = _chemin(maison, PROJETS)
    fichiers = []
    for dossier, _sous, noms in os.walk(racine):
        for n in noms:
            if n.endswith(".jsonl"):
                p = os.path.join(dossier, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if maintenant - st.st_mtime <= JOURS_LUS * 86400:
                    fichiers.append((st.st_mtime, p))
    fichiers.sort(reverse=True)
    vus, par_jour, par_modele = set(), {}, {}
    jetons_jour = invites = 0
    sessions = set()
    lus, partiel, debut = 0, False, horloge()
    for _m, p in fichiers:
        if lus >= MAX_OCTETS or horloge() - debut > MAX_CALCUL_S:
            partiel = True
            break
        try:
            f = open(p, "rb")
        except OSError:
            continue
        with f:
            for ligne in f:
                lus += len(ligne)
                if lus > MAX_OCTETS or horloge() - debut > MAX_CALCUL_S:
                    partiel = True
                    break
                if len(ligne) > MAX_LIGNE:
                    continue
                try:
                    e = json.loads(ligne)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    continue
                if not isinstance(e, dict):
                    continue
                t = epoque(e.get("timestamp"))
                if t is None:
                    continue
                jour = datetime.fromtimestamp(t).date()
                if jour < lundi or jour > aujourdhui:
                    continue
                msg = e.get("message") if isinstance(e.get("message"), dict) else {}
                if e.get("type") == "assistant":
                    u, modele = msg.get("usage"), msg.get("model")
                    if not isinstance(u, dict) or not isinstance(modele, str) or not modele.startswith("claude-"):
                        continue
                    cle = (msg.get("id"), e.get("requestId"))
                    if cle in vus:
                        continue
                    vus.add(cle)
                    n = sum(v for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                        "cache_read_input_tokens")
                            if isinstance(v := u.get(k), int) and not isinstance(v, bool) and v > 0)
                    par_jour[jour] = par_jour.get(jour, 0) + n
                    fam = famille(modele) or "autre"
                    par_modele[fam] = par_modele.get(fam, 0) + n
                    if jour == aujourdhui:
                        jetons_jour += n
                        sessions.add(p)
                elif e.get("type") == "user" and jour == aujourdhui:
                    sessions.add(p)
                    if invite(e, msg):
                        invites += 1
    semaine = sum(par_jour.values())
    actif = max(par_jour, key=lambda j: (par_jour[j], j)) if par_jour and semaine > 0 else None
    modele = max(par_modele, key=lambda m: (par_modele[m], m)) if par_modele and semaine > 0 else ""
    return {"jetons_jour": min(jetons_jour, MAX_JETONS), "jetons_semaine": min(semaine, MAX_JETONS),
            "invites_jour": min(invites, MAX_COMPTE), "sessions_jour": min(len(sessions), MAX_COMPTE),
            "modele": modele, "jour_actif": actif.weekday() if actif else -1, "partiel": partiel}


def invite(e, msg):
    """Une invite de l'utilisateur : ligne « user » du fil principal, ni méta, ni
    résultat d'outil, ni commande locale (règle d'Omarchy, version courte)."""
    if e.get("isMeta") or e.get("isSidechain") or e.get("toolUseResult") is not None:
        return False
    contenu = msg.get("content")
    if isinstance(contenu, list):
        textes = [c for c in contenu if isinstance(c, dict)]
        if not textes or any(c.get("type") == "tool_result" for c in textes):
            return False
        contenu = next((c.get("text") for c in textes if c.get("type") == "text"), None)
    if not isinstance(contenu, str) or not contenu.strip():
        return False
    debut = contenu.lstrip()[:32]
    return not debut.startswith(("<command-", "<local-command", "Caveat:"))


# --- fournisseurs de la carte « + » ------------------------------------------------------

def fournisseurs(maison, existe=os.path.exists):
    out = {}
    for nom, (binaires, auth) in CLIS.items():
        installe = any(existe(b.replace("~", maison, 1)) for b in binaires)
        out[nom] = ("connecte" if existe(_chemin(maison, auth)) else "deconnecte") if installe else "non_installe"
    return out


# --- sortie ----------------------------------------------------------------------

def mesurer(maison, maintenant=None, ouvrir=None, horloge=time.monotonic, existe=os.path.exists):
    maintenant = time.time() if maintenant is None else maintenant
    etat, offre, f = limites(maison, maintenant, ouvrir)
    sortie = {"v": 1, "etat": etat, "offre": offre if offre in OFFRES else "",
              "fenetres": f if etat in ("ok", "limite") else []}
    try:
        sortie["totaux"] = totaux(maison, maintenant, horloge)
    except (OSError, ValueError, OverflowError):
        pass
    sortie["fournisseurs"] = fournisseurs(maison, existe)
    return sortie


def ligne(sortie):
    texte = json.dumps(sortie, separators=(",", ":"), ensure_ascii=True)
    if len(texte) + 1 > MAX_SORTIE:
        texte = '{"v":1,"etat":"erreur"}'
    return texte + "\n"


def main(environ=None, sortie=None):
    environ = os.environ if environ is None else environ
    sortie = sortie or sys.stdout
    if environ.get("QREXEC_REMOTE_DOMAIN") != "dom0":
        print("refusé : qaf.Usage n'accepte que dom0", file=sys.stderr)
        return 1
    try:
        resultat = mesurer(environ.get("HOME") or os.path.expanduser("~"))
    except Exception:  # jamais de trace (elle pourrait citer un fichier) : erreur fermée
        resultat = {"v": 1, "etat": "erreur"}
    sortie.write(ligne(resultat))
    sortie.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
