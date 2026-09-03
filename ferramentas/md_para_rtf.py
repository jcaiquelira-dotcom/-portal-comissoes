# -*- coding: utf-8 -*-
"""
Converte o documento de design pra RTF.

Por que RTF: o formulario do Google so aceita .pdf, .doc ou .rtf, e o RTF e o
unico dos tres que da pra gerar aqui sem dependencia nenhuma — e abre em
qualquer editor, inclusive no Word.

Uso:
    python scripts/md_para_rtf.py entrada.md saida.rtf
"""

import io
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from caminhos import caminho, portal  # config/caminhos.json — ver app/caminhos.py
sys.path.insert(0, str(portal("app")))
import nevada_comum as C  # biblioteca comum do portal — ver la app/nevada_comum.py

BARRA = chr(92)          # o proprio caractere, pra nao depender de escape


def esc(t):
    t = t.replace(BARRA, BARRA * 2)
    t = t.replace("{", BARRA + "{").replace("}", BARRA + "}")
    # Acento fora do ASCII vira \uN? — sem isso o RTF abre com lixo na tela
    return "".join(c if ord(c) < 128 else BARRA + "u" + str(ord(c)) + "?"
                   for c in t)


def converter(md):
    out = ["{" + BARRA + "rtf1" + BARRA + "ansi" + BARRA + "deff0"
           + "{" + BARRA + "fonttbl{" + BARRA + "f0 Calibri;}{"
           + BARRA + "f1 Consolas;}}",
           BARRA + "fs22"]
    cmd = lambda s: BARRA + s
    codigo = False
    for l in md.split("\n"):
        if l.startswith("```"):
            codigo = not codigo
            out.append(cmd("f1") + cmd("fs18 ") if codigo
                       else cmd("f0") + cmd("fs22 "))
            continue
        if codigo:
            out.append(esc(l) + cmd("line"))
            continue
        if l.startswith("---"):
            out.append(cmd("par"))
        elif l.startswith("## "):
            out.append(cmd("par") + cmd("b") + cmd("fs26 ") + esc(l[3:])
                       + cmd("b0") + cmd("fs22") + cmd("par"))
        elif l.startswith("# "):
            out.append(cmd("par") + cmd("b") + cmd("fs32 ") + esc(l[2:])
                       + cmd("b0") + cmd("fs22") + cmd("par"))
        elif l.startswith("|"):
            celulas = [c.strip() for c in l.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in celulas):
                continue          # linha separadora da tabela
            out.append(esc("   ".join(celulas)) + cmd("line"))
        elif l.startswith("- "):
            out.append(esc("- " + l[2:]) + cmd("line"))
        elif not l.strip():
            out.append(cmd("par"))
        else:
            partes = re.split(r"\*\*(.+?)\*\*", l)
            out.append("".join(
                esc(p) if i % 2 == 0 else cmd("b ") + esc(p) + cmd("b0 ")
                for i, p in enumerate(partes)))
    out.append("}")
    return "\n".join(out)


def main():
    entrada, saida = sys.argv[1], sys.argv[2]
    rtf = converter(io.open(entrada, encoding="utf-8").read())
    io.open(saida, "w", encoding="ascii", errors="strict").write(rtf)

    # Confere o que quebraria o arquivo antes de alguem enviar pro Google
    abre = len(re.findall(r"(?<!" + re.escape(BARRA) + r")\{", rtf))
    fecha = len(re.findall(r"(?<!" + re.escape(BARRA) + r")\}", rtf))
    print("gerado: {}  ({:,} bytes)".format(saida, os.path.getsize(saida)))
    print("  comeca com rtf1 :", rtf.startswith("{" + BARRA + "rtf1"))
    print("  chaves           : {} abrem, {} fecham -> {}".format(
        abre, fecha, "ok" if abre == fecha else "DESBALANCEADO"))
    print("  so ascii         :", all(ord(c) < 128 for c in rtf))


if __name__ == "__main__":
    C.saida_utf8()
    main()
