"""
dados/repositorios/escolas.py
------------------------------------------------------------------
Cadastro de escolas.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.modelos import Escola

def criar_escola(sessao: Session, nome: str) -> Escola:
    nome = nome.strip()
    existente = sessao.scalar(select(Escola).where(Escola.nome == nome))
    if existente:
        return existente
    escola = Escola(nome=nome)
    sessao.add(escola)
    sessao.commit()
    return escola


def listar_escolas(sessao: Session) -> List[Escola]:
    return list(sessao.scalars(select(Escola).order_by(Escola.nome)))
