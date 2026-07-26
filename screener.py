"""
Screener de oportunidades de compra: P/E vs sector, PEG, crecimiento de
beneficios e insider buying. Rankea la watchlist, genera un PDF en tabla
con el top 10 (y titulares de noticias recientes) y lo envia a Telegram.

Uso: python screener.py
"""
import json
import os
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import requests
import yfinance as yf
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from fpdf import FPDF

# Debe ejecutarse ANTES de leer cualquier os.environ.get() a nivel de modulo
# (ej. FMP_API_KEY, SEC_EDGAR_USER_AGENT mas abajo); si no, un .env local
# nunca llegaria a tiempo para esas constantes.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.txt")
INSIDER_LOOKBACK_DAYS = 90
PEG_MAX = 1.5
EARNINGS_GROWTH_MIN = 0.15  # 15%
ROE_MIN = 0.15  # 15%
DEBT_EQUITY_MAX = 100  # yfinance lo expresa como % (100 = deuda igual al patrimonio)
CURRENT_RATIO_MIN = 1.5
TOP_N = 10
NEWS_PER_TICKER = 2
DESCRIPTION_MAX_CHARS = 500
SMALL_CAP_MAX = 2_000_000_000  # USD; por debajo se trata como "pequeña capitalizacion"
SMALL_CAP_TOP_N = 5

# Cesta tematica "Trump trade": acciones que la prensa financiera (Goldman
# Sachs, Kiplinger, Bloomberg, Investing.com...) menciona repetidamente como
# beneficiarias o perjudicadas por politicas de la administracion Trump
# (aranceles, gasto en defensa, desregulacion financiera, energia, cripto,
# inmigracion). NO es el patrimonio personal de Donald Trump ni sale de
# ningun informe de activos declarado (ver seccion 3 del informe / glosario
# "Cesta Trump trade" para el detalle y las advertencias).
TRUMP_TRADE_THEMES = {
    "DJT": "Empresa de Trump (Trump Media & Technology Group)",
    "LMT": "Defensa (gasto militar)",
    "RTX": "Defensa (gasto militar)",
    "NOC": "Defensa (gasto militar)",
    "NUE": "Aranceles al acero / manufactura domestica",
    "XOM": "Energia (petroleo y gas domesticos)",
    "COIN": "Cripto (politica regulatoria favorable)",
    "JPM": "Banca (desregulacion financiera)",
    "GEO": "Inmigracion (contratos de detencion con ICE)",
}

STRONG_BUY_GRADES = {
    "buy", "strong buy", "outperform", "overweight",
    "market outperform", "sector outperform", "long-term buy",
}

POSITIVE_WORDS = (
    "beat", "beats", "surge", "surges", "soar", "soars", "record", "upgrade",
    "outperform", "growth", "rally", "rallies", "strong", "raises", "tops",
    "jump", "jumps", "gain", "gains", "expands", "wins", "profit",
)
NEGATIVE_WORDS = (
    "falls", "fall", "drop", "drops", "plunge", "plunges", "cuts", "cut",
    "downgrade", "miss", "misses", "weak", "concern", "concerns", "lawsuit",
    "probe", "recall", "warns", "warning", "slump", "sell-off", "tumbles",
    "loss", "losses", "layoffs", "investigation",
)

# --- Fuentes de datos combinadas (ademas de Yahoo Finance) --------------
#
# SEC EDGAR (gratis, oficial, sin API key): fuente PRIMARIA de insider
# buying para acciones que reportan a la SEC (EEUU). Solo exige identificarse
# con un User-Agent descriptivo (politica de uso justo de la SEC); si no se
# configura, se usa un valor generico que funciona pero es mejor personalizar.
# No amplia cobertura a mercados fuera de EEUU (esos simplemente no estan en
# el mapa de tickers de la SEC), solo hace mas fiable el dato para EEUU en
# vez de depender de que yfinance lo raspe correctamente de Yahoo.
SEC_EDGAR_USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT", "Stock-Screener contacto-no-configurado@example.com"
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{primary_doc}"

# Financial Modeling Prep (FMP): respaldo OPCIONAL (requiere API key propia,
# hay plan gratis con 250 peticiones/dia) usado solo cuando Yahoo Finance no
# tiene suficiente cobertura de analistas para calcular crecimiento o
# recomendacion (tipico en small/micro caps). Si no se configura
# FMP_API_KEY, el informe funciona igual que antes, simplemente sin este
# respaldo.
FMP_API_KEY = os.environ.get("FMP_API_KEY")
FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"


