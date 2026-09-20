"""Motor de reglas auditable. Solo biblioteca estándar; no realiza órdenes.

Los umbrales son heurísticos y no están calibrados con un backtest.
Se distingue ausencia de datos, incumplimiento y modelos no aplicables.
"""
from __future__ import annotations

import math
import re
import statistics
import time
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

UTC = timezone.utc
MIN_PEERS = 5
MAX_QUOTE_DAYS = 7
MAX_FINANCIAL_DAYS = 180
MIN_ANALYSTS = 3


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def utc_date(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC)
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def quote_currency(code):
    """Precio en peniques/céntimos; marketCap se expresa en unidad principal."""
    return {'GBp': ('GBP', .01), 'GBX': ('GBP', .01),
            'ZAc': ('ZAR', .01), 'ILA': ('ILS', .01)}.get(code, (code, 1.0))


def expected_growth(current, following):
    """EPS medio FY+1 / FY0 - 1, no earningsGrowth opaco ni recuperación de pérdidas."""
    base, future = positive(current.get('avg')), positive(following.get('avg'))
    counts = [positive(r.get('numberOfAnalysts')) for r in (current, following)]
    result = dict(growth=None, growth_reliable=False, num_analysts=None,
                  estimate_dispersion=None, growth_period='FY0 → FY+1')
    if all(n is not None for n in counts):
        result['num_analysts'] = int(min(counts))
    if base is None or future is None:
        return result
    result['growth'] = future / base - 1
    spreads = []
    for row, avg in ((current, base), (following, future)):
        low, high = number(row.get('low')), number(row.get('high'))
        if low is None or high is None or not low <= avg <= high:
            return result
        spreads.append((high - low) / avg)
    result['estimate_dispersion'] = max(spreads)
    result['growth_reliable'] = bool(
        result['num_analysts'] is not None and result['num_analysts'] >= MIN_ANALYSTS
        and max(spreads) <= .5 and -1 < result['growth'] <= 1
    )
    return result


def fmp_estimates(records, now):
    """Orden explícito por cierre fiscal; nunca usar simplemente los dos primeros."""
    candidates = {}
    for row in records if isinstance(records, list) else []:
        date = utc_date(row.get('date'))
        if date and now.date() <= date.date() <= (now + timedelta(days=800)).date():
            candidates[date.date()] = row
    dates = sorted(candidates)
    if len(dates) < 2 or not 300 <= (dates[1] - dates[0]).days <= 400:
        return {}
    def convert(row):
        return {'avg': row.get('epsAvg'), 'low': row.get('epsLow'),
                'high': row.get('epsHigh'),
                'numberOfAnalysts': row.get('numAnalystsEps', row.get('numberAnalystsEstimatedEps'))}
    result = expected_growth(convert(candidates[dates[0]]), convert(candidates[dates[1]]))
    result['growth_period'] = f'{dates[0]} → {dates[1]}'
    result['growth_source'] = 'FMP'
    return result


