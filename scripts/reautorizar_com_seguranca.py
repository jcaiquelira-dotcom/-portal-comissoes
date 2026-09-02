# -*- coding: utf-8 -*-
"""
Reautoriza o Google e DESFAZ sozinho se quebrar o que ja funcionava.

Por que existe: em 02/09/2026 eu pedi ao gestor que reautorizasse pra somar um
escopo novo, sem dizer qual conta Google escolher. Ele escolheu outra — uma que
tem uma MCC vazia e nenhuma conta de anuncios — e o Google Ads, que estava
funcionando havia horas, parou. O refresh_token antigo so nao se perdeu porque
existia backup.

O erro nao foi dele: a tela do Google mostra varias contas e nada ali diz qual
e a certa. Entao a protecao tem que estar aqui.

Como funciona:
  1. mede o que funciona HOJE (Ads, Analytics, e o que mais responder)
  2. guarda o refresh_token atual
  3. roda a autorizacao
  4. mede de novo
  5. se algo que funcionava parou de funcionar, VOLTA o token antigo e diz o
     que quebrou. O escopo novo nao vale o preco de perder um que ja rodava.

O token novo nunca se perde: ele fica guardado em `refresh_token_recusado`
junto com o motivo, porque as vezes a conta certa pro escopo novo e mesmo
outra — e ai a resposta e ter duas credenciais, nao trocar uma pela outra.

Uso:
    python scripts/reautorizar_com_seguranca.py
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CRED = Path(r"G:\Meu Drive\portal-comissoes\segredos\google_ads.json")
AUTORIZAR = RAIZ / "scripts" / "autorizar_google_ads.py"
PY = sys.executable


def _ler() -> dict:
    return json.loads(CRED.read_text(encoding="utf-8"))


def _gravar(d: dict) -> None:
    CRED.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def testar(nome: str, script: str) -> bool:
    """Roda o --testar de um coletor. True se ele responde."""
    try:
        r = subprocess.run([PY, str(RAIZ / "app" / script), "--testar"],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace", cwd=str(RAIZ))
    except subprocess.TimeoutExpired:
        print(f"   {nome}: nao respondeu a tempo")
        return False
    ok = r.returncode == 0
    marca = "ok " if ok else "NAO"
    primeira = (r.stdout or r.stderr or "").strip().splitlines()
    print(f"   {marca} {nome}: {primeira[0][:90] if primeira else ''}")
    return ok


# Os coletores que a credencial serve hoje. Se algum deles estiver funcionando
# antes e parar depois, a troca e desfeita.
COLETORES = [("Google Ads", "google_ads_api.py"),
             ("Analytics", "analytics_api.py"),
             ("Perfil da Empresa", "perfil_google_api.py")]


def main() -> int:
    if not CRED.exists():
        sys.exit(f"Nao achei {CRED}")

    print("=" * 64)
    print("O QUE FUNCIONA AGORA (antes de mexer)")
    print("=" * 64)
    antes = {nome: testar(nome, s) for nome, s in COLETORES}
    token_antigo = _ler().get("refresh_token")

    print()
    print("=" * 64)
    print("AUTORIZACAO")
    print("=" * 64)
    print("ATENCAO: escolha a MESMA conta Google de sempre — a que administra")
    print("o Google Ads da Nevada. Se escolher outra, eu volto atras sozinho,")
    print("mas voce vai ter feito o caminho a toa.")
    print()
    r = subprocess.run([PY, str(AUTORIZAR)], cwd=str(RAIZ))
    if r.returncode != 0:
        print("\nA autorizacao nao terminou. Nada foi trocado.")
        return 1

    novo = _ler()
    if novo.get("refresh_token") == token_antigo:
        print("\nO token nao mudou. Nada a conferir.")
        return 0

    print()
    print("=" * 64)
    print("O QUE FUNCIONA DEPOIS")
    print("=" * 64)
    depois = {nome: testar(nome, s) for nome, s in COLETORES}

    quebrou = [n for n in antes if antes[n] and not depois[n]]
    ganhou = [n for n in antes if not antes[n] and depois[n]]

    print()
    if quebrou:
        d = _ler()
        d["refresh_token_recusado"] = d["refresh_token"]
        d["_nota_recusado"] = (
            f"token de {datetime.now().date().isoformat()} devolvido: quebrou "
            f"{', '.join(quebrou)}. Provavelmente e de outra conta Google.")
        d["refresh_token"] = token_antigo
        _gravar(d)
        print(f"DESFEITO. O token novo quebrou: {', '.join(quebrou)}.")
        print("Voltei o de antes; tudo que funcionava continua funcionando.")
        print("O token novo ficou guardado em 'refresh_token_recusado'.")
        print()
        print("Provavel causa: a conta Google escolhida nao e a que administra")
        print("essas contas. Rode de novo e escolha a outra.")
        return 2

    print("Token novo mantido.")
    if ganhou:
        print(f"Passou a funcionar: {', '.join(ganhou)}")
    ainda_nao = [n for n in depois if not depois[n]]
    if ainda_nao:
        print(f"Continua sem responder: {', '.join(ainda_nao)} — "
              "provavelmente a API ainda nao esta habilitada no Google Cloud.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
