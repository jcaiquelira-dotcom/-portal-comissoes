# -*- coding: utf-8 -*-
"""Chama TODAS as rotas GET do portal com tres sessoes — sem login, gestor e
vendedor — e acusa qualquer 500 ou excecao. Nao publique sem rodar isto.

Por que existe: em 03/09/2026 a varredura das rotas sem login passava limpa
(15 endpoints com o mesmo hash de antes) e o Painel Geral do gestor estava
quebrado desde a vespera — "day is out of range for month" no rateio da
Shopee, no primeiro mes de 30 dias desde que aquele codigo nasceu. Sem login,
a rota devolve 401 antes de chegar no bug. Este script entra.

Como entra: forja a sessao do Flask pelo test_client (session["admin"] = True
e session["vendedor_id"] = <primeiro vendedor ativo>). Nao precisa de senha
nenhuma. Le do banco de producao (DATABASE_URL do ambiente, ou
segredos/database_url.txt) — so GET, nada e gravado por esta ferramenta,
embora rotas que mantem cache possam gravar cache, como fariam num acesso
normal.

Uso:
    python ferramentas/checar_rotas.py            # tabela completa
    python ferramentas/checar_rotas.py --so-erros # so o que deu 500/excecao
Sai com 1 se alguma rota estourar.
"""
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py

# Rotas que precisam de parametro pra fazer sentido: sem ele devolvem 4xx de
# proposito, e isso NAO e erro. Deixadas aqui pra tabela nao assustar.
ESPERADO_4XX = {
    "/api/admin/desempenho": "precisa de ?vendedor=",
    "/api/admin/exportar-mes-xlsx": "precisa de ?mes=",
}


def main() -> int:
    so_erros = "--so-erros" in sys.argv
    os.environ["DATABASE_URL"] = C.url_banco()
    import server  # noqa: E402  (precisa do DATABASE_URL acima)
    app = server.app
    app.config["TESTING"] = True   # excecao sobe em vez de virar 500 mudo

    def cliente(papel, vid=None):
        c = app.test_client()
        with c.session_transaction() as s:
            if papel == "admin":
                s["admin"] = True
            if papel == "vend":
                s["vendedor_id"] = vid
        return c

    adm = cliente("admin")
    r = adm.get("/api/admin/vendedores")
    lista = r.get_json() if r.status_code == 200 else []
    if isinstance(lista, dict):
        lista = lista.get("vendedores") or lista.get("itens") or []
    vid = next((v["id"] for v in lista if isinstance(v, dict) and v.get("id") and v.get("ativo", True)), None)
    clientes = (("anon", cliente("anon")), ("admin", adm), ("vend", cliente("vend", vid)))
    print(f"vendedor de teste: {vid} | rotas GET sem parametro na URL:")

    rotas = sorted(r.rule for r in app.url_map.iter_rules() if "GET" in r.methods and "<" not in r.rule)
    if not so_erros:
        print(f"{'rota':<44} {'anon':>5} {'admin':>5} {'vend':>5}  ms(admin)  obs")
    ruins = []
    for rota in rotas:
        status, ms, obs = [], 0, ESPERADO_4XX.get(rota, "")
        for nome, c in clientes:
            t = time.time()
            try:
                rr = c.get(rota)
                st, corpo = rr.status_code, rr.data
            except Exception as e:  # com TESTING, o 500 chega aqui
                st, corpo = "EXC", f"{type(e).__name__}: {e}".encode()
            if nome == "admin":
                ms = int((time.time() - t) * 1000)
                if st == 200 and "json" in (rr.content_type or ""):
                    try:
                        j = json.loads(corpo)
                        if isinstance(j, dict) and j.get("erro"):
                            obs = "erro no JSON: " + str(j["erro"])[:60]
                    except Exception:
                        obs = "JSON invalido"
            if st == "EXC" or (isinstance(st, int) and st >= 500):
                ruins.append((rota, nome, st, corpo[:200].decode("utf-8", "replace")))
            status.append(st)
        if not so_erros:
            print(f"{rota:<44} {str(status[0]):>5} {str(status[1]):>5} {str(status[2]):>5}  {ms:>8}  {obs}")

    print(f"\n{len(rotas)} rotas x 3 sessoes | com 500/excecao: {len(ruins)}")
    for rota, nome, st, corpo in ruins:
        print(f"  ERRO {rota} ({nome}): {st} {corpo}")
    return 1 if ruins else 0


if __name__ == "__main__":
    C.saida_utf8()
    sys.exit(main())
