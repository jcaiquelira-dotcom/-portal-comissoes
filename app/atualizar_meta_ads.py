# -*- coding: utf-8 -*-
"""
Baixa o Meta Ads do Windsor (API key) e grava _meta_ads.json, que o
sincronizar_marketing.py le no lugar do CSV exportado a mao.

Por que existe: ate 28/08/2026 o Meta so entrava no painel por um CSV que
alguem exportava do Gerenciador. O relatorio salvo tinha periodo FIXO
(07/07 a 21/08), entao o painel travava nessa data e o investimento entrava
rateado por dia — aproximacao. Com a conta conectada no Windsor vem dia a dia,
com conversa iniciada por anuncio, e o rateio deixa de ser necessario.

Uso:
    python app/atualizar_meta_ads.py
"""

import io
import json
import sys
import urllib.request
from pathlib import Path
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = portal("segredos", "windsor_api_key.txt")
SAIDA = ROOT / "_meta_ads.json"

# O nome do campo de conversa e comprido porque e o id cru da API do Meta.
CONVERSAS = "actions_onsite_conversion_messaging_conversation_started_7d"
# publisher_platform separa Facebook de Instagram. Conferido contra a mesma
# consulta sem a quebra: fecha em R$ 0,44 no ano inteiro (arredondamento).
CAMPOS = ("date,datasource,account_name,campaign,ad_name,publisher_platform,"
          f"spend,clicks,impressions,reach,{CONVERSAS}")


def main():
    key = KEY_FILE.read_text(encoding="ascii").strip()
    # this_yearT = ano corrente incluindo hoje. O Meta consolida com atraso de
    # algumas horas, entao o dia de hoje pode vir parcial — e isso e esperado.
    url = (f"https://connectors.windsor.ai/facebook?api_key={key}"
           f"&date_preset=this_yearT&fields={CAMPOS}")
    with urllib.request.urlopen(url, timeout=300) as r:
        linhas = json.loads(r.read().decode())["data"]

    limpas = []
    for x in linhas:
        limpas.append({
            "data": x.get("date"),
            "campanha": x.get("campaign") or "—",
            "anuncio": x.get("ad_name") or "—",
            # "unknown" existe e sempre vem zerado; vira "—" pra nao virar uma
            # terceira plataforma na tela.
            "plataforma": (x.get("publisher_platform") or "").lower() or "—",
            "spend": round(float(x.get("spend") or 0), 2),
            "clicks": int(float(x.get("clicks") or 0)),
            "impressions": int(float(x.get("impressions") or 0)),
            "alcance": int(float(x.get("reach") or 0)),
            "conversas": int(float(x.get(CONVERSAS) or 0)),
        })
    limpas.sort(key=lambda x: (x["data"] or "", x["anuncio"]))
    SAIDA.write_text(json.dumps({"data": limpas}, ensure_ascii=False), encoding="utf-8")

    gasto = sum(x["spend"] for x in limpas)
    conversas = sum(x["conversas"] for x in limpas)
    datas = sorted({x["data"] for x in limpas if x["data"]})
    print(f"  _meta_ads.json: {len(limpas)} linhas | R$ {gasto:,.2f} | "
          f"{conversas} conversas | {datas[0] if datas else '—'} a "
          f"{datas[-1] if datas else '—'}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