def normalize(symbol, info, estimates, fx, now=None):
    """fx(moneda) devuelve USD por unidad principal; None si no se puede verificar."""
    now = now or datetime.now(UTC)
    currency = info.get('currency')
    major, scale = quote_currency(currency)
    price = positive(info.get('currentPrice')) or positive(info.get('regularMarketPrice'))
    cap = positive(info.get('marketCap'))
    conversion = positive(fx(major)) if major else None
    financial = info.get('financialCurrency')
    financial_fx = positive(fx(financial)) if financial else None
    cap_usd = cap * conversion if cap and conversion else None
    shares = positive(info.get('sharesOutstanding'))
    cap_mismatch = bool(cap and shares and price and abs(shares * price * scale / cap - 1) > .25)
    # Diferencias por ADR, clases de acciones o unidades: revisión, no corregir a ciegas.
    if cap_mismatch:
        cap_usd = None
    volume = positive(info.get('averageVolume'))
    liquidity = volume * price * scale * conversion if volume and price and conversion else None
    fcf = number(info.get('freeCashflow'))
    fcf_return = fcf * financial_fx / cap_usd if fcf is not None and financial_fx and cap_usd else None
    debt, cash, ebitda = (number(info.get(k)) for k in ('totalDebt', 'totalCash', 'ebitda'))
    leverage = (debt - cash) / ebitda if all(v is not None for v in (debt, cash, ebitda)) and ebitda > 0 else None
    target = positive(info.get('targetMeanPrice'))
    quote_time, quarter = utc_date(info.get('regularMarketTime')), utc_date(info.get('mostRecentQuarter'))
    quote_age = (now - quote_time).total_seconds() / 86400 if quote_time else None
    financial_age = (now - quarter).total_seconds() / 86400 if quarter else None
    unsupported = info.get('sector') in ('Financial Services', 'Real Estate')
    unsupported = unsupported or 'REIT' in (info.get('industry') or '').upper()
    row = dict(symbol=symbol, name=symbol, sector=info.get('sector'), industry=info.get('industry'),
               country=info.get('country'), currency=currency or '', financial_currency=financial,
               quote_type=info.get('quoteType'), current_price=price, target_price=target,
               upside=target / price - 1 if target and price else None,
               market_cap=cap, market_cap_usd=cap_usd, cap_mismatch=cap_mismatch,
               pe=positive(info.get('trailingPE')), eps=number(info.get('trailingEps')),
               revenue_growth=number(info.get('revenueGrowth')), fcf_yield=fcf_return,
               net_debt_ebitda=leverage, ebitda=ebitda, avg_dollar_volume=liquidity,
               roe=number(info.get('returnOnEquity')), operating_margin=number(info.get('operatingMargins')),
               debt_to_equity=number(info.get('debtToEquity')), current_ratio=number(info.get('currentRatio')),
               book_value=number(info.get('bookValue')), unsupported_model=bool(unsupported),
               price_to_book=number(info.get('priceToBook')), roa=number(info.get('returnOnAssets')),
               quote_age_days=quote_age, financial_age_days=financial_age,
               fetched_at=now.isoformat(), quote_at=quote_time.isoformat() if quote_time else None,
               financial_period=quarter.date().isoformat() if quarter else None,
               description_en=(info.get('longBusinessSummary') or '')[:500],
               analyst_opinions=number(info.get('numberOfAnalystOpinions')),
               recommendation={'strong_buy':'CF', 'buy':'CF','hold':'CN','underperform':'NC','sell':'NC'}.get(info.get('recommendationKey'), 'N/D'),
               recommendation_source='Yahoo', insider_buying=None, insider_source='sin consultar',
               growth=None, growth_reliable=False, growth_source=None, growth_period='FY0 → FY+1',
               num_analysts=None, estimate_dispersion=None)
    row.update(estimates)
    row['peg'] = (row['pe'] / (row['growth'] * 100)
                  if row['pe'] and row.get('growth_reliable') and positive(row.get('growth')) else None)
    fy = utc_date(info.get('nextFiscalYearEnd') or info.get('lastFiscalYearEnd'))
    row['fiscal_year_end'] = fy.strftime('%m/%y') if fy else 'n/d'
    return row


MIN_DESCRIPTION_CHARS = 80  # descripciones mas cortas no identifican una empresa


def mark_duplicates(rows):
    """Detecta la misma empresa cotizando en varias bolsas (ADR y local, doble cotizacion)
    por su descripcion de negocio. Se conserva la cotizacion mas liquida; las demas quedan
    con row['duplicate_of'] y no cuentan ni como candidatas ni como comparables."""
    groups = {}
    for row in rows:
        row.pop('duplicate_of', None)
        text = (row.get('description_en') or '').strip()
        if len(text) >= MIN_DESCRIPTION_CHARS:
            groups.setdefault(text[:200], []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda r: (number(r.get('avg_dollar_volume')) or 0, r['symbol']))
        for row in group:
            if row is not keep:
                row['duplicate_of'] = keep['symbol']
    return rows


MAX_STATEMENT_DAYS = 450  # un ejercicio anual mas antiguo no vale para reconstruir datos


