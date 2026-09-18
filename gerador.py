"""
gerador.py
------------------------------------------------------------------
Gera o cartao-resposta pronto para impressao, com QR Code embutido,
e monta o PDF final (opcionalmente junto com a prova do professor).

Por que o cartao gerado aqui SEMPRE alinha com a leitura:
o desenho usa `OMRConfig.calcular_blocos()`, exatamente a mesma funcao
que o `omr_engine` usa para saber onde procurar as bolhas. A unica
diferenca e a escala de impressao. Nao ha duplicacao de geometria, logo
nao existe a possibilidade de as duas sairem de sincronia.

Sistema de coordenadas:
    - "espaco retificado": 800x1000, o que o motor OMR enxerga
    - "espaco do papel": espaco retificado * ESCALA + MARGEM
    Os CENTROS das 4 ancoras definem os cantos do espaco retificado.
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import qrcode
from PIL import Image, ImageDraw, ImageFont

from omr_engine import (
    ALTERNATIVAS,
    CONFIG_PADRAO,
    OMRConfig,
    OMRError,
    montar_qr_code,
)

# ==================================================================
# CONSTANTES DE IMPRESSAO
# ==================================================================
# 800x1000 * 1.55 + margens ~= proporcao A4 em 150 DPI
ESCALA = 1.55
MARGEM = 78          # espaco entre a borda do papel e o centro da ancora
LADO_ANCORA = 44     # lado do quadrado preto de canto, no papel

PRETO = (0, 0, 0)
CINZA = (110, 110, 110)
CINZA_CLARO = (180, 180, 180)
BRANCO = (255, 255, 255)

# DejaVu vem do pacote fonts-dejavu-core instalado no Dockerfile.
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
    """Carrega a fonte, com degradacao suave se nao houver TTF no sistema."""
    for caminho in (CAMINHOS_FONTE_NEGRITO if negrito else CAMINHOS_FONTE):
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    return ImageFont.load_default()


# ==================================================================
# DADOS DO CARTAO
# ==================================================================
@dataclass
class DadosCartao:
    """Tudo que o professor informa para gerar o cartao."""

    prova_id: str
    gabarito: Dict[int, str]
    titulo: str = "Cartao-resposta"
    disciplina: str = ""
    turma: str = ""
    instrucoes: str = "Preencha a bolha por inteiro, com caneta azul ou preta."

    # Preenchidos quando o cartao e nominal (gerado para uma turma
    # cadastrada). O `aluno_id` entra no QR e e o que permite gravar a
    # nota no nome certo, ja que o campo escrito a mao nao e legivel
    # por maquina.
    aluno_id: Optional[int] = None
    aluno_nome: str = ""
    escola: str = ""

    # Quando False, o QR leva SO a referencia da prova e do aluno: as
    # respostas ficam no banco. E o padrao dos cartoes de turma, e o que
    # impede o aluno de ler o gabarito apontando o celular para o
    # proprio cartao. Cartoes avulsos precisam levar o gabarito no
    # papel, porque nao ha banco para consultar.
    gabarito_no_qr: bool = True
    # Chave HMAC. Sem ela o cartao sai sem assinatura e qualquer um pode
    # imprimir um QR proprio.
    segredo: Optional[str] = None

    @property
    def total_questoes(self) -> int:
        return max(self.gabarito) if self.gabarito else 0


# ==================================================================
# DESENHO DO CARTAO
# ==================================================================
def gerar_cartao(
    dados: DadosCartao,
    marcacoes: Optional[Dict[int, str]] = None,
) -> Image.Image:
    """
    Desenha o cartao-resposta em uma imagem PIL pronta para impressao.

    Args:
        dados: identificacao da prova e gabarito.
        marcacoes: uso interno em testes e simulacao. Preenche bolhas
            como se um aluno tivesse respondido. Ex.: {1: "A", 8: "CD"}
            — duas letras simulam rasura. Em producao fica None.
    """
    config = CONFIG_PADRAO.com_questoes(dados.total_questoes)
    blocos = config.calcular_blocos()

    largura = int(config.largura_warp * ESCALA) + 2 * MARGEM
    altura = int(config.altura_warp * ESCALA) + 2 * MARGEM
    folha = Image.new("RGB", (largura, altura), BRANCO)
    pincel = ImageDraw.Draw(folha)

    def no_papel(u: float, v: float) -> Tuple[int, int]:
        """Espaco retificado -> espaco do papel."""
        return int(round(u * ESCALA + MARGEM)), int(round(v * ESCALA + MARGEM))

    _desenhar_ancoras(pincel, config, no_papel)
    _desenhar_cabecalho(folha, pincel, dados, config, no_papel)
    _desenhar_grade(pincel, blocos, config, no_papel, marcacoes)
    _desenhar_rodape(pincel, config, no_papel)

    return folha


def _desenhar_ancoras(pincel, config: OMRConfig, no_papel) -> None:
    """
    4 quadrados pretos solidos. Os CENTROS deles delimitam a area que o
    motor retifica para 800x1000 — por isso ficam exatamente nos cantos
    do espaco retificado.
    """
    metade = LADO_ANCORA // 2
    cantos = [
        (0, 0),
        (config.largura_warp, 0),
        (config.largura_warp, config.altura_warp),
        (0, config.altura_warp),
    ]
    for u, v in cantos:
        cx, cy = no_papel(u, v)
        pincel.rectangle(
            [cx - metade, cy - metade, cx + metade, cy + metade], fill=PRETO
        )


def _desenhar_cabecalho(folha, pincel, dados: DadosCartao, config, no_papel) -> None:
    """Titulo, identificacao, campos do aluno e QR Code."""
    x_texto, y_titulo = no_papel(60, 60)

    pincel.text((x_texto, y_titulo), dados.titulo, font=_fonte(40, True), fill=PRETO)

    subtitulo = " · ".join(
        parte for parte in [dados.escola, dados.disciplina, dados.turma] if parte
    ) or f"Prova {dados.prova_id}"
    pincel.text((x_texto, y_titulo + 54), subtitulo, font=_fonte(21), fill=CINZA)

    # --- QR Code no canto superior direito -------------------------
    qr_texto = montar_qr_code(
        dados.prova_id,
        dados.gabarito if dados.gabarito_no_qr else None,
        dados.aluno_id,
        dados.segredo,
    )
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_texto)
    qr.make(fit=True)
    imagem_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    lado_qr = int(145 * ESCALA)
    imagem_qr = imagem_qr.resize((lado_qr, lado_qr), Image.NEAREST)
    x_qr, y_qr = no_papel(config.largura_warp - 160, 48)
    folha.paste(imagem_qr, (x_qr, y_qr))

    rotulo_x, rotulo_y = no_papel(config.largura_warp - 160, 48 + 152)
    pincel.text(
        (rotulo_x, rotulo_y), f"Prova {dados.prova_id}", font=_fonte(18), fill=CINZA
    )

    # --- Identificacao do aluno ------------------------------------
    if dados.aluno_nome:
        # Cartao nominal: o nome ja vem impresso e o QR carrega o id.
        # O professor entrega a folha certa para cada aluno e nao ha
        # como a nota ir parar no boletim errado.
        px, py = no_papel(60, 186)
        rotulo_x, rotulo_y = no_papel(60, 152)
        pincel.text((rotulo_x, rotulo_y), "Aluno", font=_fonte(18), fill=CINZA)
        pincel.text(
            (px, py - 6), dados.aluno_nome, font=_fonte(27, True), fill=PRETO, anchor="ls"
        )
        fim_x, _ = no_papel(545, 186)
        pincel.line([(px, py), (fim_x, py)], fill=CINZA_CLARO, width=1)
    else:
        # Cartao generico: linhas em branco para escrever a mao.
        x_campo = 60.0
        for rotulo, largura_campo in [("Nome", 430.0), ("Turma", 135.0)]:
            px, py = no_papel(x_campo, 180)
            pincel.text((px, py - 32), rotulo, font=_fonte(18), fill=CINZA)
            fim_x, _ = no_papel(x_campo + largura_campo - 20, 180)
            pincel.line([(px, py), (fim_x, py)], fill=PRETO, width=2)
            x_campo += largura_campo

    # --- Instrucao -------------------------------------------------
    ix, iy = no_papel(60, 212)
    pincel.text((ix, iy), dados.instrucoes, font=_fonte(19), fill=CINZA)


def _desenhar_grade(pincel, blocos, config, no_papel, marcacoes) -> None:
    """
    Bolhas, numeros das questoes e cabecalho de letras por coluna.

    Os tamanhos de fonte e a espessura do traco acompanham o passo da
    grade: numa prova de 10 questoes as bolhas sao grandes; numa de 100,
    tudo encolhe junto e continua legivel.
    """
    marcacoes = marcacoes or {}

    for bloco in blocos:
        passo = min(bloco.passo_x, bloco.passo_y)
        # A bolha ocupa uma fracao FIXA da area de leitura (86% do raio
        # da ROI). Uma folga em pixels absolutos funcionaria numa prova
        # de 10 questoes e sufocaria uma de 140: com raio pequeno, tirar
        # 3 px derruba a densidade medida quase pela metade.
        meia_roi = passo * config.roi_escala * ESCALA / 2
        raio = max(4, int(meia_roi * 0.86))
        traco = max(2, round(raio / 5))

        tamanho_numero = max(11, min(23, int(bloco.passo_y * ESCALA * 0.46)))
        tamanho_letra = max(10, min(20, int(passo * ESCALA * 0.40)))

        # Cabecalho de letras (A B C D E) acima da coluna
        for indice_alt, letra in enumerate(ALTERNATIVAS[: config.total_alternativas]):
            cx, _ = bloco.centro(0, indice_alt)
            px, py = no_papel(cx, bloco.y0 - 14)
            pincel.text(
                (px, py),
                letra,
                font=_fonte(tamanho_letra, True),
                fill=CINZA,
                anchor="mm",
            )

        for indice_linha in range(bloco.total_linhas):
            numero = bloco.questao_inicial + indice_linha
            _, cy = bloco.centro(indice_linha, 0)

            # Numero da questao, alinhado a direita do espaco reservado
            nx, ny = no_papel(bloco.x_bolhas - 9, cy)
            pincel.text(
                (nx, ny),
                f"{numero:02d}",
                font=_fonte(tamanho_numero, True),
                fill=PRETO,
                anchor="rm",
            )

            marcada = (marcacoes.get(numero) or "").upper()

            for indice_alt in range(config.total_alternativas):
                cx, _ = bloco.centro(indice_linha, indice_alt)
                px, py = no_papel(cx, cy)
                letra = ALTERNATIVAS[indice_alt]
                caixa = [px - raio, py - raio, px + raio, py + raio]

                if letra in marcada:
                    pincel.ellipse(caixa, fill=PRETO)          # simulacao
                else:
                    pincel.ellipse(caixa, outline=CINZA_CLARO, width=traco)


def _desenhar_rodape(pincel, config, no_papel) -> None:
    """Aviso discreto para nao rasurar a area das ancoras."""
    px, py = no_papel(60, config.altura_warp + 26)
    pincel.text(
        (px, py),
        "Nao dobre nem escreva sobre os quadrados pretos dos cantos.",
        font=_fonte(17),
        fill=CINZA_CLARO,
    )


# ==================================================================
# SAIDA EM PDF / PNG
# ==================================================================
def cartao_para_pdf(imagem: Image.Image) -> bytes:
    """Converte a imagem do cartao em um PDF de uma pagina."""
    buffer = io.BytesIO()
    imagem.convert("RGB").save(buffer, format="PDF", resolution=150.0)
    return buffer.getvalue()


def cartao_para_png(imagem: Image.Image) -> bytes:
    """PNG do cartao, usado na previa da tela."""
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


def montar_pdf_varios_cartoes(cartoes: List[Image.Image]) -> bytes:
    """
    Junta varios cartoes num PDF unico, uma pagina por aluno.

    E assim que o professor imprime a turma inteira: cada folha ja sai
    com o nome do aluno e um QR proprio.
    """
    if not cartoes:
        raise OMRError("Nenhum cartao para gerar. A turma esta sem alunos?")

    buffer = io.BytesIO()
    primeiro, *restantes = [c.convert("RGB") for c in cartoes]
    primeiro.save(
        buffer,
        format="PDF",
        resolution=150.0,
        save_all=True,
        append_images=restantes,
    )
    return buffer.getvalue()


def _paginas_da_prova(prova_bytes: bytes, prova_nome: str = ""):
    """
    Converte o arquivo enviado pelo professor em paginas de PDF.

    Aceita PDF ou imagem. O tipo e decidido pelo CONTEUDO, nao pela
    extensao nem pelo content-type: navegador e sistema operacional
    mentem sobre isso com frequencia (um PDF chega como
    "application/octet-stream" em varias combinacoes de Windows e
    arrastar-e-soltar).
    """
    from pypdf import PdfReader

    cabecalho = prova_bytes[:1024]

    if cabecalho.find(b"%PDF-") != -1:
        try:
            leitor = PdfReader(io.BytesIO(prova_bytes))
        except Exception as exc:  # noqa: BLE001
            raise OMRError(
                "O arquivo da prova parece estar corrompido e nao pode ser "
                "aberto. Tente exportar o PDF de novo."
            ) from exc

        if leitor.is_encrypted:
            # Muitos PDFs de sistema escolar so tem senha de proprietario:
            # abrem normalmente com senha vazia. Vale tentar antes de
            # desistir e mandar o professor procurar a senha.
            try:
                desbloqueado = leitor.decrypt("")
            except Exception:  # noqa: BLE001
                desbloqueado = 0
            if not desbloqueado:
                raise OMRError(
                    "O PDF da prova esta protegido por senha. Salve uma copia "
                    "sem protecao e envie de novo."
                )

        try:
            paginas = list(leitor.pages)
        except Exception as exc:  # noqa: BLE001
            raise OMRError(
                "Nao foi possivel ler as paginas do PDF da prova. Tente "
                "exportar o arquivo de novo."
            ) from exc

        if not paginas:
            raise OMRError("O PDF da prova esta vazio (nenhuma pagina).")
        return paginas

    # Nao e PDF: tenta abrir como imagem.
    try:
        imagem = Image.open(io.BytesIO(prova_bytes))
        imagem.load()
        buffer = io.BytesIO()
        imagem.convert("RGB").save(buffer, format="PDF", resolution=150.0)
        buffer.seek(0)
        return list(PdfReader(buffer).pages)
    except OMRError:
        raise
    except Exception as exc:  # noqa: BLE001
        nome = f" ({prova_nome})" if prova_nome else ""
        raise OMRError(
            f"Nao foi possivel abrir o arquivo da prova{nome}. Envie um PDF "
            f"ou uma imagem (JPG ou PNG) — arquivos do Word precisam ser "
            f"exportados como PDF antes."
        ) from exc


def anexar_prova(
    pdf_cartoes: bytes,
    prova_bytes: Optional[bytes] = None,
    prova_nome: str = "",
) -> bytes:
    """
    Coloca as paginas da prova do professor na frente dos cartoes.

    Ponto unico de montagem: os dois endpoints que anexam prova (cartao
    avulso e cartoes da turma) passam por aqui. Antes cada um tinha sua
    copia da logica, e a do endpoint da turma nao tratava PDF corrompido
    — devolvia erro 500 em vez de uma mensagem util.
    """
    if not prova_bytes:
        return pdf_cartoes

    from pypdf import PdfReader, PdfWriter

    escritor = PdfWriter()
    for pagina in _paginas_da_prova(prova_bytes, prova_nome):
        escritor.add_page(pagina)
    for pagina in PdfReader(io.BytesIO(pdf_cartoes)).pages:
        escritor.add_page(pagina)

    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def montar_pdf_completo(
    cartao: Image.Image,
    prova_bytes: Optional[bytes] = None,
    prova_nome: str = "",
) -> bytes:
    """PDF de um cartao so, opcionalmente precedido pela prova."""
    return anexar_prova(cartao_para_pdf(cartao), prova_bytes, prova_nome)


# ==================================================================
# PARSER DO GABARITO DIGITADO PELO PROFESSOR
# ==================================================================
def parse_gabarito_digitado(texto: str) -> Dict[int, str]:
    """
    Aceita o gabarito nas formas que um professor digitaria naturalmente:

        "A,B,C,D,E"        -> posicional
        "ABCDE"            -> posicional colado
        "1A,2B,3C"         -> numerado
        "1-A, 2-B, 3-C"    -> numerado com separadores variados

    Returns:
        {1: "A", 2: "B", ...}
    """
    texto = (texto or "").strip().upper()
    if not texto:
        raise OMRError("Informe o gabarito da prova.")

    # Forma colada: so letras validas, sem separadores.
    compacto = texto.replace(" ", "")
    if compacto.isalpha() and all(c in ALTERNATIVAS for c in compacto):
        return {i: letra for i, letra in enumerate(compacto, start=1)}

    itens = [
        parte.strip()
        for parte in texto.replace(";", ",").replace("\n", ",").split(",")
        if parte.strip()
    ]

    gabarito: Dict[int, str] = {}
    for posicao, item in enumerate(itens, start=1):
        numero = "".join(c for c in item if c.isdigit())
        letras = [c for c in item if c.isalpha()]

        if len(letras) != 1 or letras[0] not in ALTERNATIVAS:
            raise OMRError(
                f"Resposta invalida no gabarito: '{item}'. Use apenas as letras A a E."
            )
        gabarito[int(numero) if numero else posicao] = letras[0]

    if not gabarito:
        raise OMRError("Nenhuma resposta valida encontrada no gabarito.")

    faltando = [n for n in range(1, max(gabarito) + 1) if n not in gabarito]
    if faltando:
        raise OMRError(
            "Faltam respostas para as questoes: "
            + ", ".join(str(n) for n in faltando[:10])
        )

    return gabarito
