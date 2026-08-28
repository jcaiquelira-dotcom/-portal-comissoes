"""
Empurra a fila de retomada pro portal-comissoes, que é onde os vendedores
trabalham ela (aba "Retomada", app/static/retomada.html de lá).

Roda depois de `python app/gerar_fila_retomada.py`, que produz o
Fila_CRM_CONFIDENCIAL.json lido aqui.

Uma chave por vendedor (`crm_fila_flavia`, `crm_fila_gustavo`, ...), igual ao
resto do portal, que já guarda `vendas_<id>.json` separado por pessoa: cada
vendedor carrega só a fila dele, e uma fila grande não pesa na tela dos outros.

O que este script NÃO toca: `crm_status_<id>`, onde fica o que o vendedor
marcou. Sincronizar de novo troca a fila inteira e preserva o trabalho já
feito — quem continuar na lista volta com a marcação que tinha, porque a chave
de cada cliente é o id da sessão do Totalk, que não muda entre gerações.

Uso:
    # produção (Render/Supabase) — mesma connection string do portal-comissoes
    set DATABASE_URL=postgresql://...
    python app/sincronizar_crm.py

    # local, sem banco: grava direto na pasta data/ do portal
    python app/sincronizar_crm.py --local
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGEM = ROOT / "Fila_CRM_CONFIDENCIAL.json"
# O portal fica no Drive, fora deste projeto. Só é usado no modo --local.
PORTAL_DATA = Path(r"G:\Meu Drive\portal-comissoes\data")


def carregar():
    if not ORIGEM.exists():
        raise SystemExit(
            f"{ORIGEM.name} não existe — rode antes:\n"
            "    python app/gerar_fila_retomada.py --somente-json"
        )
    dados = json.loads(ORIGEM.read_text(encoding="utf-8"))
    print(f"fila de {dados['de']} a {dados['ate']}, gerada em {dados['gerado_em'][:16]}")
    return dados


def enviar_postgres(dados, url):
    import psycopg2
    from psycopg2.extras import Json

    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        # A tabela já existe (o portal a cria no boot); o CREATE aqui é só pra
        # este script não depender da ordem em que os dois sobem.
        cur.execute("CREATE TABLE IF NOT EXISTS dados_json ("
                    "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)")
        for vendedor_id, bloco in dados["vendedores"].items():
            payload = {
                "gerado_em": dados["gerado_em"],
                "de": dados["de"],
                "ate": dados["ate"],
                "nome": bloco["nome"],
                "itens": bloco["itens"],
            }
            cur.execute(
                "INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                (f"crm_fila_{vendedor_id}", Json(payload)),
            )
            print(f"  crm_fila_{vendedor_id}: {len(bloco['itens'])} clientes")
    conn.close()


def enviar_local(dados):
    if not PORTAL_DATA.exists():
        raise SystemExit(f"pasta do portal não encontrada: {PORTAL_DATA}")
    for vendedor_id, bloco in dados["vendedores"].items():
        destino = PORTAL_DATA / f"crm_fila_{vendedor_id}.json"
        destino.write_text(json.dumps({
            "gerado_em": dados["gerado_em"],
            "de": dados["de"],
            "ate": dados["ate"],
            "nome": bloco["nome"],
            "itens": bloco["itens"],
        }, ensure_ascii=False), encoding="utf-8")
        print(f"  {destino.name}: {len(bloco['itens'])} clientes")


def main():
    dados = carregar()
    url = os.environ.get("DATABASE_URL")
    if "--local" in sys.argv or not url:
        if not url and "--local" not in sys.argv:
            print("DATABASE_URL não definida — gravando local. Use --local pra "
                  "silenciar este aviso.")
        enviar_local(dados)
    else:
        enviar_postgres(dados, url)
    print("\npronto. Os vendedores veem em /retomada no portal.")


if __name__ == "__main__":
    main()
