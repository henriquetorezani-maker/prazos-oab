from prazos.comunica import identificar_prazo, normalizar
from prazos.rodar import comarca_de, enriquecer

TEXTO_DANIELA = (
    "INTIMAÇÃO ELETRONICA Por ordem do(a) Exmo(a). Dr(a). Juiz(a) de Direito da Vitória - Comarca da "
    "Capital - 3º Juizado Especial Criminal e Fazenda Pública, foi encaminhada a intimação eletrônica à "
    "parte autora para apresentar réplica e indicar se pretende produzir outras provas, devendo "
    "apresentá-las de forma específica e justificada, sob pena de preclusão e julgamento do processo no "
    "estado em que se encontra, no prazo legal. VITÓRIA-ES, 11 de novembro de 2026."
)


def test_prazo_expresso():
    assert identificar_prazo("Cite-se para, no prazo de 15 (quinze) dias úteis, apresentar defesa")[0] == 15
    assert identificar_prazo("no prazo de cinco dias, manifeste-se")[0] == 5


def test_varios_prazos_usa_menor():
    dias, obs = identificar_prazo(
        "Cite-se a parte requerida para, no prazo de 15 (quinze) dias úteis, apresentar defesa. "
        "Findo o prazo, intime-se a parte autora para se manifestar no prazo de 05 (cinco) dias úteis.")
    assert dias == 5 and "CONFERIR" in obs


def test_replica_prazo_legal():
    dias, obs = identificar_prazo(TEXTO_DANIELA)
    assert dias == 15 and "réplica" in obs


def test_audiencia_sem_prazo():
    dias, obs = identificar_prazo("INTIMAÇÃO AUDIÊNCIA DE CONCILIAÇÃO ... Data: 15/02/2027 Hora: 14:00")
    assert dias is None and "audiência" in obs


def test_normalizar_e_enriquecer_daniela():
    item = {
        "id": 123,
        "data_disponibilizacao": "2026-11-12",
        "siglaTribunal": "TJES",
        "nomeOrgao": "Vitória - Comarca da Capital - 3º Juizado Especial Criminal e Fazenda Pública",
        "tipoComunicacao": "Intimação",
        "texto": TEXTO_DANIELA,
        "numero_processo": "50417415320268080024",
    }
    c = normalizar(item)
    assert c.processo == "5041741-53.2026.8.08.0024"
    assert comarca_de(c.orgao) == "Vitória"
    r = enriquecer(c)
    assert r["fatal"] == "2026-12-07"
