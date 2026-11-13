"""Configuração secreta do aplicativo, guardada no bucket privado (config/app.json).

Gerada automaticamente na primeira vez: código de acesso, segredo de sessão e chaves VAPID
(usadas para assinar as notificações push).
"""
from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import armazenamento

CAMINHO = "config/app.json"
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I


def novo_codigo() -> str:
    return "-".join("".join(secrets.choice(ALFABETO) for _ in range(4)) for _ in range(4))


def _vapid() -> tuple[str, str]:
    chave = ec.generate_private_key(ec.SECP256R1())
    pem = chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
    pub = chave.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
    return pem, base64.urlsafe_b64encode(pub).rstrip(b"=").decode()


def carregar() -> dict:
    cfg = armazenamento.ler_json(CAMINHO, {})
    mudou = False
    if not cfg.get("codigo_acesso"):
        cfg["codigo_acesso"] = novo_codigo()
        mudou = True
    if not cfg.get("segredo_sessao"):
        cfg["segredo_sessao"] = secrets.token_hex(32)
        mudou = True
    if not cfg.get("vapid_privada_pem"):
        cfg["vapid_privada_pem"], cfg["vapid_publica"] = _vapid()
        mudou = True
    if mudou:
        armazenamento.gravar_json(CAMINHO, cfg)
    return cfg


def salvar(cfg: dict) -> None:
    armazenamento.gravar_json(CAMINHO, cfg)
