"""
dados/repositorios/tickets.py
------------------------------------------------------------------
Tickets de acesso: emissao, validacao e revogacao.
------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

import seguranca
from dados.conexao import agora
from dados.erros import TicketInvalido
from dados.modelos import Sessao, Ticket, Usuario
from dados.repositorios.comum import obter as _obter

def criar_ticket(
    sessao: Session,
    descricao: str = "",
    criado_por_id: Optional[int] = None,
    dias_validade: Optional[int] = None,
) -> Tuple[Ticket, str]:
    """
    Emite um ticket novo.

    Returns:
        (registro, codigo). O codigo completo so existe aqui — depois
        disso o banco tem apenas o hash, e nao ha como recupera-lo.
    """
    codigo = seguranca.gerar_ticket()
    ticket = Ticket(
        codigo_hash=seguranca.hash_ticket(codigo),
        pista=seguranca.pista_do_ticket(codigo),
        descricao=(descricao or "").strip(),
        criado_por_id=criado_por_id,
        expira_em=agora() + timedelta(days=dias_validade) if dias_validade else None,
    )
    sessao.add(ticket)
    sessao.commit()
    return ticket, codigo


def listar_tickets(sessao: Session) -> List[Ticket]:
    return list(sessao.scalars(select(Ticket).order_by(Ticket.criado_em.desc())))


def revogar_ticket(sessao: Session, ticket_id: int) -> Ticket:
    """
    Corta o acesso na hora.

    As sessoes abertas com este ticket tambem caem: manter alguem
    dentro do sistema depois de revogar o acesso seria revogar so no
    papel.
    """
    ticket = _obter(sessao, Ticket, ticket_id)
    ticket.revogado = True

    if ticket.usuario_id is not None:
        for aberta in sessao.scalars(
            select(Sessao).where(Sessao.usuario_id == ticket.usuario_id)
        ):
            sessao.delete(aberta)

    sessao.commit()
    return ticket


def validar_ticket(
    sessao: Session, codigo: str, usuario: Optional["Usuario"] = None
) -> Ticket:
    """
    Confere o ticket e, quando ha usuario, o vinculo com a conta.

    Aqui vale detalhar o motivo: quem digita o ticket ja passou pela
    senha, e "vencido" contra "revogado" muda o que a pessoa precisa
    fazer em seguida.
    """
    if not (codigo or "").strip():
        raise TicketInvalido("Informe o ticket de acesso.")

    ticket = sessao.scalar(
        select(Ticket).where(Ticket.codigo_hash == seguranca.hash_ticket(codigo))
    )
    if ticket is None:
        raise TicketInvalido("Ticket de acesso nao encontrado.")

    situacao = ticket.situacao()
    if situacao == "revogado":
        raise TicketInvalido("Este ticket foi revogado por quem administra.")
    if situacao == "vencido":
        raise TicketInvalido("Este ticket venceu. Peca um novo a quem administra.")

    if (
        usuario is not None
        and ticket.usuario_id is not None
        and ticket.usuario_id != usuario.id
    ):
        raise TicketInvalido("Este ticket ja esta em uso por outra conta.")

    return ticket


def usar_ticket(sessao: Session, ticket: Ticket, usuario: "Usuario") -> None:
    """Prende o ticket a conta no primeiro uso e registra o acesso."""
    if ticket.usuario_id is None:
        ticket.usuario_id = usuario.id
    ticket.ultimo_uso = agora()
    sessao.commit()
