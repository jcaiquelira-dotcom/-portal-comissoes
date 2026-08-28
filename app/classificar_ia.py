"""
Le cada atendimento com a API da Anthropic e classifica o que a heuristica de
palavra-chave nao consegue: se converteu de fato, por que nao converteu, qual
peca o cliente queria, se tinhamos a peca e se e oficina ou consumidor final.

Uso:
    python app/classificar_ia.py --limite 300      # teste
    python app/classificar_ia.py                   # base inteira

Guarda em vendas.db, tabela classificacao_ia. E idempotente: pula o que ja
foi classificado, entao da pra interromper e retomar sem perder credito.
"""

import argparse
import json
import os
import sqlite3
import sys
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from config import env

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "vendas.db"
MODELO = "claude-sonnet-5"  # 100% de acordo com Opus 5 no campo venda, 59% mais barato
CONCORRENCIA = 12
GRAVAR_A_CADA = 50   # salva parcial: rodada longa nao pode perder tudo numa queda
MAX_CHARS_CONVERSA = 12000  # conversas gigantes viram cauda; corta o meio, nao o fim


def carregar_chave():
    os.environ["ANTHROPIC_API_KEY"] = env("ANTHROPIC_API_KEY")
    return True


class Analise(BaseModel):
    """O que queremos saber de cada atendimento."""

    virou_venda: bool = Field(
        description="True apenas se a conversa mostra que a compra foi concluida "
        "(pagamento confirmado, peca entregue ou retirada, cliente agradecendo a compra). "
        "Negociacao em andamento, promessa de retorno ou 'vou pensar' e False."
    )
    confianca_venda: str = Field(
        description="'alta' quando ha prova explicita (comprovante, entrega confirmada, "
        "rastreio), 'media' quando e provavel mas indireto, 'baixa' quando e palpite."
    )
    motivo_nao_venda: str = Field(
        description="Se virou_venda for False, o motivo REAL em uma destas opcoes: "
        "'sem_estoque' (nao tinhamos a peca), 'preco' (achou caro/pediu desconto e desistiu), "
        "'frete' (custo ou prazo de entrega), 'pagamento' (forma de pagamento inviabilizou), "
        "'sem_resposta' (a loja nao respondeu ou demorou demais), "
        "'cliente_sumiu' (a loja respondeu tudo e o cliente parou de responder), "
        "'so_pesquisando' (cliente so queria informacao ou preco), "
        "'peca_errada' (nao servia para o veiculo dele), "
        "'outro'. Se virou_venda for True, use 'nao_aplica'."
    )
    peca_procurada: str = Field(
        description="A peca que o cliente queria, com veiculo e ano se ele disse. "
        "Ex: 'farol dianteiro esquerdo Jetta 2015'. String vazia se nao der pra saber."
    )
    tinhamos_a_peca: str = Field(
        description="'sim' se a loja confirmou ter, 'nao' se disse que nao tinha, "
        "'parcial' se tinha algo parecido ou de outro ano, 'indefinido' se nunca ficou claro."
    )
    tipo_cliente: str = Field(
        description="'oficina' se e mecanico, funilaria, loja, retifica ou revenda. Sinais: "
        "pede varias pecas de uma vez, diz 'meu cliente' ou 'do cliente', pede nota fiscal ou "
        "CNPJ, pergunta preco de parceiro/atacado, usa nome tecnico da peca sem falar do "
        "proprio carro, ja comprou antes. 'consumidor' se e dono do proprio carro. Sinais: "
        "diz 'meu carro' ou cita o proprio veiculo, descreve o defeito, pergunta se serve no "
        "dele, pede ajuda pra identificar a peca. Infira pelo sinal mais forte; use "
        "'indefinido' so quando nao houver nenhum sinal dos dois lados."
    )
    resumo: str = Field(
        description="Uma frase curta, em portugues, dizendo o que aconteceu no atendimento."
    )


