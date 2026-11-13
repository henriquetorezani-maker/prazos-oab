import importlib
import json

import pytest

from prazos import armazenamento


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("DEV", "1")
    monkeypatch.setattr(armazenamento, "RAIZ", tmp_path)
    (tmp_path / "data").mkdir()
    regs = [
        {"chave": "a", "processo": "5041741-53.2026.8.08.0024", "disponibilizacao": "2026-11-12",
         "orgao": "Vitória - Comarca da Capital - 3º JEFP", "tribunal": "TJES", "tipo": "Intimação",
         "texto": "réplica no prazo legal", "link": "", "prazo_dias": 15, "prazo_obs": "réplica",
         "fatal": "2026-12-07", "fatal_alternativo": None, "contagem": "..."},
        {"chave": "b", "processo": "5041741-53.2026.8.08.0024", "disponibilizacao": "2026-11-12",
         "orgao": "Vitória - Comarca da Capital - 3º JEFP", "tribunal": "TJES", "tipo": "Intimação",
         "texto": "réplica no prazo legal", "link": "", "prazo_dias": 15, "prazo_obs": "réplica",
         "fatal": "2026-12-07", "fatal_alternativo": None, "contagem": "..."},
        {"chave": "c", "processo": "5051272-66.2026.8.08.0024", "disponibilizacao": "2026-11-05",
         "orgao": "Vitória - 6º JEC", "tribunal": "TJES", "tipo": "Intimação", "texto": "audiência",
         "link": "", "prazo_dias": None, "prazo_obs": "intimação de audiência", "fatal": None,
         "fatal_alternativo": None, "contagem": ""},
    ]
    (tmp_path / "data" / "intimacoes.json").write_text(json.dumps(regs), encoding="utf-8")
    from prazos import app as modulo
    modulo = importlib.reload(modulo)
    return modulo.app.test_client(), modulo


def test_exige_login(cliente):
    c, _ = cliente
    assert c.get("/api/dados").status_code == 401


def test_codigo_errado(cliente):
    c, _ = cliente
    assert c.post("/api/entrar", json={"codigo": "AAAA-AAAA-AAAA-AAAA"}).status_code == 403


def test_fluxo_completo(cliente):
    c, modulo = cliente
    codigo = modulo.config.carregar()["codigo_acesso"]
    assert c.post("/api/entrar", json={"codigo": codigo.lower().replace("-", " ")}).status_code == 200
    d = c.get("/api/dados", headers={"Accept": "application/json"}).get_json()
    # repetida (mesmo processo, data e teor) aparece uma vez só
    assert [r["processo"] for r in d["abertos"]] == ["5041741-53.2026.8.08.0024"]
    assert len(d["conferir"]) == 1 and d["vapid_publica"]
    chave = d["abertos"][0]["chave"]
    assert c.post("/api/cumprido", json={"chave": chave, "cumprido": True}).status_code == 200
    d = c.get("/api/dados").get_json()
    assert d["abertos"] == [] and len(d["cumpridos"]) == 1
    c.post("/api/cumprido", json={"chave": chave, "cumprido": False})
    assert len(c.get("/api/dados").get_json()["abertos"]) == 1


def test_inscricao_push_validada(cliente):
    c, modulo = cliente
    c.post("/api/entrar", json={"codigo": modulo.config.carregar()["codigo_acesso"]})
    assert c.post("/api/push/inscrever", json={"endpoint": "x"}).status_code == 400
    ok = c.post("/api/push/inscrever", json={"endpoint": "https://push.example/abc",
                                              "keys": {"p256dh": "k", "auth": "a"}})
    assert ok.get_json()["aparelhos"] == 1


def test_arquivos_do_app(cliente):
    c, _ = cliente
    assert b"<title>Prazos</title>" in c.get("/").data
    assert c.get("/relatorio.html").status_code == 200
    assert c.get("/sw.js").status_code == 200
    assert c.get("/manifest.webmanifest").status_code == 200
    assert c.get("/../prazos/config.py").status_code == 404


def _logar(c, modulo):
    c.post("/api/entrar", json={"codigo": modulo.config.carregar()["codigo_acesso"]})


def test_saude_publica_sem_dados(cliente):
    c, _ = cliente
    j = c.get("/api/saude").get_json()
    assert set(j) == {"ok", "minutos_desde_ultima_consulta", "falhas_seguidas"}


def test_ajuste_nota_avulso(cliente):
    c, modulo = cliente
    _logar(c, modulo)
    assert c.post("/api/ajuste", json={"chave": "c", "fatal": "2026-12-01"}).status_code == 200
    assert c.post("/api/ajuste", json={"chave": "c", "dias": 999}).status_code == 400
    assert c.post("/api/nota", json={"chave": "a", "texto": "protocolar no último dia"}).status_code == 200
    r = c.post("/api/manual", json={"processo": "0001", "descricao": "laudo", "fatal": "2026-11-30"})
    assert r.status_code == 200
    d = c.get("/api/dados").get_json()
    abertos = {x["chave"]: x for x in d["abertos"]}
    assert "c" in abertos and abertos["c"]["fatal"] == "2026-12-01"
    assert any(x.get("nota") == "protocolar no último dia" for x in d["abertos"])
    assert any(x.get("manual") for x in d["abertos"])
    assert c.post("/api/manual", json={"processo": "0001", "fatal": "31/11"}).status_code == 400


def test_trocar_codigo_derruba_outros_aparelhos(cliente):
    c, modulo = cliente
    outro = modulo.app.test_client()
    _logar(c, modulo)
    _logar(outro, modulo)
    novo = c.post("/api/trocar-codigo", json={}).get_json()["codigo"]
    assert c.get("/api/dados").status_code == 200          # este aparelho continua
    assert outro.get("/api/dados").status_code == 401      # o outro cai
    assert outro.post("/api/entrar", json={"codigo": novo}).status_code == 200


def test_calendario_ics(cliente):
    c, modulo = cliente
    _logar(c, modulo)
    url = c.get("/api/dados").get_json()["calendario_url"]
    caminho = "/cal/" + url.split("/cal/")[1]
    r = c.get(caminho)
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    corpo = r.data.decode()
    assert "BEGIN:VEVENT" in corpo and "DTSTART;VALUE=DATE:20261207" in corpo
    assert c.get("/cal/token-errado.ics").status_code == 404
    novo = c.post("/api/calendario/novo", json={}).get_json()["calendario_url"]
    assert c.get(caminho).status_code == 404 and c.get("/cal/" + novo.split("/cal/")[1]).status_code == 200


def test_dados_tem_dias_uteis(cliente):
    c, modulo = cliente
    _logar(c, modulo)
    d = c.get("/api/dados").get_json()
    assert all("uteis_restantes" in r for r in d["abertos"])
