"""Cliente da API pública do DJEN (Comunica/CNJ) e normalização das comunicações."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime

import requests

API = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
POR_PAGINA = 100


class FalhaConsulta(Exception):
    pass


def _consultar(filtros: dict, inicio: date, fim: date, max_paginas: int,
               sessao: requests.Session | None = None) -> list[dict]:
    s = sessao or requests.Session()
    s.headers.setdefault("User-Agent", "prazos-oab/1.0 (acompanhamento pessoal de intimacoes)")
    itens: list[dict] = []
    pagina = 1
    while True:
        params = {**filtros,
                  "dataDisponibilizacaoInicio": inicio.isoformat(),
                  "dataDisponibilizacaoFim": fim.isoformat(),
                  "pagina": pagina, "itensPorPagina": POR_PAGINA}
        try:
            r = s.get(API, params=params, timeout=60)
        except requests.RequestException as e:
            raise FalhaConsulta(f"erro de rede: {e}") from e
        if r.status_code != 200:
            raise FalhaConsulta(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            dados = r.json()
        except ValueError as e:
            raise FalhaConsulta(f"resposta não é JSON: {r.text[:300]}") from e
        lote = dados.get("items") or dados.get("itens") or dados.get("content") or []
        if not isinstance(lote, list):
            raise FalhaConsulta(f"formato inesperado: chaves {list(dados)[:10]}")
        itens.extend(lote)
        total = dados.get("count") or dados.get("total")
        if len(lote) < POR_PAGINA or (total is not None and len(itens) >= int(total)):
            break
        pagina += 1
        if pagina > max_paginas:
            raise FalhaConsulta(f"paginação não terminou (mais de {max_paginas} páginas)")
    return itens


def buscar(oab: str, uf: str, inicio: date, fim: date, sessao: requests.Session | None = None) -> list[dict]:
    """Todas as comunicações da OAB no período. Levanta FalhaConsulta se algo der errado."""
    return _consultar({"numeroOab": oab, "ufOab": uf}, inicio, fim, 50, sessao)


def buscar_nome(nome: str, inicio: date, fim: date, sessao: requests.Session | None = None) -> list[dict]:
    """Busca reserva pelo nome do advogado (pega publicações com a OAB errada ou ausente).
    Mantém só as que citam o sobrenome, por segurança, caso o filtro do servidor seja amplo."""
    itens = _consultar({"nomeAdvogado": nome}, inicio, fim, 10, sessao)
    sobrenome = nome.split()[-1].upper()
    return [i for i in itens
            if sobrenome in (str(i.get("texto", "")) + json.dumps(i.get("destinatarioadvogados", ""),
                                                                    ensure_ascii=False)).upper()]


def _g(item: dict, *chaves, padrao=""):
    for c in chaves:
        v = item.get(c)
        if v not in (None, ""):
            return v
    return padrao


def _data(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    s = str(v)[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Comunicacao:
    chave: str
    processo: str
    disponibilizacao: str  # ISO
    tribunal: str
    orgao: str
    tipo: str
    texto: str
    link: str
    partes: str = ""

    def dict(self) -> dict:
        return asdict(self)


def normalizar(item: dict) -> Comunicacao:
    processo = str(_g(item, "numeroprocessocommascara", "numero_processo", "numeroProcesso"))
    disp = _data(_g(item, "data_disponibilizacao", "dataDisponibilizacao", "datadisponibilizacao"))
    texto = str(_g(item, "texto", "teor"))
    ident = _g(item, "hash", "id", "numeroComunicacao")
    if not ident:
        ident = hashlib.sha1(f"{processo}|{disp}|{texto[:500]}".encode()).hexdigest()
    return Comunicacao(
        chave=str(ident),
        processo=_mascara(processo),
        disponibilizacao=disp.isoformat() if disp else "",
        tribunal=str(_g(item, "siglaTribunal", "sigla_tribunal")),
        orgao=str(_g(item, "nomeOrgao", "nome_orgao")),
        tipo=str(_g(item, "tipoComunicacao", "tipo_comunicacao", "tipoDocumento")),
        texto=re.sub(r"\s+", " ", texto).strip(),
        link=str(_g(item, "link")),
        partes=extrair_partes(texto, item),
    )


_ATIVO = r"REQUERENTE|AUTORA?|EXEQUENTE|APELANTE|AGRAVANTE|EMBARGANTE|IMPETRANTE|RECORRENTE|RECLAMANTE|PROMOVENTE"
_PASSIVO = r"REQUERID[OA]|R[ÉE]U?|EXECUTAD[OA]|APELAD[OA]|AGRAVAD[OA]|EMBARGAD[OA]|IMPETRAD[OA]|RECORRID[OA]|RECLAMAD[OA]|PROMOVID[OA]"
_FIM = (r"(?:\.\s+(?=[A-ZÀ-Ú][a-zà-ú])|\s+(?:Advogad|INTIMA|DESPACHO|DECIS|SENTEN|CITA|ATO\s|CERTID|Por ordem|Vistos"
        r"|$|(?:" + _PASSIVO + r")\s*(?:\(S\))?:))")


def _nome(s: str) -> str:
    s = re.sub(r"\s+e outros?\s*\(\d+\)", " e outros", s.strip(" .,;"))
    minus = {"de", "da", "do", "das", "dos", "e", "s/a", "ltda", "ag"}
    return " ".join(w.lower() if w.lower() in minus else w.capitalize() for w in s.split())[:80]


def extrair_partes(texto: str, item: dict | None = None) -> str:
    """'Autor x Réu' a partir do cabeçalho do ato; vazio se não encontrar."""
    t = re.sub(r"\s+", " ", texto or "")
    a = re.search(r"(?:" + _ATIVO + r")\s*(?:\(S\))?:\s*(.+?)" + _FIM, t)
    p = re.search(r"(?:" + _PASSIVO + r")\s*(?:\(S\))?:\s*(.+?)" + _FIM.replace("|(?:" + _PASSIVO + r")\s*(?:\(S\))?:", ""), t)
    if a and p:
        return f"{_nome(a.group(1))} x {_nome(p.group(1))}"
    nomes = [d.get("nome") for d in (item or {}).get("destinatarios", []) if isinstance(d, dict) and d.get("nome")]
    return " / ".join(_nome(n) for n in nomes[:2])


def _mascara(n: str) -> str:
    d = re.sub(r"\D", "", n)
    if len(d) == 20:
        return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"
    return n


# --- Identificação do prazo no texto -------------------------------------------------

_NUM_EXTENSO = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
    "quinze": 15, "vinte": 20, "trinta": 30,
}

_RE_PRAZO = re.compile(
    r"prazo\s+(?:comum\s+)?(?:de\s+)?(\d{1,3}|[a-zçãéê]+)\s*(?:\([^)]{0,25}\))?\s*dias",
    re.IGNORECASE,
)


_RE_HORAS = re.compile(
    r"prazo\s+(?:comum\s+)?(?:de\s+)?(\d{1,3}|[a-zçãéê]+)\s*(?:\([^)]{0,25}\))?\s*horas",
    re.IGNORECASE,
)
# Frases cujo prazo não corre agora para o advogado intimado: citação da parte contrária
# e prazos futuros ("findo o prazo, intime-se...").
_RE_FRASE_IGNORAR = re.compile(
    r"cite-?se|citaç|findo\s+o\s+prazo|decorrido\s+o\s+prazo|ap[óo]s\s+o\s+decurso|transcorrido\s+o\s+prazo",
    re.IGNORECASE,
)


def _numero(bruto: str) -> int | None:
    bruto = bruto.lower()
    return int(bruto) if bruto.isdigit() else _NUM_EXTENSO.get(bruto)


def identificar_prazo(texto: str) -> tuple[int | None, str, str]:
    """Retorna (dias, observação, tipo), com tipo 'uteis' ou 'corridos'.

    Só devolve dias quando o texto diz expressamente. Prazos em horas ou em dias corridos
    são convertidos para contagem corrida (mais curta) e sinalizados.
    """
    t = texto or ""
    frases = re.split(r"(?<=[.;])\s+", t)
    achados, ignorados, corridos = [], 0, False
    for f in frases:
        tem_prazo = _RE_PRAZO.search(f) or _RE_HORAS.search(f)
        if not tem_prazo:
            continue
        if _RE_FRASE_IGNORAR.search(f):
            ignorados += 1
            continue
        for m in _RE_HORAS.finditer(f):
            n = _numero(m.group(1))
            if n:
                achados.append(max(1, -(-n // 24)))
                corridos = True
        for m in _RE_PRAZO.finditer(f):
            n = _numero(m.group(1))
            if n:
                achados.append(n)
                if re.search(r"corridos", f[m.start():m.end() + 30], re.IGNORECASE):
                    corridos = True
    tipo = "corridos" if corridos else "uteis"
    aviso_corr = " — prazo em horas/dias CORRIDOS: contagem corrida, CONFERIR" if corridos else ""
    if achados:
        unicos = sorted(set(achados))
        if len(unicos) == 1 and not ignorados:
            return unicos[0], f"prazo expresso no ato: {unicos[0]} dias{aviso_corr}", tipo
        extra = f"; {ignorados} trecho(s) de citação/prazo futuro desconsiderado(s)" if ignorados else ""
        return (min(unicos),
                f"prazos no ato: {', '.join(map(str, unicos))} dias — usado o menor{extra}{aviso_corr}; CONFERIR",
                tipo)
    if ignorados:
        return None, ("o ato só traz prazos de citação da outra parte ou para depois — "
                      "conferir se há providência agora"), "uteis"
    if re.search(r"prazo\s+legal", t, re.IGNORECASE):
        if re.search(r"r[ée]plica", t, re.IGNORECASE):
            return 15, "réplica 'no prazo legal': presumido 15 dias úteis (arts. 350-351 CPC) — CONFERIR", "uteis"
        return None, "ato fala em 'prazo legal' — definir o prazo conforme o ato processual", "uteis"
    if re.search(r"audi[êe]ncia", t, re.IGNORECASE):
        return None, "intimação de audiência — conferir data e se há providência", "uteis"
    return None, "prazo não identificado no texto — conferir", "uteis"


# --- Audiências -----------------------------------------------------------------------

_MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
          "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_RE_AUD = re.compile(r"audi[êe]ncia", re.IGNORECASE)
_RE_DATA_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_RE_DATA_EXT = re.compile(r"\b(\d{1,2})º?\s+de\s+(" + "|".join(_MESES) + r")\s+de\s+(\d{4})\b", re.IGNORECASE)
_RE_HORA = re.compile(r"\b([01]?\d|2[0-3])\s*(?::|h|horas?)\s*([0-5]\d)?\s*(?:min(?:utos)?)?", re.IGNORECASE)


def _datas_em(trecho: str) -> list[tuple[int, date]]:
    out = []
    for m in _RE_DATA_NUM.finditer(trecho):
        try:
            out.append((m.end(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    for m in _RE_DATA_EXT.finditer(trecho):
        try:
            out.append((m.end(), date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1)))))
        except ValueError:
            pass
    return sorted(out)


def extrair_audiencia(texto: str, referencia: date | None = None) -> dict | None:
    """Data (e hora, se houver) da audiência designada no ato: {"data": iso, "hora": "HH:MM" | ""}.

    Procura a primeira data depois de cada menção a "audiência" (até ~400 caracteres adiante) e
    descarta datas anteriores à disponibilização (audiências já realizadas). Na dúvida, devolve None
    e a intimação continua em "A conferir"."""
    t = texto or ""
    for m in _RE_AUD.finditer(t):
        trecho = t[m.start():m.start() + 400]
        for fim, d in _datas_em(trecho):
            if referencia and d < referencia:
                continue
            hora = ""
            h = _RE_HORA.search(trecho[fim:fim + 40])
            if h and (h.group(2) or re.search(r"h|hora", h.group(0), re.IGNORECASE)):
                hora = f"{int(h.group(1)):02d}:{h.group(2) or '00'}"
            return {"data": d.isoformat(), "hora": hora}
    return None
