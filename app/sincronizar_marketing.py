"""
Empurra os números de marketing pro portal-comissoes.

Duas chaves, ambas no grão diário — o portal filtra e soma, então dá pra
recortar por período, canal e vendedor sem precisar regerar nada aqui:

  marketing_leads  [{data, vendedor, canal, leads, sinal}]
      de onde veio cada conversa e quem atendeu. `sinal` é a conversa que deu
      indício de compra no chat; não é venda — o fechamento acontece fora, e o
      número de vendas o portal já tem.

  marketing_gasto  {linhas: [...], meta: {...}}
      `linhas` é o Google Ads no grão diário, via Windsor — dá pra fatiar por
      qualquer período. `meta` é o export do Gerenciador do Meta, que vem
      agregado do período inteiro e não por dia: por isso viaja separado, com o
      próprio período junto, e o portal só soma ele ao investimento quando a
      janela pedida cobre esse período inteiro. Misturar os dois como se fossem
      a mesma coisa daria um custo por lead errado em qualquer recorte menor.

Uso:
    set DATABASE_URL=postgresql://...
    python app/sincronizar_marketing.py
    python app/sincronizar_marketing.py --seco     # só confere
"""

import io
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANCO = ROOT / "vendas.db"
# _w_amplo cobre o ano todo; _windsor_periodo é mais curto mas traz clique e
# impressão. Um completa o outro: o amplo dá a série longa, o outro dá o resto.
GASTO_AMPLO = ROOT / "_w_amplo.json"
GASTO_DETALHE = ROOT / "_windsor_periodo.json"
# Export do Gerenciador do Meta. Vem agregado do periodo inteiro, nao por dia —
# por isso entra separado do gasto diario do Google, com o periodo dele junto.
CSV_META = ROOT.parent / "META-ADS-CSV-AGOSTO.csv"
# Meta Ads dia a dia pelo Windsor; substitui o CSV desde 28/08/2026.
META_JSON = ROOT / "_meta_ads.json"

ATENDENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": "flavia",
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": "matheus",
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": "gustavo",
}


def coletar_leads():
    con = sqlite3.connect(BANCO)
    con.row_factory = sqlite3.Row
    linhas = con.execute("""
        SELECT s.user_id, s.created_at, c.canal, v.classe
          FROM sessoes s
          LEFT JOIN canal c     ON c.session_id = s.id
          LEFT JOIN conversao v ON v.session_id = s.id
    """).fetchall()
    con.close()

    # `sinal` e `provavel` medem coisas diferentes e por isso viajam separados.
    # sinal   = chegou a falar de pagamento OU de entrega — mede interesse que
    #           andou, e serve pra comparar canal com canal.
    # provavel = falou dos DOIS — e o mais perto de "vendeu" que da pra saber
    #           pelo chat, e so ele pode virar dinheiro estimado. Somar o
    #           parcial na receita inflaria em quase o dobro.
    agrupado = defaultdict(lambda: {"leads": 0, "sinal": 0, "provavel": 0})
    for r in linhas:
        if not r["created_at"]:
            continue
        chave = (r["created_at"][:10],
                 ATENDENTES.get(r["user_id"], ""),      # "" = sem atendente
                 r["canal"] or "Sem origem")
        agrupado[chave]["leads"] += 1
        if r["classe"] in ("provavel", "parcial"):
            agrupado[chave]["sinal"] += 1
        if r["classe"] == "provavel":
            agrupado[chave]["provavel"] += 1

    return [{"data": d, "vendedor": v, "canal": c, **n}
            for (d, v, c), n in sorted(agrupado.items())]


