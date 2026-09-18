"""
dados/migracoes.py
------------------------------------------------------------------
Faz o banco existente acompanhar o código, sem apagar nada.

Até aqui, toda versão nova vinha com a instrução "apague o
avaliafrg.db". Isso funciona enquanto o sistema está sendo montado e
deixa de funcionar no minuto em que há nota de aluno gravada — e agora
há. Perder o bimestre de uma escola porque o sistema ganhou uma coluna
nova seria um defeito grave, não um inconveniente.

Como funciona
-------------
`sincronizar()` roda no startup e faz três coisas, nesta ordem:

1. Cria as tabelas que faltam (`create_all`).
2. Acrescenta as COLUNAS que faltam, comparando o banco real com os
   modelos. Cobre o caso mais comum de evolução de schema, que é
   acrescentar campo.
3. Aplica as migrações manuais pendentes, para o que o passo 2 não
   resolve sozinho — renomear, converter tipo, preencher dado novo a
   partir do antigo.

O passo 2 é o que dispensa escrever migração para cada campo novo. O
passo 3 existe porque nem toda mudança é aditiva, e fingir que é seria
trocar perda de dados por dado silenciosamente errado.

Limites conscientes
-------------------
Colunas novas precisam ser NULL ou ter valor padrão — não dá para
acrescentar coluna obrigatória numa tabela que já tem linhas sem saber
o que pôr nelas. REMOVER coluna não é feito automaticamente: some do
modelo, mas continua no banco. É de propósito; apagar dado precisa ser
decisão explícita, escrita como migração.
------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

TABELA_VERSAO = "versao_schema"

# Versão que este código espera. Suba de um a cada migração nova.
VERSAO_ATUAL = 1


@dataclass(frozen=True)
class Migracao:
    """Uma mudança que o passo automático não consegue fazer sozinho."""

    versao: int
    descricao: str
    aplicar: Callable[[Connection], None]


# Migrações manuais, em ordem. A versão 1 é a linha de base: bancos
# criados antes deste módulo são marcados como 1 sem nada a aplicar,
# porque o ajuste de colunas já os deixa em dia.
MIGRACOES: List[Migracao] = []


# ==================================================================
# CONTROLE DE VERSAO
# ==================================================================
def _garantir_tabela_versao(conexao: Connection) -> None:
    conexao.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {TABELA_VERSAO} "
            f"(versao INTEGER NOT NULL)"
        )
    )


def ler_versao(engine: Engine) -> int:
    """Versão do schema gravada no banco. 0 = banco novo."""
    with engine.begin() as conexao:
        _garantir_tabela_versao(conexao)
        linha = conexao.execute(text(f"SELECT versao FROM {TABELA_VERSAO}")).first()
        return int(linha[0]) if linha else 0


def gravar_versao(engine: Engine, versao: int) -> None:
    with engine.begin() as conexao:
        _garantir_tabela_versao(conexao)
        conexao.execute(text(f"DELETE FROM {TABELA_VERSAO}"))
        conexao.execute(
            text(f"INSERT INTO {TABELA_VERSAO} (versao) VALUES (:v)"), {"v": versao}
        )


# ==================================================================
# AJUSTE AUTOMATICO DE COLUNAS
# ==================================================================
def _tipo_sql(coluna, dialeto) -> str:
    return coluna.type.compile(dialect=dialeto)


def acrescentar_colunas_faltantes(engine: Engine, metadata) -> List[str]:
    """
    Compara o banco com os modelos e acrescenta o que falta.

    Returns:
        Lista de "tabela.coluna" acrescentados, para registrar no log.
    """
    inspetor = inspect(engine)
    existentes = set(inspetor.get_table_names())
    acrescentadas: List[str] = []

    for nome_tabela, tabela in metadata.tables.items():
        if nome_tabela not in existentes:
            continue   # create_all cuida das tabelas novas

        colunas_no_banco = {c["name"] for c in inspetor.get_columns(nome_tabela)}

        for coluna in tabela.columns:
            if coluna.name in colunas_no_banco:
                continue

            if not coluna.nullable and coluna.default is None:
                # Não há valor razoável para as linhas que já existem.
                # Avisar é melhor que inventar um padrão silencioso.
                logger.error(
                    "Coluna %s.%s é obrigatória e não tem padrão: precisa de "
                    "uma migração manual.",
                    nome_tabela,
                    coluna.name,
                )
                continue

            tipo = _tipo_sql(coluna, engine.dialect)
            with engine.begin() as conexao:
                conexao.execute(
                    text(f'ALTER TABLE {nome_tabela} ADD COLUMN "{coluna.name}" {tipo}')
                )
            acrescentadas.append(f"{nome_tabela}.{coluna.name}")

    return acrescentadas


# ==================================================================
# ENTRADA
# ==================================================================
def sincronizar(engine: Engine, metadata) -> dict:
    """
    Põe o banco em dia com o código. Chamado no startup.

    Returns:
        Resumo do que foi feito, para o log e para o health check.
    """
    banco_novo = not inspect(engine).get_table_names()

    # 1. Tabelas que faltam
    metadata.create_all(engine)

    # 2. Colunas que faltam
    colunas = acrescentar_colunas_faltantes(engine, metadata)
    if colunas:
        logger.warning(
            "Schema atualizado sem perda de dados. Colunas acrescentadas: %s",
            ", ".join(colunas),
        )

    # 3. Migrações manuais pendentes
    versao = VERSAO_ATUAL if banco_novo else ler_versao(engine)
    aplicadas: List[str] = []

    for migracao in sorted(MIGRACOES, key=lambda m: m.versao):
        if migracao.versao <= versao:
            continue
        logger.warning("Aplicando migração %s: %s", migracao.versao, migracao.descricao)
        with engine.begin() as conexao:
            migracao.aplicar(conexao)
        versao = migracao.versao
        aplicadas.append(migracao.descricao)

    # `max` protege o caso de um banco mais novo que o código: um
    # rollback de versão não pode rebaixar o carimbo e fazer migrações
    # já aplicadas rodarem de novo.
    gravar_versao(engine, max(versao, VERSAO_ATUAL))

    return {
        "banco_novo": banco_novo,
        "versao": ler_versao(engine),
        "colunas_acrescentadas": colunas,
        "migracoes_aplicadas": aplicadas,
    }
