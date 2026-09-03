# -*- coding: utf-8 -*-
"""
Perfil da Empresa (Google) direto da fonte, sem o Windsor no meio.

Por que existe: o mesmo motivo do google_ads_api.py. No plano basico do Windsor
so UMA fonte fica conectada por vez, entao Google Ads, Meta e Perfil se
revezavam e quem ficava de fora congelava sem avisar ninguem — o Perfil parou
em 30/08/2026 exatamente assim, e o card ficou tres dias mostrando numero velho
com cara de numero atual. Com o Ads e o Perfil saindo da fila, a vaga do
Windsor fica livre pro Meta em definitivo.

Tres decisoes que valem registrar:

1. REST com urllib puro, igual ao resto do projeto. Sem dependencia nova, sem
   compilador, sem risco de quebrar o pipeline das 07:30.

2. Grava o MESMO formato que o coletor do Windsor gravava (chave perfil_google:
   serie_dia + termos + gerado_em), entao a tela nao muda uma linha e da pra
   voltar atras trocando qual coletor roda.

3. A serie ACUMULA em cima do que ja existe, nunca substitui. A API so entrega
   os ultimos 18 meses e o portal ja tem historico anterior vindo do Windsor;
   sobrescrever jogaria fora o que a API nao alcanca mais.

Escopo necessario: https://www.googleapis.com/auth/business.manage — e o unico
que a Business Profile Performance API aceita, nao existe versao readonly. Aqui
so se le: nenhuma chamada deste arquivo escreve no perfil.

Uso:
    python app/perfil_google_api.py --testar   # so confere se responde
    python app/perfil_google_api.py            # grava perfil_google no portal
"""

import argparse
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
PORTAL_APP = portal("app")

CONTAS = "https://mybusinessaccountmanagement.googleapis.com/v1"
LOCAIS = "https://mybusinessbusinessinformation.googleapis.com/v1"
DESEMPENHO = "https://businessprofileperformance.googleapis.com/v1"

# As metricas que o card do portal mostra, no nome que a API usa. A esquerda e
# a chave que o portal ja espera — vem do coletor antigo, do Windsor, e nao
# pode mudar sem mexer na tela.
METRICAS = {
    "impressoes_maps_celular": "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "impressoes_busca_celular": "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "impressoes_maps_pc": "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "impressoes_busca_pc": "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "ligacoes": "CALL_CLICKS",
    "site": "WEBSITE_CLICKS",
    "rotas": "BUSINESS_DIRECTION_REQUESTS",
}
# Como o portal chama cada uma na serie_dia. Mantido igual ao do Windsor.
NOME_NA_SERIE = {
    "impressoes_maps_celular": "maps_celular",
    "impressoes_busca_celular": "busca_celular",
    "impressoes_maps_pc": "maps_pc",
    "impressoes_busca_pc": "busca_pc",
    "ligacoes": "ligacoes",
    "site": "site",
    "rotas": "rotas",
}






# Atalhos pra biblioteca comum: os nomes ficam pra nenhum chamador mudar.
def _cred() -> dict:
    return C.cred_google()


token_de_acesso = C.token_google


def _get(url: str, access: str, params: dict = None) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:700]
        if e.code == 403 and "SCOPE" in detalhe.upper():
            sys.exit("Falta o escopo business.manage nesta credencial.\n"
                     "Rode: python scripts/autorizar_google_ads.py\n\n" + detalhe)
        raise SystemExit(f"HTTP {e.code} em {url.split('?')[0]}:\n{detalhe}")


def achar_local(access: str) -> tuple:
    """Devolve (nome_do_recurso, titulo) do primeiro local do perfil.

    A API separa conta de local: a conta e quem administra, o local e a loja.
    Sao duas chamadas e nao da pra pular a primeira — o id do local so existe
    dentro de uma conta.
    """
    contas = _get(f"{CONTAS}/accounts", access).get("accounts") or []
    if not contas:
        sys.exit("Nenhuma conta do Perfil da Empresa nesta credencial. "
                 "Confira se a conta Google escolhida na autorizacao e a que "
                 "administra a ficha da loja.")
    for conta in contas:
        d = _get(f"{LOCAIS}/{conta['name']}/locations", access,
                 {"readMask": "name,title", "pageSize": 100})
        for loc in (d.get("locations") or []):
            return loc["name"], loc.get("title") or "(sem nome)"
    sys.exit("A conta existe mas nao tem local nenhum dentro dela.")


