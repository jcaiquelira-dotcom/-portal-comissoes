"""
Injeta dataset.json dentro de insights_dashboard.html.

O painel carrega os dados de <script id="dataset">, embutido no proprio arquivo --
assim ele funciona como arquivo unico, sem servidor e sem fetch (o artifact publicado
bloqueia requisicao pra fora). Isso significa que reexportar o dataset nao basta: sem
rodar este script o painel continua mostrando os dados antigos.

Uso:
    python app/export_dataset.py && python app/montar_painel.py
"""

import json
import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
PAINEL = ROOT / "insights_dashboard.html"
DADOS = ROOT / "dataset.json"

ABRE = re.compile(r'(<script[^>]*id="dataset"[^>]*>)')


def main():
    html = PAINEL.read_text(encoding="utf-8")
    bruto = DADOS.read_text(encoding="utf-8")

    registros = json.loads(bruto)  # valida antes de escrever: html quebrado nao tem conserto facil
    if not registros:
        raise SystemExit("dataset.json esta vazio")

    m = ABRE.search(html)
    if not m:
        raise SystemExit('nao achei <script id="dataset"> em insights_dashboard.html')
    ini = m.end()
    fim = html.index("</script>", ini)

    # "</script>" dentro do JSON encerraria a tag no meio do dado. Nao acontece hoje,
    # mas um texto de criativo com html dentro derrubaria o painel inteiro sem aviso.
    if "</script" in bruto.lower():
        bruto = bruto.replace("</script", "<\\/script").replace("</SCRIPT", "<\\/SCRIPT")

    antes = fim - ini
    html = html[:ini] + bruto + html[fim:]
    PAINEL.write_text(html, encoding="utf-8")

    campos = sorted(registros[0].keys())
    datas = [r["d"] for r in registros]
    print(f"{len(registros):,} registros embutidos ({min(datas)} a {max(datas)})")
    print(f"campos: {' '.join(campos)}")
    print(f"dataset: {antes/1024:.0f} KB -> {len(bruto)/1024:.0f} KB")
    print(f"painel : {PAINEL.stat().st_size/1024/1024:.2f} MB")

    com_ia = sum(1 for r in registros if r.get("im") is not None)
    if com_ia:
        print(f"com leitura da IA: {com_ia:,} de {len(registros):,} ({com_ia/len(registros):.0%})")


if __name__ == "__main__":
    main()
