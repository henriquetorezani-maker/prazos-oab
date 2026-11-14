from prazos.comunica import extrair_partes, identificar_prazo, normalizar
from prazos.rodar import comarca_de, enriquecer

TEXTO_DANIELA = (
    "PROCESSO Nº 5041741-53.2026.8.08.0024 PROCEDIMENTO DO JUIZADO ESPECIAL DA FAZENDA PÚBLICA (14695) "
    "REQUERENTE: DANIELA GUIMARAES NAHSSEN PEREIRA REQUERIDO: MUNICIPIO DE VITORIA "
    "INTIMAÇÃO ELETRONICA Por ordem do(a) Exmo(a). Dr(a). Juiz(a) de Direito da Vitória - Comarca da "
    "Capital - 3º Juizado Especial Criminal e Fazenda Pública, foi encaminhada a intimação eletrônica à "
    "parte autora para apresentar réplica e indicar se pretende produzir outras provas, devendo "
    "apresentá-las de forma específica e justificada, sob pena de preclusão e julgamento do processo no "
    "estado em que se encontra, no prazo legal. VITÓRIA-ES, 11 de novembro de 2026."
)

TEXTO_CARLOS = (
    "REQUERENTE: CARLOS GABRIEL SANTOS CHRYSOSTOMO REQUERIDO: DEUTSCHE LUFTHANSA AG, TAM LINHAS AEREAS S/A. "
    "DESPACHO - CITAÇÃO - INTIMAÇÃO ... dispenso, por ora, a realização da audiência conciliatória. "
    "Cite-se a parte requerida para, no prazo de 15 (quinze) dias úteis, apresentar eventual proposta de acordo, "
    "bem como defesa e documentos. Findo o prazo, intime-se a parte autora para se manifestar no prazo de "
    "05 (cinco) dias úteis. Cumpra-se."
)


def test_prazo_expresso():
    assert identificar_prazo("Intime-se o autor para, no prazo de 15 (quinze) dias úteis, emendar a inicial")[:1] == (15,)
    dias, _, tipo = identificar_prazo("no prazo de cinco dias, manifeste-se")
    assert dias == 5 and tipo == "uteis"


def test_citacao_e_prazo_futuro_ignorados():
    dias, obs, _ = identificar_prazo(TEXTO_CARLOS)
    assert dias is None and "citação" in obs


def test_horas_viram_dias_corridos():
    dias, obs, tipo = identificar_prazo("Intime-se o autor para, no prazo de 48 (quarenta e oito) horas, juntar o documento.")
    assert (dias, tipo) == (2, "corridos") and "CORRIDOS" in obs


def test_dias_corridos():
    dias, _, tipo = identificar_prazo("Manifeste-se a parte autora no prazo de 10 (dez) dias corridos.")
    assert (dias, tipo) == (10, "corridos")


def test_varios_prazos_usa_menor():
    dias, obs, _ = identificar_prazo("Manifestem-se as partes no prazo de 15 dias. Após, vista ao MP no prazo de 10 dias.")
    assert dias == 10 and "CONFERIR" in obs


def test_replica_prazo_legal():
    dias, obs, tipo = identificar_prazo(TEXTO_DANIELA)
    assert dias == 15 and tipo == "uteis" and "réplica" in obs


def test_audiencia_sem_prazo():
    dias, obs, _ = identificar_prazo("INTIMAÇÃO AUDIÊNCIA DE CONCILIAÇÃO ... Data: 15/02/2027 Hora: 14:00")
    assert dias is None and "audiência" in obs


def test_partes():
    assert extrair_partes(TEXTO_DANIELA) == "Daniela Guimaraes Nahssen Pereira x Municipio de Vitoria"
    assert extrair_partes(
        "REQUERENTE: MARCUS CAPPI CHRYSOSTOMO Advogados do(a) REQUERENTE: HENRIQUE COUTINHO TOREZANI - ES43793 "
        "REQUERIDO: DEUTSCHE LUFTHANSA AG, TAM LINHAS AEREAS S/A. INTIMAÇÃO AUDIÊNCIA"
    ).startswith("Marcus Cappi Chrysostomo x Deutsche Lufthansa")
    assert extrair_partes("texto sem cabeçalho") == ""


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
    assert r["fatal"] == "2026-12-07" and r["partes"].startswith("Daniela")


def test_extrair_audiencia():
    from datetime import date
    from prazos.comunica import extrair_audiencia as e
    ref = date(2026, 11, 1)
    assert e("INTIMAÇÃO AUDIÊNCIA DE CONCILIAÇÃO ... Data: 15/02/2027 Hora: 14:00", ref) == {"data": "2027-02-15", "hora": "14:00"}
    assert e("designo audiência de instrução para o dia 3 de março de 2027, às 14h30min.", ref) == {"data": "2027-03-03", "hora": "14:30"}
    assert e("audiência redesignada para 09/12/2026 às 9h.", ref) == {"data": "2026-12-09", "hora": "09:00"}
    assert e("Realizada audiência em 10/10/2026. Intime-se.", ref) is None
    assert e("dispenso a audiência conciliatória. Prazo de 15 dias.", ref) is None
