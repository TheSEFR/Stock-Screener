"""Screener revisado: valoración, calidad, cobertura y trazabilidad.

Uso: python screener.py --demo
     python screener.py --watchlist watchlist.txt --pdf
     python screener.py --input informes/snapshot.json
Telegram solo con --send. Ver README.md y REVISION.md.
"""
import json
import os
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

# Dependencias opcionales: --demo y --input funcionan solo con Python 3.10+.
try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    pd = yf = None
try:
    import requests
except ImportError:
    requests = None
try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False
try:
    from fpdf import FPDF
except ImportError:
    FPDF = object

import argparse
import csv
import math
import re
import statistics
import sys
import time
from pathlib import Path
from datetime import timezone
from urllib.parse import urlencode
import screener_core as core
import conviction
import alerts
import forward_test
import reconcile
import sector_models
DEEP_LIMIT = 20
HORIZON = 5
REQUIRED_RETURN = .12
HTTP = core.HttpClient()
ENABLE_TRANSLATION = False
PDF_OUTPUT_DIR = Path(__file__).parent / 'informes'


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
SMALL_CAP_TOP_N = 10

# Umbrales del filtro estricto de rank_top(): en vez de rellenar siempre hasta
# TOP_N con "las menos malas", solo entran las acciones que cumplen estos
# minimos. Si ninguna los cumple, el informe lo dice (no hay candidatas).
MIN_SCORE_RATIO = 0.75  # al menos 3 de cada 4 criterios de valor aplicables
MIN_CHECKS_APPLICABLE = 3  # con menos comprobaciones el score no es fiable
FCF_YIELD_MIN = 0.05  # flujo de caja libre / capitalizacion > 5%
NET_DEBT_EBITDA_MAX = 4.0  # deuda neta / EBITDA por encima = apalancamiento alto
MIN_AVG_DOLLAR_VOLUME = 1_000_000  # volumen medio diario (precio x acciones)

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

# SEC opcional: identidad real exigida; sin ella queda N/D.
SEC_EDGAR_USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT", ""
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{primary_doc}"

# FMP opcional: endpoint stable y acceso según el plan contratado.
FMP_API_KEY = os.environ.get("FMP_API_KEY")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"








def edgar_recent_insider_buy(symbol):
    return core.sec_insider(symbol, SEC_EDGAR_USER_AGENT, HTTP)








def load_watchlist(path=None):
    path = Path(path or WATCHLIST_FILE)
    if not path.exists():
        raise ValueError(f'No existe {path.name}. Crea un ticker por línea o usa --demo.')
    result = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        symbol = line.split('#', 1)[0].strip().upper()
        if not symbol:
            continue
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^=]{0,24}', symbol):
            raise ValueError(f'Ticker inválido: {symbol!r}')
        if symbol not in result:
            result.append(symbol)
    if not result:
        raise ValueError('La watchlist está vacía.')
    return result


def has_recent_insider_buying(ticker):
    return edgar_recent_insider_buy(ticker.ticker)


