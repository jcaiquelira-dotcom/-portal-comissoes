# -*- coding: utf-8 -*-
"""Area `contas` do portal — rotas e helpers privados. Extraida do server.py em
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
    data_de_texto,
    AREAS,
    AREAS_PROPRIAS,
    CREDENCIAIS_FILE,
    DIAS_MAXIMOS_RETROATIVOS,
    LOGIN_JANELA_MINUTOS,
    LOG_ACESSOS_FILE,
    LOG_ACOES_FILE,
    PAGINA_DA_AREA,
    _achatar_canal,
    _atd_pendentes,
    _atd_resolvidos,
    _hash_codigo,
    _hash_senha,
    _resumo_retomada,
    _senha_confere,
    _topo_retomada,
    agora_br,
    app,
    areas_do_usuario,
    areas_efetivas,
    calcular_comissao,
    calendar,
    carregar_confirmacoes,
    carregar_credenciais,
    carregar_fila_retomada,
    carregar_meses_fechados,
    carregar_metas,
    carregar_status_retomada,
    carregar_vendas_para_comissao,
    carregar_vendas_todos,
    carregar_vendas_vendedor,
    carregar_vendedores,
    date,
    desligado,
    excedeu_tentativas_login,
    exigir_admin,
    exigir_vendedor,
    hoje_br,
    jsonify,
    limpar_confirmacao,
    mes_esta_fechado,
    mes_para_intervalo,
    metas_vendedor,
    perfil_de,
    registrar_acao,
    registrar_acesso,
    request,
    resolver_pasta_dados,
    retroativo_ativo,
    salvar_confirmacoes,
    salvar_meses_fechados,
    salvar_vendas_vendedor,
    secrets,
    session,
    timedelta,
    total_vendido,
    usuario_master,
    uuid,
    valor_liquido,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

PAGINA_DA_AREA["expedicao"] = "/"

PAGINA_DA_AREA["ranking"] = "/painel.html"

def vende(vid: str, info: dict) -> bool:
    """Se esta pessoa lanca venda no portal.

    A regra e a area `minhas_vendas`, nao um campo novo: quem nao tem a tela de
    lancar venda nao lanca venda. Assim, tirar a permissao no menu de Permissoes
    ja tira a pessoa das listas de venda — sem um segundo lugar pra lembrar de
    marcar, que e onde esse tipo de coisa fica desencontrado.

    Quando a pessoa nao tem `areas` gravado, o padrao do setor decide, e o
    fallback e o pacote de vendedor. Ou seja: na duvida, ela CONTINUA aparecendo.
    Errar deixando um vendedor de fora do ranking e pior do que errar deixando
    alguem a mais.
    """
    return "minhas_vendas" in areas_efetivas(info, vid)

def exigir_expedicao():
    """Id de quem esta logado, se for da expedicao ou o gestor."""
    vid = exigir_vendedor()
    if vid and perfil_de(vid) == "expedicao":
        return vid
    if exigir_admin():
        return "gestor"
    return None

@app.route("/api/atendimento/resolver", methods=["POST"])
def api_atendimento_resolver():
    """Marca uma conversa como resolvida. Vale pro vendedor e pro gestor: os
    dois veem a mesma fila, entao resolver num lugar resolve no outro."""
    if not (exigir_vendedor() or exigir_admin()):
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    sessao = (corpo.get("id") or "").strip()
    if not sessao:
        return jsonify({"erro": "Sessão não informada."}), 400

    resolvidos = _atd_resolvidos()
    if corpo.get("desfazer"):
        resolvidos.pop(sessao, None)
    else:
        # Guarda o carimbo da ultima fala do cliente, nao a hora do clique:
        # e assim que a conversa reaparece se ele voltar a falar.
        resolvidos[sessao] = {
            "ultima_em": (corpo.get("ultima_em")
                          or agora_br().isoformat(timespec="seconds")),
            # A data do clique e o que faz a marcacao expirar amanha.
            "em": hoje_br().isoformat(),
            # Quem marcou e a que horas. Em 01/09/2026 o gestor viu a fila de
            # uma vendedora despencar e perguntou se ela tinha saido clicando
            # em tudo — e nao havia como responder, porque o registro nao
            # guardava autor nem hora. Guardar isso nao e vigiar: e conseguir
            # distinguir "resolveu 13 ao longo do dia" de "resolveu 13 em um
            # minuto", que sao coisas muito diferentes.
            "por": session.get("vendedor_id") or ("gestor" if exigir_admin() else "?"),
            "quando": agora_br().isoformat(timespec="seconds"),
        }
    # Marcacao de ontem pra tras ja nao vale — nao precisa ficar no arquivo.
    hoje = hoje_br().isoformat()
    resolvidos = {k: v for k, v in resolvidos.items()
                  if (v.get("em") if isinstance(v, dict) else str(v)[:10]) == hoje}
    escrever_json(resolver_pasta_dados() / "atendimento_resolvido.json", resolvidos)
    return jsonify({"ok": True, "resolvidas": len(resolvidos)})

@app.route("/api/meu-atendimento")
def api_meu_atendimento():
    """Os clientes que estao esperando ESTE vendedor responder.

    Mesma fonte do painel do gestor (atendimento_alerta), filtrada pelo id de
    quem esta logado: cada um ve so a propria fila. Quem age e o vendedor, por
    isso o alerta vive aqui e nao so na area do gestor.
    """
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "atendimento_alerta.json", None)
    if not d:
        return jsonify({"sem_dados": True})
    resolvidos = _atd_resolvidos()
    minhas = _atd_pendentes([c for c in (d.get("conversas") or [])
                             if c.get("vendedor_id") == vendedor_id], resolvidos)
    return jsonify({
        "gerado_em": d.get("gerado_em"),
        "limites": d.get("limites"),
        "total": len(minhas),
        "critico": sum(1 for c in minhas if c["nivel"] == "critico"),
        "urgente": sum(1 for c in minhas if c["nivel"] == "urgente"),
        "conversas": minhas,
    })

@app.route("/api/meu-painel")
def api_meu_painel():
    """Tudo que o painel do vendedor mostra, numa chamada só — evita a tela
    disparar cinco requisições e ficar montando aos pedaços."""
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401

    mes = request.args.get("mes", hoje_br().isoformat()[:7])
    de, ate = mes_para_intervalo(mes)
    vendedores = carregar_vendedores()
    vendas_comissao = carregar_vendas_para_comissao(vendedor_id, vendedores)
    comissao = calcular_comissao(vendedor_id, de, ate, vendedores, vendas_comissao)

    minhas = [
        v for v in vendas_comissao.values()
        if v["vendedor_id"] == vendedor_id and de <= v["data"] <= ate
        and v.get("tipo", "venda") == "venda"
    ]

    hoje = hoje_br()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    metas = metas_vendedor(vendedor_id, carregar_metas())

    def soma(itens):
        return round(sum(valor_liquido(v) for v in itens), 2)

    # Só conta o dia/semana se o mês em tela for o corrente — senão "vendi hoje"
    # apareceria zerado enquanto o vendedor revisa um mês passado.
    mes_corrente = mes == hoje.isoformat()[:7]
    total_hoje = soma([v for v in minhas if v["data"] == hoje.isoformat()]) if mes_corrente else None
    total_semana = soma([v for v in minhas if v["data"] >= inicio_semana.isoformat()]) if mes_corrente else None

    total_mes = comissao["total_vendido"]
    qtd = len(minhas)
    devolvidas = [v for v in minhas if v.get("devolucao")]

    # Evolução dia a dia, pro gráfico de linha
    por_dia = {}
    for v in minhas:
        por_dia[v["data"]] = round(por_dia.get(v["data"], 0) + valor_liquido(v), 2)

    def ranking(campo, rotulo_vazio):
        acumulado = {}
        for v in minhas:
            chave = (v.get(campo) or "").strip() or rotulo_vazio
            acumulado[chave] = round(acumulado.get(chave, 0) + valor_liquido(v), 2)
        top = sorted(acumulado.items(), key=lambda kv: kv[1], reverse=True)[:8]
        return [{"nome": k, "valor": val} for k, val in top]

    dias_no_mes = calendar.monthrange(int(mes[:4]), int(mes[5:7]))[1]
    dias_restantes = max(1, dias_no_mes - hoje.day + 1) if mes_corrente else 1
    meta_mensal = float(metas.get("mensal", 0))
    falta = max(0.0, meta_mensal - total_mes)

    fila = carregar_fila_retomada(vendedor_id)
    status_retomada = carregar_status_retomada(vendedor_id)
    resumo_retomada = _resumo_retomada(fila.get("itens", []), status_retomada) if fila else None
    if resumo_retomada:
        # Os tres primeiros ja no painel: e a primeira tela que ele abre, e ali o
        # follow-up vira trabalho a fazer em vez de mais um link no menu.
        resumo_retomada["topo"] = _topo_retomada(fila.get("itens", []), status_retomada)

    return jsonify({
        "mes": mes,
        "mes_corrente": mes_corrente,
        "total_mes": total_mes,
        "total_hoje": total_hoje,
        "total_semana": total_semana,
        "qtd_vendas": qtd,
        "ticket_medio": round(total_mes / qtd, 2) if qtd else 0,
        "comissao": comissao,
        "devolucoes": {
            "quantidade": len(devolvidas),
            "valor": round(sum(v["valor"] - valor_liquido(v) for v in devolvidas), 2),
        },
        "metas": {
            "diaria": float(metas.get("diaria", 0)),
            "semanal": float(metas.get("semanal", 0)),
            "mensal": meta_mensal,
            "falta_no_mes": round(falta, 2),
            "necessario_por_dia": round(falta / dias_restantes, 2) if falta else 0,
            "dias_restantes": dias_restantes if mes_corrente else 0,
        },
        "evolucao": [{"data": d, "valor": por_dia[d]} for d in sorted(por_dia)],
        "top_produtos": ranking("produto", "Sem descrição"),
        "top_canais": ranking("canal", "Sem canal"),
        "retomada": resumo_retomada,
    })

@app.route("/api/vendas", methods=["GET"])
def api_listar_vendas():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    todos = request.args.get("todos") == "1"
    mes = request.args.get("mes", hoje_br().isoformat()[:7])
    vendas = carregar_vendas_vendedor(vendedor_id)
    minhas = [
        {**v, "id": vid}
        for vid, v in vendas.items()
        if (todos or v["data"][:7] == mes) and v.get("tipo", "venda") == "venda"
    ]
    minhas.sort(key=lambda v: v["data"], reverse=True)
    return jsonify(minhas)

# O canal era texto livre e virou 19 grafias pra meia duzia de canais reais —
# "ITAU" de quatro jeitos, "B" pra balcao. Normaliza na entrada, que e a unica
# porta: o historico foi corrigido uma vez por script, e daqui pra frente toda
# venda ja chega com o nome canonico. Grafia desconhecida passa como veio,
# porque inventar mapeamento e pior que fragmentar.
CANAIS_CANONICOS = {
    "b": "Balcão", "balcao": "Balcão",
    "ml": "Mercado Livre", "mercado livre": "Mercado Livre",
    "itau": "Itaú",
    "deb": "Débito", "debito": "Débito",
    # O time deixou de usar "crediário" em 02/09/2026: na pratica o que era
    # lancado assim era cartao de credito. As 20 vendas antigas foram
    # convertidas por script na mesma data, e "crediario" continua no mapa pra
    # quem digitar por habito cair no nome novo em vez de fragmentar.
    "cred": "Crédito", "credito": "Crédito", "crediario": "Crédito",
    "din": "Dinheiro", "dinh": "Dinheiro", "dinheiro": "Dinheiro",
    "site": "Site", "loja integrada": "Site",
    "itau / cred": "Itaú / Crédito",
}

def normalizar_canal(texto) -> str:
    t = " ".join(str(texto or "").split())
    if not t:
        return ""
    chave = _achatar_canal(t)
    if chave in CANAIS_CANONICOS:
        return CANAIS_CANONICOS[chave]
    # "Cred 4x" -> "Crédito 4x": a primeira palavra e o canal, o resto e
    # detalhe (parcelamento) que nao se joga fora. Divide o texto REAL, nao a
    # chave achatada — os dois podem ter contagens de espaco diferentes.
    partes = t.split(" ", 1)
    if len(partes) == 2 and _achatar_canal(partes[0]) in CANAIS_CANONICOS:
        return CANAIS_CANONICOS[_achatar_canal(partes[0])] + " " + partes[1]
    return t

def validar_valor_produto(body: dict) -> tuple[float, str, str, str]:
    try:
        valor = round(float(body.get("valor")), 2)
    except (TypeError, ValueError):
        raise ValueError("Valor inválido.")
    if valor <= 0:
        raise ValueError("Valor deve ser maior que zero.")
    produto = (body.get("produto") or "").strip()
    if not produto:
        raise ValueError("Informe o que foi vendido.")
    canal = normalizar_canal(body.get("canal"))
    sku = (body.get("sku") or "").strip()
    return valor, produto, canal, sku

def validar_data_venda(data_venda: str, ignorar_limite: bool = False) -> None:
    """Confere se a data é válida e está dentro da janela permitida de lançamento."""
    try:
        data_obj = date.fromisoformat(data_venda)
    except ValueError:
        raise ValueError("Data inválida.")
    hoje = hoje_br()
    if data_obj > hoje:
        raise ValueError("Não é possível usar uma data futura.")
    if not ignorar_limite and (hoje - data_obj).days > DIAS_MAXIMOS_RETROATIVOS:
        raise ValueError(
            f"Essa data é de mais de {DIAS_MAXIMOS_RETROATIVOS} dias atrás. "
            "Fale com o gestor para lançar vendas retroativas além desse prazo."
        )

def montar_venda(vendedor_id: str, body: dict, ignorar_limite_retroativo: bool = False) -> dict:
    """Valida os campos de uma venda e retorna o dict pronto para salvar.
    Lança ValueError com a mensagem de erro em caso de dado inválido."""
    valor, produto, canal, sku = validar_valor_produto(body)
    data_venda = (body.get("data") or hoje_br().isoformat()).strip()
    validar_data_venda(data_venda, ignorar_limite=ignorar_limite_retroativo)
    if mes_esta_fechado(data_venda):
        raise ValueError("Esse mês já foi fechado pelo gestor e não aceita mais lançamentos.")

    venda = {
        "vendedor_id": vendedor_id,
        "data": data_venda,
        "valor": valor,
        "produto": produto,
        "tipo": "venda",
        "criado_em": agora_br().isoformat(timespec="seconds"),
    }
    if canal:
        venda["canal"] = canal
    if sku:
        venda["sku"] = sku
    return venda

def venda_igual_no_mes(vendas: dict, produto: str, valor: float, mes: str):
    """Devolve uma venda do mesmo mês com produto e valor idênticos, se houver.
    Serve pro aviso de "esse produto já foi lançado" na hora do lançamento —
    é só um alerta, porque duas peças iguais de carros iguais são possíveis."""
    produto_norm = produto.strip().lower()
    for v in vendas.values():
        if v.get("tipo", "venda") != "venda":
            continue
        if v["data"][:7] != mes:
            continue
        if v["produto"].strip().lower() == produto_norm and v["valor"] == valor:
            return v
    return None

@app.route("/api/vendas", methods=["POST"])
def api_criar_venda():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    liberado = retroativo_ativo(carregar_vendedores().get(vendedor_id, {}))
    body = request.get_json(force=True)
    try:
        venda = montar_venda(vendedor_id, body, ignorar_limite_retroativo=liberado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400

    vendas = carregar_vendas_vendedor(vendedor_id)

    # Proteção contra duplicidade. O `envio_id` é gerado pelo navegador uma vez
    # por lançamento: se a mesma tentativa chegar de novo (clique duplo, conexão
    # lenta que o vendedor achou que travou, refresh no meio do envio), a gente
    # devolve a venda que já foi salva em vez de criar outra. É mais confiável
    # que a checagem por tempo, que falhava justamente no caso ruim — servidor
    # demorando pra responder e vendedor tentando de novo depois de 8 segundos.
    envio_id = (body.get("envio_id") or "").strip()
    if envio_id:
        for vid_existente, v in vendas.items():
            if v.get("envio_id") == envio_id:
                return jsonify({"ok": True, "id": vid_existente, "ja_existia": True})
        venda["envio_id"] = envio_id

    # Aviso (não bloqueio): já existe venda igual em produto e valor no mesmo
    # mês? Pode ser legítimo — duas peças iguais de carros iguais — então só
    # perguntamos. O vendedor reenvia com `confirmar_duplicata` pra confirmar.
    if not body.get("confirmar_duplicata"):
        igual = venda_igual_no_mes(vendas, venda["produto"], venda["valor"], venda["data"][:7])
        if igual:
            return jsonify({
                "confirmar_duplicata": True,
                "existente": {"data": igual["data"], "produto": igual["produto"], "valor": igual["valor"]},
            }), 409

    novo_id = uuid.uuid4().hex[:12]
    vendas[novo_id] = venda
    salvar_vendas_vendedor(vendedor_id, vendas)
    limpar_confirmacao(vendedor_id, venda["data"][:7])
    return jsonify({"ok": True, "id": novo_id})

@app.route("/api/vendas/lote", methods=["POST"])
def api_criar_vendas_lote():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    liberado = retroativo_ativo(carregar_vendedores().get(vendedor_id, {}))
    body = request.get_json(force=True)
    linhas = body.get("vendas", [])
    if not isinstance(linhas, list) or not linhas:
        return jsonify({"erro": "Nenhuma linha para salvar."}), 400

    vendas = carregar_vendas_vendedor(vendedor_id)

    # Mesma proteção do lançamento avulso: um `envio_id` por clique em "Salvar
    # tudo". Aqui ela é ainda mais importante, porque um lote pode ter linhas
    # legitimamente iguais (duas peças iguais vendidas no mesmo dia), então não
    # dá pra deduplicar comparando produto/valor/data como no avulso.
    envio_id = (body.get("envio_id") or "").strip()
    if envio_id:
        ja_salvas = [vid for vid, v in vendas.items() if v.get("envio_id") == envio_id]
        if ja_salvas:
            return jsonify({"ok": True, "salvas": len(ja_salvas), "erros": [], "ja_existia": True})

    salvas = 0
    erros = []
    linhas_salvas = []
    meses_afetados = set()
    for idx, linha in enumerate(linhas, start=1):
        try:
            venda = montar_venda(vendedor_id, linha, ignorar_limite_retroativo=liberado)
        except ValueError as e:
            erros.append({"linha": idx, "erro": str(e)})
            continue
        if envio_id:
            venda["envio_id"] = envio_id
        vendas[uuid.uuid4().hex[:12]] = venda
        salvas += 1
        linhas_salvas.append(idx)
        meses_afetados.add(venda["data"][:7])

    if salvas:
        salvar_vendas_vendedor(vendedor_id, vendas)
        for mes in meses_afetados:
            limpar_confirmacao(vendedor_id, mes)
    # `linhas_salvas` deixa o navegador apagar da planilha só as linhas que
    # realmente entraram, mantendo as que deram erro. Sem isso, quando parte do
    # lote falhava a planilha continuava inteira na tela e o vendedor corrigia
    # e salvava tudo de novo — duplicando o que já tinha sido salvo.
    return jsonify({"ok": True, "salvas": salvas, "erros": erros, "linhas_salvas": linhas_salvas})

@app.route("/api/vendas/<venda_id>", methods=["DELETE"])
def api_remover_venda(venda_id):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    vendas = carregar_vendas_vendedor(vendedor_id)
    if venda_id not in vendas:
        return jsonify({"erro": "Venda não encontrada."}), 404
    if mes_esta_fechado(vendas[venda_id]["data"]):
        return jsonify({"erro": "Esse mês já foi fechado pelo gestor e não aceita mais alterações."}), 403
    mes_afetado = vendas[venda_id]["data"][:7]
    removida = vendas[venda_id]
    del vendas[venda_id]
    salvar_vendas_vendedor(vendedor_id, vendas)
    limpar_confirmacao(vendedor_id, mes_afetado)
    nome = carregar_vendedores().get(vendedor_id, {}).get("nome", vendedor_id)
    registrar_acao(vendedor_id, nome, "excluiu", removida["produto"], removida["valor"], removida["data"])
    return jsonify({"ok": True})

@app.route("/api/vendas/<venda_id>", methods=["PUT"])
def api_editar_venda(venda_id):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    vendas = carregar_vendas_vendedor(vendedor_id)
    atual = vendas.get(venda_id)
    if not atual:
        return jsonify({"erro": "Venda não encontrada."}), 404
    if atual.get("tipo", "venda") != "venda":
        return jsonify({"erro": "Não é possível editar esse registro."}), 400
    if mes_esta_fechado(atual["data"]):
        return jsonify({"erro": "Esse mês já foi fechado pelo gestor e não aceita mais alterações."}), 403

    liberado = retroativo_ativo(carregar_vendedores().get(vendedor_id, {}))
    body = request.get_json(force=True)
    try:
        valor, produto, canal, sku = validar_valor_produto(body)
        nova_data = (body.get("data") or atual["data"]).strip()
        if nova_data != atual["data"]:
            validar_data_venda(nova_data, ignorar_limite=liberado)
            if mes_esta_fechado(nova_data):
                raise ValueError("Esse mês já foi fechado pelo gestor.")
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400

    mes_antigo = atual["data"][:7]
    atualizada = {
        **atual,
        "data": nova_data,
        "valor": valor,
        "produto": produto,
        "editado_em": agora_br().isoformat(timespec="seconds"),
    }
    if canal:
        atualizada["canal"] = canal
    else:
        atualizada.pop("canal", None)
    # canal_original documenta a grafia que a migracao corrigiu. Se o vendedor
    # trocou o canal de verdade, a grafia antiga nao descreve mais esta venda.
    if canal != atual.get("canal"):
        atualizada.pop("canal_original", None)
    if sku:
        atualizada["sku"] = sku
    else:
        atualizada.pop("sku", None)
    vendas[venda_id] = atualizada
    salvar_vendas_vendedor(vendedor_id, vendas)

    limpar_confirmacao(vendedor_id, mes_antigo)
    if nova_data[:7] != mes_antigo:
        limpar_confirmacao(vendedor_id, nova_data[:7])

    mudancas = []
    if atual["valor"] != valor:
        mudancas.append(f"valor {atual['valor']:.2f} → {valor:.2f}")
    if atual["produto"] != produto:
        mudancas.append(f"produto \"{atual['produto']}\" → \"{produto}\"")
    if atual["data"] != nova_data:
        mudancas.append(f"data {atual['data']} → {nova_data}")
    nome = carregar_vendedores().get(vendedor_id, {}).get("nome", vendedor_id)
    registrar_acao(vendedor_id, nome, "editou", produto, valor, "; ".join(mudancas) or None)
    return jsonify({"ok": True})

@app.route("/api/vendas/<venda_id>/devolucao", methods=["POST"])
def api_marcar_devolucao(venda_id):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    vendas = carregar_vendas_vendedor(vendedor_id)
    atual = vendas.get(venda_id)
    if not atual:
        return jsonify({"erro": "Venda não encontrada."}), 404
    if atual.get("tipo", "venda") != "venda":
        return jsonify({"erro": "Não é possível marcar devolução nesse registro."}), 400

    body = request.get_json(force=True)
    tipo = (body.get("tipo") or "").strip()
    if tipo not in ("parcial", "total"):
        return jsonify({"erro": "Tipo de devolução inválido."}), 400

    if tipo == "total":
        valor_devolvido = atual["valor"]
    else:
        try:
            valor_devolvido = round(float(body.get("valor")), 2)
        except (TypeError, ValueError):
            return jsonify({"erro": "Valor devolvido inválido."}), 400
        if valor_devolvido <= 0:
            return jsonify({"erro": "Valor devolvido deve ser maior que zero."}), 400
        if valor_devolvido > atual["valor"]:
            return jsonify({"erro": "Valor devolvido não pode ser maior que o valor da venda."}), 400

    vendas[venda_id] = {
        **atual,
        "devolucao": {
            "tipo": tipo,
            "valor_devolvido": valor_devolvido,
            "marcado_em": agora_br().isoformat(timespec="seconds"),
            # Congelado AQUI, e nao consultado depois: o mes da venda vai fechar
            # em algum momento, e uma devolucao marcada com o mes aberto viraria
            # estorno retroativo so porque o tempo passou.
            "apos_fechamento": mes_esta_fechado(atual["data"]),
            "mes_estorno": (hoje_br().isoformat()[:7]
                            if mes_esta_fechado(atual["data"]) else None),
        },
    }
    salvar_vendas_vendedor(vendedor_id, vendas)
    limpar_confirmacao(vendedor_id, atual["data"][:7])
    nome = carregar_vendedores().get(vendedor_id, {}).get("nome", vendedor_id)
    acao = "marcou devolução total" if tipo == "total" else "marcou devolução parcial"
    registrar_acao(vendedor_id, nome, acao, atual["produto"], valor_devolvido)
    return jsonify({"ok": True})

@app.route("/api/vendas/<venda_id>/devolucao", methods=["DELETE"])
def api_remover_devolucao(venda_id):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    vendas = carregar_vendas_vendedor(vendedor_id)
    atual = vendas.get(venda_id)
    if not atual:
        return jsonify({"erro": "Venda não encontrada."}), 404
    if "devolucao" not in atual:
        return jsonify({"erro": "Essa venda não tem devolução marcada."}), 400

    nova = dict(atual)
    nova.pop("devolucao")
    vendas[venda_id] = nova
    salvar_vendas_vendedor(vendedor_id, vendas)
    limpar_confirmacao(vendedor_id, atual["data"][:7])
    nome = carregar_vendedores().get(vendedor_id, {}).get("nome", vendedor_id)
    registrar_acao(vendedor_id, nome, "desfez devolução", atual["produto"], atual["valor"])
    return jsonify({"ok": True})

@app.route("/api/confirmar-mes", methods=["POST"])
def api_confirmar_mes():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(force=True)
    mes = (body.get("mes") or "").strip()
    if len(mes) != 7 or mes[4] != "-":
        return jsonify({"erro": "Mês inválido."}), 400
    confirmacoes = carregar_confirmacoes(vendedor_id)
    confirmacoes[mes] = agora_br().isoformat(timespec="seconds")
    salvar_confirmacoes(vendedor_id, confirmacoes)
    return jsonify({"ok": True, "confirmado_em": confirmacoes[mes]})

@app.route("/api/minha-confirmacao")
def api_minha_confirmacao():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    mes = request.args.get("mes", hoje_br().isoformat()[:7])
    confirmacoes = carregar_confirmacoes(vendedor_id)
    return jsonify({"mes": mes, "confirmado_em": confirmacoes.get(mes)})

@app.route("/api/metas")
def api_metas():
    # So o gestor consome esta rota (tela de configuracao). Aberta, ela
    # entregava a meta individual de cada vendedor a qualquer um sem login —
    # dado que o resto do portal esconde de proposito.
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    return jsonify(carregar_metas())

@app.route("/api/painel/ranking")
def api_painel_ranking():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    vendas = carregar_vendas_todos(vendedores)
    metas = carregar_metas()

    hoje = hoje_br()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    inicio_mes = hoje.replace(day=1)

    resultado = []
    grupo_hoje = grupo_semana = grupo_mes = 0.0

    for vid, info in vendedores.items():
        m = metas_vendedor(vid, metas)
        hoje_v = total_vendido(vid, hoje.isoformat(), hoje.isoformat(), vendas)
        semana_v = total_vendido(vid, inicio_semana.isoformat(), hoje.isoformat(), vendas)
        mes_v = total_vendido(vid, inicio_mes.isoformat(), hoje.isoformat(), vendas)
        # Conta oculta so aparece na TV se tiver movimento — dinheiro na tela
        # sempre, cartao vazio de conta de teste nunca.
        if info.get("oculto") and not (hoje_v or semana_v or mes_v):
            continue
        # Quem nao vende nao disputa ranking de venda. O Pedro tem usuario do
        # portal (anuncios, meta bonus) e aparecia na TV com R$ 0,00 ao lado de
        # quem passou o mes vendendo — um cartao zerado ali le como vendedor
        # ruim, nao como "essa pessoa faz outra coisa".
        if not vende(vid, info) and not (hoje_v or semana_v or mes_v):
            continue
        if desligado(info) and not (hoje_v or semana_v or mes_v):
            continue
        grupo_hoje += hoje_v
        grupo_semana += semana_v
        grupo_mes += mes_v
        resultado.append({
            "id": vid,
            "nome": info["nome"],
            "foto": info.get("foto"),
            "avatar": info.get("avatar", ""),
            "hoje": hoje_v,
            "semana": semana_v,
            "mes": mes_v,
            "meta_diaria": float(m.get("diaria", 0)),
            "meta_semanal": float(m.get("semanal", 0)),
            "meta_mensal": float(m.get("mensal", 0)),
        })

    resultado.sort(key=lambda v: v["mes"], reverse=True)
    grupo_metas = metas.get("grupo", {})

    return jsonify({
        "agora": agora_br().isoformat(timespec="seconds"),
        "grupo": {
            "hoje": round(grupo_hoje, 2),
            "semana": round(grupo_semana, 2),
            "mes": round(grupo_mes, 2),
            "meta_diaria": float(grupo_metas.get("diaria", 0)),
            "meta_semanal": float(grupo_metas.get("semanal", 0)),
            "meta_mensal": float(grupo_metas.get("mensal", 0)),
        },
        "vendedores": resultado,
    })

@app.route("/api/admin/meses-fechados", methods=["GET"])
def api_admin_listar_meses_fechados():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    return jsonify(carregar_meses_fechados())

@app.route("/api/admin/meses-fechados", methods=["POST"])
def api_admin_alterar_mes_fechado():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(force=True)
    mes = (body.get("mes") or "").strip()
    fechar = bool(body.get("fechar"))
    if len(mes) != 7 or mes[4] != "-":
        return jsonify({"erro": "Mês inválido."}), 400

    meses = set(carregar_meses_fechados())
    if fechar:
        meses.add(mes)
    else:
        meses.discard(mes)
    salvar_meses_fechados(list(meses))
    return jsonify({"ok": True, "meses_fechados": sorted(meses)})

@app.route("/api/admin/log-acessos")
def api_admin_log_acessos():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    log = ler_json(LOG_ACESSOS_FILE, [])
    return jsonify(list(reversed(log))[:200])

@app.route("/api/admin/log-acoes")
def api_admin_log_acoes():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    log = ler_json(LOG_ACOES_FILE, [])
    return jsonify(list(reversed(log))[:200])

@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    body = request.get_json(force=True)
    senha = body.get("senha") or ""
    if excedeu_tentativas_login("admin"):
        return jsonify({"erro": f"Muitas tentativas erradas. Aguarde {LOGIN_JANELA_MINUTOS} minutos e tente de novo."}), 429
    cred = carregar_credenciais()
    if not _senha_confere(cred.get("admin_senha"), senha):
        registrar_acesso("admin", False)
        return jsonify({"erro": "Senha incorreta."}), 401
    if not str(cred.get("admin_senha") or "").startswith(("pbkdf2:", "scrypt:")):
        cred["admin_senha"] = _hash_senha(senha)
        escrever_json(CREDENCIAIS_FILE, cred)
    # Mesma regra do outro lado: a chave reserva e uma identidade sem dono, e
    # nao deve herdar o vendedor que estava logado antes.
    session.pop("vendedor_id", None)
    session.permanent = True
    session["admin"] = True
    # "chave-reserva", nao "admin". Entrar pela senha sem dono passa a ser um
    # evento visivel no log — se aparecer todo dia, e sinal de que alguem esta
    # usando a saida de emergencia como porta principal.
    registrar_acesso("chave-reserva", True, None, "senha do gestor (sem usuario)")
    return jsonify({"ok": True})

@app.route("/api/admin/gerar-codigo-recuperacao", methods=["POST"])
def api_admin_gerar_codigo_recuperacao():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(silent=True) or {}
    codigo_escolhido = (body.get("codigo") or "").strip().upper()
    if codigo_escolhido:
        if len(codigo_escolhido) < 6:
            return jsonify({"erro": "O código precisa ter pelo menos 6 caracteres."}), 400
        codigo = codigo_escolhido
    else:
        codigo = "-".join(secrets.token_hex(2).upper() for _ in range(2))
    cred = carregar_credenciais()
    cred["recuperacao_hash"] = _hash_codigo(codigo)
    cred["recuperacao_gerado_em"] = agora_br().isoformat(timespec="seconds")
    escrever_json(CREDENCIAIS_FILE, cred)
    return jsonify({"codigo": codigo})

@app.route("/api/recuperar-senha-admin", methods=["POST"])
def api_recuperar_senha_admin():
    if excedeu_tentativas_login("admin_recuperacao"):
        return jsonify({"erro": f"Muitas tentativas erradas. Aguarde {LOGIN_JANELA_MINUTOS} minutos e tente de novo."}), 429

    body = request.get_json(force=True)
    codigo = (body.get("codigo") or "").strip().upper()
    nova_senha = body.get("nova_senha") or ""

    cred = carregar_credenciais()
    hash_salvo = cred.get("recuperacao_hash")
    if not hash_salvo or _hash_codigo(codigo) != hash_salvo:
        registrar_acesso("admin_recuperacao", False)
        return jsonify({"erro": "Código inválido."}), 401

    if len(nova_senha) < 4:
        return jsonify({"erro": "A nova senha precisa ter pelo menos 4 caracteres."}), 400

    cred["admin_senha"] = _hash_senha(nova_senha)
    cred.pop("recuperacao_hash", None)
    cred.pop("recuperacao_gerado_em", None)
    escrever_json(CREDENCIAIS_FILE, cred)
    registrar_acesso("admin_recuperacao", True)
    return jsonify({"ok": True})

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})

# Sair da empresa nao apaga o passado: o Gustavo continua entrando pra conferir
# as proprias comissoes. Mas nao pode mais MUDAR nada — se ele lancasse uma
# venda hoje, ela entraria no nome dele, contra a decisao de que a partir do
# desligamento tudo e do substituto.
#
# A trava e aqui, e nao endpoint por endpoint, de proposito. Sao dezenas de
# rotas de escrita e o projeto ganha rotas novas toda semana; uma lista manual
# ficaria desatualizada no dia em que alguem esquecesse de incluir a proxima.
# Aqui o padrao e negar, e quem escrever endpoint novo nao precisa lembrar.
_METODOS_QUE_MUDAM = {"POST", "PUT", "PATCH", "DELETE"}

# Sair do sistema tem que funcionar sempre; e entrar tambem, senao ninguem
# consegue nem chegar na tela pra ver o historico.
_SEMPRE_LIBERADO = {"/api/logout", "/api/login", "/api/admin/login"}

@app.before_request
def _travar_desligado():
    if request.method not in _METODOS_QUE_MUDAM:
        return None
    if request.path in _SEMPRE_LIBERADO:
        return None
    if exigir_admin():          # o gestor segue podendo tudo, inclusive sobre ele
        return None
    vid = session.get("vendedor_id")
    if not vid:
        return None
    info = carregar_vendedores().get(vid) or {}
    if not desligado(info):
        return None
    return jsonify({
        "erro": "Seu acesso está em modo somente leitura desde {}. "
                "Você continua vendo seu histórico e suas comissões, mas não "
                "pode mais registrar ou alterar nada.".format(
                    "/".join(reversed(info["desligado_em"].split("-")))),
    }), 403

@app.route("/api/admin/me")
def api_admin_me():
    """Quem esta logado e o que pode ver.

    `gestor` diz se e a senha master; `areas` diz quais menus mostrar. A tela
    usa os dois: sem gestor e sem area nenhuma, nem entra.
    """
    areas = areas_do_usuario()
    vid = session.get("vendedor_id")
    nome = ""
    if vid and not exigir_admin():
        nome = (carregar_vendedores().get(vid) or {}).get("nome", "")
    if exigir_admin() and not nome:
        vid_m = session.get("vendedor_id")
        if vid_m:
            nome = (carregar_vendedores().get(vid_m) or {}).get("nome", "")
    return jsonify({
        "logado": bool(exigir_admin() or areas),
        "pagina_da_area": PAGINA_DA_AREA,
        "areas_proprias": AREAS_PROPRIAS,
        "gestor": exigir_admin(),
        # `master_nominal` e a pessoa com nome; `chave_reserva` e a senha sem
        # dono. A tela avisa quando alguem esta usando a reserva — ela existe
        # pra emergencia, e usar todo dia significa que algo esta errado.
        "master_nominal": usuario_master(),
        "chave_reserva": bool(session.get("admin")) and not usuario_master(),
        "areas": areas,
        "nome": nome,
        "todas_areas": AREAS,
    })

@app.route("/api/admin/resumo")
def api_admin_resumo():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    hoje = hoje_br().isoformat()
    de = request.args.get("de", f"{hoje[:7]}-01")
    ate = request.args.get("ate", f"{hoje[:7]}-31")
    filtro_vendedor = request.args.get("vendedor_id") or None

    vendedores = carregar_vendedores()
    vendas = carregar_vendas_todos(vendedores)

    ids_alvo = [filtro_vendedor] if filtro_vendedor in vendedores else list(vendedores.keys())
    mes_unico = de[:7] if de[:7] == ate[:7] else None

    por_vendedor = {vid: [] for vid in vendedores}
    for vid_venda, v in vendas.items():
        if not (de <= v["data"] <= ate):
            continue
        vendedor_id = v["vendedor_id"]
        if vendedor_id not in por_vendedor:
            por_vendedor[vendedor_id] = []
        por_vendedor[vendedor_id].append({**v, "id": vid_venda})

    metas_todas = carregar_metas()
    resultado = []
    total_geral = 0.0
    comissao_geral = 0.0
    qtd_vendas_geral = 0
    serie_por_mes = {}
    serie_por_dia = {}

    for vid in sorted(ids_alvo, key=lambda x: vendedores[x]["nome"]):
        info = vendedores[vid]
        # Conta oculta (gestor/teste) sai das listas — MAS so enquanto nao tem
        # venda no periodo. Se um dia tiver, aparece: dinheiro nunca some da tela.
        if (info.get("oculto") and not por_vendedor.get(vid)
                and not info.get("overrides") and vid != filtro_vendedor):
            continue
        # Desligado sem venda no periodo tambem sai — mas se teve venda, fica:
        # dinheiro nunca some de tela, e o mes em que ele trabalhou e dele.
        if (desligado(info) and not por_vendedor.get(vid)
                and vid != filtro_vendedor):
            continue
        # Quem nao vende fica fora das listas de venda e comissao, a nao ser
        # que tenha lancamento (dinheiro nunca some de tela). Antes isto olhava
        # so o perfil "expedicao"; agora vale a permissao, entao o Pedro — que
        # e do setor de anuncios — sai pelo mesmo motivo, sem precisar de uma
        # regra propria.
        if not vende(vid, info) and not por_vendedor.get(vid):
            continue
        lista_vendas = [v for v in por_vendedor.get(vid, []) if v.get("tipo", "venda") == "venda"]
        lista_vendas.sort(key=lambda v: v["data"], reverse=True)
        lista_bonus = [v for v in por_vendedor.get(vid, []) if v.get("tipo") == "bonus"]
        lista_bonus.sort(key=lambda v: v["data"], reverse=True)

        for v in lista_vendas:
            chave = v["data"][:7]
            serie_por_mes[chave] = serie_por_mes.get(chave, 0.0) + valor_liquido(v)
            serie_por_dia[v["data"]] = serie_por_dia.get(v["data"], 0.0) + valor_liquido(v)

        calc = calcular_comissao(vid, de, ate, vendedores, vendas)
        total_geral += calc["total_vendido"]
        comissao_geral += calc["comissao"]
        qtd_vendas_geral += len(lista_vendas)

        confirmado_em = None
        if mes_unico:
            confirmado_em = carregar_confirmacoes(vid).get(mes_unico)

        resultado.append({
            "id": vid,
            "nome": info["nome"],
            "percentual": calc["percentual"],
            "total_vendido": calc["total_vendido"],
            "comissao_propria": calc["comissao_propria"],
            "overrides": calc["overrides"],
            "comissao": calc["comissao"],
            "estorno": calc.get("estorno"),
            "total_bonus": calc["total_bonus"],
            "qtd_vendas": len(lista_vendas),
            "vendas": lista_vendas,
            "bonus": lista_bonus,
            "confirmado_em": confirmado_em,
            "meta_mensal": float(metas_vendedor(vid, metas_todas).get("mensal", 0) or 0),
        })

    resultado.sort(key=lambda r: r["total_vendido"], reverse=True)
    serie_mensal = [
        {"mes": mes, "total_vendido": round(valor, 2)}
        for mes, valor in sorted(serie_por_mes.items())
    ]
    # Num periodo dentro de um mes so, o grafico por mes vira uma barra unica e
    # nao diz nada — nesse caso mandamos o dia a dia.
    serie_diaria = [
        {"data": dia, "total_vendido": round(valor, 2)}
        for dia, valor in sorted(serie_por_dia.items())
    ] if mes_unico else []
    ticket_medio = round(total_geral / qtd_vendas_geral, 2) if qtd_vendas_geral else 0.0

    # Ritmo do mes: sem isso um vendedor com 60% da meta no dia 10 parece
    # atrasado, quando na verdade esta muito a frente.
    ritmo = None
    if mes_unico:
        dias_no_mes = calendar.monthrange(int(mes_unico[:4]), int(mes_unico[5:7]))[1]
        hoje_data = hoje_br()
        if mes_unico == hoje_data.isoformat()[:7]:
            dias_corridos = hoje_data.day
        else:
            dias_corridos = dias_no_mes          # mes fechado
        ritmo = {"dias_no_mes": dias_no_mes,
                 "dias_corridos": dias_corridos,
                 "pct_do_mes": round(100 * dias_corridos / dias_no_mes)}
    def _marketplaces_periodo(de, ate):
        """Vendas de marketplace no periodo, e os canais que existem mas
        nao tiveram numero nele.

        E funcao porque roda duas vezes: uma pro periodo filtrado e outra
        pro anterior, que alimenta a comparacao dos cartoes do topo. Os
        parametros se chamam `de` e `ate` de proposito — o corpo abaixo e o
        mesmo de antes, sem uma linha trocada.
        """

        # Vendas de marketplace somadas no MESMO periodo do filtro, a partir da
        # serie diaria que o sincronizador acumula. Lista, nao campo unico: quando
        # a Shopee chegar, e mais um item aqui e o painel ja mostra. Filtrando um
        # vendedor especifico o bloco sai — marketplace nao e de ninguem do time.
        marketplaces = []
        # Canal que EXISTE no sistema mas nao tem numero no periodo pedido entra
        # aqui, nao no silencio: sumir da tela faz o gestor achar que o total ja
        # inclui aquele canal. Foi o que aconteceu com a Shopee em agosto.
        marketplaces_ausentes = []
        if not filtro_vendedor:
            ml = ler_json(resolver_pasta_dados() / "ml_conta.json", None) or {}
            serie_ml = (ml.get("vendas") or {}).get("serie_dia") or {}
            desde_ml = (ml.get("vendas") or {}).get("serie_desde") or "9999"
            soma = round(sum(x.get("total", 0) for d, x in serie_ml.items() if de <= d <= ate), 2)
            qtd = sum(x.get("qtd", 0) for d, x in serie_ml.items() if de <= d <= ate)

            # Venda que o time lanca com canal "Mercado Livre" foi paga pelo
            # checkout do ML — ela JA esta nos pagamentos da serie acima. Conta uma
            # vez so: fica inteira no comercial (comissao e meta do vendedor nao
            # mudam) e sai da fatia do ML no consolidado. Confirmado pelo gestor em
            # 28/08/2026.
            dup_total, dup_qtd = 0.0, 0
            for v_ in vendas.values():
                if (v_.get("tipo", "venda") == "venda" and de <= v_["data"] <= ate
                        and str(v_.get("canal") or "").startswith("Mercado Livre")):
                    dup_total += valor_liquido(v_)
                    dup_qtd += 1
            dup_total = round(dup_total, 2)

            if qtd:
                marketplaces.append({
                    "id": "mercado_livre", "nome": "Mercado Livre",
                    "total": round(max(0.0, soma - dup_total), 2),
                    "qtd": max(0, qtd - dup_qtd),
                    "descontado_comercial": {"total": dup_total, "qtd": dup_qtd},
                    # Serie comeca em serie_desde: periodo pedido antes disso vem
                    # incompleto, e a tela avisa em vez de fingir que ML nao vendia.
                    "cobre_periodo": desde_ml <= de,
                })

            # Site proprio: serie diaria, mesmo tratamento do ML — INCLUSIVE o
            # desconto do que o time lancou.
            #
            # Ate 02/09/2026 este bloco assumia que venda de site nunca passa por
            # vendedor, e por isso nao descontava nada. A operacao e outra: quem
            # entra no site clica no botao do WhatsApp e cai no atendimento; o
            # vendedor que manda o link e fecha lanca a venda pra ganhar comissao,
            # e essa venda JA esta nos pedidos pagos do painel do Vaapt. Eram 12
            # vendas (R$ 8.458,37 entre 13/08 e 02/09) contadas duas vezes no
            # consolidado. Mesma regra do ML: fica inteira no comercial (comissao
            # e meta do vendedor nao mudam) e sai da fatia do site.
            st = ler_json(resolver_pasta_dados() / "site_conta.json", None) or {}
            serie_st = (st.get("vendas") or {}).get("serie_dia") or {}
            soma_t = round(sum(x.get("total", 0) for d, x in serie_st.items() if de <= d <= ate), 2)
            qtd_t = sum(x.get("qtd", 0) for d, x in serie_st.items() if de <= d <= ate)
            dup_st_total, dup_st_qtd = 0.0, 0
            for v_ in vendas.values():
                if (v_.get("tipo", "venda") == "venda" and de <= v_["data"] <= ate
                        and str(v_.get("canal") or "").startswith("Site")):
                    dup_st_total += valor_liquido(v_)
                    dup_st_qtd += 1
            dup_st_total = round(dup_st_total, 2)
            if qtd_t:
                marketplaces.append({
                    "id": "site", "nome": "Site próprio",
                    "total": round(max(0.0, soma_t - dup_st_total), 2),
                    "qtd": max(0, qtd_t - dup_st_qtd),
                    "descontado_comercial": {"total": dup_st_total, "qtd": dup_st_qtd},
                    "cobre_periodo": ((st.get("vendas") or {}).get("serie_desde") or "9999") <= de,
                })

            # Shopee vem do relatorio mensal do Seller Centre (importar_shopee_stats):
            # mes cheio dentro do filtro soma exato; mes cortado no meio rateia por
            # dia, o mesmo criterio do Meta e da agencia. Quando a API entrar com
            # serie diaria, este bloco muda de fonte e o painel nem percebe.
            #
            # Sao DUAS lojas desde 02/09/2026 — nevadaecopecas (1) e gabrielanevada
            # (2). A conta e identica nas duas, entao mora numa funcao so: duplicar
            # o rateio seria duplicar o lugar onde ele pode divergir depois.
            d_de, d_ate = data_de_texto(de), data_de_texto(ate)

            def bloco_shopee(arquivo, id_, nome):
                shp = ler_json(resolver_pasta_dados() / arquivo, None) or {}
                serie_shp = (shp.get("vendas") or {}).get("serie_mes") or {}
                serie_shp_dia = (shp.get("vendas") or {}).get("serie_dia") or {}
                if not serie_shp and not serie_shp_dia:
                    return          # loja que ainda nao teve planilha importada
                soma_s, qtd_s, rateado = 0.0, 0.0, False

                # Dia a dia primeiro (exportacao de periodo curto): soma exata, sem
                # rateio. O mes que tem cobertura diaria ignora a linha mensal logo
                # abaixo — senao a mesma venda entraria duas vezes.
                meses_com_dia = set()
                for dia_chave, dd in serie_shp_dia.items():
                    meses_com_dia.add(dia_chave[:7])
                    if de <= dia_chave <= ate:
                        soma_s += dd.get("total", 0)
                        qtd_s += dd.get("qtd", 0)

                for mes_chave, mm in serie_shp.items():
                    if mes_chave in meses_com_dia:
                        continue
                    ano, mes_n = int(mes_chave[:4]), int(mes_chave[5:7])
                    dias_mes = calendar.monthrange(ano, mes_n)[1]
                    ini_m = max(d_de, date(ano, mes_n, 1))
                    fim_m = min(d_ate, date(ano, mes_n, dias_mes))
                    if ini_m > fim_m:
                        continue
                    fracao = ((fim_m - ini_m).days + 1) / dias_mes
                    if fracao < 1:
                        rateado = True
                    soma_s += mm.get("total", 0) * fracao
                    qtd_s += mm.get("qtd", 0) * fracao

                if qtd_s >= 0.5:
                    todos = {**serie_shp, **{k[:7]: 1 for k in serie_shp_dia}}
                    marketplaces.append({
                        "id": id_, "nome": nome,
                        "total": round(soma_s, 2), "qtd": int(round(qtd_s)),
                        "rateado": rateado,
                        "cobre_periodo": min(todos) <= de[:7] and ate[:7] <= max(todos),
                    })
                    return
                # A serie diaria e mais precisa que a mensal pra dizer ate quando o
                # dado vai; misturar as duas gerava "ate 08/2026" quando na verdade
                # havia dado diario ate 27/08.
                if serie_shp_dia:
                    ult = max(serie_shp_dia)
                    ate_quando = f"{ult[8:10]}/{ult[5:7]}"
                else:
                    ult = max(serie_shp)
                    ate_quando = f"{ult[5:7]}/{ult[:4]}"
                marketplaces_ausentes.append({
                    "id": id_, "nome": nome,
                    "motivo": ("sem venda neste período" if de <= ult
                               else f"planilha importada — dados até {ate_quando}"),
                })

            bloco_shopee("shopee_conta.json", "shopee", "Shopee 1")
            bloco_shopee("shopee_conta_2.json", "shopee_2", "Shopee 2")

            # Mesma regra pros canais de serie diaria: existe historico, mas nada
            # dentro do periodo filtrado.
            # "Nao vendeu" e "ainda nao sincronizamos esse dia" sao coisas
            # diferentes, e confundir as duas faz o gestor achar que o dia foi
            # zerado quando na verdade o dado nao chegou. O ultimo dia da serie
            # decide qual das duas e.
            if not qtd and serie_ml:
                ultimo_ml = max(serie_ml)
                marketplaces_ausentes.append({
                    "id": "mercado_livre", "nome": "Mercado Livre",
                    "motivo": ("sem venda registrada neste período"
                               if de <= ultimo_ml
                               else f"ainda não sincronizado — dados até {ultimo_ml[8:10]}/{ultimo_ml[5:7]}"),
                })
            if not qtd_t and serie_st:
                ultimo_st = max(serie_st)
                marketplaces_ausentes.append({
                    "id": "site", "nome": "Site próprio",
                    "motivo": ("sem pedido pago neste período"
                               if de <= ultimo_st
                               else f"leitura manual — dados até {ultimo_st[8:10]}/{ultimo_st[5:7]}"),
                })
        return marketplaces, marketplaces_ausentes

    marketplaces, marketplaces_ausentes = _marketplaces_periodo(de, ate)

    # Consolidado do periodo atual: e o numero que aparece grande no cartao.
    cons_total = round(total_geral + sum(m["total"] for m in marketplaces), 2)
    cons_qtd = qtd_vendas_geral + sum(m["qtd"] for m in marketplaces)
    cons_ticket = round(cons_total / cons_qtd, 2) if cons_qtd else 0.0

    # ---- comparacao com o periodo anterior ----
    # Mesmo numero de dias, nao o mes calendario: no dia 1o, comparar 1 dia
    # contra 31 mostraria uma queda que e so o calendario andando.
    #
    # Reusa calcular_comissao(), a mesma funcao do periodo atual. Reescrever a
    # soma aqui criaria duas contas de comissao que um dia divergiriam — e a
    # que ninguem olha e a que fica errada.
    d1, d2 = data_de_texto(de), data_de_texto(ate)
    dias_janela = (d2 - d1).days + 1
    anterior_kpis = None
    if 0 < dias_janela <= 400:
        ant_ate = (d1 - timedelta(days=1))
        ant_de = ant_ate - timedelta(days=dias_janela - 1)
        a_de, a_ate = ant_de.isoformat(), ant_ate.isoformat()
        a_total = a_com = 0.0
        a_qtd = 0
        for vid in ids_alvo:
            calc = calcular_comissao(vid, a_de, a_ate, vendedores, vendas)
            a_total += calc["total_vendido"]
            a_com += calc["comissao"]
            a_qtd += sum(1 for v in vendas.values()
                         if v["vendedor_id"] == vid and v.get("tipo", "venda") == "venda"
                         and a_de <= v["data"] <= a_ate)
        # O cartao mostra o CONSOLIDADO (comercial + marketplaces). Comparar
        # consolidado contra so-comercial mostraria uma queda gigante que e so
        # a falta do ML e da Shopee do outro lado da conta.
        a_mkts, _ = _marketplaces_periodo(a_de, a_ate)
        a_total += sum(m["total"] for m in a_mkts)
        a_qtd += sum(m["qtd"] for m in a_mkts)
        a_ticket = round(a_total / a_qtd, 2) if a_qtd else 0.0

        def _var_kpi(agora, antes):
            """Sem base anterior devolve None: nao existe 'subiu 100%' partindo
            do zero, e mostrar isso engana mais do que nao mostrar nada."""
            if not antes:
                return None
            return round(100 * (agora - antes) / antes, 1)

        anterior_kpis = {
            "de": a_de, "ate": a_ate, "dias": dias_janela,
            "total_geral": round(a_total, 2),
            "comissao_geral": round(a_com, 2),
            "qtd_vendas_geral": a_qtd,
            "ticket_medio": a_ticket,
            "var": {
                # Contra o consolidado dos dois lados. Comissao e a excecao:
                # marketplace nao paga comissao a ninguem, entao ela compara
                # comercial com comercial.
                "total_geral": _var_kpi(cons_total, a_total),
                "comissao_geral": _var_kpi(comissao_geral, a_com),
                "qtd_vendas_geral": _var_kpi(cons_qtd, a_qtd),
                "ticket_medio": _var_kpi(cons_ticket, a_ticket),
            },
        }


    return jsonify({
        "de": de,
        "ate": ate,
        "mes_unico": mes_unico,
        "meta_grupo": float((metas_todas.get("grupo") or {}).get("mensal", 0) or 0),
        "ritmo": ritmo,
        "vendedor_id": filtro_vendedor,
        "vendedores": resultado,
        "total_geral": round(total_geral, 2),
        "comissao_geral": round(comissao_geral, 2),
        "qtd_vendas_geral": qtd_vendas_geral,
        "ticket_medio": ticket_medio,
        "anterior": anterior_kpis,
        "serie_mensal": serie_mensal,
        "serie_diaria": serie_diaria,
        "marketplaces": marketplaces,
        "marketplaces_ausentes": marketplaces_ausentes,
    })