def fill_from_statements(row, statements, now=None):
    """Reconstruye EPS, P/E y valor contable por accion desde los estados anuales
    cuando Yahoo no los da en la ficha (p. ej. algunos valores coreanos). Solo si el
    precio y las cuentas estan en la misma moneda y unidad: si no, deja el dato vacio.
    Lo reconstruido queda marcado en row['derived']; es anual, no trailing 12 meses."""
    now = now or datetime.now(UTC)
    row.setdefault('derived', [])
    quote, scale = quote_currency(row.get('currency'))
    if scale != 1.0 or not quote or quote != row.get('financial_currency'):
        return row
    incomes = sorted((r for r in (statements or {}).get('income', []) if utc_date(r.get('date'))),
                     key=lambda r: r['date'], reverse=True)
    if not incomes or (now - utc_date(incomes[0]['date'])).days > MAX_STATEMENT_DAYS:
        return row
    latest = incomes[0]
    if row.get('eps') is None:
        eps = number(latest.get('Diluted EPS'))
        if eps is not None:
            row['eps'] = eps
            row['derived'].append('eps')
            if row.get('pe') is None and eps > 0 and positive(row.get('current_price')):
                row['pe'] = row['current_price'] / eps
                row['derived'].append('pe')
    if row.get('book_value') is None:
        balance = {r.get('date'): r for r in (statements or {}).get('balance', [])}.get(latest['date'], {})
        equity, shares = number(balance.get('Stockholders Equity')), positive(latest.get('Diluted Average Shares'))
        if equity is not None and shares:
            row['book_value'] = equity / shares
            row['derived'].append('book_value')
    return row


REGIONS = {
    'Norteamerica': ('United States', 'Canada'),
    'Europa': ('Germany', 'France', 'Netherlands', 'Spain', 'Italy', 'Switzerland', 'United Kingdom', 'Sweden',
               'Denmark', 'Norway', 'Finland', 'Belgium', 'Ireland', 'Austria', 'Portugal', 'Luxembourg'),
    'Asia-Pacifico': ('Japan', 'South Korea', 'Taiwan', 'Hong Kong', 'Singapore', 'Australia', 'New Zealand',
                      'China', 'India', 'Indonesia', 'Thailand', 'Malaysia', 'Philippines', 'Vietnam'),
    'Latinoamerica': ('Brazil', 'Mexico', 'Chile', 'Argentina', 'Colombia', 'Peru'),
    'Oriente Medio y Africa': ('Israel', 'South Africa', 'Saudi Arabia', 'United Arab Emirates', 'Qatar'),
}
COUNTRY_REGION = {country: region for region, countries in REGIONS.items() for country in countries}


def add_peers(rows):
    """Mediana de otras empresas de la MISMA industria en la watchlist, ampliando el
    alcance solo si hace falta: 1) misma industria y pais; 2) misma industria y region
    (Europa, Asia-Pacifico...); 3) mismo SECTOR y region (comparacion mas gruesa).
    'peer_scope' dice cual se uso ('pais', 'region', 'sector_region' o None si no hay
    cinco comparables en ninguno).

    No se sustituye por una media mundial: comparar sectores o mercados sin relacion
    daria una referencia engañosa. Un alcance regional es menos preciso que uno de pais.
    """
    for row in rows:
        row.update(peer_scope=None, peer_count=0, sector_avg_pe=None, sector_avg_margin=None)
        if not row.get('industry') or not row.get('country'):
            continue
        region = COUNTRY_REGION.get(row['country'])
        in_country = lambda p: p.get('country') == row['country']
        in_region = lambda p: COUNTRY_REGION.get(p.get('country')) == region
        # De mas a menos preciso: industria+pais, industria+region, sector+region.
        scopes = [('pais', in_country, 'industry')]
        if region:
            scopes += [('region', in_region, 'industry')]
            if row.get('sector'):
                scopes += [('sector_region', in_region, 'sector')]
        for index, (scope, same_area, group) in enumerate(scopes):
            peers = [p for p in rows if p['symbol'] != row['symbol'] and p.get(group) == row[group]
                     and not p.get('duplicate_of')
                     and same_area(p) and p.get('quote_type') == 'EQUITY'
                     and p.get('quote_age_days') is not None and 0 <= p['quote_age_days'] <= MAX_QUOTE_DAYS
                     and p.get('financial_age_days') is not None and 0 <= p['financial_age_days'] <= MAX_FINANCIAL_DAYS]
            pes = [p['pe'] for p in peers if positive(p.get('pe'))]
            margins = [p['operating_margin'] for p in peers if number(p.get('operating_margin')) is not None]
            if index == 0:
                row['peer_count'] = len(pes)  # sin comparables suficientes queda el recuento del pais
            if len(pes) >= MIN_PEERS:
                row.update(peer_scope=scope, peer_count=len(pes), sector_avg_pe=statistics.median(pes),
                           sector_avg_margin=statistics.median(margins) if len(margins) >= MIN_PEERS else None)
                break


