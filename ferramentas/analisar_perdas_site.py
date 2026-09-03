# -*- coding: utf-8 -*-
"""Onde o dinheiro do site NAO entra: analise dos pedidos do Vaapt que nao
viraram pagamento, gravada em `site_conta.analise` pro painel secundario do
card do site (admin.js: painelPerdasSite).

Historia: a primeira versao desta analise (02/09/2026) foi um trecho avulso
rodado na sessao, sem arquivo. Funcionou uma noite: o coletor diario regravou
a chave `site_conta` inteira as 11:21 do dia seguinte e a analise sumiu do
portal. Esta ferramenta e a versao que fica: usa o MESMO parser do coletor
(scripts/coletar_vaapt.py) — uma leitura so do painel, dois usos — e grava por
atualizar_site_conta(), que preserva o que nao e dela.

Cobre o historico inteiro de proposito (nao o periodo do filtro): padrao de
falha e coisa de volume. Grupos com menos de 8 pedidos a tela esconde; o piso
de Wilson a tela calcula. Aqui so se conta.

O ataque de 12/09/2025 fica de fora: dezenas de pedidos "Retirar na Loja" em
sequencia, nenhum pago. Mante-los inflaria a taxa de falha e apontaria a
modalidade como problema onde houve fraude. Pedidos PAGOS daquele dia ficam.

Uso:
    python ferramentas/analisar_perdas_site.py --seco   # so calcula e mostra
    python ferramentas/analisar_perdas_site.py          # calcula e grava
Precisa de segredos/vaapt.json (usuario/senha do painel) e de DATABASE_URL
(ou segredos/database_url.txt).
"""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py
import coletar_vaapt as V  # parser, login e gravacao do painel do site

DIA_ATAQUE = "2025-09-12"
FAIXAS = [(300, "até R$ 300"), (600, "R$ 300 a 600"), (1200, "R$ 600 a 1.200"),
          (float("inf"), "acima de R$ 1.200")]


def faixa(valor: float) -> str:
    for teto, nome in FAIXAS:
        if valor < teto:
            return nome
    return FAIXAS[-1][1]


def analisar(pedidos: list) -> dict:
    pagos = lambda x: V.sem_acento(x["status"]) in V.PAGOS  # noqa: E731
    descartados = [x for x in pedidos if x["data"] == DIA_ATAQUE and not pagos(x)]
    uteis = [x for x in pedidos if not (x["data"] == DIA_ATAQUE and not pagos(x))]

    grupos = {"pagamento": defaultdict(lambda: [0, 0, 0.0]),
              "cruz": defaultdict(lambda: [0, 0, 0.0]),
              "faixa": defaultdict(lambda: [0, 0, 0.0]),
              "frete": defaultdict(lambda: [0, 0, 0.0]),
              "uf": defaultdict(lambda: [0, 0, 0.0])}
    n = ok = 0
    perda = 0.0
    for x in uteis:
        pg = V.familia_pagamento(x.get("pagamento", ""))
        fx = faixa(x["valor"])
        chaves = {"pagamento": pg, "cruz": f"{fx} · {pg}", "faixa": fx,
                  "frete": " ".join(str(x.get("frete") or "").split()) or "sem frete informado",
                  "uf": x.get("uf") or "sem UF"}
        pago = pagos(x)
        n += 1
        ok += pago
        if not pago:
            perda += x["valor"]
        for g, k in chaves.items():
            v = grupos[g][k]
            v[0] += 1
            v[1] += pago
            if not pago:
                v[2] = round(v[2] + x["valor"], 2)

    datas = sorted(x["data"] for x in uteis)
    return {
        "gerado_em": datetime.now(C.FUSO).isoformat(timespec="seconds"),
        "de": datas[0], "ate": datas[-1],
        "n": n, "ok": ok, "perda": round(perda, 2),
        **{g: {k: v for k, v in sorted(d.items())} for g, d in grupos.items()},
        "descartado": {
            "n": len(descartados),
            "valor": round(sum(x["valor"] for x in descartados), 2),
            "motivo": (f"São os pedidos não pagos de {DIA_ATAQUE[8:]}/{DIA_ATAQUE[5:7]}/{DIA_ATAQUE[:4]}: "
                       "dezenas de pedidos em sequência, nenhum pago — um ataque, não clientes."),
        },
    }


def mostrar(a: dict, pedidos: list) -> None:
    print(f"\n  {len(pedidos)} pedidos lidos | analisados {a['n']} | pagos {a['ok']} "
          f"({100 * a['ok'] / a['n']:.1f}%) | nao viraram dinheiro {a['n'] - a['ok']} "
          f"({100 * (1 - a['ok'] / a['n']):.1f}%) = R$ {a['perda']:,.2f}")
    print(f"  periodo {a['de']} a {a['ate']} | descartados do ataque: {a['descartado']['n']} "
          f"(R$ {a['descartado']['valor']:,.2f})")
    sem_det = sum(1 for x in pedidos if not x.get("pagamento") and not x.get("frete"))
    print(f"  pedidos sem detalhe (modal nao encontrado): {sem_det}")
    for g in ("pagamento", "faixa", "frete", "cruz"):
        print(f"\n  {g}:")
        for k, (qn, qok, qperda) in sorted(a[g].items(), key=lambda kv: -kv[1][2]):
            if qn >= 8:
                print(f"     {k:<34} {qn:>4} ped  {qok:>4} pagos  {100 * qok / qn:5.1f}%  R$ {qperda:>10,.2f}")


def main() -> int:
    seco = "--seco" in sys.argv
    s = V.segredo()
    base = s["base"].rstrip("/")
    print(f"entrando em {base} como {s['usuario']}")
    opener = V.entrar(base, s["usuario"], s["senha"])
    print("sessao aberta — lendo o historico inteiro\n")
    pedidos = V.coletar(opener, base, None)
    if not pedidos:
        print("nenhum pedido lido — nada a fazer.")
        return 1
    a = analisar(pedidos)
    mostrar(a, pedidos)
    if seco:
        print("\n(--seco: nada gravado)")
        return 0
    V.atualizar_site_conta(lambda antigo: {**antigo, "analise": a})
    print("\n  gravado site_conta.analise — o coletor diario preserva a secao.")
    return 0


if __name__ == "__main__":
    C.saida_utf8()
    sys.exit(main())
