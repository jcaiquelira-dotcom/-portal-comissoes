import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("TOTALK_API_TOKEN")
BASE_URL = "https://api.wts.chat"
SQLITE_PATH = ROOT / "vendas.db"

# Rate limit real da API: 1000 req/5min (~3,3/s) e 200 req/5s de burst.
# 0.4s entre chamadas fica bem abaixo dos dois, sem precisar de lógica de retry.
PAUSA_ENTRE_REQUISICOES = 0.4
PAGE_SIZE = 100
DIAS_PARA_TRAS = int(os.environ.get("DIAS_PARA_TRAS", "45"))


def _requisitar(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{BASE_URL}{path}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _conectar_db() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
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
        "raw TEXT)"
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
        "INSERT OR REPLACE INTO mensagens VALUES (?,?,?,?,?,?,?,?,?)",
        (
            m.get("id"), session_id, m.get("createdAt"), m.get("type"),
            m.get("direction"), m.get("status"), m.get("origin"), m.get("text"),
            json.dumps(m, ensure_ascii=False),
        ),
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
        raise SystemExit("Defina TOTALK_API_TOKEN no ambiente antes de rodar.")

    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_PARA_TRAS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Buscando sessoes criadas desde {desde}...")
    total = buscar_sessoes(desde)
    print(f"{total} sessoes salvas. Buscando mensagens de cada uma...")
    buscar_mensagens_de_todas_as_sessoes()
    print("Sincronizacao concluida.")
