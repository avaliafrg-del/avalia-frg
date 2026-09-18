"""
dados/repositorios/configuracao.py
------------------------------------------------------------------
Pares chave/valor do sistema, como o segredo do QR.
------------------------------------------------------------------
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

import seguranca
from dados.modelos import Configuracao

def obter_segredo_qr(sessao: Session) -> str:
    """
    Chave usada para assinar os QR Codes.

    Vem de SEGREDO_QR quando definida; caso contrario e gerada uma vez e
    guardada no banco. Trocar essa chave invalida a assinatura de todos
    os cartoes ja impressos, entao ela precisa sobreviver a reinicios —
    por isso banco, e nao arquivo.
    """
    do_ambiente = os.getenv("SEGREDO_QR")
    if do_ambiente:
        return do_ambiente

    registro = sessao.get(Configuracao, "segredo_qr")
    if registro is None:
        registro = Configuracao(chave="segredo_qr", valor=seguranca.gerar_segredo())
        sessao.add(registro)
        sessao.commit()
    return registro.valor
