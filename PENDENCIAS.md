# Pendências — Portal Nevada

Lista viva. Aberta em 02–03/09/2026. Marque `[x]` no que resolver.

---

## 1. Travadas, esperando alguém de fora

### 1.1 ~~Coletor do Vaapt não roda sozinho~~ — resolvido em 03/09
Senha corrigida no `segredos/vaapt.json`; login passou de primeira; dados
idênticos aos da coleta manual. Entrou no `pipeline_diario.bat` com `--dias=15`,
antes do Mercado Livre. **Conferir amanhã** no log a linha "gravado site_conta".
Continua valendo a pedida da API de pedidos ao Vaapt como solução sem raspagem.

### 1.2 Google Search Console — só esperar a propriedade nova popular
Diagnóstico fechado em 03/09, 01:03:
- A propriedade de prefixo `https://nevadaautopecas.com.br/` **foi criada hoje**
  ("Dados em processamento: volte em ~1 dia" na Visão geral). O zero em 365
  dias é isso, não bloqueio.
- **Googlebot passa pelo Cloudflare**: teste ao vivo da Inspeção de URL no
  `sitemap.xml` deu "O URL está disponível para o Google · É possível indexar".
  O "Não foi possível buscar o sitemap" foi status transitório do 1º envio.
- Sitemap `sitemap.xml` **enviado** (índice do WordPress com as páginas de produto).
- Escopo OK, API OK, acesso da conta do Ads OK.
**Próximo passo:** em 2–3 dias rodar `python app/search_console_api.py --testar`;
se vier com impressões, rodar a coleta e a aba de Marketing ganha os termos.
Opcional, definitivo: verificar a propriedade de **domínio** por TXT no DNS
(precisa do acesso ao registrador) — o coletor já prefere ela quando existir.

### 1.3 Google Perfil da Empresa — pedir acesso básico à API (formulário)
Escopo OK, APIs ativadas (03/09). Cota **0 QPM** = projeto ainda não aprovado.
NÃO é pedido de aumento de cota: é o "Application for Basic API Access", pelo
formulário de contato da GBP API (Google). Pede nome da empresa, e-mail de
contato e o número do projeto (`608560018719`). Aprovação: dias a semanas; o
Google avisa por e-mail e a cota sobe pra 300 QPM.
Ponto de partida oficial: https://developers.google.com/my-business/content/prereqs

---

## 2. Decisões do Caique

### 2.1 Crédito da Anthropic: ligar recarga automática
Está desligada. O saldo zerou **duas vezes em uma semana** e o pipeline quebrou
calado nas duas. Console → Faturamento → "Configurar recarga automática".

### 2.2 Revogar as duas chaves de API antigas
- `Varredura` (…8wAA) — expirou em 02/09
- `pipeline-nevada` (…7wAA) — tipo "Pessoal", não funciona com o código
- A que vale é a `pipeline-nevada-ws`, sem expiração, no workspace Default

### 2.3 Classificar as 2.220 conversas anteriores a 29/08
Não afetam a fila de follow-up (que olha 5 dias), mas deixam o histórico de
análise incompleto. Custo ~US$ 11. Saldo em 03/09: ~US$ 4,87.

### 2.4 ~~Confirmar se nevadaautopecas.com.br é loja separada~~ — resolvido
É **uma loja só**. `nevadaecopecas.com.br` não responde mais (nem DNS); o
Analytics mede `nevadaautopecas.com.br` (7.468 sessões/90d). Sobrou um rastro da
Loja Integrada antiga. **Fica pendente:** o rótulo "Site próprio ·
nevadaecopecas.com.br" no portal está desatualizado (server.py, linha das
receitas) — trocar pra nevadaautopecas.com.br.

### 2.5 Conversas presas no bot fora do horário — 22 clientes esperando
Não era timing. Em 03/09 10h: **22 conversas `PENDING`, todas com o bot, nenhuma
com resposta humana** (112 mensagens de cliente, 88 do robô, 0 de vendedor).
A mais antiga é das 20:31 de 02/09 — cliente esperando ~14h. Chegam à noite e
de manhã cedo e ninguém puxa da fila do Totalk. Aparecem no marketing como
"sem atendente". **Decisão:** quem cobre a fila fora do horário, ou o bot avisa
o cliente do horário de retorno? Vale olhar a distribuição por departamento
no Totalk (3 departamentos diferentes nas 22).

---

## 3. Achados do site que pedem ação comercial

Da análise dos 850 pedidos (out/2025 a set/2026, 33,2% não viram dinheiro,
R$ 226 mil). Painel dentro do card do site, no Painel Geral.

### 3.1 Boleto: 15 pedidos, ZERO pagos
Em onze meses. R$ 50.850 que nunca viraram nada. Ou o fluxo está quebrado, ou
ninguém que escolhe boleto volta pra pagar. **Investigar antes de tudo:** se não
der pra consertar, desligar é melhor que oferecer.

### 3.2 Acima de R$1.200 no cartão: 36 pedidos, 7 pagos
R$ 95.453 perdidos — **42% de toda a perda numa combinação só**. No Pix, a mesma
faixa paga 7 de 10 (amostra pequena, n=10, mas a direção é clara).
**Ideia:** destacar o Pix no checkout acima de R$1.200, talvez com desconto.

### 3.3 "Retirar na Loja" com valor alto
Pior modalidade (piso de Wilson 46,7%), e foi o vetor da fraude de 12/09/2025.
61 dos 101 pedidos dessa modalidade eram acima de R$1.200.

### 3.4 Separar "Reembolsado" das falhas
Hoje os 94 reembolsados contam como perda na análise. São diferentes: o dinheiro
entrou e voltou. Separar muda o desenho do problema.

