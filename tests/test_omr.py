"""
tests/test_omr.py
------------------------------------------------------------------
Testes automatizados de ponta a ponta.

Os cartoes de teste sao gerados em memoria pelo proprio `gerador.py` e
depois distorcidos para simular uma foto de celular. Nenhuma imagem
precisa ser versionada no repositorio.

Executar (a partir da raiz do projeto):
    pip install pytest httpx
    pytest -v
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import json
import os
import random
import sys

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# Banco proprio da suite, definido ANTES de importar `database`, que le
# a variavel na hora do import. Sem isso os testes escreveriam no banco
# real do professor.
os.environ.setdefault("DATABASE_URL", "sqlite:///./teste_avaliafrg.db")

from gerador import (  # noqa: E402
    DadosCartao,
    gerar_cartao,
    montar_pdf_completo,
    parse_gabarito_digitado,
)
from main import app  # noqa: E402
from omr_engine import (  # noqa: E402
    CONFIG_PADRAO,
    OMRError,
    ler_qr_da_imagem,
    montar_qr_code,
    obter_engine,
    parse_qr_code,
)

SEGREDO_TESTE = "chave-de-teste-nao-usar-em-producao"


def corrigir_direto(dados: bytes, gabarito: dict[int, str]) -> dict:
    """Atalho: o motor agora recebe o gabarito ja resolvido."""
    return obter_engine(max(gabarito)).processar(dados, gabarito)

LETRAS = "ABCDE"


# ==================================================================
# HELPERS
# ==================================================================
def cartao_bytes(
    gabarito: dict[int, str],
    marcacoes: dict[int, str] | None = None,
    prova_id: str = "T1",
    como_foto: bool = False,
) -> bytes:
    """Gera o cartao e devolve os bytes PNG (ou JPG distorcido)."""
    dados = DadosCartao(
        prova_id=prova_id,
        gabarito=gabarito,
        titulo="Prova de teste",
        disciplina="Ciências",
        turma="8º ano",
    )
    imagem = gerar_cartao(dados, marcacoes=marcacoes)

    if como_foto:
        return simular_foto(imagem)

    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()


def simular_foto(imagem_pil: Image.Image) -> bytes:
    """
    Degrada a imagem como uma foto de celular real: perspectiva,
    desfoque, sombra lateral e compressao JPEG.
    """
    img = cv2.cvtColor(np.array(imagem_pil), cv2.COLOR_RGB2BGR)
    altura, largura = img.shape[:2]

    origem = np.float32([[0, 0], [largura, 0], [largura, altura], [0, altura]])
    destino = np.float32(
        [
            [largura * 0.05, altura * 0.03],
            [largura * 0.97, altura * 0.07],
            [largura * 0.94, altura * 0.98],
            [largura * 0.02, altura * 0.93],
        ]
    )
    img = cv2.warpPerspective(
        img,
        cv2.getPerspectiveTransform(origem, destino),
        (largura, altura),
        borderValue=(255, 255, 255),
    )
    img = cv2.GaussianBlur(img, (3, 3), 0)

    # Gradiente de iluminacao (sombra do lado esquerdo)
    gradiente = np.linspace(0.72, 1.0, largura).astype(np.float32)
    img = np.clip(img.astype(np.float32) * gradiente[None, :, None], 0, 255).astype(
        np.uint8
    )

    ok, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buffer.tobytes()


def gabarito_aleatorio(total: int, semente: int = 1) -> dict[int, str]:
    random.seed(semente)
    return {n: random.choice(LETRAS) for n in range(1, total + 1)}


@pytest.fixture(scope="module")
def client() -> TestClient:
    """
    Cliente ja autenticado.

    Todas as rotas de dados exigem sessao desde que o login entrou: um
    teste sem login so testaria a tela de acesso.
    """
    import dados as db

    db.criar_tabelas()
    cliente = TestClient(app)

    credenciais = {"email": "teste@escola.br", "senha": "senha-de-teste"}
    with db.SessionLocal() as sessao:
        precisa_criar = not db.ha_usuarios(sessao)

    if precisa_criar:
        resposta = cliente.post(
            "/api/auth/primeira-conta",
            data={**credenciais, "nome": "Professor de Teste"},
        )
        pytest.ticket_admin = resposta.json()["ticket"]
    else:
        cliente.post(
            "/api/auth/entrar",
            data={**credenciais, "ticket": pytest.ticket_admin},
        )

    return cliente


@pytest.fixture(scope="module")
def segredo_qr() -> str:
    """Chave que a instalacao usa para assinar os cartoes."""
    import dados as db

    db.criar_tabelas()
    with db.SessionLocal() as sessao:
        return db.obter_segredo_qr(sessao)


# ==================================================================
# PARSER DO GABARITO
# ==================================================================
@pytest.mark.parametrize(
    "entrada",
    ["A,B,C", "ABC", "1A,2B,3C", "1-A, 2-B, 3-C", "a, b, c"],
)
def test_gabarito_aceita_formatos_variados(entrada):
    assert parse_gabarito_digitado(entrada) == {1: "A", 2: "B", 3: "C"}


def test_gabarito_rejeita_letra_invalida():
    with pytest.raises(OMRError):
        parse_gabarito_digitado("A,B,Z")


def test_gabarito_rejeita_questao_faltando():
    with pytest.raises(OMRError):
        parse_gabarito_digitado("1A,3C")


def test_qr_usa_forma_compacta_quando_contiguo():
    """Gabarito completo vira 'ID*ABC' — QR menor, mais facil de ler."""
    gabarito = {1: "A", 2: "B", 3: "C"}
    assert montar_qr_code("77", gabarito) == "77*ABC"


def test_qr_mantem_numeros_quando_ha_lacuna():
    assert montar_qr_code("77", {1: "A", 5: "C"}) == "77|1A,5C"


def test_ida_e_volta_do_qr():
    gabarito = gabarito_aleatorio(12)
    leitura = parse_qr_code(montar_qr_code("X9", gabarito))
    assert leitura.prova_id == "X9"
    assert leitura.gabarito == gabarito


# ==================================================================
# GEOMETRIA
# ==================================================================
@pytest.mark.parametrize("total", [1, 5, 10, 20, 21, 40, 60, 100, 140])
def test_blocos_cobrem_todas_as_questoes(total):
    """A grade nunca pode perder nem duplicar uma questao."""
    blocos = CONFIG_PADRAO.com_questoes(total).calcular_blocos()
    numeros = [
        n for b in blocos for n in range(b.questao_inicial, b.questao_final + 1)
    ]
    assert numeros == list(range(1, total + 1))


def test_layout_escolhe_o_que_deixa_a_bolha_maior():
    """
    Nao basta usar o menor numero de colunas: numa prova de 30 questoes,
    2 colunas dobram o tamanho da bolha em relacao a 1 coluna.
    """
    def passo(total):
        blocos = CONFIG_PADRAO.com_questoes(total).calcular_blocos()
        return min(blocos[0].passo_x, blocos[0].passo_y)

    assert len(CONFIG_PADRAO.com_questoes(10).calcular_blocos()) == 1
    assert len(CONFIG_PADRAO.com_questoes(30).calcular_blocos()) == 2
    assert passo(30) > passo(40)          # menos questoes, bolha maior
    assert passo(30) > 40                 # confortavel de preencher


def test_numero_de_colunas_cresce_com_a_prova():
    colunas = lambda n: len(CONFIG_PADRAO.com_questoes(n).calcular_blocos())
    assert colunas(10) <= colunas(50) <= colunas(100) <= 4


def test_limite_vem_da_geometria_e_nao_de_um_teto_fixo():
    """
    O maximo e descoberto por busca sobre a propria geometria: a folha e
    recusada quando a bolha ficaria menor que `passo_minimo`, nao quando
    passa de um numero escolhido a dedo.
    """
    maximo = CONFIG_PADRAO.maximo_questoes
    assert maximo > 100                                   # cobre prova longa
    CONFIG_PADRAO.com_questoes(maximo)                    # ainda passa
    with pytest.raises(OMRError, match="pequenas demais"):
        CONFIG_PADRAO.com_questoes(maximo + 1)


def test_rejeita_numero_de_questoes_invalido():
    with pytest.raises(OMRError):
        CONFIG_PADRAO.com_questoes(0)
    with pytest.raises(OMRError):
        CONFIG_PADRAO.com_questoes(1000)


def test_bolha_nunca_fica_menor_que_o_minimo():
    """A bolha impressa precisa continuar preenchivel em toda a faixa."""
    for total in range(1, CONFIG_PADRAO.maximo_questoes + 1, 7):
        blocos = CONFIG_PADRAO.com_questoes(total).calcular_blocos()
        passo = min(blocos[0].passo_x, blocos[0].passo_y)
        assert passo >= CONFIG_PADRAO.passo_minimo, f"{total} questoes"


# ==================================================================
# LEITURA DO QR NA IMAGEM
# ==================================================================
def test_le_qr_de_uma_foto_degradada():
    gabarito = gabarito_aleatorio(15)
    dados = cartao_bytes(gabarito, prova_id="QR1", como_foto=True)
    imagem = obter_engine(15)._decodificar_imagem(dados)
    texto = ler_qr_da_imagem(imagem)
    assert texto is not None
    leitura = parse_qr_code(texto)
    assert leitura.prova_id == "QR1"
    assert leitura.gabarito == gabarito


def test_sem_qr_e_sem_gabarito_falha_com_mensagem_util():
    branco = Image.new("RGB", (800, 1000), "white")
    buffer = io.BytesIO()
    branco.save(buffer, format="PNG")
    with pytest.raises(OMRError, match="QR"):
        texto = ler_qr_da_imagem(
            obter_engine(10)._decodificar_imagem(buffer.getvalue())
        )
        if not texto:
            raise OMRError("Nao foi possivel ler o QR Code da imagem.")


# ==================================================================
# CORRECAO
# ==================================================================
@pytest.mark.parametrize("total", [5, 10, 20, 30, 40, 60, 90, 120, CONFIG_PADRAO.maximo_questoes])
def test_prova_perfeita_em_varios_tamanhos(total):
    gabarito = gabarito_aleatorio(total, semente=total)
    dados = cartao_bytes(gabarito, marcacoes=gabarito, como_foto=True)
    resultado = corrigir_direto(dados, gabarito)

    assert resultado["acertos"] == total
    assert resultado["nota"] == 10.0
    assert resultado["alinhamento"] == "ancoras"


def test_detecta_erro_branco_e_rasura():
    gabarito = {n: "A" for n in range(1, 11)}
    marcacoes = dict(gabarito)
    marcacoes[2] = "C"     # errada
    marcacoes[8] = "AB"    # rasura
    del marcacoes[9]       # em branco

    dados = cartao_bytes(gabarito, marcacoes=marcacoes, como_foto=True)
    resultado = corrigir_direto(dados, gabarito)
    status = {d["questao"]: d["status"] for d in resultado["detalhamento"]}

    assert status[1] == "correto"
    assert status[2] == "incorreto"
    assert status[8] == "rasura"
    assert status[9] == "em_branco"
    assert resultado["acertos"] == 7
    assert resultado["nota"] == 7.0


def test_separacao_entre_bolha_marcada_e_vazia():
    """
    A bolha marcada precisa DESTOAR das vizinhas com folga.

    A medida e a diferenca dentro da propria questao, e nao um valor
    absoluto: e ela que sustenta a decisao desde que a leitura passou a
    ser relativa.
    """
    engine = obter_engine(10)
    dados = cartao_bytes({n: "A" for n in range(1, 11)}, marcacoes={1: "A"})
    imagem = engine._decodificar_imagem(dados)
    folha, _ = engine.alinhar_folha(imagem)
    leitura = engine._detectar_respostas(engine.normalizar_iluminacao(folha))[0]

    escuridoes = leitura["escuridoes"]
    vazias = [escuridoes[letra] for letra in "BCDE"]
    destaque = escuridoes["A"] - max(vazias)

    assert leitura["marcada"] == "A"
    # Pelo menos o quadruplo da margem exigida para decidir
    assert destaque > CONFIG_PADRAO.margem_relativa * 4


def test_imagem_invalida():
    with pytest.raises(OMRError):
        obter_engine(10).processar(b"isso-nao-e-uma-imagem", {1: "A"})


# ==================================================================
# GERACAO DO CARTAO
# ==================================================================
def test_pdf_do_cartao_tem_uma_pagina():
    from pypdf import PdfReader

    dados = DadosCartao(prova_id="1", gabarito=gabarito_aleatorio(10))
    pdf = montar_pdf_completo(gerar_cartao(dados))
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1


def test_pdf_anexa_a_prova_antes_do_cartao():
    from pypdf import PdfReader, PdfWriter

    escritor = PdfWriter()
    for _ in range(3):
        escritor.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    escritor.write(buffer)

    dados = DadosCartao(prova_id="1", gabarito=gabarito_aleatorio(10))
    pdf = montar_pdf_completo(gerar_cartao(dados), buffer.getvalue(), "prova.pdf")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 4


def test_pdf_aceita_prova_em_imagem():
    from pypdf import PdfReader

    pagina = Image.new("RGB", (1240, 1754), "white")
    buffer = io.BytesIO()
    pagina.save(buffer, format="PNG")

    dados = DadosCartao(prova_id="1", gabarito=gabarito_aleatorio(10))
    pdf = montar_pdf_completo(gerar_cartao(dados), buffer.getvalue(), "prova.png")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 2


# ==================================================================
# API
# ==================================================================
def test_pagina_inicial_serve_a_interface(client):
    resposta = client.get("/")
    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_endpoint_gera_cartao_png(client):
    resposta = client.post(
        "/api/cartao", data={"gabarito": "A,B,C,D,E", "prova_id": "9", "formato": "png"}
    )
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/png"


def test_endpoint_gera_cartao_pdf(client):
    resposta = client.post(
        "/api/cartao", data={"gabarito": "A,B,C,D,E", "prova_id": "9", "formato": "pdf"}
    )
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"


def test_endpoint_recusa_gabarito_invalido(client):
    resposta = client.post("/api/cartao", data={"gabarito": "A,B,Z"})
    assert resposta.status_code == 400
    assert resposta.json()["sucesso"] is False


def test_endpoint_corrige_sem_informar_gabarito(client):
    """O caso principal: o professor so manda a foto."""
    gabarito = gabarito_aleatorio(10, semente=42)
    dados = cartao_bytes(gabarito, marcacoes=gabarito, prova_id="55", como_foto=True)

    resposta = client.post(
        "/api/corrigir", files={"file": ("aluno.jpg", dados, "image/jpeg")}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["prova_id"] == "55"
    assert corpo["nota"] == 10.0
    assert corpo["origem_gabarito"] == "imagem"


def test_endpoint_ler_gabarito(client):
    gabarito = {1: "A", 2: "B", 3: "C"}
    dados = cartao_bytes(gabarito, prova_id="88")
    resposta = client.post(
        "/api/ler-gabarito", files={"file": ("c.png", dados, "image/png")}
    )
    assert resposta.status_code == 200
    assert resposta.json()["gabarito"] == ["A", "B", "C"]


def test_endpoint_lote_com_uma_foto_ruim(client):
    """Uma foto ilegivel nao pode derrubar a correcao da turma."""
    gabarito = gabarito_aleatorio(10, semente=7)
    boas = [
        ("files", (f"aluno_{i}.jpg", cartao_bytes(gabarito, gabarito, como_foto=True), "image/jpeg"))
        for i in range(3)
    ]
    branco = Image.new("RGB", (600, 800), "white")
    buffer = io.BytesIO()
    branco.save(buffer, format="JPEG")
    ruim = ("files", ("borrada.jpg", buffer.getvalue(), "image/jpeg"))

    resposta = client.post("/api/corrigir-lote", files=boas + [ruim])
    assert resposta.status_code == 200
    corpo = resposta.json()

    assert corpo["total_enviados"] == 4
    assert corpo["total_corrigidos"] == 3
    assert corpo["total_falhas"] == 1
    assert corpo["media_da_turma"] == 10.0
    assert any(not item["sucesso"] for item in corpo["itens"])


def test_endpoint_recusa_arquivo_que_nao_e_imagem(client):
    resposta = client.post(
        "/api/corrigir", files={"file": ("nota.txt", b"conteudo", "text/plain")}
    )
    assert resposta.status_code == 415


def test_endpoint_preview_de_calibracao(client):
    dados = cartao_bytes({1: "A", 2: "B"}, como_foto=True)
    resposta = client.post(
        "/api/debug/preview",
        files={"file": ("c.jpg", dados, "image/jpeg")},
        data={"total_questoes": "2"},
    )
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/png"


# ==================================================================
# ORIENTACAO
# ==================================================================
@pytest.mark.parametrize("como_foto", [False, True])
def test_foto_de_cabeca_para_baixo_e_corrigida(como_foto):
    """
    As 4 ancoras sao simetricas: sem tratamento, uma foto invertida
    produz um warp valido e o aluno leva zero sem aviso nenhum.
    """
    gabarito = gabarito_aleatorio(10, semente=5)
    dados = DadosCartao(prova_id="R1", gabarito=gabarito, titulo="Prova")
    imagem = gerar_cartao(dados, marcacoes=gabarito).rotate(180)

    if como_foto:
        conteudo = simular_foto(imagem)
    else:
        buffer = io.BytesIO()
        imagem.save(buffer, format="PNG")
        conteudo = buffer.getvalue()

    resultado = corrigir_direto(conteudo, gabarito)
    assert resultado["nota"] == 10.0


def test_margem_de_deteccao_se_mantem_em_provas_longas():
    """
    O destaque da bolha marcada nao pode encolher conforme a prova
    cresce: numa prova de 140 questoes as bolhas sao pequenas, e se a
    medida degradasse junto, a leitura ficaria no limite do ruido.
    """
    for total in (10, 90, CONFIG_PADRAO.maximo_questoes):
        gabarito = {n: "A" for n in range(1, total + 1)}
        dados = cartao_bytes(gabarito, marcacoes=gabarito, como_foto=True)

        engine = obter_engine(total)
        folha, _ = engine.alinhar_folha(engine._decodificar_imagem(dados))
        leituras = engine._detectar_respostas(engine.normalizar_iluminacao(folha))

        assert all(l["marcada"] == "A" for l in leituras), f"{total} questoes"

        menor_destaque = min(l["confianca"] for l in leituras)
        assert menor_destaque > CONFIG_PADRAO.margem_relativa * 3, (
            f"{total} questoes: destaque minimo {menor_destaque}"
        )


# ==================================================================
# DENSIDADE DO QR CODE
# ==================================================================
@pytest.mark.parametrize("total", [40, 60, 80, 100, 120, 140])
def test_qr_continua_legivel_em_provas_longas(total):
    """
    Em provas longas o QR vira o gargalo antes das bolhas. Com virgulas
    no conteudo, o codigo era empurrado para o modo byte e a 80 questoes
    ja falhava na foto. O formato colado usa o modo alfanumerico e le
    ate o limite da folha.
    """
    gabarito = gabarito_aleatorio(total, semente=total)
    dados = cartao_bytes(gabarito, marcacoes=gabarito, prova_id=f"P{total}", como_foto=True)

    engine = obter_engine(total)
    texto = ler_qr_da_imagem(engine._decodificar_imagem(dados))

    assert texto is not None, f"QR ilegivel com {total} questoes"
    assert parse_qr_code(texto).gabarito == gabarito


def test_qr_usa_modo_alfanumerico_no_formato_compacto():
    """Sem virgula e sem '|', o conteudo cabe no modo alfanumerico."""
    import qrcode

    gabarito = gabarito_aleatorio(140)
    texto = montar_qr_code("P140", gabarito)

    assert "," not in texto and "|" not in texto
    assert texto.isupper() or texto.replace("*", "").isalnum()

    codigo = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    codigo.add_data(texto)
    codigo.make(fit=True)
    # Com virgulas o mesmo gabarito exigia versao 12 (69 modulos).
    assert codigo.modules_count <= 49


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("101*ABC", {1: "A", 2: "B", 3: "C"}),        # compacto atual
        ("101|A,B,C", {1: "A", 2: "B", 3: "C"}),      # posicional antigo
        ("101|1A,2B,3C", {1: "A", 2: "B", 3: "C"}),   # numerado antigo
        ("101|1A,5C", {1: "A", 5: "C"}),              # com lacuna
    ],
)
def test_parser_aceita_formatos_antigos_e_novo(texto, esperado):
    """Cartoes ja impressos nao podem parar de funcionar."""
    assert parse_qr_code(texto).gabarito == esperado


# ==================================================================
# BANCO: ESCOLA / TURMA / ALUNO / PROVA / RESULTADO
# ==================================================================
@pytest.fixture()
def sessao():
    """Sessao limpa a cada teste, para um nao contaminar o outro."""
    import dados as db

    db.criar_tabelas()
    with db.SessionLocal() as s:
        yield s
        # Ordem das chaves estrangeiras, dos filhos para os pais.
        # `query(...).delete()` e bulk: NAO dispara o cascade do ORM,
        # entao cada tabela precisa aparecer aqui. Faltando `Questao`,
        # sobravam questoes orfas que colidiam com a proxima prova de
        # mesmo id na restricao UNIQUE(prova_id, numero).
        for modelo in (
            db.Resultado,
            db.Questao,
            db.Prova,
            db.Aluno,
            db.Turma,
            db.Escola,
        ):
            s.query(modelo).delete()
        s.commit()


def _turma_pronta(sessao, quantos=3):
    import dados as db

    escola = db.criar_escola(sessao, "EM Vila Nova")
    turma = db.criar_turma(sessao, escola.id, "8º ano B", 2026)
    nomes = ["Ana Souza", "Bruno Lima", "Carla Alves", "Diego Rocha", "Elisa Nunes"]
    db.adicionar_alunos(sessao, turma.id, nomes[:quantos])
    return escola, turma, db.listar_alunos(sessao, turma.id)


def test_hierarquia_escola_turma_aluno(sessao):
    import dados as db

    escola, turma, alunos = _turma_pronta(sessao)
    assert turma.escola_id == escola.id
    assert len(alunos) == 3
    assert all(a.turma_id == turma.id for a in alunos)


def test_alunos_repetidos_sao_ignorados(sessao):
    """O professor cola a lista de novo e nao acaba com nomes duplicados."""
    import dados as db

    _, turma, _ = _turma_pronta(sessao)
    novos = db.adicionar_alunos(sessao, turma.id, ["Ana Souza", "Fabio Reis"])
    assert [a.nome for a in novos] == ["Fabio Reis"]
    assert len(db.listar_alunos(sessao, turma.id)) == 4


def test_nome_com_espacos_extras_e_normalizado(sessao):
    import dados as db

    _, turma, _ = _turma_pronta(sessao, quantos=0)
    criados = db.adicionar_alunos(sessao, turma.id, ["  Ana   Souza  ", "", "   "])
    assert [a.nome for a in criados] == ["Ana Souza"]


def test_recorrigir_substitui_em_vez_de_duplicar(sessao):
    """
    Refazer a foto de um aluno nao pode gerar duas notas para a mesma
    prova — o professor acabaria com um boletim ambiguo.
    """
    import dados as db

    _, turma, alunos = _turma_pronta(sessao)
    gabarito = {n: "A" for n in range(1, 6)}
    prova = db.criar_prova(sessao, turma.id, "Prova 1", gabarito)

    base = {
        "nota": 6.0, "acertos": 3, "erros": 2, "em_branco": 0,
        "rasuras": 0, "detalhamento": [], "alinhamento": "ancoras",
    }
    db.salvar_resultado(sessao, prova.id, alunos[0].id, base)
    db.salvar_resultado(sessao, prova.id, alunos[0].id, {**base, "nota": 8.0, "acertos": 4})

    resultados = db.listar_resultados(sessao, prova.id)
    assert len(resultados) == 1
    assert resultados[0].nota == 8.0


def test_aluno_de_outra_turma_e_recusado(sessao):
    """Nota de aluno que nao esta na turma da prova nao pode ser gravada."""
    import dados as db

    _, turma, alunos = _turma_pronta(sessao)
    outra = db.criar_turma(sessao, turma.escola_id, "9º ano A")
    intruso = db.adicionar_alunos(sessao, outra.id, ["Zeca Pagodinho"])[0]
    prova = db.criar_prova(sessao, turma.id, "Prova 1", {1: "A"})

    with pytest.raises(db.RegistroNaoEncontrado):
        db.salvar_resultado(
            sessao, prova.id, intruso.id,
            {"nota": 10.0, "acertos": 1, "erros": 0, "em_branco": 0,
             "rasuras": 0, "detalhamento": []},
        )


def test_apagar_turma_leva_alunos_e_resultados(sessao):
    """Cascade: nao pode sobrar resultado orfao apontando para o vazio."""
    import dados as db

    _, turma, alunos = _turma_pronta(sessao)
    prova = db.criar_prova(sessao, turma.id, "Prova 1", {1: "A"})
    db.salvar_resultado(
        sessao, prova.id, alunos[0].id,
        {"nota": 10.0, "acertos": 1, "erros": 0, "em_branco": 0,
         "rasuras": 0, "detalhamento": []},
    )

    sessao.delete(turma)
    sessao.commit()

    assert db.contar(sessao, db.Aluno) == 0
    assert db.contar(sessao, db.Prova) == 0
    assert db.contar(sessao, db.Resultado) == 0


def test_estatisticas_por_questao(sessao):
    import dados as db

    _, turma, alunos = _turma_pronta(sessao)
    gabarito = {1: "A", 2: "B", 3: "C"}
    prova = db.criar_prova(sessao, turma.id, "Prova 1", gabarito)

    # Todos acertam a 1; ninguem acerta a 3.
    for indice, aluno in enumerate(alunos):
        detalhe = [
            {"questao": 1, "status": "correto", "marcada": "A", "correta": "A"},
            {"questao": 2, "status": "correto" if indice == 0 else "incorreto",
             "marcada": "B", "correta": "B"},
            {"questao": 3, "status": "incorreto", "marcada": "A", "correta": "C"},
        ]
        acertos = sum(1 for d in detalhe if d["status"] == "correto")
        db.salvar_resultado(
            sessao, prova.id, aluno.id,
            {"nota": round(acertos / 3 * 10, 2), "acertos": acertos,
             "erros": 3 - acertos, "em_branco": 0, "rasuras": 0,
             "detalhamento": detalhe},
        )

    estatisticas = db.estatisticas_da_prova(sessao, prova.id)
    por_questao = {q["questao"]: q["percentual"] for q in estatisticas["acerto_por_questao"]}

    assert estatisticas["total_corrigidos"] == 3
    assert por_questao[1] == 100
    assert por_questao[3] == 0


# ==================================================================
# QR COM IDENTIFICACAO DO ALUNO
# ==================================================================
def test_qr_carrega_o_aluno():
    from omr_engine import extrair_aluno

    texto = montar_qr_code("P12", {1: "A", 2: "B"}, aluno_id=345)
    assert texto == "P12.A345*AB"

    leitura = parse_qr_code(texto)
    assert (leitura.prova_id, leitura.aluno_id) == ("P12", 345)
    assert leitura.gabarito == {1: "A", 2: "B"}
    assert extrair_aluno("P12") == ("P12", None)


def test_ponto_no_codigo_da_prova_nao_vira_aluno():
    """'prova.final' nao pode ser lido como aluno de id desconhecido."""
    leitura = parse_qr_code("PROVA.FINAL*ABC")
    assert leitura.prova_id == "PROVA.FINAL"
    assert leitura.aluno_id is None


# ==================================================================
# FLUXO COMPLETO PELA API
# ==================================================================
def test_ciclo_completo_cadastro_cartao_correcao_boletim(client, segredo_qr):
    """
    O caminho que o professor percorre de verdade: cadastra a turma,
    cria a prova, imprime os cartoes nominais, fotografa e ve o boletim.
    """
    from pypdf import PdfReader

    escola = client.post("/api/escolas", data={"nome": "EM Teste Ciclo"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "7A Ciclo"}
    ).json()
    client.post(
        f"/api/turmas/{turma['id']}/alunos",
        data={"nomes": "Ana Ciclo\nBruno Ciclo\nCarla Ciclo"},
    )
    alunos = client.get(f"/api/turmas/{turma['id']}/alunos").json()

    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "Prova Ciclo", "gabarito": "ABCDE"},
    ).json()
    assert prova["total_questoes"] == 5

    # Um cartao por aluno, cada um com seu QR
    pdf = client.post(f"/api/provas/{prova['id']}/cartoes", data={"incluir_prova": "false"})
    assert pdf.status_code == 200
    assert len(PdfReader(io.BytesIO(pdf.content)).pages) == len(alunos)

    # Fotografa a folha do segundo aluno, que acerta 4 de 5
    gabarito = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    marcacoes = {**gabarito, 5: "A"}
    dados = DadosCartao(
        prova_id=f"P{prova['id']}",
        gabarito=gabarito,
        titulo=prova["titulo"],
        turma=turma["nome"],
        escola=escola["nome"],
        aluno_id=alunos[1]["id"],
        aluno_nome=alunos[1]["nome"],
        gabarito_no_qr=False,      # respostas ficam no banco
        segredo=segredo_qr,
    )
    foto = simular_foto(gerar_cartao(dados, marcacoes=marcacoes))

    resposta = client.post(
        "/api/corrigir", files={"file": ("foto.jpg", foto, "image/jpeg")}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()

    # A nota foi para o aluno certo, sem ninguem digitar o nome
    assert corpo["aluno_id"] == alunos[1]["id"]
    assert corpo["aluno_nome"] == alunos[1]["nome"]
    assert corpo["acertos"] == 4
    assert corpo["salvo"] is True

    boletim = client.get(f"/api/provas/{prova['id']}/boletim").json()
    assert boletim["total_alunos"] == 3
    assert boletim["total_corrigidos"] == 1

    corrigidos = [l for l in boletim["linhas"] if l["corrigido"]]
    pendentes = [l for l in boletim["linhas"] if not l["corrigido"]]
    assert len(corrigidos) == 1 and corrigidos[0]["aluno_nome"] == alunos[1]["nome"]
    # Quem ainda nao entregou continua na lista, para o professor ver
    assert len(pendentes) == 2


def test_cartao_avulso_corrige_mas_avisa_que_nao_guarda(client):
    """Sem id de aluno no QR nao ha onde guardar — e isso precisa ficar claro."""
    gabarito = {1: "A", 2: "B", 3: "C"}
    foto = simular_foto(
        gerar_cartao(
            DadosCartao(prova_id="AVULSO", gabarito=gabarito), marcacoes=gabarito
        )
    )
    corpo = client.post(
        "/api/corrigir", files={"file": ("f.jpg", foto, "image/jpeg")}
    ).json()

    assert corpo["nota"] == 10.0
    assert corpo["salvo"] is False
    assert corpo["aluno_id"] is None
    assert "aluno" in corpo["motivo_nao_salvo"].lower()


def test_cartoes_de_turma_vazia_dao_erro_util(client):
    escola = client.post("/api/escolas", data={"nome": "EM Vazia"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "Turma Vazia"}
    ).json()
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "P", "gabarito": "AB"},
    ).json()

    resposta = client.post(f"/api/provas/{prova['id']}/cartoes")
    assert resposta.status_code == 400
    assert "aluno" in resposta.json()["erro"].lower()


def test_nome_de_turma_com_acento_no_cabecalho_http(client):
    """
    Cabecalho HTTP nao carrega UTF-8: uma turma como "8º ano B" derrubava
    a resposta inteira antes de o PDF chegar ao navegador.
    """
    escola = client.post("/api/escolas", data={"nome": "EM Acentuada"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "8º ano — Educação"}
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "José Antônio"})
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "Avaliação", "gabarito": "ABC"},
    ).json()

    resposta = client.post(f"/api/provas/{prova['id']}/cartoes")
    assert resposta.status_code == 200
    resposta.headers["content-disposition"].encode("ascii")   # nao pode estourar


def test_prova_em_turma_inexistente(client):
    resposta = client.post(
        "/api/provas", data={"turma_id": 999999, "titulo": "X", "gabarito": "AB"}
    )
    assert resposta.status_code == 404


# ==================================================================
# ANEXO DA PROVA
# ==================================================================
def _pdf_de_teste(paginas: int = 2, senha_usuario: str = "", senha_dono: str = "") -> bytes:
    from pypdf import PdfWriter

    escritor = PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=595, height=842)
    if senha_usuario or senha_dono:
        escritor.encrypt(user_password=senha_usuario, owner_password=senha_dono or None)
    buffer = io.BytesIO()
    escritor.write(buffer)
    return buffer.getvalue()


def _turma_com_prova(client, sufixo: str, alunos: int = 2):
    escola = client.post("/api/escolas", data={"nome": f"EM {sufixo}"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": f"T{sufixo}"}
    ).json()
    client.post(
        f"/api/turmas/{turma['id']}/alunos",
        data={"nomes": "\n".join(f"Aluno {i}" for i in range(alunos))},
    )
    return client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "P", "gabarito": "ABCDE"},
    ).json()


def test_pdf_com_content_type_generico_e_aceito(client):
    """
    Navegador e sistema operacional erram o content-type com frequencia:
    um PDF chega como "application/octet-stream" em varias combinacoes
    de Windows e arrastar-e-soltar. Validar pelo conteudo evita um 415
    que o professor nao tem como entender.
    """
    from pypdf import PdfReader

    resposta = client.post(
        "/api/cartao",
        data={"gabarito": "ABCDE", "formato": "pdf", "incluir_prova": "true"},
        files={"file": ("prova", _pdf_de_teste(2), "application/octet-stream")},
    )
    assert resposta.status_code == 200
    # 2 paginas da prova + 1 do cartao
    assert len(PdfReader(io.BytesIO(resposta.content)).pages) == 3


def test_pdf_com_senha_de_dono_abre_normalmente(client):
    """PDF de sistema escolar costuma ter so senha de proprietario."""
    resposta = client.post(
        "/api/cartao",
        data={"gabarito": "ABCDE", "formato": "pdf", "incluir_prova": "true"},
        files={"file": ("p.pdf", _pdf_de_teste(1, senha_dono="dono"), "application/pdf")},
    )
    assert resposta.status_code == 200


@pytest.mark.parametrize(
    "conteudo,trecho",
    [
        (_pdf_de_teste(1, senha_usuario="segredo"), "senha"),
        (b"%PDF-1.4\nlixo lixo", "corrompido"),
        (b"PK\x03\x04conteudo-docx", "Word"),
    ],
)
def test_anexo_ruim_da_mensagem_util_e_nao_erro_500(client, conteudo, trecho):
    """
    O endpoint da turma duplicava a logica de anexo e nao tratava PDF
    protegido nem corrompido: devolvia 500 com pagina de erro em vez de
    uma frase que o professor pudesse agir.
    """
    prova = _turma_com_prova(client, trecho[:4])

    for rota, dados in [
        ("/api/cartao", {"gabarito": "ABCDE", "formato": "pdf", "incluir_prova": "true"}),
        (f"/api/provas/{prova['id']}/cartoes", {"incluir_prova": "true"}),
    ]:
        resposta = client.post(rota, data=dados, files={"file": ("p.pdf", conteudo, "application/pdf")})
        assert resposta.status_code in (400, 415), f"{rota} devolveu {resposta.status_code}"
        corpo = resposta.json()
        assert corpo["sucesso"] is False
        assert trecho.lower() in corpo["erro"].lower()


def test_prova_em_imagem_vira_pagina_do_pdf(client):
    from pypdf import PdfReader

    buffer = io.BytesIO()
    Image.new("RGB", (1240, 1754), "white").save(buffer, format="JPEG")
    prova = _turma_com_prova(client, "img", alunos=3)

    resposta = client.post(
        f"/api/provas/{prova['id']}/cartoes",
        data={"incluir_prova": "true"},
        files={"file": ("prova.jpg", buffer.getvalue(), "image/jpeg")},
    )
    assert resposta.status_code == 200
    # 1 pagina da prova + 3 cartoes nominais
    assert len(PdfReader(io.BytesIO(resposta.content)).pages) == 4


def test_foto_de_correcao_com_content_type_generico(client):
    """Mesmo problema no caminho da correcao: a foto pode chegar sem tipo."""
    gabarito = {1: "A", 2: "B", 3: "C"}
    foto = simular_foto(gerar_cartao(DadosCartao(prova_id="X", gabarito=gabarito), marcacoes=gabarito))

    resposta = client.post(
        "/api/corrigir", files={"file": ("captura", foto, "application/octet-stream")}
    )
    assert resposta.status_code == 200
    assert resposta.json()["nota"] == 10.0


def test_pdf_enviado_como_foto_de_correcao_e_recusado(client):
    """Corrigir espera imagem: um PDF ali precisa de recusa clara."""
    resposta = client.post(
        "/api/corrigir", files={"file": ("p.pdf", _pdf_de_teste(1), "application/pdf")}
    )
    assert resposta.status_code == 415
    assert "imagem" in resposta.json()["erro"].lower()


# ==================================================================
# ACESSO
# ==================================================================
def test_rotas_de_dados_exigem_sessao():
    """
    Sem login o sistema nao pode devolver nada: nome de aluno e dado
    pessoal e o servico fica exposto na internet.
    """
    anonimo = TestClient(app)
    for rota in ("/api/escolas", "/api/turmas", "/api/provas"):
        assert anonimo.get(rota).status_code == 401, rota


def test_pagina_e_health_continuam_abertos():
    """A tela de login precisa carregar antes de existir sessao."""
    anonimo = TestClient(app)
    assert anonimo.get("/").status_code == 200
    assert anonimo.get("/api/health").status_code == 200
    assert anonimo.get("/api/auth/estado").status_code == 200


def test_primeira_conta_so_funciona_uma_vez(client):
    """
    Se continuasse aberto, o endpoint viraria cadastro publico e
    qualquer pessoa criaria conta no sistema da escola.
    """
    anonimo = TestClient(app)
    resposta = anonimo.post(
        "/api/auth/primeira-conta",
        data={"email": "invasor@x.br", "nome": "Invasor", "senha": "12345678"},
    )
    assert resposta.status_code == 403


def test_senha_curta_e_recusada(client):
    resposta = client.post(
        "/api/usuarios",
        data={
            "email": "novo@e.br",
            "nome": "Novo",
            "senha": "123",
            "papel": "convidado",
        },
    )
    assert resposta.status_code == 400
    assert "8 caracteres" in resposta.json()["erro"]


def test_login_errado_nao_revela_se_o_email_existe(client):
    """
    Mensagens diferentes para "email nao existe" e "senha errada"
    entregam a lista de emails cadastrados a quem esta adivinhando.
    """
    anonimo = TestClient(app)
    import dados as db

    with db.SessionLocal() as sessao:
        _, ticket = db.criar_ticket(sessao, "Comparacao de mensagens")

    sem_conta = anonimo.post(
        "/api/auth/entrar",
        data={"email": "naoexiste@x.br", "senha": "12345678", "ticket": ticket},
    )
    senha_errada = anonimo.post(
        "/api/auth/entrar",
        data={"email": "teste@escola.br", "senha": "errada123", "ticket": ticket},
    )
    assert sem_conta.status_code == senha_errada.status_code == 401
    assert sem_conta.json()["erro"] == senha_errada.json()["erro"]


def test_entrar_e_sair(client):
    """
    O ciclo da sessao: fora, dentro, fora de novo.

    A conta e de ESCOLA (membro). Com um convidado, o 403 de quem nao
    acessa o banco se confundiria com o 401 de quem nao esta logado — e
    o teste deixaria de verificar o que se propoe.
    """
    import dados as db

    novo = TestClient(app)
    _conta("sair@escola.br", nome="Fulano")
    with db.SessionLocal() as sessao:
        _, codigo = db.criar_ticket(sessao, "Fulano")

    # Fora: sem sessao, 401.
    assert novo.get("/api/escolas").status_code == 401

    entrada = novo.post(
        "/api/auth/entrar",
        data={
            "email": "sair@escola.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    )
    assert entrada.status_code == 200

    # Dentro: ve a propria escola.
    listagem = novo.get("/api/escolas")
    assert listagem.status_code == 200
    assert len(listagem.json()) == 1

    novo.post("/api/auth/sair")
    assert novo.get("/api/escolas").status_code == 401


def test_senha_nao_e_guardada_em_texto():
    import dados as db
    import seguranca

    with db.SessionLocal() as sessao:
        usuario = db.criar_usuario(
            sessao, "hash@e.br", "Hash", "minha-senha-123", "convidado"
        )
        assert "minha-senha-123" not in usuario.senha_hash
        assert usuario.senha_hash.startswith("pbkdf2_sha256$")
        assert seguranca.conferir_senha("minha-senha-123", usuario.senha_hash)
        assert not seguranca.conferir_senha("outra-senha", usuario.senha_hash)


def test_sessao_vencida_deixa_de_valer():
    import dados as db
    import seguranca

    with db.SessionLocal() as sessao:
        usuario = db.criar_usuario(
            sessao, "vencida@e.br", "V", "senha-de-teste", "convidado"
        )
        token = db.abrir_sessao(sessao, usuario)
        assert db.usuario_da_sessao(sessao, token) is not None

        registro = sessao.scalar(
            db.select(db.Sessao).where(
                db.Sessao.token_hash == seguranca.hash_token(token)
            )
        )
        registro.expira_em = db.agora() - db.timedelta(minutes=1)
        sessao.commit()

        assert db.usuario_da_sessao(sessao, token) is None


# ==================================================================
# ASSINATURA DO QR
# ==================================================================
def test_cartao_de_turma_nao_carrega_o_gabarito():
    """
    O aluno aponta o celular para o proprio cartao e nao consegue
    descobrir as respostas: elas ficam no banco, nao no papel.
    """
    gabarito = gabarito_aleatorio(40)
    texto = montar_qr_code("P12", None, aluno_id=7, segredo=SEGREDO_TESTE)

    assert "*" not in texto and "|" not in texto
    leitura = parse_qr_code(texto, SEGREDO_TESTE)
    assert leitura.gabarito is None
    assert (leitura.prova_id, leitura.aluno_id) == ("P12", 7)
    assert leitura.assinatura == "valida"


def test_qr_sem_gabarito_fica_muito_menor():
    """Menos dados no QR = menos modulos = leitura mais confiavel."""
    import qrcode

    gabarito = gabarito_aleatorio(140)

    def modulos(texto):
        codigo = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
        codigo.add_data(texto)
        codigo.make(fit=True)
        return codigo.modules_count

    com_gabarito = modulos(montar_qr_code("P12", gabarito, 7, SEGREDO_TESTE))
    so_referencia = modulos(montar_qr_code("P12", None, 7, SEGREDO_TESTE))
    assert so_referencia < com_gabarito / 1.5


def test_assinatura_detecta_adulteracao():
    texto = montar_qr_code("P12", None, aluno_id=7, segredo=SEGREDO_TESTE)

    assert parse_qr_code(texto, SEGREDO_TESTE).assinatura == "valida"
    # Troca o aluno mantendo a assinatura antiga
    adulterado = texto.replace(".A7", ".A9")
    assert parse_qr_code(adulterado, SEGREDO_TESTE).assinatura == "invalida"
    # Assinatura de outra instalacao
    outro = montar_qr_code("P12", None, 7, "outro-segredo")
    assert parse_qr_code(outro, SEGREDO_TESTE).assinatura == "invalida"


def test_cartao_antigo_sem_assinatura_e_reconhecido():
    """Cartoes impressos antes desta versao continuam sendo lidos."""
    leitura = parse_qr_code("101*ABCDE", SEGREDO_TESTE)
    assert leitura.assinatura == "ausente"
    assert leitura.gabarito == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}


def test_codigo_de_prova_com_hifen_nao_vira_assinatura():
    """'PROVA-FINAL' nao pode ser confundido com um cartao assinado."""
    leitura = parse_qr_code("PROVA-FINAL*ABC")
    assert leitura.prova_id == "PROVA-FINAL"
    assert leitura.assinatura == "ausente"


def test_correcao_barra_assinatura_forjada(client, segredo_qr):
    """Aluno que imprime um QR proprio nao consegue nota."""
    escola = client.post("/api/escolas", data={"nome": "EM Forja"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "TF"}
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Vitima Silva"})
    aluno = client.get(f"/api/turmas/{turma['id']}/alunos").json()[0]
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "PF", "gabarito": "ABCDE"},
    ).json()

    gabarito = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    forjado = DadosCartao(
        prova_id=f"P{prova['id']}",
        gabarito=gabarito,
        aluno_id=aluno["id"],
        aluno_nome=aluno["nome"],
        gabarito_no_qr=False,
        segredo="chave-que-o-aluno-inventou",
    )
    foto = simular_foto(gerar_cartao(forjado, marcacoes=gabarito))

    resposta = client.post("/api/corrigir", files={"file": ("f.jpg", foto, "image/jpeg")})
    assert resposta.status_code == 400
    assert "assinatura" in resposta.json()["erro"].lower()

    # E nada entrou no boletim
    boletim = client.get(f"/api/provas/{prova['id']}/boletim").json()
    assert boletim["total_corrigidos"] == 0


def test_cartao_sem_assinatura_corrige_mas_nao_entra_no_boletim(client):
    """
    Compatibilidade sem abrir brecha: a nota aparece na tela para o
    professor, mas nao vira registro. Imprimir um QR proprio deixa de
    ser um caminho para forjar nota.
    """
    escola = client.post("/api/escolas", data={"nome": "EM SemAss"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "TSA"}
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Antigo Aluno"})
    aluno = client.get(f"/api/turmas/{turma['id']}/alunos").json()[0]
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "PSA", "gabarito": "ABCDE"},
    ).json()

    gabarito = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    antigo = DadosCartao(
        prova_id=f"P{prova['id']}",
        gabarito=gabarito,
        aluno_id=aluno["id"],
        aluno_nome=aluno["nome"],
        gabarito_no_qr=True,
        segredo=None,          # como eram os cartoes antes
    )
    foto = simular_foto(gerar_cartao(antigo, marcacoes=gabarito))
    corpo = client.post(
        "/api/corrigir", files={"file": ("f.jpg", foto, "image/jpeg")}
    ).json()

    assert corpo["nota"] == 10.0
    assert corpo["assinatura"] == "ausente"
    assert corpo["salvo"] is False
    assert "assinatura" in corpo["motivo_nao_salvo"].lower()

    boletim = client.get(f"/api/provas/{prova['id']}/boletim").json()
    assert boletim["total_corrigidos"] == 0


# ==================================================================
# HISTORICO DO ALUNO
# ==================================================================
def _montar_ano_letivo(client, segredo_qr, notas_por_aluno: dict[str, list[int]]):
    """Cria turma e corrige N provas, com os acertos indicados."""
    import uuid

    marca = uuid.uuid4().hex[:6]
    escola = client.post("/api/escolas", data={"nome": f"EM Hist {marca}"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": f"TH{marca}"}
    ).json()
    client.post(
        f"/api/turmas/{turma['id']}/alunos",
        data={"nomes": "\n".join(notas_por_aluno)},
    )
    alunos = {a["nome"]: a["id"] for a in client.get(f"/api/turmas/{turma['id']}/alunos").json()}

    total_provas = len(next(iter(notas_por_aluno.values())))
    for indice in range(total_provas):
        gabarito = gabarito_aleatorio(10, semente=indice)
        texto = ",".join(f"{n}{letra}" for n, letra in gabarito.items())
        prova = client.post(
            "/api/provas",
            data={
                "turma_id": turma["id"],
                "titulo": f"Bimestral {indice + 1}",
                "gabarito": texto,
            },
        ).json()

        for nome, acertos in notas_por_aluno.items():
            alvo = acertos[indice]
            marcacoes = {
                q: gabarito[q] if q <= alvo else ("A" if gabarito[q] != "A" else "B")
                for q in range(1, 11)
            }
            cartao = gerar_cartao(
                DadosCartao(
                    prova_id=f"P{prova['id']}",
                    gabarito=gabarito,
                    aluno_id=alunos[nome],
                    aluno_nome=nome,
                    gabarito_no_qr=False,
                    segredo=segredo_qr,
                ),
                marcacoes=marcacoes,
            )
            client.post(
                "/api/corrigir",
                files={"file": ("f.jpg", simular_foto(cartao), "image/jpeg")},
            )

    return turma, alunos


def test_historico_acompanha_o_aluno_no_ano(client, segredo_qr):
    turma, alunos = _montar_ano_letivo(
        client, segredo_qr, {"Sobe Silva": [4, 6, 8, 10], "Cai Souza": [10, 8, 6, 4]}
    )

    sobe = client.get(f"/api/alunos/{alunos['Sobe Silva']}/historico").json()
    assert sobe["total_provas"] == 4
    assert [a["nota"] for a in sobe["avaliacoes"]] == [4.0, 6.0, 8.0, 10.0]
    assert sobe["media"] == 7.0
    assert sobe["maior_nota"] == 10.0 and sobe["menor_nota"] == 4.0
    assert sobe["tendencia"] == "subindo"

    cai = client.get(f"/api/alunos/{alunos['Cai Souza']}/historico").json()
    assert cai["tendencia"] == "caindo"


def test_avaliacoes_saem_em_ordem_cronologica(client, segredo_qr):
    _, alunos = _montar_ano_letivo(client, segredo_qr, {"Ordem Lima": [5, 7, 9]})
    historico = client.get(f"/api/alunos/{alunos['Ordem Lima']}/historico").json()
    titulos = [a["titulo"] for a in historico["avaliacoes"]]
    assert titulos == ["Bimestral 1", "Bimestral 2", "Bimestral 3"]


def test_tendencia_exige_provas_suficientes(client, segredo_qr):
    """Com duas notas, dizer 'melhorando' seria chute travestido de dado."""
    _, alunos = _montar_ano_letivo(client, segredo_qr, {"Pouca Prova": [5, 9]})
    historico = client.get(f"/api/alunos/{alunos['Pouca Prova']}/historico").json()
    assert historico["total_provas"] == 2
    assert historico["tendencia"] is None


def test_aluno_sem_prova_tem_historico_vazio(client):
    escola = client.post("/api/escolas", data={"nome": "EM Vazio Hist"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "TVH"}
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Novato Costa"})
    aluno = client.get(f"/api/turmas/{turma['id']}/alunos").json()[0]

    historico = client.get(f"/api/alunos/{aluno['id']}/historico").json()
    assert historico["total_provas"] == 0
    assert historico["media"] is None
    assert historico["avaliacoes"] == []


def test_desempenho_da_turma_cruza_alunos_e_provas(client, segredo_qr):
    turma, _ = _montar_ano_letivo(
        client, segredo_qr, {"Aluno Um": [6, 8], "Aluno Dois": [4, 10]}
    )
    desempenho = client.get(f"/api/turmas/{turma['id']}/desempenho").json()

    assert len(desempenho["provas"]) == 2
    assert len(desempenho["alunos"]) == 2
    # Cada aluno tem uma posicao por prova, na mesma ordem
    for linha in desempenho["alunos"]:
        assert len(linha["notas"]) == 2
    assert desempenho["provas"][0]["media"] == 5.0    # (6 + 4) / 2
    assert desempenho["provas"][1]["media"] == 9.0    # (8 + 10) / 2


def test_historico_de_aluno_inexistente(client):
    assert client.get("/api/alunos/999999/historico").status_code == 404


# ==================================================================


# ==================================================================
# TICKETS DE ACESSO
# ==================================================================
def _emitir_ticket(descricao="Teste", dias=90):
    """Emite um ticket direto no banco e devolve (registro, codigo)."""
    import dados as db

    with db.SessionLocal() as sessao:
        return db.criar_ticket(sessao, descricao, dias_validade=dias)


def _conta(
    email,
    senha="senha-de-teste",
    nome="Fulano",
    papel="membro",
    escola_id=None,
):
    """
    Cria uma conta de teste.

    Um MEMBRO precisa de escola vinculada — sem ela entra no sistema e
    nao ve nada, e o cadastro e recusado de proposito. Quando o teste
    nao se importa com a escola, este helper cria uma.
    """
    import dados as db

    with db.SessionLocal() as sessao:
        if papel == "membro" and escola_id is None:
            escola = db.criar_escola(sessao, f"EM de {email}")
            escola_id = escola.id
        return db.criar_usuario(sessao, email, nome, senha, papel, escola_id)


def test_codigo_de_ticket_evita_caracteres_ambiguos():
    """
    O codigo vai ser lido de um papel e digitado a mao. Zero contra O,
    e um contra I ou L, viram chamado de suporte.
    """
    import seguranca

    for _ in range(60):
        codigo = seguranca.gerar_ticket()
        assert codigo.startswith("PF-")
        corpo = codigo.replace("-", "")[2:]
        assert not set(corpo) & set("O0I1L")
        assert len(corpo) == 12


def test_ticket_e_reconhecido_com_ou_sem_hifen():
    """
    Um codigo certo recusado por causa de um hifen a menos seria um
    suporte inteiro de trabalho para nada.
    """
    import seguranca

    codigo = seguranca.gerar_ticket()
    variantes = [
        codigo,
        codigo.lower(),
        codigo.replace("-", ""),
        f"  {codigo.replace('-', ' ')}  ",
    ]
    assert len({seguranca.hash_ticket(v) for v in variantes}) == 1


def test_ticket_guardado_apenas_como_hash():
    """Banco vazado nao pode entregar codigos de acesso utilizaveis."""
    import seguranca

    ticket, codigo = _emitir_ticket("Sigilo")
    assert seguranca.normalizar_ticket(codigo) not in ticket.codigo_hash
    assert ticket.codigo_hash == seguranca.hash_ticket(codigo)
    # A pista identifica na lista sem revelar o codigo inteiro
    assert ticket.pista.endswith("…")
    assert len(ticket.pista) < len(codigo)


def test_login_exige_os_tres_fatores(client):
    """Email e senha nao bastam: sem ticket valido nao entra."""
    _conta("tres@escola.br")
    _, codigo = _emitir_ticket("Tres fatores")
    anonimo = TestClient(app)

    sem_ticket = anonimo.post(
        "/api/auth/entrar", data={"email": "tres@escola.br", "senha": "senha-de-teste"}
    )
    assert sem_ticket.status_code == 403
    assert "ticket" in sem_ticket.json()["erro"].lower()

    completo = anonimo.post(
        "/api/auth/entrar",
        data={
            "email": "tres@escola.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    )
    assert completo.status_code == 200


def test_ticket_inexistente_e_recusado(client):
    _conta("inexistente@escola.br")
    resposta = TestClient(app).post(
        "/api/auth/entrar",
        data={
            "email": "inexistente@escola.br",
            "senha": "senha-de-teste",
            "ticket": "PF-ZZZZ-ZZZZ-ZZZZ",
        },
    )
    assert resposta.status_code == 403


def test_ticket_se_prende_a_primeira_conta_que_usar(client):
    """
    Repassar o codigo a um colega nao pode dar acesso a ele. Sem esse
    vinculo, um ticket vazado viraria acesso coletivo.
    """
    import dados as db

    _conta("dono@escola.br", nome="Dono")
    _conta("colega@escola.br", nome="Colega")
    ticket, codigo = _emitir_ticket("Vinculo")

    dono = TestClient(app)
    assert dono.post(
        "/api/auth/entrar",
        data={"email": "dono@escola.br", "senha": "senha-de-teste", "ticket": codigo},
    ).status_code == 200

    with db.SessionLocal() as sessao:
        assert sessao.get(db.Ticket, ticket.id).situacao() == "em uso"

    colega = TestClient(app)
    resposta = colega.post(
        "/api/auth/entrar",
        data={"email": "colega@escola.br", "senha": "senha-de-teste", "ticket": codigo},
    )
    assert resposta.status_code == 403
    assert "outra conta" in resposta.json()["erro"].lower()


def test_ticket_vencido_nao_entra(client):
    _conta("vencido@escola.br")
    _, codigo = _emitir_ticket("Vencido", dias=-1)

    resposta = TestClient(app).post(
        "/api/auth/entrar",
        data={
            "email": "vencido@escola.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    )
    assert resposta.status_code == 403
    assert "venceu" in resposta.json()["erro"].lower()


def test_ticket_sem_validade_nao_vence():
    """O ticket de quem administra nao pode expirar e trancar o sistema."""
    ticket, _ = _emitir_ticket("Permanente", dias=None)
    assert ticket.expira_em is None
    assert ticket.situacao() == "disponivel"


def test_revogar_ticket_derruba_a_sessao_aberta(client):
    """
    Cortar o acesso precisa valer na hora. Deixar a sessao viva ate
    vencer seria revogar so no papel.
    """
    import dados as db

    _conta("revogado@escola.br")
    ticket, codigo = _emitir_ticket("Sera revogado")

    professor = TestClient(app)
    professor.post(
        "/api/auth/entrar",
        data={
            "email": "revogado@escola.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    )
    assert professor.get("/api/escolas").status_code == 200

    with db.SessionLocal() as sessao:
        db.revogar_ticket(sessao, ticket.id)

    assert professor.get("/api/escolas").status_code == 401
    assert professor.post(
        "/api/auth/entrar",
        data={
            "email": "revogado@escola.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    ).status_code == 403


def test_primeira_conta_recebe_ticket_de_administrador(client):
    """
    Quem cria o sistema precisa sair da tela com um ticket em maos —
    senao ficaria trancado do lado de fora no proximo login.
    """
    assert getattr(pytest, "ticket_admin", None)
    assert pytest.ticket_admin.startswith("PF-")


def test_apenas_administrador_emite_ticket(client):
    """Professor comum nao distribui acesso ao sistema."""
    _conta("comum@escola.br", nome="Comum")
    _, codigo = _emitir_ticket("Para o comum")

    comum = TestClient(app)
    comum.post(
        "/api/auth/entrar",
        data={"email": "comum@escola.br", "senha": "senha-de-teste", "ticket": codigo},
    )

    assert comum.post("/api/tickets", data={"descricao": "Tentativa"}).status_code == 403
    assert comum.get("/api/tickets").status_code == 403
    # E o administrador consegue
    assert client.post("/api/tickets", data={"descricao": "Do admin"}).status_code == 200


def test_codigo_do_ticket_aparece_uma_vez_so(client):
    """
    A emissao devolve o codigo; a listagem, nunca. Se pudesse ser
    recuperado depois, guardar so o hash nao teria proposito.
    """
    emitido = client.post("/api/tickets", data={"descricao": "Uma vez"}).json()
    codigo = emitido["codigo"]
    assert codigo.startswith("PF-")

    lista = client.get("/api/tickets").json()
    for item in lista:
        assert codigo not in str(item)
        assert "codigo" not in item


def test_conta_nova_nasce_sem_ticket(client):
    """
    Cadastrar o professor nao concede acesso: quem administra emite o
    ticket em seguida. Sao dois passos de proposito.
    """
    escola = client.post("/api/escolas", data={"nome": "EM do Novato"}).json()
    criada = client.post(
        "/api/usuarios",
        data={
            "email": "novato@escola.br",
            "nome": "Novato",
            "senha": "senha-de-teste",
            "papel": "membro",
            "escola_id": escola["id"],
        },
    )
    assert criada.status_code == 200

    novato = TestClient(app)
    resposta = novato.post(
        "/api/auth/entrar",
        data={"email": "novato@escola.br", "senha": "senha-de-teste"},
    )
    assert resposta.status_code == 403


def test_limite_conta_so_tentativas_erradas(client):
    """
    Contar acerto junto travava quem so estava usando o sistema: numa
    escola atras de um unico IP, alguns professores entrando na mesma
    manha esgotariam a cota sem ninguem ter errado nada.
    """
    import main

    main._tentativas.clear()
    _conta("repetido@escola.br")
    _, codigo = _emitir_ticket("Repetido")

    for _ in range(12):
        resposta = TestClient(app).post(
            "/api/auth/entrar",
            data={
                "email": "repetido@escola.br",
                "senha": "senha-de-teste",
                "ticket": codigo,
            },
        )
        assert resposta.status_code == 200, "login correto nao pode ser bloqueado"

    main._tentativas.clear()


# ==================================================================
# ANO ESCOLAR
# ==================================================================
@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("3", "EF3"),
        ("3º ano", "EF3"),
        ("EF3", "EF3"),
        ("2ª série", "EM2"),
        ("2 serie medio", "EM2"),
        ("eja", "EJA"),
        ("qualquer coisa", "OUTRO"),
        (None, None),
    ],
)
def test_ano_escolar_normaliza_o_que_o_professor_digita(entrada, esperado):
    """
    Comparar "o 2º ano" entre escolas exige que todas escrevam igual.
    Com texto livre, a mesma serie viraria "2 ano", "2º Ano" e "segundo
    ano", e a comparacao — que e o objetivo — nao fecharia.
    """
    import escolaridade

    assert escolaridade.normalizar(entrada) == esperado


def test_anos_escolares_ordenam_por_serie_e_nao_por_texto():
    """Ordenacao alfabetica poria "10º" antes de "2º"."""
    import escolaridade

    ordens = [escolaridade.ordem_do(f"EF{n}") for n in range(1, 10)]
    assert ordens == sorted(ordens)
    assert escolaridade.ordem_do("EM1") > escolaridade.ordem_do("EF9")


# ==================================================================
# CONTEUDO DAS QUESTOES
# ==================================================================
def test_prova_guarda_o_conteudo_de_cada_questao(sessao):
    import dados as db

    escola = db.criar_escola(sessao, "EM Conteudo")
    turma = db.criar_turma(sessao, escola.id, "2A", ano_escolar="2")
    prova = db.criar_prova(
        sessao,
        turma.id,
        "Diagnostica",
        {1: "A", 2: "B", 3: "C"},
        "Matemática",
        conteudos={1: "Frações", 2: "Frações", 3: "Geometria"},
    )

    assert prova.gabarito_dict == {1: "A", 2: "B", 3: "C"}
    assert prova.conteudos == {1: "Frações", 2: "Frações", 3: "Geometria"}
    assert len(prova.questoes) == 3


def test_conteudo_pode_ser_preenchido_depois(sessao):
    """O professor costuma classificar so quando ja quer ver a analise."""
    import dados as db

    escola = db.criar_escola(sessao, "EM Depois")
    turma = db.criar_turma(sessao, escola.id, "2A", ano_escolar="2")
    prova = db.criar_prova(sessao, turma.id, "Sem conteudo", {1: "A", 2: "B"})
    assert prova.conteudos == {}

    db.atualizar_conteudos(sessao, prova.id, {1: "Frações"})
    sessao.refresh(prova)
    assert prova.conteudos == {1: "Frações"}


def test_endpoint_aceita_as_duas_formas_de_classificar(client):
    escola = client.post("/api/escolas", data={"nome": "EM Formas"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": "TF", "ano_escolar": "5"},
    ).json()

    # Uma linha por questao
    linhas = client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Por linha",
            "gabarito": "ABC",
            "conteudos": "Frações\nFrações\nGeometria",
        },
    ).json()
    questoes = client.get(f"/api/provas/{linhas['id']}/questoes").json()
    assert [q["conteudo"] for q in questoes] == ["Frações", "Frações", "Geometria"]

    # Forma abreviada: so o que interessa
    abreviada = client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Abreviada",
            "gabarito": "ABCDE",
            "conteudos": "1=Frações;4=Geometria",
        },
    ).json()
    questoes = client.get(f"/api/provas/{abreviada['id']}/questoes").json()
    conteudos = {q["numero"]: q["conteudo"] for q in questoes}
    assert conteudos[1] == "Frações"
    assert conteudos[4] == "Geometria"
    assert conteudos[2] == ""


# ==================================================================
# ANALISE PEDAGOGICA
# ==================================================================
def _escola_com_dados(
    sessao,
    dificuldade: dict,
    layout: list,
    alunos_por_turma: int = 20,
    anos=("2", "3"),
    nome="EM Analise",
    disciplina="Matemática",
    distrator_fixo=True,
):
    """
    Monta uma escola com dificuldade CONHECIDA por conteudo.

    Os testes conferem se a analise recupera essa dificuldade — e o
    unico jeito de saber se o numero que ela mostra significa algo.
    """
    import random

    import dados as db

    random.seed(99)
    escola = db.criar_escola(sessao, nome)

    for ano in anos:
        turma = db.criar_turma(sessao, escola.id, f"Turma {ano}", ano_escolar=ano)
        db.adicionar_alunos(
            sessao, turma.id, [f"Aluno {i} {ano}" for i in range(alunos_por_turma)]
        )
        alunos = db.listar_alunos(sessao, turma.id)

        gabarito = {i + 1: "A" for i in range(len(layout))}
        conteudos = {i + 1: c for i, c in enumerate(layout)}
        prova = db.criar_prova(
            sessao, turma.id, f"Prova {ano}", gabarito, disciplina, conteudos
        )

        for aluno in alunos:
            detalhamento, acertos = [], 0
            for numero in range(1, len(layout) + 1):
                if random.random() < dificuldade[conteudos[numero]]:
                    detalhamento.append(
                        {
                            "questao": numero,
                            "marcada": "A",
                            "correta": "A",
                            "status": "correto",
                        }
                    )
                    acertos += 1
                else:
                    marcada = "B" if distrator_fixo else random.choice("BCDE")
                    detalhamento.append(
                        {
                            "questao": numero,
                            "marcada": marcada,
                            "correta": "A",
                            "status": "incorreto",
                        }
                    )
            db.salvar_resultado(
                sessao,
                prova.id,
                aluno.id,
                {
                    "nota": round(acertos / len(layout) * 10, 2),
                    "acertos": acertos,
                    "erros": len(layout) - acertos,
                    "em_branco": 0,
                    "rasuras": 0,
                    "detalhamento": detalhamento,
                },
            )

    return escola


def test_analise_recupera_a_dificuldade_real_do_conteudo(sessao):
    import analitico

    dificuldade = {"Frações": 0.30, "Geometria": 0.85}
    escola = _escola_com_dados(
        sessao, dificuldade, ["Frações"] * 4 + ["Geometria"] * 4
    )

    conteudos = {
        c["conteudo"]: c
        for c in analitico.analise_por_conteudo(sessao, escola_id=escola.id)["conteudos"]
    }

    # Tolerancia de 12 pontos: o sorteio nao entrega o valor exato
    assert abs(conteudos["Frações"]["percentual_acerto"] - 30) < 12
    assert abs(conteudos["Geometria"]["percentual_acerto"] - 85) < 12
    assert conteudos["Frações"]["situacao"] == "critico"
    assert conteudos["Geometria"]["situacao"] == "adequado"


def test_conteudos_saem_do_pior_para_o_melhor(sessao):
    """E a ordem em que o professor quer ler."""
    import analitico

    escola = _escola_com_dados(
        sessao,
        {"Frações": 0.30, "Geometria": 0.85, "Multiplicação": 0.60},
        ["Frações", "Geometria", "Multiplicação"] * 3,
    )
    itens = analitico.analise_por_conteudo(sessao, escola_id=escola.id)["conteudos"]
    percentuais = [i["percentual_acerto"] for i in itens]
    assert percentuais == sorted(percentuais)


def test_amostra_pequena_nao_vira_recomendacao(sessao):
    """
    Uma questao respondida por poucos alunos nao distingue "conteudo mal
    aprendido" de acaso. O dado aparece marcado, mas nao gera orientacao
    — um sistema que recomenda com base em ruido perde a confianca da
    escola na primeira conferencia.
    """
    import analitico

    escola = _escola_com_dados(
        sessao,
        {"Frações": 0.20},
        ["Frações"] * 2,
        alunos_por_turma=3,
        anos=("2",),
        nome="EM Amostra",
    )

    itens = analitico.analise_por_conteudo(sessao, escola_id=escola.id)["conteudos"]
    assert itens[0]["conteudo"] == "Frações"
    assert itens[0]["respostas"] < analitico.MINIMO_RESPOSTAS_CONFIAVEL
    assert itens[0]["confiavel"] is False

    assert analitico.gerar_orientacoes(sessao, escola.id) == []


def test_amostra_suficiente_gera_orientacao(sessao):
    import analitico

    escola = _escola_com_dados(
        sessao, {"Frações": 0.25}, ["Frações"] * 5, alunos_por_turma=20, anos=("2",)
    )
    orientacoes = analitico.gerar_orientacoes(sessao, escola.id)

    assert orientacoes
    primeira = orientacoes[0]
    assert primeira["conteudo"] == "Frações"
    assert primeira["prioridade"] == "critico"
    # O texto precisa carregar a evidencia, nao so o veredito
    assert "Frações" in primeira["texto"]
    assert "2º ano" in primeira["texto"]
    assert "%" in primeira["texto"]


def test_distrator_concentrado_e_sinalizado(sessao):
    """
    Quando metade da turma escolhe a MESMA alternativa errada, nao houve
    chute: ha um erro conceitual, e da para saber qual.
    """
    import analitico

    escola = _escola_com_dados(
        sessao, {"Frações": 0.20}, ["Frações"] * 4, distrator_fixo=True
    )
    questoes = analitico.analise_por_questao(sessao, escola_id=escola.id)

    pior = questoes[0]
    assert pior["distrator"]["alternativa"] == "B"
    assert pior["distrator"]["concentrado"] is True


def test_erro_espalhado_nao_vira_distrator_concentrado(sessao):
    """Erro distribuido entre as alternativas e chute, e nao equivoco."""
    import analitico

    escola = _escola_com_dados(
        sessao,
        {"Frações": 0.20},
        ["Frações"] * 4,
        distrator_fixo=False,
        nome="EM Espalhado",
    )
    questoes = analitico.analise_por_questao(sessao, escola_id=escola.id)
    assert all(not q["distrator"]["concentrado"] for q in questoes)


def test_questoes_sem_conteudo_ficam_de_fora_mas_sao_contadas(sessao):
    """
    Silenciar essas respostas esconderia do professor que a analise
    esta incompleta.
    """
    import analitico
    import dados as db

    escola = db.criar_escola(sessao, "EM Sem Conteudo")
    turma = db.criar_turma(sessao, escola.id, "T", ano_escolar="2")
    db.adicionar_alunos(sessao, turma.id, ["Aluno Um"])
    aluno = db.listar_alunos(sessao, turma.id)[0]

    prova = db.criar_prova(
        sessao,
        turma.id,
        "Metade classificada",
        {1: "A", 2: "A"},
        conteudos={1: "Frações"},   # a questao 2 fica sem conteudo
    )
    db.salvar_resultado(
        sessao,
        prova.id,
        aluno.id,
        {
            "nota": 5.0,
            "acertos": 1,
            "erros": 1,
            "em_branco": 0,
            "rasuras": 0,
            "detalhamento": [
                {"questao": 1, "marcada": "A", "correta": "A", "status": "correto"},
                {"questao": 2, "marcada": "B", "correta": "A", "status": "incorreto"},
            ],
        },
    )

    analise = analitico.analise_por_conteudo(sessao, escola_id=escola.id)
    assert [c["conteudo"] for c in analise["conteudos"]] == ["Frações"]
    assert analise["questoes_sem_conteudo"] == 1


def test_em_branco_nao_conta_como_erro_na_analise(sessao):
    """
    Deixar em branco nao e o mesmo que errar: misturar as duas coisas
    inflaria a dificuldade aparente do conteudo.
    """
    import analitico
    import dados as db

    escola = db.criar_escola(sessao, "EM Branco")
    turma = db.criar_turma(sessao, escola.id, "T", ano_escolar="2")
    db.adicionar_alunos(sessao, turma.id, ["Aluno Um"])
    aluno = db.listar_alunos(sessao, turma.id)[0]

    prova = db.criar_prova(
        sessao, turma.id, "Com brancos", {1: "A", 2: "A"}, conteudos={1: "X", 2: "X"}
    )
    db.salvar_resultado(
        sessao,
        prova.id,
        aluno.id,
        {
            "nota": 5.0,
            "acertos": 1,
            "erros": 0,
            "em_branco": 1,
            "rasuras": 0,
            "detalhamento": [
                {"questao": 1, "marcada": "A", "correta": "A", "status": "correto"},
                {"questao": 2, "marcada": None, "correta": "A", "status": "em_branco"},
            ],
        },
    )

    item = analitico.analise_por_conteudo(sessao, escola_id=escola.id)["conteudos"][0]
    assert item["respostas"] == 1          # so a respondida entra no denominador
    assert item["percentual_acerto"] == 100.0
    assert item["em_branco"] == 1


def test_comparativo_separa_os_anos_escolares(sessao):
    import analitico

    escola = _escola_com_dados(
        sessao, {"Frações": 0.30, "Geometria": 0.85}, ["Frações"] * 3 + ["Geometria"] * 3
    )
    comparativo = analitico.comparativo_por_ano(sessao, escola.id)

    assert [a["nome"] for a in comparativo] == ["2º ano", "3º ano"]
    for ano in comparativo:
        assert ano["conteudo_mais_fragil"]["conteudo"] == "Frações"
        assert ano["alunos"] == 20


def test_filtro_por_ano_escolar_isola_o_recorte(sessao):
    import analitico

    escola = _escola_com_dados(sessao, {"Frações": 0.30}, ["Frações"] * 4)
    todos = analitico.analise_por_conteudo(sessao, escola_id=escola.id)
    so_segundo = analitico.analise_por_conteudo(
        sessao, escola_id=escola.id, ano_escolar="2"
    )
    assert so_segundo["total_respostas"] == todos["total_respostas"] / 2


def test_painel_da_escola_pela_api(client):
    """O caminho que a tela usa, de ponta a ponta."""
    import dados as db

    with db.SessionLocal() as sessao:
        escola = _escola_com_dados(
            sessao, {"Frações": 0.25, "Geometria": 0.90},
            ["Frações"] * 4 + ["Geometria"] * 4,
            nome="EM Painel API",
        )
        escola_id = escola.id

    painel = client.get(f"/api/escolas/{escola_id}/analise").json()

    assert painel["escola"] == "EM Painel API"
    assert painel["conteudos"][0]["conteudo"] == "Frações"
    assert painel["orientacoes"]
    assert len(painel["comparativo_anos"]) == 2
    assert "Matemática" in painel["disciplinas"]

    # Filtrado por ano
    filtrado = client.get(f"/api/escolas/{escola_id}/analise?ano_escolar=2").json()
    assert filtrado["total_respostas"] < painel["total_respostas"]


def test_analise_em_csv(client):
    import dados as db

    with db.SessionLocal() as sessao:
        escola = _escola_com_dados(
            sessao, {"Frações": 0.30}, ["Frações"] * 4, nome="EM CSV"
        )
        escola_id = escola.id

    resposta = client.get(f"/api/escolas/{escola_id}/analise.csv")
    assert resposta.status_code == 200
    assert "text/csv" in resposta.headers["content-type"]

    texto = resposta.content.decode("utf-8")
    assert texto.startswith("\ufeff")     # BOM, para o Excel abrir os acentos
    assert "Frações" in texto
    assert "EM CSV" in texto


def test_analise_de_escola_inexistente(client):
    assert client.get("/api/escolas/999999/analise").status_code == 404


# ==================================================================
# CARTAO-RESPOSTA DO PROFESSOR
# ==================================================================
def cartao_de_terceiro(
    questoes: int = 12,
    alternativas: int = 5,
    colunas: int = 2,
    raio: int = 13,
    passo_x: int = 62,
    passo_y: int = 78,
) -> Image.Image:
    """
    Um cartao com layout DIFERENTE do nosso.

    Existe para provar que a deteccao nao esta apenas reencontrando a
    geometria que o proprio sistema desenharia.
    """
    from PIL import ImageDraw, ImageFont

    def fonte(tamanho, negrito=False):
        caminho = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % (
            "-Bold" if negrito else ""
        )
        return (
            ImageFont.truetype(caminho, tamanho)
            if os.path.exists(caminho)
            else ImageFont.load_default()
        )

    imagem = Image.new("RGB", (1240, 1754), "white")
    pincel = ImageDraw.Draw(imagem)
    pincel.text((80, 70), "ESCOLA MUNICIPAL", font=fonte(30, True), fill="black")
    pincel.text((80, 115), "Cartão-resposta", font=fonte(20), fill=(90, 90, 90))
    pincel.line([(80, 190), (760, 190)], fill="black", width=2)
    pincel.text((80, 160), "Nome:", font=fonte(17), fill=(90, 90, 90))

    por_coluna = -(-questoes // colunas)
    x0, y0 = 150, 300
    # Largura calculada para caber na folha: fixar 460 estourava a
    # borda direita a partir de 3 colunas e cortava o ultimo bloco.
    largura_coluna = (1240 - x0 - 80) // colunas

    for coluna in range(colunas):
        for indice, letra in enumerate("ABCDE"[:alternativas]):
            pincel.text(
                (x0 + coluna * largura_coluna + indice * passo_x - 5, y0 - 38),
                letra,
                font=fonte(19, True),
                fill=(80, 80, 80),
            )
        for linha in range(por_coluna):
            numero = coluna * por_coluna + linha + 1
            if numero > questoes:
                break
            y = y0 + linha * passo_y
            pincel.text(
                (x0 + coluna * largura_coluna - 60, y - 11),
                f"{numero:02d}",
                font=fonte(21, True),
                fill="black",
            )
            for indice in range(alternativas):
                x = x0 + coluna * largura_coluna + indice * passo_x
                pincel.ellipse(
                    [x - raio, y - raio, x + raio, y + raio],
                    outline=(120, 120, 120),
                    width=3,
                )

    return imagem


def preencher_cartao(resultado, layout, marcacoes: dict) -> Image.Image:
    """Pinta as bolhas escolhidas, convertendo do espaco retificado."""
    from PIL import ImageDraw

    imagem = resultado.imagem.copy()
    pincel = ImageDraw.Draw(imagem)

    inversa = cv2.getPerspectiveTransform(
        np.array([[0, 0], [799, 0], [799, 999], [0, 999]], dtype="float32"),
        np.array(resultado.ancoras, dtype="float32"),
    )

    for questao, linha in enumerate(layout.rois, start=1):
        letra = marcacoes.get(questao)
        if not letra:
            continue
        x, y, largura, altura = linha["ABCDE".index(letra)]
        centro = cv2.perspectiveTransform(
            np.array([[[x + largura / 2, y + altura / 2]]], dtype="float32"), inversa
        )[0][0]
        pincel.ellipse(
            [centro[0] - 14, centro[1] - 14, centro[0] + 14, centro[1] + 14],
            fill="black",
        )

    return imagem


def test_estampa_nao_altera_o_desenho_do_professor():
    """
    O pedido era "só adicione o QR Code". As marcas vao nas MARGENS; o
    conteudo original tem de sair identico.
    """
    import estampa
    from omr_engine import montar_qr_code

    original = cartao_de_terceiro()
    resultado = estampa.estampar(original, montar_qr_code("P1", None, 7, "s"))
    borda = resultado.borda_adicionada

    recorte = np.array(
        resultado.imagem.crop(
            (borda, borda, borda + original.width, borda + original.height)
        ),
        dtype=np.int16,
    )
    diferenca = np.abs(recorte - np.array(original, dtype=np.int16))

    # O QR ocupa um canto e obviamente muda aquela area. Fora dela, a
    # exigencia e mais dura do que "quase igual": tem de ser IDENTICO,
    # pixel a pixel.
    diferenca[: estampa.LADO_QR + 120, -(estampa.LADO_QR + 120) :] = 0
    assert diferenca.max() == 0, "a estampa alterou o desenho do professor"


def test_detecta_grade_de_cartao_alheio():
    """A deteccao precisa funcionar num layout que nao e o nosso."""
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    resultado = estampa.estampar(
        cartao_de_terceiro(questoes=12, colunas=2), montar_qr_code("P1", None, 1, "s")
    )
    folha = estampa.recortar_area_util(resultado)
    layout = deteccao_grade.detectar_grade(folha, 5, 12)

    assert layout.total_questoes == 12
    assert layout.total_alternativas == 5
    assert layout.colunas_de_questoes == 2
    assert not layout.avisos


@pytest.mark.parametrize(
    "questoes,colunas,raio,passo_x,passo_y",
    [
        (10, 1, 15, 70, 90),     # coluna unica, bolhas grandes
        (12, 2, 13, 62, 78),     # duas colunas
        (20, 2, 11, 55, 62),     # mais denso
        (30, 3, 10, 48, 46),     # tres colunas
    ],
)
def test_detecta_layouts_variados(questoes, colunas, raio, passo_x, passo_y):
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    resultado = estampa.estampar(
        cartao_de_terceiro(questoes, 5, colunas, raio, passo_x, passo_y),
        montar_qr_code("P1", None, 1, "s"),
    )
    layout = deteccao_grade.detectar_grade(estampa.recortar_area_util(resultado))

    assert layout.total_questoes == questoes, f"{questoes} questoes em {colunas} colunas"
    assert layout.total_alternativas == 5
    assert layout.colunas_de_questoes == colunas


def test_folha_sem_bolhas_e_recusada_com_explicacao():
    """Enviar a prova em vez do cartao precisa dar erro compreensivel."""
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    branca = Image.new("RGB", (1240, 1754), "white")
    resultado = estampa.estampar(branca, montar_qr_code("P1", None, 1, "s"))

    with pytest.raises(deteccao_grade.GradeNaoDetectada, match="bolhas"):
        deteccao_grade.detectar_grade(estampa.recortar_area_util(resultado))


def test_ancoras_nao_sao_confundidas_com_bolhas():
    """
    Um quadrado tem circularidade 0,785 — acima do limiar de bolha — e
    seria detectado como alternativa, criando colunas fantasma.
    """
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    resultado = estampa.estampar(
        cartao_de_terceiro(questoes=12, colunas=2), montar_qr_code("P1", None, 1, "s")
    )
    folha = estampa.recortar_area_util(resultado)
    layout = deteccao_grade.detectar_grade(folha)

    # 2 colunas de questoes x 5 alternativas. Ancoras nas bordas
    # teriam criado colunas extras no comeco e no fim.
    assert layout.colunas_de_questoes == 2
    todos_x = [roi[0] for linha in layout.rois for roi in linha]
    assert min(todos_x) > 60
    assert max(todos_x) < 740


def test_mascara_de_ancoras_nao_estoura_em_folha_vazia():
    """
    Numa folha com muito espaco em branco e sombra lateral, o Otsu
    global partia o proprio gradiente do papel e marcava 42% da imagem
    como escuro — e o alinhamento saia completamente errado.
    """
    import estampa
    from omr_engine import montar_qr_code, obter_engine

    resultado = estampa.estampar(
        cartao_de_terceiro(questoes=10, colunas=1), montar_qr_code("P1", None, 1, "s")
    )
    foto = simular_foto(resultado.imagem)

    engine = obter_engine(10)
    mascara = engine._preprocessar_para_contornos(engine._decodificar_imagem(foto))

    assert float((mascara > 0).mean()) < 0.10
    assert engine._encontrar_ancoras(mascara) is not None


def test_corrige_cartao_do_professor_em_foto_degradada():
    """O ciclo inteiro: estampa, detecta, preenche, fotografa, corrige."""
    import deteccao_grade
    import estampa
    from omr_engine import engine_com_layout, montar_qr_code

    resultado = estampa.estampar(
        cartao_de_terceiro(questoes=12, colunas=2),
        montar_qr_code("P1", None, 7, "segredo"),
        aluno_nome="Ana Beatriz",
    )
    layout = deteccao_grade.detectar_grade(estampa.recortar_area_util(resultado))

    gabarito = {n: "ABCDE"[(n - 1) % 5] for n in range(1, 13)}
    marcacoes = dict(gabarito)
    marcacoes[5] = "A" if gabarito[5] != "A" else "B"   # erra a 5

    preenchido = preencher_cartao(resultado, layout, marcacoes)
    correcao = engine_com_layout(layout.rois).processar(
        simular_foto(preenchido), gabarito
    )

    assert correcao["alinhamento"] == "ancoras"
    assert correcao["acertos"] == 11
    status = {d["questao"]: d["status"] for d in correcao["detalhamento"]}
    assert status[5] == "incorreto"
    assert status[1] == "correto"


def test_fluxo_do_cartao_proprio_pela_api(client, segredo_qr):
    from pypdf import PdfReader

    escola = client.post("/api/escolas", data={"nome": "EM Cartão Próprio"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": "TCP", "ano_escolar": "4"},
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Ana CP\nBruno CP"})
    prova = client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Com cartão do professor",
            "gabarito": "ABCDEABCDEAB",
        },
    ).json()

    buffer = io.BytesIO()
    cartao_de_terceiro(questoes=12, colunas=2).save(buffer, format="PNG")

    analise = client.post(
        f"/api/provas/{prova['id']}/cartao-proprio",
        files={"file": ("meu_cartao.png", buffer.getvalue(), "image/png")},
    )
    assert analise.status_code == 200
    corpo = analise.json()
    assert corpo["total_questoes"] == 12
    assert corpo["total_alternativas"] == 5

    # Os cartões nominais saem sobre o modelo do professor
    pdf = client.post(f"/api/provas/{prova['id']}/cartoes")
    assert pdf.status_code == 200
    assert len(PdfReader(io.BytesIO(pdf.content)).pages) == 2

    # E a prévia mostra a grade detectada, para conferência
    previa = client.get(f"/api/provas/{prova['id']}/cartao-proprio/previa")
    assert previa.status_code == 200
    assert previa.headers["content-type"] == "image/png"


def test_previa_exige_cartao_proprio(client):
    escola = client.post("/api/escolas", data={"nome": "EM Sem Próprio"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "TSP"}
    ).json()
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "Padrão", "gabarito": "ABC"},
    ).json()

    resposta = client.get(f"/api/provas/{prova['id']}/cartao-proprio/previa")
    assert resposta.status_code == 400


# ==================================================================
# CARTAO DA ESCOLA — REGRESSOES
# ==================================================================
def _cartao_com_numeracao(questoes: int = 12, colunas: int = 2) -> Image.Image:
    """
    Cartao no estilo que as escolas realmente imprimem: o numero da
    questao ao lado das bolhas, em "01", "02"...

    O digito ZERO e redondo o bastante para passar num filtro de
    circularidade, e foi exatamente o que quebrou a deteccao num teste
    com cartao real.
    """
    from PIL import ImageDraw, ImageFont

    fonte = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    largura, altura = 1240, 1754
    imagem = Image.new("RGB", (largura, altura), "white")
    pincel = ImageDraw.Draw(imagem)

    pincel.text((90, 70), "ESCOLA MUNICIPAL", font=ImageFont.truetype(negrito, 34), fill="black")

    por_coluna = questoes // colunas
    raio = 19
    for coluna in range(colunas):
        x0 = 180 + coluna * 480
        for linha in range(por_coluna):
            numero = coluna * por_coluna + linha + 1
            y = 300 + linha * 110
            pincel.text(
                (x0 - 70, y - 14),
                f"{numero:02d}",
                font=ImageFont.truetype(negrito, 26),
                fill="black",
            )
            for indice in range(5):
                cx = x0 + indice * 62
                pincel.ellipse(
                    [cx - raio, y - raio, cx + raio, y + raio],
                    outline=(150, 150, 150),
                    width=3,
                )
    return imagem


def test_numero_da_questao_nao_vira_alternativa():
    """
    O "0" de "01" era detectado como bolha e criava uma coluna fantasma
    a esquerda de cada bloco: um cartao de 2 colunas virava 14 colunas
    e a grade era recusada como irregular.
    """
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    resultado = estampa.estampar(
        _cartao_com_numeracao(12, 2), montar_qr_code("P1", None, 1, "s")
    )
    layout = deteccao_grade.detectar_grade(
        estampa.recortar_area_util(resultado), alternativas_esperadas=5
    )

    assert layout.total_questoes == 12
    assert layout.total_alternativas == 5
    assert layout.colunas_de_questoes == 2
    assert layout.avisos == []


def test_qr_nunca_cobre_conteudo_do_cartao():
    """
    A tolerancia antiga (0,5% de pixels escuros) deixava passar uma
    sobreposicao de poucos pixels — e bastou isso, num cartao real,
    para o QR cortar a borda de uma bolha e derrubar a questao inteira
    na deteccao. Agora a folha ganha faixa branca em vez de cobrir.
    """
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    # Cartao com conteudo ocupando TODOS os cantos
    from PIL import ImageDraw, ImageFont

    cartao = _cartao_com_numeracao(12, 2)
    pincel = ImageDraw.Draw(cartao)
    fonte = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    for x, y in [(150, 150), (900, 150), (150, 1550), (900, 1550)]:
        pincel.text((x, y), "OCUPADO", font=fonte, fill="black")

    resultado = estampa.estampar(cartao, montar_qr_code("P1", None, 1, "s"))

    # Sem canto livre, a folha cresce em vez de perder conteudo
    assert resultado.borda_adicionada > 0
    assert any("faixa branca" in a or "borda branca" in a for a in resultado.avisos)

    # E a grade continua inteira
    layout = deteccao_grade.detectar_grade(
        estampa.recortar_area_util(resultado), alternativas_esperadas=5
    )
    assert layout.total_questoes == 12


def test_cartao_da_escola_corrigido_de_ponta_a_ponta(client, segredo_qr):
    """
    O caminho completo do professor: manda o cartao dele, imprime,
    o aluno responde, ele fotografa, e a nota cai no boletim.
    """
    import io as _io

    import cv2 as _cv2
    import deteccao_grade
    import estampa
    import numpy as _np
    from omr_engine import montar_qr_code

    escola = client.post("/api/escolas", data={"nome": "EM Cartao Proprio"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": "4A", "ano_escolar": "4"},
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Ana Beatriz"})
    aluno = client.get(f"/api/turmas/{turma['id']}/alunos").json()[0]

    gabarito_texto = "ABCDEABCDEAB"
    prova = client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Bimestral",
            "gabarito": gabarito_texto,
        },
    ).json()

    # 1. O professor envia o cartao-resposta dele
    buffer = _io.BytesIO()
    _cartao_com_numeracao(12, 2).save(buffer, format="PNG")
    aprendizado = client.post(
        f"/api/provas/{prova['id']}/cartao-proprio",
        files={"file": ("meu_cartao.png", buffer.getvalue(), "image/png")},
    )
    assert aprendizado.status_code == 200, aprendizado.text
    assert aprendizado.json()["total_questoes"] == 12

    # 2. O sistema gera a folha nominal a partir do cartao DELE
    import dados as db

    with db.SessionLocal() as sessao:
        registro = db.obter_prova(sessao, prova["id"])
        layout = deteccao_grade.LayoutDetectado.de_json(registro.layout)
        modelo = Image.open(_io.BytesIO(registro.modelo_cartao))

    qr = montar_qr_code(f"P{prova['id']}", None, aluno["id"], segredo_qr)
    folha = estampa.estampar(modelo, qr, rotulo="Bimestral", aluno_nome=aluno["nome"])
    pagina = _cv2.cvtColor(_np.array(folha.imagem), _cv2.COLOR_RGB2BGR)

    # 3. O aluno preenche
    altura, largura = pagina.shape[:2]
    margem = estampa.MARGEM_ANCORA
    gabarito = {i: gabarito_texto[i - 1] for i in range(1, 13)}
    marcacoes = dict(gabarito)
    marcacoes[3] = "A" if gabarito[3] != "A" else "B"   # uma errada
    del marcacoes[7]                                     # uma em branco

    for numero, letra in marcacoes.items():
        x, y, w, _h = layout.rois[numero - 1]["ABCDE".index(letra)]
        px = int(margem + (x + w / 2) / 800 * (largura - 2 * margem))
        py = int(margem + (y + w / 2) / 1000 * (altura - 2 * margem))
        _cv2.circle(pagina, (px, py), int(w * 0.42 * (largura - 2 * margem) / 800), (20, 20, 20), -1)

    # 4. O professor fotografa e envia
    foto = simular_foto(Image.fromarray(_cv2.cvtColor(pagina, _cv2.COLOR_BGR2RGB)))
    resultado = client.post(
        "/api/corrigir", files={"file": ("foto.jpg", foto, "image/jpeg")}
    ).json()

    assert resultado["aluno_nome"] == "Ana Beatriz"
    assert resultado["alinhamento"] == "ancoras"
    assert resultado["acertos"] == 10
    assert resultado["em_branco"] == 1
    assert resultado["salvo"] is True


def test_cartao_proprio_avisa_divergencia_de_questoes(client):
    """
    Cartao de 12 questoes numa prova de 10 nao pode passar em silencio:
    ou o professor mandou o cartao errado, ou a deteccao falhou.
    """
    import io as _io

    escola = client.post("/api/escolas", data={"nome": "EM Divergente"}).json()
    turma = client.post(
        "/api/turmas", data={"escola_id": escola["id"], "nome": "TD", "ano_escolar": "4"}
    ).json()
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "Dez", "gabarito": "ABCDEABCDE"},
    ).json()

    buffer = _io.BytesIO()
    _cartao_com_numeracao(12, 2).save(buffer, format="PNG")
    resposta = client.post(
        f"/api/provas/{prova['id']}/cartao-proprio",
        files={"file": ("c.png", buffer.getvalue(), "image/png")},
    ).json()

    assert resposta["total_questoes"] == 12
    assert any("12" in aviso and "10" in aviso for aviso in resposta["avisos"])


# ==================================================================
# CAMADA DE DADOS
# ==================================================================
def test_fachada_expoe_tudo_que_o_sistema_usa():
    """
    A fachada `dados` é o único ponto de importação do resto do
    sistema. Se um nome sair dela sem aviso, o erro só apareceria em
    tempo de execução, no meio de uma requisição.
    """
    import re

    import dados

    usados = set()
    for arquivo in ["main.py", "analitico.py"]:
        caminho = os.path.join(RAIZ, arquivo)
        with open(caminho, encoding="utf-8") as fp:
            texto = fp.read()
        usados |= set(re.findall(r"\bdb\.([a-zA-Z_][a-zA-Z0-9_]*)", texto))

    faltando = sorted(nome for nome in usados if not hasattr(dados, nome))
    assert faltando == [], f"a fachada não expõe: {faltando}"


def test_modelos_nao_dependem_dos_repositorios():
    """
    A dependência tem UM sentido: repositórios usam modelos, nunca o
    contrário. Uma seta na volta criaria import circular e, pior,
    esconderia regra de negócio dentro da definição das tabelas.
    """
    import ast

    caminho = os.path.join(RAIZ, "dados", "modelos.py")
    with open(caminho, encoding="utf-8") as fp:
        arvore = ast.parse(fp.read())

    # Analisa os IMPORTS, e não o texto do arquivo: a docstring cita
    # `repositorios` para explicar a organizacao, e uma busca por
    # substring acusaria isso como dependencia.
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados |= {a.name.split(".")[0] for a in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module)

    proibidos = {"main", "analitico", "gerador", "estampa"}
    assert not (importados & proibidos), importados & proibidos
    assert not any(m.startswith("dados.repositorios") for m in importados)


def test_cada_repositorio_importa_so_o_que_precisa():
    """Nenhum repositório pode importar a fachada: isso seria circular."""
    pasta = os.path.join(RAIZ, "dados", "repositorios")
    for arquivo in os.listdir(pasta):
        if not arquivo.endswith(".py"):
            continue
        with open(os.path.join(pasta, arquivo), encoding="utf-8") as fp:
            texto = fp.read()
        assert "import dados as" not in texto, arquivo
        assert "from dados import" not in texto, arquivo


def test_conexao_e_o_unico_que_conhece_a_url_do_banco():
    """
    Centralizar DATABASE_URL é o que permite trocar SQLite por Postgres
    mexendo em um arquivo só.
    """
    import dados

    for arquivo in ["modelos.py", "erros.py"]:
        caminho = os.path.join(RAIZ, "dados", arquivo)
        with open(caminho, encoding="utf-8") as fp:
            assert "DATABASE_URL" not in fp.read(), arquivo

    assert dados.DATABASE_URL


def test_menu_lateral_cobre_todos_os_paineis():
    """
    A lista de painéis do JavaScript e as seções do HTML precisam
    bater. Foi exatamente uma divergência dessas que deixou a aba
    Análise invisível: ela existia no HTML e faltava na lista.
    """
    import re

    caminho = os.path.join(RAIZ, "static", "index.html")
    with open(caminho, encoding="utf-8") as fp:
        html = fp.read()

    no_html = set(re.findall(r'id="painel-([a-z]+)"', html))
    no_menu = set(re.findall(r'class="item-menu"[^>]*data-painel="([a-z]+)"', html))
    no_menu |= set(re.findall(r'data-painel="([a-z]+)"[^>]*class="item-menu"', html))
    lista_js = re.search(r"const PAINEIS = \[(.*?)\]", html, re.S).group(1)
    no_js = set(re.findall(r'"([a-z]+)"', lista_js))

    assert no_html == no_js, f"HTML {no_html} != JS {no_js}"
    assert no_html <= no_js


# ==================================================================
# LEITURA EM CONDICOES REAIS
# ==================================================================
def _folha_marcada(
    gabarito: dict,
    marcacoes: dict,
    cor=(25, 25, 25),
    raio_relativo: float = 0.75,
    luz: float = 1.0,
) -> bytes:
    """
    Desenha as marcas do aluno com COR e TAMANHO controlados.

    `gerar_cartao(marcacoes=...)` sempre pinta a bolha inteira de preto,
    o que representa só o aluno ideal. Aqui dá para simular caneta azul,
    lápis claro, risco em vez de preenchimento e sombra na folha.
    """
    import cv2 as _cv2
    import numpy as _np

    from gerador import ESCALA, MARGEM

    total = max(gabarito)
    engine = obter_engine(total)
    cartao = gerar_cartao(DadosCartao(prova_id="R", gabarito=gabarito, titulo="Real"))
    imagem = _cv2.cvtColor(_np.array(cartao), _cv2.COLOR_RGB2BGR)

    for numero, letras in marcacoes.items():
        for letra in letras:
            x, y, w, _h = engine._rois[numero - 1]["ABCDE".index(letra)]
            centro = (
                int((x + w / 2) * ESCALA + MARGEM),
                int((y + w / 2) * ESCALA + MARGEM),
            )
            _cv2.circle(imagem, centro, int(w * ESCALA * raio_relativo / 2), cor, -1)

    if luz < 1.0:
        largura = imagem.shape[1]
        gradiente = _np.linspace(luz, 1.0, largura).astype(_np.float32)
        imagem = _np.clip(
            imagem.astype(_np.float32) * gradiente[None, :, None], 0, 255
        ).astype(_np.uint8)

    ok, buffer = _cv2.imencode(".jpg", imagem, [_cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return buffer.tobytes()


@pytest.mark.parametrize(
    "descricao,cor,raio,luz",
    [
        ("caneta preta", (25, 25, 25), 0.75, 1.0),
        ("caneta azul", (150, 70, 30), 0.75, 1.0),
        ("lapis claro", (150, 150, 150), 0.75, 1.0),
        ("lapis muito claro", (190, 190, 190), 0.75, 1.0),
        # Sombra do proprio fotografo sobre metade da folha: com limiar
        # absoluto, esse lado inteiro "parecia preenchido" e toda questao
        # virava rasura.
        ("azul com sombra forte", (150, 70, 30), 0.75, 0.45),
        ("lapis com sombra", (160, 160, 160), 0.70, 0.55),
        # O aluno que risca a bolha em vez de pintar. Ficava abaixo de
        # qualquer corte fixo e a prova inteira voltava em branco.
        ("preenchimento incompleto", (40, 40, 40), 0.42, 1.0),
        ("risco fino", (40, 40, 40), 0.30, 1.0),
    ],
)
def test_le_qualquer_marca_que_o_aluno_faca(descricao, cor, raio, luz):
    gabarito = {n: "ABCDE"[n % 5] for n in range(1, 11)}
    dados = _folha_marcada(gabarito, gabarito, cor, raio, luz)

    resultado = obter_engine(10).processar(dados, gabarito)
    assert resultado["acertos"] == 10, f"{descricao}: {resultado['detalhamento'][:3]}"
    assert resultado["em_branco"] == 0
    assert resultado["rasuras"] == 0


@pytest.mark.parametrize("luz", [1.0, 0.45])
def test_folha_em_branco_nunca_inventa_marcacao(luz):
    """
    O risco do critério relativo: sem piso, alguma bolha sempre seria
    "a menos clara" e viraria resposta. Numa folha limpa isso daria
    nota a quem não respondeu nada.
    """
    gabarito = {n: "A" for n in range(1, 11)}
    dados = _folha_marcada(gabarito, {}, luz=luz)

    resultado = obter_engine(10).processar(dados, gabarito)
    assert resultado["em_branco"] == 10
    assert resultado["acertos"] == 0
    assert resultado["rasuras"] == 0


def test_ruido_de_folha_limpa_fica_muito_abaixo_do_corte():
    """
    Mede a folga real do limiar. Em 870 questões de folhas em branco
    fotografadas mal, o destaque máximo observado foi 0,008 — contra
    uma margem exigida de 0,05.
    """
    import numpy as _np

    destaques = []
    for total in (10, 40):
        gabarito = {n: "A" for n in range(1, total + 1)}
        engine = obter_engine(total)
        dados = cartao_bytes(gabarito, marcacoes=None, como_foto=True)
        folha, _ = engine.alinhar_folha(engine._decodificar_imagem(dados))

        for leitura in engine._detectar_respostas(engine.normalizar_iluminacao(folha)):
            valores = sorted(leitura["escuridoes"].values(), reverse=True)
            destaques.append(valores[0] - float(_np.median(valores[1:])))

    assert max(destaques) < CONFIG_PADRAO.margem_relativa / 3


def test_sombra_nao_transforma_a_folha_em_rasura():
    """Era o sintoma mais confuso: nota zero sem nada marcado como branco."""
    gabarito = {n: "ABCDE"[n % 5] for n in range(1, 11)}
    dados = _folha_marcada(gabarito, gabarito, cor=(150, 70, 30), luz=0.4)

    resultado = obter_engine(10).processar(dados, gabarito)
    assert resultado["rasuras"] == 0
    assert resultado["acertos"] == 10


def test_duas_bolhas_continuam_sendo_rasura():
    """A leitura relativa não pode afrouxar a detecção de rasura."""
    gabarito = {n: "A" for n in range(1, 11)}
    marcacoes = {n: "AB" for n in range(1, 11)}
    dados = _folha_marcada(gabarito, marcacoes)

    resultado = obter_engine(10).processar(dados, gabarito)
    assert resultado["rasuras"] == 10
    assert resultado["acertos"] == 0


# ==================================================================
# CARTAO COM 4 ALTERNATIVAS
# ==================================================================
def _cartao_em_tabela(questoes: int = 5, alternativas: int = 4) -> Image.Image:
    """
    Cartão no formato de tabela usado por secretarias municipais:
    coluna do número, coluna das alternativas, e só A–D.

    Todo o restante da suíte usa 5 alternativas. Um cartão de 4 era
    justamente o caso que colocava a grade de leitura fora de lugar.
    """
    from PIL import ImageDraw, ImageFont

    fonte = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    imagem = Image.new("RGB", (1240, 1754), "white")
    pincel = ImageDraw.Draw(imagem)

    pincel.text(
        (620, 140), "AVALIAÇÃO DIAGNÓSTICA",
        font=ImageFont.truetype(negrito, 30), fill="black", anchor="mm",
    )

    x0, y0, raio = 220, 680, 20
    pincel.rectangle([x0, y0, 1040, y0 + 55], fill=(45, 45, 45))
    for linha in range(questoes):
        y = y0 + 55 + linha * 62
        pincel.rectangle([x0, y, 1040, y + 62], outline="black", width=2)
        pincel.line([(x0 + 220, y), (x0 + 220, y + 62)], fill="black", width=2)
        pincel.text(
            (x0 + 70, y + 31), f"{linha + 1:02d}",
            font=ImageFont.truetype(negrito, 20), fill="black", anchor="lm",
        )
        for indice in range(alternativas):
            cx, cy = x0 + 300 + indice * 105, y + 31
            pincel.ellipse(
                [cx - raio, cy - raio, cx + raio, cy + raio],
                outline=(60, 60, 60), width=2,
            )
            pincel.text(
                (cx, cy), "ABCDE"[indice],
                font=ImageFont.truetype(fonte, 15), fill=(60, 60, 60), anchor="mm",
            )
    return imagem


def test_detecta_cartao_com_quatro_alternativas():
    """
    Cartão de secretaria costuma ter A–D. Com o sistema assumindo cinco,
    a grade de leitura saía deslocada e o resultado virava ruído.
    """
    import deteccao_grade
    import estampa
    from omr_engine import montar_qr_code

    resultado = estampa.estampar(
        _cartao_em_tabela(5, 4), montar_qr_code("P2", None, 7, "seg")
    )
    layout = deteccao_grade.detectar_grade(
        estampa.recortar_area_util(resultado), questoes_esperadas=5
    )

    assert layout.total_alternativas == 4
    assert layout.total_questoes == 5
    assert layout.avisos == []


@pytest.mark.parametrize(
    "descricao,cor,raio",
    [
        ("caneta preta cheia", (30, 30, 30), 0.42),
        ("caneta azul média", (150, 70, 30), 0.42),
        ("marca leve", (110, 110, 110), 0.34),
        ("só contorna a bolha", (90, 90, 90), 0.26),
    ],
)
def test_corrige_cartao_de_quatro_alternativas(descricao, cor, raio):
    import cv2 as _cv2
    import deteccao_grade
    import estampa
    import numpy as _np
    from omr_engine import engine_com_layout, montar_qr_code

    resultado = estampa.estampar(
        _cartao_em_tabela(5, 4),
        montar_qr_code("P2", None, 7, "seg"),
        rotulo="Prova P2",
        aluno_nome="José Aldo",
    )
    layout = deteccao_grade.detectar_grade(
        estampa.recortar_area_util(resultado), questoes_esperadas=5
    )

    gabarito = {1: "D", 2: "C", 3: "A", 4: "B", 5: "C"}
    marcadas = {1: "D", 2: "C", 3: "A", 4: "A", 5: "B"}   # 3 certas, 2 erradas

    pagina = _cv2.cvtColor(_np.array(resultado.imagem), _cv2.COLOR_RGB2BGR)
    altura, largura = pagina.shape[:2]
    margem = estampa.MARGEM_ANCORA

    for numero, letra in marcadas.items():
        x, y, w, _h = layout.rois[numero - 1]["ABCD".index(letra)]
        px = int(margem + (x + w / 2) / 800 * (largura - 2 * margem))
        py = int(margem + (y + w / 2) / 1000 * (altura - 2 * margem))
        _cv2.circle(pagina, (px, py), int(w * raio * (largura - 2 * margem) / 800), cor, -1)

    foto = simular_foto(Image.fromarray(_cv2.cvtColor(pagina, _cv2.COLOR_BGR2RGB)))
    leitura = engine_com_layout(layout.rois).processar(foto, gabarito)

    assert leitura["acertos"] == 3, f"{descricao}: {leitura['detalhamento']}"
    assert leitura["em_branco"] == 0
    assert leitura["rasuras"] == 0
    assert leitura["alinhamento"] == "ancoras"


def test_ancora_cortada_pela_impressora_ainda_e_encontrada():
    """
    Impressora doméstica tem margem não-imprimível de 10 a 13 mm no
    rodapé. As âncoras de baixo saíam como tracinhos, o alinhamento
    falhava e a leitura vinha torta — com o aviso genérico de "cantos
    não encontrados", que não dizia ao professor o que fazer.
    """
    import cv2 as _cv2
    import numpy as _np
    import estampa
    from omr_engine import montar_qr_code, obter_engine

    gabarito = {n: "A" for n in range(1, 6)}
    resultado = estampa.estampar(
        _cartao_em_tabela(5, 4), montar_qr_code("P2", None, 7, "seg")
    )
    pagina = _cv2.cvtColor(_np.array(resultado.imagem), _cv2.COLOR_RGB2BGR)

    # Achata as duas âncoras de baixo, como a impressora faria
    altura, largura = pagina.shape[:2]
    margem = estampa.MARGEM_ANCORA
    meia = estampa.LADO_ANCORA // 2
    for cx in (margem, largura - margem):
        _cv2.rectangle(
            pagina,
            (cx - meia - 2, altura - margem - meia - 2),
            (cx + meia + 2, altura - margem + 2),
            (255, 255, 255),
            -1,
        )
        _cv2.rectangle(
            pagina,
            (cx - meia, altura - margem - 4),
            (cx + meia, altura - margem + 2),
            (0, 0, 0),
            -1,
        )

    ok, buffer = _cv2.imencode(".jpg", pagina, [_cv2.IMWRITE_JPEG_QUALITY, 85])
    leitura = obter_engine(5).processar(buffer.tobytes(), gabarito)
    assert leitura["alinhamento"] == "ancoras"


# ==================================================================
# LIMPEZA DOS DADOS
# ==================================================================
def test_limpar_dados_exige_confirmacao_exata(client):
    for tentativa in ["sim", "apagar", "APAGAR", ""]:
        resposta = client.post(
            "/api/admin/limpar-dados", data={"confirmacao": tentativa}
        )
        assert resposta.status_code in (400, 422), tentativa


def test_limpar_dados_apaga_o_pedagogico_e_preserva_o_acesso(client):
    """
    Zerar as contas junto tiraria de dentro do sistema justamente quem
    pediu a limpeza — no meio de uma apresentação, seria fatal.
    """
    import dados as db

    escola = client.post("/api/escolas", data={"nome": "EM Limpeza"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": "TL", "ano_escolar": "4"},
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Ana\nBruno"})
    client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "P", "gabarito": "ABCD"},
    )

    with db.SessionLocal() as sessao:
        usuarios_antes = db.contar(sessao, db.Usuario)
        tickets_antes = db.contar(sessao, db.Ticket)

    resposta = client.post(
        "/api/admin/limpar-dados", data={"confirmacao": "apagar tudo"}
    )
    assert resposta.status_code == 200

    assert client.get("/api/escolas").json() == []
    # Quem apagou continua dentro do sistema
    assert client.get("/api/auth/estado").json()["autenticado"] is True

    with db.SessionLocal() as sessao:
        assert db.contar(sessao, db.Usuario) == usuarios_antes
        assert db.contar(sessao, db.Ticket) == tickets_antes
        assert db.contar(sessao, db.Questao) == 0
        assert db.contar(sessao, db.Resultado) == 0


def test_professor_comum_nao_limpa_os_dados(client):
    import dados as db

    escola = client.post("/api/escolas", data={"nome": "EM da Limpeza"}).json()
    client.post(
        "/api/usuarios",
        data={
            "email": "comum-limpeza@e.br",
            "nome": "Comum",
            "senha": "senha-de-teste",
            "papel": "membro",
            "escola_id": escola["id"],
        },
    )
    with db.SessionLocal() as sessao:
        _, codigo = db.criar_ticket(sessao, "comum limpeza")

    comum = TestClient(app)
    comum.post(
        "/api/auth/entrar",
        data={
            "email": "comum-limpeza@e.br",
            "senha": "senha-de-teste",
            "ticket": codigo,
        },
    )
    resposta = comum.post("/api/admin/limpar-dados", data={"confirmacao": "APAGAR TUDO"})
    assert resposta.status_code == 403


# ==================================================================
# CORRECAO EM MASSA POR ARQUIVO
# ==================================================================
def _lote_de_folhas(
    client, segredo_qr, quantidade: int = 6, formato: str = "pdf"
) -> tuple:
    """
    Monta uma turma, corrige nada ainda, e devolve o arquivo de lote
    junto com os acertos esperados de cada aluno.
    """
    import io as _io
    import random as _random
    import uuid
    import zipfile as _zipfile

    marca = uuid.uuid4().hex[:6]
    escola = client.post("/api/escolas", data={"nome": f"EM Lote {marca}"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": f"TL{marca}", "ano_escolar": "4"},
    ).json()
    client.post(
        f"/api/turmas/{turma['id']}/alunos",
        data={"nomes": "\n".join(f"Aluno {i} {marca}" for i in range(quantidade))},
    )
    alunos = client.get(f"/api/turmas/{turma['id']}/alunos").json()

    gabarito_texto = "ABCDEABCDE"
    prova = client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Bimestral",
            "gabarito": gabarito_texto,
        },
    ).json()
    gabarito = {i: gabarito_texto[i - 1] for i in range(1, 11)}

    _random.seed(7)
    paginas, esperados = [], {}
    for aluno in alunos:
        marcacoes, acertos = {}, 0
        for numero in range(1, 11):
            if _random.random() < 0.7:
                marcacoes[numero] = gabarito[numero]
                acertos += 1
            else:
                marcacoes[numero] = next(
                    letra for letra in "ABCDE" if letra != gabarito[numero]
                )
        esperados[aluno["id"]] = acertos

        cartao = gerar_cartao(
            DadosCartao(
                prova_id=f"P{prova['id']}",
                gabarito=gabarito,
                titulo="Bimestral",
                aluno_id=aluno["id"],
                aluno_nome=aluno["nome"],
                gabarito_no_qr=False,
                segredo=segredo_qr,
            ),
            marcacoes=marcacoes,
        )
        paginas.append(
            Image.open(io.BytesIO(simular_foto(cartao))).convert("RGB")
        )

    buffer = _io.BytesIO()
    if formato == "pdf":
        paginas[0].save(
            buffer,
            format="PDF",
            save_all=True,
            append_images=paginas[1:],
            resolution=150.0,
        )
    else:
        with _zipfile.ZipFile(buffer, "w") as arquivo:
            for indice, pagina in enumerate(paginas, start=1):
                imagem = _io.BytesIO()
                pagina.save(imagem, format="JPEG", quality=88)
                arquivo.writestr(f"aluno_{indice:02d}.jpg", imagem.getvalue())
            # Lixo que o macOS coloca em todo ZIP, e um arquivo que
            # não é foto: nenhum dos dois pode virar "folha ilegível".
            arquivo.writestr("__MACOSX/._aluno_01.jpg", b"lixo")
            arquivo.writestr("leia-me.txt", b"isto nao e uma imagem")

    return prova, buffer.getvalue(), esperados


@pytest.mark.parametrize("formato,mime", [("pdf", "application/pdf"), ("zip", "application/zip")])
def test_corrige_turma_inteira_de_um_arquivo(client, segredo_qr, formato, mime):
    """
    É como o material chega: o scanner devolve um PDF de 40 páginas, ou
    alguém junta as fotos num ZIP. Cada folha precisa virar a nota de
    um aluno, com a pontuação individual correta.
    """
    prova, conteudo, esperados = _lote_de_folhas(
        client, segredo_qr, quantidade=6, formato=formato
    )

    resposta = client.post(
        "/api/corrigir-arquivo", files={"file": (f"lote.{formato}", conteudo, mime)}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()

    assert corpo["total_enviados"] == 6
    assert corpo["total_corrigidos"] == 6
    assert corpo["total_falhas"] == 0

    for item in corpo["itens"]:
        resultado = item["resultado"]
        assert resultado["acertos"] == esperados[resultado["aluno_id"]], item["arquivo"]
        assert resultado["salvo"] is True

    boletim = client.get(f"/api/provas/{prova['id']}/boletim").json()
    assert boletim["total_corrigidos"] == 6


def test_contar_folhas_antes_de_corrigir(client, segredo_qr):
    """A tela avisa o tamanho do trabalho em vez de travar em silêncio."""
    _, conteudo, _ = _lote_de_folhas(client, segredo_qr, quantidade=4, formato="pdf")

    resposta = client.post(
        "/api/contar-folhas", files={"file": ("lote.pdf", conteudo, "application/pdf")}
    ).json()

    assert resposta["folhas"] == 4
    assert resposta["tipo"] == "pdf"
    assert resposta["segundos_estimados"] > 0


def test_zip_ignora_lixo_do_sistema_operacional(client, segredo_qr):
    """`__MACOSX/` e o leia-me.txt não podem virar folhas ilegíveis."""
    _, conteudo, _ = _lote_de_folhas(client, segredo_qr, quantidade=3, formato="zip")

    corpo = client.post(
        "/api/corrigir-arquivo", files={"file": ("lote.zip", conteudo, "application/zip")}
    ).json()

    assert corpo["total_enviados"] == 3
    assert corpo["total_falhas"] == 0


def test_folha_ruim_no_meio_do_lote_nao_derruba_o_resto(client, segredo_qr):
    """E ela precisa voltar identificada, para achar o papel físico."""
    import io as _io

    import lote as modulo_lote

    prova, conteudo, _ = _lote_de_folhas(client, segredo_qr, quantidade=3, formato="pdf")
    folhas = modulo_lote.extrair_folhas(conteudo, "lote.pdf")

    branco = Image.new("RGB", (1240, 1754), "white")
    buffer = _io.BytesIO()
    paginas = [Image.open(_io.BytesIO(f.conteudo)).convert("RGB") for f in folhas]
    paginas.insert(1, branco)
    paginas[0].save(
        buffer, format="PDF", save_all=True, append_images=paginas[1:], resolution=150.0
    )

    corpo = client.post(
        "/api/corrigir-arquivo",
        files={"file": ("misto.pdf", buffer.getvalue(), "application/pdf")},
    ).json()

    assert corpo["total_enviados"] == 4
    assert corpo["total_corrigidos"] == 3
    falhas = [i for i in corpo["itens"] if not i["sucesso"]]
    assert len(falhas) == 1
    assert "página 2" in falhas[0]["arquivo"]


@pytest.mark.parametrize(
    "nome,conteudo,trecho",
    [
        # Word/Excel sao ZIPs validos, mas sem imagem dentro.
        ("doc.docx", None, "imagens"),
        # ZIP corrompido, ou qualquer coisa que so comece com PK.
        ("quebrado.zip", b"PK\x03\x04lixo-que-nao-abre", "PDF"),
        # Formato que nao e nenhum dos tres.
        ("planilha.csv", b"nome;nota\nAna;10", "PDF"),
    ],
)
def test_arquivo_que_nao_e_lote_da_mensagem_util(client, nome, conteudo, trecho):
    """
    A mensagem precisa dizer o que ENVIAR, e nao so que deu errado.
    """
    import io as _io
    import zipfile as _zipfile

    if conteudo is None:
        buffer = _io.BytesIO()
        with _zipfile.ZipFile(buffer, "w") as arquivo:
            arquivo.writestr("word/document.xml", b"<xml/>")
        conteudo = buffer.getvalue()

    resposta = client.post(
        "/api/corrigir-arquivo",
        files={"file": (nome, conteudo, "application/octet-stream")},
    )
    assert resposta.status_code == 400
    assert trecho.lower() in resposta.json()["erro"].lower()


# ==================================================================
# COMPARACAO ENTRE ESCOLAS
# ==================================================================
def test_margem_de_erro_encolhe_com_a_amostra():
    """
    É o número que impede ler 71% de 18 alunos como melhor que 68% de
    120. Sem ele, a tela vira ranking de reputação.
    """
    import comparativo

    margens = [comparativo.margem_de_erro(0.7, n) for n in (10, 50, 200, 1000)]
    assert margens == sorted(margens, reverse=True)
    assert margens[0] > 20    # 10 respostas não dizem nada
    assert margens[-1] < 5    # 1000 já é um número firme


def test_diferenca_pequena_nao_passa_no_teste():
    """4 pontos entre amostras de 100 é acaso, não desempenho."""
    import comparativo

    significativa, diferenca = comparativo.diferenca_significativa(62, 100, 58, 100)
    assert diferenca == 4.0
    assert significativa is False


def test_diferenca_grande_com_amostra_grande_passa():
    import comparativo

    significativa, diferenca = comparativo.diferenca_significativa(700, 1000, 600, 1000)
    assert diferenca == 10.0
    assert significativa is True


def _escola_com_perfil(sessao, nome, perfil, layout, alunos=30, ano="4"):
    import random as _random

    import dados as db

    _random.seed(42)
    escola = db.criar_escola(sessao, nome)
    turma = db.criar_turma(sessao, escola.id, "4A", ano_escolar=ano)
    db.adicionar_alunos(sessao, turma.id, [f"A{i} {nome[-4:]}" for i in range(alunos)])

    gabarito = {i + 1: "A" for i in range(len(layout))}
    conteudos = {i + 1: c for i, c in enumerate(layout)}
    prova = db.criar_prova(
        sessao, turma.id, "Diagnóstica", gabarito, "Matemática", conteudos
    )

    for aluno in db.listar_alunos(sessao, turma.id):
        detalhamento, acertos = [], 0
        for numero in range(1, len(layout) + 1):
            if _random.random() < perfil[conteudos[numero]]:
                detalhamento.append(
                    {"questao": numero, "marcada": "A", "correta": "A", "status": "correto"}
                )
                acertos += 1
            else:
                detalhamento.append(
                    {"questao": numero, "marcada": "B", "correta": "A", "status": "incorreto"}
                )
        db.salvar_resultado(
            sessao,
            prova.id,
            aluno.id,
            {
                "nota": acertos,
                "acertos": acertos,
                "erros": len(layout) - acertos,
                "em_branco": 0,
                "rasuras": 0,
                "detalhamento": detalhamento,
            },
        )
    return escola


def test_escolas_parecidas_saem_como_empate(sessao):
    """
    O caso que mais importa acertar. Duas escolas com desempenho quase
    igual não podem aparecer ranqueadas — a diferença é do acaso.
    """
    import comparativo

    layout = ["Frações"] * 5 + ["Geometria"] * 5
    a = _escola_com_perfil(sessao, "EM Alfa", {"Frações": 0.62, "Geometria": 0.78}, layout)
    b = _escola_com_perfil(sessao, "EM Beta", {"Frações": 0.60, "Geometria": 0.76}, layout)

    resultado = comparativo.comparar_escolas(sessao, [a.id, b.id], ano_escolar="4")
    diferenca = resultado["diferencas"][0]

    assert diferenca["significativa"] is False
    assert "empatadas" in diferenca["leitura"]


def test_escola_bem_pior_e_identificada(sessao):
    import comparativo

    layout = ["Frações"] * 5 + ["Geometria"] * 5
    a = _escola_com_perfil(sessao, "EM Alfa", {"Frações": 0.62, "Geometria": 0.78}, layout)
    c = _escola_com_perfil(sessao, "EM Gama", {"Frações": 0.31, "Geometria": 0.74}, layout)

    resultado = comparativo.comparar_escolas(sessao, [a.id, c.id], ano_escolar="4")

    assert resultado["diferencas"][0]["significativa"] is True

    fracoes = next(l for l in resultado["conteudos"] if l["conteudo"] == "Frações")
    geometria = next(l for l in resultado["conteudos"] if l["conteudo"] == "Geometria")

    # A diferença está em Frações, e só nela
    assert fracoes["vao"]["significativa"] is True
    assert geometria["vao"]["significativa"] is False


def test_todo_numero_vem_com_margem_de_erro(sessao):
    import comparativo

    layout = ["Frações"] * 5
    a = _escola_com_perfil(sessao, "EM Um", {"Frações": 0.6}, layout)
    b = _escola_com_perfil(sessao, "EM Dois", {"Frações": 0.6}, layout)

    resultado = comparativo.comparar_escolas(sessao, [a.id, b.id])

    for escola in resultado["escolas"]:
        assert escola["margem_erro"] is not None
    for linha in resultado["conteudos"]:
        for celula in linha["celulas"]:
            assert celula["margem_erro"] is not None


def test_amostra_pequena_e_sinalizada_no_comparativo(sessao):
    import comparativo

    layout = ["Frações"] * 2
    a = _escola_com_perfil(sessao, "EM Pouca", {"Frações": 0.5}, layout, alunos=3)
    b = _escola_com_perfil(sessao, "EM Outra", {"Frações": 0.5}, layout, alunos=3)

    resultado = comparativo.comparar_escolas(sessao, [a.id, b.id])

    assert all(not e["comparavel"] for e in resultado["escolas"])
    assert any("não sustentam comparação" in aviso for aviso in resultado["avisos"])


def test_comparativo_exige_duas_escolas(client):
    assert client.get("/api/comparativo?escolas=1").status_code == 400
    assert client.get("/api/comparativo?escolas=").status_code == 400


def test_comparativo_pela_api(client):
    import dados as db

    layout = ["Frações"] * 5 + ["Geometria"] * 5
    with db.SessionLocal() as sessao:
        a = _escola_com_perfil(
            sessao, "EM API Alfa", {"Frações": 0.62, "Geometria": 0.78}, layout
        )
        c = _escola_com_perfil(
            sessao, "EM API Gama", {"Frações": 0.31, "Geometria": 0.74}, layout
        )
        ids = f"{a.id},{c.id}"

    corpo = client.get(f"/api/comparativo?escolas={ids}&ano_escolar=4").json()

    assert len(corpo["escolas"]) == 2
    assert corpo["recorte"]["ano_escolar"] == "4º ano"
    assert corpo["diferencas"]
    assert all(e["margem_erro"] is not None for e in corpo["escolas"])


# ==================================================================
# MIGRACAO DE SCHEMA
# ==================================================================
def _banco_temporario(tmp_path):
    """
    Engine isolada num arquivo próprio.

    Testar migração no banco compartilhado da suíte quebraria os outros
    testes: o ponto aqui é justamente mexer no schema.
    """
    from sqlalchemy import create_engine

    caminho = tmp_path / "migracao.db"
    return create_engine(f"sqlite:///{caminho}"), caminho


def test_banco_novo_ja_nasce_na_versao_atual(tmp_path):
    from dados import migracoes
    from dados.modelos import Base

    engine, _ = _banco_temporario(tmp_path)
    resumo = migracoes.sincronizar(engine, Base.metadata)

    assert resumo["banco_novo"] is True
    assert resumo["versao"] == migracoes.VERSAO_ATUAL
    assert resumo["colunas_acrescentadas"] == []


def test_coluna_nova_e_acrescentada_sem_apagar_dados(tmp_path):
    """
    O problema que este módulo resolve: até aqui, cada versão do sistema
    vinha com "apague o banco". Aceitável enquanto não havia
    nota de aluno dentro; perda grave agora que há.
    """
    from sqlalchemy import text

    from dados import migracoes
    from dados.modelos import Base

    engine, _ = _banco_temporario(tmp_path)
    migracoes.sincronizar(engine, Base.metadata)

    with engine.begin() as conexao:
        conexao.execute(
            text("INSERT INTO escolas (nome, criada_em) VALUES ('EM Antiga', '2026-01-01')")
        )
        # Volta o banco para uma versão anterior do schema
        conexao.execute(text("ALTER TABLE turmas DROP COLUMN ano_escolar"))
        conexao.execute(text("ALTER TABLE questoes DROP COLUMN habilidade"))

    resumo = migracoes.sincronizar(engine, Base.metadata)

    assert "turmas.ano_escolar" in resumo["colunas_acrescentadas"]
    assert "questoes.habilidade" in resumo["colunas_acrescentadas"]

    with engine.begin() as conexao:
        nomes = [linha[0] for linha in conexao.execute(text("SELECT nome FROM escolas"))]
    assert nomes == ["EM Antiga"], "os dados precisam sobreviver à migração"


def test_tabela_nova_e_criada_num_banco_existente(tmp_path):
    from sqlalchemy import inspect, text

    from dados import migracoes
    from dados.modelos import Base

    engine, _ = _banco_temporario(tmp_path)
    migracoes.sincronizar(engine, Base.metadata)

    with engine.begin() as conexao:
        conexao.execute(
            text("INSERT INTO escolas (nome, criada_em) VALUES ('EM Dados', '2026-01-01')")
        )
        conexao.execute(text("DROP TABLE tickets"))

    migracoes.sincronizar(engine, Base.metadata)

    assert "tickets" in inspect(engine).get_table_names()
    with engine.begin() as conexao:
        assert conexao.execute(text("SELECT COUNT(*) FROM escolas")).scalar() == 1


def test_sincronizar_e_idempotente(tmp_path):
    """Rodar no startup toda vez não pode acumular efeito."""
    from dados import migracoes
    from dados.modelos import Base

    engine, _ = _banco_temporario(tmp_path)
    migracoes.sincronizar(engine, Base.metadata)

    for _ in range(3):
        resumo = migracoes.sincronizar(engine, Base.metadata)
        assert resumo["colunas_acrescentadas"] == []
        assert resumo["migracoes_aplicadas"] == []
        assert resumo["versao"] == migracoes.VERSAO_ATUAL


def test_versao_do_banco_nao_e_rebaixada(tmp_path):
    """
    Rodar um código mais antigo sobre um banco mais novo não pode baixar
    o carimbo — migrações já aplicadas rodariam de novo.
    """
    from dados import migracoes
    from dados.modelos import Base

    engine, _ = _banco_temporario(tmp_path)
    migracoes.sincronizar(engine, Base.metadata)
    migracoes.gravar_versao(engine, 99)

    migracoes.sincronizar(engine, Base.metadata)
    assert migracoes.ler_versao(engine) == 99


# ==================================================================
# BACKUP
# ==================================================================
def test_backup_traz_os_dados_pedagogicos(client):
    import json as _json

    escola = client.post("/api/escolas", data={"nome": "EM Backup"}).json()
    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola["id"], "nome": "TB", "ano_escolar": "4"},
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Ana Backup"})
    client.post(
        "/api/provas",
        data={
            "turma_id": turma["id"],
            "titulo": "Prova Backup",
            "gabarito": "AB",
            "conteudos": "Frações\nGeometria",
        },
    )

    resposta = client.get("/api/admin/backup")
    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["content-disposition"]

    dados = _json.loads(resposta.content)
    escolas = {e["nome"] for e in dados["escolas"]}
    assert "EM Backup" in escolas

    registro = next(e for e in dados["escolas"] if e["nome"] == "EM Backup")
    assert registro["turmas"][0]["alunos"][0]["nome"] == "Ana Backup"
    assert registro["turmas"][0]["provas"][0]["questoes"][0]["conteudo"] == "Frações"


def test_backup_nao_leva_senhas_nem_tickets(client):
    """
    Arquivo de backup circula por e-mail e pen drive. Credencial não
    pode viajar assim.
    """
    conteudo = client.get("/api/admin/backup").content.decode("utf-8").lower()

    assert "senha_hash" not in conteudo
    assert "pbkdf2" not in conteudo
    assert "codigo_hash" not in conteudo
    assert "token" not in conteudo


def test_professor_comum_nao_baixa_backup(client):
    import dados as db

    escola = client.post("/api/escolas", data={"nome": "EM Sem Backup"}).json()
    client.post(
        "/api/usuarios",
        data={
            "email": "sem-backup@e.br",
            "nome": "Comum",
            "senha": "senha-de-teste",
            "papel": "membro",
            "escola_id": escola["id"],
        },
    )
    with db.SessionLocal() as sessao:
        _, codigo = db.criar_ticket(sessao, "sem backup")

    comum = TestClient(app)
    comum.post(
        "/api/auth/entrar",
        data={"email": "sem-backup@e.br", "senha": "senha-de-teste", "ticket": codigo},
    )
    assert comum.get("/api/admin/backup").status_code == 403


# ==================================================================
# ORGANIZACAO DA CAMADA DE DADOS
# ==================================================================
def test_cada_funcao_mora_no_repositorio_do_seu_assunto():
    """
    `obter_aluno` estava em provas.py. Funciona, mas quem procura por
    aluno não abre o arquivo de provas — e é assim que um módulo vira
    depósito.
    """
    import dados

    esperado = {
        "criar_escola": "escolas",
        "criar_turma": "turmas",
        "obter_aluno": "alunos",
        "adicionar_alunos": "alunos",
        "criar_prova": "provas",
        "salvar_resultado": "resultados",
        "historico_do_aluno": "historico",
        "autenticar": "acesso",
        "validar_ticket": "tickets",
        "obter_segredo_qr": "configuracao",
        "limpar_dados_pedagogicos": "manutencao",
        "exportar_dados": "manutencao",
    }

    for funcao, modulo in esperado.items():
        real = getattr(dados, funcao).__module__
        assert real.endswith(modulo), f"{funcao} está em {real}, esperado {modulo}"


# ==================================================================
# PAPEIS E ESCOPO DE DADOS
# ==================================================================
@pytest.fixture()
def tres_papeis(client):
    """
    Monta um município de teste: duas escolas, um admin, um membro
    vinculado à escola 1 e um convidado.
    """
    import uuid

    import dados as db

    marca = uuid.uuid4().hex[:6]
    escola_a = client.post("/api/escolas", data={"nome": f"EM Alfa {marca}"}).json()
    escola_b = client.post("/api/escolas", data={"nome": f"EM Beta {marca}"}).json()

    turma = client.post(
        "/api/turmas",
        data={"escola_id": escola_a["id"], "nome": f"4A{marca}", "ano_escolar": "4"},
    ).json()
    client.post(f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Ana\nBruno"})
    prova = client.post(
        "/api/provas",
        data={"turma_id": turma["id"], "titulo": "P1", "gabarito": "ABCDE"},
    ).json()

    client.post(
        "/api/usuarios",
        data={
            "email": f"membro{marca}@e.br",
            "nome": "Escola Alfa",
            "senha": "senha-de-teste",
            "papel": "membro",
            "escola_id": escola_a["id"],
        },
    )
    client.post(
        "/api/usuarios",
        data={
            "email": f"convidado{marca}@e.br",
            "nome": "Professor",
            "senha": "senha-de-teste",
            "papel": "convidado",
        },
    )

    def entrar(email):
        cliente = TestClient(app)
        with db.SessionLocal() as sessao:
            usuario = db.autenticar(sessao, email, "senha-de-teste")
            cliente.cookies.set(
                seguranca_modulo().NOME_COOKIE, db.abrir_sessao(sessao, usuario)
            )
        return cliente

    return {
        "escola_a": escola_a,
        "escola_b": escola_b,
        "turma": turma,
        "prova": prova,
        "admin": client,
        "membro": entrar(f"membro{marca}@e.br"),
        "convidado": entrar(f"convidado{marca}@e.br"),
    }


def seguranca_modulo():
    import seguranca

    return seguranca


def test_membro_so_enxerga_a_propria_escola(tres_papeis):
    """
    O ponto central do papel de escola. Se vazar, uma escola vê as
    notas das outras — e isso é dado pessoal de criança sob a LGPD.
    """
    membro = tres_papeis["membro"]

    escolas = membro.get("/api/escolas").json()
    assert len(escolas) == 1
    assert escolas[0]["id"] == tres_papeis["escola_a"]["id"]

    # A análise da própria escola abre; a da vizinha, não.
    assert (
        membro.get(f"/api/escolas/{tres_papeis['escola_a']['id']}/analise").status_code
        == 200
    )
    assert (
        membro.get(f"/api/escolas/{tres_papeis['escola_b']['id']}/analise").status_code
        == 403
    )


def test_membro_nao_edita_cadastro(tres_papeis):
    """
    A escola corrige e consulta; quem monta turma, aluno e prova é a
    secretaria. Sem isso, cada escola alteraria a avaliação que deveria
    ser comum a todas.
    """
    membro = tres_papeis["membro"]
    escola = tres_papeis["escola_a"]
    turma = tres_papeis["turma"]

    assert membro.post("/api/escolas", data={"nome": "Nova"}).status_code == 403
    assert (
        membro.post(
            "/api/turmas",
            data={"escola_id": escola["id"], "nome": "X", "ano_escolar": "4"},
        ).status_code
        == 403
    )
    assert (
        membro.post(
            f"/api/turmas/{turma['id']}/alunos", data={"nomes": "Intruso"}
        ).status_code
        == 403
    )
    assert (
        membro.post(
            "/api/provas",
            data={"turma_id": turma["id"], "titulo": "Y", "gabarito": "AB"},
        ).status_code
        == 403
    )


def test_membro_nao_compara_escolas(tres_papeis):
    """A comparação entre escolas é exclusiva da secretaria."""
    membro = tres_papeis["membro"]
    ids = f"{tres_papeis['escola_a']['id']},{tres_papeis['escola_b']['id']}"
    assert membro.get(f"/api/comparativo?escolas={ids}").status_code == 403


def test_membro_nao_cria_usuarios_nem_tickets(tres_papeis):
    membro = tres_papeis["membro"]
    assert (
        membro.post(
            "/api/usuarios",
            data={
                "email": "novo@e.br",
                "nome": "N",
                "senha": "senha-de-teste",
                "papel": "convidado",
            },
        ).status_code
        == 403
    )
    assert membro.post("/api/tickets", data={"descricao": "t"}).status_code == 403
    assert membro.get("/api/tickets").status_code == 403


def test_membro_corrige_e_a_nota_e_gravada(tres_papeis, segredo_qr):
    """
    Corrigir é justamente o que a escola precisa fazer. Bloquear
    escrita de resultado esvaziaria o papel.
    """
    membro = tres_papeis["membro"]
    prova = tres_papeis["prova"]
    turma = tres_papeis["turma"]

    aluno = tres_papeis["admin"].get(f"/api/turmas/{turma['id']}/alunos").json()[0]
    gabarito = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    foto = simular_foto(
        gerar_cartao(
            DadosCartao(
                prova_id=f"P{prova['id']}",
                gabarito=gabarito,
                aluno_id=aluno["id"],
                aluno_nome=aluno["nome"],
                gabarito_no_qr=False,
                segredo=segredo_qr,
            ),
            marcacoes=gabarito,
        )
    )

    corpo = membro.post(
        "/api/corrigir", files={"file": ("f.jpg", foto, "image/jpeg")}
    ).json()

    assert corpo["nota"] == 10.0
    assert corpo["salvo"] is True


def test_convidado_nao_toca_no_banco(tres_papeis):
    """
    Uso individual do professor: nada fica guardado. Se ele conseguisse
    ler qualquer tabela, o papel deixaria de ser "sem banco".
    """
    convidado = tres_papeis["convidado"]

    for rota in ("/api/escolas", "/api/turmas", "/api/provas"):
        assert convidado.get(rota).status_code == 403, rota


def test_convidado_corrige_mas_nada_e_salvo(tres_papeis):
    """O cartão avulso funciona; a nota aparece e não vira registro."""
    convidado = tres_papeis["convidado"]

    previa = convidado.post(
        "/api/cartao", data={"gabarito": "ABCDE", "formato": "png"}
    )
    assert previa.status_code == 200

    gabarito = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    foto = simular_foto(
        gerar_cartao(
            DadosCartao(prova_id="AVULSO", gabarito=gabarito), marcacoes=gabarito
        )
    )
    corpo = convidado.post(
        "/api/corrigir", files={"file": ("f.jpg", foto, "image/jpeg")}
    ).json()

    assert corpo["nota"] == 10.0
    assert corpo["salvo"] is False


def test_nenhuma_rota_de_dados_responde_ao_convidado(tres_papeis):
    """
    Varredura automática de TODAS as rotas.

    É a proteção que sobrevive ao tempo: um endpoint criado daqui a seis
    meses, sem a checagem de papel, aparece aqui como falha em vez de
    virar vazamento silencioso. A lista de exceções é explícita — fica
    claro no diff quando alguém libera uma rota nova.
    """
    convidado = tres_papeis["convidado"]

    liberadas = {
        "/api/health",
        "/api/papeis",
        "/api/anos-escolares",
        "/api/conteudos",
        "/api/cartao",
        "/api/corrigir",
        "/api/corrigir-lote",
        "/api/corrigir-arquivo",
        "/api/contar-folhas",
        "/api/ler-gabarito",
        "/api/debug/preview",
    }

    vazamentos = []
    for rota in app.routes:
        caminho = getattr(rota, "path", "")
        if not hasattr(rota, "methods"):
            continue
        if not caminho.startswith("/api/") or "{" in caminho:
            continue
        if caminho in liberadas or caminho.startswith("/api/auth/"):
            continue

        for metodo in sorted(rota.methods - {"HEAD", "OPTIONS"}):
            resposta = (
                convidado.get(caminho)
                if metodo == "GET"
                else convidado.post(caminho, data={})
            )
            if resposta.status_code < 300:
                vazamentos.append(f"{metodo} {caminho} -> {resposta.status_code}")

    assert vazamentos == [], f"rotas abertas ao convidado: {vazamentos}"


def test_admin_faz_tudo(tres_papeis):
    admin = tres_papeis["admin"]
    ids = f"{tres_papeis['escola_a']['id']},{tres_papeis['escola_b']['id']}"

    assert admin.get("/api/escolas").status_code == 200
    assert admin.get(f"/api/comparativo?escolas={ids}").status_code == 200
    assert admin.get("/api/tickets").status_code == 200
    assert (
        admin.get(f"/api/escolas/{tres_papeis['escola_b']['id']}/analise").status_code
        == 200
    )


def test_membro_precisa_de_escola_vinculada(client):
    """
    Conta de escola sem escola entra no sistema e não vê nada. Recusar
    no cadastro evita um acesso que parece funcionar e não funciona.
    """
    resposta = client.post(
        "/api/usuarios",
        data={
            "email": "orfao@e.br",
            "nome": "Órfão",
            "senha": "senha-de-teste",
            "papel": "membro",
        },
    )
    assert resposta.status_code == 400
    assert "escola" in resposta.json()["erro"].lower()


def test_papel_desconhecido_e_recusado(client):
    resposta = client.post(
        "/api/usuarios",
        data={
            "email": "estranho@e.br",
            "nome": "X",
            "senha": "senha-de-teste",
            "papel": "superusuario",
        },
    )
    assert resposta.status_code == 400


def test_primeira_conta_nasce_admin():
    """
    É ela que cria todas as outras. `criar_usuario` tem padrão "membro"
    para que uma conta feita por engano venha com o menor acesso — e
    foi justamente esse padrão que, herdado aqui, deixou a instalação
    inteira sem administrador.
    """
    import dados as db

    with db.SessionLocal() as sessao:
        primeiro = sessao.scalars(
            db.select(db.Usuario).order_by(db.Usuario.id)
        ).first()
        assert primeiro is not None
        assert primeiro.papel == "admin"
        assert primeiro.administrador is True


# ==================================================================
# RELATORIO DO CONVIDADO
# ==================================================================
def _correcoes_falsas(quantidade: int = 5) -> list:
    import random as _random

    _random.seed(11)
    nomes = ["Ana Souza", "Bruno Lima", "Carla Alves", "Diego Rocha", "Elisa Nunes"]
    correcoes = []
    for indice in range(quantidade):
        detalhamento, acertos = [], 0
        for numero in range(1, 11):
            certo = _random.random() < 0.7
            acertos += certo
            detalhamento.append(
                {
                    "questao": numero,
                    "marcada": "A" if certo else "B",
                    "correta": "A",
                    "status": "correto" if certo else "incorreto",
                }
            )
        correcoes.append(
            {
                "aluno_nome": nomes[indice % len(nomes)] + f" {indice}",
                "nota": round(acertos, 1),
                "acertos": acertos,
                "total_questoes": 10,
                "erros": 10 - acertos,
                "em_branco": 0,
                "rasuras": 0,
                "detalhamento": detalhamento,
            }
        )
    return correcoes


def test_convidado_baixa_o_relatorio_em_pdf(tres_papeis):
    """
    Sem banco, o resultado sumiria ao fechar a aba. O PDF é o que o
    convidado leva embora.
    """
    import json as _json

    from pypdf import PdfReader

    resposta = tres_papeis["convidado"].post(
        "/api/relatorio",
        json={
            "correcoes": _correcoes_falsas(),
            "titulo": "Prova de Matemática",
            "turma": "4º ano B",
            "formato": "pdf",
        },
    )

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
    assert len(PdfReader(io.BytesIO(resposta.content)).pages) >= 1


def test_relatorio_em_csv(tres_papeis):
    import json as _json

    resposta = tres_papeis["convidado"].post(
        "/api/relatorio",
        json={"correcoes": _correcoes_falsas(3), "formato": "csv"},
    )

    assert resposta.status_code == 200
    texto = resposta.content.decode("utf-8")
    assert texto.startswith("\ufeff")        # BOM, para o Excel
    assert "Ana Souza" in texto
    assert len(texto.strip().splitlines()) == 4   # cabeçalho + 3 alunos


def test_relatorio_quebra_pagina_em_turma_grande(tres_papeis):
    """Uma turma de 40 não pode sair cortada na primeira folha."""
    import json as _json

    from pypdf import PdfReader

    resposta = tres_papeis["convidado"].post(
        "/api/relatorio",
        json={"correcoes": _correcoes_falsas(40), "formato": "pdf"},
    )

    assert resposta.status_code == 200
    assert len(PdfReader(io.BytesIO(resposta.content)).pages) >= 2


def test_relatorio_calcula_acerto_por_questao():
    """
    É o dado que transforma lista de notas em informação de aula.
    Em branco não entra na conta: não saber e não ter chegado na
    questão são coisas diferentes.
    """
    import relatorio

    dados = relatorio.DadosRelatorio(
        linhas=[
            relatorio.LinhaRelatorio(
                identificacao="Aluno 1",
                nota=5.0,
                acertos=1,
                total_questoes=2,
                detalhamento=[
                    {"questao": 1, "status": "correto", "marcada": "A", "correta": "A"},
                    {"questao": 2, "status": "em_branco", "marcada": None, "correta": "A"},
                ],
            ),
            relatorio.LinhaRelatorio(
                identificacao="Aluno 2",
                nota=0.0,
                acertos=0,
                total_questoes=2,
                detalhamento=[
                    {"questao": 1, "status": "incorreto", "marcada": "B", "correta": "A"},
                    {"questao": 2, "status": "incorreto", "marcada": "B", "correta": "A"},
                ],
            ),
        ]
    )

    por_questao = {q["questao"]: q for q in dados.acerto_por_questao()}
    assert por_questao[1]["percentual"] == 50    # 1 de 2 responderam certo
    assert por_questao[2]["respostas"] == 1      # o em branco ficou de fora


def test_relatorio_recusa_conteudo_vazio(tres_papeis):
    vazio = tres_papeis["convidado"].post("/api/relatorio", json={"correcoes": []})
    assert vazio.status_code == 400

    # Corpo malformado é recusado pela validação do modelo (422).
    invalido = tres_papeis["convidado"].post("/api/relatorio", json={"algo": "errado"})
    assert invalido.status_code == 422


def test_relatorio_limita_o_tamanho(tres_papeis):
    """
    O conteúdo vem do navegador. Sem teto, um payload inflado viraria
    um PDF de mil páginas e derrubaria a instância.
    """
    import json as _json

    import main

    from pypdf import PdfReader

    resposta = tres_papeis["convidado"].post(
        "/api/relatorio",
        json={"correcoes": _correcoes_falsas(5) * 200, "formato": "csv"},
    )
    assert resposta.status_code == 200
    linhas = resposta.content.decode("utf-8").strip().splitlines()
    assert len(linhas) - 1 <= main.MAXIMO_LINHAS_RELATORIO


# ==================================================================
# MODO EXPLICACAO E MANUAL
# ==================================================================
def test_arquivo_de_ajuda_e_servido(client):
    """A tela carrega o conteúdo por /static; sem a montagem, dá 404."""
    resposta = client.get("/static/ajuda.js")
    assert resposta.status_code == 200
    assert "javascript" in resposta.headers["content-type"]


def test_ajuda_cobre_todas_as_telas():
    """
    Uma tela sem texto de ajuda é pior que nenhuma ajuda: o professor
    liga o modo explicação, chega nela e encontra o vazio.
    """
    import re

    caminho_html = os.path.join(RAIZ, "static", "index.html")
    caminho_ajuda = os.path.join(RAIZ, "static", "ajuda.js")

    with open(caminho_html, encoding="utf-8") as fp:
        html = fp.read()
    with open(caminho_ajuda, encoding="utf-8") as fp:
        ajuda = fp.read()

    paineis = set(re.findall(r'id="painel-([a-z]+)"', html))
    documentados = set(re.findall(r"^  ([a-z]+): \{", ajuda, re.M))

    # A própria tela de ajuda não precisa de artigo sobre si mesma.
    faltando = paineis - documentados - {"ajuda"}
    assert faltando == set(), f"telas sem explicação: {faltando}"


def test_cada_artigo_de_ajuda_tem_conteudo_util():
    """Passo a passo e cuidados são o que o professor de fato lê."""
    import re

    caminho = os.path.join(RAIZ, "static", "ajuda.js")
    with open(caminho, encoding="utf-8") as fp:
        ajuda = fp.read()

    for chave in re.findall(r"^  ([a-z]+): \{", ajuda, re.M):
        bloco = ajuda.split(f"  {chave}: {{", 1)[1]
        assert "titulo:" in bloco[:400], chave
        assert "resumo:" in bloco[:800], chave
        assert "passos:" in bloco[:4000] or "campos:" in bloco[:4000], chave


def test_interface_carrega_o_arquivo_de_ajuda(client):
    html = client.get("/").text
    assert 'src="/static/ajuda.js"' in html
    assert 'id="btnExplicacao"' in html
    assert 'id="painel-ajuda"' in html


# ==================================================================
# PRIMEIRO ACESSO E RECUPERACAO
# ==================================================================
def test_primeiro_acesso_devolve_papel_e_ticket():
    """
    O administrador precisa sair da primeira tela com o código em mãos.
    Sem ele, não entra de volta — e é justamente a conta que cria todas
    as outras.
    """
    import dados as db

    with db.SessionLocal() as sessao:
        primeiro = sessao.scalars(db.select(db.Usuario).order_by(db.Usuario.id)).first()

    assert primeiro.papel == "admin"
    assert primeiro.administrador is True
    # O ticket foi capturado pela fixture no momento da criação
    assert getattr(pytest, "ticket_admin", "").startswith("PF-")


def test_resumo_de_permissoes_informa_o_papel(client):
    estado = client.get("/api/auth/estado").json()
    assert estado["autenticado"] is True
    assert estado["administrador"] is True


def test_recuperar_admin_emite_ticket_novo(client):
    """
    O ticket não pode ser recuperado do banco — só o hash fica lá. A
    saída é emitir um novo, e a proteção é o acesso ao computador onde
    o sistema está instalado.
    """
    import subprocess

    import dados as db

    with db.SessionLocal() as sessao:
        antes = db.contar(sessao, db.Ticket)

    resultado = subprocess.run(
        [sys.executable, os.path.join(RAIZ, "recuperar_admin.py")],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        timeout=60,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "TICKET:" in resultado.stdout
    assert "PF-" in resultado.stdout

    with db.SessionLocal() as sessao:
        assert db.contar(sessao, db.Ticket) == antes + 1


def test_ticket_recuperado_ja_nasce_vinculado(client):
    """
    Um código de recuperação solto seria acesso livre esperando para
    ser usado por outra pessoa.
    """
    import subprocess

    import dados as db

    subprocess.run(
        [sys.executable, os.path.join(RAIZ, "recuperar_admin.py")],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        timeout=60,
    )

    with db.SessionLocal() as sessao:
        recuperados = [
            t
            for t in db.listar_tickets(sessao)
            if "recupera" in (t.descricao or "").lower()
        ]
        assert recuperados
        assert all(t.usuario_id is not None for t in recuperados)


# ==================================================================
# PALETA INSTITUCIONAL
# ==================================================================
def test_paleta_usa_as_cores_da_logo():
    """
    O azul e o verde do site são EXATAMENTE os da logo. Usar um azul
    próximo, mas diferente, deixaria a marca parecendo deslocada dentro
    da própria tela.
    """
    caminho = os.path.join(RAIZ, "static", "index.html")
    with open(caminho, encoding="utf-8") as fp:
        html = fp.read().lower()

    assert "#013682" in html, "falta o azul da logo"
    assert "#067821" in html, "falta o verde da logo"

    # Azuis de versões anteriores, antes da identidade municipal.
    for antiga in ("#16469d", "#18537e", "#29854f", "#ec670c"):
        assert antiga not in html, f"cor antiga reapareceu: {antiga}"


def test_arquivos_da_logo_estao_no_lugar():
    """
    A tela referencia três recortes. Faltando um, aparece o ícone de
    imagem quebrada no cabeçalho — a primeira coisa que se vê.
    """
    from PIL import Image as _Image

    for arquivo in ("logo-avalia.png", "logo-marca.png", "logo-institucional.png"):
        caminho = os.path.join(RAIZ, "static", arquivo)
        assert os.path.exists(caminho), arquivo

        imagem = _Image.open(caminho)
        # Fundo transparente: sobre a barra branca ou sobre o selo do
        # rodapé, um retângulo branco em volta apareceria.
        assert imagem.mode == "RGBA", f"{arquivo} precisa ter transparência"


def test_logo_e_servida_pela_api(client):
    for arquivo in ("logo-avalia.png", "logo-marca.png", "logo-institucional.png"):
        resposta = client.get(f"/static/{arquivo}")
        assert resposta.status_code == 200, arquivo
        assert resposta.headers["content-type"] == "image/png"


def test_nome_da_plataforma_e_avalia_frg():
    """O nome antigo não pode sobrar em canto nenhum da interface."""
    caminho = os.path.join(RAIZ, "static", "index.html")
    with open(caminho, encoding="utf-8") as fp:
        html = fp.read()

    assert "Avalia FRG" in html
    assert "Prova Fácil" not in html
