/* ============================================================
   Menu lateral compartilhado.
   Usado pelas telas do vendedor (index, simulador, retomada) e pela do
   gestor (admin) — mexer aqui muda em todas.

   Uso: <aside id="sidebar"></aside> + montarSidebar('painel')
   e, na area do gestor, montarSidebar('painel', {portal:'gestor'}).
   ============================================================ */

const ICONES = {
  painel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  vendas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 12h18M3 18h12"/></svg>',
  cifrao: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20"/><path d="M16.5 6.5H10a3.2 3.2 0 0 0 0 6.4h4a3.2 3.2 0 0 1 0 6.4H7"/></svg>',
  simulador: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h2M12 10h2M16 10h.01M8 14h2M12 14h2M16 14h.01M8 18h6"/></svg>',
  retomada: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 20.5l1.5-4.4A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z"/></svg>',
  ranking: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/></svg>',
  expedicao: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8h11v9H2z"/><path d="M13 11h4l3 3v3h-7z"/><circle cx="6" cy="18.5" r="1.7"/><circle cx="17" cy="18.5" r="1.7"/></svg>',
  fechamento: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  metas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/></svg>',
  equipe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5.3a3.2 3.2 0 0 1 0 5.4M18 20a6.5 6.5 0 0 0-3-5.5"/></svg>',
  registros: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h11l3 3v15H5z"/><path d="M9 9h7M9 13h7M9 17h4"/></svg>',
  config: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  lupa: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/><path d="M8 10.5h5M10.5 8v5"/></svg>',
  bonus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.2l5.9-.9z"/></svg>',
  carro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M5 11l1.6-4.2A2 2 0 0 1 8.5 5.5h7a2 2 0 0 1 1.9 1.3L19 11"/><path d="M3 11h18v5H3z"/><circle cx="7" cy="16.5" r="1.6"/><circle cx="17" cy="16.5" r="1.6"/></svg>',
  marketing: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11v3a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/><path d="M19.5 5.5a9 9 0 0 1 0 13"/></svg>',
  desempenho: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l3.5-4 3 2.5L20 7"/><circle cx="10.5" cy="11" r="1.1" fill="currentColor" stroke="none"/><circle cx="13.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/></svg>',
  trocar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4L3 8l4 4"/><path d="M3 8h13a4 4 0 0 1 0 8h-1"/><path d="M17 20l4-4-4-4"/></svg>',
  sol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  lua: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  recolher: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  menu: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
};

/* ---------- Avatares ----------
   Desenhados aqui em SVG em vez de virem de arquivo: acompanham a paleta do
   tema, ficam nítidos em qualquer tamanho e não dependem de nenhum host
   externo. Servem de retrato padrão enquanto o vendedor não sobe uma foto.

   Qual avatar cada pessoa usa é escolha do gestor (campo `avatar` no cadastro),
   nunca deduzido do nome — nome não diz o gênero de ninguém. */
