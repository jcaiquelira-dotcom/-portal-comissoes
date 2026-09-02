# -*- coding: utf-8 -*-
"""Classifica cada item do fluxo de caixa numa conta do plano.

O DRE mostrava os BLOCOS da planilha — "Cartoes", "Diversos", "Fixo" — que nao
sao categorias de custo, sao as caixas em que o autor empilhou as coisas. Ver
"Diversos: 32.808" nao responde nenhuma pergunta.

Aqui cada um dos ~1.055 itens do historico vira uma conta do PLANO_DE_CONTAS,
usando o rotulo escrito e o bloco onde ele estava. O bloco importa: "amaral"
dentro de Sucatas e o mesmo "amaral" dentro de Diversos, mas "ml" dentro de
Marketing e anuncio e "ml" dentro de Parcelas e socios e parcela.

REGRA CENTRAL: o que eu nao souber com seguranca NAO recebe palpite. Vai pra
`None`, aparece no painel como "A classificar" com o rotulo original, e o
gestor decide. Um palpite errado aqui vira custo no grupo errado, margem
errada e decisao errada — e ninguem descobre, porque o numero fecha do mesmo
jeito. Preferir o buraco visivel ao numero bonito.
"""
import re
import unicodedata


def ach(t) -> str:
    """Achata pra comparar: sem acento, minusculo, espaco normalizado."""
    t = "".join(c for c in unicodedata.normalize("NFKD", str(t or "").strip().lower())
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t)


# Modelos que aparecem no historico. Servem pra reconhecer compra de sucata:
# no bloco de Sucatas ou de Diversos, um nome de carro com valor de carro e
# quase sempre o veiculo que entrou pra desmontar.
CARROS = (
    r"virtus|jetta|camaro|mustang|golf|tiguan|bmw|audi|^a3$|^q3$|discovery|"
    r"evoque|mini|sorento|taos|civic|equin[jo]?ox|polo|nivus|passat|punto|"
    r"freelander|prius|sportage|azera|xc60|tera|coro+la|onix|elantra|tucson|"
    r"defender|hrv|ix-35|t-cross|ds3|outlander|vera cruz|panamera|jaguar|"
    r"kia niro|vitara|sta fe|classe c|mercedes|^gla|^cla|mer cla|volvo|g90|"
    r"kombi|suzuki|infinity|c200|^gli$|^x1$|celta|fusion|captiva|rav4|toro|"
    r"strada|etios|^fit$|^ram$|stelantis|^tera$|^doblo$|^saveiro$"
)

