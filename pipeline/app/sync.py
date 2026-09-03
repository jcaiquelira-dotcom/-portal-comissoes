import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

from config import env

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
TOKEN = env("TOTALK_TOKEN", obrigatorio=False) or os.environ.get("TOTALK_API_TOKEN")
BASE_URL = "https://api.wts.chat"
SQLITE_PATH = ROOT / "vendas.db"

# Rate limit real da API: 1000 req/5min (~3,3/s) e 200 req/5s de burst.
# 0.4s entre chamadas fica bem abaixo dos dois.
PAUSA_ENTRE_REQUISICOES = 0.4
# Queda de rede não pode derrubar uma sincronização de 40 minutos. Já aconteceu:
# o Wi-Fi oscilou no meio da rodada, o DNS parou de resolver api.wts.chat e o
# script morreu com getaddrinfo failed, perdendo o resto da fila.
TENTATIVAS_REDE = 6
PAGE_SIZE = 100
DIAS_PARA_TRAS = int(os.environ.get("DIAS_PARA_TRAS", "45"))


def _requisitar(path: str, params: dict) -> dict:
    """Uma chamada à API, com espera crescente quando a rede cai.

    Só repete o que é transitório: queda de rede, 429 (excesso de chamadas) e
    erro 5xx do servidor. 401 e 404 sobem na hora -- repetir não conserta token
    errado nem sessão que não existe, e mascararia o problema real.
    """
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{BASE_URL}{path}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    for tentativa in range(TENTATIVAS_REDE):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or tentativa == TENTATIVAS_REDE - 1:
                raise
            espera = min(60, 2 ** tentativa * 5)
            print(f"  [rede] HTTP {e.code}, tentando de novo em {espera}s")
            time.sleep(espera)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if tentativa == TENTATIVAS_REDE - 1:
                raise
            espera = min(60, 2 ** tentativa * 5)
            motivo = getattr(e, "reason", e)
            print(f"  [rede] {motivo} — tentando de novo em {espera}s", flush=True)
            time.sleep(espera)


# O JSON bruto de cada mensagem mora em vendas_raw.db desde 03/09/2026 (Fase 5):
# era 57% do vendas.db e ninguem lia — so o userId, que virou coluna. Continua
# sendo gravado, num arquivo a parte, pra quando alguem precisar de um campo
# que nao previmos. Ver pipeline/ferramentas/separar_raw.py.
RAW_PATH = SQLITE_PATH.with_name("vendas_raw.db")


def _conectar_db() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("ATTACH DATABASE ? AS bruto", (str(RAW_PATH),))
    conn.execute("CREATE TABLE IF NOT EXISTS bruto.mensagens_raw (id TEXT PRIMARY KEY, raw TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessoes ("
        "id TEXT PRIMARY KEY, created_at TEXT, ended_at TEXT, status TEXT, "
        "contact_id TEXT, department_id TEXT, user_id TEXT, "
        "number TEXT, origin TEXT, utm TEXT, "
        "time_service TEXT, time_wait TEXT, first_response_at TEXT, "
        "bot_id TEXT, last_message_text TEXT, last_interaction_date TEXT, "
        "raw TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mensagens ("
        "id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT, "
        "type TEXT, direction TEXT, status TEXT, origin TEXT, text TEXT, "
        "user_id TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON mensagens(session_id)")
    return conn


def _salvar_sessao(conn: sqlite3.Connection, s: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sessoes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            s.get("id"), s.get("createdAt"), s.get("endAt"), s.get("status"),
            s.get("contactId"), s.get("departmentId"), s.get("userId"),
            s.get("number"), s.get("origin"),
            json.dumps(s.get("utm"), ensure_ascii=False) if s.get("utm") else None,
            s.get("timeService"), s.get("timeWait"), s.get("firstResponseAt"),
            s.get("botId"), s.get("lastMessageText"), s.get("lastInteractionDate"),
            json.dumps(s, ensure_ascii=False),
        ),
    )


def _salvar_mensagem(conn: sqlite3.Connection, session_id: str, m: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO mensagens "
        "(id, session_id, created_at, type, direction, status, origin, text, user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            m.get("id"), session_id, m.get("createdAt"), m.get("type"),
            m.get("direction"), m.get("status"), m.get("origin"), m.get("text"),
            m.get("userId"),
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO bruto.mensagens_raw (id, raw) VALUES (?, ?)",
        (m.get("id"), json.dumps(m, ensure_ascii=False)),
    )


def buscar_sessoes(desde_iso: str):
    pagina = 1
    total_sessoes = 0
    conn = _conectar_db()
    try:
        while True:
            data = _requisitar(
                "/chat/v2/session",
                {
                    "CreatedAt.After": desde_iso,
                    "PageNumber": pagina,
                    "PageSize": PAGE_SIZE,
                    "OrderBy": "createdat",
                    "OrderDirection": "ASCENDING",
                },
            )
            itens = data.get("items") or []
            for s in itens:
                _salvar_sessao(conn, s)
            conn.commit()
            total_sessoes += len(itens)
            print(f"[sessoes] pagina {pagina}/{data.get('totalPages')} - {total_sessoes} acumuladas")
            if not data.get("hasMorePages"):
                break
            pagina += 1
            time.sleep(PAUSA_ENTRE_REQUISICOES)
    finally:
        conn.close()
    return total_sessoes


def buscar_mensagens_de_todas_as_sessoes():
    conn = _conectar_db()
    try:
        sessao_ids = [row[0] for row in conn.execute("SELECT id FROM sessoes")]
    finally:
        conn.close()

    for i, session_id in enumerate(sessao_ids, start=1):
        conn = _conectar_db()
        try:
            pagina = 1
            while True:
                data = _requisitar(
                    f"/chat/v1/session/{session_id}/message",
                    {"PageNumber": pagina, "PageSize": PAGE_SIZE, "OrderDirection": "ASCENDING"},
                )
                itens = data.get("items") or []
                for m in itens:
                    _salvar_mensagem(conn, session_id, m)
                conn.commit()
                if not data.get("hasMorePages"):
                    break
                pagina += 1
                time.sleep(PAUSA_ENTRE_REQUISICOES)
        except urllib.error.HTTPError as e:
            print(f"[mensagens] erro na sessao {session_id}: {e}")
        finally:
            conn.close()
        if i % 50 == 0:
            print(f"[mensagens] {i}/{len(sessao_ids)} sessoes processadas")
        time.sleep(PAUSA_ENTRE_REQUISICOES)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina TOTALK_TOKEN no .env antes de rodar.")

    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_PARA_TRAS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Buscando sessoes criadas desde {desde}...")
    total = buscar_sessoes(desde)
    print(f"{total} sessoes salvas. Buscando mensagens de cada uma...")
    buscar_mensagens_de_todas_as_sessoes()
    print("Sincronizacao concluida.")
