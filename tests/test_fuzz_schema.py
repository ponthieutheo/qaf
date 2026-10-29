"""Fuzzing du parseur de plans (critère S2 : 10 000 entrées sans plan invalide accepté).

Deux propriétés :
1. aucun plan invalide accepté : parse_plan accepte une entrée SI ET SEULEMENT SI
   un oracle écrit ici, indépendamment de schema.py, à partir de la spécification,
   la juge valide ;
2. parse_plan(canonicalize(p)) == p pour tout plan accepté.

Total par défaut : 4 000 + 3 000 + 3 000 + 2 000 = 12 000 entrées.
QAF_FUZZ_FACTEUR=10 multiplie ces nombres (fuzzing long, à la main ou en CI de nuit).
"""

import ipaddress
import json
import os
import unicodedata

import pytest
from hypothesis import HealthCheck, event, given, settings, strategies as st

from qaf_gate.schema import MAX_BYTES, PlanError, canonicalize, parse_plan, to_dict
from exemples_ops import EXEMPLES

FACTEUR = int(os.environ.get("QAF_FUZZ_FACTEUR", "1"))


def reglages(n):
    return settings(
        max_examples=n * FACTEUR,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much,
                               HealthCheck.data_too_large],
    )


# --- Oracle indépendant (spécification : schema.py docstring, PLAN-v2 §2-3, validate_name) ---

ASCII_LETTRES = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHIFFRES = "0123456789"
LABELS_SPEC = {"red", "orange", "yellow", "green", "gray", "blue", "purple", "black"}
RESERVES = {"Domain-0", "Dom0", "none", "default"}


def nom_ok(v):
    return (
        isinstance(v, str)
        and 1 <= len(v) <= 31
        and v[0] in ASCII_LETTRES
        and all(c in ASCII_LETTRES + CHIFFRES + "_.-" for c in v)
        and v not in RESERVES
        and not v.endswith("-dm")
    )


# Spécification (revue P2-3) : contrôle, format, surrogats, séparateurs de
# ligne/paragraphe, usage privé, non assignés. Liste écrite ici, pas importée.
INTERDITS = ("Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn")


def tache_ok(v):
    return (
        isinstance(v, str)
        and 1 <= len(v) <= 1000
        and all(c == "\n" or unicodedata.category(c) not in INTERDITS for c in v)
    )


SPEC_OPS = {
    "vm.list": ({}, {}),
    "vm.info": ({"name": nom_ok}, {}),
    "vm.create": ({"name": nom_ok, "template": nom_ok,
                   "label": lambda v: isinstance(v, str) and v in LABELS_SPEC}, {}),
    "vm.start": ({"name": nom_ok}, {}),
    "vm.shutdown": ({"name": nom_ok}, {}),
    "vm.remove": ({"name": nom_ok}, {}),
    "vm.set_netvm": ({"name": nom_ok, "netvm": lambda v: v is None or nom_ok(v)}, {}),
    "dispvm.run_task": ({"task": tache_ok}, {"template": nom_ok}),
}

# OPS-v3 (docs/OPS-v3.md §3.8, §4 à §10), écrit ici sans lire schema.py.
ALNUM = ASCII_LETTRES + CHIFFRES
HEXA = "0123456789abcdefABCDEF"


def entier_ok(a, b):
    return lambda v: type(v) is int and a <= v <= b


def bool_ok(v):
    return type(v) is bool


def dans(*valeurs):
    return lambda v: isinstance(v, str) and v in valeurs


def noms_ok(a, b):
    return lambda v: (isinstance(v, list) and a <= len(v) <= b and all(nom_ok(x) for x in v)
                      and len(set(v)) == len(v))


def paquet_ok(p):
    return isinstance(p, str) and (p in ("g++", "c++", "gcc-c++") or (
        1 <= len(p) <= 63 and p[0] in ALNUM and p[-1] in ALNUM and all(c in ALNUM + "+._-" for c in p)))


def paquets_ok(v):
    return isinstance(v, list) and 1 <= len(v) <= 8 and all(paquet_ok(p) for p in v) and len(set(v)) == len(v)


