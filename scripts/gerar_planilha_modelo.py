# -*- coding: utf-8 -*-
"""Gera a planilha de fluxo de caixa pro socio preencher.

Quem alimenta o controle hoje e um dos socios, com quase 60 anos, e ele fez a
planilha atual do jeito dele — blocos de tres colunas que mudam de lugar a cada
mes, cor pra separar socio, rotulo livre. Funciona na cabeca de quem montou.

Entao esta AQUI nao e uma planilha nova: e a mesma, com uma troca so.

O que fica igual, de proposito:
    - uma aba por mes, com os mesmos nomes (Jan, Fev, ..., Maio, ..., Dez)
    - as palavras dele: Creditos, Debitos, Saldo
    - o resumo do mes no alto, onde ele ja olha
    - uma aba de ano, no lugar da "Geral"

O que muda, que e a razao de existir:
    - uma LINHA por lancamento, em vez de bloco em coluna. Coluna que anda de
      lugar e o que faz o subtotal do mes ser um quebra-cabeca.
    - a categoria vem de LISTA, nao digitada. A agencia Beelieve esta escrita
      de seis jeitos na planilha atual; sao seis gastos diferentes pra qualquer
      soma, e o mesmo boleto de R$ 1.900 na vida real.
    - "como pagou" em campo separado de "o que comprou". R$ 327 mil do ano
      estao lancados como "Cartao", que nao diz o que foi comprado.
    - o DRE se monta sozinho, por formula. Ninguem soma nada na mao.

Os quatro socios viram quatro categorias de pro-labore, no lugar da cor. A
informacao e a mesma; deixa de depender de lembrar qual cinza e qual.

Uso:
    python scripts/gerar_planilha_modelo.py --saida "C:\\...\\Fluxo 2027.xlsx"
    python scripts/gerar_planilha_modelo.py --saida "..." --ano 2027
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule

MESES = ["Jan", "Fev", "Mar", "Abr", "Maio", "Jun",
         "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
LINHAS_DE_LANCAMENTO = 400          # sobra folgada: o mes mais cheio teve 171

# Paleta puxada da planilha atual — os mesmos cinzas que ele ja usa pra separar
# bloco, mais um verde e um vermelho so pra entrada e saida.
AZUL = "1F3864"
CINZA_TITULO = "D9D9D9"
CINZA_CLARO = "F2F2F2"
VERDE = "1E7B4D"
VERMELHO = "B03A2E"
BRANCO = "FFFFFF"

DINHEIRO = 'R$ #,##0.00'
FINA = Side(style="thin", color="BFBFBF")
BORDA = Border(left=FINA, right=FINA, top=FINA, bottom=FINA)


def _plano():
    """O plano de contas do portal, sem copia. Se ele mudar la, muda aqui."""
    import server
    linhas = []
    for bloco in server.PLANO_DE_CONTAS:
        for conta in bloco["contas"]:
            c = server.CONTAS_POR_CODIGO[conta[0]]
            linhas.append({
                "rotulo": "%s · %s" % (c["codigo"], c["nome"]),
                "codigo": c["codigo"],
                "nome": c["nome"],
                "grupo": c["grupo"],
                "dre": c["dre"],
                "entrada": c["entrada"],
                "ajuda": c["ajuda"],
            })
    return linhas, server.FORMAS_DE_PAGAMENTO


def _titulo(ws, celula, texto, tamanho=14):
    ws[celula] = texto
    ws[celula].font = Font(bold=True, size=tamanho, color=AZUL)


def aba_como_usar(wb, plano, ano):
    ws = wb.create_sheet("Como usar")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 104

    _titulo(ws, "B2", "Fluxo de caixa %d — como preencher" % ano, 18)
    passos = [
        ("", ""),
        ("É a mesma planilha de sempre, com uma diferença.",
         "Cada gasto agora é uma LINHA, e não um bloco numa coluna. "
         "Os meses continuam em abas separadas, do jeito que sempre foram."),
        ("1. Abra a aba do mês", "Jan, Fev, Mar… iguais às de antes."),
        ("2. Preencha uma linha por lançamento",
         "Dia, Categoria, Histórico, Valor e como pagou. Uma linha para cada "
         "coisa que entrou ou saiu."),
        ("3. A Categoria é escolhida numa lista, não digitada",
         "Clique na célula e aparece a setinha. Escolher da lista é o que faz "
         "o resumo somar certo — quando o nome é digitado, o mesmo gasto vira "
         "dois gastos diferentes para a soma."),
        ("4. No Histórico, escreva quem e o quê",
         'Ex.: "Amaral — retífica do motor do Jetta". É aqui que vai o nome do '
         "fornecedor, e não na categoria."),
        ("5. Como pagou é outra coluna",
         "Cartão, Pix, boleto. Isso diz por onde o dinheiro saiu; a categoria "
         "diz o que foi comprado. São duas perguntas diferentes."),
        ("", ""),
        ("O resumo e o DRE se fazem sozinhos.",
         "Não precisa somar nada. O quadro no alto de cada mês e a aba "
         "«DRE do ano» são calculados por fórmula, na hora."),
        ("", ""),
        ("Retirada de sócio", "São quatro categorias, uma por sócio (5.05 a "
         "5.08) — no lugar da cor de fundo. É a mesma informação, sem precisar "
         "lembrar qual cinza é de quem."),
        ("Não sabe a categoria?",
         "Use «7.07 · Despesas gerais». Mas ela tem limite: se passar de 2% do "
         "mês, tem coisa ali que pertence a outro lugar."),
    ]
    r = 4
    for titulo, texto in passos:
        if titulo:
            ws.cell(row=r, column=2, value=titulo).font = Font(bold=True, size=11, color=AZUL)
            r += 1
        if texto:
            c = ws.cell(row=r, column=2, value=texto)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 30 if len(texto) > 95 else 15
            r += 1
        r += 1
    return ws


def aba_categorias(wb, plano, formas):
    """A lista que alimenta as setinhas. Fica visivel de proposito: ele precisa
    poder ler o que cada categoria quer dizer sem perguntar pra ninguem."""
    ws = wb.create_sheet("Categorias")
    ws.sheet_view.showGridLines = False
    cabecalho = ["Categoria", "Grupo", "O que entra aqui", "Entra ou sai"]
    for i, t in enumerate(cabecalho, start=1):
        c = ws.cell(row=1, column=i, value=t)
        c.font = Font(bold=True, color=BRANCO)
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 22

    for i, p in enumerate(plano, start=2):
        ws.cell(row=i, column=1, value=p["rotulo"])
        ws.cell(row=i, column=2, value=p["grupo"])
        ws.cell(row=i, column=3, value=p["ajuda"])
        ws.cell(row=i, column=4, value="Entra" if p["entrada"] else "Sai")
        if i % 2 == 0:
            for col in range(1, 5):
                ws.cell(row=i, column=col).fill = PatternFill("solid", fgColor=CINZA_CLARO)

    # As formas de pagamento ficam longe, pra lista de categorias poder crescer
    # sem esbarrar nelas.
    ws.cell(row=1, column=6, value="Como pagou").font = Font(bold=True, color=BRANCO)
    ws.cell(row=1, column=6).fill = PatternFill("solid", fgColor=AZUL)
    for i, f in enumerate(formas, start=2):
        ws.cell(row=i, column=6, value=f)

    for col, larg in (("A", 38), ("B", 26), ("C", 52), ("D", 12), ("F", 20)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A2"
    return ws


def aba_mes(wb, mes, plano, formas, ano):
    ws = wb.create_sheet(mes)
    ws.sheet_view.showGridLines = False
    n = len(plano)
    ult = LINHAS_DE_LANCAMENTO + 5      # os lancamentos comecam na linha 6

    _titulo(ws, "A1", "%s de %d" % (mes, ano), 16)
    ws["A2"] = "Uma linha por lançamento. A categoria vem da lista — clique na célula e escolha."
    ws["A2"].font = Font(size=10, italic=True, color="808080")

    # --- resumo do mes, com as palavras que ele ja usa -----------------------
    resumo = [
        ("Créditos (entrou)", '=SUMIFS($D$6:$D$%d,$B$6:$B$%d,"1.*")' % (ult, ult), VERDE),
        ("Débitos (saiu)",
         '=SUM($D$6:$D$%d)-SUMIFS($D$6:$D$%d,$B$6:$B$%d,"1.*")' % (ult, ult, ult), VERMELHO),
        ("Saldo", "=G4-G5", AZUL),
    ]
    ws["F3"] = "Resumo do mês"
    ws["F3"].font = Font(bold=True, size=12, color=AZUL)
    for i, (rot, formula, cor) in enumerate(resumo, start=4):
        ws.cell(row=i, column=6, value=rot).font = Font(bold=(i == 6), size=11)
        c = ws.cell(row=i, column=7, value=formula)
        c.number_format = DINHEIRO
        c.font = Font(bold=True, size=12, color=cor)
    # Saldo negativo em vermelho, sem ele precisar reparar no sinal.
    ws.conditional_formatting.add("G6", CellIsRule(
        operator="lessThan", formula=["0"], font=Font(bold=True, color=VERMELHO)))

    ws["F8"] = "Despesas gerais (limite 2%)"
    ws["F8"].font = Font(size=10, color="808080")
    ws["G8"] = ('=IF(G5=0,0,SUMIFS($D$6:$D$%d,$B$6:$B$%d,"7.07*")/G5)' % (ult, ult))
    ws["G8"].number_format = "0.0%"
    ws.conditional_formatting.add("G8", CellIsRule(
        operator="greaterThan", formula=["0.02"], font=Font(bold=True, color=VERMELHO)))

    # --- a tabela de lancamentos --------------------------------------------
    cabecalho = ["Dia", "Categoria", "Histórico — quem e o quê", "Valor",
                 "Como pagou", "Grupo (automático)"]
    for i, t in enumerate(cabecalho, start=1):
        c = ws.cell(row=5, column=i, value=t)
        c.font = Font(bold=True, color=BRANCO, size=11)
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(vertical="center", horizontal="center")
        c.border = BORDA
    ws.row_dimensions[5].height = 26

    listrado = PatternFill("solid", fgColor=CINZA_CLARO)
    for r in range(6, ult + 1):
        for col in range(1, 7):
            c = ws.cell(row=r, column=col)
            c.border = BORDA
            if r % 2 == 0:
                c.fill = listrado
        ws.cell(row=r, column=1).number_format = "DD/MM"
        ws.cell(row=r, column=4).number_format = DINHEIRO
        # O grupo se preenche sozinho a partir da categoria: serve pro DRE e
        # mostra na hora se a escolha foi a esperada.
        ws.cell(row=r, column=6, value=(
            '=IFERROR(IF($B{0}="","",VLOOKUP($B{0},Categorias!$A:$B,2,FALSE)),"")'
        ).format(r)).font = Font(size=9, color="808080")

    # --- as setinhas ---------------------------------------------------------
    dv_cat = DataValidation(
        type="list", formula1="=Categorias!$A$2:$A$%d" % (n + 1), allow_blank=True,
        showDropDown=False, errorTitle="Escolha da lista",
        error="Essa categoria não existe na lista. Clique na setinha e escolha uma.",
        promptTitle="Categoria", prompt="Clique na setinha e escolha.")
    dv_cat.error = ("Essa categoria não está na lista. Clique na setinha e escolha "
                    "uma — é o que faz o resumo somar certo.")
    ws.add_data_validation(dv_cat)
    dv_cat.add("B6:B%d" % ult)

    dv_forma = DataValidation(
        type="list", formula1="=Categorias!$F$2:$F$%d" % (len(formas) + 1),
        allow_blank=True, showDropDown=False)
    ws.add_data_validation(dv_forma)
    dv_forma.add("E6:E%d" % ult)

    dv_valor = DataValidation(
        type="decimal", operator="greaterThan", formula1="0", allow_blank=True,
        errorTitle="Valor", error="O valor precisa ser maior que zero. "
                                  "Devolução tem categoria própria (2.02).")
    ws.add_data_validation(dv_valor)
    dv_valor.add("D6:D%d" % ult)

    for col, larg in (("A", 9), ("B", 34), ("C", 46), ("D", 15), ("E", 17), ("F", 22)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A6"
    return ws


def aba_dre(wb, plano, ano):
    """O DRE do ano, por formula. Nada aqui e digitado."""
    ws = wb.create_sheet("DRE do ano")
    ws.sheet_view.showGridLines = False
    _titulo(ws, "A1", "DRE %d — monta sozinho, não preencha nada aqui" % ano, 16)

    col_mes = {m: 3 + i for i, m in enumerate(MESES)}      # C..N
    col_total = 15                                          # O

    ws.cell(row=3, column=1, value="Conta").font = Font(bold=True, color=BRANCO)
    ws.cell(row=3, column=1).fill = PatternFill("solid", fgColor=AZUL)
    for m, c in col_mes.items():
        cel = ws.cell(row=3, column=c, value=m)
        cel.font = Font(bold=True, color=BRANCO)
        cel.fill = PatternFill("solid", fgColor=AZUL)
        cel.alignment = Alignment(horizontal="center")
    cel = ws.cell(row=3, column=col_total, value="Ano")
    cel.font = Font(bold=True, color=BRANCO)
    cel.fill = PatternFill("solid", fgColor=AZUL)

    ult = LINHAS_DE_LANCAMENTO + 5

    def linha_de_conta(r, rotulo):
        ws.cell(row=r, column=1, value=rotulo).font = Font(size=10)
        for m, c in col_mes.items():
            f = "=SUMIFS('%s'!$D$6:$D$%d,'%s'!$B$6:$B$%d,$A%d)" % (m, ult, m, ult, r)
            cel = ws.cell(row=r, column=c, value=f)
            cel.number_format = DINHEIRO
            cel.font = Font(size=10)
        t = ws.cell(row=r, column=col_total,
                    value="=SUM(%s%d:%s%d)" % (get_column_letter(3), r,
                                               get_column_letter(14), r))
        t.number_format = DINHEIRO
        t.font = Font(size=10, bold=True)

    def linha_titulo(r, texto, fundo=CINZA_TITULO, formula_por_mes=None):
        c = ws.cell(row=r, column=1, value=texto)
        c.font = Font(bold=True, size=11, color=AZUL)
        for col in range(1, col_total + 1):
            ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor=fundo)
        if formula_por_mes:
            for m, col in col_mes.items():
                cel = ws.cell(row=r, column=col, value=formula_por_mes(col))
                cel.number_format = DINHEIRO
                cel.font = Font(bold=True, size=10)
            t = ws.cell(row=r, column=col_total,
                        value="=SUM(C%d:N%d)" % (r, r))
            t.number_format = DINHEIRO
            t.font = Font(bold=True, size=10)

    # Ordem de DRE: o que sai do preco antes de sobrar margem vem primeiro, e
    # o que nao e despesa (obra, retirada) fica depois do resultado.
    #
    # Nenhum rotulo comeca com "=". O Excel le celula que comeca com igual como
    # formula: "= LUCRO BRUTO" virava #NAME? na tela, com o numero certo do
    # lado. As linhas de resultado se distinguem pelo fundo e pelo negrito.
    ORDEM = [
        ("receita", "RECEITA BRUTA"),
        ("deducoes", "(−) DEDUÇÕES"),
        ("cmv", "(−) CUSTO DA PEÇA VENDIDA"),
        (None, "LUCRO BRUTO"),
        ("despesas", "(−) DESPESAS OPERACIONAIS"),
        (None, "RESULTADO OPERACIONAL"),
        ("investimento", "(−) INVESTIMENTO E OBRA"),
        ("socios", "(−) DISTRIBUIÇÃO DE LUCRO"),
        ("nao_resultado", "(−) SÓ PASSOU PELO CAIXA"),
        (None, "SOBRA DE CAIXA"),
    ]
    por_dre = {}
    for p in plano:
        por_dre.setdefault(p["dre"], []).append(p)

    r = 4
    marcos = {}
    for chave, rotulo in ORDEM:
        if chave is None:
            marcos[rotulo] = r
            linha_titulo(r, rotulo, fundo="CFE0F5")
            r += 1
            continue
        contas = por_dre.get(chave, [])
        if not contas:
            continue
        inicio = r + 1
        fim = r + len(contas)
        linha_titulo(r, rotulo, formula_por_mes=lambda col, i=inicio, f=fim:
                     "=SUM(%s%d:%s%d)" % (get_column_letter(col), i,
                                          get_column_letter(col), f))
        marcos[rotulo] = r
        r += 1
        for p in contas:
            linha_de_conta(r, p["rotulo"])
            r += 1

    # Os resultados, agora que sei em que linha cada grupo caiu.
    def preencher(rotulo, expressao):
        rr = marcos[rotulo]
        for m, col in col_mes.items():
            L = get_column_letter(col)
            ws.cell(row=rr, column=col, value=expressao(L)).number_format = DINHEIRO
            ws.cell(row=rr, column=col).font = Font(bold=True, size=10, color=AZUL)
        t = ws.cell(row=rr, column=col_total, value="=SUM(C%d:N%d)" % (rr, rr))
        t.number_format = DINHEIRO
        t.font = Font(bold=True, size=11, color=AZUL)

    lb = lambda L: "={0}{1}-{0}{2}-{0}{3}".format(
        L, marcos["RECEITA BRUTA"], marcos["(−) DEDUÇÕES"],
        marcos["(−) CUSTO DA PEÇA VENDIDA"])
    preencher("LUCRO BRUTO", lb)
    preencher("RESULTADO OPERACIONAL", lambda L: "={0}{1}-{0}{2}".format(
        L, marcos["LUCRO BRUTO"], marcos["(−) DESPESAS OPERACIONAIS"]))

    def sobra(L):
        partes = [marcos.get(k) for k in ("(−) INVESTIMENTO E OBRA",
                                          "(−) DISTRIBUIÇÃO DE LUCRO",
                                          "(−) SÓ PASSOU PELO CAIXA")]
        expr = "={0}{1}".format(L, marcos["RESULTADO OPERACIONAL"])
        for p in partes:
            if p:
                expr += "-%s%d" % (L, p)
        return expr
    preencher("SOBRA DE CAIXA", sobra)

    ws.column_dimensions["A"].width = 38
    for col in range(3, col_total + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = "C4"
    return ws


def aba_ano(wb, ano):
    """O lugar da antiga 'Geral': entradas, saidas e saldo de cada mes."""
    ws = wb.create_sheet("Resumo do ano")
    ws.sheet_view.showGridLines = False
    _titulo(ws, "A1", "Resumo de %d" % ano, 16)
    for i, t in enumerate(["Mês", "Créditos", "Débitos", "Saldo"], start=1):
        c = ws.cell(row=3, column=i, value=t)
        c.font = Font(bold=True, color=BRANCO)
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(horizontal="center")
    for i, m in enumerate(MESES, start=4):
        ws.cell(row=i, column=1, value=m).font = Font(bold=True)
        ws.cell(row=i, column=2, value="='%s'!G4" % m).number_format = DINHEIRO
        ws.cell(row=i, column=3, value="='%s'!G5" % m).number_format = DINHEIRO
        c = ws.cell(row=i, column=4, value="=B%d-C%d" % (i, i))
        c.number_format = DINHEIRO
        c.font = Font(bold=True)
        if i % 2 == 0:
            for col in range(1, 5):
                ws.cell(row=i, column=col).fill = PatternFill("solid", fgColor=CINZA_CLARO)
    r = len(MESES) + 4
    ws.cell(row=r, column=1, value="TOTAL").font = Font(bold=True, size=12, color=AZUL)
    for col in range(2, 5):
        L = get_column_letter(col)
        c = ws.cell(row=r, column=col, value="=SUM(%s4:%s%d)" % (L, L, r - 1))
        c.number_format = DINHEIRO
        c.font = Font(bold=True, size=12, color=AZUL)
    ws.conditional_formatting.add("D4:D%d" % r, CellIsRule(
        operator="lessThan", formula=["0"], font=Font(bold=True, color=VERMELHO)))
    for col, larg in (("A", 14), ("B", 18), ("C", 18), ("D", 18)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A4"
    return ws


def gerar(saida: str, ano: int) -> str:
    plano, formas = _plano()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    aba_como_usar(wb, plano, ano)
    for m in MESES:
        aba_mes(wb, m, plano, formas, ano)
    aba_ano(wb, ano)
    aba_dre(wb, plano, ano)
    aba_categorias(wb, plano, formas)

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    wb.save(saida)
    return saida


def main() -> int:
    arg = sys.argv
    if "--saida" not in arg:
        print("faltou --saida com o caminho do .xlsx")
        return 2
    saida = arg[arg.index("--saida") + 1]
    ano = int(arg[arg.index("--ano") + 1]) if "--ano" in arg else 2027
    caminho = gerar(saida, ano)
    plano, _ = _plano()
    print("gerado: %s" % caminho)
    print("  %d abas de mes + Resumo do ano + DRE + Categorias" % len(MESES))
    print("  %d categorias na lista, %d linhas por mes"
          % (len(plano), LINHAS_DE_LANCAMENTO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
