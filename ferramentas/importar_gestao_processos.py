# -*- coding: utf-8 -*-
"""Planilha "Gestão de Processos.xlsx" (Drive do gestor) -> painel Meta Bônus.

A planilha e o processo de hoje: uma aba por mes (NOVEMBRO24 ... OUTUBRO26)
com anuncios por dia e por pessoa (duas quinzenas), cadastros por pessoa no
mes (bloco CADASTRO), a lista de carros desmontados (data, codigo, modelo,
pecas) e as metas. O painel Meta Bonus do portal guarda o mesmo conteudo em
`metas_bonus_dados.json` (pessoas / lancamentos / veiculos).

Esta ferramenta le a planilha e conta o que o painel NAO tem:
  - anuncio: (pessoa, dia) sem lancamento no painel;
  - cadastro: (pessoa, mes) sem lancamento — a planilha so tem o total do mes,
    entao o lancamento entra no ultimo dia do mes, como os que ja existem;
  - veiculo: codigo (Vnnn) que o painel nao tem; e pecas quando o painel diz 0
    e a planilha tem o numero.
O que ja existe no painel NAO e alterado: quem manda e o painel, que o time
vai passar a usar direto. Divergencias (mesmo dia, quantidade diferente) so
sao listadas. Sem --gravar nada e escrito.

Nomes: coluna da planilha -> pessoa do painel pelo nome sem acento, dentro do
setor. Coluna com duas pessoas ("Pedro/Alison") e ignorada com aviso — nao da
pra saber de quem e o numero. Celula sem numero (X, -, At., Feriado) e dia
sem producao, nao lancamento zero.

Uso:
    python ferramentas/importar_gestao_processos.py                 # so compara
    python ferramentas/importar_gestao_processos.py --desde=2026-06 # a partir de um mes
    python ferramentas/importar_gestao_processos.py --gravar        # grava o que falta
"""
import os
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import openpyxl
import nevada_comum as C  # biblioteca comum — ver app/nevada_comum.py
from caminhos import caminho  # config/caminhos.json — ver app/caminhos.py

MESES = {"JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7,
         "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12}
TIPO_SETOR = {"anuncio": "anunciante", "cadastro": "cadastrador"}


def chato(t) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(t or "").lower())
                   if not unicodedata.combining(c)).strip()


def mes_da_aba(nome: str):
    m = re.fullmatch(r"([A-ZÇ]+)(\d{2})", nome.strip().upper())
    if not m or chato(m.group(1)).upper() not in MESES:
        return None
    return 2000 + int(m.group(2)), MESES[chato(m.group(1)).upper()]


