@echo off
rem =====================================================================
rem Atalho LOCAL que o Agendador chama, em vez de apontar direto pro
rem G:\...\pipeline_diario.bat (que pode nao existir ainda no login,
rem enquanto o Google Drive monta o G:).
rem
rem MORA EM C:\ProgramData DE PROPOSITO. Antes vivia em
rem %LOCALAPPDATA%\NevadaPipeline e a tarefa NUNCA conseguiu executa-lo:
rem quem criou aquele arquivo foi um app empacotado (container), e escrita
rem em AppData\Local de app empacotado vai pra uma copia espelhada que so o
rem proprio app enxerga. Pro Agendador a pasta simplesmente nao existia —
rem "dir" respondia "Arquivo nao encontrado". Resultado: 0x1 todo dia,
rem de 28/08 a 01/09/2026, sem log nenhum, porque o .bat que escreveria o
rem log e que nao rodava. ProgramData nao e virtualizado: os dois lados
rem enxergam o mesmo arquivo.
rem =====================================================================
set "ALVO=G:\Meu Drive\portal-comissoes\scripts\pipeline_diario.bat"
set "LOG=%~dp0espera_g.log"
set "MARCA=%~dp0ultimo_dia.txt"

rem ---- ja rodou hoje? (data via PowerShell: %date% muda com o idioma) ----
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "HOJE=%%d"
if exist "%MARCA%" (set /p ULTIMO=<"%MARCA%") else (set "ULTIMO=")
if "%ULTIMO%"=="%HOJE%" (
  echo %HOJE% ja rodou - pulando ^(%time%^) >> "%LOG%"
  goto fim
)

echo ===== %date% %time% - aguardando G: ===== >> "%LOG%"
set /a tentativas=0
:espera
if exist "%ALVO%" goto achou
set /a tentativas+=1
if %tentativas% GEQ 30 goto desistiu
rem ping como espera: timeout.exe recusa rodar sem stdin (caso do Agendador)
ping -n 11 127.0.0.1 >nul
goto espera

:achou
echo G: pronto apos %tentativas% tentativas ^(%date% %time%^) >> "%LOG%"

rem ---- rede: no logon o gatilho dispara ANTES do Wi-Fi conectar. Em 02/09 e
rem 03/09/2026 o pipeline morreu nos dois dias com "getaddrinfo failed" por
rem isso - e o marcador, gravado antes, barrava qualquer nova tentativa no dia.
rem Espera ate 10 minutos pela rede antes de comecar.
set /a rede=0
:esperarede
ping -n 1 -w 2000 8.8.8.8 >nul 2>&1 && goto temrede
set /a rede+=1
if %rede% GEQ 60 goto semrede
ping -n 6 127.0.0.1 >nul
goto esperarede
:temrede
echo rede ok apos %rede% tentativas ^(%time%^) >> "%LOG%"

rem ---- trava em vez de marcador antecipado. O marcador do dia so e gravado
rem quando o pipeline chega ao FIM; quem impede dois pipelines juntos e a
rem trava, que perde a validade sozinha (2h) se um deles morrer no meio.
set "TRAVA=%~dp0rodando.txt"
set "IDADE="
if exist "%TRAVA%" for /f %%t in ('powershell -NoProfile -Command "[int]((Get-Date) - (Get-Item ''%TRAVA%'').LastWriteTime).TotalMinutes"') do set "IDADE=%%t"
if defined IDADE if %IDADE% LSS 120 (
  echo ja tem pipeline rodando ha %IDADE% min - pulando ^(%time%^) >> "%LOG%"
  goto fim
)
echo %date% %time%>"%TRAVA%"
call "%ALVO%"
del "%TRAVA%" >nul 2>&1
findstr /c:"%HOJE:~8,2%/%HOJE:~5,2%/%HOJE:~0,4%" "G:\Meu Drive\portal-comissoes\segredos\pipeline_diario.log" | findstr /c:"- fim =====" >nul 2>&1 && echo %HOJE%>"%MARCA%"
echo ===== %date% %time% - atalho concluido ===== >> "%LOG%"
goto fim

:semrede
echo DESISTI: sem rede depois de 10 minutos ^(%date% %time%^) >> "%LOG%"
goto fim

:desistiu
echo DESISTI: G: nao apareceu em 5 minutos ^(%date% %time%^) >> "%LOG%"

:fim