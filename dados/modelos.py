"""
dados/modelos.py
------------------------------------------------------------------
As tabelas do sistema, e só elas.

    Escola                          Usuario
      └── Turma (ano_escolar)         ├── Sessao
            ├── Aluno                 └── Ticket
            └── Prova
                  ├── Questao  (número, correta, conteúdo)
                  └── Resultado (nota + detalhe questão a questão)

Nenhuma consulta mora aqui. Separar a FORMA dos dados das OPERAÇÕES
sobre eles permite ler o schema inteiro numa tela, e evita que uma
regra de negócio se esconda no meio da definição de uma coluna.
As operações estão em `dados/repositorios/`.
------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from dados.conexao import agora


class Base(DeclarativeBase):
    pass


# ==================================================================
# ESCOLA, TURMA, ALUNO, PROVA
# ==================================================================
class Escola(Base):
    __tablename__ = "escolas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(160), unique=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    turmas: Mapped[List["Turma"]] = relationship(
        back_populates="escola", cascade="all, delete-orphan"
    )


class Turma(Base):
    __tablename__ = "turmas"
    __table_args__ = (UniqueConstraint("escola_id", "nome", name="uq_turma_por_escola"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    escola_id: Mapped[int] = mapped_column(ForeignKey("escolas.id", ondelete="CASCADE"))
    nome: Mapped[str] = mapped_column(String(80))
    # Apelido da turma ("8º B", "Turma da Manha") e ANO ESCOLAR sao
    # coisas diferentes. Comparar o desempenho do 2º ano entre escolas
    # exige um dado estruturado; com texto livre a mesma serie viraria
    # "2 ano", "2º Ano" e "segundo ano", e a comparacao nao fecharia.
    ano_escolar: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    ano_letivo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    escola: Mapped[Escola] = relationship(back_populates="turmas")
    alunos: Mapped[List["Aluno"]] = relationship(
        back_populates="turma", cascade="all, delete-orphan"
    )
    provas: Mapped[List["Prova"]] = relationship(
        back_populates="turma", cascade="all, delete-orphan"
    )


class Aluno(Base):
    __tablename__ = "alunos"

    id: Mapped[int] = mapped_column(primary_key=True)
    turma_id: Mapped[int] = mapped_column(ForeignKey("turmas.id", ondelete="CASCADE"))
    nome: Mapped[str] = mapped_column(String(160))
    matricula: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    turma: Mapped[Turma] = relationship(back_populates="alunos")
    resultados: Mapped[List["Resultado"]] = relationship(
        back_populates="aluno", cascade="all, delete-orphan"
    )


class Prova(Base):
    __tablename__ = "provas"

    id: Mapped[int] = mapped_column(primary_key=True)
    turma_id: Mapped[int] = mapped_column(ForeignKey("turmas.id", ondelete="CASCADE"))
    titulo: Mapped[str] = mapped_column(String(120))
    disciplina: Mapped[str] = mapped_column(String(80), default="")
    # {"1": "A", "2": "C", ...} — chave em texto porque JSON nao tem
    # chave inteira; a conversao acontece nas propriedades abaixo.
    gabarito: Mapped[dict] = mapped_column(JSON)

    # Layout do cartao do professor, quando ele traz o proprio modelo.
    # Guarda as regioes onde estao as bolhas, detectadas na folha dele.
    # None significa "cartao gerado por nos", cuja geometria e
    # calculada e nao precisa ser guardada.
    layout: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # PNG da folha em branco do professor, ja estampada com ancoras e
    # sem o QR do aluno. E o molde de onde saem os cartoes nominais:
    # sem guardar, o professor teria que reenviar o arquivo toda vez
    # que quisesse reimprimir a turma.
    modelo_cartao: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    turma: Mapped[Turma] = relationship(back_populates="provas")
    resultados: Mapped[List["Resultado"]] = relationship(
        back_populates="prova", cascade="all, delete-orphan"
    )
    questoes: Mapped[List["Questao"]] = relationship(
        back_populates="prova",
        cascade="all, delete-orphan",
        order_by="Questao.numero",
    )

    @property
    def gabarito_dict(self) -> Dict[int, str]:
        """
        As respostas certas.

        A tabela `questoes` e a fonte da verdade; a coluna JSON so
        atende provas criadas antes de ela existir.
        """
        if self.questoes:
            return {q.numero: q.correta for q in self.questoes}
        return {int(numero): letra for numero, letra in (self.gabarito or {}).items()}

    @property
    def conteudos(self) -> Dict[int, str]:
        return {q.numero: q.conteudo for q in self.questoes if q.conteudo}

    @property
    def usa_cartao_proprio(self) -> bool:
        """True quando a prova usa o cartao-resposta do professor."""
        return bool(self.layout)

    @property
    def total_questoes(self) -> int:
        gabarito = self.gabarito_dict
        return max(gabarito) if gabarito else 0


class Questao(Base):
    """
    Uma questao da prova, com o conteudo que ela cobra.

    Este e o registro que separa "a turma errou a questao 15" de "a
    turma nao aprendeu fracoes". Sem o campo `conteudo`, o sistema
    consegue apontar numeros de questao — inuteis fora daquela prova
    especifica — mas nao consegue dizer o que precisa ser retomado.

    `habilidade` guarda o codigo da BNCC (ex.: EF05MA07) para quem
    trabalha com ele; e opcional porque muita escola nao usa.
    """

    __tablename__ = "questoes"
    __table_args__ = (
        UniqueConstraint("prova_id", "numero", name="uq_questao_por_prova"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prova_id: Mapped[int] = mapped_column(ForeignKey("provas.id", ondelete="CASCADE"))
    numero: Mapped[int] = mapped_column(Integer)
    correta: Mapped[str] = mapped_column(String(1))
    conteudo: Mapped[str] = mapped_column(String(120), default="")
    habilidade: Mapped[str] = mapped_column(String(30), default="")

    prova: Mapped["Prova"] = relationship(back_populates="questoes")


class Resultado(Base):
    __tablename__ = "resultados"
    # Recorrigir a folha do mesmo aluno substitui o resultado anterior
    # em vez de criar um segundo registro.
    __table_args__ = (
        UniqueConstraint("prova_id", "aluno_id", name="uq_resultado_por_aluno"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prova_id: Mapped[int] = mapped_column(ForeignKey("provas.id", ondelete="CASCADE"))
    aluno_id: Mapped[int] = mapped_column(ForeignKey("alunos.id", ondelete="CASCADE"))

    nota: Mapped[float] = mapped_column(Float)
    acertos: Mapped[int] = mapped_column(Integer, default=0)
    erros: Mapped[int] = mapped_column(Integer, default=0)
    em_branco: Mapped[int] = mapped_column(Integer, default=0)
    rasuras: Mapped[int] = mapped_column(Integer, default=0)
    # Guardamos o detalhe questao a questao: e o que permite refazer a
    # analise depois (questao mais errada da turma, recurso de aluno)
    # sem precisar da foto original.
    detalhamento: Mapped[list] = mapped_column(JSON, default=list)
    alinhamento: Mapped[str] = mapped_column(String(30), default="ancoras")
    corrigido_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    prova: Mapped[Prova] = relationship(back_populates="resultados")
    aluno: Mapped[Aluno] = relationship(back_populates="resultados")


# ==================================================================
# ACESSO
# ==================================================================
class Usuario(Base):
    """Professor ou coordenador que usa o sistema."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Guardado em minusculas: ninguem deve ficar sem entrar por ter
    # digitado o proprio email com maiuscula.
    email: Mapped[str] = mapped_column(String(160), unique=True)
    nome: Mapped[str] = mapped_column(String(160))
    senha_hash: Mapped[str] = mapped_column(String(255))
    # ---- Papel e escopo -------------------------------------------
    # "admin"     — secretaria: tudo, e é o único que cria usuários
    # "membro"    — escola: corrige e consulta, só a própria escola
    # "convidado" — professor avulso: nada é gravado no banco
    papel: Mapped[str] = mapped_column(String(12), default="membro")

    # Escopo do membro. None em admin (vê tudo) e em convidado (não vê
    # nada). Um membro SEM escola vinculada também não vê nada — falha
    # fechada de propósito: um erro de cadastro não pode abrir acesso.
    escola_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("escolas.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    ultimo_acesso: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    sessoes: Mapped[List["Sessao"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )
    escola: Mapped[Optional["Escola"]] = relationship(foreign_keys=[escola_id])

    @property
    def administrador(self) -> bool:
        """
        Derivado do papel, e nao guardado numa coluna propria.

        Havia as duas coisas ao mesmo tempo — coluna `administrador` e
        esta propriedade de mesmo nome. A propriedade vencia no acesso
        ao atributo, entao a coluna existia no banco e nunca era lida:
        duas fontes de verdade para a mesma pergunta, com uma delas
        silenciosamente morta.
        """
        return self.papel == "admin"

    @property
    def convidado(self) -> bool:
        return self.papel == "convidado"

    @property
    def pode_gravar(self) -> bool:
        """Convidado usa o sistema sem deixar nada guardado."""
        return self.papel in ("admin", "membro")
    tickets: Mapped[List["Ticket"]] = relationship(
        foreign_keys="Ticket.usuario_id",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )


class Sessao(Base):
    """Sessao ativa de um usuario."""

    __tablename__ = "sessoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE")
    )
    # Apenas o HASH do token. Se o banco vazar, o que esta guardado nao
    # serve para entrar — o cookie precisa do valor original.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime)

    usuario: Mapped[Usuario] = relationship(back_populates="sessoes")


