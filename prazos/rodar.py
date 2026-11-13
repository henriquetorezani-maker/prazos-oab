"""Rotina principal: consulta o Comunica (por OAB e, como reserva, pelo nome), detecta
intimações novas, calcula prazos, envia alertas e lembretes e atualiza data/ e PAINEL.md.

Uso: python -m prazos.rodar
Variáveis: OAB_NUMERO, OAB_UF, NOME_ADVOGADO, APP_URL, GCS_BUCKET,
           TELEGRAM_TOKEN e TELEGRAM_CHAT_ID (opcionais)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import armazenamento, comunica, dados, notifica

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "data"
ESTADO = DADOS / "estado.json"
INTIMACOES = DADOS / "intimacoes.json"
PAINEL = RAIZ / "PAINEL.md"
TZ = ZoneInfo("America/Sao_Paulo")
HORA_RESUMO = 8
HORA_LIMITE_LEMBRETE = 21
MARCOS_LEMBRETE = (5, 2, 0)  # dias antes do fatal
JANELA_DIAS = 20
JANELA_PRIMEIRA = 90

comarca_de = dados.comarca_de


def _ler(p: Path, padrao):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao


def _gravar(p: Path, conteudo) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def calcular_registro(reg: dict) -> dict:
    """(Re)calcula prazo, observação e partes a partir do texto. Aplicado a todos os registros
    a cada execução, para que melhorias nas regras valham também para o histórico."""
    reg = dict(reg)
    dias, obs, tipo = comunica.identificar_prazo(reg.get("texto", ""))
    reg.update(prazo_dias=dias, prazo_obs=obs, prazo_tipo=tipo, fatal=None, fatal_alternativo=None, contagem="")
    if not reg.get("partes"):
        reg["partes"] = comunica.extrair_partes(reg.get("texto", ""))
    if dias and reg.get("disponibilizacao"):
        reg.update(dados.calcular(dias, tipo, reg["disponibilizacao"], reg.get("orgao", "")))
    return reg


def enriquecer(c: comunica.Comunicacao) -> dict:
    return calcular_registro(c.dict())


def _br(iso: str | None) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y") if iso else "—"


def _vara(r: dict) -> str:
    return (r.get("orgao") or "").split(" - ")[-1]


def mensagem_nova(r: dict) -> str:
    linhas = [
        "🔔 Nova intimação",
        f"Processo: {r['processo']}" + (f" — {r['partes']}" if r.get("partes") else ""),
        f"{r.get('tribunal', '')} — {r.get('orgao', '')}",
        f"Disponibilizada: {_br(r.get('disponibilizacao'))} | Tipo: {r.get('tipo', '')}",
    ]
    if r.get("fatal"):
        linhas.append(f"⏰ Prazo fatal (conservador): {_br(r['fatal'])}")
        if r.get("fatal_alternativo"):
            linhas.append(f"   (se dias duvidosos não forem úteis: {_br(r['fatal_alternativo'])})")
        linhas.append(f"Contagem: {r['contagem']}")
    linhas.append(f"Obs.: {r['prazo_obs']}")
    linhas.append("")
    texto = r.get("texto", "")
    linhas.append("Trecho: " + texto[:700] + ("…" if len(texto) > 700 else ""))
    linhas.append("Confira sempre no PJe antes de agir.")
    return "\n".join(linhas)


def mensagem_resumo(unif: list[dict], hoje: date) -> str:
    ab = dados.abertos(unif)
    linhas = [f"📋 Resumo diário {hoje:%d/%m/%Y} — rotina funcionando."]
    if ab:
        linhas.append("Prazos em aberto:")
        for r in ab:
            alerta = " ⚠️" if r["dias_restantes"] <= 4 else ""
            quem = f" — {r['partes']}" if r.get("partes") else ""
            linhas.append(f"• {_br(r['fatal'])}{alerta} — {r['processo']}{quem} ({_vara(r)})")
    else:
        linhas.append("Nenhum prazo calculado em aberto.")
    cf = dados.conferir(unif, hoje, 30)
    if cf:
        linhas.append(f"Intimações recentes sem prazo definido (conferir): {len(cf)}")
        for r in cf[:10]:
            linhas.append(f"• {_br(r.get('disponibilizacao'))} — {r['processo']}: {r['prazo_obs']}")
    linhas.append("Lembrete: expedientes só do PJe (fora do DJEN) não aparecem aqui — cadastre-os como prazo avulso.")
    return "\n".join(linhas)


def resumo_curto(unif: list[dict]) -> str:
    ab = dados.abertos(unif)
    if not ab:
        return "Rotina funcionando. Nenhum prazo em aberto."
    p = ab[0]
    quem = p.get("partes") or p["processo"]
    return f"{len(ab)} prazo(s) em aberto. Próximo: {_br(p['fatal'])} — {quem}. Rotina funcionando."


def lembretes_devidos(unif: list[dict], enviados: dict) -> list[tuple[dict, int]]:
    """Para cada prazo em aberto, o marco (5, 2 ou 0 dias) ainda não avisado e já alcançado.
    Se a rotina ficou parada e pulou um marco, avisa só o mais próximo do vencimento."""
    out = []
    for r in dados.abertos(unif):
        ja = set(enviados.get(r["chave"], []))
        alcancados = [m for m in MARCOS_LEMBRETE if r["dias_restantes"] <= m and m not in ja]
        if alcancados:
            out.append((r, min(alcancados)))
    return out


def texto_lembrete(r: dict, marco: int) -> tuple[str, str]:
    quando = "VENCE HOJE" if r["dias_restantes"] == 0 else f"vence em {r['dias_restantes']} dia(s) — {_br(r['fatal'])}"
    titulo = f"⏰ Prazo {quando}"
    corpo = f"{r.get('partes') or r['processo']} ({_vara(r)})."
    if r.get("nota"):
        corpo += f" Nota: {r['nota'][:120]}"
    return titulo, corpo


def painel(unif: list[dict], agora: datetime) -> str:
    hoje = agora.date()
    out = [
        "# Painel de intimações — OAB/ES 43.793",
        "",
        f"Atualizado em {agora:%d/%m/%Y %H:%M} (Brasília). Fonte: DJEN/Comunica (CNJ).",
        "",
        "> Prazos calculados em modo conservador. Conferir sempre no PJe.",
        "",
        "## Prazos em aberto",
        "",
        "| Fatal | Processo | Partes | Órgão | Contagem |",
        "|---|---|---|---|---|",
    ]
    for r in dados.abertos(unif):
        alt = f" (alt. {_br(r['fatal_alternativo'])})" if r.get("fatal_alternativo") else ""
        out.append(f"| **{_br(r['fatal'])}**{alt} | {r['processo']} | {r.get('partes', '')} | "
                   f"{r.get('orgao', '')} | {r.get('contagem', '')} — {r.get('prazo_obs', '')} |")
    out += ["", "## Sem prazo definido (últimos 30 dias)", "",
            "| Disponibilização | Processo | Partes | Observação |", "|---|---|---|---|"]
    for r in dados.conferir(unif, hoje, 30):
        out.append(f"| {_br(r.get('disponibilizacao'))} | {r['processo']} | {r.get('partes', '')} | {r['prazo_obs']} |")
    return "\n".join(out) + "\n"


def main() -> int:
    armazenamento.baixar(RAIZ)
    try:
        return executar()
    finally:
        armazenamento.enviar(RAIZ)


def _buscar_tudo(oab: str, uf: str, nome: str, inicio: date, fim: date, estado: dict) -> list[dict]:
    """Busca principal pela OAB (falha = erro). Busca reserva pelo nome (falha só é registrada)."""
    itens = comunica.buscar(oab, uf, inicio, fim)
    if nome:
        try:
            extras = comunica.buscar_nome(nome, inicio, fim)
            estado["busca_nome_falhas"] = 0
            chaves = {comunica.normalizar(i).chave for i in itens}
            novos = [i for i in extras if comunica.normalizar(i).chave not in chaves]
            if novos:
                print(f"Busca pelo nome encontrou {len(novos)} comunicação(ões) fora da busca pela OAB.")
            itens += novos
        except comunica.FalhaConsulta as e:
            estado["busca_nome_falhas"] = estado.get("busca_nome_falhas", 0) + 1
            print(f"Aviso: busca reserva pelo nome falhou: {e}")
    return itens


def executar() -> int:
    oab = os.environ.get("OAB_NUMERO", "43793")
    uf = os.environ.get("OAB_UF", "ES")
    nome = os.environ.get("NOME_ADVOGADO", "HENRIQUE COUTINHO TOREZANI")
    agora = datetime.now(TZ)
    hoje = agora.date()

    estado = _ler(ESTADO, {"vistas": {}, "ultimo_resumo": None, "falhas_seguidas": 0})
    estado.setdefault("lembretes", {})
    registros: dict = {r["chave"]: r for r in _ler(INTIMACOES, [])}
    primeira = not estado["vistas"]
    inicio = hoje - timedelta(days=JANELA_PRIMEIRA if primeira else JANELA_DIAS)

    try:
        brutos = _buscar_tudo(oab, uf, nome, inicio, hoje, estado)
    except comunica.FalhaConsulta as e:
        estado["falhas_seguidas"] = estado.get("falhas_seguidas", 0) + 1
        n = estado["falhas_seguidas"]
        if n == 1 or n % 6 == 0:
            notifica.enviar(f"⚠️ Falha na consulta ao Comunica ({n}ª seguida): {e}\n"
                            "Enquanto isso, confira as intimações manualmente.",
                            titulo="⚠️ Falha na consulta",
                            corpo="O Comunica não respondeu. Confira as intimações manualmente.",
                            tag="falha")
        _gravar(ESTADO, estado)
        print(f"FALHA: {e}")
        return 1

    if estado.get("falhas_seguidas"):
        notifica.enviar("✅ Consulta ao Comunica voltou a funcionar.",
                        titulo="✅ Consulta normalizada", tag="falha")
    estado["falhas_seguidas"] = 0
    estado["ultima_consulta"] = agora.isoformat(timespec="minutes")

    novas = []
    for item in brutos:
        if item.get("ativo") is False or item.get("data_cancelamento"):
            print(f"Ignorada (cancelada no DJEN): {item.get('numero_processo')} {item.get('motivo_cancelamento')}")
            continue
        c = comunica.normalizar(item)
        registros[c.chave] = c.dict()
        if c.chave not in estado["vistas"]:
            estado["vistas"][c.chave] = agora.isoformat(timespec="minutes")
            novas.append(c.chave)

    # recalcula tudo (novas regras valem para o histórico)
    registros = {k: calcular_registro(r) for k, r in registros.items()}
    lista = sorted(registros.values(), key=lambda r: r.get("disponibilizacao", ""))
    _gravar(INTIMACOES, lista)
    unif = dados.unificar(lista, hoje)
    por_chave = {r["chave"]: r for r in unif}
    print(f"{len(brutos)} comunicações no período; {len(novas)} novas.")

    if primeira:
        notifica.enviar(f"🚀 Rotina iniciada. Carga inicial: {len(novas)} comunicações "
                        f"desde {inicio:%d/%m/%Y}.\n\n" + mensagem_resumo(unif, hoje),
                        titulo="🚀 Rotina iniciada",
                        corpo=f"{len(novas)} comunicações carregadas. Abra o app para ver os prazos.",
                        tag="resumo")
        estado["ultimo_resumo"] = hoje.isoformat()
    else:
        for chave in novas:
            r = por_chave.get(chave) or registros[chave]
            fatal = f" — fatal {_br(r['fatal'])}" if r.get("fatal") else ""
            notifica.enviar(mensagem_nova(r), titulo=f"🔔 Nova intimação{fatal}",
                            corpo=f"{r.get('partes') or r['processo']} — {_vara(r)}. {r['prazo_obs']}",
                            tag=chave)
        if agora.hour >= HORA_RESUMO and estado.get("ultimo_resumo") != hoje.isoformat():
            notifica.enviar(mensagem_resumo(unif, hoje), titulo="📋 Resumo do dia",
                            corpo=resumo_curto(unif), tag="resumo")
            estado["ultimo_resumo"] = hoje.isoformat()

    if HORA_RESUMO <= agora.hour < HORA_LIMITE_LEMBRETE:
        for r, marco in lembretes_devidos(unif, estado["lembretes"]):
            titulo, corpo = texto_lembrete(r, marco)
            notifica.enviar(f"{titulo}\n{corpo}", titulo=titulo, corpo=corpo, tag=f"lembrete-{r['chave']}")
            # marca também os marcos anteriores, para não avisar "5 dias" depois de já ter avisado "2 dias"
            ja = set(estado["lembretes"].get(r["chave"], []))
            estado["lembretes"][r["chave"]] = sorted(ja | {m for m in MARCOS_LEMBRETE if m >= marco})

    _gravar(ESTADO, estado)
    PAINEL.write_text(painel(unif, agora), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
