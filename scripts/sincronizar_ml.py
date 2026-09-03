"""
Leva a saúde da conta Mercado Livre pro portal, na área do gestor.

Fonte: a mesma credencial do ml-dashboard (Documents/ml-dashboard/ml_auth.json).
ATENÇÃO à regra mais importante deste arquivo: o refresh_token do ML ROTACIONA
a cada uso. O token novo é gravado de volta no ml_auth.json na hora — se isso
falhar no meio, o refresh diário dos artifacts quebra junto. Por isso a troca
de token acontece antes de qualquer outra chamada e o arquivo é reescrito
imediatamente.

O que sobe pro portal (chave ml_conta):
  - reputação oficial (nível, medalha, reclamações/cancelamentos/atrasos 60d)
  - reclamações AGORA (abertas e em mediação)
  - últimos 35 dias de pós-venda por tipo (mediações, devoluções,
    cancelamentos) — janela de 30 dias e mês corrente
  - Product Ads (investimento), se a conta tiver acesso pela API

Uso:
    set DATABASE_URL=postgresql://...
    python scripts/sincronizar_ml.py
    python scripts/sincronizar_ml.py --seco
"""

import io
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py
from caminhos import caminho  # config/caminhos.json — ver app/caminhos.py

AUTH = caminho("ml_auth")
API = "https://api.mercadolibre.com"
FUSO = C.FUSO

MOTIVOS = {
    "PDD9939": "Arrependimento", "PDD9829": "Arrependimento",
    "PDD9949": "Chegou sem funcionar", "PDD9946": "Danificado no transporte",
    "PDD9967": "Outro problema / incompatível", "PDD9944": "Diferente do anunciado",
}


