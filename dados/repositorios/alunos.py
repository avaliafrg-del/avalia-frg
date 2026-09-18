"""
dados/repositorios/alunos.py
------------------------------------------------------------------
Cadastro de alunos de uma turma.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.modelos import Aluno, Turma
from dados.repositorios.comum import obter as _obter

def adicionar_alunos(sessao: Session, turma_id: int, nomes: Iterable[str]) -> List[Aluno]:
    """
    Cadastra varios alunos de uma vez (a lista da turma vem colada de
    uma planilha, um nome por linha). Nomes repetidos na mesma turma
    sao ignorados para o professor poder colar de novo sem duplicar.
    """
    _obter(sessao, Turma, turma_id)
    existentes = {
        aluno.nome.strip().lower()
        for aluno in sessao.scalars(select(Aluno).where(Aluno.turma_id == turma_id))
    }

    novos: List[Aluno] = []
    for nome in nomes:
        nome = " ".join(nome.split())
        if not nome or nome.lower() in existentes:
            continue
        existentes.add(nome.lower())
        aluno = Aluno(turma_id=turma_id, nome=nome)
        sessao.add(aluno)
        novos.append(aluno)

    sessao.commit()
    return novos


def listar_alunos(sessao: Session, turma_id: int) -> List[Aluno]:
    return list(
        sessao.scalars(
            select(Aluno).where(Aluno.turma_id == turma_id).order_by(Aluno.nome)
        )
    )


def remover_aluno(sessao: Session, aluno_id: int) -> None:
    sessao.delete(_obter(sessao, Aluno, aluno_id))
    sessao.commit()


def obter_aluno(sessao: Session, aluno_id: int) -> Aluno:
    return _obter(sessao, Aluno, aluno_id)
