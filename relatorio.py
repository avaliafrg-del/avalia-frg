"""
relatorio.py
------------------------------------------------------------------
O documento que o convidado leva embora.

O papel de convidado existe para o professor que quer corrigir a
própria turma sem que nada fique guardado no sistema. Isso resolve a
privacidade, mas cria um problema: sem banco, o resultado desaparece
quando ele fecha a aba.

Este módulo fecha esse buraco. Recebe as correções que o navegador
acumulou durante a sessão e devolve um PDF com as notas, a média da
turma e o acerto por questão — o mesmo conteúdo que um usuário com
banco encontraria no boletim, só que para baixar.

Desenhado com PIL, como o cartão-resposta, em vez de uma biblioteca de
relatório: o projeto já depende do PIL, e a página aqui é simples o
bastante para não justificar mais uma dependência.
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

# A4 a 150 dpi, a mesma escala do cartão-resposta.
LARGURA, ALTURA = 1240, 1754
MARGEM = 90

PRETO = (22, 32, 46)
CINZA = (93, 107, 122)
CINZA_CLARO = (211, 219, 228)
AZUL = (22, 70, 157)
VERMELHO = (196, 43, 51)
VERDE = (27, 122, 78)
BRANCO = (255, 255, 255)

CAMINHOS_FONTE = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
CAMINHOS_FONTE_NEGRITO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _fonte(tamanho: int, negrito: bool = False):
    for caminho in (CAMINHOS_FONTE_NEGRITO if negrito else CAMINHOS_FONTE):
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()


# ==================================================================
# DADOS
# ==================================================================
@dataclass
class LinhaRelatorio:
    """Uma folha corrigida."""

    identificacao: str          # nome do aluno, ou o arquivo da foto
    nota: float
    acertos: int
    total_questoes: int
    erros: int = 0
    em_branco: int = 0
    rasuras: int = 0
    detalhamento: List[dict] = field(default_factory=list)


@dataclass
class DadosRelatorio:
    titulo: str = "Relatório da turma"
    professor: str = ""
    turma: str = ""
    disciplina: str = ""
    linhas: List[LinhaRelatorio] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.linhas)

    @property
    def media(self) -> Optional[float]:
        if not self.linhas:
            return None
        return round(sum(l.nota for l in self.linhas) / len(self.linhas), 2)

    @property
    def total_questoes(self) -> int:
        return max((l.total_questoes for l in self.linhas), default=0)

    def acerto_por_questao(self) -> List[dict]:
        """
        Percentual de acerto de cada questão.

        É o dado que transforma a lista de notas em informação de aula:
        mostra o que a turma não aprendeu, não só quem foi mal.
        """
        if not self.linhas:
            return []

        acertos: Dict[int, int] = {}
        respostas: Dict[int, int] = {}

        for linha in self.linhas:
            for detalhe in linha.detalhamento or []:
                numero = int(detalhe.get("questao", 0))
                if not numero:
                    continue
                situacao = detalhe.get("status")
                # Em branco não conta como erro: não saber e não ter
                # chegado na questão são coisas diferentes.
                if situacao in ("correto", "incorreto"):
                    respostas[numero] = respostas.get(numero, 0) + 1
                    if situacao == "correto":
                        acertos[numero] = acertos.get(numero, 0) + 1

        return [
            {
                "questao": numero,
                "acertos": acertos.get(numero, 0),
                "respostas": respostas[numero],
                "percentual": round(100 * acertos.get(numero, 0) / respostas[numero]),
            }
            for numero in sorted(respostas)
        ]


# ==================================================================
# DESENHO
# ==================================================================
def _cabecalho(pincel, dados: DadosRelatorio, y: int) -> int:
    pincel.text((MARGEM, y), dados.titulo, font=_fonte(38, True), fill=PRETO)
    y += 52

    partes = [p for p in (dados.disciplina, dados.turma, dados.professor) if p]
    if partes:
        pincel.text((MARGEM, y), " · ".join(partes), font=_fonte(21), fill=CINZA)
        y += 32

    momento = datetime.now().strftime("%d/%m/%Y às %H:%M")
    pincel.text((MARGEM, y), f"Gerado em {momento}", font=_fonte(17), fill=CINZA)
    y += 40

    pincel.line([(MARGEM, y), (LARGURA - MARGEM, y)], fill=CINZA_CLARO, width=2)
    return y + 34


def _resumo(pincel, dados: DadosRelatorio, y: int) -> int:
    """Os três números que o professor olha primeiro."""
    notas = [l.nota for l in dados.linhas]
    blocos = [
        (f"{dados.media:.1f}" if dados.media is not None else "—", "média da turma"),
        (str(dados.total), "folhas corrigidas"),
        (f"{max(notas):.1f}" if notas else "—", "maior nota"),
        (f"{min(notas):.1f}" if notas else "—", "menor nota"),
    ]

    x = MARGEM
    for valor, rotulo in blocos:
        pincel.text((x, y), valor, font=_fonte(34, True), fill=PRETO)
        pincel.text((x, y + 44), rotulo, font=_fonte(16), fill=CINZA)
        x += 250

    return y + 92


def _tabela_notas(pincel, dados: DadosRelatorio, y: int) -> int:
    pincel.text((MARGEM, y), "Notas", font=_fonte(24, True), fill=PRETO)
    y += 42

    colunas = [
        (MARGEM, "Aluno"),
        (MARGEM + 560, "Acertos"),
        (MARGEM + 720, "Branco"),
        (MARGEM + 860, "Rasura"),
        (MARGEM + 990, "Nota"),
    ]
    for x, titulo in colunas:
        pincel.text((x, y), titulo.upper(), font=_fonte(14, True), fill=CINZA)
    y += 26
    pincel.line([(MARGEM, y), (LARGURA - MARGEM, y)], fill=CINZA_CLARO, width=1)
    y += 12

    for linha in sorted(dados.linhas, key=lambda l: l.identificacao.lower()):
        pincel.text(
            (MARGEM, y), linha.identificacao[:46], font=_fonte(19), fill=PRETO
        )
        pincel.text(
            (MARGEM + 560, y),
            f"{linha.acertos}/{linha.total_questoes}",
            font=_fonte(19),
            fill=PRETO,
        )
        pincel.text((MARGEM + 720, y), str(linha.em_branco), font=_fonte(19), fill=CINZA)
        pincel.text((MARGEM + 860, y), str(linha.rasuras), font=_fonte(19), fill=CINZA)

        cor_nota = VERDE if linha.nota >= 6 else VERMELHO
        pincel.text(
            (MARGEM + 990, y), f"{linha.nota:.1f}", font=_fonte(19, True), fill=cor_nota
        )
        y += 32

    return y + 20


def _acerto_por_questao(pincel, dados: DadosRelatorio, y: int) -> int:
    questoes = dados.acerto_por_questao()
    if not questoes:
        return y

    pincel.text((MARGEM, y), "Acerto por questão", font=_fonte(24, True), fill=PRETO)
    y += 32
    pincel.text(
        (MARGEM, y),
        "Onde a turma tropeçou. Abaixo de 50% vale retomar o conteúdo.",
        font=_fonte(17),
        fill=CINZA,
    )
    y += 40

    largura_barra = 560
    for questao in questoes:
        pincel.text(
            (MARGEM, y), f"{questao['questao']:02d}", font=_fonte(18, True), fill=PRETO
        )

        # Trilha e barra: a leitura visual encontra a questão fraca sem
        # precisar comparar números um a um.
        inicio = MARGEM + 60
        pincel.rounded_rectangle(
            [inicio, y + 4, inicio + largura_barra, y + 16], 6, fill=(234, 238, 243)
        )
        preenchido = int(largura_barra * questao["percentual"] / 100)
        if preenchido > 0:
            pincel.rounded_rectangle(
                [inicio, y + 4, inicio + preenchido, y + 16],
                6,
                fill=VERMELHO if questao["percentual"] < 50 else VERDE,
            )

        pincel.text(
            (inicio + largura_barra + 20, y),
            f"{questao['percentual']}%",
            font=_fonte(18, True),
            fill=PRETO,
        )
        pincel.text(
            (inicio + largura_barra + 100, y + 2),
            f"{questao['acertos']} de {questao['respostas']}",
            font=_fonte(15),
            fill=CINZA,
        )
        y += 30

    return y


def _rodape(pincel, pagina: int, total_paginas: int) -> None:
    texto = f"Avalia FRG · Secretaria Municipal de Educação · página {pagina} de {total_paginas}"
    pincel.text((MARGEM, ALTURA - 60), texto, font=_fonte(15), fill=CINZA_CLARO)


# ==================================================================
# MONTAGEM
# ==================================================================
def gerar_paginas(dados: DadosRelatorio) -> List[Image.Image]:
    """
    Monta as páginas do relatório.

    Quebra de página é feita pela altura ocupada, e não por um número
    fixo de linhas: a seção de questões varia com o tamanho da prova, e
    contar linhas cortaria a tabela no meio em provas longas.
    """
    paginas: List[Image.Image] = []

    pagina = Image.new("RGB", (LARGURA, ALTURA), BRANCO)
    pincel = ImageDraw.Draw(pagina)

    y = _cabecalho(pincel, dados, MARGEM)
    y = _resumo(pincel, dados, y)

    # --- notas, quebrando quando a página acaba --------------------
    pincel.text((MARGEM, y), "Notas", font=_fonte(24, True), fill=PRETO)
    y += 42
    for x, titulo in [
        (MARGEM, "ALUNO"),
        (MARGEM + 560, "ACERTOS"),
        (MARGEM + 720, "BRANCO"),
        (MARGEM + 860, "RASURA"),
        (MARGEM + 990, "NOTA"),
    ]:
        pincel.text((x, y), titulo, font=_fonte(14, True), fill=CINZA)
    y += 26
    pincel.line([(MARGEM, y), (LARGURA - MARGEM, y)], fill=CINZA_CLARO, width=1)
    y += 12

    for linha in sorted(dados.linhas, key=lambda l: l.identificacao.lower()):
        if y > ALTURA - 140:
            paginas.append(pagina)
            pagina = Image.new("RGB", (LARGURA, ALTURA), BRANCO)
            pincel = ImageDraw.Draw(pagina)
            y = MARGEM

        pincel.text((MARGEM, y), linha.identificacao[:46], font=_fonte(19), fill=PRETO)
        pincel.text(
            (MARGEM + 560, y),
            f"{linha.acertos}/{linha.total_questoes}",
            font=_fonte(19),
            fill=PRETO,
        )
        pincel.text((MARGEM + 720, y), str(linha.em_branco), font=_fonte(19), fill=CINZA)
        pincel.text((MARGEM + 860, y), str(linha.rasuras), font=_fonte(19), fill=CINZA)
        pincel.text(
            (MARGEM + 990, y),
            f"{linha.nota:.1f}",
            font=_fonte(19, True),
            fill=VERDE if linha.nota >= 6 else VERMELHO,
        )
        y += 32

    y += 30

    # --- acerto por questão ----------------------------------------
    if dados.acerto_por_questao():
        if y > ALTURA - 400:
            paginas.append(pagina)
            pagina = Image.new("RGB", (LARGURA, ALTURA), BRANCO)
            pincel = ImageDraw.Draw(pagina)
            y = MARGEM
        _acerto_por_questao(pincel, dados, y)

    paginas.append(pagina)

    for numero, folha in enumerate(paginas, start=1):
        _rodape(ImageDraw.Draw(folha), numero, len(paginas))

    return paginas


def gerar_pdf(dados: DadosRelatorio) -> bytes:
    """O relatório completo, pronto para baixar."""
    paginas = gerar_paginas(dados)
    buffer = io.BytesIO()
    paginas[0].save(
        buffer,
        format="PDF",
        resolution=150.0,
        save_all=True,
        append_images=paginas[1:],
    )
    return buffer.getvalue()


def gerar_csv(dados: DadosRelatorio) -> bytes:
    """
    As mesmas notas em planilha.

    O PDF serve para arquivar e entregar; o CSV, para o professor somar
    com outras avaliações no caderno dele.
    """
    linhas = ["aluno;nota;acertos;total;erros;em_branco;rasuras"]
    for linha in sorted(dados.linhas, key=lambda l: l.identificacao.lower()):
        linhas.append(
            ";".join(
                str(campo)
                for campo in [
                    linha.identificacao,
                    linha.nota,
                    linha.acertos,
                    linha.total_questoes,
                    linha.erros,
                    linha.em_branco,
                    linha.rasuras,
                ]
            )
        )
    # BOM para o Excel abrir os acentos corretamente
    return ("\ufeff" + "\n".join(linhas)).encode("utf-8")
