"""Envio de alertas. Telegram se configurado; senão, imprime no log."""
from __future__ import annotations

import os

import requests


def enviar(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    print("=== ALERTA ===\n" + texto + "\n==============")
    if not (token and chat):
        return False
    # Telegram limita mensagens a 4096 caracteres
    for i in range(0, len(texto), 3900):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto[i:i + 3900], "disable_web_page_preview": True},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"Falha ao enviar ao Telegram: HTTP {r.status_code} {r.text[:200]}")
            return False
    return True
