"""Notificações push (Web Push/VAPID) para o aplicativo instalado no celular."""
from __future__ import annotations

import json
import os

from . import armazenamento, config

INSCRICOES = "data/push.json"


def inscrever(inscricao: dict) -> int:
    lista = armazenamento.ler_json(INSCRICOES, [])
    lista = [i for i in lista if i.get("endpoint") != inscricao.get("endpoint")]
    lista.append(inscricao)
    armazenamento.gravar_json(INSCRICOES, lista)
    return len(lista)


def _identificacao(cfg: dict) -> str:
    """'sub' do VAPID: os serviços de push (Apple em especial) exigem uma URL https real ou mailto."""
    url = os.environ.get("APP_URL") or cfg.get("url_app") or ""
    if not url.startswith("https://"):
        raise RuntimeError("endereço do app (APP_URL) não configurado; rode o implantar.sh de novo")
    return url


def enviar_detalhado(titulo: str, corpo: str, url: str = "/", tag: str | None = None) -> tuple[int, list[str]]:
    """Envia a todos os aparelhos inscritos. Retorna (quantos receberam, erros)."""
    lista = armazenamento.ler_json(INSCRICOES, [])
    if not lista:
        return 0, ["nenhum aparelho inscrito"]
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush

    cfg = config.carregar()
    vapid = Vapid01.from_pem(cfg["vapid_privada_pem"].encode())
    sub = _identificacao(cfg)
    dados = json.dumps({"titulo": titulo, "corpo": corpo[:500], "url": url, "tag": tag})
    ok, validas, erros = 0, [], []
    for i in lista:
        servico = i.get("endpoint", "").split("/")[2] if "//" in i.get("endpoint", "") else "?"
        try:
            webpush(i, data=dados, vapid_private_key=vapid, vapid_claims={"sub": sub}, ttl=86400)
            ok += 1
            validas.append(i)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            detalhe = (getattr(e.response, "text", "") or str(e))[:200]
            if status in (404, 410):
                print(f"Inscrição expirada removida ({servico}).")
                erros.append(f"{servico}: inscrição expirada")
                continue
            print(f"Falha no push ({servico}) HTTP {status}: {detalhe}")
            erros.append(f"{servico}: HTTP {status} {detalhe}")
            validas.append(i)
    if len(validas) != len(lista):
        armazenamento.gravar_json(INSCRICOES, validas)
    return ok, erros


def enviar(titulo: str, corpo: str, url: str = "/", tag: str | None = None) -> int:
    return enviar_detalhado(titulo, corpo, url, tag)[0]
