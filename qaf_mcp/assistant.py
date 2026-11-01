"""Assistant du terminal de connexion (qaf.Connexion+ID, sys-agent) : simple, en
français, anglais ou chinois selon la locale, étapes [1/5], clé lue par getpass.

Clé ou adresse : rien n'est enregistré avant un aller-retour réussi sur /v1/messages,
en flux et avec outil : un serveur qui liste ses modèles sans savoir appeler d'outils
ferait un agent incapable d'agir. CLI : installation épinglée annoncée avant le
téléchargement, connexion, preuve sans lire le secret, serveur MCP qaf posé.

Rien ne s'active ici : le geste reste dans la carte de l'agent, côté dom0. Le résultat
(ok, echec) est noté pour qaf.Usage. Tout texte venu du réseau passe par fournisseurs.
ID_MODELE avant d'être affiché : pas de séquence d'échappement dans le terminal.
Bibliothèque standard seulement.
"""

import getpass
import json
import os
import signal
import subprocess
import sys

from . import fournisseurs as f4
from .fournisseurs_table import IDS, LIEUX_LOCAL, PAR_ID

DELAI_TEST = 300
PAGE = 9
OUTIL = {"name": "qaf_sonde", "description": "Outil de test. Appelle-le une fois.",
         "input_schema": {"type": "object", "properties": {}}}
CONSIGNE = "Appelle l'outil qaf_sonde maintenant, sans rien écrire d'autre."

