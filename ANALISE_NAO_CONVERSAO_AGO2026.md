# Por que não vendemos — agosto/2026

Análise feita em 28/08/2026 sobre `vendas.db`, a pedido do gestor
("desses 5.178, por que só 415 tiveram sinal de compra? quantos a gente não
tinha o produto?" e depois "do que o cliente não respondeu, tem alguma métrica
que se repete?").

**Base:** 5.178 conversas criadas em agosto/2026; 5.048 (97,5%) já
classificadas pela IA (`classificacao_ia`). Todos os números abaixo são desse
recorte, salvo indicação.

---

## Correção de leitura importante

O rótulo `sem_resposta` da classificação **não** significa "o cliente não
respondeu". A definição no prompt (`app/classificar_ia.py`, linha 59) é:

> `'sem_resposta'` (a **loja** nao respondeu ou demorou demais)

Na primeira leitura eu li como "cliente não respondeu" e reportei errado ao
gestor; a correção foi comunicada. Quem sumiu de verdade é `cliente_sumiu`
(13,1%). Isso muda a conclusão: **três quartos das não-vendas se explicam por
falta de peça (48,8%) + demora nossa (24,7%)**.

---

## 1. Motivos de não-venda

| motivo | conversas | % |
|---|---|---|
| `sem_estoque` — não tínhamos a peça | 2.464 | **48,8%** |
| `sem_resposta` — **a loja** demorou ou não respondeu | 1.248 | **24,7%** |
| `cliente_sumiu` | 661 | 13,1% |
| `peca_errada` / incompatível | 241 | 4,8% |
| `nao_aplica` (spam, engano) | 178 | 3,5% |
| `outro` | 169 | 3,3% |
| `preco` | 73 | **1,4%** |
| frete / pagamento / só pesquisando | 14 | 0,3% |

Campo `tinhamos_a_peca`: **não 45,3%**, sim 17,9%, parcial 14,7%,
indefinido 22,1%.

**Preço quase não aparece (1,4%).** O gargalo é estoque e velocidade, não
condição comercial.

## 2. Peças mais pedidas que faltaram

Lidera *bancos do Polo GTS* (6 pedidos). Repetem-se: motor Amarok, motor Cruze
1.8 2014 automático, motor Sonata 2.4, rodas Nivus, lanterna traseira Nivus
GTS, capô Onix 2018, capô Gol G4, cabeçote ix35 2.0, turbina Audi. Depois, uma
cauda longa de itens pequenos de VW/Audi (válvula PCV, termostática,
solenoide).

→ **Essa lista é insumo de compra em leilão.** Query em `notas.sql` abaixo.

## 3. Tempo de resposta — a métrica que se repete

Conversão por faixa de tempo até a **primeira** resposta da loja. Piso 95% =
limite inferior de Wilson (penaliza amostra pequena; aqui todas têm volume).

| respondemos em | n | vendas | taxa | piso 95% |
|---|---|---|---|---|
| até 5 min | 506 | 37 | **7,3%** | 5,4% |
| 5–15 min | 560 | 27 | 4,8% | 3,3% |
| 15–30 min | 504 | 22 | 4,4% | 2,9% |
| 30–60 min | 515 | 19 | 3,7% | 2,4% |
| 1–3 h | 778 | 26 | 3,3% | 2,3% |
| 3–10 h | 405 | 9 | 2,2% | 1,2% |
| mais de 10 h | 1.491 | 37 | 2,5% | 1,8% |
| **nunca respondemos** | **289** | 1 | **0,3%** | 0,1% |

Curva monotônica: **responder em 5 min converte ~3x mais que em 3 horas.**

Comparação **não circular** (não usa o rótulo da IA para definir os grupos):
quem **vendeu** teve mediana de **33 min** até a primeira resposta; quem não
vendeu, **79 min**.

> Cuidado ao reusar: a mediana de 174 min do grupo `sem_resposta` **é
> parcialmente circular** — a IA classificou assim justamente por ver demora.
> Use o corte vendeu-vs-resto, não esse.

## 4. Onde a demora se concentra

% que espera **mais de 1 hora**, por hora de chegada do cliente:

- **12h–14h: 49–55%** — almoço, o pior buraco controlável
- **17h: 61%** — fim do dia
- **8h–9h: 52–59%** — fila acumulada da noite
- **15h–16h: 24–30%** — melhor desempenho do dia
- 18h–7h: 100% — fora do expediente, esperado

**35% dos leads chegam fora do expediente** (1.759 de 5.048) e convertem
**2,3%** contra **4,2%** dos que chegam no horário.

## 5. Por vendedor

Conversões estatisticamente **iguais** (intervalos se sobrepõem) — o que
difere é o tempo:

| | leads | conv. | piso 95% | mediana | >1h | nunca respondeu |
|---|---|---|---|---|---|---|
| Matheus | 1.639 | 4,0% | 3,1% | 131 min | 60% | 2% |
| Flávia | 1.981 | 3,4% | 2,7% | **64 min** | 45% | **13%** |
| Gustavo | 1.427 | 3,2% | 2,4% | 84 min | 56% | 0% |

Dois problemas distintos: Flávia é a mais rápida mas deixa **13% sem resposta
nenhuma** (~257 clientes); Matheus e Gustavo não abandonam ninguém, mas
demoram mais.

---

## Ações sugeridas (em ordem de valor)

1. **Cobrir 12h–14h com escala** — maior buraco controlável.
2. **Começar o dia pela fila da madrugada**, não pelo que chega às 8h.
3. **Zerar o "nunca respondido"** — é o que o painel *Esperando você*
   (`app/monitor_atendimento.py`) resolve. Fatia mais barata: não exige
   estoque nem preço, só alguém responder.
4. **Lista de peças em falta → pauta de compra em leilão.**

## Pendente

Levar isso para o painel de marketing: card "Por que não vendemos" + curva de
tempo de resposta, respeitando os filtros de período da tela. O gestor pediu
para guardar e usar mais à frente.

---

## notas.sql — queries para refazer

```sql
-- recorte base
-- FROM sessoes s JOIN classificacao_ia i ON i.session_id = s.id
-- WHERE substr(s.created_at,1,10) BETWEEN '2026-08-01' AND '2026-08-31'

-- motivos
SELECT COALESCE(motivo_nao_venda,'(nulo)'), COUNT(*) ... GROUP BY 1 ORDER BY 2 DESC;

-- peças que faltaram
SELECT LOWER(TRIM(peca_procurada)), COUNT(*) ...
 AND motivo_nao_venda='sem_estoque' AND TRIM(COALESCE(peca_procurada,''))<>''
 GROUP BY 1 ORDER BY 2 DESC;

-- tempo até primeira resposta: s.created_at -> s.first_response_at
-- (NULL em first_response_at = nunca respondemos)
```
