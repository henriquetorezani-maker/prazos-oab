from datetime import date

from prazos.calendario import (
    Calendario, pascoa, prazo_por_ciencia, prazo_por_disponibilizacao,
)


def test_pascoa():
    assert pascoa(2026) == date(2026, 4, 5)
    assert pascoa(2027) == date(2027, 3, 28)


def test_daniela_replica_15_dias():
    # DJEN disponibilizado 12/11/2026 (qui), publicado 13/11, início 16/11, 20/11 feriado.
    r = prazo_por_disponibilizacao(date(2026, 11, 12), 15, "Vitória")
    assert r.publicacao == date(2026, 11, 13)
    assert r.inicio == date(2026, 11, 16)
    assert r.fatal == date(2026, 12, 7)
    assert any(d == date(2026, 11, 20) for d, _ in r.pulados)


def test_marcus_5_dias_bate_com_pje():
    # DJEN disponibilizado 05/11; PJe registrou ciência 06/11 e data limite 13/11.
    r = prazo_por_disponibilizacao(date(2026, 11, 5), 5, "Vitória")
    assert r.publicacao == date(2026, 11, 6)
    assert r.fatal == date(2026, 11, 13)
    assert prazo_por_ciencia(date(2026, 11, 6), 5).fatal == date(2026, 11, 13)


def test_dia_da_justica_sinalizado():
    # Disponibilizado 13/11 (sex): publicação 16/11, início 17/11; 15 dias úteis atinge 08/12.
    r = prazo_por_disponibilizacao(date(2026, 11, 13), 15, "Vitória")
    assert r.fatal == date(2026, 12, 8)
    assert [d for d, _ in r.duvidosos_no_periodo] == [date(2026, 12, 8)]
    assert r.fatal_alternativo == date(2026, 12, 9)


def test_recesso_suspende():
    # Disponibilizado 15/12 (ter): publicação 16/12, início 17/12; conta 17 e 18/12,
    # suspende 20/12 a 20/01, retoma 21/01/2027. 15º dia útil: 08/02/2027.
    r = prazo_por_disponibilizacao(date(2026, 12, 15), 15)
    assert r.inicio == date(2026, 12, 17)
    assert r.fatal == date(2027, 2, 8)


def test_feriado_municipal_so_na_comarca():
    assert not Calendario("Vitória").util(date(2026, 9, 8))
    assert Calendario("Vila Velha").util(date(2026, 9, 8))


def test_carnaval_conservador():
    cal = Calendario()
    assert cal.util(date(2027, 2, 8))  # segunda de Carnaval 2027 contada como útil (duvidoso)
    assert not Calendario(duvidosos_como_uteis=False).util(date(2027, 2, 8))
