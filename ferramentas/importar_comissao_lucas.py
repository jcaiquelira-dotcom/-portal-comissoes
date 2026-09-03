# -*- coding: utf-8 -*-
"""Traz as vendas do Lucas da planilha do Drive pro portal.

O Lucas assumiu o lugar do Gustavo em 31/08/2026 e ainda nao lancava no portal
— as vendas dele so existiam na planilha "COMISSAO LUCAS". Este script cobre
essa lacuna UMA vez; daqui pra frente ele lanca no portal como os outros.

Decisoes que valem a leitura:

CSV, nao a API do Sheets. A resposta JSON do Google (gviz) DESCARTOU em
silencio uma celula de R$ 149,00 que o CSV traz — ela nao casava com o tipo que
o Google inferiu pra coluna. Numa planilha de comissao, uma fonte que perde
linha sem avisar nao serve. O CSV bateu com a soma conferida na mao: R$
13.215,00 em 16 vendas.

Data herdada da linha de cima. A planilha so escreve a data na primeira venda
do dia; as seguintes ficam em branco. Ler cada linha isolada jogaria 12 das 16
vendas pra data errada.

Ano deduzido, porque a planilha nao tem. Mes maior que o mes de hoje = ano
passado; e a unica leitura que sobrevive a virada de ano.

Grava pela montar_venda() do proprio portal, e nao escrevendo o JSON na mao:
assim canal, arredondamento e validacao saem identicos aos de uma venda
digitada, e nao existe um segundo jeito de uma venda nascer.

Uso:
    set DATABASE_URL=postgresql://...
    python scripts/importar_comissao_lucas.py --seco     # so mostra
    python scripts/importar_comissao_lucas.py --gravar
    python scripts/importar_comissao_lucas.py --gravar --desde 2026-08-31
"""

import csv
import io
import re
import sys
import urllib.request
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

PLANILHA = "1Y7fP8eVnpmzUpWVlvYUu3vwuh-KPfJn3y2RzvTs6WvY"
VENDEDOR = "lucas"


def baixar_csv() -> str:
    url = f"https://docs.google.com/spreadsheets/d/{PLANILHA}/export?format=csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if "text/csv" not in r.headers.get("Content-Type", ""):
            sys.exit("O Google nao devolveu CSV — a planilha deixou de ser "
                     "publica por link? Confira o compartilhamento.")
        return r.read().decode("utf-8")


def dinheiro(t: str):
    t = re.sub(r"[^\d,.]", "", t or "")
    if not t:
        return None
    return round(float(t.replace(".", "").replace(",", ".")), 2)


def ler_planilha(hoje: date) -> tuple:
    linhas, ignoradas, data_atual = [], [], None
    for r in csv.reader(io.StringIO(baixar_csv())):
        if len(r) < 4 or r[0].strip().upper() == "DATA":
            continue
        if re.fullmatch(r"\d{2}/\d{2}", r[0].strip()):
            dia, mes = int(r[0][:2]), int(r[0][3:5])
            ano = hoje.year if mes <= hoje.month else hoje.year - 1
            data_atual = f"{ano:04d}-{mes:02d}-{dia:02d}"
        produto = r[1].strip()
        if not produto:
            continue
        valor = dinheiro(r[2])
        if valor is None or valor <= 0:
            # Linha sem valor nao vira venda de R$ 0 — vira aviso. Comissao
            # zerada passa despercebida; a linha faltando, nao.
            ignoradas.append((data_atual, produto, r[2]))
            continue
        if not data_atual:
            ignoradas.append((None, produto, "sem data acima"))
            continue
        linhas.append({"data": data_atual, "produto": produto,
                       "valor": valor, "canal": r[3].strip()})
    return linhas, ignoradas


def main() -> int:
    gravar = "--gravar" in sys.argv
    desde = ""
    if "--desde" in sys.argv:
        desde = sys.argv[sys.argv.index("--desde") + 1]

    import server
    from areas.contas import montar_venda  # desde a Fase 4 mora na area de contas

    linhas, ignoradas = ler_planilha(server.hoje_br())
    if desde:
        linhas = [l for l in linhas if l["data"] >= desde]

    existentes = server.carregar_vendas_vendedor(VENDEDOR)
    ja = {(v.get("data"), round(float(v.get("valor") or 0), 2),
           re.sub(r"\W+", "", (v.get("produto") or "").lower()))
          for v in existentes.values() if v.get("tipo", "venda") == "venda"}

    novas, repetidas = [], []
    for l in linhas:
        chave = (l["data"], l["valor"],
                 re.sub(r"\W+", "", l["produto"].lower()))
        (repetidas if chave in ja else novas).append(l)

    print(f"planilha: {len(linhas)} venda(s) no recorte")
    print(f"ja no portal: {len(repetidas)}  |  a importar: {len(novas)}")
    if ignoradas:
        print("\nIGNORADAS (confira na planilha):")
        for d, p, v in ignoradas:
            print(f"   {d or 'sem data'}  {p[:40]:40} valor={v!r}")
    total = 0.0
    print()
    for l in novas:
        venda = montar_venda(VENDEDOR, {
            "data": l["data"], "produto": l["produto"],
            "valor": l["valor"], "canal": l["canal"]},
            ignorar_limite_retroativo=True)
        total += venda["valor"]
        print(f"   {venda['data']}  {venda['valor']:>9,.2f}  "
              f"{venda.get('canal', ''):14}  {venda['produto'][:40]}")
        if gravar:
            existentes[uuid.uuid4().hex[:12]] = venda
    print(f"\n   TOTAL {total:,.2f}")

    if not gravar:
        print("\n(--seco: nada foi gravado. Rode com --gravar pra valer.)")
        return 0
    server.salvar_vendas_vendedor(VENDEDOR, existentes)
    # A confirmacao de fechamento do mes precisa cair: ela dizia respeito a um
    # mes que agora tem venda a mais.
    for mes in {l["data"][:7] for l in novas}:
        server.limpar_confirmacao(VENDEDOR, mes)
    print(f"\ngravadas {len(novas)} venda(s) em vendas_{VENDEDOR}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
