"""Coureur FACTICE : exécute une tâche écrite en pas simples, sans LLM, via qaf_hand.

Il remplace le vrai coureur (LLM) pour la démonstration technique de
dispvm.run_task : jetable créé par le Doorman -> qaf.Task -> coureur -> qaf_hand.
Une ligne = un pas ; une ligne vide ou commençant par « # » est ignorée :

  lancer <prog> [args…]   démarre une application, détachée (argv, jamais de shell)
  attendre <regex> [s]    attend une fenêtre dont le titre correspond (20 s max) et la choisit
  fenetre <regex>         choisit la fenêtre courante par son titre
  taper <texte>           frappe le texte (fenêtre courante)
  touche <touches>        ex. Return, ctrl+l
  cliquer <x> <y>         clic, coordonnées relatives à la fenêtre courante
  capturer                capture la fenêtre courante (ou l'écran) -> capture-N.png
  lister                  liste les fenêtres
  exec <prog> [args…]     commande bornée (qaf.Exec), sortie au journal
  pause <s>               attend (10 s max)

Une tâche en langage naturel n'est PAS interprétable ici : le coureur le note
et s'arrête (il faut le vrai coureur). Chaque pas est écrit dans
journal.jsonl, le bilan dans rapport.json. Avec --eteindre, le coureur éteint
le qube à la fin, mais seulement si c'est un jetable (qubesdb /type).
"""

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from qaf_hand import service

MAX_PAS = 50
DUREE_MAX = 300  # secondes pour toute la tâche
VERBES = ("lancer", "attendre", "fenetre", "taper", "touche", "cliquer", "capturer", "lister",
          "exec", "pause")


class TacheInvalide(Exception):
    pass


def analyser(texte):
    """Texte de la tâche -> liste de (verbe, reste). TacheInvalide si un pas est inconnu."""
    pas = []
    for n, ligne in enumerate(texte.splitlines(), 1):
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        verbe, _, reste = ligne.partition(" ")
        if verbe not in VERBES:
            raise TacheInvalide(f"ligne {n} : « {verbe[:32]} » n'est pas un pas du coureur factice "
                                "(tâche en langage naturel : il faut le vrai coureur, LLM)")
        pas.append((verbe, reste.strip()))
    if not pas:
        raise TacheInvalide("tâche vide")
    if len(pas) > MAX_PAS:
        raise TacheInvalide(f"plus de {MAX_PAS} pas")
    return pas