TEXTES = {
    "tete_cle": ("Ce terminal tourne dans sys-agent. Ta clé y reste enregistrée, et l'agent peut la lire : "
                 "prends une clé réservée à QAF, avec un plafond.\n"
                 "Ta connexion Claude est aussi lisible ici : QAF ne l'envoie pas à ce fournisseur, mais un contenu "
                 "piégé pourrait pousser l'agent à la lire et à la lui transmettre.\n"
                 "Colle la clé depuis un autre qube : Ctrl+Maj+C là-bas, Ctrl+Maj+V ici, puis Maj+Inser. Rien ne s'affiche.",
                 "This terminal runs in sys-agent. Your key stays stored there, and the agent can read it: "
                 "use a key reserved for QAF, with a spending cap.\n"
                 "Your Claude sign-in can be read here too: QAF does not send it to this provider, but rigged content "
                 "could push the agent to read it and pass it on.\n"
                 "Paste the key from another qube: Ctrl+Shift+C there, Ctrl+Shift+V here, then Shift+Insert. Nothing is displayed.",
                 "此终端运行在 sys-agent 中。你的密钥会保存在这里，智能体可以读取：请使用 QAF 专用并设有限额的密钥。\n"
                 "你的 Claude 登录信息在此同样可读：QAF 不会把它发给此服务商，但恶意内容可能诱使智能体读取并转发。\n"
                 "从其他 qube 粘贴密钥：在那边按 Ctrl+Shift+C，在这里按 Ctrl+Shift+V，然后按 Shift+Insert。输入时不会显示任何内容。"),
    "tete_cli": ("Ce terminal tourne dans sys-agent. Ta connexion y reste enregistrée, et l'agent peut la lire : "
                 "prends un compte avec un plafond.\n"
                 "Ta connexion Claude est aussi lisible ici : QAF ne l'envoie pas à ce fournisseur, mais un contenu "
                 "piégé pourrait pousser l'agent à la lire et à la lui transmettre.",
                 "This terminal runs in sys-agent. Your sign-in stays stored there, and the agent can read it: "
                 "use an account with a spending cap.\n"
                 "Your Claude sign-in can be read here too: QAF does not send it to this provider, but rigged content "
                 "could push the agent to read it and pass it on.",
                 "此终端运行在 sys-agent 中。你的登录信息会保存在这里，智能体可以读取：请使用设有限额的账户。\n"
                 "你的 Claude 登录信息在此同样可读：QAF 不会把它发给此服务商，但恶意内容可能诱使智能体读取并转发。"),
    "titre": ("QAF · Connexion de {nom}", "QAF · Connecting {nom}", "QAF · 正在连接 {nom}"),
    "e_install": ("Installation", "Installation", "安装"),
    "e_connexion": ("Connexion", "Sign-in", "登录"),
    "e_preuve": ("Vérification", "Check", "检查"),
    "e_mcp": ("Serveur qaf", "qaf server", "qaf 服务器"),
    "e_resultat": ("Résultat", "Result", "结果"),
    "e_cle": ("Clé", "Key", "密钥"),
    "e_modele": ("Modèle", "Model", "模型"),
    "e_test": ("Test", "Test", "测试"),
    "e_enreg": ("Enregistrement", "Saving", "保存"),
    "e_adresse": ("Adresse", "Address", "地址"),
    "annonce": ("{nom} {version} va être téléchargé dans ~/.local de sys-agent, sans sudo.\nSource : {source}",
                "{nom} {version} will be downloaded into ~/.local in sys-agent, without sudo.\nSource: {source}",
                "将把 {nom} {version} 下载到 sys-agent 的 ~/.local，不使用 sudo。\n来源：{source}"),
    "empreinte_prevue": ("Empreinte attendue, contrôlée avant tout usage : {h}…",
                         "Expected checksum, verified before any use: {h}…", "预期校验值，使用前校验：{h}…"),
    "empreinte": ("Empreinte contrôlée : {h}…", "Checksum verified: {h}…", "已校验：{h}…"),
    "sans_empreinte": ("Pas d'empreinte sur cette voie : version épinglée, dépendances gelées à la date du relevé, "
                       "scripts d'installation du paquet exécutés.",
                       "No checksum on this path: pinned version, dependencies frozen at the survey date, the "
                       "package's install scripts run.",
                       "此方式没有校验值：版本已固定，依赖冻结在记录日期，软件包的安装脚本会运行。"),
    "continuer": ("Continuer ? [o/N] : ", "Continue? [y/N]: ", "继续？[y/N]："),
    "deja_installe": ("Déjà installé.", "Already installed.", "已安装。"),
    "deja_connecte": ("Déjà connecté.", "Already signed in.", "已登录。"),
    "tui": ("La connexion se fait dans {nom} : tape {cmd}, puis quitte pour revenir ici.",
            "Sign in inside {nom}: type {cmd}, then quit to come back here.",
            "请在 {nom} 中登录：输入 {cmd}，然后退出返回此处。"),
    "page_web": ("La connexion se fait dans la page web de {nom} : ouvre {cmd}, puis Ctrl+C ici pour revenir. "
                 "Le navigateur s'ouvre seul et reste ouvert : ferme-le ensuite.",
                 "Sign in on {nom}'s web page: open {cmd}, then Ctrl+C here to come back. "
                 "The browser opens by itself and stays open: close it afterwards.",
                 "请在 {nom} 的网页中登录：打开 {cmd}，然后在此按 Ctrl+C 返回。浏览器会自动打开并保持打开：之后请关闭它。"),
    "tui_seul": ("La connexion se fait dans {nom} : suis ses instructions, puis quitte pour revenir ici.",
                 "Sign in inside {nom}: follow its instructions, then quit to come back here.",
                 "请在 {nom} 中按提示登录，然后退出返回此处。"),
    "preuve_ok": ("Connexion vue.", "Sign-in found.", "已确认登录。"),
    "preuve_non": ("Connexion non vue : rien n'a été activé.", "Sign-in not found: nothing was activated.",
                   "未发现登录信息：未启用任何内容。"),
    "mcp_ok": ("Serveur qaf déclaré ; le reste du fichier n'a pas changé.",
               "qaf server declared; the rest of the file is unchanged.", "已声明 qaf 服务器；文件其余部分未改动。"),
    "pret": ("{nom} est prêt. Rien n'a changé : ton fournisseur actif reste le même.\n"
             "Pour basculer : carte de l'agent, ligne {nom}, Utiliser.",
             "{nom} is ready. Nothing changed: your active provider stays the same.\n"
             "To switch: agent card, {nom} row, Use.",
             "{nom} 已就绪。未作任何更改：当前服务商保持不变。\n切换方法：智能体卡片，{nom} 一行，使用。"),
    "pret_tiers": ("Test réussi. Ce fournisseur verra tout ce que lit l'agent.",
                   "Test passed. This provider will see everything the agent reads.",
                   "测试通过。此服务商将看到智能体读取的全部内容。"),
    "pret_local": ("Test réussi. Ton serveur verra tout ce que lit l'agent.",
                   "Test passed. Your server will see everything the agent reads.",
                   "测试通过。你的服务器将看到智能体读取的全部内容。"),
    "note_zai": ("Depuis le 16/10/2026, une clé standard dans un outil de code facture au compte, pas au Coding Plan.",
                 "Since 2026-10-16, a standard key in a coding tool bills the account, not the Coding Plan.",
                 "自 2026-10-16 起，在编程工具中使用标准密钥将按账户计费，而非 Coding Plan。"),
    "cle_ou": ("Crée-la ici : {url}", "Create it here: {url}", "在此创建：{url}"),
    "saisie_cle": ("Clé (rien ne s'affiche pendant la frappe) : ", "Key (nothing is displayed as you type): ",
                   "密钥（输入时不显示）："),
    "saisie_cle_vide": ("Clé (Entrée si le serveur n'en demande pas) : ",
                        "Key (Enter if the server does not need one): ", "密钥（服务器不需要时直接按 Enter）："),
    "cle_recue": ("Clé reçue, {n} caractères.", "Key received, {n} characters.", "已收到密钥，{n} 个字符。"),
    "cle_fictive": ("Aucune clé : QAF pose un jeton fictif, Claude Code n'envoie pas ta connexion Claude à ce serveur.",
                    "No key: QAF sets a placeholder token, Claude Code does not send your Claude sign-in to this "
                    "server.",
                    "无密钥：QAF 使用占位令牌，Claude Code 不会把你的 Claude 登录信息发送到此服务器。"),
    "cle_refusee": ("Clé refusée par {nom}. Rien n'a été enregistré.", "Key refused by {nom}. Nothing was saved.",
                    "{nom} 拒绝了密钥。未保存任何内容。"),
    "credit_epuise": ("Crédit épuisé chez {nom}. Rien n'a été enregistré.", "{nom} is out of credit. Nothing was saved.",
                      "{nom} 额度已用尽。未保存任何内容。"),
    "injoignable": ("Pas de réponse sur cette adresse. Le serveur tourne-t-il ?",
                    "No answer at this address. Is the server running?", "此地址无响应。服务器在运行吗？"),
    "adresse": ("Adresse (sans /v1, port compris), ou voisin 11434 pour un qube voisin : ",
                "Address (without /v1, with the port), or neighbour 11434 for a neighbouring qube: ",
                "地址（不含 /v1，包含端口），相邻 qube 请输入 相邻 11434："),
    "voisin_avant": ("Ce passage ne s'ouvre pas ici : carte de l'agent, IA locale, Autoriser un qube voisin, même port.",
                     "This path is not opened here: agent card, Local AI, Allow a neighbouring qube, same port.",
                     "此通道不在这里开启：智能体卡片，本地 AI，授权相邻 qube，使用相同端口。"),
    "voisin_ferme": ("Rien ne répond par le tunnel : qube non autorisé, port différent, ou serveur pas encore lancé. "
                     "Le test a pu démarrer le qube voisin : réessaie une fois son serveur prêt.",
                     "Nothing answers through the tunnel: qube not allowed, different port, or server not started "
                     "yet. The test may have started the neighbouring qube: retry once its server is ready.",
                     "隧道没有响应：qube 未获授权、端口不同，或服务器尚未启动。测试可能已启动相邻 qube：请在其服务器就绪后重试。"),
    "adresse_v1": ("QAF retire /v1 de lui-même.", "QAF removes /v1 by itself.", "QAF 会自动去掉 /v1。"),
    "adresse_invalide": ("Adresse non valable : http:// ou https://, un hôte, un port.",
                         "Invalid address: http:// or https://, a host, a port.",
                         "地址无效：需要 http:// 或 https://、主机和端口。"),
    "en_clair": ("En clair : ce que l'agent lit et écrit passera sans chiffrement sur ce réseau.",
                 "Unencrypted: what the agent reads and writes will cross this network in clear.",
                 "未加密：智能体读写的内容将以明文经过此网络。"),
    "liste_modeles": ("{n} modèles savent appeler des outils.", "{n} models can call tools.", "{n} 个模型支持调用工具。"),
    "pris_en_charge": ("pris en charge", "supported", "受支持"),
    "essai": ("essai", "trial", "试用"),
    "choix_modele": ("Choix [1], un mot pour filtrer, n page suivante, ou l'id exact : ",
                     "Choice [1], a word to filter, n next page, or the exact id: ",
                     "选择 [1]，输入关键词筛选，n 下一页，或输入完整 id："),
    "saisie_modele": ("Id du modèle : ", "Model id: ", "模型 id："),
    "modele_invalide": ("Id de modèle non valable.", "Invalid model id.", "模型 id 无效。"),
    "t_protocole": ("Protocole Anthropic", "Anthropic protocol", "Anthropic 协议"),
    "t_flux": ("Réponse en flux", "Streaming reply", "流式响应"),
    "t_outil": ("Appel d'outil", "Tool call", "工具调用"),
    "openai_seul": ("Ce serveur liste ses modèles mais ne répond pas sur /v1/messages : il ne parle que le protocole "
                    "OpenAI. Il faut un serveur compatible Anthropic (Ollama 0.14 ou plus, LM Studio 0.4.1 ou plus, "
                    "llama-server récent).",
                    "This server lists its models but does not answer on /v1/messages: it only speaks the OpenAI "
                    "protocol. You need an Anthropic-compatible server (Ollama 0.14 or later, LM Studio 0.4.1 or "
                    "later, recent llama-server).",
                    "此服务器能列出模型，但不响应 /v1/messages：它只支持 OpenAI 协议。需要兼容 Anthropic 的服务器"
                    "（Ollama 0.14 及以上、LM Studio 0.4.1 及以上、较新的 llama-server）。"),
    "pas_flux": ("Le serveur ne répond pas en flux : Claude Code ne pourrait pas s'en servir.",
                 "The server does not stream: Claude Code could not use it.", "服务器不支持流式响应：Claude Code 无法使用。"),
    "pas_outil": ("Ce modèle répond mais n'appelle pas d'outils : l'agent ne pourrait rien faire.",
                  "This model answers but does not call tools: the agent could not do anything.",
                  "此模型能回复但不调用工具：智能体将无法执行任何操作。"),
    "modele_refuse": ("Le fournisseur refuse ce modèle.", "The provider refuses this model.", "服务商拒绝此模型。"),
    "ollama_contexte": ("Ollama : le contexte vaut souvent 4096 par défaut. Relance le serveur avec "
                        "OLLAMA_CONTEXT_LENGTH=65536 (64 k conseillés).",
                        "Ollama: the context is often 4096 by default. Restart the server with "
                        "OLLAMA_CONTEXT_LENGTH=65536 (64 k recommended).",
                        "Ollama：默认上下文通常为 4096。请使用 OLLAMA_CONTEXT_LENGTH=65536 重启服务器（建议 64 k）。"),
    "non_enregistre": ("Connexion non enregistrée.", "Connection not saved.", "连接未保存。"),
    "reessai": ("r réessayer   q quitter\nChoix : ", "r retry   q quit\nChoice: ", "r 重试   q 退出\n选择："),
    "deja_configure": ("{nom} est déjà configuré.", "{nom} is already set up.", "{nom} 已配置。"),
    "menu_cle": ("   1  Changer la clé, l'adresse ou le modèle\n   2  Refaire le test\n   3  Retirer ce fournisseur\n"
                 "   4  Quitter\nChoix [4] : ",
                 "   1  Change the key, address or model\n   2  Run the test again\n   3  Remove this provider\n"
                 "   4  Quit\nChoice [4]: ",
                 "   1  更改密钥、地址或模型\n   2  重新测试\n   3  移除此服务商\n   4  退出\n选择 [4]："),
    "menu_cli": ("   1  Refaire la connexion\n   2  Retirer ce fournisseur\n   3  Quitter\nChoix [3] : ",
                 "   1  Sign in again\n   2  Remove this provider\n   3  Quit\nChoice [3]: ",
                 "   1  重新登录\n   2  移除此服务商\n   3  退出\n选择 [3]："),
    "retire": ("{nom} est retiré. La CLI et son compte, s'il y en a, restent à toi.",
               "{nom} is removed. The CLI and its account, if any, stay yours.",
               "{nom} 已移除。命令行工具及其账户（如有）仍归你所有。"),
    "connexion_seule": ("{nom} tourne dans le nuage de Cursor, sans le Doorman : il reste une ligne d'usage, jamais "
                        "l'agent. Il se connecte par le compte Cursor.",
                        "{nom} runs in Cursor's cloud, without the Doorman: it stays a usage line, never the agent. "
                        "It signs in through the Cursor account.",
                        "{nom} 运行在 Cursor 云端，不经过 Doorman：它只显示用量，绝不会成为智能体。通过 Cursor 账户登录。"),
    "note_sans_validation": ("{nom} ne demande jamais rien : ses commandes partiraient dans sys-agent sans validation. "
                             "QAF n'a pas de garde pour lui : il reste en connexion seule, jamais l'agent.\n"
                             "Son terminal de connexion est l'agent lui-même : n'y tape que la connexion, rien d'autre.",
                             "{nom} never asks: its commands would run in sys-agent with no approval. QAF has no "
                             "guard for it: it stays sign-in only, never the agent.\n"
                             "Its sign-in terminal is the agent itself: type only the sign-in there, nothing else.",
                             "{nom} 从不询问：其命令会在 sys-agent 中不经批准直接运行。QAF 没有针对它的防护：它只能登录，绝不会成为智能体。\n"
                             "它的登录终端就是智能体本身：只在其中输入登录命令，不要输入其他内容。"),
    "note_freebuff": ("{nom} ne demande jamais rien : ses commandes partiraient dans sys-agent sans validation. Il "
                      "reste en connexion seule, jamais l'agent. Il affiche de la publicité, son éditeur peut garder "
                      "ou revendre ce que tu lui envoies, et il envoie des statistiques d'usage dès le premier "
                      "lancement, avant toute connexion.",
                      "{nom} never asks: its commands would run in sys-agent with no approval. It stays sign-in only, "
                      "never the agent. It shows ads, its publisher may keep or sell what you send it, and it sends "
                      "usage statistics from the first launch, before any sign-in.",
                      "{nom} 从不询问：其命令会在 sys-agent 中不经批准直接运行。它只能登录，绝不会成为智能体。它会显示广告，其发布方可能保留或出售你发送的内容，"
                      "并且从首次启动起、在登录之前就发送使用统计。"),
    "note_amp": ("{nom} approuve tout par défaut, et le réglage censé rétablir la validation n'a jamais été vu "
                 "marcher : il reste en connexion seule, jamais l'agent.",
                 "{nom} approves everything by default, and the setting meant to bring approval back has never been "
                 "seen working: it stays sign-in only, never the agent.",
                 "{nom} 默认批准一切，而用于恢复批准的设置从未被证实有效：它只能登录，绝不会成为智能体。"),
    "note_ori": ("{nom} ne demande rien avant d'écrire un fichier, même hors de son dossier : il reste en connexion "
                 "seule, jamais l'agent.",
                 "{nom} writes files without asking, even outside its folder: it stays sign-in only, never the agent.",
                 "{nom} 写入文件时不询问，即使在其文件夹之外：它只能登录，绝不会成为智能体。"),
    "note_mcode": ("{nom} laisse passer des commandes sans question, rm hors de son dossier compris : il reste en "
                   "connexion seule, jamais l'agent.",
                   "{nom} lets commands through without asking, even rm outside its folder: it stays sign-in only, "
                   "never the agent.",
                   "{nom} 不询问就放行命令，包括其文件夹之外的 rm：它只能登录，绝不会成为智能体。"),
    "note_kiro": ("Sans compte, la validation de {nom} et son serveur MCP n'ont jamais été vus : il reste en connexion "
                  "seule, jamais l'agent.",
                  "Without an account, {nom}'s approval and MCP server were never seen: it stays sign-in only, never "
                  "the agent.",
                  "没有账户，无法验证 {nom} 的批准机制和 MCP 服务器：它只能登录，绝不会成为智能体。"),
    "reste_connexion": ("{nom} est connecté. Il reste en connexion seule : rien n'a changé pour l'agent.",
                        "{nom} is signed in. It stays sign-in only: nothing changed for the agent.",
                        "{nom} 已登录。它仍然只能登录：智能体未作任何更改。"),
    "validation_contredite": ("Cette CLI a déjà un réglage de validation différent de celui qu'exige QAF : il n'a pas "
                              "été modifié, et elle ne peut pas devenir l'agent.",
                              "This CLI already has an approval setting other than the one QAF requires: it was not "
                              "changed, and the CLI cannot become the agent.",
                              "此工具已有与 QAF 要求不同的批准设置：该设置未改动，此工具不能成为智能体。"),
    "reglage_refuse": ("Cette CLI n'a pas accepté le réglage que pose QAF : le serveur qaf a été retiré, elle ne peut "
                       "pas devenir l'agent.",
                       "This CLI did not accept the setting QAF applies: the qaf server was removed, it cannot become "
                       "the agent.",
                       "此工具未接受 QAF 设置的选项：qaf 服务器已移除，它不能成为智能体。"),
    "reglage_refuse_seul": ("Cette CLI n'a pas accepté le réglage que pose QAF : la connexion n'a pas été lancée.",
                            "This CLI did not accept the setting QAF applies: the sign-in was not started.",
                            "此工具未接受 QAF 设置的选项：未启动登录。"),
    "fichier_execute": ("Un fichier du dossier de l'agent serait exécuté au lancement de cette CLI (crushrc) : retire-le "
                        "d'abord. Elle ne peut pas devenir l'agent tant qu'il est là.",
                        "A file in the agent's folder would be run when this CLI starts (crushrc): remove it first. The "
                        "CLI cannot become the agent while it is there.",
                        "智能体文件夹中的一个文件会在此工具启动时被执行（crushrc）：请先删除。该文件存在期间，此工具不能成为智能体。"),
    "cursor_absent": ("Installe d'abord Cursor : carte de l'agent, ligne Cursor.",
                      "Install Cursor first: agent card, Cursor row.", "请先安装 Cursor：智能体卡片，Cursor 一行。"),
    "cle_invalide": ("Clé non valable : un seul mot, sans espace.", "Invalid key: a single word, no spaces.",
                     "密钥无效：应为不含空格的单个字符串。"),
    "reglage_invalide": ("Réglage non valable.", "Invalid setting.", "设置无效。"),
    "config_illisible": ("Le fichier de configuration de cette CLI n'est pas lisible : il n'a pas été modifié.",
                         "This CLI's configuration file cannot be read: it was not changed.",
                         "无法读取此命令行工具的配置文件：文件未改动。"),
    "config_autre_qaf": ("Ce fichier déclare déjà un autre serveur nommé qaf : il n'a pas été modifié.",
                         "This file already declares another server named qaf: it was not changed.",
                         "此文件已声明另一个名为 qaf 的服务器：文件未改动。"),
    "installation_echec": ("Installation échouée.", "Installation failed.", "安装失败。"),
    "empreinte_differente": ("Empreinte différente de celle attendue : fichier supprimé, rien n'est installé.",
                             "Checksum differs from the expected one: file deleted, nothing installed.",
                             "校验值与预期不符：文件已删除，未安装任何内容。"),
    "node_trop_vieux": ("La version de Node de sys-agent est trop ancienne pour cette CLI.",
                        "sys-agent's Node version is too old for this CLI.", "sys-agent 的 Node 版本过旧，无法运行此工具。"),
    "libatomic_absente": ("Il manque libatomic1 dans le modèle de sys-agent ; QAF n'utilise pas sudo ici.",
                          "libatomic1 is missing from the sys-agent template; QAF does not use sudo here.",
                          "sys-agent 模板缺少 libatomic1；QAF 在此不使用 sudo。"),
    "non_activable": ("Ce fournisseur ne peut pas être lancé.", "This provider cannot be started.", "无法启动此服务商。"),
    "erreur_inattendue": ("Erreur inattendue : rien de plus n'a été fait.", "Unexpected error: nothing more was done.",
                          "发生意外错误：未执行其他操作。"),
    "entree_fermer": ("Entrée pour fermer.", "Press Enter to close.", "按 Enter 关闭。"),
}
RANGS = {"fr": 0, "en": 1, "zh": 2}


