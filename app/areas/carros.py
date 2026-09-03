# -*- coding: utf-8 -*-
"""Area `carros` do portal — rotas e helpers privados. Extraida do server.py em
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
    AREAS,
    PADROES_SETOR_INICIAL,
    TIPO_META,
    _achatar_canal,
    _mb_bruto,
    _mb_gravar,
    _rh_ler,
    _sem_acento_simples,
    agora_br,
    app,
    exigir_admin,
    exigir_area,
    hoje_br,
    jsonify,
    padroes_setor,
    parse_dt_tolerante,
    request,
    resolver_pasta_dados,
    uuid,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

def _carros_bruto():
    return ler_json(resolver_pasta_dados() / "carros_chegar.json", None) or {}

def _inicio_acompanhamento(carros):
    """Primeiro dia em que alguém registrou chegada ou agendamento. Antes disso
    a ausência de chegada não significa nada."""
    marcos = [c[campo] for c in carros for campo in ("chegada", "agendamento") if c.get(campo)]
    return min(marcos) if marcos else None

@app.route("/api/admin/metas-bonus/dia")
def api_mb_dia():
    """O dia como uma chamada: cada pessoa com o que ja foi lancado nessa data.
    E o que a tela mostra pra preencher todo mundo de uma vez."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    data = (request.args.get("data") or "").strip() or hoje_br().isoformat()
    dados = _mb_bruto()
    setores = {}
    for setor, tipo in TIPO_META.items():
        do_dia = {}
        for l in (dados["lancamentos"].get(tipo) or {}).values():
            if l.get("data") == data:
                pid = l.get("pessoa_id")
                do_dia[pid] = do_dia.get(pid, 0.0) + float(l.get("quantidade") or 0)
        setores[setor] = sorted((
            {"id": pid, "nome": p.get("nome") or "?",
             "meta": p.get("meta") or 0, "meta_bonus": p.get("meta_bonus") or 0,
             "quantidade": do_dia.get(pid) or ""}
            # Inativo (desligado ou de funcao trocada) sai da chamada, mas
            # continua no dicionario: o historico dele segue agregando.
            for pid, p in dados["pessoas"].get(setor, {}).items()
            if not p.get("inativo")),
            key=lambda x: x["nome"])
    veiculos = sorted((
        {"id": vid, "carro": v.get("carro"), "codigo": v.get("codigo"),
         "pecas": v.get("pecas") or 0}
        for vid, v in dados["veiculos"].items() if v.get("data") == data),
        key=lambda x: x["carro"] or "")
    return jsonify({"data": data, "setores": setores, "veiculos": veiculos})

