import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
SQLITE_PATH = ROOT / "vendas.db"

STOPWORDS = {
    "para", "com", "uma", "que", "voce", "vocmonth", "você", "vocês", "esse", "essa",
    "isso", "tem", "temos", "pode", "poderia", "queria", "gostaria", "saber", "sobre",
    "obrigado", "obrigada", "bom", "boa", "dia", "tarde", "noite", "ola", "olá", "oi",
    "sim", "nao", "não", "esta", "está", "estou", "aqui", "meu", "minha", "seu", "sua",
    "mais", "muito", "ainda", "então", "vou", "fico", "fica", "pelo", "pela", "pra",
    "por", "favor", "preciso", "precisando", "necessitamos", "informacoes", "informações",
    "prosseguir", "atendimento", "consultores", "assumirao", "assumirão", "agora",
    "ajudar", "tornar", "processo", "rapido", "rápido", "nossos", "abaixo", "escolha",
    "consultor", "deseja", "conversar", "outras", "opcoes", "opções", "preferencia",
    "preferência", "ha", "há", "existe", "algum", "alguma", "vim", "quero", "gente",
    "https", "http", "source", "campaignid", "gclid", "gbraid", "wbraid", "gad",
    "nevadaautopecas", "nevada", "ecopeças", "ecopecas", "instagram", "facebook",
    "loja", "site", "com", "br", "www", "tenho", "interesse", "ajuda", "tudo",
    "qual", "lado", "posso", "compra", "comprar", "valor", "gustavo", "flávia",
    "flavia", "matheus", "srsltid", "afmb", "redirect", "utm", "type", "session",
    "id", "app", "totalk", "chat", "www.instagram.com", "fb.me",
}


def _conectar():
    return sqlite3.connect(SQLITE_PATH)


def taxa_resposta_humana(conn):
    total = conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0]
    com_humano = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM mensagens "
        "WHERE direction='TO_HUB' AND user_id IS NOT NULL"
    ).fetchone()[0]
    return total, com_humano


def tempo_primeira_resposta_humana(conn):
    sessoes = conn.execute("SELECT id, created_at FROM sessoes").fetchall()
    tempos = []
    for session_id, created_at in sessoes:
        row = conn.execute(
            "SELECT created_at FROM mensagens WHERE session_id=? AND direction='TO_HUB' "
            "AND user_id IS NOT NULL ORDER BY created_at ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row and created_at:
            try:
                t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                minutos = (t1 - t0).total_seconds() / 60
                if minutos >= 0:
                    tempos.append(minutos)
            except ValueError:
                pass
    return tempos


def origem_dos_leads(conn):
    contagem = Counter()
    for (utm,) in conn.execute("SELECT utm FROM sessoes WHERE utm IS NOT NULL"):
        d = json.loads(utm)
        contagem[d.get("source") or "outro"] += 1
    sem_utm = conn.execute("SELECT COUNT(*) FROM sessoes WHERE utm IS NULL").fetchone()[0]
    contagem["sem rastreamento (organico/direto)"] = sem_utm
    return contagem


def pecas_mais_pedidas(conn, top_n=30):
    contagem = Counter()
    for (texto,) in conn.execute(
        "SELECT text FROM mensagens WHERE direction='FROM_HUB' AND text IS NOT NULL"
    ):
        palavras = re.findall(r"[a-záàâãéêíóôõúç0-9]+", texto.lower())
        for p in palavras:
            if len(p) >= 4 and p not in STOPWORDS:
                contagem[p] += 1
    return contagem.most_common(top_n)


def loops_de_chatbot(conn, limiar=3):
    suspeitos = conn.execute(
        "SELECT session_id, text, COUNT(*) as n FROM mensagens "
        "WHERE direction='TO_HUB' AND origin='BOT' AND text IS NOT NULL "
        "GROUP BY session_id, text HAVING n >= ? ORDER BY n DESC",
        (limiar,),
    ).fetchall()
    return suspeitos


if __name__ == "__main__":
    # console do Windows e cp1252: emoji nas mensagens derrubava o relatorio
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = _conectar()

    total, com_humano = taxa_resposta_humana(conn)
    print(f"=== Resposta humana ===")
    print(f"Total de sessoes (45 dias): {total}")
    print(f"Com pelo menos 1 resposta humana real: {com_humano} ({100*com_humano/total:.1f}%)")
    print(f"Sem nenhuma resposta humana (so bot/automatico): {total - com_humano} ({100*(total-com_humano)/total:.1f}%)")

    print()
    print("=== Tempo ate a primeira resposta humana (so sessoes respondidas) ===")
    tempos = tempo_primeira_resposta_humana(conn)
    if tempos:
        tempos.sort()
        mediana = tempos[len(tempos) // 2]
        media = sum(tempos) / len(tempos)
        print(f"Mediana: {mediana:.0f} min | Media: {media:.0f} min | Min: {tempos[0]:.0f} | Max: {tempos[-1]:.0f}")

    print()
    print("=== Origem dos leads ===")
    for origem, n in origem_dos_leads(conn).most_common():
        print(f"{origem}: {n}")

    print()
    print("=== Top 30 palavras nas mensagens dos clientes (proxy de pecas/veiculos pedidos) ===")
    for palavra, n in pecas_mais_pedidas(conn):
        print(f"{palavra}: {n}")

    print()
    print("=== Sessoes com chatbot repetindo a mesma mensagem 3+ vezes (loop suspeito) ===")
    loops = loops_de_chatbot(conn)
    print(f"Total de sessoes com loop suspeito: {len(set(s for s, t, n in loops))}")
    for session_id, texto, n in loops[:10]:
        print(f"{n}x | sessao {session_id[:8]} | {texto[:80]!r}")

    conn.close()
