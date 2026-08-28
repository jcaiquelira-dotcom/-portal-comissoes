import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "vendas.db"
OUT_PATH = ROOT / "dataset.json"
IDS_PATH = ROOT / "session_ids.json"

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


# --- o cliente ficou esperando, ou so se despediu? ---
# Sem esse cuidado, toda conversa que termina com "obrigado" viraria "abandonamos o
# cliente" — e na pratica 2 em cada 3 sao so encerramento educado, nao lead perdido.
VAZIO = set("""ok okk okay oks blz blza blzz beleza belezaa obrigado obrigada obg obgg brigado brigada
vlw valeu valew agradeco agradecido agradecemos grato grata ta tah bom certo tudo bem entendi entendido
show perfeito isso sim nao ata ah aa ahh a e eh ate mais tchau abraco falou falo de nada joia tranquilo
fechou fecho amigo amiga irmao mano cara senhor senhora voce vc vcs dia tarde noite oi ola opa boa bao
entao muito mto por favor pf gratidao ha td dmr chefe deus abencoe top otimo excelente legal certeza
uhum sla kk kkk rs rsrs ne so""".split())

INTERROG = re.compile(
    r"\b(qual|quais|quanto|quantos|quanta|tem|teria|tinha|consegue|conseguiria|pode|poderia|"
    r"sabe|quando|onde|como|qto|ainda|disponivel|serve|manda|orcamento|valor|preco)\b"
)

AUTORESPOSTA = re.compile(
    r"nao estamos disponiveis|obrigado por entrar em contato|responderemos|mensagem automatica"
)


def _sem_acento(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9? ]", " ", t)


def cliente_ficou_esperando(tipo: str | None, texto: str | None) -> bool:
    """True quando a ultima mensagem do cliente pedia algo que ficou sem resposta."""
    if tipo in ("IMAGE", "AUDIO", "VIDEO", "DOCUMENT", "CONTACT", "BUTTONS"):
        return True  # mandou foto/audio/clicou e ninguem voltou
    if tipo == "STICKER" or not texto:
        return False
    n = _sem_acento(texto)
    if AUTORESPOSTA.search(n):
        return False  # robo da empresa do proprio cliente
    palavras = [p for p in n.replace("?", " ").split() if p]
    substantivas = [p for p in palavras if p not in VAZIO and not p.isdigit()]
    if not substantivas:
        return False  # so cortesia/saudacao
    if "?" in texto or INTERROG.search(n):
        return True
    return len(substantivas) >= 2


