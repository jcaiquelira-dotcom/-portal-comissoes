/* ============================================================
   DRE — o painel de resultado
   ============================================================
   Carregado por admin.html. Depende de fmtMoeda() e rotuloMes(), definidas lá.

   O que este painel assume, e que decidiu o desenho inteiro:

   1. A MARGEM MENSAL NÃO É CONFIÁVEL AQUI, e mostrá-la como número principal
      seria mentir com cara de precisão. O CMV desta empresa é o carro COMPRADO
      no mês (96% dele), não a peça VENDIDA no mês. Não existe estoque ligando
      as duas pontas, então a margem de um mês isolado oscila 11 pontos sem
      nada ter acontecido no negócio. Em trimestre móvel o ruído cai 41%.
      Por isso o KPI grande é acumulado/trimestral e o mensal fica em faixa.

   2. RANKING POR PERCENTUAL ENGANA. Uma conta que foi de R$ 165 para R$ 5.718
      "subiu 3.365%" e não significa nada; a folha subir 4% significa muito.
      Toda ordenação aqui é por delta em REAIS.

   3. O QUE FALTA TEM QUE APARECER. R$ 413 mil dos 8 meses não têm categoria, e
      a mídia lançada é um terço da real. Um painel que esconde isso faz o
      gestor decidir em cima de um número que ele acha completo.

   4. ISTO É CAIXA, NÃO COMPETÊNCIA. A correlação entre o "resultado
      operacional" e o saldo do mês é 0,99 — são a mesma curva. A tela diz isso
      em vez de fingir que é um DRE contábil.
   ============================================================ */

const DRE_ORDEM = [
  ['receita',          'Receita bruta',            'entrada'],
  ['deducoes',         'Deduções',                 'saida'],
  ['cmv',              'Custo da peça vendida',    'saida'],
  ['despesas',         'Despesas operacionais',    'saida'],
  ['investimento',     'Investimento e obra',      'fora'],
  ['socios',           'Distribuição de lucro',    'fora'],
  ['nao_resultado',    'Só passou pelo caixa',     'fora'],
  ['nao_classificado', 'A classificar',            'buraco'],
];

/* Contas que são retirada de sócio. Ficam dentro de "despesas" no dado porque
   é lá que o dinheiro sai, mas misturar isso com o custo de operar faz a
   margem parecer 8,4 pontos pior do que a operação de fato entrega. */
const DRE_SOCIOS = /^Pro-labore|^Plano de saude dos socios/i;

function dreSoma(mes, grupo){
  const g = (mes.dre || {})[grupo] || {};
  return Object.values(g).reduce((s, i) => s + (i.valor || 0), 0);
}

function dreContas(mes, grupo){
  return Object.entries((mes.dre || {})[grupo] || {})
    .map(([nome, i]) => ({nome, valor: i.valor || 0, familia: i.familia || ''}));
}

function dreProLabore(mes){
  return dreContas(mes, 'despesas')
    .filter(c => DRE_SOCIOS.test(c.nome))
    .reduce((s, c) => s + c.valor, 0);
}

/* Os números de um mês, já nas contas que a tela usa. */
function dreNumeros(mes){
  const receita = mes.entradas || 0;
  const deducoes = dreSoma(mes, 'deducoes');
  const cmv = dreSoma(mes, 'cmv');
  const despesas = dreSoma(mes, 'despesas');
  const fora = dreSoma(mes, 'investimento') + dreSoma(mes, 'socios')
             + dreSoma(mes, 'nao_resultado');
  const buraco = dreSoma(mes, 'nao_classificado');
  const bruto = receita - deducoes - cmv;
  const operacional = bruto - despesas;
  return {
    receita, deducoes, cmv, despesas, fora, buraco, bruto, operacional,
    proLabore: dreProLabore(mes),
    sobra: operacional - fora - buraco,
    saldo: mes.saldo || 0,
  };
}

const drePct = (v, base) => base ? (v / base * 100) : 0;
const dreNum = v => (v || 0).toFixed(1).replace('.', ',');

/* ---------- 1. faixa de alerta ----------
   Só aparece quando há o que dizer. Faixa fixa com "nenhum problema" vira
   paisagem em duas semanas e ninguém lê mais. */
