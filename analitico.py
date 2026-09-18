"""
analitico.py
------------------------------------------------------------------
Transforma as correções em diagnóstico pedagógico.

O que o professor precisa responder:

    "Qual conteúdo o 2º ano desta escola está errando mais?"
    "Vale retomar frações, ou foi só uma questão mal formulada?"
    "O que a rede inteira precisa reforçar neste bimestre?"

Duas decisões de projeto governam este módulo:

1. CONTEUDO ACIMA DE QUESTAO. "Erraram a 15" só vale dentro daquela
   prova. "Erraram frações" atravessa provas, turmas e anos, e é a
   única forma da conclusão virar orientação. Por isso a agregação
   principal é por conteúdo, e a questão entra como evidência.

2. AMOSTRA PEQUENA NÃO VIRA CONCLUSAO. Uma questão respondida por 8
   alunos com 40% de acerto não distingue "conteúdo mal aprendido" de
   "acaso". Todo resultado carrega o tamanho da amostra e um selo de
   confiabilidade — e o texto de orientação só é gerado acima do
   mínimo. Um sistema que produz recomendações confiantes a partir de
   ruído é pior que um que se cala.

O módulo não altera nada: só lê e agrega.
------------------------------------------------------------------
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

import escolaridade
from dados import Aluno, Escola, Prova, Questao, Resultado, Turma

# ==================================================================
# LIMIARES
# ==================================================================
# Abaixo disso o resultado é exibido, mas marcado como amostra pequena
# e fica fora das recomendações automáticas.
MINIMO_RESPOSTAS_CONFIAVEL = 25
# Percentual de acerto abaixo do qual o conteúdo entra como prioridade.
LIMIAR_ATENCAO = 60
LIMIAR_CRITICO = 40
# Um distrator só é considerado "concentrado" quando concentra pelo
# menos esta fatia de TODOS OS ERROS da questão. Com 5 alternativas, o
# chute espalha os erros em 25% para cada uma das 4 erradas; exigir
# metade coloca a barra bem acima do que o acaso produz.
LIMIAR_FATIA_DOS_ERROS = 50
# E precisa também representar uma fatia relevante da turma inteira:
# 100% dos erros numa alternativa não diz nada se só dois alunos
# erraram.
LIMIAR_DISTRATOR = 25
# Mínimo para uma questão ser CITADA como exemplo dentro de uma
# orientação. É menor que o mínimo de confiabilidade porque a
# recomendação já se apoia no conteúdo agregado; a questão entra só
# como ilustração, e o texto informa o tamanho da amostra.
MINIMO_ILUSTRACAO = 10


# ==================================================================
# ESTRUTURAS
# ==================================================================
@dataclass
class Contagem:
    """Acumulador de respostas de um recorte qualquer."""

    corretas: int = 0
    incorretas: int = 0
    em_branco: int = 0
    rasuras: int = 0
    # Quantas vezes cada alternativa foi marcada
    marcacoes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def respondidas(self) -> int:
        """Em branco e rasura não dizem nada sobre saber ou não saber."""
        return self.corretas + self.incorretas

    @property
    def total(self) -> int:
        return self.respondidas + self.em_branco + self.rasuras

    @property
    def percentual_acerto(self) -> Optional[float]:
        if not self.respondidas:
            return None
        return round(100 * self.corretas / self.respondidas, 1)

    @property
    def confiavel(self) -> bool:
        return self.respondidas >= MINIMO_RESPOSTAS_CONFIAVEL

    def registrar(self, detalhe: dict) -> None:
        situacao = detalhe.get("status")
        if situacao == "correto":
            self.corretas += 1
        elif situacao == "incorreto":
            self.incorretas += 1
        elif situacao == "rasura":
            self.rasuras += 1
        else:
            self.em_branco += 1

        marcada = detalhe.get("marcada")
        if marcada:
            self.marcacoes[marcada] += 1


def _classificar(contagem: Contagem) -> str:
    """critico | atencao | adequado | sem_dados"""
    percentual = contagem.percentual_acerto
    if percentual is None:
        return "sem_dados"
    if percentual < LIMIAR_CRITICO:
        return "critico"
    if percentual < LIMIAR_ATENCAO:
        return "atencao"
    return "adequado"


# ==================================================================
# COLETA
# ==================================================================
def _resultados_filtrados(
    sessao: Session,
    escola_id: Optional[int] = None,
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    turma_id: Optional[int] = None,
    prova_id: Optional[int] = None,
    desde: Optional[datetime] = None,
) -> List[Tuple[Resultado, Prova, Turma]]:
    """
    Traz os resultados que atendem ao recorte pedido.

    Um único SELECT com joins: percorrer turma por turma em Python
    faria uma consulta por turma e ficaria lento assim que a rede
    tivesse algumas dezenas delas.
    """
    consulta = (
        select(Resultado, Prova, Turma)
        .join(Prova, Resultado.prova_id == Prova.id)
        .join(Turma, Prova.turma_id == Turma.id)
    )

    if escola_id is not None:
        consulta = consulta.where(Turma.escola_id == escola_id)
    if ano_escolar:
        consulta = consulta.where(Turma.ano_escolar == escolaridade.normalizar(ano_escolar))
    if disciplina:
        consulta = consulta.where(Prova.disciplina == disciplina)
    if turma_id is not None:
        consulta = consulta.where(Turma.id == turma_id)
    if prova_id is not None:
        consulta = consulta.where(Prova.id == prova_id)
    if desde is not None:
        consulta = consulta.where(Resultado.corrigido_em >= desde)

    return list(sessao.execute(consulta).all())


def _mapa_de_conteudos(sessao: Session, provas: List[int]) -> Dict[Tuple[int, int], Questao]:
    """(prova_id, numero) -> Questao, em uma consulta só."""
    if not provas:
        return {}
    questoes = sessao.scalars(select(Questao).where(Questao.prova_id.in_(provas)))
    return {(q.prova_id, q.numero): q for q in questoes}


# ==================================================================
# ANALISE POR CONTEUDO
# ==================================================================
def analise_por_conteudo(sessao: Session, **filtros) -> dict:
    """
    Agrupa o desempenho por conteúdo cobrado.

    É a saída principal do módulo: responde "o que precisa ser
    retomado", que é a pergunta que o professor realmente tem.
    """
    dados = _resultados_filtrados(sessao, **filtros)
    mapa = _mapa_de_conteudos(sessao, [prova.id for _, prova, _ in dados])

    por_conteudo: Dict[str, Contagem] = defaultdict(Contagem)
    # Quais questões e provas alimentaram cada conteúdo — é o que
    # permite ao professor conferir a origem do número.
    origens: Dict[str, set] = defaultdict(set)
    sem_classificacao = Contagem()

    for resultado, prova, _turma in dados:
        for detalhe in resultado.detalhamento or []:
            questao = mapa.get((prova.id, int(detalhe.get("questao", 0))))
            conteudo = (questao.conteudo if questao else "") or ""

            if not conteudo:
                sem_classificacao.registrar(detalhe)
                continue

            por_conteudo[conteudo].registrar(detalhe)
            origens[conteudo].add((prova.id, prova.titulo, int(detalhe["questao"])))

    itens = []
    for conteudo, contagem in por_conteudo.items():
        itens.append(
            {
                "conteudo": conteudo,
                "percentual_acerto": contagem.percentual_acerto,
                "corretas": contagem.corretas,
                "incorretas": contagem.incorretas,
                "em_branco": contagem.em_branco,
                "respostas": contagem.respondidas,
                "questoes": len({numero for _, _, numero in origens[conteudo]}),
                "provas": len({pid for pid, _, _ in origens[conteudo]}),
                "situacao": _classificar(contagem),
                "confiavel": contagem.confiavel,
            }
        )

    # Pior primeiro: é a ordem em que o professor quer ler.
    itens.sort(key=lambda i: (i["percentual_acerto"] is None, i["percentual_acerto"]))

    return {
        "conteudos": itens,
        "questoes_sem_conteudo": sem_classificacao.total,
        "total_respostas": sum(i["respostas"] for i in itens),
    }


# ==================================================================
# ANALISE POR QUESTAO
# ==================================================================
def analise_por_questao(sessao: Session, **filtros) -> List[dict]:
    """
    Desempenho de cada questão, com a alternativa errada mais marcada.

    O distrator dominante é o dado mais acionável desta função: quando
    metade da turma escolhe a MESMA alternativa errada, não houve
    chute — há um erro conceitual específico, e dá para saber qual.
    """
    dados = _resultados_filtrados(sessao, **filtros)
    mapa = _mapa_de_conteudos(sessao, [prova.id for _, prova, _ in dados])

    por_questao: Dict[Tuple[int, int], Contagem] = defaultdict(Contagem)
    rotulos: Dict[Tuple[int, int], dict] = {}

    for resultado, prova, turma in dados:
        for detalhe in resultado.detalhamento or []:
            numero = int(detalhe.get("questao", 0))
            chave = (prova.id, numero)
            por_questao[chave].registrar(detalhe)

            if chave not in rotulos:
                questao = mapa.get(chave)
                rotulos[chave] = {
                    "prova_id": prova.id,
                    "prova": prova.titulo,
                    "disciplina": prova.disciplina,
                    "turma": turma.nome,
                    "ano_escolar": escolaridade.nome_do(turma.ano_escolar),
                    "questao": numero,
                    "conteudo": (questao.conteudo if questao else "") or "",
                    "habilidade": (questao.habilidade if questao else "") or "",
                    "correta": detalhe.get("correta"),
                }

    itens = []
    for chave, contagem in por_questao.items():
        rotulo = rotulos[chave]
        correta = rotulo.get("correta")

        erradas = {
            letra: quantidade
            for letra, quantidade in contagem.marcacoes.items()
            if letra != correta
        }
        distrator = None
        total_erros = sum(erradas.values())
        if erradas and contagem.respondidas and total_erros:
            letra, quantidade = max(erradas.items(), key=lambda par: par[1])
            fatia_turma = round(100 * quantidade / contagem.respondidas, 1)
            fatia_erros = round(100 * quantidade / total_erros, 1)

            # Três condições juntas. Só a fatia da turma não basta: com
            # poucos alunos, o acaso concentra o suficiente para passar
            # sozinho e o sistema anunciaria "erro conceitual" onde há
            # apenas chute.
            concentrado = (
                fatia_erros >= LIMIAR_FATIA_DOS_ERROS
                and fatia_turma >= LIMIAR_DISTRATOR
                and contagem.respondidas >= MINIMO_ILUSTRACAO
            )

            distrator = {
                "alternativa": letra,
                "escolhas": quantidade,
                "percentual": fatia_turma,
                "percentual_dos_erros": fatia_erros,
                "concentrado": concentrado,
            }

        itens.append(
            {
                **rotulo,
                "percentual_acerto": contagem.percentual_acerto,
                "respostas": contagem.respondidas,
                "em_branco": contagem.em_branco,
                "situacao": _classificar(contagem),
                "confiavel": contagem.confiavel,
                "distrator": distrator,
            }
        )

    itens.sort(key=lambda i: (i["percentual_acerto"] is None, i["percentual_acerto"]))
    return itens


# ==================================================================
# COMPARACAO ENTRE ANOS ESCOLARES
# ==================================================================
def comparativo_por_ano(sessao: Session, escola_id: int, **filtros) -> List[dict]:
    """
    Média e conteúdo mais frágil de cada ano escolar da escola.

    É a visão que a coordenação usa: mostra onde concentrar formação e
    material, em vez de olhar turma a turma.
    """
    dados = _resultados_filtrados(sessao, escola_id=escola_id, **filtros)
    mapa = _mapa_de_conteudos(sessao, [prova.id for _, prova, _ in dados])

    por_ano: Dict[str, Contagem] = defaultdict(Contagem)
    conteudo_por_ano: Dict[str, Dict[str, Contagem]] = defaultdict(
        lambda: defaultdict(Contagem)
    )
    notas_por_ano: Dict[str, List[float]] = defaultdict(list)
    turmas_por_ano: Dict[str, set] = defaultdict(set)
    alunos_por_ano: Dict[str, set] = defaultdict(set)

    for resultado, prova, turma in dados:
        codigo = turma.ano_escolar or "OUTRO"
        notas_por_ano[codigo].append(resultado.nota)
        turmas_por_ano[codigo].add(turma.id)
        alunos_por_ano[codigo].add(resultado.aluno_id)

        for detalhe in resultado.detalhamento or []:
            por_ano[codigo].registrar(detalhe)
            questao = mapa.get((prova.id, int(detalhe.get("questao", 0))))
            if questao and questao.conteudo:
                conteudo_por_ano[codigo][questao.conteudo].registrar(detalhe)

    saida = []
    for codigo, contagem in por_ano.items():
        conteudos = conteudo_por_ano[codigo]
        # Só entram na indicação os conteúdos com amostra suficiente.
        candidatos = [
            (nome, c) for nome, c in conteudos.items() if c.confiavel
        ] or list(conteudos.items())

        pior = min(
            candidatos,
            key=lambda par: par[1].percentual_acerto
            if par[1].percentual_acerto is not None
            else 101,
            default=None,
        )

        notas = notas_por_ano[codigo]
        saida.append(
            {
                "ano_escolar": codigo,
                "nome": escolaridade.nome_do(codigo),
                "etapa": escolaridade.etapa_do(codigo),
                "turmas": len(turmas_por_ano[codigo]),
                "alunos": len(alunos_por_ano[codigo]),
                "media": round(sum(notas) / len(notas), 2) if notas else None,
                "percentual_acerto": contagem.percentual_acerto,
                "respostas": contagem.respondidas,
                "confiavel": contagem.confiavel,
                "conteudo_mais_fragil": (
                    {
                        "conteudo": pior[0],
                        "percentual_acerto": pior[1].percentual_acerto,
                        "respostas": pior[1].respondidas,
                        "confiavel": pior[1].confiavel,
                    }
                    if pior
                    else None
                ),
            }
        )

    saida.sort(key=lambda item: escolaridade.ordem_do(item["ano_escolar"]))
    return saida


# ==================================================================
# ORIENTACOES
# ==================================================================
def gerar_orientacoes(
    sessao: Session, escola_id: int, limite: int = 6, **filtros
) -> List[dict]:
    """
    Monta as frases que a escola vai receber.

    Regra que vale mais que o texto: só é gerada orientação quando há
    amostra suficiente. Recomendar reforço de um conteúdo com base em
    doze respostas destruiria a confiança no sistema na primeira vez
    que a escola olhasse os dados de perto.
    """
    escola = sessao.get(Escola, escola_id)
    nome_escola = escola.nome if escola else "a escola"

    orientacoes: List[dict] = []

    for ano in comparativo_por_ano(sessao, escola_id, **filtros):
        codigo = ano["ano_escolar"]
        analise = analise_por_conteudo(
            sessao, escola_id=escola_id, ano_escolar=codigo, **filtros
        )

        for item in analise["conteudos"]:
            if item["situacao"] not in ("critico", "atencao"):
                continue
            if not item["confiavel"]:
                continue

            questoes = analise_por_questao(
                sessao, escola_id=escola_id, ano_escolar=codigo, **filtros
            )
            # Aqui o mínimo é menor de propósito. O conteúdo já passou
            # pelo corte de confiabilidade; o distrator entra como
            # ILUSTRAÇÃO do erro, não como base da recomendação — e o
            # texto declara quantos alunos responderam, para o leitor
            # pesar a evidência.
            relacionadas = [
                q
                for q in questoes
                if q["conteudo"] == item["conteudo"]
                and q["respostas"] >= MINIMO_ILUSTRACAO
                and q.get("distrator")
                and q["distrator"]["concentrado"]
            ]
            pior_questao = relacionadas[0] if relacionadas else None

            texto = (
                f"{ano['nome']} de {nome_escola}: {item['conteudo']} teve "
                f"{item['percentual_acerto']}% de acerto em {item['questoes']} "
                f"questão(ões), com {item['respostas']} respostas. "
            )
            texto += (
                "Recomenda-se retomada prioritária do conteúdo."
                if item["situacao"] == "critico"
                else "Vale reforçar o conteúdo nas próximas aulas."
            )

            if pior_questao:
                distrator = pior_questao["distrator"]
                texto += (
                    f" Na questão {pior_questao['questao']} da prova "
                    f"\"{pior_questao['prova']}\" (resposta certa: "
                    f"{pior_questao['correta']}), {distrator['percentual']}% dos "
                    f"{pior_questao['respostas']} alunos marcaram "
                    f"{distrator['alternativa']} — a concentração num mesmo erro "
                    f"sugere equívoco conceitual, e não chute."
                )

            orientacoes.append(
                {
                    "ano_escolar": codigo,
                    "ano_nome": ano["nome"],
                    "conteudo": item["conteudo"],
                    "percentual_acerto": item["percentual_acerto"],
                    "respostas": item["respostas"],
                    "prioridade": item["situacao"],
                    "texto": texto,
                }
            )

    ordem = {"critico": 0, "atencao": 1}
    orientacoes.sort(
        key=lambda o: (ordem.get(o["prioridade"], 2), o["percentual_acerto"])
    )
    return orientacoes[:limite]


# ==================================================================
# PAINEL COMPLETO
# ==================================================================
def painel_da_escola(
    sessao: Session,
    escola_id: int,
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    desde: Optional[datetime] = None,
    limite_questoes: int = 15,
) -> dict:
    """Junta tudo o que a tela de análise precisa, em uma chamada."""
    escola = sessao.get(Escola, escola_id)
    filtros = {"disciplina": disciplina, "desde": desde}

    conteudos = analise_por_conteudo(
        sessao, escola_id=escola_id, ano_escolar=ano_escolar, **filtros
    )
    questoes = analise_por_questao(
        sessao, escola_id=escola_id, ano_escolar=ano_escolar, **filtros
    )
    comparativo = comparativo_por_ano(sessao, escola_id, **filtros)
    orientacoes = gerar_orientacoes(sessao, escola_id, **filtros)

    disciplinas = sorted(
        {
            prova.disciplina
            for _, prova, _ in _resultados_filtrados(sessao, escola_id=escola_id)
            if prova.disciplina
        }
    )

    return {
        "escola": escola.nome if escola else "",
        "escola_id": escola_id,
        "filtro_ano": ano_escolar,
        "filtro_disciplina": disciplina,
        "disciplinas": disciplinas,
        "total_respostas": conteudos["total_respostas"],
        "questoes_sem_conteudo": conteudos["questoes_sem_conteudo"],
        "minimo_confiavel": MINIMO_RESPOSTAS_CONFIAVEL,
        "conteudos": conteudos["conteudos"],
        "questoes": questoes[:limite_questoes],
        "comparativo_anos": comparativo,
        "orientacoes": orientacoes,
    }
