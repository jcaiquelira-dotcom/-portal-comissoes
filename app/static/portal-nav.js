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
  simulador: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h2M12 10h2M16 10h.01M8 14h2M12 14h2M16 14h.01M8 18h6"/></svg>',
  retomada: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 20.5l1.5-4.4A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z"/></svg>',
  ranking: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/></svg>',
  expedicao: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8h11v9H2z"/><path d="M13 11h4l3 3v3h-7z"/><circle cx="6" cy="18.5" r="1.7"/><circle cx="17" cy="18.5" r="1.7"/></svg>',
  fechamento: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  metas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/></svg>',
  equipe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5.3a3.2 3.2 0 0 1 0 5.4M18 20a6.5 6.5 0 0 0-3-5.5"/></svg>',
  registros: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h11l3 3v15H5z"/><path d="M9 9h7M9 13h7M9 17h4"/></svg>',
  config: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  marketing: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11v3a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/><path d="M19.5 5.5a9 9 0 0 1 0 13"/></svg>',
  desempenho: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l3.5-4 3 2.5L20 7"/><circle cx="10.5" cy="11" r="1.1" fill="currentColor" stroke="none"/><circle cx="13.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/></svg>',
  trocar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4L3 8l4 4"/><path d="M3 8h13a4 4 0 0 1 0 8h-1"/><path d="M17 20l4-4-4-4"/></svg>',
  sol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  lua: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  recolher: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  menu: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
};

const PORTAIS = {
  vendedor: {
    titulo: 'Portal do<br><span>Vendedor</span>',
    itens: [
      {chave: 'painel',    texto: 'Painel',                href: '/#painel', icone: 'painel'},
      {chave: 'vendas',    texto: 'Minhas vendas',         href: '/#vendas', icone: 'vendas'},
      {chave: 'simulador', texto: 'Simulador de desconto', href: '/simulador', icone: 'simulador'},
      {chave: 'retomada',  texto: 'Follow-up',              href: '/follow-up', icone: 'retomada'},
      {chave: 'marketing', texto: 'Meus números',           href: '/#marketing', icone: 'marketing'},
    ],
    trocar: {texto: 'Ir para a área do gestor', href: '/admin.html'},
  },
  gestor: {
    titulo: 'Área do<br><span>Gestor</span>',
    itens: [
      {chave: 'painel',      texto: 'Painel',            href: '#painel',      icone: 'painel'},
      {chave: 'fechamento',  texto: 'Fechamento de mês', href: '#fechamento',  icone: 'fechamento'},
      {chave: 'retomada',    texto: 'Follow-up do time',  href: '#retomada',    icone: 'retomada'},
      {chave: 'desempenho',  texto: 'Desempenho',        href: '#desempenho',  icone: 'desempenho'},
      {chave: 'marketing',   texto: 'Marketing',         href: '#marketing',   icone: 'marketing'},
      // Metas, Registros e o simulador viraram topicos dentro de Configuracoes:
      // sao telas de ajuste, nao de acompanhamento do dia.
      {chave: 'configuracoes', texto: 'Configurações',   href: '#configuracoes', icone: 'config'},
    ],
    trocar: {texto: 'Ir para o portal do vendedor', href: '/'},
  },
};

const ITENS_EXTERNOS = [
  {chave: 'ranking',   texto: 'Ranking de vendas',   href: '/painel.html', icone: 'ranking'},
  {chave: 'expedicao', texto: 'Painel de expedição', href: null,           icone: 'expedicao'},
];

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

function montarSidebar(ativo, opcoes){
  opcoes = opcoes || {};
  const portal = PORTAIS[opcoes.portal] ? opcoes.portal : 'vendedor';
  const cfg = PORTAIS[portal];
  const alvo = document.getElementById('sidebar');
  if(!alvo) return;

  const linkItem = (it) => {
    const href = it.href || urlExpedicao();
    const externo = it.chave === 'expedicao' || it.chave === 'ranking';
    return '<a class="nav-item' + (it.chave === ativo ? ' ativo' : '') + '" href="' + href + '"'
      + (externo ? ' target="_blank" rel="noopener"' : '')
      + ' data-nav="' + it.chave + '">' + ICONES[it.icone] + '<span>' + it.texto + '</span>'
      + '<span class="nav-badge oculto" data-badge="' + it.chave + '"></span></a>';
  };

  alvo.innerHTML =
    '<div class="sidebar-marca">'
    + '<img src="/logo-mark.png" alt="" onerror="this.style.display=\'none\'">'
    + '<div class="nome">' + cfg.titulo + '</div>'
    + '<button class="sidebar-recolher" id="recolherBtn" title="Guardar menu">' + ICONES.recolher + '</button>'
    + '</div>'
    + '<nav class="nav-lista">'
    + cfg.itens.map(linkItem).join('')
    + '<div class="nav-sep">Outros painéis</div>'
    + ITENS_EXTERNOS.map(linkItem).join('')
    + '</nav>'
    + '<div class="sidebar-rodape">'
    + '<a class="trocar-portal" href="' + cfg.trocar.href + '">' + ICONES.trocar
    + '<span>' + cfg.trocar.texto + '</span></a>'
    + '<button class="tema-btn" id="temaBtn"></button>'
    + '<div class="perfil">'
    + '<div class="perfil-foto" id="perfilFoto">--</div>'
    + '<div><div class="perfil-nome" id="perfilNome">—</div>'
    + '<button class="perfil-sair" id="sairBtn">Sair</button></div>'
    + '</div></div>';

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

function preencherPerfil(nome, foto){
  const elNome = document.getElementById('perfilNome');
  const elFoto = document.getElementById('perfilFoto');
  if(elNome) elNome.textContent = nome || '—';
  if(elFoto){
    if(foto){
      elFoto.outerHTML = '<img class="perfil-foto" id="perfilFoto" src="/fotos/' + foto + '" alt="">';
    }else{
      elFoto.textContent = (nome || '?').trim().slice(0,2).toUpperCase();
    }
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