function dreAlertas(mes, chave, dados){
  const n = dreNumeros(mes);
  const av = [];
  const pctBuraco = drePct(n.buraco, mes.saidas || 1);
  if(pctBuraco > 5)
    av.push(['ruim', `${dreNum(pctBuraco)}% das saídas sem categoria`,
      `${fmtMoeda(n.buraco)} em ${rotuloMes(chave)} saíram sem dizer o que foram.`]);
  if(mes.dre_incompleto)
    av.push(['ruim', 'Este mês não fecha',
      'A soma das rubricas não bate com as saídas da planilha.']);
  if(mes.dupla_contagem)
    av.push(['aviso', 'Havia dupla contagem',
      `${fmtMoeda(mes.dupla_contagem)} estavam contados duas vezes na planilha; descontei.`]);
  const real = ((dados.midia_real || {})[chave] || {}).total || 0;
  const lancado = (dreContas(mes, 'despesas').find(c => /Midia paga/i.test(c.nome)) || {}).valor || 0;
  if(real && real > lancado * 1.5)
    av.push(['aviso', 'Mídia lançada a menos',
      `As plataformas cobraram ${fmtMoeda(real)} e o fluxo registrou ${fmtMoeda(lancado)}.`]);
  if(mes.conferencia)
    av.push(['aviso', 'Duas fontes para este mês',
      `Está valendo o portal; a planilha diz ${fmtMoeda(mes.conferencia.saidas)} de saídas.`]);
  if(!av.length) return '';
  return `<div class="dre-alertas">${av.map(([t, titulo, txt]) =>
    `<div class="dre-alerta ${t}" title="${txt}">
       <b>${titulo}</b><span>${txt}</span></div>`).join('')}</div>`;
}

/* ---------- 2. KPIs ----------
   Margem em acumulado e trimestre móvel, nunca mensal como número grande —
   ver a nota 1 no topo. */
function dreKpis(chaves, chave, mesesMap){
  const idx = chaves.indexOf(chave);
  const janela = chaves.slice(Math.max(0, idx - 2), idx + 1);
  const acc = (lista, f) => lista.reduce((s, k) => s + f(dreNumeros(mesesMap[k])), 0);

  const recTri = acc(janela, n => n.receita);
  const brutoTri = acc(janela, n => n.bruto);
  const recAno = acc(chaves, n => n.receita);
  const opAno = acc(chaves, n => n.operacional);
  const proAno = acc(chaves, n => n.proLabore);
  const n = dreNumeros(mesesMap[chave]);
  const buracoAno = acc(chaves, x => x.buraco);
  const saiAno = chaves.reduce((s, k) => s + (mesesMap[k].saidas || 0), 0);

  const tile = (rot, valor, sub, cor) => `<div class="dp-kpi">
    <div class="rot">${rot}</div>
    <div class="num valor-money"${cor ? ` style="color:${cor}"` : ''}>${valor}</div>
    <span class="dp-var neutro">${sub}</span></div>`;

  return `<div class="dp-kpis conta-linha">
    ${tile('Sobrou em ' + rotuloMes(chave), fmtMoeda(n.sobra),
           'entrou ' + fmtMoeda(n.receita), n.sobra >= 0 ? 'var(--good)' : 'var(--bad)')}
    ${tile('Margem bruta', dreNum(drePct(brutoTri, recTri)) + '%',
           `sobre a receita, no trimestre ${janela.map(rotuloMes).join(' + ')}`)}
    ${tile('Margem operacional', dreNum(drePct(opAno, recAno)) + '%',
           `acumulado do ano`)}
    ${tile('…sem as retiradas', dreNum(drePct(opAno + proAno, recAno)) + '%',
           `${fmtMoeda(proAno)} de pró-labore no ano`, 'var(--info)')}
    ${tile('Sem categoria', dreNum(drePct(buracoAno, saiAno)) + '%',
           `${fmtMoeda(buracoAno)} no ano`,
           drePct(buracoAno, saiAno) > 5 ? 'var(--bad)' : null)}
  </div>`;
}

/* ---------- 3. cascata ----------
   Onde cada real do mês foi parar. É a única leitura que responde "entrou
   novecentos mil, por que sobrou trezentos" sem obrigar a somar de cabeça. */
