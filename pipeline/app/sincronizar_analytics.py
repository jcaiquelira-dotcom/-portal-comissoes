# -*- coding: utf-8 -*-
"""
Empurra o Google Analytics do site pro portal.

Le o _ga4.json (gerado por app/analytics_api.py) e grava na chave
`analytics_site`, no mesmo banco que o resto do painel ja usa.

Tudo vai no grao DIARIO. E o que deixa o painel somar qualquer periodo que o
gestor filtrar sem aproximar nada — o mesmo criterio do marketing.

Uso:
    set DATABASE_URL=postgresql://...
    python app/sincronizar_analytics.py
    python app/sincronizar_analytics.py --seco     # so mostra, nao grava
"""

import argparse
import collections
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py
sys.path.insert(0, str(portal("app")))
import nevada_comum as C  # biblioteca comum do portal — ver la app/nevada_comum.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
ORIGEM = ROOT / "_ga4.json"


def _iso(d: str) -> str:
    """20260831 -> 2026-08-31. O GA4 devolve sem tracos; o painel usa com."""
    return "{}-{}-{}".format(d[:4], d[4:6], d[6:8]) if len(d) == 8 else d


def montar() -> dict:
    if not ORIGEM.exists():
        raise SystemExit("{} nao existe. Rode app/analytics_api.py antes."
                         .format(ORIGEM))
    d = json.loads(ORIGEM.read_text(encoding="utf-8"))

    por_dia = {}
    for x in d["por_dia"]:
        por_dia[_iso(x["date"])] = {
            "sessoes": x["sessions"],
            "usuarios": x["totalUsers"],
            "novos": x["newUsers"],
            "paginas_vistas": x["screenPageViews"],
            "duracao_media": round(x["averageSessionDuration"], 1),
            "rejeicao": round(100 * x["bounceRate"], 1),
        }

    origem = {}
    for x in d["por_origem"]:
        dia = origem.setdefault(_iso(x["date"]), {})
        dia[x["sessionDefaultChannelGroup"]] = {
            "sessoes": x["sessions"], "usuarios": x["totalUsers"]}

    eventos = {}
    for x in d["eventos"]:
        eventos.setdefault(_iso(x["date"]), {})[x["eventName"]] = x["eventCount"]

    # Leads por canal, por dia. Soma os eventos de lead do mesmo canal: sao dois
    # nomes de evento pra mesma acao (o atual e o antigo), e separa-los na tela
    # inventaria uma distincao que nao existe pro gestor.
    leads_origem = {}
    for x in d.get("leads_origem") or []:
        dia = leads_origem.setdefault(_iso(x["date"]), {})
        canal = x["sessionDefaultChannelGroup"] or "(sem origem)"
        dia[canal] = dia.get(canal, 0) + x["eventCount"]

    # Paginas nao tem data (o GA4 cobraria uma linha por pagina POR DIA, que
    # estoura o limite). Vem do periodo inteiro, e por isso viaja com o
    # periodo junto — o painel avisa que essa lista nao acompanha o filtro.
    paginas = [{"caminho": p["pagePath"], "titulo": p["pageTitle"],
                "vistas": p["screenPageViews"], "sessoes": p["sessions"],
                "engajamento": round(p["userEngagementDuration"], 0)}
               for p in d["paginas"]]

    return {
        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "de": _iso(d["de"].replace("-", "")) if len(d["de"]) == 8 else d["de"],
        "ate": d["ate"],
        "serie_dia": por_dia,
        "origem_dia": origem,
        "eventos_dia": eventos,
        "leads_origem_dia": leads_origem,
        "paginas": paginas,
        "paginas_periodo": {"de": d["de"], "ate": d["ate"]},
    }


def resumir(bloco: dict) -> None:
    dias = sorted(bloco["serie_dia"])
    s = sum(v["sessoes"] for v in bloco["serie_dia"].values())
    print("analytics: {} dias | {:,} sessoes | {} a {}".format(
        len(dias), s, dias[0] if dias else "-", dias[-1] if dias else "-"))
    canais = collections.Counter()
    for dia in bloco["origem_dia"].values():
        for nome, v in dia.items():
            canais[nome] += v["sessoes"]
    for nome, v in canais.most_common(6):
        print("    {:26s} {:6,}".format(nome, v))


def gravar(bloco: dict) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL nao definida.")
        return
    C.gravar_chave("analytics_site", bloco)
    print("  gravado analytics_site")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true")
    args = ap.parse_args()
    bloco = montar()
    resumir(bloco)
    if args.seco:
        print("\n(seco: nada gravado)")
        return
    gravar(bloco)


if __name__ == "__main__":
    C.saida_utf8()
    main()
