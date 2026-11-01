"""Table déclarative des 35 fournisseurs de l'agent (sys-agent) : les 23 du plugin agents
d'Omarchy, même ordre, mêmes ids, et, après ses CLI, les 12 que seul son lanceur connaît
(bin/omarchy-agent), dans son ordre. Aucune dépendance, aucun GTK :
qaf_mcp/fournisseurs.py la lit. dom0 ne l'importe PAS : il a sa propre table d'ids
(qaf_ui/usage.py), et un test croisé vérifie que les deux listes sont égales.

Quatre familles :
- « cli » : une CLI à elle, lancée sur le serveur MCP qaf de son fichier de
  configuration, installée à la demande dans ~/.local, sans sudo ;
- « cle » : Claude Code lancé par « claude --settings <fichier 0600> » vers le point
  compatible Anthropic du fournisseur ;
- « local » : pareil, vers une adresse donnée par l'utilisateur ;
- « connexion_seule » : jamais activable. grokbot (rien ne passe par le Doorman), et les
  CLI dont la validation est absente ou n'a pas été vue tenir (pi, freebuff, amp, ori, mcode,
  kiro-cli, essais du 01/11) : installation et connexion seulement.

Aucune entrée ne contient de mode « tout passe » (test) : le Doorman reste le contrôle,
et Omarchy n'est jamais suivi là où il lance ses agents sans validation. Les versions
sont épinglées ici ; une mise à jour = changer la ligne, et GEL_DEPENDANCES pour npm et uv. Une empreinte que l'éditeur ne publie pas a été relevée
par QAF le 31/10/2026 : elle prouve que le fichier n'a pas changé depuis.
Les noms sont ceux de la carte de dom0 (test croisé), l'assistant y renvoie.
"""

from dataclasses import dataclass, field

LANCEUR = "/usr/lib/qaf/qaf_mcp/lanceur.py"
PYTHON = "/usr/bin/python3"
# Commande du serveur qaf pour toutes les CLI : -I et un chemin absolu, donc ni
# PYTHONPATH (certaines CLI le bloquent dans l'environnement d'un serveur MCP) ni cwd.
COMMANDE_MCP = (PYTHON, "-I", LANCEUR)

CLAUDE = "/usr/bin/claude"


@dataclass(frozen=True)
class Install:
    """Comment poser la CLI dans ~/.local (mode : npm, archive, binaire, script, uv)."""
    mode: str
    paquet: str = ""       # npm, uv : nom@version ou nom==version
    url: str = ""          # archive, binaire, script
    sha256: str = ""       # obligatoire pour archive, binaire, script (test) : ils seront exécutés
    bin_dans: str = ""     # archive : chemin du binaire une fois dépliée
    retirer: int = 0       # archive : composants de tête à retirer
    node_min: tuple = ()   # npm : version de Node exigée
    args: tuple = ()       # script : arguments
    freres: tuple = ()     # archive : binaires voisins que la CLI lance par le PATH
    node_prive: bool = False  # npm : installée et lancée avec NODE, pas le Node du modèle


@dataclass(frozen=True)
class Lancer:
    """Forme du lancement. texte : « tiret » (-- texte), « egal » (option=texte),
    « option » (option, texte), « seul » (texte positionnel), « affiche » (la CLI
    ne sait pas semer une session interactive : le texte s'affiche avant elle)."""
    args: tuple = ()
    suite: tuple = ()      # arguments de reprise ; vide = jamais de reprise automatique
    texte: str = "tiret"
    option: str = ""
    env: tuple = ()        # paires VAR=valeur, jamais de secret
    retire: tuple = ()     # variables à retirer de l'environnement
    # Reprise + texte : la CLI n'envoie pas le texte. Avec un texte, nouvelle conversation.
    suite_sans_texte: bool = False


