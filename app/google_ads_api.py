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
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py
sys.path.insert(0, str(portal("app")))
import nevada_comum as C  # biblioteca comum do portal — ver la app/nevada_comum.py

ROOT = Path(__file__).resolve().parent.parent
CRED = portal("segredos", "google_ads.json")
SAIDA_AMPLO = ROOT / "_w_amplo.json"
SAIDA_PERIODO = ROOT / "_windsor_periodo.json"

# A versao entra na URL. Subir de versao e uma decisao consciente, nao algo que
# acontece sozinho: o Google aposenta versoes antigas com meses de aviso.
# A v18 saiu do ar; em 02/09/2026 as vivas eram v22 e v23, e v18-v21 davam
# 404 com pagina HTML de erro — que nao parece problema de versao nenhum.
# O Google aposenta uma versao por ano: quando voltar 404 em HTML, e isto.
VERSAO = "v22"
BASE = f"https://googleads.googleapis.com/{VERSAO}"






# Atalhos pra biblioteca comum: os nomes ficam pra nenhum chamador mudar.
def _cred() -> dict:
    return C.cred_google("developer_token", "customer_id", dica="Rode scripts/autorizar_google_ads.py primeiro.")


token_de_acesso = C.token_google


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


def termos_de_busca(cred, access, de, ate) -> list:
    """O que a pessoa DIGITOU pra o anuncio aparecer.

    Isto e diferente da palavra-chave que voce comprou: a palavra-chave e o
    alvo, o termo e o tiro. Quem compra "peca usada" em correspondencia ampla
    paga tambem por "peca usada e roubada", "onde vender peca usada" e
    "peca usada barata gratis" — e so esta consulta mostra isso.

    E o dado que vira dinheiro mais rapido do painel inteiro, porque a acao e
    imediata: termo que gasta e nao clica vira palavra negativa e o gasto para
    no mesmo dia.

    `search_term_view` e por termo + dia. Agrego por termo aqui: o gestor
    decide sobre o termo, nao sobre o termo-naquele-dia, e a serie diaria de um
    termo com 3 cliques no mes e ruido.
    """
    # NAO peca `segments.keyword.info.text` aqui. Ele parece util — seria a
    # palavra-chave comprada ao lado do termo digitado — mas campanha de
    # Shopping e Performance Max nao tem palavra-chave, e o Google responde
    # ZERO LINHA em vez de erro ou de campo vazio. Com esse campo a consulta
    # devolvia 0; sem ele, 34.300. Um filtro silencioso que parece "a conta nao
    # tem termo nenhum", que foi exatamente a conclusao errada que eu tirei.
    gaql = f"""
        SELECT search_term_view.search_term,
               campaign.name, metrics.cost_micros, metrics.clicks,
               metrics.impressions, metrics.conversions
          FROM search_term_view
         WHERE segments.date BETWEEN '{de}' AND '{ate}'
    """
    juntos = {}
    for res in consultar(cred, access, gaql):
        r = _achatar(res)
        termo = (r.get("searchTermView.searchTerm")
                 or r.get("search_term_view.search_term") or "").strip()
        if not termo:
            continue
        a = juntos.setdefault(termo, {
            "termo": termo, "spend": 0.0, "clicks": 0, "impressions": 0,
            "conversions": 0.0, "palavra_comprada": set(), "campanhas": set()})
        a["spend"] += int(r.get("metrics.costMicros") or 0) / 1_000_000
        a["clicks"] += int(r.get("metrics.clicks") or 0)
        a["impressions"] += int(r.get("metrics.impressions") or 0)
        a["conversions"] += float(r.get("metrics.conversions") or 0)
        if r.get("campaign.name"):
            a["campanhas"].add(r["campaign.name"])

    saida = []
    for a in juntos.values():
        a["spend"] = round(a["spend"], 2)
        a["conversions"] = round(a["conversions"], 2)
        a["palavra_comprada"] = sorted(a["palavra_comprada"])[:3]
        a["campanhas"] = sorted(a["campanhas"])[:3]
        a["ctr"] = round(a["clicks"] / a["impressions"] * 100, 2) if a["impressions"] else 0.0
        a["custo_por_clique"] = round(a["spend"] / a["clicks"], 2) if a["clicks"] else None
        saida.append(a)
    return sorted(saida, key=lambda x: -x["spend"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testar", action="store_true",
                    help="so confere se a credencial responde, sem gravar nada")
    ap.add_argument("--termos", action="store_true",
                    help="mostra os termos de busca que dispararam os anuncios")
    args = ap.parse_args()

    cred = _cred()
    access = token_de_acesso(cred)

    if args.termos:
        hoje = date.today()
        de = (hoje - timedelta(days=90)).isoformat()
        termos = termos_de_busca(cred, access, de, hoje.isoformat())
        gasto = sum(t["spend"] for t in termos)
        print(f"{len(termos)} termos em 90 dias | R$ {gasto:,.2f}\n")
        print("--- ONDE O DINHEIRO ESTA INDO ---")
        print("%9s %6s %6s %7s  %s" % ("gasto", "cliq", "impr", "CTR", "termo"))
        for t in termos[:20]:
            print("%9.2f %6d %6d %6.1f%%  %s"
                  % (t["spend"], t["clicks"], t["impressions"], t["ctr"], t["termo"][:56]))
        # Gasta e ninguem clica: e a lista que vira palavra negativa hoje mesmo.
        queima = [t for t in termos if t["spend"] >= 5 and t["clicks"] == 0]
        if queima:
            print(f"\n--- PAGOU E NINGUEM CLICOU (R$ {sum(t['spend'] for t in queima):,.2f}) ---")
            for t in sorted(queima, key=lambda x: -x["spend"])[:15]:
                print("%9.2f %6s %6d %7s  %s"
                      % (t["spend"], "0", t["impressions"], "0%", t["termo"][:56]))
        caro = [t for t in termos if t["clicks"] >= 3 and (t["custo_por_clique"] or 0) >= 2]
        if caro:
            print("\n--- CLIQUE MAIS CARO ---")
            for t in sorted(caro, key=lambda x: -(x["custo_por_clique"] or 0))[:10]:
                print("%9.2f %6d %6s R$%5.2f/clique  %s"
                      % (t["spend"], t["clicks"], "", t["custo_por_clique"], t["termo"][:48]))
        return

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
    C.saida_utf8()
    main()
