"""qaf.Task : service qrexec du JETABLE. Reçoit UNE tâche bornée et la confie au coureur.

Appelé par l'exécuteur du Doorman (dom0) juste après avoir créé le jetable
depuis le modèle dédié QAF (qaf_gate/backend_qubes.py, run_dispvm_task) :

  entrée  : {"tache": "texte de 1 à 1000 caractères"}   (16 Kio max, schéma fermé)
  réponse : {"ok": true, "accepte": true, "id": "...", "coureur": "factice"}

La réponse part tout de suite : le coureur tourne détaché (nouvelle session,
entrées/sorties vers un fichier), le Doorman n'attend pas la fin de la tâche.
Le coureur éteint le jetable quand il a fini (auto_cleanup : qubesd le
supprime alors). Une seule tâche par jetable.
"""

import json
import os
import secrets
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from qaf_hand.service import Refus, _champs

MAX_TACHE = 1000
# Mêmes catégories refusées que le schéma du Doorman (qaf_gate/schema.py,
# TASK_INTERDITS) : la tâche a déjà été validée dans dom0, on revérifie ici.
INTERDITS = ("Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn")
COUREUR = "factice"  # TODO : le vrai coureur (LLM) remplacera qaf_hand.coureur


def racine_par_defaut():
    return Path.home() / "qaf-taches"


def valider(req):
    _champs(req, ("tache",))
    t = req["tache"]
    if not isinstance(t, str) or not 1 <= len(t) <= MAX_TACHE:
        raise Refus(f"tache : 1 à {MAX_TACHE} caractères")
    if any(c != "\n" and unicodedata.category(c) in INTERDITS for c in t):
        raise Refus("tache : caractère de contrôle interdit")
    return t


def demarrer_coureur(dossier):
    """Lance le coureur détaché du service qrexec : il survit à la fin de l'appel."""
    with open(dossier / "coureur.log", "ab") as journal:
        p = subprocess.Popen(
            [sys.executable, "-I", "-m", "qaf_hand.coureur", "--eteindre", str(dossier)],
            stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
            cwd=dossier, start_new_session=True, close_fds=True)
    return p.pid


def accepter(req, demarrer=None, racine=None):
    tache = valider(req)
    demarrer = demarrer or demarrer_coureur
    racine = Path(racine) if racine is not None else racine_par_defaut()
    racine.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:  # une seule tâche par jetable : le verrou n'est jamais retiré
        os.close(os.open(racine / "verrou", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        raise Refus("une tâche a déjà été confiée à ce jetable") from None
    ident = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    dossier = racine / ident
    dossier.mkdir(mode=0o700)
    (dossier / "tache.json").write_text(json.dumps({"tache": tache}, ensure_ascii=False), "utf-8")
    demarrer(dossier)
    return {"accepte": True, "id": ident, "coureur": COUREUR}
