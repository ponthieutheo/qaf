import json

import pytest

from backend_sim import SimBackend
from qaf_gate.journal import GENESE, Journal, JournalError, verify
from qaf_gate.registry import Registry


def test_registre_persiste_et_uuid(tmp_path):
    p = tmp_path / "registre.json"
    s = SimBackend()
    q = s.create_appvm("dev", "debian-13-xfce", "blue")
    r = Registry(str(p))
    r.add("dev", q.uuid, "R-0001")
    assert Registry(str(p)).agent_qubes(s.list_qubes()) == {"dev"}
    # L'utilisateur supprime puis recrée « dev » : ce n'est plus un qube de l'agent
    s.remove("dev")
    s.create_appvm("dev", "debian-13-xfce", "red")
    assert r.agent_qubes(s.list_qubes()) == set()
    r.remove("dev")
    assert Registry(str(p)).qubes == {}
    assert [f.name for f in tmp_path.iterdir()] == ["registre.json"]  # pas de temporaire


def test_registre_corrompu(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"version": 9, "qubes": {}}')
    with pytest.raises(ValueError):
        Registry(str(p))


def test_journal_chaine(tmp_path):
    p = str(tmp_path / "journal")
    j = Journal(p)
    h1 = j.append({"plan": "R-0001", "decision": "auto"})
    j.append({"plan": "R-0002", "decision": "confirmer", "texte": "é"})
    assert verify(p) == 2
    j2 = Journal(p)  # reprise après redémarrage
    j2.append({"plan": "R-0003"})
    assert verify(p) == 3
    lignes = open(p, encoding="utf-8").read().splitlines()
    assert json.loads(lignes[0])["prev"] == GENESE
    assert json.loads(lignes[1])["prev"] == h1


@pytest.mark.parametrize("alteration", ["modifier", "supprimer", "inverser", "tronquer"])
def test_journal_altere_detecte(tmp_path, alteration):
    p = tmp_path / "journal"
    j = Journal(str(p))
    for i in range(3):
        j.append({"n": i})
    lignes = p.read_text(encoding="utf-8").splitlines()
    if alteration == "modifier":
        lignes[1] = lignes[1].replace('"n":1', '"n":7')
    elif alteration == "supprimer":
        del lignes[1]
    elif alteration == "inverser":
        lignes[0], lignes[1] = lignes[1], lignes[0]
    else:
        lignes[2] = lignes[2][:20]
    p.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    with pytest.raises(JournalError):
        verify(str(p))
    with pytest.raises(JournalError):
        Journal(str(p))  # refuse d'écrire à la suite


def test_journal_vide(tmp_path):
    assert verify(str(tmp_path / "absent")) == 0