function dreCascata(mes){
  const n = dreNumeros(mes);
  const passos = [
    ['Receita', n.receita, 'base'],
    ['Deduções', -n.deducoes, 'saida'],
    ['Custo da peça', -n.cmv, 'saida'],
    ['Lucro bruto', null, 'marco'],
    ['Despesas', -n.despesas, 'saida'],
    ['Resultado op.', null, 'marco'],
    ['Investimento', -dreSoma(mes, 'investimento'), 'fora'],
    ['Sócios', -dreSoma(mes, 'socios'), 'fora'],
    ['Só passou', -dreSoma(mes, 'nao_resultado'), 'fora'],
    ['A classificar', -n.buraco, 'buraco'],
    ['Sobra de caixa', null, 'fim'],
  ].filter(p => p[1] === null || Math.abs(p[1]) > 0.5);

  const L = 8, T = 18, B = 44, alt = 260;
  const larg = Math.max(560, passos.length * 78);
  const passo = (larg - L * 2) / passos.length;
  const barra = Math.min(52, passo * 0.62);
  const topo = Math.max(n.receita, 1);
  const escala = (alt - T - B) / topo;

  let acumulado = 0;
  const partes = passos.map((p, i) => {
    const [rot, delta, tipo] = p;
    const x = L + passo * i + (passo - barra) / 2;
    let y, h, valor;
    if(delta === null){
      valor = acumulado;
      h = Math.abs(valor) * escala;
      y = alt - B - Math.max(0, valor) * escala;
      if(valor < 0) y = alt - B;
    }else{
      valor = delta;
      const de = acumulado, ate = acumulado + delta;
      acumulado = ate;
      const yDe = alt - B - de * escala, yAte = alt - B - ate * escala;
      y = Math.min(yDe, yAte);
      h = Math.abs(yDe - yAte);
    }
    const cor = tipo === 'base' ? 'var(--good)'
      : tipo === 'saida' ? 'var(--bad)'
      : tipo === 'fora' ? 'var(--muted)'
      : tipo === 'buraco' ? 'var(--warn)'
      : tipo === 'fim' ? (valor >= 0 ? 'var(--good)' : 'var(--bad)')
      : 'var(--info)';
    const marco = tipo === 'marco' || tipo === 'fim' || tipo === 'base';
    const rotuloValor = fmtMoeda(Math.abs(valor));
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barra.toFixed(1)}"
        height="${Math.max(2, h).toFixed(1)}" rx="3" fill="${cor}"
        opacity="${marco ? '.95' : '.75'}">
        <title>${rot}: ${rotuloValor} (${dreNum(drePct(Math.abs(valor), n.receita))}% da receita)</title></rect>
      <text x="${(x + barra / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle"
        font-size="9" fill="var(--muted)">${rotuloValor.replace('R$ ', '')}</text>
      <text x="${(x + barra / 2).toFixed(1)}" y="${alt - B + 14}" text-anchor="middle"
        font-size="9.5" fill="var(--muted)">${rot}</text>
      ${marco ? `<text x="${(x + barra / 2).toFixed(1)}" y="${alt - B + 26}" text-anchor="middle"
        font-size="9" font-weight="600" fill="${cor}">${dreNum(drePct(valor, n.receita))}%</text>` : ''}`;
  }).join('');

  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">
      <line x1="${L}" y1="${alt - B}" x2="${larg - L}" y2="${alt - B}" stroke="var(--line)"/>
      ${partes}</svg></div>`;
}

/* ---------- 4. o que subiu e o que caiu ----------
   Comparado com a MÉDIA dos meses anteriores, não com o mês passado: um mês
   passado atípico viraria a régua de tudo. E ordenado por reais — ver nota 2. */
function dreMovimentos(chaves, chave, mesesMap){
  const idx = chaves.indexOf(chave);
  if(idx < 2) return '<div class="dp-aviso">Preciso de pelo menos três meses para comparar.</div>';
  const antes = chaves.slice(0, idx);
  const mediaDe = {};
  antes.forEach(k => {
    for(const g of ['deducoes', 'cmv', 'despesas', 'investimento', 'socios',
                    'nao_resultado', 'nao_classificado'])
      dreContas(mesesMap[k], g).forEach(c => {
        mediaDe[c.nome] = mediaDe[c.nome] || {soma: 0, grupo: g};
        mediaDe[c.nome].soma += c.valor;
      });
  });
  const atual = {};
  for(const g of ['deducoes', 'cmv', 'despesas', 'investimento', 'socios',
                  'nao_resultado', 'nao_classificado'])
    dreContas(mesesMap[chave], g).forEach(c => { atual[c.nome] = c.valor; });

  const nomes = new Set([...Object.keys(mediaDe), ...Object.keys(atual)]);
  const linhas = [...nomes].map(nome => {
    const media = (mediaDe[nome] ? mediaDe[nome].soma : 0) / antes.length;
    const hoje = atual[nome] || 0;
    return {nome, media, hoje, delta: hoje - media,
            novo: media < 1 && hoje > 0, sumiu: hoje < 1 && media > 0};
  }).filter(l => Math.abs(l.delta) >= 1000)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 12);

  if(!linhas.length) return '<div class="dp-aviso">Nenhuma conta mudou mais de R$ 1.000 em relação à média.</div>';
  const max = Math.max(...linhas.map(l => Math.abs(l.delta)), 1);

  return `<div class="dre-mov">${linhas.map(l => {
    const pct = (Math.abs(l.delta) / max) * 50;
    const sobe = l.delta > 0;
    return `<div class="dre-mov-linha" title="média dos ${antes.length} meses anteriores: ${fmtMoeda(l.media)} · agora: ${fmtMoeda(l.hoje)}">
      <span class="nome">${l.nome}${l.novo ? ' <em>nova</em>' : ''}${l.sumiu ? ' <em>zerou</em>' : ''}</span>
      <span class="barra">
        <span class="meio"></span>
        <span class="f ${sobe ? 'sobe' : 'desce'}" style="width:${pct.toFixed(1)}%;${
          sobe ? 'left:50%' : `right:50%`}"></span>
      </span>
      <span class="v ${sobe ? 'sobe' : 'desce'}">${sobe ? '+' : '−'} ${fmtMoeda(Math.abs(l.delta)).replace('R$ ', '')}</span>
    </div>`;
  }).join('')}</div>
  <div class="dp-aviso">Contra a <b>média dos ${antes.length} meses anteriores</b>, ordenado por
    reais e não por percentual — uma conta pequena que dobra aparece como +100% e não muda nada
    no mês. Só entram mudanças acima de R$ 1.000.
    <br><br><b>Cuidado com o calendário.</b> Comissão, impostos e pró-labore mudam de mês conforme
    a data em que foram pagos, não conforme o negócio: agosto teve a maior receita do ano e mesmo
    assim a comissão aparece caindo aqui. Antes de tratar uma dessas como economia, confira se ela
    não foi só paga noutro dia.</div>`;
}