def argv_ok(v):
    return (isinstance(v, list) and 1 <= len(v) <= 64
            and all(isinstance(a, str) and len(a) <= 4096 and chr(0) not in a for a in v)
            and v[0] != "" and sum(map(len, v)) <= 8192)


def texte_ok(n):
    return lambda v: (isinstance(v, str) and 1 <= len(v) <= n
                      and all(c == chr(10) or unicodedata.category(c) not in INTERDITS for c in v))


def fenetre_ok(v):
    return isinstance(v, str) and (v == "racine" or (v[:2] == "0x" and 1 <= len(v) - 2 <= 8
                                                     and all(c in HEXA for c in v[2:])))


def touches_ok(v):
    if not isinstance(v, str):
        return False
    morceaux = v.split("+")
    return 1 <= len(morceaux) <= 5 and all(1 <= len(m) <= 24 and all(c in ALNUM + "_" for c in m) for m in morceaux)


def hote_ok(v):
    if not isinstance(v, str) or len(v) > 253:
        return False
    try:
        ipaddress.ip_network(v, strict=True)
        return True
    except ValueError:
        pass
    return all(1 <= len(lab) <= 63 and lab[0] in ALNUM and lab[-1] in ALNUM and all(c in ALNUM + "-" for c in lab)
               for lab in v.split("."))


def ports_ok(v):
    if not isinstance(v, str):
        return False
    bornes = v.split("-")
    if len(bornes) > 2 or not all(1 <= len(b) <= 5 and all(c in CHIFFRES for c in b) for b in bornes):
        return False
    return 1 <= int(bornes[0]) <= int(bornes[-1]) <= 65535


def regle_ok(r):
    if not isinstance(r, dict) or r.get("action") not in ("accept", "drop"):
        return False
    autres = set(r) - {"action"}
    if "dns" in r:
        return autres == {"dns"} and r["dns"] is True
    return (autres <= {"host", "proto", "ports"} and ("host" not in r or hote_ok(r["host"]))
            and ("proto" not in r or r["proto"] in ("tcp", "udp", "icmp"))
            and ("ports" not in r or (r.get("proto") in ("tcp", "udp") and ports_ok(r["ports"]))))


def clavier_ok(v):
    if not isinstance(v, str) or v.count("+") != 2:
        return False
    a, b, c = v.split("+")
    return (2 <= len(a) <= 8 and all(x in "abcdefghijklmnopqrstuvwxyz" for x in a) and len(b) <= 16
            and all(x in ALNUM + "_-" for x in b) and len(c) <= 32 and all(x in ALNUM + "_,:-" for x in c))


PROPS_SPEC = {"label": lambda v: isinstance(v, str) and v in LABELS_SPEC, "memory": entier_ok(256, 65536),
              "maxmem": entier_ok(0, 65536), "vcpus": entier_ok(1, 64), "autostart": bool_ok,
              "include_in_backups": bool_ok, "keyboard_layout": clavier_ok, "shutdown_timeout": entier_ok(10, 3600),
              "qrexec_timeout": entier_ok(10, 3600), "default_dispvm": lambda v: v is None or nom_ok(v),
              "template_for_dispvms": bool_ok, "provides_network": bool_ok}
PREFS_SPEC = {"default_template": nom_ok, "default_dispvm": lambda v: v is None or nom_ok(v),
              "check_updates_vm": bool_ok}
NOM = {"name": nom_ok}
PF = {"name": nom_ok, "position": entier_ok(0, 63), "rule": regle_ok}


def tout(v):
    return True  # value : vérifiée selon prop dans op_ok


