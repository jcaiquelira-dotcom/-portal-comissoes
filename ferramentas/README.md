# ferramentas/

Rodado **à mão**: importações de planilha (fluxo de caixa, Shopee, colaboradores),
geradores e autorizações de uma vez. Nada aqui é chamado pelo `pipeline_diario.bat`
nem pelo `server.py`.

`scripts/` ficou só com o que roda sozinho: backup, sincronizadores, coletor do Vaapt,
o wrapper do agendador e o `checar_js.py` do hook de commit.

`_arquivo/` = substituído. `monitorar_sem_resposta.py` era o gêmeo local do monitor que
hoje roda dentro do servidor (`app/monitor_atendimento.py`). Fase 0 da `SIMPLIFICACAO.md`, 03/09/2026.
