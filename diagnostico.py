"""
diagnostico.py
------------------------------------------------------------------
Descobre por que a tela esta dando erro.

Rode a partir da pasta do projeto, com o servidor LIGADO em outro
terminal:

    python diagnostico.py

Ele confere, nesta ordem:
    1. quais arquivos estao na pasta
    2. se o servidor responde e qual versao ele e
    3. se a pagina servida e a mesma que esta na pasta
    4. se as rotas que a interface usa existem
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORTA = os.getenv("PORT", "8080")
BASE = f"http://localhost:{PORTA}"

ARQUIVOS_ESPERADOS = [
    "main.py",
    "omr_engine.py",
    "gerador.py",
    os.path.join("dados", "__init__.py"),
    os.path.join("dados", "modelos.py"),
    "schemas.py",
    "seguranca.py",
    "analitico.py",
    "escolaridade.py",
    "estampa.py",
    "deteccao_grade.py",
    "lote.py",
    "comparativo.py",
    "permissoes.py",
    "relatorio.py",
    "recuperar_admin.py",
    os.path.join("static", "index.html"),
]

# Rotas que a interface atual chama. Se alguma faltar, o navegador
# mostra "Falha na comunicacao com o servidor (codigo 404)".
# Rotas abertas, que respondem mesmo sem login. As rotas de dados
# devolvem 401 sem sessao — checar por elas daria falso negativo.
ROTAS = [
    ("GET", "/api/health"),
    ("GET", "/api/auth/estado"),
]

VERDE, VERMELHO, AMARELO, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
if os.name == "nt" and not os.getenv("WT_SESSION"):
    VERDE = VERMELHO = AMARELO = FIM = ""   # console antigo do Windows

problemas: list[str] = []


def ok(msg: str) -> None:
    print(f"  {VERDE}OK{FIM}    {msg}")


def falha(msg: str, correcao: str) -> None:
    print(f"  {VERMELHO}FALHA{FIM} {msg}")
    problemas.append(correcao)


def aviso(msg: str) -> None:
    print(f"  {AMARELO}!{FIM}     {msg}")


def buscar(caminho: str):
    """Devolve (status, corpo) ou (None, erro)."""
    try:
        with urllib.request.urlopen(BASE + caminho, timeout=5) as resposta:
            return resposta.status, resposta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# ==================================================================
print("\n=== 1. Arquivos nesta pasta ===")
print(f"  pasta atual: {os.getcwd()}\n")

faltando = [a for a in ARQUIVOS_ESPERADOS if not os.path.exists(a)]
for arquivo in ARQUIVOS_ESPERADOS:
    (ok if os.path.exists(arquivo) else lambda m: falha(m, ""))(arquivo)

caminho_static = os.path.join("static", "index.html")

if caminho_static in faltando and os.path.exists("index.html"):
    # Caso mais comum: os arquivos foram baixados um a um e cairam todos
    # soltos na mesma pasta, sem a subpasta "static".
    faltando.remove(caminho_static)
    aviso("index.html esta solto na pasta, e deveria estar em static/")
    problemas.append(
        "Mova o index.html para dentro da subpasta 'static'. No PowerShell:\n"
        "       mkdir static\n"
        "       move index.html static\\"
    )

if faltando:
    problemas.append(
        "Faltam arquivos nesta pasta: "
        + ", ".join(faltando)
        + ". Baixe os arquivos que faltam e coloque nesta pasta."
    )

if os.path.exists("banco.py"):
    aviso("banco.py existe e nao e usado — versao duplicada do database.py. Pode apagar.")

# ==================================================================
print("\n=== 2. Bibliotecas instaladas ===")

for modulo, pacote in [
    ("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"), ("cv2", "opencv-python-headless"),
    ("pymupdf", "pymupdf"),
    ("PIL", "pillow"), ("qrcode", "qrcode"), ("pypdf", "pypdf"), ("sqlalchemy", "SQLAlchemy"),
]:
    try:
        __import__(modulo)
        ok(pacote)
    except ImportError:
        falha(f"{pacote} nao instalado", "")
        problemas.append(
            "Faltam bibliotecas. Rode nesta pasta:  pip install -r requirements.txt"
        )

# ==================================================================
print("\n=== 3. O servidor esta no ar? ===")
status, corpo = buscar("/api/health")

if status is None:
    falha(f"nao respondeu em {BASE}  ({corpo})", "")
    problemas.append(
        f"O servidor nao esta rodando em {BASE}. Abra outro terminal, va ate "
        f"esta pasta e rode: python main.py"
    )
    print("\n" + "=" * 62)
    for numero, item in enumerate(problemas, 1):
        if item:
            print(f"{numero}. {item}")
    sys.exit(1)

if status == 404:
    falha("/api/health respondeu 404", "")
    if corpo.strip().lower().startswith(("<!doctype", "<html")):
        # Servidor de arquivos estaticos (Live Server do VS Code,
        # http.server) devolve HTML numa rota inexistente. O servidor do
        # sistema devolveria JSON. E essa a assinatura do erro "Falha na
        # comunicacao com o servidor (codigo 404)" na tela.
        problemas.append(
            f"O que esta atendendo em {BASE} NAO e o servidor do sistema — e um "
            f"servidor de arquivos, provavelmente o Live Server do VS Code. "
            f"Feche ele (clique em 'Port: 5500' no rodape do VS Code) e rode "
            f"'python main.py' nesta pasta."
        )
    else:
        problemas.append(
            "O servidor no ar e de uma versao ANTIGA (nao tem /api/health). "
            "Pare esse servidor (Ctrl+C no terminal dele) e rode 'python main.py' "
            "de dentro da pasta nova."
        )
else:
    try:
        saude = json.loads(corpo)
        versao = saude.get("versao", "?")
        ok(f"respondeu — versao {versao}, banco {saude.get('banco', '?')}")

        if versao.split(".")[0] != "10":
            falha(f"a versao esperada e 10.x, e a que respondeu e {versao}", "")
            problemas.append(
                f"O servidor no ar e a versao {versao}. Pare o processo antigo "
                f"e inicie 'python main.py' da pasta nova."
            )
        if "maximo_questoes" not in saude:
            aviso("resposta sem 'maximo_questoes' — versao intermediaria")
    except json.JSONDecodeError:
        falha("/api/health nao devolveu JSON", "")

# ==================================================================
print("\n=== 4. A pagina servida e a desta pasta? ===")
status, html = buscar("/")

if status != 200:
    falha(f"a pagina inicial respondeu {status}", "")
else:
    abas_novas = ['data-painel="turmas"', 'data-painel="boletim"']
    if all(marca in html for marca in abas_novas):
        ok("a pagina servida tem as 4 abas (Turmas, Provas, Corrigir, Boletim)")
    else:
        falha("a pagina servida NAO tem as abas Turmas/Boletim", "")
        problemas.append(
            "O servidor esta entregando um index.html antigo. Confirme que o "
            "arquivo static/index.html desta pasta e o novo e reinicie o servidor."
        )

    caminho_local = caminho_static if os.path.exists(caminho_static) else "index.html"
    if os.path.exists(caminho_local):
        with open(caminho_local, encoding="utf-8") as arquivo:
            local = arquivo.read()
        if local.strip() == html.strip():
            ok("o arquivo do disco e o que o servidor entrega sao iguais")
        else:
            falha("o servidor entrega um HTML DIFERENTE do arquivo do disco", "")
            problemas.append(
                "O servidor esta rodando de OUTRA pasta. Feche todos os "
                "terminais com servidor aberto e rode 'python main.py' aqui."
            )

# ==================================================================
print("\n=== 5. Rotas que a interface usa ===")
for metodo, rota in ROTAS:
    status, _ = buscar(rota)
    if status == 200:
        ok(f"{metodo} {rota}")
    else:
        falha(f"{metodo} {rota} respondeu {status}", "")
        problemas.append(f"A rota {rota} nao existe no servidor no ar (versao antiga).")

# ==================================================================
print("\n" + "=" * 62)
if problemas:
    print("O QUE FAZER:\n")
    vistos = set()
    numero = 1
    for item in problemas:
        if item and item not in vistos:
            vistos.add(item)
            print(f"{numero}. {item}\n")
            numero += 1
    print("Depois de corrigir, recarregue o navegador com Ctrl+F5 (limpa o cache).")
else:
    print(f"{VERDE}Tudo certo.{FIM} Servidor e interface estao na mesma versao.")
    print("Se a tela ainda der erro, recarregue com Ctrl+F5 e me mande o que")
    print("aparece no TERMINAL do servidor no momento do clique.")
print()