def score(row):
    pe, median = row.get('pe'), row.get('sector_avg_pe')
    fcf, growth, revenue = (row.get(k) for k in ('fcf_yield', 'growth', 'revenue_growth'))
    checks = {'pe_descuento_20': None if pe is None or median is None else pe <= .8 * median,
              'fcf_5': None if fcf is None else fcf >= .05,
              'eps_futuro_15': None if growth is None or not row.get('growth_reliable') else growth >= .15,
              'ingresos_5': None if revenue is None else revenue >= .05}
    row.update(checks=checks, score=sum(v is True for v in checks.values()),
               checks_applicable=sum(v is not None for v in checks.values()))
    # Denominador fijo: ocultar un dato nunca aumenta la puntuación.
    row['score_ratio'] = row['score'] / 4


def quality(row):
    roe, margin, median, debt, liquid = (row.get(k) for k in (
        'roe','operating_margin','sector_avg_margin','debt_to_equity','current_ratio'))
    checks = {'roe_15': None if roe is None else roe >= .15,
              'margen_positivo_pares': None if margin is None or median is None else margin > 0 and margin >= median,
              'deuda_patrimonio': None if debt is None else 0 <= debt <= 100,
              'liquidez_1_5': None if liquid is None else liquid >= 1.5}
    row.update(quality_checks=checks, quality_score=sum(v is True for v in checks.values()),
               quality_applicable=sum(v is not None for v in checks.values()))
    row['quality_ratio'] = row['quality_score'] / 4
    row['opportunity_score'] = round(100 * (.55 * row.get('score_ratio', 0) + .45 * row['quality_ratio']), 2)
    row['coverage'] = (row.get('checks_applicable', 0) + row['quality_applicable']) / 8


def risk_reasons(row):
    fail, missing = [], []
    if row.get('unsupported_model'):
        missing.append('Banca/seguros/inmobiliario requieren modelo específico')
    if row.get('quote_type') != 'EQUITY':
        missing.append('Instrumento no confirmado como acción')
    rules = [('eps', lambda v: v <= 0, 'EPS no positivo'),
             ('ebitda', lambda v: v <= 0, 'EBITDA no positivo'),
             ('net_debt_ebitda', lambda v: v > 4, 'Deuda neta/EBITDA > 4'),
             ('avg_dollar_volume', lambda v: v < 1e6, 'Liquidez diaria < 1 M USD'),
             ('book_value', lambda v: v <= 0, 'Patrimonio por acción no positivo'),
             ('fcf_yield', lambda v: v <= 0, 'FCF no positivo'),
             ('operating_margin', lambda v: v <= 0, 'Margen operativo no positivo'),
             ('market_cap_usd', lambda v: v <= 0, 'Capitalización no positiva'),
             ('current_price', lambda v: v <= 0, 'Precio no positivo')]
    for key, predicate, reason in rules:
        value = number(row.get(key))
        if value is None:
            missing.append('Sin dato verificable: ' + key)
        elif predicate(value):
            fail.append(reason)
    for key, limit, label in [('quote_age_days', MAX_QUOTE_DAYS, 'Cotización'),
                               ('financial_age_days', MAX_FINANCIAL_DAYS, 'Periodo financiero')]:
        age = number(row.get(key))
        if age is None or age < 0 or age > limit:
            missing.append(label + ' sin fecha válida o desactualizado')
    if row.get('cap_mismatch'):
        missing.append('Revisar unidades de capitalización / ADR / clases de acciones')
    return fail, missing


def evaluate(row):
    score(row)
    quality(row)
    fail, missing = risk_reasons(row)
    eligible = not fail and not missing and row['coverage'] >= .875 and row['score_ratio'] >= .75 and row['quality_ratio'] >= .5
    value_evidence = row['checks']['pe_descuento_20'] is True or row['checks']['fcf_5'] is True
    eligible = eligible and value_evidence
    row['eligible'] = bool(eligible)
    row['status'] = 'candidata' if eligible else ('descartada_riesgo' if fail else 'revisar_datos' if missing or row['coverage'] < .875 else 'no_cumple')
    row['reasons'] = fail + missing
    if row['coverage'] < .875:
        row['reasons'].append('Menos de 7 de 8 criterios con datos válidos')
    if row['score_ratio'] < .75:
        row['reasons'].append('Menos de 3 de 4 señales de valor/crecimiento')
    if row['quality_ratio'] < .5:
        row['reasons'].append('Menos de 2 de 4 señales de calidad')
    row['strengths'] = [k for k,v in {**row['checks'], **row['quality_checks']}.items() if v is True]
    if row.get('duplicate_of'):
        row['eligible'], row['status'] = False, 'duplicada'
        row['reasons'].append(f"Misma empresa que {row['duplicate_of']} (otra bolsa): se evalua solo esa cotizacion")
    return row


