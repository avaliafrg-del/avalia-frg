"""
dados/conexao.py
------------------------------------------------------------------
Motor do banco, fábrica de sessões e criação do schema.

É o único módulo que sabe COMO o banco é alcançado. Modelos e
repositórios trabalham com uma `Session` que chega pronta, e por isso
não precisam saber se por trás há SQLite ou Postgres.

Escolha do banco
----------------
A conexão vem de DATABASE_URL. O padrão é SQLite num arquivo local,
que resolve o desenvolvimento e o uso numa escola só.

ATENÇÃO NO CLOUD RUN: o disco da instância é efêmero e some a cada
reinício ou escala para zero. SQLite ali PERDE OS DADOS. Em produção
aponte para um Postgres (Cloud SQL):

    postgresql+psycopg://usuario:senha@host:5432/avaliafrg

Nada mais no código muda: o SQLAlchemy cuida da diferença.
------------------------------------------------------------------
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ARQUIVO_PADRAO = "avaliafrg.db"
ARQUIVO_ANTIGO = "provafacil.db"


def _url_padrao() -> str:
    """
    Escolhe o arquivo do banco.

    A plataforma passou a se chamar Avalia FRG, e o arquivo acompanhou.
    Mas trocar o nome sem olhar para o disco faria o sistema ignorar um
    banco que já existe e começar VAZIO em silêncio — perdendo turmas e
    notas sem avisar ninguém. Por isso, se o arquivo antigo estiver na
    pasta, ele continua sendo usado.
    """
    if os.path.exists(ARQUIVO_ANTIGO) and not os.path.exists(ARQUIVO_PADRAO):
        return f"sqlite:///./{ARQUIVO_ANTIGO}"
    return f"sqlite:///./{ARQUIVO_PADRAO}"


DATABASE_URL = os.getenv("DATABASE_URL") or _url_padrao()

# check_same_thread é exclusivo do SQLite: o FastAPI atende requisições
# em threads diferentes das que abriram a conexão.
_argumentos = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_argumentos, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def agora() -> datetime:
    """Momento atual em UTC. Centralizado para o banco ter um só relógio."""
    return datetime.now(timezone.utc)


def criar_tabelas() -> dict:
    """
    Põe o banco em dia com o código. Chamado no startup da API.

    Não é só `create_all`: um banco que já existe também recebe as
    colunas novas e as migrações pendentes. Antes disso, cada versão do
    sistema exigia apagar o arquivo — aceitável enquanto não havia nota
    de aluno dentro, inaceitável agora que há.

    Returns:
        Resumo do que foi feito, para o log e o health check.
    """
    from dados import migracoes
    from dados.modelos import Base

    return migracoes.sincronizar(engine, Base.metadata)


def obter_sessao():
    """Dependência do FastAPI: uma sessão por requisição."""
    sessao = SessionLocal()
    try:
        yield sessao
    finally:
        sessao.close()