const AVATARES = {
  feminino:
    '<svg viewBox="0 0 64 64" aria-hidden="true">'
    + '<circle cx="32" cy="32" r="32" fill="var(--av-fundo)"/>'
    + '<path d="M32 12c-9 0-14 6-14 14 0 3 .6 6 .6 6l-1.6 1c-.6.4-.8 1.2-.4 1.8l1.4 2.2 1 6"'
    + ' fill="var(--av-cabelo)" stroke="none"/>'
    + '<path d="M18 27c0-8 5-14 14-14s14 6 14 14c0 3-.5 5.6-.5 5.6" fill="var(--av-cabelo)"/>'
    + '<path d="M26 38v5c0 2-1 3-3 3.6l-4 1.4v4h26v-4l-4-1.4c-2-.6-3-1.6-3-3.6v-5z" fill="var(--av-pele)"/>'
    + '<path d="M32 42c-2.6 0-5-1.2-6-2.6V38h12v1.4c-1 1.4-3.4 2.6-6 2.6z" fill="var(--av-sombra)"/>'
    + '<path d="M23 26c0-6 4-10 9-10s9 4 9 10v4c0 5.6-4 10-9 10s-9-4.4-9-10z" fill="var(--av-pele)"/>'
    + '<path d="M23 27c0-7 4-11 9-11s9 4 9 11c0 0-3.4-4-9-4s-9 4-9 4z" fill="var(--av-cabelo)"/>'
    + '<path d="M22.5 24c-1.6 0-2.5 2-2 4.5.4 2 1.6 3.2 2.6 3M41.5 24c1.6 0 2.5 2 2 4.5-.4 2-1.6 3.2-2.6 3"'
    + ' fill="var(--av-cabelo)"/>'
    + '<circle cx="27.5" cy="28.5" r="1.5" fill="var(--av-traco)"/>'
    + '<circle cx="36.5" cy="28.5" r="1.5" fill="var(--av-traco)"/>'
    + '<path d="M29.5 33.5c1.5 1.2 3.5 1.2 5 0" stroke="var(--av-traco)" stroke-width="1.6"'
    + ' fill="none" stroke-linecap="round"/>'
    + '<path d="M19 52c2-5 7-7.5 13-7.5S43 47 45 52l1.4 5c-4 3.6-9 5-14.4 5s-10.4-1.4-14.4-5z"'
    + ' fill="var(--av-roupa)"/>'
    + '</svg>',
  masculino:
    '<svg viewBox="0 0 64 64" aria-hidden="true">'
    + '<circle cx="32" cy="32" r="32" fill="var(--av-fundo)"/>'
    + '<path d="M26 38v5c0 2-1 3-3 3.6l-4 1.4v4h26v-4l-4-1.4c-2-.6-3-1.6-3-3.6v-5z" fill="var(--av-pele)"/>'
    + '<path d="M32 42c-2.6 0-5-1.2-6-2.6V38h12v1.4c-1 1.4-3.4 2.6-6 2.6z" fill="var(--av-sombra)"/>'
    + '<path d="M23 26c0-6 4-10 9-10s9 4 9 10v4c0 5.6-4 10-9 10s-9-4.4-9-10z" fill="var(--av-pele)"/>'
    + '<path d="M22.6 25.5c0-6.5 4-10.5 9.4-10.5s9.4 4 9.4 10.5c0 0-1-3.5-3-4.5-2.6 1.6-9.6 2-12.4.5'
    + ' -1.6 1-2.4 2.6-3.4 4z" fill="var(--av-cabelo)"/>'
    + '<circle cx="27.5" cy="28.5" r="1.5" fill="var(--av-traco)"/>'
    + '<circle cx="36.5" cy="28.5" r="1.5" fill="var(--av-traco)"/>'
    + '<path d="M29.5 33.5c1.5 1.2 3.5 1.2 5 0" stroke="var(--av-traco)" stroke-width="1.6"'
    + ' fill="none" stroke-linecap="round"/>'
    + '<path d="M19 52c2-5 7-7.5 13-7.5S43 47 45 52l1.4 5c-4 3.6-9 5-14.4 5s-10.4-1.4-14.4-5z"'
    + ' fill="var(--av-roupa)"/>'
    + '<path d="M28 45.5l4 4 4-4-1.4-1.2h-5.2z" fill="var(--av-fundo)" opacity=".5"/>'
    + '</svg>',
};

/* Foto de verdade > avatar escolhido > iniciais. As iniciais continuam
   valendo pra quem ainda não tem nem foto nem avatar definido. */
function avatarHtml(pessoa, classe){
  const cls = classe || 'avatar';
  if(pessoa && pessoa.foto){
    return '<img class="' + cls + '" src="/fotos/' + pessoa.foto + '" alt="">';
  }
  const tipo = pessoa && AVATARES[pessoa.avatar] ? pessoa.avatar : null;
  if(tipo){
    return '<span class="' + cls + ' avatar-svg">' + AVATARES[tipo] + '</span>';
  }
  const nome = (pessoa && pessoa.nome) || '?';
  return '<span class="' + cls + '">' + nome.trim().slice(0, 2).toUpperCase() + '</span>';
}

