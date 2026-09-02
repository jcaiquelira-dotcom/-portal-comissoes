# -*- coding: utf-8 -*-
"""Pedidos do painel Vaapt (loja nevadaautopecas.com.br) -> chave `site_conta`.

Ate 02/09/2026 esta era a UNICA fonte do painel sem coletor: alguem abria o
Vaapt, somava na mao e mandava pelo endpoint de upload. Resultado previsivel —
o dado ficou parado em 27/08 enquanto ML, Shopee e Meta seguiam atualizando
sozinhos, e o gestor via a fatia do site menor do que era sem nada avisando.

POR QUE RASPAGEM E NAO API. O Vaapt e WordPress e expoe REST API, mas nenhuma
rota de pedidos: os namespaces proprios sao `motora/v1` (data e coupons) e
`webhook/v1` (que RECEBE eventos, nao entrega). Nao e WooCommerce — a rota
`wc/v3/orders` responde 404. Entao a unica saida hoje e ler a tela. Se o Vaapt
liberar API de pedidos, o unico trecho que muda e `coletar()`; `agregar()` e
`gravar()` continuam iguais.

O SEGREDO NAO MORA AQUI. `segredos/vaapt.json` fica fora do git, no mesmo lugar
das outras credenciais do portal, e e voce quem preenche:

    {"base": "https://vaapt.site", "usuario": "...", "senha": "..."}

CRITERIO DE "PAGO". Conta `Pago` e `Concluido`, que e o que o painel chama de
pedido pago — mesma regra do ML (pagamento aprovado, nao pedido feito).
`Reembolsado` fica FORA: o dinheiro entrou e voltou, e somar isso inflaria o
faturamento do dia em que a venda foi feita. `Pendente`, `Cancelado` e
`Mal Sucedido` nunca foram receita.

A serie so cresce: cada rodada atualiza os dias que leu e preserva o resto,
igual ao importador da Shopee. Reprocessar um periodo nunca duplica venda,
porque a chave e o dia.

Uso:
    set DATABASE_URL=postgresql://...
    python scripts/coletar_vaapt.py --seco      # le e mostra, nao grava
    python scripts/coletar_vaapt.py             # le e grava
    python scripts/coletar_vaapt.py --dias 15   # so os ultimos 15 dias
"""

import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

FUSO = timezone(timedelta(hours=-3))
RAIZ = Path(__file__).resolve().parent.parent
SEGREDO = RAIZ / "segredos" / "vaapt.json"

# Status que contam como dinheiro que entrou e ficou. Comparados sem acento e
# em minusculo, porque a tela escreve "Concluído" com acento e "Pago" sem.
PAGOS = {"pago", "concluido"}

# Teto de paginas: a paginacao do Vaapt ia ate 60 em 02/09/2026 (~61 pedidos
# por pagina). O teto existe pra um bug de paginacao nao virar laco infinito
# batendo no site da loja a noite inteira.
MAX_PAGINAS = 200
PAUSA = 0.4          # respiro entre paginas; nao ha pressa e o site e da loja


def segredo() -> dict:
    if not SEGREDO.exists():
        raise SystemExit(
            f"{SEGREDO} nao existe. Crie com:\n"
            '  {"base": "https://vaapt.site", "usuario": "...", "senha": "..."}'
        )
    d = json.loads(SEGREDO.read_text(encoding="utf-8"))
    faltando = [k for k in ("base", "usuario", "senha") if not d.get(k)]
    if faltando:
        raise SystemExit(f"faltam campos em {SEGREDO.name}: {', '.join(faltando)}")
    return d


