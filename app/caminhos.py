# -*- coding: utf-8 -*-
"""Onde as coisas estao NESTE computador — um lugar so.

Ate 03/09/2026 cada script cravava o proprio caminho absoluto (Desktop,
Downloads, Documents, G:). Trocar de PC ou mover uma planilha quebrava cinco
scripts em lugares diferentes, um por vez, sem aviso. Agora e um JSON,
`config/caminhos.json`. Faltou uma chave? O erro diz qual.
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
