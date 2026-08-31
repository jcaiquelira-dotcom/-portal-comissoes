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


# Tipo de campanha do Google -> o que ela busca de verdade. Campanha que leva
# o cliente ate a LOJA (mapa, ligacao, visita) nao pode ser medida pela mesma
# regua de quem vende no site: ela converte em telefone tocando e gente
# entrando na porta, coisas que o site nao registra. Sem separar, o custo por
# venda do site sai inflado e a campanha local parece ruim sem ser.
TIPO_CAMPANHA = {
    "SEARCH": "Busca", "SHOPPING": "Shopping", "DISPLAY": "Display",
    "PERFORMANCE_MAX": "Performance Max", "VIDEO": "Vídeo",
    "LOCAL": "Loja física", "LOCAL_SERVICES": "Serviços locais",
    "SMART": "Smart", "DISCOVERY": "Descoberta", "DEMAND_GEN": "Demand Gen",
}
# Os tipos que existem pra trazer gente ate a loja, nao pro site.
TIPOS_LOCAIS = {"LOCAL", "LOCAL_SERVICES"}


def coletar_gasto_google(chave_api):
    """Mesma fusao do sincronizar_marketing local: o ano inteiro da o gasto,
    os 60 dias recentes dao clique e impressao.

    Desde 30/08/2026 traz tambem o TIPO da campanha, ligacoes e o total de
    conversoes — e o que permite separar campanha de loja (mapa/telefone) de
    campanha de site, a pedido do gestor.
    """
    amplo = _windsor(chave_api, "google_ads",
                     "date,datasource,account_name,campaign,campaign_id,spend,"
                     "campaign_type,advertising_channel_sub_type",
                     "this_yearT")
    detalhe = _windsor(chave_api, "google_ads",
                       "date,datasource,account_name,campaign,campaign_id,"
                       "spend,clicks,impressions,phone_calls,all_conversions,"
                       "campaign_type", "last_60dT")

    def vazio():
        return {"spend": 0.0, "clicks": 0, "impressions": 0,
                "ligacoes": 0, "conversoes": 0.0, "tipo": ""}

    por_chave = {}
    for r in amplo:
        k = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
        d = por_chave.setdefault(k, vazio())
        d["spend"] += float(r.get("spend") or 0)
        d["tipo"] = d["tipo"] or (r.get("campaign_type") or "")
    for r in detalhe:
        k = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
        d = por_chave.setdefault(k, vazio())
        d["clicks"] += int(float(r.get("clicks") or 0))
        d["impressions"] += int(float(r.get("impressions") or 0))
        d["ligacoes"] += int(float(r.get("phone_calls") or 0))
        d["conversoes"] += float(r.get("all_conversions") or 0)
        d["tipo"] = d["tipo"] or (r.get("campaign_type") or "")

    saida = []
    for (dt, f, c), v in sorted(por_chave.items()):
        tipo = (v["tipo"] or "").upper()
        saida.append({
            "data": dt, "fonte": f, "campanha": c,
            "spend": round(v["spend"], 2),
            "clicks": v["clicks"], "impressions": v["impressions"],
            "ligacoes": v["ligacoes"],
            "conversoes": round(v["conversoes"], 2),
            "tipo": tipo,
            "tipo_nome": TIPO_CAMPANHA.get(tipo, tipo.title().replace("_", " ") if tipo else ""),
            # O objetivo e o que muda a leitura: "loja" se mede em telefone
            # tocando e gente na porta; "site" se mede em venda online.
            "objetivo": "loja" if tipo in TIPOS_LOCAIS else "site",
        })
    return saida


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


def coletar_perfil_google(chave_api):
    """Google Perfil da Empresa (Maps + Busca local) — o canal ORGANICO que
    traz gente ate a loja.

    Por que importa: e o canal que o painel nao via. Ele nao gasta midia e nao
    vende no site — converte em telefone tocando e gente pedindo rota, coisas
    que nenhuma outra fonte registra. Sem isso, todo o custo do Google era
    cobrado contra venda online e este canal ficava invisivel.

    Guarda serie diaria (12 meses) e as palavras que as pessoas digitaram —
    essas valem tanto quanto os numeros: dizem como o cliente PROCURA a loja.
    """
    linhas = _windsor(chave_api, "google_my_business",
                      "date,impressions,impressions_mobile_maps,"
                      "impressions_mobile_search,impressions_desktop_maps,"
                      "impressions_desktop_search,call_clicks,website_clicks,"
                      "direction_requests", "last_12m")
    if not linhas:
        return None

    serie = {}
    for x in linhas:
        dia = x.get("date")
        if not dia:
            continue
        serie[dia] = {
            "impressoes": int(float(x.get("impressions") or 0)),
            "maps_celular": int(float(x.get("impressions_mobile_maps") or 0)),
            "busca_celular": int(float(x.get("impressions_mobile_search") or 0)),
            "maps_pc": int(float(x.get("impressions_desktop_maps") or 0)),
            "busca_pc": int(float(x.get("impressions_desktop_search") or 0)),
            "ligacoes": int(float(x.get("call_clicks") or 0)),
            "cliques_site": int(float(x.get("website_clicks") or 0)),
            "rotas": int(float(x.get("direction_requests") or 0)),
        }

    termos = []
    try:
        kw = _windsor(chave_api, "google_my_business",
                      "search_keyword,search_keyword_value", "last_12m")
        vistos = {}
        for x in kw:
            t = (x.get("search_keyword") or "").strip()
            if not t:
                continue
            vistos[t] = vistos.get(t, 0) + int(float(x.get("search_keyword_value") or 0))
        termos = [{"termo": t, "buscas": n}
                  for t, n in sorted(vistos.items(), key=lambda kv: -kv[1])[:40]]
    except Exception:
        pass

    avaliacoes = None
    try:
        rv = _windsor(chave_api, "google_my_business",
                      "review_total_count,review_average_rating_total", "last_12m")
        if rv:
            avaliacoes = {"total": int(float(rv[0].get("review_total_count") or 0)),
                          "nota": round(float(rv[0].get("review_average_rating_total") or 0), 1)}
    except Exception:
        pass

    return {"serie_dia": serie, "termos": termos, "avaliacoes": avaliacoes}


def sincronizar_perfil(ler_chave, ler_atual, gravar, log=print):
    """Rodada do Perfil da Empresa. Chave propria (perfil_google) porque o dado
    nao e midia paga — nao entra no investimento, e sim num card proprio."""
    chave = ler_chave()
    if not chave:
        raise RuntimeError("sem chave do Windsor")
    novo = coletar_perfil_google(chave)
    if not novo:
        raise RuntimeError("Perfil da Empresa não respondeu (conta conectada?)")

    # A serie acumula entre importacoes: no plano basico so uma fonte fica
    # conectada por vez, entao o perfil pode passar semanas sem atualizar.
    anterior = (ler_atual() or {}).get("serie_dia") or {}
    novo["serie_dia"] = {**anterior, **novo["serie_dia"]}
    novo["gerado_em"] = datetime.now(FUSO).isoformat(timespec="seconds")
    gravar(novo)
    dias = novo["serie_dia"]
    lig = sum(d.get("ligacoes", 0) for d in dias.values())
    resumo = f"{len(dias)} dias | {lig:,} ligações | {len(novo['termos'])} termos"
    log(f"[perfil-google] {resumo}")
    return resumo


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