def entrar(base: str, usuario: str, senha: str):
    """Faz login no WordPress e devolve um opener que carrega a sessao.

    O WP exige o cookie de teste na mesma requisicao do login (`testcookie=1`);
    sem ele a resposta e a propria tela de login de novo, com uma mensagem que
    fala em cookie desativado — erro que parece credencial errada e nao e.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "portal-nevada/1.0 (coletor interno)")]

    dados = urllib.parse.urlencode({
        "log": usuario,
        "pwd": senha,
        "wp-submit": "Acessar",
        "redirect_to": f"{base}/pedidos/",
        "testcookie": "1",
    }).encode("utf-8")

    with opener.open(f"{base}/wp-login.php", dados, timeout=60) as r:
        corpo = r.read().decode("utf-8", "replace")

    # O WP responde 200 nos dois casos — sucesso e falha. Quem diz a verdade e
    # o cookie de sessao, nao o status.
    if not any(c.name.startswith("wordpress_logged_in") for c in jar):
        if "senha" in corpo.lower() or "incorret" in corpo.lower():
            raise SystemExit("login recusado: usuario ou senha do Vaapt errados.")
        raise SystemExit("login nao completou (sem cookie de sessao do WordPress).")
    return opener


class LinhasPedidos(HTMLParser):
    """Le a tabela de /pedidos/ e devolve uma linha por pedido.

    As colunas sao localizadas pelo CABECALHO, nao por posicao fixa: a tela ja
    reordenou coluna uma vez (os links de ordenacao mudam `orderby`), e um
    parser preso ao indice quebraria calado, somando cidade como valor.
    """

    def __init__(self):
        super().__init__()
        self.linhas, self._cab, self._celulas = [], None, None
        self._buf, self._dentro = [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._celulas = []
        elif tag in ("td", "th") and self._celulas is not None:
            self._buf, self._dentro = [], True
        elif tag == "br" and self._dentro:
            self._buf.append(" ")

    def handle_data(self, dado):
        if self._dentro:
            self._buf.append(dado)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._dentro:
            self._celulas.append(" ".join("".join(self._buf).split()))
            self._buf, self._dentro = [], False
        elif tag == "tr" and self._celulas:
            self._fechar_linha(self._celulas)
            self._celulas = None

    def _fechar_linha(self, celulas):
        achatado = [c.strip().lower() for c in celulas]
        # Cabecalho: a linha que traz "status" e alguma coluna de valor.
        if self._cab is None and "status" in achatado:
            self._cab = {}
            for i, nome in enumerate(achatado):
                if nome.startswith("data"):
                    self._cab["data"] = i
                elif nome.startswith("pedido"):
                    self._cab["pedido"] = i
                elif nome == "status":
                    self._cab["status"] = i
                elif "valor" in nome:
                    self._cab["valor"] = i
            return
        if not self._cab or "status" not in self._cab:
            return
        try:
            bruto_data = celulas[self._cab["data"]]
            bruto_valor = celulas[self._cab["valor"]]
            status = celulas[self._cab["status"]]
            pedido = celulas[self._cab.get("pedido", 1)]
        except (IndexError, KeyError):
            return
        d = re.search(r"(\d{2})/(\d{2})/(\d{4})", bruto_data)
        v = re.search(r"([\d.]+,\d{2})", bruto_valor)
        if not d or not v:
            return
        self.linhas.append({
            "data": f"{d.group(3)}-{d.group(2)}-{d.group(1)}",
            "pedido": pedido.lstrip("#").strip(),
            "status": status,
            "valor": float(v.group(1).replace(".", "").replace(",", ".")),
        })


def sem_acento(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(t or "").lower())
                   if not unicodedata.combining(c)).strip()


def coletar(opener, base: str, desde: str | None):
    """Percorre a paginacao ate acabar. Para cedo quando `desde` e dado.

    A lista vem da mais nova pra mais antiga, entao com `--dias` da pra parar
    assim que a pagina inteira for mais velha que o corte — nao ha motivo pra
    baixar 60 paginas todo dia pra atualizar os ultimos cinco.
    """
    vistos, pedidos = set(), []
    for n in range(1, MAX_PAGINAS + 1):
        url = f"{base}/pedidos/?npos={n}"
        try:
            with opener.open(url, timeout=90) as r:
                html = r.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            print(f"  [rede] pagina {n}: {e}")
            break

        p = LinhasPedidos()
        p.feed(html)
        if not p.linhas:
            print(f"  pagina {n}: sem linhas — fim da lista")
            break

        # Fim de paginacao sem erro: alguns paineis repetem a ultima pagina em
        # vez de devolver vazio. Se nenhum pedido e novo, chegamos ao fim.
        novos = [x for x in p.linhas if x["pedido"] not in vistos]
        if not novos:
            print(f"  pagina {n}: so pedidos repetidos — fim da lista")
            break
        for x in novos:
            vistos.add(x["pedido"])
        pedidos += novos
        mais_nova = max(x["data"] for x in novos)
        mais_velha = min(x["data"] for x in novos)
        print(f"  pagina {n:>3}: {len(novos):>3} pedidos  ({mais_velha} a {mais_nova})")

        if desde and mais_nova < desde:
            print(f"  pagina inteira anterior a {desde} — parando")
            break
        time.sleep(PAUSA)
    return pedidos


def agregar(pedidos, desde: str | None):
    serie, fora = defaultdict(lambda: {"qtd": 0, "total": 0.0}), defaultdict(int)
    for x in pedidos:
        if desde and x["data"] < desde:
            continue
        if sem_acento(x["status"]) not in PAGOS:
            fora[x["status"]] += 1
            continue
        serie[x["data"]]["qtd"] += 1
        serie[x["data"]]["total"] = round(serie[x["data"]]["total"] + x["valor"], 2)
    return dict(serie), dict(fora)


def gravar(serie: dict, fonte: str):
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL nao definida.")
    import psycopg2
    from psycopg2.extras import Json
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT valor FROM dados_json WHERE chave='site_conta' FOR UPDATE")
        linha = cur.fetchone()
        antiga = ((linha[0].get("vendas") or {}).get("serie_dia") or {}) if linha else {}
        juntas = {**antiga, **serie}
        cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    ("site_conta", Json({
                        "gerado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
                        "fonte": fonte,
                        "vendas": {"serie_dia": juntas, "serie_desde": min(juntas)},
                    })))
    conn.close()
    return len(juntas)


def main():
    seco = "--seco" in sys.argv
    dias = None
    for a in sys.argv[1:]:
        if a.startswith("--dias="):
            dias = int(a.split("=", 1)[1])
        elif a == "--dias":
            i = sys.argv.index(a)
            dias = int(sys.argv[i + 1])
    desde = (date.today() - timedelta(days=dias)).isoformat() if dias else None

    s = segredo()
    base = s["base"].rstrip("/")
    print(f"entrando em {base} como {s['usuario']}")
    opener = entrar(base, s["usuario"], s["senha"])
    print("sessao aberta\n")

    pedidos = coletar(opener, base, desde)
    if not pedidos:
        raise SystemExit("nenhum pedido lido — nada a fazer.")

    serie, fora = agregar(pedidos, desde)
    dias_ord = sorted(serie)
    total = round(sum(x["total"] for x in serie.values()), 2)
    qtd = sum(x["qtd"] for x in serie.values())

    print(f"\n  {len(pedidos)} pedidos lidos | {qtd} pagos em {len(dias_ord)} dias"
          f" | R$ {total:,.2f}")
    if fora:
        print("  fora do criterio de pago:")
        for st, n in sorted(fora.items(), key=lambda kv: -kv[1]):
            print(f"     {n:>4}  {st}")
    print("\n  ultimos 10 dias com venda paga:")
    for d in dias_ord[-10:]:
        print(f"     {d}  qtd {serie[d]['qtd']:>3}  R$ {serie[d]['total']:>10,.2f}")

    if seco:
        print("\n(--seco: nada gravado)")
        return
    n = gravar(serie, "painel vaapt.site — pedidos pagos (coletor)")
    print(f"\n  gravado site_conta ({n} dias na serie)")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
