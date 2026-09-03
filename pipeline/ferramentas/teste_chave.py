"""Chamada minima so pra confirmar que a chave funciona e tem credito."""

import os
from pathlib import Path

import anthropic

from config import env

ROOT = Path(__file__).resolve().parent.parent
os.environ["ANTHROPIC_API_KEY"] = env("ANTHROPIC_API_KEY")

client = anthropic.Anthropic()
try:
    r = client.messages.create(
        model="claude-opus-5",
        max_tokens=20,
        messages=[{"role": "user", "content": "Responda apenas: ok"}],
    )
    print("CHAVE OK")
    print("  modelo :", r.model)
    print("  resposta:", "".join(b.text for b in r.content if b.type == "text").strip())
    print("  tokens : entrada", r.usage.input_tokens, "| saida", r.usage.output_tokens)
except anthropic.AuthenticationError:
    print("FALHOU: chave invalida ou revogada")
except anthropic.PermissionDeniedError as e:
    print("FALHOU: sem permissao —", e)
except anthropic.BadRequestError as e:
    print("FALHOU (400):", e)
except anthropic.APIStatusError as e:
    print(f"FALHOU (HTTP {e.status_code}):", e.message)
