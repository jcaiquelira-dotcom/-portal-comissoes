# -*- coding: utf-8 -*-
"""
Google Ads direto da fonte, sem o Windsor no meio.

Por que existe: no plano basico do Windsor so UMA fonte fica conectada por vez,
entao o gestor revezava Google Ads, Meta e Perfil da Empresa — e a fonte
desligada congelava. Puxando o Google Ads aqui, o lugar no Windsor fica livre
pro Meta em definitivo.

Duas decisoes que valem registrar:

1. REST, nao a biblioteca oficial. O pacote `google-ads` puxa gRPC, que precisa
   de compilador — esta maquina nao tem toolchain de build. A API tem interface
   REST, entao da pra fazer com urllib puro, igual ao resto do projeto: sem
   dependencia nova e sem risco de quebrar o pipeline das 07:30.

2. Escreve os MESMOS dois arquivos que o Windsor escrevia (_w_amplo.json e
   _windsor_periodo.json), no mesmo formato. Assim o sincronizar_marketing.py
   nao muda nada e da pra voltar atras trocando qual coletor roda.

Credenciais em portal-comissoes/segredos/google_ads.json, fora do git:
    {"client_id": "...", "client_secret": "...", "refresh_token": "...",
     "developer_token": "...", "customer_id": "1234567890",
     "login_customer_id": "0987654321"}

`customer_id` e a conta de anuncios (so digitos, sem tracos).
`login_customer_id` e a MCC — obrigatorio quando o acesso vem por ela.
Gere o refresh_token uma vez com scripts/autorizar_google_ads.py.

Uso:
    python app/google_ads_api.py            # grava os dois arquivos
    python app/google_ads_api.py --testar   # so confere se a credencial responde
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
SAIDA_AMPLO = ROOT / "_w_amplo.json"
SAIDA_PERIODO = ROOT / "_windsor_periodo.json"

# A versao entra na URL. Subir de versao e uma decisao consciente, nao algo que
# acontece sozinho: o Google aposenta versoes antigas com meses de aviso.
VERSAO = "v18"
BASE = f"https://googleads.googleapis.com/{VERSAO}"


def _cred() -> dict:
    if not CRED.exists():
        sys.exit(f"Credenciais nao encontradas em {CRED}\n"
                 "Rode scripts/autorizar_google_ads.py primeiro.")
    d = json.loads(CRED.read_text(encoding="utf-8"))
    faltando = [k for k in ("client_id", "client_secret", "refresh_token",
                            "developer_token", "customer_id") if not d.get(k)]
    if faltando:
        sys.exit(f"Faltam campos em {CRED.name}: {', '.join(faltando)}")
    return d


def token_de_acesso(cred: dict) -> str:
    """Troca o refresh_token por um access_token (vale ~1h).

    Diferente do Mercado Livre, o refresh_token do Google NAO rotaciona: o
    mesmo vale pra sempre, ate ser revogado na mao. Por isso aqui nao tem a
    dansa de regravar credencial a cada chamada.
    """
    corpo = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", corpo)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]


def consultar(cred: dict, access: str, gaql: str) -> list:
    """Roda uma consulta GAQL e devolve as linhas ja achatadas.

    searchStream devolve um array de blocos, cada um com sua lista de results —
    e cada result e uma arvore aninhada (campaign.name vira {'campaign':
    {'name': ...}}). Achatar aqui deixa o resto do arquivo simples.
    """
    url = f"{BASE}/customers/{cred['customer_id']}/googleAds:searchStream"
    cabecalhos = {
        "Authorization": f"Bearer {access}",
        "developer-token": cred["developer_token"],
        "Content-Type": "application/json",
    }
    if cred.get("login_customer_id"):
        cabecalhos["login-customer-id"] = cred["login_customer_id"]
    req = urllib.request.Request(
        url, json.dumps({"query": gaql}).encode(), cabecalhos)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            blocos = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:600]
        sys.exit(f"HTTP {e.code} do Google Ads:\n{detalhe}")

    linhas = []
    for bloco in blocos:
        for res in bloco.get("results", []):
            linhas.append(res)
    return linhas


def _achatar(res: dict) -> dict:
    """{'campaign': {'name': 'X'}, 'metrics': {'costMicros': '123'}} ->
       {'campaign.name': 'X', 'metrics.costMicros': '123'}"""
    saida = {}
    for grupo, valor in res.items():
        if isinstance(valor, dict):
            for k, v in valor.items():
                saida[f"{grupo}.{k}"] = v
        else:
            saida[grupo] = valor
    return saida


def coletar(cred: dict, access: str, de: str, ate: str) -> list:
    """Gasto por dia e por campanha, no formato que o Windsor devolvia.

    O custo vem em micros (1 real = 1.000.000). Dividir aqui e nao la na frente
    porque um numero mil vezes maior passando pelo painel e o tipo de erro que
    so aparece depois de alguem tomar uma decisao com ele.
    """
    gaql = f"""
        SELECT campaign.id, campaign.name, segments.date,
               metrics.cost_micros, metrics.clicks, metrics.impressions,
               metrics.conversions
          FROM campaign
         WHERE segments.date BETWEEN '{de}' AND '{ate}'
    """
    linhas = []
    for res in consultar(cred, access, gaql):
        r = _achatar(res)
        linhas.append({
            "date": r.get("segments.date"),
            "datasource": "google_ads",
            "campaign": r.get("campaign.name") or "—",
            "campaign_id": str(r.get("campaign.id") or ""),
            "spend": round(int(r.get("metrics.costMicros") or 0) / 1_000_000, 2),
            "clicks": int(r.get("metrics.clicks") or 0),
            "impressions": int(r.get("metrics.impressions") or 0),
            "conversions": float(r.get("metrics.conversions") or 0),
        })
    linhas.sort(key=lambda x: (x["date"] or "", x["campaign"]))
    return linhas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testar", action="store_true",
                    help="so confere se a credencial responde, sem gravar nada")
    args = ap.parse_args()

    cred = _cred()
    access = token_de_acesso(cred)

    if args.testar:
        nome = consultar(cred, access,
                         "SELECT customer.descriptive_name, customer.currency_code "
                         "FROM customer LIMIT 1")
        if not nome:
            sys.exit("Conectou, mas a conta nao devolveu nada.")
        c = _achatar(nome[0])
        print(f"OK — conta {c.get('customer.descriptiveName')} "
              f"({c.get('customer.currencyCode')})")
        hoje = date.today()
        teste = coletar(cred, access, (hoje - timedelta(days=7)).isoformat(),
                        hoje.isoformat())
        gasto = sum(x["spend"] for x in teste)
        campanhas = len({x["campaign"] for x in teste})
        print(f"ultimos 7 dias: {len(teste)} linhas | {campanhas} campanhas | "
              f"R$ {gasto:,.2f}")
        return

    hoje = date.today()
    amplo = coletar(cred, access, f"{hoje.year}-01-01", hoje.isoformat())
    SAIDA_AMPLO.write_text(json.dumps({"data": amplo}, ensure_ascii=False),
                           encoding="utf-8")
    periodo = [x for x in amplo
               if x["date"] >= (hoje - timedelta(days=60)).isoformat()]
    SAIDA_PERIODO.write_text(json.dumps({"data": periodo}, ensure_ascii=False),
                             encoding="utf-8")

    gasto = sum(x["spend"] for x in amplo)
    datas = sorted({x["date"] for x in amplo if x["date"]})
    print(f"  _w_amplo.json: {len(amplo)} linhas | R$ {gasto:,.2f} | "
          f"{datas[0] if datas else '—'} a {datas[-1] if datas else '—'}")
    print(f"  _windsor_periodo.json: {len(periodo)} linhas (ultimos 60 dias)")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
