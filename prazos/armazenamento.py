"""Armazenamento: Cloud Storage quando GCS_BUCKET está definido; disco local nos testes.

- baixar/enviar: sincronizam os arquivos da rotina (estado, intimações, painel).
- ler_json/gravar_json: leitura e escrita direta, usadas pela API e pelas notificações.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS = ["data/estado.json", "data/intimacoes.json", "PAINEL.md"]


def _bucket():
    nome = os.environ.get("GCS_BUCKET")
    if not nome:
        return None
    from google.cloud import storage  # importado só na nuvem
    return storage.Client().bucket(nome)


def baixar(raiz: Path = RAIZ) -> None:
    b = _bucket()
    if b is None:
        return
    for rel in ARQUIVOS:
        blob = b.blob(rel)
        if blob.exists():
            destino = raiz / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(destino))


def enviar(raiz: Path = RAIZ) -> None:
    b = _bucket()
    if b is None:
        return
    for rel in ARQUIVOS:
        origem = raiz / rel
        if origem.exists():
            b.blob(rel).upload_from_filename(str(origem))
            print(f"Salvo gs://{b.name}/{rel}")


def ler_json(rel: str, padrao):
    b = _bucket()
    if b is None:
        p = RAIZ / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao
    blob = b.blob(rel)
    if not blob.exists():
        return padrao
    return json.loads(blob.download_as_text(encoding="utf-8"))


def gravar_json(rel: str, dados) -> None:
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    b = _bucket()
    if b is None:
        p = RAIZ / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
        return
    b.blob(rel).upload_from_string(texto, content_type="application/json")