class Coureur:
    def __init__(self, dossier, lanceur=service.lancer, demarrer=None, dormir=time.sleep,
                 horloge=time.monotonic):
        self.dossier = Path(dossier)
        self.lanceur = lanceur
        self.demarrer = demarrer or self._demarrer
        self.dormir, self.horloge = dormir, horloge
        self.fenetre = None  # identifiant hexadécimal de la fenêtre courante
        self.captures = 0

    @staticmethod
    def _demarrer(argv):
        env = {**os.environ}
        env.setdefault("DISPLAY", ":0")
        subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

    def _noter(self, entree):
        with open(self.dossier / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")

    def _avec_fenetre(self, req):
        return {**req, "fenetre": self.fenetre} if self.fenetre else req

    def _chercher(self, motif):
        rx = re.compile(motif)
        for f in service.lister_fenetres(self.lanceur):
            if rx.search(f["titre"]):
                return f
        return None

    def pas(self, verbe, reste):
        """Exécute un pas ; renvoie un résumé court (jamais l'image elle-même)."""
        if verbe == "lancer":
            argv = shlex.split(reste)
            if not argv:
                raise service.Refus("lancer : programme manquant")
            self.demarrer(argv)
            return {"lance": argv[0]}
        if verbe in ("attendre", "fenetre"):
            mots = shlex.split(reste)
            if not mots:
                raise service.Refus(f"{verbe} : motif de titre manquant")
            delai = min(float(mots[1]), 20) if verbe == "attendre" and len(mots) > 1 else 20
            fin = self.horloge() + (delai if verbe == "attendre" else 0)
            while True:
                f = self._chercher(mots[0])
                if f or self.horloge() >= fin:
                    break
                self.dormir(0.3)
            if not f:
                raise service.Refus(f"aucune fenêtre ne correspond à {mots[0][:64]!r}")
            self.fenetre = f["id"]
            return {"fenetre": f["id"], "titre": f["titre"]}
        if verbe == "taper":
            return service.entree(self._avec_fenetre({"action": "taper", "texte": reste}), self.lanceur)
        if verbe == "touche":
            return service.entree(self._avec_fenetre({"action": "touche", "touches": reste}), self.lanceur)
        if verbe == "cliquer":
            x, y = (int(v) for v in reste.split())
            return service.entree(self._avec_fenetre({"action": "cliquer", "x": x, "y": y}), self.lanceur)
        if verbe == "capturer":
            r = service.capturer(self.lanceur, self.fenetre or "racine")
            self.captures += 1
            nom = f"capture-{self.captures}.png"
            (self.dossier / nom).write_bytes(base64.b64decode(r.pop("image")))
            return {**r, "fichier": nom}
        if verbe == "lister":
            return {"fenetres": service.lister_fenetres(self.lanceur)}
        if verbe == "exec":
            r = service.executer({"argv": shlex.split(reste), "delai": 30}, self.lanceur)
            return {**r, "stdout": r["stdout"][:2000], "stderr": r["stderr"][:2000]}
        if verbe == "pause":
            self.dormir(min(float(reste), 10))
            return {}
        raise service.Refus(f"pas inconnu : {verbe}")

    def courir(self, texte):
        debut = self.horloge()
        try:
            etapes = analyser(texte)
        except TacheInvalide as e:
            return self._bilan("non_interpretable", 0, str(e))
        for i, (verbe, reste) in enumerate(etapes):
            if self.horloge() - debut > DUREE_MAX:
                return self._bilan("delai_depasse", i, f"plus de {DUREE_MAX} s")
            try:
                res = self.pas(verbe, reste)
            except (service.Refus, ValueError, re.error) as e:
                self._noter({"pas": i, "verbe": verbe, "ok": False, "erreur": str(e)[:300]})
                return self._bilan("echec", i, f"pas {i} ({verbe}) : {str(e)[:300]}")
            self._noter({"pas": i, "verbe": verbe, "ok": True, "resultat": res})
        return self._bilan("fini", len(etapes), "")

    def _bilan(self, statut, faits, message):
        bilan = {"statut": statut, "pas_faits": faits, "message": message, "captures": self.captures,
                 "coureur": "factice"}
        (self.dossier / "rapport.json").write_text(json.dumps(bilan, ensure_ascii=False), "utf-8")
        return bilan


def est_jetable(lanceur=service.lancer):
    try:
        # /type = classe du qube (DispVM) ; /qubes-vm-type vaut « AppVM » dans un jetable (Qubes 4.3.2).
        code, out, _ = lanceur(["qubesdb-read", "/type"], None, 5)
    except service.Refus:
        return False
    return code == 0 and out.strip() == b"DispVM"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    eteindre = "--eteindre" in argv
    reste = [a for a in argv if a != "--eteindre"]
    if len(reste) != 1:
        print("usage : python3 -m qaf_hand.coureur [--eteindre] DOSSIER", file=sys.stderr)
        return 2
    dossier = Path(reste[0])
    try:
        texte = json.loads((dossier / "tache.json").read_text("utf-8"))["tache"]
        bilan = Coureur(dossier).courir(texte)
    except Exception as e:  # le bilan doit toujours exister, et le jetable s'éteindre
        bilan = {"statut": "erreur", "message": f"{type(e).__name__}: {e}"[:300]}
        try:
            (dossier / "rapport.json").write_text(json.dumps(bilan, ensure_ascii=False), "utf-8")
        except OSError:
            pass
    print(json.dumps(bilan, ensure_ascii=False), flush=True)
    if eteindre and est_jetable():
        # Jetable auto_cleanup : à l'arrêt, qubesd le supprime avec son disque.
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "poweroff"], check=False)
    return 0 if bilan.get("statut") == "fini" else 1


if __name__ == "__main__":
    sys.exit(main())
