# -*- coding: utf-8 -*-
"""
Autoriza o acesso ao Google Ads uma vez e guarda o refresh_token.

Roda no seu computador, abre o navegador, voce escolhe a conta Google e
autoriza. O codigo que volta e trocado por um refresh_token permanente — o
Google nao rotaciona esse token, entao isso e feito UMA vez e nunca mais.

O que voce precisa ter em maos antes de rodar:
  - client_id e client_secret   (Google Cloud > APIs e servicos > Credenciais)
  - developer_token             (Google Ads > MCC > Ferramentas > API Center)
  - customer_id                 (o numero da conta de anuncios, so digitos)
  - login_customer_id           (o numero da MCC, se o acesso vem por ela)

O script NAO pede sua senha e nao ve nada disso: quem autentica e o proprio
Google, na janela dele. Aqui so chega o codigo de autorizacao.

Uso:
    python scripts/autorizar_google_ads.py
"""

import http.server
import io
import json
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

DESTINO = Path(r"G:\Meu Drive\portal-comissoes\segredos\google_ads.json")
ESCOPO = "https://www.googleapis.com/auth/adwords"
_recebido = {}


class Ouvinte(http.server.BaseHTTPRequestHandler):
    """Recebe o redirecionamento do Google com o codigo de autorizacao."""

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _recebido.update({k: v[0] for k, v in q.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _recebido
        self.wfile.write((
            "<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            + ("<h2>Pronto.</h2><p>Pode fechar esta aba e voltar pro terminal.</p>"
               if ok else
               f"<h2>Nao autorizado</h2><p>{_recebido.get('error', 'motivo desconhecido')}</p>")
            + "</body></html>").encode("utf-8"))

    def log_message(self, *a):
        pass          # sem ruido de servidor no terminal


# Portas FIXAS, e nao uma livre qualquer: o Google so redireciona pra endereco
# cadastrado antes no cliente OAuth, entao sortear porta daria
# redirect_uri_mismatch em toda tentativa. Sao tres pra funcionar mesmo se
# alguma estiver ocupada — as tres estao no guia, pra cadastrar de uma vez.
PORTAS = (8765, 8080, 8000)


def porta_livre() -> int:
    for p in PORTAS:
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
    sys.exit("Portas " + ", ".join(map(str, PORTAS)) + " todas ocupadas. "
             "Feche o que estiver usando e tente de novo.")


def perguntar(rotulo: str, atual: str = "") -> str:
    sufixo = f" [{atual}]" if atual else ""
    v = input(f"{rotulo}{sufixo}: ").strip()
    return v or atual


def main():
    print("=" * 62)
    print("AUTORIZACAO DO GOOGLE ADS")
    print("=" * 62)
    atual = {}
    if DESTINO.exists():
        atual = json.loads(DESTINO.read_text(encoding="utf-8"))
        print(f"(ja existe {DESTINO.name} — Enter mantem o valor entre colchetes)\n")

    cred = {
        "client_id": perguntar("client_id", atual.get("client_id", "")),
        "client_secret": perguntar("client_secret", atual.get("client_secret", "")),
        "developer_token": perguntar("developer_token", atual.get("developer_token", "")),
        # So digitos: o Google mostra com tracos (123-456-7890) mas a API
        # recusa nesse formato, e o erro que ela devolve nao diz isso.
        "customer_id": perguntar("customer_id (conta de anuncios, so numeros)",
                                 atual.get("customer_id", "")).replace("-", ""),
        "login_customer_id": perguntar("login_customer_id (a MCC, Enter se nao tiver)",
                                       atual.get("login_customer_id", "")).replace("-", ""),
    }
    if not (cred["client_id"] and cred["client_secret"]):
        sys.exit("client_id e client_secret sao obrigatorios.")

    porta = porta_livre()
    redirect = f"http://localhost:{porta}"
    servidor = http.server.HTTPServer(("127.0.0.1", porta), Ouvinte)
    ouvindo = threading.Thread(target=servidor.handle_request, daemon=True)
    ouvindo.start()

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": ESCOPO,
        # offline + consent: sem os dois o Google devolve so o access_token de
        # 1 hora e nenhum refresh_token — e o erro so aparece amanha, quando o
        # pipeline nao renova.
        "access_type": "offline",
        "prompt": "consent",
    })
    print(f"\nO redirecionamento sera {redirect}")
    print("IMPORTANTE: esse endereco precisa estar cadastrado no seu cliente")
    print("OAuth, em 'URIs de redirecionamento autorizados'. Se der erro de")
    print("redirect_uri_mismatch, e isso — cadastre e rode de novo.\n")
    print("Abrindo o navegador. Escolha a conta que administra o Google Ads...")
    webbrowser.open(url)
    print(f"(se nao abrir, cole no navegador:\n{url}\n)")

    # Espera de verdade. Sem o join, o server_close abaixo derrubava o socket
    # segundos depois de abrir o navegador — antes de qualquer pessoa conseguir
    # escolher a conta e clicar em autorizar. Cinco minutos e folga suficiente
    # pra quem precisa procurar a conta certa.
    print("Aguardando a autorizacao no navegador (ate 5 minutos)...")
    ouvindo.join(timeout=300)
    servidor.server_close()
    if "code" not in _recebido:
        sys.exit(f"Nao veio codigo: {_recebido.get('error', 'a janela foi fechada?')}")

    corpo = urllib.parse.urlencode({
        "code": _recebido["code"],
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", corpo),
            timeout=60) as r:
        tok = json.loads(r.read().decode())
    if not tok.get("refresh_token"):
        sys.exit("O Google nao devolveu refresh_token. Isso acontece quando a\n"
                 "conta ja autorizou este app antes: revogue em\n"
                 "myaccount.google.com/permissions e rode de novo.")

    cred["refresh_token"] = tok["refresh_token"]
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(cred, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nrefresh_token gravado em {DESTINO}")
    print("Esse arquivo fica fora do git. Agora confira a conexao:")
    print("    python app/google_ads_api.py --testar")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
