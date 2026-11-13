"""Cliente da API pública do DJEN (Comunica/CNJ) e normalização das comunicações."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime

import requests

API = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
POR_PAGINA = 100


class FalhaConsulta(Exception):
    pass


def buscar(oab: str, uf: str, inicio: date, fim: date, sessao: requests.Session | None = None) -> list[dict]:
    """Retorna todas as comunicações (itens brutos) da OAB no período. Levanta FalhaConsulta se algo der errado."""
    s = sessao or requests.Session()
    s.headers.setdefault("User-Agent", "prazos-oab/1.0 (acompanhamento pessoal de intimacoes)")
    itens: list[dict] = []
    pagina = 1
    while True:
        params = {
            "numeroOab": oab,
            "ufOab": uf,
            "dataDisponibilizacaoInicio": inicio.isoformat(),
            "dataDisponibilizacaoFim": fim.isoformat(),
            "pagina": pagina,
            "itensPorPagina": POR_PAGINA,
        }
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
        if pagina > 50:
            raise FalhaConsulta("paginação não terminou (mais de 50 páginas)")
    return itens


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
    )


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


def identificar_prazo(texto: str) -> tuple[int | None, str]:
    """Retorna (dias, observação). Só devolve dias quando o texto diz expressamente."""
    t = texto or ""
    achados = []
    for m in _RE_PRAZO.finditer(t):
        bruto = m.group(1).lower()
        n = int(bruto) if bruto.isdigit() else _NUM_EXTENSO.get(bruto)
        if n:
            achados.append(n)
    if achados:
        unicos = sorted(set(achados))
        if len(unicos) == 1:
            return unicos[0], f"prazo expresso no ato: {unicos[0]} dias"
        return min(unicos), f"vários prazos no ato ({', '.join(map(str, unicos))} dias) — usado o menor; CONFERIR"
    if re.search(r"prazo\s+legal", t, re.IGNORECASE):
        if re.search(r"r[ée]plica", t, re.IGNORECASE):
            return 15, "réplica 'no prazo legal': presumido 15 dias úteis (arts. 350-351 CPC) — CONFERIR"
        return None, "ato fala em 'prazo legal' — definir o prazo conforme o ato processual"
    if re.search(r"audi[êe]ncia", t, re.IGNORECASE):
        return None, "intimação de audiência — conferir data e se há providência"
    return None, "prazo não identificado no texto — conferir"
