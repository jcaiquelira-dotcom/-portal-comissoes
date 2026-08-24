import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "vendas.db"
OUT_PATH = ROOT / "dataset.json"

AGENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": "Flávia",
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": "Matheus",
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": "Gustavo",
    "edac79e2-5f58-443a-af8f-ad6c3fbdc148": "Comercial",
}

PAGAMENTO = [
    "pix", "comprovante", "paguei", " pago", "pago.", "pago!", "pago,",
    "transferi", "transferência", "transferencia", "chave pix", "caiu aqui",
    "pagamento confirmado", "valor pago",
]
LOGISTICA = [
    "motoboy", "retirar", "retirada", "retire", "endereço", "endereco",
    "entrega", "entregar", "entregue", "correios", "transportadora",
    "código de rastreio", "codigo de rastreio", "rastreio", "buscar na loja",
    "vou buscar", "sedex", "loggi",
]

# motivos de nao-conversao (heuristica por palavra-chave, aproximacao)
R_SEM_ESTOQUE = [
    "não tenho", "nao tenho", "não temos", "nao temos", "não vou ter", "nao vou ter",
    "já vendi", "ja vendi", "não vamos ter", "nao vamos ter", "esgotado",
    "não tem disponível", "nao tem disponivel", "infelizmente não", "infelizmente nao",
    "não temos essa", "não temos peças",
]
R_PRECO = [
    "muito caro", "tá caro", "ta caro", "desconto", "abaixa", "mais em conta",
    "baixar o valor", "salgado", "consegue melhorar", "último preço", "ultimo preco",
    "reduzir o valor", "fazer um preço", "fazer um preco", "valor muito alto",
]
R_FRETE = ["frete"]
R_PAGAMENTO_FLEX = [
    "parcela", "parcelar", "cartão", "cartao", "boleto", "à vista", "a vista",
    "dividir o pagamento",
]

# categoria de peca mencionada (heuristica por palavra-chave, em ordem de prioridade)
CATEGORIAS_PECA = [
    ("Motor", ["motor"]),
    ("Para-choque", ["para-choque", "parachoque", "pára-choque", "paralama"]),
    ("Farol/Lanterna", ["farol", "lanterna"]),
    ("Câmbio", ["câmbio", "cambio", "caixa de marcha", "caixa marcha"]),
    ("Porta", [" porta ", "porta diant", "porta tras"]),
    ("Injeção/Turbo", ["turbina", "turbo", "bico injetor"]),
    ("Suspensão", ["suspensao", "suspensão", "amortecedor", "manga de eixo"]),
    ("Multimídia", ["multimidia", "multimídia", "central multimidia"]),
    ("Cabeçote", ["cabeçote", "cabecote"]),
    ("Teto solar", ["teto solar", "teto panoramico", "teto panorâmico"]),
]


def contem(texto, termos):
    return any(t in texto for t in termos)


