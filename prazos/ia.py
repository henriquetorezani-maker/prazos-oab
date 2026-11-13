"""Leitura do ato por IA (API da Anthropic): resumo, providência e prazo sugerido.

Ativa só se ANTHROPIC_API_KEY estiver definida. O resultado é SUGESTÃO: nunca altera o
prazo sozinho; o advogado aplica com um toque no app. Falhas não interrompem a rotina.
"""
from __future__ import annotations

import json
import os
import re

import requests

API = "https://api.anthropic.com/v1/messages"
MODELO = os.environ.get("IA_MODELO", "claude-haiku-5-5")

INSTRUCOES = """Você auxilia um advogado brasileiro (OAB/ES 43.793, Henrique Coutinho Torezani) a controlar prazos.
Recebe o teor de uma comunicação processual publicada no DJEN. Responda SOMENTE com um objeto JSON, sem texto fora dele:
{
  "resumo": "1 frase: o que o ato decide ou determina",
  "providencia": "1 frase: o que o advogado intimado precisa fazer agora (ou 'nenhuma providência imediata')",
  "prazo_dias": número inteiro de dias do prazo que corre AGORA para a parte representada pelo advogado intimado, ou null,
  "tipo": "uteis" ou "corridos" (prazos em horas contam como corridos, convertidos em dias arredondando para cima), ou null,
  "destinatario": "parte autora" | "parte ré" | "ambas as partes" | "indefinido",
  "confianca": "alta" | "media" | "baixa"
}
Regras: ignore prazos de citação da parte contrária e prazos que só correrão depois ("findo o prazo, intime-se...").
Se o ato disser "prazo legal", indique o prazo legal usual do CPC/Lei 9.099/Lei 12.153 para aquele ato e use confiança "media".
Se não houver prazo para o advogado agora, use null. Nunca invente datas."""


def ativa() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _extrair_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError("resposta sem JSON")
    d = json.loads(m.group(0))
    dias = d.get("prazo_dias")
    d["prazo_dias"] = int(dias) if isinstance(dias, (int, float)) and 0 < int(dias) <= 365 else None
    if d.get("tipo") not in ("uteis", "corridos"):
        d["tipo"] = None if d["prazo_dias"] is None else "uteis"
    for k in ("resumo", "providencia", "destinatario", "confianca"):
        d[k] = str(d.get(k) or "")[:400]
    return {k: d[k] for k in ("resumo", "providencia", "prazo_dias", "tipo", "destinatario", "confianca")}


def resumir(texto: str, partes: str = "", orgao: str = "") -> dict:
    corpo = {
        "model": MODELO,
        "max_tokens": 500,
        "system": INSTRUCOES,
        "messages": [{"role": "user", "content":
                      f"Órgão: {orgao}\nPartes: {partes}\n\nTeor:\n{(texto or '')[:12000]}"}],
    }
    r = requests.post(API, json=corpo, timeout=60, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    texto_resp = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    return _extrair_json(texto_resp)
