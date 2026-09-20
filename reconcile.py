"""Contraste de fundamentales: cifras anuales de Yahoo frente a la SEC (XBRL companyfacts).

Solo compara ingresos y beneficio neto de emisores que declaran en USD ante la SEC.
Los conceptos contables varian entre empresas: si no hay un concepto comparable,
el resultado es 'sin_datos_comparables', nunca una discrepancia inventada.
NO se ha probado con una identidad real de SEC; ver README (Limitaciones conocidas).
"""
import re

from screener_core import number, utc_date

TOLERANCE = .05  # diferencia relativa a partir de la cual se senala discrepancia
DATE_TOLERANCE_DAYS = 7
CONCEPTS = {'revenue': ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet'),
            'net_income': ('NetIncomeLoss',)}
YAHOO_KEYS = {'revenue': 'Total Revenue', 'net_income': 'Net Income'}
COMPANYFACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'


def has_identity(user_agent):
    """Misma exigencia que la consulta de insiders: nombre y correo reales."""
    return bool(user_agent and re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', user_agent) and 'example.com' not in user_agent)


def annual_values(facts, concept):
    """{fecha_fin: valor} de periodos anuales (10-K, ~1 anio) en USD; ante duplicados gana la presentacion mas reciente."""
    units = (facts.get('facts', {}).get('us-gaap', {}).get(concept, {}).get('units', {}).get('USD', []))
    best = {}
    for item in units:
        value, end, start = number(item.get('val')), utc_date(item.get('end')), utc_date(item.get('start'))
        if value is None or end is None or item.get('form') not in ('10-K', '10-K/A') or item.get('fp') != 'FY':
            continue
        if start is None or not 300 <= (end - start).days <= 400:
            continue
        key = item['end']
        if key not in best or item.get('filed', '') > best[key][1]:
            best[key] = (value, item.get('filed', ''))
    return {k: v[0] for k, v in best.items()}


def _sec_value(series, date):
    target = utc_date(date)
    if target is None:
        return None
    for key, value in series.items():
        if abs((utc_date(key) - target).days) <= DATE_TOLERANCE_DAYS:
            return value
    return None


def reconcile(statements, facts, financial_currency='USD', tolerance=TOLERANCE):
    if financial_currency != 'USD':
        return {'status': 'sin_datos_comparables', 'reason': 'SEC publica en USD; estados en ' + str(financial_currency), 'checks': []}
    checks = []
    for metric, concepts in CONCEPTS.items():
        series = {}
        for concept in concepts:
            for key, value in annual_values(facts, concept).items():
                series.setdefault(key, value)
        for record in (statements or {}).get('income', [])[:3]:
            yahoo, sec = number(record.get(YAHOO_KEYS[metric])), _sec_value(series, record.get('date'))
            if yahoo is None or sec in (None, 0):
                continue
            diff = abs(yahoo - sec) / abs(sec)
            checks.append({'metric': metric, 'date': record.get('date'), 'yahoo': yahoo, 'sec': sec,
                           'diff': diff, 'ok': diff <= tolerance})
    if not checks:
        return {'status': 'sin_datos_comparables', 'reason': 'Sin concepto/periodo comparable en SEC', 'checks': []}
    return {'status': 'ok' if all(c['ok'] for c in checks) else 'discrepancia', 'checks': checks}


def fetch_companyfacts(symbol, user_agent, client):
    """JSON companyfacts del ticker, o None si no hay identidad, CIK o la consulta falla."""
    if not has_identity(user_agent):
        return None
    try:
        if not hasattr(client, 'cik_map'):
            records = client.get('https://www.sec.gov/files/company_tickers.json', user_agent)
            client.cik_map = {r['ticker'].upper(): int(r['cik_str']) for r in records.values()}
        cik = client.cik_map.get(symbol.upper().replace('.', '-'))
        return client.get(COMPANYFACTS_URL.format(cik=cik), user_agent) if cik else None
    except Exception:
        return None


def check_symbol(symbol, statements, financial_currency, user_agent, client):
    """Resultado listo para guardar en la fila; 'sin_verificar' si no se pudo consultar."""
    if not has_identity(user_agent):
        return {'status': 'sin_verificar', 'reason': 'SEC_EDGAR_USER_AGENT no configurado', 'checks': []}
    facts = fetch_companyfacts(symbol, user_agent, client)
    if not facts:
        return {'status': 'sin_verificar', 'reason': 'Emisor sin datos en SEC o consulta fallida', 'checks': []}
    return reconcile(statements, facts, financial_currency)
