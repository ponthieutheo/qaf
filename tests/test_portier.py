"""Découpage en 3 processus : talon, validateur, exécuteur (C4/C5)."""

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from qaf_gate import classify, confirm, executor, pause, proto, schema, stub, validator
from backend_sim import SimBackend
from qaf_gate.journal import Journal, verify
from qaf_gate.registry import Registry

STUB = os.path.join(os.path.dirname(stub.__file__), "stub.py")


def plan(*ops, request="R-0001"):
    return json.dumps({"version": 1, "request": request, "ops": list(ops)}).encode()


CREER = {"op": "vm.create", "name": "dev", "template": "debian-13-xfce", "label": "blue"}


@pytest.fixture
def dossier():
    d = tempfile.mkdtemp(prefix="qaf-", dir="/tmp")  # chemins de socket courts
    yield d
    shutil.rmtree(d, ignore_errors=True)


def faire_executeur(d, reponses=()):
    conf = confirm.Scripte(reponses)
    ex = executor.Executeur(
        SimBackend(), Registry(f"{d}/registre.json"), Journal(f"{d}/journal.jsonl"),
        conf, pauses=(f"{d}/pause", f"{d}/pause-auto"), pause_auto=f"{d}/pause-auto",
    )
    ex.etat["influence"] = True  # comme après une sortie de qube lue par l'agent (essais d'avant PARCOURS-1)
    return ex, conf


def faire_validateur(ex, d, **kw):
    cfg = validator.Config(pauses=(f"{d}/pause", f"{d}/pause-auto"), **kw)
    return validator.Validateur(ex.traiter, cfg)


# --- talon ---------------------------------------------------------------

def test_talon_limite_alignee_sur_schema():
    assert stub.MAX_ENTREE == schema.MAX_BYTES


def test_talon_coupe_une_entree_geante():
    lecture, ecriture = os.pipe()
    def ecrire():
        try:
            with os.fdopen(ecriture, "wb") as f:
                for _ in range(100 * 16):  # 100 Mio par blocs de 64 Kio
                    f.write(b"x" * 65536)
        except (BrokenPipeError, OSError):
            pass
    t = threading.Thread(target=ecrire, daemon=True)
    t.start()
    debut = time.monotonic()
    statut, data = stub.lire_borne(lecture, delai=5)
    assert (statut, data) == ("trop_grand", b"")
    assert time.monotonic() - debut < 1
    os.close(lecture)


def test_talon_delai():
    lecture, ecriture = os.pipe()  # personne n'écrit ni ne ferme
    assert stub.lire_borne(lecture, delai=0.2) == ("delai", b"")
    os.close(lecture)
    os.close(ecriture)


def test_talon_lit_jusqua_eof():
    lecture, ecriture = os.pipe()
    os.write(ecriture, b"a" * schema.MAX_BYTES)
    os.close(ecriture)
    assert stub.lire_borne(lecture, delai=2) == ("ok", b"a" * schema.MAX_BYTES)
    os.close(lecture)


# --- validateur (sans socket) ------------------------------------------

def test_appelant_non_autorise(dossier):
    ex, _ = faire_executeur(dossier)
    v = faire_validateur(ex, dossier)
    assert v.traiter("work", "ok", plan({"op": "vm.list"}))["statut"] == "refuse"


def test_pas_de_plafond_de_debit_ni_de_pause_auto(dossier):
    # 28/10 : un agent envoie beaucoup de commandes d'affilée ; plus de pause automatique.
    ex, _ = faire_executeur(dossier)
    v = faire_validateur(ex, dossier)
    for _ in range(60):
        assert v.traiter("sys-agent", "ok", plan({"op": "vm.list"}))["statut"] == "fait"
    assert not os.path.exists(ex.pause_auto) and pause.raison(v.config.pauses) is None


