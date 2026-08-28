# -*- coding: utf-8 -*-
"""
Baixa o Google Ads do Windsor (API key) e regrava os dois arquivos que o
sincronizar_marketing.py ja le:

  _w_amplo.json      — o ano inteiro, so gasto: e a serie longa do painel
  _windsor_periodo.json — ultimos 60 dias com clique e impressao

Antes disso os arquivos eram gerados a mao numa sessao do Claude (via MCP) e
congelavam; com a key o pipeline das 07:30 os renova sozinho. A key mora em
portal-comissoes/segredos/windsor_api_key.txt, fora do git.
"""

import io
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = Path(r"G:\Meu Drive\portal-comissoes\segredos\windsor_api_key.txt")


def puxar(campos: str, preset: str) -> list:
    key = KEY_FILE.read_text(encoding="ascii").strip()
    url = (f"https://connectors.windsor.ai/google_ads?api_key={key}"
           f"&date_preset={preset}&fields={campos}")
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.loads(r.read().decode())["data"]


def gravar(nome: str, linhas: list) -> None:
    caminho = ROOT / nome
    caminho.write_text(json.dumps({"data": linhas}, ensure_ascii=False),
                       encoding="utf-8")
    datas = sorted({x.get("date") for x in linhas if x.get("date")})
    gasto = sum(float(x.get("spend") or 0) for x in linhas)
    print(f"  {nome}: {len(linhas)} linhas | R$ {gasto:,.2f} | "
          f"{datas[0] if datas else '—'} a {datas[-1] if datas else '—'}")


def main():
    # this_yearT = ano corrente incluindo hoje; last_60dT idem pros 60 dias.
    gravar("_w_amplo.json",
           puxar("date,datasource,account_name,campaign,campaign_id,spend",
                 "this_yearT"))
    gravar("_windsor_periodo.json",
           puxar("date,datasource,account_name,campaign,campaign_id,"
                 "spend,clicks,impressions", "last_60dT"))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
