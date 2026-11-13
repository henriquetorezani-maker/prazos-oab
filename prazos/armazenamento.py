"""Sincroniza os arquivos de estado com o Cloud Storage quando GCS_BUCKET está definido.

Sem GCS_BUCKET (testes, execução local), tudo fica só no disco.
"""
from __future__ import annotations

import os
from pathlib import Path

ARQUIVOS = ["data/estado.json", "data/intimacoes.json", "PAINEL.md"]


def _bucket():
    nome = os.environ.get("GCS_BUCKET")
    if not nome:
        return None
    from google.cloud import storage  # importado só na nuvem
    return storage.Client().bucket(nome)


def baixar(raiz: Path) -> None:
    b = _bucket()
    if b is None:
        return
    for rel in ARQUIVOS:
        blob = b.blob(rel)
        if blob.exists():
            destino = raiz / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(destino))
            print(f"Baixado gs://{b.name}/{rel}")


def enviar(raiz: Path) -> None:
    b = _bucket()
    if b is None:
        return
    for rel in ARQUIVOS:
        origem = raiz / rel
        if origem.exists():
            b.blob(rel).upload_from_filename(str(origem))
            print(f"Salvo gs://{b.name}/{rel}")
