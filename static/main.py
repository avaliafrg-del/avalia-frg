"""
main.py
------------------------------------------------------------------
Camada HTTP da aplicacao.

Responsabilidades:
    - servir a interface web para os professores (static/index.html)
    - cadastro de escolas, turmas e alunos
    - geracao dos cartoes-resposta (genericos ou nominais)
    - correcao das fotos e gravacao das notas
    - boletim da turma

No Google Cloud Run o servidor DEVE escutar na porta informada pela
variavel de ambiente PORT (padrao 8080).
------------------------------------------------------------------
"""

from __future__ import annotations

import io
import json
import logging
import os
import unicodedata

import cv2
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response as RespostaFastAPI,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import seguranca

import analitico
import comparativo
import dados as db
import relatorio
import lote
import permissoes
import deteccao_grade
import estampa
import escolaridade
from gerador import (
    DadosCartao,
    anexar_prova,
    cartao_para_png,
    gerar_cartao,
    montar_pdf_completo,
    montar_pdf_varios_cartoes,
    parse_gabarito_digitado,
)
from omr_engine import (
    CONFIG_PADRAO,
    LeituraQR,
    engine_com_layout,
    montar_qr_code,
    OMRError,
    ler_qr_da_imagem,
    obter_engine,
    parse_qr_code,
)
from schemas import (
    AlunoResposta,
    PedidoRelatorio,
    UsuarioResposta,
    ComparativoEscolas,
    AnoEscolarOpcao,
    Boletim,
    DesempenhoTurma,
    HistoricoAluno,
    EscolaResposta,
    ItemLote,
    LinhaBoletim,
    PainelAnalise,
    ProvaResposta,
    QuestaoResposta,
    RespostaCorrecao,
    RespostaErro,
    RespostaGabaritoLido,
    RespostaLote,
    TurmaResposta,
)

# ==================================================================
# CONFIGURACAO GERAL
# ==================================================================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("avaliafrg.api")

DIRETORIO_BASE = os.path.dirname(os.path.abspath(__file__))
DIRETORIO_ESTATICO = os.path.join(DIRETORIO_BASE, "static")

# Assinaturas de arquivo ("magic bytes"). Validamos pelo CONTEUDO em vez
# do content-type porque navegador e sistema operacional erram esse
# campo com frequencia: um PDF chega como "application/octet-stream" em
# varias combinacoes de Windows e arrastar-e-soltar, e o professor
# levava um 415 sem entender por que.
ASSINATURAS_IMAGEM = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",        # JPEG
    b"GIF87a",
    b"GIF89a",
    b"BM",                    # BMP
)

TAMANHO_MAXIMO_MB = int(os.getenv("TAMANHO_MAXIMO_MB", "15"))
TAMANHO_MAXIMO_BYTES = TAMANHO_MAXIMO_MB * 1024 * 1024
MAXIMO_ARQUIVOS_LOTE = int(os.getenv("MAXIMO_ARQUIVOS_LOTE", "60"))
# O lote vem num arquivo so, entao o teto precisa ser bem maior que o de
# uma foto avulsa: um PDF de 40 paginas digitalizadas passa de 60 MB.
TAMANHO_MAXIMO_LOTE_MB = int(os.getenv("TAMANHO_MAXIMO_LOTE_MB", "120"))
TAMANHO_MAXIMO_LOTE_BYTES = TAMANHO_MAXIMO_LOTE_MB * 1024 * 1024
# Teto do relatorio do convidado. O conteudo vem do navegador, entao
# precisa de limite: sem ele, um payload gigante viraria um PDF de mil
# paginas e derrubaria a instancia.
MAXIMO_LINHAS_RELATORIO = int(os.getenv("MAXIMO_LINHAS_RELATORIO", "300"))

# Cartao sem assinatura valida nao entra no boletim. Desligue apenas se
# precisar corrigir cartoes impressos antes desta versao — e saiba que
# isso reabre a possibilidade de alguem imprimir um QR proprio.
EXIGIR_ASSINATURA = os.getenv("EXIGIR_ASSINATURA", "1") not in ("0", "false", "nao")

ORIGENS_CORS: List[str] = [
    origem.strip()
    for origem in os.getenv("CORS_ORIGINS", "*").split(",")
    if origem.strip()
]

@asynccontextmanager
async def ciclo_de_vida(_: "FastAPI"):
    """Garante o schema antes de atender a primeira requisicao."""
    db.criar_tabelas()
    logger.info("Banco pronto (%s)", db.DATABASE_URL.split("://")[0])
    yield


app = FastAPI(
    lifespan=ciclo_de_vida,
    title="Avalia FRG — Plataforma Municipal de Simulados",
    description=(
        "Gera cartoes-resposta com QR Code, corrige provas a partir de fotos "
        "e guarda as notas por escola, turma e aluno."
    ),
    version="10.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENS_CORS,
    allow_credentials=False,   # com allow_origins=["*"] precisa ficar False
    allow_methods=["*"],
    allow_headers=["*"],
)


# Serve os arquivos da interface (ajuda.js e o que vier depois). A
# pagina em si continua sendo entregue por `pagina_inicial`, que sabe
# procurar o index.html tambem na raiz do projeto.
if os.path.isdir(DIRETORIO_ESTATICO):
    app.mount("/static", StaticFiles(directory=DIRETORIO_ESTATICO), name="static")


# O lifespan cobre a execucao normal, mas a app tambem e importada
# direto em testes e scripts. Criar o schema aqui evita depender de
# quem instanciou o servidor.
# Guardado para o health check informar em que versão o schema está —
# útil quando alguém sobe o código novo sobre um banco antigo.
_resumo_schema = db.criar_tabelas()


# ==================================================================
# AUTENTICACAO
# ==================================================================
# Rotas abertas. Tudo o mais exige sessao. A lista e de EXCECOES, e nao
# de rotas protegidas, de proposito: esquecer de proteger um endpoint
# novo seria um vazamento silencioso, enquanto esquecer de liberar um
# endpoint publico da um erro visivel na hora.
ROTAS_ABERTAS = {
    "/",
    "/api/health",
    "/api/auth/estado",
    "/api/auth/entrar",
    "/api/auth/sair",
    "/api/auth/primeira-conta",
    "/docs",
    "/openapi.json",
}

# Tentativas de login por IP, para atrasar quem esta adivinhando senha.
# Em memoria: some no restart e nao e compartilhado entre instancias,
# mas ja quebra o ataque simples sem exigir Redis.
_tentativas: Dict[str, list] = {}
MAXIMO_TENTATIVAS = 8
JANELA_TENTATIVAS_S = 300


def _dentro_do_limite(ip: str) -> bool:
    """False quando o IP ja errou demais dentro da janela."""
    import time as _time

    agora_s = _time.time()
    recentes = [t for t in _tentativas.get(ip, []) if agora_s - t < JANELA_TENTATIVAS_S]
    _tentativas[ip] = recentes
    return len(recentes) < MAXIMO_TENTATIVAS


def _registrar_falha(ip: str) -> None:
    """
    Conta apenas tentativas que FALHARAM.

    Contar acerto junto travava quem so estava usando o sistema: numa
    escola atras de um unico IP, alguns professores entrando na mesma
    manha esgotariam a cota sem ninguem ter errado nada.
    """
    import time as _time

    _tentativas.setdefault(ip, []).append(_time.time())


@app.middleware("http")
async def exigir_sessao(request: Request, proximo):
    caminho = request.url.path

    if caminho in ROTAS_ABERTAS or not caminho.startswith("/api/"):
        return await proximo(request)

    token = request.cookies.get(seguranca.NOME_COOKIE)
    with db.SessionLocal() as sessao:
        usuario = db.usuario_da_sessao(sessao, token)
        if usuario is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content=RespostaErro(
                    erro="Sessao expirada ou nao iniciada. Entre novamente."
                ).model_dump(),
            )
        request.state.usuario_id = usuario.id
        request.state.usuario_nome = usuario.nome
        request.state.papel = usuario.papel
        request.state.escola_id = usuario.escola_id

    return await proximo(request)


def _definir_cookie(resposta: RespostaFastAPI, token: str, seguro: bool) -> None:
    resposta.set_cookie(
        key=seguranca.NOME_COOKIE,
        value=token,
        httponly=True,          # JavaScript nao le: reduz o estrago de um XSS
        samesite="lax",
        secure=seguro,          # so viaja em HTTPS quando o site e HTTPS
        max_age=seguranca.HORAS_SESSAO * 3600,
        path="/",
    )


def usuario_atual(
    request: Request, sessao: Session = Depends(db.obter_sessao)
) -> db.Usuario:
    """
    O usuário da requisição, carregado do banco.

    O middleware já garantiu que existe sessão válida; aqui o objeto é
    recarregado porque as permissões dependem de `papel` e `escola_id`,
    e ler isso do cookie seria confiar no cliente.
    """
    usuario = db.usuario_da_sessao(sessao, request.cookies.get(seguranca.NOME_COOKIE))
    if usuario is None:
        raise HTTPException(401, "Sessao expirada. Entre novamente.")
    return usuario


