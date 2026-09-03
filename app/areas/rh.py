# -*- coding: utf-8 -*-
"""Area `rh` do portal — rotas e helpers privados. Extraida do server.py em
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
    _rh_ler,
    agora_br,
    app,
    carregar_vendedores,
    date,
    exigir_admin,
    hoje_br,
    jsonify,
    request,
    resolver_pasta_dados,
    uuid,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

# Setores como a empresa se organiza de fato, na ordem da planilha de folha.
SETORES_RH = ["Comercial", "Anúncios", "Cadastro", "Desmontagem", "Expedição",
              "Estoque", "Higienização", "Pátio", "Gerência", "Administrativo",
              "Marketing", "Outro"]

CONTRATOS_RH = ["CLT", "PJ", "Estágio", "Temporário", "Sócio"]

SITUACOES_RH = {"ativo": "Ativo", "afastado": "Afastado", "desligado": "Desligado"}

TIPOS_AUSENCIA = {"ferias": "Férias", "falta": "Falta", "atestado": "Atestado",
                  "licenca": "Licença", "folga": "Folga"}

TIPOS_DOCUMENTO = ["ASO", "CNH", "Contrato", "Certificação", "Exame periódico", "Outro"]

TIPOS_OCORRENCIA = {"elogio": "Elogio", "advertencia": "Advertência",
                    "feedback": "Conversa de feedback", "suspensao": "Suspensão",
                    "nota": "Anotação"}

RH_COLECOES = ("colaboradores", "ausencias", "documentos", "ocorrencias")

def _rh_gravar(nome: str, dados: dict) -> None:
    escrever_json(resolver_pasta_dados() / f"rh_{nome}.json", dados)

def _rh_texto(v, limite=200) -> str:
    return str(v or "").strip()[:limite]

def _rh_data(v) -> str:
    """Aceita só data ISO. Campo de data vazio é normal (nem todo mundo tem
    tudo preenchido); data mal formada não entra e vira vazio."""
    t = _rh_texto(v, 10)
    try:
        date.fromisoformat(t)
        return t
    except ValueError:
        return ""

def _rh_num(v):
    """Campo de dinheiro vazio é normal e vira None, não zero: zero diz
    "ganha zero", vazio diz "não sei"."""
    t = str(v if v is not None else "").strip()
    if t in ("", "None"):
        return None
    try:
        return round(float(t.replace(",", ".")), 2)
    except ValueError:
        return None

def _idade_ou_tempo(desde: str):
    """Anos completos entre uma data e hoje. Serve pra idade e pra tempo de
    casa, que é a mesma conta."""
    if not desde:
        return None
    try:
        d = date.fromisoformat(desde)
    except ValueError:
        return None
    hoje = hoje_br()
    anos = hoje.year - d.year - ((hoje.month, hoje.day) < (d.month, d.day))
    return max(0, anos)

def _dias_ate(quando: str):
    if not quando:
        return None
    try:
        return (date.fromisoformat(quando) - hoje_br()).days
    except ValueError:
        return None

@app.route("/api/admin/rh")
def api_admin_rh():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    colaboradores = _rh_ler("colaboradores")
    ausencias = _rh_ler("ausencias")
    documentos = _rh_ler("documentos")
    ocorrencias = _rh_ler("ocorrencias")
    hoje = hoje_br().isoformat()
    mes_atual = hoje[5:7]

    lista = []
    for cid, c in colaboradores.items():
        item = {**c, "id": cid}
        item["tempo_casa"] = _idade_ou_tempo(c.get("admissao"))
        item["idade"] = _idade_ou_tempo(c.get("nascimento"))
        # Aniversário do mês corrente, independente do ano de nascimento.
        item["faz_aniversario_no_mes"] = bool(c.get("nascimento")) and c["nascimento"][5:7] == mes_atual
        item["fora_hoje"] = any(
            a["colaborador_id"] == cid and a.get("de", "") <= hoje <= (a.get("ate") or a.get("de", ""))
            for a in ausencias.values())
        lista.append(item)
    # Ordena pelo apelido quando existe: e o nome que a lista mostra, e procurar
    # "Nego" na letra R nao ajuda ninguem.
    lista.sort(key=lambda c: ((c.get("situacao") or "ativo") != "ativo",
                              (c.get("apelido") or c.get("nome") or "").lower()))

    nomes = {cid: c.get("nome", "?") for cid, c in colaboradores.items()}

    def enfeitar(colecao, extra=None):
        saida = []
        for xid, x in colecao.items():
            item = {**x, "id": xid, "colaborador": nomes.get(x.get("colaborador_id"), "(removido)")}
            if extra:
                extra(item)
            saida.append(item)
        return saida

    lista_ausencias = enfeitar(ausencias)
    lista_ausencias.sort(key=lambda a: a.get("de") or "", reverse=True)

    def marcar_vencimento(d):
        d["dias"] = _dias_ate(d.get("vence_em"))
    lista_documentos = enfeitar(documentos, marcar_vencimento)
    # Vencido e a vencer primeiro: documento com prazo longo não precisa de
    # atenção, e ordenar por data joga justamente os urgentes pro fim.
    lista_documentos.sort(key=lambda d: (d["dias"] is None, d["dias"] if d["dias"] is not None else 0))

    lista_ocorrencias = enfeitar(ocorrencias)
    lista_ocorrencias.sort(key=lambda o: o.get("data") or "", reverse=True)

    ativos = [c for c in lista if (c.get("situacao") or "ativo") == "ativo"]
    por_setor = {}
    for c in ativos:
        por_setor[c.get("setor") or "Sem setor"] = por_setor.get(c.get("setor") or "Sem setor", 0) + 1

    # Custo mensal direto: o que sai do caixa por cada pessoa todo mes. Nao e a
    # folha contabil (falta encargo, ferias, 13o), e o rodape da tela diz isso.
    def custo(c):
        return sum(float(c.get(campo) or 0) for campo in ("salario", "vt", "bonificacao"))
    custo_folha = sum(custo(c) for c in ativos)
    sem_salario = [c["nome"] for c in ativos if not c.get("salario")]

    tempos = [c["tempo_casa"] for c in ativos if c["tempo_casa"] is not None]
    ano = hoje[:4]
    desligados_ano = [c for c in lista
                      if c.get("situacao") == "desligado" and (c.get("desligamento") or "").startswith(ano)]
    admitidos_ano = [c for c in lista if (c.get("admissao") or "").startswith(ano)]

    return jsonify({
        "colaboradores": lista,
        "ausencias": lista_ausencias,
        "documentos": lista_documentos,
        "ocorrencias": lista_ocorrencias,
        "opcoes": {
            "setores": SETORES_RH, "contratos": CONTRATOS_RH,
            "situacoes": SITUACOES_RH, "tipos_ausencia": TIPOS_AUSENCIA,
            "tipos_documento": TIPOS_DOCUMENTO, "tipos_ocorrencia": TIPOS_OCORRENCIA,
        },
        "vendedores": [{"id": vid, "nome": v["nome"]}
                       for vid, v in sorted(carregar_vendedores().items(),
                                            key=lambda kv: kv[1]["nome"])],
        "indicadores": {
            "ativos": len(ativos),
            "afastados": sum(1 for c in lista if c.get("situacao") == "afastado"),
            "desligados": sum(1 for c in lista if c.get("situacao") == "desligado"),
            "por_setor": sorted(({"setor": k, "qtd": v} for k, v in por_setor.items()),
                                key=lambda x: -x["qtd"]),
            "tempo_medio_casa": round(sum(tempos) / len(tempos), 1) if tempos else None,
            "custo_folha": round(custo_folha, 2),
            "sem_salario": sem_salario,
            "fora_hoje": [c["nome"] for c in lista if c["fora_hoje"]],
            "aniversariantes": sorted(
                ({"nome": c["nome"], "dia": c["nascimento"][8:10]} for c in lista
                 if c["faz_aniversario_no_mes"] and (c.get("situacao") or "ativo") != "desligado"),
                key=lambda x: x["dia"]),
            "documentos_vencendo": sum(1 for d in lista_documentos
                                       if d["dias"] is not None and d["dias"] <= 30),
            "admitidos_no_ano": len(admitidos_ano),
            "desligados_no_ano": len(desligados_ano),
            # Turnover simples: desligamentos sobre o quadro medio do ano. Com
            # equipe pequena um desligamento move muito o numero — por isso a
            # tela mostra o valor absoluto junto.
            "turnover_ano": (round(100 * len(desligados_ano) / max(1, len(ativos) + len(desligados_ano)), 1)
                             if lista else 0),
        },
    })

# ---- Folha de pagamento -------------------------------------------------
# Pedido do gestor (03/09/2026): tres pagamentos por mes, editaveis —
#   dia 05 = vale; dia 10 = meta bonus + comissoes; dia 20 = restante do salario.
# Chave rh_folha: {mes: {colaborador_id: {vale, bonus, comissao, salario,
# descontos, obs}}}. Mes sem dado fica em branco; o historico (jan-ago/26)
# veio da planilha "Colaboradores 2026.xlsx" via ferramentas/importar_folha.py.
# Descontos ao lado de cada pagamento (03/09/2026): vale/adiantamento, emprestimo,
# falta... abatidos do dia 05, do dia 10 ou do dia 20, a escolha do gestor.
# Bonificacao e VT sao pagos no dia 05 com o vale (planilha antiga: Total Dia 5 =
# Dia 05 + Bonificacao + VT) e mudam mes a mes — por isso editaveis aqui, com o
# valor da ficha como sugestao.
CAMPOS_FOLHA_VALOR = ("vale", "bonificacao", "vt", "desc05", "bonus", "comissao", "desc10", "salario", "desc20")
CAMPOS_FOLHA_TEXTO = ("tipo05", "tipo10", "tipo20", "obs")
TIPOS_DESCONTO = {"": "", "adiantamento": "Vale / adiantamento", "emprestimo": "Empréstimo",
                  "falta": "Falta", "outro": "Outro"}


def _folha_sugestoes(mes: str, colaboradores: dict) -> dict:
    """O que o portal ja sabe sobre o dia 10 do mes de referencia: a comissao
    calculada na aba Comissoes (ficha com vendedor_id) e o bonus marcado como
    Pago no Meta Bonus (pessoa com ficha no RH vinculada). E sugestao: a
    folha so preenche quando o gestor manda, e nunca por cima de valor digitado.
    A aba de agosto da planilha antiga trazia a comissao de agosto (Gustavo
    804 x 803,35 calculado) — o mes da folha e o mes de referencia."""
    import nucleo as N   # o alias do topo da area e apagado depois de ligar os nomes
    # Regra do gestor (03/09/2026): o dia 10 do mes M paga o bonus e a comissao
    # do mes M-1. A folha de setembro traz a comissao do que se vendeu em agosto.
    ano, mn = int(mes[:4]), int(mes[5:7])
    ref = f"{ano - 1}-12" if mn == 1 else f"{ano}-{mn - 1:02d}"
    de, ate = N.mes_para_intervalo(ref)
    vendedores = N.carregar_vendedores()
    sug = {}
    for cid, c in colaboradores.items():
        vid = (c.get("vendedor_id") or "").strip()
        if vid and vid in vendedores:
            try:
                vendas = N.carregar_vendas_para_comissao(vid, vendedores)
                sug.setdefault(cid, {})["comissao"] = round(
                    float(N.calcular_comissao(vid, de, ate, vendedores, vendas).get("comissao") or 0), 2)
            except Exception:
                pass
    mb = N._mb_bruto()
    por_pessoa = {(setor, pid): p.get("colaborador_id") for setor, gente in mb["pessoas"].items()
                  for pid, p in gente.items() if p.get("colaborador_id")}
    for pg in (mb.get("saldos") or {}).get("pagamentos", {}).values():
        if pg.get("mes") != ref:
            continue
        cid = por_pessoa.get((pg.get("setor"), pg.get("pessoa_id")))
        if cid:
            s = sug.setdefault(cid, {})
            s["bonus"] = round(s.get("bonus", 0.0) + float(pg.get("valor") or 0), 2)
    sug["_referencia"] = ref
    return sug


def _folha_linhas(mes: str) -> dict:
    colaboradores = _rh_ler("colaboradores")
    folha = _rh_ler("folha")
    do_mes = folha.get(mes) or {}
    sugestoes = _folha_sugestoes(mes, colaboradores)
    linhas = []
    for cid, c in colaboradores.items():
        reg = do_mes.get(cid) or {}
        ativo = (c.get("situacao") or "ativo") == "ativo"
        tem_valor = any(float(reg.get(k) or 0) for k in CAMPOS_FOLHA_VALOR) or any(reg.get(k) for k in CAMPOS_FOLHA_TEXTO)
        # Ativo sempre aparece (e a folha do mes); desligado so se tiver valor
        # naquele mes — o historico dele continua legivel, sem poluir o atual.
        if not ativo and not tem_valor:
            continue
        vals = {k: float(reg.get(k) or 0) for k in CAMPOS_FOLHA_VALOR}
        liq05 = vals["vale"] + vals["bonificacao"] + vals["vt"] - vals["desc05"]
        liq10 = vals["bonus"] + vals["comissao"] - vals["desc10"]
        liq20 = vals["salario"] - vals["desc20"]
        linhas.append({
            "id": cid, "nome": c.get("nome") or "?", "apelido": c.get("apelido") or "",
            "setor": c.get("setor") or "", "situacao": c.get("situacao") or "ativo",
            "salario_ref": float(c.get("salario") or 0), "vt_ref": float(c.get("vt") or 0),
            "bonificacao_ref": float(c.get("bonificacao") or 0),
            **vals, **{k: reg.get(k) or "" for k in CAMPOS_FOLHA_TEXTO},
            "liq05": round(liq05, 2), "liq10": round(liq10, 2), "liq20": round(liq20, 2),
            "total": round(liq05 + liq10 + liq20, 2),
            "preenchido": tem_valor,
            "sugestao": {**{k: v for k, v in (("bonificacao", float(c.get("bonificacao") or 0)), ("vt", float(c.get("vt") or 0))) if v},
                         **(sugestoes.get(cid) or {})},
        })
    linhas.sort(key=lambda x: (x["situacao"] != "ativo", (x["apelido"] or x["nome"]).lower()))
    totais = {k: round(sum(l[k] for l in linhas), 2) for k in CAMPOS_FOLHA_VALOR}
    totais["dia05"] = round(sum(l["liq05"] for l in linhas), 2)
    totais["dia10"] = round(sum(l["liq10"] for l in linhas), 2)
    totais["dia20"] = round(sum(l["liq20"] for l in linhas), 2)
    totais["descontos"] = round(totais["desc05"] + totais["desc10"] + totais["desc20"], 2)
    totais["mes"] = round(sum(l["total"] for l in linhas), 2)
    meses = sorted(m for m, regs in folha.items()
                   if any(any(float(r.get(k) or 0) for k in CAMPOS_FOLHA_VALOR) for r in regs.values()))
    return {"mes": mes, "linhas": linhas, "totais": totais, "meses_com_dados": meses,
            "tipos_desconto": TIPOS_DESCONTO,
            "preenchidos": sum(1 for l in linhas if l["preenchido"]),
            "referencia_dia10": sugestoes.get("_referencia")}


@app.route("/api/admin/rh/folha")
def api_rh_folha():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    mes = (request.args.get("mes") or hoje_br().isoformat()[:7]).strip()
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        return jsonify({"erro": "Mês inválido."}), 400
    return jsonify(_folha_linhas(mes))


@app.route("/api/admin/rh/folha", methods=["POST"])
def api_rh_folha_salvar():
    """Grava o mes inteiro de uma vez (upsert por colaborador). Campo vazio
    ou zero em todos os valores apaga a linha do mes."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    mes = (corpo.get("mes") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        return jsonify({"erro": "Mês inválido."}), 400
    itens = corpo.get("itens")
    if not isinstance(itens, list):
        return jsonify({"erro": "Nada para salvar."}), 400
    colaboradores = _rh_ler("colaboradores")
    folha = _rh_ler("folha")
    do_mes = dict(folha.get(mes) or {})
    carimbo = agora_br().isoformat(timespec="seconds")
    gravados = 0
    for item in itens:
        cid = (item.get("id") or "").strip()
        if cid not in colaboradores:
            return jsonify({"erro": "Colaborador desconhecido."}), 400
        reg = {}
        for k in CAMPOS_FOLHA_VALOR:
            bruto = str(item.get(k) if item.get(k) is not None else "").strip().replace(".", "").replace(",", ".") \
                if isinstance(item.get(k), str) else item.get(k)
            try:
                v = float(bruto) if bruto not in (None, "") else 0.0
            except (TypeError, ValueError):
                return jsonify({"erro": f"Valor inválido em {k} de {colaboradores[cid].get('nome')}."}), 400
            if v < 0:
                return jsonify({"erro": f"Valor negativo em {k} de {colaboradores[cid].get('nome')}."}), 400
            reg[k] = v
        for k in CAMPOS_FOLHA_TEXTO:
            reg[k] = _rh_texto(item.get(k), 200)
            if k.startswith("tipo") and reg[k] not in TIPOS_DESCONTO:
                return jsonify({"erro": f"Tipo de desconto inválido: {reg[k]}."}), 400
        if any(reg[k] for k in CAMPOS_FOLHA_VALOR) or any(reg[k] for k in CAMPOS_FOLHA_TEXTO):
            reg["editado_em"] = carimbo
            do_mes[cid] = reg
            gravados += 1
        else:
            do_mes.pop(cid, None)
    if do_mes:
        folha[mes] = do_mes
    else:
        folha.pop(mes, None)
    _rh_gravar("folha", folha)
    return jsonify({"ok": True, "gravados": gravados, **{"totais": _folha_linhas(mes)["totais"]}})