@dataclass(frozen=True)
class Mcp:
    """Déclaration du serveur qaf. format : json, toml, yaml, cordis (patch dsh, un bloc
    à nous dans une liste YAML). cles : chemin dans le fichier. entree : ce qu'on y pose,
    en plus de la commande."""
    format: str
    chemin: str
    cles: tuple = ("mcpServers", "qaf")
    entree: dict = field(default_factory=dict)
    commande_liste: bool = False            # opencode : command = [cmd, args…]
    apres: tuple = ()                       # sous-commande à lancer après l'écriture
    allow: tuple = ()                       # (fichier JSON, clés, valeur ajoutée à la liste)
    reglages: tuple = ()                    # (fichier JSON, clés, valeur) posée si la clé est absente
    socle: tuple = ()                       # pareil, mais gardée au retrait : la CLI refuse son fichier sans elle
    # (fichier JSON ou .yaml, clés, valeur) : posée si absente, et autre valeur = jamais
    # activable. Pour les CLI qui valident par réglage, sans drapeau : voir mcp_configure.
    exige: tuple = ()
    couper: tuple = ()                      # cordis : ids de lignes du socle désactivées
    # (fichier JSON, clés, « * » = tout membre) : présent ou fichier illisible = jamais
    # activable. Pour ce qui défait une validation depuis un autre endroit que l'exigé.
    refuse: tuple = ()
    tot: bool = False                       # posé dès avant la connexion, qui lance déjà la CLI
    # Fichiers (sous le dossier personnel) que la CLI exécuterait au lancement : présent =
    # jamais activable.
    absents: tuple = ()


@dataclass(frozen=True)
class Preuve:
    """Preuve de connexion sans lire le secret. genre : fichier, motif (glob sous le
    dossier personnel), ligne (une ligne « NOM=… » non vide), json (clé non vide),
    json_non_vide (objet avec au moins une clé, le fichier naissant à {}), sqlite (table
    cle[0] avec au moins une ligne, lecture seule, aucune valeur lue), texte (le motif
    cle[0] trouvé dans le fichier, rien n'en sort)."""
    genre: str
    chemin: str
    cle: tuple = ()


@dataclass(frozen=True)
class Fournisseur:
    id: str
    nom: str
    famille: str  # cli, cle, local, connexion_seule
    version: str = ""
    install: Install | None = None
    binaires: tuple = ()     # le premier présent gagne ; « ~ » = dossier personnel
    login: tuple = ()        # arguments ; vide avec cli = connexion dans son TUI
    login_tui: str = ""      # ce qu'il faut taper dans le TUI
    login_page: str = ""     # login ouvre une page web : où aller dedans
    preuves: tuple = ()
    # Preuve de fichier trop faible : cette commande doit aussi réussir (sortie jamais lue).
    etat_commande: tuple = ()
    # connexion_seule : sous-commande à faire réussir avant tout autre lancement.
    avant_connexion: tuple = ()
    lancer: Lancer = field(default_factory=Lancer)
    mcp: Mcp | None = None
    # Famille « cle » :
    base_url: str = ""
    env: tuple = ()          # variables des settings, paires (nom, valeur)
    modeles_url: str = ""    # liste de modèles
    modeles_cle: str = ""    # data, models…
    modeles_fixes: tuple = ()
    cle_url: str = ""        # où créer la clé
    verif_url: str = ""      # contrôle de clé sans dépense
    note: str = ""           # clé de texte de l'assistant


# npm et uv : les dépendances publiées après ce relevé sont écartées. Le dernier
# paquet de la table doit lui être antérieur.
GEL_DEPENDANCES = "2026-10-31T00:00:00Z"

_NPM = "npm"
_AGENT = "~/.local/bin/"

# Fichiers que Kilo fond dans sa configuration, projet écarté par KILO_DISABLE_PROJECT_CONFIG.
_KILO_FICHIERS = (*(".config/kilo/" + n for n in ("config.json", "kilo.json", "kilo.jsonc", "opencode.json",
                                                  "opencode.jsonc")),
                  *(d + n for d in (".kilo/", ".kilocode/") for n in ("kilo.json", "kilo.jsonc")))


# .credentials.yaml de dsh : une clé, jamais le grant de session qu'il contient dès sa naissance.
# Forme lue dans dsh-credentials-local : « kind: api-key » sous records, ou une entrée sous refs.
_DSH_CLE = r"^[ \t]+kind:[ \t]*[\"']?api-key\b|^refs:[ \t]*\n[ \t]+[^\s#]"


def _npm(paquet, node_min=()):
    return Install(_NPM, paquet=paquet, node_min=node_min)


def _cle(id, nom, base, **k):
    return Fournisseur(id=id, nom=nom, famille="cle", base_url=base, **k)