def test_kill_switch_manuel(dossier, capsys):
    ex, _ = faire_executeur(dossier)
    v = faire_validateur(ex, dossier)
    pause.main(["on", "test"], chemins=v.config.pauses)
    r = v.traiter("sys-agent", "ok", plan({"op": "vm.list"}))
    assert r == {"statut": "refuse", "raison": "cœur du système en pause : pause manuelle (qaf-pause)"}
    pause.main(["status"], chemins=v.config.pauses)
    assert "EN PAUSE" in capsys.readouterr().out


def test_un_plan_a_la_fois_par_appelant(dossier):
    bloque, libere = threading.Event(), threading.Event()

    class Lent(confirm.Confirmateur):
        def demander(self, d):
            bloque.set()
            libere.wait(5)
            return True

    ex, _ = faire_executeur(dossier)
    ex.confirmateur = Lent()
    v = faire_validateur(ex, dossier)
    res = {}
    t = threading.Thread(target=lambda: res.update(r=v.traiter(
        "sys-agent", "ok", plan({"op": "vm.shutdown", "name": "work"}))))  # N2 influencé : confirmation
    t.start()
    assert bloque.wait(5)
    r2 = v.traiter("sys-agent", "ok", plan({"op": "vm.list"}))
    assert r2["statut"] == "refuse" and "déjà en cours" in r2["raison"]
    libere.set()
    t.join(5)
    assert res["r"]["statut"] == "fait"
    assert v.traiter("sys-agent", "ok", plan({"op": "vm.list"}))["statut"] == "fait"


@pytest.mark.parametrize("lecture,donnees,mot", [
    ("trop_grand", b"", "trop grande"),
    ("delai", b"", "délai"),
    ("ok", b"{pas du json", "plan refusé"),
    ("ok", plan({"op": "vm.shutdown", "name": "dom0"}), "refuse cette action à l'agent"),
])
def test_refus_du_validateur_journalises(dossier, lecture, donnees, mot):
    ex, _ = faire_executeur(dossier)
    v = faire_validateur(ex, dossier)
    r = v.traiter("sys-agent", lecture, donnees)
    assert r["statut"] == "refuse" and mot in r["raison"]
    assert verify(f"{dossier}/journal.jsonl") == 1


# --- exécuteur -----------------------------------------------------------

def test_executeur_exige_un_plan_canonique(dossier):
    ex, _ = faire_executeur(dossier)
    brut = plan({"op": "vm.list"}).decode()  # valide mais pas canonique (espaces)
    assert ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": brut})["raison"] == "plan non canonique"


def test_executeur_reclasse_lui_meme(dossier):
    """Un validateur compromis qui envoie un plan N3 ne contourne pas la confirmation."""
    ex, conf = faire_executeur(dossier, reponses=[False])
    canon = schema.canonicalize(schema.parse_plan(plan({"op": "vm.remove", "name": "work"}))).decode()
    r = ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": canon})
    assert r["statut"] == "refuse" and len(conf.demandes) == 1
    assert conf.demandes[0].decision == classify.CONFIRMER_RENFORCE
    assert conf.demandes[0].canonique == canon.encode()
    assert "work" in ex.backend.qubes


def test_executeur_arret_a_la_premiere_erreur(dossier):
    ex, _ = faire_executeur(dossier)
    p = plan(CREER, CREER, {"op": "vm.start", "name": "dev"})
    canon = schema.canonicalize(schema.parse_plan(p)).decode()
    r = ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": canon})
    assert r["statut"] == "erreur" and r["non_executees"] == 1
    assert [x["ok"] for x in r["resultats"]] == [True, False]
    assert r["resultats"][1]["code"] == "nom_pris"
    assert ex.backend.qubes["dev"].state == "Halted"


def test_executeur_pause_entre_deux_operations(dossier):
    ex, _ = faire_executeur(dossier)
    vrai_start = ex.backend.start
    def start_puis_pause(nom):
        vrai_start(nom)
        pause.mettre(f"{dossier}/pause", "arrêt d'urgence")
    ex.backend.start = start_puis_pause
    p = plan(CREER, {"op": "vm.start", "name": "dev"}, {"op": "vm.shutdown", "name": "dev"})
    canon = schema.canonicalize(schema.parse_plan(p)).decode()
    r = ex.traiter({"type": "plan", "appelant": "sys-agent", "plan": canon})
    assert r["statut"] == "interrompu" and r["non_executees"] == 1
    assert ex.backend.qubes["dev"].state == "Running"


