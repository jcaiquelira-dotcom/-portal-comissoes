"""
ETL do Simulador de Desconto (agora parte do portal-comissoes) — ingere as
planilhas brutas em data/simulador/raw_erp/ e recarrega o catálogo do zero.

Modo de escrita:
    - Sem DATABASE_URL no ambiente: grava em data/simulador/simulador.db
      (SQLite), pro uso local do portal-comissoes.
    - Com DATABASE_URL: grava direto no Postgres (Supabase) que a produção
      usa — pensado pra rodar localmente (é aqui que o ERP tem os arquivos
      atualizados) apontando pro banco de produção.

Reaproveita a mesma classificação de tipo de peça e cálculo de curva ABC já
usados quando isso era o projeto irmão portal-simulador.

Uso:
    python scripts/etl_simulador.py
"""
import glob
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "simulador"
RAW_DIR = DATA_DIR / "raw_erp"
DB_PATH = DATA_DIR / "simulador.db"
VEICULOS_VENDAS_FILE = RAW_DIR / "Veiculos e peças vendidas.xlsx"
DATABASE_URL = os.environ.get("DATABASE_URL")

CODIGO_VEICULO_AVULSO = "V76"  # peças sem veículo real vinculado ("DUMMY")

# Copiado verbatim do etl.py original — mesma classificação de tipo de peça.
REGRAS = [
    (r"motor.{0,20}arranque", "modulos_eletronicos", "motor_arranque", "Motor de Arranque"),
    (r"motor.{0,15}(limpador|galhada)", "modulos_eletronicos", "motor_limpador", "Motor do Limpador de Para-brisa"),
    (r"motor.{0,15}ventoinha", "modulos_eletronicos", "motor_ventoinha", "Motor da Ventoinha"),
    (r"motor.{0,15}vidro", "modulos_eletronicos", "motor_vidro", "Motor de Vidro Elétrico"),
    (r"bloco.{0,5}(do\s*)?motor", "motores", "bloco_motor", "Bloco do Motor"),
    (r"motor\s*(completo|parcial)\b", "motores", "motor_completo", "Motor Completo/Parcial"),
    (r"cabecote", "motores", "cabecote", "Cabeçote"),
    (r"carter", "motores", "carter", "Cárter"),
    (r"virabrequim", "motores", "virabrequim", "Virabrequim"),
    (r"biela", "motores", "biela", "Biela"),
    (r"pistao", "motores", "pistao", "Pistão"),
    (r"correia\s*dentada", "motores", "correia_dentada", "Correia Dentada"),
    (r"coletor.{0,15}(admissao|escape)", "motores", "coletor", "Coletor de Admissão/Escape"),
    (r"turbina", "motores", "turbina", "Turbina"),
    (r"comando\s*(de\s*)?valvula", "motores", "comando_valvulas", "Comando de Válvulas"),
    (r"\btbi\b|corpo.{0,10}borboleta", "motores", "corpo_borboleta", "Corpo de Borboleta/TBI"),
    (r"catalisador", "motores", "catalisador", "Catalisador"),
    (r"tensor.{0,10}correia|esticador", "motores", "tensor_correia", "Tensor/Esticador de Correia"),
    (r"filtro\s*(de\s*)?ar\b|caixa.{0,10}(ressonancia|filtro\s*ar)", "motores", "admissao_ar", "Admissão de Ar/Filtro de Ar"),
    (r"bomba.{0,10}combustivel|reservatorio.{0,20}(combustivel|partida\s*fria)|canister", "motores", "sistema_combustivel", "Sistema de Combustível"),
    (r"\bmotor\b", "motores", "motor_outros", "Motor (outras peças)"),

    (r"caixa\s*de\s*marcha", "cambios", "cambio", "Câmbio/Caixa de Marcha"),
    (r"cambio", "cambios", "cambio", "Câmbio/Caixa de Marcha"),
    (r"transmissao", "cambios", "cambio", "Câmbio/Caixa de Marcha"),
    (r"embreagem", "cambios", "embreagem", "Embreagem"),
    (r"semi\s*eixo|homocinetica", "cambios", "semieixo", "Semi-eixo/Homocinética"),
    (r"cruzeta", "cambios", "cruzeta", "Cruzeta"),
    (r"\bcarda", "cambios", "carda", "Cardã"),
    (r"trambulador", "cambios", "trambulador", "Cabo/Alavanca Trambulador"),

    (r"alternador", "modulos_eletronicos", "alternador", "Alternador"),
    (r"sensor", "modulos_eletronicos", "sensor", "Sensor"),
    (r"central\s*eletronica|\bmodulo\b", "modulos_eletronicos", "modulo", "Módulo/Central Eletrônica"),
    (r"bobina.{0,10}ignicao", "modulos_eletronicos", "bobina_ignicao", "Bobina de Ignição"),
    (r"bico\s*injetor|flauta.{0,10}injetor", "modulos_eletronicos", "bico_injetor", "Bico Injetor"),
    (r"\brele\b|fusivel", "modulos_eletronicos", "rele", "Relé/Fusível"),
    (r"painel\s*(de\s*)?instrumentos|mini\s*frente", "modulos_eletronicos", "painel_instrumentos", "Painel de Instrumentos"),
    (r"central\s*multimidia", "modulos_eletronicos", "central_multimidia", "Central Multimídia"),
    (r"sonda\s*lambda", "modulos_eletronicos", "sonda_lambda", "Sonda Lambda"),
    (r"\bbateria\b", "modulos_eletronicos", "bateria", "Bateria"),
    (r"comutador", "modulos_eletronicos", "comutador", "Comutador de Partida/Ignição"),
    (r"\bairbag\b", "modulos_eletronicos", "airbag", "Airbag"),
    (r"\busb\b|entrada.{0,10}aux\b", "modulos_eletronicos", "usb_aux", "Entrada USB/Aux"),
    (r"pedal.{0,20}eletronic", "modulos_eletronicos", "pedal_eletronico", "Pedal Acelerador Eletrônico"),

    (r"chicote|cablagem", "chicotes_acabamentos", "chicote", "Chicote Elétrico"),
    (r"moldura", "chicotes_acabamentos", "moldura", "Moldura"),
    (r"revestimento", "chicotes_acabamentos", "revestimento", "Revestimento"),
    (r"capa\s*do\s*painel", "chicotes_acabamentos", "capa_painel", "Capa do Painel"),
    (r"console\s*central", "chicotes_acabamentos", "console", "Console Central"),
    (r"quebra.{0,5}sol", "chicotes_acabamentos", "quebra_sol", "Quebra-sol"),
    (r"\bbotao\b|chave.{0,10}(seta|limpador)", "chicotes_acabamentos", "botao_comando", "Botão/Chave de Comando"),
    (r"\bcabo\b", "chicotes_acabamentos", "cabo_diverso", "Cabo Diverso"),

    (r"banco|assento|encosto", "jogo_banco", "banco", "Banco/Assento"),

    (r"amortecedor", "suspensao_direcao", "amortecedor", "Amortecedor"),
    (r"bandeja", "suspensao_direcao", "bandeja", "Bandeja de Suspensão"),
    (r"pivo", "suspensao_direcao", "pivo", "Pivô"),
    (r"bucha", "suspensao_direcao", "bucha", "Bucha"),
    (r"caixa\s*(de\s*)?direcao", "suspensao_direcao", "caixa_direcao", "Caixa de Direção"),
    (r"volante", "suspensao_direcao", "volante", "Volante"),
    (r"suspensao", "suspensao_direcao", "suspensao", "Suspensão (geral)"),

    (r"pastilha", "freios", "pastilha", "Pastilha de Freio"),
    (r"disco.{0,5}freio", "freios", "disco_freio", "Disco de Freio"),
    (r"pinca.{0,5}freio", "freios", "pinca_freio", "Pinça de Freio"),
    (r"cilindro\s*mestre", "freios", "cilindro_mestre", "Cilindro Mestre"),
    (r"servo\s*freio", "freios", "servo_freio", "Servo Freio"),
    (r"\bfreio", "freios", "freio_geral", "Freio (geral)"),

    (r"radiador", "ar_arrefecimento", "radiador", "Radiador"),
    (r"ar\s*condicionado|compressor.{0,10}ar\b", "ar_arrefecimento", "ar_condicionado", "Ar-condicionado/Compressor"),
    (r"evaporador", "ar_arrefecimento", "evaporador", "Evaporador"),
    (r"condensador", "ar_arrefecimento", "condensador", "Condensador"),
    (r"bomba.{0,5}dagua|bomba.{0,5}d.?agua", "ar_arrefecimento", "bomba_agua", "Bomba d'Água"),
    (r"valvula\s*termostatica", "ar_arrefecimento", "valvula_termostatica", "Válvula Termostática"),
    (r"reservatorio.{0,15}(agua|radiador)", "ar_arrefecimento", "reservatorio_agua", "Reservatório de Água"),

    (r"porta\s*(dianteira|traseira|direita|esquerda)?", "lataria_vidros_farois_rodas", "porta", "Porta"),
    (r"\bteto\b", "lataria_vidros_farois_rodas", "teto", "Teto"),
    (r"cap[o0]\b", "lataria_vidros_farois_rodas", "capo", "Capô"),
    (r"lateral\s*(direita|esquerda)?", "lataria_vidros_farois_rodas", "lateral", "Lateral"),
    (r"para.?choque", "lataria_vidros_farois_rodas", "parachoque", "Para-choque"),
    (r"para.?lama", "lataria_vidros_farois_rodas", "paralama", "Para-lama"),
    (r"farol", "lataria_vidros_farois_rodas", "farol", "Farol"),
    (r"lanterna", "lataria_vidros_farois_rodas", "lanterna", "Lanterna"),
    (r"para.?brisa|vidro", "lataria_vidros_farois_rodas", "vidro", "Vidro/Para-brisa"),
    (r"\broda\b|calota", "lataria_vidros_farois_rodas", "roda", "Roda/Calota"),
    (r"retrovisor", "lataria_vidros_farois_rodas", "retrovisor", "Retrovisor"),
    (r"macaneta", "lataria_vidros_farois_rodas", "macaneta", "Maçaneta"),
    (r"tampa\s*traseira|porta.?malas|porta.?malas", "lataria_vidros_farois_rodas", "tampa_traseira", "Tampa Traseira/Porta-malas"),
    (r"grade\b", "lataria_vidros_farois_rodas", "grade", "Grade"),
    (r"spoiler", "lataria_vidros_farois_rodas", "spoiler", "Spoiler"),

    (r"diferencial", "diferenciais", "diferencial", "Diferencial"),
    (r"coroa.{0,5}pinhao", "diferenciais", "coroa_pinhao", "Coroa e Pinhão"),
]
REGRAS_COMPILADAS = [(re.compile(p), cat, slug, rotulo) for p, cat, slug, rotulo in REGRAS]

