# -*- coding: utf-8 -*-
"""Quem atendeu cada conversa do Totalk — um lugar só.

Este modulo nasceu em 03/09/2026 de um bug de tres copias. O mapa de user_id
para vendedor existia em `export_dataset.py`, `sincronizar_marketing.py` e
`sincronizar_desempenho.py`, e SO a primeira sabia que o assento do Gustavo
tinha passado pro Lucas em 31/08. Resultado: o dataset e a fila diziam Lucas,
o painel de marketing e o de desempenho diziam Gustavo, pros MESMOS
atendimentos de setembro. Ninguem estava errado por descuido — estavam
consultando mapas diferentes que ninguem lembrou de atualizar juntos.

O assento sobrevive a quem senta nele: o Totalk identifica o usuario, nao a
pessoa. Quando alguem sai e outro assume o mesmo login, a data decide de quem e
a conversa — o historico fica com quem atendeu, e dali em diante e do novo.
Sem isso, o novo vendedor atenderia o dia inteiro e o credito (fila, conversao,
insights, comissao) iria pro nome de quem ja saiu, e o follow-up dos clientes
cairia numa fila que ninguem mais trabalha.
"""

# user_id do Totalk -> (nome de exibicao, slug usado nas chaves do portal)
AGENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": ("Flávia", "flavia"),
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": ("Matheus", "matheus"),
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": ("Gustavo", "gustavo"),
    "edac79e2-5f58-443a-af8f-ad6c3fbdc148": ("Comercial", "comercial"),
}

# user_id -> (data de corte, nome novo, slug novo). Conversa criada A PARTIR da
# data e do novo dono; antes disso continua de quem atendeu.
# Regra do gestor, dita em 31/08/2026.
ASSENTO_TRANSFERIDO = {
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": ("2026-08-31", "Lucas", "lucas"),
}


def _resolver(user_id, created_at):
    corte = ASSENTO_TRANSFERIDO.get(user_id)
    if corte and str(created_at or "") >= corte[0]:
        return corte[1], corte[2]
    return AGENTES.get(user_id) or (None, None)


def nome(user_id, created_at):
    """Nome de exibicao. Sem user_id e "N/A"; user_id desconhecido e "Outro" —
    a distincao importa: um e conversa que ninguem pegou, o outro e alguem que
    atendeu e nao esta no mapa."""
    n, _ = _resolver(user_id, created_at)
    if n:
        return n
    return "N/A" if not user_id else "Outro"


def slug(user_id, created_at, padrao=""):
    """Chave curta usada nas chaves do portal (vendas_flavia, insights_lucas).
    `padrao` e o que devolver quando nao ha atendente — cada chamador decide se
    isso e "" (marketing, que agrupa como "sem atendente") ou None."""
    _, s = _resolver(user_id, created_at)
    return s if s else padrao
