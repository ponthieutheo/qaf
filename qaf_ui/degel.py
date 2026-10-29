"""qaf-degel : filet contre le « gel » du bureau de dom0 (docs/GEL-BUREAU.md).

Le gel : le pointeur bouge mais aucun clic ne passe, l'écran ne change plus,
le clavier marche. Cause prouvée côté serveur X : le pointeur maître est
GELÉ par une saisie synchrone (GrabModeSync) que son propriétaire ne libère
jamais (pas d'AllowEvents). Sur la machine mesurée, la seule saisie synchrone sur la
barre de Plasma est celle de KWin (bouton 1, posée sur l'enveloppe de chaque
fenêtre inactive, docks compris). Tant que le maître est gelé, X met ses
événements en file : le relâchement du bouton n'arrive jamais, le bouton
reste « enfoncé » (masque 256) et plus rien n'est livré.

Détection : les événements bruts XI2 des périphériques ESCLAVES (pavé tactile,
souris, XTest) arrivent même quand le maître est gelé (seul le maître lit sa
file). Si un bouton a été relâché sur tous les esclaves depuis SEUIL_S secondes
et que le maître le croit encore enfoncé, le maître est gelé : un relâchement
normal met à jour le maître dans la même milliseconde.

Dégel : l'action XKB du serveur « Ungrab » (XF86Ungrab, option
grab:break_actions de xkeyboard-config) défait toutes les saisies actives et
écrit dans /var/log/Xorg.0.log qui tenait la saisie (pid, commande, passive ou
active, gelée ou non) : c'est aussi la preuve du coupable. On la pose le temps
de l'action sur un keycode libre (aucun symbole), on la tape par XTest, puis
on remet le keycode et les options XKB comme avant. Rien n'est tapé ailleurs
que pendant un gel détecté, au plus une fois par PAUSE_S.

python-xlib n'est importé qu'au lancement (tests sans X).
"""

import os
import select
import struct
import subprocess
import sys
import time

SEUIL_S = 3.0           # relâché partout depuis 3 s, encore tenu côté maître : gel
PAUSE_S = 30.0          # au plus un dégel automatique toutes les 30 s
BOUTONS = {1: 1 << 8, 2: 1 << 9, 3: 1 << 10}  # boutons suivis -> bit du masque X
XF86_UNGRAB = 0x1008FE20
OPTION_DEGEL = "grab:break_actions"


def lire_brut(donnees):
    """(deviceid, bouton) d'un événement brut XI2 (RawButtonPress/Release) tel
    que python-xlib le rend : octets après evtype (deviceid u16, time u32,
    detail u32, sourceid u16, …)."""
    if len(donnees) < 10:
        return None
    deviceid, _temps, detail = struct.unpack_from("=HII", donnees, 0)
    return deviceid, detail


class Suivi:
    """État des boutons vu par les esclaves ; échéances de vérification."""

    def __init__(self, maitres):
        self.maitres = set(maitres)
        self.tenus = set()        # (deviceid esclave, bouton) enfoncés
        self.echeances = {}       # bouton -> instant de vérification

    def tenu(self, bouton):
        return any(b == bouton for _, b in self.tenus)

    def brut(self, deviceid, bouton, presse, maintenant):
        if deviceid in self.maitres or bouton not in BOUTONS:
            return  # les bruts du maître sont gelés avec lui : on ne lit que les esclaves
        if presse:
            self.tenus.add((deviceid, bouton))
            self.echeances.pop(bouton, None)
        else:
            self.tenus.discard((deviceid, bouton))
            if not self.tenu(bouton):
                self.echeances[bouton] = maintenant + SEUIL_S

    def prochaine(self):
        return min(self.echeances.values()) if self.echeances else None

    def dues(self, maintenant):
        dues = [b for b, t in self.echeances.items() if t <= maintenant]
        for b in dues:
            del self.echeances[b]
        return sorted(dues)

    def gele(self, bouton, masque):
        """Vrai si le maître tient `bouton` (masque X du pointeur maître) alors
        qu'aucun esclave ne le tient."""
        return bool(masque & BOUTONS[bouton]) and not self.tenu(bouton)


def noms_xkb(valeur):
    """_XKB_RULES_NAMES (chaînes séparées par NUL) -> dict rules/model/layout/variant/options."""
    if isinstance(valeur, bytes):
        valeur = valeur.decode("latin-1")
    champs = (valeur or "").split("\0") + [""] * 5
    return dict(zip(("rules", "model", "layout", "variant", "options"), champs[:5]))


def options(noms):
    return [o for o in noms.get("options", "").split(",") if o]


def argv_setxkbmap(noms, opts):
    """Commande setxkbmap qui remet exactement ces noms et ces options."""
    argv = ["setxkbmap"]
    for cle in ("rules", "model", "layout", "variant"):
        if noms.get(cle):
            argv += [f"-{cle}", noms[cle]]
    argv += ["-option", ""]
    for o in opts:
        argv += ["-option", o]
    return argv


def journal(texte):
    print(f"qaf-degel : {texte}", file=sys.stderr, flush=True)


