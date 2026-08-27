/* ============================================================
   Painel de desempenho — desenho compartilhado
   ============================================================
   Carregado pela área do gestor (admin.html) e pelo portal do vendedor
   (index.html). O mesmo código desenha os dois: o que o gestor vê de um
   vendedor é exatamente o que o vendedor vê de si, e uma correção aqui vale
   nas duas telas de uma vez.

   Quem muda entre as duas é só o que o servidor manda: o vendedor não recebe
   participação no faturamento do time, que deixaria o total da equipe deduzível.

   Depende de fmtMoeda(), definida em cada tela.
   ============================================================ */

function dpVar(pct, invertido){
  // invertido: em tempo de resposta, subir e ruim.
  if(pct === null || pct === undefined) return '<span class="dp-var neutro">sem base anterior</span>';
  const sobe = pct > 0;
  const bom = invertido ? !sobe : sobe;
  const classe = Math.abs(pct) < 0.05 ? 'neutro' : (bom ? 'sobe' : 'desce');
  const seta = Math.abs(pct) < 0.05 ? '' : (sobe ? '▲' : '▼');
  return `<span class="dp-var ${classe}">${seta} ${Math.abs(pct).toFixed(1)}% vs mês anterior</span>`;
}

function dpKpi(rot, num, extra){
  return `<div class="dp-kpi"><div class="rot">${rot}</div>
    <div class="num valor-money">${num}</div>${extra || ''}</div>`;
}

/* barras verticais + linha do ticket medio no mesmo desenho: e a leitura que
   importa — faturar mais vendendo mais barato nao e a mesma coisa. */

