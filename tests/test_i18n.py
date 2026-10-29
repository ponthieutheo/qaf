"""QAF en français, anglais et chinois simplifié (29/10) : catalogues fr/en/zh
(qaf_ui/langues), langue fixée par la locale de dom0, aucune chaîne française en dur
dans les écrans, et la fenêtre de confirmation en anglais comme en chinois avec
exactement la même force."""

import ast
import json
import pathlib
import re

import pytest

from qaf_gate import classify, schema
from qaf_ui import confirmation as c
from qaf_ui import i18n
from qaf_ui.ecrans import agent_etat as ae
from qaf_ui.langues import en, fr, zh

from exemples_ops import EXEMPLES

RACINE = pathlib.Path(__file__).resolve().parent.parent
UI = RACINE / "qaf_ui"


def _sources():
    """Modules des écrans (tout qaf_ui sauf les catalogues)."""
    return sorted(p for p in UI.rglob("*.py") if "langues" not in p.parts and "__pycache__" not in p.parts)


# --- catalogues -------------------------------------------------------------------------

def test_memes_cles_dans_les_trois_langues():
    assert set(fr.TEXTES) == set(en.TEXTES), set(fr.TEXTES) ^ set(en.TEXTES)
    assert set(fr.TEXTES) == set(zh.TEXTES), set(fr.TEXTES) ^ set(zh.TEXTES)


@pytest.mark.parametrize("cle", sorted(fr.TEXTES))
def test_memes_champs_et_aucun_texte_vide(cle):
    a = fr.TEXTES[cle]
    assert a.strip() or a == "", cle
    for b in (en.TEXTES[cle], zh.TEXTES[cle]):
        assert i18n.champs(a) == i18n.champs(b), cle
        assert isinstance(a, str) and isinstance(b, str) and (b.strip() or not a.strip()), cle


def test_pluriels_complets():
    for cle in fr.TEXTES:
        if cle.endswith((".1", ".n")):
            base = cle[:-2]
            assert f"{base}.1" in fr.TEXTES and f"{base}.n" in fr.TEXTES, cle
            assert "n" in i18n.champs(fr.TEXTES[cle]), cle


def test_pluriels_chinois_identiques():
    """Le chinois n'a pas de pluriel : les deux formes d'une clé disent la même chose."""
    for cle in zh.TEXTES:
        if cle.endswith(".1"):
            assert zh.TEXTES[cle] == zh.TEXTES[cle[:-2] + ".n"], cle


# Chaînes chinoises sans idéogramme : noms propres, touches et unités, identiques partout.
ZH_SANS_IDEOGRAMME = {"agent.doorman", "pal.touche_echap", "pal.touche_entree", "conf.fw.dns", "usage.milliard",
                      "pal.ligne_agent", "etat.debut_demande"}
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")  # id\u00e9ogrammes et ponctuation chinoise


def test_chinois_sans_francais_ni_anglais():
    """Tout est traduit : aucune chaîne française ou anglaise oubliée dans le catalogue chinois."""
    francais = re.compile(r"[éèêàùçôîâûœ«»]|\b(le|la|les|des|ton|ta|une|pour|pas|dans|avec|sans|veut|espace)\b",
                          re.IGNORECASE)
    anglais = re.compile(r"\b(the|and|agent|wants|your|you|template|disposable)\b")
    for cle, texte in zh.TEXTES.items():
        assert not francais.search(texte), (cle, texte)
        assert not anglais.search(re.sub(r"\b(sys|tpl)-agent\b|\bqaf-[\w-]+", "", texte)), (cle, texte)
        assert CJK.search(texte) or cle in ZH_SANS_IDEOGRAMME, (cle, texte)


