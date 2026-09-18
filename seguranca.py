"""
seguranca.py
------------------------------------------------------------------
Senhas, sessoes e assinatura do QR Code.

Sem dependencia externa: tudo sai de `hashlib`, `hmac` e `secrets`, da
biblioteca padrao. Uma biblioteca a menos e uma superficie de ataque a
menos, e nada aqui exige algoritmo exotico.

Duas protecoes distintas moram neste arquivo:

1. LOGIN — impede que qualquer pessoa com o endereco veja e altere as
   notas. Nome de aluno e dado pessoal (LGPD).

2. ASSINATURA DO QR — impede que um aluno gere um cartao proprio com o
   gabarito que quiser, ou troque o identificador para jogar a nota
   dele no boletim de outro.
------------------------------------------------------------------
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Optional, Tuple

# ==================================================================
# SENHAS
# ==================================================================
# PBKDF2 com 200 mil iteracoes: caro o suficiente para tornar a
# tentativa em massa impraticavel, barato o suficiente para nao
# atrasar o login de um professor.
ITERACOES = 200_000
TAMANHO_SAL = 16
SENHA_MINIMA = 8


class SenhaFraca(Exception):
    """Senha nao atende ao minimo exigido."""


def validar_forca(senha: str) -> None:
    if len(senha or "") < SENHA_MINIMA:
        raise SenhaFraca(
            f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres."
        )


def criar_hash_senha(senha: str) -> str:
    """
    Devolve "pbkdf2_sha256$iteracoes$sal$hash", tudo em uma string.

    Guardar o algoritmo e as iteracoes junto do hash permite aumentar o
    custo no futuro sem invalidar as senhas ja cadastradas.
    """
    validar_forca(senha)
    sal = secrets.token_bytes(TAMANHO_SAL)
    derivado = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(ITERACOES),
            base64.b64encode(sal).decode(),
            base64.b64encode(derivado).decode(),
        ]
    )


def conferir_senha(senha: str, guardado: str) -> bool:
    """Compara em tempo constante, para nao vazar informacao pelo tempo."""
    try:
        algoritmo, iteracoes, sal_b64, hash_b64 = guardado.split("$")
        if algoritmo != "pbkdf2_sha256":
            return False
        derivado = hashlib.pbkdf2_hmac(
            "sha256",
            (senha or "").encode(),
            base64.b64decode(sal_b64),
            int(iteracoes),
        )
        return hmac.compare_digest(derivado, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# ==================================================================
# SESSOES
# ==================================================================
NOME_COOKIE = "avaliafrg_sessao"
HORAS_SESSAO = 12


def novo_token() -> str:
    """Token de sessao entregue ao navegador."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """
    O banco guarda apenas o hash do token.

    Se o banco vazar, os tokens guardados nao servem para entrar: o
    atacante teria o hash, e o cookie precisa do valor original.
    """
    return hashlib.sha256(token.encode()).hexdigest()


# ==================================================================
# ASSINATURA DO QR CODE
# ==================================================================
# Base32 sem padding usa apenas A-Z e 2-7 — todos dentro do conjunto
# ALFANUMERICO do QR, que gasta 5,5 bits por caractere em vez de 8.
# Uma assinatura em base64 forcaria o codigo inteiro para o modo byte
# e engordaria o QR justamente no caso das provas longas.
TAMANHO_ASSINATURA = 8
SEPARADOR_ASSINATURA = "-"
PADRAO_ASSINATURA = re.compile(rf"^[A-Z2-7]{{{TAMANHO_ASSINATURA}}}$")


def gerar_segredo() -> str:
    """Segredo novo, usado quando ainda nao existe um guardado."""
    return secrets.token_urlsafe(32)


def assinar(conteudo: str, segredo: str) -> str:
    """Assinatura curta do conteudo do QR."""
    digest = hmac.new(segredo.encode(), conteudo.encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode()[:TAMANHO_ASSINATURA]


def anexar_assinatura(conteudo: str, segredo: Optional[str]) -> str:
    if not segredo:
        return conteudo
    return f"{conteudo}{SEPARADOR_ASSINATURA}{assinar(conteudo, segredo)}"


def separar_assinatura(texto: str) -> Tuple[str, Optional[str]]:
    """
    Separa "P12.A7*ABC-K3M9QZ7X" em ("P12.A7*ABC", "K3M9QZ7X").

    So reconhece o sufixo quando ele casa exatamente com o formato da
    assinatura. Assim um codigo de prova digitado com hifen, como
    "PROVA-FINAL", nao e confundido com um cartao assinado.
    """
    if SEPARADOR_ASSINATURA not in texto:
        return texto, None

    corpo, _, sufixo = texto.rpartition(SEPARADOR_ASSINATURA)
    if corpo and PADRAO_ASSINATURA.match(sufixo):
        return corpo, sufixo
    return texto, None


def conferir_assinatura(texto: str, segredo: Optional[str]) -> Tuple[str, str]:
    """
    Confere a assinatura do QR.

    Returns:
        (conteudo_sem_assinatura, situacao), onde situacao e:
            "valida"        — assinatura confere
            "invalida"      — assinatura presente mas nao confere
            "ausente"       — cartao sem assinatura (impresso antes)
            "nao_conferida" — nao havia segredo para conferir
    """
    corpo, assinatura = separar_assinatura(texto)

    if assinatura is None:
        return corpo, "ausente"
    if not segredo:
        return corpo, "nao_conferida"

    esperada = assinar(corpo, segredo)
    return corpo, ("valida" if hmac.compare_digest(esperada, assinatura) else "invalida")


# ==================================================================
# TICKETS DE ACESSO
# ==================================================================
# Alfabeto sem caracteres que se confundem quando alguem le um codigo
# em voz alta ou copia de um papel: sem O/0, I/1, S/5 e Z/2.
ALFABETO_TICKET = "ABCDEFGHJKLMNPQRTUVWXY346789"
GRUPOS_TICKET = 2
TAMANHO_GRUPO = 4
PREFIXO_TICKET = "PF"


def gerar_codigo_ticket() -> str:
    """
    Gera um codigo no formato "PF-ABCD-EFGH".

    Oito caracteres num alfabeto de 28 dao ~38 bits: adivinhar por
    tentativa e erro e inviavel, e continua curto o bastante para o
    professor digitar sem errar.
    """
    grupos = [
        "".join(secrets.choice(ALFABETO_TICKET) for _ in range(TAMANHO_GRUPO))
        for _ in range(GRUPOS_TICKET)
    ]
    return "-".join([PREFIXO_TICKET, *grupos])


def normalizar_ticket(codigo: str) -> str:
    """
    Deixa o codigo comparavel: maiusculas, sem espacos nem hifens.

    O professor vai digitar com e sem hifen, em minuscula, com espaco
    colado do copiar-e-colar. Nada disso deve impedir a entrada.
    """
    return "".join(ch for ch in (codigo or "").upper() if ch.isalnum())


def hash_ticket(codigo: str) -> str:
    """
    O banco guarda so o hash do ticket, como faz com a senha.

    Se o banco vazar, os codigos guardados nao servem para entrar.
    """
    return hashlib.sha256(normalizar_ticket(codigo).encode()).hexdigest()


def rotulo_ticket(codigo: str) -> str:
    """
    Parte visivel do codigo, para identificar o ticket na lista sem
    revelar o codigo inteiro: "PF-ABCD-****".
    """
    partes = codigo.split("-")
    if len(partes) >= 3:
        return f"{partes[0]}-{partes[1]}-" + "*" * len(partes[2])
    return codigo[:4] + "*" * max(0, len(codigo) - 4)


# ==================================================================
# TICKETS DE ACESSO
# ==================================================================
# Alfabeto sem caracteres ambiguos: 0/O e 1/I/L saem, porque o codigo
# vai ser lido de um papel ou de uma mensagem e digitado a mao.
ALFABETO_TICKET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GRUPOS_TICKET = 3
TAMANHO_GRUPO = 4
PREFIXO_TICKET = "PF"


def gerar_ticket() -> str:
    """
    Codigo de acesso no formato PF-A3KM-9XQT-B7WZ.

    Sao 12 caracteres em um alfabeto de 31: cerca de 59 bits de entropia,
    inviavel de adivinhar. Os hifens existem so para o professor
    conseguir ler e digitar sem errar.
    """
    grupos = [
        "".join(secrets.choice(ALFABETO_TICKET) for _ in range(TAMANHO_GRUPO))
        for _ in range(GRUPOS_TICKET)
    ]
    return f"{PREFIXO_TICKET}-" + "-".join(grupos)


def normalizar_ticket(codigo: str) -> str:
    """
    Aceita o codigo como o professor digitar.

    Sem hifen, com espacos, em minuscula — tudo vira a mesma coisa. Um
    codigo correto recusado por causa de um hifen a menos seria um
    suporte inteiro de trabalho para nada.
    """
    limpo = "".join(
        c for c in (codigo or "").upper() if c.isalnum()
    )
    if limpo.startswith(PREFIXO_TICKET):
        limpo = limpo[len(PREFIXO_TICKET):]

    grupos = [
        limpo[i : i + TAMANHO_GRUPO] for i in range(0, len(limpo), TAMANHO_GRUPO)
    ]
    return f"{PREFIXO_TICKET}-" + "-".join(grupos) if grupos else ""


def hash_ticket(codigo: str) -> str:
    """
    O banco guarda so o hash do ticket, como faz com a senha.

    Se o banco vazar, os codigos guardados nao servem para entrar.
    A consequencia e que o codigo completo so pode ser mostrado uma
    vez, no momento em que e criado.
    """
    return hashlib.sha256(normalizar_ticket(codigo).encode()).hexdigest()


def pista_do_ticket(codigo: str) -> str:
    """Trecho visivel na listagem, para identificar sem revelar."""
    normalizado = normalizar_ticket(codigo)
    return normalizado[:7] + "…" if len(normalizado) > 7 else normalizado