INSTRUCOES = """Voce esta analisando atendimentos de WhatsApp de um desmonte de veiculos \
(venda de pecas usadas). Leia a conversa e responda de forma objetiva.

Convencoes da conversa:
- "CLIENTE:" e quem esta comprando.
- "LOJA:" e o vendedor. Mensagens da loja que comecam com *Nome:* foram escritas por
  uma pessoa; as demais podem ser automaticas (robo de atendimento).
- Mensagens como <IMAGE>, <AUDIO> indicam midia que voce nao consegue ver ou ouvir.
  Considere que existem, mas nao invente o conteudo delas.

Seja conservador: se a conversa nao deixa claro que a compra foi concluida, virou_venda
e False. Nao suponha venda so porque houve interesse ou porque um preco foi passado.

Responda sempre no formato estruturado pedido."""


def montar_conversa(conn, sid):
    msgs = conn.execute(
        "SELECT direction, type, text, raw FROM mensagens WHERE session_id=? "
        "ORDER BY created_at ASC", (sid,)
    ).fetchall()
    linhas = []
    for direcao, tipo, texto, raw in msgs:
        if tipo in ("TRACK", "NOTE"):
            continue
        quem = "CLIENTE" if direcao == "FROM_HUB" else "LOJA"
        corpo = " ".join((texto or "").split()) if texto else f"<{tipo}>"
        if not corpo:
            continue
        linhas.append(f"{quem}: {corpo}")
    texto = "\n".join(linhas)
    if len(texto) > MAX_CHARS_CONVERSA:
        meio = MAX_CHARS_CONVERSA // 2
        texto = texto[:meio] + "\n[...trecho do meio omitido...]\n" + texto[-meio:]
    return texto