/* Catalogo unico de itens. A chave e a mesma do servidor (AREAS), entao
   liberar uma area e ver o item aparecer nao dependem de traducao nenhuma.
   `pagina` diz onde a area mora enquanto as telas forem dois arquivos. */
const ITENS = [
  // --- o proprio trabalho ---
  {grupo: 'Meu trabalho', chave: 'meu_painel',        texto: 'Meu painel',         pagina: '/', hash: '#painel',      icone: 'painel'},
  {grupo: 'Meu trabalho', chave: 'meu_atendimento',   texto: 'Esperando você',     pagina: '/', hash: '#atendimento', icone: 'retomada'},
  {grupo: 'Meu trabalho', chave: 'minhas_vendas',     texto: 'Minhas vendas',      pagina: '/', hash: '#vendas',      icone: 'vendas'},
  {grupo: 'Meu trabalho', chave: 'simulador',         texto: 'Simulação',          pagina: '/simulador',              icone: 'cifrao'},
  {grupo: 'Meu trabalho', chave: 'meu_followup',      texto: 'Meu follow-up',      pagina: '/follow-up',              icone: 'retomada'},
  {grupo: 'Meu trabalho', chave: 'minha_performance', texto: 'Minha performance',  pagina: '/', hash: '#performance', icone: 'desempenho'},
  {grupo: 'Operação', chave: 'expedicao',         texto: 'Expedição',          pagina: '/', hash: '#expedicao',   icone: 'expedicao'},
  // --- gestao ---
  {grupo: 'Comercial', chave: 'painel',        texto: 'Painel geral',       pagina: '/admin.html', hash: '#painel',        icone: 'painel'},
  {grupo: 'Marketing', chave: 'marketing',     texto: 'Marketing',          pagina: '/admin.html', hash: '#marketing',     icone: 'marketing'},
  {grupo: 'Marketing', chave: 'analytics',     texto: 'Analytics',          pagina: '/admin.html', hash: '#analytics',     icone: 'desempenho'},
  {grupo: 'Comercial', chave: 'auditoria',     texto: 'Comissões',          pagina: '/admin.html', hash: '#auditoria',     icone: 'lupa'},
  {grupo: 'Comercial', chave: 'metabonus',     texto: 'Meta Bônus',         pagina: '/admin.html', hash: '#metabonus',     icone: 'bonus'},
  {grupo: 'Comercial', chave: 'desempenho',    texto: 'Desempenho do time', pagina: '/admin.html', hash: '#desempenho',    icone: 'desempenho'},
  {grupo: 'Operação', chave: 'carros',        texto: 'Carros pra chegar',  pagina: '/admin.html', hash: '#carros',        icone: 'carro'},
  {grupo: 'Administração', chave: 'rh',            texto: 'Gestão de pessoas',  pagina: '/admin.html', hash: '#rh',            icone: 'equipe'},
  {grupo: 'Comercial', chave: 'atendimento',   texto: 'Atendimento agora',  pagina: '/admin.html', hash: '#atendimento',   icone: 'retomada'},
  {grupo: 'Comercial', chave: 'retomada',      texto: 'Follow-up do time',  pagina: '/admin.html', hash: '#retomada',      icone: 'retomada'},
  {grupo: 'Administração', chave: 'fechamento',    texto: 'Fechamento de mês',  pagina: '/admin.html', hash: '#fechamento',    icone: 'fechamento'},
  {grupo: 'Marketing', chave: 'ranking',       texto: 'Ranking de vendas',  pagina: '/painel.html',                        icone: 'ranking'},
  {grupo: 'Administração', chave: 'permissoes',    texto: 'Permissões',         pagina: '/admin.html', hash: '#permissoes',    icone: 'equipe', soMaster: true},
  {grupo: 'Administração', chave: 'configuracoes', texto: 'Configurações',      pagina: '/admin.html', hash: '#configuracoes', icone: 'config', soMaster: true},
];

/* Ordem dos grupos. Comercial primeiro: e o que se olha todo dia.
   "Meu trabalho" por ULTIMO — quem supervisiona nao usa, e quem so vende tem
   ele como unico grupo (e aí nem cabecalho aparece). Deixar em cima
   empurrava pra baixo justamente o que o gestor abre o dia inteiro. */
