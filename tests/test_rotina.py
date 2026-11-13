"""Rotina completa com API simulada: novas intimações, ajustes, prazos avulsos e lembretes."""
import json
from datetime import datetime

import pytest

from prazos import armazenamento, comunica, dados, notifica, rodar
from tests.test_comunica import TEXTO_DANIELA


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setattr(armazenamento, "RAIZ", tmp_path)
    monkeypatch.setattr(rodar, "RAIZ", tmp_path)
    monkeypatch.setattr(rodar, "ESTADO", tmp_path / "data/estado.json")
    monkeypatch.setattr(rodar, "INTIMACOES", tmp_path / "data/intimacoes.json")
    monkeypatch.setattr(rodar, "PAINEL", tmp_path / "PAINEL.md")
    enviados = []
    monkeypatch.setattr(notifica, "enviar", lambda texto, titulo="", corpo=None, tag=None, url="/": enviados.append(titulo))
    lote = [{"id": 1, "data_disponibilizacao": "2026-11-12", "siglaTribunal": "TJES",
             "nomeOrgao": "Vitória - Comarca da Capital - 3º Juizado Especial Criminal e Fazenda Pública",
             "tipoComunicacao": "Intimação", "texto": TEXTO_DANIELA, "numero_processo": "50417415320268080024"}]
    monkeypatch.setattr(comunica, "buscar", lambda *a, **k: list(lote))
    monkeypatch.setattr(comunica, "buscar_nome", lambda *a, **k: [])
    relogio = {"agora": datetime(2026, 11, 13, 10, 0, tzinfo=rodar.TZ)}

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return relogio["agora"]
    monkeypatch.setattr(rodar, "datetime", Relogio)
    return enviados, lote, relogio, tmp_path


def test_fluxo_rotina(ambiente):
    enviados, lote, relogio, tmp = ambiente
    assert rodar.executar() == 0
    assert enviados == ["🚀 Rotina iniciada"]

    # nova intimação com 48 horas → corridos
    lote.append({"id": 2, "data_disponibilizacao": "2026-11-13", "siglaTribunal": "TJES",
                 "nomeOrgao": "Vitória - Comarca da Capital - 6º Juizado Especial Cível", "tipoComunicacao": "Intimação",
                 "texto": "REQUERENTE: FULANO DE TAL REQUERIDO: EMPRESA X. Intime-se o autor para, no prazo de 48 horas, "
                          "juntar comprovante.", "numero_processo": "50000000020268080024"})
    relogio["agora"] = datetime(2026, 11, 13, 11, 0, tzinfo=rodar.TZ)
    rodar.executar()
    assert any(t.startswith("🔔 Nova intimação — fatal") for t in enviados)
    regs = json.loads((tmp / "data/intimacoes.json").read_text())
    nova = next(r for r in regs if r["processo"].startswith("5000000"))
    assert nova["prazo_tipo"] == "corridos" and nova["partes"] == "Fulano de Tal x Empresa X"


def test_lembretes_escalonados(ambiente):
    enviados, _, relogio, _ = ambiente
    rodar.executar()                        # 13/11: fatal Daniela 07/12, longe
    enviados.clear()
    relogio["agora"] = datetime(2026, 12, 3, 9, 0, tzinfo=rodar.TZ)   # faltam 4 dias → marco 5
    rodar.executar()
    assert sum("Prazo vence em 4" in t for t in enviados) == 1
    enviados.clear()
    relogio["agora"] = datetime(2026, 12, 3, 10, 0, tzinfo=rodar.TZ)  # mesmo dia: não repete
    rodar.executar()
    assert not any(t.startswith("⏰") for t in enviados)
    relogio["agora"] = datetime(2026, 12, 7, 9, 0, tzinfo=rodar.TZ)   # dia do fatal
    rodar.executar()
    assert any("VENCE HOJE" in t for t in enviados)
    enviados.clear()
    relogio["agora"] = datetime(2026, 12, 7, 12, 0, tzinfo=rodar.TZ)
    rodar.executar()
    assert not any(t.startswith("⏰") for t in enviados)


def test_cumprido_nao_gera_lembrete(ambiente):
    enviados, _, relogio, _ = ambiente
    rodar.executar()
    regs = json.loads((ambiente[3] / "data/intimacoes.json").read_text())
    armazenamento.gravar_json(dados.STATUS, {regs[0]["chave"]: {"cumprido": "2026-12-01T10:00"}})
    enviados.clear()
    relogio["agora"] = datetime(2026, 12, 7, 9, 0, tzinfo=rodar.TZ)
    rodar.executar()
    assert not any(t.startswith("⏰") for t in enviados)


def test_ajuste_manual_e_avulso(ambiente):
    _, _, relogio, _ = ambiente
    rodar.executar()
    regs = json.loads((ambiente[3] / "data/intimacoes.json").read_text())
    chave = regs[0]["chave"]
    dados.definir_ajuste(chave, dias=5, tipo="uteis")
    hoje = relogio["agora"].date()
    u = {r["chave"]: r for r in dados.unificar(regs, hoje)}
    # 5 dias úteis: 16, 17, 18, 19 e 23/11 (20/11 é feriado)
    assert u[chave]["fatal"] == "2026-11-23" and u[chave]["ajustado"]
    dados.definir_ajuste(chave, fatal="2026-12-01")
    assert {r["chave"]: r for r in dados.unificar(regs, hoje)}[chave]["fatal"] == "2026-12-01"
    dados.definir_ajuste(chave)  # volta ao automático
    assert {r["chave"]: r for r in dados.unificar(regs, hoje)}[chave]["fatal"] == "2026-12-07"
    k = dados.criar_manual("0001234-56.2020.8.08.0035", "manifestar sobre laudo", "2026-11-30", "A x B")
    ab = dados.abertos(dados.unificar(regs, hoje))
    assert [r["chave"] for r in ab][0] == k
    dados.excluir_manual(k)
    assert all(not r.get("manual") for r in dados.unificar(regs, hoje))


def test_ia_sugestao_e_relatorio_semanal(ambiente, monkeypatch):
    from prazos import ia
    enviados, _, relogio, tmp = ambiente
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")
    chamadas = []
    monkeypatch.setattr(ia, "resumir", lambda texto, partes="", orgao="": chamadas.append(partes) or {
        "resumo": "Intima o autor para réplica.", "providencia": "Apresentar réplica.", "prazo_dias": 15,
        "tipo": "uteis", "destinatario": "parte autora", "confianca": "media"})
    rodar.executar()
    assert chamadas and json.loads((tmp / "data/ia.json").read_text())
    regs = json.loads((tmp / "data/intimacoes.json").read_text())
    u = dados.unificar(regs, relogio["agora"].date())
    assert u[0]["ia"]["providencia"] == "Apresentar réplica." and u[0]["uteis_restantes"] == 15
    # segunda-feira 8h: aviso do relatório, uma vez só
    relogio["agora"] = datetime(2026, 11, 16, 8, 30, tzinfo=rodar.TZ)
    rodar.executar(); rodar.executar()
    assert enviados.count("📄 Prazos da semana") == 1


def test_ia_resposta_mal_formada_nao_quebra():
    from prazos.ia import _extrair_json
    d = _extrair_json('Claro! {"resumo": "x", "providencia": "y", "prazo_dias": 900, "tipo": "dias", "confianca": "alta"}')
    assert d["prazo_dias"] is None and d["tipo"] is None and d["destinatario"] == ""
