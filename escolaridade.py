"""
escolaridade.py
------------------------------------------------------------------
A escala de anos escolares brasileira.

Por que isto e um modulo e nao um campo de texto livre:

O professor pede para comparar "o 2o ano" entre escolas. Se o ano
escolar for digitado a mao, a mesma serie aparece como "2 ano",
"2º Ano", "segundo ano" e "2o" — e a comparacao entre escolas, que e
justamente o objetivo, deixa de funcionar. Aqui a lista e fechada e
cada item tem uma ORDEM, que serve para ordenar do menor para o maior
sem depender de comparacao de texto ("10º" viria antes de "2º" numa
ordenacao alfabetica).

Turma continua tendo nome livre ("8º B", "Turma da Manha"): esse e o
apelido da turma. O ano escolar e um dado estruturado, separado.
------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class AnoEscolar:
    codigo: str        # guardado no banco, estavel
    nome: str          # exibido na tela
    ordem: int         # para ordenar e agrupar etapas
    etapa: str         # "Educacao Infantil" | "Fundamental" | "Medio"


ANOS_ESCOLARES: List[AnoEscolar] = [
    AnoEscolar("EI", "Educação Infantil", 0, "Educação Infantil"),
    *[
        AnoEscolar(f"EF{n}", f"{n}º ano", n, "Ensino Fundamental")
        for n in range(1, 10)
    ],
    *[
        AnoEscolar(f"EM{n}", f"{n}ª série", 9 + n, "Ensino Médio")
        for n in range(1, 4)
    ],
    AnoEscolar("EJA", "EJA", 20, "EJA"),
    AnoEscolar("OUTRO", "Outro", 99, "Outro"),
]

POR_CODIGO: Dict[str, AnoEscolar] = {a.codigo: a for a in ANOS_ESCOLARES}


def normalizar(valor: Optional[str]) -> Optional[str]:
    """
    Aceita o codigo ou algo proximo do nome e devolve o codigo canonico.

    Existe para o cadastro nao quebrar quando o dado vem de planilha ou
    de digitacao: "3", "3o", "3º ano" e "EF3" viram todos "EF3".
    """
    if not valor:
        return None

    bruto = str(valor).strip().upper().replace("°", "º")
    if bruto in POR_CODIGO:
        return bruto

    # Nome exato
    for ano in ANOS_ESCOLARES:
        if bruto == ano.nome.upper():
            return ano.codigo

    # Apenas o numero: assume Fundamental, que cobre 1 a 9
    digitos = "".join(c for c in bruto if c.isdigit())
    if digitos:
        numero = int(digitos)
        if "SÉRIE" in bruto or "SERIE" in bruto or "MÉDIO" in bruto or "MEDIO" in bruto:
            if 1 <= numero <= 3:
                return f"EM{numero}"
        if 1 <= numero <= 9:
            return f"EF{numero}"

    if "EJA" in bruto:
        return "EJA"
    if "INFANTIL" in bruto:
        return "EI"

    return "OUTRO"


def nome_do(codigo: Optional[str]) -> str:
    ano = POR_CODIGO.get(codigo or "")
    return ano.nome if ano else "Sem ano definido"


def ordem_do(codigo: Optional[str]) -> int:
    ano = POR_CODIGO.get(codigo or "")
    return ano.ordem if ano else 999


def etapa_do(codigo: Optional[str]) -> str:
    ano = POR_CODIGO.get(codigo or "")
    return ano.etapa if ano else "Outro"


def listar() -> List[dict]:
    """Lista para a interface montar o seletor."""
    return [
        {"codigo": a.codigo, "nome": a.nome, "etapa": a.etapa, "ordem": a.ordem}
        for a in ANOS_ESCOLARES
    ]