@app.route("/api/admin/metas-bonus/pessoas", methods=["POST"])
def api_mb_pessoa_salvar():
    """Cria ou edita uma pessoa da chamada. Mudar de funcao nao move o
    historico: a entrada antiga vira inativa (os meses lancados continuam
    contando la) e nasce uma entrada ativa na funcao nova."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    nome = (corpo.get("nome") or "").strip()[:60]
    if not nome:
        return jsonify({"erro": "Informe o nome."}), 400
    setor = corpo.get("setor")
    if setor not in TIPO_META:
        return jsonify({"erro": "Função inválida."}), 400

    def num(v):
        try:
            return max(0.0, float(str(v or 0).replace(",", ".")))
        except ValueError:
            return 0.0
    meta, bonus = num(corpo.get("meta")), num(corpo.get("meta_bonus"))

    dados = _mb_bruto()
    pid = (corpo.get("id") or "").strip()
    setor_atual = next((st for st, gente in dados["pessoas"].items() if pid in gente), None)

    if not pid or not setor_atual:
        # Mesmo nome de alguem inativo nesta funcao? Entao e a mesma pessoa
        # voltando (ou um engano sendo desfeito): reativa e o historico volta
        # junto, em vez de nascer um duplicado sem passado.
        igual = next((x for x, p in dados["pessoas"].get(setor, {}).items()
                      if p.get("inativo") and _achatar_canal(p.get("nome") or "") == _achatar_canal(nome)),
                     None)
        if igual:
            dados["pessoas"][setor][igual].update(
                nome=nome, meta=meta, meta_bonus=bonus, inativo=False)
        else:
            dados["pessoas"].setdefault(setor, {})[uuid.uuid4().hex[:12]] = {
                "nome": nome, "meta": meta, "meta_bonus": bonus}
    elif setor_atual == setor:
        dados["pessoas"][setor][pid].update(nome=nome, meta=meta, meta_bonus=bonus)
    else:
        antigo = dados["pessoas"][setor_atual][pid]
        tipo_antigo = TIPO_META[setor_atual]
        tem_historico = any(l.get("pessoa_id") == pid
                            for l in (dados["lancamentos"].get(tipo_antigo) or {}).values())
        if tem_historico:
            antigo["nome"] = nome
            antigo["inativo"] = True
        else:
            dados["pessoas"][setor_atual].pop(pid)
        dados["pessoas"].setdefault(setor, {})[pid] = {
            "nome": nome, "meta": meta, "meta_bonus": bonus}
    _mb_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/metas-bonus/pessoas/<setor>/<pid>", methods=["DELETE"])
def api_mb_pessoa_remover(setor, pid):
    """Tira da chamada. So apaga de verdade quem nunca lancou nada — apagar
    alguem com historico faria os meses antigos dele sumirem do painel."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    pessoa = dados["pessoas"].get(setor, {}).get(pid)
    if not pessoa:
        return jsonify({"erro": "Pessoa não encontrada."}), 404
    tipo = TIPO_META[setor]
    tem_historico = any(l.get("pessoa_id") == pid
                        for l in (dados["lancamentos"].get(tipo) or {}).values())
    if tem_historico:
        pessoa["inativo"] = True
        modo = "inativada"
    else:
        dados["pessoas"][setor].pop(pid)
        modo = "apagada"
    _mb_gravar(dados)
    return jsonify({"ok": True, "modo": modo})

