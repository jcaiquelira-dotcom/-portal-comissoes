# -*- coding: utf-8 -*-
"""Ponto de entrada do portal. Desde 03/09/2026 (Fase 4) o codigo mora em
nucleo.py (comum) e areas/*.py (uma por area); este arquivo so junta tudo na
ordem de sempre e sobe o servidor. `from nucleo import *` reexporta os nomes
publicos pra quem importa `server` de fora (importar_fluxo_caixa, os coletores
do Google) — e os privados sao trazidos nominalmente logo abaixo.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nucleo import *  # noqa: F401,F403
# Privados que scripts externos alcancam via `server` (importar_fluxo_caixa etc.):
from nucleo import (
    _REGRAS_SIMULADOR_FILE,
    _SIMULADOR_DB_LOCAL,
    _achatar_canal,
    _artigo,
    _assunto_msg,
    _atd_pendentes,
    _atd_resolvidos,
    _cache_requisicao,
    _caminho_crm,
    _chave_de,
    _contagem_marcacoes,
    _faixa_tempo_de_simulador,
    _faixa_valor_de_simulador,
    _hash_codigo,
    _hash_senha,
    _indice_do_plano,
    _limpar_msg,
    _mb_agregar,
    _mb_bruto,
    _mb_gravar,
    _nome_aba_excel,
    _peca_curta,
    _primeiro_nome,
    _resumo_retomada,
    _rh_ler,
    _sem_acento_simples,
    _senha_confere,
    _simulador_db_local,
    _topo_retomada,
    _unaccent_disponivel_simulador,
)
import nucleo as _nucleo
# O nucleo so define estes num dos modos (Postgres x arquivo), como no
# server.py original. Liga o que existir; o resto fica sem ligar, igual antes.
for _nome in ("_PgJson", "_conn_cache", "_db_conectar", "_db_descartar_conexao", "_db_escrever", "_db_ler", "_db_preparar_tabela", "_laco_perfil", "_mon", "_mon_gravar", "_mon_token", "_perfil_atual", "_perfil_gravar", "_simulador_preparar_schema", "_sn", "_sn_atual", "_sn_chave", "_sn_gravar", "_th",):
    if hasattr(_nucleo, _nome):
        globals()[_nome] = getattr(_nucleo, _nome)
del _nome, _nucleo

# Ordem de import = ordem original de registro das rotas.
import areas.simulador  # noqa: E402,F401
import areas.contas  # noqa: E402,F401
import areas.auditoria  # noqa: E402,F401
import areas.rh  # noqa: E402,F401
import areas.metas_bonus  # noqa: E402,F401
import areas.expedicao  # noqa: E402,F401
import areas.carros  # noqa: E402,F401
import areas.marketing  # noqa: E402,F401
import areas.retomada  # noqa: E402,F401
import areas.nuvem  # noqa: E402,F401

if __name__ == "__main__":
    # use_reloader liga SÓ o recarregador: mudou o código, o servidor reinicia
    # sozinho, sem ninguém precisar lembrar de reiniciar na mão.
    #
    # Continua sem debug=True de propósito. debug traz junto o depurador do
    # Werkzeug, que numa tela de erro abre um console de Python rodando dentro
    # do servidor. Como isto escuta em 0.0.0.0, esse console ficaria ao alcance
    # de qualquer um na rede da loja -- daria pra ler os telefones dos clientes
    # e os segredos do processo.
    #
    # O reloader vigia os módulos Python importados, não a pasta data/. Os JSON
    # que a sincronização reescreve não disparam reinício, e os arquivos de
    # static/ são lidos a cada requisição -- editar a tela não pede reinício.
    app.run(host="0.0.0.0", port=8010, use_reloader=True)
