"""Leitura das credenciais do .env.

Segredo nao entra em codigo: .env esta no .gitignore, o codigo nao.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ENV = ROOT / ".env"


def env(nome, obrigatorio=True):
    if os.environ.get(nome):
        return os.environ[nome]
    if _ENV.exists():
        for linha in _ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            if k.strip() == nome:
                return v.strip().strip('"').strip("'")
    if obrigatorio:
        raise SystemExit(
            f"{nome} nao encontrada.\n"
            f"Adicione uma linha  {nome}=valor  em {_ENV}"
        )
    return None
