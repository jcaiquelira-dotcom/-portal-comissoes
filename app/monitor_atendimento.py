# -*- coding: utf-8 -*-
"""
Monitor de atendimento sem resposta — a versao que roda NA NUVEM, dentro do
proprio servidor do portal (thread de fundo).

Historia: a primeira versao rodava no computador da loja pelo Agendador de
Tarefas, de 2 em 2 minutos. Em 28/08/2026 o Agendador passou a recusar
qualquer tarefa nova do usuario (resultado 1, sem log — sem admin nem da pra
ler o motivo), e de todo jeito depender do PC ligado era a parte fraca: o
gestor perguntou exatamente "isso depende do meu computador?". Agora nao
depende. O Render roda o portal 24/7 e este thread junto.

O que ele faz: lista as sessoes abertas do Totalk (leitura pura, nenhuma
escrita la), acha quem falou por ultimo sem resposta da loja, conta o tempo em
MINUTOS UTEIS (seg-sex 08:30-17:30, sab 09:00-12:00) e grava o resultado na
chave atendimento_alerta — a mesma que as telas do vendedor e do gestor leem.

Fora do horario de atendimento ele dorme: nenhuma chamada a API, so um pacote
"fora_do_horario" pra tela explicar quando volta.

O token do Totalk vem da chave segredo_totalk do banco (o mesmo banco que ja
guarda as credenciais do portal). Sem token, o thread fica quieto.

scripts/monitorar_sem_resposta.py continua existindo pra rodada manual local.
"""

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://api.wts.chat"
FUSO = timezone(timedelta(hours=-3))

ATENCAO, URGENTE, CRITICO = 15, 30, 60
INTERVALO_S = 120

JANELAS = {0: ("08:30", "17:30"), 1: ("08:30", "17:30"), 2: ("08:30", "17:30"),
           3: ("08:30", "17:30"), 4: ("08:30", "17:30"), 5: ("09:00", "12:00")}
TETO_UTEIS = 2 * 9 * 60          # dois dias cheios de expediente

# userId do Totalk -> (id no portal, nome na tela).
#
# O terceiro assento era do Gustavo, que se desligou em 31/08/2026; o Lucas
# assumiu o MESMO WhatsApp. Aqui nao ha corte por data como no historico de
# vendas: esta fila e "quem esta esperando resposta AGORA", e quem tem de
# responder e quem senta na cadeira hoje. Enquanto apontava pro gustavo, 19
# conversas (13 criticas) ficavam num usuario bloqueado — ninguem via, e o
# cliente esperando do outro lado.
ATENDENTES = {
    "75f20108-887e-47c1-b245-b1c12565e484": ("flavia", "Flávia"),
    "1d6778d5-d482-43bc-9d5b-dcbb4ed0528d": ("matheus", "Matheus"),
    "26ccb5d3-df37-429b-b509-7a122a2deb2d": ("lucas", "Lucas"),
}

ENCERRAMENTOS = {
    "ok", "okay", "okey", "blz", "beleza", "valeu", "vlw", "obrigado",
    "obrigada", "obg", "brigado", "brigada", "agradecido", "agradecida",
    "isso", "certo", "perfeito", "show", "otimo", "ótimo", "legal", "top",
    "entendi", "ta bom", "tá bom", "tabom", "ta certo", "tudo bem", "de nada",
    "bom dia", "boa tarde", "boa noite", "att", "amem", "amém",
    # Vistos na fila real de 29-31/08/2026 — todos viraram alarme CRITICO so
    # por nao estarem nesta lista:
    "grato", "grata", "gratidao", "obrigadao", "tudo sim", "sim tudo", "ah sim",
    "disponha", "imagina", "tranquilo", "tranquila", "suave", "fechado",
    "combinado", "aham", "uhum", "opa", "certinho", "maravilha",
    # Segunda leva, 01/09/2026: o gestor apontou que a fila continuava cobrando
    # resposta de quem so tinha se despedido — "a gente fala que nao temos, o
    # cliente manda ok obrigado, e fica la como sem resposta".
    "obgd", "blza", "brigadao", "demoro", "ta", "tah", "infelizmente",
    "tenha um bom dia", "tenha uma boa tarde", "tenha uma boa noite",
    "obrigado pela atencao", "obrigada pela atencao", "pela atencao",
    "agradeco", "agradecemos", "ate mais", "ate logo", "falou", "abraco",
    "bom saber", "que pena", "poxa", "entendido", "anotado", "ciente",
}
SO_EMOJI = re.compile(r"^[\W_]+$", re.UNICODE)
_PONTAS = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_SEPARA = re.compile(r"[\W_]+", re.UNICODE)

# Nome de contato nao muda: cache em memoria vive enquanto o processo viver.
_nomes: dict = {}


