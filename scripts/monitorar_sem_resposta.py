"""
Alerta de atendimento sem resposta — quem esta esperando a loja falar.

Por que existe: em 28/08/2026 o time notou que a bolinha de "nao lida" do
Totalk zera sozinha, porque o bot atende primeiro e ja marca a conversa como
vista (499 de 500 conversas do dia tinham bot). Com isso a bolinha parou de
servir como controle, e o medo do gestor e justamente passar atendimento sem
resposta. Este monitor nao depende dela: olha quem falou por ultimo.

A conta e simples e nao precisa baixar mensagem nenhuma — a propria listagem
de sessoes traz lastMessageIn (ultima do cliente) e lastMessageOut (ultima da
loja). Se a do cliente e mais recente, ninguem respondeu ainda, e o tempo de
espera e "agora menos lastMessageIn".

Grava a chave `atendimento_alerta` no mesmo banco do portal. Roda de poucos em
poucos minutos — e leitura pura, nao escreve nada no Totalk.

Uso:
    set DATABASE_URL=postgresql://...
    python scripts/monitorar_sem_resposta.py
    python scripts/monitorar_sem_resposta.py --seco
"""

import io
import json
import os
import re
import sys
import urllib.parse
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.wts.chat"
FUSO = timezone(timedelta(hours=-3))
RAIZ_INSIGHTS = Path(r"C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights")

# Faixas de urgencia, em minutos. O verde some da tela: alerta que mostra tudo
# nao e alerta. So aparece quem passou de ATENCAO.
ATENCAO, URGENTE, CRITICO = 15, 30, 60
# Teto de 24h: conversa aberta ha dias nao e "atendimento sem resposta agora",
# e sessao que ninguem encerrou — outro problema, contado a parte como
# `paradas_antigas`. Sem esse corte o alerta virava 355 linhas de 10 dias
# atras e ninguem olharia.
TETO_MINUTOS = 24 * 60

# Cliente que responde "ok" ou "obrigado" nao esta esperando nada — encerrou a
# conversa. Sem este filtro o alerta nascia com 45 "criticos" que eram so
# agradecimento, e alerta cheio de ruido ninguem olha. So vale pra mensagem
# CURTA: "obrigado, mas queria saber se tem pra Gol" precisa de resposta.
ENCERRAMENTOS = {
    "ok", "okay", "okey", "blz", "beleza", "valeu", "vlw", "obrigado",
    "obrigada", "obg", "brigado", "brigada", "agradecido", "agradecida",
    "isso", "certo", "perfeito", "show", "otimo", "ótimo", "legal", "top",
    "entendi", "ta bom", "tá bom", "tabom", "ta certo", "tudo bem", "de nada",
    "bom dia", "boa tarde", "boa noite", "att", "amem", "amém",
}
# Emoji sozinho (joinha, mãozinha, coração) tambem e encerramento.
SO_EMOJI = re.compile(r"^[\W_]+$", re.UNICODE)


def so_agradeceu(texto: str) -> bool:
    """True quando a ultima mensagem do cliente nao pede resposta."""
    t = (texto or "").strip().lower()
    if not t:
        return False
    if len(t) > 40:            # mensagem longa sempre merece olhar
        return False
    if SO_EMOJI.fullmatch(t):  # 👍 🙏 ❤️ sozinhos
        return True
    # tira pontuacao e emoji das pontas antes de comparar
    limpo = re.sub(r"^[\W_]+|[\W_]+$", "", t)
    if limpo in ENCERRAMENTOS:
        return True
    # "muito obrigado", "ok obrigado", "obrigado!!" — todas as palavras sao de
    # encerramento ou reforco ("muito", "mesmo", "entao").
    reforco = {"muito", "mesmo", "entao", "então", "ai", "aí", "ja", "já", "e", "eh"}
    palavras = [p for p in re.split(r"[\W_]+", limpo) if p]
    return bool(palavras) and all(p in ENCERRAMENTOS or p in reforco for p in palavras)

# userId do Totalk -> (id no portal, nome). Mesmo mapa do vendas-insights; o
# id e o que deixa cada vendedor ver so a fila dele no proprio portal.
ATENDENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": ("flavia", "Flávia"),
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": ("matheus", "Matheus"),
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": ("gustavo", "Gustavo"),
}


def token() -> str:
    """Le o TOTALK_TOKEN do .env do vendas-insights, que ja e a fonte dele."""
    env = RAIZ_INSIGHTS / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            if linha.strip().startswith("TOTALK_TOKEN"):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    valor = os.environ.get("TOTALK_TOKEN") or os.environ.get("TOTALK_API_TOKEN")
    if not valor:
        raise SystemExit("TOTALK_TOKEN não encontrado (.env do vendas-insights).")
    return valor


TOK = token()


def get(path: str, params: dict = None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOK}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def quando(txt: str):
    """"2026-08-28T14:48:23.38Z" -> datetime com fuso. A API varia o numero de
    casas decimais, entao corta em 26 caracteres antes de converter."""
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt[:26].rstrip("Z") + "+00:00")
    except ValueError:
        return None


