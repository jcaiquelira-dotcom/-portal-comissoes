"""
Cruza o gasto do Google Ads (via Windsor) com as vendas do painel.

O casamento aqui e exato, diferente do Meta: o link que o cliente cola no
WhatsApp carrega "gad_campaignid=NNN", que e o mesmo campaign_id que o Google
Ads reporta. Nao precisa adivinhar por nome de veiculo.
"""

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDSOR = ROOT / "_windsor_periodo.json"
TICKET = 968

MARCAS = ["gclid=", "gad_source=", "gad_campaignid=", "gbraid=", "wbraid="]


def main():
    conn = sqlite3.connect(ROOT / "vendas.db")
    dados = json.loads((ROOT / "dataset.json").read_text(encoding="utf-8"))
    ids = json.loads((ROOT / "session_ids.json").read_text(encoding="utf-8"))
    por_id = dict(zip(ids, dados))

    # --- lado painel: qual campanha trouxe cada conversa ---
    por_campanha = defaultdict(lambda: {"leads": 0, "vendas": 0})
    sem_id = {"leads": 0, "vendas": 0}
    for sid, txt in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' AND text IS NOT NULL"
    ):
        t = txt.lower()
        if "nevadaautopecas.com.br" not in t or not any(m in t for m in MARCAS):
            continue
        if sid not in por_id:
            continue
        m = re.search(r"gad_campaignid=(\d+)", t)
        alvo = por_campanha[m.group(1)] if m else sem_id
        # uma sessao so conta uma vez, mesmo se colar o link duas vezes
        if alvo.get("_visto") is None:
            alvo["_visto"] = set()
        if sid in alvo["_visto"]:
            continue
        alvo["_visto"].add(sid)
        alvo["leads"] += 1
        alvo["vendas"] += 1 if por_id[sid]["cv"] == "P" else 0

    # --- lado Google: gasto por campanha ---
    linhas = json.loads(WINDSOR.read_text(encoding="utf-8"))["data"]
    google = defaultdict(lambda: {"gasto": 0.0, "cliques": 0, "nome": ""})
    for r in linhas:
        cid = str(r.get("campaign_id") or "")
        g = google[cid]
        g["gasto"] += float(r.get("spend") or 0)
        g["cliques"] += int(r.get("clicks") or 0)
        g["nome"] = r.get("campaign") or g["nome"]

    print("=" * 112)
    print("GOOGLE ADS — CUSTO POR VENDA E RETORNO  (07/07 a 21/08/2026)")
    print("=" * 112)
    print(f"{'Campanha':46s} {'Gasto':>10s} {'Cliques':>8s} {'Leads':>7s} "
          f"{'Vendas':>7s} {'Custo/venda':>12s} {'Retorno':>9s}")
    print("-" * 112)

    chaves = sorted(set(google) | set(por_campanha),
                    key=lambda k: -google.get(k, {}).get("gasto", 0))
    tg = tl = tv = 0
    for k in chaves:
        g = google.get(k, {"gasto": 0, "cliques": 0, "nome": f"(id {k})"})
        p = por_campanha.get(k, {"leads": 0, "vendas": 0})
        if not g["gasto"] and not p["leads"]:
            continue
        tg += g["gasto"]; tl += p["leads"]; tv += p["vendas"]
        receita = p["vendas"] * TICKET
        cpv = f"R$ {g['gasto']/p['vendas']:,.0f}" if p["vendas"] else "— sem venda"
        roi = f"{receita/g['gasto']:.1f}x" if g["gasto"] else "—"
        nome = (g["nome"] or f"(id {k})")[:46]
        print(f"{nome:46s} R$ {g['gasto']:>7,.0f} {g['cliques']:>8d} "
              f"{p['leads']:>7d} {p['vendas']:>7d} {cpv:>12s} {roi:>9s}")

    if sem_id["leads"]:
        print(f"{'(link sem id de campanha)':46s} {'—':>10s} {'—':>8s} "
              f"{sem_id['leads']:>7d} {sem_id['vendas']:>7d} {'—':>12s} {'—':>9s}")
        tl += sem_id["leads"]; tv += sem_id["vendas"]

    print("-" * 112)
    receita = tv * TICKET
    print(f"{'TOTAL':46s} R$ {tg:>7,.0f} {'':>8s} {tl:>7d} {tv:>7d} "
          f"{'R$ '+format(tg/tv, ',.0f') if tv else '—':>12s} "
          f"{receita/tg if tg else 0:>8.1f}x")

    print("\n" + "=" * 56)
    print("COMPARANDO OS DOIS CANAIS PAGOS")
    print("=" * 56)
    meta_gasto, meta_leads, meta_vendas = 5546.50, 2866, 49
    for nome, gasto, leads, vendas in [
        ("Google Ads", tg, tl, tv),
        ("Meta Ads", meta_gasto, meta_leads, meta_vendas),
    ]:
        print(f"\n  {nome}")
        print(f"    investido      : R$ {gasto:,.2f}")
        print(f"    leads          : {leads:,}")
        print(f"    vendas         : {vendas}")
        print(f"    conversão      : {100*vendas/leads:.2f}%")
        print(f"    custo por venda: R$ {gasto/vendas:,.2f}")
        print(f"    retorno        : {vendas*TICKET/gasto:.1f}x")


if __name__ == "__main__":
    main()