/* ---------- 5. o que sai mesmo sem vender ----------
   Fixo = presente nos 8 meses com variação baixa. É a pergunta "se a receita
   cair pela metade, quanto eu ainda tenho que pagar no dia 5". */
function dreEstrutura(chaves, mesesMap){
  const porConta = {};
  chaves.forEach(k => {
    for(const g of ['deducoes', 'cmv', 'despesas', 'investimento', 'socios',
                    'nao_resultado', 'nao_classificado'])
      dreContas(mesesMap[k], g).forEach(c => {
        porConta[c.nome] = porConta[c.nome] || {vals: [], grupo: g};
        porConta[c.nome].vals.push(c.valor);
      });
  });
  const n = chaves.length;
  const baldes = {fixo: [], semifixo: [], variavel: [], retirada: []};
  Object.entries(porConta).forEach(([nome, d]) => {
    const soma = d.vals.reduce((a, b) => a + b, 0);
    const media = soma / n;
    const meses = d.vals.length;
    const dp = Math.sqrt(d.vals.reduce((a, v) => a + (v - media) ** 2, 0) / n);
    const cv = media ? dp / media : 9;
    const item = {nome, soma, media, cv, meses};
    if(DRE_SOCIOS.test(nome)) baldes.retirada.push(item);
    else if(meses >= n && cv <= 0.25) baldes.fixo.push(item);
    else if(meses >= n && cv <= 0.45) baldes.semifixo.push(item);
    else baldes.variavel.push(item);
  });
  const total = Object.values(baldes).flat().reduce((s, i) => s + i.soma, 0) || 1;
  const cores = {fixo: 'var(--bad)', semifixo: 'var(--warn)',
                 variavel: 'var(--good)', retirada: 'var(--info)'};
  const rot = {fixo: 'Fixo — vem todo mês igual', semifixo: 'Semifixo — vem sempre, varia',
               variavel: 'Variável — acompanha o movimento', retirada: 'Retirada dos sócios'};

  const ordem = ['fixo', 'semifixo', 'retirada', 'variavel'];
  const faixa = ordem.map(k => {
    const s = baldes[k].reduce((a, i) => a + i.soma, 0);
    return `<span class="dre-faixa-parte" style="width:${(s / total * 100).toFixed(2)}%;background:${cores[k]}"
      title="${rot[k]}: ${fmtMoeda(s)} (${dreNum(s / total * 100)}%)"></span>`;
  }).join('');

  const legenda = ordem.map(k => {
    const lista = baldes[k].sort((a, b) => b.soma - a.soma);
    const s = lista.reduce((a, i) => a + i.soma, 0);
    return `<div class="dre-legenda-item">
      <span class="ponto" style="background:${cores[k]}"></span>
      <span class="rot">${rot[k]}</span>
      <span class="val">${fmtMoeda(s / n)}<em>/mês</em></span>
      <span class="pct">${dreNum(s / total * 100)}%</span>
      <div class="quais">${lista.slice(0, 5).map(i => i.nome).join(' · ')}${
        lista.length > 5 ? ` <em>e mais ${lista.length - 5}</em>` : ''}</div>
    </div>`;
  }).join('');

  const fixoMes = (baldes.fixo.reduce((a, i) => a + i.soma, 0)
                 + baldes.semifixo.reduce((a, i) => a + i.soma, 0)) / n;
  return `<div class="dre-faixa">${faixa}</div>${legenda}
    <div class="dp-aviso"><b>${fmtMoeda(fixoMes)} por mês</b> saem da conta mesmo num mês fraco —
      é o fixo mais o semifixo. Fixo aqui é conta que aparece nos ${n} meses com variação
      pequena; a classificação sai do próprio histórico, não de uma lista escrita à mão.</div>`;
}

