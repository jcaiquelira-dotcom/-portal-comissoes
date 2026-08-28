"""
Empurra as métricas de atendimento pro portal-comissoes, pra alimentar o
painel "Desempenho" da área do gestor.

O portal só conhece o que o vendedor lançou (data, produto, valor). Tudo que
acontece ANTES da venda — quantos clientes ele atendeu, de que canal vieram,
quanto demorou pra responder o primeiro — só existe aqui, no espelho do
Totalk. Este script fecha essa lacuna: agrega por vendedor e por mês e grava
uma chave `insights_<vendedor>` no mesmo banco que o portal já lê.

Manda agregado, nunca conversa: o portal não precisa (nem deve) receber texto
de cliente. O que vai é contagem.

Uso:
    # produção — mesma connection string do portal-comissoes
    set DATABASE_URL=postgresql://...
    python app/sincronizar_desempenho.py

    # só conferir, sem gravar nada
    python app/sincronizar_desempenho.py --seco
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANCO = ROOT / "vendas.db"

# user_id do Totalk -> id do vendedor no portal. Mesma tabela do
# export_dataset.py; Brenda não atende pelo Totalk, então não aparece aqui.
ATENDENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": "flavia",
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": "matheus",
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": "gustavo",
}


def _minutos(inicio: str, fim: str):
    """Minutos entre dois carimbos ISO do Totalk. Devolve None quando falta um
    dos lados — atendimento sem primeira resposta não entra na média, senão
    puxaria o número pra baixo como se tivesse sido respondido na hora."""
    if not inicio or not fim:
        return None
    try:
        a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    except ValueError:
        return None
    minutos = (b - a).total_seconds() / 60
    return minutos if minutos >= 0 else None


def coletar():
    con = sqlite3.connect(BANCO)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    linhas = cur.execute("""
        SELECT s.user_id, s.created_at, s.first_response_at,
               c.canal, v.classe,
               ia.virou_venda, ia.tipo_cliente, ia.tinhamos_a_peca
          FROM sessoes s
          LEFT JOIN canal c            ON c.session_id = s.id
          LEFT JOIN conversao v        ON v.session_id = s.id
          LEFT JOIN classificacao_ia ia ON ia.session_id = s.id
         WHERE s.user_id IS NOT NULL
    """).fetchall()

    por_vendedor = {}
    for r in linhas:
        vid = ATENDENTES.get(r["user_id"])
        if not vid or not r["created_at"]:
            continue
        mes = r["created_at"][:7]
        d = por_vendedor.setdefault(vid, {}).setdefault(mes, {
            "atendimentos": 0, "classificados": 0, "com_venda": 0,
            "sinal_venda": 0, "oficina": 0, "tinha_peca": 0,
            "canais": {}, "respostas": [],
        })
        d["atendimentos"] += 1
        if r["canal"]:
            d["canais"][r["canal"]] = d["canais"].get(r["canal"], 0) + 1
        # `provavel`/`parcial` = a conversa deu sinal de compra. Nao e venda
        # confirmada: o fechamento acontece fora do chat, entao o numero real
        # de vendas vem do portal, nao daqui.
        if r["classe"] in ("provavel", "parcial"):
            d["sinal_venda"] += 1
        if r["virou_venda"] is not None:
            d["classificados"] += 1
            if r["virou_venda"]:
                d["com_venda"] += 1
            if (r["tipo_cliente"] or "").strip().lower().startswith("ofic"):
                d["oficina"] += 1
            if (r["tinhamos_a_peca"] or "").strip().lower().startswith("s"):
                d["tinha_peca"] += 1
        m = _minutos(r["created_at"], r["first_response_at"])
        if m is not None:
            d["respostas"].append(m)

    con.close()

    # fecha as contas e tira a lista de tempos (nao interessa ao portal)
    saida = {}
    for vid, meses in por_vendedor.items():
        bloco = {}
        for mes, d in sorted(meses.items()):
            tempos = sorted(d.pop("respostas"))
            bloco[mes] = {
                **d,
                "canais": dict(sorted(d["canais"].items(),
                                      key=lambda kv: kv[1], reverse=True)),
                # Mediana, nao media: um atendimento respondido no dia seguinte
                # sozinho joga a media pra dezenas de horas e esconde o normal.
                "resposta_mediana_min": (round(tempos[len(tempos) // 2], 1)
                                         if tempos else None),
                "com_resposta": len(tempos),
                "pct_sinal": (round(100 * d["sinal_venda"] / d["atendimentos"], 1)
                              if d["atendimentos"] else 0),
            }
        saida[vid] = bloco
    return saida


def resumir(dados):
    for vid, meses in sorted(dados.items()):
        print(f"\n=== {vid} ===")
        for mes, d in meses.items():
            print(f"  {mes}: {d['atendimentos']:>5} atendimentos | "
                  f"sinal de venda {d['sinal_venda']:>4} ({d['pct_sinal']}%) | "
                  f"1a resposta {d['resposta_mediana_min']} min | "
                  f"canais {list(d['canais'])[:3]}")


def gravar(dados, url):
    import psycopg2
    from psycopg2.extras import Json

    gerado_em = datetime.now().astimezone().isoformat(timespec="seconds")
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS dados_json ("
                    "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)")
        for vid, meses in dados.items():
            cur.execute(
                "INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                (f"insights_{vid}", Json({"gerado_em": gerado_em, "meses": meses})))
            print(f"  gravado insights_{vid} ({len(meses)} meses)")
    conn.close()


def main():
    if not BANCO.exists():
        raise SystemExit(f"{BANCO} não existe — rode antes o sync do Totalk.")
    dados = coletar()
    resumir(dados)

    if "--seco" in sys.argv:
        print("\n(--seco: nada foi gravado)")
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("\nDATABASE_URL não definida. Use --seco pra só conferir.")
    print()
    gravar(dados, url)


if __name__ == "__main__":
    main()
