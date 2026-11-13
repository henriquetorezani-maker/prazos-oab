"""Visão unificada das intimações: aplica prazos definidos à mão, prazos avulsos,
anotações e cumpridos. Usada pelo aplicativo e pela rotina (resumo, lembretes, painel)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import armazenamento
from .calendario import prazo_corridos, prazo_por_disponibilizacao

TZ = ZoneInfo("America/Sao_Paulo")
STATUS = "data/status.json"      # {chave: {"cumprido": iso}}
AJUSTES = "data/ajustes.json"    # {chave: {"dias": n, "tipo": "uteis"|"corridos"} | {"fatal": iso}}
NOTAS = "data/notas.json"        # {chave: texto}
MANUAIS = "data/manuais.json"    # [ {chave, processo, partes, orgao, descricao, fatal, criado} ]


def comarca_de(orgao: str) -> str:
    return orgao.split(" - ")[0].strip() if orgao else ""


def calcular(dias: int, tipo: str, disponibilizacao: str, orgao: str) -> dict:
    disp = date.fromisoformat(disponibilizacao)
    f = prazo_corridos if tipo == "corridos" else prazo_por_disponibilizacao
    r = f(disp, int(dias), comarca_de(orgao))
    return {
        "fatal": r.fatal.isoformat(),
        "fatal_alternativo": r.fatal_alternativo.isoformat() if r.fatal_alternativo else None,
        "contagem": r.explicacao(),
    }


def _aplicar_ajuste(r: dict, aj: dict) -> dict:
    r = dict(r)
    if aj.get("fatal"):
        r.update(fatal=aj["fatal"], fatal_alternativo=None,
                 contagem=f"fatal definido manualmente: {date.fromisoformat(aj['fatal']):%d/%m/%Y}",
                 prazo_obs="prazo definido manualmente")
    elif aj.get("dias") and r.get("disponibilizacao"):
        tipo = aj.get("tipo", "uteis")
        r.update(calcular(aj["dias"], tipo, r["disponibilizacao"], r.get("orgao", "")))
        r["prazo_dias"] = int(aj["dias"])
        r["prazo_obs"] = f"prazo definido manualmente: {aj['dias']} dias {'corridos' if tipo == 'corridos' else 'úteis'}"
    r["ajustado"] = True
    return r


def _chave_dup(r: dict) -> tuple:
    return (r.get("processo"), r.get("disponibilizacao"), (r.get("texto") or "")[:300])


def unificar(regs: list[dict], hoje: date | None = None) -> list[dict]:
    """Lista única (sem repetidas), com ajustes, notas, cumpridos e prazos avulsos."""
    hoje = hoje or datetime.now(TZ).date()
    status = armazenamento.ler_json(STATUS, {})
    ajustes = armazenamento.ler_json(AJUSTES, {})
    notas = armazenamento.ler_json(NOTAS, {})
    manuais = armazenamento.ler_json(MANUAIS, [])

    vistos, out = set(), []
    for r in sorted(regs, key=lambda r: r.get("disponibilizacao", ""), reverse=True):
        k = _chave_dup(r)
        if k in vistos:
            continue
        vistos.add(k)
        r = dict(r)
        if r["chave"] in ajustes:
            r = _aplicar_ajuste(r, ajustes[r["chave"]])
        out.append(r)
    for m in manuais:
        out.append({**m, "manual": True, "tipo": "Prazo avulso", "tribunal": "",
                    "texto": m.get("descricao", ""), "link": "", "prazo_obs": "prazo avulso cadastrado à mão",
                    "contagem": f"fatal informado manualmente: {date.fromisoformat(m['fatal']):%d/%m/%Y}",
                    "disponibilizacao": m.get("criado", "")[:10], "fatal_alternativo": None})
    for r in out:
        r["cumprido"] = status.get(r["chave"], {}).get("cumprido")
        r["nota"] = notas.get(r["chave"], "")
        if r.get("fatal"):
            r["dias_restantes"] = (date.fromisoformat(r["fatal"]) - hoje).days
    return out


def abertos(lista: list[dict]) -> list[dict]:
    return sorted((r for r in lista if r.get("fatal") and r.get("dias_restantes", -1) >= 0 and not r["cumprido"]),
                  key=lambda r: r["fatal"])


def conferir(lista: list[dict], hoje: date, dias: int = 45) -> list[dict]:
    limite = (hoje - timedelta(days=dias)).isoformat()
    return [r for r in lista if not r.get("fatal") and not r["cumprido"]
            and r.get("disponibilizacao", "") >= limite]


# --- Escritas feitas pelo aplicativo ------------------------------------------------

def definir_ajuste(chave: str, dias: int | None = None, tipo: str = "uteis", fatal: str | None = None) -> None:
    aj = armazenamento.ler_json(AJUSTES, {})
    if dias is None and not fatal:
        aj.pop(chave, None)
    elif fatal:
        date.fromisoformat(fatal)  # valida
        aj[chave] = {"fatal": fatal}
    else:
        if not (1 <= int(dias) <= 365) or tipo not in ("uteis", "corridos"):
            raise ValueError("prazo inválido")
        aj[chave] = {"dias": int(dias), "tipo": tipo}
    armazenamento.gravar_json(AJUSTES, aj)


def definir_nota(chave: str, texto: str) -> None:
    notas = armazenamento.ler_json(NOTAS, {})
    texto = (texto or "").strip()[:2000]
    if texto:
        notas[chave] = texto
    else:
        notas.pop(chave, None)
    armazenamento.gravar_json(NOTAS, notas)


def criar_manual(processo: str, descricao: str, fatal: str, partes: str = "", orgao: str = "") -> str:
    date.fromisoformat(fatal)
    lista = armazenamento.ler_json(MANUAIS, [])
    chave = "manual-" + uuid.uuid4().hex[:10]
    lista.append({"chave": chave, "processo": processo.strip()[:40], "descricao": descricao.strip()[:1000],
                  "fatal": fatal, "partes": partes.strip()[:120], "orgao": orgao.strip()[:160],
                  "criado": datetime.now(TZ).isoformat(timespec="minutes")})
    armazenamento.gravar_json(MANUAIS, lista)
    return chave


def excluir_manual(chave: str) -> None:
    lista = [m for m in armazenamento.ler_json(MANUAIS, []) if m.get("chave") != chave]
    armazenamento.gravar_json(MANUAIS, lista)