/* ---------- 6. mídia: o que o DRE vê × o que foi cobrado ---------- */
function dreMidia(chaves, mesesMap, midiaReal){
  if(!midiaReal || !Object.keys(midiaReal).length) return '';
  const linhas = chaves.map(k => ({
    mes: k,
    real: (midiaReal[k] || {}).total || 0,
    lancado: (dreContas(mesesMap[k], 'despesas')
      .find(c => /Midia paga/i.test(c.nome)) || {}).valor || 0,
  })).filter(l => l.real || l.lancado);
  if(!linhas.length) return '';
  const somaReal = linhas.reduce((s, l) => s + l.real, 0);
  const somaLanc = linhas.reduce((s, l) => s + l.lancado, 0);
  if(!somaReal) return '';

  const T = 14, B = 26, alt = 170;
  const larg = Math.max(340, linhas.length * 72);
  const max = Math.max(...linhas.map(l => Math.max(l.real, l.lancado)), 1);
  const passo = (larg - 16) / linhas.length;
  const bw = Math.min(22, passo * 0.3);

  const barras = linhas.map((l, i) => {
    const x = 8 + passo * i + passo / 2;
    const hr = (l.real / max) * (alt - T - B);
    const hl = (l.lancado / max) * (alt - T - B);
    return `<rect x="${(x - bw - 2).toFixed(1)}" y="${(alt - B - hr).toFixed(1)}" width="${bw}"
        height="${Math.max(1, hr).toFixed(1)}" rx="3" fill="var(--info)" opacity=".9">
        <title>${rotuloMes(l.mes)} · cobrado pelas plataformas: ${fmtMoeda(l.real)}</title></rect>
      <rect x="${(x + 2).toFixed(1)}" y="${(alt - B - hl).toFixed(1)}" width="${bw}"
        height="${Math.max(1, hl).toFixed(1)}" rx="3" fill="var(--accent)" opacity=".9">
        <title>${rotuloMes(l.mes)} · lançado no fluxo: ${fmtMoeda(l.lancado)}</title></rect>
      <text x="${x.toFixed(1)}" y="${alt - 8}" text-anchor="middle" font-size="9.5"
        fill="var(--muted)">${rotuloMes(l.mes).slice(0, 3)}</text>`;
  }).join('');

  const vezes = somaLanc ? (somaReal / somaLanc) : 0;
  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">
      <line x1="8" y1="${alt - B}" x2="${larg - 8}" y2="${alt - B}" stroke="var(--line)"/>
      ${barras}</svg></div>
    <div class="dre-legenda-linha">
      <span><i style="background:var(--info)"></i> cobrado pelo Meta e Google — ${fmtMoeda(somaReal)}</span>
      <span><i style="background:var(--accent)"></i> lançado como Mídia paga — ${fmtMoeda(somaLanc)}</span>
    </div>
    <div class="dp-aviso"><b>Faltam ${fmtMoeda(somaReal - somaLanc)} no fluxo.</b>
      As plataformas cobraram ${vezes.toFixed(1).replace('.', ',')}× o que foi lançado. O resto saiu
      da conta por outro caminho — provavelmente dentro de "Cartão". Enquanto não fechar, não dá
      pra saber quanto custa trazer um cliente.</div>`;
}

/* ---------- 7. o buraco: quanto saiu sem nome ---------- */
function dreBuraco(chaves, mesesMap){
  const serie = chaves.map(k => ({
    mes: k, valor: dreSoma(mesesMap[k], 'nao_classificado'),
    saidas: mesesMap[k].saidas || 0,
  }));
  const total = serie.reduce((s, x) => s + x.valor, 0);
  if(!total) return '';
  const comp = {};
  chaves.forEach(k => dreContas(mesesMap[k], 'nao_classificado')
    .forEach(c => { comp[c.nome] = (comp[c.nome] || 0) + c.valor; }));
  const tops = Object.entries(comp).sort((a, b) => b[1] - a[1]).slice(0, 6);

  const alt = 92, larg = Math.max(300, serie.length * 54);
  const max = Math.max(...serie.map(s => drePct(s.valor, s.saidas)), 1);
  const passo = larg / serie.length;
  const barras = serie.map((s, i) => {
    const pct = drePct(s.valor, s.saidas);
    const h = (pct / max) * (alt - 26);
    const ruim = pct > 5;
    return `<rect x="${(passo * i + passo * 0.22).toFixed(1)}" y="${(alt - 16 - h).toFixed(1)}"
      width="${(passo * 0.56).toFixed(1)}" height="${Math.max(2, h).toFixed(1)}" rx="3"
      fill="${ruim ? 'var(--bad)' : 'var(--warn)'}" opacity=".85">
      <title>${rotuloMes(s.mes)}: ${fmtMoeda(s.valor)} (${dreNum(pct)}% das saídas)</title></rect>
      <text x="${(passo * i + passo / 2).toFixed(1)}" y="${alt - 4}" text-anchor="middle"
        font-size="9" fill="var(--muted)">${rotuloMes(s.mes).slice(0, 3)}</text>`;
  }).join('');

  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">${barras}</svg></div>
    <div class="dp-lista">${tops.map(([nome, v]) => `
      <div class="dp-destaque"><span class="rot">${nome}</span>
        <span class="val">${fmtMoeda(v)}</span></div>`).join('')}</div>
    <div class="dp-aviso"><b>${fmtMoeda(total)} nos ${chaves.length} meses</b> saíram sem dizer o que
      foram. "Cartão e banco" responde por qual conta o dinheiro saiu, nunca o que foi comprado;
      "Sem identificação" é o antigo <code>Div.</code> da planilha. É exatamente isso que os blocos
      por categoria da planilha nova existem para acabar.</div>`;
}

