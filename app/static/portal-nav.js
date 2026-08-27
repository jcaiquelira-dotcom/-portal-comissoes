/* ============================================================
   Menu lateral do Portal do Vendedor.
   Incluido por index.html, simulador.html e retomada.html — as tres telas
   montam o mesmo menu a partir daqui, entao mexer numa so muda em todas.

   Uso: <div id="sidebar"></div> + montarSidebar('painel')
   onde o argumento e a chave do item que deve ficar destacado.
   ============================================================ */

const ICONES = {
  painel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  vendas: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 12h18M3 18h12"/></svg>',
  simulador: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h2M12 10h2M16 10h.01M8 14h2M12 14h2M16 14h.01M8 18h6"/></svg>',
  retomada: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 20.5l1.5-4.4A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z"/></svg>',
  ranking: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/></svg>',
  expedicao: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8h11v9H2z"/><path d="M13 11h4l3 3v3h-7z"/><circle cx="6" cy="18.5" r="1.7"/><circle cx="17" cy="18.5" r="1.7"/></svg>',
  sol: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  lua: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  menu: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
};

const ITENS_MENU = [
  {chave: 'painel',    texto: 'Painel',              href: '/#painel',     icone: 'painel'},
  {chave: 'vendas',    texto: 'Minhas vendas',       href: '/#vendas',     icone: 'vendas'},
  {chave: 'simulador', texto: 'Simulador de desconto', href: '/simulador', icone: 'simulador'},
  {chave: 'retomada',  texto: 'Retomada',            href: '/retomada',    icone: 'retomada'},
];

const ITENS_EXTERNOS = [
  {chave: 'ranking',   texto: 'Painel de ranking',   href: '/painel.html', icone: 'ranking'},
  {chave: 'expedicao', texto: 'Painel de expedição', href: null,           icone: 'expedicao'},
];

function urlExpedicao(){
  return location.hostname.endsWith('.onrender.com')
    ? 'https://nevada-expedicao.onrender.com'
    : 'http://' + location.hostname + ':8000';
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
    + '<div class="nome">Portal do<br><span>Vendedor</span></div>'
    + '</div>'
    + '<nav class="nav-lista">'
    + ITENS_MENU.map(linkItem).join('')
    + '<div class="nav-sep">Outros painéis</div>'
    + ITENS_EXTERNOS.map(linkItem).join('')
    + '</nav>'
    + '<div class="sidebar-rodape">'
    + '<button class="tema-btn" id="temaBtn"></button>'
    + '<div class="perfil">'
    + '<div class="perfil-foto" id="perfilFoto">--</div>'
    + '<div><div class="perfil-nome" id="perfilNome">—</div>'
    + '<button class="perfil-sair" id="sairBtn">Sair</button></div>'
    + '</div></div>';

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
  document.getElementById('sidebar').classList.add('aberto');
  document.getElementById('sidebarFundo').classList.add('ativo');
}
function fecharMenu(){
  document.getElementById('sidebar').classList.remove('aberto');
  document.getElementById('sidebarFundo').classList.remove('ativo');
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