def main():
    conn = sqlite3.connect(SQLITE_PATH)

    links_site = set(
        sid for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
            "AND text LIKE '%nevadaautopecas.com.br%'"
        )
    )
    ig_organico = set(
        sid for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
            "AND text='Vim do Instagram!'"
        )
    )

    textos_por_sessao = defaultdict(list)
    imagem_cliente = defaultdict(int)
    bot_textos = defaultdict(lambda: defaultdict(int))
    primeira_resposta_humana = {}
    # comportamento do vendedor (so mensagens humanas, nao do bot)
    msgs_vendedor = defaultdict(int)
    midia_vendedor = defaultdict(int)
    textos_vendedor = defaultdict(list)
    ultima_direcao = {}

    for sid, direction, tipo, texto, created_at, origin_json in conn.execute(
        "SELECT session_id, direction, type, text, created_at, raw FROM mensagens ORDER BY created_at ASC"
    ):
        if texto:
            textos_por_sessao[sid].append(texto.lower())
        if direction == "FROM_HUB" and tipo in ("IMAGE", "DOCUMENT"):
            imagem_cliente[sid] += 1
        ultima_direcao[sid] = direction
        if direction == "TO_HUB":
            d = json.loads(origin_json)
            if d.get("origin") == "BOT" and texto:
                bot_textos[sid][texto] += 1
            if d.get("userId"):
                if sid not in primeira_resposta_humana:
                    primeira_resposta_humana[sid] = created_at
                msgs_vendedor[sid] += 1
                if tipo in ("IMAGE", "VIDEO"):
                    midia_vendedor[sid] += 1
                if texto:
                    textos_vendedor[sid].append(texto.lower())

    loop_sessoes = set()
    for sid, contagens in bot_textos.items():
        if any(n >= 3 for n in contagens.values()):
            loop_sessoes.add(sid)

    # cliente novo x recorrente: primeira sessao de cada contato (por ordem cronologica) e "novo"
    contatos_ja_vistos = set()
    novo_por_sessao = {}
    for sid, created_at, contact_id in conn.execute(
        "SELECT id, created_at, contact_id FROM sessoes ORDER BY created_at ASC"
    ):
        if not contact_id:
            novo_por_sessao[sid] = None
        elif contact_id in contatos_ja_vistos:
            novo_por_sessao[sid] = "recorrente"
        else:
            contatos_ja_vistos.add(contact_id)
            novo_por_sessao[sid] = "novo"

    dataset = []
    for sid, created_at, ended_at, status, user_id, utm, origin_sessao in conn.execute(
        "SELECT id, created_at, ended_at, status, user_id, utm, origin FROM sessoes"
    ):
        if utm:
            fonte_utm = json.loads(utm).get("source")
            canal_cod = "AF" if fonte_utm == "FACEBOOK" else "AI" if fonte_utm == "INSTAGRAM" else "AO"
        elif sid in links_site:
            canal_cod = "S"
        elif sid in ig_organico:
            canal_cod = "I"
        else:
            canal_cod = "D"

        textos = textos_por_sessao.get(sid, [])
        todo_texto = " ".join(textos)
        pag = contem(todo_texto, PAGAMENTO)
        log = contem(todo_texto, LOGISTICA)
        img = imagem_cliente.get(sid, 0) > 0
        if (pag or img) and log:
            conv = "P"
        elif pag or log:
            conv = "R"
        else:
            conv = "N"

        rt_min = None
        if sid in primeira_resposta_humana:
            try:
                t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(primeira_resposta_humana[sid].replace("Z", "+00:00"))
                delta = (t1 - t0).total_seconds() / 60
                if delta >= 0:
                    rt_min = round(delta, 1)
            except (ValueError, AttributeError):
                pass

        data_str = created_at[:10] if created_at else None
        vendedor = AGENTES.get(user_id, "N/A" if not user_id else "Outro")

        atencao = (rt_min is None) or (rt_min is not None and rt_min > 240) or (sid in loop_sessoes)
        motivos = []
        if conv != "P":
            if contem(todo_texto, R_SEM_ESTOQUE):
                motivos.append("E")
            if atencao:
                motivos.append("A")
            if contem(todo_texto, R_PRECO):
                motivos.append("P")
            if contem(todo_texto, R_PAGAMENTO_FLEX):
                motivos.append("G")
            if contem(todo_texto, R_FRETE):
                motivos.append("F")

        texto_com_espacos = " " + todo_texto + " "
        categoria_peca = "Outro/genérico"
        for nome_cat, termos_cat in CATEGORIAS_PECA:
            if contem(texto_com_espacos, termos_cat):
                categoria_peca = nome_cat
                break

        # depois de avisar que nao tem a peca, o vendedor parou ali ou seguiu oferecendo
        # alternativa? (2+ mensagens depois = seguiu). None quando nunca disse que nao tinha.
        comportamento_sem_estoque = None
        msgs_v = textos_vendedor.get(sid, [])
        for i, t in enumerate(msgs_v):
            if contem(t, R_SEM_ESTOQUE):
                comportamento_sem_estoque = "seguiu" if (len(msgs_v) - i - 1) >= 2 else "parou"
                break

        ad_content = None
        ad_source = None
        if utm:
            u = json.loads(utm)
            ad_content = (u.get("content") or "").strip().replace("\n", " ") or None
            ad_source = u.get("source")

        dataset.append({
            "d": data_str,
            "u": vendedor,
            "c": canal_cod,
            "cv": conv,
            "st": status,
            "rt": rt_min,
            "lp": sid in loop_sessoes,
            "mv": motivos,
            "ac": ad_content,
            "as": ad_source,
            "pc": categoria_peca,
            "nv": novo_por_sessao.get(sid),
            "ei": origin_sessao == "Empresa",
            # comportamento do vendedor, pra aba de dicas
            "nm": msgs_vendedor.get(sid, 0),
            "fv": midia_vendedor.get(sid, 0) > 0,
            # abandonada por nos: cliente falou por ultimo e a venda nao saiu
            "ab": ultima_direcao.get(sid) == "FROM_HUB" and conv != "P",
            # comportamento apos dizer "nao tenho": parou ali ou seguiu oferecendo
            "ne": comportamento_sem_estoque,
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, separators=(",", ":"))

    print(f"{len(dataset)} sessoes exportadas para {OUT_PATH}")
    print(f"tamanho do arquivo: {OUT_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
