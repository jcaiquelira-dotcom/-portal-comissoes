# -*- coding: utf-8 -*-
"""
Remonta as tabelas `canal` e `conversao` — as duas que o painel de marketing le.

Por que existe: elas eram montadas por um script que se perdeu, entao pararam
em 8.009 sessoes enquanto a base seguia crescendo. No painel isso aparecia como
um canal chamado "Sem origem" com 1.269 conversas e zero sinal de compra — o
que sugeria desinteresse quando era so falta de processamento.

O ponto que demorei a enxergar, e que vale registrar pra ninguem repetir o
caminho: NENHUMA das duas tabelas depende da leitura da IA. Canal sai do utm e
das mensagens; sinal sai de palavras nas mensagens. Elas cobrem as 10.559
sessoes sem gastar um centavo de API e sem esperar a fila de leitura.

Nas duas, a regra e a do export_dataset.py, e o script se recusa a gravar se
nao reproduzir o que ja existia:

1. CANAL — link do site so vale nas 3 primeiras mensagens; gclid separa Google
   Ads de site organico; a frase automatica do Meta recupera anuncio que perdeu
   o utm. As divergencias contra a tabela antiga precisam ser atribuiveis a
   essas regras (a tabela antiga e anterior a elas).

2. SINAL — regra identica a antiga, entao a exigencia e mais dura: tem que
   bater em 99%. Reproduz 99,71%; o que sobra e mensagem que chegou depois.

Uso:
    python app/remontar_canal_sinal.py --seco    # so mostra o que mudaria
    python app/remontar_canal_sinal.py
"""

import argparse
import io
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path

BANCO = Path(__file__).resolve().parent.parent / "vendas.db"

# A tabela antiga NAO bate 100% com a regra nova, e isso e esperado: ela foi
# construida antes de duas correcoes que o export_dataset ja documenta —
# a frase automatica do Meta (recupera anuncio pago que perdeu o utm; o codigo
# diz "sem isso, 273 leads pagos ficavam contados como contato direto") e o
# link do site so valer nas 3 primeiras mensagens (auditoria de 38 casos).
#
# Por isso a trava nao e uma porcentagem cega, que so precisaria ser afrouxada
# ate passar. Ela exige que cada divergencia seja ATRIBUIVEL a uma dessas duas
# regras; o que sobra sem explicacao e que tem teto. Se aparecer divergencia
# nova em volume, alguma premissa mudou de verdade e o script para.
TETO_INEXPLICADO = 0.005   # 0,5% das linhas antigas
# O sinal e a regra ANTIGA, sem mudanca nenhuma: tem que reproduzir quase
# tudo. Divergencia so se espera pra cima, de mensagem que chegou depois.
PISO_SINAL = 0.99

NOME_CANAL = {"AF": "Anuncio (FB/IG)", "AI": "Anuncio (FB/IG)",
              "AO": "Anuncio (FB/IG)", "AX": "Anuncio (FB/IG)",
              "G": "Site (produto)", "S": "Site (produto)",
              "I": "Instagram bio", "D": "Contato direto"}

# SINAL mede estagio de fechamento, nao interesse: a conversa chegou a falar de
# pagamento e de entrega? Quem procura peca usada quase sempre quer comprar, so
# que isso nao separa ninguem — medir intencao daria "96% com sinal" e nao
# serviria pra decisao nenhuma. Falar de pix e de motoboy, sim, separa.
#
# A regra e a mesma do export_dataset.py, palavra por palavra. Tentei trocar
# pela leitura da IA e foi pior: `motivo_nao_venda` diz por que a venda falhou,
# nao ate onde a conversa chegou — "sem_estoque" (3.251 casos) so tinha sinal
# em 2,7% pela regua antiga, porque a conversa morria antes do fechamento.
PAGAMENTO = [
    "pix", "comprovante", "paguei", " pago", "pago.", "pago!", "pago,",
    "transferi", "transferência", "transferencia", "chave pix", "caiu aqui",
    "pagamento confirmado", "valor pago",
]
LOGISTICA = [
    "motoboy", "retirar", "retirada", "retire", "endereço", "endereco",
    "entrega", "entregar", "entregue", "correios", "transportadora",
    "código de rastreio", "codigo de rastreio", "rastreio", "buscar na loja",
    "vou buscar", "sedex", "loggi",
]