---

## 4. Planilha de fluxo de caixa

### 4.1 AA4 no bloco de sucatas — células órfãs
`AA4` em Jan, Fev, Mar e Maio não é somado por fórmula nenhuma. Em maio são
R$ 171.000. É do lado das **entradas**, assunto separado do APS — não foi mexido,
a pedido do Caique.

### 4.2 Aba "Geral" desatualizada
Fevereiro mostra saídas de R$ 702.511 enquanto a aba do mês soma R$ 723.511 (a
diferença é o Aps lançado depois). O DRE usa a aba do mês, que é a correta — mas
quem olhar a Geral direto vê número errado.

---

## 5. Dívida técnica

### 5.1 `classificar_ia.py`: processar em blocos
Hoje submete todas as tarefas ao pool de uma vez. Se o laço principal morrer, as
chamadas na fila continuam rodando **e cobrando**. Foi assim que ~US$ 8 viraram
pó em 02/09. As três barreiras que adicionei impedem aquela falha específica, mas
não essa classe de problema. O conserto é submeter em blocos de 200–300.

### 5.2 ~~Marcador do pipeline grava antes de rodar~~ — corrigido em 03/09
O wrapper agora espera a rede (até 10 min), usa trava de 2h e só marca o dia
quando o log tem "- fim". Cópia versionada em `scripts/disparar_pipeline.bat`.
**Validado em 03/09 às 10:59:** rodada disparada pelo wrapper novo terminou, a trava
foi removida e o marcador foi gravado **depois** do `fim`. Falta só ver a das 07:30
de amanhã acontecer sem ninguém por perto.

### 5.3 `painel-metas` e `portal-pecas` sem git
Rodam só local, nunca precisaram de repo pra deploy. Mas qualquer edição neles é
definitiva — sem histórico pra voltar atrás. Dois minutos pra criar repo local,
sem precisar subir pro GitHub.

### 5.4 ~~Mudanças não commitadas~~ — commitadas em 03/09 junto com o passo do pipeline

### 5.5 Plano de simplificação — ver `SIMPLIFICACAO.md`
Diagnóstico medido em 03/09 e plano em 6 fases (arrumar a casa → biblioteca
comum → um dono por chave → um repositório → quebrar server.py/admin.html →
banco de conversas). Cada fase é uma sessão. **Fase 2 concluída em 03/09** — um dono por chave: gasto e Perfil são do pipeline
local (nuvem vira reserva de 30h); `ml_conta` é da nuvem (passo local saiu).
Conferir amanhã: `ml_conta` deve seguir com `origem` e hora cheia; `marketing_gasto`
gravado ~08:00 pelo local sem a nuvem sobrescrever.

**Fase 1 concluída em 03/09** — biblioteca comum `app/nevada_comum.py`, duas ondas,
tudo verificado rodando os scripts de verdade. Próxima: Fase 2 (um dono por chave),
que precisa de decisão sua sobre nuvem × local.

**Fase 0 concluída em 03/09.** Órfãos em `ferramentas/` e `_arquivo/` nos dois repos;
`config/caminhos.json` + `app/caminhos.py` em cada um (21 caminhos absolutos viraram
2 arquivos); `ml_auth.json` saiu de Documents pra `segredos/`. Deixado de propósito:
o `.env` do vendas-insights fica onde está até a Fase 3 (um repositório) — mover
agora só trocaria um caminho cravado por outro.

---

## 6. Ideias que ficaram no ar

### 6.1 Custo por lead do site
O portal já tem o gasto do Google Ads (R$ 33.454 até 02/09) e agora tem os leads
do site separados entre pago e orgânico. Falta cruzar: custo por lead pago.

### 6.2 Automatizar a coleta do Vaapt via navegador
Se a API não sair e o login continuar barrado, dá pra rodar a coleta com um
navegador de verdade (Playwright), que passa pelo Cloudflare. Mais pesado, mas
roda desacompanhado.

---

## Resolvido em 02–03/09/2026

- [x] Fila de follow-up restaurada e regenerada — 175 clientes, cobertura até 02/09
- [x] Bug do `classificar_ia.py` que descartava trabalho já pago (3 barreiras)
- [x] R$ 213.450 de investimento externo (APS) que estavam fora do DRE — jun, jul,
      ago, mais os R$ 21 mil de fev e maio
- [x] Débito e Crédito no lugar do Crediário, com 22 vendas históricas convertidas
- [x] Shopee 2 (gabrielanevada) no painel, com card próprio
- [x] Site parou de contar venda de vendedor duas vezes (12 vendas, R$ 8.458,37)
- [x] `site_conta` atualizado até 02/09 (251 dias)
- [x] Leads do site separados entre Google Ads e orgânico, por atribuição do GA4
- [x] Painel de análise de perdas dentro do card do site, ordenado por Wilson
- [x] Chave de API nova, sem expiração, no workspace Default
- [x] `.gitignore`: `.env.*` e `*.bak` (backups de chave estavam a um `git add -A`
      de ir pro GitHub em texto puro)
- [x] Mapa de atendentes unificado em `app/agentes.py` — marketing e desempenho
      creditavam 97 conversas de setembro ao Gustavo, que saiu em 31/08; eram do
      Lucas. O mapa tinha três cópias e só uma sabia da transferência de assento.
- [x] Follow-up: histórico de marcações voltou pras duas telas (03/09). Remontar a
      fila escondia 76 das 78 marcações; agora cada marcação carrega o retrato do
      cliente, o vendedor tem o chip "Histórico" e o gestor vê totais reais + o
      recorte do mês (base pra bonificação). As 78 antigas foram recuperadas.