def _janela_do_dia(d):
    faixa = JANELAS.get(d.weekday())
    if not faixa:
        return None
    h1, m1 = map(int, faixa[0].split(":"))
    h2, m2 = map(int, faixa[1].split(":"))
    return (d.replace(hour=h1, minute=m1, second=0, microsecond=0),
            d.replace(hour=h2, minute=m2, second=0, microsecond=0))


def em_horario(agora_local) -> bool:
    j = _janela_do_dia(agora_local)
    return bool(j) and j[0] <= agora_local <= j[1]


def proxima_abertura(agora_local):
    d = agora_local
    for _ in range(8):
        j = _janela_do_dia(d)
        if j and d <= j[0]:
            return j[0]
        if j and d <= j[1]:
            return d
        d = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    return None


def minutos_uteis(desde_utc, agora_utc) -> int:
    ini = desde_utc.astimezone(FUSO)
    fim = agora_utc.astimezone(FUSO)
    total, d = 0.0, ini
    for _ in range(40):
        if d.date() > fim.date():
            break
        j = _janela_do_dia(d)
        if j:
            a = max(d if d.date() == ini.date() else j[0], j[0])
            b = min(fim, j[1]) if d.date() == fim.date() else j[1]
            if b > a:
                total += (b - a).total_seconds() / 60
        d = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    return int(total)


_REPETIDA = re.compile(r"(.)\1{2,}", re.UNICODE)
_ENC_ACHATADO = None


def _achatar(t: str) -> str:
    """Tira acento e encolhe letra esticada: "Blzzz" -> "blz", "ahhh" -> "ah".

    Cliente escreve como fala. Sem isto, "blz" era encerramento e "blzzz" era
    urgencia critica — a mesma pessoa dizendo a mesma coisa.
    """
    import unicodedata
    sem = "".join(c for c in unicodedata.normalize("NFKD", t)
                  if not unicodedata.combining(c))
    return _REPETIDA.sub(r"\1", sem)


def _encerramentos():
    """ENCERRAMENTOS na mesma forma achatada do texto que chega.

    Comparar texto sem acento contra uma lista COM acento nunca casaria "otimo"
    com "ótimo" — o achatamento tem que valer pros dois lados.
    """
    global _ENC_ACHATADO
    if _ENC_ACHATADO is None:
        _ENC_ACHATADO = {_achatar(x) for x in ENCERRAMENTOS}
    return _ENC_ACHATADO


# Palavras que nao encerram sozinhas, mas acompanham quem encerra: "obrigado
# MESMO", "brigado VC", "TA BOM obrigado". Os primeiros nomes dos atendentes
# entram junto, porque "Obrigado Flavia" e agradecimento e nao pergunta — e
# saem do proprio mapa ATENDENTES, pra nao virar lista paralela pra manter.
REFORCO = {"muito", "mesmo", "entao", "ai", "ja", "e", "eh", "ah", "ok",
           "vc", "voce", "vcs", "ta", "tah", "bom", "boa", "sim", "por", "isso",
           "tudo", "nada", "de", "pra", "pela", "pelo", "a", "o", "tb", "tbm",
           "bem", "atencao", "dia", "tarde", "noite", "com", "seu", "sua",
           "tao", "entao", "so", "mais", "demais", "viu", "hein", "ne",
           "ate", "que", "pena", "logo", "vez", "outra", "qualquer", "coisa"}


def so_agradeceu(texto: str) -> bool:
    t = (texto or "").strip().lower()
    if not t or len(t) > 40:
        return False
    if SO_EMOJI.fullmatch(t):
        return True
    enc = _encerramentos()
    limpo = _achatar(_PONTAS.sub("", t))
    if limpo in enc:
        return True
    nomes = {_achatar(nome.split()[0].lower()) for _, nome in ATENDENTES.values()}
    palavras = [p for p in _SEPARA.split(limpo) if p]
    if not palavras:
        return False
    # Exige PELO MENOS UMA palavra de encerramento de verdade. Sem isso, uma
    # mensagem so de reforco ("ta bom?", "voce ai") seria lida como despedida e
    # a conversa sumiria da fila com o cliente ainda esperando.
    if not any(p in enc for p in palavras):
        return False
    return all(p in enc or p in REFORCO or p in nomes for p in palavras)


def _get(token, path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def _quando(txt):
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt[:26].rstrip("Z") + "+00:00")
    except ValueError:
        return None


