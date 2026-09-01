# -*- coding: utf-8 -*-
"""Faturamento do Mercado Livre: o que o ML cobra, venda por venda.

Ate aqui o painel sabia quanto entrou (pagamentos aprovados) e nada sobre o
quanto saiu. Faltava tarifa, frete e parcelamento — que num marketplace nao sao
detalhe: em julho foram R$ 79.255,85 de cobrancas contra o faturamento do mes.

A API de Orders o app nao tem permissao pra ler. Esta aqui ele tem, e devolve
mais do que Orders daria: cada linha de cobranca vem com o pedido, a peca, a
categoria e o estado do comprador. Da pra montar tarifa por venda, curva ABC
por peca e venda por estado sem pedir permissao nova.

O que este modulo NAO faz: custo do produto e imposto. Nao existem em API
nenhuma — sao dado do vendedor. Sem eles nao existe lucro, so margem de
contribuicao, e o painel diz isso com essas palavras em vez de chamar de lucro
um numero que nao e.

Uso e limite: a doc do ML pede consumo SEQUENCIAL e desaconselha lote — o dado
e estatico durante o dia. Entao roda uma vez por dia, com pausa entre paginas e
recuo no 429. Um mes tem ~3.000 linhas, ~75 paginas.
"""
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

API = "https://api.mercadolibre.com"
FUSO = timezone(timedelta(hours=-3))
# 1000 e o maximo que o endpoint aceita, e faz diferenca de outra ordem: o
# limitador libera mais ou menos a cada 4 minutos, entao o custo do mes e o
# NUMERO DE PAGINAS, nao o tamanho delas. Com 40 por pagina um mes levava 75
# chamadas (~5 horas); com 1000, tres (~8 minutos).
PAGINA = 1000
PAUSA_ENTRE_PAGINAS = 240
TENTATIVAS_429 = 3


def _sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFKD", (t or "").lower())
                   if not unicodedata.combining(c))


# Cada cobranca do ML vira um destes grupos. A lista e por palavra-chave e nao
# por texto exato de proposito: o ML reescreve os rotulos ("Custo por vender no
# Mercado Livre" ja foi "Tarifa de venda"), e casar exato faria a tarifa sumir
# silenciosamente na primeira mudanca de redacao.
GRUPOS = (
    ("frete",        ("tarifa de envio", "tarifa de devolucao", "custo de envio",
                      "servico de entrega", "mercado envios", "tarifa por devolucao",
                      "envios no mercado livre", "medidas e no peso")),
    ("ads",          ("product ads", "publicidade", "brand ads", "display ads")),
    ("parcelamento", ("parcelamento",)),
    ("tarifa_venda", ("custo por vender", "tarifa de venda", "comissao")),
    ("tarifa_pagamento", ("custo por cobrar", "mercado pago", "taxa de recebimento")),
)

# Estorno tambem vem em espanhol. A primeira coleta trouxe "Anulacion del cargo
# por campana de publicidad de Display Ads" — e como eu so procurava
# "cancelamento", ela entrou SOMANDO em vez de subtraindo, e a linha contou duas
# vezes contra a empresa.
PREFIXOS_ESTORNO = ("cancelamento", "anulacion", "anulacao", "estorno", "devolucao do")


def classificar(texto):
    """Grupo da cobranca e se ela e estorno.

    Estorno vem como uma linha propria comecando com "Cancelamento d...", com
    valor positivo. Somar sem olhar isso inflaria a tarifa do mes com a tarifa
    que o ML devolveu.
    """
    t = _sem_acento(texto)
    estorno = t.startswith(PREFIXOS_ESTORNO)
    for grupo, chaves in GRUPOS:
        if any(c in t for c in chaves):
            return grupo, estorno
    return "outros", estorno


class LimiteAtingido(Exception):
    """O ML disse 'chega por agora'. Nao e erro: e o sinal de guardar o que tem
    e voltar depois. Tratar como falha faria o coletor jogar fora paginas que
    ja custaram cota."""


