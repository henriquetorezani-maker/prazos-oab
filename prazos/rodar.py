"""Rotina principal: consulta o Comunica, detecta intimações novas, calcula prazos,
avisa no Telegram e atualiza data/ e PAINEL.md.

Uso: python -m prazos.rodar
Variáveis: OAB_NUMERO, OAB_UF, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (opcionais as duas últimas)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import armazenamento, comunica, notifica
from .calendario import prazo_por_disponibilizacao

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "data"
ESTADO = DADOS / "estado.json"
INTIMACOES = DADOS / "intimacoes.json"
PAINEL = RAIZ / "PAINEL.md"
TZ = ZoneInfo("America/Sao_Paulo")
HORA_RESUMO = 8
JANELA_DIAS = 20
JANELA_PRIMEIRA = 90


def _ler(p: Path, padrao):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao


def _gravar(p: Path, dados) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def comarca_de(orgao: str) -> str:
    return orgao.split(" - ")[0].strip() if orgao else ""


def enriquecer(c: comunica.Comunicacao) -> dict:
    reg = c.dict()
    dias, obs = comunica.identificar_prazo(c.texto)
    reg["prazo_dias"] = dias
    reg["prazo_obs"] = obs
    reg["fatal"] = None
    reg["fatal_alternativo"] = None
    reg["contagem"] = ""
    if dias and c.disponibilizacao:
        r = prazo_por_disponibilizacao(date.fromisoformat(c.disponibilizacao), dias, comarca_de(c.orgao))
        reg["fatal"] = r.fatal.isoformat()
        reg["fatal_alternativo"] = r.fatal_alternativo.isoformat() if r.fatal_alternativo else None
        reg["contagem"] = r.explicacao()
    return reg


def _br(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "—"


def mensagem_nova(r: dict) -> str:
    linhas = [
        "🔔 Nova intimação",
        f"Processo: {r['processo']}",
        f"{r['tribunal']} — {r['orgao']}",
        f"Disponibilizada: {_br(r['disponibilizacao'])} | Tipo: {r['tipo']}",
    ]
    if r["fatal"]:
        linhas.append(f"⏰ Prazo fatal (conservador): {_br(r['fatal'])}")
        if r["fatal_alternativo"]:
            linhas.append(f"   (se dias duvidosos não forem úteis: {_br(r['fatal_alternativo'])})")
        linhas.append(f"Contagem: {r['contagem']}")
    linhas.append(f"Obs.: {r['prazo_obs']}")
    linhas.append("")
    linhas.append("Trecho: " + r["texto"][:700] + ("…" if len(r["texto"]) > 700 else ""))
    linhas.append("Confira sempre no PJe antes de agir.")
    return "\n".join(linhas)


def abertos(registros: list[dict], hoje: date) -> list[dict]:
    return sorted(
        (r for r in registros if r.get("fatal") and date.fromisoformat(r["fatal"]) >= hoje),
        key=lambda r: r["fatal"],
    )


def sem_prazo_recentes(registros: list[dict], hoje: date, dias: int = 30) -> list[dict]:
    limite = (hoje - timedelta(days=dias)).isoformat()
    return sorted(
        (r for r in registros if not r.get("fatal") and r.get("disponibilizacao", "") >= limite),
        key=lambda r: r["disponibilizacao"], reverse=True,
    )


def mensagem_resumo(registros: list[dict], hoje: date) -> str:
    ab = abertos(registros, hoje)
    linhas = [f"📋 Resumo diário {hoje:%d/%m/%Y} — rotina funcionando."]
    if ab:
        linhas.append("Prazos em aberto:")
        for r in ab:
            dias_rest = (date.fromisoformat(r["fatal"]) - hoje).days
            alerta = " ⚠️" if dias_rest <= 4 else ""
            linhas.append(f"• {_br(r['fatal'])}{alerta} — {r['processo']} ({r['orgao'].split(' - ')[-1]})")
    else:
        linhas.append("Nenhum prazo calculado em aberto.")
    sp = sem_prazo_recentes(registros, hoje)
    if sp:
        linhas.append(f"Intimações recentes sem prazo identificado (conferir): {len(sp)}")
        for r in sp[:10]:
            linhas.append(f"• {_br(r['disponibilizacao'])} — {r['processo']}: {r['prazo_obs']}")
    linhas.append("Lembrete: expedientes só do PJe (fora do DJEN) não aparecem aqui.")
    return "\n".join(linhas)


def painel(registros: list[dict], agora: datetime) -> str:
    hoje = agora.date()
    out = [
        "# Painel de intimações — OAB/ES 43.793",
        "",
        f"Atualizado em {agora:%d/%m/%Y %H:%M} (Brasília). Fonte: DJEN/Comunica (CNJ).",
        "",
        "> Prazos calculados em modo conservador. Conferir sempre no PJe. "
        "Expedientes intimados só pelo sistema PJe não aparecem aqui.",
        "",
        "## Prazos em aberto",
        "",
        "| Fatal | Processo | Órgão | Disponibilização | Contagem |",
        "|---|---|---|---|---|",
    ]
    for r in abertos(registros, hoje):
        alt = f" (alt. {_br(r['fatal_alternativo'])})" if r.get("fatal_alternativo") else ""
        out.append(f"| **{_br(r['fatal'])}**{alt} | {r['processo']} | {r['orgao']} | "
                   f"{_br(r['disponibilizacao'])} | {r['contagem']} — {r['prazo_obs']} |")
    out += ["", "## Sem prazo identificado (últimos 30 dias)", "",
            "| Disponibilização | Processo | Órgão | Observação |", "|---|---|---|---|"]
    for r in sem_prazo_recentes(registros, hoje):
        out.append(f"| {_br(r['disponibilizacao'])} | {r['processo']} | {r['orgao']} | {r['prazo_obs']} |")
    return "\n".join(out) + "\n"


def main() -> int:
    armazenamento.baixar(RAIZ)
    try:
        return executar()
    finally:
        armazenamento.enviar(RAIZ)


def executar() -> int:
    oab = os.environ.get("OAB_NUMERO", "43793")
    uf = os.environ.get("OAB_UF", "ES")
    agora = datetime.now(TZ)
    hoje = agora.date()

    estado = _ler(ESTADO, {"vistas": {}, "ultimo_resumo": None, "falhas_seguidas": 0})
    registros: dict = {r["chave"]: r for r in _ler(INTIMACOES, [])}
    primeira = not estado["vistas"]
    inicio = hoje - timedelta(days=JANELA_PRIMEIRA if primeira else JANELA_DIAS)

    try:
        brutos = comunica.buscar(oab, uf, inicio, hoje)
    except comunica.FalhaConsulta as e:
        estado["falhas_seguidas"] = estado.get("falhas_seguidas", 0) + 1
        n = estado["falhas_seguidas"]
        if n == 1 or n % 6 == 0:
            notifica.enviar(f"⚠️ Falha na consulta ao Comunica ({n}ª seguida): {e}\n"
                            "Enquanto isso, confira as intimações manualmente.")
        _gravar(ESTADO, estado)
        print(f"FALHA: {e}")
        return 1

    if estado.get("falhas_seguidas"):
        notifica.enviar("✅ Consulta ao Comunica voltou a funcionar.")
    estado["falhas_seguidas"] = 0

    novas = []
    for item in brutos:
        if item.get("ativo") is False or item.get("data_cancelamento"):
            print(f"Ignorada (cancelada no DJEN): {item.get('numero_processo')} {item.get('motivo_cancelamento')}")
            continue
        c = comunica.normalizar(item)
        reg = enriquecer(c)
        registros[c.chave] = reg
        if c.chave not in estado["vistas"]:
            estado["vistas"][c.chave] = agora.isoformat(timespec="minutes")
            novas.append(reg)

    lista = sorted(registros.values(), key=lambda r: r.get("disponibilizacao", ""))
    print(f"{len(brutos)} comunicações no período; {len(novas)} novas.")

    if primeira:
        notifica.enviar(f"🚀 Rotina iniciada. Carga inicial: {len(novas)} comunicações "
                        f"desde {inicio:%d/%m/%Y}.\n\n" + mensagem_resumo(lista, hoje))
        estado["ultimo_resumo"] = hoje.isoformat()
    else:
        for r in sorted(novas, key=lambda r: r.get("disponibilizacao", "")):
            notifica.enviar(mensagem_nova(r))
        if agora.hour >= HORA_RESUMO and estado.get("ultimo_resumo") != hoje.isoformat():
            notifica.enviar(mensagem_resumo(lista, hoje))
            estado["ultimo_resumo"] = hoje.isoformat()

    _gravar(INTIMACOES, lista)
    _gravar(ESTADO, estado)
    PAINEL.write_text(painel(lista, agora), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