/* ---------- 8. concentração ---------- */
function drePareto(chaves, mesesMap){
  const porConta = {};
  chaves.forEach(k => {
    for(const g of ['deducoes', 'cmv', 'despesas', 'investimento', 'socios',
                    'nao_resultado', 'nao_classificado'])
      dreContas(mesesMap[k], g).forEach(c => {
        porConta[c.nome] = (porConta[c.nome] || 0) + c.valor;
      });
  });
  const lista = Object.entries(porConta).sort((a, b) => b[1] - a[1]);
  const total = lista.reduce((s, x) => s + x[1], 0) || 1;
  let acc = 0, corte = 0;
  const comAcc = lista.map(([nome, v], i) => {
    acc += v;
    if(!corte && acc / total >= 0.8) corte = i + 1;
    return {nome, valor: v, acc: acc / total};
  });
  const mostra = comAcc.slice(0, 12);
  const max = mostra[0] ? mostra[0].valor : 1;

  return `<div class="dp-lista">${mostra.map((c, i) => `
    <div class="dp-linha${i < corte ? ' dre-pareto-dentro' : ''}">
      <span class="nome">${c.nome}</span>
      <span class="v">${fmtMoeda(c.valor)} <em>${dreNum(c.valor / total * 100)}%</em></span>
      <span class="t"><span class="f" style="width:${(c.valor / max * 100).toFixed(1)}%"></span></span>
    </div>`).join('')}</div>
    <div class="dp-aviso"><b>${corte} contas de ${lista.length}</b> explicam 80% de tudo que sai.
      Economia de verdade mora nessas; cortar as de baixo não muda o mês.</div>`;
}

/* ---------- 9. sazonalidade ----------
   Índice de cada mês contra a média do PRÓPRIO ano — sem isso o crescimento da
   empresa (de R$ 1,1 mi em 2018 para R$ 10,9 mi em 2026) dominaria tudo e os
   anos recentes definiriam sozinhos o formato da curva. */
