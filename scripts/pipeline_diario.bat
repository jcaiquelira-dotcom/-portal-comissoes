@echo off
rem =====================================================================
rem Pipeline diario da Nevada - roda sozinho pelo Agendador de Tarefas.
rem
rem Ordem importa: primeiro o Totalk atualiza o vendas.db, dai a IA le as
rem conversas novas, a fila de retomada e refeita, e so entao os numeros
rem sobem pro portal. O Mercado Livre e independente e vai por ultimo.
rem
rem Log em segredos\pipeline_diario.log (fora do git). Cada passo continua
rem mesmo se o anterior falhar: um erro no Totalk nao pode segurar a
rem reputacao do ML, e o log conta o que quebrou.
rem
rem CRLF obrigatorio neste arquivo: com quebra LF o cmd embaralha as
rem variaveis (ja aconteceu). Caminhos em 8.3 (JOSCAI~1) pra nao depender
rem de acento em nome de pasta.
rem =====================================================================
set "PY=C:\Users\JOSCAI~1\AppData\Local\Programs\Python\Python312\python.exe"
set "PORTAL=G:\Meu Drive\portal-comissoes"
set "INSIGHTS=C:\Users\JOSCAI~1\Desktop\ARQUIVOS IA\vendas-insights"
set "LOG=%PORTAL%\segredos\pipeline_diario.log"
set /p DATABASE_URL=<"%PORTAL%\segredos\database_url.txt"

echo. >> "%LOG%"
echo ===== %date% %time% - inicio ===== >> "%LOG%"

cd /d "%INSIGHTS%"
echo --- totalk (sync incremental) --- >> "%LOG%"
"%PY%" app\sync_incremental.py >> "%LOG%" 2>&1

echo --- classificacao IA (so conversas novas) --- >> "%LOG%"
"%PY%" app\classificar_ia.py --limite 300 >> "%LOG%" 2>&1

echo --- dataset (base da fila) --- >> "%LOG%"
"%PY%" app\export_dataset.py >> "%LOG%" 2>&1

echo --- fila de retomada --- >> "%LOG%"
"%PY%" app\gerar_fila_retomada.py >> "%LOG%" 2>&1
"%PY%" app\sincronizar_crm.py >> "%LOG%" 2>&1

echo --- google ads (windsor) --- >> "%LOG%"
"%PY%" app\atualizar_google_ads.py >> "%LOG%" 2>&1

echo --- desempenho e marketing --- >> "%LOG%"
"%PY%" app\sincronizar_desempenho.py >> "%LOG%" 2>&1
"%PY%" app\sincronizar_marketing.py >> "%LOG%" 2>&1

echo --- conta mercado livre --- >> "%LOG%"
"%PY%" "%PORTAL%\scripts\sincronizar_ml.py" >> "%LOG%" 2>&1

echo ===== %date% %time% - fim ===== >> "%LOG%"
