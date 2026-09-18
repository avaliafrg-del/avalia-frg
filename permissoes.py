"""
permissoes.py
------------------------------------------------------------------
Quem pode ver o quê, e fazer o quê. Em um lugar só.

Regra de projeto: nenhuma verificação de permissão fica solta dentro de
um endpoint. Espalhar `if usuario.papel == "admin"` pelo código garante
que, mais cedo ou mais tarde, um endpoint novo nasça sem a checagem — e
um vazamento de dado de aluno não avisa quando acontece.

Os três papéis
--------------
ADMIN (secretaria)
    Tudo. É o único que cria usuários e o único que compara escolas.

MEMBRO (escola)
    Corrige as provas da própria escola e consulta os resultados dela.
    Não enxerga outra escola, não compara escolas, e não altera
    cadastro — a prova e a turma vêm prontas da secretaria.

CONVIDADO (professor avulso)
    Usa o sistema sem banco: monta o gabarito, corrige as fotos e baixa
    o relatório. Nada é gravado, então nada precisa ser protegido — e o
    professor fica responsável por guardar o próprio arquivo.

Princípio de falha fechada
--------------------------
Toda função responde "não" quando está em dúvida. Um membro sem escola
vinculada não vê nada, em vez de ver tudo; um papel desconhecido cai no
mais restrito. Erro de cadastro vira acesso de menos, nunca de mais.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Optional

ADMIN = "admin"
MEMBRO = "membro"
CONVIDADO = "convidado"

PAPEIS = (ADMIN, MEMBRO, CONVIDADO)

NOMES = {
    ADMIN: "Administrador",
    MEMBRO: "Escola",
    CONVIDADO: "Convidado",
}

DESCRICOES = {
    ADMIN: "Acesso total: cadastro, correção, análise e comparação entre escolas.",
    MEMBRO: (
        "Corrige as provas da própria escola e vê os resultados dela. "
        "Não altera cadastro nem enxerga outras escolas."
    ),
    CONVIDADO: (
        "Uso individual, sem banco de dados. Monta o gabarito, corrige e "
        "baixa o relatório — nada fica guardado no sistema."
    ),
}


class SemPermissao(Exception):
    """A ação não é permitida para este papel. Vira 403 na API."""


# ==================================================================
# CONSULTAS
# ==================================================================
def papel_de(usuario) -> str:
    """Papel do usuário, com o mais restritivo como padrão."""
    papel = getattr(usuario, "papel", None)
    return papel if papel in PAPEIS else CONVIDADO


def e_admin(usuario) -> bool:
    return papel_de(usuario) == ADMIN


def e_convidado(usuario) -> bool:
    return papel_de(usuario) == CONVIDADO


def usa_banco(usuario) -> bool:
    """Convidado opera sem persistência nenhuma."""
    return papel_de(usuario) in (ADMIN, MEMBRO)


def pode_editar_cadastro(usuario) -> bool:
    """
    Criar e apagar escola, turma, aluno e prova.

    Só a secretaria. A escola recebe a prova pronta e padronizada; se
    pudesse editar o gabarito, a comparação entre escolas deixaria de
    ter sentido — cada uma estaria medindo uma coisa diferente.
    """
    return e_admin(usuario)


def pode_comparar_escolas(usuario) -> bool:
    """
    Só a secretaria compara escolas.

    Não é detalhe de tela: uma escola ver o desempenho das vizinhas
    produz ranking informal entre equipes a partir de dados que não
    controlam o contexto de cada uma.
    """
    return e_admin(usuario)


def pode_gerenciar_usuarios(usuario) -> bool:
    return e_admin(usuario)


def pode_corrigir(usuario) -> bool:
    """Todos corrigem. Muda o que acontece com o resultado depois."""
    return True


def escola_permitida(usuario) -> Optional[int]:
    """
    A qual escola este usuário está restrito.

    None significa "sem restrição" para admin — e "nenhuma escola" para
    os demais. Quem chama precisa distinguir os dois casos com
    `e_admin`, e por isso `filtrar_escola` existe logo abaixo.
    """
    return getattr(usuario, "escola_id", None)


def pode_ver_escola(usuario, escola_id: Optional[int]) -> bool:
    """Um membro só enxerga a própria escola."""
    if e_admin(usuario):
        return True
    if e_convidado(usuario):
        return False
    return escola_id is not None and escola_id == escola_permitida(usuario)


def filtrar_escola(usuario, escola_id: Optional[int] = None) -> Optional[int]:
    """
    Resolve qual escola usar numa consulta, respeitando o escopo.

    Admin recebe o que pediu (ou None, para todas). Membro recebe SEMPRE
    a própria escola, mesmo que peça outra — ignorar o parâmetro é mais
    seguro que recusar, porque uma tentativa de troca de id vira consulta
    válida ao próprio dado em vez de erro que revela a existência da
    outra escola.
    """
    if e_admin(usuario):
        return escola_id
    return escola_permitida(usuario)


# ==================================================================
# EXIGENCIAS
# ==================================================================
def exigir(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise SemPermissao(mensagem)


def exigir_admin(usuario) -> None:
    exigir(
        e_admin(usuario),
        "Esta ação é exclusiva de quem administra o sistema.",
    )


def exigir_banco(usuario) -> None:
    exigir(
        usa_banco(usuario),
        "Sua conta é de uso individual e não guarda dados no sistema. "
        "Use a correção avulsa e baixe o relatório ao final.",
    )


def exigir_edicao(usuario) -> None:
    exigir(
        pode_editar_cadastro(usuario),
        "Sua conta pode consultar e corrigir, mas não alterar o cadastro. "
        "Peça à secretaria.",
    )


def exigir_acesso_a_escola(usuario, escola_id: Optional[int]) -> None:
    exigir(
        pode_ver_escola(usuario, escola_id),
        "Sua conta não tem acesso aos dados desta escola.",
    )


def resumo(usuario) -> dict:
    """O que a interface precisa saber para montar o menu."""
    papel = papel_de(usuario)
    return {
        "papel": papel,
        "papel_nome": NOMES[papel],
        # Derivado do papel, mas exposto separado porque a interface
        # decide a visibilidade do menu de sistema com esta resposta.
        "administrador": papel == ADMIN,
        "escola_id": escola_permitida(usuario) if papel == MEMBRO else None,
        "pode_editar_cadastro": pode_editar_cadastro(usuario),
        "pode_comparar_escolas": pode_comparar_escolas(usuario),
        "pode_gerenciar_usuarios": pode_gerenciar_usuarios(usuario),
        "usa_banco": usa_banco(usuario),
    }
