"""Une opération valide par type d'opération du schéma (tests seulement)."""

from qaf_gate import schema

EXEMPLES = {
    "vm.list": {},
    "vm.info": {"name": "work"},
    "vm.create": {"name": "neuf", "template": "debian-13-xfce", "label": "blue"},
    "vm.start": {"name": "work"},
    "vm.shutdown": {"name": "work"},
    "vm.remove": {"name": "work"},
    "vm.set_netvm": {"name": "work", "netvm": "sys-net"},
    "dispvm.run_task": {"task": "x"},
    "vm.settings": {"name": "work"},
    "update.status": {"targets": ["work", "debian-13-xfce"]},
    "sys.status": {},
    "prefs.get": {},
    "fw.list": {"name": "work"},
    "dev.list": {"class": "usb"},
    "vm.set_prop": {"name": "work", "prop": "memory", "value": 4096},
    "vm.reset_prop": {"name": "work", "prop": "memory"},
    "vm.set_service": {"name": "work", "service": "cups", "state": "on"},
    "vm.restart": {"name": "work"},
    "vm.set_template": {"name": "work", "template": "debian-13-xfce"},
    "pkg.install": {"name": "debian-13-xfce", "packages": ["git", "curl"]},
    "dispvm.create": {},
    "qube.exec": {"name": "work", "argv": ["git", "status"], "timeout": 30},
    "qube.windows": {"name": "work", "hidden": False},
    "qube.screenshot": {"name": "work", "window": "racine"},
    "qube.type": {"name": "work", "text": "bonjour"},
    "qube.key": {"name": "work", "keys": "ctrl+l"},
    "qube.mouse": {"name": "work", "x": 10, "y": 20, "button": 1},
    "qube.scroll": {"name": "work", "direction": "bas", "steps": 3},
    "prefs.set": {"prop": "default_template", "value": "debian-13-xfce"},
    "fw.add": {"name": "work", "position": 0, "rule": {"action": "accept", "host": "exemple.org",
                                                         "proto": "tcp", "ports": "443"}},
    "fw.remove": {"name": "work", "position": 0, "rule": {"action": "drop", "host": "10.0.0.0/8"}},
    # Lots 2 et 3.
    "update.run": {"targets": ["debian-13-xfce"], "apply": "none"},
    "template.available": {"repo": "official"},
    "template.install": {"template": "debian-13-minimal", "repo": "official"},
    "job.status": {"job": "T-123456"},
    "job.cancel": {"job": "T-123456"},
    "enter.open": {"name": "work", "minutes": 30},
    "enter.close": {"name": "work"},
    "file.copy": {"source": "work", "path": "Documents/rapport.pdf", "dest": "personal"},
    "file.open_dispvm": {"name": "work", "path": "facture.pdf"},
    "dev.attach": {"class": "block", "backend": "sys-usb", "port": "sda", "device_id": "0951:1666:abc",
                   "target": "work", "read_only": True},
    "dev.detach": {"class": "block", "backend": "sys-usb", "port": "sda", "target": "work"},
    "backup.profiles": {},
    "backup.run": {"profile": "hebdo"},
    "pkg.remove": {"name": "debian-13-xfce", "packages": ["cups"]},
    "template.remove": {"template": "fedora-42-xfce"},
    "vm.tag": {"name": "work", "tag": "projet-x", "action": "add"},
    "vm.grow_private": {"name": "work", "size_mib": 20480},
    "file.move": {"source": "work", "path": "Documents/rapport.pdf", "dest": "personal"},
    # PARCOURS-1 B2.
    "vm.clone": {"name": "tpl-agent-dev", "source": "debian-13-xfce", "label": "gray"},
}
assert set(EXEMPLES) == set(schema.OPS)


def op(nom, **champs):
    return {"op": nom, **EXEMPLES[nom], **champs}
