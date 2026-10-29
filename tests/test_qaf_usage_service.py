"""Service qaf.Usage de sys-agent (qaf_mcp/usage.py) : faux point d'usage, faux ~/.claude."""

import hashlib
import io
import json
import os
import time
import urllib.error

from qaf_mcp import usage as s
from qaf_ui import usage as dom0

JETON = "sk-ant-oat01-SECRET-NE-SORT-PAS"
T = time.time()


def maison(tmp_path, expire=T + 3600, rafr=T + 86400 * 30, sub="max", tier="default_claude_max_20x"):
    (tmp_path / ".claude" / "projects" / "-home-user-agent").mkdir(parents=True)
    (tmp_path / ".claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": JETON, "refreshToken": "rt-SECRET", "expiresAt": int(expire * 1000),
        "refreshTokenExpiresAt": int(rafr * 1000), "subscriptionType": sub, "rateLimitTier": tier}}))
    return str(tmp_path)


class Reponse(io.BytesIO):
    def __init__(self, corps, status=200):
        super().__init__(json.dumps(corps).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


CORPS = {"five_hour": {"utilization": 6.7, "resets_at": iso(T + 3600)},
         "seven_day": {"utilization": 35.0, "resets_at": iso(T + 3 * 86400)},
         "limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 20, "resets_at": iso(T + 3 * 86400),
                     "scope": {"model": {"display_name": "Fable"}}},
                    {"kind": "session", "group": "session", "percent": 6, "resets_at": iso(T + 3600), "scope": None}]}


def ouvrir_qui_note(vus, corps=CORPS, erreur=None):
    def ouvrir(req, timeout):
        vus.append((req.full_url, dict(req.header_items()), timeout))
        if erreur:
            raise erreur
        return Reponse(corps)
    return ouvrir


def test_entetes_exacts_et_fenetres(tmp_path):
    vus = []
    m = s.mesurer(maison(tmp_path), T, ouvrir_qui_note(vus), existe=lambda p: False)
    url, entetes, delai = vus[0]
    assert url == "https://api.anthropic.com/api/oauth/usage" and delai == 10
    assert entetes == {"Authorization": f"Bearer {JETON}", "Anthropic-beta": "oauth-2025-04-20",
                       "Accept": "application/json"}
    assert m["etat"] == "ok" and m["offre"] == "max_20x"
    assert [(f["nom"], f["modele"], f["pourcent"]) for f in m["fenetres"]] == [
        ("5h", "", 6), ("semaine", "", 35), ("semaine", "fable", 20)]
    assert m["fournisseurs"] == {"codex": "non_installe", "grok": "non_installe"}
    # dom0 l'accepte tel quel
    assert dom0.lire_mesure(s.ligne(m).encode(), T) is not None


def test_le_jeton_ne_sort_jamais(tmp_path):
    m = maison(tmp_path)
    for corps in (CORPS, {"five_hour": {"utilization": JETON}}):
        sortie = s.ligne(s.mesurer(m, T + 100 * len(str(corps)), ouvrir_qui_note([], corps)))
        assert JETON not in sortie and "SECRET" not in sortie


