"""
dados/repositorios/provas.py
------------------------------------------------------------------
Provas, questoes e o cartao-resposta proprio da escola.
------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from dados.modelos import Aluno, Prova, Questao, Turma
from dados.repositorios.comum import obter as _obter

def criar_prova(
    sessao: Session,
    turma_id: int,
    titulo: str,
    gabarito: Dict[int, str],
    disciplina: str = "",
    conteudos: Optional[Dict[int, str]] = None,
    habilidades: Optional[Dict[int, str]] = None,
) -> Prova:
    """
    Cria a prova e uma linha por questao.

    `conteudos` e opcional, mas e ele que decide se a analise vai poder
    dizer "fracoes" ou apenas "questao 15".
    """
    _obter(sessao, Turma, turma_id)
    conteudos = conteudos or {}
    habilidades = habilidades or {}

    prova = Prova(
        turma_id=turma_id,
        titulo=titulo.strip(),
        disciplina=disciplina.strip(),
        gabarito={str(numero): letra for numero, letra in gabarito.items()},
    )
    sessao.add(prova)
    sessao.flush()   # precisa do id para as questoes

    for numero in sorted(gabarito):
        sessao.add(
            Questao(
                prova_id=prova.id,
                numero=numero,
                correta=gabarito[numero],
                conteudo=(conteudos.get(numero) or "").strip()[:120],
                habilidade=(habilidades.get(numero) or "").strip()[:30],
            )
        )

    sessao.commit()
    return prova


def definir_cartao_proprio(
    sessao: Session,
    prova_id: int,
    layout: dict,
    modelo_png: bytes,
) -> Prova:
    """
    Registra que esta prova usa o cartao-resposta do professor.

    Guarda o layout detectado e o molde ja estampado. Os dois juntos
    permitem reimprimir a turma e corrigir depois sem pedir o arquivo
    de novo.
    """
    prova = _obter(sessao, Prova, prova_id)
    prova.layout = layout
    prova.modelo_cartao = modelo_png
    sessao.commit()
    return prova


def atualizar_conteudos(
    sessao: Session, prova_id: int, conteudos: Dict[int, str]
) -> Prova:
    """
    Preenche ou corrige o conteudo das questoes depois da prova criada.

    Serve para o caso comum de o professor so parar para classificar os
    conteudos quando ja quer ver a analise.
    """
    prova = _obter(sessao, Prova, prova_id)
    por_numero = {q.numero: q for q in prova.questoes}

    for numero, conteudo in conteudos.items():
        questao = por_numero.get(int(numero))
        if questao is not None:
            questao.conteudo = (conteudo or "").strip()[:120]

    sessao.commit()
    return prova


def conteudos_usados(sessao: Session, escola_id: Optional[int] = None) -> List[str]:
    """
    Conteudos ja cadastrados, para a interface sugerir em vez de deixar
    o professor digitar "Fracoes", "fracao" e "FRAÇÕES" na mesma escola.
    """
    consulta = select(Questao.conteudo).where(Questao.conteudo != "")
    if escola_id is not None:
        consulta = consulta.join(Prova).join(Turma).where(Turma.escola_id == escola_id)
    return sorted({c for c in sessao.scalars(consulta.distinct()) if c})


def listar_provas(sessao: Session, turma_id: Optional[int] = None) -> List[Prova]:
    consulta = select(Prova).order_by(Prova.criada_em.desc())
    if turma_id is not None:
        consulta = consulta.where(Prova.turma_id == turma_id)
    return list(sessao.scalars(consulta))


def obter_prova(sessao: Session, prova_id: int) -> Prova:
    return _obter(sessao, Prova, prova_id)
