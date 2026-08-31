# Conectar o Meta Ads direto na API

Mesma ideia do Google Ads, mas **sem fila de aprovação**: ler a sua própria
conta de anúncios não exige App Review. Isso só entra quando um app lê dados de
terceiros.

Tempo estimado: **20 minutos**.

---

## O que já está pronto

`app/meta_ads_api.py` — o coletor. Escreve o **mesmo** `_meta_ads.json` que o
coletor do Windsor escrevia, no mesmo formato, então nada mais no projeto muda
e dá pra voltar atrás trocando qual script roda.

---

## Passo 1 — App no Meta for Developers

1. <https://developers.facebook.com/apps> → **Criar app**
2. Caso de uso: **Outro** → Tipo: **Empresa**
3. Nome: `Painel Nevada` · e-mail de contato: o seu
4. Em **Conta empresarial**, escolha o Gerenciador de Negócios da Nevada

Não precisa configurar produto, nem publicar, nem pedir revisão.

**Anote:** nada. O app só precisa existir.

---

## Passo 2 — Usuário de sistema

É aqui que sai o token. Um usuário de sistema é um "funcionário robô" do
Gerenciador de Negócios — o token dele **não expira**, ao contrário do token de
usuário comum, que morre em ~60 dias e derrubaria o pipeline sem aviso.

1. <https://business.facebook.com/settings> → **Usuários** → **Usuários de
   sistema** → **Adicionar**
2. Nome: `coletor-painel` · Função: **Funcionário** (não precisa ser admin)
3. Com ele selecionado: **Adicionar ativos** → **Contas de anúncios** →
   marque a conta da Nevada → permissão **Ver desempenho**
4. Ainda nele: **Gerar novo token**
   - App: `Painel Nevada`
   - Validade: **Nunca expira**
   - Permissões: marque **`ads_read`**
5. Copie o token **na hora** — ele não aparece de novo

> **Só `ads_read`.** Não marque `ads_management`: ela dá poder de alterar
> campanha e orçamento, e o coletor só lê. Token com permissão a mais é risco
> a mais sem nenhum ganho.

**Anote:** o token.

---

## Passo 3 — Número da conta de anúncios

No mesmo **Configurações do Negócio** → **Contas de anúncios**, o número
aparece abaixo do nome. É só o número, sem o `act_`.

**Anote:** o `ad_account_id`.

---

## Passo 4 — Salvar as credenciais

Crie `portal-comissoes/segredos/meta_ads.json` (a pasta já está fora do git):

```json
{
  "access_token": "COLE_O_TOKEN_AQUI",
  "ad_account_id": "1234567890"
}
```

Ou me manda os dois valores que eu gravo.

---

## Passo 5 — Conferir

```bash
cd "C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights" && python app/meta_ads_api.py --testar
```

Deve responder com o nome da conta, a moeda e o gasto do mês corrente.
**Compare com o Gerenciador de Anúncios: os dois têm que bater.** Se não
baterem, pare e me chame — número que não fecha na origem não melhora depois.

Batendo:

```bash
cd "C:\Users\José Caique\Desktop\ARQUIVOS IA\vendas-insights" && python app/meta_ads_api.py
```

E aí eu troco a chamada no pipeline das 07:30.

---

## Erros que provavelmente vão aparecer

| Mensagem | O que é |
|---|---|
| `Invalid OAuth access token` | Token colado errado ou expirado |
| `(#200) Requires ads_read permission` | Faltou marcar `ads_read` ao gerar o token |
| `(#100) Unsupported get request` sobre a conta | A conta de anúncios não foi atribuída ao usuário de sistema (passo 2.3) |
| Pede **verificação do negócio** | A Meta às vezes exige CNPJ e documento antes de liberar. Aí vira processo de dias, como no Google |

> **Sobre a versão da API:** o script não fixa uma. A Graph API exige a versão
> na URL e aposenta cada uma em ~2 anos; fixar significa que um dia o pipeline
> quebra sozinho. Ele testa da mais nova pra mais velha, usa a primeira que
> responder e grava qual foi. Se o token estiver errado, ele percebe que o
> problema é o token e para na primeira tentativa, em vez de varrer 13 versões.

---

## O que muda no painel

**Nada nos números que já existem.** Conferi a quebra do Windsor contra a
consulta sem quebra: fecha em R$ 0,44 no ano inteiro. O ganho é:

- **O Windsor fica livre.** Com Google Ads e Meta diretos, sobra só o Perfil da
  Empresa nele — fim do rodízio de fontes.
- **Campos novos:** idade, gênero, região e posicionamento separado (feed,
  stories, reels — hoje só temos facebook × instagram), e ações por tipo de
  conversão.

O que **não** resolve: saber se quem comprou veio do Facebook ou do Instagram.
Isso depende do canal de origem no Totalk, que junta os dois como
"Anuncio (FB/IG)". Nenhuma API da Meta conserta isso.