def test_rien_n_est_ecrit_dans_claude(tmp_path):
    m = maison(tmp_path)

    def empreintes():
        r = {}
        for d, _s, noms in os.walk(os.path.join(m, ".claude")):
            for n in noms:
                p = os.path.join(d, n)
                r[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        return r

    avant = empreintes()
    s.mesurer(m, T, ouvrir_qui_note([]))
    assert empreintes() == avant


def test_jeton_absent_echu_expire(tmp_path):
    vus = []
    assert s.mesurer(str(tmp_path), T, ouvrir_qui_note(vus))["etat"] == "sans_jeton"
    m = maison(tmp_path / "a", expire=T - 10)
    assert s.mesurer(m, T, ouvrir_qui_note(vus))["etat"] == "en_pause"
    m = maison(tmp_path / "b", expire=T - 10, rafr=T - 5)
    assert s.mesurer(m, T, ouvrir_qui_note(vus))["etat"] == "expire"
    assert vus == []  # aucun appel réseau


def test_codes_http(tmp_path):
    for code, etat in ((401, "refuse"), (403, "refuse"), (429, "limite"), (500, "erreur")):
        e = urllib.error.HTTPError(s.URL, code, "x", {}, None)
        m = s.mesurer(maison(tmp_path / str(code)), T, ouvrir_qui_note([], erreur=e))
        assert m["etat"] == etat and m["fenetres"] == []
    m = s.mesurer(maison(tmp_path / "net"), T, ouvrir_qui_note([], erreur=urllib.error.URLError("x")))
    assert m["etat"] == "injoignable"


def test_reponse_sans_five_hour(tmp_path):
    m = s.mesurer(maison(tmp_path), T, ouvrir_qui_note([], {"seven_day": {"utilization": 1, "resets_at": T + 99}}))
    assert m["etat"] == "ok" and [f["nom"] for f in m["fenetres"]] == ["semaine"]


def test_plancher_de_15_s(tmp_path):
    m, vus = maison(tmp_path), []
    s.mesurer(m, T, ouvrir_qui_note(vus))
    s.mesurer(m, T + 5, ouvrir_qui_note(vus))
    assert len(vus) == 1
    s.mesurer(m, T + 16, ouvrir_qui_note(vus))
    assert len(vus) == 2


def test_offres():
    assert s.offre_de("max", "default_claude_max_5x") == "max_5x"
    assert s.offre_de("max", "default_claude_max_20x") == "max_20x"
    assert s.offre_de("pro", None) == "pro"
    assert s.offre_de("team", "x_max_5x") == "team_5x"
    assert s.offre_de("bizarre", None) == ""


def ecrire(chemin, lignes, mtime=None):
    with open(chemin, "w", encoding="utf-8") as f:
        for ligne in lignes:
            f.write((ligne if isinstance(ligne, str) else json.dumps(ligne)) + "\n")
    if mtime:
        os.utime(chemin, (mtime, mtime))


def assistant(t, mid, rid, modele="claude-opus-4", n=100):
    return {"type": "assistant", "timestamp": iso(t), "requestId": rid,
            "message": {"id": mid, "model": modele, "usage": {"input_tokens": n, "output_tokens": n,
                                                               "cache_creation_input_tokens": 0,
                                                               "cache_read_input_tokens": n}}}


def user(t, contenu, **k):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": contenu}, **k}


def test_totaux(tmp_path):
    m = maison(tmp_path)
    d = os.path.join(m, ".claude", "projects", "-home-user-agent")
    ecrire(os.path.join(d, "a.jsonl"), [
        user(T - 60, "bonjour"),
        user(T - 50, [{"type": "tool_result", "content": "x"}]),
        user(T - 40, "<command-name>/clear</command-name>"),
        user(T - 30, "méta", isMeta=True),
        assistant(T - 20, "m1", "r1"), assistant(T - 19, "m1", "r1"),  # doublon
        assistant(T - 10, "m2", "r2", "claude-sonnet-4", 10),
        assistant(T - 5, "m3", "r3", "gpt-5", 10 ** 6),  # pas un modèle Claude
        "pas du json", "[1]",
    ])
    ecrire(os.path.join(d, "vieux.jsonl"), [assistant(T - 20 * 86400, "v", "v")], mtime=T - 20 * 86400)
    t = s.totaux(m, T)
    assert t["jetons_jour"] == 330 and t["jetons_semaine"] == 330
    assert t["invites_jour"] == 1 and t["sessions_jour"] == 1
    assert t["modele"] == "opus" and t["partiel"] is False
    assert t["jour_actif"] == time.localtime(T).tm_wday


def test_totaux_plafond_partiel(tmp_path, monkeypatch):
    m = maison(tmp_path)
    d = os.path.join(m, ".claude", "projects", "-home-user-agent")
    ecrire(os.path.join(d, "a.jsonl"), [assistant(T - 10 - i, f"m{i}", f"r{i}") for i in range(50)])
    monkeypatch.setattr(s, "MAX_OCTETS", 2000)
    assert s.totaux(m, T)["partiel"] is True


def test_sortie_bornee():
    assert s.ligne({"v": 1, "etat": "ok", "x": "y" * 5000}) == '{"v":1,"etat":"erreur"}\n'


def test_fournisseurs(tmp_path):
    m = str(tmp_path)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}")
    existe = lambda p: p == "/usr/bin/codex" or os.path.exists(p)  # noqa: E731
    assert s.fournisseurs(m, existe) == {"codex": "connecte", "grok": "non_installe"}


def test_main_refuse_un_qube(tmp_path):
    out = io.StringIO()
    assert s.main({"QREXEC_REMOTE_DOMAIN": "qube-admin", "HOME": str(tmp_path)}, out) == 1
    assert out.getvalue() == ""
