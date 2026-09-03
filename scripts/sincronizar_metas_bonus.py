"""
Leva os números do painel-metas pro portal, na área do gestor.

O painel-metas roda local, na máquina da loja, e guarda tudo em JSON. Quem
precisa olhar o resultado é o gestor, que já vive no portal — então aqui a
produção de cada pessoa é agregada por mês e empurrada pro mesmo banco.

Manda agregado por pessoa/mês, não lançamento a lançamento: o portal só precisa
saber quanto cada um fez no mês contra a meta dele, e o detalhe do dia a dia
continua no painel-metas, que é onde se lança.

Uso:
    set DATABASE_URL=postgresql://...
    python scripts/sincronizar_metas_bonus.py
    python scripts/sincronizar_metas_bonus.py --seco
"""

import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py
from caminhos import caminho  # config/caminhos.json — ver app/caminhos.py

ORIGEM = caminho("painel_metas_data")

# setor da pessoa -> tipo do lançamento, como o painel-metas nomeia os dois.
TIPO = {"anunciante": "anuncio", "cadastrador": "cadastro"}


def ler(nome, padrao):
    caminho = ORIGEM / nome
    if not caminho.exists():
        return padrao
    return json.loads(caminho.read_text(encoding="utf-8"))


def coletar():
    pessoas = ler("pessoas.json", {})
    lancamentos = ler("lancamentos.json", {})
    veiculos = ler("veiculos.json", {})
    meta_veic = ler("meta_veiculos.json", {"meta": 0, "meta_bonus": 0})

    # produção por (setor, pessoa, mês)
    producao = defaultdict(float)
    for setor, tipo in TIPO.items():
        for l in (lancamentos.get(tipo) or {}).values():
            producao[(setor, l["pessoa_id"], l["data"][:7])] += l.get("quantidade") or 0

    meses = sorted({chave[2] for chave in producao}
                   | {v["data"][:7] for v in veiculos.values() if v.get("data")})

    por_mes = {}
    for mes in meses:
        setores = {}
        for setor, gente in pessoas.items():
            linhas = []
            for pid, p in gente.items():
                total = round(producao.get((setor, pid, mes), 0), 2)
                meta = float(p.get("meta") or 0)
                bonus = float(p.get("meta_bonus") or 0)
                # Quem não lançou nada no mês não entra: apareceria como 0% e
                # pareceria alguém que trabalhou e não produziu, quando na
                # verdade não estava no time naquele mês.
                if total <= 0:
                    continue
                linhas.append({
                    "nome": p.get("nome") or "(sem nome)",
                    "total": total, "meta": meta, "meta_bonus": bonus,
                    "pct": round(100 * total / meta, 1) if meta else None,
                    "bateu_meta": bool(meta) and total >= meta,
                    "bateu_bonus": bool(bonus) and total >= bonus,
                })
            linhas.sort(key=lambda x: -x["total"])
            if linhas:
                setores[setor] = linhas

        do_mes = [v for v in veiculos.values() if (v.get("data") or "")[:7] == mes]
        pecas = round(sum(v.get("pecas") or 0 for v in do_mes), 2)
        por_mes[mes] = {
            "setores": setores,
            "veiculos": {
                "carros": len(do_mes),
                "pecas": pecas,
                "meta": float(meta_veic.get("meta") or 0),
                "meta_bonus": float(meta_veic.get("meta_bonus") or 0),
                "lista": sorted(({"data": v.get("data"), "carro": v.get("carro"),
                                  "codigo": v.get("codigo"), "pecas": v.get("pecas") or 0}
                                 for v in do_mes), key=lambda v: v["data"] or ""),
            },
        }
    return por_mes


def resumir(dados):
    print(f"{len(dados)} meses, de {min(dados)} a {max(dados)}\n")
    for mes in sorted(dados)[-3:]:
        d = dados[mes]
        print(f"  {mes}:")
        for setor, linhas in d["setores"].items():
            bonus = sum(1 for x in linhas if x["bateu_bonus"])
            meta = sum(1 for x in linhas if x["bateu_meta"])
            print(f"     {setor:12} {len(linhas):>2} pessoas | {meta} na meta | {bonus} no bonus")
        v = d["veiculos"]
        print(f"     veiculos     {v['carros']:>2} carros | {v['pecas']:>8,.0f} pecas "
              f"(meta {v['meta']:.0f} / bonus {v['meta_bonus']:.0f})")


def gravar(dados, url):
    import psycopg2
    from psycopg2.extras import Json

    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS dados_json ("
                    "chave TEXT PRIMARY KEY, valor JSONB NOT NULL)")
        cur.execute("INSERT INTO dados_json (chave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                    ("metas_bonus", Json({
                        "gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "meses": dados})))
    conn.close()
    print("\n  gravado metas_bonus")


def main():
    if not ORIGEM.exists():
        raise SystemExit(f"pasta do painel-metas não encontrada: {ORIGEM}")
    dados = coletar()
    if not dados:
        raise SystemExit("nenhum lançamento encontrado.")
    resumir(dados)
    if "--seco" in sys.argv:
        print("\n(--seco: nada foi gravado)")
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("\nDATABASE_URL não definida.")
    gravar(dados, url)


if __name__ == "__main__":
    C.saida_utf8()
    main()
