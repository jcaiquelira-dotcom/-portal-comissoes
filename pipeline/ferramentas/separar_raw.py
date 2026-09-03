# -*- coding: utf-8 -*-
"""Fase 5 da SIMPLIFICACAO.md: tira `mensagens.raw` do vendas.db.

Em 03/09/2026 o vendas.db tinha 406 MB, e 233 MB (57%) eram a coluna `raw` de
mensagens — o JSON inteiro que o Totalk devolve, guardado por precaucao. Dos
tres scripts que liam essa coluna, um nao usava nada dela (classificar_ia) e os
outros dois so tiravam `userId` (export_dataset, gerar_fila_retomada) e `origin`,
que ja e coluna. Ou seja: 233 MB parseados linha a linha, toda manha, por um
campo so.

O que esta ferramenta faz, nesta ordem, parando no primeiro erro:
  1. copia o vendas.db inteiro pra `vendas_backup_<data>_antes_fase5.db`;
  2. grava cada `raw` em `vendas_raw.db` (tabela mensagens_raw: id, raw) e
     confere que TODAS as linhas chegaram identicas;
  3. cria a coluna `mensagens.user_id` e preenche a partir do raw (json_extract);
  4. so entao derruba a coluna `raw` e roda VACUUM pra devolver o espaco.

`sessoes.raw` (17 MB) fica onde esta: a fila usa `previewUrl` dela e nao vale
a cirurgia. Dali em diante o sync (app/sync.py) grava o raw novo direto no
vendas_raw.db — nada se perde, so muda de arquivo. Ninguem le o vendas_raw.db;
ele e arquivo morto, pra quando alguem precisar de um campo que ninguem previu.

E idempotente: se a coluna raw ja nao existe, nao faz nada.

Uso:
    python pipeline/ferramentas/separar_raw.py            # so mede e mostra
    python pipeline/ferramentas/separar_raw.py --executar # faz de verdade
"""
import shutil
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho  # config/caminhos.json — ver app/caminhos.py

DADOS = Path(caminho("dados"))
DB = DADOS / "vendas.db"
RAW = DADOS / "vendas_raw.db"


def mb(caminho_arquivo: Path) -> str:
    return f"{caminho_arquivo.stat().st_size / 1e6:.1f} MB"


def colunas(conn, tabela):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")]


def um(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def main() -> int:
    executar = "--executar" in sys.argv
    print(f"vendas.db: {mb(DB)} | sqlite {sqlite3.sqlite_version}")
    conn = sqlite3.connect(DB)
    cols = colunas(conn, "mensagens")
    if "raw" not in cols:
        print("ja migrado: mensagens nao tem mais a coluna raw. Nada a fazer.")
        return 0

    n = um(conn, "SELECT COUNT(*) FROM mensagens")
    n_raw = um(conn, "SELECT COUNT(*) FROM mensagens WHERE raw IS NOT NULL")
    n_uid = um(conn, "SELECT COUNT(*) FROM mensagens WHERE json_extract(raw,'$.userId') IS NOT NULL")
    bytes_raw = um(conn, "SELECT COALESCE(SUM(LENGTH(raw)),0) FROM mensagens")
    invalidos = um(conn, "SELECT COUNT(*) FROM mensagens WHERE raw IS NOT NULL AND json_valid(raw)=0")
    print(f"mensagens: {n:,} linhas | raw preenchido em {n_raw:,} ({bytes_raw/1e6:.1f} MB) "
          f"| userId presente em {n_uid:,} | JSON invalido: {invalidos}")
    if invalidos:
        print("ha raw invalido — nao sigo, olhe antes.")
        return 1
    if not executar:
        print("plano: backup -> copiar raw pra vendas_raw.db -> user_id -> DROP COLUMN raw -> VACUUM")
        print("rode com --executar pra fazer.")
        return 0

    # 1) backup inteiro, com o banco fechado
    conn.close()
    backup = DADOS / f"vendas_backup_{time.strftime('%Y-%m-%d_%Hh%M')}_antes_fase5.db"
    t = time.time()
    shutil.copy2(DB, backup)
    print(f"1) backup: {backup.name} ({mb(backup)}) em {time.time()-t:.0f}s")

    # 2) raw -> vendas_raw.db, e prova de que chegou inteiro
    conn = sqlite3.connect(DB)
    conn.execute("ATTACH DATABASE ? AS bruto", (str(RAW),))
    conn.execute("CREATE TABLE IF NOT EXISTS bruto.mensagens_raw (id TEXT PRIMARY KEY, raw TEXT)")
    t = time.time()
    conn.execute("INSERT OR REPLACE INTO bruto.mensagens_raw (id, raw) "
                 "SELECT id, raw FROM mensagens WHERE raw IS NOT NULL")
    conn.commit()
    iguais = um(conn, "SELECT COUNT(*) FROM mensagens m JOIN bruto.mensagens_raw r ON r.id = m.id "
                      "WHERE m.raw IS NOT NULL AND r.raw = m.raw")
    print(f"2) vendas_raw.db: {um(conn, 'SELECT COUNT(*) FROM bruto.mensagens_raw'):,} linhas | "
          f"identicas ao original: {iguais:,} de {n_raw:,} | {time.time()-t:.0f}s")
    if iguais != n_raw:
        print("   NAO bateu — parei antes de mexer no vendas.db. O backup esta intacto.")
        return 1

    # 3) user_id como coluna de verdade
    if "user_id" not in cols:
        conn.execute("ALTER TABLE mensagens ADD COLUMN user_id TEXT")
    conn.execute("UPDATE mensagens SET user_id = json_extract(raw,'$.userId') WHERE raw IS NOT NULL")
    conn.commit()
    com_uid = um(conn, "SELECT COUNT(*) FROM mensagens WHERE user_id IS NOT NULL")
    print(f"3) user_id preenchido em {com_uid:,} linhas (esperado {n_uid:,})")
    if com_uid != n_uid:
        print("   NAO bateu — parei. A coluna raw ainda esta la; o backup tambem.")
        return 1

    # 4) agora sim: fora com a coluna, e devolve o espaco
    t = time.time()
    conn.execute("ALTER TABLE mensagens DROP COLUMN raw")
    conn.commit()
    conn.execute("DETACH DATABASE bruto")
    conn.execute("VACUUM")
    conn.close()
    print(f"4) DROP COLUMN raw + VACUUM em {time.time()-t:.0f}s")
    print(f"resultado: vendas.db {mb(DB)} | vendas_raw.db {mb(RAW)} | backup {backup.name} {mb(backup)}")
    print("Quando tudo estiver conferido, o backup pode ser apagado a mao.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