def _sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFKD", (t or "").lower())
                   if not unicodedata.combining(c))


def _marcadores(conn):
    """Os dois conjuntos que explicam por que a regra nova discorda da antiga:
    quem traz a frase automatica do Meta, e quem so mostrou link do site tarde
    demais na conversa pra isso contar como origem."""
    FR = [_sem_acento(x) for x in ("tenho interesse e queria mais informa",
                                   "posso ter mais informa")]
    frase_meta = {sid for sid, t in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' "
        "AND type='TEXT' AND text IS NOT NULL")
        if any(f in _sem_acento(t) for f in FR)}
    link_tardio = set()
    for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
            "AND text LIKE '%nevadaautopecas.com.br%'"):
        pr = conn.execute(
            "SELECT text FROM mensagens WHERE session_id=? AND direction='FROM_HUB' "
            "AND text IS NOT NULL ORDER BY created_at ASC LIMIT 3", (sid,)).fetchall()
        if not any("nevadaautopecas.com.br" in (t or "") for (t,) in pr):
            link_tardio.add(sid)
    return frase_meta, link_tardio


def canais(conn):
    """Devolve {session_id: nome_do_canal}. Regra copiada do export_dataset."""
    links_site = set()
    for (sid,) in conn.execute(
            "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
            "AND text LIKE '%nevadaautopecas.com.br%'"):
        primeiras = conn.execute(
            "SELECT text FROM mensagens WHERE session_id=? AND direction='FROM_HUB' "
            "AND text IS NOT NULL ORDER BY created_at ASC LIMIT 3", (sid,)).fetchall()
        if any("nevadaautopecas.com.br" in (t or "") for (t,) in primeiras):
            links_site.add(sid)

    MARCAS_GOOGLE = ["gclid=", "gad_source=", "gad_campaignid=", "gbraid=", "wbraid="]
    google_ads = {sid for sid, texto in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' "
        "AND text LIKE '%nevadaautopecas.com.br%'")
        if any(m in (texto or "").lower() for m in MARCAS_GOOGLE)}

    ig_organico = {sid for (sid,) in conn.execute(
        "SELECT DISTINCT session_id FROM mensagens WHERE direction='FROM_HUB' "
        "AND text='Vim do Instagram!'")}

    FRASES_META = [_sem_acento(x) for x in
                   ("tenho interesse e queria mais informa", "posso ter mais informa")]
    anuncio_sem_rastreio = {sid for sid, texto in conn.execute(
        "SELECT session_id, text FROM mensagens WHERE direction='FROM_HUB' "
        "AND type='TEXT' AND text IS NOT NULL")
        if sid not in links_site and any(f in _sem_acento(texto) for f in FRASES_META)}

    saida = {}
    for sid, utm in conn.execute("SELECT id, utm FROM sessoes"):
        if utm:
            fonte = json.loads(utm).get("source")
            cod = "AF" if fonte == "FACEBOOK" else "AI" if fonte == "INSTAGRAM" else "AO"
        elif sid in google_ads:
            cod = "G"
        elif sid in links_site:
            cod = "S"
        elif sid in anuncio_sem_rastreio:
            cod = "AX"
        elif sid in ig_organico:
            cod = "I"
        else:
            cod = "D"
        saida[sid] = NOME_CANAL[cod]
    return saida


def sinais(conn):
    """Devolve {session_id: classe} para TODAS as sessoes.

    Nao depende da IA — le as mensagens direto, entao cobre a base inteira sem
    gastar credito e sem depender de a leitura estar em dia.
    """
    textos = {}
    imagem = {}
    for sid, direcao, tipo, texto in conn.execute(
            "SELECT session_id, direction, type, text FROM mensagens"):
        if texto:
            textos.setdefault(sid, []).append(texto.lower())
        if direcao == "FROM_HUB" and tipo in ("IMAGE", "DOCUMENT"):
            imagem[sid] = imagem.get(sid, 0) + 1

    saida = {}
    for (sid,) in conn.execute("SELECT id FROM sessoes"):
        todo = " ".join(textos.get(sid, []))
        pagou = any(p in todo for p in PAGAMENTO)
        entregou = any(p in todo for p in LOGISTICA)
        # Foto do cliente vale como pagamento: quase sempre e o comprovante.
        if (pagou or imagem.get(sid, 0) > 0) and entregou:
            saida[sid] = "provavel"
        elif pagou or entregou:
            saida[sid] = "parcial"
        else:
            saida[sid] = "sem_sinal"
    return saida


