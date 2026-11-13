"""Envio de alertas: push no aplicativo e, se configurado, Telegram (canal reserva).
Sempre imprime no log."""
from __future__ import annotations

import os

import requests


def _telegram(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    for i in range(0, len(texto), 3900):  # limite de 4096 caracteres por mensagem
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": texto[i:i + 3900],
                                    "disable_web_page_preview": True}, timeout=30)
        except requests.RequestException as e:
            print(f"Falha ao enviar ao Telegram: {e}")
            return False
        if r.status_code != 200:
            print(f"Falha ao enviar ao Telegram: HTTP {r.status_code}")
            return False
    return True


def _push(titulo: str, corpo: str, tag: str | None, url: str = "/") -> int:
    try:
        from . import push
        return push.enviar(titulo, corpo, url, tag)
    except Exception as e:  # o push nunca pode derrubar a rotina
        print(f"Falha no push: {e}")
        return 0


def enviar(texto: str, titulo: str = "Prazos OAB", corpo: str | None = None, tag: str | None = None,
           url: str = "/") -> None:
    """texto: mensagem completa (log e Telegram). titulo/corpo: versão curta para o push."""
    print("=== ALERTA ===\n" + texto + "\n==============")
    n = _push(titulo, corpo or texto, tag, url)
    print(f"Push enviado a {n} aparelho(s).")
    _telegram(texto)