RECOMMENDATION_LABELS = {
    "strong_buy": "CF",
    "buy": "CF",
    "hold": "CN",
    "underperform": "NC",
    "sell": "NC",
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


def is_small_cap(r):
    cap = core.positive(r.get('market_cap_usd'))
    return cap is not None and cap < SMALL_CAP_MAX


_ES_NUMBER_TABLE = str.maketrans({",": ".", ".": ","})


def fmt_es(value: float, decimals: int = 2) -> str:
    """Formatea un numero con el convenio numerico español/europeo (punto
    de millar, coma decimal: '249.500,00' en vez de '249,500.00'). Los
    precios grandes (ej. Samsung en KRW) se leian "mal escritos" con el
    formato de Python por defecto, que es el convenio anglosajon inverso."""
    return f"{value:,.{decimals}f}".translate(_ES_NUMBER_TABLE)


def fmt_pct(value: float, decimals: int = 1, signed: bool = False) -> str:
    """Porcentaje en convenio español (coma decimal), opcionalmente con
    signo +/- explicito (ej. Potencial de subida/bajada). Valores extremos
    (crecimientos disparados por una base de comparacion muy baja, ej.
    Samsung con un unico analista) se redondean sin decimales: con decimal
    no caben en el ancho calibrado de la columna (ver SUMMARY_WIDTHS) y
    rompen el 'todo en una pagina' de toda la tabla por una sola fila."""
    if abs(value) >= 1000:
        decimals = 0
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


DISPLAY_NAME_MAX_CHARS = 8


def display_name(info: dict, symbol: str) -> str:
    """El ticker tal cual (AAPL, GOOGL, TSM...) para la tabla: son las
    'siglas' ya reconocibles de la accion. Unica excepcion: tickers que
    empiezan por digito (ej. '005930.KS' de Samsung en la bolsa de Corea),
    donde el codigo no dice nada y se sustituye por un nombre corto
    legible, truncado para no romper la calibracion de columnas (ver
    comentario de SUMMARY_WIDTHS)."""
    if not symbol[:1].isdigit():
        return symbol
    # longName primero: "Samsung Electronics Co., Ltd." -> "Samsung"; el
    # shortName de Yahoo suele venir pegado ("SamsungElec") y se lee peor.
    full = (info.get("longName") or info.get("shortName") or symbol).strip()
    name = re.split(r"[\s,]+", full)[0] or symbol
    if len(name) > DISPLAY_NAME_MAX_CHARS + 1:
        return name[:DISPLAY_NAME_MAX_CHARS] + "."
    return name


def shown_name(row: dict) -> str:
    """Como mostrar una accion en fichas, indice y titulos: el nombre si el
    ticker es solo un codigo numerico (Samsung), con el codigo entre
    parentesis para poder identificarla; el propio ticker en el resto."""
    name = row.get("name") or row["symbol"]
    return name if name == row["symbol"] else f"{name} ({row['symbol']})"


def fiscal_year_end(info: dict) -> str:
    # Formato aun mas corto (MM/AA, ej. "09/26"): la tabla resumen tiene
    # cada columna calibrada al milimetro exacto de su contenido (ver
    # SUMMARY_WIDTHS) y el ano completo no aporta tanto como mes + 2 digitos.
    ts = info.get("nextFiscalYearEnd") or info.get("lastFiscalYearEnd")
    if not ts:
        return "n/d"
    return datetime.fromtimestamp(ts).strftime("%m/%y")


def fcf_yield(info):
    # Compatibilidad: sin FX, solo calcular cuando ambas monedas coinciden.
    major, _ = core.quote_currency(info.get('currency'))
    cap, cash = core.positive(info.get('marketCap')), core.number(info.get('freeCashflow'))
    return cash / cap if cap and cash is not None and major and major == info.get('financialCurrency') else None


def net_debt_to_ebitda(info):
    values = [core.number(info.get(k)) for k in ('totalDebt','totalCash','ebitda')]
    debt, cash, ebitda = values
    return (debt-cash)/ebitda if all(v is not None for v in values) and ebitda > 0 else None


def avg_dollar_volume(info):
    # Compatibilidad: solo USD sin un convertidor explícito.
    price = core.positive(info.get('currentPrice')) or core.positive(info.get('regularMarketPrice'))
    volume = core.positive(info.get('averageVolume'))
    return price*volume if price and volume and info.get('currency') == 'USD' else None


USE_CACHE = False  # --resume: reutiliza lo ya descargado hoy si una ejecucion se interrumpio
CACHE_DIR = Path(__file__).parent / 'cache'


def _cache_file(symbol, now):
    return CACHE_DIR / now.date().isoformat() / (re.sub(r'[^A-Za-z0-9._-]', '_', symbol) + '.json')


def _cache_load(symbol, now):
    if not USE_CACHE:
        return None
    try:
        data = json.loads(_cache_file(symbol, now).read_text(encoding='utf-8'))
        return data if isinstance(data.get('info'), dict) and isinstance(data.get('estimates'), dict) else None
    except (OSError, ValueError):
        return None


def _cache_save(symbol, now, fetched):
    if not USE_CACHE:
        return
    path = _cache_file(symbol, now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(fetched, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temp.replace(path)  # escritura atomica: una interrupcion no deja un JSON a medias
    except (OSError, ValueError, TypeError):
        pass  # la cache es solo una optimizacion; nunca debe romper la ejecucion


def _fetch_ticker(sym, now, errors):
    """Info y estimaciones de un ticker, o None si Yahoo no da informacion."""
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info
        if not isinstance(info, dict) or not info:
            raise ValueError('Sin información')
    except Exception as exc:
        errors.append({'symbol':sym, 'stage':'info', 'error':type(exc).__name__})
        return None
    estimates = {}
    try:
        frame = ticker.earnings_estimate
        if frame is not None and all(k in frame.index for k in ('0y','+1y')):
            estimates = core.expected_growth(frame.loc['0y'].to_dict(),frame.loc['+1y'].to_dict())
            estimates['growth_source'] = 'Yahoo earnings_estimate'
    except Exception as exc:
        errors.append({'symbol':sym, 'stage':'estimates', 'error':type(exc).__name__})
    if estimates.get('growth') is None and FMP_API_KEY:
        try:
            url = 'https://financialmodelingprep.com/stable/analyst-estimates?' + urlencode(
                {'symbol':sym, 'period':'annual', 'limit':10, 'apikey':FMP_API_KEY})
            estimates = core.fmp_estimates(HTTP.get(url), now)
        except Exception as exc:
            errors.append({'symbol':sym, 'stage':'FMP', 'error':type(exc).__name__})
    return {'info': info, 'estimates': estimates}


def analyze(symbols):
    if yf is None:
        raise RuntimeError('Instala requirements.txt para consultar Yahoo. --demo funciona sin dependencias.')
    now = datetime.now(timezone.utc)
    rows, errors, snapshots = [], [], []
    rates = {'USD':1.0}
    def fx(currency):
        if currency in rates:
            return rates[currency]
        if not currency or not re.fullmatch('[A-Z]{3}', currency):
            return None
        try:
            history = yf.Ticker(f'{currency}USD=X').history(period='5d', auto_adjust=False)
            series = history['Close'].dropna()
            stamp = core.utc_date(series.index[-1].isoformat())
            rates[currency] = core.positive(series.iloc[-1]) if stamp and 0 <= (now-stamp).days <= 7 else None
        except Exception as exc:
            rates[currency] = None
            errors.append({'symbol':currency, 'stage':'fx', 'error':type(exc).__name__})
        return rates[currency]
    for sym in dict.fromkeys(symbols):
        print(f'Analizando {sym}...', flush=True)
        cached = _cache_load(sym, now)
        fetched = cached or _fetch_ticker(sym, now, errors)
        if not fetched:
            continue
        if not cached:
            _cache_save(sym, now, fetched)
        info, estimates = fetched['info'], fetched['estimates']
        row = core.normalize(sym, info, estimates, fx, now)
        row['name'] = display_name(info, sym)
        rows.append(row)
        snapshots.append({'symbol':sym, 'info':info, 'estimates':estimates})
        time.sleep(.25)
    core.add_peers(rows)
    for row in rows:
        core.evaluate(row)
    inputs={r['symbol']:r for r in snapshots}
    by_symbol={r['symbol']:r for r in rows}
    for candidate in core.rank(rows,n=DEEP_LIMIT,strict=False):
        sym=candidate['symbol']
        if candidate.get('unsupported_model'):
            continue
        try:
            ticker=yf.Ticker(sym)
            statements={'income':conviction.table_records(ticker.income_stmt),
                        'cashflow':conviction.table_records(ticker.cashflow),
                        'balance':conviction.table_records(ticker.balance_sheet)}
            inputs[sym]['statements']=statements
            by_symbol[sym]['reconciliation']=reconcile.check_symbol(
                sym,statements,candidate.get('financial_currency'),SEC_EDGAR_USER_AGENT,HTTP)
        except Exception as exc:
            errors.append({'symbol':sym,'stage':'historical_statements','error':type(exc).__name__})
    for row in rows:
        historical=conviction.historical_quality(inputs[row['symbol']].get('statements',{}),now)
        row['historical']=historical
        row['valuation']=conviction.scenarios(row,historical,rates,HORIZON,REQUIRED_RETURN)
        row['sector_model']=sector_models.evaluate(row)
        if (row.get('reconciliation') or {}).get('status')=='discrepancia':
            # Cifras que no cuadran con la SEC no pueden sostener una prioridad alta.
            valuation=row['valuation']
            valuation['conviction_reasons'].append('Discrepancia entre Yahoo y SEC en ingresos o beneficio')
            if valuation['conviction']=='prioridad_alta_para_estudio':
                valuation['conviction']='revisar_calidad'
    analyze.errors, analyze.snapshot = errors, {'as_of':now.isoformat(), 'fx_rates':rates, 'records':snapshots}
    valid = [r['pe'] for r in rows if r['pe']]
    return rows, statistics.mean(valid) if valid else None


def score(r):
    core.score(r)


def quality(r):
    core.quality(r)


def is_excluded(r):
    failures, missing = core.risk_reasons(r)
    return bool(failures or missing)


def risk_label(r):
    failures, missing = core.risk_reasons(r)
    return 'Alto' if failures else 'Revisar' if missing else 'OK'


def passes_strict_filter(r):
    return core.evaluate(r)['eligible']


def rank_top(rows, n=TOP_N, strict=True):
    return core.rank(rows, n, strict)


TRANSLATION_ERROR_MARKERS = (
    "server error", "that's an error", "please try again later",
    "that's all we know",
)


def translate(text, target='es'):
    if not ENABLE_TRANSLATION or GoogleTranslator is None or not text:
        return text or ''
    try:
        result = GoogleTranslator(source='auto', target=target).translate(text)
        if result and not any(w in result.lower() for w in TRANSLATION_ERROR_MARKERS):
            return result
    except Exception:
        pass
    return text


def crude_sentiment(text):
    # No inferimos impacto bursátil con un diccionario sin contexto/negación.
    return 'Titular informativo; impacto no evaluado'


def get_strong_buy_banks(symbol, limit=4):
    if yf is None:
        return []
    try:
        frame = yf.Ticker(symbol).upgrades_downgrades
        if frame is None or frame.empty:
            return []
        frame = frame.sort_index(ascending=False)
        # Última opinión por firma ANTES de filtrar compras: elimina upgrades revocados.
        frame = frame.dropna(subset=['Firm','ToGrade']).drop_duplicates('Firm', keep='first')
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        dates = pd.to_datetime(frame.index, utc=True, errors='coerce')
        frame = frame[dates >= cutoff]
        return frame.loc[frame['ToGrade'].str.lower().isin(STRONG_BUY_GRADES),'Firm'].tolist()[:limit]
    except Exception:
        return []


def get_recent_news_detailed(symbol, limit=NEWS_PER_TICKER):
    if yf is None:
        return []
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    for item in items:
        c = item.get('content') or item
        date = core.utc_date(c.get('pubDate') or c.get('providerPublishTime'))
        title = c.get('title')
        canonical = c.get('canonicalUrl') or {}
        link = canonical.get('url', '') if isinstance(canonical, dict) else ''
        link = link or c.get('link', '')
        if not title or title in seen or not date or not 0 <= (now-date).total_seconds() <= 7*86400:
            continue
        if not link.startswith(('https://','http://')):
            link = ''
        seen.add(title)
        out.append({'title_es':translate(title),'summary_es':translate(c.get('summary') or ''),
                    'link':link,'published_at':date.isoformat(),'sentiment':crude_sentiment(title)})
    return sorted(out,key=lambda n:n['published_at'],reverse=True)[:limit]


def enrich_top(top):
    for row in top:
        row['description_es'] = translate(row.get('description_en') or '')
        row['strong_buy_banks'] = get_strong_buy_banks(row['symbol'])
        row['news'] = get_recent_news_detailed(row['symbol'])
        row['insider_buying'] = edgar_recent_insider_buy(row['symbol'])
        row['insider_source'] = 'SEC Form 4, ventana limitada; P incluye compra privada'
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


GLOSSARY = [('Score', 'Aciertos sobre 4 criterios fijos: descuento P/E del 20% frente a mediana de otros pares, FCF yield >=5%, EPS FY+1/FY0 >=15% con estimaciones suficientes e ingresos >=5%. Un dato ausente no aumenta el score. Se exige 3/4, calidad >=2/4 y 7/8 criterios disponibles. Orden: 55% valor/crecimiento +45% calidad. Heurística sin backtest.'), ('P/E', 'Precio/beneficio trailing positivo. Referencia: mediana de al menos cinco OTRAS empresas de la misma industria y país, con fechas verificables, presentes en la watchlist. No es un benchmark sectorial exhaustivo. Sin pares suficientes queda sin dato. P/E bajo no implica infravaloración.'), ('PEG', 'P/E trailing / porcentaje de crecimiento estimado FY+1 frente a FY0. Solo orientativo: mezcla beneficio histórico y estimación futura, no es PEG plurianual estándar. No puntúa, para no duplicar crecimiento. No se calcula sobre pérdidas, crecimiento >100% ni estimaciones insuficientes.'), ('Crecim.', 'EPS medio estimado FY+1 / EPS medio FY0 -1. Yahoo earnings_estimate, filas 0y y +1y; FMP stable como respaldo ordenado por cierre fiscal. No se interpreta earningsGrowth como consenso. Se requieren EPS positivos, mínimo tres analistas en ambos periodos y rango alto-bajo <=50% del EPS medio. Fechas de actualización individuales no disponibles; confirmar con fuente primaria.'), ('Insider buy', 'Compras P de valores no derivados según Form 4 SEC, con fecha de operación dentro de 90 días. P incluye mercado abierto O compra privada: no permite afirmar compra exclusivamente en bolsa. No suma puntos ni demuestra rentabilidad. N/D incluye fallo de red, ventana incompleta y falta de User-Agent. No significa ausencia de operaciones. No reconcilia enmiendas.'), ('Rec', 'Etiqueta del consenso Yahoo, sin factor de puntuación. No se usa FMP grade como sustituto de consenso. CF/CN/NC son etiquetas del proveedor, no recomendaciones propias.'), ('CF', "= Compra Fuerte. Aparece en la columna 'Rec' cuando la mayoria de analistas recientes dan buy/strong buy sobre la accion."), ('CN', "= Compra Neutral. Aparece en la columna 'Rec' cuando la mayoria de analistas recientes dan hold (ni comprar ni vender)."), ('NC', "= No Comprar. Aparece en la columna 'Rec' cuando la mayoria de analistas recientes dan underperform/sell."), ('Bancos', 'Última nota disponible por firma, filtrada a 90 días, que conserva una calificación positiva. Una compra antigua revocada por una rebaja posterior no se incluye. La lista no es consenso completo ni factor del score.'), ('Sentimiento noticia', 'No se infiere impacto bursátil con coincidencias de palabras. Se muestran titulares fechados en los últimos siete días; contexto y efecto quedan sin evaluar.'), ('Precio', 'Último precio entregado por el proveedor, que puede tener retraso. La fecha de cotización se guarda en JSON/HTML. No se afirma que sea una cotización en tiempo real.'), ('P.OBJ', "Precio objetivo medio segun el consenso de analistas (campo targetMeanPrice de Yahoo Finance, mismo modulo que 'Crecim.' y 'Rec'). Igual que esos campos, depende de la cobertura de analistas: mas fiable con mucha cobertura, mas ruidoso o ausente (n/d) con poca. NO es una prediccion propia de este informe. HORIZONTE TEMPORAL (importante, NO es 'hasta el 31 de diciembre' ni un reloj que arranca el dia de este informe): por convencion de Wall Street, un 'price target' es a ~12 MESES desde que ESE analista publico su nota — no desde hoy. Yahoo agrega los targets de varios analistas que publicaron sus notas en fechas distintas (uno hace 2 semanas, otro hace 3 meses), asi que 'P.Objetivo' es una media de estimaciones a ~12 meses desde momentos ligeramente distintos, no un plazo fijo idéntico para todas. En cualquier caso, nunca es una proyeccion a 5 o 10 años."), ('Potencial', "Diferencia porcentual entre 'P.Objetivo' y 'Precio': cuanto subiria (o bajaria) la accion si alcanzase el precio objetivo de consenso EN ~12 MESES (ver horizonte temporal en 'P.Objetivo'). Positivo no garantiza subida real, es solo la distancia a la expectativa actual de los analistas a un año vista, con las mismas limitaciones de cobertura que 'P.Objetivo'."), ('Cap.', 'La tabla muestra capitalización en unidad principal de la moneda de cotización. Para small caps se convierte a USD; umbral 2.000 millones USD. GBP/GBp y otras subunidades se normalizan para volumen. Discrepancias frente a precio por acciones >25% se remiten a revisión (ADR/clases/unidades).'), ('Analy', 'Mínimo de analistas de EPS en FY0 y FY+1, no el número de recomendaciones bursátiles. Se exigen tres y dispersión acotada para usar crecimiento. Más analistas no garantiza acierto.'), ('Calidad', 'Cuatro reglas heurísticas, NO Piotroski F-Score: ROE >=15%, margen positivo >=mediana de pares, deuda/patrimonio entre 0 y 100%, current ratio >=1,5. Denominador fijo cuatro. No se usan estos umbrales para recomendar banca, seguros o inmobiliario: requieren otro modelo.'), ('ROE', 'Return on Equity (retorno sobre el patrimonio neto): beneficio neto dividido entre el patrimonio de los accionistas. Mide que tan eficiente es la empresa generando beneficio con el capital que ya tiene, sin depender de mas deuda o mas emision de acciones. Por encima del 15% se considera bueno en este informe. Fuente: Yahoo Finance (financialData).'), ('Margen operativo', 'Resultado operativo/ingresos. Se exige positivo y se compara con mediana de al menos cinco otros pares de industria/país en la watchlist. No hay sustitución por media mundial.'), ('Deuda/Patrimonio', 'Deuda total dividida entre el patrimonio neto, en porcentaje (100 = la empresa debe tanto como vale su patrimonio). Por debajo de 100 se considera apalancamiento conservador en este informe: menos riesgo de que una subida de tipos de interes o una mala racha ahogue a la empresa. Fuente: Yahoo Finance (financialData).'), ('Liquidez', 'Current ratio: activo corriente dividido entre pasivo corriente, es decir cuantas veces puede la empresa cubrir sus deudas de corto plazo con lo que tiene a mano. Por encima de 1.5 se considera comodo en este informe; por debajo de 1 significa que el activo corriente no llega a cubrir el pasivo corriente. Fuente: Yahoo Finance (financialData).'), ('FCL', 'FCF en moneda de los estados convertido a USD / capitalización en USD. Sin moneda o cambio verificable no se calcula. Se exige FCF positivo y >=5% suma una señal. Un periodo de caja excepcional puede engañar: validar normalización plurianual.'), ('FR', 'OK: datos esenciales presentes y verificables; Alto: riesgo medido incumple; Revisar: faltan datos, están fuera de plazo, instrumento no compatible o modelo sectorial no aplicable. Se bloquean pérdidas, EBITDA/FCF/margen/patrimonio no positivos, deuda neta/EBITDA >4 y liquidez <1 millón USD/día. Cotización <=7 días; cierre financiero <=180 días. No cubre todos los riesgos.'), ('Cesta Trump trade', 'Lista temática estática heredada del archivo original, sin validación de su vigencia política. Solo informativa: strict=False puede mostrar descartadas y datos insuficientes. No se considera selección de oportunidades ni patrimonio personal.'), ('F.Y.', 'Cierre fiscal estimado según proveedor; no es la fecha de publicación de resultados.')]

# Paleta institucional (inspirada en el formato tipico de notas de analisis
# de bancos de inversion: navy + sans-serif + tablas con cabecera solida,
# en vez del estilo editorial/revista usado antes). No afiliado a JPMorgan
# Chase & Co. ni a ningun banco concreto: es una interpretacion generica de
# ese lenguaje visual, no una plantilla real de ninguna entidad.
INK = (0, 0, 0)
BODY_GRAY = (90, 90, 90)
HAIRLINE = (200, 205, 212)
WHITE = (255, 255, 255)


def _lighten(color: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """Aclara 'color' hacia blanco en la proporcion 'amount' (0=igual, 1=blanco)."""
    return tuple(round(c + (255 - c) * amount) for c in color)


NAVY = (0, 47, 94)  # acento unico: kickers, enlaces, cabecera de tablas y barra de portada
# Paleta ciclica para las graficas circulares (seccion "Panorama de mercado"):
# navy + el naranja del logo + un par de tonos neutros de apoyo.
PIE_PALETTE = [NAVY, (214, 122, 44), (90, 140, 130), (170, 170, 170), (190, 150, 60), (150, 90, 90)]
# Fondo de pagina completa para diferenciar secciones a simple vista (ver
# ReportPDF via pdf.page_background, atributo nativo de fpdf2): escala de
# azul estrictamente decreciente (a juego con el navy, el color de acento
# del resto del informe), interpolando en linea recta entre un azul intenso
# (portada) y un azul muy suave (seccion 5/6) en pasos iguales, en vez de
# tonos elegidos a mano uno por uno.
def _section_shade(t: float) -> tuple[int, int, int]:
    r0, g0, b0 = 70, 120, 175  # t=0: azul intenso (portada)
    r1, g1, b1 = 210, 225, 238  # t=1: el mas suave (Noticias/Glosario), pero claramente azul, no blanco
    return (round(r0 + (r1 - r0) * t), round(g0 + (g1 - g0) * t), round(b0 + (b1 - b0) * t))


_SECTION_SCALE = [_section_shade(i / 6) for i in range(7)]
PORTADA_BG = _SECTION_SCALE[0]  # Portada
INDICE_BG = _SECTION_SCALE[1]  # Indice
SECTION1_BG = _SECTION_SCALE[2]  # Seccion 1: Panorama de mercado
SECTION2_BG = _SECTION_SCALE[3]  # Seccion 2: Principales acciones
SECTION3_BG = _SECTION_SCALE[4]  # Seccion 3: Empresas de pequeña capitalizacion
SECTION4_BG = _SECTION_SCALE[5]  # Seccion 4: Cesta tematica "Trump trade"
SECTION56_BG = _SECTION_SCALE[6]  # Seccion 5 y 6: Noticias / Glosario


def pe_verdict(pe: float | None) -> str:
    if pe is None:
        return "n/d"
    if pe < 15:
        return "barato (ref. general)"
    if pe <= 25:
        return "razonable (ref. general)"
    return "caro / alto crecimiento (ref. general)"


SEF_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "sef_logo.png")
# GNU FreeFont (GPLv3 + font exception, incluye cobertura arabe con tablas
# GSUB/GPOS): usada solo en la portada para la transliteracion fonetica
# "SEF-Financial" en arabe, via el motor de shaping de fpdf2 (uharfbuzz).
SEF_ARABIC_FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "FreeSerif.ttf")


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
        if getattr(self, "in_toc_rendering", False):
            # Si el indice necesita paginas extra (allow_extra_pages=True,
            # por los subpuntos de cada accion), esas paginas se crean e
            # insertan al final del documento y luego se reordenan; el
            # texto de este pie ya quedaria dibujado con el numero de
            # pagina TEMPORAL de ese momento (ej. "18 de 18") y no se
            # actualiza al recolocar la pagina, asi que se omite aqui en
            # vez de imprimir un numero incorrecto.
            return
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


# Tope del aire entre fichas de una misma hoja (ver
# render_detailed_descriptions), para las paginas con muy pocas fichas y
# mucho sobrante (si no, quedarian centradas con huecos enormes).
MAX_FICHA_SPACING = 40

# Tope de la altura de fila de las tablas resumen: con pocas filas, repartir
# TODO el alto de la hoja entre ellas daria filas desproporcionadas.
MAX_TABLE_ROW_HEIGHT = 12


SUMMARY_HEADERS = ["#", "Ticker", "Precio", "P.OBJ", "Potencial", "Pais", "Sector", "Cap.", "Analy", "Score", "Calidad", "P/E", "PEG", "Crecim.", "Insider buy", "FCL", "FR", "Rec", "F.Y."]
SUMMARY_LINK_COLS = {"Precio", "P.OBJ", "Potencial", "Cap.", "Analy", "Score", "Calidad", "P/E", "PEG", "Crecim.", "Insider buy", "FCL", "FR", "Rec", "F.Y."}
# Anchos calculados a partir del ancho REAL en mm (Helvetica 8) del texto
# mas largo que debe caber sin partirse en cada columna, mas los 2mm que
# fpdf2 reserva de margen interno de celda (c_margin = 1mm por lado).
# fpdf2 los reescala proporcionalmente para llenar el ancho imprimible, asi
# que lo que importa es la proporcion entre ellos, no el valor absoluto.
# Cada valor es el minimo que necesita esa columna: el ancho del texto mas
# largo que puede mostrar (o de su cabecera en negrita, si es mas ancha) mas
# los 2mm de margen interno. Sumados dan 246mm, asi que la tabla entra
# dentro de los margenes normales del documento (257mm imprimibles) con
# ~11mm de holgura, que fpdf2 reparte proporcionalmente.
# Con estos anchos TODA celda cabe en una sola linea, de modo que todas las
# filas miden lo mismo y su altura se puede fijar de golpe (ver line_height
# en render_summary_table). Los casos peores, que antes envolvian a dos
# lineas y hacian esas filas el doble de altas, son:
#   Pais y Sector ya NO se muestran completos (ver COUNTRY_ABBR/SECTOR_ABBR
#   mas abajo): "United States" -> "USA", "Communication Services" -> "Comm"
#   etc, asi que el peor caso de cada columna es mucho mas corto.
# Medidos con fpdf: cada valor = max(ancho cabecera negrita, ancho peor
# valor abreviado) + 2mm de margen interno de celda. Suma total 187,6mm.
# FCL (ej. "12,3%" o "-4,5%") y FR ("OK"/"Alto"/"n/d") se añadieron despues:
# 9.5 y 8.0 mm, mismo criterio (texto mas largo + 2mm de margen). Suma 205,1mm.
SUMMARY_WIDTHS = (5.1, 16.6, 12.2, 12.2, 14.6, 8.1, 15.0, 10.9, 9.7, 9.8, 12.2, 9.1, 8.0, 12.3, 17.1, 9.5, 8.0, 7.2, 9.1)
# Numeros a la derecha (mas facil comparar cifras de un vistazo), texto a la
# izquierda; "Insider buy" centrado por ser un valor corto (Si/No/N/D).
SUMMARY_ALIGN = ["R", "L", "R", "R", "R", "L", "L", "R", "R", "R", "R", "R", "R", "R", "C", "R", "C", "L", "L"]

# Pais/sector abreviados (pedido explicito: nada de nombres largos que
# obliguen a estirar la tabla o dejen huecos). Fallback: primeras 3-4
# letras en mayuscula si no esta en el diccionario.
COUNTRY_ABBR = {
    "United States": "USA", "Canada": "CAN", "Germany": "GER",
    "France": "FRA", "Switzerland": "SUI", "Netherlands": "NED",
    "Spain": "ESP", "Italy": "ITA", "United Kingdom": "UK",
    "Sweden": "SWE", "Belgium": "BEL", "China": "CHN",
    "Taiwan": "TAI", "South Korea": "KOR", "Japan": "JPN",
    "Hong Kong": "HKG",
}
SECTOR_ABBR = {
    "Technology": "Tech", "Communication Services": "Comm",
    "Consumer Cyclical": "Cons.Cyc", "Consumer Defensive": "Cons.Def",
    "Financial Services": "Finance", "Industrials": "Indus.",
    "Healthcare": "Health", "Energy": "Energy", "Utilities": "Util.",
    "Real Estate": "R.Estate", "Basic Materials": "Materials",
}


def country_abbr(country: str | None) -> str:
    if not country:
        return "n/a"
    return COUNTRY_ABBR.get(country, country[:4].upper())


def sector_abbr(sector: str | None) -> str:
    if not sector:
        return "n/a"
    return SECTOR_ABBR.get(sector, sector[:8])


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


def render_table(
    pdf: FPDF,
    headers: list[str],
    widths: tuple[float, ...],
    align: list[str],
    rows: list[list[str]],
    section_bg: tuple[int, int, int],
    link_map: dict | None = None,
    table_width: float | None = None,
    table_align: str = "CENTER",
) -> None:
    """Version generica de render_summary_table (misma logica exacta:
    zebra derivada de section_bg, cabecera negra sobre franja azul, altura
    de fila estirada a la pagina) para tablas con columnas distintas a las
    del informe principal, ej. el informe mensual/anual. Rows ya viene
    formateado a texto (sin dicts con claves especificas)."""
    from fpdf.fonts import FontFace

    table_base_bg = _lighten(section_bg, 0.6)
    table_stripe_bg = _lighten(section_bg, 0.35)
    pdf.set_fill_color(*table_base_bg)
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_font("Helvetica", size=8)
    headings_style = FontFace(emphasis="B", color=INK, fill_color=table_stripe_bg)

    n_rows = len(rows) + 1
    available_h = (pdf.h - pdf.b_margin) - pdf.get_y() - 2
    line_height = max(2 * pdf.font_size, min(available_h / n_rows, MAX_TABLE_ROW_HEIGHT))

    with pdf.table(
        col_widths=widths,
        width=table_width,
        align=table_align,
        text_align=align,
        headings_style=headings_style,
        line_height=line_height,
        cell_fill_color=table_stripe_bg,
        cell_fill_mode="EVEN_ROWS",
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        row = table.row()
        for h in headers:
            row.cell(h, link=(link_map or {}).get(h))
        for r in rows:
            row = table.row()
            for cell_val in r:
                row.cell(cell_val)


def render_summary_table(pdf: FPDF, entries: list[dict], glossary_links: dict, section_bg: tuple[int, int, int]) -> None:
    """Tabla neutra (cabecera gris muy claro, texto negro): el navy se
    reserva para el logo y los enlaces, no se reparte por toda la tabla.
    Zebra en dos tonos de azul derivados del propio fondo de la seccion
    ('section_bg', aclarado en dos proporciones distintas) en vez de un
    gris fijo: asi la tabla encaja con el tono de cada seccion en vez de
    verse como un bloque blanco pegado encima. Lineas finas horizontales
    — mismo tratamiento en las 3 tablas del informe.

    La tabla ocupa el ancho imprimible completo, alineada con los margenes
    del documento (fpdf2 reparte col_widths sobre ese ancho).

    La altura de fila (line_height) se estira para que la tabla ocupe todo
    el alto que queda de hoja en vez de quedarse como una franja fina
    arriba. Como ninguna celda envuelve a dos lineas (ver SUMMARY_WIDTHS),
    todas las filas miden exactamente line_height y el reparto es exacto."""
    from fpdf.fonts import FontFace

    table_base_bg = _lighten(section_bg, 0.6)  # franja "menos azul"
    table_stripe_bg = _lighten(section_bg, 0.35)  # franja "mas azul" (y cabecera)
    pdf.set_fill_color(*table_base_bg)  # ver nota en section_header sobre fill_color heredado
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_font("Helvetica", size=8)
    headings_style = FontFace(emphasis="B", color=INK, fill_color=table_stripe_bg)

    # +1 por la fila de cabecera. Se descuentan 2mm de aire para no rozar el
    # limite del salto de pagina automatico, y se acota entre la altura
    # normal de fpdf2 (2 * font_size) y MAX_TABLE_ROW_HEIGHT.
    n_rows = len(entries) + 1
    available_h = (pdf.h - pdf.b_margin) - pdf.get_y() - 2
    line_height = max(2 * pdf.font_size, min(available_h / n_rows, MAX_TABLE_ROW_HEIGHT))

    with pdf.table(
        col_widths=SUMMARY_WIDTHS,
        text_align=SUMMARY_ALIGN,
        headings_style=headings_style,
        line_height=line_height,
        cell_fill_color=table_stripe_bg,
        cell_fill_mode="EVEN_ROWS",
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        row = table.row()
        for h in SUMMARY_HEADERS:
            row.cell(h, link=glossary_links[h] if h in SUMMARY_LINK_COLS else None)
        for i, o in enumerate(entries, start=1):
            row = table.row()
            row.cell(str(i))
            row.cell(o["name"])
            row.cell(fmt_es(o["current_price"]) if o["current_price"] else "n/d")
            row.cell(fmt_es(o["target_price"]) if o["target_price"] else "n/d")
            row.cell(fmt_pct(o["upside"] * 100, signed=True) if o["upside"] is not None else "n/d")
            row.cell(sanitize(country_abbr(o["country"])))
            row.cell(sanitize(sector_abbr(o["sector"])))
            row.cell(format_market_cap(o["market_cap"]))
            row.cell(str(o["num_analysts"]) if o["num_analysts"] else "n/d")
            row.cell(f"{o['score']}/4")
            row.cell(f"{o['quality_score']}/4")
            row.cell(fmt_es(o["pe"], 1) if o["pe"] else "n/d")
            row.cell(fmt_es(o["peg"], 2) if o["peg"] else "n/d")
            row.cell(fmt_pct(o["growth"] * 100) if o["growth"] is not None else "n/d")
            insider = o["insider_buying"]
            row.cell("N/D" if insider is None else ("Si" if insider else "No"))
            row.cell(fmt_pct(o["fcf_yield"] * 100) if o.get("fcf_yield") is not None else "n/d")
            row.cell(risk_label(o))
            row.cell(o["recommendation"])
            row.cell(o["fiscal_year_end"])


def _render_ficha(blk: FPDF, x: float, width: float, i: int, o: dict, glossary_links: dict, section_number: int, theme: str, register_section: bool = True) -> None:
    """Dibuja el contenido de una ficha (una accion) empezando en 'x' con
    ancho 'width'. Factorizado fuera de render_detailed_descriptions para
    poder invocarlo tambien en seco (pdf.offset_rendering) y medir cuanto
    ocupa antes de pintarlo de verdad. register_section=False en esa pasada
    de medicion: si no, cada ficha entraria DOS veces en el indice."""
    if register_section:
        blk.start_section(sanitize(shown_name(o)), level=1)
    blk.set_font("Helvetica", size=12, style="B")
    blk.set_x(x)
    header = f"{section_number}.{i} {shown_name(o)} ({o['sector'] or 'n/a'}, {o['country'] or 'n/a'})"
    if theme:
        header += f" - {theme}"
    blk.cell(width, 8, sanitize(header), new_x="LEFT", new_y="NEXT")

    blk.set_font("Helvetica", size=9)
    blk.set_text_color(*NAVY)  # lineas con enlace al glosario
    blk.set_x(x)
    price_txt = f"{fmt_es(o['current_price'])} {o['currency']}" if o["current_price"] else "n/d"
    target_txt = f"{fmt_es(o['target_price'])} {o['currency']}" if o["target_price"] else "n/d"
    upside_txt = f" ({fmt_pct(o['upside'] * 100, signed=True)})" if o["upside"] is not None else ""
    blk.multi_cell(
        width, 6,
        sanitize(f"Precio de la última cotización disponible: {price_txt} | Precio objetivo a ~12 meses (consenso analistas): {target_txt}{upside_txt}"),
        link=glossary_links["Precio"], align="L",
    )

    blk.set_x(x)
    pe_txt = fmt_es(o["pe"], 1) if o["pe"] else "n/d"
    sector_avg_txt = fmt_es(o["sector_avg_pe"], 1) if o.get("sector_avg_pe") else "n/d"
    blk.multi_cell(
        width, 6,
        sanitize(f"P/E: {pe_txt} (trailing) | mediana de pares ({o.get('peer_count',0)}): {sector_avg_txt}"),
        link=glossary_links["P/E"], align="L",
    )

    # Nota de procedencia: si Yahoo no tenia el dato y se relleno con FMP,
    # se marca explicitamente (ver glosario "Crecim." / "Recomendacion").
    growth_txt = fmt_pct(o["growth"] * 100) if o["growth"] is not None else "n/d"
    growth_note = " (via FMP)" if o.get("growth_source") == "FMP" else ""
    rec_note = " (via FMP)" if o.get("recommendation_source") == "FMP" else ""
    blk.set_x(x)
    blk.multi_cell(
        width, 6,
        sanitize(f"Crecim.: {growth_txt}{growth_note} | Recomendacion: {o['recommendation']}{rec_note}"),
        link=glossary_links["Crecim."], align="L",
    )

    roe_txt = fmt_pct(o["roe"] * 100) if o["roe"] is not None else "n/d"
    margin_txt = fmt_pct(o["operating_margin"] * 100) if o["operating_margin"] is not None else "n/d"
    sector_margin_txt = fmt_pct(o["sector_avg_margin"] * 100) if o.get("sector_avg_margin") else "n/d"
    debt_txt = fmt_es(o["debt_to_equity"], 0) if o["debt_to_equity"] is not None else "n/d"
    liquidity_txt = fmt_es(o["current_ratio"], 2) if o["current_ratio"] is not None else "n/d"
    blk.set_x(x)
    blk.multi_cell(
        width, 6,
        sanitize(
            f"Calidad {o['quality_score']}/4: ROE {roe_txt} | "
            f"margen operativo {margin_txt} (sector: {sector_margin_txt}) | "
            f"deuda/patrimonio {debt_txt} | liquidez {liquidity_txt}"
        ),
        link=glossary_links["Calidad"], align="L",
    )

    blk.set_x(x)
    blk.multi_cell(width, 5, sanitize(f"Prioridad: {o.get('opportunity_score',0):.1f}/100 | Cobertura: {o.get('coverage',0):.0%} | Estado: {o.get('status','sin evaluar')} | Cotizacion: {o.get('quote_at') or 'sin fecha'}"), align="L")
    if o.get('reasons'):
        blk.set_x(x)
        blk.multi_cell(width, 5, sanitize("Revisar: " + "; ".join(o['reasons'])), align="L")
    banks = o.get("strong_buy_banks") or []
    banks_txt = ", ".join(banks) if banks else "no consultado o sin nota positiva reciente"
    blk.set_x(x)
    blk.cell(width, 6, "Bancos/entidades con compra fuerte:", link=glossary_links["Bancos"])
    blk.ln(6)
    blk.set_text_color(*INK)  # fin de las lineas con enlace, vuelve el texto normal
    blk.set_x(x)
    blk.multi_cell(width, 5, sanitize(banks_txt), align="L")

    blk.set_x(x)
    description = o.get("description_es") or o.get("description_en") or "Sin descripcion disponible."
    blk.multi_cell(width, 5, sanitize(description), align="L")
    blk.ln(4)


def render_detailed_descriptions(pdf: FPDF, entries: list[dict], glossary_links: dict, section_number: int, theme_map: dict | None = None) -> None:
    """Fichas detalladas por accion (precio/objetivo, P/E, crecimiento,
    calidad, bancos y descripcion), a una sola columna y a todo el ancho.
    Se usa en las 3 secciones con tabla (principal, small caps, Trump
    trade), en las hojas siguientes a su tabla resumen.

    En vez de dejar que fpdf2 corte donde le toque (lo que partia fichas a
    medias) o de envolver cada ficha en pdf.unbreakable() (lo que dejaba un
    hueco grande al final de cada hoja cuando la siguiente ficha no cabia
    entera), aqui se mide antes cuanto ocupa cada ficha (pintandola en seco
    con pdf.offset_rendering), se agrupan tantas como quepan enteras en una
    hoja, y cada grupo se centra verticalmente: no se parte ninguna ficha y
    el sobrante de cada hoja queda repartido arriba y abajo en vez de
    acumularse al final.

    'section_number' es el numero de la seccion principal (2, 3 o 4) para
    que la cabecera de cada ficha use la misma numeracion jerarquica que
    el indice (ej. "2.1", "3.1")."""
    if not entries:
        return

    def theme_of(o: dict) -> str:
        return theme_map.get(o["symbol"], "") if theme_map else ""

    # 1) Medir cada ficha en seco (no se dibuja nada: offset_rendering
    #    rebobina el estado al salir del bloque).
    heights: list[float] = []
    for i, o in enumerate(entries, start=1):
        y0 = pdf.y
        with pdf.offset_rendering() as dummy:
            _render_ficha(
                dummy, pdf.l_margin, pdf.epw, i, o, glossary_links,
                section_number, theme_of(o), register_section=False,
            )
            heights.append(dummy.y - y0)

    # 2) Agrupar por hoja: tantas fichas enteras como quepan.
    usable_h = (pdf.h - pdf.b_margin) - pdf.t_margin
    groups: list[list[int]] = []
    current: list[int] = []
    current_h = 0.0
    for idx, h in enumerate(heights):
        if current and current_h + h > usable_h:
            groups.append(current)
            current, current_h = [], 0.0
        current.append(idx)
        current_h += h
    if current:
        groups.append(current)

    # 3) Pintar cada grupo en su hoja, repartiendo el sobrante en
    #    (numero de fichas + 1) partes iguales: una arriba, una entre cada
    #    par de fichas, y una — sin dibujarla, simplemente sin usarla — al
    #    pie de la hoja. Al reservar esa parte final en el propio calculo
    #    (en vez de solo repartir arriba/entre y dejar lo que sobre sin
    #    contar), el hueco de abajo sale igual al de arriba y al de enmedio.
    for group_i, group in enumerate(groups):
        if group_i > 0:
            pdf.add_page()
        group_h = sum(heights[idx] for idx in group)
        available_h = (pdf.h - pdf.b_margin) - pdf.y
        leftover = max(0.0, available_h - group_h)
        spacing = min(leftover / (len(group) + 1), MAX_FICHA_SPACING)
        pdf.ln(spacing)
        for position, idx in enumerate(group):
            if position > 0:
                pdf.ln(spacing)
            o = entries[idx]
            _render_ficha(
                pdf, pdf.l_margin, pdf.epw, idx + 1, o, glossary_links,
                section_number, theme_of(o),
            )


def estimate_toc_pages(n_top: int, n_small: int, n_trump: int) -> int:
    """Cuenta cuantas paginas necesitara el indice renderizando render_toc()
    DE VERDAD sobre un PDF de prueba desechable, para reservarlas EXACTAS
    con insert_toc_placeholder. Un calculo aproximado a mano (alturas fijas
    estimadas) se desvio de la realidad; simularlo con el mismo codigo que
    se usara luego es la unica forma de que coincida siempre. Reservar de
    mas o de menos obligaria a fpdf2 a insertar/quitar paginas a
    posteriori (allow_extra_pages), lo que deja mal el numero de pagina ya
    dibujado en el pie de la ultima pagina insertada (bug conocido de
    fpdf2: el pie se dibuja con la posicion temporal antes de reordenar)."""
    from fpdf.outline import OutlineSection

    def section(name: str, level: int) -> OutlineSection:
        return OutlineSection(name=name, level=level, page_number=1, dest=None)

    fake_outline = [section("Panorama de mercado", 0), section("Tabla 10 principales acciones", 0)]
    fake_outline += [section(f"T{i}", 1) for i in range(n_top)]
    fake_outline.append(section("Empresas de pequeña capitalizacion", 0))
    fake_outline += [section(f"S{i}", 1) for i in range(n_small)]
    fake_outline.append(section("Cesta tematica 'Trump trade'", 0))
    fake_outline += [section(f"P{i}", 1) for i in range(n_trump)]
    fake_outline.append(section("Noticias recientes", 0))
    fake_outline.append(section("Oportunidades con margen de seguridad", 0))
    fake_outline.append(section("Glosario de variables", 0))

    scratch = ReportPDF(orientation="L", format="A4")
    scratch.set_auto_page_break(True, margin=15)
    scratch.set_margins(left=18, top=10, right=18)
    scratch.add_page()
    start_page = scratch.page_no()
    render_toc(scratch, fake_outline)
    return scratch.page_no() - start_page + 1


def render_toc(pdf: FPDF, outline) -> None:
    # insert_toc_placeholder restaura la Y guardada al momento de reservar la
    # pagina, pero no la X: sin este set_x, el titulo hereda la posicion X
    # donde quedo el cursor tras la ULTIMA pagina del documento (normalmente
    # cerca del margen derecho) y sale cortado en la esquina.
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=22, style="B")
    pdf.set_text_color(*INK)
    pdf.cell(0, 13, "Indice", new_x="LMARGIN", new_y="NEXT")
    y_line = pdf.get_y() + 1
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, y_line, pdf.w - pdf.r_margin, y_line)
    y_start = y_line + 8

    # Dos columnas en vez de una lista vertical unica: con un subpunto por
    # cada accion (top 10 + pequeña cap top 10 + cesta tematica) el indice
    # no cabe en una pagina a una sola columna. La 4a seccion principal
    # ("Cesta tematica" en adelante) arranca la segunda columna: reparte el
    # contenido de forma razonablemente equilibrada (seccion 2 y 3, con sus
    # 10 subpuntos cada una, pesan mucho mas que 4+5+6 juntas).
    col_gap = 10
    col_width = (pdf.epw - col_gap) / 2
    col_x = [pdf.l_margin, pdf.l_margin + col_width + col_gap]
    col_y = [y_start, y_start]
    col = 0
    SWITCH_AT_MAIN = 4
    max_y = pdf.h - pdf.b_margin

    # Numeracion jerarquica: un punto principal (1, 2, 3...) por cada
    # seccion/tabla, y un subpunto (2.1, 2.2...) por cada accion dentro de
    # ella -- en vez de una lista plana con todo al mismo nivel.
    main_i = 0
    sub_i = 0
    for section in outline:
        link = pdf.add_link(page=section.page_number)
        if section.level == 0:
            main_i += 1
            sub_i = 0
            if main_i == SWITCH_AT_MAIN and col == 0:
                col = 1
            label = f"{main_i}."
            font_size = 12
            row_h = 9
            indent = ""
        else:
            sub_i += 1
            label = f"{main_i}.{sub_i}"
            font_size = 10
            row_h = 6.5
            indent = "    "
        if col_y[col] + row_h > max_y:
            # Seguridad: si en algun caso extremo (watchlist mucho mayor)
            # el indice no cupiese en 2 columnas / 1 pagina, sigue en una
            # pagina nueva en vez de desbordar el pie de pagina.
            pdf.add_page()
            col_y = [pdf.t_margin, pdf.t_margin]
        pdf.set_xy(col_x[col], col_y[col])
        pdf.set_font("Helvetica", size=font_size)
        pdf.set_text_color(*NAVY)
        pdf.cell(
            col_width, row_h,
            sanitize(f"{indent}{label} {section.name}  ...  pag. {section.page_number}"),
            link=link,
        )
        col_y[col] += row_h
    pdf.set_text_color(*INK)
    pdf.set_y(max(col_y))


def draw_cover_page(pdf: FPDF, title_text: str) -> None:
    """Portada reutilizable (logo/titular centrados, firma anclada abajo a
    la derecha) para cualquier informe de esta app: cambia solo el titulo,
    todo el resto (colores, logo, firma en arabe) es identico en todos.
    NO es una plantilla real de JPMorgan, ING ni de ningun otro banco: ver
    clausula de no afiliacion en la pagina siguiente. Logo grande solo aqui
    (paginas interiores llevan la version pequeña via header())."""
    pdf.page_background = PORTADA_BG
    pdf.add_page()
    if os.path.exists(SEF_ARABIC_FONT_PATH):
        pdf.add_font("FreeSerifArabic", "", SEF_ARABIC_FONT_PATH)
    logo_size = 40
    kicker_h = 6
    title_line_h = 14
    arabic_h = 8
    gap_title_arabic = 2
    brand_h = 10
    credit_h = 5  # altura de cada linea de la firma, en columna (3 lineas)
    gap_logo_kicker = 6
    gap_kicker_title = 3

    title_size = 30
    pdf.set_font("Helvetica", size=title_size, style="B")
    while pdf.get_string_width(title_text) > pdf.epw and title_size > 20:
        title_size -= 1
        pdf.set_font("Helvetica", size=title_size, style="B")
    title_lines = 2 if pdf.get_string_width(title_text) > pdf.epw else 1

    block_height = (
        logo_size + gap_logo_kicker + kicker_h + gap_kicker_title
        + title_line_h * title_lines + gap_title_arabic + arabic_h
    )
    y = (pdf.h - block_height) / 2

    if os.path.exists(SEF_LOGO_PATH):
        pdf.image(SEF_LOGO_PATH, x=(pdf.w - logo_size) / 2, y=y, w=logo_size, h=logo_size)
    y += logo_size + gap_logo_kicker

    pdf.set_y(y)
    pdf.set_font("Helvetica", size=9, style="B")
    pdf.set_text_color(*NAVY)
    pdf.cell(0, kicker_h, sanitize(f"ANALISIS AUTOMATIZADO DE MERCADOS - {datetime.now():%Y}"), align="C", new_x="LMARGIN", new_y="NEXT")
    y += kicker_h + gap_kicker_title

    pdf.set_y(y)
    pdf.set_font("Helvetica", size=title_size, style="B")
    pdf.set_text_color(*INK)
    if title_lines == 2:
        words = title_text.split()
        mid = len(words) // 2
        pdf.cell(0, title_line_h, sanitize(" ".join(words[:mid])), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, title_line_h, sanitize(" ".join(words[mid:])), align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(0, title_line_h, sanitize(title_text), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(gap_title_arabic)

    # "SEF-Financial" tambien en arabe (transliteracion fonetica), como
    # detalle bilingue bajo el titulo. set_text_shaping activa el motor de
    # HarfBuzz (via uharfbuzz) para el trazado de derecha a izquierda y las
    # formas contextuales del arabe; sin el, las letras saldrian sueltas y
    # en el orden equivocado.
    if os.path.exists(SEF_ARABIC_FONT_PATH):
        pdf.set_font("FreeSerifArabic", size=13)
        pdf.set_text_color(*NAVY)
        pdf.set_text_shaping(True)
        pdf.cell(0, arabic_h, "سيف فايننشال", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_shaping(False)
        pdf.set_text_color(*INK)

    # Firma compacta, alineada a la derecha y anclada abajo.
    pdf.set_y(-55)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*NAVY)
    pdf.cell(pdf.epw, brand_h, "SEF-Financial", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(*INK)
    pdf.cell(pdf.epw, credit_h, "Realizado por SEF", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.cell(pdf.epw, credit_h, sanitize(f"{datetime.now():%d/%m/%Y a las %H:%M}"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.cell(pdf.epw, credit_h, "Generado mediante reglas heurísticas", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.page_background = None


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
    # Margenes iguales a ambos lados. Las tablas siguen centrandose solas
    # (fpdf2 escala col_widths para llenar el ancho impreso disponible, no
    # son mm fijos).
    pdf.set_margins(left=20, top=10, right=20)

    # Enlaces internos del glosario (P/E, PEG, etc. -> definicion). fpdf
    # exige pagina asignada desde ya; se corrigen al final del todo.
    glossary_links = {name: pdf.add_link(page=1) for name, _ in GLOSSARY}

    draw_cover_page(pdf, "DEMO - datos ficticios" if rows and all(r.get("growth_source") == "DEMO" for r in rows) else "Candidatas para estudiar")

    # --- Indice (paginas reservadas EXACTAS, se rellenan solas al final) ---
    # Con un subpunto por accion (ver render_detailed_descriptions) el
    # indice puede necesitar mas de 1 pagina. Se reserva el numero exacto
    # (estimate_toc_pages) y NO se usa allow_extra_pages=True: con esa
    # opcion fpdf2 trata CUALQUIER salto de pagina durante el renderizado
    # del indice como una insercion nueva al final del documento (a
    # reordenar despues), incluso si la reserva ya era del tamaño justo;
    # el pie de esa pagina insertada queda dibujado con el numero de
    # pagina temporal (mal) porque el reordenamiento posterior no vuelve a
    # dibujar el pie. Reservando el numero exacto de paginas (calculado
    # simulando el renderizado real) y dejando allow_extra_pages en False,
    # todas las paginas del indice se crean en la pasada normal, sin
    # insercion ni reordenamiento, y su pie sale bien a la primera.
    pdf.page_background = INDICE_BG
    pdf.add_page()
    toc_pages = estimate_toc_pages(len(top), len(top_small), len(top_trump))
    # El tinte de "Panorama de mercado" se activa AQUI (tras crear la pagina
    # del indice, pero antes de insert_toc_placeholder) para que la pagina
    # que ese metodo crea internamente para "saltar" el indice (ver
    # FPDF._perform_page_break, llamado 'toc_pages' veces en bucle) ya nazca
    # con el fondo puesto: esa es la pagina que reutiliza la Seccion 1 sin
    # necesitar su propio add_page() (ver nota mas abajo).
    pdf.page_background = SECTION1_BG
    pdf.insert_toc_placeholder(render_toc, pages=toc_pages)

    # --- Seccion 1: Panorama de mercado (graficas circulares + aviso legal) ---
    # Sin add_page() aqui: insert_toc_placeholder ya salto a una pagina
    # nueva (con el fondo ya aplicado, ver arriba); añadir otra generaba una
    # pagina en blanco de mas en cada informe.
    pdf.start_section("Panorama de mercado")
    section_header(pdf, "Seccion 1", "1. Panorama de mercado")

    disclaimer_text = (
        "Informe generado mediante reglas de forma automatica, basado en datos publicos "
        "(Yahoo Finance, SEC EDGAR y, opcionalmente, Financial Modeling Prep). No "
        "constituye asesoramiento financiero ni recomendacion de inversion "
        "personalizada. El diseño de este documento esta inspirado, con fines de "
        "legibilidad, en el formato habitual de una carta/nota de analisis "
        "financiero; no es una publicacion real de J.P. Morgan Chase & Co., ING, "
        "Value School ni de ninguna otra entidad financiera regulada, ni esta "
        "afiliado, respaldado o revisado por ellas. Limitaciones conocidas: no se ha "
        "realizado un backtest, asi que la rentabilidad de estas reglas no esta "
        "demostrada; la consulta a SEC EDGAR no se ha probado en vivo con una "
        "identidad real (User-Agent); y Financial Modeling Prep y el envio por "
        "Telegram tampoco estan validados."
    )
    pdf.set_font("Helvetica", size=7.5)
    disclaimer_h = pdf.multi_cell(pdf.epw, 4, disclaimer_text, align="L", dry_run=True, output="HEIGHT")
    # Subtitulo y graficas van pegados a la cabecera (sin centrar el bloque
    # entero, que era lo que abria un hueco grande entre el titulo de la
    # seccion y las graficas); el aviso legal se ancla abajo, asi que el
    # sobrante queda entre las graficas y el aviso en vez de acumularse
    # justo debajo del titulo.
    subtitle_gap = 6
    chart_block_h = 85
    line_gap = 4

    pdf.set_font("Helvetica", size=9, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(
        0, 6,
        sanitize(f"Cobertura del analisis: {len(rows)} acciones ({n_small_cap} de pequeña capitalizacion) - P/E medio global: {avg_txt}"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*INK)
    pdf.ln(subtitle_gap)

    top_sector_counts: Counter = Counter(o["sector"] or "n/a" for o in top)
    col_w = pdf.epw / 3
    y0 = pdf.get_y()
    render_pie_block(pdf, pdf.l_margin, col_w - 6, y0, "Mercados - todas las acciones analizadas", dict(coverage.most_common()))
    if top:  # sin candidatas no hay nada que repartir en un grafico
        render_pie_block(pdf, pdf.l_margin + col_w, col_w - 6, y0, f"Mercados - Top {len(top)}", dict(top_coverage.most_common()))
        render_pie_block(pdf, pdf.l_margin + 2 * col_w, col_w - 6, y0, f"Sectores - Top {len(top)}", dict(top_sector_counts.most_common()))
    pdf.set_y(y0 + chart_block_h)

    # Aviso legal anclado al pie de la pagina (no justo debajo de las
    # graficas), para que el sobrante de la hoja quede aqui.
    bottom_y = (pdf.h - pdf.b_margin) - disclaimer_h - line_gap
    if bottom_y > pdf.get_y():
        pdf.set_y(bottom_y)
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(line_gap)
    pdf.set_font("Helvetica", size=7.5)
    pdf.set_text_color(*BODY_GRAY)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, 4, disclaimer_text, align="L")
    pdf.set_text_color(*INK)

    # --- Seccion 2: Principales acciones ---
    pdf.page_background = SECTION2_BG
    pdf.add_page()
    pdf.start_section("Principales acciones")
    section_header(pdf, "Seccion 2", "2. Principales acciones")
    # La tabla va en esta misma hoja, bajo su cabecera/intro; lo que se
    # separa son las fichas detalladas, que empiezan en la hoja siguiente.
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.cell(0, 6, "Toca los encabezados de columna para saltar a la explicacion de cada variable (seccion Glosario).", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.ln(3)
    if top:
        render_summary_table(pdf, top, glossary_links, section_bg=SECTION2_BG)
        pdf.add_page()
        render_detailed_descriptions(pdf, top, glossary_links, section_number=2)
    else:
        pdf.set_font("Helvetica", size=10, style="B")
        pdf.multi_cell(
            pdf.epw, 6,
            sanitize(
                f"Hoy ninguna de las {len(rows)} acciones analizadas cumple el filtro "
                f"(al menos {MIN_SCORE_RATIO:.0%} de los criterios de valor, "
                f"7 de 8 comprobaciones con dato, calidad >=2/4, sin beneficios "
                f"negativos, apalancamiento excesivo ni volumen insuficiente). "
                f"No mostrar candidatas es el resultado esperado cuando el mercado no "
                f"cumple estas reglas; no demuestra ausencia de oportunidades."
            ),
            align="L",
        )

    # --- Seccion 3: Empresas de pequeña capitalizacion ---
    # Fondo de pagina propio para diferenciarla a simple vista (se aplica a
    # TODAS las paginas que cree add_page() de aqui en adelante, hasta que
    # se cambie de nuevo mas abajo, incluidas paginas extra por overflow).
    pdf.page_background = SECTION3_BG
    # add_page() antes de start_section: si no, el indice enlaza a la
    # pagina anterior (la de la tabla), no a la de esta seccion.
    pdf.add_page()
    pdf.start_section("Empresas de pequeña capitalizacion")
    section_header(pdf, "Seccion 3", "3. Empresas de pequeña capitalizacion")
    small_cap_intro = sanitize(
        f"Mismos criterios de la seccion 1, aplicados solo a acciones con "
        f"capitalizacion de mercado por debajo de "
        f"{SMALL_CAP_MAX / 1_000_000_000:.0f}.000 millones de USD "
        f"({n_small_cap} de las {len(rows)} acciones analizadas). Ojo: suelen "
        f"tener mucha menos cobertura de analistas (columna # Analistas) que "
        f"las grandes tecnologicas del resto del informe, lo que hace menos "
        f"fiables el 'Crecim.' y la 'Recomendacion' (ver Glosario), y suelen "
        f"tener mayor volatilidad y menor liquidez."
    )
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.multi_cell(pdf.epw, 5, small_cap_intro, align="L")
    pdf.set_text_color(*INK)
    pdf.ln(3)
    if top_small:
        # Tabla en esta misma hoja; solo las fichas se van a la siguiente,
        # igual que en la Seccion 2.
        render_summary_table(pdf, top_small, glossary_links, section_bg=SECTION3_BG)
        pdf.add_page()
        render_detailed_descriptions(pdf, top_small, glossary_links, section_number=3)
    else:
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 6, "Ninguna pequeña capitalizacion con datos suficientes supera el filtro configurado.", new_x="LMARGIN", new_y="NEXT")

    # --- Seccion 4: Cesta tematica "Trump trade" ---
    pdf.page_background = SECTION4_BG
    pdf.add_page()
    pdf.start_section("Cesta tematica 'Trump trade'")
    section_header(pdf, "Seccion 4", "4. Cesta tematica 'Trump trade'")
    trump_intro = sanitize("Cesta tematica estatica heredada del script original, sin validar su vigencia politica. No es el patrimonio personal de Donald Trump ni una seleccion de oportunidades. Puede incluir empresas descartadas o con datos insuficientes; consultar FR y los motivos en JSON.")
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.set_text_color(*BODY_GRAY)
    pdf.multi_cell(pdf.epw, 5, trump_intro, align="L")
    pdf.set_text_color(*INK)
    pdf.ln(3)
    # Tabla en esta misma hoja; solo las fichas se van a la siguiente,
    # igual que en las secciones 2 y 3.
    if top_trump:
        render_summary_table(pdf, top_trump, glossary_links, section_bg=SECTION4_BG)
        pdf.add_page()
        render_detailed_descriptions(pdf, top_trump, glossary_links, section_number=4, theme_map=TRUMP_TRADE_THEMES)
    else:
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 6, "Sin valores de esta cesta en la watchlist.")

    # --- Seccion 5: Noticias recientes (al final, antes del glosario) ---
    # El azul mas suave de todos, igual que el Glosario (seccion 6): el
    # gradiente de azul de las secciones anteriores termina aqui. Ambas
    # quedan activas hasta el final del documento, no hace falta resetear
    # entre medias.
    pdf.page_background = SECTION56_BG
    pdf.add_page()
    pdf.start_section("Noticias recientes")
    section_header(pdf, "Seccion 5", "5. Noticias recientes")
    if not any(o.get("news") for o in top + top_small + top_trump):
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 6, "Noticias no consultadas o sin titulares recientes verificables.")
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
        pdf.cell(0, 7, sanitize(shown_name(o)), new_x="LMARGIN", new_y="NEXT")
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

    render_conviction_pdf(pdf, rows)

    # --- Seccion 7: Glosario (aqui aterrizan todos los hipervinculos) ---
    pdf.add_page()
    pdf.start_section("Glosario de variables")
    glossary_page = pdf.page_no()
    section_header(pdf, "Seccion 7", "7. Glosario de variables")
    for name, explanation in GLOSSARY:
        # Mantener el encabezado junto a las primeras líneas de su explicación.
        if pdf.get_y() + 22 > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.set_link(glossary_links[name], page=pdf.page_no(), y=pdf.get_y())
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, sanitize(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, sanitize(explanation), align="L")
        pdf.ln(3)

    out_path = str(PDF_OUTPUT_DIR / f"InformeFinanciero_{datetime.now():%d-%m-%Y}.pdf")
    pdf.output(out_path)
    return out_path


GENERATE_NOW_BUTTON = {
    "inline_keyboard": [[{"text": "Generar informe ahora", "callback_data": "informe"}]]
}


def send_telegram_document(path, caption):
    if requests is None:
        raise RuntimeError('Falta requests; instala requirements.txt')
    token = ''.join(os.environ.get('TELEGRAM_BOT_TOKEN','').split())
    chat = os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:
        raise ValueError('Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID para --send')
    try:
        with open(path,'rb') as stream:
            response = requests.post(f'https://api.telegram.org/bot{token}/sendDocument',
                data={'chat_id':chat,'caption':caption,'reply_markup':json.dumps(GENERATE_NOW_BUTTON)},
                files={'document':stream},timeout=(10,60))
        if not response.ok or not response.json().get('ok'):
            raise RuntimeError('Telegram rechazó el envío; revisa la configuración')
    except requests.RequestException:
        # No reintentar POST automáticamente (duplicados) ni revelar URL con token.
        raise RuntimeError('Envío no confirmado; comprueba Telegram antes de repetir') from None


def generate_and_send_report():
    """Punto de entrada de bot_listener.py (/informe o botón de Telegram): pide
    el envío de forma explícita. Lanza error si falla, para que el workflow lo
    marque en rojo en vez de terminar en silencio."""
    code = main(['--pdf', '--enrich', '--send'])
    if code != 0:
        raise RuntimeError(f'El informe no se generó o no se envió (código {code})')


def demo_snapshot():
    now = datetime.now(timezone.utc)
    records=[]
    for i in range(8):
        info = dict(currency='USD',financialCurrency='USD',quoteType='EQUITY',sector='Industrials',
            industry='Demo Industry',country='United States',currentPrice=20,marketCap=1e9,
            sharesOutstanding=50e6,averageVolume=2e6,freeCashflow=80e6,totalDebt=100e6,
            totalCash=60e6,ebitda=80e6,trailingPE=8 if i==0 else 15+i,
            trailingEps=2,returnOnEquity=.2,operatingMargins=.2 if i==0 else .15,
            debtToEquity=30,currentRatio=2,bookValue=10,revenueGrowth=.1,
            regularMarketTime=now.timestamp(),mostRecentQuarter=(now-timedelta(days=70)).timestamp(),
            longBusinessSummary='EMPRESA FICTICIA. Datos sintéticos para probar el programa; no invertir.')
        estimate=core.expected_growth({'avg':2,'low':1.9,'high':2.1,'numberOfAnalysts':8},
                                     {'avg':2.5,'low':2.4,'high':2.6,'numberOfAnalysts':7})
        estimate['growth_source']='DEMO'
        if i==6:
            info['trailingEps']=-1
        if i==7:
            info.pop('totalCash')
        statements={'income':[],'cashflow':[],'balance':[]}
        for age in range(4):
            date=f'{now.year-1-age}-12-31'
            statements['income'].append({'date':date,'Diluted EPS':2-.1*age,
                'Net Income':100e6-5e6*age,'EBIT':80e6,'Pretax Income':75e6,
                'Tax Provision':15e6,'Total Revenue':500e6-20e6*age,'Diluted Average Shares':50e6})
            statements['cashflow'].append({'date':date,'Operating Cash Flow':110e6-5e6*age,
                'Capital Expenditure':-30e6,'Free Cash Flow':80e6-5e6*age})
            statements['balance'].append({'date':date,'Total Debt':100e6,'Stockholders Equity':300e6,
                'Cash Cash Equivalents And Short Term Investments':60e6})
        records.append({'symbol':f'DEMO{i+1}', 'info':info, 'estimates':estimate,'statements':statements})
    return {'as_of':now.isoformat(),'fx_rates':{'USD':1.0},'records':records,'demo':True}

def replay(snapshot):
    # Se evalúa a la fecha guardada, no se disfraza de cotización actual.
    as_of=core.utc_date(snapshot['as_of'])
    if as_of is None:
        raise ValueError('Snapshot sin fecha válida')
    rates=snapshot['fx_rates']
    rows=[core.normalize(r['symbol'],r['info'],r.get('estimates',{}),rates.get,as_of) for r in snapshot['records']]
    if len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('Snapshot con tickers duplicados')
    core.add_peers(rows)
    inputs={r['symbol']:r for r in snapshot['records']}
    for row in rows:
        core.evaluate(row)
        historical=conviction.historical_quality(inputs[row['symbol']].get('statements',{}),as_of)
        row['historical']=historical
        row['valuation']=conviction.scenarios(row,historical,rates,HORIZON,REQUIRED_RETURN)
        row['sector_model']=sector_models.evaluate(row)
    return rows

def clean_json(value):
    if isinstance(value,dict):
        return {str(k):clean_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):
        return None
    return value

def write_reports(rows, errors, snapshot, output):
    output.mkdir(parents=True,exist_ok=True)
    snapshot['valuation_parameters']={'horizon':HORIZON,'required_return':REQUIRED_RETURN}
    for name,payload in [('resultados.json',{'rows':rows,'errors':errors,'as_of':snapshot['as_of']}),('snapshot.json',snapshot)]:
        target=output/name
        temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(clean_json(payload),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        temp.replace(target)
    columns=['symbol','status','opportunity_score','coverage','market_cap_usd','avg_dollar_volume','pe',
             'peer_count','fcf_yield','growth','growth_reliable','growth_source','growth_period','num_analysts',
             'quote_at','financial_period','insider_buying','conviction','margin_of_safety','study_price_limit',
             'sector_model','reasons']
    with (output/'resultados.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        for row in rows:
            entry={k:row.get(k) for k in columns}
            entry.update({k:row.get('valuation',{}).get(k) for k in ('conviction','margin_of_safety','study_price_limit')})
            model=row.get('sector_model')
            entry['sector_model']=f"{model['model']}: {model['status']}" if model else ''
            entry['reasons']='; '.join(row['reasons'])
            # Evita fórmulas al abrir textos externos en Excel.
            entry={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in entry.items()}
            writer.writerow(entry)

def render_conviction_pdf(pdf, rows):
    pdf.add_page()
    pdf.start_section('Oportunidades con margen de seguridad')
    section_header(pdf,'Seccion 6','6. Oportunidades con margen de seguridad')
    pdf.set_font('Helvetica',size=10)
    high=[r for r in rows if r.get('valuation',{}).get('conviction')=='prioridad_alta_para_estudio']
    available=[r for r in rows if r.get('valuation',{}).get('valuation_available')]
    pdf.multi_cell(pdf.epw,6,sanitize(f'{len(high)} empresas de prioridad alta para estudio. {len(available)} valoraciones calculables. Horizonte {HORIZON} anos; descuento {REQUIRED_RETURN:.0%}. Se exigen historial de beneficios/caja, ROIC, baja dilucion, deuda contenida, margen base >=25% y que ese margen aguante hipotesis peores (sensibilidad). No garantiza rentabilidad.'),new_x='LMARGIN',new_y='NEXT')
    pdf.multi_cell(pdf.epw,6,'Escenarios de EPS normalizado por PER final. Crecimiento base limitado al 8%, favorable al 12% y adverso -5%. Sin dividendos, impuestos personales ni costes. Los supuestos son revisables y no representan probabilidades.',new_x='LMARGIN',new_y='NEXT')
    if not high:
        pdf.multi_cell(pdf.epw,6,'Ninguna empresa cumple todos los requisitos adicionales.',new_x='LMARGIN',new_y='NEXT')
    for row in sorted(available,key=lambda r:(r['valuation']['conviction']!='prioridad_alta_para_estudio',-r['valuation']['margin_of_safety']))[:10]:
        v=row['valuation']
        if pdf.get_y()+60>pdf.h-pdf.b_margin:
            pdf.add_page()
        pdf.ln(5)
        pdf.set_font('Helvetica','B',11)
        pdf.multi_cell(pdf.epw,6,sanitize(f"{shown_name(row)} - {v['conviction']} | Margen base: {v['margin_of_safety']:.1%}"),new_x='LMARGIN',new_y='NEXT')
        pdf.set_font('Helvetica',size=9)
        pdf.multi_cell(pdf.epw,5,sanitize(f"Precio: {row['current_price']:.2f} {row['currency']} | Umbral con margen 25%: {v['study_price_limit']:.2f} {row['currency']} (no es una orden de compra)."),new_x='LMARGIN',new_y='NEXT')
        for label,case in v['scenarios'].items():
            pdf.multi_cell(pdf.epw,5,sanitize(f"{label}: EPS {case['eps_growth']:+.1%}/ano | PER {case['exit_pe']:.1f} | Precio final {case['terminal_price']:.2f} | Valor descontado {case['present_value']:.2f} {row['currency']} | Retorno anual precio {case['annual_price_return']:.1%}"),new_x='LMARGIN',new_y='NEXT')
        rec=row.get('reconciliation')
        if rec:
            detail=f"{len(rec['checks'])} cifras comparadas" if rec['checks'] else rec.get('reason','')
            pdf.multi_cell(pdf.epw,5,sanitize(f"Contraste con SEC (ingresos y beneficio neto): {rec['status']} - {detail}."),new_x='LMARGIN',new_y='NEXT')
        sens=v.get('sensitivity')
        if sens:
            verdict='conclusion robusta' if sens['robust'] else 'conclusion fragil: depende de las hipotesis'
            pdf.multi_cell(pdf.epw,5,sanitize(f"Sensibilidad ({sens['combinations']} combinaciones de crecimiento, PER, beneficio y tasa): margen minimo {sens['min_margin']:.1%}, mediano {sens['median_margin']:.1%}; margen >=25% en {sens['share_margin_ok']:.0%} de los casos - {verdict}."),new_x='LMARGIN',new_y='NEXT')
        pdf.multi_cell(pdf.epw,5,sanitize('; '.join(v['conviction_reasons']) or 'Cumple reglas; pendiente de analisis cualitativo.'),new_x='LMARGIN',new_y='NEXT')
    pdf.ln(4)
    pdf.multi_cell(pdf.epw,5,'JSON contiene todas las empresas, calidad historica, motivos y datos insuficientes. Un precio calculado depende de los supuestos; no es un valor intrinseco verificado.',new_x='LMARGIN',new_y='NEXT')

def main(argv=None):
    parser=argparse.ArgumentParser(description='Screener auditable. No ejecuta órdenes. Envío solo con --send.')
    parser.add_argument('--watchlist',type=Path,default=Path(WATCHLIST_FILE))
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'informes')
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--demo',action='store_true',help='Prueba sin red con empresas ficticias')
    source.add_argument('--input',type=Path,help='Reevaluar snapshot guardado a su fecha original, sin red')
    parser.add_argument('--pdf',action='store_true',help='Generar también el PDF (requiere fpdf2)')
    parser.add_argument('--enrich',action='store_true',help='Noticias, notas recientes e insiders SEC para candidatas')
    parser.add_argument('--translate',action='store_true',help='Traducción externa opcional, requiere --enrich')
    parser.add_argument('--send',action='store_true',help='Enviar informe a tu Telegram configurado')
    parser.add_argument('--resume',action='store_true',help='Reutilizar lo descargado hoy (cache/) si una ejecución anterior se interrumpió')
    parser.add_argument('--only-changes',action='store_true',help='Con --send: no enviar si no hay cambios relevantes desde la decisión anterior')
    parser.add_argument('--history',type=Path,default=forward_test.DEFAULT_HISTORY,help='Historial de decisiones (jsonl)')
    parser.add_argument('--deep-limit',type=int,default=20,help='Máximo de empresas para ampliar históricos anuales (0 desactiva)')
    parser.add_argument('--horizon',type=int,help='Horizonte de escenarios, 1–10 años (5 por defecto)')
    parser.add_argument('--required-return',type=float,help='Tasa de descuento anual, 0.01–0.40 (0.12 por defecto)')
    args=parser.parse_args(argv)
    if args.send and not args.pdf:
        parser.error('--send requiere --pdf')
    global ENABLE_TRANSLATION, PDF_OUTPUT_DIR, DEEP_LIMIT, HORIZON, REQUIRED_RETURN, USE_CACHE
    USE_CACHE=args.resume
    if not 0<=args.deep_limit<=500 or (args.horizon is not None and not 1<=args.horizon<=10) or (args.required_return is not None and not .01<=args.required_return<=.4):
        parser.error('Parámetros de análisis profundo o valoración fuera de rango')
    DEEP_LIMIT,HORIZON,REQUIRED_RETURN=args.deep_limit,args.horizon or 5,args.required_return or .12
    ENABLE_TRANSLATION=args.translate
    PDF_OUTPUT_DIR=args.output.resolve()
    if args.demo or args.input:
        snapshot=demo_snapshot() if args.demo else json.loads(args.input.read_text(encoding='utf-8'))
        if args.input:
            params=snapshot.get('valuation_parameters',{})
            HORIZON=args.horizon if args.horizon is not None else params.get('horizon',5)
            REQUIRED_RETURN=args.required_return if args.required_return is not None else params.get('required_return',.12)
        rows=replay(snapshot)
        errors=[]
        if args.enrich:
            parser.error('--enrich no se combina con --demo/--input: deben ser reproducibles y sin red')
    else:
        rows,_=analyze(load_watchlist(args.watchlist))
        errors,snapshot=analyze.errors,analyze.snapshot
    if args.enrich:
        enriched={r['symbol']:r for r in enrich_top(rank_top(rows))}
        rows=[enriched.get(r['symbol'],r) for r in rows]
    # Siempre deja resultados y errores auditables, incluso si fallan todos los tickers.
    write_reports(rows,errors,snapshot,args.output)
    print(f'Resultados: {(args.output/"resultados.json").resolve()}')
    alert_list, has_changes = [], True
    if not (args.demo or args.input):
        # Solo ejecuciones reales: la demo y las reevaluaciones no son decisiones.
        day = datetime.now(timezone.utc).date().isoformat()
        previous = forward_test.previous_entry(args.history, day)
        thematic = [r for r in rows if r['symbol'] in TRUMP_TRADE_THEMES]
        entry = forward_test.record_run(
            {'principales': rank_top(rows),
             'pequena_capitalizacion': rank_top([r for r in rows if is_small_cap(r)]),
             'cesta_tematica': rank_top(thematic, n=len(thematic), strict=False)},
            args.history)
        alert_list = alerts.compare(previous, entry)
        text = alerts.format_text(alert_list, previous['date'] if previous else None, entry['date'])
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'alertas.md').write_text(text + '\n', encoding='utf-8')
        print(text)
        has_changes = bool(alert_list) or previous is None  # primera ejecucion: siempre se envia
    if not rows:
        print('No se obtuvieron datos válidos. Revisa resultados.json; no equivale a cero oportunidades.',file=sys.stderr)
        return 2
    path=args.output/'resultados.csv'
    if args.pdf:
        if FPDF is object:
            print('CSV/JSON guardados. Falta fpdf2; instala requirements.txt.',file=sys.stderr)
            return 3
        top=rank_top(rows)
        small=rank_top([r for r in rows if is_small_cap(r)])
        thematic=rank_top([r for r in rows if r['symbol'] in TRUMP_TRADE_THEMES],strict=False)
        path=Path(build_pdf(top,small,thematic,rows,None))
    if args.send:
        if args.only_changes and not has_changes:
            print('Sin cambios relevantes desde la decisión anterior: no se envía el informe.')
            return 0
        extra = f' | {len(alert_list)} cambios' if alert_list else ''
        send_telegram_document(str(path),f'Screener: {sum(r["eligible"] for r in rows)} candidatas para estudiar{extra}')
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