# (padrao, conta). Ordem importa: a primeira que casar vence. Regras globais,
# aplicadas em qualquer bloco.
REGRAS = [
    # --- deducoes -----------------------------------------------------------
    (r"^das$|^darf$|^draf$|^daf$|^simples$|^impostos?$|^tricutos?$|^trivut$|"
     r"^das impostos$|^ano \d{4}$", "2.01"),
    (r"devolu|^dev\b|^dev\.", "2.02"),

    # --- custo da peca ------------------------------------------------------
    (r"goiaba|guincho|ghincho|^mafia$", "3.02"),
    (r"amaral|^solda$|retifica|^carlao$|^turbina$", "3.03"),
    (r"^motor |motor stelantis|^cambio|c[aâ]mbio|^carter |^r\. fusion$|"
     r"^2 cambio", "3.04"),
    (r"prodesp|detran|^cadri$|cetesb|^doc$|^doc |lic\.|licenciamento|"
     r"tx fiscaliza", "3.05"),

    # --- comerciais ---------------------------------------------------------
    (r"comiss[ao]", "4.01"),
    (r"^bonus$|^bonific|^boni$|^meta$|^metas$|^meta bonus$|^bonus ", "4.02"),
    (r"^google|mark\.google|seconds|^k2$|^marketing$|fina\. marketing|"
     r"^ml real$|meta ads|google ads", "4.03"),
    (r"be+l[ei]?ve|beliv|belev|bele+v", "4.04"),
    (r"^frete|^correios$|^tm$|^tn$|^flex$|transportadora|^rodobor", "4.05"),
    (r"bolha|papel[ao]|fita adesiva|st?r[ea]ch|^durex$|abracadeira|etiqueta|"
     r"envelope|saquinho|^saco$|^caixas?$|bobina|embalagem|^cx |^cxs ", "4.06"),
    (r"^vaapt$|to ?talk|^erp$|integracao|com\. vaap|^ponto$|pontatel", "4.07"),

    # --- pessoal ------------------------------------------------------------
    (r"^fgts|^inss|sindicato|^sind\b|^sind\.|sincomercio|trein\.sind", "5.02"),
    (r"^almoco|^cafe|convenio|^uniformes?$|^epi$|sta ?helena|san ?helena|"
     r"santa helena|^bones$|^cesta", "5.03"),
    (r"^ferias|ferias$|recis[ao]|rescis[ao]|^rec\.|^13", "5.04"),
    (r"^pro.?labore", "5.05"),

    # --- ocupacao -----------------------------------------------------------
    (r"^aluguel", "6.01"),
    (r"^iptu|^cond\b|^cond\.|condominio|^lello$|^c\.pg$|^cond ", "6.02"),
    (r"^enel$|^ebnel$|energia", "6.03"),
    (r"s+abesp|^agua$", "6.04"),
    (r"^vivo$|^tel$|^tel |telefone|celular|^cel$", "6.05"),
    (r"camera|seguranca|seguraca|^onra seg$|vigilan|^bento", "6.06"),
    (r"^racao$|^limpeza|^pia\b|picina|piscina|jardin", "6.07"),
    (r"eletricista|eletrolest|^telhado$|^pintura$|^pintor$|m\.o\.|mat\.?el[te]|"
     r"material eletric|^serralheiro$|^calha$|^laje\b", "6.08"),

    # --- administrativas ----------------------------------------------------
    (r"contabilidade|acessoria|assessoria", "7.01"),
    (r"^adv|advo|advig|juridic|proc\. judicial|^acordo", "7.02"),
    (r"papelari|^cartorio$|^pasta$|^gabinete|^impressora$", "7.03"),
    (r"^tx (bb|itau)$|^tx |^itau$|^bb$ ?taxa|cartcielo", "7.04"),
    (r"^abcar$|associacao|^sind\.$", "7.05"),
    (r"^festa$|^pascoa$|confra|^presente$|evento|dia dospais|dia dos pais", "7.06"),

    # --- frota --------------------------------------------------------------
    (r"^ipvas?\b|^ipva ", "8.01"),
    (r"^seg\b|^seg\.|^seguro", "8.02"),
    (r"^gas\b|^gas\.|gasolina|combustivel|^flex$", "8.03"),

    # --- financeiro ---------------------------------------------------------
    (r"^consorcios?$", "9.02"),
    (r"^multa|^juros", "9.03"),

    # --- fora do resultado --------------------------------------------------
    (r"^obra$|^concreto$|^cimento$|^aterro$|sondagem|topografo|^terreno|"
     r"terrenos|compensacao terreno|limpeza terreno|pro\.estrutural|"
     r"^projeto$|^planta$|^portas|janela|^escada$|^tabuas$|^piso|borracha p|"
     r"^lote pg$|^deposito$|^quadra$|^telhas$|^gesso", "0.01"),
    (r"^p[1-4]\b|^p[1-4] ", "0.02"),
    (r"^caucao|^calcao", "0.04"),
]

REGRAS = [(re.compile(p), c) for p, c in REGRAS]

# Rotulos que NAO dizem nada sobre o que foi comprado. Nao sao "outros": sao
# ausencia de informacao, e o painel precisa mostrar isso separado do que foi
# de fato classificado como diverso.
SEM_NOME = re.compile(r"^(div|div\.|diversos?|desp|serv|pg|nf|ok|\?+|)$")
SO_PAGAMENTO = re.compile(
    r"^(cartao|cartao p[1-4]|cartao diversos|cartao ri|visa|elo|master|"
    r"bb|itau|pix|dinheiro|cheque|deposito|transferencia)$")

