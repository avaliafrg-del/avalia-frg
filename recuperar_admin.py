"""
recuperar_admin.py
------------------------------------------------------------------
Emite um ticket novo de administrador quando o antigo se perdeu.

O ticket aparece uma única vez, no momento em que é criado — depois o
banco guarda apenas o hash. Isso é proposital: se o banco vazar, os
códigos guardados não servem para entrar. Mas cria um risco óbvio, e
sem uma saída o administrador ficaria trancado do lado de fora do
próprio sistema.

A saída é esta, e a proteção dela é o acesso ao computador: quem roda
este script já está na máquina onde o sistema está instalado, com
acesso ao arquivo do banco. Quem tem isso já poderia fazer o que
quisesse com os dados de qualquer forma.

Uso, na pasta do sistema:

    python recuperar_admin.py                 mostra as contas e escolhe
    python recuperar_admin.py --email a@b.br  direto para uma conta
    python recuperar_admin.py --promover a@b.br  torna a conta admin

Toda emissão fica registrada no log de auditoria.
------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import os
import sys

# Roda a partir da pasta do projeto, como o professor faria.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    argumentos = argparse.ArgumentParser(
        description="Emite um ticket novo de administrador."
    )
    argumentos.add_argument("--email", help="Email da conta que vai receber o ticket.")
    argumentos.add_argument(
        "--promover",
        metavar="EMAIL",
        help="Torna a conta administradora antes de emitir o ticket.",
    )
    argumentos.add_argument(
        "--dias",
        type=int,
        default=None,
        help="Validade em dias. Sem isso, o ticket não expira.",
    )
    opcoes = argumentos.parse_args()

    try:
        import dados as db
    except ImportError:
        print(
            "Não encontrei o sistema nesta pasta.\n"
            "Rode este script de dentro da pasta onde está o main.py."
        )
        return 1

    db.criar_tabelas()

    with db.SessionLocal() as sessao:
        contas = list(sessao.scalars(db.select(db.Usuario).order_by(db.Usuario.id)))

        if not contas:
            print(
                "Ainda não existe nenhuma conta.\n"
                "Abra o sistema no navegador: a primeira tela cria a conta de\n"
                "administrador e já mostra o ticket."
            )
            return 1

        # ---- promover, se pedido -------------------------------
        if opcoes.promover:
            alvo = sessao.scalar(
                db.select(db.Usuario).where(
                    db.Usuario.email == opcoes.promover.strip().lower()
                )
            )
            if alvo is None:
                print(f"Não achei a conta {opcoes.promover}.")
                return 1
            alvo.papel = "admin"
            alvo.escola_id = None   # admin enxerga tudo; vínculo perde sentido
            sessao.commit()
            print(f"{alvo.nome} agora é administrador.")
            opcoes.email = alvo.email

        # ---- escolher a conta ----------------------------------
        if opcoes.email:
            usuario = sessao.scalar(
                db.select(db.Usuario).where(
                    db.Usuario.email == opcoes.email.strip().lower()
                )
            )
            if usuario is None:
                print(f"Não achei a conta {opcoes.email}.")
                return 1
        else:
            administradores = [c for c in contas if c.papel == "admin"]
            if not administradores:
                print(
                    "Nenhuma conta é administradora.\n"
                    "Use: python recuperar_admin.py --promover EMAIL"
                )
                print("\nContas existentes:")
                for conta in contas:
                    print(f"   {conta.email}  ({conta.papel})")
                return 1

            if len(administradores) == 1:
                usuario = administradores[0]
            else:
                print("Contas de administrador:\n")
                for indice, conta in enumerate(administradores, start=1):
                    print(f"  {indice}. {conta.nome} — {conta.email}")
                escolha = input("\nPara qual delas emitir o ticket? ").strip()
                if not escolha.isdigit() or not (
                    1 <= int(escolha) <= len(administradores)
                ):
                    print("Escolha inválida.")
                    return 1
                usuario = administradores[int(escolha) - 1]

        if usuario.papel != "admin":
            print(
                f"{usuario.nome} não é administrador.\n"
                f"Use: python recuperar_admin.py --promover {usuario.email}"
            )
            return 1

        # ---- emitir --------------------------------------------
        ticket, codigo = db.criar_ticket(
            sessao,
            descricao=f"Ticket de recuperação — {usuario.nome}",
            criado_por_id=usuario.id,
            dias_validade=opcoes.dias,
        )
        # O ticket já nasce preso à conta: um código de recuperação
        # solto seria um acesso livre esperando ser usado por outra
        # pessoa.
        db.usar_ticket(sessao, ticket, usuario)

        try:
            from dados.repositorios import auditoria

            auditoria.registrar(
                sessao,
                usuario_id=usuario.id,
                acao="ticket_recuperado",
                alvo=f"ticket {ticket.pista}",
                detalhe="emitido pelo recuperar_admin.py, no computador do sistema",
            )
        except Exception:  # noqa: BLE001 - auditoria não pode travar a recuperação
            pass

        print("\n" + "=" * 54)
        print(f"  Conta:  {usuario.nome} <{usuario.email}>")
        print(f"  TICKET: {codigo}")
        print("=" * 54)
        print(
            "\nAnote agora. Este código aparece uma única vez e já está\n"
            "vinculado a esta conta — não serve para mais ninguém.\n"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
