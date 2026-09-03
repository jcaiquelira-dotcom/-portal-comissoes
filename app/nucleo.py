# -*- coding: utf-8 -*-
"""Nucleo do portal: configuracao, app Flask, banco, autenticacao e o que
toda area usa. Nasceu em 03/09/2026 (Fase 4 da SIMPLIFICACAO.md) do server.py
de 6.578 linhas — o corte foi por CAMADA calculada (o que e usado por mais de
uma area vem pra ca, com fecho transitivo), nao pelos cabecalhos de comentario,
que nao refletiam a arquitetura real. Nada aqui mudou de comportamento: o texto
de cada funcao e o mesmo do server.py de antes, so mudou de arquivo.

As areas (areas/*.py) importam daqui explicitamente o que usam e registram
suas rotas no mesmo `app`. O server.py continua sendo o ponto de entrada.
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

# Servidor (Render) roda em UTC — Brasil não tem horário de verão desde 2019,
# então um offset fixo de -3h é sempre correto, sem depender de tzdata/IANA.
FUSO_BRASILIA = timezone(timedelta(hours=-3))

def agora_br() -> datetime:
    return datetime.now(FUSO_BRASILIA)

def hoje_br() -> date:
    return agora_br().date()

def parse_dt_tolerante(valor: str) -> datetime:
    """Converte uma string ISO em datetime timezone-aware. Registros gravados
    antes da correção de fuso ficaram sem offset, no horário UTC do servidor
    (Render) — nesse caso assumimos UTC. Registros novos já vêm com -03:00."""
    dt = datetime.fromisoformat(valor)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = ROOT / "config.json"

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Credenciais, senhas e vendas ficam sempre locais, na pasta segredos/dados.
SEGREDOS_DIR = ROOT / "segredos"

VENDEDORES_FILE = SEGREDOS_DIR / "vendedores.json"

CREDENCIAIS_FILE = SEGREDOS_DIR / "credenciais.json"

SECRET_KEY_FILE = SEGREDOS_DIR / "secret_key.txt"

MESES_FECHADOS_FILE = SEGREDOS_DIR / "meses_fechados.json"

LOG_ACESSOS_FILE = SEGREDOS_DIR / "log_acessos.json"

LOG_ACOES_FILE = SEGREDOS_DIR / "log_acoes.json"

DATA_DIR_NAME = "data"

DIAS_MAXIMOS_RETROATIVOS = 7

LIBERACAO_RETROATIVA_MINUTOS = 5

MAX_LOG_ACESSOS = 500

MAX_LOG_ACOES = 500

LOGIN_MAX_TENTATIVAS = 5

LOGIN_JANELA_MINUTOS = 15

PRODUCAO = os.environ.get("PORTAL_PRODUCAO") == "1"

FOTOS_DIR = STATIC_DIR / "fotos"

EXTENSOES_FOTO_PERMITIDAS = {"jpg", "jpeg", "png", "webp"}

METAS_FILE_NAME = "metas.json"

# Em produção (Render), não há disco persistente: os dados ficam num banco
# Postgres (Supabase) e as fotos num bucket, escolhidos por variável de ambiente.
# Sem essas variáveis (uso local), tudo continua indo pra arquivo, como sempre foi.
DATABASE_URL = os.environ.get("DATABASE_URL")

SUPABASE_URL = os.environ.get("SUPABASE_URL")

SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

def carregar_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def resolver_pasta_dados() -> Path:
    config = carregar_config()
    data_dir = Path(config["data_dir"])
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def _cache_requisicao():
    """Memória de curta duração, válida só durante uma requisição. Várias
    funções carregam o mesmo dado (ex.: `carregar_vendedores()` é chamada 2-3x
    por requisição) — sem isso, cada chamada dessas era uma ida ao banco."""
    if not has_request_context():
        return None
    if not hasattr(g, "_cache_db"):
        g._cache_db = {}
    return g._cache_db

def _chave_de(caminho: Path) -> str:
    """Deriva uma chave curta e única a partir do nome do arquivo (sem extensão) —
    todos os arquivos do projeto já têm nomes únicos (vendedores, vendas_brenda, etc.)."""
    return Path(caminho).stem

if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import Json as _PgJson

    # Reaproveita uma única conexão em vez de abrir uma nova a cada leitura/
    # escrita. Antes, um clique em "Adicionar venda" abria ~17 conexões novas
    # (cada uma com handshake TLS completo com o Supabase), o que deixava o
    # portal lento a ponto do vendedor achar que travou e clicar de novo —
    # gerando venda duplicada. Detalhe importante: `with psycopg2.connect(...)`
    # NÃO fecha a conexão (só encerra a transação), então o modelo antigo ainda
    # dependia do coletor de lixo pra liberar cada conexão.
    _conn_cache = {"conn": None}

    def _db_descartar_conexao(conn) -> None:
        """Some com uma conexão que deu erro, pra próxima operação abrir uma
        nova e limpa em vez de reaproveitar uma transação abortada."""
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        if _conn_cache.get("conn") is conn:
            _conn_cache["conn"] = None
        cache = _cache_requisicao()
        if cache is not None:
            cache.pop("_conexao_ok", None)

    def _db_conectar():
        conn = _conn_cache["conn"]
        cache = _cache_requisicao()
        if conn is not None and conn.closed == 0:
            # A conexão reaproveitada pode ter caído entre uma requisição e
            # outra (Supabase derruba conexões ociosas), então confirmamos que
            # ainda está viva — mas só uma vez por requisição, senão esse
            # "SELECT 1" viraria uma ida ao banco a cada leitura.
            if cache is not None and cache.get("_conexao_ok"):
                return conn
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                if cache is not None:
                    cache["_conexao_ok"] = True
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        conn = psycopg2.connect(DATABASE_URL)
        _conn_cache["conn"] = conn
        if cache is not None:
            cache["_conexao_ok"] = True
        return conn

    def _db_preparar_tabela() -> None:
        conn = _db_conectar()
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS dados_json ("
                "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)"
            )
            conn.commit()

    _db_preparar_tabela()

    def _db_ler(chave: str, padrao):
        cache = _cache_requisicao()
        if cache is not None and chave in cache:
            return cache[chave]
        conn = _db_conectar()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT valor FROM dados_json WHERE chave = %s", (chave,))
                linha = cur.fetchone()
                valor = linha[0] if linha else padrao
        except Exception:
            # Sem o rollback, a conexão reaproveitada ficaria travada em
            # "transação abortada" e derrubaria as próximas consultas também.
            _db_descartar_conexao(conn)
            raise
        if cache is not None:
            cache[chave] = valor
        return valor

    def _db_escrever(chave: str, dados) -> None:
        conn = _db_conectar()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    (chave, _PgJson(dados)),
                )
                conn.commit()
        except Exception:
            _db_descartar_conexao(conn)
            raise
        cache = _cache_requisicao()
        if cache is not None:
            cache[chave] = dados

    def ler_json(caminho: Path, padrao):
        return _db_ler(_chave_de(caminho), padrao)

    def escrever_json(caminho: Path, dados) -> None:
        _db_escrever(_chave_de(caminho), dados)

else:
    def ler_json(caminho: Path, padrao):
        if caminho.exists():
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        return padrao

    def escrever_json(caminho: Path, dados) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)

# ============================================================
# Simulador de desconto e parcelamento
# ============================================================
# Auto-contido aqui dentro (antigo projeto irmão portal-simulador,
# incorporado): o catálogo ERP e as regras vivem em data/simulador/, geridos
# pela área do gestor em Configurações. scripts/etl_simulador.py reimporta
# as planilhas do ERP — grava em SQLite local sem DATABASE_URL, ou direto no
# Postgres de produção quando DATABASE_URL está setada (mesmo rodando local,
# pra empurrar uma atualização pra produção).
_SIMULADOR_DB_LOCAL = ROOT / "data" / "simulador" / "simulador.db"

_unaccent_disponivel_simulador = {"valor": False}

if DATABASE_URL:
    def _simulador_preparar_schema() -> None:
        with _db_conectar() as conn, conn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
                conn.commit()
                _unaccent_disponivel_simulador["valor"] = True
            except Exception:
                conn.rollback()
                _unaccent_disponivel_simulador["valor"] = False
            cur.execute("""
                CREATE TABLE IF NOT EXISTS catalogo_erp (
                    id SERIAL PRIMARY KEY,
                    cod_peca TEXT NOT NULL UNIQUE,
                    nome_produto TEXT NOT NULL,
                    etiqueta TEXT,
                    preco DOUBLE PRECISION,
                    qtd_disponivel INTEGER NOT NULL DEFAULT 0,
                    condicao TEXT,
                    localizacao TEXT,
                    codigo_veiculo TEXT,
                    categoria_auto TEXT NOT NULL,
                    tipo_peca_auto TEXT NOT NULL,
                    tipo_peca_rotulo TEXT NOT NULL,
                    apelido_veiculo TEXT,
                    data_compra_veiculo TEXT,
                    tempo_estoque_conhecido INTEGER NOT NULL DEFAULT 0,
                    classe_abc TEXT NOT NULL DEFAULT 'C',
                    ingestido_em TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_catalogo_disponivel ON catalogo_erp(qtd_disponivel)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS overrides_estoque (
                    cod_peca TEXT PRIMARY KEY,
                    data_entrada TEXT NOT NULL,
                    definido_por TEXT,
                    definido_em TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS etl_execucoes (
                    id SERIAL PRIMARY KEY,
                    executado_em TEXT NOT NULL,
                    linhas_catalogo INTEGER,
                    linhas_disponiveis INTEGER,
                    linhas_com_tempo_estoque INTEGER,
                    duracao_seg DOUBLE PRECISION
                )
            """)
            conn.commit()

    _simulador_preparar_schema()

def _simulador_db_local():
    conn = sqlite3.connect(_SIMULADOR_DB_LOCAL)
    conn.row_factory = sqlite3.Row
    return conn

def buscar_pecas_simulador(q: str) -> list:
    if DATABASE_URL:
        termo = f"%{q}%"
        campos = (
            "cod_peca, nome_produto, etiqueta, preco, classe_abc, apelido_veiculo, "
            "tempo_estoque_conhecido, tipo_peca_rotulo"
        )
        if _unaccent_disponivel_simulador["valor"]:
            condicao = (
                "(unaccent(nome_produto) ILIKE unaccent(%s) OR unaccent(cod_peca) ILIKE unaccent(%s) "
                "OR unaccent(coalesce(etiqueta, '')) ILIKE unaccent(%s))"
            )
        else:
            condicao = "(nome_produto ILIKE %s OR cod_peca ILIKE %s OR coalesce(etiqueta, '') ILIKE %s)"
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {campos} FROM catalogo_erp WHERE qtd_disponivel > 0 AND {condicao} LIMIT 30",
                (termo, termo, termo),
            )
            colunas = [d[0] for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]

    conn = _simulador_db_local()
    termo = f"%{q}%"
    linhas = conn.execute(
        "SELECT cod_peca, nome_produto, etiqueta, preco, classe_abc, apelido_veiculo, "
        "tempo_estoque_conhecido, tipo_peca_rotulo FROM catalogo_erp WHERE qtd_disponivel > 0 AND "
        "(nome_produto LIKE ? OR cod_peca LIKE ? OR etiqueta LIKE ?) LIMIT 30",
        (termo, termo, termo),
    ).fetchall()
    conn.close()
    return [dict(r) for r in linhas]

def obter_peca_simulador(cod_peca: str):
    if DATABASE_URL:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM catalogo_erp WHERE cod_peca = %s", (cod_peca,))
            linha = cur.fetchone()
            if not linha:
                return None
            colunas = [d[0] for d in cur.description]
            return dict(zip(colunas, linha))
    conn = _simulador_db_local()
    peca = conn.execute("SELECT * FROM catalogo_erp WHERE cod_peca = ?", (cod_peca,)).fetchone()
    conn.close()
    return dict(peca) if peca else None

def definir_data_entrada_simulador(cod_peca: str, data_entrada_str: str, definido_por: str) -> bool:
    """data_entrada_str já formatada como 'YYYY-MM-DD 00:00:00'. Retorna
    False se a peça não existe no catálogo."""
    agora = agora_br().isoformat()
    if DATABASE_URL:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM catalogo_erp WHERE cod_peca = %s", (cod_peca,))
            if not cur.fetchone():
                return False
            cur.execute(
                "INSERT INTO overrides_estoque (cod_peca, data_entrada, definido_por, definido_em) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (cod_peca) DO UPDATE SET "
                "data_entrada=EXCLUDED.data_entrada, definido_por=EXCLUDED.definido_por, "
                "definido_em=EXCLUDED.definido_em",
                (cod_peca, data_entrada_str, definido_por, agora),
            )
            cur.execute(
                "UPDATE catalogo_erp SET data_compra_veiculo=%s, tempo_estoque_conhecido=1 WHERE cod_peca=%s",
                (data_entrada_str, cod_peca),
            )
            conn.commit()
        return True

    conn = _simulador_db_local()
    peca = conn.execute("SELECT * FROM catalogo_erp WHERE cod_peca = ?", (cod_peca,)).fetchone()
    if not peca:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO overrides_estoque (cod_peca, data_entrada, definido_por, definido_em) "
        "VALUES (?,?,?,?) ON CONFLICT(cod_peca) DO UPDATE SET "
        "data_entrada=excluded.data_entrada, definido_por=excluded.definido_por, definido_em=excluded.definido_em",
        (cod_peca, data_entrada_str, definido_por, agora),
    )
    conn.execute(
        "UPDATE catalogo_erp SET data_compra_veiculo=?, tempo_estoque_conhecido=1 WHERE cod_peca=?",
        (data_entrada_str, cod_peca),
    )
    conn.commit()
    conn.close()
    return True

_REGRAS_SIMULADOR_FILE = ROOT / "data" / "simulador" / "regras.json"

def carregar_regras_simulador():
    if DATABASE_URL:
        return _db_ler("simulador_regras", None)
    if not _REGRAS_SIMULADOR_FILE.exists():
        return None
    return json.loads(_REGRAS_SIMULADOR_FILE.read_text(encoding="utf-8"))

def salvar_regras_simulador(regras) -> None:
    if DATABASE_URL:
        _db_escrever("simulador_regras", regras)
        return
    _REGRAS_SIMULADOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REGRAS_SIMULADOR_FILE.write_text(json.dumps(regras, indent=2, ensure_ascii=False), encoding="utf-8")

def status_simulador() -> dict:
    if DATABASE_URL:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM catalogo_erp")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM catalogo_erp WHERE qtd_disponivel > 0")
            disponiveis = cur.fetchone()[0]
            cur.execute(
                "SELECT executado_em, linhas_catalogo, linhas_disponiveis, linhas_com_tempo_estoque, duracao_seg "
                "FROM etl_execucoes ORDER BY id DESC LIMIT 1"
            )
            linha = cur.fetchone()
            ultima = None
            if linha:
                ultima = {
                    "executado_em": linha[0], "linhas_catalogo": linha[1],
                    "linhas_disponiveis": linha[2], "linhas_com_tempo_estoque": linha[3],
                    "duracao_seg": linha[4],
                }
        return {"total_pecas": total, "pecas_disponiveis": disponiveis, "ultima_importacao": ultima}

    conn = _simulador_db_local()
    total = conn.execute("SELECT COUNT(*) FROM catalogo_erp").fetchone()[0]
    disponiveis = conn.execute("SELECT COUNT(*) FROM catalogo_erp WHERE qtd_disponivel > 0").fetchone()[0]
    linha = conn.execute(
        "SELECT executado_em, linhas_catalogo, linhas_disponiveis, linhas_com_tempo_estoque, duracao_seg "
        "FROM etl_execucoes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "total_pecas": total, "pecas_disponiveis": disponiveis,
        "ultima_importacao": dict(linha) if linha else None,
    }

def _faixa_tempo_de_simulador(dias, regras):
    for faixa in regras["faixas_tempo"]:
        if dias >= faixa["min_dias"] and (faixa["max_dias"] is None or dias <= faixa["max_dias"]):
            return faixa
    return regras["faixas_tempo"][-1]

def _faixa_valor_de_simulador(valor, regras):
    for faixa in regras["faixas_valor"]:
        if valor >= faixa["min_valor"] and (faixa["max_valor"] is None or valor <= faixa["max_valor"]):
            return faixa
    return regras["faixas_valor"][-1]

def montar_simulacao(valor_base, curva, dias_em_estoque, desconto_escolhido_pct, regras):
    """Desconto vale só pra dinheiro/PIX à vista. Cartão de crédito NUNCA
    tem desconto, nem em 1x — todas as opções de cartão são sobre o valor
    cheio."""
    faixa_tempo = (
        _faixa_tempo_de_simulador(dias_em_estoque, regras)
        if dias_em_estoque is not None else regras["faixas_tempo"][0]
    )
    desconto_max_pct = regras["desconto_max_pct"][curva][faixa_tempo["id"]]
    nivel_flex = regras["nivel_flexibilidade"][curva][faixa_tempo["id"]]

    desconto_pct = desconto_escolhido_pct if desconto_escolhido_pct is not None else desconto_max_pct
    desconto_pct = max(0, min(desconto_pct, desconto_max_pct))
    preco_dinheiro_pix = round(valor_base * (1 - desconto_pct / 100), 2)

    parcelas_max = 1
    if valor_base >= regras["valor_minimo_parcelamento"]:
        faixa_valor = _faixa_valor_de_simulador(valor_base, regras)
        parcelas_max = regras["parcelas_max"][faixa_valor["id"]][str(nivel_flex)]
    opcoes_cartao = [
        {"parcelas": n, "valor_parcela": round(valor_base / n, 2), "valor_total": valor_base}
        for n in range(1, parcelas_max + 1)
    ]

    return {
        "valor_base": valor_base,
        "dias_em_estoque": dias_em_estoque,
        "faixa_tempo": faixa_tempo,
        "curva": curva,
        "desconto_max_pct": desconto_max_pct,
        "desconto_aplicado_pct": desconto_pct,
        "nivel_flexibilidade": nivel_flex,
        "preco_dinheiro_pix": preco_dinheiro_pix,
        "opcoes_cartao": opcoes_cartao,
    }

def calcular_simulacao_peca(peca, valor_override, desconto_escolhido_pct, regras):
    valor_base = valor_override if valor_override is not None else (peca["preco"] or 0)

    if peca["tempo_estoque_conhecido"]:
        data_compra = datetime.fromisoformat(peca["data_compra_veiculo"])
        dias_em_estoque = (agora_br().date() - data_compra.date()).days
        dias_em_estoque = max(dias_em_estoque, 0)
    else:
        dias_em_estoque = None

    curva = peca["classe_abc"]
    resultado = montar_simulacao(valor_base, curva, dias_em_estoque, desconto_escolhido_pct, regras)
    resultado["tempo_estoque_conhecido"] = bool(peca["tempo_estoque_conhecido"])
    return resultado

def carregar_vendedores() -> dict:
    return ler_json(VENDEDORES_FILE, {})

def salvar_vendedores(vendedores: dict) -> None:
    escrever_json(VENDEDORES_FILE, vendedores)

def carregar_credenciais() -> dict:
    dados = ler_json(CREDENCIAIS_FILE, None)
    if dados is None:
        dados = {"admin_senha": "troque-esta-senha"}
        escrever_json(CREDENCIAIS_FILE, dados)
    return dados

def arquivo_vendas(vendedor_id: str) -> Path:
    """Cada vendedor tem seu próprio arquivo — assim cada um só grava no que é dele."""
    return resolver_pasta_dados() / f"vendas_{vendedor_id}.json"

def carregar_vendas_vendedor(vendedor_id: str) -> dict:
    return ler_json(arquivo_vendas(vendedor_id), {})

def salvar_vendas_vendedor(vendedor_id: str, vendas: dict) -> None:
    escrever_json(arquivo_vendas(vendedor_id), vendas)

def carregar_vendas_para_comissao(vendedor_id: str, vendedores: dict) -> dict:
    """Carrega só as vendas que a comissão desse vendedor realmente usa: as
    dele e as de quem ele tem override. Antes carregava as de todo mundo (~4.400
    registros) em toda atualização de tela, mesmo pra quem não tem override."""
    info = vendedores.get(vendedor_id, {})
    ids = {vendedor_id}
    for over in info.get("overrides", []):
        outro = over.get("vendedor_id")
        if outro in vendedores:
            ids.add(outro)
    todas = {}
    for vid in ids:
        todas.update(carregar_vendas_vendedor(vid))
    return todas

def carregar_vendas_todos(vendedores: dict) -> dict:
    """Junta as vendas de todos os vendedores — usado só para comissão com
    overrides e para o resumo do gestor, que precisam da visão completa."""
    todas = {}
    for vid in vendedores:
        todas.update(carregar_vendas_vendedor(vid))
    return todas

def carregar_meses_fechados() -> list:
    return ler_json(MESES_FECHADOS_FILE, [])

def salvar_meses_fechados(meses: list) -> None:
    escrever_json(MESES_FECHADOS_FILE, sorted(set(meses)))

def mes_esta_fechado(data_venda: str) -> bool:
    return data_venda[:7] in carregar_meses_fechados()

def arquivo_confirmacoes(vendedor_id: str) -> Path:
    return resolver_pasta_dados() / f"confirmacoes_{vendedor_id}.json"

def carregar_confirmacoes(vendedor_id: str) -> dict:
    return ler_json(arquivo_confirmacoes(vendedor_id), {})

def salvar_confirmacoes(vendedor_id: str, confirmacoes: dict) -> None:
    escrever_json(arquivo_confirmacoes(vendedor_id), confirmacoes)

def limpar_confirmacao(vendedor_id: str, mes: str) -> None:
    """Uma confirmação vira inválida se o vendedor mexer nos dados daquele mês depois."""
    confirmacoes = carregar_confirmacoes(vendedor_id)
    if mes in confirmacoes:
        del confirmacoes[mes]
        salvar_confirmacoes(vendedor_id, confirmacoes)

def arquivo_metas() -> Path:
    return resolver_pasta_dados() / METAS_FILE_NAME

def carregar_metas() -> dict:
    return ler_json(arquivo_metas(), {"grupo": {"diaria": 0, "semanal": 0, "mensal": 0}, "vendedores": {}})

def salvar_metas(metas: dict) -> None:
    escrever_json(arquivo_metas(), metas)

def metas_vendedor(vendedor_id: str, metas: dict) -> dict:
    return metas.get("vendedores", {}).get(vendedor_id, {"diaria": 0, "semanal": 0, "mensal": 0})

def registrar_acesso(tipo: str, sucesso: bool, vendedor_id: str = None, nome: str = None) -> None:
    log = ler_json(LOG_ACESSOS_FILE, [])
    log.append({
        "quando": agora_br().isoformat(timespec="seconds"),
        "tipo": tipo,
        "vendedor_id": vendedor_id,
        "nome": nome,
        "sucesso": sucesso,
        "ip": request.remote_addr,
    })
    escrever_json(LOG_ACESSOS_FILE, log[-MAX_LOG_ACESSOS:])

def registrar_acao(vendedor_id: str, nome: str, acao: str, produto: str, valor: float, detalhe: str = None) -> None:
    """Histórico de edição/exclusão/devolução de vendas, pra o gestor conseguir
    revisar o que cada vendedor mexeu — inclusive vendas já excluídas."""
    log = ler_json(LOG_ACOES_FILE, [])
    log.append({
        "quando": agora_br().isoformat(timespec="seconds"),
        "vendedor_id": vendedor_id,
        "nome": nome,
        "acao": acao,
        "produto": produto,
        "valor": valor,
        "detalhe": detalhe,
    })
    escrever_json(LOG_ACOES_FILE, log[-MAX_LOG_ACOES:])

def excedeu_tentativas_login(tipo: str, vendedor_id: str = None) -> bool:
    """Bloqueia login depois de várias senhas erradas seguidas, pra dificultar
    tentativa de adivinhação por força bruta (importante agora que fica na internet)."""
    log = ler_json(LOG_ACESSOS_FILE, [])
    limite = agora_br() - timedelta(minutes=LOGIN_JANELA_MINUTOS)
    falhas = 0
    for item in reversed(log):
        try:
            quando = parse_dt_tolerante(item["quando"])
        except (KeyError, ValueError):
            continue
        if quando < limite:
            break
        if item.get("tipo") == tipo and item.get("vendedor_id") == vendedor_id:
            if item.get("sucesso"):
                break
            falhas += 1
            if falhas >= LOGIN_MAX_TENTATIVAS:
                return True
    return False

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

def mes_para_intervalo(mes: str) -> tuple[str, str]:
    return f"{mes}-01", f"{mes}-31"

def valor_liquido(v: dict) -> float:
    """Valor da venda menos o que foi devolvido, sem apagar o histórico.

    Exceção: devolução marcada depois que o mês já tinha fechado NÃO mexe na
    venda. Aquele mês já foi pago com esse valor, e mudar um número que virou
    pagamento significa que o relatório de ontem não bate mais com o dinheiro
    que saiu. O acerto vira estorno no mês corrente — ver `estorno_devolucoes`.
    """
    devolucao = v.get("devolucao")
    if not devolucao:
        return v["valor"]
    if devolucao.get("apos_fechamento"):
        return v["valor"]
    if devolucao.get("tipo") == "total":
        return 0.0
    return max(0.0, v["valor"] - float(devolucao.get("valor_devolvido", 0)))

def estorno_devolucoes(vendedor_id: str, de: str, ate: str, vendas: dict) -> tuple:
    """Quanto voltou, no período, de vendas cujo mês já estava fechado.

    Devolve (valor devolvido, lista das devoluções) — o valor é de VENDA; quem
    aplica o percentual é quem calcula a comissão, porque cada um tem o seu.
    """
    total, itens = 0.0, []
    for vid, v in vendas.items():
        d = v.get("devolucao") or {}
        if not d.get("apos_fechamento") or v.get("vendedor_id") != vendedor_id:
            continue
        if not (de <= (d.get("mes_estorno") or "") + "-01" <= ate):
            continue
        valor = float(d.get("valor_devolvido") or 0)
        total += valor
        itens.append({"id": vid, "data": v.get("data"), "produto": v.get("produto"),
                      "valor_venda": v.get("valor"), "valor_devolvido": round(valor, 2),
                      "tipo": d.get("tipo"), "mes_estorno": d.get("mes_estorno")})
    itens.sort(key=lambda x: x["data"])
    return round(total, 2), itens

def total_vendido(vendedor_id: str, de: str, ate: str, vendas: dict, tipo: str = "venda") -> float:
    total = sum(
        valor_liquido(v)
        for v in vendas.values()
        if v["vendedor_id"] == vendedor_id and de <= v["data"] <= ate and v.get("tipo", "venda") == tipo
    )
    return round(total, 2)

def residuo_estorno(vendedor_id: str, mes: str, vendedores: dict, vendas: dict) -> float:
    """Estorno que sobrou dos meses ANTERIORES a `mes` e ainda nao foi abatido.

    Comissao de 1% e devolucao de peca cara nao cabem no mesmo mes: um motor de
    R$ 10.000 que volta gera R$ 100 de estorno, mais do que a comissao mensal
    inteira de quem vendeu pouco. Sem carregar o resto, a diferenca simplesmente
    sumiria — a empresa perdoaria a divida sem ninguem decidir isso.

    Percorre mes a mes consumindo o que cabe em cada um. So faz sentido no
    fechamento de UM mes, que e como a comissao e paga de verdade.
    """
    meses = sorted({(v.get("devolucao") or {}).get("mes_estorno")
                    for v in vendas.values()
                    if (v.get("devolucao") or {}).get("apos_fechamento")
                    and v.get("vendedor_id") == vendedor_id
                    and (v.get("devolucao") or {}).get("mes_estorno")})
    meses = [m for m in meses if m and m < mes]
    if not meses:
        return 0.0
    # Percorre TODOS os meses do primeiro estorno ate aqui, e nao so os que tem
    # estorno: o mes que ABATEU a divida tambem precisa entrar na conta. Sem
    # isso a divida ressuscitava todo mes seguinte, ja quitada.
    todos, cursor, limite = [], min(meses), mes
    while cursor < limite:
        todos.append(cursor)
        ano, m_ = int(cursor[:4]), int(cursor[5:7])
        ano, m_ = (ano + 1, 1) if m_ == 12 else (ano, m_ + 1)
        cursor = f"{ano:04d}-{m_:02d}"
        if len(todos) > 240:
            break
    pct = float(vendedores.get(vendedor_id, {}).get("percentual", 0))
    sobra = 0.0
    for m in todos:
        d0, d1 = mes_para_intervalo(m)
        bruto = total_vendido(vendedor_id, d0, d1, vendas) * pct / 100
        estorno, _ = estorno_devolucoes(vendedor_id, d0, d1, vendas)
        divida = estorno * pct / 100 + sobra
        sobra = max(0.0, divida - bruto)
    return round(sobra, 2)

def calcular_comissao(vendedor_id: str, de: str, ate: str, vendedores: dict, vendas: dict):
    info = vendedores[vendedor_id]
    proprio = total_vendido(vendedor_id, de, ate, vendas)
    percentual = float(info.get("percentual", 0))
    comissao = proprio * percentual / 100

    # Devolucao de venda de mes ja pago: abate aqui, no mes em que a peca
    # voltou. O mes de origem fica intacto porque ele ja virou pagamento.
    estorno_valor, estorno_itens = estorno_devolucoes(vendedor_id, de, ate, vendas)
    # Sobra dos meses anteriores entra junto: divida de estorno nao caduca por
    # nao caber no mes em que nasceu.
    vem_de_antes = (residuo_estorno(vendedor_id, de[:7], vendedores, vendas)
                    if de[:7] == ate[:7] else 0.0)
    estorno_comissao = round(estorno_valor * percentual / 100 + vem_de_antes, 2)
    comissao -= estorno_comissao

    overrides_detalhe = []
    for over in info.get("overrides", []):
        outro_id = over.get("vendedor_id")
        outro_percentual = float(over.get("percentual", 0))
        if outro_id not in vendedores:
            continue
        outro_total = total_vendido(outro_id, de, ate, vendas)
        # O override tambem estorna: se a venda voltou, quem ganhava por cima
        # dela recebeu por venda que nao houve, igual ao vendedor.
        outro_estorno, _ = estorno_devolucoes(outro_id, de, ate, vendas)
        valor_over = round((outro_total - outro_estorno) * outro_percentual / 100, 2)
        comissao += valor_over
        overrides_detalhe.append({
            "vendedor_id": outro_id,
            "nome": vendedores[outro_id]["nome"],
            "percentual": outro_percentual,
            "total_vendido": outro_total,
            "estorno": outro_estorno,
            "valor": valor_over,
        })

    return {
        "total_vendido": proprio,
        "percentual": percentual,
        "comissao_propria": round(proprio * percentual / 100, 2),
        "overrides": overrides_detalhe,
        # Nunca negativa: se o estorno for maior que a comissao do mes, o que
        # sobra fica devendo pro mes seguinte em vez de virar cobranca. O saldo
        # a carregar vai em `estorno_a_carregar`, visivel na tela.
        "comissao": round(max(0.0, comissao), 2),
        "estorno": {
            "valor_devolvido": estorno_valor,
            "comissao": estorno_comissao,
            "de_meses_anteriores": round(vem_de_antes, 2),
            "a_carregar": round(max(0.0, -comissao), 2),
            "itens": estorno_itens,
        },
        "total_bonus": total_vendido(vendedor_id, de, ate, vendas, tipo="bonus"),
    }

# Areas do portal que podem ser liberadas uma a uma. A chave e a mesma que o
# menu lateral usa, entao marcar aqui e ver o item la nao dependem de traducao.
AREAS = {
    "painel": "Painel",
    "marketing": "Marketing",
    "analytics": "Analytics",
    "auditoria": "Comissoes",
    "metabonus": "Meta Bonus",
    "desempenho": "Desempenho",
    "carros": "Carros pra chegar",
    "rh": "Gestao de pessoas",
    "atendimento": "Atendimento agora",
    "retomada": "Follow-up do time",
    "fechamento": "Fechamento de mes",
    "fluxo_caixa": "Fluxo de caixa",
    "dre": "DRE",
    "lancamentos": "Lancamentos",
    # Estes dois nao sao secoes da tela do gestor, sao links pra outras
    # paginas. Entram aqui pra o gestor poder decidir quem ve o atalho — o
    # menu nao deve oferecer porta que a pessoa nao usa.
    "ranking": "Ranking de vendas (TV)",
    "expedicao": "Painel de expedicao",
    # --- o que era o "portal do vendedor" ---
    # Viraram areas como as outras. Antes eram implicitas por morar num site
    # separado: quem tinha login via tudo isso, sem ninguem decidir. Num portal
    # so, "implicito" deixa de existir — ou esta marcado, ou nao aparece.
    "meu_painel": "Meu painel",
    "minhas_vendas": "Minhas vendas",
    "meu_atendimento": "Esperando voce",
    "minha_performance": "Minha performance",
    "simulador": "Simulacao de desconto",
    "meu_followup": "Meu follow-up",
}

# O que qualquer pessoa da equipe ve sobre o proprio trabalho. Nao e privilegio
# — e a razao de ela ter login. Vira o padrao de quem entra, e o gestor pode
# tirar caso a caso (a expedicao, por exemplo, nao vende).
AREAS_PROPRIAS = ["meu_painel", "minhas_vendas", "meu_atendimento",
                  "minha_performance", "simulador", "meu_followup"]

# Onde cada area vive. Enquanto as duas telas nao viram um arquivo so, o menu
# precisa saber pra onde mandar — e e isso que faz o portal parecer um lugar so
# mesmo morando em dois arquivos.
# ---------------------------------------------------------------------------
# Plano de contas
#
# Montado em 01/09/2026 sobre os R$ 3.647.009 que sairam entre janeiro e agosto
# de 2026, e nao sobre um modelo generico: cada conta aqui existe porque tem
# gasto real da Nevada caindo nela.
#
# `dre` diz em qual bloco do demonstrativo a conta entra, e e o que faz este
# plano valer alguma coisa: "investimento", "socios" e "nao_resultado" saem do
# caixa mas NAO sao despesa — obra vira patrimonio, P1..P4 e o proprio lucro
# sendo repartido, e caucao so passa. Hoje os tres somam junto com as despesas
# na planilha, e e por isso que o mes parece pior do que foi.
#
# `entrada=True` marca as contas de receita; o resto e saida. O sinal vem da
# conta escolhida e nao de um campo separado, senao existiria a combinacao
# "entrada de Aluguel", que nao quer dizer nada.
PLANO_DE_CONTAS = [
    {"grupo": "Receita bruta", "dre": "receita", "entrada": True, "contas": [
        ("1.01", "Mercado Livre", "Valor cheio da venda, antes da tarifa do ML"),
        ("1.02", "Site proprio", "nevadaecopecas.com.br"),
        ("1.03", "Shopee", "Valor cheio da venda"),
        ("1.04", "Balcao e WhatsApp", "O que a planilha chama de VB"),
        ("1.05", "Outras receitas", "Ferro/metal, venda de ativo, indenizacao"),
    ]},
    {"grupo": "Deducoes da receita", "dre": "deducoes", "contas": [
        ("2.01", "Impostos sobre venda", "DAS, DARF, Simples"),
        ("2.02", "Devolucoes e cancelamentos", "So o valor que voltou"),
        ("2.03", "Tarifa de marketplace", "Comissao que o ML e a Shopee descontam"),
        ("2.04", "Taxa de cartao e gateway", "Taxa da maquininha e do gateway"),
    ]},
    {"grupo": "Custo da peca vendida", "dre": "cmv", "contas": [
        ("3.01", "Compra de veiculo / sucata", "O carro que entrou pra desmontar"),
        ("3.02", "Guincho e remocao", "Guincheiros que trazem a sucata"),
        ("3.03", "Preparo e manutencao da sucata", "Oficina, retifica — o que faz a peca vender"),
        ("3.04", "Peca comprada de terceiro", "Motor, cambio e peca de fora"),
        ("3.05", "Documentacao e baixa", "DOC, Detran, Prodesp, CADRI, CETESB"),
    ]},
    {"grupo": "Despesas comerciais", "dre": "despesas", "contas": [
        ("4.01", "Comissao de vendedores", "Comissao paga sobre venda"),
        ("4.02", "Bonus e metas", "Meta Bonus, premiacao, bonificacao"),
        ("4.03", "Midia paga", "Google Ads, Meta Ads"),
        ("4.04", "Agencia de marketing", "Beelieve — R$ 1.900 fixos por mes"),
        ("4.05", "Frete de venda", "Correios, transportadoras, TM"),
        ("4.06", "Embalagem", "Caixa, plastico bolha, fita, strech, etiqueta"),
        ("4.07", "Ferramentas de venda", "ERP, To Talk, Vaapt, integracoes"),
    ]},
    {"grupo": "Pessoal", "dre": "despesas", "contas": [
        ("5.01", "Salarios", ""),
        ("5.02", "Encargos", "FGTS, INSS, sindicato, Sincomercio"),
        ("5.03", "Beneficios", "Almoco, convenio, uniforme, EPI"),
        ("5.04", "Ferias, 13o e rescisoes", "Separado do salario: e sazonal"),
        # Um por socio: na planilha as retiradas ja vem separadas em quatro
        # faixas de linhas, e juntar tudo numa conta so jogaria fora uma
        # informacao que o gestor ja mantem na mao.
        ("5.05", "Pro-labore P1 — Ricardo", "Retirada do socio, em dinheiro ou em conta paga"),
        ("5.06", "Pro-labore P2 — Odilon", "Retirada do socio, em dinheiro ou em conta paga"),
        ("5.07", "Pro-labore P3 — Caique", "Retirada do socio, em dinheiro ou em conta paga"),
        ("5.08", "Pro-labore P4 — Gabriela", "Retirada do socio, em dinheiro ou em conta paga"),
        ("5.09", "Pro-labore — socio nao identificado",
         "Retirada que nao deu pra atribuir a um socio"),
        # Plano de saude dos familiares dos socios. Nao e beneficio de
        # funcionario — confirmado pelo gestor em 01/09/2026 — entao nao pode
        # entrar em 5.03, senao o custo de pessoal da empresa fica inflado.
        ("5.10", "Plano de saude dos socios", "Santa Helena — familiares dos socios"),
    ]},
    {"grupo": "Ocupacao e estrutura", "dre": "despesas", "contas": [
        ("6.01", "Aluguel", ""),
        ("6.02", "Condominio e IPTU", ""),
        ("6.03", "Energia", "Enel"),
        ("6.04", "Agua", "Sabesp"),
        ("6.05", "Telefone e internet", "Vivo, celulares"),
        ("6.06", "Seguranca e monitoramento", "Cameras, vigilancia, portaria"),
        ("6.07", "Limpeza e conservacao", "Material de limpeza, racao, jardinagem"),
        ("6.08", "Manutencao predial", "Reparo — obra nova vai em Investimento"),
    ]},
    {"grupo": "Administrativas", "dre": "despesas", "contas": [
        ("7.01", "Contabilidade", ""),
        ("7.02", "Juridico", "Advogados, processos, acordos"),
        ("7.03", "Material de escritorio", "Papelaria, cartorio"),
        ("7.04", "Taxas bancarias", "Tarifa da conta — nao a compra feita no cartao"),
        ("7.05", "Associacoes e sindicato", "Abcar, Sincomercio, associacao"),
        ("7.06", "Confraternizacao e eventos", "Festa, Pascoa, almoco de equipe"),
        ("7.07", "Despesas gerais", "O unico 'diversos' — teto de 2% do mes"),
    ]},
    {"grupo": "Frota propria", "dre": "despesas", "contas": [
        ("8.01", "IPVA e licenciamento", "Um lancamento por veiculo"),
        ("8.02", "Seguro de frota", ""),
        ("8.03", "Combustivel", ""),
        ("8.04", "Manutencao da frota", "O carro da empresa; sucata pra vender e CMV"),
    ]},
    {"grupo": "Financeiro", "dre": "despesas", "contas": [
        ("9.01", "Juros de financiamento", "So os juros; o principal e caixa"),
        ("9.02", "Consorcio", "Taxa de administracao e seguro"),
        ("9.03", "Multas e encargos", "Atraso de imposto, protesto"),
    ]},
    {"grupo": "Sai do caixa, nao e despesa", "dre": "fora", "contas": [
        ("0.01", "Investimento e obra", "Terreno, laje, telhado — vira patrimonio",
         "investimento"),
        ("0.02", "Distribuicao de lucro", "P1 Ricardo, P2 Odilon, P3 Caique, P4 Gabriela",
         "socios"),
        ("0.03", "Emprestimo — principal", "Entrada e amortizacao; o juro e 9.01",
         "nao_resultado"),
        ("0.04", "Caucao e transito", "Entra e sai; nao mexe no resultado",
         "nao_resultado"),
    ]},
]

# Como voce pagou — separado do que voce comprou. Sao R$ 327.497 em oito meses
# lancados como "Cartao", "BB", "Visa": isso responde por qual conta o dinheiro
# saiu, e nunca o que foi comprado. Dois campos resolvem; um campo so nao vai
# resolver nunca.
FORMAS_DE_PAGAMENTO = ["Cartao", "Pix", "Boleto", "Dinheiro", "Transferencia",
                       "Debito automatico", "Cheque"]

# Teto da conta de sobra. Toda planilha precisa de um "diversos", senao o
# lancamento trava e a pessoa inventa um rotulo — foi assim que nasceram os
# R$ 287.067 de "Div.". Ele existe, mas com limite visivel: passou disso, tem
# coisa mal classificada dentro. Hoje esse numero esta em 8%.
TETO_DESPESAS_GERAIS = 0.02

CONTA_DESPESAS_GERAIS = "7.07"

def _indice_do_plano() -> dict:
    """codigo -> dados da conta. Montado uma vez, usado a cada lancamento."""
    idx = {}
    for bloco in PLANO_DE_CONTAS:
        for conta in bloco["contas"]:
            codigo, nome, ajuda = conta[0], conta[1], conta[2]
            idx[codigo] = {
                "codigo": codigo,
                "nome": nome,
                "ajuda": ajuda,
                "grupo": bloco["grupo"],
                # O 4o item sobrescreve o bloco: dentro de "nao e despesa" cada
                # conta cai num lugar diferente do DRE.
                "dre": conta[3] if len(conta) > 3 else bloco["dre"],
                "entrada": bool(bloco.get("entrada")),
            }
    return idx

CONTAS_POR_CODIGO = _indice_do_plano()

PAGINA_DA_AREA = {a: "/" for a in AREAS_PROPRIAS}

# Semente conservadora. Deliberadamente pobre: dar acesso a mais por chute e
# pior do que dar de menos, porque o de menos aparece (a pessoa pede) e o de
# mais nao aparece nunca. O gestor ajusta na tela de Permissoes.
PADROES_SETOR_INICIAL = {
    "Comercial": list(AREAS_PROPRIAS),
    "Gerencia": list(AREAS_PROPRIAS) + ["painel", "desempenho"],
    "Expedicao": ["meu_painel", "meu_atendimento", "expedicao"],
    "Anuncios": ["meu_painel", "carros", "metabonus"],
    "Cadastro": ["meu_painel", "carros"],
    "Estoque": ["meu_painel", "carros"],
    "Administrativo": ["meu_painel"],
    "Desmontagem": ["meu_painel"],
    "Higienizacao": ["meu_painel"],
}

def _sem_acento_simples(t: str) -> str:
    """Setor digitado no RH vem com acento; a chave do padrao, sem. Comparar
    achatado evita que 'Expedição' e 'Expedicao' virem dois setores."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (t or "").strip().lower())
                   if not unicodedata.combining(c))

def padroes_setor() -> dict:
    """{setor achatado: [areas]}. Editavel pelo gestor; a semente so vale
    enquanto ninguem tiver salvo nada."""
    salvo = ler_json(resolver_pasta_dados() / "padroes_setor.json", None)
    base = salvo if isinstance(salvo, dict) and salvo else PADROES_SETOR_INICIAL
    return {_sem_acento_simples(k): [a for a in v if a in AREAS]
            for k, v in base.items()}

def setor_do_usuario(vid: str) -> str:
    """O setor que o RH registra pra pessoa ligada a este usuario."""
    if not vid:
        return ""
    for c in (_rh_ler("colaboradores") or {}).values():
        if (c.get("vendedor_id") or "").strip().lower() == vid.lower():
            return c.get("setor") or ""
    return ""

def areas_efetivas(v: dict, vid: str = "") -> list:
    """As areas que valem pra um usuario, ja com os padroes aplicados.

    Existe pra que a tela e o servidor concordem. Se a lista de usuarios
    devolvesse o campo cru, a grade de Permissoes mostraria "nenhuma area" pra
    quem na verdade ve o proprio trabalho — e o primeiro clique apagaria o que
    a pessoa tinha, porque a tela reenvia o que leu.
    """
    areas = [a for a in (v.get("areas") or []) if a in AREAS]
    # Ausencia do campo significa "o padrao", nao "nada". Sem isso, os usuarios
    # que ja existiam perderiam o portal no instante em que isto subisse.
    #
    # A ordem importa: `areas` gravado manda sempre. O setor so entra quando
    # ninguem decidiu nada pra pessoa — assim o Pedro pode ser de Anuncios e
    # ainda ter Meta Bonus, sem precisar de um setor inventado pra ele.
    if "areas" not in v:
        do_setor = padroes_setor().get(_sem_acento_simples(setor_do_usuario(vid)))
        areas = list(do_setor) if do_setor else list(AREAS_PROPRIAS)
    if v.get("perfil") == "expedicao":
        # Nao vende: as areas de venda sao ruido de menu, nao permissao a mais.
        areas = [a for a in areas if a not in ("minhas_vendas", "simulador",
                                               "meu_followup")]
        if "expedicao" not in areas:
            areas.append("expedicao")
    return areas

def areas_do_usuario() -> list:
    """As areas que quem esta logado pode ver.

    O gestor (senha master) ve tudo — devolve a lista inteira. Um usuario comum
    ve so o que foi marcado no cadastro dele. Quem nao tem nada marcado nao ve
    area nenhuma da area do gestor, que e o comportamento de sempre.
    """
    if exigir_admin():
        # Master NAO herda as areas do proprio trabalho. Quem supervisiona nao
        # atende nem vende — dar "Esperando voce" e "Minhas vendas" pra ele
        # enche o menu com o que ele nunca vai usar, e pior: esses itens moram
        # na outra tela, entao clicar neles parecia sair do portal e entrar de
        # novo. O equivalente dele ja existe e e outro: "Atendimento agora",
        # "Follow-up do time", "Desempenho do time".
        #
        # Se o gestor tambem vender, basta marcar as areas dele na grade —
        # decisao explicita, nao heranca automatica.
        base = [a for a in AREAS if a not in AREAS_PROPRIAS]
        vid_m = session.get("vendedor_id")
        if vid_m:
            v_m = carregar_vendedores().get(vid_m) or {}
            base += [a for a in (v_m.get("areas") or []) if a in AREAS_PROPRIAS]
        return base
    vid = session.get("vendedor_id")
    if not vid:
        return []
    return areas_efetivas(carregar_vendedores().get(vid) or {}, vid)

def exigir_area(area: str) -> bool:
    """Substitui exigir_admin() nos endpoints que passam a ser compartilhados.

    Continua valendo pro gestor exatamente como antes; a diferenca e que agora
    um usuario com a area marcada tambem passa. Endpoint sem essa troca segue
    exclusivo do gestor — o padrao continua sendo negar.
    """
    return exigir_admin() or area in areas_do_usuario()

def desligado(info: dict, quando: str = None) -> bool:
    """Se a pessoa ja estava desligada na data dada (padrao: hoje).

    A comparacao e por data, nao booleana: quem saiu em 31/08 continua
    aparecendo em qualquer relatorio de agosto — o trabalho dela existiu — e so
    some das telas de lancamento de 31/08 em diante.
    """
    d = (info or {}).get("desligado_em")
    if not d:
        return False
    return (quando or hoje_br().isoformat()) >= d

def perfil_de(vendedor_id: str) -> str:
    """"vendedor" (padrao) ou "expedicao".

    O portal nasceu so com vendedores; a expedicao entrou depois e reaproveita
    o mesmo login. Quem e da expedicao nao vende — some das listas de venda,
    comissao e meta — e em troca ve a fila de pedidos pra liberar.
    """
    v = carregar_vendedores().get(vendedor_id) or {}
    return v.get("perfil") or "vendedor"

def exigir_vendedor():
    vendedor_id = session.get("vendedor_id")
    if not vendedor_id:
        return None
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return None
    return vendedor_id

def _atd_resolvidos() -> dict:
    """{id da sessao: carimbo da ultima mensagem quando foi marcada}."""
    return ler_json(resolver_pasta_dados() / "atendimento_resolvido.json", None) or {}

def _atd_pendentes(conversas, resolvidos):
    """Tira da lista o que foi resolvido, com DUAS validades:

    1. O cliente nao pode ter falado depois. Se mandou mensagem nova, a
       conversa volta — esconder quem voltou a falar seria pior que nao ter
       alerta nenhum.
    2. A marcacao vale so pelo dia em que foi feita. "Ja resolvi" quer dizer
       "estou tratando isso agora", nao "esquece pra sempre": se amanhecer e a
       conversa continuar sem resposta da loja, ela volta pra fila. Foi o que o
       gestor pediu em 28/08/2026 — nada pode cair no esquecimento.
    """
    hoje = hoje_br().isoformat()
    saida = []
    for c in conversas:
        marca = resolvidos.get(c["id"])
        if isinstance(marca, str):        # formato antigo: so o carimbo
            marca = {"ultima_em": marca, "em": marca[:10]}
        if (marca and marca.get("em") == hoje
                and c.get("ultima_em") and c["ultima_em"] <= marca.get("ultima_em", "")):
            continue
        saida.append(c)
    return saida

def _achatar_canal(x: str) -> str:
    """minusculo, sem acento, espacos colapsados. O NFKD pode CRIAR espaco —
    o acento morto (U+00B4, tecla morta do ABNT) vira espaco + combinante —
    entao colapsa de novo no fim, senao "ML" + acento morto vira "ml " e nao
    casa com nada (e ja rendeu um 500 no lancamento)."""
    x = "".join(c for c in unicodedata.normalize("NFKD", x.lower())
                if not unicodedata.combining(c))
    return " ".join(x.split())

def retroativo_ativo(vendedor: dict) -> bool:
    """Verifica se a liberação temporária de lançamento retroativo do gestor
    ainda está dentro da janela de tempo (não é mais de uso único)."""
    ate = vendedor.get("liberacao_retroativa_ate")
    if not ate:
        return False
    try:
        return agora_br() < parse_dt_tolerante(ate)
    except ValueError:
        return False

def _hash_senha(senha: str) -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(senha)

def _senha_confere(guardada: str, digitada: str) -> bool:
    """Confere a senha aceitando os dois formatos.

    O banco guardava senha em texto puro — qualquer um com acesso ao banco (ou
    a um backup) lia a senha de todo mundo. Agora grava hash; o formato antigo
    continua aceito porque trocar a senha de 8 pessoas de uma vez nao e opcao.
    Quem loga com senha em texto puro tem ela promovida a hash NAQUELE momento
    (ver os dois logins) — a migracao acontece sozinha, um login de cada vez.
    """
    if not guardada or not digitada:
        return False
    if guardada.startswith(("pbkdf2:", "scrypt:")):
        from werkzeug.security import check_password_hash
        return check_password_hash(guardada, digitada)
    return guardada == digitada

def _hash_codigo(codigo: str) -> str:
    return hashlib.sha256(codigo.encode()).hexdigest()

def usuario_master() -> bool:
    """Se quem esta logado e um usuario com acesso master.

    Separado de `session["admin"]` de proposito: aquele e a senha reserva, este
    e uma pessoa com nome. Os dois abrem as mesmas portas, mas so um deles
    aparece no log dizendo quem era.
    """
    vid = session.get("vendedor_id")
    if not vid:
        return False
    v = carregar_vendedores().get(vid) or {}
    # Desligado nao administra mais nada, por mais master que fosse.
    return bool(v.get("master")) and not desligado(v)

def exigir_admin():
    return bool(session.get("admin")) or usuario_master()

def _nome_aba_excel(nome: str) -> str:
    """Aba do Excel não aceita \\ / ? * [ ] : nem mais de 31 caracteres."""
    limpo = re.sub(r'[\\/?*\[\]:]', "-", nome)
    return limpo[:31] or "Vendedor"

def _rh_ler(nome: str) -> dict:
    return ler_json(resolver_pasta_dados() / f"rh_{nome}.json", None) or {}

SETORES_META = {"anunciante": "Anunciantes", "cadastrador": "Cadastradores"}

# setor da pessoa -> tipo do lancamento, nomes herdados do painel-metas.
TIPO_META = {"anunciante": "anuncio", "cadastrador": "cadastro"}

def _mb_bruto() -> dict:
    d = ler_json(resolver_pasta_dados() / "metas_bonus_dados.json", None) or {}
    d.setdefault("pessoas", {})
    d.setdefault("lancamentos", {})
    d.setdefault("veiculos", {})
    d.setdefault("meta_veiculos", {"meta": 0, "meta_bonus": 0})
    return d

def _mb_gravar(dados: dict) -> None:
    escrever_json(resolver_pasta_dados() / "metas_bonus_dados.json", dados)

def _mb_agregar(dados: dict) -> dict:
    """Producao por pessoa/mes contra meta e bonus — o mesmo agregado que o
    sincronizador antigo montava, agora calculado do dado vivo."""
    producao = {}
    for setor, tipo in TIPO_META.items():
        for l in (dados["lancamentos"].get(tipo) or {}).values():
            chave = (setor, l.get("pessoa_id"), (l.get("data") or "")[:7])
            producao[chave] = producao.get(chave, 0.0) + float(l.get("quantidade") or 0)

    meses_set = ({c[2] for c in producao}
                 | {(v.get("data") or "")[:7] for v in dados["veiculos"].values() if v.get("data")})
    meta_veic = dados["meta_veiculos"]

    por_mes = {}
    for mes in sorted(m for m in meses_set if m):
        setores = {}
        for setor, gente in dados["pessoas"].items():
            linhas = []
            for pid, p in gente.items():
                total = round(producao.get((setor, pid, mes), 0), 2)
                meta = float(p.get("meta") or 0)
                bonus = float(p.get("meta_bonus") or 0)
                # Quem nao lancou nada no mes nao entra: apareceria como 0% e
                # pareceria alguem que trabalhou e nao produziu, quando na
                # verdade nao estava no time naquele mes.
                if total <= 0:
                    continue
                linhas.append({
                    "nome": p.get("nome") or "(sem nome)",
                    "total": total, "meta": meta, "meta_bonus": bonus,
                    "pct": round(100 * total / meta, 1) if meta else None,
                    "bateu_meta": bool(meta) and total >= meta,
                    "bateu_bonus": bool(bonus) and total >= bonus,
                })
            linhas.sort(key=lambda x: -x["total"])
            if linhas:
                setores[setor] = linhas

        do_mes = [v for v in dados["veiculos"].values() if (v.get("data") or "")[:7] == mes]
        por_mes[mes] = {
            "setores": setores,
            "veiculos": {
                "carros": len(do_mes),
                "pecas": round(sum(float(v.get("pecas") or 0) for v in do_mes), 2),
                "meta": float(meta_veic.get("meta") or 0),
                "meta_bonus": float(meta_veic.get("meta_bonus") or 0),
                "lista": sorted(({"data": v.get("data"), "carro": v.get("carro"),
                                  "codigo": v.get("codigo"), "pecas": v.get("pecas") or 0}
                                 for v in do_mes), key=lambda v: v["data"] or ""),
            },
        }
    return por_mes

STATUS_TRABALHADO = ("chamei", "respondeu", "vendeu", "perdido")

def _caminho_crm(prefixo: str, vendedor_id: str) -> Path:
    return resolver_pasta_dados() / f"crm_{prefixo}_{vendedor_id}.json"

def carregar_fila_retomada(vendedor_id: str):
    # Padrão None (nunca Path.exists()): em modo banco o arquivo local não
    # existe, e checar o disco faria a fila parecer vazia em produção.
    return ler_json(_caminho_crm("fila", vendedor_id), None)

def carregar_status_retomada(vendedor_id: str) -> dict:
    return ler_json(_caminho_crm("status", vendedor_id), None) or {}

def _contagem_marcacoes(status: dict, desde: str = None) -> dict:
    """Quantas marcacoes de cada tipo, contando TODAS — inclusive as de clientes
    que ja sairam da fila. `desde` (ISO) recorta pela data da marcacao: e o que
    permite "quantos fecharam este mes", que e a pergunta da bonificacao."""
    c = {k: 0 for k in STATUS_TRABALHADO}
    for m in status.values():
        if desde and (m.get("em") or "") < desde:
            continue
        if m.get("status") in c:
            c[m["status"]] += 1
    c["trabalhados"] = sum(c[k] for k in STATUS_TRABALHADO)
    return c

def _resumo_retomada(itens: list, status: dict) -> dict:
    """Pendente vem da fila atual; chamei/respondeu/vendeu/perdido vem de
    TODAS as marcacoes, dentro ou fora da fila. Antes as duas contagens vinham
    so da fila, e remontar a fila zerava o placar do vendedor."""
    contagem = {"pendente": sum(1 for i in itens
                                if (status.get(i["sid"]) or {}).get("status", "pendente") == "pendente")}
    contagem.update(_contagem_marcacoes(status))
    contagem["total"] = len(itens)
    return contagem

# Assunto pela última fala do cliente. Ordem importa: a lista é percorrida de
# cima pra baixo e o primeiro que casar vence, então o mais decisivo vem antes.
ASSUNTOS_MSG = [
    ("fechar",  r"vou compr|quero compr|pode separar|vou fechar|realizar a compra|manda o pix|mandar o pix|fazer o pix"),
    ("foto",    r"\bfoto|\bvídeo|\bvideo|imagem|manda uma foto|ver a peç"),
    ("compat",  r"\bserve\b|compatív|compativ|código|codigo|numeraç|original|se encaixa|dá certo no"),
    ("frete",   r"\bfrete|entreg|\bprazo|quanto tempo|quantos dias|chega em|correio|transportadora|sedex"),
    ("terceiro", r"mecânic|mecanic|meu marido|minha esposa|patrão|patrao|meu chefe|confirmaç[aã]o do|falar com o"),
    ("preco",   r"\bpreç|\bpreco|\bvalor|quanto (fica|custa|sai|é)|desconto|mais barat|melhor preç|tá caro|ta caro"),
    ("pensar",  r"vou ver|vou pensar|depois eu|te falo|semana que vem|mês que vem|mes que vem|mais pra frente|qualquer coisa eu"),
]

# Por que a conversa morreu (campo `gancho` da fila).
GANCHOS_MSG = {
    "A conversa parou do nosso lado": "nosso_lado",
    "Respondemos tudo e ele sumiu": "sumiu",
    "Conversou e não fechou": "nao_fechou",
    "Achou caro — cabe negociar": "caro",
    "Travou no frete ou no prazo": "frete",
}

# Os textos evitam de propósito pronome e adjetivo com gênero ("tenho ela
# separada", "ainda está disponível pra você"): a peça vem do texto livre da
# conversa e não dá pra saber o gênero com segurança. Onde precisa de artigo,
# entra {a}, que é calculado por peça. O resto fala "a peça", que é sempre
# feminino e nunca erra.
MODELOS_PADRAO = {
    "saudacao": "Oi {nome}, tudo bem?",
    # Quando a bola ficou com a gente, pedir desculpa é a abertura certa: o
    # cliente não sumiu, nós que não voltamos.
    "saudacao_atraso": "Oi {nome}, tudo bem? Desculpa a demora pra te responder.",
    "corpo": {
        "fechar": "Vi que você ia fechar {a} {peca} e a gente acabou não concluindo. Ainda não vendi essa peça — quer que eu te mande os dados do pix?",
        "foto": "Sobre {a} {peca}: tenho fotos e vídeo aqui. Quer que eu te mande pra você conferir?",
        "compat": "Sobre {a} {peca}: consigo confirmar a compatibilidade pelo número da peça. Me passa o ano e o modelo do carro que eu te garanto se serve.",
        "frete": "Sobre {a} {peca}: consigo te confirmar o prazo e o frete certinho. Me passa seu CEP que eu calculo agora.",
        "terceiro": "Você ia confirmar {a} {peca}. Conseguiu falar com o mecânico? A peça continua aqui.",
        "preco": "Sobre {a} {peca} que você olhou com a gente: consigo ver uma condição melhor pra fechar. Ainda está precisando?",
        "pensar": "Passando pra saber se você chegou a decidir sobre {a} {peca}. Ainda tenho a peça disponível.",
        "nosso_lado": "Sobre {a} {peca} que você procurou: ficou faltando eu te retornar. Ainda está precisando?",
        "sumiu": "Sobre {a} {peca} que a gente conversou: ainda está precisando? A peça continua aqui comigo.",
        "nao_fechou": "Sobre {a} {peca} que você procurou com a gente: ainda tenho aqui. Quer que eu retome o orçamento?",
        "caro": "Sobre {a} {peca}: sei que o valor pesou. Me fala quanto você conseguiria pagar que eu vejo o que dá pra fazer por você.",
    },
    # Trocas pra quando a peça exata não era nossa (`tinha` = "Parecida"):
    # aqui a gente não tem o que prometer, tem o que oferecer.
    "corpo_parecida": {
        "fechar": "Vi que você ia fechar {a} {peca}. Consegui uma opção compatível aqui — quer que eu te mande os detalhes?",
        "terceiro": "Você ia confirmar {a} {peca}. Deu certo com o mecânico? Consigo uma compatível aqui.",
        "pensar": "Passando pra saber se você resolveu {a} {peca}. Se ainda precisar, consigo uma opção compatível.",
        "nosso_lado": "Sobre {a} {peca} que você procurou: ficou faltando eu te retornar. Se ainda precisar, consigo uma opção compatível.",
        "sumiu": "Sobre {a} {peca} que a gente conversou: ainda está precisando? Consigo uma opção compatível aqui.",
        "nao_fechou": "Sobre {a} {peca}: se ainda precisar, consigo uma opção compatível. Quer que eu veja pra você?",
        "caro": "Sobre {a} {peca}: sei que o valor pesou. Me fala quanto cabe no seu orçamento que eu procuro uma opção.",
    },
}

MODELOS_FILE = "crm_modelos"

# Nomes salvos no WhatsApp que não servem pra abrir uma mensagem.
NOMES_RUINS = {"cliente", "contato", "whatsapp", "teste", "novo", "sim", "nao", "não", "ok"}

# Masculinos terminados em -a, que fugiriam da regra pela terminação.
MASCULINOS_EM_A = {"sistema", "problema", "mapa", "dia", "emblema",
                   "paralama", "para-lama", "parabrisa", "para-brisa"}

# Femininos que não terminam em -a — a regra pela terminação chamaria de
# masculino e sairia "o central multimídia".
FEMININAS_EXTRA = {"central", "chave", "luz", "grade", "ponte", "lente", "haste",
                   "torre", "base", "fonte", "corrente", "árvore", "arvore",
                   "hélice", "helice", "cruz", "face", "rede", "sede", "parte",
                   "mangá"}

def carregar_modelos_msg() -> dict:
    salvos = ler_json(resolver_pasta_dados() / f"{MODELOS_FILE}.json", None)
    if not salvos:
        return MODELOS_PADRAO
    # Mescla com o padrão: se um dia entrar uma situação nova no código, ela
    # aparece pro time sem precisar que o gestor salve a tela de novo.
    return {
        "saudacao": salvos.get("saudacao") or MODELOS_PADRAO["saudacao"],
        "saudacao_atraso": salvos.get("saudacao_atraso") or MODELOS_PADRAO["saudacao_atraso"],
        "corpo": {**MODELOS_PADRAO["corpo"], **(salvos.get("corpo") or {})},
        "corpo_parecida": {**MODELOS_PADRAO["corpo_parecida"], **(salvos.get("corpo_parecida") or {})},
    }

def _primeiro_nome(nome: str) -> str:
    """Nome do WhatsApp é o que o cliente quis: tem '.', 'Fn', 'Dede_6cc'. Nesses
    casos é melhor não chamar pelo nome do que chamar errado — a saudação sem
    nome continua natural ('Oi, tudo bem?')."""
    bruto = (nome or "").strip()
    if not bruto:
        return ""
    # Pontuação nas pontas é comum e inofensiva ("Conrado…"); no meio do nome
    # já é apelido de perfil ("Dede_6cc"), e aí é melhor não chamar pelo nome.
    primeiro = re.sub(r"^[^A-Za-zÀ-ÿ]+|[^A-Za-zÀ-ÿ]+$", "", bruto.split()[0])
    valido = re.fullmatch(r"[A-Za-zÀ-ÿ]+(['-][A-Za-zÀ-ÿ]+)?", primeiro)
    if len(primeiro) < 3 or not valido or primeiro.lower() in NOMES_RUINS:
        return ""
    limpo = primeiro
    return limpo if (limpo[:1].isupper() and not limpo.isupper()) else limpo.capitalize()

def _artigo(peca: str) -> str:
    """'o motor', 'a bomba', 'as molas'. Sem isso sai 'a motor Audi Q3', que
    entrega na hora que a mensagem foi feita por máquina."""
    palavras = re.sub(r"[^a-zà-ÿ\s-]", " ", (peca or "").lower()).split()
    if not palavras:
        return "a"
    primeira = palavras[0]
    plural = primeira.endswith("s") and len(primeira) > 3
    base = primeira[:-1] if plural else primeira
    feminino = (base in FEMININAS_EXTRA
                or base.endswith(("ção", "são", "dade", "gem"))
                or (base.endswith("a") and base not in MASCULINOS_EM_A))
    if plural:
        return "as" if feminino else "os"
    return "a" if feminino else "o"

def _peca_curta(peca: str, limite: int = 55) -> str:
    """A fila guarda a peça descrita inteira ('soleira dianteira direita e
    friso/cromado do para-choque traseiro Volkswagen Tiguan R Line 2018/2019').
    Numa mensagem isso não cabe: corta no primeiro separador natural, e quando a
    IA não conseguiu identificar a peça, vira só 'peça'."""
    texto = (peca or "").strip()
    if not texto or re.search(r"não (especificad|identificad|inform)", texto, re.I):
        return "peça"
    if len(texto) > limite:
        for corte in (",", " e "):
            if corte in texto:
                texto = texto.split(corte)[0].strip()
                break
    if len(texto) > limite:
        texto = texto[:limite].rsplit(" ", 1)[0].rstrip(" ,;-/")
    return texto or "peça"

def _assunto_msg(ultimas) -> str:
    """O que o cliente falou por último. Só as falas dele entram na fila, então
    não tem risco de casar com o que a loja escreveu."""
    texto = " ".join(ultimas or []).lower()
    if not texto.strip():
        return ""
    for chave, padrao in ASSUNTOS_MSG:
        if re.search(padrao, texto):
            return chave
    return ""

def _limpar_msg(texto: str) -> str:
    """Sem nome a saudação vira 'Oi , tudo bem?'. Arruma a pontuação solta."""
    texto = re.sub(r"\s+([,.:;!?])", r"\1", texto)
    return re.sub(r"\s{2,}", " ", texto).strip()

def montar_mensagem(item: dict, modelos: dict) -> dict:
    gancho = GANCHOS_MSG.get(item.get("gancho"), "sumiu")
    assunto = _assunto_msg(item.get("ultimas"))
    situacao = assunto or gancho

    tabela = modelos["corpo"]
    if item.get("tinha") != "Sim":
        tabela = {**modelos["corpo"], **modelos["corpo_parecida"]}
    corpo = tabela.get(situacao) or tabela.get(gancho) or modelos["corpo"]["sumiu"]

    # A desculpa pela demora só entra quando a bola ficou mesmo com a gente.
    saudacao = modelos["saudacao_atraso"] if gancho == "nosso_lado" else modelos["saudacao"]

    peca = _peca_curta(item.get("peca"))
    campos = {"nome": _primeiro_nome(item.get("nome")),
              "peca": peca,
              "a": _artigo(peca),
              "dias": item.get("dias", 0)}

    def preencher(txt):
        try:
            return txt.format(**campos)
        except (KeyError, IndexError, ValueError):
            # Texto editado pelo gestor com chave inventada não pode derrubar a
            # fila inteira — melhor mostrar o modelo cru do que não mostrar nada.
            return txt

    return {"texto": _limpar_msg(preencher(saudacao) + " " + preencher(corpo)),
            "situacao": situacao}

def _topo_retomada(itens: list, status: dict, quantos: int = 3) -> list:
    """Os clientes mais quentes que ainda não foram chamados. Mesma ordem da
    fila (ALTA primeiro, depois conversa mais recente, nota desempata), pra o
    painel e o follow-up nunca discordarem sobre quem vem primeiro."""
    pendentes = [i for i in itens
                 if (status.get(i["sid"]) or {}).get("status", "pendente") == "pendente"]
    pendentes.sort(key=lambda x: (x.get("prio") != "ALTA", x.get("dias", 999), -x.get("nota", 0)))
    modelos = carregar_modelos_msg()
    return [{
        "sid": i["sid"],
        "nome": i.get("nome"),
        "peca": i.get("peca"),
        "dias": i.get("dias"),
        "prio": i.get("prio"),
        "link": i.get("link"),
        "gancho": i.get("gancho"),
        "msg": montar_mensagem(i, modelos),
    } for i in pendentes[:quantos]]

# ---------- sincronizador de gasto na nuvem (thread de fundo) ----------
# Google e Meta pelo Windsor, uma vez por dia, sem depender do PC da loja.
try:
    import sincronizador_nuvem as _sn

    def _sn_chave():
        d = ler_json(resolver_pasta_dados() / "segredo_windsor.json", None) or {}
        return d.get("chave")

    def _sn_atual():
        return ler_json(resolver_pasta_dados() / "marketing_gasto.json", None)

    def _sn_gravar(corpo):
        escrever_json(resolver_pasta_dados() / "marketing_gasto.json", corpo)

    _sn.iniciar(_sn_chave, _sn_atual, _sn_gravar)

    # Perfil da Empresa (Google) na mesma cadencia diaria. Ate hoje esse card
    # so atualizava quando alguem clicava em "Atualizar" — e o gestor disse que
    # e um dos que ele mais olha. Card favorito nao pode depender de clique.
    def _perfil_atual():
        return ler_json(resolver_pasta_dados() / "perfil_google.json", None)

    def _perfil_gravar(d):
        escrever_json(resolver_pasta_dados() / "perfil_google.json", d)

    def _laco_perfil():
        import time as _t
        from datetime import datetime as _dt
        _t.sleep(120)
        while True:
            try:
                agora = _dt.now(FUSO_BRASILIA)
                if agora.strftime("%H:%M") >= "06:50":
                    # Reserva, nao dono (Fase 2, 03/09/2026): o Perfil e do pipeline
                    # local pela API oficial. So entra se a chave parou ha 30h+.
                    atual = _perfil_atual() or {}
                    if not _sn.recente(atual.get("gerado_em")):
                        _sn.sincronizar_perfil(_sn_chave, _perfil_atual, _perfil_gravar)
            except Exception as e:   # nunca derruba o portal
                print(f"[perfil-google] {type(e).__name__}: {str(e)[:140]}")
            _t.sleep(30 * 60)

    import threading as _th
    _th.Thread(target=_laco_perfil, daemon=True, name="perfil-google").start()
except Exception as _e:
    print(f"[sinc-nuvem] não subiu: {_e}")

# ---------- monitor de atendimento (thread de fundo) ----------
# Roda dentro do proprio servidor pra nao depender do computador da loja
# ligado — historico completo em app/monitor_atendimento.py. No gunicorn cada
# worker teria o seu; o servico usa 1 worker e as escritas sao idempotentes,
# entao duplicata eventual so custaria chamadas repetidas, nunca dado errado.
try:
    import monitor_atendimento as _mon

    def _mon_token():
        d = ler_json(resolver_pasta_dados() / "segredo_totalk.json", None) or {}
        return d.get("token")

    def _mon_gravar(pacote):
        escrever_json(resolver_pasta_dados() / "atendimento_alerta.json", pacote)

    _mon.iniciar(_mon_token, _mon_gravar)
except Exception as _e:   # o monitor nunca pode derrubar o portal
    print(f"[monitor-atendimento] não subiu: {_e}")
