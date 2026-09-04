# -*- coding: utf-8 -*-
"""Area `auditoria` do portal — rotas e helpers privados. Extraida do server.py em
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
    _achatar_canal,
    agora_br,
    app,
    calendar,
    carregar_vendas_todos,
    carregar_vendedores,
    date,
    exigir_admin,
    hashlib,
    hoje_br,
    jsonify,
    parse_dt_tolerante,
    request,
    resolver_pasta_dados,
    valor_liquido,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

STATUS_AUDITORIA = {
    "conferida": "Confere com o caixa",
    "divergente": "Não bate",
    "nao_achei": "Não encontrei no caixa",
}

# Peso de cada sinal na hora de decidir o que entra na amostra. Não é
# probabilidade de fraude — é "isto merece um olhar antes daquilo".
SINAIS_AUDITORIA = {
    "duplicata": ("Possível duplicata", 5),
    "lancada_tarde": ("Lançada dias depois da data", 4),
    "editada": ("Editada depois de criada", 3),
    "valor_alto": ("Entre as maiores do mês", 2),
    "fim_de_semana": ("Lançada em fim de semana", 1),
}

def _chave_sorteio(venda_id: str, mes: str) -> int:
    """Ordem estável: mesma venda, mesmo mês, mesma posição — sempre."""
    return int(hashlib.sha256(f"{mes}:{venda_id}".encode()).hexdigest()[:12], 16)

def carregar_auditoria() -> dict:
    return ler_json(resolver_pasta_dados() / "auditoria.json", None) or {}

def _sinais_da_venda(v, contagem_dup, corte_alto):
    sinais = []
    chave = (v["data"], (v.get("produto") or "").strip().lower(), round(v["valor"], 2))
    if contagem_dup.get(chave, 0) > 1:
        sinais.append("duplicata")
    criado = v.get("criado_em")
    if criado:
        try:
            dias = (parse_dt_tolerante(criado).date() - date.fromisoformat(v["data"])).days
            if dias > 3:
                sinais.append("lancada_tarde")
        except (ValueError, TypeError):
            pass
    # Só `editado_em` marca edição. O log de ações não guarda o id da venda,
    # então não dá pra cruzar de volta — inventar esse vínculo por produto e
    # valor acertaria umas e erraria outras.
    if v.get("editado_em"):
        sinais.append("editada")
    if corte_alto and v["valor"] >= corte_alto:
        sinais.append("valor_alto")
    if date.fromisoformat(v["data"]).weekday() >= 5:
        sinais.append("fim_de_semana")
    return sinais

@app.route("/api/admin/auditoria")
def api_admin_auditoria():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    # Periodo por data, nao por mes: conferencia raramente casa com o calendario
    # — e mais comum querer "a semana passada" ou "do dia 10 ao 20". `mes` ainda
    # e aceito pra nao quebrar link salvo.
    mes = request.args.get("mes") or ""
    de = request.args.get("de") or ""
    ate = request.args.get("ate") or ""
    if not (de and ate):
        base = mes or hoje_br().isoformat()[:7]
        ultimo = calendar.monthrange(int(base[:4]), int(base[5:7]))[1]
        de, ate = f"{base}-01", f"{base}-{ultimo:02d}"

    try:
        tamanho = max(5, min(100, int(request.args.get("tamanho") or 20)))
    except ValueError:
        tamanho = 20
    filtro_vendedor = request.args.get("vendedor") or ""
    busca = (request.args.get("busca") or "").strip().lower()

    vendedores = carregar_vendedores()
    nomes_vend = {vid: v["nome"] for vid, v in vendedores.items()}

    def casa_busca(v):
        """Procura no que a pessoa lembra: nome da peca, do carro, SKU, canal,
        vendedor — ou o valor. Numero digitado casa tanto por texto ("1.200")
        quanto por comparacao, entao '1200' acha 1.200,00 e 1.200,50."""
        if not busca:
            return True
        valor = float(v.get("valor") or 0)
        alvo = _achatar_canal(" ".join(str(x) for x in (
            v.get("produto"), v.get("sku"), v.get("canal"),
            # A grafia antiga do canal continua achavel: quem sempre digitou
            # "ITAU" nao pode perder a venda porque ela virou "Itaú".
            v.get("canal_original"),
            nomes_vend.get(v.get("vendedor_id"), ""), v.get("data"),
            # Tres jeitos de escrever o mesmo valor. Quem procura digita como le
            # na tela ("1.200,00"), como pensa ("1200") ou como o teclado dá.
            f"{valor:.2f}".replace(".", ","), f"{valor:,.2f}".replace(",", "."),
            f"{int(valor)}",
        ) if x))
        # Todas as palavras precisam aparecer (sem acento dos dois lados):
        # "farol sorento" nao traz farol de outro carro, "itau" acha "Itaú".
        return all(termo in alvo for termo in _achatar_canal(busca).split())

    todas = carregar_vendas_todos(vendedores)
    vendas = [{**v, "id": vid} for vid, v in todas.items()
              if v.get("tipo", "venda") == "venda" and de <= v["data"] <= ate
              and (not filtro_vendedor or v["vendedor_id"] == filtro_vendedor)
              and casa_busca(v)]
    if not vendas:
        return jsonify({
            "mes": mes, "de": de, "ate": ate, "busca": busca, "vazio": True,
            "modo": request.args.get("modo") or "amostra", "foco": "",
            "amostra": [], "revisadas": [], "total_lista": [],
            "rotulos": STATUS_AUDITORIA, "sinais": {k: x[0] for k, x in SINAIS_AUDITORIA.items()},
            "total": {"vendas": 0, "valor": 0.0},
            "devolucoes": {"qtd": 0, "total": 0, "parcial": 0, "valor_perdido": 0.0},
            "cobertura": {"conferidas": 0, "divergentes": 0, "valor_conferido": 0.0,
                          "pct_qtd": 0, "pct_valor": 0},
            "com_sinal": 0,
            "qualidade": {"sem_canal": 0, "sem_sku": 0, "sem_criado_em": 0},
            "nomes": {k: v["nome"] for k, v in vendedores.items()},
            "filtro": {"vendedor": filtro_vendedor, "tamanho": tamanho},
            "vendedores": [{"id": k, "nome": v["nome"]}
                           for k, v in sorted(vendedores.items(),
                                              key=lambda kv: kv[1]["nome"])]})

    contagem_dup = {}
    for v in vendas:
        chave = (v["data"], (v.get("produto") or "").strip().lower(), round(v["valor"], 2))
        contagem_dup[chave] = contagem_dup.get(chave, 0) + 1

    ordenados = sorted(vendas, key=lambda v: -v["valor"])
    corte_alto = ordenados[max(0, len(ordenados) // 10 - 1)]["valor"] if len(ordenados) >= 10 else None

    for v in vendas:
        v["sinais"] = _sinais_da_venda(v, contagem_dup, corte_alto)
        v["risco"] = sum(SINAIS_AUDITORIA[s][1] for s in v["sinais"])

    marcas = carregar_auditoria()
    for v in vendas:
        m = marcas.get(v["id"]) or {}
        v["status"] = m.get("status")
        v["obs"] = m.get("obs")
        v["conferida_em"] = m.get("em")

    # Quem tem sinal entra antes; dentro do mesmo risco, o sorteio estável
    # decide. Assim a amostra cobre o que chama atenção sem virar uma lista só
    # dos casos estranhos — venda normal também precisa ser conferida, senão a
    # auditoria não diz nada sobre o conjunto.
    ja_marcadas = [v for v in vendas if v["status"]]
    candidatas = [v for v in vendas if not v["status"]]
    candidatas.sort(key=lambda v: (-v["risco"], _chave_sorteio(v["id"], de + ate)))
    com_sinal = [v for v in candidatas if v["risco"] > 0]
    sem_sinal = [v for v in candidatas if v["risco"] == 0]

    metade = max(1, tamanho // 2)
    amostra = com_sinal[:metade] + sem_sinal[:tamanho - min(metade, len(com_sinal))]
    amostra.sort(key=lambda v: (-v["risco"], v["data"]))

    def enxuto(v):
        return {k: v.get(k) for k in ("id", "data", "produto", "valor", "canal", "sku",
                                      "vendedor_id", "criado_em", "sinais", "risco",
                                      "status", "obs", "conferida_em", "devolucao")}

    conferidas = [v for v in vendas if v["status"] == "conferida"]
    divergentes = [v for v in vendas if v["status"] in ("divergente", "nao_achei")]
    total_mes = round(sum(v["valor"] for v in vendas), 2)
    valor_conferido = round(sum(v["valor"] for v in conferidas), 2)

    # Modo total: a planilha inteira do mês, sem sorteio. Serve pra fechar o mês
    # de ponta a ponta; a amostra serve pra rodar rápido no meio do mês. Os dois
    # gravam no mesmo lugar, então o que for conferido num aparece no outro.
    #
    # `foco` é o clique num dos números do topo: mostra exatamente aquelas
    # vendas. Vem do servidor e não da tela porque no modo amostra a tela só tem
    # as vendas sorteadas — filtrar ali devolveria menos do que o número promete,
    # e um contador que não bate com a lista é pior do que não ter contador.
    FOCOS = {
        "conferidas": lambda v: v["status"] == "conferida",
        "divergentes": lambda v: v["status"] in ("divergente", "nao_achei"),
        "sinal": lambda v: v["risco"] > 0,
        # Devolucao: o dado ja existia na venda e nao tinha como procurar por
        # ele. Tres recortes porque sao tres perguntas diferentes no fechamento.
        "devolvidas": lambda v: bool(v.get("devolucao")),
        "devolvidas_total": lambda v: (v.get("devolucao") or {}).get("tipo") == "total",
        "devolvidas_parcial": lambda v: (v.get("devolucao") or {}).get("tipo") == "parcial",
    }
    foco = request.args.get("foco") or ""
    lista_total = None
    if foco in FOCOS:
        lista_total = [enxuto(v) for v in sorted(
            (x for x in vendas if FOCOS[foco](x)), key=lambda v: (v["data"], -v["valor"]))]
    elif request.args.get("modo") == "total":
        lista_total = [enxuto(v) for v in sorted(
            vendas, key=lambda v: (v["data"], -v["valor"]))]
    elif request.args.get("modo") == "pendentes":
        # O que ainda nao passou por ninguem, do maior valor pro menor. E a
        # pergunta de quem abre a tela no meio do mes: "o que falta conferir?".
        # Ordem por valor, nao por data: se o tempo acabar, o que ficou de fora
        # e o que menos importa.
        lista_total = [enxuto(v) for v in sorted(
            (x for x in vendas if not x["status"]),   # sem marca = ninguem olhou
            key=lambda v: -v["valor"])]

    return jsonify({
        "mes": mes,
        "de": de,
        "ate": ate,
        "busca": busca,
        "modo": request.args.get("modo") or "amostra",
        "foco": foco if foco in FOCOS else "",
        "total_lista": lista_total,
        "rotulos": STATUS_AUDITORIA,
        "sinais": {k: v[0] for k, v in SINAIS_AUDITORIA.items()},
        "vendedores": [{"id": k, "nome": v["nome"]}
                       for k, v in sorted(vendedores.items(), key=lambda kv: kv[1]["nome"])],
        "nomes": {k: v["nome"] for k, v in vendedores.items()},
        "filtro": {"vendedor": filtro_vendedor, "tamanho": tamanho},
        "total": {"vendas": len(vendas), "valor": total_mes},
        # Quanto voltou, e quanto disso e comissao que o vendedor nao ganha.
        # `valor_perdido` usa a mesma regra do calculo de comissao: devolucao
        # total tira a venda inteira, parcial tira so o pedaco devolvido.
        "devolucoes": {
            "qtd": sum(1 for v in vendas if v.get("devolucao")),
            "total": sum(1 for v in vendas
                         if (v.get("devolucao") or {}).get("tipo") == "total"),
            "parcial": sum(1 for v in vendas
                           if (v.get("devolucao") or {}).get("tipo") == "parcial"),
            "valor_perdido": round(sum(
                v["valor"] - valor_liquido(v) for v in vendas if v.get("devolucao")), 2),
        },
        "cobertura": {
            "conferidas": len(conferidas),
            "divergentes": len(divergentes),
            "valor_conferido": valor_conferido,
            "pct_qtd": round(100 * len(conferidas) / len(vendas), 1),
            "pct_valor": round(100 * valor_conferido / total_mes, 1) if total_mes else 0,
        },
        "com_sinal": len(com_sinal) + sum(1 for v in ja_marcadas if v["risco"] > 0),
        "qualidade": {
            "sem_canal": sum(1 for v in vendas if not (v.get("canal") or "").strip()),
            "sem_sku": sum(1 for v in vendas if not (v.get("sku") or "").strip()),
            "sem_criado_em": sum(1 for v in vendas if not v.get("criado_em")),
        },
        "amostra": [enxuto(v) for v in amostra],
        "revisadas": [enxuto(v) for v in sorted(ja_marcadas, key=lambda v: v.get("conferida_em") or "",
                                                reverse=True)[:40]],
    })

@app.route("/api/admin/auditoria/<venda_id>", methods=["POST"])
def api_admin_auditoria_marcar(venda_id):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    novo = (corpo.get("status") or "").strip()
    if novo and novo not in STATUS_AUDITORIA:
        return jsonify({"erro": "status inválido"}), 400

    # Só aceita venda que existe: id chutado viraria uma marca órfã que conta
    # como conferida e infla a cobertura.
    vendedores = carregar_vendedores()
    if venda_id not in carregar_vendas_todos(vendedores):
        return jsonify({"erro": "Venda não encontrada."}), 404

    marcas = carregar_auditoria()
    if not novo:
        marcas.pop(venda_id, None)
    else:
        marcas[venda_id] = {"status": novo,
                            "obs": (corpo.get("obs") or "").strip()[:400],
                            "em": agora_br().isoformat(timespec="seconds")}
    escrever_json(resolver_pasta_dados() / "auditoria.json", marcas)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# Avaliacoes do Google (gestor, 04/09/2026). O vendedor registra a avaliacao
# que o cliente deixou; aqui o gestor confere no perfil do Google se ela
# existe e e de cliente de verdade, e marca. So a validada paga os R$ 20 —
# igual a conferencia de venda, mas com o dinheiro dependendo da marca.
def _periodo_pedido():
    mes = request.args.get("mes") or ""
    de = request.args.get("de") or ""
    ate = request.args.get("ate") or ""
    if not (de and ate):
        base = mes or hoje_br().isoformat()[:7]
        ultimo = calendar.monthrange(int(base[:4]), int(base[5:7]))[1]
        de, ate = f"{base}-01", f"{base}-{ultimo:02d}"
    return de, ate

@app.route("/api/admin/avaliacoes")
def api_admin_avaliacoes():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    import nucleo as N
    de, ate = _periodo_pedido()
    filtro_vendedor = request.args.get("vendedor") or ""
    busca = _achatar_canal((request.args.get("busca") or "").strip())

    vendedores = carregar_vendedores()
    nomes = {vid: v["nome"] for vid, v in vendedores.items()}
    todas = carregar_vendas_todos(vendedores)
    marcas = N.carregar_validacao_avaliacoes()

    itens = []
    for rid, v in todas.items():
        if v.get("tipo") != "bonus" or not (de <= v["data"] <= ate):
            continue
        if filtro_vendedor and v.get("vendedor_id") != filtro_vendedor:
            continue
        m = marcas.get(rid) or {}
        item = {
            "id": rid, "data": v["data"], "cliente": v.get("produto") or "",
            "valor": float(v.get("valor") or 0),
            "vendedor_id": v.get("vendedor_id"),
            "status": N.status_avaliacao(rid, v, marcas),
            "obs": m.get("obs") or "", "conferida_em": m.get("em"),
            "nota_vendedor": v.get("obs") or "",
            "criado_em": v.get("criado_em"),
            # Sem `origem` = historico importado da planilha, ja pago fora do
            # portal. Aparece pra consulta, mas nao se marca.
            "importada": v.get("origem") != "avaliacao",
        }
        if busca:
            alvo = _achatar_canal(" ".join(x for x in (
                item["cliente"], nomes.get(item["vendedor_id"], ""), item["obs"],
                item["nota_vendedor"], item["data"]) if x))
            if not all(termo in alvo for termo in busca.split()):
                continue
        itens.append(item)
    ordem = {"pendente": 0, "validada": 1, "recusada": 2}
    itens.sort(key=lambda x: (ordem.get(x["status"], 9), x["data"]), reverse=False)
    itens.sort(key=lambda x: ordem.get(x["status"], 9))

    def conta(st):
        return [x for x in itens if x["status"] == st]

    # Bonus da Shopee no mesmo periodo, por vendedor: nao se confere (a venda
    # ja passa pela conferencia normal), mas e pago junto e o gestor precisa
    # ver o numero no mesmo lugar em que fecha os R$ 20 das avaliacoes.
    shopee = []
    for vid in sorted(vendedores, key=lambda x: vendedores[x]["nome"]):
        if filtro_vendedor and vid != filtro_vendedor:
            continue
        r = N.resumo_bonus(vid, de, ate, todas, marcas)["shopee"]
        if r["qtd"]:
            shopee.append({"vendedor_id": vid, "nome": nomes[vid],
                           "qtd": r["qtd"], "valor": r["valor"], "itens": r["itens"]})

    return jsonify({
        "de": de, "ate": ate, "busca": request.args.get("busca") or "",
        "itens": itens,
        "resumo": {
            "pendentes": len(conta("pendente")),
            "validadas": len(conta("validada")),
            "recusadas": len(conta("recusada")),
            "valor_validado": round(sum(x["valor"] for x in conta("validada")), 2),
            "valor_pendente": round(sum(x["valor"] for x in conta("pendente")), 2),
            "por_avaliacao": N.BONUS_AVALIACAO,
            "por_venda_shopee": N.BONUS_SHOPEE,
        },
        "shopee": shopee,
        "rotulos": N.STATUS_AVALIACAO,
        "nomes": nomes,
        "vendedores": [{"id": k, "nome": v["nome"]}
                       for k, v in sorted(vendedores.items(), key=lambda kv: kv[1]["nome"])],
    })

@app.route("/api/admin/avaliacoes/<rid>", methods=["POST"])
def api_admin_avaliacao_marcar(rid):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    import nucleo as N
    corpo = request.get_json(silent=True) or {}
    novo = (corpo.get("status") or "").strip()
    if novo and novo not in N.STATUS_AVALIACAO:
        return jsonify({"erro": "status inválido"}), 400
    todas = carregar_vendas_todos(carregar_vendedores())
    v = todas.get(rid)
    if not v or v.get("tipo") != "bonus":
        return jsonify({"erro": "Avaliação não encontrada."}), 404
    if v.get("origem") != "avaliacao":
        return jsonify({"erro": "Essa avaliação veio da planilha antiga e já foi paga; não precisa de conferência."}), 400
    marcas = N.carregar_validacao_avaliacoes()
    if not novo:
        marcas.pop(rid, None)
    else:
        marcas[rid] = {"status": novo,
                       "obs": (corpo.get("obs") or "").strip()[:400],
                       "em": agora_br().isoformat(timespec="seconds")}
    escrever_json(resolver_pasta_dados() / "avaliacoes.json", marcas)
    return jsonify({"ok": True})
