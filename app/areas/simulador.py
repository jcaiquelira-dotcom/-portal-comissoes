# -*- coding: utf-8 -*-
"""Area `simulador` do portal — rotas e helpers privados. Extraida do server.py em
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
    DATABASE_URL,
    LOGIN_JANELA_MINUTOS,
    PRODUCAO,
    SECRET_KEY_FILE,
    SEGREDOS_DIR,
    STATIC_DIR,
    _hash_senha,
    _senha_confere,
    app,
    areas_do_usuario,
    calcular_comissao,
    carregar_vendas_para_comissao,
    carregar_vendedores,
    desligado,
    excedeu_tentativas_login,
    exigir_vendedor,
    hoje_br,
    jsonify,
    mes_para_intervalo,
    registrar_acesso,
    request,
    salvar_vendedores,
    secrets,
    send_from_directory,
    session,
    timedelta,
    usuario_master,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("_db_escrever", "_db_ler",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

def obter_secret_key() -> str:
    if DATABASE_URL:
        chave = _db_ler("secret_key", None)
        if not chave:
            chave = secrets.token_hex(32)
            _db_escrever("secret_key", chave)
        return chave
    if not SECRET_KEY_FILE.exists():
        SEGREDOS_DIR.mkdir(parents=True, exist_ok=True)
        SECRET_KEY_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()

app.secret_key = obter_secret_key()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCAO,
    # 7 dias, nao 2 horas: o portal e ferramenta de dia inteiro, e a sessao
    # morrendo no meio do expediente derrubou o gestor duas vezes num so dia
    # (01/09/2026). Logout continua existindo pra maquina compartilhada.
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/admin.html")
def admin_page():
    return send_from_directory(STATIC_DIR, "admin.html")

@app.route("/painel.html")
def painel_page():
    return send_from_directory(STATIC_DIR, "painel.html")

@app.route("/api/vendedores-publico")
def api_vendedores_publico():
    vendedores = carregar_vendedores()
    return jsonify([{"id": vid, "nome": v["nome"]} for vid, v in sorted(vendedores.items(), key=lambda kv: kv[1]["nome"])])

@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True)
    vendedor_id = (body.get("vendedor_id") or "").strip()
    senha = body.get("senha") or ""
    if excedeu_tentativas_login("vendedor", vendedor_id):
        return jsonify({"erro": f"Muitas tentativas erradas. Aguarde {LOGIN_JANELA_MINUTOS} minutos e tente de novo."}), 429
    vendedores = carregar_vendedores()
    v = vendedores.get(vendedor_id)
    if not v or not _senha_confere(v.get("senha"), senha):
        registrar_acesso("vendedor", False, vendedor_id, v["nome"] if v else None)
        return jsonify({"erro": "Vendedor ou senha inválidos."}), 401
    session.clear()
    # Promocao silenciosa: senha antiga em texto puro vira hash no primeiro
    # login que der certo. Ninguem precisa trocar senha pra migracao acontecer.
    if not str(v.get("senha") or "").startswith(("pbkdf2:", "scrypt:")):
        vendedores[vendedor_id]["senha"] = _hash_senha(senha)
        salvar_vendedores(vendedores)
    # Uma sessao, uma identidade. Entrar como vendedor DERRUBA a sessao de
    # gestor que estivesse aberta: sem isso o portal do vendedor montava o menu
    # do gestor (o Pedro via 15 areas em vez de 3), e clicar em qualquer item
    # de gestao jogava a pessoa de volta pro /admin.html — que da cadeira de
    # quem clicou parece o portal quebrando.
    session.pop("admin", None)
    session.permanent = True      # sem isso o lifetime acima nem se aplica
    session["vendedor_id"] = vendedor_id
    # Quem administra entra com nome. Era exatamente isso que a senha sem dono
    # nao permitia registrar.
    registrar_acesso("master" if v.get("master") else "vendedor",
                     True, vendedor_id, v["nome"])
    return jsonify({"ok": True, "nome": v["nome"]})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/ambiente")
def api_ambiente():
    """Diz se este servidor é o de produção ou uma cópia local.

    Sem isso as duas telas são idênticas, e já aconteceu três vezes de alguém
    olhar o portal local — com dados congelados e sem os arquivos de
    atendimento — e concluir que produção estava quebrada. Público de propósito:
    o aviso precisa aparecer antes do login, que é onde a confusão começa."""
    # A versao e o carimbo dos arquivos da tela. A pagina guarda o valor que
    # recebeu ao abrir e reconfere de tempos em tempos: mudou, e porque saiu
    # publicacao nova e aquela aba esta velha. Sem isso, quem deixa a janela
    # aberta o dia todo (atalho do Chrome em modo app) fica vendo a versao
    # antiga e concluindo que o portal esta com defeito.
    try:
        marcas = [(STATIC_DIR / nome).stat().st_mtime
                  for nome in ("index.html", "admin.html", "portal-nav.js")
                  if (STATIC_DIR / nome).exists()]
        versao = str(int(max(marcas))) if marcas else "0"
    except OSError:
        versao = "0"
    return jsonify({"local": not bool(DATABASE_URL), "versao": versao})

@app.route("/api/me")
def api_me():
    vendedor_id = session.get("vendedor_id")
    if not vendedor_id:
        return jsonify({"logado": False}), 401
    vendedores = carregar_vendedores()
    v = vendedores.get(vendedor_id)
    if not v:
        session.clear()
        return jsonify({"logado": False}), 401
    return jsonify({
        "logado": True,
        "id": vendedor_id,
        "nome": v["nome"],
        "percentual": v.get("percentual", 0),
        # A tela precisa AVISAR antes, nao deixar a pessoa preencher um
        # lancamento inteiro pra descobrir no botao que nao pode.
        "somente_leitura": desligado(v),
        "desligado_em": v.get("desligado_em") or "",
        # O menu desta tela e o mesmo da outra: precisa das areas pra se montar.
        "areas": areas_do_usuario(),
        "master": usuario_master(),
    })

@app.route("/api/minha-comissao")
def api_minha_comissao():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    mes = request.args.get("mes", hoje_br().isoformat()[:7])
    de, ate = mes_para_intervalo(mes)
    vendedores = carregar_vendedores()
    vendas = carregar_vendas_para_comissao(vendedor_id, vendedores)
    return jsonify(calcular_comissao(vendedor_id, de, ate, vendedores, vendas))
