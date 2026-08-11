import json
import os
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session

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

DATA_DIR_NAME = "data"
DIAS_MAXIMOS_RETROATIVOS = 7
MAX_LOG_ACESSOS = 500
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


def _chave_de(caminho: Path) -> str:
    """Deriva uma chave curta e única a partir do nome do arquivo (sem extensão) —
    todos os arquivos do projeto já têm nomes únicos (vendedores, vendas_brenda, etc.)."""
    return Path(caminho).stem


if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import Json as _PgJson

    def _db_conectar():
        return psycopg2.connect(DATABASE_URL)

    def _db_preparar_tabela() -> None:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS dados_json ("
                "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)"
            )
            conn.commit()

    _db_preparar_tabela()

    def _db_ler(chave: str, padrao):
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT valor FROM dados_json WHERE chave = %s", (chave,))
            linha = cur.fetchone()
            return linha[0] if linha else padrao

    def _db_escrever(chave: str, dados) -> None:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                (chave, _PgJson(dados)),
            )
            conn.commit()

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


def carregar_vendedores() -> dict:
    return ler_json(VENDEDORES_FILE, {})


def salvar_vendedores(vendedores: dict) -> None:
    escrever_json(VENDEDORES_FILE, vendedores)


def carregar_credenciais() -> dict:
    if not CREDENCIAIS_FILE.exists():
        escrever_json(CREDENCIAIS_FILE, {"admin_senha": "troque-esta-senha"})
    return ler_json(CREDENCIAIS_FILE, {})


def arquivo_vendas(vendedor_id: str) -> Path:
    """Cada vendedor tem seu próprio arquivo — assim cada um só grava no que é dele."""
    return resolver_pasta_dados() / f"vendas_{vendedor_id}.json"


def carregar_vendas_vendedor(vendedor_id: str) -> dict:
    return ler_json(arquivo_vendas(vendedor_id), {})


def salvar_vendas_vendedor(vendedor_id: str, vendas: dict) -> None:
    escrever_json(arquivo_vendas(vendedor_id), vendas)


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
        "quando": datetime.now().isoformat(timespec="seconds"),
        "tipo": tipo,
        "vendedor_id": vendedor_id,
        "nome": nome,
        "sucesso": sucesso,
        "ip": request.remote_addr,
    })
    escrever_json(LOG_ACESSOS_FILE, log[-MAX_LOG_ACESSOS:])


def excedeu_tentativas_login(tipo: str, vendedor_id: str = None) -> bool:
    """Bloqueia login depois de várias senhas erradas seguidas, pra dificultar
    tentativa de adivinhação por força bruta (importante agora que fica na internet)."""
    log = ler_json(LOG_ACESSOS_FILE, [])
    limite = datetime.now() - timedelta(minutes=LOGIN_JANELA_MINUTOS)
    falhas = 0
    for item in reversed(log):
        try:
            quando = datetime.fromisoformat(item["quando"])
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
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
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


