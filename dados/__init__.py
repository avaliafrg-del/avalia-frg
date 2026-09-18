"""
dados/__init__.py
------------------------------------------------------------------
A porta de entrada da camada de dados.

O resto do sistema importa daqui e só daqui:

    import dados as db
    db.criar_escola(sessao, "EM Vila Nova")

Por que uma fachada, e não importar cada repositório direto: quem
chama não deveria precisar saber que `criar_prova` mora em
`repositorios/provas.py` e `autenticar` em `repositorios/acesso.py`.
Mover uma função entre repositórios passa a ser um detalhe interno,
sem tocar em `main.py` nem nos testes.

Organização por trás desta fachada:

    dados/
    ├── conexao.py       engine, sessões, criação do schema
    ├── modelos.py       as tabelas, e nada além delas
    ├── erros.py         exceções que a camada HTTP traduz
    └── repositorios/    consultas e regras, um módulo por assunto
        ├── comum.py         busca por id compartilhada
        ├── escolas.py
        ├── turmas.py
        ├── alunos.py
        ├── provas.py        questões e cartão próprio
        ├── resultados.py    notas e estatísticas da prova
        ├── historico.py     desempenho ao longo do ano
        ├── acesso.py        usuários, senhas e sessões
        ├── tickets.py       autorização de entrada
        └── configuracao.py  segredo do QR
------------------------------------------------------------------
"""

from __future__ import annotations

# Reexportado porque parte do código monta janelas de tempo
# (últimos 30 dias) e ordena por data.
from datetime import timedelta

from sqlalchemy import select

from dados.conexao import (
    DATABASE_URL,
    SessionLocal,
    agora,
    criar_tabelas,
    engine,
    obter_sessao,
)
from dados.erros import CredencialInvalida, RegistroNaoEncontrado, TicketInvalido
from dados.modelos import (
    Aluno,
    RegistroAuditoria,
    Base,
    Configuracao,
    Escola,
    Prova,
    Questao,
    Resultado,
    Sessao,
    Ticket,
    Turma,
    Usuario,
)
from dados.repositorios.acesso import (
    abrir_sessao,
    autenticar,
    contar_admins,
    criar_usuario,
    listar_usuarios,
    obter_usuario,
    remover_usuario,
    fechar_sessao,
    ha_usuarios,
    limpar_sessoes_vencidas,
    usuario_da_sessao,
)
from dados.repositorios import auditoria
from dados.repositorios.alunos import (
    adicionar_alunos,
    listar_alunos,
    obter_aluno,
    remover_aluno,
)
from dados.repositorios.comum import contar
from dados.repositorios.configuracao import obter_segredo_qr
from dados.repositorios.escolas import criar_escola, listar_escolas
from dados.repositorios.manutencao import (
    exportar_dados,
    limpar_dados_pedagogicos,
)
from dados.repositorios.historico import desempenho_da_turma, historico_do_aluno
from dados.repositorios.provas import (
    atualizar_conteudos,
    conteudos_usados,
    criar_prova,
    definir_cartao_proprio,
    listar_provas,
    obter_prova,
)
from dados.repositorios.resultados import (
    estatisticas_da_prova,
    listar_resultados,
    salvar_resultado,
)
from dados.repositorios.tickets import (
    criar_ticket,
    listar_tickets,
    revogar_ticket,
    usar_ticket,
    validar_ticket,
)
from dados.repositorios.turmas import criar_turma, listar_turmas, obter_turma

__all__ = [
    # infraestrutura
    "DATABASE_URL",
    "SessionLocal",
    "engine",
    "agora",
    "timedelta",
    "select",
    "criar_tabelas",
    "obter_sessao",
    # erros
    "RegistroNaoEncontrado",
    "CredencialInvalida",
    "TicketInvalido",
    # modelos
    "Base",
    "Escola",
    "Turma",
    "Aluno",
    "Prova",
    "Questao",
    "Resultado",
    "Usuario",
    "Sessao",
    "Ticket",
    "Configuracao",
    # escolas / turmas / alunos
    "criar_escola",
    "listar_escolas",
    "criar_turma",
    "listar_turmas",
    "obter_turma",
    "adicionar_alunos",
    "listar_alunos",
    "remover_aluno",
    # provas
    "criar_prova",
    "listar_provas",
    "obter_prova",
    "obter_aluno",
    "atualizar_conteudos",
    "conteudos_usados",
    "definir_cartao_proprio",
    # resultados
    "salvar_resultado",
    "listar_resultados",
    "estatisticas_da_prova",
    "historico_do_aluno",
    "desempenho_da_turma",
    # acesso
    "ha_usuarios",
    "criar_usuario",
    "listar_usuarios",
    "obter_usuario",
    "remover_usuario",
    "contar_admins",
    "autenticar",
    "abrir_sessao",
    "usuario_da_sessao",
    "fechar_sessao",
    "limpar_sessoes_vencidas",
    "criar_ticket",
    "listar_tickets",
    "revogar_ticket",
    "validar_ticket",
    "usar_ticket",
    "obter_segredo_qr",
    "contar",
    "limpar_dados_pedagogicos",
    "auditoria",
    "RegistroAuditoria",
    "exportar_dados",
]
