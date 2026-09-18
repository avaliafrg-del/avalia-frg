"""
estampa.py
------------------------------------------------------------------
Adiciona âncoras e QR Code ao cartão-resposta DO PROFESSOR, sem mexer
no que ele desenhou.

O pedido "só adicione o QR Code" esbarra num detalhe: o QR identifica
a prova e o aluno, mas não diz onde estão as bolhas nem permite
endireitar uma foto torta. Por isso a estampa coloca duas coisas:

    - os 4 quadrados pretos dos cantos (âncoras), que permitem
      retificar a perspectiva da foto;
    - o QR Code, que identifica prova e aluno.

Tudo o mais fica intacto: o layout, o cabeçalho, o espaçamento e as
bolhas continuam exatamente como o professor fez.

Onde as marcas são colocadas
----------------------------
Nas margens. Antes de estampar, o módulo confere se a faixa escolhida
está VAZIA na folha original — carimbar por cima de um texto do
professor seria pior que recusar. Se não houver espaço, a folha é
ampliada com uma borda branca em vez de sobrescrever conteúdo.
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

from omr_engine import OMRError, montar_qr_code

logger = logging.getLogger(__name__)

# ==================================================================
# GEOMETRIA DA ESTAMPA
# ==================================================================
# Tudo em pixels de uma folha renderizada a 150 DPI (A4 = 1240x1754).
DPI = 150
LADO_ANCORA = 46
# Distância do centro da âncora até a borda do papel.
# 88 px a 150 dpi = 15 mm. Era 58 (10 mm), e a impressora cortava: quase
# toda impressora doméstica tem margem não-imprimível de 10 a 13 mm no
# rodapé, e as duas âncoras de baixo saíam como tracinhos finos — o
# suficiente para o alinhamento falhar e a leitura sair torta.
MARGEM_ANCORA = 88
# Faixa livre exigida ao redor de cada âncora para não cobrir nada.
FOLGA_ANCORA = 12
LADO_QR = 210
MARGEM_QR = 26

BRANCO = (255, 255, 255)
PRETO = (0, 0, 0)
CINZA = (110, 110, 110)

CAMINHOS_FONTE = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def _fonte(tamanho: int):
    for caminho in CAMINHOS_FONTE:
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()


@dataclass
class ResultadoEstampa:
    """Folha estampada e o que foi preciso fazer para caber."""

    imagem: Image.Image
    # Centros das âncoras no espaço da imagem final. É o retângulo que
    # o motor vai retificar para 800x1000.
    ancoras: List[Tuple[int, int]]
    borda_adicionada: int = 0
    avisos: List[str] = None

    def __post_init__(self):
        if self.avisos is None:
            self.avisos = []


# ==================================================================
# LEITURA DO ARQUIVO DO PROFESSOR
# ==================================================================
def carregar_paginas(conteudo: bytes, nome: str = "") -> List[Image.Image]:
    """
    Converte o arquivo enviado em páginas de imagem a 150 DPI.

    Aceita PDF ou imagem, decidido pelo CONTEÚDO — navegador e sistema
    operacional erram o content-type com frequência.
    """
    if conteudo[:1024].find(b"%PDF-") != -1:
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            import fitz as pymupdf

        try:
            documento = pymupdf.open(stream=conteudo, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise OMRError(
                "Não consegui abrir o PDF do cartão-resposta. Ele pode estar "
                "corrompido ou protegido por senha."
            ) from exc

        if documento.needs_pass:
            raise OMRError(
                "O PDF está protegido por senha. Salve uma cópia sem proteção "
                "e envie de novo."
            )

        paginas = []
        escala = DPI / 72.0    # o PDF usa 72 pontos por polegada
        for pagina in documento:
            pixmap = pagina.get_pixmap(matrix=pymupdf.Matrix(escala, escala))
            paginas.append(
                Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            )
        if not paginas:
            raise OMRError("O PDF do cartão-resposta está vazio.")
        return paginas

    try:
        imagem = Image.open(io.BytesIO(conteudo))
        imagem.load()
        return [imagem.convert("RGB")]
    except Exception as exc:  # noqa: BLE001
        nome_arquivo = f" ({nome})" if nome else ""
        raise OMRError(
            f"Não consegui abrir o cartão-resposta{nome_arquivo}. Envie um PDF "
            f"ou uma imagem (JPG ou PNG)."
        ) from exc


# ==================================================================
# ESPACO LIVRE
# ==================================================================
def _regiao_esta_livre(imagem: Image.Image, caixa: Tuple[int, int, int, int]) -> bool:
    """
    True quando a região é praticamente branca.

    Carimbar por cima de algo que o professor escreveu seria pior que
    avisar que não coube.
    """
    x0, y0, x1, y1 = caixa
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(imagem.width, x1), min(imagem.height, y1)
    if x1 <= x0 or y1 <= y0:
        return False

    recorte = np.array(imagem.crop((x0, y0, x1, y1)).convert("L"))
    # 0,1% de pixels escuros. A tolerância anterior, de 0,5%, deixava
    # passar uma sobreposição de poucos pixels — e bastou isso, num
    # teste real, para o QR cortar a borda de uma bolha e derrubar a
    # questão inteira na detecção. Cobrir 4 px do cartão do professor
    # não é aceitável em nenhum caso.
    return float((recorte < 200).mean()) < 0.001


def _espaco_para_ancoras(imagem: Image.Image) -> bool:
    metade = LADO_ANCORA // 2 + FOLGA_ANCORA
    for cx, cy in _posicoes_ancoras(imagem.width, imagem.height):
        if not _regiao_esta_livre(
            imagem, (cx - metade, cy - metade, cx + metade, cy + metade)
        ):
            return False
    return True


def _posicoes_ancoras(largura: int, altura: int) -> List[Tuple[int, int]]:
    m = MARGEM_ANCORA
    return [
        (m, m),
        (largura - m, m),
        (largura - m, altura - m),
        (m, altura - m),
    ]


def _ampliar_com_borda(imagem: Image.Image, borda: int) -> Image.Image:
    """Aumenta a folha com margem branca, preservando o conteúdo."""
    nova = Image.new(
        "RGB", (imagem.width + 2 * borda, imagem.height + 2 * borda), BRANCO
    )
    nova.paste(imagem, (borda, borda))
    return nova


# ==================================================================
# ESTAMPA
# ==================================================================
def estampar(
    pagina: Image.Image,
    texto_qr: str,
    rotulo: str = "",
    aluno_nome: str = "",
) -> ResultadoEstampa:
    """
    Devolve a folha do professor com âncoras e QR adicionados.

    Nada do conteúdo original é apagado ou movido: quando as margens
    não têm espaço, a folha ganha uma borda branca e o conteúdo desce
    junto, inteiro.
    """
    imagem = pagina.convert("RGB")
    avisos: List[str] = []
    borda = 0

    if not _espaco_para_ancoras(imagem):
        borda = MARGEM_ANCORA + LADO_ANCORA
        imagem = _ampliar_com_borda(imagem, borda)
        avisos.append(
            "As margens do seu cartão não tinham espaço livre para as marcas de "
            "canto, então a folha ganhou uma borda branca. O conteúdo não foi "
            "alterado, mas o cartão ficou um pouco menor ao imprimir em A4."
        )

    pincel = ImageDraw.Draw(imagem)
    ancoras = _posicoes_ancoras(imagem.width, imagem.height)
    metade = LADO_ANCORA // 2

    for cx, cy in ancoras:
        pincel.rectangle(
            [cx - metade, cy - metade, cx + metade, cy + metade], fill=PRETO
        )

    if _encontrar_canto_qr(imagem) is None:
        # Nenhum canto comporta o QR sem encostar no conteúdo. Em vez de
        # carimbar por cima — que estraga o cartão e quebra a leitura da
        # questão coberta — a folha ganha uma faixa branca no topo. O
        # desenho do professor desce inteiro, sem nada apagado.
        faixa = LADO_QR + MARGEM_QR * 2
        imagem = _ampliar_faixa_superior(imagem, faixa)
        borda += faixa
        avisos.append(
            "Não havia canto livre para o QR Code, então a folha ganhou uma "
            "faixa branca no topo. Nada do seu cartão foi coberto, mas ele "
            "ficou um pouco menor ao imprimir em A4."
        )
        pincel = ImageDraw.Draw(imagem)
        ancoras = _posicoes_ancoras(imagem.width, imagem.height)
        for cx, cy in ancoras:
            pincel.rectangle(
                [cx - metade, cy - metade, cx + metade, cy + metade], fill=PRETO
            )

    _colar_qr(imagem, pincel, texto_qr, rotulo, aluno_nome, avisos)

    return ResultadoEstampa(
        imagem=imagem, ancoras=ancoras, borda_adicionada=borda, avisos=avisos
    )


def _ampliar_faixa_superior(imagem: Image.Image, faixa: int) -> Image.Image:
    """Acrescenta espaço em branco no topo, empurrando o conteúdo para baixo."""
    nova = Image.new("RGB", (imagem.width, imagem.height + faixa), BRANCO)
    nova.paste(imagem, (0, faixa))
    return nova


def _cantos_para_qr(largura: int, altura: int) -> List[Tuple[int, int]]:
    """
    Cantos candidatos, na ordem de preferência.

    O superior direito vem primeiro porque é o lugar convencional e é o
    que o motor usa para descobrir se a foto está de cabeça para baixo.
    """
    dentro = MARGEM_ANCORA + LADO_ANCORA // 2 + MARGEM_QR
    altura_rotulo = 30
    return [
        (largura - dentro - LADO_QR, dentro),
        (dentro, dentro),
        (largura - dentro - LADO_QR, altura - dentro - LADO_QR - altura_rotulo),
        (dentro, altura - dentro - LADO_QR - altura_rotulo),
    ]


def _encontrar_canto_qr(imagem: Image.Image) -> Optional[Tuple[int, int]]:
    """Primeiro canto realmente livre, ou None se não houver nenhum."""
    for x, y in _cantos_para_qr(imagem.width, imagem.height):
        caixa = (x - 8, y - 8, x + LADO_QR + 8, y + LADO_QR + 30)
        if _regiao_esta_livre(imagem, caixa):
            return x, y
    return None


def _colar_qr(imagem, pincel, texto_qr, rotulo, aluno_nome, avisos) -> None:
    """
    Coloca o QR no primeiro canto livre, começando pelo superior direito.

    A ordem existe porque o canto superior direito é o lugar
    convencional e o que o motor usa para descobrir se a foto está de
    cabeça para baixo; os outros são reserva.
    """
    codigo = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2
    )
    codigo.add_data(texto_qr)
    codigo.make(fit=True)
    imagem_qr = codigo.make_image(fill_color="black", back_color="white").convert("RGB")
    imagem_qr = imagem_qr.resize((LADO_QR, LADO_QR), Image.NEAREST)

    # A faixa branca já foi acrescentada por `estampar` quando nenhum
    # canto estava livre, então aqui sempre há lugar.
    posicao = _encontrar_canto_qr(imagem) or _cantos_para_qr(
        imagem.width, imagem.height
    )[0]
    x, y = posicao

    imagem.paste(imagem_qr, (x, y))
    if rotulo:
        pincel.text((x, y + LADO_QR + 6), rotulo, font=_fonte(17), fill=CINZA)
    if aluno_nome:
        pincel.text(
            (x, y + LADO_QR + 6 + (19 if rotulo else 0)),
            aluno_nome[:26],
            font=_fonte(18),
            fill=PRETO,
        )


# ==================================================================
# RETIFICACAO DA FOLHA ESTAMPADA
# ==================================================================
def recortar_area_util(resultado: ResultadoEstampa) -> np.ndarray:
    """
    Devolve a folha retificada em 800x1000, como o motor vai vê-la.

    Usada para detectar a grade logo depois da estampa: o layout tem de
    ser descoberto no MESMO espaço em que a leitura vai acontecer, ou
    as coordenadas não baterão.
    """
    from omr_engine import CONFIG_PADRAO

    origem = np.array(resultado.ancoras, dtype="float32")
    destino = np.array(
        [
            [0, 0],
            [CONFIG_PADRAO.largura_warp - 1, 0],
            [CONFIG_PADRAO.largura_warp - 1, CONFIG_PADRAO.altura_warp - 1],
            [0, CONFIG_PADRAO.altura_warp - 1],
        ],
        dtype="float32",
    )

    matriz = cv2.getPerspectiveTransform(origem, destino)
    imagem = cv2.cvtColor(np.array(resultado.imagem), cv2.COLOR_RGB2BGR)
    return cv2.warpPerspective(
        imagem, matriz, (CONFIG_PADRAO.largura_warp, CONFIG_PADRAO.altura_warp)
    )


# ==================================================================
# SAIDA
# ==================================================================
def paginas_para_pdf(paginas: List[Image.Image]) -> bytes:
    """Junta as folhas estampadas num PDF, uma página por aluno."""
    if not paginas:
        raise OMRError("Nenhuma folha para gerar.")

    buffer = io.BytesIO()
    primeira, *restantes = [p.convert("RGB") for p in paginas]
    primeira.save(
        buffer,
        format="PDF",
        resolution=float(DPI),
        save_all=True,
        append_images=restantes,
    )
    return buffer.getvalue()


def imagem_para_png(imagem: Image.Image) -> bytes:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()