@app.route("/api/admin/rh/colaboradores", methods=["POST"])
def api_admin_rh_salvar():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    nome = _rh_texto(corpo.get("nome"), 80)
    if not nome:
        return jsonify({"erro": "Informe o nome."}), 400

    dados = _rh_ler("colaboradores")
    cid = _rh_texto(corpo.get("id"), 12) or uuid.uuid4().hex[:12]
    antigo = dados.get(cid, {})

    situacao = corpo.get("situacao") if corpo.get("situacao") in SITUACOES_RH else "ativo"
    ficha = {
        "nome": nome,
        "apelido": _rh_texto(corpo.get("apelido"), 40),
        "cargo": _rh_texto(corpo.get("cargo"), 60),
        "setor": _rh_texto(corpo.get("setor"), 40),
        "contrato": _rh_texto(corpo.get("contrato"), 20),
        "situacao": situacao,
        "admissao": _rh_data(corpo.get("admissao")),
        "desligamento": _rh_data(corpo.get("desligamento")) if situacao == "desligado" else "",
        "motivo_desligamento": (_rh_texto(corpo.get("motivo_desligamento"), 200)
                                if situacao == "desligado" else ""),
        "nascimento": _rh_data(corpo.get("nascimento")),
        "telefone": _rh_texto(corpo.get("telefone"), 30),
        "email": _rh_texto(corpo.get("email"), 80),
        "endereco": _rh_texto(corpo.get("endereco"), 160),
        "emergencia": _rh_texto(corpo.get("emergencia"), 120),
        "cpf": _rh_texto(corpo.get("cpf"), 20),
        "rg": _rh_texto(corpo.get("rg"), 20),
        "salario": _rh_num(corpo.get("salario")),
        "vt": _rh_num(corpo.get("vt")),
        "bonificacao": _rh_num(corpo.get("bonificacao")),
        # Liga a ficha ao vendedor do portal: com isso a tela de RH consegue
        # abrir o desempenho da pessoa sem cadastro duplicado.
        "vendedor_id": _rh_texto(corpo.get("vendedor_id"), 40),
        "obs": _rh_texto(corpo.get("obs"), 500),
        "criado_em": antigo.get("criado_em") or agora_br().isoformat(timespec="seconds"),
        "editado_em": agora_br().isoformat(timespec="seconds"),
    }
    dados[cid] = ficha
    _rh_gravar("colaboradores", dados)
    return jsonify({"ok": True, "id": cid})