ABC_LIMITE_A = 0.80
ABC_LIMITE_B = 0.95


def normalizar(texto):
    if not texto:
        return ""
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


def classificar(nome_produto):
    alvo = normalizar(nome_produto)
    for regex, categoria, slug, rotulo in REGRAS_COMPILADAS:
        if regex.search(alvo):
            return categoria, slug, rotulo
    return "outros", "nao_classificado", "Não Classificado"


def to_iso_datetime(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    ts = pd.to_datetime(valor, errors="coerce", dayfirst=False)
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def to_float(valor):
    if valor is None or valor == "":
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if not pd.isna(f) else None


def to_str(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    s = str(valor).strip()
    return s if s else None


def ler_veiculos():
    if not VEICULOS_VENDAS_FILE.exists():
        print(f"AVISO: {VEICULOS_VENDAS_FILE} não encontrado — pulando veículos.")
        return []
    df = pd.read_excel(VEICULOS_VENDAS_FILE, sheet_name="Veiculos", header=0)
    linhas = []
    for _, r in df.iterrows():
        cod_v = to_str(r.get("CodV"))
        if not cod_v:
            continue
        linhas.append({
            "cod_v": cod_v,
            "apelido": to_str(r.get("Apelido veiculo")),
            "data_compra": to_iso_datetime(r.get("Data da Compra")),
        })
    return linhas


def ler_vendas():
    if not VEICULOS_VENDAS_FILE.exists():
        return []
    df = pd.read_excel(VEICULOS_VENDAS_FILE, sheet_name="Peças vendidas", header=0)
    linhas = []
    for _, r in df.iterrows():
        nome_item = to_str(r.get("nomeItem"))
        valor = to_float(r.get("valorItem"))
        if not nome_item or valor is None or valor <= 0:
            continue
        categoria, slug, rotulo = classificar(nome_item)
        linhas.append({"categoria": categoria, "tipo": slug, "rotulo": rotulo, "valor": valor})
    return linhas


def calcular_abc(vendas):
    por_categoria = {}
    agregados = {}
    for v in vendas:
        chave = (v["tipo"], v["rotulo"], v["categoria"])
        ag = agregados.setdefault(chave, {"qtd": 0, "receita": 0.0})
        ag["qtd"] += 1
        ag["receita"] += v["valor"]
    for (tipo, rotulo, categoria), ag in agregados.items():
        por_categoria.setdefault(categoria, []).append((tipo, rotulo, ag["qtd"], ag["receita"]))

    registros = []
    for categoria, itens in por_categoria.items():
        itens.sort(key=lambda x: x[3], reverse=True)
        receita_categoria = sum(i[3] for i in itens)
        acumulado = 0.0
        for tipo, rotulo, qtd, receita in itens:
            acumulado += receita
            pct_acumulado = acumulado / receita_categoria if receita_categoria else 0
            if pct_acumulado <= ABC_LIMITE_A:
                classe = "A"
            elif pct_acumulado <= ABC_LIMITE_B:
                classe = "B"
            else:
                classe = "C"
            registros.append({"tipo": tipo, "rotulo": rotulo, "categoria": categoria, "classe": classe})
    return registros


def ler_catalogo():
    arquivos = sorted(
        glob.glob(str(RAW_DIR / "relatorio_produtos_76_parte*.xlsx")),
        key=lambda p: int(re.search(r"parte(\d+)", p).group(1)),
    )
    agora = datetime.now().isoformat()
    linhas = []
    for caminho in arquivos:
        nome_arquivo = Path(caminho).name
        df = pd.read_excel(caminho, header=1)
        esperado = {"Cod Peça", "Nome Produto", "Etiqueta", "Preço", "Qtd Disponivel", "Código Veículo"}
        faltando = esperado - set(df.columns)
        if faltando:
            print(f"AVISO: {nome_arquivo} sem colunas esperadas {faltando} — pulando arquivo.")
            continue
        contagem = 0
        for _, r in df.iterrows():
            cod_peca = to_str(r.get("Cod Peça"))
            nome_produto = to_str(r.get("Nome Produto"))
            if not cod_peca or not nome_produto:
                continue
            categoria, slug, rotulo = classificar(nome_produto)
            qtd_disp = int(to_float(r.get("Qtd Disponivel")) or 0)
            linhas.append({
                "cod_peca": cod_peca, "nome_produto": nome_produto,
                "etiqueta": to_str(r.get("Etiqueta")), "preco": to_float(r.get("Preço")),
                "qtd_disponivel": qtd_disp, "condicao": to_str(r.get("Condição")),
                "localizacao": to_str(r.get("Localização Produto")),
                "codigo_veiculo": to_str(r.get("Código Veículo")),
                "categoria_auto": categoria, "tipo_peca_auto": slug, "tipo_peca_rotulo": rotulo,
                "ingestido_em": agora,
            })
            contagem += 1
        print(f"  {nome_arquivo}: {contagem} linhas")
    return linhas


def enriquecer(catalogo, veiculos, abc, overrides_existentes):
    veic_por_cod = {v["cod_v"]: v for v in veiculos}
    abc_por_tipo = {a["tipo"]: a["classe"] for a in abc}
    com_tempo = 0
    for peca in catalogo:
        codigo_veiculo = peca["codigo_veiculo"]
        veic = veic_por_cod.get(codigo_veiculo) if codigo_veiculo and codigo_veiculo != CODIGO_VEICULO_AVULSO else None
        peca["apelido_veiculo"] = veic["apelido"] if veic else None
        data_compra = veic["data_compra"] if veic else None
        if data_compra is None and peca["cod_peca"] in overrides_existentes:
            data_compra = overrides_existentes[peca["cod_peca"]]
        peca["data_compra_veiculo"] = data_compra
        peca["tempo_estoque_conhecido"] = 1 if data_compra else 0
        if data_compra:
            com_tempo += 1
        peca["classe_abc"] = abc_por_tipo.get(peca["tipo_peca_auto"], "C")
    return com_tempo


# ---------------- SQLite (uso local) ----------------

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS catalogo_erp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cod_peca TEXT NOT NULL UNIQUE,
    nome_produto TEXT NOT NULL,
    etiqueta TEXT,
    preco REAL,
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
);
CREATE INDEX IF NOT EXISTS idx_catalogo_disponivel ON catalogo_erp(qtd_disponivel);
CREATE VIRTUAL TABLE IF NOT EXISTS catalogo_erp_fts USING fts5(
    nome_produto, cod_peca, etiqueta, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS overrides_estoque (
    cod_peca TEXT PRIMARY KEY,
    data_entrada TEXT NOT NULL,
    definido_por TEXT,
    definido_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executado_em TEXT NOT NULL,
    linhas_catalogo INTEGER,
    linhas_disponiveis INTEGER,
    linhas_com_tempo_estoque INTEGER,
    duracao_seg REAL
);
"""


def rodar_sqlite(catalogo, veiculos, abc, duracao_ate_aqui):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQLITE)

    overrides = dict(conn.execute("SELECT cod_peca, data_entrada FROM overrides_estoque").fetchall())
    com_tempo = enriquecer(catalogo, veiculos, abc, overrides)

    conn.executescript("DELETE FROM catalogo_erp; DELETE FROM catalogo_erp_fts;")
    conn.executemany(
        "INSERT INTO catalogo_erp (cod_peca, nome_produto, etiqueta, preco, qtd_disponivel, condicao, "
        "localizacao, codigo_veiculo, categoria_auto, tipo_peca_auto, tipo_peca_rotulo, apelido_veiculo, "
        "data_compra_veiculo, tempo_estoque_conhecido, classe_abc, ingestido_em) "
        "VALUES (:cod_peca,:nome_produto,:etiqueta,:preco,:qtd_disponivel,:condicao,:localizacao,"
        ":codigo_veiculo,:categoria_auto,:tipo_peca_auto,:tipo_peca_rotulo,:apelido_veiculo,"
        ":data_compra_veiculo,:tempo_estoque_conhecido,:classe_abc,:ingestido_em)",
        catalogo,
    )
    rows = conn.execute("SELECT rowid, nome_produto, cod_peca, etiqueta FROM catalogo_erp").fetchall()
    conn.executemany("INSERT INTO catalogo_erp_fts (rowid, nome_produto, cod_peca, etiqueta) VALUES (?,?,?,?)", rows)

    disponiveis = sum(1 for p in catalogo if p["qtd_disponivel"] > 0)
    conn.execute(
        "INSERT INTO etl_execucoes (executado_em, linhas_catalogo, linhas_disponiveis, "
        "linhas_com_tempo_estoque, duracao_seg) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(), len(catalogo), disponiveis, com_tempo, duracao_ate_aqui),
    )
    conn.commit()
    conn.close()
    return len(catalogo), disponiveis, com_tempo


# ---------------- Postgres (uso em produção) ----------------

SCHEMA_POSTGRES = """
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
);
CREATE INDEX IF NOT EXISTS idx_catalogo_disponivel ON catalogo_erp(qtd_disponivel);
CREATE TABLE IF NOT EXISTS overrides_estoque (
    cod_peca TEXT PRIMARY KEY,
    data_entrada TEXT NOT NULL,
    definido_por TEXT,
    definido_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_execucoes (
    id SERIAL PRIMARY KEY,
    executado_em TEXT NOT NULL,
    linhas_catalogo INTEGER,
    linhas_disponiveis INTEGER,
    linhas_com_tempo_estoque INTEGER,
    duracao_seg DOUBLE PRECISION
);
"""


def rodar_postgres(catalogo, veiculos, abc, duracao_ate_aqui):
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_POSTGRES)
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
            except Exception:
                conn.rollback()
                cur.execute(SCHEMA_POSTGRES)
            conn.commit()

            cur.execute("SELECT cod_peca, data_entrada FROM overrides_estoque")
            overrides = dict(cur.fetchall())
            com_tempo = enriquecer(catalogo, veiculos, abc, overrides)

            cur.execute("DELETE FROM catalogo_erp")
            psycopg2.extras.execute_batch(
                cur,
                "INSERT INTO catalogo_erp (cod_peca, nome_produto, etiqueta, preco, qtd_disponivel, condicao, "
                "localizacao, codigo_veiculo, categoria_auto, tipo_peca_auto, tipo_peca_rotulo, apelido_veiculo, "
                "data_compra_veiculo, tempo_estoque_conhecido, classe_abc, ingestido_em) "
                "VALUES (%(cod_peca)s,%(nome_produto)s,%(etiqueta)s,%(preco)s,%(qtd_disponivel)s,%(condicao)s,"
                "%(localizacao)s,%(codigo_veiculo)s,%(categoria_auto)s,%(tipo_peca_auto)s,%(tipo_peca_rotulo)s,"
                "%(apelido_veiculo)s,%(data_compra_veiculo)s,%(tempo_estoque_conhecido)s,%(classe_abc)s,%(ingestido_em)s)",
                catalogo,
                page_size=500,
            )
            disponiveis = sum(1 for p in catalogo if p["qtd_disponivel"] > 0)
            cur.execute(
                "INSERT INTO etl_execucoes (executado_em, linhas_catalogo, linhas_disponiveis, "
                "linhas_com_tempo_estoque, duracao_seg) VALUES (%s,%s,%s,%s,%s)",
                (datetime.now().isoformat(), len(catalogo), disponiveis, com_tempo, duracao_ate_aqui),
            )
            conn.commit()
    finally:
        conn.close()
    return len(catalogo), disponiveis, com_tempo


def main():
    inicio = time.time()
    print(f"Modo: {'Postgres (produção)' if DATABASE_URL else 'SQLite local'}")

    print("\n== Veículos ==")
    veiculos = ler_veiculos()
    print(f"  {len(veiculos)} veículos")

    print("\n== Vendas (histórico p/ curva ABC) ==")
    vendas = ler_vendas()
    print(f"  {len(vendas)} vendas")

    print("\n== Curva ABC por tipo de peça ==")
    abc = calcular_abc(vendas)
    print(f"  {len(abc)} tipos classificados")

    print("\n== Catálogo ERP ==")
    catalogo = ler_catalogo()
    print(f"  total: {len(catalogo)} linhas")

    duracao_parcial = time.time() - inicio
    print("\n== Gravando + enriquecendo (tempo em estoque + curva por peça) ==")
    if DATABASE_URL:
        total, disponiveis, com_tempo = rodar_postgres(catalogo, veiculos, abc, duracao_parcial)
    else:
        total, disponiveis, com_tempo = rodar_sqlite(catalogo, veiculos, abc, duracao_parcial)
    print(f"  {disponiveis} disponíveis em estoque, {com_tempo} com tempo em estoque conhecido")

    print(f"\nDuração total: {time.time() - inicio:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