def main():
    conn = sqlite3.connect(SQLITE_PATH)

    # O link do site so indica ORIGEM se aparecer logo no comeco. Link colado no meio da
    # conversa costuma ser o cliente devolvendo um link que o proprio vendedor mandou --
    # contar isso como "veio do site" inflava o canal (auditoria: 38 casos, convertendo
    # 31,6%, contra 11,0% dos que realmente chegaram pelo site).
    links_site = set()
    for (sid,) in conn.execute(
        "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
        "AND text LIKE '%nevadaautopecas.com.br%'"
    ):
        primeiras = conn.execute(
            "SELECT text FROM mensagens WHERE session_id=? AND direction='FROM_HUB' "
            "AND text IS NOT NULL ORDER BY created_at ASC LIMIT 3",
            (sid,),
        ).fetchall()
        if any("nevadaautopecas.com.br" in (t or "") for (t,) in primeiras):
            links_site.add(sid)
    # Parte do que chega "pelo site" e na verdade Google Ads: o link traz gclid /
    # gad_campaignid grudado. Sem separar, anuncio pago do Google era contado como
    # site organico -- eram 990 de 1.704 leads do canal Site.
    MARCAS_GOOGLE = ["gclid=", "gad_source=", "gad_campaignid=", "gbraid=", "wbraid="]
    google_ads = set()
    for sid, texto in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' "
        "AND text LIKE '%nevadaautopecas.com.br%'"
    ):
        t = (texto or "").lower()
        if any(m in t for m in MARCAS_GOOGLE):
            google_ads.add(sid)

    ig_organico = set(
        sid for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
            "AND text='Vim do Instagram!'"
        )
    )

    # Anuncio que chegou sem UTM. O Meta injeta uma frase automatica no clique do
    # anuncio; dessas conversas, 87% a 91% chegam COM utm -- ou seja, a frase e
    # assinatura de anuncio, e quando o utm falta e falha de rastreamento, nao outro
    # canal. Sem isso, 273 leads pagos ficavam contados como "contato direto".
    # (As frases do botao do site sao outras e trazem link junto; ficam de fora.)
    FRASES_META = ["tenho interesse e queria mais informa", "posso ter mais informa"]
    anuncio_sem_rastreio = set()
    for sid, texto in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' "
        "AND type='TEXT' AND text IS NOT NULL"
    ):
        if sid in links_site:
            continue
        t = _sem_acento(texto)
        if any(f in t for f in (_sem_acento(x) for x in FRASES_META)):
            anuncio_sem_rastreio.add(sid)

    textos_por_sessao = defaultdict(list)
    imagem_cliente = defaultdict(int)
    bot_textos = defaultdict(lambda: defaultdict(int))
    primeira_resposta_humana = {}
    # comportamento do vendedor (so mensagens humanas, nao do bot)
    msgs_vendedor = defaultdict(int)
    midia_vendedor = defaultdict(int)
    textos_vendedor = defaultdict(list)
    ultima_direcao = {}
    ultima_msg = {}

    for sid, direction, tipo, texto, created_at, origin_json in conn.execute(
        "SELECT session_id, direction, type, text, created_at, raw FROM mensagens ORDER BY created_at ASC"
    ):
        if texto:
            textos_por_sessao[sid].append(texto.lower())
        if direction == "FROM_HUB" and tipo in ("IMAGE", "DOCUMENT"):
            imagem_cliente[sid] += 1
        ultima_direcao[sid] = direction
        ultima_msg[sid] = (direction, tipo, texto)
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

    # Leitura da conversa pela IA (app/classificar_ia.py). Onde ela existe, o motivo da
    # perda vem daqui: a heuristica de palavra-chave deixava um terco dos atendimentos
    # como "sem sinal claro", e nao sabia distinguir "nao tinhamos" de "o cliente sumiu".
    ia = {}
    try:
        ia = {r[0]: r[1:] for r in conn.execute(
            "SELECT session_id, virou_venda, motivo_nao_venda, tinhamos_a_peca, tipo_cliente "
            "FROM classificacao_ia")}
        print(f"classificacao da IA disponivel para {len(ia)} sessoes")
    except sqlite3.OperationalError:
        print("sem tabela classificacao_ia — exportando so com a heuristica")

    dataset = []
    ids_ordem = []
    for sid, created_at, ended_at, status, user_id, utm, origin_sessao in conn.execute(
        "SELECT id, created_at, ended_at, status, user_id, utm, origin FROM sessoes"
    ):
        if utm:
            fonte_utm = json.loads(utm).get("source")
            canal_cod = "AF" if fonte_utm == "FACEBOOK" else "AI" if fonte_utm == "INSTAGRAM" else "AO"
        elif sid in google_ads:
            canal_cod = "G"   # Google Ads (chegou pelo site, mas via clique pago)
        elif sid in links_site:
            canal_cod = "S"   # site organico
        elif sid in anuncio_sem_rastreio:
            canal_cod = "AX"  # anuncio, rastreio perdido (plataforma nao identificada)
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
        ad_id = None
        if utm:
            u = json.loads(utm)
            ad_content = (u.get("content") or "").strip().replace("\n", " ") or None
            ad_source = u.get("source")
            # sourceId e o id do anuncio no Meta. Agrupar por ele em vez de pelo texto
            # do criativo: o mesmo anuncio roda no Facebook e no Instagram, e casar por
            # palavra do criativo ja jogou 304 leads no anuncio errado (caso #tiguanrline).
            ad_id = u.get("sourceId")

        ids_ordem.append(sid)
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
            "ai": ad_id,
            # leitura da IA: venda / motivo da perda / tinhamos a peca / perfil do cliente
            "iv": bool(ia[sid][0]) if sid in ia else None,
            "im": ia[sid][1] if sid in ia else None,
            "it": ia[sid][2] if sid in ia else None,
            "ip": ia[sid][3] if sid in ia else None,
            "pc": categoria_peca,
            "nv": novo_por_sessao.get(sid),
            "ei": origin_sessao == "Empresa",
            # comportamento do vendedor, pra aba de dicas
            "nm": msgs_vendedor.get(sid, 0),
            "fv": midia_vendedor.get(sid, 0) > 0,
            # cliente ficou esperando: mandou a ultima mensagem, pedindo algo (nao so
            # "obrigado"), e a venda nao saiu. Ver cliente_ficou_esperando() -- sem esse
            # filtro o numero fica ~4x inflado por conversas encerradas com cortesia.
            "ab": (
                ultima_direcao.get(sid) == "FROM_HUB"
                and conv != "P"
                and cliente_ficou_esperando(*ultima_msg.get(sid, (None, None, None))[1:])
            ),
            # comportamento apos dizer "nao tenho": parou ali ou seguiu oferecendo
            "ne": comportamento_sem_estoque,
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, separators=(",", ":"))

    # Os ids ficam num arquivo separado, na mesma ordem do dataset. Sem isso, quem
    # quiser parear dataset.json com o banco precisa adivinhar a ordem das linhas --
    # e "SELECT id FROM sessoes" usa indice de cobertura e devolve em ordem alfabetica,
    # diferente de qualquer query que toque a tabela. Ja gerou analise errada aqui.
    with open(IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(ids_ordem, f, separators=(",", ":"))

    print(f"{len(dataset)} sessoes exportadas para {OUT_PATH}")
    print(f"tamanho do arquivo: {OUT_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
