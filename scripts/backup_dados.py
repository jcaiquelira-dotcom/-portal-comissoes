# -*- coding: utf-8 -*-
"""Backup diario da tabela dados_json — o banco inteiro do portal.

Tudo que o portal sabe mora numa tabela so (chave -> JSON): vendas, comissoes,
usuarios, permissoes, RH, series de marketplace. Um DELETE errado, uma migracao
mal feita ou um vacilo meu apaga historico que nao existe em mais lugar nenhum.
Hoje (01/09/2026) o backup era eu lembrando de copiar na mao antes de mexer —
duas vezes num dia so, o que e exatamente o tipo de disciplina que falha.

Grava um arquivo por dia em segredos/backups/ (fora do git — tem dado de
comissao e senha de usuario) e guarda os ultimos 14. Na pasta do Google Drive,
entao cada backup ainda sobe pra nuvem de carona.

Restaurar: o arquivo e {chave: valor}; reescrever uma chave especifica e um
UPDATE simples. Nao ha script de restauracao de proposito — restaurar tudo de
uma vez e decisao grande demais pra ficar a um comando de distancia.

Uso (o pipeline diario chama; na mao tambem funciona):
    set DATABASE_URL=postgresql://...
    python scripts/backup_dados.py
"""
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py

FUSO = C.FUSO
PASTA = Path(__file__).resolve().parent.parent / "segredos" / "backups"
GUARDAR = 14


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("backup: sem DATABASE_URL — nada a fazer")
        return 1

    import psycopg2

    hoje = datetime.now(FUSO).date().isoformat()
    PASTA.mkdir(parents=True, exist_ok=True)
    destino = PASTA / f"dados_json-{hoje}.json.gz"
    if destino.exists():
        print(f"backup: {destino.name} já existe — um por dia basta")
        return 0

    con = psycopg2.connect(url)
    with con.cursor() as cur:
        cur.execute("SELECT chave, valor FROM dados_json ORDER BY chave")
        dados = {chave: valor for chave, valor in cur.fetchall()}
    con.close()

    if len(dados) < 10:
        # Um banco quase vazio no lugar do de sempre e sinal de problema, nao
        # de backup: gravar isso por cima da rotacao empurraria os backups
        # bons pra fora da janela de 14 dias.
        print(f"backup: só {len(dados)} chaves — parece errado, não gravei")
        return 1

    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    with gzip.open(destino, "wb") as f:
        f.write(corpo)
    print(f"backup: {destino.name} — {len(dados)} chaves, "
          f"{destino.stat().st_size // 1024} KB (de {len(corpo) // 1024} KB)")

    antigos = sorted(PASTA.glob("dados_json-*.json.gz"))[:-GUARDAR]
    for a in antigos:
        a.unlink()
        print(f"backup: apagado {a.name} (fora da janela de {GUARDAR} dias)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