def coletar_gasto():
    if not GASTO_AMPLO.exists():
        return []
    amplo = json.loads(GASTO_AMPLO.read_text(encoding="utf-8"))["data"]
    por_chave = {}
    for r in amplo:
        chave = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
        d = por_chave.setdefault(chave, {"spend": 0.0, "clicks": 0, "impressions": 0})
        d["spend"] += float(r.get("spend") or 0)

    # clique e impressão só existem no arquivo do período curto
    if GASTO_DETALHE.exists():
        for r in json.loads(GASTO_DETALHE.read_text(encoding="utf-8"))["data"]:
            chave = (r["date"], r.get("datasource", "google_ads"), r.get("campaign", "—"))
            d = por_chave.setdefault(chave, {"spend": 0.0, "clicks": 0, "impressions": 0})
            d["clicks"] += int(r.get("clicks") or 0)
            d["impressions"] += int(r.get("impressions") or 0)

    return [{"data": d, "fonte": f, "campanha": c,
             "spend": round(v["spend"], 2),
             "clicks": v["clicks"], "impressions": v["impressions"]}
            for (d, f, c), v in sorted(por_chave.items())]


def coletar_meta():
    """Meta Ads dia a dia, vindo do Windsor (app/atualizar_meta_ads.py).

    Ate 28/08/2026 isso era um CSV exportado a mao do Gerenciador, com periodo
    fixo — o painel travava na data do relatorio e rateava o investimento por
    dia. Agora vem por dia e o rateio some. O CSV continua como reserva: se o
    _meta_ads.json nao existir, cai nele.
    """
    if not META_JSON.exists():
        return _coletar_meta_csv()

    linhas = json.loads(META_JSON.read_text(encoding="utf-8"))["data"]
    if not linhas:
        return _coletar_meta_csv()

    datas = sorted({x["data"] for x in linhas if x.get("data")})

    por_anuncio = {}
    for x in linhas:
        a = por_anuncio.setdefault(x["anuncio"], {
            "anuncio": x["anuncio"], "spend": 0.0, "impressions": 0,
            "alcance": 0, "conversas": 0})
        a["spend"] += x["spend"]
        a["impressions"] += x["impressions"]
        a["conversas"] += x["conversas"]
        # Alcance por anuncio ainda e soma de dias — a mesma pessoa pode ter
        # visto em dias diferentes. Serve pra ordenar, nao como total.
        a["alcance"] += x["alcance"]
    detalhe = sorted(por_anuncio.values(), key=lambda a: -a["spend"])
    for a in detalhe:
        a["spend"] = round(a["spend"], 2)

    # A serie por dia e o que deixa o painel somar qualquer periodo filtrado
    # sem aproximar nada. A quebra por plataforma vive DENTRO do dia, senao um
    # filtro de periodo teria que confiar num total ja fechado.
    serie = {}
    for x in linhas:
        d = serie.setdefault(x["data"], {"spend": 0.0, "clicks": 0,
                                         "impressions": 0, "conversas": 0,
                                         "plataforma": {}})
        d["spend"] = round(d["spend"] + x["spend"], 2)
        d["clicks"] += x["clicks"]
        d["impressions"] += x["impressions"]
        d["conversas"] += x["conversas"]
        # Alcance NAO entra aqui: e a unica metrica que nao pode ser somada
        # entre plataformas, porque a mesma pessoa aparece nas duas.
        p = d["plataforma"].setdefault(x.get("plataforma") or "—",
                                       {"spend": 0.0, "clicks": 0,
                                        "impressions": 0, "conversas": 0})
        p["spend"] = round(p["spend"] + x["spend"], 2)
        p["clicks"] += x["clicks"]
        p["impressions"] += x["impressions"]
        p["conversas"] += x["conversas"]

    total_spend = round(sum(x["spend"] for x in linhas), 2)
    total_conversas = sum(x["conversas"] for x in linhas)
    return {
        "de": datas[0], "ate": datas[-1],
        "spend": total_spend,
        "impressions": sum(x["impressions"] for x in linhas),
        "conversas": total_conversas,
        # Sem a linha de TOTAL do relatorio nao da pra deduplicar alcance: a
        # soma conta de novo quem viu em mais de um dia. Marcado como tal.
        "alcance": sum(x["alcance"] for x in linhas),
        "alcance_deduplicado": False,
        "custo_por_conversa": (round(total_spend / total_conversas, 2)
                               if total_conversas else None),
        "anuncios": detalhe,
        "serie_dia": serie,
        "fonte": "windsor",
    }