def _checar(funcao, *argumentos) -> None:
    """Traduz SemPermissao em 403, com a mensagem que o módulo escreveu."""
    try:
        funcao(*argumentos)
    except permissoes.SemPermissao as exc:
        raise HTTPException(403, str(exc))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _turma_permitida(sessao: Session, usuario, turma_id: int):
    """Carrega a turma conferindo que o usuário pode vê-la."""
    try:
        turma = db.obter_turma(sessao, turma_id)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    _checar(permissoes.exigir_acesso_a_escola, usuario, turma.escola_id)
    return turma


def _prova_permitida(sessao: Session, usuario, prova_id: int):
    try:
        prova = db.obter_prova(sessao, prova_id)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    _checar(permissoes.exigir_acesso_a_escola, usuario, prova.turma.escola_id)
    return prova


# ==================================================================
# ROTAS DE ACESSO
# ==================================================================
@app.get("/api/auth/estado", tags=["acesso"], summary="Quem esta logado")
def estado_do_acesso(request: Request, sessao: Session = Depends(db.obter_sessao)):
    """
    Diz a interface o que mostrar: tela de primeira conta, tela de
    login, ou o sistema.
    """
    if not db.ha_usuarios(sessao):
        return {"autenticado": False, "precisa_primeira_conta": True}

    usuario = db.usuario_da_sessao(sessao, request.cookies.get(seguranca.NOME_COOKIE))
    if usuario is None:
        return {"autenticado": False, "precisa_primeira_conta": False}

    return {
        "autenticado": True,
        "precisa_primeira_conta": False,
        "nome": usuario.nome,
        "email": usuario.email,
        "administrador": usuario.administrador,
    }


