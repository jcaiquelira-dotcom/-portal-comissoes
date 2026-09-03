# -*- coding: utf-8 -*-
"""Onde as coisas estao NESTE computador — um lugar so.

Ate 03/09/2026, 13 arquivos deste projeto cravavam o caminho do portal-comissoes
por conta propria. O portal e o unico vizinho que este projeto precisa achar;
tudo que ele usa la (segredos, data, app) deriva de `portal_raiz`.
"""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = RAIZ / "config" / "caminhos.json"


def caminho(chave: str) -> Path:
    try:
        d = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{ARQUIVO} nao existe. E o JSON com os caminhos desta maquina.")
    if chave not in d:
        raise SystemExit(f"falta a chave {chave!r} em {ARQUIVO}")
    return Path(d[chave])


def portal(*partes) -> Path:
    """Um caminho dentro do portal-comissoes: portal("segredos", "google_ads.json")."""
    return caminho("portal_raiz").joinpath(*partes)
