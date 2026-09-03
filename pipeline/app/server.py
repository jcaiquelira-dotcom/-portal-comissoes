import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
PRODUCAO = os.environ.get("VENDAS_INSIGHTS_PRODUCAO") == "1"

# Secret na própria URL do webhook, já que o Totalk não ofereceu campo de
# assinatura/HMAC na configuração — sem isso, qualquer um que descubra a URL
# poderia mandar eventos falsos.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

DATABASE_URL = os.environ.get("DATABASE_URL")
SQLITE_PATH = ROOT / "eventos.db"

app = Flask(__name__)


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if DATABASE_URL:
    import psycopg2

    def _db_conectar():
        return psycopg2.connect(DATABASE_URL)

    def _db_preparar_tabela() -> None:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS eventos_totalk ("
                "id SERIAL PRIMARY KEY, "
                "recebido_em TIMESTAMPTZ NOT NULL, "
                "tipo_evento TEXT, "
                "payload JSONB NOT NULL)"
            )
            conn.commit()

    _db_preparar_tabela()

    def salvar_evento(tipo_evento, payload: dict) -> None:
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eventos_totalk (recebido_em, tipo_evento, payload) "
                "VALUES (%s, %s, %s)",
                (_agora_iso(), tipo_evento, json.dumps(payload)),
            )
            conn.commit()

    def listar_eventos(limite: int):
        with _db_conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT recebido_em, tipo_evento, payload FROM eventos_totalk "
                "ORDER BY id DESC LIMIT %s", (limite,))
            return [{"recebido_em": str(r[0]), "tipo": r[1], "payload": r[2]}
                    for r in cur.fetchall()]

else:
    def _sqlite_conectar():
        conn = sqlite3.connect(SQLITE_PATH)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS eventos_totalk ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "recebido_em TEXT NOT NULL, "
            "tipo_evento TEXT, "
            "payload TEXT NOT NULL)"
        )
        return conn

    def salvar_evento(tipo_evento, payload: dict) -> None:
        with _sqlite_conectar() as conn:
            conn.execute(
                "INSERT INTO eventos_totalk (recebido_em, tipo_evento, payload) "
                "VALUES (?, ?, ?)",
                (_agora_iso(), tipo_evento, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def listar_eventos(limite: int):
        with _sqlite_conectar() as conn:
            linhas = conn.execute(
                "SELECT recebido_em, tipo_evento, payload FROM eventos_totalk "
                "ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [{"recebido_em": r[0], "tipo": r[1], "payload": json.loads(r[2])}
                for r in linhas]


def _tipo_evento_de(payload: dict) -> str | None:
    # Formato exato do payload do Totalk ainda não confirmado (sem doc pública) —
    # tenta os nomes de campo mais prováveis e cai pra None se não achar,
    # já que o evento inteiro é salvo de qualquer forma em `payload`.
    for chave in ("event", "evento", "type", "tipo", "eventType"):
        if isinstance(payload, dict) and chave in payload:
            return str(payload[chave])
    return None


@app.route("/webhook/totalk/<secret>", methods=["POST"])
def receber_webhook(secret):
    if not WEBHOOK_SECRET or not secrets.compare_digest(secret, WEBHOOK_SECRET):
        return jsonify({"error": "not found"}), 404

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid json"}), 400

    salvar_evento(_tipo_evento_de(payload), payload)
    return jsonify({"ok": True}), 200


@app.route("/webhook/totalk/<secret>/ultimos", methods=["GET"])
def inspecionar_eventos(secret):
    """Mostra os últimos eventos crus recebidos.

    Existe porque o formato do payload do Totalk não é documentado: sem ver um
    evento real não dá pra saber em que campo vem o id da sessão nem como
    distinguir mensagem do cliente de mensagem da loja. Assim que o parsing
    estiver fechado, este endpoint pode sair.

    Devolve conteúdo de conversa de cliente, então fica atrás do mesmo segredo
    do webhook e limitado a poucos eventos por chamada.
    """
    if not WEBHOOK_SECRET or not secrets.compare_digest(secret, WEBHOOK_SECRET):
        return jsonify({"error": "not found"}), 404
    try:
        limite = min(int(request.args.get("n", 10)), 50)
    except ValueError:
        limite = 10
    try:
        return jsonify({"eventos": listar_eventos(limite)})
    except Exception as e:  # tabela ainda não existe = nada chegou
        return jsonify({"eventos": [], "aviso": f"{type(e).__name__}: {e}"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8020))
    app.run(host="0.0.0.0", port=port, debug=not PRODUCAO)
