"""Visão unificada das intimações: aplica prazos definidos à mão, prazos avulsos,
anotações e cumpridos. Usada pelo aplicativo e pela rotina (resumo, lembretes, painel)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import armazenamento
from .calendario import dias_uteis_ate, prazo_corridos, prazo_por_disponibilizacao

TZ = ZoneInfo("America/Sao_Paulo")
STATUS = "data/status.json"      # {chave: {"cumprido": iso}}
AJUSTES = "data/ajustes.json"    # {chave: {"dias": n, "tipo": "uteis"|"corridos"} | {"fatal": iso}}
NOTAS = "data/notas.json"        # {chave: texto}
MANUAIS = "data/manuais.json"    # [ {chave, processo, partes, orgao, descricao, fatal, criado} ]
IA = "data/ia.json"              # {chave: {resumo, providencia, prazo_dias, tipo, destinatario, confianca}}
AUDIENCIAS = "data/audiencias.json"  # {chave: {"data": iso, "hora": "HH:MM"} | {"sem": true}}


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
    ia = armazenamento.ler_json(IA, {})
    auds = armazenamento.ler_json(AUDIENCIAS, {})

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
        if m.get("audiencia"):
            out.append({**m, "manual": True, "tipo": "Audiência avulsa", "tribunal": "", "fatal": None,
                        "texto": m.get("descricao", ""), "link": "", "prazo_obs": "audiência cadastrada à mão",
                        "contagem": "", "disponibilizacao": m.get("criado", "")[:10], "fatal_alternativo": None})
            continue
        out.append({**m, "manual": True, "tipo": "Prazo avulso", "tribunal": "",
                    "texto": m.get("descricao", ""), "link": "", "prazo_obs": "prazo avulso cadastrado à mão",
                    "contagem": f"fatal informado manualmente: {date.fromisoformat(m['fatal']):%d/%m/%Y}",
                    "disponibilizacao": m.get("criado", "")[:10], "fatal_alternativo": None})
    for r in out:
        if r["chave"] in auds:
            a = auds[r["chave"]]
            r["audiencia"] = None if a.get("sem") else {"data": a["data"], "hora": a.get("hora", "")}
            r["audiencia_ajustada"] = True
        if r.get("audiencia"):
            r["dias_ate_audiencia"] = (date.fromisoformat(r["audiencia"]["data"]) - hoje).days
        r["cumprido"] = status.get(r["chave"], {}).get("cumprido")
        r["nota"] = notas.get(r["chave"], "")
        r["ia"] = ia.get(r["chave"])
        if r.get("fatal"):
            fatal = date.fromisoformat(r["fatal"])
            r["dias_restantes"] = (fatal - hoje).days
            r["uteis_restantes"] = dias_uteis_ate(hoje, fatal, comarca_de(r.get("orgao", "")))
    return out


def abertos(lista: list[dict]) -> list[dict]:
    return sorted((r for r in lista if r.get("fatal") and r.get("dias_restantes", -1) >= 0 and not r["cumprido"]),
                  key=lambda r: r["fatal"])


def conferir(lista: list[dict], hoje: date, dias: int = 45) -> list[dict]:
    limite = (hoje - timedelta(days=dias)).isoformat()
    return [r for r in lista if not r.get("fatal") and not r["cumprido"] and not r.get("audiencia")
            and r.get("disponibilizacao", "") >= limite]


def audiencias(lista: list[dict]) -> list[dict]:
    """Audiências de hoje em diante, em ordem de data. Se o mesmo processo tiver várias intimações
    com audiência (ex.: redesignação), vale a da intimação mais recente; as avulsas entram todas.
    Marcar a intimação como cumprida não tira a audiência da lista: ela ainda vai acontecer."""
    vistos, out = set(), []
    for r in sorted(lista, key=lambda r: r.get("disponibilizacao", ""), reverse=True):
        a = r.get("audiencia")
        if not a or r.get("dias_ate_audiencia", -1) < 0:
            continue
        if not r.get("manual"):
            if r.get("processo") in vistos:
                continue
            vistos.add(r.get("processo"))
        out.append(r)
    return sorted(out, key=lambda r: (r["audiencia"]["data"], r["audiencia"].get("hora") or "99"))


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


def _hora_valida(hora: str) -> str:
    hora = (hora or "").strip()
    if hora:
        h, m = hora.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("hora inválida")
        hora = f"{int(h):02d}:{int(m):02d}"
    return hora


def criar_manual(processo: str, descricao: str, fatal: str | None, partes: str = "", orgao: str = "",
                 audiencia: str | None = None, hora: str = "") -> str:
    """Prazo avulso (com fatal) ou audiência avulsa (com data da audiência e hora opcional)."""
    item = {"processo": processo.strip()[:40], "descricao": descricao.strip()[:1000],
            "partes": partes.strip()[:120], "orgao": orgao.strip()[:160],
            "criado": datetime.now(TZ).isoformat(timespec="minutes")}
    if audiencia:
        date.fromisoformat(audiencia)
        item["audiencia"] = {"data": audiencia, "hora": _hora_valida(hora)}
    else:
        date.fromisoformat(fatal or "")
        item["fatal"] = fatal
    lista = armazenamento.ler_json(MANUAIS, [])
    chave = "manual-" + uuid.uuid4().hex[:10]
    lista.append({"chave": chave, **item})
    armazenamento.gravar_json(MANUAIS, lista)
    return chave


def excluir_manual(chave: str) -> None:
    lista = [m for m in armazenamento.ler_json(MANUAIS, []) if m.get("chave") != chave]
    armazenamento.gravar_json(MANUAIS, lista)


def definir_audiencia(chave: str, data: str | None = None, hora: str = "", sem: bool = False,
                      limpar: bool = False) -> None:
    """Corrige a audiência de uma intimação: define data/hora, marca que não há audiência
    (cancelada ou leitura errada) ou volta à leitura automática do texto."""
    auds = armazenamento.ler_json(AUDIENCIAS, {})
    if limpar:
        auds.pop(chave, None)
    elif sem:
        auds[chave] = {"sem": True}
    else:
        date.fromisoformat(data or "")
        auds[chave] = {"data": data, "hora": _hora_valida(hora)}
    armazenamento.gravar_json(AUDIENCIAS, auds)
