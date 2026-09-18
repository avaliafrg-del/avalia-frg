"""
dados/repositorios/manutencao.py
------------------------------------------------------------------
Operações destrutivas e de backup.

Ficam separadas de propósito. `comum.py` guarda utilidades que todo
repositório usa; apagar o ano letivo de uma rede não é utilidade
comum, e misturar as duas coisas faz uma função perigosa parecer
rotina de apoio.
------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session


def limpar_dados_pedagogicos(sessao: Session) -> dict:
    """
    Apaga escolas, turmas, alunos, provas e notas.

    NÃO toca em usuários nem tickets: quem está usando o sistema
    continua logado e com acesso. Zerar as contas junto tiraria a
    própria pessoa que pediu a limpeza de dentro do sistema.

    A ordem segue as chaves estrangeiras, dos filhos para os pais.
    `query(...).delete()` é bulk e NÃO dispara o cascade do ORM, então
    cada tabela precisa aparecer aqui explicitamente.
    """
    from dados.modelos import Aluno, Escola, Prova, Questao, Resultado, Turma

    contagem = {
        "resultados": 0,
        "questoes": 0,
        "provas": 0,
        "alunos": 0,
        "turmas": 0,
        "escolas": 0,
    }
    ordem = [
        ("resultados", Resultado),
        ("questoes", Questao),
        ("provas", Prova),
        ("alunos", Aluno),
        ("turmas", Turma),
        ("escolas", Escola),
    ]

    for nome, modelo in ordem:
        contagem[nome] = sessao.query(modelo).delete()

    sessao.commit()
    return contagem


def exportar_dados(sessao: Session) -> dict:
    """
    Despeja escolas, turmas, alunos, provas e notas num dicionário.

    É o backup que funciona em qualquer banco. Copiar o arquivo
    `avaliafrg.db` só serve no SQLite, e em produção o banco é
    Postgres — um backup que só funciona em desenvolvimento não é
    backup.

    Senhas e tickets NÃO entram: um arquivo de backup circula por
    e-mail e pen drive, e credencial não pode viajar assim.
    """
    from dados.modelos import Aluno, Escola, Prova, Questao, Resultado, Turma

    def data(valor: Optional[datetime]) -> Optional[str]:
        return valor.isoformat() if valor else None

    escolas = []
    for escola in sessao.scalars(select(Escola).order_by(Escola.nome)):
        turmas = []
        for turma in escola.turmas:
            provas = []
            for prova in turma.provas:
                provas.append(
                    {
                        "titulo": prova.titulo,
                        "disciplina": prova.disciplina,
                        "criada_em": data(prova.criada_em),
                        "questoes": [
                            {
                                "numero": q.numero,
                                "correta": q.correta,
                                "conteudo": q.conteudo,
                                "habilidade": q.habilidade,
                            }
                            for q in prova.questoes
                        ],
                        "resultados": [
                            {
                                "aluno": r.aluno.nome,
                                "nota": r.nota,
                                "acertos": r.acertos,
                                "erros": r.erros,
                                "em_branco": r.em_branco,
                                "rasuras": r.rasuras,
                                "corrigido_em": data(r.corrigido_em),
                                "detalhamento": r.detalhamento,
                            }
                            for r in prova.resultados
                        ],
                    }
                )

            turmas.append(
                {
                    "nome": turma.nome,
                    "ano_escolar": turma.ano_escolar,
                    "ano_letivo": turma.ano_letivo,
                    "alunos": [
                        {"nome": a.nome, "matricula": a.matricula}
                        for a in sorted(turma.alunos, key=lambda x: x.nome)
                    ],
                    "provas": provas,
                }
            )

        escolas.append({"nome": escola.nome, "turmas": turmas})

    return {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "escolas": escolas,
        "aviso": (
            "Backup dos dados pedagógicos. Contas de acesso e tickets não "
            "estão aqui, de propósito: credencial não deve circular em arquivo."
        ),
    }