def serie_diaria(access: str, local: str, de: date, ate: date) -> dict:
    """Metricas dia a dia, no formato que o portal ja espera.

    Uma chamada so pra todas as metricas: `fetchMultiDailyMetricsTimeSeries`
    aceita a lista inteira e devolve uma serie por metrica. Pedir uma de cada
    vez seriam sete chamadas pro mesmo intervalo.
    """
    d = _get(f"{DESEMPENHO}/{local}:fetchMultiDailyMetricsTimeSeries", access, {
        "dailyMetrics": list(METRICAS.values()),
        "dailyRange.start_date.year": de.year,
        "dailyRange.start_date.month": de.month,
        "dailyRange.start_date.day": de.day,
        "dailyRange.end_date.year": ate.year,
        "dailyRange.end_date.month": ate.month,
        "dailyRange.end_date.day": ate.day,
    })
    por_api = {v: k for k, v in METRICAS.items()}
    serie = {}
    for bloco in (d.get("multiDailyMetricTimeSeries") or []):
        for item in (bloco.get("dailyMetricTimeSeries") or []):
            nome = por_api.get(item.get("dailyMetric"))
            if not nome:
                continue
            for ponto in ((item.get("timeSeries") or {}).get("datedValues") or []):
                dt = ponto.get("date") or {}
                if not dt.get("year"):
                    continue
                dia = "%04d-%02d-%02d" % (dt["year"], dt.get("month", 1), dt.get("day", 1))
                # `value` vem ausente quando o dia teve zero — a API omite em vez
                # de mandar 0, e sem o default o dia sumiria da serie.
                serie.setdefault(dia, {})[NOME_NA_SERIE[nome]] = int(ponto.get("value") or 0)

    for dia, v in serie.items():
        for chave in NOME_NA_SERIE.values():
            v.setdefault(chave, 0)
        # `impressoes` e a soma das quatro, do mesmo jeito que o Windsor
        # entregava — a tela le esse campo pronto.
        v["impressoes"] = (v["maps_celular"] + v["busca_celular"]
                           + v["maps_pc"] + v["busca_pc"])
    return serie


def termos_de_busca(access: str, local: str, de: date, ate: date, teto: int = 60) -> list:
    """O que as pessoas digitaram pra achar a loja.

    Vale tanto quanto o numero: diz como o cliente PROCURA. A API devolve
    faixa (`insightsValue.threshold`) quando o volume e baixo demais pra
    contar exato — nesse caso o valor e "20 ou menos", e guardar o limiar e
    mais honesto que fingir precisao.
    """
    saida, token, paginas = [], None, 0
    while paginas < 10:
        params = {
            "monthlyRange.start_month.year": de.year,
            "monthlyRange.start_month.month": de.month,
            "monthlyRange.end_month.year": ate.year,
            "monthlyRange.end_month.month": ate.month,
            "pageSize": 100,
        }
        if token:
            params["pageToken"] = token
        d = _get(f"{DESEMPENHO}/{local}/searchkeywords/impressions/monthly",
                 access, params)
        for k in (d.get("searchKeywordsCounts") or []):
            v = k.get("insightsValue") or {}
            exato = v.get("value")
            saida.append({
                "termo": k.get("searchKeyword") or "",
                "buscas": int(exato if exato is not None else (v.get("threshold") or 0)),
                "aproximado": exato is None,
            })
        token = d.get("nextPageToken")
        paginas += 1
        if not token:
            break
    juntos = {}
    for t in saida:
        a = juntos.setdefault(t["termo"], {"termo": t["termo"], "buscas": 0,
                                           "aproximado": False})
        a["buscas"] += t["buscas"]
        a["aproximado"] = a["aproximado"] or t["aproximado"]
    return sorted(juntos.values(), key=lambda x: -x["buscas"])[:teto]


def coletar(dias: int = 540) -> dict:
    cred = _cred()
    access = token_de_acesso(cred)
    local, titulo = achar_local(access)
    # A API entrega ate ~18 meses e o dia de ontem e o ultimo fechado: pedir
    # ate hoje devolve um dia parcial que muda de valor durante o dia.
    ate = date.today() - timedelta(days=1)
    de = ate - timedelta(days=dias)
    serie = serie_diaria(access, local, de, ate)
    termos = termos_de_busca(access, local, de, ate)
    return {"local": local, "titulo": titulo, "serie_dia": serie, "termos": termos}


def gravar_no_portal(novo: dict) -> str:
    """Junta com o que ja existe e grava a chave perfil_google.

    Acumula: a API alcanca ~18 meses e o portal tem historico anterior vindo do
    Windsor. Substituir jogaria fora o que a fonte nova nao consegue mais ver.
    """
    sys.path.insert(0, str(PORTAL_APP))
    import server as S
    with S.app.test_request_context():
        caminho = S.resolver_pasta_dados() / "perfil_google.json"
        atual = S.ler_json(caminho, None) or {}
        serie = {**(atual.get("serie_dia") or {}), **novo["serie_dia"]}
        corpo = {
            "gerado_em": S.agora_br().isoformat(timespec="seconds"),
            "fonte": "Google Business Profile API (direto)",
            "local": novo["local"],
            "titulo": novo["titulo"],
            "serie_dia": serie,
            "termos": novo["termos"] or (atual.get("termos") or []),
        }
        S.escrever_json(caminho, corpo)
    lig = sum(d.get("ligacoes", 0) for d in serie.values())
    return (f"{novo['titulo']} | {len(serie)} dias | {lig:,} ligacoes | "
            f"{len(corpo['termos'])} termos")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--testar", action="store_true",
                   help="so confere se a credencial responde, sem gravar")
    p.add_argument("--dias", type=int, default=540)
    a = p.parse_args()

    if a.testar:
        cred = _cred()
        access = token_de_acesso(cred)
        local, titulo = achar_local(access)
        ate = date.today() - timedelta(days=1)
        serie = serie_diaria(access, local, ate - timedelta(days=7), ate)
        lig = sum(d.get("ligacoes", 0) for d in serie.values())
        imp = sum(d.get("impressoes", 0) for d in serie.values())
        print(f"OK — {titulo} ({local})")
        print(f"ultimos 7 dias: {len(serie)} dias | {imp:,} impressoes | {lig:,} ligacoes")
        return 0

    novo = coletar(a.dias)
    print("  " + gravar_no_portal(novo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
