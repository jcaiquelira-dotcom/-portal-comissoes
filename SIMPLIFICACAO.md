# Simplificação do sistema — diagnóstico e plano

Medido em 03/09/2026 nos dois repositórios (`portal-comissoes`, `vendas-insights`).
Regra do plano: **nenhuma funcionalidade sai**. Cada fase é uma sessão curta, com
commit próprio e caminho de volta.

## O que a medição mostrou

**1. Dois repositórios que são um sistema só.** 19 arquivos têm o caminho de um
dentro do outro. O `vendas-insights` escreve 8 chaves direto no banco do portal.
Os dois só funcionam juntos, mas vivem separados — e o pipeline diário costura
os dois por caminho absoluto (`G:\Meu Drive\...`, `C:\Users\José Caique\Desktop\...`).

**2. Utilitários copiados em vez de compartilhados.**
- 15 arquivos abrem o Postgres por conta própria (`psycopg2.connect`)
- 11 definem o fuso (`timezone(timedelta(hours=-3))`)
- 6 têm um `_cred()`, 4 têm um `token_de_acesso()` — o mesmo OAuth do Google
- 18 repetem o mesmo `sys.stdout = io.TextIOWrapper(...)`
- O mapa de atendentes tinha 3 cópias e só uma sabia da troca Gustavo→Lucas
  (bug real, corrigido em 03/09 — é o caso típico do que a duplicação produz).

**3. O mesmo número produzido por dois caminhos.** O `server.py` sobe 5 threads
no Render que gravam as MESMAS chaves que o pipeline local grava:
`marketing_gasto` (Windsor), `perfil_google` (Windsor), `ml_conta`, `ml_faturamento`.
A única trava é "ainda não gravou hoje". Google Ads já saiu do Windsor no local
(vem da API) mas a nuvem continua puxando do Windsor — duas fontes, dois números.
Ninguém decidiu qual manda.

**4. Scripts que ninguém chama.** Não estão no pipeline nem são importados:
- vendas-insights: `atualizar_google_ads.py` (substituído; só citado num `rem`),
  `cruzar_google_ads.py`, `cruzar_meta_ads.py` (lê um CSV cravado no Desktop),
  `calibrar_ticket_medio.py`, `extrair_ticket.py`, `insights.py`, `montar_painel.py`,
  `gerar_pdf_relatorio.py`, `gerar_relatorio_vendedor.py`, `sync_janela.py`,
  `_teste_chave.py` (nem versionado). `sync.py` fica: é biblioteca do incremental.
- portal: `monitorar_sem_resposta.py` (gêmeo local do monitor da nuvem),
  `etl_simulador.py`, `gerar_planilha_modelo.py`, `autorizar_google_sheets.py`,
  `importar_comissao_lucas.py` — ferramentas de uma vez, misturadas com o que roda todo dia.

**5. Dois arquivos gigantes.** `server.py`: 6.578 linhas, 104 rotas. `admin.html`:
6.484 linhas com todo o JavaScript dentro. Os dois já têm fronteiras escritas em
comentário (Simulador · Plano de contas · Auditoria · RH · Meta Bônus · Expedição ·
Carros · Marketing · Retomada · Nuvem) — só não viraram arquivos.

**6. Caminhos de usuário cravados no código.** Planilha de carros no Desktop,
colaboradores no Desktop, `ml_auth.json` em Documents/ml-dashboard, fluxo de caixa
em Downloads (com nome que muda todo mês), CSV do Meta no Desktop. Trocar de PC ou
de pasta quebra cinco scripts.

**7. Segredos em três lugares.** `portal/segredos/`, `vendas-insights/.env`,
`Documents/ml-dashboard/ml_auth.json`.

**8. `vendas.db` tem 387 MB, e 222 MB (57%) é `mensagens.raw`** — o JSON bruto
de cada mensagem, guardado "por precaução". O texto útil ocupa 8 MB.

**9. `painel-metas` e `portal-pecas` não têm git**, e o portal lê dados do
painel-metas por caminho absoluto.

## O plano, em fases (cada uma é uma sessão)

### Fase 0 — arrumar a casa (risco zero)
- `git mv` dos scripts do item 4 pra `ferramentas/` (os de uma vez) e
  `_arquivo/` (os substituídos). Nada é apagado; sai do caminho.
- `config/caminhos.json` com todos os caminhos do item 6; os cinco scripts leem dele.
- Segredos do item 7 concentrados em `portal/segredos/`; o `.env` vira um ponteiro.

### Fase 1 — uma biblioteca comum
`nevada_comum.py` com: `banco()` (conexão + `ler/escrever_json` com a mesma
regra do server), `AGORA/FUSO`, `cred_google()` + `token_google()`, `saida_utf8()`.
Os 15+11+6+4+18 pontos passam a importar dela. É a fase que impede o próximo bug
do tipo "três mapas de atendente".

**Estado (03/09):** feita. `app/nevada_comum.py` no portal; os dois repos usam.
Sobrou, de propósito: o `server.py` (tem o próprio `ler_json/escrever_json` com a
regra banco-ou-arquivo do Flask — é assunto da Fase 4) e duas ferramentas de mão
(`importar_colaboradores`, `etl_simulador`) que ainda abrem o banco sozinhas.