def preparar_tabela(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classificacao_ia (
            session_id TEXT PRIMARY KEY,
            virou_venda INTEGER,
            confianca_venda TEXT,
            motivo_nao_venda TEXT,
            peca_procurada TEXT,
            tinhamos_a_peca TEXT,
            tipo_cliente TEXT,
            resumo TEXT,
            tokens_entrada INTEGER,
            tokens_saida INTEGER,
            modelo TEXT
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(classificacao_ia)")}
    if "modelo" not in cols:
        conn.execute("ALTER TABLE classificacao_ia ADD COLUMN modelo TEXT")
        conn.execute("UPDATE classificacao_ia SET modelo='claude-opus-5' WHERE modelo IS NULL")
    conn.commit()


_t0 = time.time()
_lock = threading.Lock()
_custo = {"entrada": 0, "saida": 0, "erros": 0, "feitos": 0, "cache_lido": 0, "cache_escrito": 0}


def classificar_uma(client, sid, conversa, tentativas=5):
    for n in range(tentativas):
        try:
            return _chamar(client, conversa)
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.InternalServerError):
            if n == tentativas - 1:
                raise
            time.sleep(min(60, 2 ** n) + random.random() * 2)


def _chamar(client, conversa):
    r = client.messages.parse(
        model=MODELO,
        max_tokens=2000,
        system=[{"type": "text", "text": INSTRUCOES,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "CONVERSA:" + chr(10) + conversa}],
        output_format=Analise,
    )
    u = r.usage
    return (r.parsed_output, u.input_tokens,
            u.output_tokens, getattr(u, "cache_read_input_tokens", 0) or 0,
            getattr(u, "cache_creation_input_tokens", 0) or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="quantas conversas classificar")
    ap.add_argument("--desde", type=str, default=None, help="so atendimentos criados a partir de AAAA-MM-DD")
    ap.add_argument("--ate", type=str, default=None, help="so atendimentos criados ate AAAA-MM-DD (inclusive)")
    args = ap.parse_args()

    if not carregar_chave():
        sys.exit("ANTHROPIC_API_KEY nao encontrada no .env")

    conn = sqlite3.connect(DB)
    preparar_tabela(conn)
    ja_feitas = {r[0] for r in conn.execute("SELECT session_id FROM classificacao_ia")}

    # prioriza conversas com substancia: as rasas nao tem o que classificar
    q = "SELECT id FROM sessoes"
    filtros, params = [], []
    if args.desde:
        filtros.append("substr(created_at,1,10) >= ?"); params.append(args.desde)
    if args.ate:
        filtros.append("substr(created_at,1,10) <= ?"); params.append(args.ate)
    if filtros:
        q += " WHERE " + " AND ".join(filtros)
    params = tuple(params)
    candidatos = []
    for (sid,) in conn.execute(q, params):
        if sid in ja_feitas:
            continue
        candidatos.append(sid)

    print(f"montando {len(candidatos)} conversas do banco...", flush=True)
    conversas = {}
    for sid in candidatos:
        txt = montar_conversa(conn, sid)
        if len(txt) < 40:  # praticamente vazia
            continue
        conversas[sid] = txt
    conn.close()

    alvo = list(conversas)[: args.limite] if args.limite else list(conversas)
    print(f"a classificar: {len(alvo)} conversas com {MODELO} "
          f"(ja feitas antes: {len(ja_feitas)})", flush=True)
    if not alvo:
        return

    client = anthropic.Anthropic()

    def tarefa(sid):
        try:
            res, ent, sai, cache_r, cache_w = classificar_uma(client, sid, conversas[sid])
            with _lock:
                _custo["entrada"] += ent
                _custo["saida"] += sai
                _custo["cache_lido"] += cache_r
                _custo["cache_escrito"] += cache_w
                _custo["feitos"] += 1
                if _custo["feitos"] % 100 == 0:
                    passou = time.time() - _t0
                    ritmo = _custo["feitos"] / passou
                    falta = (len(alvo) - _custo["feitos"]) / ritmo / 60
                    print(f"  {_custo['feitos']}/{len(alvo)}  "
                          f"({ritmo*60:.0f}/min, faltam ~{falta:.0f} min)", flush=True)
            return sid, res, ent, sai
        except Exception as e:  # rede, rate limit, recusa — registra e segue
            with _lock:
                _custo["erros"] += 1
                if _custo["erros"] <= 3:
                    print(f"  [erro] {sid[:8]}: {type(e).__name__}: {str(e)[:120]}")
            return None

    conn = sqlite3.connect(DB)
    preparar_tabela(conn)

    def gravar(lote):
        conn.executemany(
            "INSERT OR REPLACE INTO classificacao_ia VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(sid, int(a.virou_venda), a.confianca_venda, a.motivo_nao_venda,
              a.peca_procurada, a.tinhamos_a_peca, a.tipo_cliente, a.resumo, ent, sai, MODELO)
             for sid, a, ent, sai in lote],
        )
        conn.commit()

    total = 0
    pendentes = []
    with ThreadPoolExecutor(max_workers=CONCORRENCIA) as pool:
        for fut in as_completed([pool.submit(tarefa, s) for s in alvo]):
            r = fut.result()
            if not r:
                continue
            pendentes.append(r)
            if len(pendentes) >= GRAVAR_A_CADA:
                gravar(pendentes)
                total += len(pendentes)
                pendentes = []
    if pendentes:
        gravar(pendentes)
        total += len(pendentes)
    conn.close()
    resultados = [None] * total  # so o tamanho importa daqui pra frente

    p_ent, p_sai = {"claude-opus-5": (5, 25), "claude-sonnet-5": (3, 15),
                    "claude-haiku-4-5-20251001": (1, 5)}.get(MODELO, (5, 25))
    custo = (_custo["entrada"] / 1e6 * p_ent
             + _custo["saida"] / 1e6 * p_sai
             + _custo["cache_lido"] / 1e6 * p_ent * 0.1
             + _custo["cache_escrito"] / 1e6 * p_ent * 1.25)
    print(f"\nclassificadas: {len(resultados)} | erros: {_custo['erros']}")
    print(f"tokens: entrada {_custo['entrada']:,} | saida {_custo['saida']:,}")
    print(f"cache: lido {_custo['cache_lido']:,} | escrito {_custo['cache_escrito']:,}")
    print(f"custo desta rodada: US$ {custo:.2f}  ({MODELO})")
    if resultados:
        print(f"media por conversa: US$ {custo/len(resultados):.4f}")
        print(f"projecao p/ base inteira (8.143): US$ {custo/len(resultados)*8143:.2f}")


if __name__ == "__main__":
    main()
