# -*- coding: utf-8 -*-
"""
Meta Ads direto da Graph API, sem o Windsor no meio.

Escreve o MESMO _meta_ads.json que o app/atualizar_meta_ads.py escrevia, no
mesmo formato — o sincronizar_marketing.py nao muda nada e da pra voltar atras
trocando qual coletor roda.

Duas decisoes que valem registrar:

1. A versao da API e DESCOBERTA, nao chutada. A Graph API exige a versao na URL
   (/v21.0/...) e aposenta cada uma em ~2 anos. Fixar uma no codigo significa
   que um dia o pipeline quebra sozinho com "Unsupported get request", que nao
   diz nada sobre o motivo real. O script testa da mais nova pra mais velha,
   usa a primeira que responde e grava qual foi — na proxima vez ja comeca por
   ela.

2. Token de usuario de sistema, nao token de usuario comum. O de usuario expira
   em ~60 dias e derrubaria o pipeline sem aviso; o de sistema nao expira.

Credenciais em portal-comissoes/segredos/meta_ads.json, fora do git:
    {"access_token": "...", "ad_account_id": "1234567890"}

`ad_account_id` e so o numero, sem o prefixo "act_".

Uso:
    python app/meta_ads_api.py             # grava _meta_ads.json
    python app/meta_ads_api.py --testar    # so confere se a credencial responde
"""

import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py
sys.path.insert(0, str(portal("app")))
import nevada_comum as C  # biblioteca comum do portal — ver la app/nevada_comum.py

ROOT = Path(__file__).resolve().parent.parent
CRED = portal("segredos", "meta_ads.json")
SAIDA = ROOT / "_meta_ads.json"

# Da mais nova pra mais velha. A primeira que responder e a que vale.
#
# A faixa e ampla de proposito. Nao da pra descobrir a versao mais nova sem um
# token valido: a Graph API valida o token ANTES da versao, entao com token
# ruim ate "vBANANA" responde OAuthException — testei. Como a lista so e
# percorrida na primeira execucao (depois a que funcionou fica gravada), vale
# mais cobrir demais do que fixar uma que envelhece e quebra o pipeline com
# "Unsupported get request", erro que nao diz nada sobre o motivo real.
VERSOES = ["v{}.0".format(n) for n in range(30, 17, -1)]

# O id da acao de conversa iniciada. Nome comprido porque e o id cru da Meta —
# o mesmo que o coletor do Windsor ja usava, pra serie nao mudar de definicao.
ACAO_CONVERSA = "onsite_conversion.messaging_conversation_started_7d"

CAMPOS = ("campaign_name,adset_name,ad_name,spend,clicks,impressions,reach,actions")


def _cred() -> dict:
    if not CRED.exists():
        sys.exit("Credenciais nao encontradas em {}\n"
                 "Veja CONECTAR_META_ADS.md pra gerar o token.".format(CRED))
    d = json.loads(CRED.read_text(encoding="utf-8"))
    faltando = [k for k in ("access_token", "ad_account_id") if not d.get(k)]
    if faltando:
        sys.exit("Faltam campos em {}: {}".format(CRED.name, ", ".join(faltando)))
    d["ad_account_id"] = str(d["ad_account_id"]).replace("act_", "")
    return d


