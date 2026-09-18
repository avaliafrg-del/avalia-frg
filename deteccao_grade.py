"""
deteccao_grade.py
------------------------------------------------------------------
Descobre onde estão as bolhas num cartão-resposta que NÃO foi gerado
por este sistema.

Por que este módulo existe
--------------------------
O motor de leitura original sabia onde procurar porque ele mesmo tinha
desenhado a folha. Quando o professor traz o cartão dele, essa certeza
some: espaçamento, tamanho de bolha, número de colunas e margens são
outros. Adicionar só o QR Code a uma folha alheia produziria um papel
bonito e ilegível.

A saída daqui é um LAYOUT — a lista de regiões onde ficam as bolhas, em
coordenadas do espaço retificado de 800x1000. Guardado junto com a
prova, ele substitui a geometria calculada e faz o mesmo motor ler
qualquer cartão.

Limite honesto: a detecção acerta em cartões de grade regular, que é o
formato de praticamente todo cartão-resposta. Layouts exóticos — bolhas
em diagonal, tamanhos misturados — vão falhar, e é por isso que a
interface mostra a grade detectada para conferência ANTES de imprimir.
------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ==================================================================
# PARAMETROS DA DETECCAO
# ==================================================================
# Faixa de raio aceitável para uma bolha, no espaço retificado.
# Abaixo de 6 px seria ruído de impressão; acima de 40 seria um
# desenho ou moldura, não uma alternativa.
RAIO_MINIMO = 6.0
RAIO_MAXIMO = 40.0
# 4πA/P²: 1.0 é o círculo perfeito. Bolha impressa e digitalizada fica
# acima de 0.62; retângulo fica perto de 0.78 mas é barrado depois pela
# razão de aspecto e pela regularidade da grade.
CIRCULARIDADE_MINIMA = 0.62
RAZAO_ASPECTO = (0.70, 1.43)
# Duas bolhas na mesma linha não podem diferir mais que isto em y.
TOLERANCIA_LINHA = 0.6      # em múltiplos do raio mediano
TOLERANCIA_COLUNA = 0.8
# Uma folha com menos que isso não é um cartão-resposta.
MINIMO_BOLHAS = 10
# Raio, em torno de cada canto da folha retificada, onde ficam as
# âncoras. Um quadrado tem circularidade 0,785 — acima do limiar — e
# seria detectado como bolha, criando colunas fantasma nas bordas.
ZONA_ANCORA = 70


class GradeNaoDetectada(Exception):
    """Não foi possível reconhecer uma grade de bolhas na folha."""


# ==================================================================
# ESTRUTURA
# ==================================================================
@dataclass
class LayoutDetectado:
    """
    Onde estão as bolhas, e como elas viram questões.

    `rois` é a lista de regiões na ordem das questões: rois[0] são as
    alternativas da questão 1. Cada região é (x, y, largura, altura) no
    espaço retificado.
    """

    rois: List[List[Tuple[int, int, int, int]]] = field(default_factory=list)
    total_questoes: int = 0
    total_alternativas: int = 0
    colunas_de_questoes: int = 1
    raio_medio: float = 0.0
    # Avisos que o professor precisa ler antes de imprimir
    avisos: List[str] = field(default_factory=list)

    def para_json(self) -> dict:
        return {
            "rois": [[list(roi) for roi in linha] for linha in self.rois],
            "total_questoes": self.total_questoes,
            "total_alternativas": self.total_alternativas,
            "colunas_de_questoes": self.colunas_de_questoes,
            "raio_medio": round(self.raio_medio, 2),
            "avisos": self.avisos,
        }

    @staticmethod
    def de_json(dados: dict) -> "LayoutDetectado":
        return LayoutDetectado(
            rois=[[tuple(roi) for roi in linha] for linha in dados.get("rois", [])],
            total_questoes=dados.get("total_questoes", 0),
            total_alternativas=dados.get("total_alternativas", 0),
            colunas_de_questoes=dados.get("colunas_de_questoes", 1),
            raio_medio=dados.get("raio_medio", 0.0),
            avisos=dados.get("avisos", []),
        )


# ==================================================================
# DETECCAO
# ==================================================================
def _encontrar_circulos(folha: np.ndarray) -> List[Tuple[float, float, float]]:
    """
    Devolve (cx, cy, raio) de cada bolha vazia encontrada.

    Usa contornos e não HoughCircles: bolha impressa é um anel fino e
    bem definido, que o contorno pega com folga, enquanto o Hough exige
    calibrar raio mínimo e máximo — justamente o que não se sabe de
    antemão num cartão alheio.
    """
    cinza = cv2.cvtColor(folha, cv2.COLOR_BGR2GRAY) if folha.ndim == 3 else folha
    suavizada = cv2.GaussianBlur(cinza, (5, 5), 0)

    # Threshold adaptativo: o cartão do professor pode chegar com
    # iluminação irregular, e o Otsu global perderia bolhas de um lado
    # da folha.
    binaria = cv2.adaptiveThreshold(
        suavizada, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 8
    )

    contornos, _ = cv2.findContours(
        binaria, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )

    circulos: List[Tuple[float, float, float]] = []
    for contorno in contornos:
        area = cv2.contourArea(contorno)
        if area < 20:
            continue

        perimetro = cv2.arcLength(contorno, True)
        if perimetro <= 0:
            continue

        circularidade = 4 * np.pi * area / (perimetro * perimetro)
        if circularidade < CIRCULARIDADE_MINIMA:
            continue

        (cx, cy), raio = cv2.minEnclosingCircle(contorno)
        if not (RAIO_MINIMO <= raio <= RAIO_MAXIMO):
            continue

        x, y, largura, altura = cv2.boundingRect(contorno)
        if altura == 0:
            continue
        razao = largura / float(altura)
        if not (RAZAO_ASPECTO[0] <= razao <= RAZAO_ASPECTO[1]):
            continue

        circulos.append((cx, cy, raio))

    return _manter_tamanho_dominante(
        _remover_duplicados(_fora_dos_cantos(circulos, folha.shape))
    )


def _manter_tamanho_dominante(
    circulos: List[Tuple[float, float, float]], tolerancia: float = 0.28
) -> List[Tuple[float, float, float]]:
    """
    Fica só com os círculos do tamanho dominante da folha.

    Numa grade impressa todas as bolhas têm o MESMO diâmetro. O que
    aparece fora desse tamanho é outra coisa — e a "outra coisa" mais
    comum é o dígito 0 do número da questão, redondo o bastante para
    passar no filtro de circularidade.

    Sem este corte, "01" contribuía com uma coluna fantasma à esquerda
    de cada bloco: um cartão de duas colunas virava 14 colunas em vez
    de 10, e a grade era recusada como irregular.
    """
    if len(circulos) < MINIMO_BOLHAS:
        return circulos

    raio_dominante = float(np.median([c[2] for c in circulos]))
    limite_baixo = raio_dominante * (1 - tolerancia)
    limite_alto = raio_dominante * (1 + tolerancia)

    filtrados = [c for c in circulos if limite_baixo <= c[2] <= limite_alto]

    # Se o corte for agressivo demais, é melhor devolver tudo e deixar
    # o agrupamento tentar do que falhar aqui sem explicação.
    if len(filtrados) < MINIMO_BOLHAS:
        return circulos

    descartados = len(circulos) - len(filtrados)
    if descartados:
        logger.info(
            "Descartados %d círculos fora do tamanho dominante (%.1f px)",
            descartados,
            raio_dominante,
        )
    return filtrados


def _fora_dos_cantos(
    circulos: List[Tuple[float, float, float]], forma
) -> List[Tuple[float, float, float]]:
    """Descarta o que está em cima das âncoras."""
    altura, largura = forma[:2]
    cantos = [(0, 0), (largura, 0), (largura, altura), (0, altura)]

    return [
        (cx, cy, raio)
        for cx, cy, raio in circulos
        if all(
            (cx - ax) ** 2 + (cy - ay) ** 2 > ZONA_ANCORA**2 for ax, ay in cantos
        )
    ]


def _remover_duplicados(
    circulos: List[Tuple[float, float, float]]
) -> List[Tuple[float, float, float]]:
    """
    Uma bolha vazia gera dois contornos: a borda externa e a interna do
    anel. Sem isto, cada alternativa apareceria duas vezes e a grade
    sairia com o dobro das colunas.
    """
    if not circulos:
        return []

    circulos = sorted(circulos, key=lambda c: -c[2])   # maiores primeiro
    mantidos: List[Tuple[float, float, float]] = []

    for cx, cy, raio in circulos:
        repetido = any(
            (cx - mx) ** 2 + (cy - my) ** 2 < (mraio * 0.8) ** 2
            for mx, my, mraio in mantidos
        )
        if not repetido:
            mantidos.append((cx, cy, raio))

    return mantidos


def _descartar_grupos_ralos(
    grupos: List[List[int]], fracao_minima: float = 0.45
) -> List[List[int]]:
    """
    Remove linhas e colunas com poucos membros.

    É o filtro que separa bolha de ruído sem depender de calibrar
    tamanho: um "0" impresso no número da questão passa pelos testes de
    circularidade, mas fica sozinho na sua coluna, enquanto uma coluna
    de alternativas tem uma bolha por linha. Comparar o tamanho do
    grupo com a mediana dos grupos resolve isso em qualquer layout —
    calibrar raio só resolveria neste cartão.
    """
    if not grupos:
        return grupos

    tamanhos = sorted(len(g) for g in grupos)
    mediana = tamanhos[len(tamanhos) // 2]
    limite = max(2, int(mediana * fracao_minima))

    filtrados = [g for g in grupos if len(g) >= limite]
    if len(filtrados) < len(grupos):
        logger.info(
            "Detecção descartou %d grupo(s) esparso(s) — provável texto da folha.",
            len(grupos) - len(filtrados),
        )
    return filtrados or grupos


def _agrupar(valores: List[float], tolerancia: float) -> List[List[int]]:
    """
    Agrupa índices cujos valores estão a menos de `tolerancia` entre si.

    Um k-means precisaria saber o número de grupos de antemão — que é
    exatamente o que se quer descobrir.
    """
    ordenados = sorted(range(len(valores)), key=lambda i: valores[i])
    grupos: List[List[int]] = []
    atual: List[int] = []

    for indice in ordenados:
        if not atual:
            atual = [indice]
            continue
        anterior = valores[atual[-1]]
        if abs(valores[indice] - anterior) <= tolerancia:
            atual.append(indice)
        else:
            grupos.append(atual)
            atual = [indice]

    if atual:
        grupos.append(atual)
    return grupos


def detectar_grade(
    folha: np.ndarray,
    alternativas_esperadas: Optional[int] = None,
    questoes_esperadas: Optional[int] = None,
) -> LayoutDetectado:
    """
    Monta o layout a partir da folha JÁ RETIFICADA (800x1000).

    Args:
        folha: imagem retificada do cartão em branco.
        alternativas_esperadas: quando o professor informa (5, por
            exemplo), serve para validar e avisar em vez de aceitar
            silenciosamente uma leitura errada.
        questoes_esperadas: idem, para o número de questões.

    Raises:
        GradeNaoDetectada: quando não há bolhas suficientes ou a grade
            é irregular demais para confiar.
    """
    circulos = _encontrar_circulos(folha)

    if len(circulos) < MINIMO_BOLHAS:
        raise GradeNaoDetectada(
            f"Encontrei apenas {len(circulos)} bolhas nesta folha. Verifique se "
            f"o arquivo é o cartão-resposta em branco, com as alternativas "
            f"impressas como círculos vazios."
        )

    raios = [c[2] for c in circulos]
    raio_medio = float(np.median(raios))
    avisos: List[str] = []

    # --- Colunas: agrupa todas as bolhas pelo x ---------------------
    xs = [c[0] for c in circulos]
    grupos_x = _descartar_grupos_ralos(_agrupar(xs, raio_medio * TOLERANCIA_COLUNA))
    centros_coluna = sorted(
        float(np.mean([xs[i] for i in grupo])) for grupo in grupos_x
    )

    # --- Linhas: agrupa pelo y --------------------------------------
    ys = [c[1] for c in circulos]
    grupos_y = _descartar_grupos_ralos(_agrupar(ys, raio_medio * TOLERANCIA_LINHA))
    centros_linha = sorted(
        float(np.mean([ys[i] for i in grupo])) for grupo in grupos_y
    )

    if not centros_coluna or not centros_linha:
        raise GradeNaoDetectada("Não consegui organizar as bolhas em linhas e colunas.")

    # --- Quantas alternativas por questão ---------------------------
    # Um cartão com 2 blocos de 5 alternativas produz 10 colunas. O
    # espaço ENTRE blocos é bem maior que entre alternativas, e é isso
    # que separa um caso do outro.
    blocos_x = _separar_blocos(centros_coluna, raio_medio)
    blocos_x, descartados = _manter_blocos_regulares(blocos_x)
    if descartados:
        # Resto do desenho da folha — um traço, uma moldura — pode
        # sobreviver aos filtros anteriores e formar uma coluna solta.
        # Um bloco de questões tem TODAS as alternativas; o que não
        # tem, não é bloco.
        logger.info("Detecção descartou %d coluna(s) irregular(es).", descartados)

    alternativas = len(blocos_x[0])

    if alternativas_esperadas and alternativas != alternativas_esperadas:
        avisos.append(
            f"Detectei {alternativas} alternativas por questão, e você informou "
            f"{alternativas_esperadas}. Confira a prévia antes de imprimir."
        )



    # --- Monta as ROIs na ordem das questões ------------------------
    lado = raio_medio * 2 * 0.92
    meia = lado / 2.0
    rois: List[List[Tuple[int, int, int, int]]] = []

    # Cada bloco é uma coluna de questões, lida de cima para baixo;
    # depois passa para o bloco seguinte. É a numeração usada em
    # praticamente todo cartão-resposta.
    for bloco in blocos_x:
        for y in centros_linha:
            linha: List[Tuple[int, int, int, int]] = []
            for x in bloco:
                # Só cria a região se existe mesmo uma bolha ali: numa
                # coluna mais curta que as outras, as posições que
                # sobram não viram questão fantasma.
                if not _tem_bolha(circulos, x, y, raio_medio):
                    linha = []
                    break
                linha.append(
                    (
                        int(round(x - meia)),
                        int(round(y - meia)),
                        int(round(lado)),
                        int(round(lado)),
                    )
                )
            if linha:
                rois.append(linha)

    if not rois:
        raise GradeNaoDetectada("As bolhas encontradas não formam uma grade regular.")

    if questoes_esperadas and len(rois) != questoes_esperadas:
        avisos.append(
            f"Detectei {len(rois)} questões, e a prova tem {questoes_esperadas}. "
            f"Confira a prévia: pode haver bolhas fora da grade ou questões "
            f"que não foram reconhecidas."
        )

    return LayoutDetectado(
        rois=rois,
        total_questoes=len(rois),
        total_alternativas=alternativas,
        colunas_de_questoes=len(blocos_x),
        raio_medio=raio_medio,
        avisos=avisos,
    )


def _manter_blocos_regulares(
    blocos: List[List[float]]
) -> Tuple[List[List[float]], int]:
    """
    Fica só com os blocos que têm o número de alternativas dominante.

    Returns:
        (blocos_mantidos, quantos_descartados)
    """
    if len(blocos) <= 1:
        return blocos, 0

    tamanhos = [len(b) for b in blocos]
    dominante = max(set(tamanhos), key=tamanhos.count)
    mantidos = [b for b in blocos if len(b) == dominante]
    return mantidos, len(blocos) - len(mantidos)


def _separar_blocos(centros: List[float], raio: float) -> List[List[float]]:
    """
    Quebra as colunas em blocos de questões.

    O critério é o tamanho do vão: dentro de uma questão as
    alternativas ficam próximas; entre blocos há um vão bem maior.
    """
    if len(centros) < 2:
        return [centros]

    vaos = [centros[i + 1] - centros[i] for i in range(len(centros) - 1)]
    vao_tipico = float(np.median(vaos))
    # 1.8x a distância típica é folgado o bastante para não quebrar por
    # variação de impressão, e apertado o bastante para pegar o vão
    # entre blocos, que costuma passar de 2x.
    limite = vao_tipico * 1.8

    blocos: List[List[float]] = [[centros[0]]]
    for indice, vao in enumerate(vaos):
        if vao > limite:
            blocos.append([])
        blocos[-1].append(centros[indice + 1])

    return blocos


def _tem_bolha(
    circulos: List[Tuple[float, float, float]], x: float, y: float, raio: float
) -> bool:
    limite = (raio * 0.9) ** 2
    return any((cx - x) ** 2 + (cy - y) ** 2 < limite for cx, cy, _ in circulos)


# ==================================================================
# PREVIA PARA CONFERENCIA
# ==================================================================
def desenhar_previa(folha: np.ndarray, layout: LayoutDetectado) -> np.ndarray:
    """
    Marca a grade detectada sobre a folha.

    Existe porque detecção automática erra, e o professor precisa ver o
    que o sistema entendeu ANTES de imprimir a turma inteira — não
    depois, com 30 cartões corrigidos errado na mão.
    """
    previa = folha.copy() if folha.ndim == 3 else cv2.cvtColor(folha, cv2.COLOR_GRAY2BGR)

    for indice, linha in enumerate(layout.rois, start=1):
        for (x, y, largura, altura) in linha:
            cv2.rectangle(previa, (x, y), (x + largura, y + altura), (40, 70, 200), 2)

        if linha:
            x, y, _, altura = linha[0]
            cv2.putText(
                previa,
                str(indice),
                (max(0, x - 34), y + altura - 3),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 200),
                2,
                cv2.LINE_AA,
            )

    return previa