@app.post("/api/auth/primeira-conta", tags=["acesso"], summary="Cria a conta inicial")
def primeira_conta(
    request: Request,
    resposta: RespostaFastAPI,
    email: str = Form(...),
    nome: str = Form(...),
    senha: str = Form(...),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    So funciona enquanto nao existir NENHUM usuario.

    Sem essa trava, o endpoint viraria um cadastro publico e qualquer
    pessoa criaria conta no sistema da escola.
    """
    if db.ha_usuarios(sessao):
        raise HTTPException(
            403, "O sistema ja tem uma conta. Peca acesso a quem administra."
        )

    try:
        # A primeira conta nasce ADMIN por definição: é ela que cria
        # todas as outras. `criar_usuario` tem padrão "membro" para que
        # uma conta feita por engano venha com o menor acesso possível,
        # mas aqui o papel é explícito, e não herdado do padrão.
        usuario = db.criar_usuario(sessao, email, nome, senha, papel="admin")
    except (ValueError, seguranca.SenhaFraca) as exc:
        raise HTTPException(400, str(exc))

    # Ticket permanente de quem administra. E o unico momento em que o
    # codigo existe em texto: depois so fica o hash. Vai tambem para o
    # log do servidor, para nao se perder se a tela fechar antes da hora.
    ticket, codigo = db.criar_ticket(
        sessao, descricao=f"Ticket de {usuario.nome} (administracao)", dias_validade=None
    )
    logger.warning("TICKET DE ADMINISTRACAO: %s  — guarde este codigo", codigo)

    token = db.abrir_sessao(sessao, usuario)
    _definir_cookie(resposta, token, request.url.scheme == "https")
    db.usar_ticket(sessao, ticket, usuario)
    return {
        "sucesso": True,
        "nome": usuario.nome,
        **permissoes.resumo(usuario),
        "ticket": codigo,
        "aviso_ticket": (
            "Guarde este ticket. Ele sera pedido em todo login e nao pode ser "
            "recuperado depois — se perder, sera preciso emitir outro."
        ),
    }


@app.post("/api/auth/entrar", tags=["acesso"], summary="Entrar no sistema")
def entrar(
    request: Request,
    resposta: RespostaFastAPI,
    email: str = Form(...),
    senha: str = Form(...),
    # Form("") e nao Form(...): campo faltando deve virar "informe o
    # ticket" em portugues, e nao o erro cru de validacao do Pydantic.
    ticket: str = Form("", description="Ticket de acesso, ex.: PF-ABCD-EFGH."),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Entrar exige email, senha E ticket de acesso.

    A senha diz QUEM e a pessoa; o ticket diz que ela CONTINUA
    autorizada. Com as duas coisas separadas, cortar o acesso de quem
    saiu da escola e revogar um ticket — nao mexer na senha de ninguem.
    """
    ip = request.client.host if request.client else "desconhecido"
    if not _dentro_do_limite(ip):
        raise HTTPException(
            429, "Muitas tentativas. Aguarde alguns minutos e tente de novo."
        )

    # O ticket e conferido ANTES da senha: sem autorizacao valida nao ha
    # por que sequer testar credencial.
    try:
        registro_ticket = db.validar_ticket(sessao, ticket)
    except db.TicketInvalido as exc:
        _registrar_falha(ip)
        raise HTTPException(403, str(exc))

    try:
        usuario = db.autenticar(sessao, email, senha)
    except db.CredencialInvalida as exc:
        _registrar_falha(ip)
        raise HTTPException(401, str(exc))

    # Segunda conferencia, agora com a conta em maos: o ticket pode
    # estar preso a OUTRA pessoa. Repassar o codigo a um colega nao
    # pode dar acesso a ele.
    try:
        db.validar_ticket(sessao, ticket, usuario)
    except db.TicketInvalido as exc:
        _registrar_falha(ip)
        raise HTTPException(403, str(exc))

    db.usar_ticket(sessao, registro_ticket, usuario)
    token = db.abrir_sessao(sessao, usuario)
    _definir_cookie(resposta, token, request.url.scheme == "https")
    return {
        "sucesso": True,
        "nome": usuario.nome,
        "administrador": usuario.administrador,
    }


@app.post("/api/auth/sair", tags=["acesso"], summary="Sair do sistema")
def sair(
    request: Request,
    resposta: RespostaFastAPI,
    sessao: Session = Depends(db.obter_sessao),
):
    db.fechar_sessao(sessao, request.cookies.get(seguranca.NOME_COOKIE))
    resposta.delete_cookie(seguranca.NOME_COOKIE, path="/")
    return {"sucesso": True}


def _exigir_admin(request: Request, sessao: Session) -> db.Usuario:
    """Só quem administra emite e revoga tickets."""
    usuario = sessao.get(db.Usuario, getattr(request.state, "usuario_id", 0))
    if usuario is None or not usuario.administrador:
        raise HTTPException(
            403, "Apenas quem administra o sistema pode gerenciar os tickets."
        )
    return usuario


@app.get("/api/tickets", tags=["acesso"], summary="Lista os tickets emitidos")
def listar_tickets(request: Request, sessao: Session = Depends(db.obter_sessao)):
    _exigir_admin(request, sessao)
    return [
        {
            "id": t.id,
            "pista": t.pista,
            "descricao": t.descricao,
            "situacao": t.situacao(),
            "criado_em": t.criado_em,
            "expira_em": t.expira_em,
            "ultimo_uso": t.ultimo_uso,
            "usuario": t.usuario.nome if t.usuario else None,
        }
        for t in db.listar_tickets(sessao)
    ]


@app.post("/api/tickets", tags=["acesso"], summary="Emite um ticket de acesso")
def emitir_ticket(
    request: Request,
    descricao: str = Form("", description="Para quem e o ticket."),
    dias_validade: Optional[int] = Form(90),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Emite o ticket e devolve o codigo.

    O codigo aparece UMA vez. O banco guarda so o hash, entao nem quem
    administra consegue recupera-lo depois — se perder, revogue e emita
    outro.
    """
    admin = _exigir_admin(request, sessao)
    ticket, codigo = db.criar_ticket(
        sessao,
        descricao=descricao,
        criado_por_id=admin.id,
        dias_validade=dias_validade if dias_validade and dias_validade > 0 else None,
    )
    return {
        "sucesso": True,
        "id": ticket.id,
        "codigo": codigo,
        "pista": ticket.pista,
        "expira_em": ticket.expira_em,
        "aviso": "Anote agora: este codigo nao aparece de novo.",
    }


@app.post("/api/tickets/{ticket_id}/revogar", tags=["acesso"], summary="Revoga um ticket")
def revogar_ticket(
    ticket_id: int, request: Request, sessao: Session = Depends(db.obter_sessao)
):
    _exigir_admin(request, sessao)
    try:
        ticket = db.revogar_ticket(sessao, ticket_id)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    return {"sucesso": True, "pista": ticket.pista, "situacao": ticket.situacao()}


@app.post(
    "/api/admin/limpar-dados",
    tags=["acesso"],
    summary="Apaga escolas, turmas, alunos, provas e notas",
)
def limpar_dados(
    request: Request,
    confirmacao: str = Form(
        ..., description="Precisa ser exatamente APAGAR TUDO."
    ),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Zera os dados pedagógicos. Só administrador.

    Existe para preparar uma demonstração sem os testes anteriores no
    meio. NÃO apaga usuários nem tickets: quem pediu a limpeza
    continuaria logado, e zerar as contas junto tiraria a própria
    pessoa de dentro do sistema.

    A confirmação por texto é proposital. Um botão que apaga o ano
    letivo inteiro não pode disparar num clique errado, e digitar a
    frase obriga a pessoa a parar um segundo.
    """
    usuario_admin = _exigir_admin(request, sessao)

    if confirmacao.strip().upper() != "APAGAR TUDO":
        raise HTTPException(
            400, 'Para confirmar, digite exatamente: APAGAR TUDO'
        )

    contagem = db.limpar_dados_pedagogicos(sessao)
    db.auditoria.registrar(
        sessao, db.auditoria.LIMPAR_DADOS, usuario_admin, str(contagem), _ip(request)
    )
    logger.warning("Dados pedagogicos apagados pelo administrador: %s", contagem)
    return {"sucesso": True, "apagados": contagem}


@app.get(
    "/api/admin/backup",
    tags=["acesso"],
    summary="Baixa um backup dos dados pedagógicos",
)
def backup(request: Request, sessao: Session = Depends(db.obter_sessao)) -> Response:
    """
    Backup em JSON, só administrador.

    Copiar o arquivo do banco só funciona no SQLite, e em
    produção o banco é Postgres — um backup que só serve em
    desenvolvimento não é backup. O JSON abre em qualquer lugar e
    sobrevive a troca de banco.

    Senhas e tickets ficam de fora: arquivo de backup circula por
    e-mail e pen drive, e credencial não pode viajar assim.
    """
    _exigir_admin(request, sessao)

    conteudo = json.dumps(
        db.exportar_dados(sessao), ensure_ascii=False, indent=2
    ).encode("utf-8")
    nome = f"backup-avaliafrg-{db.agora():%Y-%m-%d}.json"

    return Response(
        content=conteudo,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/api/papeis", tags=["acesso"], summary="Papéis disponíveis")
def papeis():
    """Alimenta o seletor da tela de usuários."""
    return [
        {
            "codigo": papel,
            "nome": permissoes.NOMES[papel],
            "descricao": permissoes.DESCRICOES[papel],
        }
        for papel in permissoes.PAPEIS
    ]


@app.get("/api/usuarios", tags=["acesso"], response_model=List[UsuarioResposta])
def listar_contas(
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _checar(permissoes.exigir_admin, usuario)
    return [
        UsuarioResposta(
            id=u.id,
            nome=u.nome,
            email=u.email,
            papel=u.papel,
            papel_nome=permissoes.NOMES.get(u.papel, u.papel),
            escola_id=u.escola_id,
            escola=u.escola.nome if u.escola else None,
            criado_em=u.criado_em,
            ultimo_acesso=u.ultimo_acesso,
        )
        for u in db.listar_usuarios(sessao)
    ]


@app.post("/api/usuarios", tags=["acesso"], summary="Cria uma conta")
def novo_usuario(
    request: Request,
    email: str = Form(...),
    nome: str = Form(...),
    senha: str = Form(...),
    papel: str = Form("membro", description="admin, membro ou convidado"),
    escola_id: Optional[int] = Form(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """
    Só o administrador cria contas.

    A conta nasce SEM ticket: quem administra emite um em seguida e
    entrega à pessoa. Sem isso ela tem senha mas não entra — dois
    passos de propósito, para que uma senha vazada não baste.
    """
    _checar(permissoes.exigir_admin, usuario)

    try:
        criado = db.criar_usuario(sessao, email, nome, senha, papel, escola_id)
    except (ValueError, seguranca.SenhaFraca) as exc:
        raise HTTPException(400, str(exc))

    db.auditoria.registrar(
        sessao,
        db.auditoria.CRIAR_USUARIO,
        usuario,
        f"{criado.nome} <{criado.email}> como {permissoes.NOMES[criado.papel]}",
        _ip(request),
    )
    return {
        "sucesso": True,
        "id": criado.id,
        "nome": criado.nome,
        "papel": criado.papel,
    }


@app.delete("/api/usuarios/{usuario_id}", tags=["acesso"], summary="Remove uma conta")
def remover_conta(
    request: Request,
    usuario_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _checar(permissoes.exigir_admin, usuario)

    if usuario_id == usuario.id:
        raise HTTPException(400, "Você não pode remover a própria conta.")

    alvo = db.obter_usuario(sessao, usuario_id)
    if alvo.papel == "admin" and db.contar_admins(sessao) <= 1:
        # Sem essa trava, o sistema poderia ficar sem nenhum
        # administrador e ninguém mais conseguiria criar contas.
        raise HTTPException(
            400, "Este é o único administrador. Crie outro antes de removê-lo."
        )

    nome = db.remover_usuario(sessao, usuario_id)
    db.auditoria.registrar(
        sessao, db.auditoria.REMOVER_USUARIO, usuario, nome, _ip(request)
    )
    return {"sucesso": True}


@app.get("/api/auditoria", tags=["acesso"], summary="Histórico de ações")
def historico_de_acoes(
    limite: int = 200,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """
    Quem fez o quê, e quando.

    A LGPD trata prestação de contas como obrigação do controlador —
    e num sistema com dados de criança, "a nota mudou" sem "quem mudou"
    não responde nada.
    """
    _checar(permissoes.exigir_admin, usuario)
    return [
        {
            "id": r.id,
            "usuario": r.usuario_nome or "—",
            "papel": permissoes.NOMES.get(r.papel, r.papel),
            "acao": r.acao,
            "detalhe": r.detalhe,
            "ip": r.ip,
            "quando": r.criado_em,
        }
        for r in db.auditoria.listar(sessao, limite)
    ]


# ==================================================================
# HELPERS
# ==================================================================
def identificar_arquivo(conteudo: bytes) -> Optional[str]:
    """
    Descobre o tipo real do arquivo pelos primeiros bytes.

    Returns:
        "pdf", "imagem" ou None se nao for nenhum dos dois.
    """
    # A especificacao do PDF permite lixo antes do cabecalho, entao
    # procuramos a assinatura no inicio do arquivo em vez de exigir que
    # ela esteja no byte zero.
    if conteudo[:1024].find(b"%PDF-") != -1:
        return "pdf"

    if conteudo.startswith(ASSINATURAS_IMAGEM):
        return "imagem"
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return "imagem"

    return None


async def _ler_upload(file: UploadFile, aceitar_pdf: bool = False) -> bytes:
    """Valida o conteudo e o tamanho, devolvendo os bytes do arquivo."""
    conteudo = await file.read()

    if not conteudo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O arquivo enviado esta vazio.",
        )
    if len(conteudo) > TAMANHO_MAXIMO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo maior que o limite de {TAMANHO_MAXIMO_MB} MB.",
        )

    tipo = identificar_arquivo(conteudo)
    aceitos = {"imagem", "pdf"} if aceitar_pdf else {"imagem"}

    if tipo not in aceitos:
        nome = f" ({file.filename})" if file.filename else ""
        if aceitar_pdf:
            detalhe = (
                f"Nao reconheci o arquivo{nome} como PDF nem como imagem. "
                f"Arquivos do Word precisam ser exportados como PDF antes."
            )
        else:
            detalhe = (
                f"O arquivo{nome} nao e uma imagem. Envie a foto do "
                f"cartao-resposta em JPG ou PNG."
            )
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=detalhe
        )

    return conteudo


def _nome_arquivo_seguro(texto: str, padrao: str = "arquivo") -> str:
    """
    Reduz o texto a ASCII para usar em Content-Disposition.

    Cabecalhos HTTP nao carregam UTF-8: um nome de turma como
    "8º ano B" derruba a resposta inteira com UnicodeDecodeError antes
    mesmo de o PDF chegar ao navegador.
    """
    normalizado = unicodedata.normalize("NFKD", texto)
    limpo = "".join(
        c if (c.isalnum() or c in "-_") else "-"
        for c in normalizado.encode("ascii", "ignore").decode()
    )
    limpo = "-".join(parte for parte in limpo.split("-") if parte)
    return limpo[:60] or padrao


def _cartoes_do_professor(prova, alunos, segredo: str):
    """
    Estampa o molde do professor uma vez por aluno.

    O molde ja foi guardado no banco quando ele enviou o cartao. Aqui
    so muda o QR e o nome impresso — o layout, o cabecalho e as bolhas
    sao exatamente os do arquivo original.
    """
    from PIL import Image as PILImage

    if not prova.modelo_cartao:
        raise HTTPException(
            400,
            "O modelo do cartao-resposta nao foi encontrado. Envie o arquivo de "
            "novo na aba Provas.",
        )

    pagina = PILImage.open(io.BytesIO(prova.modelo_cartao))
    cartoes = []

    for aluno in alunos:
        texto_qr = montar_qr_code(f"P{prova.id}", None, aluno.id, segredo)
        resultado = estampa.estampar(
            pagina, texto_qr, rotulo=f"Prova P{prova.id}", aluno_nome=aluno.nome
        )
        cartoes.append(resultado.imagem)

    return cartoes


def _interpretar_conteudos(bruto: str, total: int) -> Dict[int, str]:
    """
    Aceita as duas formas que o professor naturalmente usaria.

        "Fracoes\nFracoes\nGeometria"     -> uma linha por questao
        "1=Fracoes;3=Geometria"           -> so as que ele quis marcar

    A segunda existe porque em prova longa ninguem quer preencher 140
    linhas para classificar seis questoes.
    """
    texto = (bruto or "").strip()
    if not texto:
        return {}

    if "=" in texto:
        conteudos: Dict[int, str] = {}
        for parte in texto.replace("\n", ";").split(";"):
            if "=" not in parte:
                continue
            numero, _, conteudo = parte.partition("=")
            numero = "".join(c for c in numero if c.isdigit())
            if numero and conteudo.strip():
                conteudos[int(numero)] = conteudo.strip()
        return conteudos

    linhas = [linha.strip() for linha in texto.splitlines()]
    return {
        indice: linha
        for indice, linha in enumerate(linhas, start=1)
        if linha and indice <= total
    }


async def _ler_upload_bruto(file: UploadFile) -> bytes:
    """
    Lê o arquivo sem exigir que seja imagem.

    O lote pode ser PDF ou ZIP; quem valida o tipo de verdade e explica
    o que fazer quando não reconhece e o modulo `lote`.
    """
    conteudo = await file.read()
    if not conteudo:
        raise HTTPException(400, "O arquivo enviado esta vazio.")
    if len(conteudo) > TAMANHO_MAXIMO_LOTE_BYTES:
        raise HTTPException(
            413,
            f"Arquivo maior que o limite de {TAMANHO_MAXIMO_LOTE_MB} MB. "
            f"Divida o PDF em partes menores.",
        )
    return conteudo


def _erro_404(exc: db.RegistroNaoEncontrado) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _resolver_gabarito(sessao: Optional[Session], leitura: LeituraQR) -> Dict[int, str]:
    """
    Descobre as respostas certas a partir do que o QR trouxe.

    Cartao de turma nao carrega o gabarito impresso — o QR so aponta
    para a prova, e as respostas vem do banco. Isso e o que impede o
    aluno de ler o gabarito apontando o celular para o proprio cartao.
    """
    if leitura.gabarito is not None:
        return leitura.gabarito

    if sessao is None:
        raise OMRError(
            "Este cartao guarda o gabarito no sistema, e a consulta ao banco "
            "nao esta disponivel."
        )

    try:
        prova = db.obter_prova(sessao, int(str(leitura.prova_id).lstrip("Pp")))
    except (db.RegistroNaoEncontrado, ValueError):
        raise OMRError(
            f"A prova {leitura.prova_id} nao existe mais no sistema. Se ela foi "
            f"apagada, o cartao nao pode mais ser corrigido."
        )

    return prova.gabarito_dict


def _motor_para(sessao: Optional[Session], leitura: LeituraQR, gabarito: dict):
    """
    Escolhe onde o motor vai procurar as bolhas.

    Cartao gerado por nos: geometria calculada. Cartao do professor:
    layout detectado na folha dele e guardado com a prova. Sem essa
    bifurcacao, uma folha de terceiro seria lida nas coordenadas
    erradas e produziria notas sem sentido — em silencio.
    """
    if sessao is not None:
        try:
            prova = db.obter_prova(sessao, int(str(leitura.prova_id).lstrip("Pp")))
            if prova.usa_cartao_proprio:
                layout = deteccao_grade.LayoutDetectado.de_json(prova.layout)
                return engine_com_layout(layout.rois)
        except (db.RegistroNaoEncontrado, ValueError):
            pass

    # `obter_engine` valida a geometria: se a prova for grande demais
    # para caber numa folha legivel, levanta OMRError com a orientacao.
    return obter_engine(max(gabarito))


def _corrigir_bytes(
    imagem_bytes: bytes,
    qr_code_str: Optional[str],
    sessao: Optional[Session] = None,
    usuario=None,
) -> dict:
    """
    Corrige uma folha e, quando possivel, grava a nota no banco.

    A gravacao so acontece se o QR trouxer o id do aluno (cartao
    nominal) e a prova existir. Um cartao generico continua sendo
    corrigido normalmente, apenas sem persistencia — e o campo
    `motivo_nao_salvo` explica isso ao professor em vez de falhar em
    silencio.
    """
    origem = "informado"

    if not qr_code_str:
        # Precisamos do QR antes de escolher o motor: usa um motor
        # qualquer so para decodificar a imagem e ler o codigo.
        imagem = obter_engine(10)._decodificar_imagem(imagem_bytes)
        qr_lido = ler_qr_da_imagem(imagem)
        if not qr_lido:
            raise OMRError(
                "Nao foi possivel ler o QR Code da foto. Enquadre o codigo "
                "por inteiro, com boa luz, ou informe o gabarito manualmente."
            )
        qr_code_str = qr_lido
        origem = "imagem"

    segredo = db.obter_segredo_qr(sessao) if sessao is not None else None
    leitura = parse_qr_code(qr_code_str, segredo)

    if leitura.assinatura == "invalida":
        # Assinatura errada significa cartao adulterado ou impresso por
        # outra instalacao. Corrigir assim mesmo seria aceitar uma nota
        # que o aluno pode ter fabricado.
        raise OMRError(
            "A assinatura deste QR Code nao confere. O cartao pode ter sido "
            "alterado ou foi impresso por outro sistema."
        )

    gabarito = _resolver_gabarito(sessao, leitura)
    motor = _motor_para(sessao, leitura, gabarito)
    resultado = motor.processar(imagem_bytes, gabarito)
    resultado["prova_id"] = leitura.prova_id
    resultado["aluno_id"] = leitura.aluno_id
    resultado["origem_gabarito"] = origem
    resultado["assinatura"] = leitura.assinatura
    resultado["salvo"] = False
    resultado["aluno_nome"] = None
    resultado["motivo_nao_salvo"] = None

    # Convidado usa o sistema sem deixar rastro: a nota aparece na tela
    # e vai para o relatório que ele baixa, e nada é gravado.
    if usuario is not None and not permissoes.usa_banco(usuario):
        resultado["motivo_nao_salvo"] = (
            "Sua conta é de uso individual: a nota não fica guardada no "
            "sistema. Baixe o relatório ao terminar a turma."
        )
        return resultado

    if sessao is None or leitura.aluno_id is None:
        if leitura.aluno_id is None:
            resultado["motivo_nao_salvo"] = (
                "Cartao sem identificacao de aluno. Gere os cartoes a partir "
                "de uma turma para que as notas sejam guardadas."
            )
        return resultado

    if EXIGIR_ASSINATURA and leitura.assinatura != "valida":
        # Um cartao que aponta para um aluno mexe no boletim. Aceitar um
        # sem assinatura deixaria a fraude trivial: bastaria imprimir um
        # QR proprio com o gabarito desejado. A correcao continua sendo
        # mostrada na tela — o professor ve a nota — mas ela nao entra
        # no registro.
        resultado["motivo_nao_salvo"] = (
            "Este cartao nao tem assinatura valida e por isso a nota nao foi "
            "guardada. Cartoes impressos antes desta versao precisam ser "
            "gerados de novo na aba Provas."
        )
        return resultado

    try:
        aluno = db.obter_aluno(sessao, leitura.aluno_id)

        # Uma escola não pode gravar nota de aluno de outra, nem por
        # engano de folha misturada na pilha.
        if usuario is not None and not permissoes.pode_ver_escola(
            usuario, aluno.turma.escola_id
        ):
            resultado["motivo_nao_salvo"] = (
                f"Este cartão é de {aluno.turma.escola.nome}, e sua conta "
                f"atende outra escola. A nota não foi guardada."
            )
            return resultado

        prova_id = int(str(leitura.prova_id).lstrip("Pp"))
        db.salvar_resultado(sessao, prova_id, leitura.aluno_id, resultado)
        resultado["aluno_nome"] = aluno.nome
        resultado["salvo"] = True
    except (db.RegistroNaoEncontrado, ValueError) as exc:
        logger.warning("Resultado nao gravado: %s", exc)
        resultado["motivo_nao_salvo"] = str(exc)

    return resultado


# ==================================================================
# INTERFACE WEB
# ==================================================================
# Locais onde a interface pode estar. O segundo existe porque baixar os
# arquivos um a um faz todos cairem soltos na mesma pasta, sem a subpasta
# "static" — e ai o servidor subia sem pagina nenhuma.
CAMINHOS_INTERFACE = (
    os.path.join(DIRETORIO_ESTATICO, "index.html"),
    os.path.join(DIRETORIO_BASE, "index.html"),
)


def _localizar_interface() -> Optional[str]:
    for caminho in CAMINHOS_INTERFACE:
        if os.path.exists(caminho):
            return caminho
    return None


@app.get("/", include_in_schema=False)
def pagina_inicial() -> Response:
    """Serve a interface usada pelos professores."""
    caminho = _localizar_interface()

    if caminho is None:
        # Devolver JSON aqui so confundia: o professor via um bloco de
        # texto tecnico sem entender que faltava um arquivo. Melhor
        # dizer exatamente o que fazer, na propria pagina.
        return HTMLResponse(
            status_code=500,
            content=f"""<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="utf-8"><title>Falta um arquivo</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#eaeef3;color:#16202e;
      margin:0;padding:48px 20px;line-height:1.6}}
 main{{max-width:620px;margin:0 auto;background:#fff;border:1px solid #d3dbe4;
      border-radius:10px;padding:32px}}
 h1{{margin:0 0 8px;font-size:22px}}
 code{{background:#eaeef3;padding:2px 6px;border-radius:4px;font-size:14px}}
 pre{{background:#16202e;color:#fff;padding:14px 16px;border-radius:8px;
     overflow-x:auto;font-size:13.5px}}
 p{{margin:12px 0}}
</style></head><body><main>
<h1>Falta o arquivo da interface</h1>
<p>O servidor subiu, mas não encontrou o <code>index.html</code>.
   Ele precisa estar em <code>static/index.html</code>, dentro desta pasta:</p>
<pre>{DIRETORIO_BASE}</pre>
<p>Se o <code>index.html</code> estiver solto na pasta, crie a subpasta
   <code>static</code> e mova o arquivo para dentro dela. Depois pare o
   servidor (Ctrl+C) e rode <code>python main.py</code> de novo.</p>
<p>A API em si está funcionando: <a href="/docs">/docs</a></p>
</main></body></html>""",
        )

    if caminho != CAMINHOS_INTERFACE[0]:
        logger.warning(
            "index.html encontrado na raiz do projeto. O lugar correto e "
            "static/index.html — mova o arquivo para evitar confusao."
        )

    return FileResponse(caminho, media_type="text/html")


@app.get("/api/health", tags=["infra"], summary="Health check")
def health() -> dict:
    return {
        "status": "ok",
        "versao": app.version,
        "schema": _resumo_schema.get("versao"),
        # A interface le este valor para saber ate quantas questoes
        # oferecer, em vez de ter o numero fixo no HTML.
        "maximo_questoes": CONFIG_PADRAO.maximo_questoes,
        "banco": db.DATABASE_URL.split("://")[0],
    }


# ==================================================================
# CADASTRO
# ==================================================================
@app.get("/api/escolas", tags=["cadastro"], response_model=List[EscolaResposta])
def get_escolas(
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """Admin vê todas; membro vê só a dele; convidado não chega aqui."""
    _checar(permissoes.exigir_banco, usuario)
    escolas = db.listar_escolas(sessao)

    if permissoes.e_admin(usuario):
        return escolas
    return [e for e in escolas if e.id == usuario.escola_id]


@app.post("/api/escolas", tags=["cadastro"], response_model=EscolaResposta)
def post_escola(
    nome: str = Form(...),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _checar(permissoes.exigir_edicao, usuario)
    if not nome.strip():
        raise HTTPException(400, "Informe o nome da escola.")
    return db.criar_escola(sessao, nome)


@app.get("/api/turmas", tags=["cadastro"], response_model=List[TurmaResposta])
def get_turmas(
    escola_id: Optional[int] = None,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """Admin vê todas; membro só a da própria escola; convidado nenhuma."""
    # Sem esta linha o convidado recebia 200 com lista vazia. Devolver
    # "vazio" em vez de "proibido" é pior: mascara a falta da regra e,
    # no dia em que o filtro falhar, o vazamento passa despercebido.
    _checar(permissoes.exigir_banco, usuario)
    return [
        TurmaResposta(
            id=turma.id,
            escola_id=turma.escola_id,
            nome=turma.nome,
            ano_escolar=turma.ano_escolar,
            ano_escolar_nome=escolaridade.nome_do(turma.ano_escolar),
            ano_letivo=turma.ano_letivo,
            total_alunos=len(turma.alunos),
        )
        for turma in db.listar_turmas(sessao, permissoes.filtrar_escola(usuario, escola_id))
    ]


@app.post("/api/turmas", tags=["cadastro"], response_model=TurmaResposta)
def post_turma(
    escola_id: int = Form(...),
    nome: str = Form(...),
    ano_escolar: Optional[str] = Form(
        None, description="Codigo do ano escolar, ex.: EF2. Aceita '2' ou '2º ano'."
    ),
    ano_letivo: Optional[int] = Form(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _checar(permissoes.exigir_edicao, usuario)
    if not nome.strip():
        raise HTTPException(400, "Informe o nome da turma.")
    try:
        turma = db.criar_turma(sessao, escola_id, nome, ano_escolar, ano_letivo)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    return TurmaResposta(
        id=turma.id,
        escola_id=turma.escola_id,
        nome=turma.nome,
        ano_escolar=turma.ano_escolar,
        ano_escolar_nome=escolaridade.nome_do(turma.ano_escolar),
        ano_letivo=turma.ano_letivo,
        total_alunos=0,
    )


@app.get(
    "/api/turmas/{turma_id}/alunos", tags=["cadastro"], response_model=List[AlunoResposta]
)
def get_alunos(
    turma_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _turma_permitida(sessao, usuario, turma_id)
    return db.listar_alunos(sessao, turma_id)


@app.post(
    "/api/turmas/{turma_id}/alunos",
    tags=["cadastro"],
    response_model=List[AlunoResposta],
    summary="Adiciona alunos (um nome por linha)",
)
def post_alunos(
    turma_id: int,
    nomes: str = Form(..., description="Lista de nomes, um por linha."),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """
    Aceita a lista colada de uma planilha. Nomes repetidos na turma sao
    ignorados, para o professor poder colar de novo sem duplicar.
    """
    _checar(permissoes.exigir_edicao, usuario)
    try:
        criados = db.adicionar_alunos(sessao, turma_id, nomes.splitlines())
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    return criados


@app.delete("/api/alunos/{aluno_id}", tags=["cadastro"])
def delete_aluno(
    request: Request,
    aluno_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _checar(permissoes.exigir_edicao, usuario)
    try:
        nome = db.obter_aluno(sessao, aluno_id).nome
        db.remover_aluno(sessao, aluno_id)
        db.auditoria.registrar(
            sessao, db.auditoria.APAGAR_ALUNO, usuario, nome, _ip(request)
        )
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    return {"sucesso": True}


# ==================================================================
# PROVAS
# ==================================================================
@app.get("/api/provas", tags=["provas"], response_model=List[ProvaResposta])
def get_provas(
    turma_id: Optional[int] = None,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """Admin vê todas; membro só as da própria escola; convidado nenhuma."""
    _checar(permissoes.exigir_banco, usuario)
    return [
        ProvaResposta(
            id=p.id,
            turma_id=p.turma_id,
            titulo=p.titulo,
            disciplina=p.disciplina,
            total_questoes=p.total_questoes,
            criada_em=p.criada_em,
        )
        for p in db.listar_provas(sessao, turma_id)
        if permissoes.pode_ver_escola(usuario, p.turma.escola_id)
    ]


@app.post("/api/provas", tags=["provas"], response_model=ProvaResposta)
def post_prova(
    turma_id: int = Form(...),
    titulo: str = Form(...),
    gabarito: str = Form(..., description="Ex.: 'ABCDE' ou '1A,2B,3C'."),
    disciplina: str = Form(""),
    conteudos: str = Form(
        "",
        description=(
            "Conteudo de cada questao, um por linha na ordem, ou "
            "'1=Fracoes;2=Geometria'. Opcional, mas e o que permite a analise "
            "dizer 'fracoes' em vez de 'questao 15'."
        ),
    ),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """Cria a prova, o gabarito e a classificacao de conteudo de cada questao."""
    _checar(permissoes.exigir_edicao, usuario)
    try:
        respostas = parse_gabarito_digitado(gabarito)
        CONFIG_PADRAO.com_questoes(max(respostas))   # valida o tamanho
        prova = db.criar_prova(
            sessao,
            turma_id,
            titulo,
            respostas,
            disciplina,
            conteudos=_interpretar_conteudos(conteudos, max(respostas)),
        )
    except OMRError as exc:
        raise HTTPException(400, str(exc))
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)

    return ProvaResposta(
        id=prova.id,
        turma_id=prova.turma_id,
        titulo=prova.titulo,
        disciplina=prova.disciplina,
        total_questoes=prova.total_questoes,
        criada_em=prova.criada_em,
    )


@app.post(
    "/api/provas/{prova_id}/cartoes",
    tags=["provas"],
    summary="PDF com um cartao nominal por aluno da turma",
)
async def cartoes_da_turma(
    prova_id: int,
    incluir_prova: bool = Form(False),
    file: Optional[UploadFile] = File(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> Response:
    """
    Gera a folha de cada aluno com o nome ja impresso e um QR proprio.

    E este passo que torna possivel guardar a nota no nome certo: o QR
    carrega o id do aluno, que a maquina le com confianca — coisa que
    ela nao consegue fazer com o nome escrito a mao.
    """
    prova = _prova_permitida(sessao, usuario, prova_id)

    turma = prova.turma
    alunos = db.listar_alunos(sessao, turma.id)
    if not alunos:
        raise HTTPException(
            400, f"A turma {turma.nome} ainda nao tem alunos cadastrados."
        )

    gabarito = prova.gabarito_dict
    segredo = db.obter_segredo_qr(sessao)

    if prova.usa_cartao_proprio:
        # O professor trouxe o cartao dele: estampamos o molde guardado
        # uma vez por aluno, trocando so o QR e o nome. O desenho
        # original nao e recriado nem alterado.
        cartoes = _cartoes_do_professor(prova, alunos, segredo)
    else:
        cartoes = [
            gerar_cartao(
                DadosCartao(
                    prova_id=f"P{prova.id}",
                    gabarito=gabarito,
                    gabarito_no_qr=False,
                    segredo=segredo,
                    titulo=prova.titulo,
                    disciplina=prova.disciplina,
                    turma=turma.nome,
                    escola=turma.escola.nome,
                    aluno_id=aluno.id,
                    aluno_nome=aluno.nome,
                )
            )
            for aluno in alunos
        ]

    prova_bytes: Optional[bytes] = None
    prova_nome = ""
    if incluir_prova and file is not None and file.filename:
        prova_bytes = await _ler_upload(file, aceitar_pdf=True)
        prova_nome = file.filename

    try:
        # A prova entra UMA vez, no inicio: o professor imprime a prova
        # e depois a pilha de cartoes nominais.
        pdf_cartoes = (
            estampa.paginas_para_pdf(cartoes)
            if prova.usa_cartao_proprio
            else montar_pdf_varios_cartoes(cartoes)
        )
        pdf = anexar_prova(pdf_cartoes, prova_bytes, prova_nome)
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    nome_arquivo = f"cartoes-{_nome_arquivo_seguro(turma.nome, 'turma')}-P{prova.id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nome_arquivo}"'},
    )


@app.get(
    "/api/provas/{prova_id}/boletim",
    tags=["provas"],
    response_model=Boletim,
    summary="Notas da turma e desempenho por questao",
)
def boletim(
    prova_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> Boletim:
    prova = _prova_permitida(sessao, usuario, prova_id)

    turma = prova.turma
    alunos = db.listar_alunos(sessao, turma.id)
    por_aluno = {r.aluno_id: r for r in db.listar_resultados(sessao, prova_id)}
    estatisticas = db.estatisticas_da_prova(sessao, prova_id)

    linhas: List[LinhaBoletim] = []
    for aluno in alunos:
        resultado = por_aluno.get(aluno.id)
        if resultado is None:
            # Aluno sem folha corrigida continua na lista: o professor
            # precisa ver quem falta, nao so quem ja tem nota.
            linhas.append(
                LinhaBoletim(aluno_id=aluno.id, aluno_nome=aluno.nome, corrigido=False)
            )
        else:
            linhas.append(
                LinhaBoletim(
                    aluno_id=aluno.id,
                    aluno_nome=aluno.nome,
                    nota=resultado.nota,
                    acertos=resultado.acertos,
                    erros=resultado.erros,
                    em_branco=resultado.em_branco,
                    rasuras=resultado.rasuras,
                    corrigido_em=resultado.corrigido_em,
                    corrigido=True,
                    detalhamento=resultado.detalhamento or [],
                )
            )

    return Boletim(
        prova=ProvaResposta(
            id=prova.id,
            turma_id=prova.turma_id,
            titulo=prova.titulo,
            disciplina=prova.disciplina,
            total_questoes=prova.total_questoes,
            criada_em=prova.criada_em,
        ),
        turma=turma.nome,
        escola=turma.escola.nome,
        total_alunos=len(alunos),
        total_corrigidos=estatisticas["total_corrigidos"],
        media=estatisticas["media"],
        maior_nota=estatisticas["maior_nota"],
        menor_nota=estatisticas["menor_nota"],
        acerto_por_questao=estatisticas["acerto_por_questao"],
        linhas=linhas,
    )


# ==================================================================
# CARTAO-RESPOSTA DO PROFESSOR
# ==================================================================
@app.post(
    "/api/provas/{prova_id}/cartao-proprio",
    tags=["cartao"],
    summary="Usa o cartao-resposta do professor em vez do nosso",
)
async def usar_cartao_proprio(
    prova_id: int,
    file: UploadFile = File(..., description="Cartao-resposta em branco, PDF ou imagem."),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Recebe o cartao do professor, estampa as marcas e aprende o layout.

    Adicionar so o QR Code nao bastaria: o motor precisa das 4 ancoras
    para endireitar a foto, e precisa saber ONDE estao as bolhas — o
    cartao de cada escola tem espacamento e tamanho proprios. Por isso
    esta rota faz tres coisas:

        1. estampa as ancoras e o QR nas MARGENS, sem tocar no desenho
        2. detecta a grade de bolhas na folha ja retificada
        3. guarda o layout e o molde, para reimprimir e corrigir depois

    O desenho do professor nao e alterado: se as margens nao tiverem
    espaco, a folha ganha uma borda branca em vez de receber carimbo
    por cima do conteudo.
    """
    try:
        prova = db.obter_prova(sessao, prova_id)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)

    conteudo = await _ler_upload(file, aceitar_pdf=True)

    try:
        paginas = estampa.carregar_paginas(conteudo, file.filename or "")
        if len(paginas) > 1:
            logger.info(
                "Cartao com %d paginas; usando a primeira como modelo.", len(paginas)
            )

        # Estampa sem QR de aluno: este e o MOLDE. O QR de cada aluno
        # e aplicado na hora de gerar a turma.
        molde = estampa.estampar(paginas[0], f"P{prova.id}", rotulo="")
        folha = estampa.recortar_area_util(molde)

        layout = deteccao_grade.detectar_grade(
            folha,
            alternativas_esperadas=CONFIG_PADRAO.total_alternativas,
            questoes_esperadas=prova.total_questoes,
        )
    except deteccao_grade.GradeNaoDetectada as exc:
        raise HTTPException(400, str(exc))
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    db.definir_cartao_proprio(
        sessao,
        prova.id,
        layout.para_json(),
        estampa.imagem_para_png(paginas[0]),
    )

    return {
        "sucesso": True,
        "total_questoes": layout.total_questoes,
        "total_alternativas": layout.total_alternativas,
        "colunas": layout.colunas_de_questoes,
        "borda_adicionada": molde.borda_adicionada > 0,
        "avisos": molde.avisos + layout.avisos,
    }


@app.get(
    "/api/provas/{prova_id}/cartao-proprio/previa",
    tags=["cartao"],
    summary="PNG do cartao do professor com a grade detectada marcada",
)
def previa_do_cartao_proprio(
    prova_id: int, sessao: Session = Depends(db.obter_sessao)
) -> Response:
    """
    Mostra o que o sistema entendeu, antes de imprimir a turma.

    Deteccao automatica erra. Ver a grade marcada custa dez segundos;
    descobrir o erro depois custa trinta cartoes corrigidos errado.
    """
    try:
        prova = db.obter_prova(sessao, prova_id)
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)

    if not prova.usa_cartao_proprio or not prova.modelo_cartao:
        raise HTTPException(400, "Esta prova nao usa cartao-resposta proprio.")

    from PIL import Image as PILImage

    pagina = PILImage.open(io.BytesIO(prova.modelo_cartao))
    molde = estampa.estampar(pagina, f"P{prova.id}", rotulo="")
    folha = estampa.recortar_area_util(molde)
    layout = deteccao_grade.LayoutDetectado.de_json(prova.layout)

    previa = deteccao_grade.desenhar_previa(folha, layout)
    ok, buffer = cv2.imencode(".png", previa)
    if not ok:
        raise HTTPException(500, "Falha ao gerar a previa.")
    return Response(content=buffer.tobytes(), media_type="image/png")


# ==================================================================
# ANALISE PEDAGOGICA
# ==================================================================
@app.get(
    "/api/anos-escolares",
    tags=["analise"],
    response_model=List[AnoEscolarOpcao],
    summary="Lista de anos escolares para o seletor",
)
def anos_escolares():
    """
    Lista fechada, e nao texto livre.

    Comparar "o 2º ano" entre escolas exige que todas escrevam o ano da
    mesma forma. Com campo livre, a mesma serie viraria "2 ano",
    "2º Ano" e "segundo ano", e a comparacao — que e o objetivo — nao
    fecharia.
    """
    return escolaridade.listar()


@app.get(
    "/api/provas/{prova_id}/questoes",
    tags=["analise"],
    response_model=List[QuestaoResposta],
    summary="Questoes da prova com o conteudo que cobram",
)
def questoes_da_prova(
    prova_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    return _prova_permitida(sessao, usuario, prova_id).questoes


@app.put(
    "/api/provas/{prova_id}/questoes",
    tags=["analise"],
    summary="Classifica o conteudo das questoes",
)
def classificar_questoes(
    prova_id: int,
    conteudos: str = Form(...),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Preenche ou corrige o conteudo depois da prova criada.

    Existe porque o professor costuma so parar para classificar quando
    ja quer ver a analise — e ate ali a prova ja foi aplicada.
    """
    try:
        prova = db.obter_prova(sessao, prova_id)
        db.atualizar_conteudos(
            sessao, prova_id, _interpretar_conteudos(conteudos, prova.total_questoes)
        )
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)
    return {"sucesso": True, "conteudos": prova.conteudos}


@app.get(
    "/api/conteudos",
    tags=["analise"],
    summary="Conteudos ja cadastrados, para sugerir na digitacao",
)
def conteudos(
    escola_id: Optional[int] = None, sessao: Session = Depends(db.obter_sessao)
):
    """
    Sugerir o que ja existe evita "Fracoes", "fracao" e "FRAÇÕES"
    convivendo na mesma escola — cada variacao vira um conteudo
    diferente na analise e fragmenta a amostra.
    """
    return db.conteudos_usados(sessao, escola_id)


@app.get(
    "/api/escolas/{escola_id}/analise",
    tags=["analise"],
    response_model=PainelAnalise,
    summary="Diagnostico pedagogico da escola",
)
def analise_da_escola(
    escola_id: int,
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    dias: Optional[int] = None,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """
    Conteudos mais frageis, questoes mais erradas, comparativo entre
    anos escolares e as orientacoes prontas para a escola.
    """
    _checar(permissoes.exigir_acesso_a_escola, usuario, escola_id)
    if sessao.get(db.Escola, escola_id) is None:
        raise HTTPException(404, f"Escola {escola_id} nao encontrada.")

    desde = db.agora() - db.timedelta(days=dias) if dias and dias > 0 else None
    return PainelAnalise(
        **analitico.painel_da_escola(
            sessao,
            escola_id,
            ano_escolar=escolaridade.normalizar(ano_escolar) if ano_escolar else None,
            disciplina=disciplina or None,
            desde=desde,
        )
    )


@app.get(
    "/api/escolas/{escola_id}/analise.csv",
    tags=["analise"],
    summary="Analise por conteudo em planilha",
)
def analise_csv(
    escola_id: int,
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    """CSV para a coordenacao levar para reuniao ou anexar em relatorio."""
    _checar(permissoes.exigir_acesso_a_escola, usuario, escola_id)
    escola = sessao.get(db.Escola, escola_id)
    if escola is None:
        raise HTTPException(404, f"Escola {escola_id} nao encontrada.")

    analise = analitico.analise_por_conteudo(
        sessao,
        escola_id=escola_id,
        ano_escolar=escolaridade.normalizar(ano_escolar) if ano_escolar else None,
        disciplina=disciplina or None,
    )

    linhas = [
        "escola;ano_escolar;disciplina;conteudo;percentual_acerto;respostas;"
        "questoes;situacao;amostra_confiavel"
    ]
    for item in analise["conteudos"]:
        linhas.append(
            ";".join(
                str(campo)
                for campo in [
                    escola.nome,
                    escolaridade.nome_do(escolaridade.normalizar(ano_escolar))
                    if ano_escolar
                    else "todos",
                    disciplina or "todas",
                    item["conteudo"],
                    item["percentual_acerto"],
                    item["respostas"],
                    item["questoes"],
                    item["situacao"],
                    "sim" if item["confiavel"] else "nao",
                ]
            )
        )

    # BOM para o Excel abrir os acentos corretamente
    conteudo_csv = "\ufeff" + "\n".join(linhas)
    nome = f"analise-{_nome_arquivo_seguro(escola.nome, 'escola')}.csv"
    return Response(
        content=conteudo_csv.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ==================================================================
# COMPARACAO ENTRE ESCOLAS
# ==================================================================
@app.get(
    "/api/comparativo",
    tags=["analise"],
    response_model=ComparativoEscolas,
    summary="Compara escolas no mesmo recorte",
)
def comparar(
    escolas: str = Query(..., description="Ids separados por vírgula: 1,2,5"),
    ano_escolar: Optional[str] = None,
    disciplina: Optional[str] = None,
    dias: Optional[int] = None,
    usuario: db.Usuario = Depends(usuario_atual),
    sessao: Session = Depends(db.obter_sessao),
):
    """
    Compara duas ou mais escolas, com margem de erro em cada número.

    Comparar "a escola toda" mistura 1º e 9º ano e não diz nada; o
    recorte por ano escolar e disciplina é o que torna o resultado
    interpretável. Diferenças que não passam no teste estatístico são
    reportadas como empate, com todas as letras.
    """
    _checar(
        lambda u: permissoes.exigir(
            permissoes.pode_comparar_escolas(u),
            "A comparação entre escolas é exclusiva da secretaria.",
        ),
        usuario,
    )
    try:
        ids = [int(parte) for parte in escolas.split(",") if parte.strip()]
    except ValueError:
        raise HTTPException(400, "Lista de escolas inválida. Use ids: 1,2,5")

    if len(ids) < 2:
        raise HTTPException(400, "Selecione pelo menos duas escolas.")

    desde = db.agora() - db.timedelta(days=dias) if dias and dias > 0 else None
    return ComparativoEscolas(
        **comparativo.comparar_escolas(
            sessao,
            ids,
            ano_escolar=escolaridade.normalizar(ano_escolar) if ano_escolar else None,
            disciplina=disciplina or None,
            desde=desde,
        )
    )


# ==================================================================
# HISTORICO
# ==================================================================
@app.get(
    "/api/alunos/{aluno_id}/historico",
    tags=["historico"],
    response_model=HistoricoAluno,
    summary="Todas as notas do aluno no ano",
)
def historico_aluno(
    aluno_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    try:
        aluno = db.obter_aluno(sessao, aluno_id)
        _checar(permissoes.exigir_acesso_a_escola, usuario, aluno.turma.escola_id)
        return HistoricoAluno(**db.historico_do_aluno(sessao, aluno_id))
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)


@app.get(
    "/api/turmas/{turma_id}/desempenho",
    tags=["historico"],
    response_model=DesempenhoTurma,
    summary="Media de cada aluno e de cada prova da turma",
)
def desempenho_turma(
    turma_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
):
    _turma_permitida(sessao, usuario, turma_id)
    try:
        return DesempenhoTurma(**db.desempenho_da_turma(sessao, turma_id))
    except db.RegistroNaoEncontrado as exc:
        raise _erro_404(exc)


# ==================================================================
# CARTAO AVULSO (sem turma)
# ==================================================================
@app.post(
    "/api/cartao",
    tags=["cartao"],
    summary="Gera um cartao-resposta avulso, sem vinculo com turma",
)
async def gerar_cartao_avulso(
    gabarito: str = Form(...),
    prova_id: str = Form("1"),
    titulo: str = Form("Cartao-resposta"),
    disciplina: str = Form(""),
    turma: str = Form(""),
    formato: str = Form("pdf"),
    incluir_prova: bool = Form(False),
    file: Optional[UploadFile] = File(None),
) -> Response:
    """
    Cartao com linhas em branco para o aluno escrever o nome.

    Corrige normalmente, mas a nota NAO e guardada: sem o id do aluno
    no QR nao ha como saber de quem e a folha.
    """
    try:
        respostas = parse_gabarito_digitado(gabarito)
        dados = DadosCartao(
            prova_id=prova_id.strip() or "1",
            gabarito=respostas,
            titulo=titulo.strip() or "Cartao-resposta",
            disciplina=disciplina.strip(),
            turma=turma.strip(),
        )
        cartao = gerar_cartao(dados)
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    if formato.lower() == "png":
        return Response(content=cartao_para_png(cartao), media_type="image/png")

    prova_bytes: Optional[bytes] = None
    prova_nome = ""
    if incluir_prova and file is not None and file.filename:
        prova_bytes = await _ler_upload(file, aceitar_pdf=True)
        prova_nome = file.filename

    try:
        pdf = montar_pdf_completo(cartao, prova_bytes, prova_nome)
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="cartao-{_nome_arquivo_seguro(dados.prova_id, "1")}.pdf"'
            )
        },
    )


@app.post(
    "/api/provas/{prova_id}/previa",
    tags=["provas"],
    summary="PNG do cartao do primeiro aluno, para conferir antes de imprimir",
)
def previa_da_prova(
    prova_id: int,
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> Response:
    prova = _prova_permitida(sessao, usuario, prova_id)

    turma = prova.turma
    alunos = db.listar_alunos(sessao, turma.id)
    primeiro = alunos[0] if alunos else None

    cartao = gerar_cartao(
        DadosCartao(
            prova_id=f"P{prova.id}",
            gabarito=prova.gabarito_dict,
            titulo=prova.titulo,
            disciplina=prova.disciplina,
            turma=turma.nome,
            escola=turma.escola.nome,
            aluno_id=primeiro.id if primeiro else None,
            aluno_nome=primeiro.nome if primeiro else "",
            gabarito_no_qr=False,
            segredo=db.obter_segredo_qr(sessao),
        )
    )
    return Response(content=cartao_para_png(cartao), media_type="image/png")


# ==================================================================
# CORRECAO
# ==================================================================
@app.post(
    "/api/corrigir",
    tags=["correcao"],
    response_model=RespostaCorrecao,
    summary="Corrige um cartao-resposta",
)
async def corrigir(
    file: UploadFile = File(...),
    qr_code_str: Optional[str] = Form(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> RespostaCorrecao:
    """
    Le a foto, compara com o gabarito e devolve a nota.

    Se o cartao for nominal, a nota tambem e gravada no banco no nome
    do aluno correspondente.
    """
    imagem_bytes = await _ler_upload(file)

    try:
        resultado = _corrigir_bytes(imagem_bytes, qr_code_str or None, sessao, usuario)
    except OMRError as exc:
        logger.warning("Falha ao corrigir: %s", exc)
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 - blindagem do endpoint
        logger.exception("Erro inesperado no pipeline OMR")
        raise HTTPException(500, f"Erro interno ao processar a imagem: {exc}")

    resultado["arquivo"] = file.filename
    return RespostaCorrecao(**resultado)


@app.post(
    "/api/corrigir-lote",
    tags=["correcao"],
    response_model=RespostaLote,
    summary="Corrige varios cartoes de uma vez",
)
async def corrigir_lote(
    files: List[UploadFile] = File(...),
    qr_code_str: Optional[str] = Form(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> RespostaLote:
    """
    Corrige a turma inteira em uma chamada.

    Uma foto ruim nao derruba o lote: o item entra no resultado com a
    mensagem de erro e os demais seguem normalmente.
    """
    if len(files) > MAXIMO_ARQUIVOS_LOTE:
        raise HTTPException(
            400, f"Envie no maximo {MAXIMO_ARQUIVOS_LOTE} fotos por vez."
        )

    itens: List[ItemLote] = []
    notas: List[float] = []

    for file in files:
        nome = file.filename or "sem-nome"
        try:
            imagem_bytes = await _ler_upload(file)
            resultado = _corrigir_bytes(imagem_bytes, qr_code_str or None, sessao, usuario)
            resultado["arquivo"] = nome
            itens.append(
                ItemLote(
                    arquivo=nome, sucesso=True, resultado=RespostaCorrecao(**resultado)
                )
            )
            notas.append(resultado["nota"])
        except (OMRError, HTTPException) as exc:
            mensagem = exc.detail if isinstance(exc, HTTPException) else str(exc)
            itens.append(ItemLote(arquivo=nome, sucesso=False, erro=str(mensagem)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado no lote, arquivo %s", nome)
            itens.append(
                ItemLote(arquivo=nome, sucesso=False, erro=f"Erro interno: {exc}")
            )

    return RespostaLote(
        total_enviados=len(files),
        total_corrigidos=len(notas),
        total_falhas=len(files) - len(notas),
        media_da_turma=round(sum(notas) / len(notas), 2) if notas else None,
        itens=itens,
    )


@app.post(
    "/api/corrigir-arquivo",
    tags=["correcao"],
    response_model=RespostaLote,
    summary="Corrige um PDF ou ZIP com a turma inteira",
)
async def corrigir_arquivo(
    file: UploadFile = File(..., description="PDF digitalizado ou ZIP de fotos."),
    qr_code_str: Optional[str] = Form(None),
    sessao: Session = Depends(db.obter_sessao),
    usuario: db.Usuario = Depends(usuario_atual),
) -> RespostaLote:
    """
    Um arquivo só, a turma inteira dentro.

    É como o material chega na prática: o scanner do administrativo
    devolve um PDF de 40 páginas, ou alguém junta as fotos num ZIP.
    Pedir que o professor selecione 40 arquivos um a um é trabalho que
    a máquina faz melhor.

    Uma folha ilegível não derruba o lote: ela volta identificada pela
    origem ("página 7") para o professor achar o papel físico.
    """
    conteudo = await _ler_upload_bruto(file)

    try:
        folhas = lote.extrair_folhas(conteudo, file.filename or "")
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    itens: List[ItemLote] = []
    notas: List[float] = []

    for folha in folhas:
        try:
            resultado = _corrigir_bytes(folha.conteudo, qr_code_str or None, sessao, usuario)
            resultado["arquivo"] = folha.origem
            itens.append(
                ItemLote(
                    arquivo=folha.origem,
                    sucesso=True,
                    resultado=RespostaCorrecao(**resultado),
                )
            )
            notas.append(resultado["nota"])
        except OMRError as exc:
            itens.append(ItemLote(arquivo=folha.origem, sucesso=False, erro=str(exc)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro inesperado na folha %s", folha.origem)
            itens.append(
                ItemLote(
                    arquivo=folha.origem, sucesso=False, erro=f"Erro interno: {exc}"
                )
            )

    return RespostaLote(
        total_enviados=len(folhas),
        total_corrigidos=len(notas),
        total_falhas=len(folhas) - len(notas),
        media_da_turma=round(sum(notas) / len(notas), 2) if notas else None,
        itens=itens,
    )


@app.post(
    "/api/contar-folhas",
    tags=["correcao"],
    summary="Quantas folhas há no arquivo, sem corrigir",
)
async def contar_folhas_do_arquivo(file: UploadFile = File(...)) -> dict:
    """
    A tela pergunta isto antes de começar, para avisar quanto tempo vai
    levar em vez de deixar o professor olhando uma tela parada.
    """
    conteudo = await _ler_upload_bruto(file)
    total = lote.contar_folhas(conteudo)
    return {
        "folhas": total,
        "tipo": lote.identificar_lote(conteudo),
        # ~0,6 s por folha, medido em lote de 12 páginas.
        "segundos_estimados": round(total * 0.6),
    }


@app.post(
    "/api/relatorio",
    tags=["correcao"],
    summary="Monta o PDF ou CSV com as notas corrigidas na sessão",
)
def montar_relatorio(
    pedido: PedidoRelatorio,
    usuario: db.Usuario = Depends(usuario_atual),
) -> Response:
    """
    O documento que o convidado leva embora.

    Sem banco, o resultado sumiria ao fechar a aba. O navegador acumula
    as correções da sessão e manda de volta para virar um arquivo — as
    notas, a média e o acerto por questão, o mesmo que o boletim
    mostraria a quem tem banco.

    Recebe JSON, e não formulário: campo de multipart tem limite de
    tamanho, e a turma grande estourava esse limite silenciosamente.

    Aberto aos três papéis: admin e escola também podem querer o PDF de
    uma correção avulsa, que não foi gravada.
    """
    if not pedido.correcoes:
        raise HTTPException(400, "Nenhuma correção para montar o relatório.")

    linhas = [
        relatorio.LinhaRelatorio(
            identificacao=(item.aluno_nome or item.arquivo or "Sem nome"),
            nota=item.nota,
            acertos=item.acertos,
            total_questoes=item.total_questoes,
            erros=item.erros,
            em_branco=item.em_branco,
            rasuras=item.rasuras,
            detalhamento=item.detalhamento,
        )
        for item in pedido.correcoes[:MAXIMO_LINHAS_RELATORIO]
    ]

    dados = relatorio.DadosRelatorio(
        titulo=pedido.titulo.strip() or "Relatório da turma",
        professor=pedido.professor.strip(),
        turma=pedido.turma.strip(),
        disciplina=pedido.disciplina.strip(),
        linhas=linhas,
    )

    if pedido.formato.lower() == "csv":
        return Response(
            content=relatorio.gerar_csv(dados),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_nome_arquivo_seguro(dados.titulo, "notas")}.csv"'
                )
            },
        )

    return Response(
        content=relatorio.gerar_pdf(dados),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="{_nome_arquivo_seguro(dados.titulo, "relatorio")}.pdf"'
            )
        },
    )


@app.post(
    "/api/ler-gabarito",
    tags=["correcao"],
    response_model=RespostaGabaritoLido,
    summary="Le o gabarito do QR Code de uma imagem",
)
async def ler_gabarito(file: UploadFile = File(...)) -> RespostaGabaritoLido:
    """Extrai apenas o gabarito do QR, sem corrigir nem gravar nada."""
    imagem_bytes = await _ler_upload(file)

    try:
        imagem = obter_engine(10)._decodificar_imagem(imagem_bytes)
        texto = ler_qr_da_imagem(imagem)
        if not texto:
            raise OMRError("Nenhum QR Code encontrado nesta imagem.")
        leitura = parse_qr_code(texto)
        if leitura.gabarito is None:
            raise OMRError(
                "Este cartao nao carrega o gabarito impresso: as respostas "
                "ficam guardadas no sistema."
            )
        prova_id, gabarito = leitura.prova_id, leitura.gabarito
    except OMRError as exc:
        raise HTTPException(400, str(exc))

    total = max(gabarito)
    return RespostaGabaritoLido(
        prova_id=prova_id,
        total_questoes=total,
        gabarito=[gabarito.get(n, "?") for n in range(1, total + 1)],
    )


@app.post(
    "/api/debug/preview",
    tags=["correcao"],
    summary="Folha retificada com a grade de leitura desenhada (PNG)",
)
async def debug_preview(
    file: UploadFile = File(...), total_questoes: int = Form(10)
) -> Response:
    """Ferramenta de calibracao: mostra o que o motor enxerga."""
    imagem_bytes = await _ler_upload(file)
    try:
        png = obter_engine(total_questoes).gerar_preview_debug(imagem_bytes)
    except OMRError as exc:
        raise HTTPException(400, str(exc))
    return Response(content=png, media_type="image/png")


# ==================================================================
# TRATAMENTO GLOBAL DE ERROS
# ==================================================================
@app.exception_handler(HTTPException)
async def handler_http(_, exc: HTTPException) -> JSONResponse:
    """Padroniza o corpo de todos os erros da API."""
    return JSONResponse(
        status_code=exc.status_code,
        content=RespostaErro(erro=str(exc.detail)).model_dump(),
    )


# ==================================================================
# EXECUCAO LOCAL / CLOUD RUN
# ==================================================================
if __name__ == "__main__":
    import uvicorn
    import webbrowser
    from threading import Timer

    porta = int(os.getenv("PORT", "8080"))

    # Função para abrir diretamente a página do GitHub Pages
    def abrir_navegador():
        webbrowser.open("https://avaliafrg-del.github.io/avalia-frg/")

    # Aguarda 1.5 segundo para o servidor subir e abre o link
    Timer(1.5, abrir_navegador).start()

    # Inicia o servidor local FastAPI/Uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=False)