const ORDEM_GRUPOS = ['Comercial', 'Marketing', 'Operação', 'Administração',
                      'Meu trabalho'];

/* Grupos que comecam recolhidos quando existe mais de um. Quem tem gestao E
   vendas nao quer os dois blocos abertos competindo por altura; o do proprio
   trabalho fica guardado ate ser preciso. */
const FECHADOS_POR_PADRAO = ['Meu trabalho'];

const GRUPOS_FECHADOS = 'nevada_menu_grupos_fechados';
function gruposFechados(){
  try{ return new Set(JSON.parse(localStorage.getItem(GRUPOS_FECHADOS) || '[]')); }
  catch(e){ return new Set(); }
}
function guardarGruposFechados(s){
  try{ localStorage.setItem(GRUPOS_FECHADOS, JSON.stringify([...s])); }catch(e){}
}

/* Compatibilidade: as duas telas ainda chamam montarSidebar com `portal`.
   Enquanto isso, o titulo vem daqui — mas o conteudo ja vem das areas. */
const PORTAIS = {
  vendedor: {titulo: 'Portal<br><span>Nevada</span>'},
  gestor:   {titulo: 'Portal<br><span>Nevada</span>'},
};

function urlExpedicao(){
  return location.hostname.endsWith('.onrender.com')
    ? 'https://nevada-expedicao.onrender.com'
    : 'http://' + location.hostname + ':8000';
}

/* ---------- menu guardado ---------- */
function menuRecolhido(){
  return localStorage.getItem('portalMenuRecolhido') === '1';
}
function aplicarMenuRecolhido(recolhido){
  document.body.classList.toggle('menu-recolhido', recolhido);
  localStorage.setItem('portalMenuRecolhido', recolhido ? '1' : '0');
}

/* ---------- tema ---------- */
function temaAtual(){
  return localStorage.getItem('portalTema') || 'claro';
}
function aplicarTema(tema){
  document.documentElement.setAttribute('data-tema', tema);
  localStorage.setItem('portalTema', tema);
  const btn = document.getElementById('temaBtn');
  if(btn){
    const escuro = tema === 'escuro';
    btn.innerHTML = (escuro ? ICONES.sol : ICONES.lua) + '<span>' + (escuro ? 'Claro' : 'Escuro') + '</span>';
  }
}
// Aplica antes de pintar a tela, senao pisca branco pra quem usa o escuro.
aplicarTema(temaAtual());

/* ---------- aviso de copia local ----------
   O portal local e o de producao sao identicos na tela, e ja aconteceu de
   alguem olhar o local — com dados congelados e sem os arquivos de
   atendimento — e concluir que producao estava fora. A faixa some sozinha em
   producao, entao ninguem ve nada no dia a dia. */
let versaoDaAba = null;

function avisarCopiaLocal(){
  if(document.querySelector('.faixa-local')) return;
  const faixa = document.createElement('div');
  faixa.className = 'faixa-local';
  faixa.innerHTML = '<b>Cópia local</b> — dados de teste, congelados. '
    + 'O portal de verdade é <a href="https://nevadaecopecas.onrender.com">nevadaecopecas.onrender.com</a>';
  document.body.appendChild(faixa);
  document.body.classList.add('com-faixa-local');
}

/* Quem deixa a janela aberta o dia todo — o atalho do Chrome em modo app, a TV
   da expedicao — nao recarrega sozinho e continua vendo a versao antiga. A aba
   guarda a versao que recebeu ao abrir e reconfere; mudou, avisa. */
function avisarVersaoNova(){
  if(document.querySelector('.faixa-versao')) return;
  const faixa = document.createElement('div');
  faixa.className = 'faixa-versao';
  faixa.innerHTML = '<b>Tem versão nova do portal.</b> '
    + '<button type="button" id="recarregarAgora">Atualizar agora</button>';
  document.body.appendChild(faixa);
  document.body.classList.add('com-faixa-versao');
  document.getElementById('recarregarAgora')
    .addEventListener('click', () => location.reload());
}