def test_evenement_trop_gros(dossier):
    ex, _ = faire_executeur(dossier)
    assert ex.traiter({"type": "evenement", "event": {"x": "a" * 5000}})["statut"] == "erreur"
    assert ex.traiter({"type": "inconnu"})["statut"] == "erreur"


def test_confirmation_console():
    import io
    p = schema.parse_plan(plan({"op": "vm.remove", "name": "work"}, request="R-0042"))
    d = confirm.Demande("sys-agent", p, schema.canonicalize(p), (classify.N3,), classify.CONFIRMER_RENFORCE,
                        "731904")
    sortie = io.StringIO()
    assert not confirm.Console(io.StringIO("oui\n"), sortie).demander(d)
    assert not confirm.Console(io.StringIO("R-0042\n"), io.StringIO()).demander(d)  # choisi par l'agent
    assert confirm.Console(io.StringIO("731904\n"), io.StringIO()).demander(d)
    assert "Tu n'as rien demandé dans la palette" in sortie.getvalue()  # PARCOURS-1 M8 : sans code
    assert "1. vm.remove name='work'" in sortie.getvalue() and "N3" not in sortie.getvalue()
    assert "R-0042" not in sortie.getvalue()
    assert not confirm.ToujoursRefuser().demander(d)


# --- chaîne complète par sockets ----------------------------------------

def lancer_talon(sock, entree, appelant="sys-agent"):
    env = {"PATH": os.environ.get("PATH", ""), "QAF_STUB_SOCKET": sock, "QREXEC_REMOTE_DOMAIN": appelant}
    p = subprocess.run([sys.executable, "-I", STUB], input=entree, env=env, capture_output=True, timeout=30)
    return json.loads(p.stdout)


@pytest.fixture
def chaine(dossier):
    ex, conf = faire_executeur(dossier, reponses=[True])
    s_ex = executor.serveur(ex, f"{dossier}/ex.sock")
    v = validator.Validateur(proto.client(f"{dossier}/ex.sock", delai=30), validator.Config(pauses=ex.pauses))
    s_v = validator.serveur(v, f"{dossier}/v.sock")
    for s in (s_ex, s_v):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"{dossier}/v.sock", ex, conf
    for s in (s_v, s_ex):
        s.shutdown()
        s.server_close()


def test_chaine_complete(chaine, dossier):
    sock, ex, conf = chaine
    r = lancer_talon(sock, plan(CREER, {"op": "vm.start", "name": "dev"}))
    assert r["statut"] == "fait", r
    assert ex.backend.qubes["dev"].netvm is None  # C2
    assert ex.backend.qubes["dev"].state == "Running"
    assert set(ex.registre.qubes) == {"dev"}
    # donner le réseau à son qube : N2 influencé -> confirmation (accordée par le script)
    r = lancer_talon(sock, plan({"op": "vm.set_netvm", "name": "dev", "netvm": "sys-firewall"}))
    assert r["statut"] == "fait" and len(conf.demandes) == 1
    # confirmation suivante refusée (le script est vide)
    r = lancer_talon(sock, plan({"op": "vm.shutdown", "name": "work"}))
    assert r == {"statut": "refuse", "raison": "non confirmé par l'utilisateur", "request": "R-0001"}
    assert verify(f"{dossier}/journal.jsonl") > 5


def test_chaine_entree_geante_et_appelant(chaine):
    sock, ex, _ = chaine
    assert lancer_talon(sock, b"x" * (10 * 1024 * 1024))["raison"] == "entrée trop grande"
    assert lancer_talon(sock, plan({"op": "vm.list"}), appelant="work")["raison"] == "appelant non autorisé"


