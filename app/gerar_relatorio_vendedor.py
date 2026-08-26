"""
Gera um relatorio individual (PDF) para cada vendedor.

A ideia e ser ferramenta de apoio, nao avaliacao: os tres tem resultado
praticamente igual (6,0% / 6,5% / 5,8%), entao o relatorio nao rankeia --
ele mostra, para cada um, qual habito tem o maior espaco de ganho DELE,
medido dentro dos proprios atendimentos dele.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset.json"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

TICKET = 968
VENDEDORES = ["Flávia", "Gustavo", "Matheus"]


def pct(n, total):
    return (100 * n / total) if total else 0


def fmt_pct(v, casas=1):
    return f"{v:.{casas}f}".replace(".", ",") + "%"


def fmt_brl(v):
    return "R$ " + f"{int(round(v)):,}".replace(",", ".")


def fmt_n(v):
    return f"{int(v):,}".replace(",", ".")


def taxa(rows):
    n = len(rows)
    c = sum(1 for r in rows if r["cv"] == "P")
    return n, c, pct(c, n)


def metricas(dados, vendedor):
    linhas = [x for x in dados if x["u"] == vendedor]
    eng = [x for x in linhas if x["nm"] >= 6]

    com_foto = [x for x in eng if x["fv"]]
    sem_foto = [x for x in eng if not x["fv"]]
    _, _, t_com_foto = taxa(com_foto)
    _, _, t_sem_foto = taxa(sem_foto)

    com_est = [x for x in eng if x["ne"]]
    seguiu = [x for x in com_est if x["ne"] == "seguiu"]
    parou = [x for x in com_est if x["ne"] == "parou"]
    _, _, t_seguiu = taxa(seguiu)
    _, _, t_parou = taxa(parou)

    esperando = [x for x in linhas if x["ab"]]
    rts = sorted(x["rt"] for x in linhas if x["rt"] is not None)
    n, conv, t_geral = taxa(linhas)

    return {
        "nome": vendedor,
        "n": n,
        "conv": conv,
        "taxa": t_geral,
        "receita": conv * TICKET,
        "resp_humana": pct(len(rts), n),
        "tempo_mediana": rts[len(rts) // 2] if rts else 0,
        "eng_n": len(eng),
        "foto_pct": pct(len(com_foto), len(eng)),
        "foto_com_n": len(com_foto), "foto_sem_n": len(sem_foto),
        "foto_com_t": t_com_foto, "foto_sem_t": t_sem_foto,
        "alt_pct": pct(len(seguiu), len(com_est)),
        "alt_seguiu_n": len(seguiu), "alt_parou_n": len(parou),
        "alt_seguiu_t": t_seguiu, "alt_parou_t": t_parou,
        "esperando_n": len(esperando),
        "esperando_pct": pct(len(esperando), n),
    }


def prioridade(m, media):
    """Escolhe a alavanca com maior ganho estimado para ESTE vendedor.

    Ganho = (diferenca de conversao entre fazer e nao fazer, medida nos
    proprios atendimentos dele) x (quantas vezes ele deixou de fazer).
    E estimativa correlacional -- serve pra priorizar, nao e promessa.
    """
    opcoes = []

    ganho_foto = (m["foto_com_t"] - m["foto_sem_t"]) / 100 * m["foto_sem_n"] * TICKET
    if m["foto_com_t"] > m["foto_sem_t"] and m["foto_sem_n"] >= 30:
        opcoes.append({
            "chave": "foto",
            "titulo": "Mandar foto ou vídeo da peça em mais atendimentos",
            "ganho": ganho_foto,
            "texto": (
                f"Nos seus próprios atendimentos, quando você manda foto ou vídeo a conversa fecha "
                f"<b>{fmt_pct(m['foto_com_t'])}</b> das vezes; quando não manda, <b>{fmt_pct(m['foto_sem_t'])}</b>. "
                f"É a maior diferença que aparece no seu histórico. Hoje você manda em "
                f"<b>{fmt_pct(m['foto_pct'], 0)}</b> das conversas — restam "
                f"<b>{fmt_n(m['foto_sem_n'])}</b> atendimentos engajados sem nenhuma foto."
            ),
            "acao": "Antes de responder preço, mande uma foto real da peça — mesmo que o cliente não peça.",
        })

    ganho_alt = (m["alt_seguiu_t"] - m["alt_parou_t"]) / 100 * m["alt_parou_n"] * TICKET
    if m["alt_seguiu_t"] > m["alt_parou_t"] and m["alt_parou_n"] >= 30:
        opcoes.append({
            "chave": "alternativa",
            "titulo": 'Não encerrar o atendimento no "não tenho"',
            "ganho": ganho_alt,
            "texto": (
                f"Quando você avisa que não tem a peça e <b>segue oferecendo</b> alguma saída, a conversa "
                f"fecha <b>{fmt_pct(m['alt_seguiu_t'])}</b> das vezes. Quando o atendimento para na negativa, "
                f"<b>{fmt_pct(m['alt_parou_t'])}</b>. Hoje você segue oferecendo em "
                f"<b>{fmt_pct(m['alt_pct'], 0)}</b> dos casos — em <b>{fmt_n(m['alt_parou_n'])}</b> "
                f"atendimentos a conversa parou ali."
            ),
            "acao": "Depois do \"não tenho\", ofereça similar, de outro ano, ou anote pra avisar quando chegar.",
        })

    # Metade da taxa normal: esses clientes ja esfriaram (perguntaram e ficaram sem
    # resposta, as vezes semanas atras), entao supor que converteriam como lead novo
    # seria otimista demais -- e deixaria essa alavanca ganhando das outras sem merecer.
    ganho_esp = m["esperando_n"] * m["taxa"] / 100 * TICKET * 0.5
    if m["esperando_n"] >= 30:
        opcoes.append({
            "chave": "esperando",
            "titulo": "Voltar nos clientes que ficaram sem resposta",
            "ganho": ganho_esp,
            "texto": (
                f"<b>{fmt_n(m['esperando_n'])}</b> clientes seus perguntaram alguma coisa e não tiveram "
                f"retorno (isso não conta quem só se despediu com \"obrigado\"). São "
                f"<b>{fmt_pct(m['esperando_pct'])}</b> dos seus atendimentos. Contando que boa parte já "
                f"esfriou, esse grupo ainda valeria perto de <b>{fmt_brl(ganho_esp)}</b>."
            ),
            "acao": "Reserve 15 minutos no fim do dia pra varrer conversas sem resposta sua.",
        })

    # Se a pessoa ja e melhor que a media do time naquele habito, o relatorio precisa
    # reconhecer isso -- senao soa como correcao de defeito justamente pra quem lidera.
    atual = {"foto": m["foto_pct"], "alternativa": m["alt_pct"], "esperando": m["esperando_pct"]}
    ref = {"foto": media["foto_pct"], "alternativa": media["alt_pct"], "esperando": media["esperando_pct"]}
    for o in opcoes:
        k = o["chave"]
        if k == "esperando":
            o["ja_bom"] = atual[k] < ref[k]
        else:
            o["ja_bom"] = atual[k] > ref[k]

    opcoes.sort(key=lambda o: -o["ganho"])
    return opcoes


def bloco_comparativo(m, media):
    def linha(rotulo, valor, valor_media, sufixo="%", melhor_maior=True):
        diff = valor - valor_media
        if abs(diff) < 2:
            tag = '<span class="tag neutro">na média do time</span>'
        elif (diff > 0) == melhor_maior:
            tag = '<span class="tag ok">acima da média</span>'
        else:
            tag = '<span class="tag atencao">espaço pra crescer</span>'
        v = fmt_pct(valor) if sufixo == "%" else f"{valor:.0f} min".replace(".", ",")
        vm = fmt_pct(valor_media) if sufixo == "%" else f"{valor_media:.0f} min".replace(".", ",")
        return (f"<tr><td class='nome'>{rotulo}</td><td class='n'><b>{v}</b></td>"
                f"<td class='n'>{vm}</td><td class='n'>{tag}</td></tr>")

    return (
        linha("Manda foto ou vídeo", m["foto_pct"], media["foto_pct"])
        + linha("Segue oferecendo após \"não tenho\"", m["alt_pct"], media["alt_pct"])
        + linha("Clientes deixados sem resposta", m["esperando_pct"], media["esperando_pct"], melhor_maior=False)
    )


CSS = """
@page { size: A4; margin: 14mm 13mm; }
:root {
  --ink:#1A1611; --ink-2:#4A423A; --ink-3:#7A7166;
  --line:#DDD5C7; --line-soft:#EFE9DE;
  --accent:#B4501A; --accent-soft:#FBF1E7;
  --good:#2F7A4E; --good-soft:#E6F2EA;
  --atencao:#8A6416; --atencao-soft:#FBF2DC;
  --surface:#FBF8F3;
}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);
  font-family:"Segoe UI",-apple-system,Roboto,Helvetica,Arial,sans-serif;
  font-size:10.5pt;line-height:1.5}
