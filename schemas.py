"""
schemas.py
------------------------------------------------------------------
Modelos Pydantic (v2) que validam a entrada e serializam a resposta
da API.

Os modulos de dominio (`omr_engine`, `gerador`) nao conhecem FastAPI
nem HTTP: devolvem estruturas simples que sao validadas aqui.
------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==================================================================
# ENUMS
# ==================================================================
class StatusQuestao(str, Enum):
    """Situacao final de cada questao apos a correcao."""

    CORRETO = "correto"
    INCORRETO = "incorreto"
    EM_BRANCO = "em_branco"   # nenhuma bolha preenchida acima do limiar
    RASURA = "rasura"         # duas ou mais bolhas com preenchimento similar


class FormatoCartao(str, Enum):
    """Formato de saida do cartao gerado."""

    PDF = "pdf"
    PNG = "png"


# ==================================================================
# SAIDA DA CORRECAO
# ==================================================================
class DetalheQuestao(BaseModel):
    """Resultado individual de uma questao."""

    questao: int = Field(..., ge=1)
    marcada: Optional[str] = Field(
        None, description="Alternativa detectada. None se em branco ou rasura."
    )
    correta: Optional[str] = Field(None, description="Alternativa do gabarito.")
    status: StatusQuestao
    confianca: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Densidade de preenchimento (0..1) da bolha vencedora.",
    )


class RespostaCorrecao(BaseModel):
    """Payload de sucesso da correcao de um cartao."""

    sucesso: bool = True
    prova_id: str
    nota: float = Field(..., description="Nota final na escala 0..10.")
    total_questoes: int
    acertos: int
    erros: int = 0
    em_branco: int = 0
    rasuras: int = 0
    alinhamento: str = Field(
        "ancoras",
        description="Metodo de retificacao: 'ancoras', 'contorno_pagina' ou 'fallback_resize'.",
    )
    origem_gabarito: str = Field(
        "informado",
        description="'imagem' se o QR foi lido da foto, 'informado' se veio digitado.",
    )
    assinatura: str = Field(
        "ausente",
        description=(
            "Situacao da assinatura do QR: 'valida', 'ausente' (cartao antigo) "
            "ou 'nao_conferida'. Assinatura 'invalida' barra a correcao."
        ),
    )
    tempo_processamento_ms: float = 0.0
    detalhamento: List[DetalheQuestao] = []
    # Preenchido apenas na correcao em lote, para o professor saber a
    # qual arquivo cada resultado corresponde.
    arquivo: Optional[str] = None

    # --- Identificacao vinda do QR do cartao nominal ---------------
    aluno_id: Optional[int] = None
    aluno_nome: Optional[str] = None
    salvo: bool = Field(
        False, description="True quando a nota foi gravada no banco."
    )
    motivo_nao_salvo: Optional[str] = None

    # Persistencia: `salvo` so e verdadeiro quando escola, turma e aluno
    # foram informados. Corrigir sem identificar continua funcionando —
    # o resultado apenas nao entra no boletim.
    salvo: bool = False
    identificacao: Optional[Identificacao] = None


class ItemLote(BaseModel):
    """Um cartao dentro de uma correcao em lote (sucesso ou falha)."""

    arquivo: str
    sucesso: bool
    resultado: Optional[RespostaCorrecao] = None
    erro: Optional[str] = None


class RespostaLote(BaseModel):
    """Resultado de corrigir varios cartoes de uma vez."""

    sucesso: bool = True
    total_enviados: int
    total_corrigidos: int
    total_falhas: int
    media_da_turma: Optional[float] = None
    itens: List[ItemLote] = []


# ==================================================================
# SAIDA DA GERACAO DE CARTAO
# ==================================================================
class RespostaGabaritoLido(BaseModel):
    """Devolvido ao ler o QR de uma imagem, sem corrigir nada."""

    sucesso: bool = True
    prova_id: str
    total_questoes: int
    gabarito: List[str] = Field(
        ..., description="Respostas em ordem, da questao 1 ate a ultima."
    )


# ==================================================================
# ERROS
# ==================================================================
class Identificacao(BaseModel):
    """A quem pertence um resultado gravado."""

    escola: Optional[str] = None
    turma: Optional[str] = None
    aluno: Optional[str] = None


class EscolaResumo(BaseModel):
    id: int
    nome: str
    turmas: int = 0


class TurmaResumo(BaseModel):
    id: int
    nome: str
    escola_id: int
    escola: str
    alunos: int = 0


class AlunoResumo(BaseModel):
    id: int
    nome: str


class ProvaResumo(BaseModel):
    id: int
    codigo: str
    titulo: str = ""
    disciplina: str = ""
    total_questoes: int = 0
    corrigidas: int = 0


class LinhaBoletim(BaseModel):
    """Uma linha achatada do boletim, pronta para tabela ou CSV."""

    escola: str
    turma: str
    aluno: str
    prova: str
    titulo: str = ""
    nota: float
    acertos: int
    erros: int
    em_branco: int
    rasuras: int
    total_questoes: int
    respostas: List[Optional[str]] = []
    gabarito: List[str] = []
    alinhamento: str = ""
    corrigido_em: str


class RespostaBoletim(BaseModel):
    sucesso: bool = True
    total: int
    media: Optional[float] = None
    linhas: List[LinhaBoletim] = []


class RespostaErro(BaseModel):
    """Payload padronizado de erro."""

    sucesso: bool = False
    erro: str
    detalhe: Optional[str] = None


# ==================================================================
# CADASTRO (escola / turma / aluno)
# ==================================================================
class EscolaResposta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str


class TurmaResposta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    escola_id: int
    nome: str
    # Codigo estruturado ("EF2"), separado do apelido da turma ("2º B").
    # E o que permite comparar o mesmo ano entre escolas.
    ano_escolar: Optional[str] = None
    ano_escolar_nome: str = ""
    ano_letivo: Optional[int] = None
    total_alunos: int = 0


class AlunoResposta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    turma_id: int
    nome: str
    matricula: Optional[str] = None


# ==================================================================
# PROVAS E BOLETIM
# ==================================================================
class ProvaResposta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    turma_id: int
    titulo: str
    disciplina: str = ""
    total_questoes: int
    criada_em: datetime


class LinhaBoletim(BaseModel):
    """Uma linha do boletim da turma."""

    aluno_id: int
    aluno_nome: str
    nota: Optional[float] = None
    acertos: Optional[int] = None
    erros: Optional[int] = None
    em_branco: Optional[int] = None
    rasuras: Optional[int] = None
    corrigido_em: Optional[datetime] = None
    # Alunos ainda sem folha corrigida aparecem com `corrigido` False,
    # para o professor ver quem falta em vez de sumir da lista.
    corrigido: bool = False
    detalhamento: List[DetalheQuestao] = []


class AcertoQuestao(BaseModel):
    """Desempenho da turma numa questao."""

    questao: int
    acertos: int
    percentual: int
    correta: Optional[str] = None


class Boletim(BaseModel):
    """Visao consolidada de uma prova."""

    prova: ProvaResposta
    turma: str
    escola: str
    total_alunos: int
    total_corrigidos: int
    media: Optional[float] = None
    maior_nota: Optional[float] = None
    menor_nota: Optional[float] = None
    acerto_por_questao: List[AcertoQuestao] = []
    linhas: List[LinhaBoletim] = []


# ==================================================================
# HISTORICO
# ==================================================================
class AvaliacaoDoAluno(BaseModel):
    """Uma prova na linha do tempo do aluno."""

    prova_id: int
    titulo: str
    disciplina: str = ""
    data: Optional[datetime] = None
    nota: float
    acertos: int
    total_questoes: int


class HistoricoAluno(BaseModel):
    """Desempenho de um aluno ao longo do ano."""

    aluno_id: int
    aluno_nome: str
    turma: str
    escola: str
    total_provas: int
    media: Optional[float] = None
    maior_nota: Optional[float] = None
    menor_nota: Optional[float] = None
    # "subindo" | "caindo" | "estavel" | None (poucas provas para dizer)
    tendencia: Optional[str] = None
    avaliacoes: List[AvaliacaoDoAluno] = []


class MediaDaProva(BaseModel):
    prova_id: int
    titulo: str
    media: Optional[float] = None
    corrigidas: int = 0


class LinhaDesempenho(BaseModel):
    aluno_id: int
    aluno_nome: str
    # Uma posicao por prova, na mesma ordem de `provas`. None = sem folha.
    notas: List[Optional[float]] = []
    media: Optional[float] = None
    provas_feitas: int = 0


class DesempenhoTurma(BaseModel):
    """Grade de notas da turma: alunos nas linhas, provas nas colunas."""

    turma: str
    escola: str
    provas: List[MediaDaProva] = []
    alunos: List[LinhaDesempenho] = []


# ==================================================================
# ANALISE PEDAGOGICA
# ==================================================================
class AnoEscolarOpcao(BaseModel):
    """Item do seletor de ano escolar."""

    codigo: str
    nome: str
    etapa: str
    ordem: int


class QuestaoResposta(BaseModel):
    """Uma questao da prova, com o conteudo que cobra."""

    model_config = ConfigDict(from_attributes=True)

    numero: int
    correta: str
    conteudo: str = ""
    habilidade: str = ""


class ConteudoAnalisado(BaseModel):
    """Desempenho agregado de um conteudo."""

    conteudo: str
    percentual_acerto: Optional[float] = None
    corretas: int = 0
    incorretas: int = 0
    em_branco: int = 0
    respostas: int = 0
    questoes: int = 0
    provas: int = 0
    # "critico" | "atencao" | "adequado" | "sem_dados"
    situacao: str = "sem_dados"
    # False quando a amostra e pequena demais para sustentar conclusao
    confiavel: bool = False


class Distrator(BaseModel):
    """A alternativa errada mais escolhida numa questao."""

    alternativa: str
    escolhas: int
    # Fatia da turma inteira que marcou esta alternativa
    percentual: float
    # Fatia dos ERROS que caiu nesta alternativa. Com 5 alternativas, o
    # chute espalha os erros em ~25% para cada errada; muito acima
    # disso indica um raciocinio errado compartilhado.
    percentual_dos_erros: float = 0.0
    concentrado: bool = False


class QuestaoAnalisada(BaseModel):
    prova_id: int
    prova: str
    disciplina: str = ""
    turma: str = ""
    ano_escolar: str = ""
    questao: int
    conteudo: str = ""
    habilidade: str = ""
    correta: Optional[str] = None
    percentual_acerto: Optional[float] = None
    respostas: int = 0
    em_branco: int = 0
    situacao: str = "sem_dados"
    confiavel: bool = False
    distrator: Optional[Distrator] = None


class ConteudoFragil(BaseModel):
    conteudo: str
    percentual_acerto: Optional[float] = None
    respostas: int = 0
    confiavel: bool = False


class AnoComparado(BaseModel):
    """Uma linha do comparativo entre anos escolares da escola."""

    ano_escolar: str
    nome: str
    etapa: str = ""
    turmas: int = 0
    alunos: int = 0
    media: Optional[float] = None
    percentual_acerto: Optional[float] = None
    respostas: int = 0
    confiavel: bool = False
    conteudo_mais_fragil: Optional[ConteudoFragil] = None


class Orientacao(BaseModel):
    """Frase pronta para a escola, com a evidencia que a sustenta."""

    ano_escolar: str
    ano_nome: str
    conteudo: str
    percentual_acerto: Optional[float] = None
    respostas: int = 0
    prioridade: str
    texto: str


class PainelAnalise(BaseModel):
    """Tudo o que a tela de analise mostra."""

    escola: str
    escola_id: int
    filtro_ano: Optional[str] = None
    filtro_disciplina: Optional[str] = None
    disciplinas: List[str] = []
    total_respostas: int = 0
    # Questoes sem conteudo cadastrado ficam de fora da analise por
    # conteudo; o numero e exibido para o professor saber o que falta
    # classificar.
    questoes_sem_conteudo: int = 0
    minimo_confiavel: int = 25
    conteudos: List[ConteudoAnalisado] = []
    questoes: List[QuestaoAnalisada] = []
    comparativo_anos: List[AnoComparado] = []
    orientacoes: List[Orientacao] = []


# ==================================================================
# COMPARACAO ENTRE ESCOLAS
# ==================================================================
class EscolaComparada(BaseModel):
    """
    Os números de uma escola no recorte escolhido.

    `margem_erro` e `comparavel` não são enfeite: uma escola com 18
    alunos e 71% de acerto tem margem de ±10 pontos, e exibir o 71%
    sozinho convida a uma conclusão que o dado não sustenta.
    """

    escola_id: int
    escola: str
    alunos: int = 0
    turmas: int = 0
    provas: int = 0
    respostas: int = 0
    corretas: int = 0
    media: Optional[float] = None
    percentual_acerto: Optional[float] = None
    # Em pontos percentuais, para 95% de confiança
    margem_erro: Optional[float] = None
    comparavel: bool = False


class CelulaConteudo(BaseModel):
    """Desempenho de uma escola num conteúdo."""

    escola_id: int
    escola: str
    percentual_acerto: Optional[float] = None
    respostas: int = 0
    margem_erro: Optional[float] = None
    confiavel: bool = False


class VaoDoConteudo(BaseModel):
    """Distância entre a melhor e a pior escola num conteúdo."""

    diferenca: float
    # False quando a diferença cabe dentro do acaso das amostras
    significativa: bool
    melhor: str
    pior: str


class LinhaConteudoComparada(BaseModel):
    conteudo: str
    celulas: List[CelulaConteudo] = []
    vao: Optional[VaoDoConteudo] = None


class DiferencaEntreEscolas(BaseModel):
    escola_a: str
    escola_b: str
    diferenca: float
    significativa: bool
    # Frase pronta, que diz "empate dentro da margem" quando é o caso
    leitura: str


class RecorteComparativo(BaseModel):
    ano_escolar: str = "todos"
    disciplina: str = "todas"
    minimo_comparavel: int = 30


class ComparativoEscolas(BaseModel):
    escolas: List[EscolaComparada] = []
    conteudos: List[LinhaConteudoComparada] = []
    diferencas: List[DiferencaEntreEscolas] = []
    recorte: RecorteComparativo = RecorteComparativo()
    avisos: List[str] = []


# ==================================================================
# CONTAS
# ==================================================================
class UsuarioResposta(BaseModel):
    """Uma conta, como aparece na tela de administração."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
    # "admin" | "membro" | "convidado"
    papel: str
    papel_nome: str = ""
    # Preenchido só em conta de escola: é o escopo dela.
    escola_id: Optional[int] = None
    escola: Optional[str] = None
    criado_em: Optional[datetime] = None
    ultimo_acesso: Optional[datetime] = None


# ==================================================================
# RELATORIO
# ==================================================================
class CorrecaoDoRelatorio(BaseModel):
    """
    Uma folha corrigida, como o navegador guardou.

    Campos extras sao ignorados: o navegador manda a resposta inteira
    da correcao, e exigir que ela case exatamente com este modelo
    quebraria o relatorio a cada campo novo na correcao.
    """

    model_config = ConfigDict(extra="ignore")

    aluno_nome: Optional[str] = None
    arquivo: Optional[str] = None
    nota: float = 0.0
    acertos: int = 0
    total_questoes: int = 0
    erros: int = 0
    em_branco: int = 0
    rasuras: int = 0
    detalhamento: List[dict] = []


class PedidoRelatorio(BaseModel):
    """
    O pedido chega como JSON, e nao como campo de formulario.

    Um formulario multipart tem limite de tamanho por campo — cerca de
    1 MB — e a turma grande passava disso. O campo era truncado no meio,
    o JSON deixava de fechar e o professor recebia "nenhuma correcao"
    sem entender por que.
    """

    correcoes: List[CorrecaoDoRelatorio]
    titulo: str = "Relatório da turma"
    professor: str = ""
    turma: str = ""
    disciplina: str = ""
    formato: str = "pdf"
