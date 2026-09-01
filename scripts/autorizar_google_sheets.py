# -*- coding: utf-8 -*-
"""Amplia a autorizacao do Google para incluir as planilhas de comissao.

O cliente OAuth ja existe e ja tem refresh_token para Ads e Analytics — o que
falta e o consentimento para Sheets. Este script reaproveita client_id e
client_secret do google_ads.json e pede TODOS os escopos de novo, porque o
Google devolve um refresh_token por consentimento, nao por escopo: pedir so o
novo trocaria o token antigo por um mais fraco e quebraria Ads e Analytics.

Diferente de scripts/autorizar_google_ads.py, este nao pergunta nada — roda
direto. Serve pra quando os dados ja estao no arquivo e o unico passo humano e
clicar "permitir" na janela do Google.

Escopos pedidos, e por que cada um:
    adwords                  ja usado, mantido pra nao perder
    analytics.readonly       ja usado, mantido pra nao perder
    spreadsheets.readonly    ler as planilhas de comissao
    drive.metadata.readonly  achar cada planilha pelo NOME e pegar o id.
                             So metadado: lista nome e id, nao abre conteudo de
                             mais nada. drive.readonly daria leitura do Drive
                             inteiro, muito alem do que a tarefa precisa.

Uso:
    python scripts/autorizar_google_sheets.py
"""

import http.server
import json
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

DESTINO = Path(r"G:\Meu Drive\portal-comissoes\segredos\google_ads.json")
PORTAS = (8765, 8080, 8000)      # as mesmas ja cadastradas no cliente OAuth
ESCOPO = " ".join([
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
])
_recebido = {}


class Ouvinte(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _recebido.update({k: v[0] for k, v in q.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _recebido
        self.wfile.write((
            "<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            + ("<h2>Pronto.</h2><p>Pode fechar esta aba.</p>" if ok else
               f"<h2>Nao autorizado</h2><p>{_recebido.get('error', '?')}</p>")
            + "</body></html>").encode("utf-8"))

    def log_message(self, *a):
        pass


def porta_livre():
    for p in PORTAS:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    sys.exit("Portas 8765/8080/8000 ocupadas.")


def main():
    if not DESTINO.exists():
        sys.exit(f"{DESTINO} nao existe — rode autorizar_google_ads.py antes.")
    cred = json.loads(DESTINO.read_text(encoding="utf-8"))

    porta = porta_livre()
    redirect = f"http://localhost:{porta}"
    servidor = http.server.HTTPServer(("127.0.0.1", porta), Ouvinte)
    t = threading.Thread(target=servidor.handle_request, daemon=True)
    t.start()

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",
        "prompt": "consent",
    })
    print(f"redirecionamento: {redirect}")
    print("Abrindo o navegador. Escolha a conta do Drive e autorize.\n")
    print("Se o navegador nao abrir, cole este endereco nele:\n")
    print(url + "\n")
    webbrowser.open(url)

    # 20 minutos. Cinco nao bastaram em 01/09/2026: entre eu abrir a janela e
    # o gestor escolher a conta passou mais que isso, e a espera morreu antes
    # do clique — obrigando a refazer tudo por causa de um cronometro meu.
    t.join(timeout=1200)
    if "code" not in _recebido:
        sys.exit("Nao chegou codigo de autorizacao. "
                 f"Motivo: {_recebido.get('error', 'tempo esgotado')}")

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
        sys.exit("O Google nao devolveu refresh_token. Revogue o acesso em\n"
                 "myaccount.google.com/permissions e rode de novo.")

    # Grava so o que mudou. O arquivo carrega developer_token, customer_id e
    # ga4_property_id que nada aqui deveria encostar.
    cred["refresh_token"] = tok["refresh_token"]
    cred["escopos"] = tok.get("scope", ESCOPO)
    cred["autorizado_em"] = datetime.now().isoformat(timespec="seconds")
    DESTINO.write_text(json.dumps(cred, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nrefresh_token atualizado.")
    print("escopos concedidos:", tok.get("scope"))


if __name__ == "__main__":
    main()
