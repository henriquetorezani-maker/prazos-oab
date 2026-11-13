"""Calendário forense e contagem de prazos em dias úteis.

Princípio: na dúvida, o prazo é contado da forma MAIS CURTA (conservadora).
Dias de suspensão incerta (pontos facultativos, feriados forenses que dependem
de ato do TJES) são tratados como ÚTEIS na contagem principal e sinalizados,
para o advogado conferir. A data "alternativa" mostra o fatal caso esses dias
não sejam úteis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo anônimo gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


# Feriados nacionais fixos (Lei 662/49, Lei 6.802/80, Lei 14.759/23 etc.)
NACIONAIS_FIXOS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independência",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Dia Nacional de Zumbi e da Consciência Negra",
    (12, 25): "Natal",
}

# Feriados municipais certos, por comarca (chave em minúsculas, sem acento).
MUNICIPAIS_FIXOS = {
    "vitoria": {(9, 8): "Nossa Senhora da Vitória (Vitória)"},
}

# Dias em que a suspensão depende de ato do Tribunal: conferir no calendário do TJES.
DUVIDOSOS_FIXOS = {
    (10, 28): "Dia do Servidor Público (ponto facultativo?)",
    (12, 8): "Dia da Justiça (feriado forense?)",
}


def _norm(comarca: str | None) -> str:
    if not comarca:
        return ""
    s = comarca.lower()
    for a, b in (("á", "a"), ("ã", "a"), ("â", "a"), ("é", "e"), ("ê", "e"),
                 ("í", "i"), ("ó", "o"), ("ô", "o"), ("õ", "o"), ("ú", "u"), ("ç", "c")):
        s = s.replace(a, b)
    for nome in MUNICIPAIS_FIXOS:
        if nome in s:
            return nome
    return s


def feriados_certos(ano: int, comarca: str | None = None) -> dict[date, str]:
    out = {date(ano, m, d): n for (m, d), n in NACIONAIS_FIXOS.items()}
    p = pascoa(ano)
    out[p - timedelta(days=2)] = "Sexta-feira Santa"
    for (m, d), n in MUNICIPAIS_FIXOS.get(_norm(comarca), {}).items():
        out[date(ano, m, d)] = n
    return out


def dias_duvidosos(ano: int) -> dict[date, str]:
    p = pascoa(ano)
    out = {date(ano, m, d): n for (m, d), n in DUVIDOSOS_FIXOS.items()}
    out[p - timedelta(days=48)] = "Segunda de Carnaval (ponto facultativo?)"
    out[p - timedelta(days=47)] = "Terça de Carnaval (ponto facultativo?)"
    out[p - timedelta(days=46)] = "Quarta-feira de Cinzas (expediente parcial?)"
    out[p - timedelta(days=3)] = "Quinta-feira Santa (feriado forense?)"
    out[p + timedelta(days=60)] = "Corpus Christi (ponto facultativo?)"
    return out


def em_recesso(d: date) -> bool:
    """Suspensão de prazos de 20/12 a 20/01, inclusive (CPC, art. 220)."""
    return (d.month == 12 and d.day >= 20) or (d.month == 1 and d.day <= 20)


@dataclass
class Calendario:
    comarca: str | None = None
    duvidosos_como_uteis: bool = True  # modo conservador

    def motivo_nao_util(self, d: date) -> str | None:
        if d.weekday() >= 5:
            return "fim de semana"
        if em_recesso(d):
            return "recesso forense (art. 220 CPC)"
        f = feriados_certos(d.year, self.comarca).get(d)
        if f:
            return f
        if not self.duvidosos_como_uteis:
            f = dias_duvidosos(d.year).get(d)
            if f:
                return f
        return None

    def util(self, d: date) -> bool:
        return self.motivo_nao_util(d) is None

    def proximo_util(self, d: date) -> date:
        """Primeiro dia útil ESTRITAMENTE posterior a d."""
        d += timedelta(days=1)
        while not self.util(d):
            d += timedelta(days=1)
        return d


@dataclass
class ResultadoPrazo:
    dias: int
    publicacao: date | None
    inicio: date
    fatal: date
    fatal_alternativo: date | None = None
    duvidosos_no_periodo: list[tuple[date, str]] = field(default_factory=list)
    pulados: list[tuple[date, str]] = field(default_factory=list)
    corridos: bool = False

    def explicacao(self) -> str:
        partes = []
        if self.publicacao:
            partes.append(f"publicação {self.publicacao:%d/%m/%Y}")
        partes.append(f"início {self.inicio:%d/%m/%Y}")
        if self.corridos:
            partes.append(f"{self.dias} dias CORRIDOS → fatal {self.fatal:%d/%m/%Y} "
                          "(se cair em dia não útil, recuado para o dia útil anterior)")
            return "; ".join(partes)
        partes.append(f"{self.dias} dias úteis → fatal {self.fatal:%d/%m/%Y}")
        feriados = [f"{d:%d/%m} ({m})" for d, m in self.pulados if m != "fim de semana"]
        if feriados:
            partes.append("excluídos: " + "; ".join(feriados))
        if self.duvidosos_no_periodo:
            dv = "; ".join(f"{d:%d/%m} ({m})" for d, m in self.duvidosos_no_periodo)
            alt = f" — se não forem úteis, fatal {self.fatal_alternativo:%d/%m/%Y}" if self.fatal_alternativo else ""
            partes.append(f"CONFERIR {dv}, contados como úteis{alt}")
        return "; ".join(partes)


def _contar(inicio: date, dias: int, cal: Calendario) -> tuple[date, list]:
    pulados = []
    d = inicio
    contados = 1
    while contados < dias:
        d += timedelta(days=1)
        m = cal.motivo_nao_util(d)
        if m:
            pulados.append((d, m))
        else:
            contados += 1
    return d, pulados


def prazo_corridos(disponibilizacao: date, dias: int, comarca: str | None = None) -> ResultadoPrazo:
    """Prazo em dias corridos (ou em horas convertidas em dias), intimação pelo DJEN.

    Conservador: conta do 1º dia útil após a publicação e, se o último dia cair em dia
    não útil, RECUA para o dia útil anterior (a lei prorrogaria; aqui preferimos o mais cedo).
    """
    cal = Calendario(comarca)
    publicacao = cal.proximo_util(disponibilizacao)
    inicio = cal.proximo_util(publicacao)
    fatal = inicio + timedelta(days=max(dias, 1) - 1)
    while fatal > inicio and not cal.util(fatal):
        fatal -= timedelta(days=1)
    r = ResultadoPrazo(dias, publicacao, inicio, fatal)
    r.corridos = True
    return r


def prazo_por_disponibilizacao(disponibilizacao: date, dias: int,
                               comarca: str | None = None) -> ResultadoPrazo:
    """Intimação pelo DJEN: publicação no 1º dia útil seguinte à disponibilização;
    o prazo começa no 1º dia útil seguinte à publicação (Lei 11.419/06, art. 4º, §§ 3º e 4º)."""
    return _resultado(disponibilizacao, True, dias, comarca)


def prazo_por_ciencia(ciencia: date, dias: int, comarca: str | None = None) -> ResultadoPrazo:
    """Ciência registrada no sistema (expediente do PJe): prazo começa no dia útil seguinte."""
    return _resultado(ciencia, False, dias, comarca)


def _calcular(evento: date, via_djen: bool, dias: int, cal: Calendario):
    publicacao = cal.proximo_util(evento) if via_djen else None
    inicio = cal.proximo_util(publicacao or evento)
    fatal, pulados = _contar(inicio, dias, cal)
    return publicacao, inicio, fatal, pulados


def _resultado(evento: date, via_djen: bool, dias: int, comarca: str | None) -> ResultadoPrazo:
    cal = Calendario(comarca)
    publicacao, inicio, fatal, pulados = _calcular(evento, via_djen, dias, cal)

    # Dias duvidosos dentro do período (contados como úteis no modo conservador)
    duv = []
    d = evento + timedelta(days=1)
    while d <= fatal:
        m = dias_duvidosos(d.year).get(d)
        if m and cal.util(d):
            duv.append((d, m))
        d += timedelta(days=1)

    alternativo = None
    if duv:
        cal2 = Calendario(comarca, duvidosos_como_uteis=False)
        _, _, alternativo, _ = _calcular(evento, via_djen, dias, cal2)
        if alternativo == fatal:
            alternativo = None
    return ResultadoPrazo(dias, publicacao, inicio, fatal, alternativo, duv, pulados)
