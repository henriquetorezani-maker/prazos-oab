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

from . import armazenamento, config, dados as dados_mod, push

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
    d["vapid_publica"] = config.carregar()["vapid_publica"]
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
