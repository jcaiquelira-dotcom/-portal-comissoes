# -*- coding: utf-8 -*-
"""Area `expedicao` do portal — rotas e helpers privados. Extraida do server.py em
03/09/2026 (Fase 4). Texto das funcoes identico ao original; o que e comum
vem do nucleo, importado nominalmente pra ninguem adivinhar de onde vem.
"""
import calendar
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from flask import (
    Flask, g, has_request_context, jsonify, redirect, request, send_file,
    send_from_directory, session,
)
from openpyxl import Workbook
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nucleo import (
    CONTAS_POR_CODIGO,
    CONTA_DESPESAS_GERAIS,
    FORMAS_DE_PAGAMENTO,
    PLANO_DE_CONTAS,
    SETORES_META,
    TETO_DESPESAS_GERAIS,
    _atd_pendentes,
    _atd_resolvidos,
    _mb_agregar,
    _mb_bruto,
    agora_br,
    app,
    calendar,
    carregar_vendedores,
    date,
    exigir_admin,
    exigir_area,
    exigir_vendedor,
    hoje_br,
    jsonify,
    mes_esta_fechado,
    perfil_de,
    request,
    resolver_pasta_dados,
    session,
    timedelta,
    uuid,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

FORMAS_PAGAMENTO = ["Pix", "Dinheiro", "Cartão", "Boleto", "Na entrega", "Faturado"]

STATUS_PEDIDO = {"aguardando": "Aguardando separação",
                 "separado": "Separado, aguardando retirada",
                 "liberado": "Liberado / entregue",
                 "cancelado": "Cancelado"}

def _exp_ler() -> dict:
    return ler_json(resolver_pasta_dados() / "expedicao_pedidos.json", None) or {}

def _exp_gravar(dados: dict) -> None:
    escrever_json(resolver_pasta_dados() / "expedicao_pedidos.json", dados)

def _exp_visivel(p: dict, dias=3) -> bool:
    """Pedido fechado some da fila depois de alguns dias; o aberto fica sempre.
    Sem isso a tela da expedição viraria histórico e ninguém acharia o que
    precisa separar agora."""
    if p.get("status") in ("aguardando", "separado"):
        return True
    corte = (agora_br() - timedelta(days=dias)).isoformat(timespec="seconds")
    return (p.get("liberado_em") or p.get("criado_em") or "") >= corte

def _exp_texto(v, limite=120) -> str:
    return str(v or "").strip()[:limite]

@app.route("/api/expedicao/pedidos")
def api_exp_listar():
    """A fila da expedição. Vendedor vê os próprios; expedição e gestor veem
    todos."""
    vid = exigir_vendedor()
    if not vid and not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    ve_tudo = bool(exigir_admin()) or (vid and perfil_de(vid) == "expedicao")
    nomes = {k: x["nome"] for k, x in carregar_vendedores().items()}
    itens = []
    for pid, p in _exp_ler().items():
        if not ve_tudo and p.get("criado_por") != vid:
            continue
        if not _exp_visivel(p):
            continue
        itens.append({**p, "id": pid,
                      "vendedor": nomes.get(p.get("criado_por"), "?"),
                      "liberado_por_nome": nomes.get(p.get("liberado_por"),
                                                     p.get("liberado_por") or "")})
    # Aguardando primeiro, e dentro disso o mais antigo no topo: a fila é FIFO,
    # quem chegou antes espera menos.
    ordem = {"aguardando": 0, "separado": 1, "liberado": 2, "cancelado": 3}
    itens.sort(key=lambda p: (ordem.get(p.get("status"), 9), p.get("criado_em") or ""))
    abertos = [p for p in itens if p["status"] in ("aguardando", "separado")]
    return jsonify({
        "pedidos": itens,
        "abertos": len(abertos),
        "nao_pagos_abertos": sum(1 for p in abertos if not p.get("pago")),
        "opcoes": {"pagamento": FORMAS_PAGAMENTO, "status": STATUS_PEDIDO},
        "sou_expedicao": ve_tudo,
    })

@app.route("/api/expedicao/pedidos", methods=["POST"])
def api_exp_criar():
    vid = exigir_vendedor()
    if not vid:
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    peca = _exp_texto(corpo.get("peca"), 200)
    if not peca:
        return jsonify({"erro": "Informe a peça."}), 400
    quem_retira = _exp_texto(corpo.get("quem_retira"), 80)
    if not quem_retira:
        return jsonify({"erro": "Informe quem vai retirar."}), 400
    forma = _exp_texto(corpo.get("forma_pagamento"), 30)
    if forma and forma not in FORMAS_PAGAMENTO:
        return jsonify({"erro": "Forma de pagamento inválida."}), 400
    try:
        valor = round(float(str(corpo.get("valor") or 0).replace(",", ".")), 2)
    except ValueError:
        return jsonify({"erro": "Valor inválido."}), 400

    dados = _exp_ler()
    pid = uuid.uuid4().hex[:12]
    dados[pid] = {
        "criado_em": agora_br().isoformat(timespec="seconds"),
        "criado_por": vid,
        "peca": peca,
        "sku": _exp_texto(corpo.get("sku"), 40),
        "cliente": _exp_texto(corpo.get("cliente"), 80),
        "quem_retira": quem_retira,
        "telefone_retira": _exp_texto(corpo.get("telefone_retira"), 30),
        "forma_pagamento": forma,
        "pago": bool(corpo.get("pago")),
        "valor": valor,
        "obs": _exp_texto(corpo.get("obs"), 300),
        "status": "aguardando",
    }
    _exp_gravar(dados)
    return jsonify({"ok": True, "id": pid})

@app.route("/api/expedicao/pedidos/<pid>", methods=["POST"])
def api_exp_atualizar(pid):
    """Muda o status ou marca como pago. Vendedor mexe só no próprio pedido e
    só enquanto ele não saiu; a expedição mexe em qualquer um."""
    vid = exigir_vendedor()
    eh_exp = bool(exigir_admin()) or (vid and perfil_de(vid) == "expedicao")
    if not vid and not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    dados = _exp_ler()
    p = dados.get(pid)
    if not p:
        return jsonify({"erro": "Pedido não encontrado."}), 404
    if not eh_exp and p.get("criado_por") != vid:
        return jsonify({"erro": "Esse pedido não é seu."}), 403

    corpo = request.get_json(silent=True) or {}
    if "pago" in corpo:
        p["pago"] = bool(corpo["pago"])
        p["pago_marcado_por"] = vid or "gestor"
        p["pago_marcado_em"] = agora_br().isoformat(timespec="seconds")

    novo = corpo.get("status")
    if novo:
        if novo not in STATUS_PEDIDO:
            return jsonify({"erro": "Status inválido."}), 400
        if novo in ("separado", "liberado") and not eh_exp:
            return jsonify({"erro": "Só a expedição pode separar ou liberar."}), 403
        p["status"] = novo
        if novo == "liberado":
            p["liberado_em"] = agora_br().isoformat(timespec="seconds")
            p["liberado_por"] = vid or "gestor"
            # Saiu sem pagamento: fica gravado no proprio pedido, nao so no log.
            p["liberado_sem_pagamento"] = not p.get("pago")
    _exp_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/expedicao/pedidos/<pid>", methods=["DELETE"])
def api_exp_apagar(pid):
    """Só quem criou (e só antes de sair) ou o gestor."""
    vid = exigir_vendedor()
    dados = _exp_ler()
    p = dados.get(pid)
    if not p:
        return jsonify({"erro": "Pedido não encontrado."}), 404
    if not exigir_admin():
        if p.get("criado_por") != vid:
            return jsonify({"erro": "Esse pedido não é seu."}), 403
        if p.get("status") == "liberado":
            return jsonify({"erro": "Pedido já liberado não pode ser apagado."}), 400
    dados.pop(pid)
    _exp_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/atendimento-alerta")
def api_admin_atendimento_alerta():
    """Quem esta esperando a loja responder agora. Alimentado de poucos em
    poucos minutos por scripts/monitorar_sem_resposta.py — nao depende da
    bolinha de nao-lida do Totalk, que o bot zera sozinho."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "atendimento_alerta.json", None)
    if not d:
        return jsonify({"sem_dados": True})
    resolvidos = _atd_resolvidos()
    conversas = _atd_pendentes(d.get("conversas") or [], resolvidos)
    # por_vendedor tambem precisa descontar os resolvidos: vinha do monitor,
    # calculado antes do filtro, e o gestor via um numero maior que o do portal
    # do proprio vendedor.
    por_vendedor = {}
    for vid, info in (d.get("por_vendedor") or {}).items():
        minhas = [c for c in conversas if c.get("vendedor_id") == vid]
        por_vendedor[vid] = {"nome": info.get("nome"), "total": len(minhas),
                             "critico": sum(1 for c in minhas if c["nivel"] == "critico")}
    d = {**d, "conversas": conversas, "total": len(conversas),
         "resolvidas": len(d.get("conversas") or []) - len(conversas),
         "por_vendedor": por_vendedor,
         "contagem": {n: sum(1 for c in conversas if c["nivel"] == n)
                      for n in ("critico", "urgente", "atencao")}}
    return jsonify(d)

# Custo da peca e imposto NAO existem em API nenhuma — sao decisao do gestor.
# Ficam aqui com o valor que ele definiu em 31/08/2026, e valem como percentual
# do faturamento porque num desmanche nao ha custo unitario de compra: o que se
# compra e o carro inteiro.
PARAMETROS_ML_PADRAO = {"custo_pct": 30.0, "imposto_pct": 11.0}

def parametros_ml() -> dict:
    salvo = ler_json(resolver_pasta_dados() / "parametros_ml.json", None)
    base = dict(PARAMETROS_ML_PADRAO)
    if isinstance(salvo, dict):
        for k in base:
            try:
                base[k] = round(float(salvo[k]), 2)
            except (KeyError, TypeError, ValueError):
                pass
    return base

@app.route("/api/admin/ml-faturamento")
def api_admin_ml_faturamento():
    """O que o ML cobra, por periodo, e a margem que sobra.

    Chama de MARGEM e nao de lucro de proposito: os 30% de custo sao uma regra
    de bolso, nao o custo real daquela peca. Chamar de lucro faria o numero
    parecer apurado quando ele e estimado.
    """
    if not exigir_area("painel"):
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "ml_faturamento.json", None)
    if not d or not (d.get("periodos") or {}):
        # Sem periodo nenhum ainda. Devolve o erro da ultima tentativa junto,
        # se houve: a tela precisa poder dizer "quebrou" e nao so "aguarde".
        return jsonify({"sem_dados": True, "parametros": parametros_ml(),
                        "erro": (d or {}).get("erro"),
                        "erro_em": (d or {}).get("erro_em")})
    d["parametros"] = parametros_ml()
    return jsonify(d)

@app.route("/api/admin/ml-faturamento/parametros", methods=["POST"])
def api_admin_ml_parametros():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    novo = parametros_ml()
    for k in PARAMETROS_ML_PADRAO:
        if k in corpo:
            try:
                v_ = round(float(corpo[k]), 2)
            except (TypeError, ValueError):
                return jsonify({"erro": f"Valor inválido em {k}."}), 400
            if not 0 <= v_ <= 100:
                return jsonify({"erro": "Percentual fora de 0 a 100."}), 400
            novo[k] = v_
    escrever_json(resolver_pasta_dados() / "parametros_ml.json", novo)
    return jsonify({"ok": True, **novo})

_FAT_RODANDO = None

@app.route("/api/admin/sincronizar-faturamento", methods=["POST"])
def api_admin_sincronizar_faturamento():
    """Dispara a coleta na hora. Ela e retomavel: o limitador do ML e apertado,
    entao cada disparo avanca o que der e guarda onde parou."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    # Em segundo plano, SEMPRE. A coleta espera 4 minutos entre paginas por
    # causa do limitador do ML — rodar dentro da requisicao estourava o timeout
    # do servidor e o botao respondia "Falhou" mesmo com a coleta indo bem.
    global _FAT_RODANDO
    if _FAT_RODANDO and _FAT_RODANDO.is_alive():
        return jsonify({"ok": True, "resumo": "Já tem uma coleta em andamento — "
                        "ela avança sozinha, pode fechar a tela."})
    try:
        import threading

        import coletor_faturamento_ml as _cf

        def ler_cred():
            return ler_json(resolver_pasta_dados() / "segredo_ml.json", None)

        def ler_atual():
            return ler_json(resolver_pasta_dados() / "ml_faturamento.json", None)

        def gravar(p):
            escrever_json(resolver_pasta_dados() / "ml_faturamento.json", p)

        def _roda():
            try:
                _cf.sincronizar(ler_cred, ler_atual, gravar)
            except Exception as e:
                print(f"[faturamento] disparo manual falhou: {e}")

        _FAT_RODANDO = threading.Thread(target=_roda, daemon=True)
        _FAT_RODANDO.start()
        return jsonify({"ok": True, "resumo": "Coleta iniciada. O ML libera devagar "
                        "(~4 min por página); o card atualiza sozinho quando terminar."})
    except Exception as e:
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 502

def valor_em_reais(bruto) -> float:
    """Converte o que foi digitado num numero. Levanta ValueError se nao der.

    Aceita "4.500,00", "4500,00", "4.500", "4500" e "R$ 4.500,00" — todos os
    jeitos que aparecem quando se copia valor de boleto, de extrato ou da
    propria planilha. Recusa o resto em vez de chutar.
    """
    t = str(bruto or "").strip()
    for lixo in ("R$", "r$", " ", "\u00a0"):
        t = t.replace(lixo, "")
    if not t:
        raise ValueError("vazio")
    if "," in t:
        # Virgula manda: ponto so pode ser milhar.
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") >= 1:
        # "1.234" e "12.345.678" sao milhar; "4.5" e "12.75" sao decimal. O que
        # separa e o tamanho de cada grupo depois do ponto: milhar tem 3.
        partes = t.split(".")
        if all(len(p) == 3 for p in partes[1:]) and partes[0].isdigit():
            t = "".join(partes)
    return round(float(t), 2)

def _arquivo_lancamentos():
    return resolver_pasta_dados() / "financeiro_lancamentos.json"

def carregar_lancamentos() -> dict:
    return ler_json(_arquivo_lancamentos(), None) or {}

def dre_dos_lancamentos(lancs: list) -> dict:
    """Agrupa lancamentos no mesmo formato que a importacao da planilha produz.

    Sair no formato existente e de proposito: a tela de DRE ja sabe desenhar
    {grupo: {rotulo: {valor, celulas}}}, e um formato novo obrigaria a manter
    dois desenhos que precisam concordar entre si.
    """
    grupos = {}
    for l in lancs:
        conta = CONTAS_POR_CODIGO.get(l.get("conta"))
        if not conta or conta["entrada"]:
            continue
        d = grupos.setdefault(conta["dre"], {})
        item = d.setdefault(conta["nome"], {"valor": 0.0, "celulas": []})
        item["valor"] = round(item["valor"] + float(l.get("valor") or 0), 2)
        item["celulas"].append(l.get("id", ""))
    return grupos

def mes_dos_lancamentos(lancs: list) -> dict:
    """Monta o registro de um mes a partir dos lancamentos, no mesmo formato
    que `importar_fluxo_caixa` grava — mesma razao de `dre_dos_lancamentos`."""
    entradas = saidas = 0.0
    for l in lancs:
        conta = CONTAS_POR_CODIGO.get(l.get("conta"))
        if not conta:
            continue
        valor = float(l.get("valor") or 0)
        if conta["entrada"]:
            entradas += valor
        else:
            saidas += valor
    entradas, saidas = round(entradas, 2), round(saidas, 2)
    return {
        "entradas": entradas,
        "saidas": saidas,
        "saldo": round(entradas - saidas, 2),
        "fonte": "portal",
        "lancamentos": len(lancs),
        "dre": dre_dos_lancamentos(lancs),
    }

def financeiro_consolidado() -> dict:
    """Planilha + lancamentos do portal, SEM somar um no outro.

    Mes com lancamento no portal e do portal; a planilha daquele mes vira
    `conferencia`, visivel na tela pra o gestor ver a diferenca em vez de ela
    sumir. Mes sem lancamento continua vindo da planilha, como sempre veio.
    """
    pacote = ler_json(resolver_pasta_dados() / "financeiro_fluxo.json", None) or {}
    meses = dict(pacote.get("meses") or {})

    por_mes = {}
    for lid, l in carregar_lancamentos().items():
        data = l.get("data") or ""
        if len(data) < 7:
            continue
        por_mes.setdefault(data[:7], []).append(dict(l, id=lid))

    for chave, lancs in por_mes.items():
        registro = mes_dos_lancamentos(lancs)
        antigo = meses.get(chave)
        if antigo:
            # A planilha nao e apagada: ela fica ao lado, pra o gestor ver que
            # os dois numeros existem e qual esta valendo.
            registro["conferencia"] = {
                "fonte": antigo.get("fonte"),
                "entradas": antigo.get("entradas"),
                "saidas": antigo.get("saidas"),
                "saldo": antigo.get("saldo"),
            }
        meses[chave] = registro

    pacote["meses"] = meses
    pacote["midia_real"] = midia_por_mes()
    return pacote

def midia_por_mes() -> dict:
    """Quanto o Meta e o Google cobraram de anuncio, por mes.

    Vai junto com o financeiro pra tela poder confrontar com o que foi lancado
    em "Midia paga". Nao e curiosidade: entre janeiro e agosto de 2026 sairam
    R$ 71.427 de anuncio e so R$ 22.436 apareceram no fluxo — o DRE enxerga um
    terco do que a empresa gastou pra vender. Enquanto isso nao fechar, nao ha
    como saber quanto custa trazer um cliente.
    """
    d = ler_json(resolver_pasta_dados() / "marketing_gasto.json", None) or {}
    fora = {}
    for dia, v in ((d.get("meta") or {}).get("serie_dia") or {}).items():
        mes = str(dia)[:7]
        valor = v.get("spend") if isinstance(v, dict) else v
        fora.setdefault(mes, {"meta": 0.0, "google": 0.0})["meta"] += float(valor or 0)
    for linha in (d.get("linhas") or []):
        mes = str(linha.get("data") or "")[:7]
        if len(mes) != 7:
            continue
        alvo = "google" if linha.get("fonte") == "google_ads" else "meta"
        fora.setdefault(mes, {"meta": 0.0, "google": 0.0})[alvo] += float(
            linha.get("spend") or 0)
    for mes, v in fora.items():
        v["meta"] = round(v["meta"], 2)
        v["google"] = round(v["google"], 2)
        v["total"] = round(v["meta"] + v["google"], 2)
    return fora

@app.route("/api/admin/plano-contas")
def api_admin_plano_contas():
    """O plano de contas, pra tela montar a lista. Fonte unica: a tela nao tem
    copia propria da lista, senao as duas divergem no primeiro ajuste."""
    if not exigir_area("lancamentos") and not exigir_area("dre") \
            and not exigir_area("fluxo_caixa"):
        return jsonify({"erro": "Nao autenticado."}), 401
    return jsonify({
        "plano": [{"grupo": b["grupo"], "dre": b["dre"],
                   "entrada": bool(b.get("entrada")),
                   "contas": [CONTAS_POR_CODIGO[c[0]] for c in b["contas"]]}
                  for b in PLANO_DE_CONTAS],
        "formas": FORMAS_DE_PAGAMENTO,
        "teto_gerais": TETO_DESPESAS_GERAIS,
        "conta_gerais": CONTA_DESPESAS_GERAIS,
    })

@app.route("/api/admin/lancamentos")
def api_admin_lancamentos():
    """Lancamentos de um mes, com o resumo que a tela usa pro teto de gerais."""
    if not exigir_area("lancamentos"):
        return jsonify({"erro": "Nao autenticado."}), 401
    mes = (request.args.get("mes") or hoje_br().isoformat()[:7])[:7]
    itens = [dict(l, id=lid) for lid, l in carregar_lancamentos().items()
             if (l.get("data") or "")[:7] == mes]
    itens.sort(key=lambda x: (x.get("data") or "", x.get("criado_em") or ""),
               reverse=True)

    entradas = saidas = gerais = 0.0
    for l in itens:
        conta = CONTAS_POR_CODIGO.get(l.get("conta"))
        if not conta:
            continue
        valor = float(l.get("valor") or 0)
        if conta["entrada"]:
            entradas += valor
        else:
            saidas += valor
            if conta["codigo"] == CONTA_DESPESAS_GERAIS:
                gerais += valor
    return jsonify({
        "mes": mes,
        "fechado": mes_esta_fechado(mes + "-01"),
        "lancamentos": itens,
        "resumo": {
            "entradas": round(entradas, 2),
            "saidas": round(saidas, 2),
            "saldo": round(entradas - saidas, 2),
            "gerais": round(gerais, 2),
            # Fracao das saidas, nao da receita: a pergunta e "quanto do que eu
            # gastei eu nao sei explicar", e receita nenhuma muda essa resposta.
            "gerais_pct": round(gerais / saidas * 100, 2) if saidas else 0.0,
            "teto_pct": round(TETO_DESPESAS_GERAIS * 100, 2),
        },
    })

@app.route("/api/admin/lancamentos", methods=["POST"])
def api_admin_criar_lancamento():
    if not exigir_area("lancamentos"):
        return jsonify({"erro": "Nao autenticado."}), 401
    body = request.get_json(silent=True) or {}

    conta = CONTAS_POR_CODIGO.get(str(body.get("conta") or "").strip())
    if not conta:
        return jsonify({"erro": "Escolha uma conta da lista."}), 400

    data = str(body.get("data") or "").strip()[:10]
    try:
        date.fromisoformat(data)
    except ValueError:
        return jsonify({"erro": "Data invalida."}), 400
    if mes_esta_fechado(data):
        return jsonify({"erro": "Esse mes ja foi fechado. Lance no mes "
                                "corrente ou reabra o fechamento."}), 400

    try:
        valor = valor_em_reais(body.get("valor"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Valor invalido. Use 4.500,00 ou 4500."}), 400
    if valor <= 0:
        return jsonify({"erro": "O valor precisa ser maior que zero."}), 400

    historico = str(body.get("historico") or "").strip()[:180]
    if not historico:
        return jsonify({"erro": "Escreva o historico — e onde entra o nome do "
                                "fornecedor e o que foi comprado."}), 400

    forma = str(body.get("forma") or "").strip()[:40]
    if forma and forma not in FORMAS_DE_PAGAMENTO:
        return jsonify({"erro": "Forma de pagamento invalida."}), 400

    lancs = carregar_lancamentos()
    lid = uuid.uuid4().hex[:12]
    lancs[lid] = {
        "data": data,
        "conta": conta["codigo"],
        "valor": valor,
        "historico": historico,
        "forma": forma,
        "criado_em": agora_br().isoformat(timespec="seconds"),
        "criado_por": session.get("vendedor_id") or "gestor",
    }
    escrever_json(_arquivo_lancamentos(), lancs)
    return jsonify({"ok": True, "id": lid, "conta": conta})

@app.route("/api/admin/lancamentos/<lid>", methods=["DELETE"])
def api_admin_apagar_lancamento(lid):
    if not exigir_area("lancamentos"):
        return jsonify({"erro": "Nao autenticado."}), 401
    lancs = carregar_lancamentos()
    atual = lancs.get(lid)
    if not atual:
        return jsonify({"erro": "Lancamento nao encontrado."}), 404
    if mes_esta_fechado(atual.get("data") or ""):
        return jsonify({"erro": "Mes fechado — nao da pra apagar."}), 400
    lancs.pop(lid)
    escrever_json(_arquivo_lancamentos(), lancs)
    return jsonify({"ok": True})

@app.route("/api/admin/financeiro")
def api_admin_financeiro():
    """Fluxo de caixa e DRE, do mesmo pacote.

    Um endpoint so porque as duas telas leem o MESMO mes: separar em dois
    convidaria a divergirem, e o valor deste modulo e justamente as duas contas
    fecharem uma na outra.
    """
    if not exigir_area("fluxo_caixa") and not exigir_area("dre"):
        return jsonify({"erro": "Nao autenticado."}), 401
    d = financeiro_consolidado()
    if not d or not (d.get("meses") or {}):
        return jsonify({"sem_dados": True})
    return jsonify(d)

@app.route("/api/admin/shopee-conta")
def api_admin_shopee_conta():
    """Serie mensal da Shopee (importar_shopee_stats). Endpoint separado do
    resumo porque o card dela e analise propria: mesmo quando o periodo
    filtrado nao tem Shopee, a evolucao historica continua na tela."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    # Duas lojas desde 02/09/2026. `loja` default 1 mantem o endereco antigo
    # funcionando pra qualquer coisa que ainda chame sem parametro.
    loja = (request.args.get("loja") or "1").strip()
    arquivo = "shopee_conta_2.json" if loja == "2" else "shopee_conta.json"
    d = ler_json(resolver_pasta_dados() / arquivo, None)
    if not d or not ((d.get("vendas") or {}).get("serie_mes")
                     or (d.get("vendas") or {}).get("serie_dia")):
        return jsonify({"sem_dados": True})
    return jsonify(d)

@app.route("/api/admin/metas-bonus")
def api_admin_metas_bonus():
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401

    dados_brutos = _mb_bruto()
    tem_bruto = any(dados_brutos["lancamentos"].values()) or dados_brutos["veiculos"]
    if tem_bruto:
        meses = _mb_agregar(dados_brutos)
    else:
        # Sem dado bruto, vale o agregado que o sincronizador antigo empurrou.
        bruto = ler_json(resolver_pasta_dados() / "metas_bonus.json", None) or {}
        meses = bruto.get("meses") or {}
    if not meses:
        return jsonify({"sem_dados": True})

    disponiveis = sorted(meses)
    mes = request.args.get("mes") or disponiveis[-1]
    if mes not in meses:
        mes = disponiveis[-1]
    atual = meses[mes]

    # Quantos bateram bonus mes a mes — e a serie que mostra se a meta esta
    # calibrada. Ninguem batendo nunca, ou todo mundo batendo sempre, sao os
    # dois jeitos de uma meta nao significar nada.
    historico = []
    for m in disponiveis:
        d = meses[m]
        pessoas = [p for linhas in d["setores"].values() for p in linhas]
        historico.append({
            "mes": m,
            "pessoas": len(pessoas),
            "na_meta": sum(1 for p in pessoas if p["bateu_meta"]),
            "no_bonus": sum(1 for p in pessoas if p["bateu_bonus"]),
            "pecas": d["veiculos"]["pecas"],
            "carros": d["veiculos"]["carros"],
        })

    ritmo = None
    hoje = hoje_br()
    if mes == hoje.isoformat()[:7]:
        dias = calendar.monthrange(hoje.year, hoje.month)[1]
        ritmo = {"dias_no_mes": dias, "dias_corridos": hoje.day,
                 "pct_do_mes": round(100 * hoje.day / dias)}

    # Comparativo dos ultimos 6 meses (calendario, terminando no mes escolhido):
    # producao por pessoa e por setor, carros e pecas. Pedido do gestor em
    # 03/09/2026: "so tem informacao atual, quero ver se caiu ou subiu".
    ano, mn = int(mes[:4]), int(mes[5:7])
    janela = []
    for k in range(5, -1, -1):
        a, m_ = ano, mn - k
        while m_ <= 0:
            a, m_ = a - 1, m_ + 12
        janela.append(f"{a:04d}-{m_:02d}")
    pessoas_cmp, setor_tot, veic_cmp = {}, {}, {}
    for m in janela:
        dm = meses.get(m)
        veic_cmp[m] = {"carros": dm["veiculos"]["carros"] if dm else 0,
                       "pecas": dm["veiculos"]["pecas"] if dm else 0}
        if not dm:
            continue
        for setor, linhas in dm["setores"].items():
            for p in linhas:
                reg = pessoas_cmp.setdefault((setor, p["nome"]), {
                    "setor": setor, "nome": p["nome"], "meta": p["meta"],
                    "meta_bonus": p["meta_bonus"], "por_mes": {}})
                reg["por_mes"][m] = p["total"]
                setor_tot.setdefault(setor, {})[m] = setor_tot.get(setor, {}).get(m, 0) + p["total"]
    comparativo = {
        "meses": janela,
        "pessoas": sorted(pessoas_cmp.values(),
                          key=lambda x: (x["setor"], -x["por_mes"].get(mes, 0), x["nome"])),
        "setores": setor_tot,
        "veiculos": veic_cmp,
    }

    return jsonify({
        "lancar": {
            "pessoas": {setor: sorted(({"id": pid, "nome": p.get("nome") or "?"}
                                       for pid, p in gente.items()), key=lambda x: x["nome"])
                        for setor, gente in dados_brutos["pessoas"].items()},
            "ativo": True,
        },
        "gerado_em": (agora_br().isoformat(timespec="seconds") if tem_bruto else bruto.get("gerado_em")),
        "mes": mes,
        "meses": disponiveis,
        "rotulos": SETORES_META,
        "setores": atual["setores"],
        "veiculos": atual["veiculos"],
        "historico": historico,
        "ritmo": ritmo,
        "comparativo": comparativo,
    })
