"""
dados/repositorios/comum.py
------------------------------------------------------------------
O que todos os repositórios usam.

`obter` existe para que "id que não existe" produza sempre a mesma
exceção, com a mesma mensagem. Cada repositório reimplementando essa
busca acabaria com quatro mensagens diferentes para o mesmo erro.
------------------------------------------------------------------
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from dados.erros import RegistroNaoEncontrado


def obter(sessao: Session, modelo, id_: int):
    """Busca por id ou levanta RegistroNaoEncontrado."""
    objeto = sessao.get(modelo, id_)
    if objeto is None:
        raise RegistroNaoEncontrado(
            f"{modelo.__name__} de id {id_} nao foi encontrado."
        )
    return objeto


def contar(sessao: Session, modelo) -> int:
    from sqlalchemy import func, select

    return sessao.scalar(select(func.count()).select_from(modelo)) or 0
