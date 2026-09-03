"""
Gera o PDF do relatorio de marketing a partir de relatorio_marketing.html.

O HTML fonte nao tem <html>/<head> proprios (fica assim pra poder ser publicado
como Artifact tambem). Aqui ele e envolvido num documento completo com charset
declarado -- sem isso o Chrome renderiza em latin-1 e todos os acentos quebram.
"""

import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py

ROOT = caminho("dados")   # pasta de dados desta maquina (config/caminhos.json)
FONTE = ROOT / "relatorio_marketing.html"
TEMP = ROOT / "_relatorio_print.html"
SAIDA = ROOT / "Relatorio_Criativos_Nevada_jul-ago2026.pdf"

CHROME = caminho("chrome")


def main():
    corpo = FONTE.read_text(encoding="utf-8")
    doc = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + corpo.replace('<div class="page">', "</head><body>" + '<div class="page">')
        + "</body></html>"
    )
    TEMP.write_text(doc, encoding="utf-8")

    subprocess.run(
        [
            str(CHROME),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            f"--print-to-pdf={SAIDA}",
            "--no-pdf-header-footer",
            TEMP.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    TEMP.unlink(missing_ok=True)
    print(f"PDF gerado: {SAIDA} ({SAIDA.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
