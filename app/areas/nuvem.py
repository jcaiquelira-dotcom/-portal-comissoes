# -*- coding: utf-8 -*-
"""Area `nuvem` do portal — rotas e helpers privados. Extraida do server.py em
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
    app,
    exigir_admin,
    jsonify,
    request,
    resolver_pasta_dados,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("_sn", "_sn_atual", "_sn_chave", "_sn_gravar", "escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

@app.route("/api/admin/analytics")
def api_admin_analytics():
    """Google Analytics do site: o que acontece ANTES do WhatsApp.

    O painel ja sabia o lead (Totalk) e a venda (portal). O que faltava era o
    meio: quanta gente chegou no site, por onde, que peca olhou e onde parou.
    """
    if not exigir_admin():
        return jsonify({"erro": "Nao autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "analytics_site.json", None)
    if not d or not d.get("serie_dia"):
        return jsonify({"sem_dados": True})

    de = request.args.get("de") or ""
    ate = request.args.get("ate") or ""
    serie = d["serie_dia"]
    dias = sorted(k for k in serie if (not de or k >= de) and (not ate or k <= ate))

    # Sem dia no periodo: a serie tem inicio e fim, e dizer isso e melhor do
    # que devolver zero — o mesmo criterio do card do Perfil da Empresa.
    todos = sorted(serie)
    if not dias:
        return jsonify({"sem_cobertura": True,
                        "cobertura": {"de": todos[0], "ate": todos[-1]},
                        "gerado_em": d.get("gerado_em")})

    def soma(campo):
        return sum(serie[k].get(campo, 0) for k in dias)

    sessoes = soma("sessoes")
    # Rejeicao e duracao sao MEDIAS: somar daria numero sem sentido. Pondera
    # pelas sessoes do dia, senao um dia de 3 visitas pesa igual a um de 300.
    peso = sum(serie[k]["sessoes"] for k in dias) or 1
    rejeicao = round(sum(serie[k]["rejeicao"] * serie[k]["sessoes"]
                         for k in dias) / peso, 1)
    duracao = round(sum(serie[k]["duracao_media"] * serie[k]["sessoes"]
                        for k in dias) / peso, 1)

    canais = {}
    for k in dias:
        for nome, v in (d.get("origem_dia", {}).get(k) or {}).items():
            c = canais.setdefault(nome, {"canal": nome, "sessoes": 0, "usuarios": 0})
            c["sessoes"] += v.get("sessoes", 0)
            c["usuarios"] += v.get("usuarios", 0)
    canais = sorted(canais.values(), key=lambda c: -c["sessoes"])

    eventos = {}
    for k in dias:
        for nome, n in (d.get("eventos_dia", {}).get(k) or {}).items():
            eventos[nome] = eventos.get(nome, 0) + n
    eventos = sorted(({"evento": k, "n": v} for k, v in eventos.items()),
                     key=lambda e: -e["n"])

    return jsonify({
        "de": dias[0], "ate": dias[-1], "dias": len(dias),
        "gerado_em": d.get("gerado_em"),
        "cobertura": {"de": todos[0], "ate": todos[-1]},
        "total": {
            "sessoes": sessoes,
            "usuarios": soma("usuarios"),
            "novos": soma("novos"),
            "paginas_vistas": soma("paginas_vistas"),
            "rejeicao": rejeicao,
            "duracao_media": duracao,
            "paginas_por_sessao": round(soma("paginas_vistas") / sessoes, 2)
            if sessoes else 0,
        },
        "serie_dia": [{"data": k, **serie[k]} for k in dias],
        "canais": canais,
        "eventos": eventos,
        # As paginas nao tem data (seria uma linha por pagina por dia, o que
        # estoura o limite da API). Vem do periodo inteiro da coleta, e o
        # painel precisa dizer isso em vez de fingir que acompanha o filtro.
        "paginas": d.get("paginas", []),
        "paginas_periodo": d.get("paginas_periodo") or {},
    })

@app.route("/api/admin/perfil-google")
def api_admin_perfil_google():
    """Google Perfil da Empresa: quem acha a loja no Maps/Busca e liga."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "perfil_google.json", None)
    if not d or not d.get("serie_dia"):
        return jsonify({"sem_dados": True})
    return jsonify(d)

@app.route("/api/admin/sincronizar-perfil", methods=["POST"])
def api_admin_sincronizar_perfil():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    try:
        def ler_atual():
            return ler_json(resolver_pasta_dados() / "perfil_google.json", None)

        def gravar(d):
            escrever_json(resolver_pasta_dados() / "perfil_google.json", d)

        return jsonify({"ok": True,
                        "resumo": _sn.sincronizar_perfil(_sn_chave, ler_atual, gravar)})
    except Exception as e:
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 502

@app.route("/api/admin/sincronizar-gasto", methods=["POST"])
def api_admin_sincronizar_gasto():
    """Dispara na hora a atualizacao de Google+Meta — o mesmo que o thread
    diario faz as 06:45. Serve pro gestor nao esperar o ciclo quando quer o
    numero fresco agora."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    try:
        resumo = _sn.sincronizar_gasto(_sn_chave, _sn_atual, _sn_gravar)
        return jsonify({"ok": True, "resumo": resumo})
    except Exception as e:
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 502

# ---------- Mercado Livre na nuvem (thread de fundo) ----------
# O refresh_token do ML rotaciona a cada uso, entao a credencial mora no banco
# (segredo_ml) e quem renova grava la antes de tudo — detalhes em
# app/sincronizador_ml_nuvem.py. O script local le da mesma chave.
try:
    import sincronizador_ml_nuvem as _sml

    def _sml_cred():
        return ler_json(resolver_pasta_dados() / "segredo_ml.json", None)

    def _sml_gravar_cred(cred):
        escrever_json(resolver_pasta_dados() / "segredo_ml.json", cred)

    def _sml_atual():
        return ler_json(resolver_pasta_dados() / "ml_conta.json", None)

    def _sml_gravar(pacote):
        escrever_json(resolver_pasta_dados() / "ml_conta.json", pacote)

    _sml.iniciar(_sml_cred, _sml_gravar_cred, _sml_atual, _sml_gravar)
except Exception as _e:
    print(f"[sinc-ml] não subiu: {_e}")

@app.route("/api/admin/sincronizar-ml", methods=["POST"])
def api_admin_sincronizar_ml():
    """Dispara a atualizacao do Mercado Livre na hora."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    try:
        resumo = _sml.sincronizar(_sml_cred, _sml_gravar_cred, _sml_atual, _sml_gravar)
        return jsonify({"ok": True, "resumo": resumo})
    except Exception as e:
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 502

# ---------- faturamento do ML (thread de fundo, 1x por dia) ----------
# Uma vez por dia porque o limitador do ML e apertado e o dado e estatico
# durante o dia. A coleta e retomavel: se o limite bater no meio, ela guarda
# onde parou e continua no dia seguinte em vez de comecar do zero.
try:
    import coletor_faturamento_ml as _cfat

    def _cfat_atual():
        return ler_json(resolver_pasta_dados() / "ml_faturamento.json", None)

    def _cfat_gravar(p):
        escrever_json(resolver_pasta_dados() / "ml_faturamento.json", p)

    _cfat.iniciar(_sml_cred, _cfat_atual, _cfat_gravar)
except Exception as _e:
    print(f"[faturamento-ml] não subiu: {_e}")