def total_vendido(vendedor_id: str, de: str, ate: str, vendas: dict, tipo: str = "venda") -> float:
    total = sum(
        v["valor"]
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
    mes = request.args.get("mes", date.today().isoformat()[:7])
    de, ate = mes_para_intervalo(mes)
    vendedores = carregar_vendedores()
    vendas = carregar_vendas_todos(vendedores)
    return jsonify(calcular_comissao(vendedor_id, de, ate, vendedores, vendas))


def exigir_vendedor():
    vendedor_id = session.get("vendedor_id")
    if not vendedor_id:
        return None
    vendedores = carregar_vendedores()
    if vendedor_id not in vendedores:
        return None
    return vendedor_id


# ---------- Vendas do vendedor logado ----------

@app.route("/api/vendas", methods=["GET"])
def api_listar_vendas():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    mes = request.args.get("mes", date.today().isoformat()[:7])
    vendas = carregar_vendas_vendedor(vendedor_id)
    minhas = [
        {**v, "id": vid}
        for vid, v in vendas.items()
        if v["data"][:7] == mes and v.get("tipo", "venda") == "venda"
    ]
    minhas.sort(key=lambda v: v["data"], reverse=True)
    return jsonify(minhas)


def validar_valor_produto(body: dict) -> tuple[float, str, str]:
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
    return valor, produto, canal


def validar_data_venda(data_venda: str) -> None:
    """Confere se a data é válida e está dentro da janela permitida de lançamento."""
    try:
        data_obj = date.fromisoformat(data_venda)
    except ValueError:
        raise ValueError("Data inválida.")
    hoje = date.today()
    if data_obj > hoje:
        raise ValueError("Não é possível usar uma data futura.")
    if (hoje - data_obj).days > DIAS_MAXIMOS_RETROATIVOS:
        raise ValueError(
            f"Essa data é de mais de {DIAS_MAXIMOS_RETROATIVOS} dias atrás. "
            "Fale com o gestor para lançar vendas retroativas além desse prazo."
        )


def montar_venda(vendedor_id: str, body: dict) -> dict:
    """Valida os campos de uma venda e retorna o dict pronto para salvar.
    Lança ValueError com a mensagem de erro em caso de dado inválido."""
    valor, produto, canal = validar_valor_produto(body)
    data_venda = (body.get("data") or date.today().isoformat()).strip()
    validar_data_venda(data_venda)
    if mes_esta_fechado(data_venda):
        raise ValueError("Esse mês já foi fechado pelo gestor e não aceita mais lançamentos.")

    venda = {
        "vendedor_id": vendedor_id,
        "data": data_venda,
        "valor": valor,
        "produto": produto,
        "tipo": "venda",
        "criado_em": datetime.now().isoformat(timespec="seconds"),
    }
    if canal:
        venda["canal"] = canal
    return venda


@app.route("/api/vendas", methods=["POST"])
def api_criar_venda():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    body = request.get_json(force=True)
    try:
        venda = montar_venda(vendedor_id, body)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400

    vendas = carregar_vendas_vendedor(vendedor_id)
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
    body = request.get_json(force=True)
    linhas = body.get("vendas", [])
    if not isinstance(linhas, list) or not linhas:
        return jsonify({"erro": "Nenhuma linha para salvar."}), 400

    vendas = carregar_vendas_vendedor(vendedor_id)
    salvas = 0
    erros = []
    meses_afetados = set()
    for idx, linha in enumerate(linhas, start=1):
        try:
            venda = montar_venda(vendedor_id, linha)
        except ValueError as e:
            erros.append({"linha": idx, "erro": str(e)})
            continue
        vendas[uuid.uuid4().hex[:12]] = venda
        salvas += 1
        meses_afetados.add(venda["data"][:7])

    if salvas:
        salvar_vendas_vendedor(vendedor_id, vendas)
        for mes in meses_afetados:
            limpar_confirmacao(vendedor_id, mes)
    return jsonify({"ok": True, "salvas": salvas, "erros": erros})


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
    del vendas[venda_id]
    salvar_vendas_vendedor(vendedor_id, vendas)
    limpar_confirmacao(vendedor_id, mes_afetado)
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

    body = request.get_json(force=True)
    try:
        valor, produto, canal = validar_valor_produto(body)
        nova_data = (body.get("data") or atual["data"]).strip()
        if nova_data != atual["data"]:
            validar_data_venda(nova_data)
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
        "editado_em": datetime.now().isoformat(timespec="seconds"),
    }
    if canal:
        atualizada["canal"] = canal
    else:
        atualizada.pop("canal", None)
    vendas[venda_id] = atualizada
    salvar_vendas_vendedor(vendedor_id, vendas)

    limpar_confirmacao(vendedor_id, mes_antigo)
    if nova_data[:7] != mes_antigo:
        limpar_confirmacao(vendedor_id, nova_data[:7])
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
    confirmacoes[mes] = datetime.now().isoformat(timespec="seconds")
    salvar_confirmacoes(vendedor_id, confirmacoes)
    return jsonify({"ok": True, "confirmado_em": confirmacoes[mes]})


@app.route("/api/minha-confirmacao")
def api_minha_confirmacao():
    vendedor_id = exigir_vendedor()
    if not vendedor_id:
        return jsonify({"erro": "Não autenticado."}), 401
    mes = request.args.get("mes", date.today().isoformat()[:7])
    confirmacoes = carregar_confirmacoes(vendedor_id)
    return jsonify({"mes": mes, "confirmado_em": confirmacoes.get(mes)})


# ---------- Painel público de ranking (sem login, pensado pra ficar numa TV/monitor) ----------

@app.route("/api/metas")
def api_metas():
    return jsonify(carregar_metas())


@app.route("/api/painel/ranking")
def api_painel_ranking():
    vendedores = carregar_vendedores()
    vendas = carregar_vendas_todos(vendedores)
    metas = carregar_metas()

    hoje = date.today()
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
        "agora": datetime.now().isoformat(timespec="seconds"),
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

    hoje = date.today().isoformat()
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

    for vid in sorted(ids_alvo, key=lambda x: vendedores[x]["nome"]):
        info = vendedores[vid]
        lista_vendas = [v for v in por_vendedor.get(vid, []) if v.get("tipo", "venda") == "venda"]
        lista_vendas.sort(key=lambda v: v["data"], reverse=True)
        lista_bonus = [v for v in por_vendedor.get(vid, []) if v.get("tipo") == "bonus"]
        lista_bonus.sort(key=lambda v: v["data"], reverse=True)

        for v in lista_vendas:
            chave = v["data"][:7]
            serie_por_mes[chave] = serie_por_mes.get(chave, 0.0) + v["valor"]

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
    })


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
        }
        for vid, v in sorted(vendedores.items(), key=lambda kv: kv[1]["nome"])
    ])


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
    salvar_vendedores(vendedores)
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8010)
