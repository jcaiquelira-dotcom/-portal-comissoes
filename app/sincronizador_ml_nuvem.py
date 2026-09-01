# -*- coding: utf-8 -*-
"""
Mercado Livre atualizado pelo proprio servidor, sem depender do PC da loja.

O CUIDADO QUE DEFINE ESTE ARQUIVO: o refresh_token do ML rotaciona a cada uso —
gastar um token devolve outro e queima o anterior. Se a nuvem e o computador
renovarem em paralelo, um invalida o outro e a integracao morre calada. Por
isso, desde 28/08/2026, o BANCO e a fonte unica: a credencial vive na chave
segredo_ml, e quem renova grava o token novo la ANTES de qualquer outra
chamada. O ml_auth.json local virou espelho de leitura; o sincronizador local
e a rotina diaria dos artifacts passaram a ler do banco tambem.

A gravacao do token usa SELECT ... FOR UPDATE: se duas rodadas comecarem
juntas (deploy no meio do ciclo, por exemplo), a segunda espera a primeira
terminar em vez de renovar por cima.

O que sobe: reputacao, pos-venda, faturamento real (pagamentos do Mercado
Pago) e Product Ads — o mesmo pacote que scripts/sincronizar_ml.py monta,
gravado na mesma chave ml_conta, com merge da serie diaria.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

API = "https://api.mercadolibre.com"
FUSO = timezone(timedelta(hours=-3))
# Antes rodava 1x as 06:20 — e ai a venda de hoje so aparecia amanha, que foi
# exatamente a queixa do gestor em 30/08/2026 ("coloquei pra atualizar as
# vendas de hoje e nao consta nada"). Agora roda de hora em hora das 6h as 22h;
# com o access_token reaproveitado isso quase nao gasta refresh a mais.
INTERVALO_MIN = 60
HORA_INICIO, HORA_FIM = 6, 22

MOTIVOS = {
    "PDD9939": "Arrependimento", "PDD9829": "Arrependimento",
    "PDD9949": "Chegou sem funcionar", "PDD9946": "Danificado no transporte",
    "PDD9967": "Outro problema / incompatível", "PDD9944": "Diferente do anunciado",
}


def _http(url, token=None, corpo=None, cabecalhos=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (cabecalhos or {}).items():
        req.add_header(k, v)
    dados = None
    if corpo is not None:
        dados = urllib.parse.urlencode(corpo).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, dados, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def token_valido(cred, gravar_cred):
    """Devolve um access_token usavel, renovando so quando precisa.

    O access_token do ML dura ~6h. Guardar ele (com a hora de expiracao) deixa
    o coletor rodar de hora em hora sem gastar um refresh_token a cada vez —
    e o refresh ROTACIONA, entao cada uso e uma chance de erro. Com isso a
    rotacao cai de ~12x/dia pra ~4x, e a venda de hoje aparece hoje.
    """
    agora = datetime.now(timezone.utc).timestamp()
    # 5 min de folga: token que expira no meio da coleta daria 401 no fim.
    if cred.get("access_token") and float(cred.get("access_expira_em") or 0) > agora + 300:
        return cred["access_token"], cred["user_id"]
    return renovar(cred, gravar_cred)


def renovar(cred, gravar_cred):
    """Troca o refresh_token por um par novo e GRAVA na hora.

    A gravacao vem antes de devolver o access_token de proposito: se o
    processo morrer aqui, o banco ja tem o token valido. Na ordem inversa,
    uma queda deixaria o banco com um refresh_token que o ML acabou de queimar
    — e ai so reautorizando na mao.
    """
    resp = _http(f"{API}/oauth/token", corpo={
        "grant_type": "refresh_token",
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
    })
    novo = dict(cred)
    novo["refresh_token"] = resp["refresh_token"]
    novo["access_token"] = resp["access_token"]
    novo["access_expira_em"] = (datetime.now(timezone.utc).timestamp()
                                + float(resp.get("expires_in") or 21600))
    novo["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    novo["rotacionado_por"] = "servidor"
    gravar_cred(novo)
    return resp["access_token"], cred["user_id"]


def _quando(txt):
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt[:26].rstrip("Z") + "+00:00")
    except ValueError:
        return None


def coletar_reputacao(token, user_id):
    u = _http(f"{API}/users/{user_id}", token)
    sr = u.get("seller_reputation") or {}
    met = sr.get("metrics") or {}
    trans = sr.get("transactions") or {}
    return {
        "nivel": sr.get("level_id"),
        "medalha": sr.get("power_seller_status"),
        "vendas_60d": (met.get("sales") or {}).get("completed"),
        "reclamacoes_60d": met.get("claims") or {},
        "atrasos_60d": met.get("delayed_handling_time") or {},
        "cancelamentos_60d": met.get("cancellations") or {},
        "avaliacoes": trans.get("ratings") or {},
        "transacoes_total": trans.get("total"),
    }


def _contar(token, user_id, filtro):
    d = _http(f"{API}/post-purchase/v1/claims/search?{filtro}&limit=1", token,
              cabecalhos={"x-caller.id": str(user_id)})
    return (d.get("paging") or {}).get("total", 0)


def coletar_pos_venda(token, user_id):
    registros = []
    for status in ("closed", "opened"):
        offset = 0
        while offset < 3000:
            d = _http(f"{API}/post-purchase/v1/claims/search?status={status}"
                      f"&range=date_created:after:now-35d,before:now"
                      f"&limit=50&offset={offset}", token,
                      cabecalhos={"x-caller.id": str(user_id)})
            dados = d.get("data") or []
            registros.extend(dados)
            offset += 50
            if offset >= (d.get("paging") or {}).get("total", 0) or not dados:
                break

    agora = datetime.now(FUSO)
    corte = (agora - timedelta(days=30)).isoformat()
    mes = agora.isoformat()[:7]

    def resumo(grupo):
        tipos = Counter(r.get("type") for r in grupo)
        motivos = Counter(
            MOTIVOS.get(r.get("reason_id"),
                        "Problema de entrega" if str(r.get("reason_id") or "").startswith("PNR")
                        else "Outros")
            for r in grupo if r.get("type") == "mediations")
        return {
            "mediacoes": tipos.get("mediations", 0),
            "devolucoes": tipos.get("returns", 0),
            "cancel_comprador": tipos.get("cancel_purchase", 0),
            "cancel_vendedor": tipos.get("cancel_sale", 0),
            "motivos": [{"motivo": m, "qtd": q} for m, q in motivos.most_common(5)],
        }

    return {
        "abertas_agora": _contar(token, user_id, "status=opened"),
        "mediacoes_agora": _contar(token, user_id, "stage=mediation"),
        "dias30": resumo([r for r in registros if (r.get("date_created") or "") >= corte]),
        "mes_atual": resumo([r for r in registros if (r.get("date_created") or "")[:7] == mes]),
    }


def coletar_vendas(token, user_id):
    """Faturamento pelos pagamentos aprovados do Mercado Pago com origem MELI.
    A permissao de Orders o app nao tem, mas o dinheiro conta a mesma historia."""
    agora = datetime.now(FUSO)
    inicio = min(agora - timedelta(days=30),
                 agora.replace(day=1, hour=0, minute=0, second=0))
    begin = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = agora.strftime("%Y-%m-%dT%H:%M:%SZ")

    pagamentos, offset = [], 0
    while offset < 20000:
        d = _http(f"{API}/collections/search?seller_id={user_id}&limit=50&offset={offset}"
                  f"&range=date_approved&begin_date={begin}&end_date={end}", token)
        pagamentos.extend(x.get("collection") or {} for x in d.get("results") or [])
        offset += 50
        if offset >= (d.get("paging") or {}).get("total", 0):
            break

    validos = [c for c in pagamentos
               if c.get("status") == "approved"
               and c.get("operation_type") == "regular_payment"
               and c.get("marketplace") == "MELI"]
    corte = (agora - timedelta(days=30)).isoformat()
    mes = agora.isoformat()[:7]

    def resumo(grupo):
        soma = round(sum(float(c.get("transaction_amount") or 0) for c in grupo), 2)
        return {"pagamentos": len(grupo), "total": soma,
                "ticket": round(soma / len(grupo), 2) if grupo else 0}

    serie = {}
    for c in validos:
        try:
            dia = datetime.fromisoformat(c["date_approved"]).astimezone(FUSO).date().isoformat()
        except (KeyError, ValueError, TypeError):
            continue
        d = serie.setdefault(dia, {"total": 0.0, "qtd": 0})
        d["total"] = round(d["total"] + float(c.get("transaction_amount") or 0), 2)
        d["qtd"] += 1

    # Devolucao. Duas formas, e elas entram no faturamento de jeitos opostos:
    #
    #   integral  -> o pagamento sai de "approved" e vira "refunded", ou seja,
    #                ja NAO esta no faturamento acima. Aparece aqui so pra dizer
    #                o tamanho do problema.
    #   parcial   -> o pagamento continua "approved" pelo valor cheio, e o que
    #                voltou pro comprador esta em amount_refunded. Esse SIM
    #                precisa sair, senao o faturamento conta dinheiro que a
    #                empresa devolveu.
    devolvidos = [c for c in pagamentos
                  if c.get("marketplace") == "MELI" and c.get("status") == "refunded"]
    parciais = round(sum(float(c.get("amount_refunded") or 0) for c in validos), 2)

    def dev(grupo):
        return {"qtd": len(grupo),
                "total": round(sum(float(c.get("transaction_amount") or 0)
                                   for c in grupo), 2)}

    serie_dev = {}
    for c in devolvidos:
        try:
            dia = datetime.fromisoformat(c["date_approved"]).astimezone(FUSO).date().isoformat()
        except (KeyError, ValueError, TypeError):
            continue
        d_ = serie_dev.setdefault(dia, {"total": 0.0, "qtd": 0})
        d_["total"] = round(d_["total"] + float(c.get("transaction_amount") or 0), 2)
        d_["qtd"] += 1

    return {
        "dias30": resumo([c for c in validos if (c.get("date_approved") or "") >= corte]),
        "mes_atual": resumo([c for c in validos if (c.get("date_approved") or "")[:7] == mes]),
        "devolucoes": {
            "dias30": dev([c for c in devolvidos if (c.get("date_approved") or "") >= corte]),
            "mes_atual": dev([c for c in devolvidos if (c.get("date_approved") or "")[:7] == mes]),
            "parcial_no_periodo": parciais,
            "serie_dia": serie_dev,
        },
        "fora_meli_30d": len([c for c in pagamentos
                              if c.get("status") == "approved"
                              and c.get("marketplace") != "MELI"]),
        "serie_dia": serie,
    }


def coletar_ads(token):
    try:
        adv = _http(f"{API}/advertising/advertisers?product_id=PADS", token,
                    cabecalhos={"Api-Version": "1"})
        lista = adv.get("advertisers") or []
        if not lista:
            return None
        a = lista[0]
        base = (f"{API}/marketplace/advertising/{a['site_id']}/advertisers/"
                f"{a['advertiser_id']}/product_ads/campaigns/search")
        ate = datetime.now(FUSO).date()
        de = ate - timedelta(days=30)
        campanhas, offset = [], 0
        while offset < 500:
            d = _http(f"{base}?limit=50&offset={offset}&date_from={de}&date_to={ate}"
                      f"&metrics=cost,clicks,prints,units_quantity,direct_amount,"
                      f"indirect_amount,total_amount,acos",
                      token, cabecalhos={"Api-Version": "2"})
            campanhas.extend(d.get("results") or [])
            offset += 50
            if offset >= (d.get("paging") or {}).get("total", 0):
                break

        def soma(campo):
            return round(sum(float((c.get("metrics") or {}).get(campo) or 0)
                             for c in campanhas), 2)
        investido = soma("cost")
        receita = soma("total_amount") or (soma("direct_amount") + soma("indirect_amount"))
        return {
            "de": str(de), "ate": str(ate),
            "investido": investido,
            "cliques": int(soma("clicks")),
            "impressoes": int(soma("prints")),
            "vendas_atribuidas": int(soma("units_quantity")),
            "receita_atribuida": receita,
            "acos": round(100 * investido / receita, 1) if receita else None,
            "campanhas_ativas": sum(1 for c in campanhas if c.get("status") == "active"),
            "campanhas": len(campanhas),
        }
    except Exception:
        return None       # conta sem acesso a Product Ads: o painel so nao mostra o bloco


def sincronizar(ler_cred, gravar_cred, ler_atual, gravar, log=print):
    """Uma rodada completa. Devolve um resumo curto."""
    cred = ler_cred()
    if not cred or not cred.get("refresh_token"):
        raise RuntimeError("sem credencial do ML (segredo_ml)")

    token, user_id = token_valido(cred, gravar_cred)
    rep = coletar_reputacao(token, user_id)
    pos = coletar_pos_venda(token, user_id)
    vendas = coletar_vendas(token, user_id)
    ads = coletar_ads(token)

    # A serie diaria acumula: a rodada cobre 30 dias, os dias antigos ja
    # gravados nao podem sumir. Dia rebaixado agora substitui (pega estorno).
    atual = ler_atual() or {}
    antiga = ((atual.get("vendas") or {}).get("serie_dia") or {})
    vendas["serie_dia"] = {**antiga, **vendas["serie_dia"]}
    if vendas["serie_dia"]:
        vendas["serie_desde"] = min(vendas["serie_dia"])

    gravar({
        "gerado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
        "reputacao": rep, "pos_venda": pos, "vendas": vendas, "ads": ads,
        "origem": "servidor",
    })
    resumo = (f"{rep['nivel']}/{rep['medalha']} | mês R$ "
              f"{vendas['mes_atual']['total']:,.2f} | {len(vendas['serie_dia'])} dias")
    log(f"[sinc-ml] {resumo}")
    return resumo


def iniciar(ler_cred, gravar_cred, ler_atual, gravar, log=print):
    def laco():
        time.sleep(60)     # deixa o servidor subir antes
        while True:
            try:
                agora = datetime.now(FUSO)
                if HORA_INICIO <= agora.hour < HORA_FIM:
                    atual = ler_atual() or {}
                    # Uma vez por hora: se a ultima gravacao ja e desta hora,
                    # espera a proxima.
                    ultima = str(atual.get("gerado_em", ""))[:13]
                    if ultima != agora.isoformat()[:13]:
                        sincronizar(ler_cred, gravar_cred, ler_atual, gravar, log)
            except Exception as e:
                log(f"[sinc-ml] {type(e).__name__}: {str(e)[:140]}")
            time.sleep(INTERVALO_MIN * 60)

    t = threading.Thread(target=laco, daemon=True, name="sincronizador-ml")
    t.start()
    return t