# --- X (python-xlib) -----------------------------------------------------------
class Degel:
    def __init__(self):
        from Xlib import X, display
        from Xlib.ext import xinput
        self.X, self.xinput = X, xinput
        self.d = display.Display()
        for ext in ("XInputExtension", "XTEST"):
            if not self.d.has_extension(ext):
                raise RuntimeError(f"extension X absente : {ext}")
        self.d.xinput_query_version()
        self.racine = self.d.screen().root
        maitres = [dv.deviceid for dv in self.d.xinput_query_device(xinput.AllDevices).devices
                   if dv.use in (xinput.MasterPointer, xinput.MasterKeyboard)]
        self.suivi = Suivi(maitres)
        self.racine.xinput_select_events(
            [(xinput.AllDevices, xinput.RawButtonPressMask | xinput.RawButtonReleaseMask)])
        self.d.flush()
        self.dernier = -PAUSE_S

    def tourner(self):
        xi = self.xinput
        while True:
            prochaine = self.suivi.prochaine()
            attente = None if prochaine is None else max(0.0, prochaine - time.monotonic())
            if not self.d.pending_events():
                select.select([self.d.fileno()], [], [], attente)
            while self.d.pending_events():
                e = self.d.next_event()
                ev = getattr(e, "evtype", None)
                if ev in (xi.RawButtonPress, xi.RawButtonRelease):
                    lu = lire_brut(e.data)
                    if lu:
                        self.suivi.brut(lu[0], lu[1], ev == xi.RawButtonPress, time.monotonic())
            for bouton in self.suivi.dues(time.monotonic()):
                masque = self.racine.query_pointer().mask
                if self.suivi.gele(bouton, masque):
                    self.gel(bouton, masque)

    def _proprietaire(self, xid):
        """« pid commande » du client X qui a créé la ressource xid (X-Resource)."""
        try:
            from Xlib.ext import res
            ids = self.d.res_query_client_ids([{"client": xid, "mask": res.LocalClientPIDMask}])
            pid = next((c.value[0] for c in ids.ids if c.value), None)
            if pid is None:
                return "?"
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f"{pid} {f.read().replace(bytes(1), b' ').decode(errors='replace')[:80].strip()}"
        except Exception:
            return "?"

    def gel(self, bouton, masque):
        maintenant = time.monotonic()
        p = self.racine.query_pointer()
        sous = p.child.id if p.child else 0
        journal(f"GEL détecté : bouton {bouton} tenu par le pointeur maître (masque {masque}) "
                f"mais relâché sur tous les périphériques depuis {SEUIL_S:.0f} s ; pointeur {p.root_x},{p.root_y} "
                f"sur {sous:#x} (client {self._proprietaire(sous) if sous else '-'})")
        if maintenant - self.dernier < PAUSE_S:
            journal("dégel automatique déjà tenté il y a moins de 30 s : rien de plus "
                    "(procédure à la main : Ctrl+Alt+F2 puis Ctrl+Alt+F1, docs/GEL-BUREAU.md)")
            return
        self.dernier = maintenant
        ok = self.degeler(bouton)
        if ok:
            journal("dégel réussi ; propriétaire de la saisie dans /var/log/Xorg.0.log "
                    "(« Ungrabbing all devices »)")
        else:
            journal("dégel sans effet : Ctrl+Alt+F2 puis Ctrl+Alt+F1 (docs/GEL-BUREAU.md)")
        try:
            from . import notif
            from .i18n import t
            notif.notifier(t("degel.debloque") if ok else t("degel.bloque"),
                           t("degel.debloque.texte") if ok else t("degel.bloque.texte"))
        except Exception:
            pass

    def degeler(self, bouton):
        X, d = self.X, self.d
        atome = d.intern_atom("_XKB_RULES_NAMES")
        prop = self.racine.get_full_property(atome, X.AnyPropertyType)
        noms = noms_xkb(prop.value if prop else b"")
        avant = options(noms)
        pose = OPTION_DEGEL not in avant
        if pose:
            self._setxkbmap(argv_setxkbmap(noms, avant + [OPTION_DEGEL]))
        premier = d.display.info.min_keycode
        nombre = d.display.info.max_keycode - premier + 1
        carte = d.get_keyboard_mapping(premier, nombre)
        libres = [premier + i for i in range(nombre) if not any(carte[i])]
        try:
            if not libres:
                journal("aucun keycode libre : dégel impossible")
                return False
            k = libres[-1]
            d.change_keyboard_mapping(k, [(XF86_UNGRAB,)])
            d.sync()
            time.sleep(0.1)
            d.xtest_fake_input(X.KeyPress, k)
            d.xtest_fake_input(X.KeyRelease, k)
            d.sync()
            time.sleep(0.1)
            d.change_keyboard_mapping(k, [(X.NoSymbol,)])
            d.sync()
        finally:
            if pose:
                self._setxkbmap(argv_setxkbmap(noms, avant))
        time.sleep(0.3)
        return not (self.racine.query_pointer().mask & BOUTONS[bouton])

    def _setxkbmap(self, argv):
        try:
            subprocess.run(argv, check=True, timeout=10, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except Exception as e:
            journal(f"{' '.join(argv)} : {e!r}")


def main():
    if not os.environ.get("DISPLAY"):
        journal("DISPLAY absent : rien à surveiller")
        return 1
    try:
        g = Degel()
    except Exception as e:
        journal(f"démarrage impossible ({e!r})")
        return 1
    journal(f"surveille les boutons {sorted(BOUTONS)} (seuil {SEUIL_S:.0f} s)")
    g.tourner()
    return 0


if __name__ == "__main__":
    sys.exit(main())
