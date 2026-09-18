"""
omr_engine.py
------------------------------------------------------------------
Motor de Visao Computacional (OMR - Optical Mark Recognition).

Pipeline executado por `OMREngine.processar()`:

    1. Leitura do QR Code (automatica na imagem, ou string informada)
    2. Decodificacao da imagem (bytes -> ndarray BGR)
    3. Pre-processamento (cinza + blur + threshold Otsu invertido)
    4. Localizacao das 4 ancoras nos cantos (cv2.findContours)
    5. Reordenacao dos cantos + warpPerspective -> folha 800x1000
    6. Binarizacao da folha retificada
    7. Mapeamento da grade de bolhas (N questoes x 5 alternativas)
    8. Deteccao da marcacao (cv2.countNonZero por ROI)
    9. Validacao (branco / rasura) e comparacao com o gabarito
   10. Calculo da nota final

IMPORTANTE - fonte unica de verdade da geometria:
`OMRConfig.calcular_blocos()` define onde cada bolha fica no espaco
retificado de 800x1000. O gerador do cartao (`gerador.py`) usa a MESMA
funcao para desenhar. Assim o cartao impresso e a leitura nunca saem de
sincronia, qualquer que seja o numero de questoes.

Nenhuma dependencia de FastAPI aqui: e Python puro + OpenCV.
------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from seguranca import anexar_assinatura, conferir_assinatura

logger = logging.getLogger(__name__)

# Letras das alternativas na ordem das colunas (esquerda -> direita)
ALTERNATIVAS: Tuple[str, ...] = ("A", "B", "C", "D", "E")

MIN_QUESTOES = 1


# ==================================================================
# EXCECOES DE NEGOCIO
# ==================================================================
class OMRError(Exception):
    """Erro previsivel do pipeline (imagem ruim, QR invalido, etc.)."""


# ==================================================================
# GEOMETRIA DA GRADE
# ==================================================================
@dataclass(frozen=True)
class BlocoGrade:
    """
    Uma coluna de questoes dentro da folha retificada.

    Ex.: uma prova de 30 questoes vira 2 blocos de 15, lado a lado.
    """

    questao_inicial: int          # 1-based, inclusivo
    questao_final: int            # 1-based, inclusivo
    x_numero: float               # x do texto "01", "02"...
    x_bolhas: float               # x onde comeca a 1a coluna de bolhas
    largura_bolhas: float         # largura ocupada pelas 5 bolhas
    y0: float
    y1: float
    passo_y: float                # espacamento vertical entre questoes

    @property
    def total_linhas(self) -> int:
        return self.questao_final - self.questao_inicial + 1

    @property
    def passo_x(self) -> float:
        return self.largura_bolhas / len(ALTERNATIVAS)

    def centro(self, indice_linha: int, indice_alternativa: int) -> Tuple[float, float]:
        """Centro da bolha, no espaco retificado 800x1000."""
        cx = self.x_bolhas + self.passo_x * (indice_alternativa + 0.5)
        cy = self.y0 + self.passo_y * (indice_linha + 0.5)
        return cx, cy


# ==================================================================
# CONFIGURACAO
# ==================================================================
@dataclass(frozen=True)
class OMRConfig:
    # ---- Dimensao padronizada da folha apos o warp -----------------
    largura_warp: int = 800
    altura_warp: int = 1000

    # ---- Layout da prova ------------------------------------------
    total_questoes: int = 10
    total_alternativas: int = 5

    # A grade se reorganiza sozinha conforme a prova cresce: o numero de
    # colunas e escolhido para MAXIMIZAR o tamanho da bolha impressa.
    colunas_maximas: int = 4
    # Passo minimo entre bolhas, no espaco retificado. 20 px equivalem a
    # uma bolha de ~3,5 mm impressa em A4 — o mesmo porte das provas de
    # larga escala. Abaixo disso o aluno nao consegue preencher direito
    # e a leitura perde confiabilidade.
    passo_minimo: float = 20.0

    # ---- Area util da grade dentro da folha retificada ------------
    grade_x0: float = 55.0
    grade_y0: float = 262.0
    grade_x1: float = 750.0
    grade_y1: float = 965.0
    # Respiro horizontal entre colunas de questoes
    espaco_entre_colunas: float = 20.0

    # ---- Deteccao das bolhas --------------------------------------
    roi_escala: float = 0.62          # fracao da celula usada como ROI
    roi_quadrada: bool = True         # ROI quadrada separa melhor cheio/vazio

    # A decisao e RELATIVA: a bolha marcada e a que destoa das outras da
    # MESMA questao. Um limiar absoluto quebrava em dois casos comuns —
    # sombra na folha (tudo escurece e vira rasura) e preenchimento
    # incompleto (fica abaixo do corte e vira branco). Comparar dentro
    # da propria linha elimina os dois, porque sombra e cor de caneta
    # afetam as cinco alternativas igualmente.
    # 0,05 nao e chute: em 870 questoes de folhas EM BRANCO fotografadas
    # com perspectiva, desfoque e sombra, o destaque maximo observado foi
    # 0,008. O preenchimento mais fraco que um aluno produz — risco de
    # lapis claro — mede 0,058. O corte fica a seis vezes o ruido e a
    # pouco mais de uma vez do sinal mais fraco.
    margem_relativa: float = 0.05
    # Piso de seguranca: sem ele, uma folha totalmente em branco teria
    # sempre uma bolha "menos clara" eleita vencedora por ruido.
    piso_escuridao: float = 0.09
    # Fracao da margem que a segunda bolha precisa atingir para a
    # questao ser considerada rasurada.
    limiar_rasura: float = 0.70

    # Mantido para o preview de calibracao e compatibilidade.
    limiar_preenchimento: float = 0.35

    # ---- Deteccao das ancoras (quadrados pretos nos cantos) --------
    area_minima_ancora: int = 120
    fracao_maxima_ancora: float = 0.05
    # Faixa larga de proposito. A ancora e desenhada quadrada, mas
    # chega achatada quando a impressora corta parte dela no rodape, ou
    # esticada quando a foto e tirada muito de lado. Recusar essas duas
    # situacoes derrubava o alinhamento em folha impressa de verdade —
    # e o filtro de posicao (a mais proxima de cada canto) ja limita o
    # estrago de um falso positivo.
    razao_aspecto_min: float = 0.28
    razao_aspecto_max: float = 3.60
    solidez_minima: float = 0.55

    # ---- Normalizacao de entrada ----------------------------------
    largura_maxima_entrada: int = 1600

    # --------------------------------------------------------------
    @property
    def maximo_questoes(self) -> int:
        """
        Quantas questoes cabem numa folha sem que a bolha fique menor
        que `passo_minimo`. E descoberto por busca, nao chutado: se voce
        mexer nas margens ou no numero de colunas, este valor acompanha.
        """
        maior = 0
        for total in range(1, 501):
            try:
                OMRConfig(**{**self.__dict__, "total_questoes": total})._escolher_layout()
            except OMRError:
                break
            maior = total
        return maior

    @property
    def largura_numero(self) -> float:
        """
        Espaco a esquerda de cada coluna para o numero da questao.
        Provas com 100+ questoes precisam de 3 digitos.
        """
        return 52.0 if self.total_questoes >= 100 else 40.0

    def com_questoes(self, total: int) -> "OMRConfig":
        """Devolve uma copia da config com outro numero de questoes."""
        if total < MIN_QUESTOES:
            raise OMRError("A prova precisa ter pelo menos uma questao.")
        nova = OMRConfig(**{**self.__dict__, "total_questoes": total})
        nova._escolher_layout()   # valida a geometria antes de devolver
        return nova

    def _escolher_layout(self) -> Tuple[int, float, float, float]:
        """
        Decide em quantas colunas dividir a prova.

        Nao usa o menor numero de colunas possivel: testa todas as
        divisoes e fica com a que deixa a BOLHA MAIOR. Mais colunas
        estreitam horizontalmente mas encurtam cada coluna, entao o
        otimo raramente e o extremo. Numa prova de 30 questoes, por
        exemplo, 2 colunas dobram o tamanho da bolha em relacao a 1.

        Returns:
            (num_colunas, passo, passo_x, largura_coluna)

        Raises:
            OMRError: se nem a melhor divisao produzir bolhas
                preenchiveis a caneta.
        """
        largura_total = self.grade_x1 - self.grade_x0
        altura_total = self.grade_y1 - self.grade_y0
        melhor: Optional[Tuple[int, float, float, float]] = None

        for colunas in range(1, self.colunas_maximas + 1):
            largura_coluna = (
                largura_total - self.espaco_entre_colunas * (colunas - 1)
            ) / colunas
            passo_x = (largura_coluna - self.largura_numero) / self.total_alternativas
            if passo_x <= 0:
                continue

            linhas = math.ceil(self.total_questoes / colunas)
            passo_y = altura_total / linhas

            # A ROI e quadrada: quem manda no tamanho da bolha e o menor
            # dos dois passos.
            passo = min(passo_x, passo_y)
            if melhor is None or passo > melhor[1]:
                melhor = (colunas, passo, passo_x, largura_coluna)

        if melhor is None or melhor[1] < self.passo_minimo:
            raise OMRError(
                f"Com {self.total_questoes} questoes as bolhas ficariam pequenas "
                f"demais para preencher a caneta. Divida a prova em dois "
                f"cartoes-resposta."
            )
        return melhor

    def calcular_blocos(self) -> List[BlocoGrade]:
        """
        Divide as questoes em colunas e devolve a geometria de cada uma.

        Fonte unica de verdade: usada tanto pela LEITURA quanto pelo
        DESENHO do cartao. Mudar aqui muda os dois ao mesmo tempo.
        """
        total = self.total_questoes
        num_colunas, _, _, largura_coluna = self._escolher_layout()

        # Distribui as questoes o mais uniformemente possivel.
        base, resto = divmod(total, num_colunas)
        linhas_por_coluna = [base + (1 if i < resto else 0) for i in range(num_colunas)]
        maior_coluna = max(linhas_por_coluna)

        # Passo vertical unico: todas as colunas ficam alinhadas entre si.
        passo_y = (self.grade_y1 - self.grade_y0) / maior_coluna

        blocos: List[BlocoGrade] = []
        proxima = 1
        for indice, quantidade in enumerate(linhas_por_coluna):
            if quantidade == 0:
                continue
            x_coluna = self.grade_x0 + indice * (
                largura_coluna + self.espaco_entre_colunas
            )
            blocos.append(
                BlocoGrade(
                    questao_inicial=proxima,
                    questao_final=proxima + quantidade - 1,
                    x_numero=x_coluna,
                    x_bolhas=x_coluna + self.largura_numero,
                    largura_bolhas=largura_coluna - self.largura_numero,
                    y0=self.grade_y0,
                    y1=self.grade_y0 + passo_y * quantidade,
                    passo_y=passo_y,
                )
            )
            proxima += quantidade

        return blocos


CONFIG_PADRAO = OMRConfig()


# ==================================================================
# QR CODE
# ==================================================================
@dataclass(frozen=True)
class LeituraQR:
    """O que o QR Code de um cartao carrega."""

    prova_id: str
    aluno_id: Optional[int] = None
    # None significa "o gabarito nao esta no QR, busque no banco".
    # E o caso dos cartoes gerados a partir de uma turma.
    gabarito: Optional[Dict[int, str]] = None
    # "valida" | "invalida" | "ausente" | "nao_conferida"
    assinatura: str = "ausente"
    texto: str = ""

    @property
    def confiavel(self) -> bool:
        return self.assinatura != "invalida"


def parse_qr_code(qr_code_str: str, segredo: Optional[str] = None) -> LeituraQR:
    """
    Interpreta o conteudo do QR Code.

    Formatos aceitos:
        "P12.A7-K3M9QZ7X"  cartao de turma: so a referencia, assinada.
                           O gabarito NAO viaja no papel — fica no banco.
        "101*ABCDE"        cartao avulso: respostas coladas
        "101.A7*ABCDE"     idem, identificando o aluno
        "101|A,C,B"        posicional com virgulas (formato antigo)
        "101|1A,2C,3B"     numerada (necessaria se houver lacunas)

    Args:
        segredo: chave para conferir a assinatura. Sem ela a assinatura
            e reportada como "nao_conferida" em vez de barrar a leitura,
            porque o motor precisa continuar utilizavel fora da API.
    """
    texto = (qr_code_str or "").strip()
    if not texto:
        raise OMRError("Gabarito vazio.")

    corpo, situacao = conferir_assinatura(texto, segredo)

    separador = next((s for s in ("*", "|") if s in corpo), None)

    if separador is None:
        # Cartao de turma: so a referencia da prova e do aluno.
        prova_id, aluno_id = extrair_aluno(corpo)
        if not prova_id:
            raise OMRError("QR Code sem identificador da prova.")
        return LeituraQR(
            prova_id=prova_id,
            aluno_id=aluno_id,
            gabarito=None,
            assinatura=situacao,
            texto=texto,
        )

    referencia, gabarito_raw = corpo.split(separador, 1)
    prova_id, aluno_id = extrair_aluno(referencia.strip())
    if not prova_id:
        raise OMRError("Gabarito sem identificador da prova.")

    gabarito = _interpretar_respostas(gabarito_raw)
    return LeituraQR(
        prova_id=prova_id,
        aluno_id=aluno_id,
        gabarito=gabarito,
        assinatura=situacao,
        texto=texto,
    )


def _interpretar_respostas(bruto: str) -> Dict[int, str]:
    """
    Le a lista de respostas nas varias formas ja usadas pelo sistema.

    Tolerante a "1:A", "1-A", "1 a", espacos e minusculas, para nao
    quebrar com gabaritos digitados a mao ou vindos de outro sistema.
    """
    corpo = bruto.strip().upper()

    # Forma colada: so letras validas, sem separador interno.
    compacto = corpo.replace(" ", "")
    if compacto and all(c in ALTERNATIVAS for c in compacto):
        return {i: letra for i, letra in enumerate(compacto, start=1)}

    gabarito: Dict[int, str] = {}
    itens = [item.strip() for item in corpo.split(",") if item.strip()]

    for posicao, item in enumerate(itens, start=1):
        numero = "".join(ch for ch in item if ch.isdigit())
        letra = "".join(ch for ch in item if ch.isalpha())

        if len(letra) != 1 or letra not in ALTERNATIVAS:
            logger.warning("Item de gabarito ignorado: %r", item)
            continue

        # Sem numero explicito -> usa a posicao na lista.
        gabarito[int(numero) if numero else posicao] = letra

    if not gabarito:
        raise OMRError("Nenhuma resposta valida encontrada no gabarito.")
    return gabarito


def montar_qr_code(
    prova_id: str,
    gabarito: Optional[Dict[int, str]] = None,
    aluno_id: Optional[int] = None,
    segredo: Optional[str] = None,
) -> str:
    """
    Monta a string gravada no QR Code.

    Formato: "PROVA[.A<id_aluno>][*RESPOSTAS][-ASSINATURA]"

    O identificador do aluno e o que permite guardar a nota no nome
    certo: o campo "Nome" impresso e preenchido a mao e o OpenCV nao le
    letra cursiva.

    QUANDO OMITIR O GABARITO (`gabarito=None`): nos cartoes gerados a
    partir de uma turma, as respostas ficam no banco e o papel leva so a
    referencia. Isso resolve de uma vez dois problemas — o aluno nao
    consegue mais ler o gabarito apontando o celular para o proprio
    cartao, e o QR fica minusculo mesmo numa prova de 140 questoes.

    Quando o gabarito PRECISA viajar no papel (cartao avulso, sem turma
    cadastrada), as respostas vao COLADAS: "101*ABCDE" em vez de
    "101|1A,2B,3C". O QR tem um modo ALFANUMERICO (digitos, A-Z e alguns
    simbolos, entre eles "*", "." e "-") que gasta 5,5 bits por
    caractere contra 8 do modo byte; a virgula e o "|" ficam de fora
    desse conjunto e sozinhos empurram o codigo inteiro para o modo
    byte. Numa prova de 140 questoes isso e a diferenca entre 45 e 69
    modulos — entre o celular ler e nao ler.
    """
    referencia = prova_id.upper()
    if aluno_id is not None:
        referencia = f"{referencia}.A{aluno_id}"

    if gabarito is None:
        return anexar_assinatura(referencia, segredo)

    numeros = sorted(gabarito)
    contiguo = numeros == list(range(1, len(numeros) + 1))

    if contiguo:
        # Maiusculas: o modo alfanumerico do QR nao aceita minusculas.
        conteudo = f"{referencia}*{''.join(gabarito[n] for n in numeros)}"
    else:
        # Gabarito com lacunas exige a forma numerada, mais longa.
        conteudo = f"{referencia}|{','.join(f'{n}{gabarito[n]}' for n in numeros)}"

    return anexar_assinatura(conteudo, segredo)


def extrair_aluno(prova_ref: str) -> Tuple[str, Optional[int]]:
    """
    Separa "P12.A345" em ("P12", 345).

    So reconhece o sufixo quando ele casa exatamente com ".A<numeros>",
    para nao confundir com um codigo de prova que o professor tenha
    digitado com ponto, como "prova.final".
    """
    if "." not in prova_ref:
        return prova_ref, None

    inicio, _, sufixo = prova_ref.rpartition(".")
    if inicio and sufixo[:1] == "A" and sufixo[1:].isdigit():
        return inicio, int(sufixo[1:])
    return prova_ref, None


def ler_qr_da_imagem(imagem: np.ndarray) -> Optional[str]:
    """
    Tenta ler o QR Code direto da foto, para o professor nao precisar
    digitar o gabarito.

    Foto de celular costuma ter o QR pequeno, desfocado ou com pouco
    contraste, entao tentamos em cascata:
        1. imagem original
        2. imagem ampliada 2x (QR pequeno demais para o detector)
        3. escala de cinza equalizada (foto clara ou escura demais)

    Returns:
        A string do QR, ou None se nao encontrar.
    """
    detector = cv2.QRCodeDetector()
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)

    tentativas = [
        imagem,
        cv2.resize(imagem, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC),
        cv2.cvtColor(cv2.equalizeHist(cinza), cv2.COLOR_GRAY2BGR),
    ]

    for candidata in tentativas:
        try:
            texto, _, _ = detector.detectAndDecode(candidata)
        except cv2.error:  # detector pode falhar em imagens degeneradas
            continue
        if texto and texto.strip():
            return texto.strip()

    return None


# ==================================================================
# MOTOR PRINCIPAL
# ==================================================================
class OMREngine:
    """Encapsula todo o pipeline de visao computacional."""

    def __init__(
        self,
        config: OMRConfig = CONFIG_PADRAO,
        rois: Optional[List[List[Tuple[int, int, int, int]]]] = None,
    ) -> None:
        """
        Args:
            config: geometria do cartao gerado por este sistema.
            rois: layout JA CONHECIDO, uma lista de regioes por questao.
                E o caminho usado quando a folha e do professor: ali a
                grade foi DETECTADA na folha dele, e nao calculada por
                nos. Informado, substitui a geometria da config.
        """
        self.config = config

        if rois is not None:
            self.blocos = []
            self._rois = rois
        else:
            self.blocos = config.calcular_blocos()
            # A grade e fixa para uma dada config: calcula uma vez so.
            self._rois = self._mapear_grade()

    # --------------------------------------------------------------
    # API PUBLICA
    # --------------------------------------------------------------
    def processar(self, imagem_bytes: bytes, gabarito: Dict[int, str]) -> dict:
        """
        Corrige uma folha contra o gabarito informado.

        O gabarito chega pronto: quem o obteve — do QR do papel ou do
        banco de dados — foi a camada de cima. Manter essa decisao fora
        do motor e o que permite que os cartoes de turma nao precisem
        carregar as respostas impressas.
        """
        inicio = time.perf_counter()

        imagem = self._decodificar_imagem(imagem_bytes)

        # --- Etapas 3 a 5: retificacao ------------------------------
        folha, metodo = self.alinhar_folha(imagem)

        # --- Etapa 6: correcao de iluminacao ------------------------
        normalizada = self.normalizar_iluminacao(folha)

        # --- Etapas 7 e 8: leitura das bolhas -----------------------
        leituras = self._detectar_respostas(normalizada)

        # --- Etapas 9 e 10: correcao e nota -------------------------
        resultado = self._corrigir(leituras, gabarito)
        resultado["alinhamento"] = metodo
        resultado["tempo_processamento_ms"] = round(
            (time.perf_counter() - inicio) * 1000, 2
        )
        return resultado

    # --------------------------------------------------------------
    # ETAPA 2 - DECODIFICACAO DA IMAGEM
    # --------------------------------------------------------------
    def _decodificar_imagem(self, imagem_bytes: bytes) -> np.ndarray:
        if not imagem_bytes:
            raise OMRError("Arquivo de imagem vazio.")

        buffer = np.frombuffer(imagem_bytes, dtype=np.uint8)
        imagem = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if imagem is None:
            raise OMRError("Nao foi possivel abrir a imagem (formato invalido).")

        # Fotos de celular chegam com 4000px+ e so gastam CPU.
        altura, largura = imagem.shape[:2]
        limite = self.config.largura_maxima_entrada
        if largura > limite:
            escala = limite / float(largura)
            imagem = cv2.resize(
                imagem, (limite, int(altura * escala)), interpolation=cv2.INTER_AREA
            )
        return imagem

    # --------------------------------------------------------------
    # ETAPAS 3 A 5 - ANCORAS + WARP PERSPECTIVE
    # --------------------------------------------------------------
    def alinhar_folha(self, imagem: np.ndarray) -> Tuple[np.ndarray, str]:
        """
        Retifica o cartao para `largura_warp` x `altura_warp`.

        Estrategia em cascata:
            1. 4 ancoras quadradas nos cantos  -> "ancoras"
            2. maior contorno retangular       -> "contorno_pagina"
            3. resize da imagem inteira        -> "fallback_resize"

        Em todos os casos a orientacao e conferida no fim: as ancoras
        sao simetricas, entao uma foto de cabeca para baixo passaria
        pelo warp sem erro e produziria uma nota errada.
        """
        mascara = self._preprocessar_para_contornos(imagem)

        cantos = self._encontrar_ancoras(mascara)
        if cantos is not None:
            return self._aplicar_warp(imagem, cantos), "ancoras"

        cantos = self._encontrar_contorno_pagina(mascara)
        if cantos is not None:
            logger.warning("Ancoras nao encontradas; usando contorno da pagina.")
            return self._aplicar_warp(imagem, cantos), "contorno_pagina"

        logger.warning("Alinhamento por contorno falhou; aplicando resize direto.")
        redimensionada = cv2.resize(
            imagem,
            (self.config.largura_warp, self.config.altura_warp),
            interpolation=cv2.INTER_AREA,
        )
        return self._corrigir_orientacao(redimensionada), "fallback_resize"

    def _corrigir_orientacao(self, folha: np.ndarray) -> np.ndarray:
        """
        Gira a folha 180 graus se ela veio de cabeca para baixo.

        As 4 ancoras sao simetricas: uma foto invertida produz um warp
        perfeitamente valido, mas a leitura sai trocada e o aluno leva
        zero sem nenhum aviso. O QR Code quebra essa simetria, porque
        e impresso sempre no canto SUPERIOR DIREITO. Se ele aparecer na
        metade de baixo da folha retificada, a imagem esta invertida.
        """
        detector = cv2.QRCodeDetector()
        try:
            encontrado, pontos = detector.detect(folha)
        except cv2.error:
            return folha

        if not encontrado or pontos is None:
            return folha  # sem referencia: melhor nao arriscar girar

        centro = pontos.reshape(-1, 2).mean(axis=0)
        altura, largura = folha.shape[:2]

        # QR na metade inferior e do lado esquerdo => folha invertida
        if centro[1] > altura / 2 and centro[0] < largura / 2:
            logger.info("Folha detectada de cabeca para baixo; girando 180 graus.")
            return cv2.rotate(folha, cv2.ROTATE_180)

        return folha

    def _preprocessar_para_contornos(self, imagem: np.ndarray) -> np.ndarray:
        """
        Prepara a mascara onde as ancoras serao procuradas.

        A normalizacao de iluminacao NAO e enfeite. O Otsu global
        assume duas populacoes de brilho bem separadas; numa folha com
        muito espaco em branco e sombra de um lado — que e o caso de
        qualquer cartao fotografado — ele acaba partindo o proprio
        gradiente do papel ao meio e marcando metade da folha como
        "escuro". Dividir pela versao borrada remove o gradiente antes
        de decidir o corte.
        """
        cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)

        # O fundo e estimado numa versao reduzida: um desfoque com
        # sigma grande na imagem inteira custa caro e nao acrescenta
        # nada, porque o que se quer capturar e justamente a variacao
        # LENTA de iluminacao.
        pequena = cv2.resize(cinza, None, fx=0.125, fy=0.125, interpolation=cv2.INTER_AREA)
        fundo_pequeno = cv2.GaussianBlur(pequena, (0, 0), sigmaX=max(pequena.shape) / 12)
        fundo = cv2.resize(
            fundo_pequeno, (cinza.shape[1], cinza.shape[0]), interpolation=cv2.INTER_LINEAR
        )
        normalizada = cv2.divide(cinza, fundo, scale=255)

        suavizada = cv2.GaussianBlur(normalizada, (5, 5), 0)
        _, mascara = cv2.threshold(
            suavizada, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

        # Rede de seguranca: as ancoras e o texto ocupam uma fracao
        # pequena da folha. Se a mascara cobrir muito, o corte saiu
        # errado e um limiar fixo e mais confiavel que o automatico.
        if float((mascara > 0).mean()) > 0.25:
            logger.info("Mascara ampla demais (%.0f%%); usando limiar fixo.",
                        float((mascara > 0).mean()) * 100)
            _, mascara = cv2.threshold(suavizada, 110, 255, cv2.THRESH_BINARY_INV)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, kernel, iterations=2)

    def _encontrar_ancoras(self, mascara: np.ndarray) -> Optional[np.ndarray]:
        """
        Procura os 4 marcadores quadrados dos cantos.

        Filtros: area plausivel, 4 vertices na aproximacao poligonal,
        razao de aspecto proxima de 1 e alta solidez.
        """
        cfg = self.config
        altura, largura = mascara.shape[:2]
        area_imagem = float(altura * largura)

        contornos, _ = cv2.findContours(
            mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        candidatos: List[Tuple[float, float, float]] = []
        for contorno in contornos:
            area = cv2.contourArea(contorno)
            if area < cfg.area_minima_ancora:
                continue
            if area > area_imagem * cfg.fracao_maxima_ancora:
                continue

            perimetro = cv2.arcLength(contorno, True)
            aprox = cv2.approxPolyDP(contorno, 0.04 * perimetro, True)
            if len(aprox) != 4:
                continue

            x, y, w, h = cv2.boundingRect(aprox)
            if h == 0:
                continue
            razao = w / float(h)
            if not (cfg.razao_aspecto_min <= razao <= cfg.razao_aspecto_max):
                continue
            if area / float(w * h) < cfg.solidez_minima:
                continue

            candidatos.append((x + w / 2.0, y + h / 2.0, area))

        if len(candidatos) < 4:
            return None

        # Para cada canto da imagem, pega o candidato mais proximo.
        cantos_imagem = [(0.0, 0.0), (largura, 0.0), (largura, altura), (0.0, altura)]
        selecionados: List[Tuple[float, float]] = []
        usados: set[int] = set()

        for alvo_x, alvo_y in cantos_imagem:
            melhor_idx, melhor_dist = -1, float("inf")
            for idx, (cx, cy, _) in enumerate(candidatos):
                if idx in usados:
                    continue
                dist = (cx - alvo_x) ** 2 + (cy - alvo_y) ** 2
                if dist < melhor_dist:
                    melhor_dist, melhor_idx = dist, idx
            usados.add(melhor_idx)
            selecionados.append(candidatos[melhor_idx][:2])

        pontos = np.array(selecionados, dtype="float32")

        # 4 pontos quase colineares invalidariam o warp.
        if cv2.contourArea(pontos.astype(np.int32)) < area_imagem * 0.05:
            logger.warning("Ancoras formam area degenerada; descartando.")
            return None

        return pontos

    def _encontrar_contorno_pagina(self, mascara: np.ndarray) -> Optional[np.ndarray]:
        """Fallback: assume que a folha e o maior quadrilatero da cena."""
        contornos, _ = cv2.findContours(
            mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contornos:
            return None

        area_imagem = float(mascara.shape[0] * mascara.shape[1])
        for contorno in sorted(contornos, key=cv2.contourArea, reverse=True)[:5]:
            if cv2.contourArea(contorno) < area_imagem * 0.20:
                break
            perimetro = cv2.arcLength(contorno, True)
            aprox = cv2.approxPolyDP(contorno, 0.02 * perimetro, True)
            if len(aprox) == 4:
                return aprox.reshape(4, 2).astype("float32")
        return None

    @staticmethod
    def ordenar_pontos(pontos: Sequence) -> np.ndarray:
        """
        Reordena 4 pontos para [sup-esq, sup-dir, inf-dir, inf-esq].

        soma (x+y): minima = TL, maxima = BR
        diferenca (y-x): minima = TR, maxima = BL
        """
        pts = np.array(pontos, dtype="float32").reshape(4, 2)
        ordenados = np.zeros((4, 2), dtype="float32")

        soma = pts.sum(axis=1)
        ordenados[0] = pts[np.argmin(soma)]
        ordenados[2] = pts[np.argmax(soma)]

        diferenca = np.diff(pts, axis=1).ravel()
        ordenados[1] = pts[np.argmin(diferenca)]
        ordenados[3] = pts[np.argmax(diferenca)]

        return ordenados

    def _aplicar_warp(self, imagem: np.ndarray, cantos: np.ndarray) -> np.ndarray:
        """getPerspectiveTransform + warpPerspective para 800x1000."""
        cfg = self.config
        origem = self.ordenar_pontos(cantos)
        destino = np.array(
            [
                [0, 0],
                [cfg.largura_warp - 1, 0],
                [cfg.largura_warp - 1, cfg.altura_warp - 1],
                [0, cfg.altura_warp - 1],
            ],
            dtype="float32",
        )
        matriz = cv2.getPerspectiveTransform(origem, destino)
        folha = cv2.warpPerspective(
            imagem, matriz, (cfg.largura_warp, cfg.altura_warp)
        )
        return self._corrigir_orientacao(folha)

    # --------------------------------------------------------------
    # ETAPA 6 - BINARIZACAO
    # --------------------------------------------------------------
    def binarizar(self, folha: np.ndarray) -> np.ndarray:
        """
        Cinza -> Gaussian Blur -> THRESH_BINARY_INV + THRESH_OTSU.
        Bolhas preenchidas (escuras) viram pixels BRANCOS, o que permite
        medir densidade com cv2.countNonZero.
        """
        cinza = cv2.cvtColor(folha, cv2.COLOR_BGR2GRAY)
        suavizada = cv2.GaussianBlur(cinza, (5, 5), 0)
        _, binaria = cv2.threshold(
            suavizada, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        return binaria

    def _lado_medio_roi(self) -> float:
        """Lado tipico da area de leitura, em pixels do espaco retificado."""
        if not self._rois:
            return 40.0
        larguras = [w for linha in self._rois for (_x, _y, w, _h) in linha]
        return float(np.median(larguras))

    def normalizar_iluminacao(self, folha: np.ndarray) -> np.ndarray:
        """
        Remove o gradiente de luz da folha antes de medir as bolhas.

        Foto de celular quase nunca tem luz uniforme: a sombra do proprio
        fotografo escurece um lado do papel. Com limiar global, esse lado
        inteiro passava a "parecer preenchido" e a questao virava rasura.

        A correcao estima o FUNDO com um fechamento morfologico usando
        kernel maior que as bolhas — a operacao apaga as marcas escuras e
        deixa so a iluminacao — e divide a imagem por ele. O que sobra e
        a folha como se estivesse sob luz uniforme.

        Returns:
            Imagem em escala de cinza, 0..255, com fundo achatado em branco.
        """
        cinza = cv2.cvtColor(folha, cv2.COLOR_BGR2GRAY)

        # Kernel maior que a bolha: precisa engoli-la para o fundo nao
        # levar a marca junto.
        lado = int(max(31, self._lado_medio_roi() * 3)) | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (lado, lado))
        fundo = cv2.morphologyEx(cinza, cv2.MORPH_CLOSE, kernel)
        fundo = cv2.GaussianBlur(fundo, (0, 0), lado / 4.0)

        # Evita divisao por zero em regioes totalmente pretas.
        fundo = np.maximum(fundo, 1)
        return cv2.divide(cinza, fundo, scale=255)

    # --------------------------------------------------------------
    # ETAPA 7 - MAPEAMENTO DA GRADE
    # --------------------------------------------------------------
    def _mapear_grade(self) -> List[List[Tuple[int, int, int, int]]]:
        """
        Pre-calcula a ROI (x, y, w, h) de cada bolha, na ordem das questoes.

        A geometria vem de `OMRConfig.calcular_blocos()` - a mesma usada
        pelo gerador do cartao.
        """
        cfg = self.config
        rois: List[List[Tuple[int, int, int, int]]] = []

        for bloco in self.blocos:
            if cfg.roi_quadrada:
                lado = min(bloco.passo_x, bloco.passo_y) * cfg.roi_escala
                meia_largura = meia_altura = lado / 2.0
            else:
                meia_largura = bloco.passo_x * cfg.roi_escala / 2.0
                meia_altura = bloco.passo_y * cfg.roi_escala / 2.0

            for indice_linha in range(bloco.total_linhas):
                linha: List[Tuple[int, int, int, int]] = []
                for indice_alt in range(cfg.total_alternativas):
                    cx, cy = bloco.centro(indice_linha, indice_alt)
                    linha.append(
                        (
                            int(round(cx - meia_largura)),
                            int(round(cy - meia_altura)),
                            max(1, int(round(meia_largura * 2))),
                            max(1, int(round(meia_altura * 2))),
                        )
                    )
                rois.append(linha)

        return rois

    # --------------------------------------------------------------
    # ETAPA 8 - DETECCAO DA MARCACAO
    # --------------------------------------------------------------
    def _detectar_respostas(self, normalizada: np.ndarray) -> List[dict]:
        """
        Decide qual bolha o aluno marcou, em cada questao.

        Mede a ESCURIDAO media de cada area de leitura (0 = papel limpo,
        1 = totalmente preenchido) sobre a imagem ja corrigida de
        iluminacao, e compara as cinco alternativas ENTRE SI.

        Por que a comparacao e relativa e nao contra um valor fixo:

        - Caneta azul, lapis e caneta preta deixam marcas de escuridao
          bem diferentes. Um corte fixo calibrado para caneta preta
          descarta o lapis; calibrado para lapis, aceita sujeira.
        - Sombra escurece as cinco alternativas juntas, entao a
          DIFERENCA entre elas sobrevive ao que o valor absoluto perde.
        - Preenchimento incompleto — o aluno que so risca a bolha —
          continua muito mais escuro que as quatro vizinhas intactas,
          mesmo ficando longe de qualquer limiar absoluto.

        O piso absoluto entra apenas para o caso da folha em branco: sem
        ele, ruido elegeria sempre alguma "vencedora".
        """
        cfg = self.config
        altura_img, largura_img = normalizada.shape[:2]
        leituras: List[dict] = []

        for indice_questao, linha in enumerate(self._rois):
            escuridoes: List[float] = []

            for (x, y, w, h) in linha:
                x0, y0 = max(0, x), max(0, y)
                x1, y1 = min(largura_img, x + w), min(altura_img, y + h)
                if x1 <= x0 or y1 <= y0:
                    escuridoes.append(0.0)
                    continue

                roi = normalizada[y0:y1, x0:x1]
                # 0 = branco do papel, 1 = preto cheio.
                escuridoes.append(1.0 - float(roi.mean()) / 255.0)

            ordem = sorted(
                range(len(escuridoes)), key=lambda i: escuridoes[i], reverse=True
            )
            melhor_idx = ordem[0]
            melhor = escuridoes[melhor_idx]
            segundo = escuridoes[ordem[1]] if len(ordem) > 1 else 0.0

            # A referencia e a MEDIANA das nao-vencedoras: a media seria
            # puxada para cima justamente pela segunda bolha quando ha
            # rasura, escondendo o caso que mais interessa detectar.
            demais = [escuridoes[i] for i in ordem[1:]]
            base = float(np.median(demais)) if demais else 0.0

            destaque = melhor - base
            destaque_segundo = segundo - base

            marcada: Optional[str] = None
            rasura = False

            if destaque >= cfg.margem_relativa and melhor >= cfg.piso_escuridao:
                if destaque_segundo >= cfg.margem_relativa * cfg.limiar_rasura:
                    rasura = True
                else:
                    marcada = ALTERNATIVAS[melhor_idx]

            leituras.append(
                {
                    "questao": indice_questao + 1,
                    "marcada": marcada,
                    # Quanto a vencedora destoou das demais: e essa a
                    # medida que sustenta a decisao, entao e ela que o
                    # professor precisa ver quando confere um resultado.
                    "confianca": round(float(min(1.0, max(0.0, destaque))), 4),
                    "rasura": rasura,
                    "escuridoes": {
                        ALTERNATIVAS[i]: round(float(e), 4)
                        for i, e in enumerate(escuridoes)
                    },
                    "densidades": {
                        ALTERNATIVAS[i]: round(float(e), 4)
                        for i, e in enumerate(escuridoes)
                    },
                }
            )

        return leituras

    # --------------------------------------------------------------
    # ETAPAS 9 E 10 - CORRECAO E NOTA
    # --------------------------------------------------------------
    def _corrigir(self, leituras: List[dict], gabarito: Dict[int, str]) -> dict:
        """Compara as leituras com o gabarito e calcula a nota (0..10)."""
        detalhamento: List[dict] = []
        acertos = erros = em_branco = rasuras = 0

        for leitura in leituras:
            numero = leitura["questao"]
            marcada = leitura["marcada"]
            correta = gabarito.get(numero)

            if leitura["rasura"]:
                status = "rasura"
                rasuras += 1
            elif marcada is None:
                status = "em_branco"
                em_branco += 1
            elif correta is not None and marcada == correta:
                status = "correto"
                acertos += 1
            else:
                status = "incorreto"
                erros += 1

            detalhamento.append(
                {
                    "questao": numero,
                    "marcada": marcada,
                    "correta": correta,
                    "status": status,
                    "confianca": leitura["confianca"],
                }
            )

        # As ROIs sao a fonte da verdade nos dois modos: calculadas a
        # partir da config no cartao gerado por nos, ou detectadas na
        # folha quando o cartao e da propria escola.
        total = len(self._rois)
        nota = round((acertos / total) * 10.0, 2) if total else 0.0

        return {
            "sucesso": True,
            "nota": nota,
            "total_questoes": total,
            "acertos": acertos,
            "erros": erros,
            "em_branco": em_branco,
            "rasuras": rasuras,
            "detalhamento": detalhamento,
        }

    # --------------------------------------------------------------
    # UTILITARIO DE CALIBRACAO
    # --------------------------------------------------------------
    def gerar_preview_debug(self, imagem_bytes: bytes) -> bytes:
        """PNG da folha retificada com as ROIs desenhadas em vermelho."""
        imagem = self._decodificar_imagem(imagem_bytes)
        folha, _ = self.alinhar_folha(imagem)
        binaria = self.binarizar(folha)
        preview = cv2.cvtColor(binaria, cv2.COLOR_GRAY2BGR)

        for linha in self._rois:
            for (x, y, w, h) in linha:
                cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 0, 255), 1)

        ok, buffer = cv2.imencode(".png", preview)
        if not ok:
            raise OMRError("Falha ao gerar o preview de calibracao.")
        return buffer.tobytes()


# ==================================================================
# CACHE DE MOTORES
# ==================================================================
# Cada numero de questoes gera uma grade diferente. O calculo e barato,
# mas se repete a cada requisicao: guardamos os motores prontos.
_cache_motores: Dict[int, OMREngine] = {}


def obter_engine(total_questoes: int) -> OMREngine:
    """
    Devolve (criando se preciso) o motor para N questoes.

    Cartao proprio da escola nao passa por aqui: use `engine_com_layout`,
    que nao usa cache — cada cartao tem geometria propria, e um cache
    por numero de questoes devolveria a grade errada para a prova
    seguinte.
    """
    if total_questoes not in _cache_motores:
        _cache_motores[total_questoes] = OMREngine(
            CONFIG_PADRAO.com_questoes(total_questoes)
        )
    return _cache_motores[total_questoes]


def engine_com_layout(rois: List[List[Tuple[int, int, int, int]]]) -> OMREngine:
    """
    Motor que le nas coordenadas detectadas no cartao do professor.

    Nao entra no cache: cada prova com folha propria tem seu layout, e
    guardar por numero de questoes misturaria layouts diferentes de
    mesmo tamanho.
    """
    if not rois:
        raise OMRError("Layout vazio: nao ha onde procurar as bolhas.")
    total = len(rois)
    alternativas = len(rois[0])
    config = OMRConfig(
        **{
            **CONFIG_PADRAO.__dict__,
            "total_questoes": total,
            "total_alternativas": alternativas,
        }
    )
    return OMREngine(config, rois=rois)
