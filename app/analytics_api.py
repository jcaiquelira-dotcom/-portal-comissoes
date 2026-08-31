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

ROOT = Path(__file__).resolve().parent.parent
CRED = Path(r"G:\Meu Drive\portal-comissoes\segredos\google_ads.json")
SAIDA = ROOT / "_ga4.json"
BASE = "https://analyticsdata.googleapis.com/v1beta"


def _cred() -> dict:
    if not CRED.exists():
        sys.exit("Credenciais nao encontradas em {}".format(CRED))
    d = json.loads(CRED.read_text(encoding="utf-8"))
    faltando = [k for k in ("client_id", "client_secret", "refresh_token",
                            "ga4_property_id") if not d.get(k)]
    if faltando:
        sys.exit("Faltam campos em {}: {}\n"
                 "O refresh_token sai de scripts/autorizar_google_ads.py."
                 .format(CRED.name, ", ".join(faltando)))
    return d


def token_de_acesso(cred: dict) -> str:
    corpo = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", corpo)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]


def relatorio(cred, access, dimensoes, metricas, de, ate, limite=10000,
              ordem=None):
    """Um runReport. Devolve [{dim: valor, ..., met: numero, ...}]."""
    corpo = {
        "dateRanges": [{"startDate": de, "endDate": ate}],
        "dimensions": [{"name": d} for d in dimensoes],
        "metrics": [{"name": m} for m in metricas],
        "limit": limite,
    }
    if ordem:
        corpo["orderBys"] = ordem
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

    return {"de": de, "ate": ate, "por_dia": por_dia, "por_origem": por_origem,
            "paginas": paginas, "eventos": eventos}


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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    main()