def http(url, token=None, corpo=None, cabecalhos=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (cabecalhos or {}).items():
        req.add_header(k, v)
    dados = None
    if corpo is not None:
        dados = urllib.parse.urlencode(corpo).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, dados, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _cred_do_banco():
    """Credencial no banco (chave segredo_ml). Desde 28/08/2026 ela e a fonte
    unica: o refresh_token do ML rotaciona a cada uso, e com duas copias
    renovando em paralelo uma queima a da outra. Sem DATABASE_URL, cai no
    arquivo local — util pra rodar solto, mas ai o servidor nao pode estar
    renovando ao mesmo tempo."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None, None
    import psycopg2
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT valor FROM dados_json WHERE chave='segredo_ml'")
    linha = cur.fetchone()
    return (linha[0] if linha else None), conn


def _gravar_cred(conn, cred):
    from psycopg2.extras import Json
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    ("segredo_ml", Json(cred)))


def renovar_token():
    cred, conn = _cred_do_banco()
    if cred:
        resp = http(f"{API}/oauth/token", corpo={
            "grant_type": "refresh_token",
            "client_id": cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
        })
        novo = dict(cred)
        novo["refresh_token"] = resp["refresh_token"]
        novo["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        novo["rotacionado_por"] = "script local"
        _gravar_cred(conn, novo)      # grava ANTES de seguir: queda aqui nao perde o token
        conn.close()
        # Espelha no arquivo pra quem ainda le de la nao ficar pra tras.
        try:
            arq = json.loads(AUTH.read_text(encoding="utf-8"))
            arq.update({k: novo[k] for k in ("refresh_token", "last_updated")})
            AUTH.write_text(json.dumps(arq, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return resp["access_token"], novo["user_id"]

    auth = json.loads(AUTH.read_text(encoding="utf-8"))
    resp = http(f"{API}/oauth/token", corpo={
        "grant_type": "refresh_token",
        "client_id": auth["client_id"],
        "client_secret": auth["client_secret"],
        "refresh_token": auth["refresh_token"],
    })
    # O refresh rotacionou: gravar o novo AGORA, antes de qualquer outra coisa.
    auth["refresh_token"] = resp["refresh_token"]
    auth["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    AUTH.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding="utf-8")
    return resp["access_token"], auth["user_id"]


def coletar_reputacao(token, user_id):
    u = http(f"{API}/users/{user_id}", token)
    sr = u.get("seller_reputation") or {}
    met = sr.get("metrics") or {}
    trans = sr.get("transactions") or {}
    return {
        "nivel": sr.get("level_id"),
        "medalha": sr.get("power_seller_status"),
        "vendas_60d": (met.get("sales") or {}).get("completed"),
        "reclamacoes_60d": met.get("claims") or {},
        "atrasos_60d": met.get("delayed_handling_time") or {},
        "cancelamentos_60d": met.get("cancellations") or {},
        "avaliacoes": trans.get("ratings") or {},
        "transacoes_total": trans.get("total"),
    }


def contar(token, user_id, filtro):
    d = http(f"{API}/post-purchase/v1/claims/search?{filtro}&limit=1", token,
             cabecalhos={"x-caller.id": str(user_id)})
    return (d.get("paging") or {}).get("total", 0)


def coletar_pos_venda(token, user_id):
    """Todos os registros dos últimos 35 dias, paginados uma vez, e daí saem a
    janela de 30 dias e o mês corrente — mesma tática do ml-dashboard."""
    registros = []
    for status in ("closed", "opened"):
        offset = 0
        while True:
            d = http(f"{API}/post-purchase/v1/claims/search?status={status}"
                     f"&range=date_created:after:now-35d,before:now"
                     f"&limit=50&offset={offset}", token,
                     cabecalhos={"x-caller.id": str(user_id)})
            dados = d.get("data") or []
            registros.extend(dados)
            offset += 50
            if offset >= (d.get("paging") or {}).get("total", 0) or not dados:
                break

    agora = datetime.now(FUSO)
    corte_30d = (agora - timedelta(days=30)).isoformat()
    mes = agora.isoformat()[:7]

    def resumo(grupo):
        tipos = Counter(r.get("type") for r in grupo)
        motivos = Counter(
            MOTIVOS.get(r.get("reason_id"),
                        "Problema de entrega" if str(r.get("reason_id") or "").startswith("PNR")
                        else "Outros")
            for r in grupo if r.get("type") == "mediations")
        return {
            "mediacoes": tipos.get("mediations", 0),
            "devolucoes": tipos.get("returns", 0),
            "cancel_comprador": tipos.get("cancel_purchase", 0),
            "cancel_vendedor": tipos.get("cancel_sale", 0),
            "motivos": [{"motivo": m, "qtd": q} for m, q in motivos.most_common(5)],
        }

    return {
        "abertas_agora": contar(token, user_id, "status=opened"),
        "mediacoes_agora": contar(token, user_id, "stage=mediation"),
        "dias30": resumo([r for r in registros if (r.get("date_created") or "") >= corte_30d]),
        "mes_atual": resumo([r for r in registros if (r.get("date_created") or "")[:7] == mes]),
    }


def coletar_vendas(token, user_id, desde=None):
    """Faturamento real pelos pagamentos do Mercado Pago (/collections): a
    permissao de Orders o app nao tem, mas o dinheiro aprovado conta a mesma
    historia. So marketplace=MELI e status=approved entram — pagamento de
    maquininha/link fica de fora pra nao misturar com o que ja e Itau no
    portal. Uma paginacao cobre 30 dias e o mes corrente."""
    agora = datetime.now(FUSO)
    inicio = min(agora - timedelta(days=30), agora.replace(day=1, hour=0, minute=0, second=0))
    if desde:
        inicio = datetime.fromisoformat(desde + "T00:00:00-03:00")
    begin = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = agora.strftime("%Y-%m-%dT%H:%M:%SZ")

    pagamentos, offset = [], 0
    while True:
        d = http(f"{API}/collections/search?seller_id={user_id}&limit=50&offset={offset}"
                 f"&range=date_approved&begin_date={begin}&end_date={end}", token)
        pagamentos.extend(x.get("collection") or {} for x in d.get("results") or [])
        offset += 50
        total = (d.get("paging") or {}).get("total", 0)
        if offset >= total or offset >= 20000:
            break

    validos = [c for c in pagamentos
               if c.get("status") == "approved"
               and c.get("operation_type") == "regular_payment"
               and c.get("marketplace") == "MELI"]
    corte_30d = (agora - timedelta(days=30)).isoformat()
    mes = agora.isoformat()[:7]

    def resumo(grupo):
        soma = round(sum(float(c.get("transaction_amount") or 0) for c in grupo), 2)
        return {"pagamentos": len(grupo), "total": soma,
                "ticket": round(soma / len(grupo), 2) if grupo else 0}

    # Serie por dia (fuso de Brasilia — o carimbo do ML vem em -04:00): e ela
    # que deixa o painel somar marketplace em qualquer periodo filtrado, e e o
    # molde que a Shopee vai seguir quando chegar.
    serie = {}
    for c in validos:
        try:
            dia = datetime.fromisoformat(c["date_approved"]).astimezone(FUSO).date().isoformat()
        except (KeyError, ValueError, TypeError):
            continue
        d = serie.setdefault(dia, {"total": 0.0, "qtd": 0})
        d["total"] = round(d["total"] + float(c.get("transaction_amount") or 0), 2)
        d["qtd"] += 1

    return {
        "dias30": resumo([c for c in validos if (c.get("date_approved") or "") >= corte_30d]),
        "mes_atual": resumo([c for c in validos if (c.get("date_approved") or "")[:7] == mes]),
        "fora_meli_30d": len([c for c in pagamentos
                              if c.get("status") == "approved" and c.get("marketplace") != "MELI"]),
        "serie_dia": serie,
        "serie_desde": inicio.date().isoformat(),
    }


def coletar_ads(token, user_id):
    """Product Ads pelo caminho novo (marketplace/advertising). Soma as
    campanhas do periodo; falhou, devolve None e o portal nao mostra o bloco."""
    try:
        adv = http(f"{API}/advertising/advertisers?product_id=PADS", token,
                   cabecalhos={"Api-Version": "1"})
        lista = adv.get("advertisers") or []
        if not lista:
            return None
        a = lista[0]
        base = (f"{API}/marketplace/advertising/{a['site_id']}/advertisers/"
                f"{a['advertiser_id']}/product_ads/campaigns/search")
        ate = datetime.now(FUSO).date()
        de = ate - timedelta(days=30)
        campanhas, offset = [], 0
        while True:
            d = http(f"{base}?limit=50&offset={offset}&date_from={de}&date_to={ate}"
                     f"&metrics=cost,clicks,prints,units_quantity,direct_amount,"
                     f"indirect_amount,total_amount,acos",
                     token, cabecalhos={"Api-Version": "2"})
            campanhas.extend(d.get("results") or [])
            offset += 50
            if offset >= (d.get("paging") or {}).get("total", 0):
                break

        def soma(campo):
            return round(sum(float((c.get("metrics") or {}).get(campo) or 0)
                             for c in campanhas), 2)
        investido = soma("cost")
        receita = soma("total_amount") or (soma("direct_amount") + soma("indirect_amount"))
        return {
            "de": str(de), "ate": str(ate),
            "investido": investido,
            "cliques": int(soma("clicks")),
            "impressoes": int(soma("prints")),
            "vendas_atribuidas": int(soma("units_quantity")),
            "receita_atribuida": receita,
            "acos": round(100 * investido / receita, 1) if receita else None,
            "campanhas_ativas": sum(1 for c in campanhas if c.get("status") == "active"),
            "campanhas": len(campanhas),
        }
    except Exception as e:
        print(f"  Product Ads indisponível ({type(e).__name__}) — seguindo sem ads.")
        return None


def gravar(pacote, url):
    import psycopg2
    from psycopg2.extras import Json
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        # A serie diaria acumula entre execucoes: a rodada de hoje so cobre a
        # janela baixada, e os dias antigos ja gravados nao podem sumir. Dia
        # rebaixado agora substitui o antigo (pega estorno recente); o resto fica.
        cur.execute("SELECT valor FROM dados_json WHERE chave='ml_conta' FOR UPDATE")
        linha = cur.fetchone()
        antiga = ((linha[0].get("vendas") or {}).get("serie_dia") or {}) if linha else {}
        nova = pacote["vendas"].get("serie_dia") or {}
        pacote["vendas"]["serie_dia"] = {**antiga, **nova}
        if pacote["vendas"]["serie_dia"]:
            pacote["vendas"]["serie_desde"] = min(pacote["vendas"]["serie_dia"])
        cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    ("ml_conta", Json(pacote)))
    conn.close()
    print(f"\n  gravado ml_conta ({len(pacote['vendas']['serie_dia'])} dias de serie)")

def main():
    token, user_id = renovar_token()
    print("token renovado e rotacionado no ml_auth.json")

    rep = coletar_reputacao(token, user_id)
    print(f"reputação: {rep['nivel']} / {rep['medalha']} | vendas 60d: {rep['vendas_60d']}")

    pos = coletar_pos_venda(token, user_id)
    print(f"agora: {pos['abertas_agora']} abertas, {pos['mediacoes_agora']} em mediação")
    print(f"30d  : {pos['dias30']['mediacoes']} mediações, {pos['dias30']['devolucoes']} devoluções, "
          f"{pos['dias30']['cancel_comprador']}+{pos['dias30']['cancel_vendedor']} cancelamentos")

    desde = None
    for arg in sys.argv[1:]:
        if arg.startswith("--desde="):
            desde = arg.split("=", 1)[1]
    vendas = coletar_vendas(token, user_id, desde)
    print(f"vendas: mês R$ {vendas['mes_atual']['total']:,.0f} em "
          f"{vendas['mes_atual']['pagamentos']} pagamentos | "
          f"30d R$ {vendas['dias30']['total']:,.0f}")

    ads = coletar_ads(token, user_id)
    if ads:
        print(f"ads  : R$ {ads['investido']} em 30d, {ads['cliques']} cliques")

    pacote = {"gerado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
              "reputacao": rep, "pos_venda": pos, "vendas": vendas, "ads": ads}
    if "--seco" in sys.argv:
        print("\n(--seco: nada gravado)")
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL não definida.")
    gravar(pacote, url)


if __name__ == "__main__":
    C.saida_utf8()
    main()