def _get(caminho, token, tentativas=TENTATIVAS_429):
    espera = 8
    for i in range(tentativas):
        req = urllib.request.Request(API + caminho)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("x-format-new", "true")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if i < tentativas - 1:
                    time.sleep(espera)
                    espera *= 2
                    continue
                raise LimiteAtingido(caminho)
            raise


def _acumulador(anterior=None):
    """Estado da coleta em formato que sobrevive a um JSON.

    defaultdict nao serializa; e a memoria de deduplicacao (quais pedidos ja
    contei) precisa atravessar um disparo pro outro, senao retomar contaria a
    mesma venda de novo — uma venda gera varias linhas de cobranca.
    """
    a = anterior or {}
    return {
        "offset": a.get("offset", 0),
        "linhas": a.get("linhas", 0),
        "total_cobrado": a.get("total_cobrado", 0.0),
        "por_dia": defaultdict(lambda: defaultdict(float),
                               {d: defaultdict(float, g)
                                for d, g in (a.get("por_dia") or {}).items()}),
        "por_item": {k: {**v, "pedidos": set(v.get("pedidos") or [])}
                     for k, v in (a.get("por_item") or {}).items()},
        "por_estado": defaultdict(lambda: {"qtd": 0, "receita": 0.0},
                                  a.get("por_estado") or {}),
        "rotulos": defaultdict(float, a.get("rotulos") or {}),
        "pedidos": set(a.get("pedidos_vistos") or []),
    }


