"""
dados/repositorios/acesso.py
------------------------------------------------------------------
Usuarios, senhas e sessoes.
------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import seguranca
from dados.conexao import agora
from dados.erros import CredencialInvalida
from dados.modelos import Sessao, Usuario
from dados.repositorios.comum import contar

def ha_usuarios(sessao: Session) -> bool:
    """False no primeiro uso: a interface oferece criar a conta inicial."""
    return contar(sessao, Usuario) > 0


def criar_usuario(
    sessao: Session,
    email: str,
    nome: str,
    senha: str,
    papel: str = "membro",
    escola_id: Optional[int] = None,
) -> Usuario:
    """
    Cria uma conta.

    O padrao e MEMBRO, e nao admin: uma conta criada por engano nasce
    com o menor acesso possivel. Um membro sem escola vinculada nao
    enxerga escola nenhuma — falha fechada de proposito.
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValueError("Informe um email valido.")
    if not (nome or "").strip():
        raise ValueError("Informe o nome da pessoa.")
    if papel not in ("admin", "membro", "convidado"):
        raise ValueError(f"Papel desconhecido: {papel}.")

    if sessao.scalar(select(Usuario).where(Usuario.email == email)):
        raise ValueError("Ja existe uma conta com este email.")

    if papel == "membro" and escola_id is None:
        raise ValueError(
            "Conta de escola precisa estar vinculada a uma escola. "
            "Sem isso ela entra no sistema e nao ve nada."
        )
    if papel != "membro":
        # Admin ve tudo e convidado nao ve nada: em nenhum dos dois o
        # vinculo faz sentido, e deixa-lo preenchido so confundiria.
        escola_id = None

    usuario = Usuario(
        email=email,
        nome=nome.strip(),
        senha_hash=seguranca.criar_hash_senha(senha),
        papel=papel,
        escola_id=escola_id,
    )
    sessao.add(usuario)
    sessao.commit()
    return usuario


def listar_usuarios(sessao: Session):
    return list(sessao.scalars(select(Usuario).order_by(Usuario.nome)))


def obter_usuario(sessao: Session, usuario_id: int) -> Usuario:
    from dados.repositorios.comum import obter

    return obter(sessao, Usuario, usuario_id)


def remover_usuario(sessao: Session, usuario_id: int) -> str:
    """
    Remove a conta e derruba as sessoes dela.

    Deixar a sessao viva ate vencer seria remover so no papel: a pessoa
    continuaria dentro do sistema por ate doze horas.
    """
    from dados.repositorios.comum import obter

    usuario = obter(sessao, Usuario, usuario_id)
    nome = usuario.nome
    sessao.delete(usuario)   # cascade leva sessoes e tickets
    sessao.commit()
    return nome


def contar_admins(sessao: Session) -> int:
    from sqlalchemy import func

    return (
        sessao.scalar(
            select(func.count()).select_from(Usuario).where(Usuario.papel == "admin")
        )
        or 0
    )


def autenticar(sessao: Session, email: str, senha: str) -> Usuario:
    """
    Confere as credenciais.

    A mesma mensagem serve para email inexistente e senha errada: dizer
    qual dos dois falhou entrega a lista de emails cadastrados a quem
    esta tentando adivinhar.
    """
    usuario = sessao.scalar(
        select(Usuario).where(Usuario.email == (email or "").strip().lower())
    )
    if usuario is None or not seguranca.conferir_senha(senha, usuario.senha_hash):
        raise CredencialInvalida("Email ou senha incorretos.")

    usuario.ultimo_acesso = agora()
    sessao.commit()
    return usuario


def abrir_sessao(sessao: Session, usuario: Usuario) -> str:
    """Cria a sessao e devolve o token que vai no cookie."""
    token = seguranca.novo_token()
    sessao.add(
        Sessao(
            usuario_id=usuario.id,
            token_hash=seguranca.hash_token(token),
            expira_em=agora() + timedelta(hours=seguranca.HORAS_SESSAO),
        )
    )
    sessao.commit()
    return token


def usuario_da_sessao(sessao: Session, token: Optional[str]) -> Optional[Usuario]:
    """Devolve o usuario do token, ou None se ausente, invalido ou vencido."""
    if not token:
        return None

    registro = sessao.scalar(
        select(Sessao).where(Sessao.token_hash == seguranca.hash_token(token))
    )
    if registro is None:
        return None

    # SQLite devolve datetime sem fuso; normaliza antes de comparar.
    expira = registro.expira_em
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=timezone.utc)

    if expira < agora():
        sessao.delete(registro)
        sessao.commit()
        return None

    return registro.usuario


def fechar_sessao(sessao: Session, token: Optional[str]) -> None:
    if not token:
        return
    registro = sessao.scalar(
        select(Sessao).where(Sessao.token_hash == seguranca.hash_token(token))
    )
    if registro is not None:
        sessao.delete(registro)
        sessao.commit()


def limpar_sessoes_vencidas(sessao: Session) -> int:
    """Faxina das sessoes expiradas, chamada no startup."""
    vencidas = list(sessao.scalars(select(Sessao).where(Sessao.expira_em < agora())))
    for registro in vencidas:
        sessao.delete(registro)
    if vencidas:
        sessao.commit()
    return len(vencidas)
