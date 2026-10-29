import json

import pytest

from qaf_gate.schema import MAX_BYTES, PlanError, canonicalize, parse_plan


def plan(*ops, request="R-0142", version=1):
    return json.dumps({"version": version, "request": request, "ops": list(ops)}).encode()


VALIDES = [
    {"op": "vm.list"},
    {"op": "vm.info", "name": "work"},
    {"op": "vm.create", "name": "dev", "template": "debian-13-xfce", "label": "blue"},
    {"op": "vm.start", "name": "dev"},
    {"op": "vm.shutdown", "name": "dev"},
    {"op": "vm.remove", "name": "dev"},
    {"op": "vm.set_netvm", "name": "dev", "netvm": "sys-firewall"},
    {"op": "vm.set_netvm", "name": "dev", "netvm": None},
    {"op": "dispvm.run_task", "task": "ouvre exemple.fr et remplis le formulaire"},
    {"op": "dispvm.run_task", "task": "x", "template": "default-dvm"},
]


@pytest.mark.parametrize("op", VALIDES)
def test_plans_valides(op):
    p = parse_plan(plan(op))
    assert p.request == "R-0142" and p.ops[0].op == op["op"]


def test_canonicalize_deterministe_et_stable():
    a = parse_plan(b'{"ops":[{"label":"red","template":"t","name":"a","op":"vm.create"}],'
                   b'"request":"R-0001","version":1}')
    b = parse_plan(plan({"op": "vm.create", "name": "a", "template": "t", "label": "red"}, request="R-0001"))
    assert canonicalize(a) == canonicalize(b)
    assert parse_plan(canonicalize(a)) == a
    assert b" " not in canonicalize(a)


@pytest.mark.parametrize("nom", [
    "1abc", "-a", "_a", ".a", "a b", "a/b", "a+b", "é", "", "a" * 32,
    "Dom0", "Domain-0", "none", "default", "sys-net-dm", 3, None,
])
def test_noms_refuses(nom):
    with pytest.raises(PlanError):
        parse_plan(plan({"op": "vm.start", "name": nom}))


def test_nom_31_caracteres_accepte():
    parse_plan(plan({"op": "vm.start", "name": "a" * 31}))


INVALIDES = [
    b"",
    b"\xff\xfe",
    b"[]",
    b"null",
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.list"}],"extra":1}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.list"}],"version":1}',
    b'{"version":1.0,"request":"R-0142","ops":[{"op":"vm.list"}]}',
    b'{"version":true,"request":"R-0142","ops":[{"op":"vm.list"}]}',
    b'{"version":2,"request":"R-0142","ops":[{"op":"vm.list"}]}',
    b'{"version":1,"request":"R-12","ops":[{"op":"vm.list"}]}',
    b'{"version":1,"request":"r-0142","ops":[{"op":"vm.list"}]}',
    b'{"version":1,"request":"R-0142\\n","ops":[{"op":"vm.list"}]}',
    b'{"version":1,"request":"R-0142","ops":[]}',
    b'{"version":1,"request":"R-0142","ops":{}}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.list","x":NaN}]}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"shell","cmd":"rm -rf /"}]}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.list","name":"a"}]}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.start"}]}',
    b'{"version":1,"request":"R-0142","ops":[{"op":"vm.start","name":"a","name":"b"}]}',
    b'{"version":1,"request":"R-0142","ops":["vm.list"]}',
    b'[' * 100000,
]


@pytest.mark.parametrize("data", INVALIDES)
def test_plans_invalides(data):
    with pytest.raises(PlanError):
        parse_plan(data)


@pytest.mark.parametrize("op", [
    # vm.create n'accepte aucun champ netvm (C2)
    {"op": "vm.create", "name": "a", "template": "t", "label": "red", "netvm": "sys-net"},
    {"op": "vm.create", "name": "a", "template": "t", "label": "rose"},
    {"op": "vm.create", "name": "a", "template": "t"},
    {"op": "vm.set_netvm", "name": "a"},
    {"op": "dispvm.run_task", "task": ""},
    {"op": "dispvm.run_task", "task": "x" * 1001},
    {"op": "dispvm.run_task", "task": "a\x00b"},
    {"op": "dispvm.run_task", "task": "a\u202eb"},  # inversion bidi
    {"op": "dispvm.run_task", "task": "a\ud800b"},  # surrogat isolé
    {"op": "dispvm.run_task", "task": "x", "stdin": "y"},
])
def test_operations_invalides(op):
    with pytest.raises(PlanError):
        parse_plan(plan(op))


def test_bornes():
    with pytest.raises(PlanError):
        parse_plan(b" " * (MAX_BYTES + 1))
    with pytest.raises(PlanError):
        parse_plan(plan(*[{"op": "vm.list"}] * 17))
    parse_plan(plan(*[{"op": "vm.list"}] * 16))
    with pytest.raises(PlanError):
        parse_plan("pas des octets")


# Fuzzing léger (le fuzzing 10 000 entrées de S2 viendra dans un fichier dédié).
from hypothesis import given, settings, strategies as st

_json = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=40),
    lambda e: st.lists(e, max_size=4) | st.dictionaries(st.text(max_size=12), e, max_size=4),
    max_leaves=20,
)


@settings(max_examples=500)
@given(st.binary(max_size=200) | _json.map(lambda v: json.dumps(v).encode()))
def test_fuzz_parse_plan(data):
    try:
        p = parse_plan(data)
    except PlanError:
        return
    assert parse_plan(canonicalize(p)) == p


@pytest.mark.parametrize("car", ["\u2028", "\u2029", "\ue000", "\U000f0000", "\u0378"])
def test_tache_refuse_separateurs_prives_non_assignes(car):
    # P2-3 : Zl, Zp, Co (usage privé), Cn (non assigné).
    with pytest.raises(PlanError):
        parse_plan(plan({"op": "dispvm.run_task", "task": "a" + car + "b"}))


def test_messages_d_erreur_ne_recopient_pas_les_cles():
    # P2-2 : une clé de 60 000 caractères ou piégée ne repart pas dans « raison ».
    geante = "k\u202e" + "x" * 60000
    for data in (plan({"op": "vm.list", geante: 1}),
                 b'{"version":1,"request":"R-0001","' + geante.encode() + b'":1,"'
                 + geante.encode() + b'":2,"ops":[]}'):
        with pytest.raises(PlanError) as e:
            parse_plan(data)
        msg = str(e.value)
        assert len(msg) < 200 and "\u202e" not in msg, msg
    with pytest.raises(PlanError) as e:
        parse_plan(plan({"op": "vm.list", **{f"c{i}": i for i in range(50)}}))
    assert len(str(e.value)) < 200