def rank(rows, n=10, strict=True):
    evaluated = [evaluate(dict(row)) for row in rows]
    return sorted((r for r in evaluated if r['eligible'] or not strict),
                  key=lambda r: (-r['opportunity_score'], -r['coverage'], r['symbol']))[:max(0,n)]


def parse_form4(xml, now=None, days=90):
    """P incluye compras privadas; XML inválido/incompleto => desconocido, no False."""
    now = now or datetime.now(UTC)
    try:
        root = ET.fromstring(xml)
        for node in root.iter():
            node.tag = node.tag.split('}')[-1]
        if root.tag != 'ownershipDocument':
            return None
        unknown = False
        for tx in root.iter('nonDerivativeTransaction'):
            code = tx.findtext('./transactionCoding/transactionCode')
            acquired = tx.findtext('./transactionAmounts/transactionAcquiredDisposedCode/value')
            if code == 'P' and acquired == 'A':
                date = utc_date(tx.findtext('./transactionDate/value'))
                if date is None:
                    unknown = True
                elif (now-timedelta(days=days)).date() <= date.date() <= now.date():
                    return True
        return None if unknown else False
    except (ET.ParseError, TypeError):
        return None


class HttpClient:
    """GET con plazo, tres intentos y pausa global; sin secretos en mensajes de error."""
    def __init__(self):
        self.last_request = 0

    def get(self, url, user_agent='Screener/2.0', as_json=True):
        for attempt in range(3):
            time.sleep(max(0, .25 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={'User-Agent': user_agent})
                with urllib.request.urlopen(req, timeout=20) as response:
                    data = response.read(10_000_001)
                if len(data) > 10_000_000:
                    raise ValueError('Respuesta demasiado grande')
                return json.loads(data) if as_json else data
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt == 2:
                    raise RuntimeError(f'Proveedor HTTP {exc.code}') from None
                retry = number(exc.headers.get('Retry-After'))
                time.sleep(min(30,max(2 ** attempt, retry or 0)))
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError('Proveedor inaccesible') from None
                time.sleep(2 ** attempt)


def sec_insider(symbol, user_agent, client, now=None):
    now = now or datetime.now(UTC)
    if not user_agent or not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', user_agent) or 'example.com' in user_agent:
        return None
    try:
        if not hasattr(client, 'cik_map'):
            records = client.get('https://www.sec.gov/files/company_tickers.json', user_agent)
            client.cik_map = {r['ticker'].upper(): int(r['cik_str']) for r in records.values()}
        cik = client.cik_map.get(symbol.upper().replace('.', '-'))
        if cik is None:
            return None
        data = client.get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json', user_agent)
        recent = data.get('filings', {}).get('recent', {})
        dates = [utc_date(s) for s in recent.get('filingDate', [])]
        cutoff = now - timedelta(days=90)
        # Si la ventana está truncada, una ausencia no demuestra que no haya compras.
        incomplete = not dates or any(d is None for d in dates) or min(d for d in dates if d) > cutoff
        columns = [recent.get(k, []) for k in ('form','filingDate','accessionNumber','primaryDocument')]
        incomplete = incomplete or len(set(map(len,columns))) != 1
        count = 0
        for form, date, accession, doc in zip(*columns):
            dt = utc_date(date)
            if form not in ('4','4/A') or not dt or dt < cutoff or dt > now:
                continue
            if form == '4/A':
                # Una enmienda requiere reconciliar con el original; no inferimos ausencia.
                incomplete = True
                continue
            count += 1
            if count > 60:
                return None
            doc = re.sub(r'^xsl[^/]+/', '', doc)
            if not re.fullmatch(r'[\w.\-/]+', doc) or '..' in doc:
                incomplete = True
                continue
            raw = client.get(f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{doc}', user_agent, False)
            result = parse_form4(raw, now)
            if result is True:
                return True
            incomplete = incomplete or result is None
        return None if incomplete else False
    except (RuntimeError, ValueError, KeyError, TypeError):
        return None