SPEC_OPS.update({
    "vm.settings": (NOM, {}), "update.status": ({}, {"targets": noms_ok(0, 64)}), "sys.status": ({}, {}),
    "prefs.get": ({}, {}), "fw.list": (NOM, {}), "dev.list": ({}, {"class": dans("usb", "block", "mic")}),
    "vm.set_prop": ({"name": nom_ok, "prop": dans(*PROPS_SPEC), "value": tout}, {}),
    "vm.reset_prop": ({"name": nom_ok, "prop": dans(*PROPS_SPEC)}, {}),
    "vm.set_service": ({"name": nom_ok, "service": dans("crond", "cups", "qubes-update-check", "network-manager"),
                        "state": dans("on", "off", "default")}, {}),
    "vm.restart": (NOM, {}), "vm.set_template": ({"name": nom_ok, "template": nom_ok}, {}),
    "pkg.install": ({"name": nom_ok, "packages": paquets_ok}, {}), "dispvm.create": ({}, {"template": nom_ok}),
    "qube.exec": ({"name": nom_ok, "argv": argv_ok, "timeout": entier_ok(1, 120)}, {}),
    "qube.windows": ({"name": nom_ok, "hidden": bool_ok}, {}),
    "qube.screenshot": ({"name": nom_ok, "window": fenetre_ok}, {}),
    "qube.type": ({"name": nom_ok, "text": texte_ok(2000)}, {"window": fenetre_ok}),
    "qube.key": ({"name": nom_ok, "keys": touches_ok}, {"window": fenetre_ok}),
    "qube.mouse": ({"name": nom_ok, "x": entier_ok(0, 16383), "y": entier_ok(0, 16383),
                    "button": entier_ok(0, 3)}, {"window": fenetre_ok}),
    "qube.scroll": ({"name": nom_ok, "direction": dans("haut", "bas"), "steps": entier_ok(1, 20)}, {}),
    "prefs.set": ({"prop": dans(*PREFS_SPEC), "value": tout}, {}),
    "fw.add": (PF, {}), "fw.remove": (PF, {}),
})
LOURDES_SPEC = {"dev.list", "qube.exec", "qube.windows", "qube.screenshot", "pkg.install"}


# Lots 2 et 3 (OPS-v3 §3.8 et sondes P-6/P-13 du §13.1), écrit ici sans lire schema.py.
def chemin_ok(v):
    if not isinstance(v, str) or not 1 <= len(v) <= 255:
        return False
    if any(unicodedata.category(c) in INTERDITS for c in v):
        return False
    return all(p != "" and (p[0].isalnum() or p[0] == "_") for p in v.split("/"))


def dev_ok(n):
    return lambda v: (isinstance(v, str) and 1 <= len(v) <= n and v != "_"
                      and all(c in ALNUM + "_.:-" for c in v))


def motif_ok(prefixe, chiffres):
    return lambda v: (isinstance(v, str) and len(v) == len(prefixe) + chiffres and v.startswith(prefixe)
                      and all(c in CHIFFRES for c in v[len(prefixe):]))


def profil_ok(v):
    return isinstance(v, str) and 1 <= len(v) <= 64 and all(c in ALNUM + "_-" for c in v)


def tag_ok(v):
    return (isinstance(v, str) and 1 <= len(v) <= 31 and v[0] in "abcdefghijklmnopqrstuvwxyz"
            and all(c in "abcdefghijklmnopqrstuvwxyz" + CHIFFRES + "_-" for c in v))


DEPOT = dans("official", "community")
CLASSE = dans("usb", "block", "mic")
FICHIER = {"source": nom_ok, "path": chemin_ok, "dest": nom_ok}
SPEC_OPS.update({
    "update.run": ({"targets": noms_ok(1, 32), "apply": dans("none", "sys", "all")}, {}),
    "template.available": ({"repo": DEPOT}, {}), "template.install": ({"template": nom_ok, "repo": DEPOT}, {}),
    "template.remove": ({"template": nom_ok}, {}),
    "job.status": ({"job": motif_ok("T-", 6)}, {}), "job.cancel": ({"job": motif_ok("T-", 6)}, {}),
    "enter.open": ({"name": nom_ok, "minutes": entier_ok(1, 60)}, {}), "enter.close": (NOM, {}),
    "file.copy": (FICHIER, {}), "file.move": (FICHIER, {}),
    "file.open_dispvm": ({"name": nom_ok, "path": chemin_ok}, {}),
    "dev.attach": ({"class": CLASSE, "backend": nom_ok, "port": dev_ok(64), "device_id": dev_ok(256),
                    "target": nom_ok}, {"read_only": bool_ok}),
    "dev.detach": ({"class": CLASSE, "backend": nom_ok, "port": dev_ok(64), "target": nom_ok}, {}),
    "backup.profiles": ({}, {}), "backup.run": ({"profile": profil_ok}, {}),
    "pkg.remove": ({"name": nom_ok, "packages": paquets_ok}, {}),
    "vm.tag": ({"name": nom_ok, "tag": tag_ok, "action": dans("add", "remove")}, {}),
    "vm.grow_private": ({"name": nom_ok, "size_mib": entier_ok(1024, 1048576)}, {}),
})
LOURDES_SPEC |= {"pkg.remove", "template.available", "file.copy", "file.move"}
# PARCOURS-1 : clone d'un modèle en modèle de l'agent (lourd : copie du disque).
SPEC_OPS["vm.clone"] = ({"name": nom_ok, "source": nom_ok, "label": dans(*LABELS_SPEC)}, {})
LOURDES_SPEC.add("vm.clone")