def coletar_periodo(token, chave, anterior=None, max_paginas=None, log=print):
    """Todas as linhas de cobranca de um periodo, agregadas.

    Guarda agregado, nao linha a linha: sao ~3.000 por mes e o painel nunca
    pergunta por uma cobranca especifica — pergunta quanto foi de tarifa no dia,
    qual peca vendeu mais, qual estado comprou. Guardar tudo cru seria pagar
    banco pra armazenar o que ninguem le.
    """
    base = (f"/billing/integration/periods/key/{chave}/group/ML/details"
            f"?document_type=BILL&limit={PAGINA}")

    ac = _acumulador(anterior)
    por_dia, por_item = ac["por_dia"], ac["por_item"]
    por_estado, rotulos, pedidos = ac["por_estado"], ac["rotulos"], ac["pedidos"]
    por_categoria = defaultdict(lambda: {"qtd": 0, "receita": 0.0})
    desconhecidos = defaultdict(float)
    soma_conferencia = ac["total_cobrado"]
    linhas = ac["linhas"]
    offset = ac["offset"]

    total = (anterior or {}).get("total_linhas")
    paginas, parou_no_limite = 0, False
    while True:
        if max_paginas is not None and paginas >= max_paginas:
            break
        try:
            d = _get(f"{base}&offset={offset}", token)
        except LimiteAtingido:
            # Guarda o que tem. Sem isso as paginas ja baixadas iriam pro lixo e
            # a proxima tentativa comecaria do zero — com a mesma cota.
            parou_no_limite = True
            log(f"[faturamento] {chave}: limite do ML na página {offset}; "
                f"guardando o que já veio")
            break
        paginas += 1
        res = d.get("results") or []
        if d.get("total"):
            total = d["total"]
        if not res:
            offset = total or offset
            break

        for l in res:
            ci = l.get("charge_info") or {}
            valor = float(ci.get("detail_amount") or 0)
            rotulo = ci.get("transaction_detail") or "(sem rótulo)"
            grupo, estorno = classificar(rotulo)
            assinado = -valor if estorno else valor
            soma_conferencia += assinado
            linhas += 1
            rotulos[rotulo] += assinado
            if grupo == "outros":
                desconhecidos[rotulo] += assinado

            vendas = l.get("sales_info") or []
            # Sem venda ligada (cobranca de conta, assinatura), a data da
            # cobranca e a unica que existe.
            dia = (vendas[0].get("sale_date_time") if vendas
                   else ci.get("creation_date_time") or "")[:10]
            if dia:
                por_dia[dia][grupo] += assinado

            for v in vendas:
                oid = v.get("order_id")
                if oid and oid not in pedidos:
                    pedidos.add(oid)
                    receita = float(v.get("transaction_amount") or 0)
                    est = v.get("state_name") or "—"
                    por_estado[est]["qtd"] += 1
                    por_estado[est]["receita"] += receita
                    if dia:
                        por_dia[dia]["receita"] += receita
                        por_dia[dia]["vendas"] += 1   # vira int na saida

            itens = [x for x in (l.get("items_info") or []) if x.get("item_id")]
            # Uma cobranca pode cobrir mais de uma peca do mesmo pedido. Jogar o
            # valor inteiro em cada uma faria a tarifa aparecer multiplicada na
            # curva ABC.
            fatia = assinado / len(itens) if itens else 0.0
            for it in itens:
                iid = it["item_id"]
                # Uma venda gera VARIAS linhas de cobranca (tarifa, frete,
                # parcelamento), e todas repetem o mesmo item. Contar unidade a
                # cada linha multiplicaria a peca por 3.
                reg = por_item.setdefault(iid, {
                    "titulo": it.get("item_title") or "",
                    "categoria": (it.get("item_category") or "").split(" > ")[-1],
                    "qtd": 0, "receita": 0.0, "custo_ml": 0.0, "pedidos": set(),
                })
                oid = it.get("order_id")
                if oid is not None and oid not in reg["pedidos"]:
                    reg["pedidos"].add(oid)
                    reg["qtd"] += int(it.get("item_amount") or 0)
                    reg["receita"] += float(it.get("item_price") or 0) * int(it.get("item_amount") or 1)
                reg["custo_ml"] += fatia

        offset += PAGINA
        if offset >= (total or 0):
            break
        time.sleep(PAUSA_ENTRE_PAGINAS)

    completo_parcial = bool(total) and offset >= total
    for iid, reg in por_item.items():
        cat = reg["categoria"] or "—"
        por_categoria[cat]["qtd"] += reg["qtd"]
        por_categoria[cat]["receita"] += reg["receita"]
        # Mesma logica da memoria acima, por peca.
        reg["pedidos"] = [] if completo_parcial else sorted(reg.get("pedidos") or [])
        reg["receita"] = round(reg["receita"], 2)
        reg["custo_ml"] = round(reg["custo_ml"], 2)

    def arred(d):
        return {k: (int(v) if k == "vendas" else round(v, 2)) for k, v in d.items()}

    completo = bool(total) and offset >= total

    return {
        "completo": completo,
        "parou_no_limite": parou_no_limite,
        "offset": offset,
        "total_linhas": total,
        "linhas": linhas,
        "pedidos": len(pedidos),
        "total_cobrado": round(soma_conferencia, 2),
        # Memoria de deduplicacao: so enquanto falta retomar. Terminado o mes
        # ela vira peso morto (milhares de ids que ninguem le).
        "pedidos_vistos": ([] if completo else sorted(pedidos)),
        "por_dia": {d: arred(g) for d, g in sorted(por_dia.items())},
        "por_estado": {k: {"qtd": v["qtd"], "receita": round(v["receita"], 2)}
                       for k, v in sorted(por_estado.items(),
                                          key=lambda x: -x[1]["receita"])},
        "por_categoria": {k: {"qtd": v["qtd"], "receita": round(v["receita"], 2)}
                          for k, v in sorted(por_categoria.items(),
                                             key=lambda x: -x[1]["receita"])[:40]},
        # Curva ABC: so as 60 primeiras. O resto e cauda longa que ninguem abre,
        # e a lista inteira sao milhares de anuncios.
        # Enquanto falta retomar, guarda todos: cortar no top-60 agora jogaria
        # fora o acumulado de uma peca que ainda vai subir nas paginas seguintes.
        "top_itens": dict(sorted(por_item.items(), key=lambda x: -x[1]["receita"])
                          [:60 if completo else 100000]),
        "rotulos": arred(rotulos),
        # Rotulo que nao caiu em nenhum grupo aparece aqui em vez de sumir
        # dentro de "outros": e assim que a gente descobre que o ML criou uma
        # cobranca nova.
        "nao_classificados": arred(desconhecidos),
    }


