"""
Busca uma janela especifica de datas e adiciona ao banco existente.

Serve pra tapar buraco de sincronizacao: o sync original comecou as 17h32 de
07/07, entao a primeira metade daquele dia nunca entrou. Como as tabelas usam
INSERT OR REPLACE, rodar de novo em cima do que ja existe e seguro.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))  # movido pra ferramentas/ em 03/09/2026
from sync import (BASE_URL, PAGE_SIZE, PAUSA_ENTRE_REQUISICOES, TOKEN, _conectar_db,
                  _requisitar, _salvar_mensagem, _salvar_sessao)


def buscar_janela(inicio_iso: str, fim_iso: str):
    pagina, total = 1, 0
    novos_ids = []
    conn = _conectar_db()
    try:
        while True:
            data = _requisitar("/chat/v2/session", {
                "CreatedAt.After": inicio_iso,
                "CreatedAt.Before": fim_iso,
                "PageNumber": pagina,
                "PageSize": PAGE_SIZE,
                "OrderBy": "createdat",
                "OrderDirection": "ASCENDING",
            })
            itens = data.get("items") or []
            for s in itens:
                _salvar_sessao(conn, s)
                novos_ids.append(s["id"])
            conn.commit()
            total += len(itens)
            print(f"  [sessoes] pagina {pagina}/{data.get('totalPages')} - {total}")
            if not data.get("hasMorePages"):
                break
            pagina += 1
            time.sleep(PAUSA_ENTRE_REQUISICOES)
    finally:
        conn.close()
    return novos_ids


def buscar_mensagens(ids):
    for i, sid in enumerate(ids, start=1):
        conn = _conectar_db()
        try:
            pagina = 1
            while True:
                data = _requisitar(f"/chat/v1/session/{sid}/message", {
                    "PageNumber": pagina, "PageSize": PAGE_SIZE,
                    "OrderDirection": "ASCENDING",
                })
                for m in data.get("items") or []:
                    _salvar_mensagem(conn, sid, m)
                conn.commit()
                if not data.get("hasMorePages"):
                    break
                pagina += 1
                time.sleep(PAUSA_ENTRE_REQUISICOES)
        except urllib.error.HTTPError as e:
            print(f"  [erro] sessao {sid}: {e}")
        finally:
            conn.close()
        if i % 25 == 0:
            print(f"  [mensagens] {i}/{len(ids)}")
        time.sleep(PAUSA_ENTRE_REQUISICOES)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("defina TOTALK_TOKEN no .env")
    inicio, fim = sys.argv[1], sys.argv[2]
    print(f"buscando janela {inicio} -> {fim}")
    ids = buscar_janela(inicio, fim)
    print(f"{len(ids)} sessoes na janela; buscando mensagens...")
    buscar_mensagens(ids)
    print("concluido.")