def session_ok(v):
    return isinstance(v, str) and len(v) == 18 and v[:2] == "S-" and all(c in "0123456789abcdef" for c in v[2:])


def op_ok(o):
    if not isinstance(o, dict) or not isinstance(o.get("op"), str) or o["op"] not in SPEC_OPS:
        return False
    oblig, facult = SPEC_OPS[o["op"]]
    champs = set(o) - {"op"}
    if not set(oblig) <= champs or not champs <= set(oblig) | set(facult):
        return False
    if not all({**oblig, **facult}[k](o[k]) for k in champs):
        return False
    table = {"vm.set_prop": PROPS_SPEC, "prefs.set": PREFS_SPEC}.get(o["op"])
    return table is None or table[o["prop"]](o["value"])


def plan_ok(obj, taille):
    return (
        taille <= MAX_BYTES
        and isinstance(obj, dict)
        and {"version", "request", "ops"} <= set(obj) <= {"version", "request", "ops", "session"}
        and ("session" not in obj or session_ok(obj["session"]))
        and type(obj["version"]) is int and obj["version"] == 1
        and isinstance(obj["request"], str)
        and obj["request"][:2] == "R-"
        and 4 <= len(obj["request"]) - 2 <= 8
        and all(c in CHIFFRES for c in obj["request"][2:])
        and isinstance(obj["ops"], list)
        and 1 <= len(obj["ops"]) <= 16
        and all(op_ok(o) for o in obj["ops"])
        and sum(o["op"] in LOURDES_SPEC for o in obj["ops"]) <= 1
    )


def verifier(data, obj=None):
    """Compare le parseur à l'oracle. obj = objet JSON d'origine s'il est connu."""
    try:
        p = parse_plan(data)
    except PlanError:
        if obj is not None:
            assert not plan_ok(obj, len(data)), f"plan valide refusé : {data[:300]!r}"
        event("refusé")
        return None
    except Exception as e:  # tout autre exception = défaut du parseur
        raise AssertionError(f"exception non prévue {type(e).__name__} pour {data[:300]!r}")
    # Accepté : l'objet relu doit être valide selon l'oracle et identique au plan.
    relu = json.loads(data.decode("utf-8")) if obj is None else obj
    assert plan_ok(relu, len(data)), f"plan INVALIDE accepté : {data[:300]!r}"
    event("accepté")
    assert to_dict(p) == relu
    c = canonicalize(p)
    assert parse_plan(c) == p
    assert canonicalize(parse_plan(c)) == c
    return p


# --- Générateurs ---