function dpGraficoHistorico(historico){
  if(!historico.length) return '<div class="vazio">Sem histórico ainda.</div>';
  const L = 44, R = 52, T = 14, B = 26, alt = 190;
  const larg = Math.max(320, historico.length * 76);
  const maxTotal = Math.max(...historico.map(h => h.total), 1);
  const maxTicket = Math.max(...historico.map(h => h.ticket), 1);
  const passo = (larg - L - R) / historico.length;
  const larguraBarra = Math.min(38, passo * 0.55);

  const barras = historico.map((h, i) => {
    const x = L + passo * i + (passo - larguraBarra) / 2;
    const altura = (h.total / maxTotal) * (alt - T - B);
    const y = alt - B - altura;
    const cor = h.bateu ? 'var(--good)' : 'var(--accent)';
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${larguraBarra.toFixed(1)}"
              height="${Math.max(2, altura).toFixed(1)}" rx="4" fill="${cor}" opacity=".85">
              <title>${h.mes}: ${fmtMoeda(h.total)}${h.bateu ? ' (bateu a meta)' : ''}</title></rect>`;
  }).join('');

  const pontos = historico.map((h, i) => {
    const x = L + passo * i + passo / 2;
    const y = alt - B - (h.ticket / maxTicket) * (alt - T - B);
    return [x, y];
  });
  const linha = `<polyline fill="none" stroke="var(--info)" stroke-width="2"
     stroke-linejoin="round" points="${pontos.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ')}"/>`
    + pontos.map(([x, y], i) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.2"
        fill="var(--surface)" stroke="var(--info)" stroke-width="2">
        <title>ticket ${fmtMoeda(historico[i].ticket)}</title></circle>`).join('');

  const rotulos = historico.map((h, i) => {
    const x = L + passo * i + passo / 2;
    const [ano, m] = h.mes.split('-');
    const nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
    return `<text x="${x.toFixed(1)}" y="${alt - 8}" text-anchor="middle"
             font-size="10" fill="var(--muted)">${nomes[+m - 1]}</text>`;
  }).join('');

  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">
      <line x1="${L}" y1="${alt - B}" x2="${larg - R}" y2="${alt - B}" stroke="var(--line)"/>
      ${barras}${linha}${rotulos}
      <text x="2" y="${T + 4}" font-size="9.5" fill="var(--muted)">${fmtMoeda(maxTotal)}</text>
      <text x="${larg - R + 6}" y="${T + 4}" font-size="9.5" fill="var(--info)">ticket</text>
    </svg></div>
    <div class="dp-aviso">Barras = faturamento do mês (verde quando bateu a meta atual).
      Linha azul = ticket médio.</div>`;
}

function dpSerieDia(serie){
  if(!serie.length) return '<div class="vazio">Nenhuma venda neste mês.</div>';
  const alt = 110, larg = Math.max(320, serie.length * 22);
  const max = Math.max(...serie.map(s => s.total), 1);
  const passo = larg / serie.length;
  const barras = serie.map((s, i) => {
    const h = (s.total / max) * (alt - 22);
    return `<rect x="${(passo * i + passo * 0.18).toFixed(1)}" y="${(alt - 16 - h).toFixed(1)}"
      width="${(passo * 0.64).toFixed(1)}" height="${Math.max(2, h).toFixed(1)}" rx="3"
      fill="var(--accent)" opacity=".8"><title>${s.data.slice(8)}/${s.data.slice(5,7)}: ${fmtMoeda(s.total)}</title></rect>
      <text x="${(passo * i + passo / 2).toFixed(1)}" y="${alt - 4}" text-anchor="middle"
        font-size="8.5" fill="var(--muted)">${s.data.slice(8)}</text>`;
  }).join('');
  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">${barras}</svg></div>`;
}

function dpBarrasLista(itens, formatar){
  const max = Math.max(...itens.map(i => i.peso), 1);
  return '<div class="dp-lista">' + itens.map(i => `
    <div class="dp-linha">
      <span class="nome">${i.nome}</span>
      <span class="v">${formatar(i)}</span>
      <span class="t"><span class="f" style="width:${Math.max(2, (i.peso / max) * 100).toFixed(1)}%"></span></span>
    </div>`).join('') + '</div>';
}

function renderDesempenho(d){
  const v = d.vendedor;
  document.getElementById('dpNome').textContent = v.nome;
  document.getElementById('dpFoto').outerHTML =
    avatarHtml(v, 'dp-foto').replace('class="dp-foto', 'id="dpFoto" class="dp-foto');
  const pos = d.time.posicao;
  document.getElementById('dpSub').textContent =
    `${pos}º de ${d.time.de} no mês · ${d.time.participacao}% do faturamento do time`;

  const a = d.atual, meta = d.meta, r = d.ritmo, at = d.atendimento;

  // ---- KPIs ----
  const kpis = [
    dpKpi('Faturamento', fmtMoeda(a.total), dpVar(d.variacao.total)),
    dpKpi('Vendas', a.qtd, dpVar(d.variacao.qtd)),
    dpKpi('Ticket médio', fmtMoeda(a.ticket), dpVar(d.variacao.ticket)),
    dpKpi('Comissão', fmtMoeda(d.comissao.valor),
          `<span class="dp-var neutro">${v.percentual}%${d.comissao.bonus ? ' · bônus ' + fmtMoeda(d.comissao.bonus) : ''}</span>`),
  ].join('');

  // ---- meta ----
  let cardMeta;
  if(meta.mensal){
    const pct = meta.pct || 0;
    const ver = classificarRitmo(pct, r.pct_do_mes);
    cardMeta = `<div class="card">
      <p class="card-titulo">Meta do mês</p>
      <div class="dp-kpi" style="border:none;padding:0;background:none;">
        <div class="num" style="font-size:30px;color:${ver.cor}">${Math.round(pct)}%</div>
        <span class="dp-selo ${ver.classe === 'bom' ? 'bom' : (ver.classe === 'ruim' ? 'ruim' : 'neutro')}">${ver.texto}</span>
      </div>
      <div class="dp-destaque"><span class="rot">Meta</span><span class="val valor-money">${fmtMoeda(meta.mensal)}</span></div>
      <div class="dp-destaque"><span class="rot">Falta</span><span class="val valor-money">${meta.falta > 0 ? fmtMoeda(meta.falta) : 'batida'}</span></div>
      <div class="dp-destaque"><span class="rot">Projeção no ritmo atual</span><span class="val valor-money">${fmtMoeda(r.projecao)}</span></div>
      <div class="dp-destaque"><span class="rot">Meses que bateu</span><span class="val">${meta.batidas} de ${meta.meses_com_venda}</span></div>
      <div class="dp-aviso">Dia ${r.dias_corridos} de ${r.dias_no_mes} (${r.pct_do_mes}% do mês).
        “Meses que bateu” compara o histórico com a meta de hoje — o portal não guarda a meta que valia em cada mês.</div>
    </div>`;
  }else{
    cardMeta = `<div class="card"><p class="card-titulo">Meta do mês</p>
      <div class="vazio">Sem meta definida. Configure em
      <a href="#configuracoes/metas">Configurações · Metas</a>.</div></div>`;
  }

  // ---- funil de atendimento ----
  let cardFunil;
  if(at){
    const passos = [
      {rot: 'Clientes atendidos', n: at.atendimentos, taxa: ''},
      {rot: 'Deram sinal de compra no chat', n: at.sinal_venda, taxa: at.pct_sinal + '% dos atendidos'},
      {rot: 'Vendas lançadas no portal', n: at.vendas_no_mes, taxa: at.taxa_conversao + '% dos atendidos'},
    ];
    const maxN = Math.max(...passos.map(p => p.n), 1);
    cardFunil = `<div class="card">
      <p class="card-titulo">Do atendimento à venda</p>
      <div class="funil">
        ${passos.map(p => `<div class="funil-passo">
            <div class="funil-rot">${p.rot}</div>
            <div class="funil-barra" style="width:${(22 + (p.n / maxN) * 78).toFixed(1)}%">${p.n}</div>
            ${p.taxa ? `<span class="funil-taxa">${p.taxa}</span>` : ''}
          </div>`).join('')}
      </div>
      <div class="dp-aviso">A taxa de conversão usa as vendas lançadas no portal, não o que a IA
        detectou no chat: o fechamento acontece fora da conversa, então o número do chat enxerga só
        uma parte. Dados do Totalk sincronizados em ${(at.gerado_em || '').slice(8,10)}/${(at.gerado_em || '').slice(5,7)}.</div>
    </div>`;
  }else{
    cardFunil = `<div class="card"><p class="card-titulo">Do atendimento à venda</p>
      <div class="vazio">Este vendedor não atende pelo Totalk, então não há dado de atendimento.</div></div>`;
  }

  // ---- tempo de resposta + canais ----
  let cardAtendimento = '';
  if(at){
    const min = at.resposta_mediana_min;
    const grave = min !== null && min > 60;
    const canais = Object.entries(at.canais || {})
      .map(([nome, n]) => ({nome, peso: n, n}))
      .sort((x, y) => y.n - x.n);
    cardAtendimento = `<div class="card">
      <p class="card-titulo">Atendimento</p>
      <div class="dp-kpi" style="border:none;padding:0;background:none;margin-bottom:10px;">
        <div class="rot">Tempo até a primeira resposta (mediana)</div>
        <div class="num" style="color:${grave ? 'var(--bad)' : 'var(--good)'}">
          ${min === null ? '—' : (min >= 60 ? (min / 60).toFixed(1) + ' h' : Math.round(min) + ' min')}</div>
        <span class="dp-var neutro">${at.com_resposta} de ${at.atendimentos} atendimentos responderam</span>
      </div>
      <p class="card-titulo" style="margin:14px 0 9px;">De onde vieram</p>
      ${canais.length ? dpBarrasLista(canais, i => i.n) : '<div class="vazio">Sem canal registrado.</div>'}
    </div>`;
  }

  // ---- consistência ----
  const cardConsistencia = `<div class="card">
    <p class="card-titulo">Consistência no mês</p>
    <div class="dp-destaque"><span class="rot">Dias com venda</span><span class="val">${a.dias_ativos} de ${r.dias_corridos}</span></div>
    <div class="dp-destaque"><span class="rot">Média por dia ativo</span><span class="val valor-money">${fmtMoeda(a.media_dia_ativo)}</span></div>
    <div class="dp-destaque"><span class="rot">Melhor dia</span><span class="val valor-money">${
      d.melhor_dia ? d.melhor_dia.data.slice(8) + '/' + d.melhor_dia.data.slice(5,7) + ' · ' + fmtMoeda(d.melhor_dia.total) : '—'}</span></div>
    <div class="dp-destaque"><span class="rot">Maior venda</span><span class="val valor-money">${
      d.maior_venda ? fmtMoeda(d.maior_venda.valor) : '—'}</span></div>
    ${d.maior_venda ? `<div class="dp-aviso">${d.maior_venda.produto}</div>` : ''}
  </div>`;

  // ---- faixas ----
  const faixas = d.faixas.filter(f => f.qtd > 0).map(f => ({nome: f.rotulo, peso: f.qtd, ...f}));
  const cardFaixas = `<div class="card">
    <p class="card-titulo">Perfil das vendas</p>
    ${faixas.length ? dpBarrasLista(faixas, i => `${i.qtd} · ${fmtMoeda(i.total)}`)
                    : '<div class="vazio">Nenhuma venda neste mês.</div>'}
    <div class="dp-aviso">Mostra se o mês veio de muita peça barata ou de poucas caras — dois caminhos
      bem diferentes pro mesmo faturamento.</div>
  </div>`;

  // ---- follow-up ----
  const f = d.followup;
  const cardFollow = f ? `<div class="card">
    <p class="card-titulo">Follow-up</p>
    <div class="dp-destaque"><span class="rot">Clientes na fila</span><span class="val">${f.total}</span></div>
    <div class="dp-destaque"><span class="rot">Já trabalhados</span><span class="val">${f.trabalhados} · ${f.pct_trabalhado}%</span></div>
    <div class="dp-destaque"><span class="rot">Responderam</span><span class="val">${f.respondeu}${f.trabalhados ? ' · ' + f.pct_resposta + '%' : ''}</span></div>
    <div class="dp-destaque"><span class="rot">Fecharam venda</span><span class="val">${f.vendeu}</span></div>
    <div class="dp-destaque"><span class="rot">Não vai rolar</span><span class="val">${f.perdido}</span></div>
  </div>` : `<div class="card"><p class="card-titulo">Follow-up</p>
    <div class="vazio">Nenhuma fila sincronizada para este vendedor.</div></div>`;

  // ---- maiores vendas ----
  const cardMaiores = `<div class="card">
    <p class="card-titulo">Maiores vendas do mês</p>
    ${d.maiores_vendas.length ? `<div class="dp-lista">${d.maiores_vendas.map(m => `
      <div class="dp-linha">
        <span class="nome">${m.produto}</span>
        <span class="v">${m.data.slice(8)}/${m.data.slice(5,7)} · ${fmtMoeda(m.valor)}</span>
      </div>`).join('')}</div>` : '<div class="vazio">Nenhuma venda neste mês.</div>'}
  </div>`;

  document.getElementById('dpCorpo').innerHTML = `
    <div class="dp-kpis">${kpis}</div>
    <div class="dp-grid duas">${cardMeta}${cardFunil}</div>
    <div class="card" style="margin-bottom:14px;">
      <p class="card-titulo">Evolução mês a mês</p>
      ${dpGraficoHistorico(d.historico)}
    </div>
    <div class="card" style="margin-bottom:14px;">
      <p class="card-titulo">Vendas dia a dia no mês</p>
      ${dpSerieDia(d.serie_dia)}
    </div>
    <div class="dp-grid">${cardConsistencia}${cardFaixas}${cardAtendimento}</div>
    <div class="dp-grid duas">${cardMaiores}${cardFollow}</div>`;
}

function classificarRitmo(pct, pctDoMes){
  if(pct >= 100) return {texto: 'Meta batida', classe: 'bom', cor: 'var(--good)'};
  if(pct >= pctDoMes) return {texto: 'No ritmo', classe: 'bom', cor: 'var(--good)'};
  if(pct >= pctDoMes - 12) return {texto: 'Quase no ritmo', classe: 'atencao', cor: 'var(--warn)'};
  return {texto: 'Atrasado', classe: 'ruim', cor: 'var(--bad)'};
}