class Ticket(Base):
    """
    Autorizacao de acesso ao sistema.

    Entrar exige email, senha E um ticket valido. A senha diz QUEM e a
    pessoa; o ticket diz que ela CONTINUA autorizada. Separar as duas
    coisas permite cortar o acesso de um professor que saiu da escola
    sem precisar trocar a senha de ninguem — basta revogar o ticket.

    No primeiro uso o ticket se prende a conta que o usou. A partir dai
    ele nao serve para mais ninguem, entao repassar o codigo a um colega
    nao concede acesso.
    """

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Apenas o hash: se o banco vazar, os codigos nao servem para entrar.
    # O codigo completo so aparece uma vez, no momento em que e emitido.
    codigo_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Comeco do codigo ("PF-ABCD…"), para identificar na lista sem revelar.
    pista: Mapped[str] = mapped_column(String(16))
    descricao: Mapped[str] = mapped_column(String(120), default="")

    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    # None = nao expira. E o caso do ticket de quem administra.
    expira_em: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revogado: Mapped[bool] = mapped_column(Boolean, default=False)
    ultimo_uso: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    criado_por_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    # Preenchido no primeiro uso.
    usuario_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=True
    )

    usuario: Mapped[Optional["Usuario"]] = relationship(
        foreign_keys=[usuario_id], back_populates="tickets"
    )

    def situacao(self) -> str:
        """"disponivel", "em uso", "revogado" ou "vencido"."""
        if self.revogado:
            return "revogado"
        if self.expira_em is not None:
            expira = self.expira_em
            if expira.tzinfo is None:
                expira = expira.replace(tzinfo=timezone.utc)
            if expira < agora():
                return "vencido"
        return "em uso" if self.usuario_id else "disponivel"


