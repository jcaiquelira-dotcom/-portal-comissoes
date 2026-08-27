import calendar
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
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
# O catálogo (ERP) e as regras de desconto são preparados pelo gestor no
# projeto irmão portal-simulador, rodado localmente (edição de regras,
# reimportação do ERP), e sincronizados pra cá com
# portal-simulador/scripts/sincronizar_supabase.py. Aqui só existe a
# consulta/simulação pros vendedores — sem tela de administração.
#
# Em modo local (sem DATABASE_URL), lê direto o simulador.db do projeto
# irmão, só pra facilitar teste — em produção é sempre via Postgres.
_SIMULADOR_DB_LOCAL = ROOT.parent / "portal-simulador" / "data" / "simulador.db"
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


def carregar_regras_simulador():
    if DATABASE_URL:
        return _db_ler("simulador_regras", None)
    caminho = ROOT.parent / "portal-simulador" / "data" / "regras.json"
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


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


app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.secret_key = obter_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCAO,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
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


# ---------- Login do vendedor ----------

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
    if not v or v.get("senha") != senha:
        registrar_acesso("vendedor", False, vendedor_id, v["nome"] if v else None)
        return jsonify({"erro": "Vendedor ou senha inválidos."}), 401
    session.clear()
    session["vendedor_id"] = vendedor_id
    registrar_acesso("vendedor", True, vendedor_id, v["nome"])
    return jsonify({"ok": True, "nome": v["nome"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


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
    })


def mes_para_intervalo(mes: str) -> tuple[str, str]:
    return f"{mes}-01", f"{mes}-31"


def valor_liquido(v: dict) -> float:
    """Valor de uma venda descontando o que foi devolvido, sem apagar o histórico original."""
    devolucao = v.get("devolucao")
    if not devolucao:
        return v["valor"]
    if devolucao.get("tipo") == "total":
        return 0.0
    return max(0.0, v["valor"] - float(devolucao.get("valor_devolvido", 0)))


def total_vendido(vendedor_id: str, de: str, ate: str, vendas: dict, tipo: str = "venda") -> float:
    total = sum(
        valor_liquido(v)
        for v in vendas.values()
        if v["vendedor_id"] == vendedor_id and de <= v["data"] <= ate and v.get("tipo", "venda") == tipo
    )
    return round(total, 2)


def calcular_comissao(vendedor_id: str, de: str, ate: str, vendedores: dict, vendas: dict):
    info = vendedores[vendedor_id]
    proprio = total_vendido(vendedor_id, de, ate, vendas)
    percentual = float(info.get("percentual", 0))
    comissao = proprio * percentual / 100

    overrides_detalhe = []
    for over in info.get("overrides", []):
        outro_id = over.get("vendedor_id")
        outro_percentual = float(over.get("percentual", 0))
        if outro_id not in vendedores:
            continue
        outro_total = total_vendido(outro_id, de, ate, vendas)
        valor_over = round(outro_total * outro_percentual / 100, 2)
        comissao += valor_over
        overrides_detalhe.append({
            "vendedor_id": outro_id,
            "nome": vendedores[outro_id]["nome"],
            "percentual": outro_percentual,
            "total_vendido": outro_total,
            "valor": valor_over,
        })

    return {
        "total_vendido": proprio,
        "percentual": percentual,
        "comissao_propria": round(proprio * percentual / 100, 2),
        "overrides": overrides_detalhe,
        "comissao": round(comissao, 2),
        "total_bonus": total_vendido(vendedor_id, de, ate, vendas, tipo="bonus"),
    }


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


def exigir_vendedor():
    vendedor_id = session.get("vendedor_id")
    if not vendedor_id:
        return None
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return None
    return vendedor_id


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


# ---------- Vendas do vendedor logado ----------

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
    canal = (body.get("canal") or "").strip()
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


# ---------- Painel público de ranking (sem login, pensado pra ficar numa TV/monitor) ----------

@app.route("/api/metas")
def api_metas():
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
        grupo_hoje += hoje_v
        grupo_semana += semana_v
        grupo_mes += mes_v
        resultado.append({
            "id": vid,
            "nome": info["nome"],
            "foto": info.get("foto"),
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


# ---------- Área do gestor ----------

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
    if senha != cred.get("admin_senha"):
        registrar_acesso("admin", False)
        return jsonify({"erro": "Senha incorreta."}), 401
    session["admin"] = True
    registrar_acesso("admin", True)
    return jsonify({"ok": True})


def _hash_codigo(codigo: str) -> str:
    return hashlib.sha256(codigo.encode()).hexdigest()


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

    cred["admin_senha"] = nova_senha
    cred.pop("recuperacao_hash", None)
    cred.pop("recuperacao_gerado_em", None)
    escrever_json(CREDENCIAIS_FILE, cred)
    registrar_acesso("admin_recuperacao", True)
    return jsonify({"ok": True})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


def exigir_admin():
    return bool(session.get("admin"))


@app.route("/api/admin/me")
def api_admin_me():
    return jsonify({"logado": exigir_admin()})


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

    resultado = []
    total_geral = 0.0
    comissao_geral = 0.0
    qtd_vendas_geral = 0
    serie_por_mes = {}
    serie_por_dia = {}

    for vid in sorted(ids_alvo, key=lambda x: vendedores[x]["nome"]):
        info = vendedores[vid]
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
            "total_bonus": calc["total_bonus"],
            "qtd_vendas": len(lista_vendas),
            "vendas": lista_vendas,
            "bonus": lista_bonus,
            "confirmado_em": confirmado_em,
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

    return jsonify({
        "de": de,
        "ate": ate,
        "mes_unico": mes_unico,
        "vendedor_id": filtro_vendedor,
        "vendedores": resultado,
        "total_geral": round(total_geral, 2),
        "comissao_geral": round(comissao_geral, 2),
        "qtd_vendas_geral": qtd_vendas_geral,
        "ticket_medio": ticket_medio,
        "serie_mensal": serie_mensal,
        "serie_diaria": serie_diaria,
    })


def _nome_aba_excel(nome: str) -> str:
    """Aba do Excel não aceita \\ / ? * [ ] : nem mais de 31 caracteres."""
    limpo = re.sub(r'[\\/?*\[\]:]', "-", nome)
    return limpo[:31] or "Vendedor"


@app.route("/api/admin/exportar-mes-xlsx")
def api_admin_exportar_mes_xlsx():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401

    mes = request.args.get("mes", "")
    if len(mes) != 7 or mes[4] != "-":
        return jsonify({"erro": "Mês inválido."}), 400
    de, ate = f"{mes}-01", f"{mes}-31"

    vendedores = carregar_vendedores()
    vendas = carregar_vendas_todos(vendedores)

    wb = Workbook()
    wb.remove(wb.active)
    cabecalho = ["Data", "Produto", "SKU", "Canal", "Valor", "Devolução", "Valor Devolvido", "Valor Líquido"]

    for vid in sorted(vendedores, key=lambda x: vendedores[x]["nome"]):
        lista = [
            v for v in vendas.values()
            if v["vendedor_id"] == vid and de <= v["data"] <= ate and v.get("tipo", "venda") == "venda"
        ]
        lista.sort(key=lambda v: v["data"])

        ws = wb.create_sheet(_nome_aba_excel(vendedores[vid]["nome"]))
        ws.append(cabecalho)
        for v in lista:
            dev = v.get("devolucao")
            dev_tipo = ("Total" if dev.get("tipo") == "total" else "Parcial") if dev else ""
            dev_valor = dev.get("valor_devolvido") if dev else None
            ws.append([
                v["data"],
                v["produto"],
                v.get("sku", ""),
                v.get("canal", ""),
                v["valor"],
                dev_tipo,
                dev_valor,
                valor_liquido(v),
            ])
        for coluna, largura in zip("ABCDEFGH", (12, 42, 12, 14, 12, 12, 14, 14)):
            ws.column_dimensions[coluna].width = largura

    if not wb.sheetnames:
        wb.create_sheet("Vendedores").append(cabecalho)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"vendas_{mes}.xlsx",
    )


@app.route("/api/admin/vendedores", methods=["GET"])
def api_admin_listar_vendedores():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    return jsonify([
        {
            "id": vid,
            "nome": v["nome"],
            "percentual": v.get("percentual", 0),
            "overrides": v.get("overrides", []),
            "foto": v.get("foto"),
            "liberacao_retroativa": retroativo_ativo(v),
            "liberacao_retroativa_ate": v.get("liberacao_retroativa_ate") if retroativo_ativo(v) else None,
        }
        for vid, v in sorted(vendedores.items(), key=lambda kv: kv[1]["nome"])
    ])


@app.route("/api/admin/vendedores/<vendedor_id>/liberar-retroativo", methods=["POST"])
def api_admin_liberar_retroativo(vendedor_id):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return jsonify({"erro": "Vendedor não encontrado."}), 404
    ate = agora_br() + timedelta(minutes=LIBERACAO_RETROATIVA_MINUTOS)
    vendedores[vendedor_id]["liberacao_retroativa_ate"] = ate.isoformat(timespec="seconds")
    salvar_vendedores(vendedores)
    return jsonify({"ok": True, "liberacao_retroativa_ate": vendedores[vendedor_id]["liberacao_retroativa_ate"]})


@app.route("/api/admin/vendedores/<vendedor_id>/liberar-retroativo", methods=["DELETE"])
def api_admin_cancelar_liberacao_retroativo(vendedor_id):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return jsonify({"erro": "Vendedor não encontrado."}), 404
    vendedores[vendedor_id].pop("liberacao_retroativa_ate", None)
    salvar_vendedores(vendedores)
    return jsonify({"ok": True})


@app.route("/api/admin/vendedores", methods=["POST"])
def api_admin_salvar_vendedor():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(force=True)
    vendedor_id = (body.get("id") or "").strip().lower()
    nome = (body.get("nome") or "").strip()
    senha = body.get("senha")
    try:
        percentual = float(body.get("percentual"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Percentual inválido."}), 400
    if not vendedor_id or not nome:
        return jsonify({"erro": "Informe id e nome do vendedor."}), 400
    if percentual < 0 or percentual > 100:
        return jsonify({"erro": "Percentual deve estar entre 0 e 100."}), 400

    overrides = []
    for over in body.get("overrides", []):
        outro_id = (over.get("vendedor_id") or "").strip().lower()
        try:
            outro_percentual = float(over.get("percentual"))
        except (TypeError, ValueError):
            continue
        if not outro_id or outro_id == vendedor_id or outro_percentual <= 0:
            continue
        overrides.append({"vendedor_id": outro_id, "percentual": outro_percentual})

    vendedores = carregar_vendedores()
    existente = vendedores.get(vendedor_id, {})
    if not senha and not existente.get("senha"):
        return jsonify({"erro": "Defina uma senha para o vendedor."}), 400
    vendedores[vendedor_id] = {
        "nome": nome,
        "senha": senha if senha else existente.get("senha"),
        "percentual": percentual,
        "overrides": overrides,
    }
    if existente.get("foto"):
        vendedores[vendedor_id]["foto"] = existente["foto"]
    codigo_recuperacao = (body.get("codigo_recuperacao") or "").strip().upper()
    if codigo_recuperacao:
        if len(codigo_recuperacao) < 6:
            return jsonify({"erro": "O código de recuperação precisa ter pelo menos 6 caracteres."}), 400
        vendedores[vendedor_id]["recuperacao_hash"] = _hash_codigo(codigo_recuperacao)
    elif existente.get("recuperacao_hash"):
        vendedores[vendedor_id]["recuperacao_hash"] = existente["recuperacao_hash"]
    salvar_vendedores(vendedores)
    return jsonify({"ok": True})


@app.route("/api/recuperar-senha-vendedor", methods=["POST"])
def api_recuperar_senha_vendedor():
    if excedeu_tentativas_login("vendedor_recuperacao"):
        return jsonify({"erro": f"Muitas tentativas erradas. Aguarde {LOGIN_JANELA_MINUTOS} minutos e tente de novo."}), 429

    body = request.get_json(force=True)
    vendedor_id = (body.get("vendedor_id") or "").strip().lower()
    codigo = (body.get("codigo") or "").strip().upper()
    nova_senha = body.get("nova_senha") or ""

    vendedores = carregar_vendedores()
    info = vendedores.get(vendedor_id)
    hash_salvo = info.get("recuperacao_hash") if info else None
    if not info or not hash_salvo or _hash_codigo(codigo) != hash_salvo:
        registrar_acesso("vendedor_recuperacao", False, vendedor_id)
        return jsonify({"erro": "Código inválido."}), 401

    if len(nova_senha) < 4:
        return jsonify({"erro": "A nova senha precisa ter pelo menos 4 caracteres."}), 400

    vendedores[vendedor_id]["senha"] = nova_senha
    vendedores[vendedor_id].pop("recuperacao_hash", None)
    salvar_vendedores(vendedores)
    registrar_acesso("vendedor_recuperacao", True, vendedor_id)
    return jsonify({"ok": True})


@app.route("/api/admin/vendedores/<vendedor_id>", methods=["DELETE"])
def api_admin_remover_vendedor(vendedor_id):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return jsonify({"erro": "Vendedor não encontrado."}), 404
    del vendedores[vendedor_id]
    salvar_vendedores(vendedores)
    return jsonify({"ok": True})


@app.route("/api/admin/metas", methods=["POST"])
def api_admin_salvar_metas():
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(force=True)
    vendedores = carregar_vendedores()

    def limpar_trio(dados):
        resultado = {}
        for chave in ("diaria", "semanal", "mensal"):
            try:
                resultado[chave] = max(0.0, float(dados.get(chave, 0)))
            except (TypeError, ValueError):
                resultado[chave] = 0.0
        return resultado

    metas = {
        "grupo": limpar_trio(body.get("grupo", {})),
        "vendedores": {
            vid: limpar_trio(body.get("vendedores", {}).get(vid, {}))
            for vid in vendedores
        },
    }
    salvar_metas(metas)
    return jsonify({"ok": True, "metas": metas})


def _supabase_storage_upload(nome_arquivo: str, conteudo: bytes, content_type: str) -> None:
    url = f"{SUPABASE_URL}/storage/v1/object/fotos/{nome_arquivo}"
    req = urllib.request.Request(url, data=conteudo, method="POST", headers={
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": content_type,
        "x-upsert": "true",
    })
    urllib.request.urlopen(req).read()


def _supabase_storage_delete(nome_arquivo: str) -> None:
    url = f"{SUPABASE_URL}/storage/v1/object/fotos/{nome_arquivo}"
    req = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    })
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError:
        pass


@app.route("/api/admin/vendedores/<vendedor_id>/foto", methods=["POST"])
def api_admin_upload_foto(vendedor_id):
    if not exigir_admin():
        return jsonify({"erro": "Não autenticado."}), 401
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return jsonify({"erro": "Vendedor não encontrado."}), 404

    arquivo = request.files.get("foto")
    if not arquivo or not arquivo.filename:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400
    ext = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if ext not in EXTENSOES_FOTO_PERMITIDAS:
        return jsonify({"erro": "Formato inválido. Use JPG, PNG ou WEBP."}), 400

    foto_antiga = vendedores[vendedor_id].get("foto")
    nome_arquivo = f"{vendedor_id}.{ext}"

    if SUPABASE_URL:
        if foto_antiga:
            _supabase_storage_delete(foto_antiga)
        _supabase_storage_upload(nome_arquivo, arquivo.read(), arquivo.content_type or "application/octet-stream")
    else:
        FOTOS_DIR.mkdir(parents=True, exist_ok=True)
        if foto_antiga:
            (FOTOS_DIR / foto_antiga).unlink(missing_ok=True)
        arquivo.save(FOTOS_DIR / nome_arquivo)

    vendedores[vendedor_id]["foto"] = nome_arquivo
    salvar_vendedores(vendedores)
    return jsonify({"ok": True, "foto": nome_arquivo})


@app.route("/fotos/<path:filename>")
def servir_foto(filename):
    if SUPABASE_URL:
        return redirect(f"{SUPABASE_URL}/storage/v1/object/public/fotos/{filename}")
    return send_from_directory(FOTOS_DIR, filename)


@app.route("/simulador")
def pagina_simulador():
    return send_from_directory(STATIC_DIR, "simulador.html")


@app.route("/api/simulador/regras")
def api_simulador_regras():
    if not exigir_vendedor():
        return jsonify({"erro": "Não autenticado."}), 401
    regras = carregar_regras_simulador()
    if regras is None:
        return jsonify({"erro": "As regras de desconto ainda não foram sincronizadas."}), 404
    return jsonify(regras)


@app.route("/api/simulador/buscar")
def api_simulador_buscar():
    if not exigir_vendedor():
        return jsonify({"erro": "Não autenticado."}), 401
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(buscar_pecas_simulador(q))


@app.route("/api/simulador/simular", methods=["POST"])
def api_simulador_simular():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    cod_peca = (corpo.get("cod_peca") or "").strip()
    if not cod_peca:
        return jsonify({"erro": "cod_peca é obrigatório"}), 400

    peca = obter_peca_simulador(cod_peca)
    if not peca:
        return jsonify({"erro": "peça não encontrada"}), 404

    regras = carregar_regras_simulador()
    if regras is None:
        return jsonify({"erro": "As regras de desconto ainda não foram sincronizadas."}), 404

    valor_override = corpo.get("valor_base")
    desconto_escolhido = corpo.get("desconto_pct")
    resultado = calcular_simulacao_peca(peca, valor_override, desconto_escolhido, regras)
    resultado["cod_peca"] = peca["cod_peca"]
    resultado["nome_produto"] = peca["nome_produto"]
    resultado["etiqueta"] = peca["etiqueta"]
    resultado["apelido_veiculo"] = peca["apelido_veiculo"]
    resultado["tipo_peca_rotulo"] = peca["tipo_peca_rotulo"]
    return jsonify(resultado)


@app.route("/api/simulador/simular-rapido", methods=["POST"])
def api_simulador_simular_rapido():
    """Simulação sem buscar peça no catálogo: o vendedor informa valor,
    curva e faixa de tempo em estoque na mão — pensado pra atender o
    cliente rápido no balcão, sem precisar achar o item no sistema."""
    if not exigir_vendedor():
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    try:
        valor_base = float(corpo.get("valor_base"))
    except (TypeError, ValueError):
        return jsonify({"erro": "valor_base é obrigatório e deve ser numérico"}), 400
    if valor_base <= 0:
        return jsonify({"erro": "valor_base deve ser maior que zero"}), 400

    regras = carregar_regras_simulador()
    if regras is None:
        return jsonify({"erro": "As regras de desconto ainda não foram sincronizadas."}), 404

    curva = (corpo.get("curva") or "").strip()
    if curva not in regras["desconto_max_pct"]:
        return jsonify({"erro": "curva inválida"}), 400

    faixa_tempo_id = (corpo.get("faixa_tempo_id") or "").strip()
    ids_validos = {f["id"] for f in regras["faixas_tempo"]}
    if faixa_tempo_id not in ids_validos:
        return jsonify({"erro": "faixa_tempo_id inválida"}), 400
    dias_representativos = next(f["min_dias"] for f in regras["faixas_tempo"] if f["id"] == faixa_tempo_id)

    desconto_escolhido = corpo.get("desconto_pct")
    resultado = montar_simulacao(valor_base, curva, dias_representativos, desconto_escolhido, regras)
    resultado["dias_em_estoque"] = None  # é uma faixa escolhida à mão, não uma data real
    return jsonify(resultado)


@app.route("/api/simulador/peca/<cod_peca>/data-entrada", methods=["POST"])
def api_simulador_definir_data_entrada(cod_peca):
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    corpo = request.get_json(silent=True) or {}
    data_entrada = (corpo.get("data_entrada") or "").strip()
    if not data_entrada:
        return jsonify({"erro": "data_entrada é obrigatória"}), 400
    try:
        data_parseada = datetime.fromisoformat(data_entrada)
    except ValueError:
        return jsonify({"erro": "data inválida, use AAAA-MM-DD"}), 400

    nome = carregar_vendedores().get(vendedor_id, {}).get("nome", vendedor_id)
    ok = definir_data_entrada_simulador(cod_peca, data_parseada.strftime("%Y-%m-%d 00:00:00"), nome)
    if not ok:
        return jsonify({"erro": "peça não encontrada"}), 404
    return jsonify({"ok": True})


# ============================================================
# Retomada — CRM dos clientes que não fecharam
# ============================================================
# A fila não é montada aqui. Ela vem do projeto vendas-insights
# (app/gerar_fila_retomada.py), que lê as conversas do Totalk, classifica cada
# uma com IA e decide quem vale uma segunda tentativa; de lá ela é empurrada
# pra cá por vendas-insights/app/sincronizar_crm.py. Este arquivo só mostra a
# fila do vendedor logado e guarda o que ele marcou.
#
# Duas chaves separadas de propósito: `crm_fila_<id>` é substituída inteira a
# cada sincronização, `crm_status_<id>` é do vendedor e a sincronização nunca
# encosta nela. Se fossem a mesma chave, regerar a fila apagaria o trabalho
# já feito.

STATUS_RETOMADA = {
    "pendente": "Não chamei ainda",
    "chamei": "Chamei, sem resposta",
    "respondeu": "Respondeu",
    "vendeu": "Fechou venda",
    "perdido": "Não vai rolar",
}
STATUS_TRABALHADO = ("chamei", "respondeu", "vendeu", "perdido")


def _caminho_crm(prefixo: str, vendedor_id: str) -> Path:
    return resolver_pasta_dados() / f"crm_{prefixo}_{vendedor_id}.json"


def carregar_fila_retomada(vendedor_id: str):
    # Padrão None (nunca Path.exists()): em modo banco o arquivo local não
    # existe, e checar o disco faria a fila parecer vazia em produção.
    return ler_json(_caminho_crm("fila", vendedor_id), None)


def carregar_status_retomada(vendedor_id: str) -> dict:
    return ler_json(_caminho_crm("status", vendedor_id), None) or {}


def _resumo_retomada(itens: list, status: dict) -> dict:
    contagem = {chave: 0 for chave in STATUS_RETOMADA}
    for item in itens:
        atual = (status.get(item["sid"]) or {}).get("status", "pendente")
        contagem[atual if atual in contagem else "pendente"] += 1
    contagem["total"] = len(itens)
    contagem["trabalhados"] = sum(contagem[s] for s in STATUS_TRABALHADO)
    return contagem


@app.route("/retomada")
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
    itens = []
    for item in fila.get("itens", []):
        marca = status.get(item["sid"]) or {}
        itens.append({**item, "status": marca.get("status", "pendente"),
                      "marcado_em": marca.get("em")})
    # Pendente primeiro, e dentro dele prioridade ALTA sempre no topo — nesses a
    # conversa parou do nosso lado, ninguém disse não pro vendedor, e são os que
    # ele tem que chamar antes. Só depois a nota desempata. O que já foi
    # trabalhado desce mas continua na tela, pra ele corrigir a marcação ou
    # voltar num cliente que pediu pra chamar depois.
    itens.sort(key=lambda x: (x["status"] != "pendente", x["prio"] != "ALTA", -x["nota"]))
    return jsonify({
        "gerado_em": fila.get("gerado_em"),
        "de": fila.get("de"),
        "ate": fila.get("ate"),
        "itens": itens,
        "resumo": _resumo_retomada(fila.get("itens", []), status),
        "rotulos": STATUS_RETOMADA,
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
    # Só aceita cliente que está na fila DESTE vendedor: sem isso um id chutado
    # entraria no arquivo dele e apareceria no painel do gestor como trabalho.
    if not any(item["sid"] == sid for item in itens):
        return jsonify({"erro": "Este cliente não está na sua fila."}), 404
    status = carregar_status_retomada(vendedor_id)
    if novo == "pendente":
        status.pop(sid, None)
    else:
        status[sid] = {"status": novo, "em": agora_br().isoformat()}
    escrever_json(_caminho_crm("status", vendedor_id), status)
    return jsonify({"ok": True, "resumo": _resumo_retomada(itens, status)})


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
        resumo = _resumo_retomada(itens, carregar_status_retomada(vendedor_id))
        trabalhados = resumo["trabalhados"]
        linhas.append({
            "vendedor_id": vendedor_id,
            "nome": vendedor["nome"],
            **resumo,
            "pct_trabalhado": round(100 * trabalhados / len(itens)) if itens else 0,
            # Entre os que ele chamou, quantos deram sinal de vida. É a medida
            # que interessa: percentual sobre a fila inteira mede só o quanto
            # ele avançou na lista, não se a abordagem funcionou.
            "pct_resposta": (round(100 * (resumo["respondeu"] + resumo["vendeu"])
                                   / trabalhados) if trabalhados else 0),
        })
    return jsonify({"gerado_em": gerado_em, "periodo": periodo,
                    "vendedores": linhas, "rotulos": STATUS_RETOMADA})



if __name__ == "__main__":
    # use_reloader liga SÓ o recarregador: mudou o código, o servidor reinicia
    # sozinho, sem ninguém precisar lembrar de reiniciar na mão.
    #
    # Continua sem debug=True de propósito. debug traz junto o depurador do
    # Werkzeug, que numa tela de erro abre um console de Python rodando dentro
    # do servidor. Como isto escuta em 0.0.0.0, esse console ficaria ao alcance
    # de qualquer um na rede da loja -- daria pra ler os telefones dos clientes
    # e os segredos do processo.
    #
    # O reloader vigia os módulos Python importados, não a pasta data/. Os JSON
    # que a sincronização reescreve não disparam reinício, e os arquivos de
    # static/ são lidos a cada requisição -- editar a tela não pede reinício.
    app.run(host="0.0.0.0", port=8010, use_reloader=True)