NOMS_PIEGES = [
    "dom0", "Dom0", "Domain-0", "none", "default", "work-dm", "a" * 31, "a" * 32, "", "1abc",
    "-x", ".x", "_x", "a b", "a\n", "a/b", "a:b", "é", "ａbc", "vault", "sys-net", "a‮",
    "a\x00", "A", "x-dm2", "DOM0", "@dispvm", "$adminvm", "\ud800",
]
nom_valide = st.builds(
    lambda a, b: a + b,
    st.sampled_from(ASCII_LETTRES),
    st.text(alphabet=ASCII_LETTRES + CHIFFRES + "_.-", max_size=30),
).filter(nom_ok)
tache_valide = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=INTERDITS) | st.just("\n"),
    min_size=1, max_size=200,
)
requete_valide = st.builds(lambda c: "R-" + c, st.text(alphabet=CHIFFRES, min_size=4, max_size=8))
nom = st.one_of(nom_valide, st.sampled_from(NOMS_PIEGES), st.text(max_size=33))
label = st.one_of(st.sampled_from(sorted(LABELS_SPEC)), st.sampled_from(["Red", "white", ""]),
                  st.text(max_size=8))
tache = st.one_of(
    st.text(min_size=1, max_size=60),
    st.text(alphabet=st.characters(codec="utf-8"), min_size=0, max_size=1002),
    st.sampled_from(["x" * 1000, "x" * 1001, "", "ligne\nligne", "a\rb", "a⁦b", "a​b",
                     "a\tb", "﻿"]),
)
scalaire = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text(max_size=10))
json_quelconque = st.recursive(
    scalaire,
    lambda e: st.lists(e, max_size=4) | st.dictionaries(st.text(max_size=10), e, max_size=4),
    max_leaves=15,
)
valeur = st.one_of(nom, label, tache, scalaire, json_quelconque)
CHAMPS = ["op", "name", "template", "label", "netvm", "task", "guivm", "tags", "cmd", "stdin", "value", "prop",
          "argv", "rule", "packages", "targets", "window", "keys", "path", "source", "dest", "device_id", "port",
          "backend", "target", "job", "profile", "repo", "apply", "tag", "minutes", "size_mib", "read_only"]
# Pièges des lots 2 et 3 : jokers de périphérique, chemins hors du dossier personnel.
PIEGES_V3 = ["*", "_", "", "_x", "..", "../x", ".ssh/id", "a//b", "-x", "a/", "/etc", "a\nb", "a\0b", "~", "é/x",
             "T-12345", "T-1234567", "t-123456", "x" * 256, "a:b::c", "2-1", "sys-usb:2-1"]
valeur = st.one_of(valeur, st.sampled_from(PIEGES_V3))
# Exemples OPS-v3 des tests (valides selon l'oracle ci-dessus), noms tirés au hasard.
EXEMPLES_V3 = {k: v for k, v in EXEMPLES.items() if k not in (
    "vm.list", "vm.info", "vm.create", "vm.start", "vm.shutdown", "vm.remove", "vm.set_netvm", "dispvm.run_task")}
NOMS_OPS = list(SPEC_OPS) + ["vm.tag_add", "VM.START", "vm.start ", "qube.copy", "", "dom0.shell"]


@st.composite
def op_valide(draw):
    o = draw(st.sampled_from(sorted(SPEC_OPS)))
    d = {"op": o}
    if o in ("vm.info", "vm.start", "vm.shutdown", "vm.remove", "vm.create", "vm.set_netvm"):
        d["name"] = draw(nom_valide)
    if o == "vm.create":
        d["template"] = draw(nom_valide)
        d["label"] = draw(st.sampled_from(sorted(LABELS_SPEC)))
    if o == "vm.set_netvm":
        d["netvm"] = draw(st.none() | nom_valide)
    if o == "dispvm.run_task":
        d["task"] = draw(tache_valide)
        if draw(st.booleans()):
            d["template"] = draw(nom_valide)
    if o in EXEMPLES_V3:  # exemple fixe, nom de qube tiré au hasard
        d.update(EXEMPLES_V3[o])
        if "name" in d:
            d["name"] = draw(nom_valide)
    return d


@st.composite
def op_mutee(draw):
    d = draw(op_valide())
    for _ in range(draw(st.integers(1, 3))):
        action = draw(st.sampled_from(["suppr", "remplacer", "ajouter", "op"]))
        if action == "suppr" and d:
            d.pop(draw(st.sampled_from(sorted(d))))
        elif action == "remplacer" and d:
            d[draw(st.sampled_from(sorted(d)))] = draw(valeur)
        elif action == "ajouter":
            d[draw(st.sampled_from(CHAMPS) | st.text(max_size=8))] = draw(valeur)
        else:
            d["op"] = draw(st.sampled_from(NOMS_OPS) | st.text(max_size=12))
    return d


