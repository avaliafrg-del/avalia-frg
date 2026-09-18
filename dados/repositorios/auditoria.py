"""
dados/repositorios/auditoria.py
------------------------------------------------------------------
Registro de quem fez o quê.

Num sistema municipal com dados de criança, a LGPD trata a prestação de
contas como obrigação do controlador. Saber que uma nota mudou não
basta: é preciso saber quem mudou, quando, e de onde.

O que é registrado: entrada e saída, criação e remoção de conta, emissão
e revogação de ticket, limpeza de dados e falhas de login. O que NÃO é
registrado: navegação, consultas de leitura e conteúdo de prova — um log
que guarda tudo vira um rastreador, e passa a ser ele o risco.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.modelos import RegistroAuditoria

# Ações reconhecidas. A lista fechada evita que cada endpoint invente um
# nome diferente para a mesma coisa e o histórico fique impossível de ler.
ENTRAR = "entrar"
ENTRADA_NEGADA = "entrada_negada"
SAIR = "sair"
CRIAR_USUARIO = "criar_usuario"
REMOVER_USUARIO = "remover_usuario"
EMITIR_TICKET = "emitir_ticket"
REVOGAR_TICKET = "revogar_ticket"
LIMPAR_DADOS = "limpar_dados"
APAGAR_ALUNO = "apagar_aluno"


def registrar(
    sessao: Session,
    acao: str,
    usuario=None,
    detalhe: str = "",
    ip: str = "",
) -> RegistroAuditoria:
    """
    Grava uma linha no histórico.

    Guarda o NOME junto do id: se a conta for removida depois, um
    registro apontando para um id inexistente não responde nada.
    """
    registro = RegistroAuditoria(
        usuario_id=getattr(usuario, "id", None),
        usuario_nome=getattr(usuario, "nome", "") or "",
        papel=getattr(usuario, "papel", "") or "",
        acao=acao,
        detalhe=(detalhe or "")[:400],
        ip=(ip or "")[:45],
    )
    sessao.add(registro)
    sessao.commit()
    return registro


def listar(
    sessao: Session,
    limite: int = 200,
    acao: Optional[str] = None,
    usuario_id: Optional[int] = None,
) -> List[RegistroAuditoria]:
    """Do mais recente para o mais antigo."""
    consulta = select(RegistroAuditoria).order_by(RegistroAuditoria.criado_em.desc())

    if acao:
        consulta = consulta.where(RegistroAuditoria.acao == acao)
    if usuario_id is not None:
        consulta = consulta.where(RegistroAuditoria.usuario_id == usuario_id)

    return list(sessao.scalars(consulta.limit(min(limite, 1000))))
