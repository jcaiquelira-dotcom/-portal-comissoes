# -*- coding: utf-8 -*-
"""Area `metas_bonus` do portal — rotas e helpers privados. Extraida do server.py em
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
    agora_br,
    app,
    exigir_admin,
    jsonify,
    re,
    request,
    resolver_pasta_dados,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("escrever_json", "ler_json",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

@app.route("/api/admin/ml-conta")
def api_admin_ml_conta():
    """Saude da conta Mercado Livre: reputacao oficial, pos-venda e Product
    Ads, empurrados por scripts/sincronizar_ml.py a partir do ml-dashboard."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "ml_conta.json", None)
    if not d:
        return jsonify({"sem_dados": True})
    return jsonify(d)

@app.route("/api/admin/site-conta", methods=["POST"])
def api_admin_site_gravar():
    """Recebe a serie diaria do site proprio (vaapt). O painel de la so filtra
    um dia por vez e nao expoe API de pedidos, entao a leitura das paginas e
    feita no navegador do gestor, ja logado, e o resultado chega aqui.
    Merge por dia: releitura atualiza os dias lidos e preserva os antigos."""
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    serie = corpo.get("serie_dia")
    if not isinstance(serie, dict) or not serie:
        return jsonify({"erro": "Série vazia."}), 400

    limpa = {}
    for dia, val in serie.items():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(dia)):
            return jsonify({"erro": f"Data inválida: {dia}"}), 400
        try:
            limpa[dia] = {"total": round(float(val.get("total") or 0), 2),
                          "qtd": int(val.get("qtd") or 0)}
        except (TypeError, ValueError, AttributeError):
            return jsonify({"erro": f"Valor inválido em {dia}"}), 400

    atual = ler_json(resolver_pasta_dados() / "site_conta.json", None) or {}
    antiga = (atual.get("vendas") or {}).get("serie_dia") or {}
    juntas = {**antiga, **limpa}
    escrever_json(resolver_pasta_dados() / "site_conta.json", {
        "gerado_em": agora_br().isoformat(timespec="seconds"),
        "fonte": corpo.get("fonte") or "painel do site (pedidos pagos)",
        "vendas": {"serie_dia": juntas, "serie_desde": min(juntas)},
    })
    return jsonify({"ok": True, "dias": len(juntas)})

@app.route("/api/admin/site-conta")
def api_admin_site_conta():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    d = ler_json(resolver_pasta_dados() / "site_conta.json", None)
    if not d or not (d.get("vendas") or {}).get("serie_dia"):
        return jsonify({"sem_dados": True})
    return jsonify(d)