### Fase 2 — um dono por chave
Decidir, chave a chave, quem grava: pipeline local ou thread da nuvem. Sugestão:
- **local**: `marketing_gasto` (Ads já vem da API), `analytics_site`, `perfil_google`
  (quando a cota sair), `site_conta`, `crm_*`, `insights_*`, `marketing_leads`
- **nuvem**: `ml_conta`, `ml_faturamento` (o token do ML rotaciona e mora no
  banco — faz sentido na nuvem), monitor de atendimento
- e **apagar o perdedor**: o Google via Windsor na nuvem sai.

**Estado (03/09):** feita, por decisão do gestor.
- `marketing_gasto` e `perfil_google`: **dono é o pipeline local**. As threads da
  nuvem viraram reserva: só gravam se a chave estiver há mais de 30h sem
  atualização (PC da loja desligado). Regra em `sincronizador_nuvem.recente()`.
- `ml_conta`: **dono é a nuvem** (de hora em hora, um só rotacionador do token).
  O passo local saiu do pipeline; `ferramentas/sincronizar_ml.py` fica como reserva manual.
- `ml_faturamento`: já era só da nuvem.

### Fase 3 — um repositório
`nevada/` com `portal/` (o Flask, Render aponta pra ele), `pipeline/` (o que hoje é
vendas-insights), `ferramentas/`, `comum/`. Os 19 caminhos cruzados viram imports
relativos. Exige trocar o *root directory* do serviço no Render — passo do Caique.

**Estado (03/09):** feita, com um ajuste de desenho medido na hora.
- O repo único é **este** (`-portal-comissoes`); o Flask continua na raiz, então o
  Render do portal não mudou nada. O `vendas-insights` entrou como `pipeline/` via
  `git subtree` — histórico inteiro preservado.
- **Os dados ficaram fora do Drive, de propósito.** `vendas.db` tem 388 MB e é
  reescrito por 30 min todo dia; SQLite + sincronizador de nuvem é receita de
  corrupção. A pasta antiga (`Desktop\ARQUIVOS IAendas-insights`) virou só pasta
  de DADOS (vendas.db, dataset.json, .env, `_*.json`), apontada por
  `pipeline/config/caminhos.json` → `dados`. 27 scripts passaram a achar a raiz por lá.
- `pipeline_diario.bat`: `INSIGHTS=%PORTAL%\pipeline`.
- **Falta você:** o serviço do webhook no Render (`vendas-insights.onrender.com`) ainda
  aponta pro repo antigo. Repontar pra `-portal-comissoes` com *Root Directory*
  `pipeline` (build e start iguais). Até lá o repo antigo continua servindo, sem risco.

### Fase 4 — quebrar os gigantes — CONCLUÍDA em 03/09/2026
`server.py` (6.578 linhas) virou `app/nucleo.py` (1.609: config, Flask, banco,
autenticação e tudo que mais de uma área usa) + `app/areas/*.py` (dez áreas:
simulador, contas, auditoria, rh, metas_bonus, expedicao, carros, marketing,
retomada, nuvem) + um `server.py` de 79 linhas que só importa tudo na ordem de
sempre e sobe. O corte foi por camada CALCULADA no AST (nome usado por outra
seção vai pro núcleo, com fecho transitivo), não pelos cabeçalhos de comentário —
eles não batiam com as dependências reais. Texto de cada função idêntico ao
original. Sem blueprints, de propósito: blueprint prefixa o nome do endpoint e
isso mudaria `url_for` e qualquer coisa chaveada por endpoint; módulos comuns
registrando no mesmo `app` preservam as 105 rotas com os mesmos nomes.

Nomes que o núcleo só define num dos modos (`_db_*`, `_PgJson`, `ler_json`…,
dentro de `if DATABASE_URL:`) são ligados nas áreas um a um por `hasattr`, como
no original: o que não existe no modo atual fica sem ligar, igual antes.

`admin.html` → `admin.css` (794 linhas) + `admin.js` (5.045); `index.html` →
`index.css` + `index.js`. Recompor o HTML a partir dos arquivos novos devolve o
original byte a byte. `checar_js.py` passou a validar os `.js` novos.

Provas: mapa de rotas idêntico (105/105); 15 endpoints com o mesmo status e hash
de antes; zero nome global sem definição em qualquer módulo (symtable); sobe em
modo Postgres como no Render; as duas telas abrem no navegador sem erro de
console. Único ajuste fora do `app/`: `importar_comissao_lucas.py` passou a
importar `montar_venda` de `areas.contas` (não é mais atributo de `server`).

### Fase 5 — o banco de conversas
`mensagens.raw` sai pra um `vendas_raw.db` de arquivo, só leitura. O `vendas.db`
cai pra ~160 MB, o backup diário fica 2x mais leve e o sync mais rápido.

### Fase 6 — git nos dois que faltam
`painel-metas` e `portal-pecas` ganham repo local (dois minutos cada).

## O que NÃO fazer
Reescrever. O código tem comentários que explicam decisões de negócio ("Confirmado
pelo gestor em 28/08"), regras de arredondamento e armadilhas já vividas. Reescrever
joga isso fora. O plano acima move e desduplica — não reinventa.
