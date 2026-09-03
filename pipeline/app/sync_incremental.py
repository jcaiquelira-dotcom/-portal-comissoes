"""
Atualiza vendas.db com o que mudou no Totalk, sem rebaixar a base inteira.

Por que existe, se ja existe o sync.py: o sync.py refaz TODAS as sessoes do banco
e rebaixa todas as mensagens de cada uma. Com 8 mil sessoes e 156 mil mensagens
isso passa de uma hora e gasta a cota da API pra reescrever dado identico.

A economia vem de `lastInteractionDate`, que a propria listagem de sessoes ja
devolve: conversa cuja ultima atividade foi ha semanas nao recebe mensagem nova,
entao so as recem-ativas precisam ser rebaixadas.

Nao da pra comparar `lastInteractionDate` (nem `lastMessageIn`/`lastMessageOut`)
com a data da ultima mensagem guardada pra decidir isso -- foi a primeira
tentativa e apontou movimento em metade da base parada. Esses campos contam
evento interno do Totalk (acao de atendente, mudanca de status) que o endpoint
de mensagens nao devolve, entao ficam sempre alguns segundos a frente da ultima
mensagem real. Conferido baixando uma sessao suspeita inteira de novo: a API
devolveu exatamente as mesmas mensagens que ja estavam no banco.

Isso importa mais do que parece pra fila de retomada: buscar so as sessoes
criadas depois do ultimo sync pegaria as conversas novas, mas perderia a resposta
que chegou hoje numa conversa aberta semana passada. Esse cliente continuaria
marcado como "sumiu" e o vendedor ligaria pra quem ja respondeu.

Uso:
    python app/sync_incremental.py              # ultimos 60 dias de sessoes
    python app/sync_incremental.py --dias 90
    python app/sync_incremental.py --so-listar  # nao baixa nada, so diz o que mudou
"""

import argparse
import sqlite3
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync import (  # noqa: E402  (precisa do sys.path acima)
    PAGE_SIZE,
    PAUSA_ENTRE_REQUISICOES,
    TOKEN,
    _conectar_db,
    _requisitar,
    _salvar_mensagem,
    _salvar_sessao,
)

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
def listar_sessoes(desde_iso):
    """Baixa a listagem de sessoes (barato: ~100 por pagina, sem as mensagens)."""
    sessoes, pagina = [], 1
    while True:
        d = _requisitar("/chat/v2/session", {
            "CreatedAt.After": desde_iso, "PageNumber": pagina, "PageSize": PAGE_SIZE,
            "OrderBy": "createdat", "OrderDirection": "ASCENDING",
        })
        sessoes.extend(d.get("items") or [])
        if pagina % 20 == 0:
            print(f"  listando... pagina {pagina}/{d.get('totalPages')}", flush=True)
        if not d.get("hasMorePages"):
            break
        pagina += 1
        time.sleep(PAUSA_ENTRE_REQUISICOES)
    return sessoes


def decidir_o_que_baixar(conn, sessoes, dias_ativas):
    """Separa em: sessao nova, sessao ativa recentemente, e sessao parada."""
    com_mensagem = {
        sid for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens")
    }
    conhecidas = {sid for (sid,) in conn.execute("SELECT id FROM sessoes")}
    corte = (datetime.now(timezone.utc) - timedelta(days=dias_ativas)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    novas, ativas, paradas = [], [], 0
    for s in sessoes:
        sid = s["id"]
        if sid not in conhecidas or sid not in com_mensagem:
            novas.append(s)          # nunca vista, ou vista sem mensagem baixada
        elif (s.get("lastInteractionDate") or "") >= corte:
            ativas.append(s)         # andou dentro da janela: pode ter mensagem nova
        else:
            paradas += 1
    return novas, ativas, paradas


def baixar_mensagens(session_id):
    """Todas as mensagens da sessao. INSERT OR REPLACE cuida da sobreposicao."""
    conn = _conectar_db()
    n = 0
    try:
        pagina = 1
        while True:
            d = _requisitar(f"/chat/v1/session/{session_id}/message",
                            {"PageNumber": pagina, "PageSize": PAGE_SIZE,
                             "OrderDirection": "ASCENDING"})
            itens = d.get("items") or []
            for m in itens:
                _salvar_mensagem(conn, session_id, m)
            conn.commit()
            n += len(itens)
            if not d.get("hasMorePages"):
                break
            pagina += 1
            time.sleep(PAUSA_ENTRE_REQUISICOES)
    except urllib.error.HTTPError as e:
        print(f"  [erro] sessao {session_id[:8]}: {e}")
    finally:
        conn.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=60,
                    help="quantos dias pra tras listar sessoes (padrao 60)")
    ap.add_argument("--dias-ativas", type=int, default=10,
                    help="rebaixa mensagens de sessao com atividade nos ultimos N dias")
    ap.add_argument("--so-listar", action="store_true",
                    help="mostra o que mudou e sai, sem baixar mensagem")
    args = ap.parse_args()

    if not TOKEN:
        raise SystemExit("Defina TOTALK_TOKEN no .env antes de rodar.")

    desde = (datetime.now(timezone.utc) - timedelta(days=args.dias)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(ROOT / "vendas.db")
    antes = conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0]
    antes_msg = conn.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
    ate_antes = conn.execute("SELECT MAX(created_at) FROM sessoes").fetchone()[0]
    print(f"banco: {antes:,} sessoes, {antes_msg:,} mensagens, ate {ate_antes[:10]}")
    print(f"listando sessoes criadas desde {desde[:10]}...", flush=True)

    sessoes = listar_sessoes(desde)
    novas, ativas, paradas = decidir_o_que_baixar(conn, sessoes, args.dias_ativas)
    print(f"\n  {len(sessoes):,} sessoes na janela")
    print(f"  {len(novas):,} novas          -> baixar mensagens")
    print(f"  {len(ativas):,} ativas ({args.dias_ativas}d)   -> rebaixar mensagens")
    print(f"  {paradas:,} sem mudanca    -> pular")
    total = len(novas) + len(ativas)
    print(f"\n  a baixar: {total:,} sessoes em vez de {antes:,} "
          f"({1 - total/antes:.0%} de economia)")
    if args.so_listar:
        conn.close()
        return

    # grava a listagem toda: status, fim de atendimento e ultima interacao mudam
    # mesmo em sessao que nao recebeu mensagem nova.
    for s in sessoes:
        _salvar_sessao(conn, s)
    conn.commit()
    conn.close()

    if not total:
        print("\nnada novo pra baixar.")
        return

    t0 = time.time()
    baixadas = 0
    for i, s in enumerate(novas + ativas, start=1):
        baixadas += baixar_mensagens(s["id"])
        if i % 25 == 0 or i == total:
            passou = time.time() - t0
            falta = (total - i) / (i / passou) / 60 if i else 0
            print(f"  {i}/{total} sessoes | {baixadas:,} mensagens | "
                  f"faltam ~{falta:.0f} min", flush=True)
        time.sleep(PAUSA_ENTRE_REQUISICOES)

    conn = sqlite3.connect(ROOT / "vendas.db")
    depois = conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0]
    depois_msg = conn.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0]
    ate = conn.execute("SELECT MAX(created_at) FROM sessoes").fetchone()[0]
    conn.close()
    print(f"\nsessoes : {antes:,} -> {depois:,}  (+{depois-antes:,})")
    print(f"mensagens: {antes_msg:,} -> {depois_msg:,}  (+{depois_msg-antes_msg:,})")
    print(f"cobertura ate {ate[:10]} | {(time.time()-t0)/60:.0f} min")


if __name__ == "__main__":
    main()