.page{max-width:190mm;margin:0 auto}
header{border-bottom:2.5pt solid var(--ink);padding-bottom:7pt;margin-bottom:14pt}
.marca{font-size:8pt;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  color:var(--accent);margin-bottom:3pt}
h1{font-size:22pt;font-weight:800;letter-spacing:-.02em;margin:0 0 4pt}
.subtitulo{font-size:9.5pt;color:var(--ink-3)}
h2{font-size:12.5pt;font-weight:800;margin:18pt 0 3pt;letter-spacing:-.01em;page-break-after:avoid}
.sub-h2{font-size:9pt;color:var(--ink-3);margin-bottom:8pt;page-break-after:avoid}
p{margin:0 0 7pt}
.numeros{display:flex;gap:9pt;margin-bottom:4pt;page-break-inside:avoid}
.num-card{flex:1;border:.75pt solid var(--line);background:var(--surface);
  padding:9pt 10pt;border-radius:3pt}
.num-card .rot{font-size:7.5pt;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:3pt}
.num-card .val{font-size:17pt;font-weight:800;line-height:1;letter-spacing:-.02em}
.num-card .nota{font-size:8pt;color:var(--ink-3);margin-top:3pt}
.prioridade{background:var(--accent-soft);border-left:3.5pt solid var(--accent);
  padding:12pt 14pt;margin-bottom:6pt;page-break-inside:avoid}
