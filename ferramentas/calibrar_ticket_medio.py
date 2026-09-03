"""
Compara a estimativa de vendas do Totalk (dataset.json) contra as vendas reais
lancadas no portal-comissoes, pra calibrar o ticket medio usado no painel.

Le os arquivos locais data/vendas_<vendedor>.json do portal-comissoes -- se esses
arquivos estiverem desatualizados (o time usa o portal hospedado em producao no
dia a dia), os numeros reais aqui tambem ficam desatualizados. Nesse caso e
preciso puxar do banco de producao (Supabase) em vez destes arquivos locais.
"""

import json
from pathlib import Path

PORTAL_COMISSOES_DIR = Path("G:/Meu Drive/portal-comissoes/data")
DATASET_PATH = Path(__file__).resolve().parent.parent / "dataset.json"

VENDEDORES = ["flavia", "gustavo", "matheus"]  # Brenda fica de fora de proposito
NOME_EXIBICAO = {"flavia": "Flávia", "gustavo": "Gustavo", "matheus": "Matheus"}


def vendas_reais(inicio: str, fim: str) -> dict:
    totais = {}
    for v in VENDEDORES:
        caminho = PORTAL_COMISSOES_DIR / f"vendas_{v}.json"
        d = json.loads(caminho.read_text(encoding="utf-8"))
        registros = [
            r for r in d.values()
            if r.get("tipo") == "venda" and inicio <= r["data"] <= fim
        ]
        totais[NOME_EXIBICAO[v]] = {
            "n": len(registros),
            "soma": sum(r["valor"] for r in registros),
        }
    return totais


def vendas_estimadas(inicio: str, fim: str, ticket_medio: float) -> dict:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    totais = {}
    for v in NOME_EXIBICAO.values():
        rows = [d for d in dataset if d["u"] == v and d["d"] and inicio <= d["d"] <= fim]
        provaveis = [d for d in rows if d["cv"] == "P"]
        totais[v] = {"n": len(provaveis), "soma": len(provaveis) * ticket_medio}
    return totais


def main():
    inicio, fim = "2026-07-07", "2026-08-18"
    ticket_atual = 968

    reais = vendas_reais(inicio, fim)
    estimados = vendas_estimadas(inicio, fim, ticket_atual)

    n_real_total = sum(v["n"] for v in reais.values())
    soma_real_total = sum(v["soma"] for v in reais.values())
    n_estim_total = sum(v["n"] for v in estimados.values())
    soma_estim_total = sum(v["soma"] for v in estimados.values())

    print(f"Periodo: {inicio} a {fim}\n")
    for nome in NOME_EXIBICAO.values():
        r, e = reais[nome], estimados[nome]
        print(f"{nome}: real={r['n']} vendas / R${r['soma']:,.2f}  |  "
              f"estimado={e['n']} / R${e['soma']:,.2f}")

    print()
    print(f"TOTAL real: {n_real_total} vendas, R$ {soma_real_total:,.2f}")
    print(f"TOTAL estimado: {n_estim_total} vendas, R$ {soma_estim_total:,.2f}")
    print(f"Cobertura da heuristica de conversao: {100*n_estim_total/n_real_total:.1f}%")
    print(f"Ticket medio real calibrado: R$ {soma_real_total/n_real_total:.2f}")
    print(f"(ticket medio em uso no painel: R$ {ticket_atual})")


if __name__ == "__main__":
    main()