requete = st.one_of(
    requete_valide,
    st.sampled_from(["R-123", "R-123456789", "r-0001", "R-0001\n", "R-٠١٢٣", "R-00 1", ""]),
    st.text(max_size=12),
)


@st.composite
def plan_valide(draw):
    ops = draw(st.lists(op_valide(), min_size=1, max_size=16))
    lourdes = [i for i, o in enumerate(ops) if o["op"] in LOURDES_SPEC]
    ops = [o for i, o in enumerate(ops) if i not in lourdes[1:]]
    return {"version": 1, "request": draw(requete_valide), "ops": ops}


@st.composite
def plan_mute(draw):
    """Plan valide auquel on applique 0 à 2 mutations : explore la frontière."""
    p = draw(plan_valide())
    for _ in range(draw(st.integers(0, 2))):
        m = draw(st.sampled_from(["version", "requete", "op", "op", "cle", "liste", "ops"]))
        if m == "version":
            p["version"] = draw(st.sampled_from([2, 0, -1, True, False, 1.0, "1", None, [1]]))
        elif m == "requete":
            p["request"] = draw(requete)
        elif m == "op" and p.get("ops") and isinstance(p["ops"], list):
            p["ops"][draw(st.integers(0, len(p["ops"]) - 1))] = draw(op_mutee())
        elif m == "cle":
            if draw(st.booleans()) and p:
                p.pop(draw(st.sampled_from(sorted(p))))
            else:
                p[draw(st.sampled_from(["extra", "pourquoi", "mode", "Ops"]) | st.text(max_size=6))] = draw(valeur)
        elif m == "liste":
            p["ops"] = draw(st.sampled_from([[], [{"op": "vm.list"}] * 17]))
        else:
            p["ops"] = draw(json_quelconque)
    return p


def encoder(obj, ascii_):
    return json.dumps(obj, ensure_ascii=ascii_).encode("utf-8", "surrogatepass")


# --- Propriétés ---

@reglages(4000)
@given(plan_mute(), st.booleans())
def test_plans_mutes(obj, ascii_):
    verifier(encoder(obj, ascii_), obj)


@reglages(3000)
@given(json_quelconque | st.lists(op_mutee(), max_size=3), st.booleans())
def test_json_quelconque(obj, ascii_):
    verifier(encoder(obj, ascii_), obj)


@st.composite
def octets(draw):
    """Octets bruts : purement aléatoires, ou un plan valide abîmé octet par octet."""
    if draw(st.booleans()):
        return draw(st.binary(max_size=300))
    b = bytearray(encoder(draw(plan_valide()), draw(st.booleans())))
    for _ in range(draw(st.integers(1, 4))):
        i = draw(st.integers(0, len(b)))
        action = draw(st.sampled_from(["changer", "inserer", "suppr"]))
        if action == "inserer" or not b:
            b[i:i] = draw(st.binary(min_size=1, max_size=4))
        elif action == "changer":
            b[min(i, len(b) - 1)] = draw(st.integers(0, 255))
        else:
            del b[min(i, len(b) - 1)]
    return bytes(b)


@reglages(3000)
@given(octets())
def test_octets_bruts(data):
    verifier(data)


@reglages(2000)
@given(plan_valide(), st.booleans())
def test_plans_valides_aller_retour(obj, ascii_):
    data = encoder(obj, ascii_)
    p = verifier(data, obj)
    assert p is not None, "un plan valide doit être accepté"


# Régressions trouvées par le fuzzing.

@pytest.mark.parametrize("op", [[], {}, ["vm.list"], {"a": 1}])
def test_regression_op_non_hachable(op):
    # « [] in OPS » levait TypeError au lieu de PlanError.
    with pytest.raises(PlanError):
        parse_plan(json.dumps({"version": 1, "request": "R-0000", "ops": [{"op": op}]}).encode())
