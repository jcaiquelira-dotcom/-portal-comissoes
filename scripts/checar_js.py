"""
Confere se o JavaScript das telas está sintaticamente válido ANTES de publicar.

Por que existe: em 30/08/2026 um patch meu inseriu uma linha dentro de um
`if(...){` em vez de antes dele. O Python continuou válido, o HTML continuou
válido, o deploy subiu — e a área do gestor parou de abrir, porque o navegador
engasgava num `Unexpected token ';'`. O gestor descobriu usando o portal.

Erro de sintaxe em JS não aparece em nenhuma checagem de Python. Este script
fecha esse buraco: extrai os blocos <script> de cada tela e passa por um
parser de verdade (Node, se existir) ou por um verificador de balanceamento
que pega o caso comum — chave, parêntese ou colchete que não fecha.

Uso:
    python scripts/checar_js.py
    → sai com código 1 se achar problema, pra travar o commit
"""

import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TELAS = ["app/static/admin.html", "app/static/index.html",
         "app/static/portal-nav.js", "app/static/desempenho.js"]


def blocos_js(caminho: Path):
    """Devolve [(rotulo, codigo, linha_inicial)] de cada trecho de JS."""
    texto = caminho.read_text(encoding="utf-8")
    if caminho.suffix == ".js":
        return [(caminho.name, texto, 1)]
    saida = []
    for i, m in enumerate(re.finditer(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>",
                                      texto, re.S)):
        linha = texto[:m.start(1)].count("\n") + 1
        saida.append((f"{caminho.name} <script #{i + 1}>", m.group(1), linha))
    return saida


def checar_com_node(codigo: str):
    """(ok, mensagem). Node valida de verdade — pega tudo que o navegador pega."""
    node = shutil.which("node")
    if not node:
        return None, "sem node"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(codigo)
        tmp = f.name
    try:
        r = subprocess.run([node, "--check", tmp], capture_output=True,
                           text=True, timeout=60)
        return r.returncode == 0, (r.stderr or "").strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def checar_balanceamento(codigo: str):
    """Reserva quando não há Node: confere se (), [] e {} fecham.

    Ignora o que está dentro de string, template literal, regex ou comentário —
    senão qualquer `{` num texto daria falso positivo. Não é um parser
    completo, mas pega o erro que de fato acontece ao editar por script: um
    bloco que abre e não fecha.
    """
    pilha, linha = [], 1
    i, n = 0, len(codigo)
    pares = {")": "(", "]": "[", "}": "{"}
    while i < n:
        c = codigo[i]
        prox = codigo[i + 1] if i + 1 < n else ""
        if c == "\n":
            linha += 1
            i += 1
            continue
        if c == "/" and prox == "/":
            while i < n and codigo[i] != "\n":
                i += 1
            continue
        if c == "/" and prox == "*":
            i += 2
            while i < n - 1 and not (codigo[i] == "*" and codigo[i + 1] == "/"):
                if codigo[i] == "\n":
                    linha += 1
                i += 1
            i += 2
            continue
        if c in "\"'`":
            aspa, i = c, i + 1
            while i < n:
                if codigo[i] == "\\":
                    i += 2
                    continue
                if codigo[i] == "\n":
                    linha += 1
                    if aspa != "`":      # string comum não cruza linha
                        break
                if codigo[i] == aspa:
                    i += 1
                    break
                # ${...} dentro de template pode conter chaves — conta junto
                if aspa == "`" and codigo[i] == "$" and codigo[i + 1:i + 2] == "{":
                    profundidade = 1
                    i += 2
                    while i < n and profundidade:
                        if codigo[i] == "{":
                            profundidade += 1
                        elif codigo[i] == "}":
                            profundidade -= 1
                        elif codigo[i] == "\n":
                            linha += 1
                        i += 1
                    continue
                i += 1
            continue
        if c in "([{":
            pilha.append((c, linha))
        elif c in ")]}":
            if not pilha:
                return False, f"linha {linha}: fecha '{c}' sem abrir"
            abriu, ln = pilha.pop()
            if abriu != pares[c]:
                return False, (f"linha {linha}: fecha '{c}' mas o aberto era "
                               f"'{abriu}' da linha {ln}")
        i += 1
    if pilha:
        abriu, ln = pilha[-1]
        return False, f"'{abriu}' aberto na linha {ln} nunca fecha"
    return True, ""


def main():
    problemas = 0
    for rel in TELAS:
        caminho = RAIZ / rel
        if not caminho.exists():
            continue
        for rotulo, codigo, offset in blocos_js(caminho):
            ok, msg = checar_com_node(codigo)
            via = "node"
            if ok is None:
                ok, msg = checar_balanceamento(codigo)
                via = "balanceamento"
            if ok:
                print(f"  ok   {rotulo} ({via})")
            else:
                problemas += 1
                print(f"  ERRO {rotulo} ({via}): {msg[:300]}")
                print(f"       o bloco começa na linha {offset} do arquivo")
    if problemas:
        print(f"\n{problemas} problema(s) — NÃO publique assim.")
        return 1
    print("\nJavaScript das telas está válido.")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