function conferirAmbiente(primeira){
  fetch('/api/ambiente', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if(!d) return;
      if(d.local) avisarCopiaLocal();
      if(primeira){ versaoDaAba = d.versao; return; }
      if(versaoDaAba && d.versao && d.versao !== versaoDaAba) avisarVersaoNova();
    })
    .catch(() => {});
}

conferirAmbiente(true);
setInterval(() => conferirAmbiente(false), 5 * 60 * 1000);
// Voltar pra aba e o momento natural de descobrir que ela envelheceu.
document.addEventListener('visibilitychange', () => {
  if(!document.hidden) conferirAmbiente(false);
});

/* Um grupo com um item so nao e grupo — vira ruido de cabecalho. Nesse caso o
   item sobe direto pra lista, sem titulo. */
function montarGrupos(itens, ativo, linkItem){
  const fechados = gruposFechados();
  const porGrupo = new Map();
  itens.forEach(it => {
    const g = it.grupo || 'Outros';
    if(!porGrupo.has(g)) porGrupo.set(g, []);
    porGrupo.get(g).push(it);
  });
  const ordem = [...ORDEM_GRUPOS.filter(g => porGrupo.has(g)),
                 ...[...porGrupo.keys()].filter(g => !ORDEM_GRUPOS.includes(g))];
  // Com um grupo so, cabecalho nenhum ajuda: mostra a lista direto.
  if(ordem.length <= 1) return itens.map(linkItem).join('');

  return ordem.map(g => {
    const lista = porGrupo.get(g);
    if(lista.length === 1) return linkItem(lista[0]);
    // O grupo do item aberto nunca aparece fechado: esconder onde a pessoa
    // esta faz o menu parecer que perdeu a opcao.
    const temAtivo = lista.some(it => it.chave === ativo);
    // Padrao fechado vale so na primeira vez: depois manda o que a pessoa
    // escolheu, incluindo reabrir e deixar aberto.
    const nuncaMexeu = !localStorage.getItem(GRUPOS_FECHADOS);
    const fechaSozinho = nuncaMexeu && FECHADOS_POR_PADRAO.includes(g);
    const aberto = temAtivo || (!fechados.has(g) && !fechaSozinho);
    return '<div class="nav-grupo' + (aberto ? '' : ' fechado') + '" data-grupo="' + g + '">'
      + '<button class="nav-grupo-titulo" data-grupo-btn="' + g + '">'
      + '<span>' + g + '</span>' + ICONES.recolher + '</button>'
      + '<div class="nav-grupo-itens">' + lista.map(linkItem).join('') + '</div>'
      + '</div>';
  }).join('');
}