# Regras que valem DENTRO de um bloco so, testadas antes das globais.
#
# "itau" solto poderia ser tarifa bancaria; dentro do bloco de Parcelas ele e
# R$ 10.000 exatos em abril, maio, junho e julho — parcela de financiamento,
# nao tarifa. O mesmo vale pra "bb", "ml" e "cartao" ali: sao pagamento de
# saldo devedor, e amortizacao de principal nao e despesa do mes.
#
# O "(vazio)" desse bloco e a distribuicao aos socios: em agosto ele vale
# R$ 53.793, exatamente o que o DRE ja mostrava como socios antes desta
# mudanca. E a confirmacao de que a coluna e essa mesmo.
REGRAS_POR_BLOCO = {
    "Parcelas e socios": [
        # Rotulo vazio nessa coluna e a distribuicao aos socios: em agosto vale
        # R$ 53.793, exatamente o que o DRE mostrava como socios antes disto.
        (r"^$|^\(vazio\)$|^p[1-4]\b", "0.02"),
        (r"^(bb|itau|ml|cartao|visa|elo|cartcielo)$", "0.03"),
        (r"^consorcios?$", "9.02"),
    ],
}
REGRAS_POR_BLOCO = {b: [(re.compile(p), c) for p, c in regras]
                    for b, regras in REGRAS_POR_BLOCO.items()}

# Conta que cada bloco usa quando o rotulo nao casa com regra nenhuma. So
# existe onde o bloco INTEIRO tem uma natureza so — em Transportes tudo e
# frete, entao um rotulo desconhecido ali ainda e frete com seguranca.
PADRAO_DO_BLOCO = {
    "Sucatas": "3.01",
    "Transportes": "4.05",
    "Embalagem": "4.06",
    "Marketing": "4.03",
    "Impostos": "2.01",
    "Comissoes": "4.01",
    "Devolucoes": "2.02",
    "Caucao e transito": "0.04",
    "Colaboradores": "5.01",     # bloco de folha: desconhecido ali e gente
    "Patrimonial": "0.01",
    "Investimentos": "0.01",
}

# Blocos onde nome de carro significa compra de sucata. Fora deles um nome de
# carro pode ser IPVA, seguro ou peca — e ai a regra normal e que decide.
BLOCOS_DE_SUCATA = {"Sucatas", "Diversos"}
RE_CARROS = re.compile(CARROS)


def classificar(rotulo: str, bloco: str = "") -> str:
    """Conta do plano pra este item, ou None se eu nao souber.

    None nao e falha: e a resposta honesta pra "Div.", pra "Cartao" e pra nome
    de pessoa solto. Quem chama transforma isso em linha visivel no painel.
    """
    r = ach(rotulo)

    # Regra do bloco vem primeiro: e a unica que conhece o contexto, e contexto
    # e o que separa "itau, tarifa do banco" de "itau, parcela de 10 mil".
    for padrao, conta in REGRAS_POR_BLOCO.get(bloco, ()):
        if padrao.search(r):
            return conta

    # Sem nome e meio-de-pagamento: o rotulo nao ajuda, mas o bloco ainda pode.
    # Num bloco de natureza unica — Transportes so tem frete, Sucatas so tem
    # veiculo — a caixa diz o que e mesmo com o item sem rotulo. Nos outros
    # vira buraco, que e o caso de "Diversos" e "Cartoes" e e justamente o que
    # precisa aparecer na tela em vez de sumir dentro de um "outros".
    if SEM_NOME.match(r) or SO_PAGAMENTO.match(r):
        return PADRAO_DO_BLOCO.get(bloco)

    for padrao, conta in REGRAS:
        if padrao.search(r):
            return conta

    # Carro dentro do bloco de sucata: e o veiculo que entrou pra desmontar.
    # Testado DEPOIS das regras pra "ipva ram" e "seg polo" nao virarem compra.
    if bloco in BLOCOS_DE_SUCATA and RE_CARROS.search(r):
        return "3.01"

    return PADRAO_DO_BLOCO.get(bloco)


def rotulo_do_buraco(rotulo: str, bloco: str = "") -> str:
    """Como o item nao classificado se chama no painel.

    Separar os tres tipos importa porque a acao e diferente: 'sem nome' exige
    mudar como se lanca, 'cartao' exige abrir a fatura, e nome proprio exige
    so perguntar pra alguem o que era.
    """
    r = ach(rotulo)
    # O bloco Cartoes nao escreve rotulo em nada: la o vazio nao e "sem nome",
    # e uma fatura inteira que ninguem abriu.
    if bloco == "Cartoes" or SO_PAGAMENTO.match(r):
        return "Cartao e banco — fatura a abrir"
    if SEM_NOME.match(r):
        return "Sem identificacao"
    return "A identificar: " + (str(rotulo).strip() or "(vazio)")
