"""
dados/erros.py
------------------------------------------------------------------
Exceções da camada de dados.

Ficam num módulo próprio porque são o contrato entre o banco e quem o
usa: a camada HTTP traduz cada uma em um código de resposta, e para
isso precisa importá-las sem arrastar junto os modelos e a conexão.
------------------------------------------------------------------
"""

from __future__ import annotations


class RegistroNaoEncontrado(Exception):
    """Pedido de um id que não existe. Vira 404 na API."""


class CredencialInvalida(Exception):
    """Email ou senha não conferem. Vira 401."""


class TicketInvalido(Exception):
    """Ticket inexistente, revogado, vencido ou de outra pessoa. Vira 403."""