def numero(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*\*?\s*", v)   # "16*" = numero com nota
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def ultimo_dia(ano, mes):
    import calendar
    return date(ano, mes, calendar.monthrange(ano, mes)[1]).isoformat()


def ler_aba(ws, ano, mes):
    """Devolve (anuncios[(nome, data)] = qtd, cadastros[nome] = qtd, veiculos[...])."""
    linhas = list(ws.iter_rows(values_only=True))
    anuncios, cadastros, veiculos, avisos = {}, {}, [], []
    cab = None
    for row in linhas:
        a = row[0] if row else None
        if isinstance(a, str) and chato(a) == "dia":
            cab = {}
            for j, nome in enumerate(row[1:], start=1):
                if isinstance(nome, str) and chato(nome) == "total":
                    break   # dali em diante e o bloco CADASTRO / carros, na mesma linha
                if not isinstance(nome, str) or not nome.strip():
                    continue
                # "Pedro/Alison": os dois trabalharam juntos naquele periodo e a
                # planilha contou junto. Regra do gestor (03/09/2026): conta junto
                # mesmo — vira uma pessoa conjunta com esse nome no painel.
                cab[j] = " / ".join(p.strip() for p in nome.split("/")) if "/" in nome else nome.strip()
            continue
        if cab and numero(a) is not None and 1 <= numero(a) <= 31:
            dia = int(numero(a))
            try:
                d = date(ano, mes, dia).isoformat()
            except ValueError:
                continue
            for j, nome in cab.items():
                q = numero(row[j]) if j < len(row) else None
                if q is not None and q > 0:
                    anuncios[(nome, d)] = anuncios.get((nome, d), 0) + q
            continue
        if isinstance(a, str) and chato(a) == "total":
            cab = None
    # bloco CADASTRO: nome na coluna H (8), total na J (10) ou K (11)
    dentro = False
    for row in linhas:
        h = row[7] if len(row) > 7 else None
        if isinstance(h, str) and chato(h) == "cadastro":
            dentro = True
            continue
        if dentro:
            if isinstance(h, str) and h.strip() and chato(h) not in ("carros desmontados",):
                q = numero(row[9] if len(row) > 9 else None)
                if q is None:
                    q = numero(row[10] if len(row) > 10 else None)
                if q and q > 0:
                    cadastros[h.strip()] = q
            elif h is None and row[9] is not None and not isinstance(row[9], str):
                dentro = False   # linha do total geral encerra o bloco
            if isinstance(h, str) and chato(h) == "carros desmontados":
                dentro = False
    # bloco Carros desmontados: I=data, J=codigo, K=modelo, O=pecas
    lendo = False
    for row in linhas:
        h = row[7] if len(row) > 7 else None
        if isinstance(h, str) and chato(h) == "carros desmontados":
            lendo = True
            continue
        if not lendo:
            continue
        dt = row[8] if len(row) > 8 else None
        if isinstance(dt, (datetime, date)):
            codigo = str(row[9] or "").strip() if len(row) > 9 else ""
            carro = " ".join(str(row[10] or "").split()) if len(row) > 10 else ""
            pecas = numero(row[14] if len(row) > 14 else None)
            if carro:
                veiculos.append({"data": dt.date().isoformat() if isinstance(dt, datetime) else dt.isoformat(),
                                 "codigo": codigo, "carro": carro, "pecas": pecas})
        elif isinstance(h, str) and chato(h) in ("total",):
            lendo = False
    return anuncios, cadastros, veiculos, avisos


def main() -> int:
    gravar = "--gravar" in sys.argv
    desde = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--desde=")), "2026-06")
    os.environ["DATABASE_URL"] = C.url_banco()
    import nucleo  # noqa: E402  (precisa do DATABASE_URL acima)

    arquivo = Path(caminho("gestao_processos_xlsx"))
    print(f"planilha: {arquivo} | comparando meses >= {desde}")
    wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
    dados = nucleo._mb_bruto()

    criar = "--criar-pessoas" in sys.argv
    tipo_do = {"anunciante": "anuncio", "cadastrador": "cadastro"}
    criadas = []

    def pessoa_id(setor, nome):
        alvo = chato(nome)
        achados = [pid for pid, p in dados["pessoas"].get(setor, {}).items() if chato(p.get("nome")) == alvo]
        if len(achados) > 1:
            # Dois "Pedro" no mesmo setor: fica com o que ja tem lancamento deste tipo.
            com_lanc = [pid for pid in achados
                        if any(l.get("pessoa_id") == pid
                               for l in (dados["lancamentos"].get(tipo_do[setor]) or {}).values())]
            achados = com_lanc or achados[:1]
        if achados:
            return achados[0]
        if not criar:
            return None
        modelo = next((p for p in dados["pessoas"].get(setor, {}).values() if not p.get("inativo")), {})
        pid = uuid.uuid4().hex[:12]
        dados["pessoas"].setdefault(setor, {})[pid] = {
            "nome": nome.strip(), "meta": modelo.get("meta") or 0, "meta_bonus": modelo.get("meta_bonus") or 0,
            "inativo": True,   # veio do historico; o gestor ativa se a pessoa ainda esta no time
        }
        criadas.append((setor, nome.strip()))
        return pid

    ja = {tipo: {(l.get("pessoa_id"), l.get("data")): (lid, l) for lid, l in (dados["lancamentos"].get(tipo) or {}).items()}
          for tipo in ("anuncio", "cadastro")}
    por_codigo = {}
    for vid, v in dados["veiculos"].items():
        cod = (v.get("codigo") or "").strip().upper()
        if re.fullmatch(r"V\d{3,}", cod):
            por_codigo[cod] = (vid, v)

    novos = {"anuncio": [], "cadastro": [], "veiculo": [], "pecas": []}
    divergentes, sem_pessoa = [], set()
    for ws in wb.worksheets:
        am = mes_da_aba(ws.title)
        if not am or f"{am[0]:04d}-{am[1]:02d}" < desde:
            continue
        ano, mes = am
        anuncios, cadastros, veiculos, avisos = ler_aba(ws, ano, mes)
        for a in avisos:
            print(f"  [{ws.title}] {a}")
        for (nome, d), q in sorted(anuncios.items()):
            pid = pessoa_id("anunciante", nome)
            if not pid:
                sem_pessoa.add(("anunciante", nome))
                continue
            atual = ja["anuncio"].get((pid, d))
            if atual is None:
                novos["anuncio"].append({"pessoa_id": pid, "nome": nome, "data": d, "quantidade": q})
            elif float(atual[1].get("quantidade") or 0) != q:
                divergentes.append(("anuncio", nome, d, atual[1].get("quantidade"), q))
        d_fim = ultimo_dia(ano, mes)
        for nome, q in cadastros.items():
            pid = pessoa_id("cadastrador", nome)
            if not pid:
                sem_pessoa.add(("cadastrador", nome))
                continue
            atual = ja["cadastro"].get((pid, d_fim))
            if atual is None:
                novos["cadastro"].append({"pessoa_id": pid, "nome": nome, "data": d_fim, "quantidade": q})
            elif float(atual[1].get("quantidade") or 0) != q:
                divergentes.append(("cadastro", nome, d_fim, atual[1].get("quantidade"), q))
        for v in veiculos:
            cod = v["codigo"].upper()
            if not re.fullmatch(r"V\d{3,}", cod):
                continue   # "V8--": ainda sem codigo na planilha
            if cod not in por_codigo:
                if not any(x["codigo"].upper() == cod for x in novos["veiculo"]):
                    novos["veiculo"].append(v)
            else:
                vid, atual = por_codigo[cod]
                if v["pecas"] and not float(atual.get("pecas") or 0):
                    novos["pecas"].append((vid, cod, atual.get("carro"), v["pecas"]))

    print(f"\n  anuncios que faltam no painel: {len(novos['anuncio'])}")
    for x in novos["anuncio"][:40]:
        print(f"     {x['data']}  {x['nome']:<12} {x['quantidade']:>6.0f}")
    print(f"  cadastros (mes) que faltam: {len(novos['cadastro'])}")
    for x in novos["cadastro"]:
        print(f"     {x['data']}  {x['nome']:<12} {x['quantidade']:>6.0f}")
    print(f"  veiculos que faltam: {len(novos['veiculo'])}")
    for v in novos["veiculo"]:
        print(f"     {v['data']}  {v['codigo']:<6} {v['carro'][:40]:<40} pecas={v['pecas']}")
    print(f"  veiculos no painel com pecas=0 que a planilha tem: {len(novos['pecas'])}")
    for vid, cod, carro, p in novos["pecas"][:40]:
        print(f"     {cod:<6} {str(carro)[:40]:<40} -> {p:.0f}")
    if divergentes:
        print(f"\n  DIVERGENCIAS (painel x planilha) — nao mexo, so aviso: {len(divergentes)}")
        for t, nome, d, a, b in divergentes[:40]:
            print(f"     {t:<9} {d}  {nome:<12} painel={a} planilha={b:.0f}")
    if criadas:
        print("\n  pessoas criadas (inativas, com a meta padrao do setor): "
              + ", ".join(f"{s}: {n}" for s, n in criadas))
    if sem_pessoa:
        print("\n  nomes da planilha sem pessoa no painel (rode com --criar-pessoas, ou cadastre no painel):")
        for setor, nome in sorted(sem_pessoa):
            print(f"     {setor}: {nome}")

    if not gravar:
        print("\n(sem --gravar: nada foi escrito)")
        return 0
    carimbo = C.agora().isoformat(timespec="seconds")
    for x in novos["anuncio"]:
        dados["lancamentos"].setdefault("anuncio", {})[uuid.uuid4().hex[:12]] = {
            "pessoa_id": x["pessoa_id"], "data": x["data"], "quantidade": x["quantidade"],
            "lancado_em": carimbo, "origem": "planilha Gestão de Processos"}
    for x in novos["cadastro"]:
        dados["lancamentos"].setdefault("cadastro", {})[uuid.uuid4().hex[:12]] = {
            "pessoa_id": x["pessoa_id"], "data": x["data"], "quantidade": x["quantidade"],
            "lancado_em": carimbo, "origem": "planilha Gestão de Processos"}
    for v in novos["veiculo"]:
        dados["veiculos"][uuid.uuid4().hex[:12]] = {
            "data": v["data"], "carro": v["carro"][:80], "codigo": v["codigo"][:20],
            "pecas": v["pecas"] or 0, "lancado_em": carimbo, "origem": "planilha Gestão de Processos"}
    for vid, cod, carro, p in novos["pecas"]:
        dados["veiculos"][vid]["pecas"] = p
    nucleo._mb_gravar(dados)
    print(f"\n  gravado: {len(novos['anuncio'])} anuncios, {len(novos['cadastro'])} cadastros, "
          f"{len(novos['veiculo'])} veiculos, {len(novos['pecas'])} pecas atualizadas")
    return 0


if __name__ == "__main__":
    C.saida_utf8()
    sys.exit(main())
