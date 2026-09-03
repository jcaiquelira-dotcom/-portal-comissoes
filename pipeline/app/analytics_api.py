# -*- coding: utf-8 -*-
"""
Google Analytics 4 do site, via Data API.

Preenche o buraco do meio do funil. Hoje o painel sabe o lead (Totalk) e a
venda (portal), mas nao sabe o que aconteceu ANTES do WhatsApp: quanta gente
chegou no site, por onde, quais pecas olhou e onde desistiu.

Nao precisa de developer token nem de analise do Google — reaproveita o mesmo
projeto Cloud, o mesmo cliente OAuth e o mesmo refresh_token do coletor do
Google Ads, so com o escopo `analytics.readonly` a mais.

Credenciais: as MESMAS de google_ads.json, mais a propriedade:
    {"...": "...", "ga4_property_id": "442735225"}

Uso:
    python app/analytics_api.py            # grava _ga4.json
    python app/analytics_api.py --testar   # so confere se responde
"""

import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py
sys.path.insert(0, str(portal("app")))
import nevada_comum as C  # biblioteca comum do portal — ver la app/nevada_comum.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
CRED = portal("segredos", "google_ads.json")
SAIDA = ROOT / "_ga4.json"
BASE = "https://analyticsdata.googleapis.com/v1beta"






# O que conta como lead no site: o clique que manda a pessoa pro WhatsApp.
# `generate_lead` e o evento atual; `start_contact` aparece no historico ate
# o inicio de 2026 e fica na lista pra serie antiga nao virar zero.
EVENTOS_DE_LEAD = ["generate_lead", "start_contact"]


# Atalhos pra biblioteca comum: os nomes ficam pra nenhum chamador mudar.
def _cred() -> dict:
    return C.cred_google("ga4_property_id")


token_de_acesso = C.token_google


def relatorio(cred, access, dimensoes, metricas, de, ate, limite=10000,
              ordem=None, filtro=None):
    """Um runReport. Devolve [{dim: valor, ..., met: numero, ...}]."""
    corpo = {
        "dateRanges": [{"startDate": de, "endDate": ate}],
        "dimensions": [{"name": d} for d in dimensoes],
        "metrics": [{"name": m} for m in metricas],
        "limit": limite,
    }
    if ordem:
        corpo["orderBys"] = ordem
    if filtro:
        corpo["dimensionFilter"] = filtro
    url = "{}/properties/{}:runReport".format(BASE, cred["ga4_property_id"])
    req = urllib.request.Request(
        url, json.dumps(corpo).encode(),
        {"Authorization": "Bearer " + access, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:500]
        if e.code == 403:
            sys.exit("403 do Analytics:\n  {}\n\n"
                     "Provavel: a conta que autorizou nao tem acesso a "
                     "propriedade {}, ou a Analytics Data API nao esta ativada "
                     "no projeto.".format(detalhe, cred["ga4_property_id"]))
        sys.exit("HTTP {} do Analytics:\n  {}".format(e.code, detalhe))

    linhas = []
    for r_ in d.get("rows", []):
        item = {}
        for i, dim in enumerate(dimensoes):
            item[dim] = r_["dimensionValues"][i].get("value")
        for i, met in enumerate(metricas):
            v = r_["metricValues"][i].get("value")
            try:
                item[met] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                item[met] = 0
        linhas.append(item)
    return linhas


def coletar(cred, access, de, ate) -> dict:
    # Por dia: o grao que deixa o painel somar qualquer periodo filtrado.
    por_dia = relatorio(
        cred, access, ["date"],
        ["sessions", "totalUsers", "newUsers", "screenPageViews",
         "averageSessionDuration", "bounceRate"], de, ate)

    # Por origem: e o que cruza com o custo de midia que o painel ja tem.
    por_origem = relatorio(
        cred, access, ["date", "sessionDefaultChannelGroup"],
        ["sessions", "totalUsers"], de, ate)

    # Paginas mais vistas. Aqui mora o dado que o Totalk nao tem: QUAL peca a
    # pessoa olhou antes de chamar no WhatsApp.
    paginas = relatorio(
        cred, access, ["pagePath", "pageTitle"],
        ["screenPageViews", "sessions", "userEngagementDuration"], de, ate,
        limite=300,
        ordem=[{"metric": {"metricName": "screenPageViews"}, "desc": True}])

    # Eventos POR DIA. Sem a data eles nao servem pro painel: qualquer filtro
    # de periodo teria que confiar num total ja fechado, e o resto do projeto
    # inteiro trabalha no grao diario justamente pra nao precisar disso.
    eventos = relatorio(
        cred, access, ["date", "eventName"], ["eventCount"], de, ate,
        limite=20000,
        ordem=[{"dimension": {"dimensionName": "date"}}])

    # Lead POR CANAL. Ate 02/09/2026 o painel sabia quantos leads vieram do
    # site e, em outra tabela, quantas SESSOES eram Paid Search — mas nunca
    # quantos LEADS eram pagos. Ratear leads pela proporcao de sessoes seria
    # estimativa; pedir canal e evento na mesma consulta e atribuicao, e a
    # diferenca importa: no periodo de teste o Paid Search fazia 65% dos leads
    # com uma fatia de sessoes bem diferente disso.
    #
    # O filtro por eventName e obrigatorio aqui: sem ele a consulta devolve
    # TODOS os eventos vezes TODOS os canais vezes todos os dias, que estoura
    # o limite e traz 90% de linha que ninguem le (scroll, page_view).
    leads_origem = relatorio(
        cred, access, ["date", "sessionDefaultChannelGroup", "eventName"],
        ["eventCount"], de, ate, limite=50000,
        ordem=[{"dimension": {"dimensionName": "date"}}],
        filtro={"filter": {"fieldName": "eventName",
                           "inListFilter": {"values": EVENTOS_DE_LEAD}}})

    return {"de": de, "ate": ate, "por_dia": por_dia, "por_origem": por_origem,
            "paginas": paginas, "eventos": eventos,
            "leads_origem": leads_origem}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testar", action="store_true")
    ap.add_argument("--desde", help="AAAA-MM-DD (padrao: 1o de janeiro)")
    args = ap.parse_args()

    cred = _cred()
    access = token_de_acesso(cred)
    hoje = date.today()

    if args.testar:
        d = relatorio(cred, access, ["date"], ["sessions", "totalUsers"],
                      (hoje - timedelta(days=7)).isoformat(), hoje.isoformat())
        s = sum(x["sessions"] for x in d)
        u = sum(x["totalUsers"] for x in d)
        print("OK  propriedade {} | ultimos 7 dias: {} sessoes, {} usuarios"
              .format(cred["ga4_property_id"], s, u))
        for x in d[-3:]:
            print("   {}  {} sessoes".format(x["date"], x["sessions"]))
        return

    de = args.desde or "{}-01-01".format(hoje.year)
    d = coletar(cred, access, de, hoje.isoformat())
    SAIDA.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    s = sum(x["sessions"] for x in d["por_dia"])
    print("  _ga4.json: {} dias | {:,} sessoes | {} paginas | {} eventos"
          .format(len(d["por_dia"]), s, len(d["paginas"]), len(d["eventos"])))


if __name__ == "__main__":
    C.saida_utf8()
    main()