@lru_cache(maxsize=1)
def _load_edgar_cik_map() -> dict[str, str]:
    """Ticker (mayusculas) -> CIK de 10 digitos segun SEC EDGAR. Se descarga
    una sola vez por ejecucion (el fichero es grande y no cambia en minutos)."""
    try:
        resp = requests.get(
            SEC_TICKERS_URL, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    return {entry["ticker"].upper(): f"{entry['cik_str']:010d}" for entry in data.values()}


def _form4_has_open_market_buy(cik: str, accession: str, primary_doc: str) -> bool:
    """Parsea un Form 4 (XML) y busca una transaccion de compra en mercado
    abierto (transactionCode 'P', acquired/disposed 'A')."""
    url = SEC_ARCHIVES_URL.format(
        cik=int(cik), accession_nodash=accession.replace("-", ""), primary_doc=primary_doc
    )
    try:
        resp = requests.get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return False
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.find("./transactionCoding/transactionCode")
        acquired = tx.find("./transactionAmounts/transactionAcquiredDisposedCode/value")
        if code is not None and code.text == "P" and acquired is not None and acquired.text == "A":
            return True
    return False


def edgar_recent_insider_buy(symbol: str) -> bool | None:
    """True/False si SEC EDGAR confirma una compra de insider en mercado
    abierto en los ultimos INSIDER_LOOKBACK_DAYS; None si el ticker no esta
    en el mapa de EDGAR (no reporta a la SEC, ej. fuera de EEUU) o si la
    consulta falla, para que quien llama pueda recurrir a Yahoo como
    respaldo en vez de asumir que no hay compras."""
    cik = _load_edgar_cik_map().get(symbol.upper())
    if not cik:
        return None
    try:
        resp = requests.get(
            SEC_SUBMISSIONS_URL.format(cik=int(cik)),
            headers={"User-Agent": SEC_EDGAR_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        recent = resp.json().get("filings", {}).get("recent", {})
    except Exception:
        return None

    cutoff = datetime.now() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    for form, date_str, accession, primary_doc in zip(forms, dates, accessions, docs):
        if form != "4":
            continue
        try:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if filing_date < cutoff:
            continue
        if _form4_has_open_market_buy(cik, accession, primary_doc):
            return True
    return False


def fmp_growth_and_coverage(symbol: str) -> tuple[float | None, int | None]:
    """Crecimiento interanual de EPS y numero de analistas via Financial
    Modeling Prep, solo si hay FMP_API_KEY configurada. Respaldo para cuando
    Yahoo Finance no tiene cobertura suficiente (None) para calcularlo."""
    if not FMP_API_KEY:
        return None, None
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/analyst-estimates/{symbol}",
            params={"period": "annual", "limit": 2, "apikey": FMP_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        estimates = resp.json()
    except Exception:
        return None, None
    if not isinstance(estimates, list) or len(estimates) < 2:
        return None, None
    current, previous = estimates[0], estimates[1]  # FMP: del mas reciente al mas antiguo
    eps_now, eps_prev = current.get("epsAvg"), previous.get("epsAvg")
    growth = None
    if eps_now is not None and eps_prev not in (None, 0):
        growth = (eps_now - eps_prev) / abs(eps_prev)
    num_analysts = (
        current.get("numberAnalystsEstimatedEps")
        or current.get("numberAnalystEstimatedEps")
        or current.get("numAnalystsEps")
    )
    return growth, num_analysts


def fmp_recommendation(symbol: str) -> str | None:
    """Consenso de analistas via FMP (notas individuales de upgrade/downgrade
    recientes), respaldo cuando Yahoo no tiene recommendationKey para el
    ticker. None si no hay FMP_API_KEY o no hay notas recientes."""
    if not FMP_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/grade/{symbol}",
            params={"limit": 10, "apikey": FMP_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        grades = resp.json()
    except Exception:
        return None
    if not isinstance(grades, list) or not grades:
        return None
    recent_grades = [g.get("newGrade", "").lower() for g in grades[:10] if g.get("newGrade")]
    if not recent_grades:
        return None
    strong = sum(1 for g in recent_grades if g in STRONG_BUY_GRADES)
    weak = sum(1 for g in recent_grades if g in {"underperform", "sell", "reduce"})
    if strong > len(recent_grades) / 2:
        return "COMPRA FUERTE"
    if weak > len(recent_grades) / 2:
        return "NO COMPRAR"
    return "COMPRA NEUTRAL"


def load_watchlist() -> list[str]:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return [
            line.strip().upper()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def has_recent_insider_buying(ticker: yf.Ticker) -> bool | None:
    """True/False si hay compras de insiders recientes; None si no hay dato
    en ninguna fuente. Prueba primero SEC EDGAR (fuente oficial, solo cubre
    acciones que reportan a la SEC) y si no aplica o falla, recurre a Yahoo
    Finance como respaldo. Devolver None en vez de False evita penalizar en
    el score a acciones sin este dato en ningun sitio (la mayoria fuera de
    EEUU)."""
    edgar_result = edgar_recent_insider_buy(ticker.ticker)
    if edgar_result is not None:
        return edgar_result

    try:
        df = ticker.insider_transactions
    except Exception:
        return None
    if df is None or df.empty or "Transaction" not in df or "Start Date" not in df:
        return None
    cutoff = datetime.now() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    buys = df[df["Transaction"].str.contains("Buy", case=False, na=False)]
    recent = buys[pd.to_datetime(buys["Start Date"], errors="coerce") >= cutoff]
    return not recent.empty


RECOMMENDATION_LABELS = {
    "strong_buy": "COMPRA FUERTE",
    "buy": "COMPRA FUERTE",
    "hold": "COMPRA NEUTRAL",
    "underperform": "NO COMPRAR",
    "sell": "NO COMPRAR",
}


def recommendation_label(key: str | None) -> str:
    return RECOMMENDATION_LABELS.get((key or "").lower(), "N/D")


REGION_BY_COUNTRY = {
    "United States": "EEUU", "Canada": "EEUU/Canada",
    "Germany": "Europa", "France": "Europa", "Switzerland": "Europa",
    "Netherlands": "Europa", "Spain": "Europa", "Italy": "Europa",
    "United Kingdom": "Europa", "Sweden": "Europa", "Belgium": "Europa",
    "China": "Asia/China", "Taiwan": "Asia/China", "South Korea": "Asia/China",
    "Japan": "Asia/China", "Hong Kong": "Asia/China",
}


def region_for(country: str | None) -> str:
    return REGION_BY_COUNTRY.get(country or "", "Otros")


def is_small_cap(r: dict) -> bool:
    return r["market_cap"] is not None and r["market_cap"] < SMALL_CAP_MAX


_ES_NUMBER_TABLE = str.maketrans({",": ".", ".": ","})


def fmt_es(value: float, decimals: int = 2) -> str:
    """Formatea un numero con el convenio numerico español/europeo (punto
    de millar, coma decimal: '249.500,00' en vez de '249,500.00'). Los
    precios grandes (ej. Samsung en KRW) se leian "mal escritos" con el
    formato de Python por defecto, que es el convenio anglosajon inverso."""
    return f"{value:,.{decimals}f}".translate(_ES_NUMBER_TABLE)


def fmt_pct(value: float, decimals: int = 1, signed: bool = False) -> str:
    """Porcentaje en convenio español (coma decimal), opcionalmente con
    signo +/- explicito (ej. Potencial de subida/bajada)."""
    sign = ("+" if value >= 0 else "-") if signed else ""
    return f"{sign}{fmt_es(abs(value) if signed else value, decimals)}%"


def format_market_cap(value: float | None) -> str:
    """Escala compacta del market cap en la moneda nativa del ticker (ver
    columna 'Pais'/glosario 'Cap.'). Necesita nivel T (billones/trillion):
    acciones en wones surcoreanos, yenes, etc. usan cifras mucho mayores
    que en USD/EUR para el mismo valor real, y sin este nivel el numero se
    desbordaba la columna (ej. Samsung: "1638357.6B" en vez de "1638,4T")."""
    if not value:
        return "n/d"
    if value >= 1_000_000_000_000:
        return f"{fmt_es(value / 1_000_000_000_000, 1)}T"
    if value >= 1_000_000_000:
        return f"{fmt_es(value / 1_000_000_000, 1)}B"
    return f"{fmt_es(value / 1_000_000, 0)}M"


def analyze(symbols: list[str]) -> tuple[list[dict], float | None]:
    rows = []
    for sym in symbols:
        t = yf.Ticker(sym)
        info = t.info
        pe = info.get("trailingPE")
        # "earningsGrowth" viene del modulo financialData de Yahoo Finance, el
        # mismo bloque que agrega precios objetivo y recomendaciones: es un
        # consenso de analistas (ver glosario "Crecim."), no un calculo propio.
        growth = info.get("earningsGrowth")  # fraccion, ej 0.18 = 18%
        num_analysts = info.get("numberOfAnalystOpinions")
        recommendation = recommendation_label(info.get("recommendationKey"))
        growth_source = "Yahoo" if growth is not None else None
        recommendation_source = "Yahoo" if recommendation != "N/D" else None

        # Respaldo FMP: solo se activa cuando Yahoo no tiene suficiente
        # cobertura de analistas para calcular estas cifras (tipico en
        # small/micro caps). Si FMP_API_KEY no esta configurada, estas
        # llamadas devuelven None de inmediato y no cambia nada.
        if growth is None or num_analysts is None:
            fmp_growth, fmp_analysts = fmp_growth_and_coverage(sym)
            if growth is None and fmp_growth is not None:
                growth, growth_source = fmp_growth, "FMP"
            if num_analysts is None and fmp_analysts is not None:
                num_analysts = fmp_analysts
        if recommendation == "N/D":
            fmp_rec = fmp_recommendation(sym)
            if fmp_rec is not None:
                recommendation, recommendation_source = fmp_rec, "FMP"

        peg = (pe / (growth * 100)) if pe and growth and growth > 0 else None

        # Precio actual (a fecha/hora de ESTA ejecucion, no un valor fijo) y
        # precio objetivo de consenso de analistas (mismo modulo financialData
        # que "Crecim."/"Recomendacion": ver glosario "Precio objetivo").
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        target_price = info.get("targetMeanPrice")
        currency = info.get("currency") or ""
        upside = (
            (target_price - current_price) / current_price
            if current_price and target_price else None
        )

        rows.append(
            {
                "symbol": sym,
                "sector": info.get("sector"),
                "country": info.get("country"),
                "pe": pe,
                "growth": growth,
                "growth_source": growth_source,
                "peg": peg,
                "current_price": current_price,
                "target_price": target_price,
                "currency": currency,
                "upside": upside,
                "market_cap": info.get("marketCap"),
                "num_analysts": num_analysts,
                "insider_buying": has_recent_insider_buying(t),
                "recommendation": recommendation,
                "recommendation_source": recommendation_source,
                # Señales de "calidad" (ver quality() y glosario "Calidad"):
                # rentabilidad, margen, apalancamiento y liquidez.
                "roe": info.get("returnOnEquity"),
                "operating_margin": info.get("operatingMargins"),
                "debt_to_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"),
                "description_en": (info.get("longBusinessSummary") or "")[:DESCRIPTION_MAX_CHARS],
            }
        )

    # P/E y margen operativo medios POR SECTOR: comparar una farmaceutica
    # suiza contra un semiconductor de EEUU con un unico promedio global no
    # es representativo (ni para P/E ni para margenes, que varian aun mas
    # entre sectores: un supermercado y un software no son comparables).
    pe_by_sector = defaultdict(list)
    margin_by_sector = defaultdict(list)
    for r in rows:
        if r["pe"]:
            pe_by_sector[r["sector"]].append(r["pe"])
        if r["operating_margin"] is not None:
            margin_by_sector[r["sector"]].append(r["operating_margin"])
    sector_avg_pe = {sector: sum(vals) / len(vals) for sector, vals in pe_by_sector.items()}
    sector_avg_margin = {sector: sum(vals) / len(vals) for sector, vals in margin_by_sector.items()}

    valid_pe = [r["pe"] for r in rows if r["pe"]]
    global_avg_pe = sum(valid_pe) / len(valid_pe) if valid_pe else None
    valid_margin = [r["operating_margin"] for r in rows if r["operating_margin"] is not None]
    global_avg_margin = sum(valid_margin) / len(valid_margin) if valid_margin else None
    for r in rows:
        r["sector_avg_pe"] = sector_avg_pe.get(r["sector"], global_avg_pe)
        r["sector_avg_margin"] = sector_avg_margin.get(r["sector"], global_avg_margin)

    return rows, global_avg_pe


def score(r: dict) -> None:
    """Cada check vale None cuando no hay dato para evaluarlo (no aplica),
    en vez de contar como fallo. Asi una accion de un mercado donde Yahoo no
    publica insider trading (la mayoria fuera de EEUU) no queda penalizada
    frente a una accion estadounidense por un dato que nunca podra tener."""
    avg_pe = r.get("sector_avg_pe")
    checks = {
        "pe_bajo": None if avg_pe is None or r["pe"] is None else r["pe"] < avg_pe,
        "peg_bueno": None if r["peg"] is None else r["peg"] < PEG_MAX,
        "crecimiento": None if r["growth"] is None else r["growth"] > EARNINGS_GROWTH_MIN,
        "insider_buying": r["insider_buying"],
    }
    r["checks"] = checks
    applicable = [v for v in checks.values() if v is not None]
    r["checks_applicable"] = len(applicable)
    r["score"] = sum(applicable)
    r["score_ratio"] = (r["score"] / len(applicable)) if applicable else 0.0


def quality(r: dict) -> None:
    """Score de 'calidad' (version simplificada del Piotroski F-Score):
    rentabilidad (ROE), margen operativo vs sector, apalancamiento y
    liquidez. La evidencia academica (Piotroski 1976-1996 y estudios
    posteriores) muestra que la calidad funciona sobre todo como FILTRO
    dentro de acciones ya baratas, no como señal aislada — por eso aqui se
    usa como desempate DESPUES del score de valor/crecimiento en
    rank_top(), no mezclada en el mismo numero. Mismo criterio que score():
    None si no hay dato, no penaliza."""
    avg_margin = r.get("sector_avg_margin")
    checks = {
        "roe_bueno": None if r["roe"] is None else r["roe"] > ROE_MIN,
        "margen_bueno": (
            None if avg_margin is None or r["operating_margin"] is None
            else r["operating_margin"] > avg_margin
        ),
        "deuda_baja": None if r["debt_to_equity"] is None else r["debt_to_equity"] < DEBT_EQUITY_MAX,
        "liquidez_buena": None if r["current_ratio"] is None else r["current_ratio"] > CURRENT_RATIO_MIN,
    }
    r["quality_checks"] = checks
    applicable = [v for v in checks.values() if v is not None]
    r["quality_applicable"] = len(applicable)
    r["quality_score"] = sum(applicable)
    r["quality_ratio"] = (r["quality_score"] / len(applicable)) if applicable else 0.0


def rank_top(rows: list[dict], n: int = TOP_N) -> list[dict]:
    for r in rows:
        score(r)
        quality(r)

    def sort_key(r: dict) -> tuple:
        # Desempate deliberado: NO usar el orden del watchlist.txt (que
        # empieza por EEUU) como criterio implicito via sorted() estable,
        # eso favorecia sistematicamente a las primeras acciones de la lista.
        # La calidad desempata DESPUES del score principal (ver quality()).
        peg = r["peg"] if r["peg"] is not None else float("inf")
        return (-r["score_ratio"], -r["quality_ratio"], -r["score"], peg)

    ranked = sorted(rows, key=sort_key)
    return ranked[:n]


def translate(text: str, target: str = "es") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target=target).translate(text) or text
    except Exception:
        return text  # servicio de traduccion caido: mostramos el original


def crude_sentiment(text: str) -> str:
    """Heuristica por palabras clave, NO es analisis experto ni de un LLM."""
    low = text.lower()
    positive = any(w in low for w in POSITIVE_WORDS)
    negative = any(w in low for w in NEGATIVE_WORDS)
    if positive and not negative:
        return "Posible impacto positivo (heuristica)"
    if negative and not positive:
        return "Posible impacto negativo (heuristica)"
    return "Impacto incierto / mixto (heuristica)"


def get_strong_buy_banks(symbol: str, limit: int = 4) -> list[str]:
    try:
        df = yf.Ticker(symbol).upgrades_downgrades
    except Exception:
        return []
    if df is None or df.empty or "ToGrade" not in df or "Firm" not in df:
        return []
    df = df.sort_index(ascending=False)
    mask = df["ToGrade"].str.lower().isin(STRONG_BUY_GRADES)
    firms = df.loc[mask, "Firm"].dropna().unique().tolist()
    return firms[:limit]


def get_recent_news_detailed(symbol: str, limit: int = NEWS_PER_TICKER) -> list[dict]:
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    out = []
    for item in items[:limit]:
        content = item.get("content", item)  # yfinance nuevo anida en 'content'
        title = content.get("title")
        if not title:
            continue
        summary = content.get("summary") or ""
        link = (content.get("canonicalUrl") or {}).get("url", "")
        out.append(
            {
                "title_es": translate(title),
                "summary_es": translate(summary),
                "link": link,
                "sentiment": crude_sentiment(f"{title} {summary}"),
            }
        )
    return out


def enrich_top(top: list[dict]) -> list[dict]:
    """Trabajo 'caro' (traduccion, notas de bancos, noticias) solo para el
    top ya rankeado, no para toda la watchlist."""
    for o in top:
        o["description_es"] = translate(o["description_en"])
        o["strong_buy_banks"] = get_strong_buy_banks(o["symbol"])
        o["news"] = get_recent_news_detailed(o["symbol"])
    return top


def sanitize(text: str) -> str:
    """Normaliza puntuacion 'inteligente' y descarta lo que no cabe en latin-1
    (fuentes PDF core no soportan unicode completo, ni en Windows ni en el
    runner de GitHub Actions)."""
    replacements = {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFC", text)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


GLOSSARY = [
    ("Score", "Numero de criterios cumplidos sobre el total de criterios con "
              "datos disponibles para ese ticker (ej. 3/3 si a esa accion "
              "solo le aplican 3 de los 4 criterios). El divisor puede "
              "variar entre acciones: no todos los mercados publican los "
              "mismos datos (ej. insider buying, ver mas abajo), asi que un "
              "3/3 y un 3/4 no son directamente comparables en puntos "
              "brutos, solo en proporcion de aciertos."),
    ("P/E", "Precio / Beneficio por accion (trailing). Cuantas veces el "
            "beneficio anual se paga por la accion. En esta tabla se compara "
            "contra el promedio DEL MISMO SECTOR (no el global), para no "
            "comparar por ejemplo un banco con una tecnologica. Como "
            "referencia general (varía mucho por sector): por debajo de 15 "
            "se suele considerar barato, entre 15 y 25 razonable, por "
            "encima de 25-30 caro / de alto crecimiento."),
    ("PEG", "P/E dividido por el % de crecimiento esperado de beneficios. "
            "Por debajo de 1.5 sugiere que el precio no esta sobrepagando "
            "ese crecimiento; por debajo de 1 se suele considerar barato."),
    ("Crecim.", "Crecimiento interanual esperado del beneficio por accion "
                "(EPS). Por encima del 15% se considera fuerte. "
                "De donde sale: es el campo 'earningsGrowth' del modulo "
                "financialData de Yahoo Finance, el mismo bloque de datos "
                "que agrega los precios objetivo y la recomendacion de "
                "analistas (ver 'Recomendacion' y 'Bancos' mas abajo). No "
                "es un calculo propio de este informe ni un dato verificado "
                "de forma independiente: es un CONSENSO construido por "
                "Yahoo a partir de las proyecciones de los analistas que "
                "cubren esa accion. Por eso depende directamente de cuantos "
                "analistas la cubran (ver columna/glosario '# Analistas'): "
                "con muchos analistas (grandes tecnologicas de EEUU) suele "
                "ser una cifra robusta y actualizada; con pocos o ningun "
                "analista (tipico en small/micro caps o acciones poco "
                "seguidas fuera de EEUU) puede estar desactualizada, basada "
                "en una sola estimacion, o directamente no existir (n/a). "
                "Si Yahoo no tenia cobertura suficiente, este informe intenta "
                "rellenarlo con Financial Modeling Prep (FMP) como respaldo "
                "opcional; en ese caso se marca '(via FMP)' junto a la cifra "
                "en la seccion de descripcion detallada. "
                "HORIZONTE TEMPORAL (importante, no es un reloj de 12 meses "
                "desde hoy): esta cifra compara el AÑO FISCAL de la empresa "
                "actual/proximo contra su año fiscal anterior. El año fiscal "
                "de una empresa NO tiene por que coincidir con el año "
                "natural: por ejemplo, el de Apple termina en septiembre, no "
                "en diciembre. Asi que 'crecimiento interanual' compara el "
                "año fiscal de ESA empresa consigo mismo el año fiscal "
                "anterior, sea cual sea su calendario — no necesariamente "
                "'2026 vs 2025' en sentido de año natural. Aviso: ni Yahoo "
                "ni yfinance documentan publicamente el detalle exacto de "
                "este campo concreto, asi que esto es la convencion mas "
                "probable, no una certeza verificada al 100%. Un 60% de "
                "crecimiento no significa lo mismo a 1 año fiscal que a 10: "
                "trata esta cifra siempre como una estimacion a ~1 año "
                "fiscal, nunca plurianual."),
    ("Insider buy", "Si algun directivo o accionista relevante compro "
                     "acciones con su propio dinero en los ultimos 90 dias. "
                     "Fuente primaria: SEC EDGAR (comunicados Form 4 "
                     "oficiales, gratis, solo cubre acciones que reportan a "
                     "la SEC); si EDGAR no tiene el ticker o falla la "
                     "consulta, se recurre a Yahoo Finance como respaldo. "
                     "N/D significa que ninguna de las dos fuentes tiene el "
                     "dato para ese ticker, algo habitual fuera de EEUU "
                     "(esas empresas no presentan Form 4); en ese caso el "
                     "criterio no cuenta ni a favor ni en contra en el "
                     "score."),
    ("Recomendacion", "Consenso agregado de analistas de bancos y brokers "
                       "que cubren la accion (Yahoo Finance recopila estas "
                       "notas; si Yahoo no tiene cobertura, se intenta un "
                       "respaldo opcional via Financial Modeling Prep, "
                       "marcado como '(via FMP)'). COMPRA FUERTE = mayoria "
                       "buy/strong buy, COMPRA NEUTRAL = mayoria hold, NO "
                       "COMPRAR = mayoria underperform/sell, N/D = sin "
                       "cobertura suficiente en ninguna fuente."),
    ("Bancos", "Firmas de analisis (bancos de inversion, brokers) cuya nota "
               "mas reciente sobre la accion fue de compra/sobreponderar. "
               "Fuente: notas de upgrade/downgrade recopiladas por Yahoo "
               "Finance, no son recomendaciones propias."),
    ("Sentimiento noticia", "Etiqueta automatica por palabras clave sobre el "
                             "titular+resumen de cada noticia. Es una "
                             "heuristica simple, NO un analisis experto ni "
                             "generado por IA: leela como orientacion, no "
                             "como veredicto."),
    ("Precio", "Precio actual de la accion en el momento en que se genero "
               "ESTE informe (no un valor fijo ni anual): el screener corre "
               "varias veces al dia (ver workflows de GitHub Actions) y cada "
               "vez consulta precios en vivo. En cada moneda local del "
               "ticker (ver columna 'Pais' para contexto: USD para EEUU, "
               "EUR para Europa, KRW para Samsung, etc.), no convertido a "
               "una divisa comun, asi que no compares precios en bruto "
               "entre acciones de paises distintos."),
    ("P.Objetivo", "Precio objetivo medio segun el consenso de analistas "
                   "(campo targetMeanPrice de Yahoo Finance, mismo modulo "
                   "que 'Crecim.' y 'Recomendacion'). Igual que esos "
                   "campos, depende de la cobertura de analistas: mas "
                   "fiable con mucha cobertura, mas ruidoso o ausente (n/d) "
                   "con poca. NO es una prediccion propia de este informe. "
                   "HORIZONTE TEMPORAL (importante, NO es 'hasta el 31 de "
                   "diciembre' ni un reloj que arranca el dia de este "
                   "informe): por convencion de Wall Street, un 'price "
                   "target' es a ~12 MESES desde que ESE analista publico "
                   "su nota — no desde hoy. Yahoo agrega los targets de "
                   "varios analistas que publicaron sus notas en fechas "
                   "distintas (uno hace 2 semanas, otro hace 3 meses), asi "
                   "que 'P.Objetivo' es una media de estimaciones a ~12 "
                   "meses desde momentos ligeramente distintos, no un plazo "
                   "fijo idéntico para todas. En cualquier caso, nunca es "
                   "una proyeccion a 5 o 10 años."),
    ("Potencial", "Diferencia porcentual entre 'P.Objetivo' y 'Precio': "
                  "cuanto subiria (o bajaria) la accion si alcanzase el "
                  "precio objetivo de consenso EN ~12 MESES (ver horizonte "
                  "temporal en 'P.Objetivo'). Positivo no garantiza subida "
                  "real, es solo la distancia a la expectativa actual de "
                  "los analistas a un año vista, con las mismas "
                  "limitaciones de cobertura que 'P.Objetivo'."),
    ("Cap.", "Capitalizacion bursatil (precio de la accion x numero "
                      "de acciones en circulacion), segun Yahoo Finance. Se "
                      "usa para clasificar una accion como 'pequeña "
                      "capitalizacion' en la seccion 2 de este informe "
                      f"(por debajo de {SMALL_CAP_MAX / 1_000_000_000:.0f}.000 "
                      "millones de USD)."),
    ("# Analistas", "Numero de analistas de bancos/brokers que Yahoo Finance "
                     "contabiliza cubriendo esa accion (campo "
                     "numberOfAnalystOpinions). Cuantos menos analistas, "
                     "menos fiables son 'Crecim.' y 'Recomendacion': se "
                     "basan en menos opiniones y se actualizan con menos "
                     "frecuencia. n/d = Yahoo no reporta cobertura para ese "
                     "ticker."),
    ("Calidad", "Version simplificada del Piotroski F-Score: suma 4 señales "
                "de solidez financiera (rentabilidad, margen, apalancamiento "
                "y liquidez, detalladas en 'ROE', 'Margen operativo', "
                "'Deuda/Patrimonio' y 'Liquidez' mas abajo). Igual que "
                "'Score', se muestra como aciertos/aplicables porque no "
                "todas las acciones tienen los 4 datos disponibles. "
                "Evidencia: el estudio original de Piotroski (1976-1996) "
                "encontro que las acciones con F-Score alto batieron a las "
                "de F-Score bajo en, de media, unos 23 puntos porcentuales "
                "al año — PERO ese estudio aplicaba el F-Score solo a "
                "acciones YA baratas (value), no a todo el mercado; usado "
                "solo, el efecto es mucho mas debil. Por eso en este informe "
                "la Calidad NO se mezcla con el Score principal: se usa como "
                "criterio de DESEMPATE despues de 'Score' (ver rank_top en "
                "el codigo), asi refuerza el ranking de valor/crecimiento en "
                "vez de sustituirlo. Ademas, ningun factor de este tipo "
                "garantiza rendimiento futuro: su efecto historico varia "
                "por ciclo de mercado y tiende a debilitarse con el tiempo."),
    ("ROE", "Return on Equity (retorno sobre el patrimonio neto): beneficio "
            "neto dividido entre el patrimonio de los accionistas. Mide que "
            "tan eficiente es la empresa generando beneficio con el capital "
            "que ya tiene, sin depender de mas deuda o mas emision de "
            "acciones. Por encima del 15% se considera bueno en este "
            "informe. Fuente: Yahoo Finance (financialData)."),
    ("Margen operativo", "Beneficio operativo dividido entre ingresos: que "
                         "parte de cada venta se convierte en beneficio "
                         "antes de intereses e impuestos. Varia mucho por "
                         "sector (un supermercado y una empresa de software "
                         "no son comparables), por eso se compara contra la "
                         "media DEL MISMO SECTOR, igual que el P/E. Fuente: "
                         "Yahoo Finance (financialData)."),
    ("Deuda/Patrimonio", "Deuda total dividida entre el patrimonio neto, en "
                         "porcentaje (100 = la empresa debe tanto como vale "
                         "su patrimonio). Por debajo de 100 se considera "
                         "apalancamiento conservador en este informe: menos "
                         "riesgo de que una subida de tipos de interes o una "
                         "mala racha ahogue a la empresa. Fuente: Yahoo "
                         "Finance (financialData)."),
    ("Liquidez", "Current ratio: activo corriente dividido entre pasivo "
                 "corriente, es decir cuantas veces puede la empresa cubrir "
                 "sus deudas de corto plazo con lo que tiene a mano. Por "
                 "encima de 1.5 se considera comodo en este informe; por "
                 "debajo de 1 significa que el activo corriente no llega a "
                 "cubrir el pasivo corriente. Fuente: Yahoo Finance "
                 "(financialData)."),
    ("Cesta Trump trade", "IMPORTANTE: esta cesta NO es el patrimonio "
                     "personal de Donald Trump ni sale de ningun informe de "
                     "activos declarado (esos informes publicos, cuando "
                     "existen, son sobre todo inmuebles y negocios privados, "
                     "no acciones cotizadas). Es una seleccion tematica de "
                     "acciones que la prensa financiera (Goldman Sachs, "
                     "Kiplinger, Bloomberg, Investing.com, entre otros) "
                     "menciona repetidamente como beneficiarias o "
                     "perjudicadas por politicas de su administracion: "
                     "aranceles, gasto en defensa, desregulacion financiera, "
                     "energia, cripto e inmigracion. Son tesis especulativas "
                     "y muy sensibles a titulares y giros de politica: por "
                     "ejemplo, GEO Group subio fuerte tras la eleccion por "
                     "sus contratos de detencion con ICE y luego borro esas "
                     "subidas cuando hubo backlash publico. Que una accion "
                     "aparezca aqui no es una recomendacion de compra ni de "
                     "venta en ningun sentido, solo documenta una narrativa "
                     "de mercado."),
]

# Paleta institucional (inspirada en el formato tipico de notas de analisis
# de bancos de inversion: navy + sans-serif + tablas con cabecera solida,
# en vez del estilo editorial/revista usado antes). No afiliado a JPMorgan
# Chase & Co. ni a ningun banco concreto: es una interpretacion generica de
# ese lenguaje visual, no una plantilla real de ninguna entidad.
INK = (0, 0, 0)
BODY_GRAY = (90, 90, 90)
HAIRLINE = (200, 205, 212)
CANVAS_SOFT = (238, 241, 246)
WHITE = (255, 255, 255)
NAVY = (0, 47, 94)  # acento unico: kickers, enlaces, cabecera de tablas y barra de portada
# Paleta ciclica para las graficas circulares (seccion "Panorama de mercado"):
# navy + el naranja del logo + un par de tonos neutros de apoyo.
PIE_PALETTE = [NAVY, (214, 122, 44), (90, 140, 130), (170, 170, 170), (190, 150, 60), (150, 90, 90)]


def pe_verdict(pe: float | None) -> str:
    if pe is None:
        return "n/d"
    if pe < 15:
        return "barato (ref. general)"
    if pe <= 25:
        return "razonable (ref. general)"
    return "caro / alto crecimiento (ref. general)"


SEF_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "sef_logo.png")


class ReportPDF(FPDF):
    def header(self) -> None:
        """Logo + marca 'SEF-Financial' repetidos en la esquina superior de
        cada pagina. En la portada no se dibuja aqui: lleva su propio logo
        grande (ver build_pdf), igual que una carta con logo de cabecera
        pequeño en las paginas interiores pero un logo grande en la
        portada."""
        if self.page_no() == 1:
            return
        logo_size = 9
        x_logo = self.w - self.r_margin - logo_size
        y_logo = 5
        if os.path.exists(SEF_LOGO_PATH):
            self.image(SEF_LOGO_PATH, x=x_logo, y=y_logo, w=logo_size, h=logo_size)
        self.set_font("Helvetica", size=9, style="B")
        self.set_text_color(*NAVY)
        self.set_xy(x_logo - 55, y_logo + 1)
        self.cell(53, logo_size - 1, "SEF-FINANCIAL", align="R")
        self.set_text_color(*INK)
        self.set_y(self.t_margin)

    def footer(self) -> None:
        if self.page_no() == 1:
            return  # portada sin pie de pagina (es la unica pagina "de cubierta")
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(*BODY_GRAY)
        self.cell(0, 8, sanitize(f"Pagina {self.page_no()} de {{nb}}"), align="C")


def section_header(pdf: FPDF, kicker: str, title: str) -> None:
    """Cabecera integrada con el cuerpo (kicker gris pequeño + titulo negro
    en negrita, sin regla ni bloque de color separandolo del texto que
    sigue): el color (navy) se reserva para el logo y los enlaces, no se
    reparte por toda la maqueta, y el titulo queda pegado al parrafo
    siguiente en vez de flotar como un bloque aparte."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=8, style="B")
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(0, 5, sanitize(kicker.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", size=17, style="B")
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 9, sanitize(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.ln(4)


SUMMARY_HEADERS = ["#", "Ticker", "Precio", "P.Objetivo", "Potencial", "Pais", "Sector", "Cap.", "# Analistas", "Score", "Calidad", "P/E", "PEG", "Crecim.", "Insider buy", "Recomendacion"]
SUMMARY_LINK_COLS = {"Precio", "P.Objetivo", "Potencial", "Cap.", "# Analistas", "Score", "Calidad", "P/E", "PEG", "Crecim.", "Insider buy", "Recomendacion"}
SUMMARY_WIDTHS = (7, 16, 15, 18, 16, 20, 24, 14, 14, 12, 14, 11, 11, 13, 17, 24)
# Numeros a la derecha (mas facil comparar cifras de un vistazo), texto a la
# izquierda; "Insider buy" centrado por ser un valor corto (Si/No/N/D).
SUMMARY_ALIGN = ["R", "L", "R", "R", "R", "L", "L", "R", "R", "R", "R", "R", "R", "R", "C", "L"]


def make_donut_chart(data: dict[str, int], size: int = 400, hole_ratio: float = 0.55):
    """Grafica circular (donut) en PIL a partir de un Counter/dict
    etiqueta->cantidad. Se devuelve una imagen en memoria (fpdf2 acepta
    objetos PIL directamente, sin escribir a disco)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    total = sum(data.values()) or 1
    start = -90.0
    for i, (label, value) in enumerate(data.items()):
        extent = 360.0 * value / total
        if extent > 0:
            color = PIE_PALETTE[i % len(PIE_PALETTE)]
            d.pieslice([2, 2, size - 2, size - 2], start, start + extent, fill=(*color, 255))
        start += extent
    hole = size * hole_ratio
    off = (size - hole) / 2
    d.ellipse([off, off, off + hole, off + hole], fill=(255, 255, 255, 255))
    return img


def render_pie_block(pdf: FPDF, x: float, width: float, y: float, title: str, data: dict[str, int]) -> None:
    """Dibuja una grafica circular con su leyenda (color + etiqueta + %)
    dentro de una columna de ancho 'width', empezando en (x, y)."""
    diameter = 40
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", size=10, style="B")
    pdf.set_text_color(*INK)
    pdf.multi_cell(width, 5, sanitize(title), align="L")

    total = sum(data.values()) or 1
    chart_x = x + (width - diameter) / 2
    chart_y = pdf.get_y() + 2
    if total and data:
        img = make_donut_chart(data)
        pdf.image(img, x=chart_x, y=chart_y, w=diameter, h=diameter)

    legend_y = chart_y + diameter + 4
    pdf.set_font("Helvetica", size=8)
    for i, (label, value) in enumerate(data.items()):
        color = PIE_PALETTE[i % len(PIE_PALETTE)]
        pct = fmt_es(100 * value / total, 1)
        pdf.set_xy(x, legend_y)
        pdf.set_fill_color(*color)
        pdf.rect(x, legend_y + 1, 3, 3, style="F")
        pdf.set_text_color(*INK)
        pdf.set_x(x + 5)
        pdf.cell(width - 5, 4.5, sanitize(f"{label}: {value} ({pct}%)"))
        legend_y += 4.5
    pdf.set_text_color(*INK)


def render_summary_table(pdf: FPDF, entries: list[dict], glossary_links: dict) -> None:
    """Tabla neutra (cabecera gris muy claro, texto negro): el navy se
    reserva para el logo y los enlaces, no se reparte por toda la tabla.
    Filas con zebra gris-azulado suave y lineas finas horizontales —
    mismo tratamiento en las 3 tablas del informe."""
    from fpdf.fonts import FontFace

    pdf.set_fill_color(255, 255, 255)  # ver nota en section_header sobre fill_color heredado
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_font("Helvetica", size=8)
    headings_style = FontFace(emphasis="B", color=INK, fill_color=CANVAS_SOFT)
    with pdf.table(
        col_widths=SUMMARY_WIDTHS,
        text_align=SUMMARY_ALIGN,
        headings_style=headings_style,
        cell_fill_color=CANVAS_SOFT,
        cell_fill_mode="EVEN_ROWS",
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        row = table.row()
        for h in SUMMARY_HEADERS:
            row.cell(h, link=glossary_links[h] if h in SUMMARY_LINK_COLS else None)
        for i, o in enumerate(entries, start=1):
            row = table.row()
            row.cell(str(i))
            row.cell(o["symbol"])
            row.cell(fmt_es(o["current_price"]) if o["current_price"] else "n/d")
            row.cell(fmt_es(o["target_price"]) if o["target_price"] else "n/d")
            row.cell(fmt_pct(o["upside"] * 100, signed=True) if o["upside"] is not None else "n/d")
            row.cell(sanitize(o["country"] or "n/a"))
            row.cell(sanitize(o["sector"] or "n/a"))
            row.cell(format_market_cap(o["market_cap"]))
            row.cell(str(o["num_analysts"]) if o["num_analysts"] else "n/d")
            row.cell(f"{o['score']}/{o['checks_applicable']}")
            row.cell(f"{o['quality_score']}/{o['quality_applicable']}")
            row.cell(fmt_es(o["pe"], 1) if o["pe"] else "n/d")
            row.cell(fmt_es(o["peg"], 2) if o["peg"] else "n/d")
            row.cell(fmt_pct(o["growth"] * 100) if o["growth"] else "n/d")
            insider = o["insider_buying"]
            row.cell("N/D" if insider is None else ("Si" if insider else "No"))
            row.cell(o["recommendation"])


def render_detailed_descriptions(pdf: FPDF, entries: list[dict], glossary_links: dict, theme_map: dict | None = None) -> None:
    """Ficha detallada por accion (precio/objetivo, P/E, crecimiento,
    calidad, bancos y descripcion). Se usa en las 3 secciones con tabla
    (principal, small caps, Trump trade), justo despues de su tabla resumen."""
    for i, o in enumerate(entries, start=1):
        pdf.set_font("Helvetica", size=12, style="B")
        pdf.set_x(pdf.l_margin)
        header = f"{i}. {o['symbol']} ({o['sector'] or 'n/a'}, {o['country'] or 'n/a'})"
        theme = theme_map.get(o["symbol"], "") if theme_map else ""
        if theme:
            header += f" - {theme}"
        pdf.cell(0, 8, sanitize(header), new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(*NAVY)  # lineas con enlace al glosario
        pdf.set_x(pdf.l_margin)
        price_txt = f"{fmt_es(o['current_price'])} {o['currency']}" if o["current_price"] else "n/d"
        target_txt = f"{fmt_es(o['target_price'])} {o['currency']}" if o["target_price"] else "n/d"
        upside_txt = f" ({fmt_pct(o['upside'] * 100, signed=True)})" if o["upside"] is not None else ""
        pdf.cell(
            0, 6,
            sanitize(f"Precio actual (a fecha de este informe): {price_txt} | Precio objetivo a ~12 meses (consenso analistas): {target_txt}{upside_txt}"),
            link=glossary_links["Precio"], new_x="LMARGIN", new_y="NEXT",
        )

        pdf.set_x(pdf.l_margin)
        pe_txt = fmt_es(o["pe"], 1) if o["pe"] else "n/d"
        sector_avg_txt = fmt_es(o["sector_avg_pe"], 1) if o.get("sector_avg_pe") else "n/d"
        pdf.cell(
            0, 6,
            sanitize(f"P/E: {pe_txt} -> {pe_verdict(o['pe'])} | media del sector ({o['sector'] or 'n/a'}): {sector_avg_txt}"),
            link=glossary_links["P/E"], new_x="LMARGIN", new_y="NEXT",
        )

        # Nota de procedencia: si Yahoo no tenia el dato y se relleno con FMP,
        # se marca explicitamente (ver glosario "Crecim." / "Recomendacion").
        growth_txt = fmt_pct(o["growth"] * 100) if o["growth"] else "n/d"
        growth_note = " (via FMP)" if o.get("growth_source") == "FMP" else ""
        rec_note = " (via FMP)" if o.get("recommendation_source") == "FMP" else ""
        pdf.set_x(pdf.l_margin)
        pdf.cell(
            0, 6,
            sanitize(f"Crecim.: {growth_txt}{growth_note} | Recomendacion: {o['recommendation']}{rec_note}"),
            link=glossary_links["Crecim."], new_x="LMARGIN", new_y="NEXT",
        )

        roe_txt = fmt_pct(o["roe"] * 100) if o["roe"] is not None else "n/d"
        margin_txt = fmt_pct(o["operating_margin"] * 100) if o["operating_margin"] is not None else "n/d"
        sector_margin_txt = fmt_pct(o["sector_avg_margin"] * 100) if o.get("sector_avg_margin") else "n/d"
        debt_txt = fmt_es(o["debt_to_equity"], 0) if o["debt_to_equity"] is not None else "n/d"
        liquidity_txt = fmt_es(o["current_ratio"], 2) if o["current_ratio"] is not None else "n/d"
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            pdf.epw, 6,
            sanitize(
                f"Calidad {o['quality_score']}/{o['quality_applicable']}: ROE {roe_txt} | "
                f"margen operativo {margin_txt} (sector: {sector_margin_txt}) | "
                f"deuda/patrimonio {debt_txt} | liquidez {liquidity_txt}"
            ),
            link=glossary_links["Calidad"], align="L",
        )

        banks = o.get("strong_buy_banks") or []
        banks_txt = ", ".join(banks) if banks else "sin nota de compra fuerte reciente"
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Bancos/entidades con compra fuerte:", link=glossary_links["Bancos"])
        pdf.ln(6)
        pdf.set_text_color(*INK)  # fin de las lineas con enlace, vuelve el texto normal
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, sanitize(banks_txt), align="L")

        pdf.set_x(pdf.l_margin)
        description = o.get("description_es") or "Sin descripcion disponible."
        pdf.multi_cell(pdf.epw, 5, sanitize(description), align="L")
        pdf.ln(4)


def render_toc(pdf: FPDF, outline) -> None:
    # insert_toc_placeholder restaura la Y guardada al momento de reservar la
    # pagina, pero no la X: sin este set_x, el titulo hereda la posicion X
    # donde quedo el cursor tras la ULTIMA pagina del documento (normalmente
    # cerca del margen derecho) y sale cortado en la esquina.
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=22, style="B")
    pdf.set_text_color(*INK)
    pdf.cell(0, 13, "Indice", new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y() + 1
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(8)
    pdf.set_font("Helvetica", size=12)
    for i, section in enumerate(outline, start=1):
        link = pdf.add_link(page=section.page_number)
        indent = "    " * section.level
        pdf.set_x(pdf.l_margin)
        pdf.set_text_color(*NAVY)
        pdf.cell(
            0, 10,
            sanitize(f"{indent}{i}. {section.name}  ...  pag. {section.page_number}"),
            new_x="LMARGIN", new_y="NEXT", link=link,
        )
    pdf.set_text_color(*INK)


def build_pdf(top: list[dict], top_small: list[dict], top_trump: list[dict], rows: list[dict], avg_pe: float | None) -> str:
    avg_txt = fmt_es(avg_pe, 1) if avg_pe else "n/d"
    coverage = Counter(region_for(r["country"]) for r in rows)
    coverage_txt = " - ".join(f"{region}: {n}" for region, n in coverage.most_common())
    top_coverage = Counter(region_for(o["country"]) for o in top)
    top_coverage_txt = " - ".join(f"{region}: {n}" for region, n in top_coverage.most_common())
    n_small_cap = sum(1 for r in rows if is_small_cap(r))

    pdf = ReportPDF(orientation="L", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=15)
    # Margenes iguales a ambos lados (antes ~10mm, los minimos de fpdf): las
    # tablas ya se centran solas al ser l_margin == r_margin (fpdf2 escala
    # col_widths para llenar el ancho impreso disponible, no son mm fijos),
    # pero con 10mm el contenido llegaba casi al borde de la pagina A4
    # apaisada. 18mm da un marco mas comodo sin estrechar tanto las columnas
    # como para que las cabeceras cortas empiecen a partirse en varias lineas.
    pdf.set_margins(left=18, top=10, right=18)

    # Enlaces internos del glosario (P/E, PEG, etc. -> definicion). fpdf
    # exige pagina asignada desde ya; se corrigen al final del todo.
    glossary_links = {name: pdf.add_link(page=1) for name, _ in GLOSSARY}

    # --- Portada: al estilo "carta anual" (logo arriba a la izquierda,
    # titular grande, credito + logo pequeño abajo), a peticion del usuario
    # tras enseñar la portada de la carta de Buffett editada por ING/Value
    # School como referencia. NO es una plantilla real de JPMorgan, ING ni
    # de ningun otro banco: ver clausula de no afiliacion en la pagina
    # siguiente. Logo grande solo aqui (paginas interiores llevan la
    # version pequeña via header()).
    pdf.add_page()
    if os.path.exists(SEF_LOGO_PATH):
        pdf.image(SEF_LOGO_PATH, x=pdf.l_margin, y=12, w=20, h=20)

    pdf.set_y(95)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=34, style="B")
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 14, "Informe de acciones", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 22)
    pdf.cell(0, 14, "para tu watchlist", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, sanitize(f"ANALISIS AUTOMATIZADO DE MERCADOS - {datetime.now():%Y}"), new_x="LMARGIN", new_y="NEXT")

    # Credito + logo pequeño abajo a la izquierda (estilo "Edicion a cargo de").
    pdf.set_y(-45)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(0, 6, "Informe realizado por", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    if os.path.exists(SEF_LOGO_PATH):
        pdf.image(SEF_LOGO_PATH, x=pdf.l_margin, y=pdf.get_y(), w=8, h=8)
    pdf.set_xy(pdf.l_margin + 10, pdf.get_y() + 1)
    pdf.set_font("Helvetica", size=11, style="B")
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 6, "SEF-FINANCIAL", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 10)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(0, 5, sanitize(f"{datetime.now():%d/%m/%Y a las %H:%M}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)

    # --- Indice (pagina reservada, se rellena sola al final) ---
    pdf.add_page()
    pdf.insert_toc_placeholder(render_toc)

    # --- Seccion 1: Panorama de mercado (graficas circulares + aviso legal) ---
    # Sin add_page() aqui: insert_toc_placeholder ya salto a una pagina
    # nueva; añadir otra generaba una pagina en blanco de mas en cada informe.
    pdf.start_section("Panorama de mercado")
    section_header(pdf, "Seccion 1", "Panorama de mercado")
    pdf.set_font("Helvetica", size=9, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(
        0, 6,
        sanitize(f"Cobertura del analisis: {len(rows)} acciones ({n_small_cap} de pequeña capitalizacion) - P/E medio global: {avg_txt}"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*INK)
    pdf.ln(6)

    recommendation_counts: Counter = Counter(o["recommendation"] for o in top)
    col_w = pdf.epw / 3
    y0 = pdf.get_y()
    render_pie_block(pdf, pdf.l_margin, col_w - 6, y0, "Mercados - todas las acciones analizadas", dict(coverage.most_common()))
    render_pie_block(pdf, pdf.l_margin + col_w, col_w - 6, y0, f"Mercados - Top {len(top)}", dict(top_coverage.most_common()))
    render_pie_block(pdf, pdf.l_margin + 2 * col_w, col_w - 6, y0, f"Recomendacion - Top {len(top)}", dict(recommendation_counts.most_common()))
    pdf.set_y(y0 + 85)

    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=7.5)
    pdf.set_text_color(*BODY_GRAY)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        pdf.epw, 4,
        "Informe automatico basado en datos publicos (Yahoo Finance, SEC EDGAR y, "
        "opcionalmente, Financial Modeling Prep). No constituye asesoramiento "
        "financiero ni recomendacion de inversion personalizada. El diseño de este "
        "documento esta inspirado, con fines de legibilidad, en el formato habitual "
        "de una carta/nota de analisis financiero; no es una publicacion real de "
        "J.P. Morgan Chase & Co., ING, Value School ni de ninguna otra entidad "
        "financiera regulada, ni esta afiliado, respaldado o revisado por ellas.",
        align="L",
    )
    pdf.set_text_color(*INK)

    # --- Seccion 2: Tabla resumen ---
    pdf.add_page()
    pdf.start_section("Tabla resumen (Top 10)")
    section_header(pdf, "Seccion 2", "Tabla resumen (Top 10)")
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(0, 6, "Toca los encabezados de columna para saltar a la explicacion de cada variable (seccion Glosario).", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.ln(3)
    render_summary_table(pdf, top, glossary_links)
    pdf.ln(4)
    render_detailed_descriptions(pdf, top, glossary_links)

    # --- Seccion 3: Empresas de pequeña capitalizacion ---
    # add_page() antes de start_section: si no, el indice enlaza a la
    # pagina anterior (la de la tabla), no a la de esta seccion.
    pdf.add_page()
    pdf.start_section("Empresas de pequeña capitalizacion")
    section_header(pdf, "Seccion 3", f"Empresas de pequeña capitalizacion (Top {len(top_small)})")
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.multi_cell(
        pdf.epw, 5,
        sanitize(
            f"Mismos criterios de la seccion 1, aplicados solo a acciones con "
            f"capitalizacion de mercado por debajo de "
            f"{SMALL_CAP_MAX / 1_000_000_000:.0f}.000 millones de USD "
            f"({n_small_cap} de las {len(rows)} acciones analizadas). Ojo: suelen "
            f"tener mucha menos cobertura de analistas (columna # Analistas) que "
            f"las grandes tecnologicas del resto del informe, lo que hace menos "
            f"fiables el 'Crecim.' y la 'Recomendacion' (ver Glosario), y suelen "
            f"tener mayor volatilidad y menor liquidez."
        ),
        align="L",
    )
    pdf.set_text_color(*INK)
    pdf.ln(3)
    if top_small:
        render_summary_table(pdf, top_small, glossary_links)
        pdf.ln(4)
        render_detailed_descriptions(pdf, top_small, glossary_links)
    else:
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 6, "Ninguna accion de la watchlist esta por debajo del umbral de pequeña capitalizacion.", new_x="LMARGIN", new_y="NEXT")

    # --- Seccion 4: Cesta tematica "Trump trade" ---
    pdf.add_page()
    pdf.start_section("Cesta tematica 'Trump trade'")
    section_header(pdf, "Seccion 4", "Cesta tematica 'Trump trade'")
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.multi_cell(
        pdf.epw, 5,
        sanitize(
            "Esta seccion NO es el patrimonio personal de Donald Trump ni sale de "
            "ningun informe de activos declarado. Es una cesta tematica de acciones "
            "que la prensa financiera (Goldman Sachs, Kiplinger, Bloomberg, "
            "Investing.com, entre otros) asocia repetidamente con politicas de su "
            "administracion (aranceles, defensa, desregulacion, energia, cripto, "
            "inmigracion). Son tesis especulativas, sensibles a titulares y pueden "
            "revertirse de un dia para otro (ver detalle y ejemplo en el Glosario, "
            "entrada 'Cesta Trump trade'). No es una recomendacion de compra ni de "
            "venta."
        ),
        align="L",
    )
    pdf.set_text_color(*INK)
    pdf.ln(3)
    render_summary_table(pdf, top_trump, glossary_links)
    pdf.ln(4)
    render_detailed_descriptions(pdf, top_trump, glossary_links, theme_map=TRUMP_TRADE_THEMES)

    # --- Seccion 5: Noticias recientes (al final, antes del glosario) ---
    pdf.add_page()
    pdf.start_section("Noticias recientes")
    section_header(pdf, "Seccion 5", "Noticias recientes (traducidas)")
    seen_symbols = set()
    for o in top + top_small + top_trump:
        if o["symbol"] in seen_symbols:
            continue  # evita repetir noticias si un ticker sale en varias secciones
        seen_symbols.add(o["symbol"])
        news = o.get("news") or []
        if not news:
            continue
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, sanitize(o["symbol"]), new_x="LMARGIN", new_y="NEXT")
        for item in news:
            pdf.set_font("Helvetica", size=9, style="B")
            pdf.set_text_color(*NAVY)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                pdf.epw, 5, sanitize(f"- {item['title_es']}"),
                link=item["link"] or None, align="L",
            )
            pdf.set_text_color(*INK)
            if item["summary_es"]:
                pdf.set_font("Helvetica", size=9)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(pdf.epw, 5, sanitize(item["summary_es"]), align="L")
            pdf.set_font("Helvetica", size=8, style="I")
            pdf.set_text_color(*NAVY)
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 5, sanitize(item["sentiment"]), link=glossary_links["Sentimiento noticia"], new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*INK)
            pdf.ln(2)
        pdf.ln(2)

    # --- Seccion 6: Glosario (aqui aterrizan todos los hipervinculos) ---
    pdf.add_page()
    pdf.start_section("Glosario de variables")
    glossary_page = pdf.page_no()
    section_header(pdf, "Seccion 6", "Glosario de variables")
    for name, explanation in GLOSSARY:
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, sanitize(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, sanitize(explanation), align="L")
        pdf.ln(3)
    for name in glossary_links:
        pdf.set_link(glossary_links[name], page=glossary_page)

    out_path = os.path.join(os.path.dirname(__file__), "informe.pdf")
    pdf.output(out_path)
    return out_path


GENERATE_NOW_BUTTON = {
    "inline_keyboard": [[{"text": "Generar informe ahora", "callback_data": "informe"}]]
}


def send_telegram_document(path: str, caption: str) -> None:
    # .split()/"".join() quita cualquier espacio o salto de linea que se
    # haya colado al copiar el secret (frecuente al pegar desde el movil):
    # un token de Telegram nunca lleva espacios de verdad.
    token = "".join(os.environ["TELEGRAM_BOT_TOKEN"].split())
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
                "reply_markup": json.dumps(GENERATE_NOW_BUTTON),
            },
            files={"document": f},
            timeout=30,
        )
    if not resp.ok:
        print(f"Telegram respondio {resp.status_code}: {resp.text}")
    resp.raise_for_status()


def generate_and_send_report() -> None:
    symbols = load_watchlist()
    rows, avg_pe = analyze(symbols)
    top = rank_top(rows)
    small_cap_rows = [r for r in rows if is_small_cap(r)]
    top_small = rank_top(small_cap_rows, n=SMALL_CAP_TOP_N)
    trump_rows = [r for r in rows if r["symbol"] in TRUMP_TRADE_THEMES]
    top_trump = rank_top(trump_rows, n=len(trump_rows))
    top = enrich_top(top)
    top_small = enrich_top(top_small)
    top_trump = enrich_top(top_trump)
    pdf_path = build_pdf(top, top_small, top_trump, rows, avg_pe)
    print(f"PDF generado: {pdf_path}")
    send_telegram_document(pdf_path, caption=f"Screener - Top {len(top)} ({datetime.now():%d/%m/%Y})")


if __name__ == "__main__":
    generate_and_send_report()