def coletar(token):
    agora = datetime.now(timezone.utc)
    esperando, antigas = [], []
    agradecimentos = 0

    for status in ("PENDING", "IN_PROGRESS"):
        pagina = 1
        while pagina <= 40:
            d = _get(token, "/chat/v2/session", {
                "Status": status, "PageNumber": pagina, "PageSize": 100,
                "OrderBy": "createdat", "OrderDirection": "DESCENDING"})
            itens = d.get("items") or []
            for s in itens:
                entrada = _quando(s.get("lastMessageIn"))
                saida = _quando(s.get("lastMessageOut"))
                if not entrada or (saida and saida >= entrada):
                    continue
                minutos = minutos_uteis(entrada, agora)
                if minutos < ATENCAO:
                    continue
                if minutos > TETO_UTEIS:
                    antigas.append(minutos)
                    continue
                if so_agradeceu(s.get("lastMessageText")):
                    agradecimentos += 1
                    continue
                vend = ATENDENTES.get(s.get("userId"))
                esperando.append({
                    "id": s["id"],
                    "contato_id": s.get("contactId"),
                    "url": s.get("previewUrl"),
                    "ultima_em": entrada.isoformat(timespec="seconds"),
                    "minutos": minutos,
                    "desde": entrada.astimezone(FUSO).isoformat(timespec="minutes"),
                    "status": status,
                    "vendedor_id": vend[0] if vend else "",
                    "atendente": vend[1] if vend else ("—" if s.get("userId") else "sem atendente"),
                    "ultima_msg": (s.get("lastMessageText") or "")[:120],
                    "nunca_respondida": saida is None,
                    "nivel": ("critico" if minutos >= CRITICO
                              else "urgente" if minutos >= URGENTE else "atencao"),
                })
            if not d.get("hasMorePages"):
                break
            pagina += 1

    esperando.sort(key=lambda e: -e["minutos"])
    # O nome vem do cache quando ja foi visto; o teto vale so pras buscas NOVAS,
    # pra uma rodada nao virar centenas de chamadas. Quem ficar sem nome nesta
    # rodada aparece com "?" e ganha o nome na proxima — melhor que sumir.
    novas = 0
    for e in esperando:
        cid = e["contato_id"]
        if cid in _nomes:
            e["cliente"] = _nomes[cid]
            continue
        if novas >= 40:
            e["cliente"] = "?"
            continue
        try:
            e["cliente"] = _get(token, f"/core/v1/contact/{cid}").get("name") or "?"
        except Exception:
            e["cliente"] = "?"
        if e["cliente"] != "?":
            _nomes[cid] = e["cliente"]
        novas += 1
        time.sleep(0.25)
    return esperando, antigas, agradecimentos


def montar_pacote(token):
    agora_local = datetime.now(FUSO)
    if not em_horario(agora_local):
        abre = proxima_abertura(agora_local)
        return {
            "gerado_em": agora_local.isoformat(timespec="seconds"),
            "fora_do_horario": True,
            "volta_em": abre.isoformat(timespec="minutes") if abre else None,
            "total": 0, "contagem": {"critico": 0, "urgente": 0, "atencao": 0},
            "conversas": [],
            "limites": {"atencao": ATENCAO, "urgente": URGENTE, "critico": CRITICO},
        }

    esperando, antigas, agradecimentos = coletar(token)
    return {
        "gerado_em": agora_local.isoformat(timespec="seconds"),
        "limites": {"atencao": ATENCAO, "urgente": URGENTE, "critico": CRITICO},
        "contagem": {n: sum(1 for e in esperando if e["nivel"] == n)
                     for n in ("critico", "urgente", "atencao")},
        "total": len(esperando),
        "paradas_antigas": len(antigas),
        "so_agradeceram": agradecimentos,
        # Sem corte: com 3 vendedores dividindo a fila, um teto global fazia
        # sumir do portal de um deles quem estava no fim da ordem por tempo.
        # O gestor contava 23 pra Flavia e o portal dela mostrava 17.
        "conversas": esperando,
        "por_vendedor": {
            vid: {"nome": nome,
                  "total": sum(1 for e in esperando if e["vendedor_id"] == vid),
                  "critico": sum(1 for e in esperando
                                 if e["vendedor_id"] == vid and e["nivel"] == "critico")}
            for vid, nome in {v[0]: v[1] for v in ATENDENTES.values()}.items()
        },
    }


def iniciar(ler_token, gravar, log=print):
    """Sobe o thread. ler_token() devolve o token (ou None); gravar(pacote)
    persiste no mesmo lugar que as telas leem."""

    def laco():
        # Espera curta antes da primeira rodada: deixa o servidor terminar de
        # subir antes de comecar a trabalhar.
        time.sleep(10)
        ultimo_fora = False
        while True:
            try:
                token = ler_token()
                if token:
                    pacote = montar_pacote(token)
                    fora = bool(pacote.get("fora_do_horario"))
                    # Fora do horario, grava so uma vez (na virada) e dorme
                    # mais: nada muda ate abrir de novo.
                    if not fora or not ultimo_fora:
                        gravar(pacote)
                    ultimo_fora = fora
                    if fora:
                        time.sleep(15 * 60)
                        continue
            except Exception as e:  # rede caiu, API 500 — a proxima rodada tenta de novo
                log(f"[monitor-atendimento] {type(e).__name__}: {str(e)[:120]}")
            time.sleep(INTERVALO_S)

    t = threading.Thread(target=laco, daemon=True, name="monitor-atendimento")
    t.start()
    return t