@app.route("/api/admin/metas-bonus/dia", methods=["POST"])
def api_mb_dia_salvar():
    """Grava o dia inteiro de uma vez. Upsert por (pessoa, data): o campo da
    tela E o valor do dia — vazio ou zero apaga, numero substitui. Assim a
    grade pode ser corrigida e salva de novo sem duplicar nada."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    data = (corpo.get("data") or "").strip()
    try:
        parse_dt_tolerante(data)
    except Exception:
        return jsonify({"erro": "Data inválida."}), 400
    itens = corpo.get("itens")
    if not isinstance(itens, list):
        return jsonify({"erro": "Nada para salvar."}), 400

    dados = _mb_bruto()
    carimbo = agora_br().isoformat(timespec="seconds")
    gravados = apagados = 0
    for item in itens:
        tipo = item.get("tipo")
        if tipo not in ("anuncio", "cadastro"):
            return jsonify({"erro": f"Tipo inválido: {tipo}"}), 400
        setor = next(st for st, t in TIPO_META.items() if t == tipo)
        pid = (item.get("pessoa_id") or "").strip()
        if pid not in dados["pessoas"].get(setor, {}):
            return jsonify({"erro": "Pessoa desconhecida."}), 400
        bruto_qtd = str(item.get("quantidade") or "").strip().replace(",", ".")
        try:
            quantidade = float(bruto_qtd) if bruto_qtd else 0.0
        except ValueError:
            nome = dados["pessoas"][setor][pid].get("nome") or "?"
            return jsonify({"erro": f"Quantidade inválida para {nome}."}), 400
        if quantidade < 0:
            nome = dados["pessoas"][setor][pid].get("nome") or "?"
            return jsonify({"erro": f"Quantidade negativa para {nome}."}), 400

        lanc = dados["lancamentos"].setdefault(tipo, {})
        havia = [lid for lid, l in lanc.items()
                 if l.get("pessoa_id") == pid and l.get("data") == data]
        for lid in havia:
            lanc.pop(lid)
        if quantidade > 0:
            lanc[uuid.uuid4().hex[:12]] = {
                "pessoa_id": pid, "data": data, "quantidade": quantidade,
                "lancado_em": carimbo,
            }
            gravados += 1
        elif havia:
            apagados += 1
    _mb_gravar(dados)
    return jsonify({"ok": True, "gravados": gravados, "apagados": apagados})

@app.route("/api/admin/metas-bonus/lancamentos", methods=["POST"])
def api_mb_lancar():
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    tipo = corpo.get("tipo")
    if tipo not in ("anuncio", "cadastro"):
        return jsonify({"erro": "Tipo inválido."}), 400
    dados = _mb_bruto()
    setor = next(st for st, t in TIPO_META.items() if t == tipo)
    pessoa_id = (corpo.get("pessoa_id") or "").strip()
    if pessoa_id not in dados["pessoas"].get(setor, {}):
        return jsonify({"erro": "Escolha a pessoa."}), 400
    data = (corpo.get("data") or "").strip() or hoje_br().isoformat()
    try:
        parse_dt_tolerante(data)
    except Exception:
        return jsonify({"erro": "Data inválida."}), 400
    try:
        quantidade = float(str(corpo.get("quantidade")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"erro": "Quantidade inválida."}), 400
    if quantidade <= 0:
        return jsonify({"erro": "Quantidade deve ser maior que zero."}), 400

    dados["lancamentos"].setdefault(tipo, {})[uuid.uuid4().hex[:12]] = {
        "pessoa_id": pessoa_id, "data": data, "quantidade": quantidade,
        "lancado_em": agora_br().isoformat(timespec="seconds"),
    }
    _mb_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/metas-bonus/lancamentos/<tipo>/<lid>", methods=["DELETE"])
def api_mb_apagar(tipo, lid):
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    if lid not in dados["lancamentos"].get(tipo, {}):
        return jsonify({"erro": "Lançamento não encontrado."}), 404
    dados["lancamentos"][tipo].pop(lid)
    _mb_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/metas-bonus/veiculos", methods=["POST"])
def api_mb_veiculo():
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    carro = (corpo.get("carro") or "").strip()
    if not carro:
        return jsonify({"erro": "Informe o carro."}), 400
    data = (corpo.get("data") or "").strip() or hoje_br().isoformat()
    try:
        parse_dt_tolerante(data)
    except Exception:
        return jsonify({"erro": "Data inválida."}), 400
    # Pecas pode ficar em branco: o carro chega hoje e a contagem sai dias
    # depois, quando termina a desmontagem. Zero = "a contar"; preenche-se
    # depois na propria lista (PUT abaixo). Antes a tela exigia o numero na
    # hora, e o carro so era lancado quando ja estava tudo contado — ou nunca.
    bruto = str(corpo.get("pecas") or "").strip().replace(",", ".")
    try:
        pecas = float(bruto) if bruto else 0.0
    except ValueError:
        return jsonify({"erro": "Quantidade de peças inválida."}), 400
    if pecas < 0:
        return jsonify({"erro": "Peças não pode ser negativo."}), 400

    dados = _mb_bruto()
    dados["veiculos"][uuid.uuid4().hex[:12]] = {
        "data": data, "carro": carro[:80],
        "codigo": (corpo.get("codigo") or "").strip()[:20], "pecas": pecas,
        "lancado_em": agora_br().isoformat(timespec="seconds"),
    }
    _mb_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/metas-bonus/veiculos/<vid>", methods=["PUT"])
def api_mb_veiculo_editar(vid):
    """Atualiza a contagem de pecas (e, se vier, carro/codigo) de um veiculo
    ja lancado — e assim que o "a contar" vira numero."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    v = dados["veiculos"].get(vid)
    if not v:
        return jsonify({"erro": "Veículo não encontrado."}), 404
    corpo = request.get_json(silent=True) or {}
    if "pecas" in corpo:
        bruto = str(corpo.get("pecas") or "").strip().replace(",", ".")
        try:
            pecas = float(bruto) if bruto else 0.0
        except ValueError:
            return jsonify({"erro": "Quantidade de peças inválida."}), 400
        if pecas < 0:
            return jsonify({"erro": "Peças não pode ser negativo."}), 400
        v["pecas"] = pecas
    if (corpo.get("carro") or "").strip():
        v["carro"] = corpo["carro"].strip()[:80]
    if "codigo" in corpo:
        v["codigo"] = (corpo.get("codigo") or "").strip()[:20]
    if (corpo.get("data") or "").strip():
        data = corpo["data"].strip()
        try:
            parse_dt_tolerante(data)
        except Exception:
            return jsonify({"erro": "Data inválida."}), 400
        v["data"] = data
    v["editado_em"] = agora_br().isoformat(timespec="seconds")
    _mb_gravar(dados)
    return jsonify({"ok": True, "pecas": v["pecas"]})