@app.route("/api/admin/rh/colaboradores/<cid>", methods=["DELETE"])
def api_admin_rh_remover(cid):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    dados = _rh_ler("colaboradores")
    if cid not in dados:
        return jsonify({"erro": "Colaborador não encontrado."}), 404
    dados.pop(cid)
    _rh_gravar("colaboradores", dados)
    # Ausência, documento e ocorrência de quem saiu do cadastro viram lixo
    # órfão: aparecem como "(removido)" e sujam os indicadores pra sempre.
    for colecao in ("ausencias", "documentos", "ocorrencias"):
        itens = _rh_ler(colecao)
        sobra = {k: v for k, v in itens.items() if v.get("colaborador_id") != cid}
        if len(sobra) != len(itens):
            _rh_gravar(colecao, sobra)
    return jsonify({"ok": True})

@app.route("/api/admin/rh/<colecao>", methods=["POST"])
def api_admin_rh_registro(colecao):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    if colecao not in ("ausencias", "documentos", "ocorrencias"):
        return jsonify({"erro": "Coleção inválida."}), 404

    corpo = request.get_json(silent=True) or {}
    cid = _rh_texto(corpo.get("colaborador_id"), 12)
    if cid not in _rh_ler("colaboradores"):
        return jsonify({"erro": "Escolha o colaborador."}), 400

    if colecao == "ausencias":
        de = _rh_data(corpo.get("de"))
        if not de:
            return jsonify({"erro": "Informe a data de início."}), 400
        ate = _rh_data(corpo.get("ate")) or de
        if ate < de:
            return jsonify({"erro": "A data final é anterior à inicial."}), 400
        registro = {"colaborador_id": cid,
                    "tipo": corpo.get("tipo") if corpo.get("tipo") in TIPOS_AUSENCIA else "falta",
                    "de": de, "ate": ate,
                    "dias": (date.fromisoformat(ate) - date.fromisoformat(de)).days + 1,
                    "obs": _rh_texto(corpo.get("obs"), 200)}
    elif colecao == "documentos":
        registro = {"colaborador_id": cid,
                    "tipo": _rh_texto(corpo.get("tipo"), 40) or "Outro",
                    "numero": _rh_texto(corpo.get("numero"), 40),
                    "vence_em": _rh_data(corpo.get("vence_em")),
                    "obs": _rh_texto(corpo.get("obs"), 200)}
    else:
        registro = {"colaborador_id": cid,
                    "tipo": corpo.get("tipo") if corpo.get("tipo") in TIPOS_OCORRENCIA else "nota",
                    "data": _rh_data(corpo.get("data")) or hoje_br().isoformat(),
                    "texto": _rh_texto(corpo.get("texto"), 800)}
        if not registro["texto"]:
            return jsonify({"erro": "Escreva o que aconteceu."}), 400

    registro["criado_em"] = agora_br().isoformat(timespec="seconds")
    itens = _rh_ler(colecao)
    rid = uuid.uuid4().hex[:12]
    itens[rid] = registro
    _rh_gravar(colecao, itens)
    return jsonify({"ok": True, "id": rid})

@app.route("/api/admin/rh/<colecao>/<rid>", methods=["DELETE"])
def api_admin_rh_remover_registro(colecao, rid):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    if colecao not in ("ausencias", "documentos", "ocorrencias"):
        return jsonify({"erro": "Coleção inválida."}), 404
    itens = _rh_ler(colecao)
    if rid not in itens:
        return jsonify({"erro": "Registro não encontrado."}), 404
    itens.pop(rid)
    _rh_gravar(colecao, itens)
    return jsonify({"ok": True})
