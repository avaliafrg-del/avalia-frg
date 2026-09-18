"""
dados/repositorios/resultados.py
------------------------------------------------------------------
Notas corrigidas e as estatisticas de uma prova.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.conexao import agora
from dados.erros import RegistroNaoEncontrado
from dados.modelos import Aluno, Prova, Resultado
from dados.repositorios.comum import obter as _obter

def salvar_resultado(
    sessao: Session, prova_id: int, aluno_id: int, correcao: dict
) -> Resultado:
    """
    Grava (ou regrava) o resultado de um aluno.

    Recorrigir a mesma folha atualiza o registro existente: o professor
    que refaz uma foto ruim nao deve acabar com duas notas para o mesmo
    aluno na mesma prova.
    """
    prova = _obter(sessao, Prova, prova_id)
    aluno = _obter(sessao, Aluno, aluno_id)

    if aluno.turma_id != prova.turma_id:
        raise RegistroNaoEncontrado(
            f"O aluno {aluno.nome} nao pertence a turma desta prova."
        )

    resultado = sessao.scalar(
        select(Resultado).where(
            Resultado.prova_id == prova_id, Resultado.aluno_id == aluno_id
        )
    )
    if resultado is None:
        resultado = Resultado(prova_id=prova_id, aluno_id=aluno_id)
        sessao.add(resultado)

    resultado.nota = correcao["nota"]
    resultado.acertos = correcao["acertos"]
    resultado.erros = correcao["erros"]
    resultado.em_branco = correcao["em_branco"]
    resultado.rasuras = correcao["rasuras"]
    resultado.detalhamento = correcao["detalhamento"]
    resultado.alinhamento = correcao.get("alinhamento", "ancoras")
    resultado.corrigido_em = agora()

    sessao.commit()
    return resultado


def listar_resultados(sessao: Session, prova_id: int) -> List[Resultado]:
    return list(
        sessao.scalars(
            select(Resultado)
            .join(Aluno)
            .where(Resultado.prova_id == prova_id)
            .order_by(Aluno.nome)
        )
    )


def estatisticas_da_prova(sessao: Session, prova_id: int) -> dict:
    """
    Media, extremos e o acerto por questao.

    O acerto por questao e o que transforma a correcao em informacao
    pedagogica: mostra onde a turma inteira tropecou, e nao so quem foi
    bem ou mal.
    """
    prova = _obter(sessao, Prova, prova_id)
    resultados = listar_resultados(sessao, prova_id)

    if not resultados:
        return {
            "total_corrigidos": 0,
            "media": None,
            "maior_nota": None,
            "menor_nota": None,
            "acerto_por_questao": [],
        }

    notas = [r.nota for r in resultados]
    total_questoes = prova.total_questoes
    acertos_por_questao = [0] * (total_questoes + 1)

    for resultado in resultados:
        for detalhe in resultado.detalhamento or []:
            if detalhe.get("status") == "correto":
                numero = int(detalhe["questao"])
                if 1 <= numero <= total_questoes:
                    acertos_por_questao[numero] += 1

    return {
        "total_corrigidos": len(resultados),
        "media": round(sum(notas) / len(notas), 2),
        "maior_nota": max(notas),
        "menor_nota": min(notas),
        "acerto_por_questao": [
            {
                "questao": numero,
                "acertos": acertos_por_questao[numero],
                "percentual": round(100 * acertos_por_questao[numero] / len(resultados)),
                "correta": prova.gabarito_dict.get(numero),
            }
            for numero in range(1, total_questoes + 1)
        ],
    }
