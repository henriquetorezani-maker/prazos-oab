"""Notificações push (Web Push/VAPID) para o aplicativo instalado no celular."""
from __future__ import annotations

import json

from . import armazenamento, config

INSCRICOES = "data/push.json"


def inscrever(inscricao: dict) -> int:
    lista = armazenamento.ler_json(INSCRICOES, [])
    lista = [i for i in lista if i.get("endpoint") != inscricao.get("endpoint")]
    lista.append(inscricao)
    armazenamento.gravar_json(INSCRICOES, lista)
    return len(lista)


def enviar(titulo: str, corpo: str, url: str = "/", tag: str | None = None) -> int:
    """Envia a todos os aparelhos inscritos. Retorna quantos receberam."""
    lista = armazenamento.ler_json(INSCRICOES, [])
    if not lista:
        return 0
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush

    cfg = config.carregar()
    vapid = Vapid01.from_pem(cfg["vapid_privada_pem"].encode())
    sub = cfg.get("url_app") or "https://prazos-oab.invalid"
    dados = json.dumps({"titulo": titulo, "corpo": corpo[:500], "url": url, "tag": tag})
    ok, validas = 0, []
    for i in lista:
        try:
            webpush(i, data=dados, vapid_private_key=vapid, vapid_claims={"sub": sub}, ttl=86400)
            ok += 1
            validas.append(i)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                print("Inscrição expirada removida.")
                continue
            print(f"Falha no push: {e}")
            validas.append(i)
    if len(validas) != len(lista):
        armazenamento.gravar_json(INSCRICOES, validas)
    return ok