def _gravar_cred(d: dict) -> None:
    CRED.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _pedir(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")
        try:
            erro = json.loads(corpo).get("error", {})
            raise RuntimeError("{}: {}".format(
                erro.get("type", "erro"), erro.get("message", corpo)[:300]))
        except json.JSONDecodeError:
            raise RuntimeError("HTTP {}: {}".format(e.code, corpo[:300]))


def descobrir_versao(cred: dict) -> str:
    """Devolve a primeira versao da Graph API que aceita uma chamada minima."""
    guardada = cred.get("versao_api")
    tentativas = ([guardada] if guardada else []) + \
                 [v for v in VERSOES if v != guardada]
    ultimo = ""
    for v in tentativas:
        url = ("https://graph.facebook.com/{}/act_{}?fields=name,currency"
               "&access_token={}".format(v, cred["ad_account_id"],
                                         urllib.parse.quote(cred["access_token"])))
        try:
            _pedir(url)
            if cred.get("versao_api") != v:
                cred["versao_api"] = v
                _gravar_cred(cred)
            return v
        except RuntimeError as e:
            ultimo = str(e)
            # Token ruim ou sem permissao falha em TODAS as versoes: nao adianta
            # continuar tentando, e o erro real e esse.
            if "OAuthException" in ultimo and "version" not in ultimo.lower():
                sys.exit("A Meta recusou a credencial:\n  {}\n\n"
                         "Confira se o token e de usuario de SISTEMA, se ele tem\n"
                         "a permissao ads_read e se a conta de anuncios foi\n"
                         "atribuida a esse usuario.".format(ultimo))
    sys.exit("Nenhuma versao da Graph API respondeu. Ultimo erro:\n  " + ultimo)


def _conversas(acoes) -> int:
    for a in (acoes or []):
        if a.get("action_type") == ACAO_CONVERSA:
            try:
                return int(float(a.get("value") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def coletar(cred: dict, versao: str, de: str, ate: str) -> list:
    """Insights por dia, anuncio e plataforma, no formato que o painel ja le."""
    params = {
        "level": "ad",
        "fields": CAMPOS,
        "breakdowns": "publisher_platform",
        "time_increment": "1",          # 1 = uma linha por dia
        "time_range": json.dumps({"since": de, "until": ate}),
        "limit": "500",
        "access_token": cred["access_token"],
    }
    url = "https://graph.facebook.com/{}/act_{}/insights?{}".format(
        versao, cred["ad_account_id"], urllib.parse.urlencode(params))

    linhas = []
    paginas = 0
    while url:
        d = _pedir(url)
        for r in d.get("data", []):
            linhas.append({
                "data": r.get("date_start"),
                "campanha": r.get("campaign_name") or "\u2014",
                "anuncio": r.get("ad_name") or "\u2014",
                "plataforma": (r.get("publisher_platform") or "").lower() or "\u2014",
                "spend": round(float(r.get("spend") or 0), 2),
                "clicks": int(float(r.get("clicks") or 0)),
                "impressions": int(float(r.get("impressions") or 0)),
                # Alcance por dia+anuncio+plataforma. NAO pode ser somado entre
                # plataformas: quem viu nos dois lados contaria duas vezes. O
                # painel ja trata isso; aqui so nao se inventa um total.
                "alcance": int(float(r.get("reach") or 0)),
                "conversas": _conversas(r.get("actions")),
            })
        url = (d.get("paging") or {}).get("next")
        paginas += 1
        if paginas > 200:            # trava contra paginacao que nao termina
            print("  aviso: parei em 200 paginas")
            break
    linhas.sort(key=lambda x: (x["data"] or "", x["anuncio"], x["plataforma"]))
    return linhas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testar", action="store_true",
                    help="so confere se a credencial responde, sem gravar")
    ap.add_argument("--desde", help="AAAA-MM-DD (padrao: 1o de janeiro)")
    args = ap.parse_args()

    cred = _cred()
    versao = descobrir_versao(cred)
    hoje = date.today()
    de = args.desde or "{}-01-01".format(hoje.year)

    if args.testar:
        url = ("https://graph.facebook.com/{}/act_{}?fields=name,currency,"
               "account_status&access_token={}".format(
                   versao, cred["ad_account_id"],
                   urllib.parse.quote(cred["access_token"])))
        conta = _pedir(url)
        print("OK  conta {} ({}) | versao {}".format(
            conta.get("name"), conta.get("currency"), versao))
        amostra = coletar(cred, versao, (hoje.replace(day=1)).isoformat(),
                          hoje.isoformat())
        gasto = sum(x["spend"] for x in amostra)
        plats = sorted({x["plataforma"] for x in amostra})
        print("mes corrente: {} linhas | R$ {:,.2f} | plataformas: {}".format(
            len(amostra), gasto, ", ".join(plats) or "nenhuma"))
        return

    linhas = coletar(cred, versao, de, hoje.isoformat())
    SAIDA.write_text(json.dumps({"data": linhas}, ensure_ascii=False),
                     encoding="utf-8")
    gasto = sum(x["spend"] for x in linhas)
    conversas = sum(x["conversas"] for x in linhas)
    datas = sorted({x["data"] for x in linhas if x["data"]})
    print("  _meta_ads.json: {} linhas | R$ {:,.2f} | {} conversas | {} a {}"
          .format(len(linhas), gasto, conversas,
                  datas[0] if datas else "\u2014",
                  datas[-1] if datas else "\u2014"))


if __name__ == "__main__":
    C.saida_utf8()
    main()