def langue(environ):
    """Même ordre que qaf_ui/i18n.py : LANGUAGE, sauf locale C ou POSIX, puis LC_ALL,
    LC_MESSAGES, LANG ; en… = anglais, zh… = chinois, le reste = français."""
    vals = [environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    premiere = next((v for v in vals if v), "")
    choix = environ.get("LANGUAGE", "").split(":")[0] if premiere not in ("C", "POSIX") else ""
    choix = (choix or premiere).lower()
    return "en" if choix.startswith("en") else "zh" if choix.startswith("zh") else "fr"


# --- essais réseau ------------------------------------------------------------------

def modele_api(modele):
    """L'id qui part vraiment. [1m] est une consigne de contexte pour Claude Code, qui le
    retire avant d'envoyer (vu sur 2.1.316 : glm-5.3[1m] arrive en glm-5.3) : le test doit
    envoyer la même chose que lui."""
    return modele.removesuffix("[1m]")


def _message(modele, flux, outils=True, max_tokens=64):
    corps = {"model": modele_api(modele), "max_tokens": max_tokens, "stream": flux,
             "messages": [{"role": "user", "content": CONSIGNE}]}
    if outils:
        corps["tools"] = [OUTIL]
    return json.dumps(corps).encode()


def _raison_code(code):
    return {0: "injoignable", 401: "cle_refusee", 403: "cle_refusee", 402: "credit_epuise",
            400: "modele_refuse", 404: "modele_refuse", 422: "modele_refuse"}.get(code, "modele_refuse")


def protocole_anthropic(base, cle, modele, ouvrir=None):
    """Seul un 200 avec type message prouve le protocole : un 404 ne dit rien, un
    modèle inconnu donne 404 aussi. max_tokens 1, le moins cher possible."""
    code, corps = f4.appeler(base + "/v1/messages", cle, DELAI_TEST, _message(modele, False, False, 1), ouvrir)
    if code != 200:
        return False, code
    try:
        return json.loads(corps.decode("utf-8")).get("type") == "message", code
    except (UnicodeDecodeError, ValueError, AttributeError, RecursionError):
        return False, code


def aller_retour(base, cle, modele, ouvrir=None):
    """POST /v1/messages en flux avec un outil. Rend {flux, outil, raison}."""
    code, corps = f4.appeler(base + "/v1/messages", cle, DELAI_TEST, _message(modele, True), ouvrir)
    if code != 200:
        return {"flux": False, "outil": False, "raison": _raison_code(code)}
    debut = fin = outil = False
    for ligne in corps.splitlines():
        if not ligne.startswith(b"data:"):
            continue
        try:
            e = json.loads(ligne[5:].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(e, dict):
            continue
        debut = debut or e.get("type") == "message_start"
        fin = fin or e.get("type") == "message_stop"
        b = e.get("content_block")
        if e.get("type") == "content_block_start" and isinstance(b, dict) \
                and b.get("type") == "tool_use" and b.get("name") == OUTIL["name"]:
            outil = True
    flux = debut and fin
    return {"flux": flux, "outil": outil, "raison": "" if flux and outil else "pas_outil" if flux else "pas_flux"}


def liste_modeles(f, base, cle, ouvrir=None):
    """[(id, contexte, essai)] sûrs à afficher ; la liste fixe de la table d'abord."""
    urls = [f.modeles_url] if f.modeles_url else []
    if f.famille == "local":
        urls = [base + "/v1/models", base + "/api/v1/models"]
    # OpenRouter : liste publique, demandée sans en-têtes. Avec anthropic-version, il rend le format
    # Anthropic, sans supported_parameters, et le tri par outils ne garderait rien (vu avec une vraie clé).
    cle_liste = "" if f.id == "openrouter" else cle
    out, vus = [], set()

    def ajouter(i, contexte=0):
        if isinstance(i, str) and f4.ID_MODELE.match(i) and i not in vus:
            vus.add(i)
            out.append((i, contexte if type(contexte) is int and 0 < contexte < 10 ** 9 else 0,
                        f.id == "openrouter" and "anthropic" not in i))

    for m in f.modeles_fixes:
        ajouter(m)
    for u in urls:
        code, corps = f4.appeler(u, cle_liste, f4.DELAI_RESEAU, ouvrir=ouvrir)
        if code != 200:
            continue
        try:
            d = json.loads(corps.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        liste = d.get(f.modeles_cle or "data") if isinstance(d, dict) else None
        for m in liste if isinstance(liste, list) else []:
            if not isinstance(m, dict):
                continue
            if f.id == "openrouter" and "tools" not in (m.get("supported_parameters") or ()):
                continue
            ajouter(m.get("id") or m.get("name"), m.get("context_length", 0))
        if out:
            break
    out.sort(key=lambda m: m[2])
    return out


# --- l'assistant --------------------------------------------------------------------

class Assistant:
    def __init__(self, id, maison, environ, entree=input, secret=getpass.getpass, ecrire=print, ouvrir=None,
                 executer=subprocess.run):
        self.f, self.maison, self.environ = PAR_ID[id], maison, environ
        self.entree, self.secret, self.ecrire, self.ouvrir, self.executer = entree, secret, ecrire, ouvrir, executer
        self.rang = RANGS[langue(environ)]
        self.lieu = "reseau"
        self.pose_tot = False  # déclaration posée ici avant la connexion, pas encore confirmée

    def t(self, cle, **champs):
        return TEXTES[cle][self.rang].format(**champs)

    def dire(self, cle, **champs):
        self.ecrire(self.t(cle, **champs))

    def etape(self, n, cle):
        self.ecrire(f"\n[{n}/5] {self.t(cle)}")

    def oui(self):
        return self.entree(self.t("continuer")).strip().lower() in ("o", "oui", "y", "yes", "是")

    def env(self):
        """Même garde qu'au lancement : la connexion démarre déjà la CLI."""
        lc = self.f.lancer
        env = {k: v for k, v in self.environ.items() if k not in f4.ENV_RETIRE + lc.retire}
        env["PATH"] = f4.chemin_de(self.f, self.maison) + ":" + env.get("PATH", "/usr/bin:/bin")
        env.update(lc.env)
        return env

    def lancer(self, argv, **k):
        try:
            return self.executer(argv, env=self.env(), **k).returncode
        except (OSError, subprocess.SubprocessError):
            return 1

    def lancer_muet(self, args):
        return self.lancer([f4.binaire(self.f, self.maison), *args], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=30)

    def connecte(self):
        """La preuve de fichier, et la commande d'état si la table en a une."""
        f = self.f
        return f4.preuve(f, self.maison) and not (f.etat_commande and self.lancer_muet(f.etat_commande) != 0)

    def lancer_connexion(self, argv, **k):
        """Ctrl+C va à la CLI seule : on passe à l'étape suivante, on ne quitte pas. Un
        gestionnaire, pas SIG_IGN : la CLI hériterait de l'ignorance, pas d'un gestionnaire."""
        avant = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            return self.lancer(argv, **k)
        finally:
            signal.signal(signal.SIGINT, avant)

    def executer_tout(self):
        """Vrai : prêt. Faux : échec. None : aucun test n'a tourné (retrait, quitter), il
        n'y a pas de résultat à noter."""
        self.ecrire(self.t("titre", nom=self.f.nom))
        if self.f.id == "claude":  # le chemin d'origine : claude auth login
            return self.lancer([f4.CLAUDE, *self.f.login]) == 0
        if self.f.famille == "connexion_seule":
            return self.connexion_seule()
        return self.cli() if self.f.famille == "cli" else self.cle()

    def connexion_seule(self):
        if self.f.install is not None:
            return self.cli_sans_agent()
        self.dire("connexion_seule", nom=self.f.nom)
        cursor = PAR_ID["cursor"]
        b = f4.binaire(cursor, self.maison)
        if b is None:
            self.dire("cursor_absent")
            return False
        if not f4.preuve(cursor, self.maison) and self.lancer([b, *cursor.login]) != 0:
            return False
        return f4.preuve(cursor, self.maison)

    def cli_sans_agent(self):
        """CLI qui ne peut pas être l'agent : installation et connexion, jamais de serveur
        qaf, rien à activer. Le texte dit pourquoi avant toute installation."""
        f = self.f
        self.ecrire("")
        self.dire(f.note, nom=f.nom)
        self.dire("tete_cli")
        self.etape(1, "e_install")
        if not self.cli_installer():
            return False
        if f.avant_connexion and self.lancer_muet(f.avant_connexion) != 0:
            self.dire("reglage_refuse_seul")
            return False
        self.etape(2, "e_connexion")
        if self.connecte():
            self.dire("deja_connecte")
        else:
            self.cli_connexion()
        self.etape(3, "e_preuve")
        if not self.connecte():
            self.dire("preuve_non")
            return False
        self.dire("preuve_ok")
        self.etape(5, "e_resultat")
        self.dire("reste_connexion", nom=f.nom)
        return True

    # CLI ------------------------------------------------------------------------

    def cli_installer(self):
        f = self.f
        if f4.binaire(f, self.maison):
            self.dire("deja_installe")
            return True
        i = f.install
        self.dire("annonce", nom=f.nom, version=f.version, source=i.url or i.paquet)
        self.dire("empreinte_prevue", h=i.sha256[:16]) if i.sha256 else self.dire("sans_empreinte")
        if not self.oui():
            return False
        try:
            f4.installer(f, self.maison, self.ouvrir, self.executer, self.environ)
        except f4.Refus as e:
            self.dire(e.args[0])
            return False
        if i.sha256:
            self.dire("empreinte", h=i.sha256[:16])
        if f4.binaire(f, self.maison) is None:
            self.dire("installation_echec")
            return False
        return True

    def cli(self):
        f = self.f
        self.ecrire("")
        self.dire("tete_cli")
        if f4.mcp_declare(f, self.maison):  # même déconnectée : la déclaration se retire d'ici
            self.dire("deja_configure", nom=f.nom)
            choix = self.entree(self.t("menu_cli")).strip() or "3"
            if choix == "2":
                f4.retirer(f.id, self.maison)
                self.dire("retire", nom=f.nom)
                return None
            if choix != "1":
                return None
            if f4.binaire(f, self.maison):
                return self.cli_connexion() and self.cli_fin()
        self.etape(1, "e_install")
        if not self.cli_installer():
            return False
        self.etape(2, "e_connexion")
        if self.connecte():
            self.dire("deja_connecte")
        elif not self.cli_connexion():
            return False
        return self.cli_fin()

    def cli_connexion(self):
        f = self.f
        b = f4.binaire(f, self.maison)
        if f.mcp is not None and f.mcp.tot and not self.poser_tot():
            return False
        if f.login_page:
            self.dire("page_web", nom=f.nom, cmd=f.login_page)
        elif f.login_tui:
            self.dire("tui", nom=f.nom, cmd=f.login_tui)
        elif not f.login:
            self.dire("tui_seul", nom=f.nom)
        cwd = f4.dossier_vide(self.maison, f.id) if f.famille == "connexion_seule" else self.maison
        self.lancer_connexion([b, *f.login], cwd=cwd)
        return True

    def poser_tot(self):
        """Coupures qui doivent tenir pendant la connexion. L'échec de la sous-commande ne se
        juge pas ici : elle n'a peut-être pas encore de compte, cli_fin la relance et tranche."""
        f = self.f
        deja = f4.mcp_declare(f, self.maison)
        try:
            f4.poser_mcp(f, self.maison)
        except (f4.Refus, OSError) as e:
            self.dire(e.args[0] if isinstance(e, f4.Refus) else "config_illisible")
            return False
        self.pose_tot = not deja
        if f.mcp.apres:
            self.lancer_muet(f.mcp.apres)
        return True

    def defaire_pose(self):
        self.pose_tot = False
        try:
            f4.retirer_mcp(self.f, self.maison)
        except (f4.Refus, OSError):
            pass

    def abandon(self):
        """Sortie avant la fin : une déclaration posée ici et jamais confirmée ne reste pas."""
        if self.pose_tot:
            self.defaire_pose()

    def cli_fin(self):
        f = self.f
        self.etape(3, "e_preuve")
        if not self.connecte():
            self.dire("preuve_non")
            if f.mcp.tot:
                self.defaire_pose()
            return False
        self.dire("preuve_ok")
        self.etape(4, "e_mcp")
        try:
            f4.poser_mcp(f, self.maison)
        except (f4.Refus, OSError) as e:
            self.dire(e.args[0] if isinstance(e, f4.Refus) else "config_illisible")
            return False
        if f.mcp.apres and self.lancer_muet(f.mcp.apres) != 0:  # déclaration défaite, sinon la ligne serait activable
            self.defaire_pose()
            self.dire("reglage_refuse")
            return False
        self.pose_tot = False
        self.dire("mcp_ok")
        self.etape(5, "e_resultat")
        self.dire("pret", nom=f.nom)
        return True

    # clé ou adresse --------------------------------------------------------------

    def cle(self):
        f = self.f
        self.ecrire("")
        self.dire("tete_cle")
        if f.note:
            self.dire(f.note)
        if f4.reglages_valides(f.id, self.maison):
            self.dire("deja_configure", nom=f.nom)
            choix = self.entree(self.t("menu_cle")).strip() or "4"
            if choix == "3":
                f4.retirer(f.id, self.maison)
                self.dire("retire", nom=f.nom)
                return None
            if choix == "2":
                c = f4.config(self.maison, f.id)
                self.lieu = c.get("lieu") if c.get("lieu") in LIEUX_LOCAL else "reseau"
                return self.tester_et_enregistrer(c.get("base", ""), f4.lire_cle(self.maison, f.id), c.get("modele", ""),
                                                  bool(c.get("essai")), 2, garde_cle=True)
            if choix != "1":
                return None
        tunnel_avant = f4.port_tunnel(self.maison)
        while True:
            try:
                if self.parcours():
                    return True
            except f4.Refus as e:
                self.dire(e.args[0])
            if self.lieu == "voisin":  # le tunnel doit rester celui des réglages encore en place
                if tunnel_avant and f4.reglages_valides(f.id, self.maison):
                    f4.poser_tunnel(self.maison, tunnel_avant, self.executer)
                else:
                    f4.retirer_tunnel(self.maison, self.executer)
            self.dire("non_enregistre")
            if self.entree(self.t("reessai")).strip().lower() != "r":
                return False

    def parcours(self):
        f = self.f
        local = f.famille == "local"
        if local:
            self.etape(1, "e_adresse")
            brut = self.entree(self.t("adresse"))
            port = f4.port_voisin(brut)
            self.lieu = "reseau" if port is None else "voisin"
            if port is not None:
                self.dire("voisin_avant")
                f4.poser_tunnel(self.maison, port, self.executer)
            base = f4.nettoyer_url(f"http://127.0.0.1:{port}" if port else brut)
            if brut.strip().rstrip("/").endswith("/v1"):
                self.dire("adresse_v1")
            if f4.adresse_en_clair(base):
                self.dire("en_clair")
                if not self.oui():
                    return False
            self.etape(2, "e_cle")
            cle = self.secret(self.t("saisie_cle_vide")).strip()
            if not cle:
                self.dire("cle_fictive")
        else:
            base = f.base_url
            self.etape(1, "e_cle")
            if f.cle_url:
                self.dire("cle_ou", url=f.cle_url)
            cle = self.secret(self.t("saisie_cle")).strip()
            if not cle or not all("!" <= c <= "~" for c in cle):
                raise f4.Refus("cle_invalide")
            self.dire("cle_recue", n=len(cle))
            if f.verif_url:
                code, _ = f4.appeler(f.verif_url, cle, f4.DELAI_RESEAU, ouvrir=self.ouvrir)
                if code in (401, 403):
                    self.dire("cle_refusee", nom=f.nom)
                    return False
                if code == 0:
                    self.dire("injoignable")
                    return False
        cle_http = cle or f4.JETON_REMPLISSAGE
        self.etape(3 if local else 2, "e_modele")
        modele, essai = self.choisir_modele(base, cle_http)
        if modele is None:
            return False
        return self.tester_et_enregistrer(base, cle, modele, essai, 4 if local else 3)

    def choisir_modele(self, base, cle):
        modeles = liste_modeles(self.f, base, cle, self.ouvrir)
        if not modeles:
            brut = self.entree(self.t("saisie_modele")).strip()
            if not f4.ID_MODELE.match(brut):
                self.dire("modele_invalide")
                return None, False
            return brut, self.f.id == "openrouter" and "anthropic" not in brut
        if self.f.id == "openrouter":
            self.dire("liste_modeles", n=len(modeles))
        filtre, debut = "", 0
        while True:
            vue = [m for m in modeles if filtre in m[0].lower()]
            page = vue[debut:debut + PAGE]
            for n, (i, ctx, essai) in enumerate(page, 1):
                marque = self.t("essai") if essai else self.t("pris_en_charge") if self.f.id == "openrouter" else ""
                taille = f"{ctx // 1000} k" if ctx else ""
                self.ecrire(f"  {n:>2}  {i:<44} {taille:>8}  {marque}".rstrip())
            r = self.entree(self.t("choix_modele")).strip()
            if not r:
                r = "1"
            if r == "n":
                debut = debut + PAGE if debut + PAGE < len(vue) else 0
            elif r.isdigit() and page and 1 <= int(r) <= len(page):
                m = page[int(r) - 1]
                return m[0], m[2]
            elif f4.ID_MODELE.match(r) and ("/" in r or ":" in r or any(r == m[0] for m in modeles)):
                return r, self.f.id == "openrouter" and "anthropic" not in r
            else:
                filtre, debut = r.lower(), 0

    def tester_et_enregistrer(self, base, cle, modele, essai, n, garde_cle=False):
        f = self.f
        local = f.famille == "local"
        self.etape(n, "e_test")
        cle_http = cle or f4.JETON_REMPLISSAGE
        if local:
            ok, code = protocole_anthropic(base, cle_http, modele, self.ouvrir)
            if not ok:
                self.ecrire(f"  non {self.t('t_protocole')}")
                voisin = self.lieu == "voisin" and code == 0
                self.dire("voisin_ferme" if voisin else "injoignable" if code == 0
                          else "cle_refusee" if code in (401, 403) else "openai_seul", nom=f.nom)
                return False
            self.ecrire(f"  ok  {self.t('t_protocole')}")
        r = aller_retour(base, cle_http, modele, self.ouvrir)
        if r["raison"] in ("injoignable", "cle_refusee", "credit_epuise", "modele_refuse"):
            self.ecrire(f"  non {self.t('t_protocole')}")
            self.dire(r["raison"], nom=f.nom)
            return False
        if not local:
            self.ecrire(f"  ok  {self.t('t_protocole')}")
        self.ecrire(f"  {'ok ' if r['flux'] else 'non'} {self.t('t_flux')}")
        if not r["flux"]:
            self.dire("pas_flux")
            return False
        self.ecrire(f"  {'ok ' if r['outil'] else 'non'} {self.t('t_outil')}")
        if not r["outil"]:
            self.dire("pas_outil")
            return False
        self.etape(n + 1, "e_enreg")
        if cle and not garde_cle:
            f4.ecrire_cle(self.maison, f.id, cle)
        f4.generer_settings(self.maison, f.id, base, modele, cle=bool(cle), essai=essai, lieu=self.lieu)
        if local and self.lieu == "reseau":
            f4.retirer_tunnel(self.maison, self.executer)  # ancien voisin remplacé par une adresse
        f4.noter_test(self.maison, f.id, f4.nettoyer_url(base), modele)
        if n + 1 < 5:
            self.etape(5, "e_resultat")
        if local and f4.appeler(base + "/api/version", cle_http, f4.DELAI_SANTE, ouvrir=self.ouvrir)[0] == 200:
            self.dire("ollama_contexte")
        self.dire("pret_local" if local else "pret_tiers")
        self.dire("pret", nom=f.nom)
        return True


def main(argv=None, environ=None, entree=input):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    if len(argv) != 1 or argv[0] not in IDS:
        print("usage : assistant ID", file=sys.stderr)
        return 2
    maison = environ.get("HOME") or os.path.expanduser("~")
    a = Assistant(argv[0], maison, environ, entree=entree)
    try:
        ok = a.executer_tout()
    except (EOFError, KeyboardInterrupt):
        ok = False
    except f4.Refus as e:
        a.dire(e.args[0])
        ok = False
    except Exception:  # noqa: BLE001 (la carte attend un résultat, le terminal doit rester lisible)
        a.dire("erreur_inattendue")
        ok = False
    if ok is False:
        a.abandon()
    try:
        if ok is None:
            f4.effacer_connexion(maison)
        else:
            f4.noter_connexion(maison, "ok" if ok else "echec")
    except OSError:
        pass
    try:
        print()
        entree(a.t("entree_fermer"))
    except (EOFError, KeyboardInterrupt):
        pass
    return 1 if ok is False else 0


if __name__ == "__main__":
    sys.exit(main())