FOURNISSEURS = (
    Fournisseur(  # chemin actuel : lancé par demande.py et console.py, inchangé
        "claude", "Claude", "cli", version="2.1.310", binaires=(CLAUDE,), login=("auth", "login"),
        preuves=(Preuve("fichier", ".claude/.credentials.json"), Preuve("fichier", ".config/qaf/jeton-claude")),
        lancer=Lancer(suite=("--continue",)),
        mcp=None),
    Fournisseur(
        "codex", "Codex", "cli", version="0.166.0", install=_npm("@openai/codex@0.166.0"),
        binaires=("/usr/bin/codex", _AGENT + "codex"), login=("login",),
        preuves=(Preuve("fichier", ".codex/auth.json"),),
        lancer=Lancer(args=("--ask-for-approval", "on-request", "--sandbox", "workspace-write"),
                      suite=("resume", "--last")),
        mcp=Mcp("toml", ".codex/config.toml", ("mcp_servers", "qaf"),
                {"env_vars": ["QAF_REQUEST"], "default_tools_approval_mode": "approve"})),
    Fournisseur(
        "grok", "Grok", "cli", version="1.0.70",
        install=Install("binaire", url="https://storage.googleapis.com/grok-build-public-artifacts/cli/"
                                       "grok-1.0.70-linux-x86_64",
                        sha256="22d281c5964c5444147328096ba2a221f3f0874c6858e27c825656a80f6e58e9"),
        binaires=("/usr/bin/grok", "~/.grok/bin/grok", _AGENT + "grok"), login=("login",),
        preuves=(Preuve("fichier", ".grok/auth.json"),),
        lancer=Lancer(args=("--no-auto-update",), suite=("-c",)),
        mcp=Mcp("toml", ".grok/config.toml", ("mcp_servers", "qaf"), {"env": {"QAF_REQUEST": "${QAF_REQUEST:-}"}})),
    Fournisseur(
        "kimi", "Kimi", "cli", version="2.4.1", install=_npm("@moonshot-ai/kimi-code@2.4.1", (22, 19)),
        binaires=(_AGENT + "kimi",), login=("login",),
        preuves=(Preuve("fichier", ".kimi-code/credentials/kimi-code.json"),),
        # « kimi -p » est non interactif et passe en Never Ask : refusé. Le texte s'affiche.
        lancer=Lancer(suite=("-c",), texte="affiche", env=(("KIMI_CODE_NO_AUTO_UPDATE", "1"),)),
        mcp=Mcp("json", ".kimi-code/mcp.json")),
    Fournisseur(
        "cursor", "Cursor", "cli", version="2026.10.22-27dcf5f",
        install=Install("archive", url="https://downloads.cursor.com/lab/2026.10.22-27dcf5f/linux/x64/"
                                       "agent-cli-package.tar.gz",
                        sha256="85296944e4600a83c4b764f578d1502bf9c416db200d33a593e8f3ecabfdc294",
                        bin_dans="cursor-agent", retirer=1),
        binaires=(_AGENT + "cursor-agent", _AGENT + "agent"), login=("login",),
        preuves=(Preuve("fichier", ".config/cursor/auth.json"),),
        lancer=Lancer(args=("agent",), suite=("--continue",)),
        mcp=Mcp("json", ".cursor/mcp.json", entree={"env": {"QAF_REQUEST": "${env:QAF_REQUEST}"}},
                apres=("mcp", "enable", "qaf"),
                allow=((".cursor/cli-config.json", ("permissions", "allow"), "Mcp(qaf:*)"),))),
    Fournisseur(  # un produit dans le nuage, sans Doorman : une ligne d'usage, jamais un agent
        "grokbot", "Grok Bot", "connexion_seule"),
    Fournisseur(
        "copilot", "Copilot", "cli", version="1.0.105", install=_npm("@github/copilot@1.0.105", (22,)),
        binaires=(_AGENT + "copilot",), login=("login",),
        preuves=(Preuve("motif", ".copilot/*token*"), Preuve("motif", ".copilot/*auth*")),
        lancer=Lancer(suite=("--continue",), texte="option", option="-i"),
        mcp=Mcp("json", ".copilot/mcp-config.json",
                entree={"type": "local", "env": {"QAF_REQUEST": "${QAF_REQUEST:-}"}, "tools": ["*"]})),
    Fournisseur(
        "muse", "Muse", "cli", version="1.4.14-R7309.1",
        install=Install("binaire", url="https://lookaside.facebook.com/lookaside/muse/download/"
                                       "?channel=muse&version=1.4.14-R7309.1&file=muse-x86-linux",
                        sha256="0809aac7cb58d18f5022b6d05e02aa1974683c0ec430e5f04d25b60ed17de2da"),
        binaires=(_AGENT + "muse",), login_tui="/login",
        preuves=(Preuve("fichier", ".config/muse/auth.json"),),
        lancer=Lancer(args=("--approval-mode", "on-request")),
        mcp=Mcp("json", ".config/muse/settings.json", ("mcp_servers", "qaf"), {"transport": "stdio"},
                socle=((".config/muse/settings.json", ("schema_version",), 1),))),
    Fournisseur(
        "agy", "Google", "cli", version="1.4.12",
        install=Install("archive", url="https://github.com/google-antigravity/antigravity-cli/releases/download/"
                                       "1.4.12/agy_cli_linux_x64.tar.gz",
                        sha256="3368d1923fc869f21ec8fce83252a8c96c89283aac9393958ac91fbf7a3949e4",
                        bin_dans="antigravity"),
        binaires=(_AGENT + "agy",),
        preuves=(Preuve("motif", ".gemini/antigravity-cli/*oauth-token*"),),
        lancer=Lancer(suite=("-c",), texte="option", option="-i", env=(("AGY_CLI_DISABLE_AUTO_UPDATE", "1"),)),
        mcp=Mcp("json", ".gemini/config/mcp_config.json")),
    Fournisseur(  # jamais son installeur : il greffe Claude Code et Codex
        "kimchi", "Kimchi", "cli", version="v1.25.1",
        install=Install("archive", url="https://github.com/getkimchi/kimchi/releases/download/v1.25.1/"
                                       "kimchi_linux_amd64.tar.gz",
                        sha256="2d782cf78d40d0303d72374ae6d827895b083afcfd8dc6be483d503a854cd242",
                        bin_dans="bin/kimchi"),
        binaires=(_AGENT + "kimchi",), login=("setup",),
        preuves=(Preuve("json", ".config/kimchi/config.json", ("api_key",)),),
        lancer=Lancer(texte="affiche"),
        mcp=Mcp("json", ".config/kimchi/harness/mcp.json")),
    Fournisseur(  # script officiel du commit, empreinte relevée, puis figé par --commit ; jamais sudo ni Desktop
        "hermes", "Hermes", "cli", version="v0.22.5",
        install=Install("script", url="https://raw.githubusercontent.com/NousResearch/hermes-agent/"
                                      "bf0203a794a07aac3ff764972ab9087baa4e4f4e/scripts/install.sh",
                        sha256="99cb3691d55019c6973a7acb775a915648666cc24e483ca827a1cdbb3531a674",
                        args=("--commit", "bf0203a794a07aac3ff764972ab9087baa4e4f4e", "--skip-setup")),
        binaires=(_AGENT + "hermes",), login=("setup", "--portal"),
        preuves=(Preuve("fichier", ".hermes/auth.json"),),
        lancer=Lancer(args=("chat",), suite=("-c",), texte="egal", option="--query=",
                      retire=("HERMES_SESSION_SOURCE",)),
        mcp=Mcp("yaml", ".hermes/config.yaml", ("mcp_servers", "qaf"), {"env": {"QAF_REQUEST": "${QAF_REQUEST}"}})),
    Fournisseur(
        "devin", "Devin", "cli", version="3000.11.234",
        install=Install("archive", url="https://static.devin.ai/cli/3000.11.234/"
                                       "devin-3000.11.234-x86_64-unknown-linux.tar.gz",
                        sha256="201370cddd2924294e3ca6b46fd8ece49b5763953b644a1dd823fce5490cb41a",
                        bin_dans="bin/devin"),
        binaires=(_AGENT + "devin",), login=("auth", "login"),
        preuves=(Preuve("fichier", ".local/share/devin/credentials.toml"),),
        lancer=Lancer(suite=("-c",)),
        mcp=Mcp("json", ".config/devin/mcp_config.json",
                allow=((".config/devin/config.json", ("permissions", "allow"), "mcp__qaf__*"),))),
    Fournisseur(
        "vibe", "Mistral Vibe", "cli", version="3.0.4", install=Install("uv", paquet="mistral-vibe==3.0.4"),
        binaires=(_AGENT + "vibe",), login=("--setup",),
        preuves=(Preuve("ligne", ".vibe/.env", ("MISTRAL_API_KEY",)),),
        lancer=Lancer(suite=("--continue",), texte="seul"),
        mcp=Mcp("toml", ".vibe/config.toml", ("mcp_servers",), {"name": "qaf", "transport": "stdio"})),
    Fournisseur(
        "qwen", "Qwen", "cli", version="0.26.4", install=_npm("@qwen-code/qwen-code@0.26.4", (22,)),
        binaires=(_AGENT + "qwen",), login_tui="/auth",
        preuves=(Preuve("json", ".qwen/settings.json", ("security", "auth")),),
        lancer=Lancer(suite=("--continue",), texte="egal", option="--prompt-interactive="),
        mcp=Mcp("json", ".qwen/settings.json", entree={"trust": True})),
    Fournisseur(  # V2, comme Omarchy. Sans --standalone, son service d'arrière-plan survit à la session et
        # garde l'environnement du premier lancement, donc la QAF_REQUEST d'une ancienne demande (vu le 31/10).
        "opencode", "OpenCode", "cli", version="2.2.5", install=_npm("@opencode/cli@2.2.5"),
        binaires=(_AGENT + "opencode",), login=("--standalone",), login_tui="/connect",
        preuves=(Preuve("fichier", ".local/share/opencode/auth.json"),
                 Preuve("fichier", ".local/share/opencode/opencode-v2.db")),
        lancer=Lancer(args=("--standalone",), texte="affiche"),
        mcp=Mcp("json", ".config/opencode/opencode.json", ("mcp", "servers", "qaf"), {"type": "local"},
                commande_liste=True,
                reglages=((".config/opencode/opencode.json", ("autoupdate",), False),))),
    Fournisseur(  # sans validation : pas d'argv d'agent, pas de MCP tant que QAF n'a pas de garde pour lui.
        # Sa connexion est son TUI complet : dans un dossier vide, ressources de projet ignorées.
        "pi", "Pi", "connexion_seule", version="1.4.0",
        install=Install("archive", url="https://github.com/earendil-works/pi/releases/download/v1.4.0/"
                                       "pi-linux-x64.tar.gz",
                        sha256="f2e4e4329603223d03260cdb8bd5033dbdc1f73ba58ab8939db2e56546e115d2",
                        bin_dans="pi", retirer=1),
        binaires=(_AGENT + "pi",), login=("--no-approve",), login_tui="/login",
        preuves=(Preuve("json_non_vide", ".pi/agent/auth.json"),), note="note_sans_validation",
        lancer=Lancer(env=(("PI_SKIP_VERSION_CHECK", "1"), ("PI_TELEMETRY", "0")))),
    Fournisseur(  # sans --approval-mode, omp est en yolo ; « write » laisserait passer les écritures
        "omp", "Oh My Pi", "cli", version="18.14.2",
        install=Install("binaire", url="https://github.com/can1357/oh-my-pi/releases/download/v18.14.2/omp-linux-x64",
                        sha256="cde0bc4ac8ec333c4880039a5242ff33dbfc6544747d67aeb99bb077874aeec1"),
        binaires=(_AGENT + "omp",), login=("login",),
        preuves=(Preuve("sqlite", ".omp/agent/agent.db", ("auth_credentials",)),),
        lancer=Lancer(args=("--approval-mode", "always-ask-cached"), suite=("-c",)),
        mcp=Mcp("json", ".omp/agent/mcp.json", entree={"type": "stdio"}, tot=True,
                apres=("config", "set", "startup.checkUpdate", "false"))),
    Fournisseur(  # « write » ne demande rien en --approvals manual, même hors du dossier (vu le 01/11)
        "ori", "Ori", "connexion_seule", version="0.16.3-f483c10",
        install=Install("binaire", url="https://github.com/OpenRouterLabs/ori-releases/releases/download/"
                                       "cli-0.16.3-f483c10/ori-linux-x64",
                        sha256="315e16fd1992c9415d1c2b05eaadffc6071d9521687487614af24e5e90604fc0"),
        binaires=(_AGENT + "ori",), login=("login",), etat_commande=("auth",),
        preuves=(Preuve("fichier", ".ori/auth/credentials.sqlite"),), note="note_ori",
        lancer=Lancer(env=(("ORI_NO_UPDATE_CHECK", "1"), ("ORI_DISABLE_AUTOUPDATER", "1"),
                           ("ORI_TELEMETRY_LEVEL", "off")))),
    Fournisseur(  # jamais « crush run » : il approuve tout, le texte s'affiche. Connexion dans son TUI :
        # « crush login » sans argument ne connaît que Hyper
        "crush", "Crush", "cli", version="0.99.0",
        install=Install("archive", url="https://github.com/charmbracelet/crush/releases/download/v0.99.0/"
                                       "crush_0.99.0_Linux_x86_64.tar.gz",
                        sha256="ec20f001028dfb7b6427225f0cf5215081275c8f09f45001a8535847cc4fd6ea",
                        bin_dans="crush", retirer=1),
        binaires=(_AGENT + "crush",), login_tui="ctrl+l",
        preuves=(Preuve("json", ".local/share/crush/crush.json", ("providers", "hyper", "api_key")),
                 Preuve("json", ".local/share/crush/crush.json", ("providers", "openai", "oauth")),
                 Preuve("json", ".local/share/crush/crush.json", ("providers", "copilot", "api_key")),
                 Preuve("json", ".local/share/crush/crush.json", ("providers", "xai", "api_key"))),
        lancer=Lancer(suite=("--continue",), texte="affiche", env=(("CRUSH_DISABLE_METRICS", "1"),)),
        mcp=Mcp("json", ".config/crush/crush.json", ("mcp", "qaf"), {"type": "stdio"},
                absents=("agent/crushrc", "agent/.crushrc"))),
    Fournisseur(  # « chat » : sans passerelle ; sans tools.exec.mode, aucune question ; write et edit n'ont
        # aucune validation, donc bornés au dossier de travail par tools.fs. Node 24 privé
        "openclaw", "OpenClaw", "cli", version="2026.10.9",
        install=Install(_NPM, paquet="openclaw@2026.10.9", node_prive=True),
        binaires=(_AGENT + "openclaw",), login=("onboard", "--skip-daemon"),
        etat_commande=("models", "status", "--check"),
        preuves=(Preuve("fichier", ".openclaw/state/openclaw.sqlite"),),
        lancer=Lancer(args=("chat",), texte="option", option="--message",
                      env=(("OPENCLAW_NO_AUTO_UPDATE", "1"),)),
        mcp=Mcp("json", ".openclaw/openclaw.json", ("mcp", "servers", "qaf"),
                {"transport": "stdio", "env": {"QAF_REQUEST": "${QAF_REQUEST}"}}, tot=True,
                reglages=((".openclaw/openclaw.json", ("update", "checkOnStart"), False),),
                exige=((".openclaw/openclaw.json", ("tools", "exec", "mode"), "ask"),
                       (".openclaw/openclaw.json", ("tools", "fs", "workspaceOnly"), True)),
                refuse=((".openclaw/openclaw.json", ("agents", "entries", "*", "tools", "exec")),
                        (".openclaw/openclaw.json", ("agents", "entries", "*", "tools", "fs")),
                        (".openclaw/openclaw.json", ("agents", "entries", "*", "workspace")),
                        (".openclaw/openclaw.json", ("agents", "defaults", "workspace"))))),
    Fournisseur(  # préversion, aucune stable publiée. Jamais DSH_PERMISSION_MODE, journal vers l'éditeur coupé
        "dsh", "DeepSeek Harness", "cli", version="0.2.1-rc.5", install=_npm("@deepseek-ai/dsh@0.2.1-rc.5", (22, 19)),
        binaires=(_AGENT + "dsh",), login=("web",), login_page="Settings → Models",
        # Ce fichier naît au premier lancement, avec un simple grant de session : il faut une vraie clé.
        preuves=(Preuve("texte", ".dsh/.credentials.yaml", (_DSH_CLE,)),),
        lancer=Lancer(args=("web",), texte="affiche", env=(("DSH_TELEMETRY_DISABLED", "1"),),
                      retire=("DSH_PERMISSION_MODE",)),
        mcp=Mcp("cordis", ".dsh/cordis.patch.yml", couper=("session-log-deepseek",), tot=True)),
    Fournisseur(  # permissionMode: default laisse passer rm hors dossier, touch et write sans question (vu)
        "mcode", "MiniMax Code", "connexion_seule", version="0.7.4",
        install=_npm("@minimax-ai/code@0.7.4", (22, 19)),
        binaires=(_AGENT + "mcode",), login=("login", "--region", "global", "--no-browser"),
        preuves=(Preuve("fichier", ".minimax/auth.json"),), note="note_mcode"),
    Fournisseur(  # validation et serveur jamais vus sans compte. Trois binaires ; jamais son install.sh
        "kiro-cli", "Kiro CLI", "connexion_seule", version="2.37.0",
        install=Install("archive", url="https://prod.download.cli.kiro.dev/stable/2.37.0/"
                                       "kirocli-x86_64-linux.tar.gz",
                        sha256="02c49873ba64285f34048aac377ccf0b546b5627f39dd775bae9fa306112ff19",
                        bin_dans="bin/kiro-cli", retirer=1, freres=("kiro-cli-chat", "kiro-cli-term")),
        binaires=(_AGENT + "kiro-cli",), login=("login", "--use-device-flow"),
        # Une fausse clé laisse une ligne dans auth_kv : la commande d'état tranche.
        preuves=(Preuve("sqlite", ".local/share/kiro-cli/data.sqlite3", ("auth_kv",)),), etat_commande=("whoami",),
        avant_connexion=("settings", "app.disableAutoupdates", "true"), note="note_kiro"),
    Fournisseur(  # aucune validation, ni drapeau ni réglage ; jamais son lanceur npm, il se met à jour seul
        "freebuff", "Freebuff", "connexion_seule", version="0.2.35",
        install=Install("archive", url="https://github.com/CodebuffAI/codebuff-community/releases/download/"
                                       "freebuff-v0.2.35/freebuff-linux-x64.tar.gz",
                        sha256="41b1e82659cd5c9185ebb201b1e4ddaff0a2fc1c68e14a2d18577b0de6e29335",
                        bin_dans="freebuff"),
        binaires=(_AGENT + "freebuff",), login=("login",),
        preuves=(Preuve("fichier", ".config/manicode/credentials.json"),), note="note_freebuff"),
    Fournisseur(  # fourche d'OpenCode : tout passe par défaut, sauf ce qu'exige la ligne « permission ».
        # KILO_PERMISSION s'applique après tous ses fichiers, la ligne du fichier reste un second cran.
        # KILO_NO_DAEMON : un démon garderait la demande de son premier lancement.
        "kilo", "Kilo", "cli", version="7.8.18", install=_npm("@kilocode/cli@7.8.18"),
        binaires=(_AGENT + "kilo",), login=("auth", "login"),
        preuves=(Preuve("fichier", ".local/share/kilo/auth.json"),),
        lancer=Lancer(suite=("--continue",), suite_sans_texte=True, texte="egal", option="--prompt=",
                      env=(("KILO_NO_DAEMON", "1"), ("KILO_DISABLE_AUTOUPDATE", "1"),
                           ("KILO_DISABLE_PROJECT_CONFIG", "1"),
                           ("KILO_PERMISSION", '{"bash": "ask", "edit": "ask"}')),
                      retire=("KILO_CONFIG", "KILO_CONFIG_DIR", "KILO_CONFIG_CONTENT")),
        mcp=Mcp("json", ".config/kilo/kilo.json", ("mcp", "qaf"), {"type": "local"}, commande_liste=True,
                reglages=((".config/kilo/kilo.json", ("autoupdate",), False),),
                exige=((".config/kilo/kilo.json", ("permission",), {"bash": "ask", "edit": "ask"}),),
                refuse=tuple((x, (s, "*", "permission")) for x in _KILO_FICHIERS for s in ("agent", "mode")))),
    Fournisseur(  # approuve tout par défaut ; le réglage qui le rétablirait n'a jamais été vu marcher
        "amp", "Amp", "connexion_seule", version="0.0.1793476832-ga2d2ae",
        install=Install("binaire", url="https://static.ampcode.com/cli/0.0.1793476832-ga2d2ae/amp-linux-x64",
                        sha256="8c43cea70ab7006005ce07c5c5f809b02b6acbf40f41c308524204a6ef51d9a7"),
        binaires=(_AGENT + "amp",), login=("login",),
        preuves=(Preuve("fichier", ".local/share/amp/secrets.json"),), note="note_amp",
        lancer=Lancer(env=(("AMP_SKIP_UPDATE_CHECK", "1"),))),
    Fournisseur(  # sans défaut, ${QAF_REQUEST} absente fait tomber le serveur : « :- » obligatoire
        "commandcode", "Command Code", "cli", version="1.88.2", install=_npm("command-code@1.88.2", (22,)),
        binaires=(_AGENT + "commandcode",), login=("login",), etat_commande=("status",),
        preuves=(Preuve("fichier", ".commandcode/auth.json"),),
        lancer=Lancer(args=("--permission-mode", "default"), suite=("--continue",), suite_sans_texte=True,
                      env=(("COMMANDCODE_SKIP_UPDATES", "1"), ("DO_NOT_TRACK", "1"))),
        mcp=Mcp("json", ".commandcode/mcp.json",
                entree={"transport": "stdio", "enabled": True, "env": {"QAF_REQUEST": "${QAF_REQUEST:-}"}},
                allow=((".commandcode/settings.json", ("permissions", "allow"), "mcp__qaf__*"),))),
    _cle("zai", "Z.ai", "https://api.z.ai/api/anthropic",
         modeles_fixes=("glm-5.3[1m]", "glm-5.3-flash[1m]"), note="note_zai"),
    _cle("ollama", "Ollama Cloud", "https://ollama.com",
         modeles_url="https://ollama.com/api/tags", modeles_cle="models", cle_url="https://ollama.com/settings/keys"),
    _cle("minimax", "MiniMax", "https://api.minimax.io/anthropic",
         modeles_url="https://api.minimax.io/anthropic/v1/models", modeles_cle="data",
         modeles_fixes=("MiniMax-M3.1-Flash[1m]",), env=(("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "524288"),)),
    _cle("openrouter", "OpenRouter", "https://openrouter.ai/api",
         modeles_url="https://openrouter.ai/api/v1/models", modeles_cle="data",
         modeles_fixes=("~anthropic/claude-opus-latest[1m]",), cle_url="https://openrouter.ai/settings/keys",
         verif_url="https://openrouter.ai/api/v1/key"),
    _cle("deepseek", "DeepSeek", "https://api.deepseek.com/anthropic",
         modeles_url="https://api.deepseek.com/models", modeles_cle="data",
         modeles_fixes=("deepseek-flash[1m]", "deepseek-v4-pro")),
    _cle("fireworks", "Fireworks", "https://api.fireworks.ai/inference",
         modeles_fixes=("glm-latest", "kimi-fast-latest", "deepseek-flash-latest"),
         env=(("ENABLE_TOOL_SEARCH", "true"),)),
    _cle("synthetic", "Synthetic", "https://api.synthetic.new/anthropic",
         modeles_url="https://api.synthetic.new/openai/v1/models", modeles_cle="data",
         modeles_fixes=("syn:large:vision", "syn:small:text"),
         env=(("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"), ("CLAUDE_CODE_ATTRIBUTION_HEADER", "0"))),
    Fournisseur("local", "IA locale", "local"),
)

PAR_ID = {f.id: f for f in FOURNISSEURS}
IDS = tuple(f.id for f in FOURNISSEURS)
IDS_HORS_CLAUDE = IDS[1:]

# Outil uv de Mistral Vibe : absent de tpl-agent, posé épinglé dans le dossier personnel.
UV = Install("archive", url="https://github.com/astral-sh/uv/releases/download/0.13.6/"
                            "uv-x86_64-unknown-linux-gnu.tar.gz",
             sha256="f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645",
             bin_dans="uv", retirer=1)

# Node privé d'OpenClaw, qui exige 24.22.1 ou plus : le Node du modèle ne bouge pas.
NODE = Install("archive", url="https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-x64.tar.gz",
               sha256="361718c444e49af2ca5a110c83ba6a8901143f23ab285ebbef0341f391f482a9",
               bin_dans="bin/node", retirer=1)
NODE_VERSION = "24.22.2"

# Modes « tout passe » : aucune entrée de la table ne doit les porter, ni en argv, ni en
# variable, ni en valeur posée dans un fichier (test). Beaucoup sont le choix d'Omarchy.
INTERDITS = ("--yolo", "--dangerously-skip-permissions", "--allow-all", "--always-approve", "--force",
             "--auto", "--approve-for-me", "dangerous", "never", "--allow-dangerously-skip-permissions",
             "--auto-approve", "--dangerously-allow-all", "--trust-all-tools", "--trust", "--trust-agents",
             "-y", "yolo", "bypass", "bypassPermissions", "dont-ask", "dontAsk", "accept-edits", "self-drive",
             "danger-full-access", "full", "auto", "allow")

# Lieux possibles d'un serveur local : une adresse réseau, ou un qube voisin joint par
# qubes.ConnectTCP (autorisé dans dom0, tunnel de fournisseurs.poser_tunnel).
LIEUX_LOCAL = ("reseau", "voisin")