class RegistroAuditoria(Base):
    """
    Quem fez o quê, e quando.

    Num sistema municipal com dados de criança, saber que uma nota foi
    alterada não basta: é preciso saber QUEM alterou. A LGPD trata isso
    como obrigação do controlador, e sem registro não há como responder
    a um pedido de prestação de contas.

    Guarda o NOME junto do id porque o usuário pode ser removido depois
    — e um registro que aponta para uma conta apagada não serve de nada.
    """

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    usuario_nome: Mapped[str] = mapped_column(String(160), default="")
    papel: Mapped[str] = mapped_column(String(12), default="")

    acao: Mapped[str] = mapped_column(String(40), index=True)
    detalhe: Mapped[str] = mapped_column(String(400), default="")
    # Só o IP, sem user-agent nem mais nada: o mínimo que responde
    # "de onde partiu" sem virar um rastreador de navegação.
    ip: Mapped[str] = mapped_column(String(45), default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, index=True)


class Configuracao(Base):
    """
    Pares chave/valor do sistema.

    Guarda o segredo usado para assinar os QR Codes. Precisa ficar no
    banco, e nao em disco, porque no Cloud Run o disco e efemero: um
    segredo perdido invalidaria todos os cartoes ja impressos.
    """

    __tablename__ = "configuracoes"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(String(255))