ACTIVATION = r"""
import os, socket, sys
sys.path[:0] = [sys.argv[2], os.path.join(sys.argv[2], "tests")]
s = socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen()
if s.fileno() != 3:
    os.dup2(s.fileno(), 3); s.close()
else:
    s.detach()
os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="1")
from qaf_gate import executor, proto
from backend_sim import SimBackend
from qaf_gate.journal import Journal
from qaf_gate.registry import Registry
d = os.path.dirname(sys.argv[1])
ex = executor.Executeur(SimBackend(), Registry(d + "/r.json"), Journal(d + "/j.jsonl"), pauses=())
srv = executor.serveur(ex)  # sans chemin : doit prendre le fd 3 de systemd
srv.daemon_threads = False  # server_close() attend la fin du traitement
print("pret", flush=True)
srv.handle_request()
srv.server_close()
"""


def test_activation_par_socket_systemd(dossier):
    sock = f"{dossier}/act.sock"
    racine = os.path.dirname(os.path.dirname(executor.__file__))
    p = subprocess.Popen([sys.executable, "-c", ACTIVATION, sock, racine], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "pret"
        r = proto.client(sock, delai=10)({"type": "contexte"})
        assert "sys-net" in r["existants"] and r["agent"] == []
    finally:
        p.wait(10)


def test_chaine_validateur_absent(dossier):
    r = lancer_talon(f"{dossier}/absent.sock", plan({"op": "vm.list"}))
    assert r == {"raison": "cœur du système indisponible", "statut": "erreur"}


# --- contrat de réponse et délais (P1-6, P2-7) -------------------------------

def canon_de(*ops):
    return schema.canonicalize(schema.parse_plan(plan(*ops))).decode()


def msg_plan(*ops):
    return {"type": "plan", "appelant": "sys-agent", "plan": canon_de(*ops)}


def test_delais_ordonnes():
    assert (proto.DELAI_CONFIRMATION < proto.DELAI_EXECUTEUR < proto.DELAI_VALIDATEUR
            < proto.DELAI_TALON < proto.DELAI_AGENT)
    assert stub.DELAI_REPONSE == proto.DELAI_TALON  # copie : le talon n'importe rien
    from qaf_mcp import server
    assert server.DELAI >= proto.DELAI_AGENT


def test_reponse_hors_contrat_impossible():
    with pytest.raises(ValueError):
        proto.reponse("execute")
    assert json.loads('{"raison":"cœur du système indisponible","statut":"erreur"}')["statut"] in proto.STATUTS


def test_confirmation_expiree_jamais_executee(dossier):
    tard = threading.Event()

    class Absent(confirm.Confirmateur):
        def demander(self, d):
            tard.wait(5)
            return True  # l'utilisateur répond, mais trop tard

    ex, _ = faire_executeur(dossier)
    ex.confirmateur, ex.delai_confirmation = Absent(), 0.2
    r = ex.traiter(msg_plan({"op": "vm.shutdown", "name": "work"}))  # N2 influencé
    assert r["statut"] == proto.EXPIRE and "expirée" in r["raison"]
    tard.set()
    time.sleep(0.2)
    assert ex.backend.qubes["work"].state == "Running"
    with open(f"{dossier}/journal.jsonl", encoding="utf-8") as f:
        conf = [json.loads(l)["event"] for l in f if '"confirmation"' in l]
    assert conf[-1]["expiree"] is True and conf[-1]["accordee"] is False


def test_confirmation_tardive_selon_horloge(dossier):
    t = [0.0]

    class Lent(confirm.Confirmateur):
        def demander(self, d):
            t[0] += proto.DELAI_CONFIRMATION + 1
            return True

    ex, _ = faire_executeur(dossier)
    ex.confirmateur, ex.horloge = Lent(), lambda: t[0]
    assert ex.traiter(msg_plan({"op": "vm.shutdown", "name": "work"}))["statut"] == proto.EXPIRE
    assert ex.backend.qubes["work"].state == "Running"


def test_executeur_aucune_operation_apres_son_delai(dossier):
    t = [0.0]
    ex, _ = faire_executeur(dossier)
    ex.horloge = lambda: t[0]
    vrai_create = ex.backend.create_appvm

    def create_lent(*a):
        t[0] += proto.DELAI_EXECUTEUR
        return vrai_create(*a)

    ex.backend.create_appvm = create_lent
    r = ex.traiter(msg_plan(CREER, {"op": "vm.start", "name": "dev"}))
    assert r["statut"] == proto.INTERROMPU and r["non_executees"] == 1
    assert ex.backend.qubes["dev"].state == "Halted"


def test_toutes_les_reponses_respectent_le_contrat(dossier):
    ex, _ = faire_executeur(dossier)
    v = faire_validateur(ex, dossier)
    for donnees in (plan({"op": "vm.list"}), b"{", plan({"op": "vm.shutdown", "name": "dom0"}),
                    plan({"op": "vm.remove", "name": "work"}), plan(CREER, CREER)):
        assert v.traiter("sys-agent", "ok", donnees)["statut"] in proto.STATUTS


def test_aucune_operation_si_le_delai_restant_ne_suffit_pas(dossier):
    # Le plan ne tient pas dans le temps restant : refusé avant toute opération.
    t = [0.0]
    ex, _ = faire_executeur(dossier)
    ex.horloge, ex.delai = (lambda: t[0]), proto.DUREE_MAX_OP["vm.create"]
    r = ex.traiter(msg_plan(CREER))
    assert r["statut"] == proto.REFUSE and "délai insuffisant" in r["raison"]
    assert "dev" not in ex.backend.qubes
    with open(f"{dossier}/journal.jsonl", encoding="utf-8") as f:
        assert any('"délai insuffisant"' in l for l in f)


def test_delai_executeur_couvre_le_pire_plan():
    # Une seule opération lourde par plan (schema.LOURDES), les autres au pire.
    lourde = max(proto.DUREE_MAX_OP[o] for o in schema.LOURDES)
    autres = max(d for o, d in proto.DUREE_MAX_OP.items() if o not in schema.LOURDES)
    assert proto.DELAI_EXECUTEUR >= proto.DELAI_CONFIRMATION + lourde + (schema.MAX_OPS - 1) * autres
    assert set(proto.DUREE_MAX_OP) == set(schema.OPS)


# --- durcissement de l'exécuteur et des pauses ------------------------------

def lignes_journal(d):
    with open(f"{d}/journal.jsonl", encoding="utf-8") as f:
        return [json.loads(l)["event"] for l in f]


def test_evenement_ne_peut_pas_usurper_la_source(dossier):
    # P1-5 : le contenu du validateur est imbriqué, jamais fusionné.
    ex, _ = faire_executeur(dossier)
    ev = {"type": "refus", "appelant": "sys-agent", "raison": "x"}
    assert ex.traiter({"type": "evenement", "event": ev})["statut"] == proto.FAIT
    for mauvais in ({**ev, "source": "executeur"}, {**ev, "type": "plan"}, {**ev, "raison": "a" * 201},
                    {**ev, "appelant": 3}, ["refus"]):
        assert ex.traiter({"type": "evenement", "event": mauvais})["statut"] == proto.ERREUR
    assert lignes_journal(dossier) == [{"source": "validateur", "event": ev}]


def test_evenements_debit_plafonne(dossier):
    t = [0.0]
    ex, _ = faire_executeur(dossier)
    ex.horloge = lambda: t[0]
    ev = {"type": "evenement", "event": {"type": "refus", "appelant": "sys-agent", "raison": "x"}}
    for _ in range(executor.MAX_EVENEMENTS_MINUTE):
        assert ex.traiter(ev)["statut"] == proto.FAIT
    for _ in range(50):
        assert ex.traiter(ev)["statut"] == proto.ERREUR
    j = lignes_journal(dossier)
    assert len(j) == executor.MAX_EVENEMENTS_MINUTE + 1 and j[-1]["type"] == "evenements_ignores"
    t[0] = 61.0
    assert ex.traiter(ev)["statut"] == proto.FAIT


def test_executeur_reverifie_l_appelant(dossier):
    ex, _ = faire_executeur(dossier)
    r = ex.traiter({"type": "plan", "appelant": "work", "plan": canon_de({"op": "vm.list"})})
    assert r["statut"] == proto.REFUSE and r["raison"] == "appelant non autorisé"


def test_jeton_de_confirmation_tire_dans_dom0(dossier):
    ex, conf = faire_executeur(dossier, reponses=[False, False])
    for _ in range(2):
        ex.traiter(msg_plan({"op": "vm.remove", "name": "work"}))
    jetons = [d.jeton for d in conf.demandes]
    assert all(len(j) == 6 and j.isdigit() for j in jetons) and "R-0001" not in jetons


def test_coffres_transmis_au_validateur(dossier):
    # P3-6 : le validateur classe avec la liste de coffres de l'exécuteur.
    ex, _ = faire_executeur(dossier)
    ex.config = dataclasses.replace(ex.config, coffres=frozenset({"personal"}))
    v = faire_validateur(ex, dossier)
    r = v.traiter("sys-agent", "ok", plan({"op": "vm.set_netvm", "name": "personal", "netvm": "sys-net"}))
    assert r["statut"] == proto.REFUSE and "refuse cette action à l'agent" in r["raison"]
    assert not any(e.get("type") == "plan" for e in lignes_journal(dossier))  # refusé avant l'exécuteur


@pytest.mark.parametrize("genre", ["lien", "fifo", "dossier"])
def test_pause_jamais_ouverte(dossier, genre):
    # Le validateur ne doit pas pouvoir bloquer ou détourner la lecture d'une
    # pause : seul lstat est utilisé et la raison est fixe.
    p = f"{dossier}/pause-auto"
    if genre == "lien":
        with open(f"{dossier}/secret", "w") as f:
            f.write("contenu à ne jamais renvoyer")
        os.symlink(f"{dossier}/secret", p)
    elif genre == "fifo":
        os.mkfifo(p)  # une lecture bloquerait
    else:
        os.mkdir(p)
    debut = time.monotonic()
    r = pause.raison((f"{dossier}/pause", p))
    assert r == "pause automatique (débit dépassé)" and time.monotonic() - debut < 1
    ex, _ = faire_executeur(dossier)
    assert ex.traiter(msg_plan({"op": "vm.list"})) == {"statut": "refuse",
                                                       "raison": "cœur du système en pause : " + r}


def test_pause_lien_casse_compte_aussi(dossier):
    os.symlink(f"{dossier}/absent", f"{dossier}/pause")
    assert pause.raison((f"{dossier}/pause",)) == "pause manuelle (qaf-pause)"


def test_serveur_borne_ses_threads(dossier):
    # P3-5 : au-delà de max_connexions, la connexion est fermée tout de suite.
    import socket
    import socketserver
    entre, libere = threading.Event(), threading.Event()

    class Bloque(socketserver.BaseRequestHandler):
        def handle(self):
            entre.set()
            libere.wait(5)

    srv = proto.serveur(Bloque, f"{dossier}/b.sock")
    srv.max_connexions = 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c1 = socket.socket(socket.AF_UNIX)
        c1.connect(f"{dossier}/b.sock")
        assert entre.wait(5)
        c2 = socket.socket(socket.AF_UNIX)
        c2.settimeout(2)
        c2.connect(f"{dossier}/b.sock")
        assert c2.recv(1) == b""  # fermée sans attendre
        libere.set()
        c1.close()
        c2.close()
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("op", [[], {}, {"a": 1}, ["vm.remove"]])
def test_validateur_op_non_hachable_refuse_proprement(dossier, op):
    # P2-4 : sans le correctif de schema.py, TypeError tuait le thread du
    # validateur et le talon répondait « portier indisponible ».
    ex, _ = faire_executeur(dossier)
    r = faire_validateur(ex, dossier).traiter("sys-agent", "ok", plan({"op": op}))
    assert r["statut"] == proto.REFUSE and r["raison"].startswith("plan refusé")
