"""
dados/repositorios/turmas.py
------------------------------------------------------------------
Turmas e a associacao com o ano escolar.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import escolaridade
from dados.modelos import Escola, Turma
from dados.repositorios.comum import obter as _obter

def criar_turma(
    sessao: Session,
    escola_id: int,
    nome: str,
    ano_escolar: Optional[str] = None,
    ano_letivo: Optional[int] = None,
) -> Turma:
    _obter(sessao, Escola, escola_id)
    turma = Turma(
        escola_id=escola_id,
        nome=nome.strip(),
        ano_escolar=escolaridade.normalizar(ano_escolar),
        ano_letivo=ano_letivo,
    )
    sessao.add(turma)
    sessao.commit()
    return turma


def listar_turmas(sessao: Session, escola_id: Optional[int] = None) -> List[Turma]:
    consulta = select(Turma).order_by(Turma.nome)
    if escola_id is not None:
        consulta = consulta.where(Turma.escola_id == escola_id)
    return list(sessao.scalars(consulta))


def obter_turma(sessao: Session, turma_id: int) -> Turma:
    """Busca uma turma por id, ou levanta RegistroNaoEncontrado."""
    return _obter(sessao, Turma, turma_id)
