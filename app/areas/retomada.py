# -*- coding: utf-8 -*-
"""Area `retomada` do portal — rotas e helpers privados. Extraida do server.py em
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
    MODELOS_FILE,
    MODELOS_PADRAO,
    STATIC_DIR,
    _caminho_crm,
    _contagem_marcacoes,
    _resumo_retomada,
    agora_br,
    app,
    carregar_fila_retomada,
    carregar_modelos_msg,
    carregar_status_retomada,
    carregar_vendedores,
    exigir_admin,
    exigir_vendedor,
    hoje_br,
    jsonify,
    montar_mensagem,
    request,
    resolver_pasta_dados,
    send_from_directory,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

STATUS_RETOMADA = {
    "pendente": "Não chamei ainda",
    "chamei": "Chamei, sem resposta",
    "respondeu": "Respondeu",
    "vendeu": "Fechou venda",
    "perdido": "Não vai rolar",
}

# Quantos clientes a chamar aparecem por vez. O resto fica de fora da tela
# (nao e apagado) — fila gigante desanima e ninguem trabalha.
MAX_FILA_PENDENTES = 50

# Meta de contatos por dia. Fila grande so anda com alvo diario: o vendedor
# precisa saber quando pode parar. Chamou 10 fez o minimo, 20 bateu a meta.
META_CONTATOS_DIA = {"minimo": 10, "bom": 15, "meta": 20}

# Campos do cliente que a marcacao passa a carregar. Ate 03/09/2026 a marcacao
# guardava so {status, em}; quando a fila era remontada, o cliente saia da
# lista e a marcacao virava um id sem dono — 76 das 78 marcacoes do time
# sumiram da tela de uma vez, e o gestor achou que o historico tinha sido
# apagado. Nao tinha; so ninguem conseguia mais ler. Com o retrato aqui, a
# marcacao sobrevive a qualquer fila.
RETRATO = ("nome", "fone", "peca", "data", "canal", "prio", "link")

def _contatos_de_hoje(status: dict) -> int:
    """Quantos clientes o vendedor trabalhou hoje. Conta a marcacao, nao o
    cliente: se ele marcou e depois corrigiu, continua sendo um contato feito."""
    hoje = hoje_br().isoformat()
    return sum(1 for m in status.values() if (m.get("em") or "").startswith(hoje))

# Como cada situação aparece pro gestor na tela de edição.
ROTULOS_SITUACAO = {
    "fechar": "Já ia comprar",
    "foto": "Pediu foto ou vídeo",
    "compat": "Perguntou se serve no carro dele",
    "frete": "Perguntou frete ou prazo",
    "terceiro": "Ia confirmar com o mecânico",
    "preco": "Falou de preço ou desconto",
    "pensar": "Disse que ia pensar",
    "nosso_lado": "A conversa parou do nosso lado",
    "sumiu": "Respondemos tudo e ele sumiu",
    "nao_fechou": "Conversou e não fechou",
    "caro": "Achou caro",
}

@app.route("/follow-up")
@app.route("/retomada")   # endereco antigo, mantido pra nao quebrar link salvo
def pagina_retomada():
    return send_from_directory(STATIC_DIR, "retomada.html")

@app.route("/api/retomada/fila")
def api_retomada_fila():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    fila = carregar_fila_retomada(vendedor_id)
    if not fila:
        return jsonify({"sem_fila": True, "itens": [], "rotulos": STATUS_RETOMADA})
    status = carregar_status_retomada(vendedor_id)
    modelos = carregar_modelos_msg()
    itens = []
    for item in fila.get("itens", []):
        marca = status.get(item["sid"]) or {}
        itens.append({**item, "status": marca.get("status", "pendente"),
                      "marcado_em": marca.get("em"),
                      "msg": montar_mensagem(item, modelos)})
    # Pendente primeiro, e dentro dele prioridade ALTA sempre no topo — nesses a
    # conversa parou do nosso lado, ninguém disse não pro vendedor, e são os que
    # ele tem que chamar antes. Só depois a nota desempata. O que já foi
    # trabalhado desce mas continua na tela, pra ele corrigir a marcação ou
    # voltar num cliente que pediu pra chamar depois.
    itens.sort(key=lambda x: (x["status"] != "pendente", x["prio"] != "ALTA", -x["nota"]))

    # Uma fila de 98 nomes ninguem trabalha — vira lista morta. Entao a tela
    # mostra so os MAX_FILA_PENDENTES mais quentes: prioridade ALTA primeiro,
    # depois os mais recentes (conversa de 5 dias converte mais que a de 30) e
    # a nota da IA desempata. Quem ja foi trabalhado nunca some, pra ele poder
    # corrigir a marcacao. A fila inteira continua guardada; o corte e so aqui.
    pendentes = [i for i in itens if i["status"] == "pendente"]
    trabalhados = [i for i in itens if i["status"] != "pendente"]
    pendentes.sort(key=lambda x: (x["prio"] != "ALTA", x.get("dias", 999), -x["nota"]))
    cortados = max(0, len(pendentes) - MAX_FILA_PENDENTES)
    itens = pendentes[:MAX_FILA_PENDENTES] + trabalhados

    # Historico: o que ele marcou em clientes que JA SAIRAM da fila. Sem isto o
    # trabalho sumia da tela a cada remontagem. Mais recente primeiro.
    na_fila = {i["sid"] for i in fila.get("itens", [])}
    historico = sorted(
        [{"sid": sid, **{k: v for k, v in m.items() if k in RETRATO},
          "status": m.get("status"), "marcado_em": m.get("em")}
         for sid, m in status.items() if sid not in na_fila],
        key=lambda x: x.get("marcado_em") or "", reverse=True)

    return jsonify({
        "gerado_em": fila.get("gerado_em"),
        "de": fila.get("de"),
        "ate": fila.get("ate"),
        "itens": itens,
        "historico": historico,
        "resumo": _resumo_retomada(fila.get("itens", []), status),
        "rotulos": STATUS_RETOMADA,
        "limite_fila": MAX_FILA_PENDENTES,
        "fora_do_corte": cortados,
        "contatos_hoje": _contatos_de_hoje(status),
        "meta_contatos": META_CONTATOS_DIA,
    })

@app.route("/api/retomada/<sid>/status", methods=["POST"])
def api_retomada_status(sid):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    novo = (corpo.get("status") or "").strip()
    if novo not in STATUS_RETOMADA:
        return jsonify({"erro": "status inválido"}), 400
    fila = carregar_fila_retomada(vendedor_id) or {}
    itens = fila.get("itens", [])
    status = carregar_status_retomada(vendedor_id)
    item = next((i for i in itens if i["sid"] == sid), None)
    # Aceita cliente da fila DESTE vendedor ou do historico dele (marcacao que
    # ja existe): e assim que "chamei" vira "fechou" depois que o cliente saiu
    # da fila. Id que nao esta em nenhum dos dois continua barrado — senao um id
    # chutado entraria no arquivo e apareceria pro gestor como trabalho.
    if item is None and sid not in status:
        return jsonify({"erro": "Este cliente não está na sua fila."}), 404
    if novo == "pendente":
        status.pop(sid, None)
    else:
        retrato = {k: item.get(k) for k in RETRATO if item and item.get(k)}                   or {k: v for k, v in (status.get(sid) or {}).items() if k in RETRATO}
        status[sid] = {**retrato, "status": novo, "em": agora_br().isoformat()}
    escrever_json(_caminho_crm("status", vendedor_id), status)
    return jsonify({"ok": True, "resumo": _resumo_retomada(itens, status),
                    "contatos_hoje": _contatos_de_hoje(status)})

@app.route("/api/admin/retomada/modelos", methods=["GET", "POST"])
def api_admin_modelos_msg():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    if request.method == "GET":
        return jsonify({"modelos": carregar_modelos_msg(),
                        "padrao": MODELOS_PADRAO,
                        "rotulos": ROTULOS_SITUACAO})
    corpo = request.get_json(silent=True) or {}
    atual = carregar_modelos_msg()
    novo = {
        "saudacao": (corpo.get("saudacao") or "").strip() or atual["saudacao"],
        "saudacao_atraso": (corpo.get("saudacao_atraso") or "").strip() or atual["saudacao_atraso"],
        "corpo": {},
        "corpo_parecida": {},
    }
    # So aceita situacao que o codigo conhece: chave inventada viraria um modelo
    # que nunca e escolhido, e o gestor acharia que salvou.
    for bloco in ("corpo", "corpo_parecida"):
        enviado = corpo.get(bloco) or {}
        for chave in MODELOS_PADRAO[bloco]:
            texto = (enviado.get(chave) or "").strip()
            if texto:
                novo[bloco][chave] = texto
    escrever_json(resolver_pasta_dados() / f"{MODELOS_FILE}.json", novo)
    return jsonify({"ok": True, "modelos": carregar_modelos_msg()})

@app.route("/api/admin/retomada/resumo")
def api_admin_retomada_resumo():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    linhas, gerado_em, periodo = [], None, {}
    for vendedor_id, vendedor in sorted(carregar_vendedores().items(),
                                        key=lambda kv: kv[1]["nome"]):
        fila = carregar_fila_retomada(vendedor_id)
        if not fila:
            continue
        gerado_em = gerado_em or fila.get("gerado_em")
        periodo = periodo or {"de": fila.get("de"), "ate": fila.get("ate")}
        itens = fila.get("itens", [])
        status = carregar_status_retomada(vendedor_id)
        resumo = _resumo_retomada(itens, status)
        trabalhados = resumo["trabalhados"]
        # "Este mes" pela data da MARCACAO, nao da conversa: e quando o vendedor
        # trabalhou, que e o que uma bonificacao de follow-up premia.
        mes = _contagem_marcacoes(status, desde=hoje_br().strftime("%Y-%m-01"))
        linhas.append({
            "vendedor_id": vendedor_id,
            "nome": vendedor["nome"],
            **resumo,
            "mes": mes,
            # Quanto da fila ATUAL ja foi tocado. Nao pode passar de 100%: as
            # marcacoes de clientes antigos nao contam aqui, so no total.
            "pct_trabalhado": round(100 * sum(1 for i in itens if i["sid"] in status)
                                    / len(itens)) if itens else 0,
            # Entre os que ele chamou, quantos deram sinal de vida. É a medida
            # que interessa: percentual sobre a fila inteira mede só o quanto
            # ele avançou na lista, não se a abordagem funcionou.
            "pct_resposta": (round(100 * (resumo["respondeu"] + resumo["vendeu"])
                                   / trabalhados) if trabalhados else 0),
        })
    # Historico mes a mes, pela data da MARCACAO (quando o vendedor trabalhou):
    # quantos contatos, quantos responderam, fecharam, nao rolou — por pessoa.
    # E a base pra ver se o follow-up esta rendendo, mes contra mes (gestor,
    # 04/09/2026). Conta todas as marcacoes, inclusive de fila ja remontada.
    import nucleo as N
    historico = {}
    for vendedor_id, vendedor in carregar_vendedores().items():
        for m in carregar_status_retomada(vendedor_id).values():
            mes_m, st = (m.get("em") or "")[:7], m.get("status")
            if not mes_m or st not in N.STATUS_TRABALHADO:
                continue
            h = historico.setdefault(mes_m, {}).setdefault(vendedor_id, {
                "nome": vendedor["nome"], "trabalhados": 0,
                **{k: 0 for k in N.STATUS_TRABALHADO}})
            h[st] += 1
            h["trabalhados"] += 1
    return jsonify({"gerado_em": gerado_em, "periodo": periodo,
                    "vendedores": linhas, "rotulos": STATUS_RETOMADA,
                    "historico": [{"mes": mes_m, "vendedores": sorted(v.values(), key=lambda x: x["nome"])}
                                  for mes_m, v in sorted(historico.items(), reverse=True)]})
