"""
dados/repositorios/historico.py
------------------------------------------------------------------
Desempenho do aluno e da turma ao longo do ano.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.modelos import Aluno, Prova, Resultado, Turma
from dados.repositorios.alunos import listar_alunos
from dados.repositorios.comum import obter as _obter
from dados.repositorios.provas import listar_provas
from dados.repositorios.resultados import listar_resultados

def historico_do_aluno(sessao: Session, aluno_id: int) -> dict:
    """
    Todas as notas do aluno, da mais antiga para a mais recente.

    E o que transforma o sistema de "corretor de prova" em
    "acompanhamento": o professor ve a evolucao, nao so o resultado
    isolado de uma avaliacao.
    """
    aluno = _obter(sessao, Aluno, aluno_id)

    resultados = list(
        sessao.scalars(
            select(Resultado)
            .join(Prova)
            .where(Resultado.aluno_id == aluno_id)
            .order_by(Prova.criada_em)
        )
    )

    avaliacoes = [
        {
            "prova_id": r.prova_id,
            "titulo": r.prova.titulo,
            "disciplina": r.prova.disciplina,
            "data": r.corrigido_em,
            "nota": r.nota,
            "acertos": r.acertos,
            "total_questoes": r.prova.total_questoes,
        }
        for r in resultados
    ]

    notas = [a["nota"] for a in avaliacoes]
    # Compara a primeira metade com a segunda: com poucas provas, media
    # movel ou regressao diriam pouco e enganariam mais do que ajudam.
    tendencia = None
    if len(notas) >= 4:
        meio = len(notas) // 2
        inicio = sum(notas[:meio]) / meio
        fim = sum(notas[meio:]) / (len(notas) - meio)
        diferenca = fim - inicio
        if abs(diferenca) < 0.5:
            tendencia = "estavel"
        else:
            tendencia = "subindo" if diferenca > 0 else "caindo"

    return {
        "aluno_id": aluno.id,
        "aluno_nome": aluno.nome,
        "turma": aluno.turma.nome,
        "escola": aluno.turma.escola.nome,
        "total_provas": len(avaliacoes),
        "media": round(sum(notas) / len(notas), 2) if notas else None,
        "maior_nota": max(notas) if notas else None,
        "menor_nota": min(notas) if notas else None,
        "tendencia": tendencia,
        "avaliacoes": avaliacoes,
    }


def desempenho_da_turma(sessao: Session, turma_id: int) -> dict:
    """Media de cada aluno e media da turma em cada prova."""
    turma = _obter(sessao, Turma, turma_id)
    alunos = listar_alunos(sessao, turma_id)
    provas = sorted(listar_provas(sessao, turma_id), key=lambda p: p.criada_em)

    notas_por_prova: Dict[int, Dict[int, float]] = {}
    for prova in provas:
        notas_por_prova[prova.id] = {
            r.aluno_id: r.nota for r in listar_resultados(sessao, prova.id)
        }

    linhas = []
    for aluno in alunos:
        notas = [
            notas_por_prova[p.id][aluno.id]
            for p in provas
            if aluno.id in notas_por_prova[p.id]
        ]
        linhas.append(
            {
                "aluno_id": aluno.id,
                "aluno_nome": aluno.nome,
                "notas": [notas_por_prova[p.id].get(aluno.id) for p in provas],
                "media": round(sum(notas) / len(notas), 2) if notas else None,
                "provas_feitas": len(notas),
            }
        )

    medias_prova = []
    for prova in provas:
        valores = list(notas_por_prova[prova.id].values())
        medias_prova.append(
            {
                "prova_id": prova.id,
                "titulo": prova.titulo,
                "media": round(sum(valores) / len(valores), 2) if valores else None,
                "corrigidas": len(valores),
            }
        )

    return {
        "turma": turma.nome,
        "escola": turma.escola.nome,
        "provas": medias_prova,
        "alunos": linhas,
    }
