"""
comparativo.py
------------------------------------------------------------------
Compara escolas — com a honestidade que esse tipo de número exige.

A comparação entre escolas é a informação mais delicada do sistema.
Ela influencia decisão de formação, alocação de material e, na prática,
a reputação de equipes inteiras. Um ranking apresentado sem cuidado
transforma ruído em veredito.

Três cuidados estão embutidos aqui, e nenhum é opcional:

1. MARGEM DE ERRO EM TUDO. Uma escola com 18 alunos e 71% de acerto
   não está "acima" de outra com 120 alunos e 68%: a primeira tem
   margem de ±10 pontos. Todo percentual sai acompanhado do intervalo,
   e a diferença entre duas escolas só é chamada de diferença quando
   passa no teste estatístico.

2. COMPARAR O QUE É COMPARÁVEL. Duas escolas que aplicaram provas
   diferentes não são comparáveis por nota. O módulo alinha a
   comparação por CONTEÚDO, e informa quantas respostas de cada lado
   sustentam cada número.

3. NÃO PRODUZIR RANKING DE QUALIDADE. A saída mostra onde há diferença
   e do tamanho dela; não diz que uma escola "é melhor". Contexto
   socioeconômico, rotatividade de professor e perfil de entrada dos
   alunos não estão nestes dados, e concluir sobre eles a partir daqui
   seria atribuir à escola um resultado que ela não controla sozinha.
------------------------------------------------------------------
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import analitico
import escolaridade
from dados import Escola

# Nível de confiança de 95%: o valor convencional em educação, e o que
# a maioria dos relatórios da área usa.
Z_95 = 1.96

# Abaixo disso o número existe mas não sustenta comparação.
MINIMO_COMPARAVEL = 30


# ==================================================================
# ESTATISTICA
# ==================================================================
def margem_de_erro(proporcao: float, amostra: int) -> float:
    """
    Margem de erro de uma proporção, em pontos percentuais.

    `proporcao` vai de 0 a 1; o retorno é o ± em pontos. Com 25 alunos
    e 70% de acerto dá ±18 pontos — número que, exibido ao lado do
    resultado, impede a leitura de que 70% e 62% são coisas diferentes.
    """
    if amostra <= 0:
        return 100.0
    erro_padrao = math.sqrt(max(proporcao * (1 - proporcao), 1e-9) / amostra)
    return round(100 * Z_95 * erro_padrao, 1)


def diferenca_significativa(
    acertos_a: int, total_a: int, acertos_b: int, total_b: int
) -> Tuple[bool, float]:
    """
    Testa se duas taxas de acerto realmente diferem.

    Teste z para duas proporções. Sem ele, "62% contra 58%" vira
    manchete mesmo quando as duas amostras são de 20 alunos e a
    diferença cabe inteira dentro do acaso.

    Returns:
        (é_significativa, diferença_em_pontos)
    """
    if total_a <= 0 or total_b <= 0:
        return False, 0.0

    p_a, p_b = acertos_a / total_a, acertos_b / total_b
    diferenca = round(100 * (p_a - p_b), 1)

    # Proporção combinada, como manda o teste de duas amostras.
    p_geral = (acertos_a + acertos_b) / (total_a + total_b)
    erro = math.sqrt(max(p_geral * (1 - p_geral), 1e-9) * (1 / total_a + 1 / total_b))
    if erro == 0:
        return False, diferenca

    return abs((p_a - p_b) / erro) > Z_95, diferenca


# ==================================================================
# COLETA POR ESCOLA
# ==================================================================
def _resumo_da_escola(
    sessao: Session,
    escola: Escola,
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    desde: Optional[datetime] = None,
) -> dict:
    """Números de uma escola, já com margem de erro."""
    filtros = {
        "escola_id": escola.id,
        "ano_escolar": ano_escolar,
        "disciplina": disciplina,
        "desde": desde,
    }

    dados = analitico._resultados_filtrados(sessao, **filtros)
    mapa = analitico._mapa_de_conteudos(sessao, [p.id for _, p, _ in dados])

    geral = analitico.Contagem()
    por_conteudo: Dict[str, analitico.Contagem] = defaultdict(analitico.Contagem)
    notas: List[float] = []
    alunos, turmas, provas = set(), set(), set()

    for resultado, prova, turma in dados:
        notas.append(resultado.nota)
        alunos.add(resultado.aluno_id)
        turmas.add(turma.id)
        provas.add(prova.id)

        for detalhe in resultado.detalhamento or []:
            geral.registrar(detalhe)
            questao = mapa.get((prova.id, int(detalhe.get("questao", 0))))
            if questao and questao.conteudo:
                por_conteudo[questao.conteudo].registrar(detalhe)

    percentual = geral.percentual_acerto

    return {
        "escola_id": escola.id,
        "escola": escola.nome,
        "alunos": len(alunos),
        "turmas": len(turmas),
        "provas": len(provas),
        "respostas": geral.respondidas,
        "corretas": geral.corretas,
        "media": round(sum(notas) / len(notas), 2) if notas else None,
        "percentual_acerto": percentual,
        "margem_erro": (
            margem_de_erro(percentual / 100, geral.respondidas)
            if percentual is not None
            else None
        ),
        "comparavel": geral.respondidas >= MINIMO_COMPARAVEL,
        "_conteudos": por_conteudo,
    }


# ==================================================================
# COMPARACAO
# ==================================================================
def comparar_escolas(
    sessao: Session,
    escolas_ids: List[int],
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    desde: Optional[datetime] = None,
) -> dict:
    """
    Compara duas ou mais escolas no mesmo recorte.

    O recorte importa tanto quanto o número: comparar "a escola toda"
    mistura 1º e 9º ano e não diz nada. Filtrar por ano escolar e
    disciplina é o que torna a comparação interpretável.
    """
    escolas = [
        sessao.get(Escola, identificador)
        for identificador in escolas_ids
        if sessao.get(Escola, identificador) is not None
    ]

    if len(escolas) < 2:
        return {
            "escolas": [],
            "conteudos": [],
            "diferencas": [],
            "recorte": {},
            "avisos": ["Selecione pelo menos duas escolas para comparar."],
        }

    resumos = [
        _resumo_da_escola(sessao, escola, ano_escolar, disciplina, desde)
        for escola in escolas
    ]

    avisos: List[str] = []
    frageis = [r["escola"] for r in resumos if not r["comparavel"]]
    if frageis:
        avisos.append(
            f"{', '.join(frageis)} tem menos de {MINIMO_COMPARAVEL} respostas no "
            f"recorte escolhido. Os números aparecem, mas não sustentam "
            f"comparação — corrija mais folhas ou amplie o período."
        )

    sem_dados = [r["escola"] for r in resumos if r["respostas"] == 0]
    if sem_dados:
        avisos.append(
            f"{', '.join(sem_dados)} não tem nenhuma prova corrigida neste recorte."
        )

    # --- Conteúdos lado a lado -------------------------------------
    # Só entram conteúdos que EXISTEM em todas as escolas comparadas:
    # uma linha com buraco convida a comparar o que não é comparável.
    conjuntos = [set(r["_conteudos"]) for r in resumos if r["_conteudos"]]
    comuns = sorted(set.intersection(*conjuntos)) if conjuntos else []

    if conjuntos and not comuns:
        avisos.append(
            "As escolas selecionadas não têm nenhum conteúdo em comum "
            "classificado. Sem isso, só é possível comparar o desempenho geral."
        )

    linhas_conteudo = []
    for conteudo in comuns:
        celulas = []
        for resumo in resumos:
            contagem = resumo["_conteudos"][conteudo]
            percentual = contagem.percentual_acerto
            celulas.append(
                {
                    "escola_id": resumo["escola_id"],
                    "escola": resumo["escola"],
                    "percentual_acerto": percentual,
                    "respostas": contagem.respondidas,
                    "margem_erro": (
                        margem_de_erro(percentual / 100, contagem.respondidas)
                        if percentual is not None
                        else None
                    ),
                    "confiavel": contagem.respondidas >= MINIMO_COMPARAVEL,
                }
            )

        validas = [c for c in celulas if c["percentual_acerto"] is not None]
        melhor = max(validas, key=lambda c: c["percentual_acerto"], default=None)
        pior = min(validas, key=lambda c: c["percentual_acerto"], default=None)

        # A diferença entre o topo e a base só é relatada quando passa
        # no teste; caso contrário o campo sai como None de propósito.
        vao = None
        if melhor and pior and melhor is not pior:
            contagem_melhor = resumos_por_id(resumos, melhor["escola_id"])["_conteudos"][conteudo]
            contagem_pior = resumos_por_id(resumos, pior["escola_id"])["_conteudos"][conteudo]
            significativa, diferenca = diferenca_significativa(
                contagem_melhor.corretas,
                contagem_melhor.respondidas,
                contagem_pior.corretas,
                contagem_pior.respondidas,
            )
            vao = {
                "diferenca": diferenca,
                "significativa": significativa,
                "melhor": melhor["escola"],
                "pior": pior["escola"],
            }

        linhas_conteudo.append(
            {"conteudo": conteudo, "celulas": celulas, "vao": vao}
        )

    # Pior primeiro: a leitura começa pelo que precisa de ação.
    linhas_conteudo.sort(
        key=lambda linha: min(
            (c["percentual_acerto"] for c in linha["celulas"] if c["percentual_acerto"] is not None),
            default=101,
        )
    )

    # --- Diferenças no desempenho geral ----------------------------
    diferencas = []
    for i in range(len(resumos)):
        for j in range(i + 1, len(resumos)):
            a, b = resumos[i], resumos[j]
            if not (a["respostas"] and b["respostas"]):
                continue
            significativa, diferenca = diferenca_significativa(
                a["corretas"], a["respostas"], b["corretas"], b["respostas"]
            )
            diferencas.append(
                {
                    "escola_a": a["escola"],
                    "escola_b": b["escola"],
                    "diferenca": diferenca,
                    "significativa": significativa,
                    "leitura": _frase_da_diferenca(a, b, diferenca, significativa),
                }
            )

    for resumo in resumos:
        resumo.pop("_conteudos", None)

    return {
        "escolas": resumos,
        "conteudos": linhas_conteudo,
        "diferencas": diferencas,
        "recorte": {
            "ano_escolar": escolaridade.nome_do(ano_escolar) if ano_escolar else "todos",
            "disciplina": disciplina or "todas",
            "minimo_comparavel": MINIMO_COMPARAVEL,
        },
        "avisos": avisos,
    }


def resumos_por_id(resumos: List[dict], escola_id: int) -> dict:
    return next(r for r in resumos if r["escola_id"] == escola_id)


def _frase_da_diferenca(
    a: dict, b: dict, diferenca: float, significativa: bool
) -> str:
    """
    Escreve a leitura do resultado.

    Quando a diferença não passa no teste, a frase diz isso com todas
    as letras. É o ponto em que o sistema mais pode induzir a erro: um
    número maior que o outro parece conclusão, e quase nunca é.
    """
    if not significativa:
        return (
            f"{a['escola']} e {b['escola']} estão empatadas dentro da margem de "
            f"erro ({abs(diferenca)} pontos de diferença). Com estas amostras, "
            f"não dá para afirmar que uma vai melhor que a outra."
        )

    frente, atras = (a, b) if diferenca > 0 else (b, a)
    return (
        f"{frente['escola']} tem {abs(diferenca)} pontos percentuais a mais de "
        f"acerto que {atras['escola']}, e a diferença resiste ao tamanho das "
        f"amostras ({frente['respostas']} e {atras['respostas']} respostas). "
        f"Vale investigar o que difere entre as duas — currículo, material ou "
        f"formação — antes de concluir qualquer coisa sobre as equipes."
    )