def _conta(d):
    c = {}
    for v in d.values():
        c[v] = c.get(v, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true", help="so mostra, nao grava")
    args = ap.parse_args()

    conn = sqlite3.connect(BANCO)
    antigos_canal = dict(conn.execute("SELECT session_id, canal FROM canal"))
    antigos_classe = dict(conn.execute("SELECT session_id, classe FROM conversao"))

    novo_canal = canais(conn)
    novo_sinal = sinais(conn)

    # --- trava: toda divergencia contra a tabela antiga precisa ter explicacao ---
    comuns = [s for s in antigos_canal if s in novo_canal]
    divergentes = [s for s in comuns if antigos_canal[s] != novo_canal[s]]
    frase_meta, link_tardio = _marcadores(conn)
    por_meta = [s for s in divergentes if s in frase_meta]
    por_link = [s for s in divergentes if s not in frase_meta and s in link_tardio]
    inexplicado = [s for s in divergentes
                   if s not in frase_meta and s not in link_tardio]
    print("canal: {:,} linhas antigas, {:,} iguais, {:,} divergentes".format(
        len(comuns), len(comuns) - len(divergentes), len(divergentes)))
    print("  {:4d} anuncio recuperado pela frase automatica do Meta".format(len(por_meta)))
    print("  {:4d} link do site colado depois das 3 primeiras mensagens".format(len(por_link)))
    print("  {:4d} sem explicacao ({:.2f}% do total)".format(
        len(inexplicado), 100 * len(inexplicado) / max(len(comuns), 1)))
    if len(inexplicado) > TETO_INEXPLICADO * len(comuns):
        print("ABORTADO: divergencia inexplicada acima de {:.1f}%. "
              "Alguma premissa mudou; conferir antes de gravar.".format(
                  100 * TETO_INEXPLICADO))
        for s in inexplicado[:5]:
            print("  ex: {} era {!r}, viraria {!r}".format(
                s[:8], antigos_canal[s], novo_canal[s]))
        return 1

    # --- trava do sinal: aqui a regra e identica a antiga, entao tem que bater ---
    comuns_s = [s for s in antigos_classe if s in novo_sinal]
    iguais_s = sum(1 for s in comuns_s if antigos_classe[s] == novo_sinal[s])
    taxa_s = iguais_s / len(comuns_s) if comuns_s else 0
    print("sinal: regra reproduz {:,}/{:,} dos rotulos antigos = {:.2f}%".format(
        iguais_s, len(comuns_s), 100 * taxa_s))
    if taxa_s < PISO_SINAL:
        print("ABORTADO: a regra do sinal deveria ser a MESMA de antes. "
              "Ficou em {:.2f}%, abaixo de {:.0f}% — algo divergiu.".format(
                  100 * taxa_s, 100 * PISO_SINAL))
        for s in [x for x in comuns_s if antigos_classe[x] != novo_sinal[x]][:5]:
            print("  ex: {} era {!r}, viraria {!r}".format(
                s[:8], antigos_classe[s], novo_sinal[s]))
        return 1

    print("\ncanal  antes {:,} -> depois {:,}".format(len(antigos_canal), len(novo_canal)))
    print("  antes:  {}".format(_conta(antigos_canal)))
    print("  depois: {}".format(_conta(novo_canal)))
    print("\nsinal  antes {:,} -> depois {:,}".format(len(antigos_classe), len(novo_sinal)))
    print("  antes:  {}".format(_conta(antigos_classe)))
    print("  depois: {}".format(_conta(novo_sinal)))

    if args.seco:
        print("\n(seco: nada gravado)")
        return 0

    conn.execute("DELETE FROM canal")
    conn.executemany("INSERT INTO canal VALUES (?,?)", novo_canal.items())
    conn.execute("DELETE FROM conversao")
    conn.executemany("INSERT INTO conversao VALUES (?,?)", novo_sinal.items())
    conn.commit()
    print("\ngravado: {:,} canais, {:,} sinais".format(len(novo_canal), len(novo_sinal)))
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
