# -*- coding: utf-8 -*-
"""O que todo script da Nevada repetia — num lugar so.

Nasceu em 03/09/2026 (Fase 1 da SIMPLIFICACAO.md) de uma contagem: 15 arquivos
abriam o Postgres por conta propria, 12 escreviam a mesma query de upsert, 9
definiam o mesmo fuso, 6 tinham um _cred() e 4 um token_de_acesso() identicos,
18 repetiam o mesmo ajuste de stdout. Cada copia e um lugar onde uma regra pode
mudar so de um lado — foi assim que o mapa de atendentes ficou com tres versoes
e o painel creditou setembro a quem ja tinha saido.

Vale pros DOIS projetos. O vendas-insights chega aqui por `caminhos.portal("app")`
no sys.path, o mesmo caminho que ele ja usava pra importar o server.

O que NAO esta aqui: nada de Flask, nada do server. Isto e o minimo que um
script de linha de comando precisa pra falar com o banco, com o Google e com o
console sem reinventar.
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent          # portal-comissoes/
SEGREDOS = RAIZ / "segredos"

# ---------- tempo ----------
# Brasil nao tem horario de verao desde 2019; o servidor (Render) roda em UTC.
FUSO = timezone(timedelta(hours=-3))


def agora() -> datetime:
    return datetime.now(FUSO)


def hoje() -> date:
    return agora().date()


def carimbo() -> str:
    """ISO com segundos, no fuso da loja — o formato de todo `gerado_em`."""
    return agora().isoformat(timespec="seconds")


# ---------- console ----------
def saida_utf8() -> None:
    """Windows abre o stdout em cp1252 e o primeiro acento derruba o script.
    Chamar isto no `if __name__ == "__main__"` — antes era uma linha copiada
    em 18 arquivos, em duas variantes."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------- segredos ----------
def segredo(nome: str) -> Path:
    """Caminho de um arquivo em segredos/ — nunca o conteudo."""
    return SEGREDOS / nome


def url_banco() -> str:
    """DATABASE_URL do ambiente (e o que o pipeline_diario.bat exporta); sem
    ela, o arquivo segredos/database_url.txt. Sem nenhum dos dois, para com a
    mensagem que os scripts sempre deram — e nao cai silenciosamente num
    arquivo local dizendo que gravou (ja aconteceu com o importar_fluxo_caixa)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    arq = segredo("database_url.txt")
    if arq.exists():
        return arq.read_text(encoding="utf-8").strip()
    raise SystemExit("DATABASE_URL nao definida (nem no ambiente, nem em segredos/database_url.txt).")


# ---------- banco: a tabela dados_json ----------
def conexao():
    """Conexao crua, pra quem precisa de transacao propria (SELECT ... FOR UPDATE)."""
    import psycopg2
    return psycopg2.connect(url_banco())


def ler_chave(chave: str, padrao=None):
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("SELECT valor FROM dados_json WHERE chave = %s", (chave,))
        linha = cur.fetchone()
    return linha[0] if linha else padrao


def gravar_chaves(pares: dict) -> None:
    """Upsert de varias chaves numa transacao so. E a query que 12 scripts
    escreviam a mao; o CREATE TABLE continua aqui pra nenhum script depender da
    ordem em que ele e o servidor sobem."""
    from psycopg2.extras import Json
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS dados_json ("
                    "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)")
        for chave, valor in pares.items():
            cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                        "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                        (chave, Json(valor)))


def gravar_chave(chave: str, valor) -> None:
    gravar_chaves({chave: valor})


# ---------- Google (Ads, Analytics, Perfil, Search Console) ----------
def cred_google(*obrigatorias: str, dica: str = "") -> dict:
    """Le segredos/google_ads.json e confere as chaves que o chamador precisa.
    Cada API pede um conjunto (Analytics quer ga4_property_id; Ads quer
    developer_token e customer_id) — por isso a lista vem de fora."""
    arq = segredo("google_ads.json")
    if not arq.exists():
        raise SystemExit(f"Credenciais nao encontradas em {arq}" + (f"\n{dica}" if dica else ""))
    d = json.loads(arq.read_text(encoding="utf-8"))
    faltando = [k for k in ("client_id", "client_secret", "refresh_token", *obrigatorias) if not d.get(k)]
    if faltando:
        raise SystemExit(f"Faltam campos em {arq.name}: {', '.join(faltando)}")
    return d


def token_google(cred: dict) -> str:
    """Troca o refresh_token por um access_token (vale ~1h). Diferente do
    Mercado Livre, o refresh_token do Google NAO rotaciona: o mesmo serve
    pra sempre, ate alguem revogar."""
    corpo = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", corpo)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]
