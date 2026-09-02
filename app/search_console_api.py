# -*- coding: utf-8 -*-
"""
Google Search Console: o que o site ganha SEM pagar anuncio.

Por que importa, e por que e diferente de tudo que o painel ja tem: o Google
Ads diz o que a empresa comprou, o Analytics diz o que aconteceu depois que a
pessoa chegou. Nenhum dos dois diz o que a Nevada aparece na busca de graca —
em que posicao, pra qual busca, e quanto disso vira clique.

As tres perguntas que so esta fonte responde:

  1. Que busca traz gente sem custo? Se "coxim do motor gol g5 usado" traz 40
     cliques por mes de graca, pagar anuncio pra ela e queimar dinheiro.
  2. O que aparece muito e ninguem clica? Impressao alta com CTR baixo e
     titulo ruim ou preco fora — e conserto barato, mexe no anuncio do site.
  3. O que esta na posicao 11 a 20? E a segunda pagina: quem esta ali quase
     aparece, e um empurrao pequeno vira trafego. Posicao 50 nao vale esforco.

Escopo: webmasters.readonly — leitura de verdade, diferente do Perfil da
Empresa, que so tem escopo de administracao.

A API entrega no maximo 16 meses e trabalha com atraso de ~2 dias: pedir ate
hoje devolve dia vazio, o que parece queda de trafego e nao e.

Uso:
    python app/search_console_api.py --testar    # confere e mostra o resumo
    python app/search_console_api.py             # grava a chave search_console
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED = Path(r"G:\Meu Drive\portal-comissoes\segredos\google_ads.json")
PORTAL_APP = Path(r"G:\Meu Drive\portal-comissoes\app")
BASE = "https://searchconsole.googleapis.com/webmasters/v3"

# O Google so fecha o dado depois de ~2 dias. Sem essa folga, os ultimos dias
# vem menores do que foram e a serie parece estar caindo.
ATRASO_DIAS = 3


def _cred() -> dict:
    if not CRED.exists():
        sys.exit(f"Credenciais nao encontradas em {CRED}")
    return json.loads(CRED.read_text(encoding="utf-8"))


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


def _chamar(url: str, access: str, corpo: dict = None) -> dict:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    cab = {"Authorization": f"Bearer {access}"}
    if dados:
        cab["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, dados, cab), timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:700]
        if e.code == 403 and "SCOPE" in detalhe.upper():
            sys.exit("Falta o escopo webmasters.readonly nesta credencial.\n"
                     "Rode: python scripts/reautorizar_com_seguranca.py\n\n" + detalhe)
        if "SERVICE_DISABLED" in detalhe:
            sys.exit("A API do Search Console nao esta habilitada neste projeto "
                     "do Google Cloud. O link pra habilitar vem na mensagem:\n\n"
                     + detalhe)
        raise SystemExit(f"HTTP {e.code} em {url.split('?')[0]}:\n{detalhe}")


def escolher_site(access: str, preferido: str = None) -> str:
    """Qual propriedade usar.

    Uma conta costuma ter varias (dominio, com www, sem www, http e https). A
    de dominio (`sc-domain:`) junta todas e e sempre a melhor quando existe —
    escolher uma variante de URL perde o trafego que entrou pelas outras.
    """
    sites = (_chamar(f"{BASE}/sites", access).get("siteEntry") or [])
    donos = [s["siteUrl"] for s in sites
             if s.get("permissionLevel") in ("siteOwner", "siteFullUser",
                                             "siteRestrictedUser")]
    if not donos:
        sys.exit("Nenhuma propriedade do Search Console nesta conta Google. "
                 "Confira se e a mesma conta que administra o site.")
    if preferido and preferido in donos:
        return preferido
    dominio = [s for s in donos if s.startswith("sc-domain:")]
    if dominio:
        return dominio[0]
    nevada = [s for s in donos if "nevada" in s.lower()]
    return (nevada or donos)[0]


def consultar(access: str, site: str, de: date, ate: date,
              dimensoes: list, limite: int = 5000) -> list:
    """Uma consulta ao searchAnalytics, ja pagina a pagina."""
    url = f"{BASE}/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query"
    linhas, inicio = [], 0
    while True:
        d = _chamar(url, access, {
            "startDate": de.isoformat(), "endDate": ate.isoformat(),
            "dimensions": dimensoes, "rowLimit": min(limite, 25000),
            "startRow": inicio, "type": "web",
        })
        lote = d.get("rows") or []
        linhas += lote
        if len(lote) < min(limite, 25000) or len(linhas) >= limite:
            break
        inicio += len(lote)
    return linhas


def coletar(dias: int = 480) -> dict:
    cred = _cred()
    access = token_de_acesso(cred)
    site = escolher_site(access, cred.get("search_console_site"))
    ate = date.today() - timedelta(days=ATRASO_DIAS)
    de = ate - timedelta(days=dias)

    por_dia = {}
    for r in consultar(access, site, de, ate, ["date"], 2000):
        dia = r["keys"][0]
        por_dia[dia] = {
            "cliques": int(r.get("clicks") or 0),
            "impressoes": int(r.get("impressions") or 0),
            "ctr": round(float(r.get("ctr") or 0) * 100, 2),
            "posicao": round(float(r.get("position") or 0), 1),
        }

    # Ultimos 90 dias pras listas: o ano inteiro mistura busca que ja morreu
    # com busca de agora, e a decisao e sempre sobre agora.
    recente = ate - timedelta(days=90)
    termos = [{
        "termo": r["keys"][0],
        "cliques": int(r.get("clicks") or 0),
        "impressoes": int(r.get("impressions") or 0),
        "ctr": round(float(r.get("ctr") or 0) * 100, 2),
        "posicao": round(float(r.get("position") or 0), 1),
    } for r in consultar(access, site, recente, ate, ["query"], 5000)]

    paginas = [{
        "pagina": r["keys"][0],
        "cliques": int(r.get("clicks") or 0),
        "impressoes": int(r.get("impressions") or 0),
        "posicao": round(float(r.get("position") or 0), 1),
    } for r in consultar(access, site, recente, ate, ["page"], 2000)]

    return {
        "site": site, "de": de.isoformat(), "ate": ate.isoformat(),
        "serie_dia": por_dia,
        "termos": sorted(termos, key=lambda x: -x["cliques"])[:300],
        "paginas": sorted(paginas, key=lambda x: -x["cliques"])[:200],
        # As duas leituras que viram acao. Separadas aqui pra tela nao precisar
        # refazer a conta — e pra ficar registrado o criterio de cada uma.
        "aparece_e_ninguem_clica": sorted(
            [t for t in termos if t["impressoes"] >= 100 and t["ctr"] < 1.0],
            key=lambda x: -x["impressoes"])[:40],
        "quase_la": sorted(
            [t for t in termos if 10 < t["posicao"] <= 20 and t["impressoes"] >= 30],
            key=lambda x: -x["impressoes"])[:40],
    }


def gravar_no_portal(novo: dict) -> str:
    sys.path.insert(0, str(PORTAL_APP))
    import server as S
    with S.app.test_request_context():
        caminho = S.resolver_pasta_dados() / "search_console.json"
        atual = S.ler_json(caminho, None) or {}
        # A serie acumula: a API alcanca 16 meses e o historico so cresce.
        serie = {**(atual.get("serie_dia") or {}), **novo["serie_dia"]}
        corpo = {**novo, "serie_dia": serie,
                 "gerado_em": S.agora_br().isoformat(timespec="seconds"),
                 "fonte": "Google Search Console API"}
        S.escrever_json(caminho, corpo)
    cl = sum(d["cliques"] for d in serie.values())
    return (f"{novo['site']} | {len(serie)} dias | {cl:,} cliques organicos | "
            f"{len(novo['termos'])} termos | {len(novo['quase_la'])} na 2a pagina")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--testar", action="store_true")
    p.add_argument("--dias", type=int, default=480)
    a = p.parse_args()

    if a.testar:
        cred = _cred()
        access = token_de_acesso(cred)
        site = escolher_site(access, cred.get("search_console_site"))
        ate = date.today() - timedelta(days=ATRASO_DIAS)
        linhas = consultar(access, site, ate - timedelta(days=28), ate, ["query"], 100)
        cl = sum(int(r.get("clicks") or 0) for r in linhas)
        im = sum(int(r.get("impressions") or 0) for r in linhas)
        print(f"OK — {site}")
        print(f"ultimos 28 dias: {cl:,} cliques | {im:,} impressoes | "
              f"{len(linhas)} termos")
        for r in sorted(linhas, key=lambda x: -(x.get("clicks") or 0))[:5]:
            print(f"   {int(r.get('clicks') or 0):>4} cliques  "
                  f"pos {float(r.get('position') or 0):>4.1f}  {r['keys'][0][:52]}")
        return 0

    print("  " + gravar_no_portal(coletar(a.dias)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
