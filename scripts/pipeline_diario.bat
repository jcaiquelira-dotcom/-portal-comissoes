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

rem Backup ANTES de qualquer escrita do dia: se algum passo abaixo gravar
rem besteira, o estado de ontem ainda existe inteiro.
echo --- backup do banco --- >> "%LOG%"
"%PY%" "%PORTAL%\scripts\backup_dados.py" >> "%LOG%" 2>&1

cd /d "%INSIGHTS%"
echo --- totalk (sync incremental) --- >> "%LOG%"
"%PY%" app\sync_incremental.py >> "%LOG%" 2>&1

echo --- classificacao IA (so conversas novas) --- >> "%LOG%"
"%PY%" app\classificar_ia.py --limite 300 >> "%LOG%" 2>&1

rem O canal de cada conversa (anuncio, site, direto) sai daqui, e NAO da
rem leitura da IA: e utm mais texto das mensagens. Ficou fora do pipeline
rem ate 02/09/2026, e o resultado foram 699 conversas paradas em "Sem
rem origem" desde 28/08 - o painel mostrava o Meta com conversas e o card
rem de origem com zero anuncio, como se a integracao tivesse caido.
rem Precisa vir DEPOIS do sync e ANTES do dataset, que le a tabela canal.
echo --- canal e sinal das conversas --- >> "%LOG%"
"%PY%" app\remontar_canal_sinal.py >> "%LOG%" 2>&1

echo --- dataset (base da fila) --- >> "%LOG%"
"%PY%" app\export_dataset.py >> "%LOG%" 2>&1

echo --- fila de retomada --- >> "%LOG%"
"%PY%" app\gerar_fila_retomada.py >> "%LOG%" 2>&1
"%PY%" app\sincronizar_crm.py >> "%LOG%" 2>&1

echo --- meta ads (windsor) --- >> "%LOG%"
"%PY%" app\atualizar_meta_ads.py >> "%LOG%" 2>&1

rem Google Ads vem DIRETO da API do Google desde 02/09/2026, nao mais do
rem Windsor. Motivo: no plano basico do Windsor so UMA fonte fica conectada
rem por vez, e o Google ficou desligado desde 28/08 sem ninguem notar - o
rem painel mostrava R$ 4.353 de agosto quando foram R$ 4.881.
rem Escreve os MESMOS dois arquivos (_w_amplo.json e _windsor_periodo.json),
rem entao o sincronizar_marketing.py nao muda. Pra voltar atras, e so trocar
rem de volta pelo atualizar_google_ads.py.
echo --- google ads (api direta) --- >> "%LOG%"
"%PY%" app\google_ads_api.py >> "%LOG%" 2>&1

rem Perfil da Empresa e Search Console, tambem direto do Google. Enquanto as
rem APIs nao estiverem habilitadas no projeto do Cloud eles falham e o
rem pipeline segue - cada passo aqui continua mesmo se o anterior quebrar, e
rem o log conta qual foi.
echo --- perfil da empresa (api direta) --- >> "%LOG%"
"%PY%" app\perfil_google_api.py >> "%LOG%" 2>&1

echo --- search console --- >> "%LOG%"
"%PY%" app\search_console_api.py >> "%LOG%" 2>&1

echo --- desempenho e marketing --- >> "%LOG%"
"%PY%" app\sincronizar_desempenho.py >> "%LOG%" 2>&1
"%PY%" app\sincronizar_marketing.py >> "%LOG%" 2>&1

rem Orfaos ate 01/09/2026: existiam como script mas ninguem os chamava — o
rem gestor via "Carros pra chegar" 5 dias velho sem saber por que.
echo --- carros pra chegar --- >> "%LOG%"
"%PY%" "%PORTAL%\scripts\sincronizar_carros.py" >> "%LOG%" 2>&1

echo --- metas bonus (producao) --- >> "%LOG%"
"%PY%" "%PORTAL%\scripts\sincronizar_metas_bonus.py" >> "%LOG%" 2>&1

rem O sincronizar_analytics so EMPURRA o _ga4.json pro portal; quem o gera e
rem o analytics_api. Ate 02/09/2026 so o segundo passo rodava, entao o card
rem carimbava data de hoje em cima de dado congelado em 30/08 - parado com
rem cara de atualizado, que e pior que parado com cara de parado.
echo --- google analytics (coleta) --- >> "%LOG%"
"%PY%" app\analytics_api.py >> "%LOG%" 2>&1

echo --- google analytics do site --- >> "%LOG%"
"%PY%" app\sincronizar_analytics.py >> "%LOG%" 2>&1

rem Site proprio (painel do Vaapt). Ate 03/09/2026 era o unico canal somado na
rem mao. --dias=15: a serie so cresce e a rodada diaria so precisa dos dias
rem recentes; ler 60 paginas todo dia foi o que acordou o Cloudflare.
echo --- site proprio (vaapt) --- >> "%LOG%"
"%PY%" "%PORTAL%\scripts\coletar_vaapt.py" --dias=15 >> "%LOG%" 2>&1

rem Mercado Livre: desde 03/09/2026 quem grava ml_conta e o servidor (Render),
rem de hora em hora, com um unico rotacionador do token. O sincronizar_ml.py
rem foi pra ferramentas/ como reserva manual. Decisao do gestor, Fase 2.

echo ===== %date% %time% - fim ===== >> "%LOG%"
