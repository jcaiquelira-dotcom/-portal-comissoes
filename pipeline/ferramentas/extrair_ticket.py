import json
import re
import sqlite3
import statistics
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
SQLITE_PATH = ROOT / "vendas.db"

# valores em contexto de frete/entrega devem ser ignorados na hora de achar o preco da peca
CONTEXTO_FRETE = ["frete", "motoboy", "lalamove", "entrega", "sedex", "correios"]

RE_VALOR = re.compile(
    r"(?:r\$\s*([\d.]{1,7},\d{2}|\d{1,7}(?:[.,]\d{2})?)"  # R$1.300,00 / R$500 / R$500.00
    r"|([\d.]{1,7},\d{2})\s*\$"  # 830,00$
    r"|(?<![\d.,])(\d{2,5}(?:[.,]\d{2})?)\s*(?=\bno\s+pix|\bpix\b))",  # 470 no pix / 1900 pix
    re.IGNORECASE,
)


def normaliza_valor(txt: str) -> float | None:
    txt = txt.replace(" ", "")
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        v = float(txt)
    except ValueError:
        return None
    if v < 15 or v > 30000:
        return None
    return v


def extrai_valores(texto: str):
    achados = []
    tl = texto.lower()
    tem_frete_no_contexto = any(k in tl for k in CONTEXTO_FRETE)
    for m in RE_VALOR.finditer(texto):
        bruto = next(g for g in m.groups() if g)
        v = normaliza_valor(bruto)
        if v is None:
            continue
        achados.append((v, "pix" in tl, tem_frete_no_contexto))
    return achados


def main():
    conn = sqlite3.connect(SQLITE_PATH)
    convertidas = [sid for (sid,) in conn.execute("SELECT session_id FROM conversao WHERE classe='provavel'")]

    resultados = []
    for sid in convertidas:
        msgs = conn.execute(
            "SELECT text, created_at FROM mensagens WHERE session_id=? AND direction='TO_HUB' "
            "AND text IS NOT NULL AND user_id IS NOT NULL ORDER BY created_at ASC",
            (sid,),
        ).fetchall()

        candidatos_pix = []
        candidatos_gerais = []
        for texto, created_at in msgs:
            for v, tem_pix, tem_frete in extrai_valores(texto):
                if tem_frete and not tem_pix:
                    continue  # provavel valor de frete isolado, nao e o preco da peca
                if tem_pix:
                    candidatos_pix.append(v)
                else:
                    candidatos_gerais.append(v)

        valor_final = None
        origem = None
        if candidatos_pix:
            valor_final = candidatos_pix[-1]  # ultimo valor fechado "no pix" = preco final negociado
            origem = "pix"
        elif candidatos_gerais:
            valor_final = candidatos_gerais[-1]
            origem = "geral"

        resultados.append({"session_id": sid, "valor": valor_final, "origem": origem})

    com_valor = [r for r in resultados if r["valor"] is not None]
    valores = [r["valor"] for r in com_valor]

    print(f"sessoes convertidas: {len(resultados)}")
    print(f"com valor extraido: {len(com_valor)} ({100*len(com_valor)/len(resultados):.1f}%)")
    print(f"  via contexto 'pix': {sum(1 for r in com_valor if r['origem']=='pix')}")
    print(f"  via valor geral (sem pix explicito): {sum(1 for r in com_valor if r['origem']=='geral')}")
    print()
    if valores:
        print(f"media: R$ {statistics.mean(valores):.2f}")
        print(f"mediana: R$ {statistics.median(valores):.2f}")
        print(f"min: R$ {min(valores):.2f} | max: R$ {max(valores):.2f}")
        print(f"desvio padrao: R$ {statistics.stdev(valores):.2f}")

    with open(ROOT / "ticket_extraido.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
