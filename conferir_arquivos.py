"""
conferir_arquivos.py
------------------------------------------------------------------
Diz exatamente quais arquivos faltam na pasta, e onde cada um vai.

Rode ANTES do `python main.py`:

    python conferir_arquivos.py

Existe porque o erro que o Python dá quando falta um módulo
(`ModuleNotFoundError: No module named 'seguranca'`) mostra apenas o
PRIMEIRO que faltou. Corrigido esse, aparece o próximo, um de cada
vez. Aqui a lista sai inteira de uma vez.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
import sys

# Onde cada arquivo precisa estar. A chave é a pasta.
ESTRUTURA = {
    "": [
        "main.py",
        "omr_engine.py",
        "gerador.py",
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
        "schemas.py",
        "diagnostico.py",
        "requirements.txt",
    ],
    "dados": [
        "__init__.py",
        "conexao.py",
        "modelos.py",
        "migracoes.py",
        "erros.py",
    ],
    # `repositorios` não tem __init__.py de propósito: o Python 3 trata
    # a pasta como pacote de mesmo assim, e assim não existem dois
    # arquivos de mesmo nome para o navegador embaralhar no download.
    os.path.join("dados", "repositorios"): [
        "comum.py",
        "escolas.py",
        "turmas.py",
        "alunos.py",
        "provas.py",
        "resultados.py",
        "historico.py",
        "acesso.py",
        "tickets.py",
        "configuracao.py",
        "manutencao.py",
    ],
    "static": [
        "index.html",
        "ajuda.js",
        "logo-avalia.png",
        "logo-marca.png",
        "logo-institucional.png",
    ],
    "tests": ["test_omr.py"],
}

VERDE, VERMELHO, AMARELO, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
if os.name == "nt" and not os.getenv("WT_SESSION"):
    VERDE = VERMELHO = AMARELO = FIM = ""   # console antigo do Windows


def main() -> int:
    print(f"\nPasta: {os.getcwd()}\n")

    faltando: list[str] = []
    fora_do_lugar: list[tuple[str, str]] = []

    for pasta, arquivos in ESTRUTURA.items():
        titulo = pasta or "(raiz)"
        print(f"--- {titulo} ---")

        for arquivo in arquivos:
            caminho = os.path.join(pasta, arquivo) if pasta else arquivo

            if os.path.exists(caminho):
                print(f"  {VERDE}OK{FIM}    {arquivo}")
                continue

            # O arquivo pode ter sido baixado, só que na pasta errada.
            solto = arquivo if pasta else None
            if solto and os.path.exists(solto):
                print(f"  {AMARELO}MOVER{FIM} {arquivo}  (está solto na raiz)")
                fora_do_lugar.append((solto, pasta))
            else:
                print(f"  {VERMELHO}FALTA{FIM} {arquivo}")
                faltando.append(caminho)
        print()

    # O __init__.py de dados/ é o que expõe a fachada. Se vier o
    # arquivo errado, o erro só apareceria na primeira requisição.
    principal = os.path.join("dados", "__init__.py")
    if os.path.exists(principal):
        with open(principal, encoding="utf-8") as fp:
            if "criar_escola" not in fp.read():
                print(
                    f"{VERMELHO}ATENÇÃO{FIM}: dados/__init__.py não parece ser o "
                    f"arquivo certo.\n  Ele deve conter a lista de funções "
                    f"exportadas (criar_escola, autenticar…).\n"
                )

    print("=" * 60)
    if fora_do_lugar:
        print("\nMova estes arquivos (PowerShell):\n")
        for arquivo, destino in fora_do_lugar:
            print(f"    Move-Item {arquivo} {destino}\\ -Force")

    if faltando:
        print(f"\n{len(faltando)} arquivo(s) faltando. Baixe e coloque em:\n")
        for caminho in faltando:
            print(f"    {caminho}")
        print("\nDepois rode este script de novo.")
        return 1

    if not fora_do_lugar:
        print(f"\n{VERDE}Está tudo no lugar.{FIM} Agora rode:\n")
        print("    pip install -r requirements.txt")
        print("    python main.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