def sincronizar(ler_cred, ler_atual, gravar, meses=2, log=print):
    """Atualiza os ultimos `meses` periodos. Um por vez, com pausa."""
    # Usa o access_token que o coletor de hora em hora ja mantem fresco. Nao
    # renova aqui de proposito: o refresh_token do ML rotaciona a cada uso, e
    # dois processos renovando a mesma credencial derrubam um ao outro.
    cred = ler_cred()
    if not cred:
        raise RuntimeError("sem credencial do ML")
    token = cred.get("access_token")

    d = _get("/billing/integration/monthly/periods"
             "?group=ML&document_type=BILL&limit=6&offset=0", token)
    periodos = [p for p in (d.get("results") or []) if p.get("key")][:meses]

    atual = ler_atual() or {}
    guardados = atual.get("periodos") or {}
    for p in periodos:
        chave = p["key"]
        anterior = guardados.get(chave[:7])
        # Mes fechado e completo nao se busca de novo: o numero nao muda mais e
        # cada pagina custa cota que o mes aberto precisa.
        if anterior and anterior.get("completo") and anterior.get("status") == "CLOSED":
            log(f"[faturamento] {chave[:7]}: fechado e completo, pulando")
            continue
        # Mes aberto ja completo recomeca do zero: chegou cobranca nova desde
        # ontem, e continuar do fim perderia tudo que entrou no meio.
        if anterior and anterior.get("completo"):
            anterior = None
        dados = coletar_periodo(token, chave, anterior=anterior, log=log)
        cobrado_ml = round(float(p.get("amount") or 0), 2)
        dados["periodo"] = p.get("period") or {}
        dados["status"] = p.get("period_status")
        # Conferencia contra o numero que o proprio ML fecha no periodo. Se
        # divergir, o painel mostra a diferenca em vez de esconder: somatorio
        # que nao bate quase sempre e cobranca nova que eu ainda nao classifico.
        dados["cobrado_ml"] = cobrado_ml
        dados["diferenca"] = round(dados["total_cobrado"] - cobrado_ml, 2)
        guardados[chave[:7]] = dados
        log(f"[faturamento] {chave[:7]}: cobrado {cobrado_ml} | somei "
            f"{dados['total_cobrado']} | dif {dados['diferenca']}")
        time.sleep(2)

    # So os 6 meses mais recentes: historico mais antigo nao muda e ocupa banco.
    guardados = dict(sorted(guardados.items())[-6:])
    gravar({"gerado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
            "periodos": guardados})
    return f"{len(periodos)} período(s) atualizado(s)"


def iniciar(ler_cred, ler_atual, gravar, log=print):
    """Thread diaria. Roda no servidor pra nao depender do PC da loja ligado.

    Uma vez por dia porque e o que a doc do ML pede, e porque o dado nao muda
    durante o dia. A primeira volta espera 3 minutos: subir o portal e ja sair
    consumindo cota atrasaria o que o gestor abre no primeiro minuto.
    """
    import threading

    def ciclo():
        time.sleep(180)
        while True:
            try:
                log("[faturamento] " + sincronizar(ler_cred, ler_atual, gravar, log=log))
            except Exception as e:                      # nunca derruba o portal
                log(f"[faturamento] falhou: {type(e).__name__}: {e}")
            time.sleep(24 * 60 * 60)

    threading.Thread(target=ciclo, daemon=True).start()
