# Conectar o Google Ads direto na API

O que isso resolve: hoje o Google Ads chega pelo Windsor, e o plano básico só
deixa **uma fonte conectada por vez** — por isso o Google Ads, o Meta e o
Perfil da Empresa ficam se revezando, e a fonte desligada congela. Puxando o
Google Ads direto, o lugar no Windsor fica livre pro Meta em definitivo.

Custo: **zero**. O acesso Básico da API dá 15 mil operações por dia; o
coletor usa cerca de 5.

---

## O que já está pronto (não precisa fazer nada)

| Arquivo | O que faz |
|---|---|
| `app/google_ads_api.py` | Baixa gasto, cliques, impressões e conversões por dia e por campanha |
| `scripts/autorizar_google_ads.py` | Gera o `refresh_token` uma vez, pelo navegador |

O coletor escreve **os mesmos dois arquivos** que o Windsor escrevia
(`_w_amplo.json` e `_windsor_periodo.json`), no mesmo formato. Nada mais no
projeto muda, e dá pra voltar atrás trocando qual coletor roda.

---

## Passo 1 — Conta de administrador (MCC)

O developer token só é emitido por uma conta de administrador, não pela conta
de anúncios comum.

1. Abra <https://ads.google.com/home/tools/manager-accounts/>
2. **Criar conta de administrador** → nome (ex.: "Nevada Eco Peças"), país
   Brasil, moeda BRL.
3. Dentro dela: **Contas** → **+** → **Vincular conta existente** → informe o
   número da sua conta de anúncios atual.
4. Aceite o convite pela conta de anúncios.

Se você já tem uma MCC, pule para o passo 2.

**Anote:** o número da MCC (formato `123-456-7890`) → é o `login_customer_id`.

---

## Passo 2 — Developer token

1. Dentro da **MCC**: **Ferramentas e configurações** → **Configuração** →
   **API Center**.
2. Preencha os dados da empresa e aceite os termos. O token aparece na hora.
3. Ele nasce com acesso **de teste** — só funciona em conta de teste, não na
   sua. Na mesma tela, clique em **Solicitar acesso Básico**.

No formulário, o que importa é deixar claro que é **uso interno, para
relatório da própria conta**. Sugestão de texto:

> Uso interno. Ferramenta própria que lê os dados de gasto e desempenho das
> nossas campanhas para montar um painel de gestão interno. Não revendemos,
> não distribuímos e não gerenciamos contas de terceiros.

**Prazo: de 1 dia a cerca de uma semana.** É o único passo que não depende de
nós — só do Google. O resto pode ser feito enquanto espera.

**Anote:** o developer token.

---

## Passo 3 — Projeto no Google Cloud

1. <https://console.cloud.google.com/> → **Novo projeto** → nome
   "nevada-google-ads".
2. **APIs e serviços** → **Biblioteca** → busque **Google Ads API** →
   **Ativar**.
3. **APIs e serviços** → **Tela de consentimento OAuth**:
   - Tipo: **Externo**
   - Nome do app, e-mail de suporte, e-mail de contato: os seus
   - Em **Usuários de teste**, adicione **o seu próprio e-mail**
   - Não precisa publicar nem passar por verificação: é só você usando
4. **APIs e serviços** → **Credenciais** → **Criar credenciais** → **ID do
   cliente OAuth**:
   - Tipo: **App da Web**
   - Em **URIs de redirecionamento autorizados**, adicione **os três**:
     - `http://localhost:8765`
     - `http://localhost:8080`
     - `http://localhost:8000`

> **Por que três:** o Google só aceita redirecionar para um endereço cadastrado
> antes. O script usa a 8765 e cai para as outras se ela estiver ocupada —
> cadastrando as três de uma vez, o passo funciona na primeira tentativa.

**Anote:** `client_id` e `client_secret`.

---

## Passo 4 — Autorizar (quando o token for aprovado)

Com o developer token aprovado e as credenciais em mãos:

```bash
cd "C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights" && python scripts/autorizar_google_ads.py
```

Ele pergunta os cinco valores, abre o navegador, você escolhe a conta e
autoriza. O `refresh_token` é gravado em
`portal-comissoes/segredos/google_ads.json`, que fica **fora do git**.

O script não pede e não vê sua senha: quem autentica é o Google, na janela
dele. Aqui só chega o código de autorização.

---

## Passo 5 — Conferir

```bash
cd "C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights" && python app/google_ads_api.py --testar
```

Deve responder com o nome da conta e o gasto dos últimos 7 dias. Compare com o
painel do Google Ads: **os dois têm que bater**. Se não baterem, pare e me
chame antes de trocar o coletor — número que não fecha na origem não melhora
depois.

Batendo, é só rodar:

```bash
cd "C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights" && python app/google_ads_api.py
```

E aí eu troco a chamada no pipeline das 07:30, e o Windsor fica livre pro Meta.

---

## Erros que provavelmente vão aparecer

| Mensagem | O que é |
|---|---|
| `redirect_uri_mismatch` | O `http://localhost:PORTA` não está cadastrado no cliente OAuth (passo 3.4) |
| `DEVELOPER_TOKEN_NOT_APPROVED` | Ainda com acesso de teste — o Básico não saiu |
| `USER_PERMISSION_DENIED` | Falta o `login_customer_id`, ou a conta autorizada não tem acesso à conta de anúncios |
| `CUSTOMER_NOT_FOUND` | `customer_id` com traços — tem que ser só dígitos |
| O Google não devolveu `refresh_token` | A conta já autorizou este app antes. Revogue em <https://myaccount.google.com/permissions> e rode de novo |

---

## O que muda no painel

**Nada nos números que já existem** — o Windsor entrega o gasto correto, e eu
conferi. O que a API traz é:

- O Windsor livre pro Meta em definitivo (fim do rodízio)
- Conversões por tipo (as PMax vinham zeradas)
- Campos novos, sendo o mais útil o **termo de busca**: o que a pessoa digitou
  antes de clicar, que dá pra cruzar com a peça que ela pediu no Totalk

Esse último é o que eu acho que vale mais pro negócio, mas é um passo seguinte
— primeiro a conexão de pé, com os números batendo.
