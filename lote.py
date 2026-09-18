"""
lote.py
------------------------------------------------------------------
Correção em massa: um arquivo só, com a turma inteira dentro.

O caminho real da escola é este: o scanner do administrativo cospe um
PDF de 40 páginas, ou alguém junta as fotos do celular num ZIP. Pedir
que o professor selecione 40 arquivos um a um é trabalho que a máquina
deveria fazer.

Este módulo só EXTRAI as folhas. Quem corrige continua sendo o
`omr_engine`, e quem grava é a camada de dados — separar isso permite
que o mesmo pipeline de correção sirva à foto avulsa, à câmera e ao
lote, sem três implementações diferentes.

Formatos aceitos:
    PDF   uma folha por página (saída típica de scanner)
    ZIP   uma folha por imagem dentro do arquivo
    imagem única (trata como lote de um)
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from typing import Iterator, List, Optional

import cv2
import numpy as np

from omr_engine import OMRError

logger = logging.getLogger(__name__)

# Resolução de rasterização das páginas do PDF. 200 dpi é o ponto em
# que o QR de uma prova longa ainda tem módulos suficientes sem que a
# memória exploda num PDF de 60 páginas.
DPI_PAGINA = 200

# Teto de segurança. Um PDF de mil páginas travaria a instância e
# estouraria o tempo da requisição sem nunca terminar.
MAXIMO_FOLHAS = 120

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class FolhaExtraida:
    """Uma folha pronta para correção, com a origem preservada."""

    # De onde saiu: "página 7" ou o nome do arquivo dentro do ZIP.
    # É o que permite ao professor localizar a folha física quando
    # alguma dá problema.
    origem: str
    conteudo: bytes


class LoteInvalido(OMRError):
    """Arquivo que não contém folhas utilizáveis."""


# ==================================================================
# IDENTIFICACAO
# ==================================================================
def identificar_lote(conteudo: bytes) -> str:
    """Devolve "pdf", "zip", "imagem" ou "desconhecido"."""
    if conteudo[:1024].find(b"%PDF-") != -1:
        return "pdf"
    if conteudo[:4] == b"PK\x03\x04":
        return "zip"

    assinaturas = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")
    if conteudo.startswith(assinaturas):
        return "imagem"
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return "imagem"
    if conteudo[:4] in (b"II*\x00", b"MM\x00*"):      # TIFF
        return "imagem"

    return "desconhecido"


# ==================================================================
# EXTRACAO
# ==================================================================
def _folhas_do_pdf(conteudo: bytes) -> Iterator[FolhaExtraida]:
    """
    Uma folha por página.

    Rasteriza sob demanda, página a página, em vez de carregar o PDF
    inteiro em memória: um arquivo de 60 páginas a 200 dpi passaria de
    1 GB se tudo fosse convertido de uma vez.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf

    try:
        documento = pymupdf.open(stream=conteudo, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise LoteInvalido(
            "Não consegui abrir o PDF. Se ele estiver protegido por senha, "
            "salve uma cópia sem proteção."
        ) from exc

    if documento.is_encrypted and not documento.authenticate(""):
        raise LoteInvalido(
            "O PDF está protegido por senha. Salve uma cópia sem proteção."
        )

    total = documento.page_count
    if total == 0:
        raise LoteInvalido("O PDF não tem nenhuma página.")

    for indice in range(min(total, MAXIMO_FOLHAS)):
        pixmap = documento[indice].get_pixmap(dpi=DPI_PAGINA)
        matriz = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        if pixmap.n == 4:
            imagem = cv2.cvtColor(matriz, cv2.COLOR_RGBA2BGR)
        elif pixmap.n == 1:
            imagem = cv2.cvtColor(matriz, cv2.COLOR_GRAY2BGR)
        else:
            imagem = cv2.cvtColor(matriz, cv2.COLOR_RGB2BGR)

        ok, buffer = cv2.imencode(".jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            yield FolhaExtraida(f"página {indice + 1}", buffer.tobytes())


def _folhas_do_zip(conteudo: bytes) -> Iterator[FolhaExtraida]:
    """Uma folha por imagem dentro do ZIP, em ordem de nome."""
    try:
        arquivo = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as exc:
        raise LoteInvalido(
            "Não consegui abrir este arquivo. Envie um PDF com as folhas "
            "digitalizadas, um ZIP com as imagens dos cartões, ou uma foto. "
            "Arquivos do Word e do Excel não servem."
        ) from exc

    nomes = [
        nome
        for nome in sorted(arquivo.namelist())
        # Pastas, arquivos ocultos e o lixo que o macOS coloca em todo
        # ZIP ("__MACOSX/") entrariam como folhas ilegíveis.
        if not nome.endswith("/")
        and not os.path.basename(nome).startswith(".")
        and "__MACOSX" not in nome
        and os.path.splitext(nome)[1].lower() in EXTENSOES_IMAGEM
    ]

    if not nomes:
        raise LoteInvalido(
            "Não encontrei imagens dentro do ZIP. Ele precisa conter as fotos "
            "dos cartões (JPG ou PNG)."
        )

    for nome in nomes[:MAXIMO_FOLHAS]:
        dados = arquivo.read(nome)
        if dados:
            yield FolhaExtraida(os.path.basename(nome), dados)


def extrair_folhas(conteudo: bytes, nome_arquivo: str = "") -> List[FolhaExtraida]:
    """
    Abre o arquivo enviado e devolve as folhas que há dentro.

    Raises:
        LoteInvalido: com uma mensagem que diz o que fazer, não apenas
            que deu errado.
    """
    if not conteudo:
        raise LoteInvalido("O arquivo enviado está vazio.")

    tipo = identificar_lote(conteudo)

    if tipo == "pdf":
        folhas = list(_folhas_do_pdf(conteudo))
    elif tipo == "zip":
        folhas = list(_folhas_do_zip(conteudo))
    elif tipo == "imagem":
        folhas = [FolhaExtraida(nome_arquivo or "foto enviada", conteudo)]
    else:
        raise LoteInvalido(
            "Não reconheci o arquivo. Envie um PDF com as folhas digitalizadas, "
            "um ZIP com as fotos, ou uma imagem."
        )

    if not folhas:
        raise LoteInvalido("Não encontrei nenhuma folha dentro do arquivo.")

    return folhas


def contar_folhas(conteudo: bytes) -> int:
    """
    Quantas folhas há no arquivo, sem rasterizar nada.

    Serve para a tela avisar "são 42 folhas, isso leva cerca de meio
    minuto" antes de começar, em vez de deixar o professor olhando uma
    tela parada sem saber se travou.
    """
    tipo = identificar_lote(conteudo)

    if tipo == "pdf":
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            import fitz as pymupdf
        try:
            return min(
                pymupdf.open(stream=conteudo, filetype="pdf").page_count, MAXIMO_FOLHAS
            )
        except Exception:  # noqa: BLE001
            return 0

    if tipo == "zip":
        try:
            arquivo = zipfile.ZipFile(io.BytesIO(conteudo))
        except zipfile.BadZipFile:
            return 0
        return min(
            sum(
                1
                for nome in arquivo.namelist()
                if not nome.endswith("/")
                and "__MACOSX" not in nome
                and not os.path.basename(nome).startswith(".")
                and os.path.splitext(nome)[1].lower() in EXTENSOES_IMAGEM
            ),
            MAXIMO_FOLHAS,
        )

    return 1 if tipo == "imagem" else 0