function dreSazonalidade(mesesMap){
  const porAno = {};
  Object.entries(mesesMap).forEach(([k, m]) => {
    const [ano, mes] = k.split('-');
    if(!m.entradas) return;
    (porAno[ano] = porAno[ano] || {})[+mes] = m.entradas;
  });
  const indices = {};
  Object.values(porAno).forEach(meses => {
    const vals = Object.values(meses);
    if(vals.length < 10) return;              // ano incompleto distorce a média
    const media = vals.reduce((a, b) => a + b, 0) / vals.length;
    Object.entries(meses).forEach(([m, v]) => {
      (indices[m] = indices[m] || []).push(v / media);
    });
  });
  const nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  const dados = nomes.map((nome, i) => {
    const lista = indices[i + 1] || [];
    const media = lista.length ? lista.reduce((a, b) => a + b, 0) / lista.length : null;
    return {nome, indice: media, anos: lista.length};
  });
  if(dados.every(d => d.indice === null)) return '';

  const alt = 150, larg = Math.max(360, 12 * 48), B = 26, T = 12;
  const base = alt - B - (alt - T - B) * 0.5;
  const escala = (alt - T - B) / 1.6;
  const hoje = new Date().getMonth();
  const barras = dados.map((d, i) => {
    if(d.indice === null) return '';
    const passo = larg / 12;
    const x = passo * i + passo * 0.22;
    const w = passo * 0.56;
    const desvio = (d.indice - 1) * escala;
    const y = desvio >= 0 ? base - desvio : base;
    const forte = d.indice >= 1.08, fraco = d.indice <= 0.92;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}"
        height="${Math.max(2, Math.abs(desvio)).toFixed(1)}" rx="3"
        fill="${forte ? 'var(--good)' : fraco ? 'var(--bad)' : 'var(--muted)'}"
        opacity="${i === hoje ? '1' : '.65'}">
        <title>${d.nome}: fatura ${d.indice >= 1 ? '+' : ''}${dreNum((d.indice - 1) * 100)}% da média do ano (${d.anos} anos)</title></rect>
      <text x="${(x + w / 2).toFixed(1)}" y="${alt - 8}" text-anchor="middle" font-size="9.5"
        fill="${i === hoje ? 'var(--text)' : 'var(--muted)'}"
        font-weight="${i === hoje ? '700' : '400'}">${d.nome}</text>`;
  }).join('');

  const forte = dados.filter(d => d.indice).sort((a, b) => b.indice - a.indice)[0];
  const fraco = dados.filter(d => d.indice).sort((a, b) => a.indice - b.indice)[0];
  const anos = Math.max(...dados.map(d => d.anos));
  return `<div style="overflow-x:auto"><svg class="dp-svg" viewBox="0 0 ${larg} ${alt}" height="${alt}">
      <line x1="0" y1="${base}" x2="${larg}" y2="${base}" stroke="var(--line)" stroke-dasharray="3 3"/>
      ${barras}</svg></div>
    <div class="dp-aviso">Média de <b>${anos} anos</b>. ${forte.nome} fatura
      <b>${dreNum((forte.indice / fraco.indice - 1) * 100)}% mais que ${fraco.nome}</b>, todo ano.
      Cada barra é o quanto o mês foge da média do próprio ano — comparado assim porque a empresa
      cresceu muito no período, e sem normalizar os anos recentes decidiriam a curva sozinhos.</div>`;
}

/* ---------- montagem ---------- */
function renderDre(dados, chave, el){
  const mesesMap = dados.meses || {};
  const chaves = Object.keys(mesesMap).filter(k => mesesMap[k].dre
    && Object.keys(mesesMap[k].dre).length).sort();
  if(!chaves.length){
    el.innerHTML = `<div class="card"><p class="card-titulo">DRE</p>
      <div class="dp-aviso" style="margin-top:0;">Ainda não importei a planilha de fluxo.</div></div>`;
    return;
  }
  const mesSel = chaves.includes(chave) ? chave : chaves[chaves.length - 1];
  const mes = mesesMap[mesSel];
  const ateAqui = chaves.slice(0, chaves.indexOf(mesSel) + 1);

  const card = (titulo, sub, corpo, extra) => `<div class="card">
    <div class="aud-cabecalho"><p class="card-titulo" style="margin:0;">${titulo}</p>
      ${extra || ''}</div>
    ${sub ? `<p class="dre-sub">${sub}</p>` : ''}${corpo}</div>`;

  el.innerHTML = `
    ${dreAlertas(mes, mesSel, dados)}
    <div class="card" style="margin-bottom:14px;">
      <div class="aud-cabecalho">
        <p class="card-titulo" style="margin:0;">DRE · ${rotuloMes(mesSel)}</p>
        <span class="aud-conta">${chaves.length} meses com detalhe</span>
      </div>
      <div class="preset-row">${chaves.map(k => `<button class="preset-btn${
        k === mesSel ? ' active' : ''}" data-dre-mes="${k}">${rotuloMes(k)}</button>`).join('')}</div>
    </div>

    ${dreKpis(ateAqui, mesSel, mesesMap)}

    ${card('Para onde foi cada real de ' + rotuloMes(mesSel),
      'Entrou à esquerda, sobrou à direita. Cada barra vermelha é uma mordida.',
      dreCascata(mes))}

    <div class="dp-grid duas">
      ${card('O que mudou este mês', 'Contra a média dos meses anteriores.',
        dreMovimentos(ateAqui, mesSel, mesesMap))}
      ${card('O que sai mesmo sem vender', 'Quanto da estrutura não depende do movimento.',
        dreEstrutura(ateAqui, mesesMap))}
    </div>

    ${dreMidia(ateAqui, mesesMap, dados.midia_real)
      ? card('Mídia paga: o que foi cobrado × o que foi lançado',
          'Cruzamento com o que o Meta e o Google efetivamente cobraram.',
          dreMidia(ateAqui, mesesMap, dados.midia_real))
      : ''}

    <div class="dp-grid duas">
      ${card('Onde economizar de verdade', 'Concentração do gasto no ano.',
        drePareto(ateAqui, mesesMap))}
      ${card('Quanto saiu sem nome', 'O que o lançamento não deixou saber.',
        dreBuraco(ateAqui, mesesMap))}
    </div>

    ${dreSazonalidade(mesesMap)
      ? card('Quando o caixa aperta', 'O ano tem forma, e ela se repete.',
          dreSazonalidade(mesesMap))
      : ''}

    ${card('DRE linha a linha', 'De onde saiu cada número acima.',
      dreTabela(mes), `<span class="aud-conta">${mes.itens || 0} lançamentos</span>`)}

    <div class="card">
      <p class="card-titulo">O que esta tela não responde</p>
      <div class="dp-aviso" style="margin-bottom:0;">
        <b>Isto é caixa, não competência.</b> O "resultado operacional" acompanha o saldo do mês
        quase perfeitamente porque são a mesma conta: o que entrou menos o que saiu. Uma venda de
        agosto paga em setembro conta em setembro.<br><br>
        <b>A margem de um mês isolado não vale.</b> O custo da peça aqui é o carro comprado no mês,
        não a peça vendida — não existe estoque ligando os dois. Por isso os KPIs de margem são
        acumulados e de trimestre móvel; o mês sozinho oscila sem nada ter acontecido.<br><br>
        <b>Não dá para separar receita por canal</b> (ML, site, Shopee, balcão), nem enxergar a
        tarifa de marketplace: a receita entra no fluxo como um valor só, já líquido.
      </div>
    </div>`;
}

/* A tabela detalhada, agrupada por família dentro de cada bloco do DRE. */
function dreTabela(mes){
  const n = dreNumeros(mes);
  const pct = v => n.receita
    ? `<span class="pct">${dreNum(drePct(v, n.receita))}%</span>` : '';

  const bloco = (grupo, rotulo) => {
    const contas = dreContas(mes, grupo);
    if(!contas.length) return '';
    const soma = contas.reduce((s, c) => s + c.valor, 0);
    const familias = {};
    contas.forEach(c => (familias[c.familia || ''] = familias[c.familia || ''] || []).push(c));
    const corpo = Object.entries(familias)
      .sort((a, b) => b[1].reduce((s, c) => s + c.valor, 0)
                    - a[1].reduce((s, c) => s + c.valor, 0))
      .map(([fam, lista]) => {
        const sf = lista.reduce((s, c) => s + c.valor, 0);
        const cab = Object.keys(familias).length > 1 && fam
          ? `<tr class="familia"><td>${fam}</td><td>${fmtMoeda(sf)}${pct(sf)}</td></tr>` : '';
        return cab + lista.sort((a, b) => b.valor - a.valor).map(c =>
          `<tr class="item"><td>${c.nome}</td><td>${fmtMoeda(c.valor)}${pct(c.valor)}</td></tr>`).join('');
      }).join('');
    return `<tr class="grupo"><td>${rotulo}</td><td>${fmtMoeda(soma)}${pct(soma)}</td></tr>${corpo}`;
  };

  const resultado = (rot, v, dica) => `<tr class="resultado ${v >= 0 ? 'bom' : 'ruim'}">
    <td>${rot}${dica ? `<br><span class="pct" style="margin:0">${dica}</span>` : ''}</td>
    <td>${fmtMoeda(v)}${pct(v)}</td></tr>`;

  return `<div class="tabela-rolante"><table class="dre-tab">
    <tr class="grupo"><td>RECEITA</td><td>${fmtMoeda(n.receita)}</td></tr>
    ${bloco('deducoes', '(−) Deduções')}
    ${bloco('cmv', '(−) Custo da peça vendida')}
    ${resultado('= LUCRO BRUTO', n.bruto)}
    ${bloco('despesas', '(−) Despesas operacionais')}
    ${resultado('= RESULTADO OPERACIONAL', n.operacional,
                'o que a operação gerou, antes de investir ou distribuir')}
    ${bloco('investimento', '(−) Investimento e obra')}
    ${bloco('socios', '(−) Distribuição de lucro')}
    ${bloco('nao_resultado', '(−) Só passou pelo caixa')}
    ${bloco('nao_classificado', '(−) A classificar')}
    ${resultado('= SOBRA DE CAIXA', n.sobra, 'confere com o saldo do fluxo de caixa')}
  </table></div>`;
}