def coletar():
    agora = datetime.now(timezone.utc)
    esperando, antigas = [], []
    agradecimentos = 0

    # PENDING = ainda na fila; IN_PROGRESS = com atendente. As duas contam:
    # cliente esperando na fila e cliente esperando resposta pesam igual.
    for status in ("PENDING", "IN_PROGRESS"):
        pagina = 1
        while pagina <= 20:
            d = get("/chat/v2/session", {
                "Status": status, "PageNumber": pagina, "PageSize": 100,
                "OrderBy": "createdat", "OrderDirection": "DESCENDING",
            })
            itens = d.get("items") or []
            for s in itens:
                entrada, saida = quando(s.get("lastMessageIn")), quando(s.get("lastMessageOut"))
                if not entrada:
                    continue
                # A loja ja respondeu depois da ultima do cliente: nada a fazer.
                if saida and saida >= entrada:
                    continue
                minutos = int((agora - entrada).total_seconds() // 60)
                if minutos < ATENCAO:
                    continue
                if minutos > TETO_MINUTOS:
                    antigas.append(minutos)
                    continue
                if so_agradeceu(s.get("lastMessageText")):
                    agradecimentos += 1
                    continue
                esperando.append({
                    "id": s["id"],
                    "contato_id": s.get("contactId"),
                    # Link direto pra conversa no Totalk: o vendedor clica no
                    # cartao e cai na conversa, sem procurar na lista.
                    "url": s.get("previewUrl"),
                    # Carimbo da ultima fala do cliente. E o que permite o
                    # "resolvido" saber se a conversa andou depois de marcada.
                    "ultima_em": entrada.isoformat(timespec="seconds"),
                    "minutos": minutos,
                    "desde": entrada.astimezone(FUSO).isoformat(timespec="minutes"),
                    "status": status,
                    "vendedor_id": (ATENDENTES.get(s.get("userId")) or ("", ""))[0],
                    "atendente": (ATENDENTES.get(s.get("userId")) or
                                  (None, "—" if s.get("userId") else "sem atendente"))[1],
                    "ultima_msg": (s.get("lastMessageText") or "")[:120],
                    "nunca_respondida": saida is None,
                    "nivel": ("critico" if minutos >= CRITICO
                              else "urgente" if minutos >= URGENTE else "atencao"),
                })
            if not d.get("hasMorePages"):
                break
            pagina += 1

    # Mais tempo esperando primeiro, e so entao busca o nome — assim o limite
    # de chamadas cai sobre quem realmente vai aparecer na tela.
    esperando.sort(key=lambda e: -e["minutos"])
    for e in esperando[:40]:
        try:
            e["cliente"] = get(f"/core/v1/contact/{e['contato_id']}").get("name") or "?"
        except Exception:
            e["cliente"] = "?"
        time.sleep(0.25)   # a API corta rajada; 4 por segundo passa tranquilo
    return esperando, antigas, agradecimentos


def main():
    esperando, antigas, agradecimentos = coletar()
    agora = datetime.now(FUSO)
    contagem = {n: sum(1 for e in esperando if e["nivel"] == n)
                for n in ("critico", "urgente", "atencao")}
    print(f"{len(esperando)} conversas esperando resposta há {ATENCAO}min ou mais")
    if agradecimentos:
        print(f"  ({agradecimentos} ignoradas: última do cliente era só "
              f'"ok"/"obrigado" — não pedem resposta)')
    if antigas:
        print(f"  (+{len(antigas)} sessões abertas há mais de 24h — provavelmente "
              f"esquecidas em aberto, contadas à parte)")
    print(f"  crítico (>{CRITICO}min): {contagem['critico']} | "
          f"urgente (>{URGENTE}min): {contagem['urgente']} | "
          f"atenção (>{ATENCAO}min): {contagem['atencao']}")
    for e in esperando[:10]:
        print(f"  {e['minutos']:>4}min | {e['nivel']:<8} | {e['atendente']:<14} | "
              f"{e.get('cliente','?')[:24]}")

    pacote = {
        "gerado_em": agora.isoformat(timespec="seconds"),
        "limites": {"atencao": ATENCAO, "urgente": URGENTE, "critico": CRITICO},
        "contagem": contagem,
        "total": len(esperando),
        "paradas_antigas": len(antigas),
        "so_agradeceram": agradecimentos,
        "conversas": esperando[:40],
        # Contagem por vendedor: o gestor ve a distribuicao sem abrir a lista,
        # e o portal de cada um usa como cabecalho.
        "por_vendedor": {
            vid: {
                "nome": nome,
                "total": sum(1 for e in esperando if e["vendedor_id"] == vid),
                "critico": sum(1 for e in esperando
                               if e["vendedor_id"] == vid and e["nivel"] == "critico"),
            }
            for vid, nome in {v[0]: v[1] for v in ATENDENTES.values()}.items()
        },
    }
    if "--seco" in sys.argv:
        print("\n(--seco: nada gravado)")
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL não definida.")
    import psycopg2
    from psycopg2.extras import Json
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    ("atendimento_alerta", Json(pacote)))
    conn.close()
    print("\n  gravado atendimento_alerta")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