def _coletar_meta_csv():
    """Reserva: o CSV exportado do Gerenciador, formato antigo."""
    if not CSV_META.exists():
        return None
    import csv
    with io.open(CSV_META, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    if not linhas:
        return None

    def num(v):
        try:
            return float(str(v).replace(",", ".")) if str(v).strip() else 0.0
        except ValueError:
            return 0.0

    anuncios = [x for x in linhas if (x.get("Nome do anúncio") or "").strip()]
    if not anuncios:
        return None

    periodos = {(x.get("Início dos relatórios"), x.get("Encerramento dos relatórios"))
                for x in anuncios}
    de, ate = sorted(periodos)[0]

    detalhe = sorted(({
        "anuncio": x["Nome do anúncio"].strip(),
        "spend": round(num(x.get("Valor gasto (BRL)")), 2),
        "impressions": int(num(x.get("Impressões"))),
        "alcance": int(num(x.get("Alcance"))),
        "conversas": int(num(x.get("Conversas por mensagem iniciadas"))),
    } for x in anuncios), key=lambda a: -a["spend"])

    total = {c: sum(a[c] for a in detalhe)
             for c in ("spend", "impressions", "conversas")}
    total["spend"] = round(total["spend"], 2)

    # Alcance nao se soma: quem viu dois anuncios e uma pessoa so. Somando os 14
    # anuncios dava 364.204 quando o real eram 215.585 — 69% a mais. Quem sabe o
    # numero certo e a linha de TOTAL do relatorio, a unica que deduplica.
    linha_total = next((x for x in linhas if not (x.get("Nome do anúncio") or "").strip()), None)
    alcance_real = int(num(linha_total.get("Alcance"))) if linha_total else 0
    total["alcance"] = alcance_real or sum(a["alcance"] for a in detalhe)
    total["alcance_deduplicado"] = bool(alcance_real)
    return {"de": de, "ate": ate, **total,
            "custo_por_conversa": (round(total["spend"] / total["conversas"], 2)
                                   if total["conversas"] else None),
            "anuncios": detalhe}


def resumir(leads, gasto):
    dias = {l["data"] for l in leads}
    print(f"leads : {len(leads)} linhas | {sum(l['leads'] for l in leads)} conversas | "
          f"{min(dias)} a {max(dias)}")
    canais = defaultdict(int)
    for l in leads:
        canais[l["canal"]] += l["leads"]
    for c, n in sorted(canais.items(), key=lambda kv: -kv[1]):
        print(f"        {c:22} {n:>6}")
    if gasto:
        dg = {g["data"] for g in gasto}
        print(f"gasto : {len(gasto)} linhas | R$ {sum(g['spend'] for g in gasto):,.2f} | "
              f"{min(dg)} a {max(dg)} | cliques {sum(g['clicks'] for g in gasto)}")
    else:
        print("gasto : nenhum arquivo de investimento encontrado")


def gravar(leads, gasto, meta, url):
    import psycopg2
    from psycopg2.extras import Json

    gerado_em = datetime.now().astimezone().isoformat(timespec="seconds")
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS dados_json ("
                    "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)")
        for chave, corpo in (("marketing_leads", {"gerado_em": gerado_em, "linhas": leads}),
                             ("marketing_gasto", {"gerado_em": gerado_em, "linhas": gasto,
                                                  "meta": meta,
                                                  "fontes_ausentes": [] if meta else ["Meta Ads"]})):
            cur.execute(
                "INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                (chave, Json(corpo)))
            print(f"  gravado {chave}")
    conn.close()


def main():
    if not BANCO.exists():
        raise SystemExit(f"{BANCO} não existe.")
    leads, gasto, meta = coletar_leads(), coletar_gasto(), coletar_meta()
    resumir(leads, gasto)
    if meta:
        print(f"meta  : R$ {meta['spend']:,.2f} de {meta['de']} a {meta['ate']} | "
              f"{meta['conversas']} conversas | R$ {meta['custo_por_conversa']}/conversa | "
              f"{len(meta['anuncios'])} anuncios")
    else:
        print(f"meta  : CSV nao encontrado em {CSV_META}")
    if "--seco" in sys.argv:
        print("\n(--seco: nada foi gravado)")
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("\nDATABASE_URL não definida.")
    print()
    gravar(leads, gasto, meta, url)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
