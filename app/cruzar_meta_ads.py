"""
Cruza o gasto real do Meta Ads (CSV do Gerenciador) com as vendas identificadas
no painel de atendimento -- o numero que nenhum dos dois lados tem sozinho.

O Meta sabe quanto custou e quantas conversas comecaram, mas as colunas de
"Compras" e "ROAS" vem vazias: ele nao enxerga a venda, que acontece no
WhatsApp e no balcao. O painel sabe quais conversas viraram venda, mas nao
sabe quanto custou traze-las. Juntando os dois sai custo por venda e retorno.

Casamento anuncio<->criativo e feito por modelo de veiculo, porque o nome do
anuncio no Gerenciador ("ADV08 - Polo GTS") e diferente do texto do criativo
que chega no UTM ("Chegou mais uma sucata... Polo GTS 1.4 TSI").
"""

import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_META = Path(r"C:\Users\José Caique\Desktop\META-ADS-CSV-AGOSTO.csv")
TICKET = 968


def limpa(t):
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# chave de casamento -> (rotulo, termos que identificam o veiculo/tema)
GRUPOS = [
    ("polo_gts",    "Polo GTS",        ["polo gts"]),
    ("nivus",       "Nivus GTS",       ["nivus"]),
    ("up_tsi",      "Up TSI / T-Cross",["up tsi", "tcross", "t-cross"]),
    ("jetta_gli",   "Jetta GLI",       ["jetta gli", "jetta"]),
    ("audi_a3",     "Audi A3",         ["audi a3", "audi"]),
    ("tiguan_rline","Tiguan RLine",    ["tiguan rline", "rline"]),
    ("tiguan",      "Tiguan 250/350",  ["tiguan"]),
    ("golf_gti",    "Golf GTI",        ["golf gti", "golf"]),
    ("tera",        "VW Tera Highline",["tera"]),
    ("camaro",      "Camaro SS",       ["camaro"]),
    ("motor",       "Motor (genérico)",["motor", "gaiola"]),
    ("mercedes",    "Mercedes C200",   ["mercedes", "c200", "kompressor"]),
]


def classificar(texto):
    """Devolve a chave do grupo. Ordem importa: 'tiguan rline' antes de 'tiguan'.

    As hashtags saem antes da comparacao: o criativo do Tiguan 250/350 termina em
    "#tiguanrline #tiguan #ea888gen3", e o "rline" da hashtag jogava ele inteiro
    (304 leads, 11 vendas) para o grupo do Tiguan RLine, que so teve 1 lead --
    inflando aquele anuncio para um retorno impossivel de 732x.
    """
    t = re.sub(r"#\w+", " ", limpa(texto))
    for chave, _, termos in GRUPOS:
        if any(termo in t for termo in termos):
            return chave
    return None


def main():
    # --- lado Meta: gasto e conversas por anuncio ---
    meta = defaultdict(lambda: {"gasto": 0.0, "conversas": 0, "novos": 0, "anuncios": []})
    total_meta = None
    with open(CSV_META, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            nome = (r["Nome do anúncio"] or "").strip()
            gasto = float(r["Valor gasto (BRL)"] or 0)
            conversas = int(r["Resultados"] or 0)
            novos = int(r["Novos contatos de mensagem"] or 0)
            if not nome:  # linha sem nome = total da conta
                total_meta = {"gasto": gasto, "conversas": conversas, "novos": novos}
                continue
            chave = classificar(nome)
            if chave is None:
                print(f"  [aviso] anuncio sem grupo: {nome!r}")
                continue
            g = meta[chave]
            g["gasto"] += gasto
            g["conversas"] += conversas
            g["novos"] += novos
            g["anuncios"].append(nome)

    # --- lado painel: leads e vendas por criativo ---
    dados = json.loads((ROOT / "dataset.json").read_text(encoding="utf-8"))
    painel = defaultdict(lambda: {"leads": 0, "vendas": 0, "criativos": set()})
    sem_grupo = defaultdict(int)
    for d in dados:
        if d["c"] not in ("AF", "AI") or not d["ac"]:
            continue
        chave = classificar(d["ac"])
        if chave is None:
            sem_grupo[d["ac"][:45]] += 1
            continue
        p = painel[chave]
        p["leads"] += 1
        p["vendas"] += 1 if d["cv"] == "P" else 0
        p["criativos"].add(d["ac"][:40])

    if sem_grupo:
        print("  [aviso] criativos do painel sem grupo:", dict(sem_grupo))

    # --- resultado ---
    rotulos = {c: r for c, r, _ in GRUPOS}
    chaves = sorted(set(meta) | set(painel),
                    key=lambda k: -meta.get(k, {}).get("gasto", 0))

    print("\n" + "=" * 108)
    print("CUSTO POR VENDA E RETORNO, POR ANÚNCIO  (07/07 a 21/08/2026)")
    print("=" * 108)
    print(f"{'Anúncio':20s} {'Gasto':>10s} {'Conv.Meta':>10s} {'Leads':>7s} "
          f"{'Vendas':>7s} {'Receita':>11s} {'Custo/venda':>12s} {'Retorno':>9s}")
    print("-" * 108)

    tot_gasto = tot_leads = tot_vendas = 0
    for k in chaves:
        m = meta.get(k, {"gasto": 0, "conversas": 0})
        p = painel.get(k, {"leads": 0, "vendas": 0})
        receita = p["vendas"] * TICKET
        tot_gasto += m["gasto"]
        tot_leads += p["leads"]
        tot_vendas += p["vendas"]
        cpv = f"R$ {m['gasto']/p['vendas']:,.0f}" if p["vendas"] else "— sem venda"
        roi = f"{receita/m['gasto']:.1f}x" if m["gasto"] else "—"
        print(f"{rotulos.get(k,k)[:20]:20s} R$ {m['gasto']:>7,.0f} {m['conversas']:>10d} "
              f"{p['leads']:>7d} {p['vendas']:>7d} R$ {receita:>8,.0f} {cpv:>12s} {roi:>9s}")

    print("-" * 108)
    receita_tot = tot_vendas * TICKET
    print(f"{'TOTAL':20s} R$ {tot_gasto:>7,.0f} {total_meta['conversas']:>10d} "
          f"{tot_leads:>7d} {tot_vendas:>7d} R$ {receita_tot:>8,.0f} "
          f"{'R$ '+format(tot_gasto/tot_vendas, ',.0f'):>12s} {receita_tot/tot_gasto:>8.1f}x")

    print("\n" + "=" * 60)
    print("CONFERINDO OS DOIS LADOS")
    print("=" * 60)
    print(f"  gasto total no Gerenciador : R$ {total_meta['gasto']:,.2f}")
    print(f"  soma dos anúncios          : R$ {tot_gasto:,.2f}")
    print(f"  conversas (Meta)           : {total_meta['conversas']:,}")
    print(f"  novos contatos (Meta)      : {total_meta['novos']:,}")
    print(f"  leads no painel (com UTM)  : {tot_leads:,}")
    falta = total_meta["conversas"] - tot_leads
    print(f"  diferença                  : {falta:,} ({100*falta/total_meta['conversas']:.1f}%)")
    print(f"\n  custo por conversa (Meta)  : R$ {total_meta['gasto']/total_meta['conversas']:.2f}")
    print(f"  custo por venda real       : R$ {total_meta['gasto']/tot_vendas:,.2f}")
    print(f"  ticket médio               : R$ {TICKET:,}")
    print(f"  retorno sobre o investido  : {receita_tot/total_meta['gasto']:.1f}x")


if __name__ == "__main__":
    main()