.prioridade .rot{font-size:7.5pt;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--accent);margin-bottom:4pt}
.prioridade .titulo{font-size:13pt;font-weight:800;margin-bottom:6pt;letter-spacing:-.01em}
.prioridade p{font-size:10pt;color:var(--ink-2);margin:0 0 7pt}
.prioridade .acao{background:#fff;border:.75pt solid var(--line);border-radius:3pt;
  padding:7pt 9pt;font-size:9.5pt;font-weight:600}
.prioridade .acao::before{content:"Na prática: ";color:var(--accent);font-weight:800}
table{width:100%;border-collapse:collapse;font-size:9.5pt;margin:4pt 0 6pt;page-break-inside:avoid}
th{text-align:left;font-size:7.5pt;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
  color:var(--ink-3);padding:0 6pt 4pt 0;border-bottom:.75pt solid var(--line)}
th.n,td.n{text-align:right}
td{padding:5pt 6pt 5pt 0;border-bottom:.5pt solid var(--line-soft);font-variant-numeric:tabular-nums}
td.nome{font-variant-numeric:normal;font-weight:600}
.tag{display:inline-block;font-size:7.5pt;font-weight:700;padding:1pt 5pt;border-radius:8pt;white-space:nowrap}
.tag.ok{background:var(--good-soft);color:var(--good)}
.tag.atencao{background:var(--atencao-soft);color:var(--atencao)}
.tag.neutro{background:#F0EDE6;color:var(--ink-3)}
.playbook{display:flex;flex-direction:column;gap:6pt}
.pb{border:.75pt solid var(--line);border-radius:3pt;padding:9pt 11pt;page-break-inside:avoid}
.pb-t{font-size:10pt;font-weight:800;margin-bottom:2pt}
.pb p{font-size:9.5pt;color:var(--ink-2);margin:0}
.pb .ev{font-size:8.5pt;color:var(--accent);font-weight:700;margin-top:3pt}
.nota-final{background:var(--good-soft);border-left:3.5pt solid var(--good);
  padding:10pt 12pt;font-size:9.5pt;color:var(--ink-2);page-break-inside:avoid;margin-top:6pt}
.metodo{background:var(--surface);border:.75pt solid var(--line);border-radius:3pt;
  padding:9pt 11pt;font-size:8.5pt;color:var(--ink-2);page-break-inside:avoid}
footer{margin-top:14pt;padding-top:7pt;border-top:.75pt solid var(--line);
  font-size:8pt;color:var(--ink-3)}
"""


def gerar_html(m, media, opcoes):
    p = opcoes[0]
    secundarias = opcoes[1:3]

    sec_html = ""
    for o in secundarias:
        sec_html += (
            f'<div class="pb"><div class="pb-t">{o["titulo"]}</div>'
            f'<p>{o["texto"]}</p>'
            f'<div class="ev">Potencial estimado: {fmt_brl(o["ganho"])}</div></div>'
        )

    return f"""<title>Relatório — {m['nome']}</title>
<style>{CSS}</style>
<div class="page">
  <header>
    <div class="marca">Nevada Ecopeças · Apoio ao time comercial</div>
    <h1>{m['nome']}</h1>
    <div class="subtitulo">
      Seus atendimentos no WhatsApp entre 7 de julho e 21 de agosto de 2026 &nbsp;·&nbsp;
      Documento de apoio — não é avaliação de desempenho
    </div>
  </header>

  <h2>Seus números no período</h2>
  <div class="numeros">
    <div class="num-card">
      <div class="rot">Atendimentos</div>
      <div class="val">{fmt_n(m['n'])}</div>
      <div class="nota">time: {fmt_n(media['n'])} em média</div>
    </div>
    <div class="num-card">
      <div class="rot">Vendas prováveis</div>
      <div class="val">{fmt_n(m['conv'])}</div>
      <div class="nota">{fmt_pct(m['taxa'])} dos seus atendimentos</div>
    </div>
    <div class="num-card">
      <div class="rot">Receita estimada</div>
      <div class="val">{fmt_brl(m['receita'])}</div>
      <div class="nota">ticket médio de {fmt_brl(TICKET)}</div>
    </div>
    <div class="num-card">
      <div class="rot">Tempo de resposta</div>
      <div class="val">{str(round(m['tempo_mediana'])).replace('.', ',')} min</div>
      <div class="nota">mediana até sua 1ª resposta</div>
    </div>
  </div>

  <h2>Sua maior oportunidade agora</h2>
  <div class="sub-h2">
    Escolhida pelo tamanho do ganho possível <b>nos seus próprios atendimentos</b> — não é a mesma para todo mundo
  </div>
  <div class="prioridade">
    <div class="rot">Prioridade individual</div>
    <div class="titulo">{p['titulo']}</div>
    {'<p style="font-size:9.5pt;color:var(--good);font-weight:700">Você já está acima da média do time nesse ponto — aqui é refino, não correção.</p>' if p.get('ja_bom') else ''}
    <p>{p['texto']}</p>
    <p style="font-size:9.5pt"><b>Potencial estimado: {fmt_brl(p['ganho'])}</b> no mesmo intervalo de 45 dias,
    se esse hábito virasse rotina.</p>
    <div class="acao">{p['acao']}</div>
  </div>

  <h2>Seus hábitos, comparados com a média do time</h2>
  <div class="sub-h2">Medido só em conversas engajadas ({fmt_n(m['eng_n'])} suas), pra comparar situações parecidas</div>
  <table>
    <thead>
      <tr><th>Hábito</th><th class="n">Você</th><th class="n">Média do time</th><th class="n">&nbsp;</th></tr>
    </thead>
    <tbody>{bloco_comparativo(m, media)}</tbody>
  </table>

  <h2>Outras alavancas suas</h2>
  <div class="sub-h2">Em ordem de potencial, calculadas do mesmo jeito</div>
  <div class="playbook">{sec_html}</div>

  <h2>O que vale para todo mundo</h2>
  <div class="sub-h2">Padrões testados nos 8.009 atendimentos do time no período</div>
  <div class="playbook">
    <div class="pb">
      <div class="pb-t">Responder rápido demais não é o melhor</div>
      <p>Atendimento respondido em menos de 15 minutos converte 5,3%. Respondido entre 15 e 60 minutos,
      9,6%. Vale o minuto a mais para já vir com preço, foto ou a informação exata que o cliente pediu,
      em vez de um "oi, pois não" imediato.</p>
      <div class="ev">15–60 min: 9,6% &nbsp;·&nbsp; menos de 15 min: 5,3%</div>
    </div>
    <div class="pb">
      <div class="pb-t">Conversa rasa quase nunca vira venda</div>
      <p>70,7% dos atendimentos terminam com 5 mensagens ou menos do vendedor, e esses convertem 0,6%.
      Não é "mandar muita mensagem" — é que desistir cedo demais fecha a porta. Passar da quinta troca
      é onde a venda começa a existir.</p>
      <div class="ev">até 5 mensagens: 0,6% &nbsp;·&nbsp; 11 ou mais: 34,4%</div>
    </div>
    <div class="pb">
      <div class="pb-t">Puxar assunto funciona melhor que esperar</div>
      <p>Quando a loja inicia o contato em vez de esperar o cliente chegar, a conversão é 18,9% contra
      7,7%. Cliente antigo parado raramente volta sozinho.</p>
      <div class="ev">loja inicia: 18,9% &nbsp;·&nbsp; cliente chega sozinho: 7,7%</div>
    </div>
    <div class="pb">
      <div class="pb-t">Motor é a peça que mais converte</div>
      <p>Atendimento sobre motor fecha 9,7% das vezes, contra 6,1% da média geral — e é também a
      categoria mais procurada. Quando o assunto for motor, vale investir mais tempo no atendimento.</p>
      <div class="ev">motor: 9,7% &nbsp;·&nbsp; média geral: 6,1%</div>
    </div>
  </div>

  <div class="nota-final">
    <b>Uma observação importante:</b> os três vendedores do time convertem praticamente igual
    ({fmt_pct(media['taxa'])} de média, com diferença menor que um ponto entre o primeiro e o último) e
    recebem a mesma mistura de leads. Este relatório não é ranking nem cobrança — cada um tem um hábito
    diferente com mais espaço para crescer, e é isso que ele aponta.
  </div>

  <h2>Como esses números foram medidos</h2>
  <div class="metodo">
    <p><b>Venda provável.</b> Não existe integração entre o WhatsApp e o sistema de vendas, então a venda é
    identificada pelo conteúdo da conversa: sinais de pagamento (pix, comprovante, transferência) junto com
    sinais de entrega (motoboy, retirada, endereço, rastreio). Conferido contra as vendas reais do portal de
    comissões, o método encontra 89% delas — então seus números aqui tendem a estar levemente subestimados.</p>
    <p><b>Comparações de hábito.</b> Sempre feitas dentro de conversas já engajadas (6 ou mais respostas suas),
    para não confundir "atendimento que fechou" com "atendimento que naturalmente teve mais mensagens".</p>
    <p><b>Potencial estimado.</b> É a diferença de conversão observada nos seus atendimentos multiplicada
    pelas vezes em que o hábito não aconteceu. Serve para priorizar o que atacar primeiro — não é uma
    promessa de faturamento.</p>
    <p><b>Ponto cego conhecido.</b> Cerca de 15% das conversas têm áudios do cliente, que não são transcritos
    e ficam de fora da análise. Vendas fechadas por telefone ou presencialmente também não aparecem.</p>
  </div>

  <footer>
    Nevada Ecopeças · Dados de 7/7/2026 a 21/8/2026, extraídos da plataforma de atendimento · Documento interno de apoio
  </footer>
</div>
"""


def main():
    dados = json.loads(DATASET.read_text(encoding="utf-8"))
    todas = {v: metricas(dados, v) for v in VENDEDORES}

    media = {
        k: sum(m[k] for m in todas.values()) / len(todas)
        for k in ["n", "taxa", "foto_pct", "alt_pct", "esperando_pct", "tempo_mediana"]
    }

    gerados = []
    for v in VENDEDORES:
        m = todas[v]
        opcoes = prioridade(m, media)
        html = gerar_html(m, media, opcoes)

        temp = ROOT / f"_rel_{v}.html"
        doc = ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
               + html.replace('<div class="page">', "</head><body>" + '<div class="page">', 1)
               + "</body></html>")
        temp.write_text(doc, encoding="utf-8")

        saida = ROOT / f"Relatorio_{v.replace('á','a').replace('í','i')}_jul-ago2026.pdf"
        subprocess.run(
            [str(CHROME), "--headless", "--disable-gpu", "--no-sandbox",
             f"--print-to-pdf={saida}", "--no-pdf-header-footer", temp.as_uri()],
            check=True, capture_output=True,
        )
        temp.unlink(missing_ok=True)
        gerados.append((v, saida, opcoes[0]["titulo"], opcoes[0]["ganho"]))
        print(f"{v:10s} -> {saida.name} ({saida.stat().st_size/1024:.0f} KB)")
        print(f"           prioridade: {opcoes[0]['titulo']} ({fmt_brl(opcoes[0]['ganho'])})")

    return gerados


if __name__ == "__main__":
    main()