def test_chinois_vocabulaire_tenu():
    """Un mot, un sens : qube reste « qube », jamais 虚拟机 ; « 暂停 » (pause, tout refusé)
    ne sert jamais à la suspension des confirmations (免确认, tout passe)."""
    for cle, texte in zh.TEXTES.items():
        assert "虚拟机" not in texte and "空间" not in texte, cle
        if cle.startswith(("susp.", "suspendre.")):
            assert "暂停" not in texte, cle
    assert zh.TEXTES["niveau.modele"] == "模板" and zh.TEXTES["conf.sys.jetable"] == "一次性 qube"
    assert zh.TEXTES["niveau.coeur"] == "系统核心" and zh.TEXTES["niveau.coffre"] == "保险库"


def test_anglais_sans_francais():
    """Aucun mot français évident dans le catalogue anglais (oubli de traduction)."""
    francais = re.compile(r"[éèêàùçôîâûœ«»]|\b(le|la|les|des|ton|ta|tes|une|pour|pas|dans|avec|sans|ou)\b",
                          re.IGNORECASE)
    for cle, texte in en.TEXTES.items():
        assert not francais.search(texte), (cle, texte)


def _constantes(arbre):
    return [n for n in ast.walk(arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _appels(arbre):
    """Premier argument littéral de chaque appel t(...) / tn(...) / i18n.t(...)."""
    for n in ast.walk(arbre):
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            nom = n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) \
                else None
            if nom in ("t", "tn"):
                yield nom, n.args[0].value


def test_aucune_cle_orpheline_ni_manquante():
    litteraux, appels = set(), set()
    for p in _sources():
        arbre = ast.parse(p.read_text(encoding="utf-8"))
        litteraux |= {n.value for n in _constantes(arbre)}
        appels |= set(_appels(arbre))
    utilisees = {cle for cle in fr.TEXTES if cle in litteraux}
    # Pluriel choisi par une expression (tn("a" if … else "b", n)) : la base littérale suffit.
    utilisees |= {cle for cle in fr.TEXTES if cle.endswith((".1", ".n")) and cle[:-2] in litteraux}
    for nom, cle in appels:  # chaque appel nomme une clé qui existe
        if nom == "tn":
            assert f"{cle}.1" in fr.TEXTES and f"{cle}.n" in fr.TEXTES, cle
            utilisees |= {f"{cle}.1", f"{cle}.n"}
        else:
            assert cle in fr.TEXTES, cle
    # Famille construite par agent_etat (une phrase par opération du schéma).
    utilisees |= {cle for paire in ae.PHRASES.values() for cle in paire}
    for paire in ae.PHRASES.values():
        assert all(cle in fr.TEXTES for cle in paire), paire
    assert set(fr.TEXTES) - utilisees == set(), "clés orphelines"


# Écrans : une chaîne française en dur est une chaîne que l'anglais ne verra jamais traduite.
FRANCAIS = re.compile(r"[éèêàùçôîâûœÉÈÀÇ«»]|\b(le|la|les|de|du|des|ton|ta|tes|un|une|et|pour|pas|est|sur|dans|"
                      r"Tu|Ta|Ton|Le|La|Les|Aucun|Aucune)\b")
# Hors de l'écran : journal de service (stderr, journal()), exceptions internes, valeurs
# écrites par le Doorman et comparées (clés de dict, opérandes de comparaison), et les
# libellés des paires de contraste (jetons.py, lus par les tests seulement).
HORS_ECRAN_FICHIERS = {"nuit/jetons.py", "i18n.py"}
HORS_ECRAN_VALEURS = {
    # Raison écrite dans le fichier de pause du Doorman (journal), jamais affichée par un écran.
    ("ecrans/agent_etat.py", "pause d'urgence depuis le panneau de l'agent"),
    # Instantane.erreur : choisit l'alerte du panneau (texte traduit), jamais affichée telle quelle.
    ("ecrans/agent_etat.py", "droit de lecture manquant"), ("ecrans/agent_etat.py", "journal illisible"),
    # Identifiants des niveaux (clés de FORMES, SYMBOLES, MOTS) ; le mot affiché vient de confiance.mot().
    ("nuit/confiance.py", "Risqué"), ("nuit/confiance.py", "Modèle"), ("nuit/confiance.py", "Système"),
    ("nuit/confiance.py", "Cœur du système"),
}
# Clés de texte, noms techniques et chemins : jamais du texte affiché.
TECHNIQUE = re.compile(r"\A(/[\w./-]+|[a-z0-9_]+(\.[a-z0-9_]+)+)\Z")


def _hors_ecran(noeud, parents):
    for p in parents:
        if isinstance(p, ast.Raise):
            return True
        if isinstance(p, ast.Call):
            f = p.func
            nom = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if nom in ("journal", "_journal", "_repli"):
                return True
            if nom == "print" and any(k.arg == "file" for k in p.keywords):
                return True
        if isinstance(p, ast.Dict) and any(k is noeud for k in p.keys):
            return True
        if isinstance(p, ast.Compare):
            return True
    return False


def _docstrings(arbre):
    return {id(n.body[0].value) for n in ast.walk(arbre)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}


def test_aucune_chaine_francaise_en_dur_dans_les_ecrans():
    trouvees = []
    for p in _sources():
        rel = p.relative_to(UI).as_posix()
        if rel in HORS_ECRAN_FICHIERS:
            continue
        arbre = ast.parse(p.read_text(encoding="utf-8"))
        docs = _docstrings(arbre)
        parents = {}
        for n in ast.walk(arbre):
            for enfant in ast.iter_child_nodes(n):
                parents[id(enfant)] = n

        def chaine(n):
            out = []
            while id(n) in parents:
                n = parents[id(n)]
                out.append(n)
            return out

        for n in _constantes(arbre):
            if id(n) in docs or not FRANCAIS.search(n.value) or (rel, n.value) in HORS_ECRAN_VALEURS                     or TECHNIQUE.match(n.value):
                continue
            # Une clé de dict est sa propre parente directe : on passe le nœud lui-même.
            if _hors_ecran(n, chaine(n)):
                continue
            trouvees.append(f"{rel}:{n.lineno}: {n.value[:60]!r}")
    assert trouvees == [], "\n".join(trouvees)


def test_fichiers_desktop_traduits():
    for p in sorted((RACINE / "packaging").glob("*.desktop")):
        lignes = p.read_text(encoding="utf-8").splitlines()
        cles = {l.split("=", 1)[0] for l in lignes if "=" in l}
        for champ in ("Name", "GenericName", "Comment"):
            if champ in cles:
                assert f"{champ}[en]" in cles, (p.name, champ)


def test_services_recoivent_la_langue_de_la_session():
    # Les services --user démarrés par un .desktop parlent la langue de la session
    # (même langue pour la fenêtre de confirmation et pour la palette).
    for nom in ("qaf-ui", "qaf-agent-panneau", "qaf-suspension", "qaf-degel"):
        d = (RACINE / "packaging" / f"{nom}.desktop").read_text(encoding="utf-8")
        ligne = next(l for l in d.splitlines() if l.startswith("Exec="))
        importees = ligne.split("import-environment", 1)[1].split(";", 1)[0].split()
        assert {"DISPLAY", "XAUTHORITY", "LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"} <= set(importees), nom


# --- langue de la session ----------------------------------------------------------------

@pytest.mark.parametrize("env,attendue", [
    ({}, "fr"), ({"LANG": "C.UTF-8"}, "fr"), ({"LANG": "POSIX"}, "fr"), ({"LANG": "fr_FR.UTF-8"}, "fr"),
    ({"LANG": "en_US.UTF-8"}, "en"), ({"LANG": "en_GB"}, "en"), ({"LANG": "de_DE.UTF-8"}, "fr"),
    ({"LANG": "fr_FR.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}, "en"),
    ({"LANG": "en_US.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8"}, "fr"),
    ({"LANG": "fr_FR.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8", "LC_ALL": "en_US.UTF-8"}, "en"),
    ({"LANG": "en_US.UTF-8", "LC_ALL": ""}, "en"), ({"LANG": "en@quot"}, "en"),
    # LANGUAGE d'abord, comme gettext et KDE (langue de Plasma), sauf locale C ou POSIX
    ({"LANG": "en_US.UTF-8", "LANGUAGE": "fr"}, "fr"), ({"LANG": "fr_FR.UTF-8", "LANGUAGE": "en_US:fr"}, "en"),
    ({"LANG": "fr_FR.UTF-8", "LANGUAGE": "de:en"}, "en"), ({"LANG": "en_US.UTF-8", "LANGUAGE": "de"}, "en"),
    ({"LANG": "C", "LANGUAGE": "en"}, "fr"), ({"LANG": "C.UTF-8", "LANGUAGE": "en"}, "fr"),
    ({"LANGUAGE": "en"}, "fr"), ({"LC_ALL": "POSIX", "LANG": "en_US.UTF-8", "LANGUAGE": "en"}, "fr"),
    # Chinois : toute locale zh (simplifié ; zh_TW et zh_HK aussi, faute de catalogue traditionnel)
    ({"LANG": "zh_CN.UTF-8"}, "zh"), ({"LANG": "zh_TW.UTF-8"}, "zh"), ({"LANG": "zh_SG"}, "zh"),
    ({"LANG": "fr_FR.UTF-8", "LANGUAGE": "zh_CN:en"}, "zh"), ({"LANG": "zh_CN.UTF-8", "LANGUAGE": "en_US"}, "en"),
    ({"LANG": "zh_CN.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8"}, "fr"), ({"LANG": "C", "LANGUAGE": "zh_CN"}, "fr"),
])
def test_langue_de_la_locale(env, attendue):
    assert i18n.langue_de(env) == attendue


def test_langue_lue_une_fois_dans_l_environnement(monkeypatch):
    i18n.choisir(None)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert i18n.langue() == "en"
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    assert i18n.langue() == "en"  # figée au premier usage : rien ne la change en cours de route
    with pytest.raises(ValueError):
        i18n.choisir("de")


def test_pluriels_selon_la_langue():
    assert i18n.tn("conf.autres_actions", 1) == " (et 1 autre action : voir la liste)"
    assert i18n.tn("conf.autres_actions", 0).startswith(" (et 0 autre action")  # français : 0 au singulier
    i18n.choisir("en")
    assert i18n.tn("conf.autres_actions", 1) == " (and 1 other action: see the list)"
    assert i18n.tn("conf.autres_actions", 2) == " (and 2 other actions: see the list)"
    i18n.choisir("zh")
    assert i18n.tn("conf.autres_actions", 1) == "（另有 1 项操作：见列表）"
    assert i18n.tn("conf.autres_actions", 3) == "（另有 3 项操作：见列表）"
    assert i18n.guillemets("work") == "“work”"


# --- fenêtre de confirmation en anglais : même force -------------------------------------

def _message(*ops, influence=True, appelant="sys-agent", **extra):
    p = schema.parse_plan(json.dumps({"version": 1, "request": "R-0147", "ops": list(ops)}).encode())
    msg = {"type": "confirmer", "appelant": appelant, "canonique": schema.canonicalize(p).decode(),
           "niveaux": [classify.N3] * len(p.ops), "decision": classify.CONFIRMER_RENFORCE, "jeton": "482913",
           "influence": influence,
           "qubes": [["vault", "black", "coffre"], ["sys-net", "red", "utilisateur"], ["work", "blue", "utilisateur"],
                     ["personal", "yellow", "utilisateur"], ["dev", "purple", "agent"],
                     ["debian-13-xfce", "black", "utilisateur-modele"]],
           "expire_dans": 118, "texte_demande": None, "age_demande": None, "hors_portee": []}
    msg.update(extra)
    return msg


def test_la_langue_ne_vient_jamais_de_la_demande():
    """Schéma fermé : un champ « langue » (ou tout autre) envoyé avec la demande la fait refuser."""
    for cle in ("langue", "lang", "locale"):
        with pytest.raises(c.DemandeInvalide):
            c.lire_requete(_message({"op": "vm.start", "name": "work"}, **{cle: "en"}))


def test_avertissements_anglais_de_meme_force():
    i18n.choisir("en")
    v = c.rediger(c.lire_requete(_message({"op": "vm.set_netvm", "name": "vault", "netvm": "sys-net"})))
    assert v.demande == "You did not request this action in the palette"
    assert "Permanent agent" in v.alerte and "counts as influenced for every action" in v.alerte
    assert v.entete == "SYSTEM CORE" and v.sous_entete == "the agent can neither see nor click this window"
    assert v.pied == "Allow: press A on the internal keyboard only"
    assert v.gravite == "high" and v.gravite_forte and v.renforce and v.jeton == "482913"
    assert v.titre == ("The agent wants to connect your Vault “vault” to the Internet through "
                       "“sys-net”")
    assert v.reversible == "yes, but data could leave in the meantime"
    # Jetable d'agent influencé : l'alerte d'influence, pas celle de l'agent permanent.
    v = c.rediger(c.lire_requete(_message({"op": "vm.start", "name": "work"}, appelant="disp1234")))
    assert v.alerte.startswith("The agent has read text that came from a qube")
    # Demande citée, hors portée, autre session.
    v = c.rediger(c.lire_requete(_message({"op": "vm.remove", "name": "work"}, texte_demande="start personal",
                                          age_demande=300, hors_portee=[0], non_nommes=["work"],
                                          autre_session=True)))
    assert v.demande == "typed in the palette, 5 min ago" and v.age_demande == "5 min ago"
    assert "different agent window" in v.alerte and "Your request does not name “work”" in v.alerte
    assert v.reversible.startswith("no: ") and v.irreversible
    assert c.ALERTE_PORTEE == "does not follow from your request"
    assert c.SUITE_NON_VERIFIEE == "This window only checks your request, not what was typed afterwards in the terminal."


def test_etat_anglais_entree_refuse_et_a_au_clavier_interne():
    i18n.choisir("en")
    vue = c.rediger(c.lire_requete(_message({"op": "vm.start", "name": "work"})))
    clavier = c.Clavier("AT Raw Set 2 keyboard", True, "")
    heure = [1000.0]
    e = c.Etat(vue, clavier, 60, lambda: heure[0])
    e.focus(True)
    assert e.clic("autoriser") is None and e.message == "Click ignored: to allow, press A on the internal keyboard."
    assert e.touche("a", "sys-usb: Logitech USB Receiver") is None
    assert e.message == "Keystroke ignored: it does not come from the internal keyboard."
    heure[0] += 2
    assert e.touche("a", "AT Raw Set 2 keyboard") is None and e.message == "First type the code shown, then A."
    assert e.touche("Return", "AT Raw Set 2 keyboard").motif == c.REFUSE  # Entrée = Refuser
    dev = c.Etat(vue, c.Clavier("AT Raw Set 2 keyboard", True, "", dev=True), 60, lambda: 0.0)
    assert dev.message.startswith("DEVELOPMENT MODE: internal keyboard check turned off")
    impossible = c.Etat(vue, c.verifier_clavier(c.ConfigClavier("AT Raw Set 2 keyboard", None), ""), 60, lambda: 0.0)
    assert impossible.message == "Cannot allow: internal keyboard not found. You can still deny."


@pytest.mark.parametrize("op", sorted(EXEMPLES))
def test_chaque_operation_redigee_en_anglais_sans_francais(op):
    i18n.choisir("en")
    o = {"op": op, **EXEMPLES[op]}
    v = c.rediger(c.lire_requete(_message(o, texte_demande="do it", age_demande=30)))
    textes = [v.titre, v.reversible, v.alerte or "", v.demande, v.gravite, v.entete, v.sous_entete, v.pied]
    for a in v.actions:
        textes += [a.phrase, a.nuance, a.contenu] + [e.nom for e in (a.source, a.destination) if e] \
            + [e.mention for e in (a.source, a.destination) if e]
    francais = re.compile(r"[«»]|\b(le|la|les|des|ton|ta|une|pour|pas|dans|avec|sans|veut|espace)\b")
    for texte in textes:
        assert not francais.search(texte), (op, texte)
    assert v.titre.startswith("The agent wants to "), v.titre
    # Le même plan en français garde ses phrases (repli) : rien de mélangé d'une langue à l'autre.
    i18n.choisir("fr")
    assert c.rediger(c.lire_requete(_message(o))).titre.startswith("L'agent veut ")


# --- fenêtre de confirmation en chinois : même force ------------------------------------
# Contre-traduction (zh → fr) relue chaîne par chaîne contre fr.py : chaque assertion
# ci-dessous fige une phrase dont la force a été vérifiée.

def test_avertissements_chinois_de_meme_force():
    i18n.choisir("zh")
    v = c.rediger(c.lire_requete(_message({"op": "vm.set_netvm", "name": "vault", "netvm": "sys-net"})))
    assert v.demande == "你没有在提问框中请求过这项操作"  # « Tu n'as rien demandé dans la palette pour cette action »
    assert v.alerte == "常驻智能体：它的记忆从不清空。每一项操作都按“受影响”对待。"
    assert v.entete == "系统核心" and v.sous_entete == "智能体既看不到也点不到这个窗口"
    assert v.pied == "允许：只能按内置键盘上的 A 键"
    assert v.gravite == "高" and v.gravite_forte and v.renforce and v.jeton == "482913"
    assert v.titre == "智能体想让你的保险库“vault”经由“sys-net”连接互联网"
    assert v.reversible == "是，但在此期间数据可能外流"
    v = c.rediger(c.lire_requete(_message({"op": "vm.start", "name": "work"}, appelant="disp1234")))
    assert v.alerte == "智能体读取过来自某个 qube 的文本（网页、文件、命令输出）：这些文本可能已经操纵了它。"
    v = c.rediger(c.lire_requete(_message({"op": "vm.remove", "name": "work"}, texte_demande="启动 personal",
                                          age_demande=300, hors_portee=[0], non_nommes=["work"],
                                          autre_session=True)))
    assert v.demande == "在提问框中输入，5 分钟前"
    # Phrases collées à la chinoise (chacune finit par « 。 »), aucune espace parasite.
    assert v.alerte == ("常驻智能体：它的记忆从不清空。每一项操作都按“受影响”对待。"
                        "这项操作来自另一个智能体窗口，而不是为你的请求打开的那个：请确认它确实是你的。"
                        "你的请求没有提到“work”。请确认这确实是你想要的。")
    assert v.reversible == "否：该 qube 及其文件将被抹除" and v.irreversible
    assert c.ALERTE_PORTEE == "并非出自你的请求"
    assert c.SUITE_NON_VERIFIEE == "此窗口只核对你的请求，不核对之后在终端里输入的内容。"
    assert i18n.t("conf.fen.refuser") == "拒绝（Enter）" and i18n.t("conf.fen.autoriser") == "允许（A 键）"
    assert i18n.t("conf.fen.renforcee") == "加强确认：请在内置键盘上输入此确认码，再按 A"
    assert i18n.t("conf.fen.minuteur", duree="1:58") == "1:58 后关闭（即拒绝）"


def test_etat_chinois_entree_refuse_et_a_au_clavier_interne():
    i18n.choisir("zh")
    vue = c.rediger(c.lire_requete(_message({"op": "vm.start", "name": "work"})))
    heure = [1000.0]
    e = c.Etat(vue, c.Clavier("AT Raw Set 2 keyboard", True, ""), 60, lambda: heure[0])
    e.focus(True)
    assert e.clic("autoriser") is None and e.message == "点击已忽略：要允许，请按内置键盘上的 A 键。"
    assert e.touche("a", "sys-usb: Logitech USB Receiver") is None
    assert e.message == "按键已忽略：它不是来自内置键盘。"
    heure[0] += 2
    assert e.touche("a", "AT Raw Set 2 keyboard") is None and e.message == "请先输入显示的确认码，再按 A。"
    assert e.touche("Return", "AT Raw Set 2 keyboard").motif == c.REFUSE  # Enter = 拒绝
    dev = c.Etat(vue, c.Clavier("AT Raw Set 2 keyboard", True, "", dev=True), 60, lambda: 0.0)
    assert dev.message.startswith("【开发模式】已关闭内置键盘校验。")  # majuscules du français : 【】
    impossible = c.Etat(vue, c.verifier_clavier(c.ConfigClavier("AT Raw Set 2 keyboard", None), ""), 60, lambda: 0.0)
    assert impossible.message == "无法允许：找不到内置键盘。你仍可以拒绝。"


@pytest.mark.parametrize("op", sorted(EXEMPLES))
def test_chaque_operation_redigee_en_chinois(op):
    i18n.choisir("zh")
    o = {"op": op, **EXEMPLES[op]}
    v = c.rediger(c.lire_requete(_message(o, texte_demande="照做", age_demande=30)))
    textes = [v.titre, v.reversible, v.alerte or "", v.demande, v.gravite, v.entete, v.sous_entete, v.pied]
    for a in v.actions:
        textes += [a.phrase, a.nuance] + [e.nom for e in (a.source, a.destination) if e] \
            + [e.mention for e in (a.source, a.destination) if e]
    etranger = re.compile(r"[«»]|\b(le|la|les|des|ton|une|pour|pas|dans|veut|espace|the|wants|your)\b"
                          r"|  |(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff“])")
    for texte in textes:
        assert not etranger.search(texte), (op, texte)
    assert v.titre.startswith("智能体想"), v.titre


def test_polices_avec_repli_cjk():
    """Le chinois s'affiche en Noto Sans CJK SC (présente dans dom0), jamais en carrés."""
    from qaf_ui import couleurs_plasma
    from qaf_ui.nuit import jetons
    for police in (jetons.POLICE, jetons.POLICE_TITRE, couleurs_plasma.Palette().POLICE):
        assert '"Noto Sans CJK SC"' in police and police.endswith("sans-serif"), police
    assert '"Noto Sans Mono CJK SC"' in jetons.POLICE_CODE and jetons.POLICE_CODE.endswith("monospace")
    assert "Noto Sans CJK SC" in (RACINE / "qaf_ui/fenetre.py").read_text(encoding="utf-8")


def test_fichiers_desktop_en_chinois():
    for p in sorted((RACINE / "packaging").glob("*.desktop")):
        cles = {l.split("=", 1)[0] for l in p.read_text(encoding="utf-8").splitlines() if "=" in l}
        for champ in ("Name", "GenericName", "Comment"):
            if champ in cles:
                assert f"{champ}[zh_CN]" in cles, (p.name, champ)


def test_mots_de_confiance_traduits():
    i18n.choisir("en")
    e = c.Espace("dev", "purple", "agent")
    assert e.mention == "Risky · created by the agent" and e.confiance == c.RISQUE  # identifiant inchangé
    assert c.Espace("debian-13-xfce", "black", "utilisateur-modele").mention == "Template"
    assert c.Espace("vault", "black", "coffre").mention == "Vault"


def test_skill_qaf_repond_dans_la_langue_de_l_utilisateur():
    texte = (RACINE / "qaf_mcp/skill/qaf/SKILL.md").read_text(encoding="utf-8")
    assert "réponds toujours dans la langue de l'utilisateur" in texte


# --- vitrine : README et schémas dans les deux langues -------------------------------------

def test_readme_trilingue():
    fr_md = (RACINE / "README.md").read_text(encoding="utf-8")
    en_md = (RACINE / "README.en.md").read_text(encoding="utf-8")
    zh_md = (RACINE / "README.zh-CN.md").read_text(encoding="utf-8")
    fr_l, en_l = '<a href="README.md">Français</a>', '<a href="README.en.md">English</a>'
    zh_l = '<a href="README.zh-CN.md">中文</a>'
    assert fr_md.splitlines()[0] == f'<p align="center"><strong>Français</strong> · {en_l} · {zh_l}</p>'
    assert en_md.splitlines()[0] == f'<p align="center">{fr_l} · <strong>English</strong> · {zh_l}</p>'
    assert zh_md.splitlines()[0] == f'<p align="center">{fr_l} · {en_l} · <strong>中文</strong></p>'
    # La vitrine chinoise ne parle que du produit : vérifié avec les deux autres par
    # tests/test_vitrine.py (test_vitrine_sans_outillage_de_developpement).
    for md in (fr_md, en_md, zh_md):  # chaque lien local mène à un fichier du dépôt
        for cible in re.findall(r'(?:src|href)="([^"#:]+)"|\]\(([^)#:]+)\)', md):
            chemin = next(c for c in cible if c)
            assert (RACINE / chemin).exists(), chemin
    # Le README anglais montre les schémas anglais, le français les siens.
    for schema_ in ("banniere", "schema-architecture", "schema-flux", "schema-defense"):
        assert f"docs/vitrine/{schema_}-en.svg" in en_md and f"docs/vitrine/{schema_}.svg" in fr_md
        assert f"{schema_}.svg" not in en_md
        # Bannière chinoise ; pour les schémas, pas de version chinoise : les anglais.
        attendu = "banniere-zh" if schema_ == "banniere" else f"{schema_}-en"
        assert f"docs/vitrine/{attendu}.svg" in zh_md


def test_banniere_chinoise():
    en_svg = (RACINE / "docs/vitrine/banniere-en.svg").read_text(encoding="utf-8")
    zh_svg = (RACINE / "docs/vitrine/banniere-zh.svg").read_text(encoding="utf-8")
    # Même dessin : seuls les textes (et la police, avec repli CJK) changent.
    assert re.sub(r">[^<>]+<", "><", en_svg).count("<") == re.sub(r">[^<>]+<", "><", zh_svg).count("<")
    assert "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', sans-serif" in zh_svg
    textes = re.findall(r">([^<>]+)<", zh_svg) + re.findall(r'aria-label="([^"]*)"', zh_svg)
    assert not [x for x in textes if re.search(r"[éèêàùçôîâûœ«»]", x)]
    phrases = [x for x in textes if re.search(r"[一-鿿]", x)]
    assert len(phrases) >= 5  # titre, deux lignes d'accroche, sous-titre, « 不含 LLM », « 计划 »
    assert "智能体" in zh_svg and "Doorman" in zh_svg and "虚拟机" not in zh_svg
    assert "docs/vitrine/banniere-zh.svg" in (RACINE / "README.zh-CN.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("nom", ["banniere", "schema-architecture", "schema-flux", "schema-defense"])
def test_schemas_anglais(nom):
    fr_svg = (RACINE / f"docs/vitrine/{nom}.svg").read_text(encoding="utf-8")
    en_svg = (RACINE / f"docs/vitrine/{nom}-en.svg").read_text(encoding="utf-8")
    textes = re.findall(r">([^<>]+)<", en_svg) + re.findall(r'aria-label="([^"]*)"', en_svg)
    assert not [x for x in textes if re.search(r"[éèêàùçôîâûœ«»]", x)], nom
    # Même dessin : seuls les textes changent.
    assert re.sub(r">[^<>]+<", "><", fr_svg).count("<") == re.sub(r">[^<>]+<", "><", en_svg).count("<")
