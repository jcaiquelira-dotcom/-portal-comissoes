# -*- coding: utf-8 -*-
"""
Sincronizador de nuvem: o gasto de midia (Google Ads + Meta Ads via Windsor)
atualizado pelo PROPRIO servidor, uma vez por dia, sem depender do computador
da loja ligado.

Contexto (28/08/2026): o gestor pediu pra tirar o maximo possivel da
dependencia do PC. O que da pra mover sao as fontes que vivem atras de API com
chave estatica — Google e Meta pelo Windsor. O que NAO da por enquanto:

  - leads/fila/insights: nascem do vendas.db (364 MB de conversas do Totalk,
    no disco local; o Supabase esta em 62 MB dos 500 MB do plano — migrar o
    banco inteiro e projeto a parte)
  - Mercado Livre: o refresh_token ROTACIONA a cada uso; mover a rotacao pra
    nuvem exige tirar ela do ml-dashboard local tambem, senao um invalida o
    token do outro. Fica pra proxima etapa, com cuidado.
  - carros/colaboradores/shopee: planilhas que chegam por arquivo.

Este modulo grava a chave marketing_gasto no MESMO formato do
sincronizar_marketing.py local (que continua rodando as 07:30 quando o PC
liga — os dois escrevem dado igualmente fresco, entao a ordem nao importa).
A chave marketing_leads fica intocada: e do pipeline local.

A chave do Windsor vem de segredo_windsor no banco. Sem ela, o thread dorme.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FUSO = timezone(timedelta(hours=-3))
HORA_DIARIA = "06:45"     # antes do expediente abrir
CONVERSAS = "actions_onsite_conversion_messaging_conversation_started_7d"


def _windsor(chave_api, conector, campos, preset):
    url = (f"https://connectors.windsor.ai/{conector}?api_key={chave_api}"
           f"&date_preset={preset}&fields={campos}")
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(r.read().decode())["data"]


def coletar_gasto_google(chave_api):
    """Mesma fusao do sincronizar_marketing local: o ano inteiro da o gasto,
    os 60 dias recentes dao clique e impressao."""
    amplo = _windsor(chave_api, "google_ads",
                     "date,datasource,account_name,campaign,campaign_id,spend",
                     "this_yearT")
    detalhe = _windsor(chave_api, "google_ads",
                       "date,datasource,account_name,campaign,campaign_id,"
                       "spend,clicks,impressions", "last_60dT")

    por_chave = {}
    for r in amplo:
        k = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
        d = por_chave.setdefault(k, {"spend": 0.0, "clicks": 0, "impressions": 0})
        d["spend"] += float(r.get("spend") or 0)
    for r in detalhe:
        k = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
        d = por_chave.setdefault(k, {"spend": 0.0, "clicks": 0, "impressions": 0})
        d["clicks"] += int(r.get("clicks") or 0)
        d["impressions"] += int(r.get("impressions") or 0)

    return [{"data": dt, "fonte": f, "campanha": c,
             "spend": round(v["spend"], 2),
             "clicks": v["clicks"], "impressions": v["impressions"]}
            for (dt, f, c), v in sorted(por_chave.items())]


def coletar_meta(chave_api):
    """Meta Ads dia a dia — o mesmo agregado que o coletar_meta local monta a
    partir do _meta_ads.json, so que direto do Windsor."""
    linhas = _windsor(chave_api, "facebook",
                      f"date,datasource,account_name,campaign,ad_name,"
                      f"spend,clicks,impressions,reach,{CONVERSAS}", "this_yearT")
    limpas = [{
        "data": x.get("date"),
        "anuncio": x.get("ad_name") or "—",
        "spend": round(float(x.get("spend") or 0), 2),
        "clicks": int(float(x.get("clicks") or 0)),
        "impressions": int(float(x.get("impressions") or 0)),
        "alcance": int(float(x.get("reach") or 0)),
        "conversas": int(float(x.get(CONVERSAS) or 0)),
    } for x in linhas]
    if not limpas:
        return None

    datas = sorted({x["data"] for x in limpas if x["data"]})
    por_anuncio = {}
    for x in limpas:
        a = por_anuncio.setdefault(x["anuncio"], {
            "anuncio": x["anuncio"], "spend": 0.0, "impressions": 0,
            "alcance": 0, "conversas": 0})
        a["spend"] += x["spend"]
        a["impressions"] += x["impressions"]
        a["conversas"] += x["conversas"]
        a["alcance"] += x["alcance"]
    detalhe = sorted(por_anuncio.values(), key=lambda a: -a["spend"])
    for a in detalhe:
        a["spend"] = round(a["spend"], 2)

    serie = {}
    for x in limpas:
        d = serie.setdefault(x["data"], {"spend": 0.0, "clicks": 0,
                                         "impressions": 0, "conversas": 0})
        d["spend"] = round(d["spend"] + x["spend"], 2)
        d["clicks"] += x["clicks"]
        d["impressions"] += x["impressions"]
        d["conversas"] += x["conversas"]

    total_spend = round(sum(x["spend"] for x in limpas), 2)
    total_conversas = sum(x["conversas"] for x in limpas)
    return {
        "de": datas[0], "ate": datas[-1],
        "spend": total_spend,
        "impressions": sum(x["impressions"] for x in limpas),
        "conversas": total_conversas,
        "alcance": sum(x["alcance"] for x in limpas),
        "alcance_deduplicado": False,
        "custo_por_conversa": (round(total_spend / total_conversas, 2)
                               if total_conversas else None),
        "anuncios": detalhe,
        "serie_dia": serie,
        "fonte": "windsor (nuvem)",
    }


def sincronizar_gasto(ler_chave, ler_atual, gravar, log=print):
    """Uma rodada completa. Devolve um resumo (ou lanca excecao)."""
    chave = ler_chave()
    if not chave:
        raise RuntimeError("sem chave do Windsor (segredo_windsor)")

    # Uma fonte fora do ar nao pode derrubar a outra: se o Google cair (conta
    # desconectada no Windsor, por exemplo), o Meta ainda entra, e o painel
    # avisa qual fonte faltou em vez de mostrar um total silenciosamente menor.
    ausentes = []
    try:
        gasto = coletar_gasto_google(chave)
    except Exception as e:
        log(f"[sinc-nuvem] Google Ads indisponível: {type(e).__name__}")
        gasto, ausentes = None, ["Google Ads"]
    try:
        meta = coletar_meta(chave)
    except Exception as e:
        log(f"[sinc-nuvem] Meta Ads indisponível: {type(e).__name__}")
        meta = None
    if not meta:
        ausentes.append("Meta Ads")
    if gasto is None and meta is None:
        raise RuntimeError("nenhuma fonte de mídia respondeu")

    # Fonte que falhou preserva o que ja estava gravado, em vez de zerar.
    #
    # Isso e o que sustenta o revezamento: no plano basico do Windsor so uma
    # conta fica conectada por vez, entao o gestor alterna Google e Meta. A
    # fonte desligada continua com o ultimo numero que trouxe, e a data por
    # fonte (abaixo) deixa a tela dizer quao velho ele e — em vez de somar um
    # dado congelado como se fosse de hoje.
    anterior = ler_atual() or {}
    agora_iso = datetime.now(FUSO).isoformat(timespec="seconds")
    datas = dict(anterior.get("atualizado_em") or {})

    if gasto is None:
        gasto = anterior.get("linhas") or []
    else:
        datas["google"] = agora_iso
    if meta is None:
        meta = anterior.get("meta")
    else:
        datas["meta"] = agora_iso

    corpo = {
        "gerado_em": agora_iso,
        "linhas": gasto,
        "meta": meta,
        "fontes_ausentes": ausentes,
        "atualizado_em": datas,
        "origem": "servidor",
    }
    gravar(corpo)
    total_g = round(sum(x["spend"] for x in gasto), 2)
    resumo = (f"google R$ {total_g:,.2f} ({len(gasto)} linhas) | "
              f"meta R$ {meta['spend'] if meta else 0:,.2f}"
              + (f" | faltou: {', '.join(ausentes)}" if ausentes else ""))
    log(f"[sinc-nuvem] gasto atualizado: {resumo}")
    return resumo


def iniciar(ler_chave, ler_atual, gravar, log=print):
    """Thread diario: roda apos HORA_DIARIA se a gravacao do dia ainda nao
    aconteceu (nem aqui, nem pelo pipeline local — a data do gerado_em conta
    pros dois, entao nao ha rodada dupla)."""

    def laco():
        time.sleep(30)
        while True:
            try:
                agora = datetime.now(FUSO)
                hoje = agora.date().isoformat()
                if agora.strftime("%H:%M") >= HORA_DIARIA:
                    atual = ler_atual() or {}
                    ja_hoje = str(atual.get("gerado_em", ""))[:10] == hoje
                    if not ja_hoje:
                        sincronizar_gasto(ler_chave, ler_atual, gravar, log)
            except Exception as e:
                log(f"[sinc-nuvem] {type(e).__name__}: {str(e)[:140]}")
            time.sleep(30 * 60)

    t = threading.Thread(target=laco, daemon=True, name="sincronizador-nuvem")
    t.start()
    return t
