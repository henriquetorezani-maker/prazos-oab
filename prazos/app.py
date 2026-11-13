"""API e aplicativo web instalável (PWA). Roda como serviço no Cloud Run.

Rotas públicas: arquivos do aplicativo, /api/saude, /api/entrar.
Demais rotas /api/* exigem sessão (cookie assinado), obtida com o código de acesso.
"""
from __future__ import annotations

import hmac
import os
import time
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory, session

from . import armazenamento, config, dados as dados_mod, ia as ia_mod, push

TZ = ZoneInfo("America/Sao_Paulo")
PASTA_APP = Path(__file__).resolve().parent.parent / "app"

app = Flask(__name__, static_folder=None)
# O Cloud Run termina o HTTPS na frente do contêiner; isto faz o Flask enxergar https e o host real.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
_cfg = config.carregar()
app.secret_key = _cfg["segredo_sessao"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("DEV") != "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
)


def exige_login(f):
    @wraps(f)
    def inner(*a, **k):
        if not session.get("ok") or session.get("v", 0) != config.carregar().get("versao_sessao", 0):
            session.clear()
            return jsonify(erro="não autenticado"), 401
        return f(*a, **k)
    return inner


@app.after_request
def cabecalhos(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --- Arquivos do aplicativo ---------------------------------------------------------

@app.get("/")
def inicio():
    return send_from_directory(PASTA_APP, "index.html")


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(PASTA_APP, "sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/cal/<token>.ics")
def calendario(token):
    """Feed de calendário (Google Agenda, iPhone). Protegido por um token secreto no endereço."""
    cfg = config.carregar()
    if not hmac.compare_digest(token, cfg["calendario_token"]):
        return "não encontrado", 404
    regs = armazenamento.ler_json("data/intimacoes.json", [])
    hoje = datetime.now(TZ).date()
    itens = [r for r in dados_mod.unificar(regs, hoje) if r.get("fatal") and not r["cumprido"]
             and r.get("dias_restantes", -1) >= -30]
    resp = app.response_class(gerar_ics(itens, cfg.get("url_app", "")), mimetype="text/calendar")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _ics_esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_dobra(linha: str) -> str:
    """Quebra linhas longas (RFC 5545: até 75 octetos, continuação com espaço)."""
    out, atual = [], ""
    for ch in linha:
        if len((atual + ch).encode()) > 73:
            out.append(atual)
            atual = " " + ch
        else:
            atual += ch
    out.append(atual)
    return "\r\n".join(out)


def gerar_ics(itens: list[dict], url_app: str) -> str:
    agora = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    linhas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//prazos-oab//PT-BR", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH", "X-WR-CALNAME:Prazos OAB", "X-WR-TIMEZONE:America/Sao_Paulo",
              "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for r in itens:
        d = date.fromisoformat(r["fatal"])
        fim = d + timedelta(days=1)
        quem = r.get("partes") or r["processo"]
        desc = f"Processo {r['processo']}\n{r.get('orgao', '')}\n{r.get('prazo_obs', '')}"
        if r.get("ia") and r["ia"].get("providencia"):
            desc += f"\nProvidência (IA): {r['ia']['providencia']}"
        if r.get("nota"):
            desc += f"\nNota: {r['nota']}"
        desc += "\nConfira sempre no PJe."
        linhas += ["BEGIN:VEVENT", f"UID:{r['chave']}@prazos-oab", f"DTSTAMP:{agora}",
                   f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{fim:%Y%m%d}",
                   f"SUMMARY:{_ics_esc('⚖️ Prazo fatal — ' + quem)}",
                   f"DESCRIPTION:{_ics_esc(desc)}", f"URL:{url_app or ''}", "TRANSP:TRANSPARENT",
                   "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Prazo fatal amanhã", "TRIGGER:-PT15H", "END:VALARM",
                   "END:VEVENT"]
    linhas.append("END:VCALENDAR")
    return "\r\n".join(_ics_dobra(l) for l in linhas) + "\r\n"


@app.get("/<path:arquivo>")
def estatico(arquivo):
    if arquivo.startswith("api/"):
        return jsonify(erro="não encontrado"), 404
    return send_from_directory(PASTA_APP, arquivo)


# --- API -----------------------------------------------------------------------------

@app.get("/api/saude")
def saude():
    """Público e sem dados de processos: usado pelo vigia externo (GitHub)."""
    cfg = config.carregar()
    url = request.host_url.rstrip("/")
    if url.startswith("https://") and cfg.get("url_app") != url:
        cfg["url_app"] = url
        config.salvar(cfg)
    estado = armazenamento.ler_json("data/estado.json", {})
    minutos = None
    if estado.get("ultima_consulta"):
        minutos = int((datetime.now(TZ) - datetime.fromisoformat(estado["ultima_consulta"])).total_seconds() // 60)
    return jsonify(ok=True, minutos_desde_ultima_consulta=minutos,
                   falhas_seguidas=estado.get("falhas_seguidas", 0))


@app.post("/api/entrar")
def entrar():
    codigo = (request.get_json(silent=True) or {}).get("codigo", "")
    certo = config.carregar()["codigo_acesso"]
    normal = codigo.strip().upper().replace(" ", "")
    if not hmac.compare_digest(normal.replace("-", ""), certo.replace("-", "")):
        time.sleep(1.5)
        return jsonify(erro="código inválido"), 403
    session.permanent = True
    session["ok"] = True
    session["v"] = config.carregar().get("versao_sessao", 0)
    return jsonify(ok=True)


@app.post("/api/sair")
def sair():
    session.clear()
    return jsonify(ok=True)


def montar_dados(hoje: date | None = None) -> dict:
    hoje = hoje or datetime.now(TZ).date()
    regs = armazenamento.ler_json("data/intimacoes.json", [])
    estado = armazenamento.ler_json("data/estado.json", {})
    unif = dados_mod.unificar(regs, hoje)
    return {
        "hoje": hoje.isoformat(),
        "ultima_consulta": estado.get("ultima_consulta"),
        "falhas_seguidas": estado.get("falhas_seguidas", 0),
        "abertos": dados_mod.abertos(unif),
        "conferir": dados_mod.conferir(unif, hoje),
        "cumpridos": sorted((r for r in unif if r["cumprido"]), key=lambda r: r["cumprido"], reverse=True)[:50],
        "historico": sorted(unif, key=lambda r: r.get("disponibilizacao", ""), reverse=True),
    }


@app.get("/api/dados")
@exige_login
def dados():
    d = montar_dados()
    cfg = config.carregar()
    d["vapid_publica"] = cfg["vapid_publica"]
    base = cfg.get("url_app") or request.host_url.rstrip("/")
    d["calendario_url"] = f"{base}/cal/{cfg['calendario_token']}.ics"
    d["ia_ativa"] = bool(armazenamento.ler_json(dados_mod.IA, {})) or ia_mod.ativa()
    return jsonify(d)


@app.post("/api/cumprido")
@exige_login
def cumprido():
    corpo = request.get_json(silent=True) or {}
    chave = str(corpo.get("chave", ""))
    if not chave:
        return jsonify(erro="chave ausente"), 400
    status = armazenamento.ler_json(dados_mod.STATUS, {})
    if corpo.get("cumprido", True):
        status[chave] = {"cumprido": datetime.now(TZ).isoformat(timespec="minutes")}
    else:
        status.pop(chave, None)
    armazenamento.gravar_json(dados_mod.STATUS, status)
    return jsonify(ok=True)


@app.post("/api/push/inscrever")
@exige_login
def inscrever():
    insc = request.get_json(silent=True) or {}
    if not insc.get("endpoint") or not (insc.get("keys") or {}).get("p256dh"):
        return jsonify(erro="inscrição inválida"), 400
    total = push.inscrever(insc)
    return jsonify(ok=True, aparelhos=total)


@app.post("/api/push/teste")
@exige_login
def push_teste():
    try:
        n, erros = push.enviar_detalhado("Prazos OAB", "Notificações funcionando neste aparelho.", "/", "teste")
    except Exception as e:  # configuração incompleta, rede etc.
        return jsonify(erro=str(e)), 502
    if n == 0:
        return jsonify(erro="nenhum aparelho recebeu: " + ("; ".join(erros) or "sem inscrições")), 502
    return jsonify(ok=True, enviados=n, erros=erros)


def _corpo() -> dict:
    return request.get_json(silent=True) or {}


@app.post("/api/ajuste")
@exige_login
def ajuste():
    c = _corpo()
    chave = str(c.get("chave", ""))
    if not chave:
        return jsonify(erro="chave ausente"), 400
    try:
        if c.get("limpar"):
            dados_mod.definir_ajuste(chave)
        elif c.get("fatal"):
            dados_mod.definir_ajuste(chave, fatal=str(c["fatal"]))
        else:
            dados_mod.definir_ajuste(chave, dias=int(c.get("dias", 0)), tipo=str(c.get("tipo", "uteis")))
    except (ValueError, TypeError):
        return jsonify(erro="prazo inválido"), 400
    return jsonify(ok=True)


@app.post("/api/nota")
@exige_login
def nota():
    c = _corpo()
    if not c.get("chave"):
        return jsonify(erro="chave ausente"), 400
    dados_mod.definir_nota(str(c["chave"]), str(c.get("texto", "")))
    return jsonify(ok=True)


@app.post("/api/manual")
@exige_login
def manual():
    c = _corpo()
    try:
        if c.get("excluir"):
            dados_mod.excluir_manual(str(c["excluir"]))
            return jsonify(ok=True)
        if not c.get("processo") or not c.get("fatal"):
            return jsonify(erro="informe processo e data fatal"), 400
        chave = dados_mod.criar_manual(str(c["processo"]), str(c.get("descricao", "")), str(c["fatal"]),
                                       str(c.get("partes", "")), str(c.get("orgao", "")))
    except ValueError:
        return jsonify(erro="data inválida"), 400
    return jsonify(ok=True, chave=chave)


@app.post("/api/trocar-codigo")
@exige_login
def trocar_codigo():
    """Gera novo código e desconecta todos os aparelhos (inclusive este, que entra de novo)."""
    cfg = config.carregar()
    cfg["codigo_acesso"] = config.novo_codigo()
    cfg["versao_sessao"] = cfg.get("versao_sessao", 0) + 1
    config.salvar(cfg)
    session["v"] = cfg["versao_sessao"]  # mantém este aparelho conectado
    return jsonify(ok=True, codigo=cfg["codigo_acesso"])


@app.post("/api/calendario/novo")
@exige_login
def calendario_novo():
    """Gera um novo endereço de calendário (o antigo deixa de funcionar)."""
    import secrets as _s
    cfg = config.carregar()
    cfg["calendario_token"] = _s.token_urlsafe(24)
    config.salvar(cfg)
    base = cfg.get("url_app") or request.host_url.rstrip("/")
    return jsonify(ok=True, calendario_url=f"{base}/cal/{cfg['calendario_token']}.ics")


@app.post("/api/ia/aplicar")
@exige_login
def ia_aplicar():
    """Aplica como prazo manual a sugestão da IA para uma intimação."""
    chave = str(_corpo().get("chave", ""))
    sug = armazenamento.ler_json(dados_mod.IA, {}).get(chave) or {}
    if not sug.get("prazo_dias"):
        return jsonify(erro="sem prazo sugerido para esta intimação"), 400
    dados_mod.definir_ajuste(chave, dias=int(sug["prazo_dias"]), tipo=sug.get("tipo") or "uteis")
    return jsonify(ok=True)