function montarSidebar(ativo, opcoes){
  opcoes = opcoes || {};
  const portal = PORTAIS[opcoes.portal] ? opcoes.portal : 'vendedor';
  const cfg = PORTAIS[portal];
  const alvo = document.getElementById('sidebar');
  if(!alvo) return;

  /* O menu nasce das AREAS da pessoa, nao de uma lista fixa por portal.
     `opcoes.areas` vem de /api/admin/me ou /api/me; sem ela (tela ainda nao
     sabe quem e), mostra tudo — o servidor recusa o que nao for permitido, e
     esconder aqui e cortesia, nao seguranca. */
  const permitidas = opcoes.areas || null;
  const ehMaster = !!opcoes.master;
  const itens = ITENS.filter(it => {
    if(it.soMaster && !ehMaster) return false;
    return !permitidas || permitidas.includes(it.chave);
  });
  // A pagina atual nao precisa recarregar: dentro dela o link e so o hash.
  const aqui = location.pathname.replace(/index\.html$/, '/') || '/';
  const linkItem = (it) => {
    const mesmaPagina = it.pagina === aqui || (it.pagina === '/' && aqui === '/');
    const href = it.chave === 'expedicao' && !it.hash
      ? urlExpedicao()
      : (mesmaPagina && it.hash ? it.hash : it.pagina + (it.hash || ''));
    // O ranking e um painel de TV e a expedicao roda em outro servico: os dois
    // sao destino final, nao navegacao. Abrir em aba nova evita que quem
    // clicou perca de onde veio.
    const abaNova = it.chave === 'ranking' || (it.chave === 'expedicao' && !it.hash);
    return '<a class="nav-item' + (it.chave === ativo ? ' ativo' : '') + '" href="' + href + '"'
      + (abaNova ? ' target="_blank" rel="noopener"' : '')
      + ' data-nav="' + it.chave + '">' + ICONES[it.icone] + '<span>' + it.texto + '</span>'
      + '<span class="nav-badge oculto" data-badge="' + it.chave + '"></span></a>';
  };

  alvo.innerHTML =
    '<div class="sidebar-marca">'
    + '<img src="/logo-mark.png" alt="" onerror="this.style.display=\'none\'">'
    + '<div class="nome">' + cfg.titulo + '</div>'
    + '<button class="sidebar-recolher" id="recolherBtn" title="Guardar menu">' + ICONES.recolher + '</button>'
    + '</div>'
    + '<nav class="nav-lista">' + montarGrupos(itens, ativo, linkItem) + '</nav>'
    + '<div class="sidebar-rodape">'
    + '<button class="tema-btn" id="temaBtn"></button>'
    + '<div class="perfil">'
    + '<div class="perfil-foto" id="perfilFoto">--</div>'
    + '<div><div class="perfil-nome" id="perfilNome">—</div>'
    + '<button class="perfil-sair" id="sairBtn">Sair</button></div>'
    + '</div></div>';

  alvo.querySelectorAll('[data-grupo-btn]').forEach(b => {
    b.addEventListener('click', () => {
      const g = b.dataset.grupoBtn;
      const caixa = alvo.querySelector('[data-grupo="' + CSS.escape(g) + '"]');
      const fechados = gruposFechados();
      const vaiFechar = !caixa.classList.contains('fechado');
      caixa.classList.toggle('fechado', vaiFechar);
      if(vaiFechar) fechados.add(g); else fechados.delete(g);
      guardarGruposFechados(fechados);
    });
  });

  aplicarMenuRecolhido(menuRecolhido());
  document.getElementById('recolherBtn').addEventListener('click', () => aplicarMenuRecolhido(true));

  aplicarTema(temaAtual());
  document.getElementById('temaBtn').addEventListener('click', () => {
    aplicarTema(temaAtual() === 'escuro' ? 'claro' : 'escuro');
    if(typeof window.aoTrocarTema === 'function') window.aoTrocarTema();
  });
  document.getElementById('sairBtn').addEventListener('click', () => {
    if(typeof opcoes.aoSair === 'function') opcoes.aoSair();
    else fetch('/api/logout', {method:'POST'}).then(() => location.href = '/');
  });

  // fundo escuro pra fechar o menu no celular
  if(!document.getElementById('sidebarFundo')){
    const fundo = document.createElement('div');
    fundo.className = 'sidebar-fundo';
    fundo.id = 'sidebarFundo';
    fundo.addEventListener('click', fecharMenu);
    document.body.appendChild(fundo);
  }
}

function abrirMenu(){
  if(menuRecolhido()){ aplicarMenuRecolhido(false); return; }
  document.getElementById('sidebar').classList.add('aberto');
  document.getElementById('sidebarFundo').classList.add('ativo');
}
function fecharMenu(){
  document.getElementById('sidebar').classList.remove('aberto');
  const f = document.getElementById('sidebarFundo');
  if(f) f.classList.remove('ativo');
}

function preencherPerfil(nome, foto, avatar){
  const elNome = document.getElementById('perfilNome');
  const elFoto = document.getElementById('perfilFoto');
  if(elNome) elNome.textContent = nome || '—';
  if(elFoto){
    elFoto.outerHTML = avatarHtml({nome, foto, avatar}, 'perfil-foto')
      .replace('class="perfil-foto', 'id="perfilFoto" class="perfil-foto');
  }
}

function marcarBadge(chave, valor){
  const el = document.querySelector('[data-badge="' + chave + '"]');
  if(!el) return;
  if(valor){
    el.textContent = valor;
    el.classList.remove('oculto');
  }else{
    el.classList.add('oculto');
  }
}