@app.route("/api/admin/metas-bonus/meta-veiculos", methods=["POST"])
def api_mb_meta_veiculos():
    """Meta e bonus de pecas desmontadas no mes (na planilha, "Pç Grande")."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    dados = _mb_bruto()
    novo = dict(dados["meta_veiculos"])
    # Desmontagem se mede por CARROS no mes (regra do gestor, 03/09/2026);
    # pecas e a medida secundaria. Os quatro campos sao opcionais: o que nao
    # vier no corpo fica como esta.
    for campo in ("meta", "meta_bonus", "meta_carros", "meta_carros_bonus"):
        if campo not in corpo:
            continue
        bruto = str(corpo.get(campo) or "").strip().replace(",", ".")
        try:
            novo[campo] = float(bruto) if bruto else 0.0
        except ValueError:
            return jsonify({"erro": f"Valor inválido em {campo}."}), 400
    dados["meta_veiculos"] = novo
    _mb_gravar(dados)
    return jsonify({"ok": True, **novo})

# ---- Saldo de bonus ----------------------------------------------------
# Regra do gestor (03/09/2026): a cada 50 unidades ACIMA DA META (anuncios ou
# cadastros) a pessoa recebe R$ 50. O pagamento do mes leva os multiplos de
# 50; o que sobra (ex.: 55 acima -> paga 50, sobram 5) fica de credito, em
# unidades, pro mes seguinte. E acumulativo: mes nao pago continua somando.
PASSO_BONUS = 50.0


def _mb_saldos(dados: dict, mes: str) -> list:
    base = dados["saldos"].get("base") or {}
    pagos = dados["saldos"].get("pagamentos") or {}
    # Mes de partida: tudo ate ele esta acertado (a planilha antiga era a
    # verdade ate agosto/26). Sem isso, quem nao tinha credito na planilha
    # aparecia com excedente de 2025 como "a pagar".
    inicio_geral = dados["saldos"].get("inicio") or "0000-00"
    producao = {}
    for setor, tipo in TIPO_META.items():
        for l in (dados["lancamentos"].get(tipo) or {}).values():
            k = (setor, l.get("pessoa_id"), (l.get("data") or "")[:7])
            producao[k] = producao.get(k, 0.0) + float(l.get("quantidade") or 0)
    pago_de = {(p["setor"], p["pessoa_id"], p["mes"]): (pgid, p) for pgid, p in pagos.items()}

    linhas = []
    for setor, gente in dados["pessoas"].items():
        for pid, p in gente.items():
            meta = float(p.get("meta") or 0)
            b = base.get(f"{setor}:{pid}") or {}
            inicio = b.get("mes") or inicio_geral
            carry = float(b.get("unidades") or 0)
            meses = sorted({m for (s, pp, m) in producao if s == setor and pp == pid and inicio < m <= mes})
            if mes not in meses and (mes > inicio):
                meses.append(mes)   # mes sem producao ainda: mostra o credito que ja tem
            anterior = producao_mes = acima = acumulado = 0.0
            pago = None
            for m in meses:
                total = producao.get((setor, pid, m), 0.0)
                acima_m = max(0.0, total - meta) if meta else 0.0
                anterior = carry
                acumulado = carry + acima_m
                pg = pago_de.get((setor, pid, m))
                if pg:
                    carry = max(0.0, acumulado - float(pg[1].get("unidades") or 0))
                else:
                    carry = acumulado   # nao pago: segue acumulando inteiro
                if m == mes:
                    producao_mes, acima, pago = total, acima_m, (pg and {"id": pg[0], **pg[1]})
            if not meses or (producao_mes <= 0 and acumulado <= 0):
                continue
            a_pagar = (float(pago["valor"]) if pago
                       else int(acumulado // PASSO_BONUS) * PASSO_BONUS)
            linhas.append({
                "setor": setor, "pessoa_id": pid, "nome": p.get("nome") or "?",
                "inativo": bool(p.get("inativo")), "meta": meta,
                "saldo_anterior": anterior, "producao": producao_mes, "acima": acima,
                "acumulado": acumulado, "a_pagar": a_pagar,
                "sobra": (acumulado - float(pago["unidades"])) if pago else acumulado - a_pagar,
                "pago": pago,
            })
    linhas.sort(key=lambda x: (x["setor"], -x["acumulado"], x["nome"]))
    return linhas


@app.route("/api/admin/metas-bonus/saldos")
def api_mb_saldos():
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    mes = (request.args.get("mes") or hoje_br().isoformat()[:7]).strip()
    dados = _mb_bruto()
    return jsonify({"mes": mes, "passo": PASSO_BONUS, "linhas": _mb_saldos(dados, mes)})


@app.route("/api/admin/metas-bonus/saldos/pagar", methods=["POST"])
def api_mb_saldo_pagar():
    """Marca o mes da pessoa como pago: leva os multiplos de 50 acumulados;
    a sobra vira credito do mes seguinte."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    setor, pid, mes = corpo.get("setor"), corpo.get("pessoa_id"), (corpo.get("mes") or "").strip()
    dados = _mb_bruto()
    if setor not in dados["pessoas"] or pid not in dados["pessoas"][setor]:
        return jsonify({"erro": "Pessoa desconhecida."}), 400
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        return jsonify({"erro": "Mês inválido."}), 400
    linha = next((x for x in _mb_saldos(dados, mes) if x["setor"] == setor and x["pessoa_id"] == pid), None)
    if not linha:
        return jsonify({"erro": "Nada acumulado para essa pessoa neste mês."}), 400
    if linha["pago"]:
        return jsonify({"erro": "Este mês já está marcado como pago."}), 400
    valor = int(linha["acumulado"] // PASSO_BONUS) * PASSO_BONUS
    if valor <= 0:
        return jsonify({"erro": "Ainda não chegou a 50 acima da meta — nada a pagar."}), 400
    dados["saldos"].setdefault("pagamentos", {})[uuid.uuid4().hex[:12]] = {
        "setor": setor, "pessoa_id": pid, "mes": mes, "valor": valor, "unidades": valor,
        "pago_em": agora_br().isoformat(timespec="seconds"),
    }
    _mb_gravar(dados)
    return jsonify({"ok": True, "valor": valor, "sobra": linha["acumulado"] - valor})


@app.route("/api/admin/metas-bonus/saldos/pagar/<pgid>", methods=["DELETE"])
def api_mb_saldo_desfazer(pgid):
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    if pgid not in (dados["saldos"].get("pagamentos") or {}):
        return jsonify({"erro": "Pagamento não encontrado."}), 404
    dados["saldos"]["pagamentos"].pop(pgid)
    _mb_gravar(dados)
    return jsonify({"ok": True})


@app.route("/api/admin/metas-bonus/saldos/base", methods=["POST"])
def api_mb_saldo_base():
    """Credito inicial (unidades) que a pessoa trazia quando o painel assumiu
    — o "Saldo individual" da planilha antiga. `mes` = ultimo mes ja acertado."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    setor, pid, mes = corpo.get("setor"), corpo.get("pessoa_id"), (corpo.get("mes") or "").strip()
    dados = _mb_bruto()
    if setor not in dados["pessoas"] or pid not in dados["pessoas"][setor]:
        return jsonify({"erro": "Pessoa desconhecida."}), 400
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        return jsonify({"erro": "Mês inválido."}), 400
    try:
        unidades = float(str(corpo.get("unidades") or "0").replace(",", "."))
    except ValueError:
        return jsonify({"erro": "Unidades inválidas."}), 400
    dados["saldos"].setdefault("base", {})[f"{setor}:{pid}"] = {"mes": mes, "unidades": max(0.0, unidades)}
    _mb_gravar(dados)
    return jsonify({"ok": True})


@app.route("/api/admin/metas-bonus/veiculos/<vid>", methods=["DELETE"])
def api_mb_veiculo_apagar(vid):
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    if vid not in dados["veiculos"]:
        return jsonify({"erro": "Veículo não encontrado."}), 404
    dados["veiculos"].pop(vid)
    _mb_gravar(dados)
    return jsonify({"ok": True})

@app.route("/api/admin/metas-bonus/recentes")
def api_mb_recentes():
    """Ultimos lancamentos, pra conferir e desfazer erro de digitacao."""
    if not exigir_area("metabonus"):
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _mb_bruto()
    nomes = {pid: p.get("nome") or "?"
             for gente in dados["pessoas"].values() for pid, p in gente.items()}
    itens = []
    for tipo, lanc in dados["lancamentos"].items():
        for lid, l in lanc.items():
            itens.append({"id": lid, "tipo": tipo, "data": l.get("data"),
                          "nome": nomes.get(l.get("pessoa_id"), "?"),
                          "quantidade": l.get("quantidade"),
                          "ordem": l.get("lancado_em") or l.get("data") or ""})
    for vid, v in dados["veiculos"].items():
        itens.append({"id": vid, "tipo": "veiculo", "data": v.get("data"),
                      "nome": v.get("carro"), "codigo": v.get("codigo"),
                      "quantidade": v.get("pecas"),
                      "ordem": v.get("lancado_em") or v.get("data") or ""})
    itens.sort(key=lambda x: x["ordem"], reverse=True)
    return jsonify({"itens": itens[:12]})

@app.route("/api/admin/padroes-setor")
def api_admin_padroes_setor():
    """O padrao de cada setor, e quais setores o RH conhece hoje."""
    if not exigir_admin():
        return jsonify({"erro": "Nao autenticado."}), 401
    setores = sorted({(c.get("setor") or "").strip()
                      for c in (_rh_ler("colaboradores") or {}).values()
                      if (c.get("setor") or "").strip()})
    padroes = padroes_setor()
    return jsonify({
        "setores": [{"setor": s_, "areas": padroes.get(_sem_acento_simples(s_), [])}
                    for s_ in setores],
        "todas_areas": AREAS,
    })

@app.route("/api/admin/padroes-setor", methods=["POST"])
def api_admin_salvar_padroes_setor():
    """Guarda o padrao de UM setor. Um de cada vez, pra dois gestores mexendo
    ao mesmo tempo nao apagarem o trabalho um do outro."""
    if not exigir_admin():
        return jsonify({"erro": "Nao autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    setor = (corpo.get("setor") or "").strip()
    if not setor:
        return jsonify({"erro": "Informe o setor."}), 400
    atual = ler_json(resolver_pasta_dados() / "padroes_setor.json", None)
    if not isinstance(atual, dict) or not atual:
        atual = dict(PADROES_SETOR_INICIAL)
    atual[setor] = [a for a in (corpo.get("areas") or []) if a in AREAS]
    escrever_json(resolver_pasta_dados() / "padroes_setor.json", atual)
    return jsonify({"ok": True, "setor": setor, "areas": atual[setor]})

@app.route("/api/admin/carros")
def api_admin_carros():
    if not exigir_area("carros"):
        return jsonify({"erro": "Não autenticado."}), 401

    bruto = _carros_bruto()
    carros = bruto.get("carros", [])
    if not carros:
        return jsonify({"sem_dados": True})

    inicio = _inicio_acompanhamento(carros)
    filtro_estado = request.args.get("estado") or ""
    filtro_leilao = request.args.get("leilao") or ""
    de = request.args.get("de") or ""
    ate = request.args.get("ate") or ""

    def dentro(c):
        if de and (not c["data"] or c["data"] < de):
            return False
        if ate and (not c["data"] or c["data"] > ate):
            return False
        if filtro_leilao and c["leilao"] != filtro_leilao:
            return False
        if filtro_estado and c["estado"] != filtro_estado:
            return False
        return True

    visiveis = [c for c in carros if dentro(c)]

    # Acompanhados = comprados depois que o controle de chegada passou a existir.
    acompanhados = [c for c in visiveis
                    if inicio and c["data"] and c["data"] >= inicio]
    antigos = [c for c in visiveis
               if not (inicio and c["data"] and c["data"] >= inicio)]

    def somar(lista):
        return {"qtd": len(lista),
                "valor": round(sum(c["valor"] or 0 for c in lista), 2)}

    pendentes = [c for c in acompanhados if c["estado"] != "chegou"]
    pendentes.sort(key=lambda c: -(c["dias_parado"] or 0))
    chegaram = [c for c in acompanhados if c["estado"] == "chegou"]

    tempos = sorted(c["dias_ate_chegar"] for c in chegaram
                    if c["dias_ate_chegar"] is not None)
    mediana = tempos[len(tempos) // 2] if tempos else None

    def agrupar(lista, campo):
        d = {}
        for c in lista:
            chave = c.get(campo) or "—"
            item = d.setdefault(chave, {"qtd": 0, "valor": 0.0, "pendentes": 0})
            item["qtd"] += 1
            item["valor"] += c["valor"] or 0
            if c["estado"] != "chegou":
                item["pendentes"] += 1
        return sorted(({campo: k, "qtd": v["qtd"], "valor": round(v["valor"], 2),
                        "pendentes": v["pendentes"]} for k, v in d.items()),
                      key=lambda x: -x["qtd"])

    return jsonify({
        "gerado_em": bruto.get("gerado_em"),
        "inicio_acompanhamento": inicio,
        "filtro": {"estado": filtro_estado, "leilao": filtro_leilao, "de": de, "ate": ate},
        "leiloes": sorted({c["leilao"] for c in carros if c["leilao"]}),
        "estados": ["comprado", "agendado", "chegou", "sem_situacao"],
        "acompanhados": {
            **somar(acompanhados),
            "pendentes": somar(pendentes),
            "chegaram": somar(chegaram),
            "mediana_dias": mediana,
        },
        "historico_sem_registro": somar([c for c in antigos if c["estado"] != "chegou"]),
        "lista_pendentes": pendentes,
        # A planilha inteira, nao so os pendentes: o painel respondia "o que
        # esta atrasado?" mas nao "cade o carro tal?". Ordem por compra, mais
        # recente em cima; a busca acontece na tela, que ja tem tudo em maos.
        "lista": sorted(visiveis, key=lambda c: (c["data"] or "", c["veiculo"] or ""),
                        reverse=True),
        "por_leilao": agrupar(acompanhados, "leilao"),
        "por_estado": agrupar(acompanhados, "estado"),
        "total_geral": somar(visiveis),
    })
