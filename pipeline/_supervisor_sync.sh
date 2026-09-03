#!/bin/bash
cd "/c/Users/José Caique/Desktop/ARQUIVOS IA/vendas-insights"
PY="/c/Users/José Caique/AppData/Local/Programs/Python/Python312/python.exe"
faltam() {
  PYTHONIOENCODING=utf-8 "$PY" -c "
import sqlite3
c=sqlite3.connect('file:vendas.db?mode=ro',uri=True)
print(c.execute('''SELECT COUNT(*) FROM sessoes s WHERE NOT EXISTS
  (SELECT 1 FROM mensagens m WHERE m.session_id=s.id)''').fetchone()[0])"
}
for tentativa in 1 2 3 4 5 6; do
  n=$(faltam)
  echo "[tentativa $tentativa] faltam $n sessoes sem mensagem"
  if [ "$n" -le 5 ]; then echo "CONCLUIDO: $n restantes"; break; fi
  PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 "$PY" app/sync_incremental.py --dias 60 --dias-ativas 3 >> _log_sync.txt 2>&1
  echo "[tentativa $tentativa] processo saiu com codigo $?"
done
echo "FIM DO SUPERVISOR — faltam $(faltam)"
