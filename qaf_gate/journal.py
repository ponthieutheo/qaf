"""Journal append-only, chaîné par SHA-256.

Une ligne JSON par événement :
    {"seq": n, "ts": secondes, "prev": hash précédent, "event": {...}, "hash": h}
h = sha256(JSON canonique de l'entrée sans "hash"). La première entrée a
prev = 64 zéros. Modifier, supprimer ou réordonner une ligne casse la chaîne.
"""

import hashlib
import json
import os
import time

GENESE = "0" * 64


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(entree):
    return hashlib.sha256(_canon({k: v for k, v in entree.items() if k != "hash"})).hexdigest()


class JournalError(Exception):
    pass


def verify(path):
    """Vérifie toute la chaîne. Renvoie le nombre d'entrées ou lève JournalError."""
    prev, n = GENESE, 0
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        for i, ligne in enumerate(f, 1):
            try:
                e = json.loads(ligne)
            except ValueError:
                raise JournalError(f"ligne {i} : JSON invalide") from None
            if not isinstance(e, dict) or set(e) != {"seq", "ts", "prev", "event", "hash"}:
                raise JournalError(f"ligne {i} : entrée mal formée")
            if e["seq"] != n or e["prev"] != prev:
                raise JournalError(f"ligne {i} : chaîne rompue")
            if _hash(e) != e["hash"]:
                raise JournalError(f"ligne {i} : empreinte fausse")
            prev, n = e["hash"], n + 1
    return n


class Journal:
    def __init__(self, path):
        self.path = path
        self.n = verify(path)  # refuse d'écrire à la suite d'un journal altéré
        self.prev = GENESE
        if self.n:
            with open(path, encoding="utf-8") as f:
                for ligne in f:
                    pass
            self.prev = json.loads(ligne)["hash"]

    def append(self, event: dict) -> str:
        e = {"seq": self.n, "ts": int(time.time()), "prev": self.prev, "event": event}
        e["hash"] = _hash(e)
        with open(self.path, "ab") as f:
            f.write(_canon(e) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        self.n, self.prev = self.n + 1, e["hash"]
        return e["hash"]
