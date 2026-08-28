#!/bin/bash
cd "/c/Users/José Caique/Desktop/ARQUIVOS IA/vendas-insights"
PY="/c/Users/José Caique/AppData/Local/Programs/Python/Python312/python.exe"
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
for tentativa in 1 2 3 4; do
  echo "===== tentativa $tentativa ====="
  "$PY" app/classificar_ia.py --desde 2026-06-01 --ate 2026-06-30
  "$PY" app/classificar_ia.py --desde 2026-08-01
  restam=$("$PY" -c "
import sqlite3
c=sqlite3.connect('file:vendas.db?mode=ro',uri=True)
feitas={r[0] for r in c.execute('SELECT session_id FROM classificacao_ia')}
n=0
for sid,d in c.execute(\"SELECT id, created_at FROM sessoes WHERE substr(created_at,1,7) IN ('2026-06','2026-08')\"):
    if sid in feitas: continue
    t=c.execute('SELECT SUM(LENGTH(COALESCE(text,\'\'))) FROM mensagens WHERE session_id=? AND type NOT IN (\'TRACK\',\'NOTE\')',(sid,)).fetchone()[0] or 0
    if t>=40: n+=1
print(n)")
  echo "restam $restam de junho+agosto"
  [ "$restam" -le 5 ] && { echo "CONCLUIDO"; break; }
done
