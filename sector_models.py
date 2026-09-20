"""Modelos sectoriales heuristicos: banca/seguros, inmobiliario (REIT), ciclicas y tecnologicas.

Son INFORMATIVOS: no cambian 'eligible' ni el ranking general. Sirven para que los
sectores que el filtro industrial remite a revision tengan un criterio propio.
Umbrales heuristicos, sin backtest. Un dato ausente da 'revisar_datos', nunca aprueba.
"""
from screener_core import number, positive

CYCLICAL_SECTORS = {'Basic Materials', 'Energy', 'Industrials', 'Consumer Cyclical'}
MAX_QUOTE_DAYS = 7
MIN_LIQUIDITY_USD = 1e6
BANK_MAX_PRICE_TO_BOOK = 1.2
BANK_MIN_ROE = .10
BANK_MIN_ROA = .008
REIT_MIN_FCF_YIELD = .05  # el FCF es un sustituto tosco del AFFO: Yahoo no da FFO
REIT_MAX_NET_DEBT_EBITDA = 7
CYCLICAL_PE_DISCOUNT = .8  # PER normalizado <= 80% de la mediana de pares
TECH_RULE_OF_40 = .40  # crecimiento de ingresos + margen operativo


def _status(checks, minimum):
    """revisar_datos si falta algun dato; candidata_sector si pasan al menos 'minimum'."""
    if any(v is None for v in checks.values()):
        return 'revisar_datos'
    return 'candidata_sector' if sum(checks.values()) >= minimum else 'no_cumple'


def _basic_risk(row):
    """Bloqueos comunes. Devuelve (incumplimientos, datos_que_faltan)."""
    fail, missing = [], []
    eps, liquidity, age = number(row.get('eps')), number(row.get('avg_dollar_volume')), number(row.get('quote_age_days'))
    if eps is None:
        missing.append('EPS ausente')
    elif eps <= 0:
        fail.append('EPS no positivo')
    if liquidity is None:
        missing.append('Liquidez sin dato')
    elif liquidity < MIN_LIQUIDITY_USD:
        fail.append('Liquidez diaria < 1 M USD')
    if age is None or not 0 <= age <= MAX_QUOTE_DAYS:
        missing.append('Cotizacion sin fecha valida o desactualizada')
    return fail, missing


def _is_bank_or_insurer(row):
    industry = (row.get('industry') or '').lower()
    return row.get('sector') == 'Financial Services' and any(k in industry for k in ('bank', 'insurance'))


def _is_reit(row):
    return row.get('sector') == 'Real Estate' or 'REIT' in (row.get('industry') or '').upper()


def _bank(row):
    checks = {'precio_valor_contable': None, 'roe': None, 'roa': None}
    pb, roe, roa = positive(row.get('price_to_book')), number(row.get('roe')), number(row.get('roa'))
    if pb is not None:
        checks['precio_valor_contable'] = pb <= BANK_MAX_PRICE_TO_BOOK
    if roe is not None:
        checks['roe'] = roe >= BANK_MIN_ROE
    if roa is not None:
        checks['roa'] = roa >= BANK_MIN_ROA
    return 'banca_seguros', checks, 3, ['No evalua capital regulatorio, morosidad ni solvencia: confirmar con cuentas.']


def _reit(row):
    fcf, lev, margin = number(row.get('fcf_yield')), number(row.get('net_debt_ebitda')), number(row.get('operating_margin'))
    checks = {'fcf_yield': None if fcf is None else fcf >= REIT_MIN_FCF_YIELD,
              'deuda_neta_ebitda': None if lev is None else lev <= REIT_MAX_NET_DEBT_EBITDA,
              'margen_operativo_positivo': None if margin is None else margin > 0}
    return 'inmobiliario', checks, 3, ['Sin FFO/AFFO, ocupacion ni vencimientos de deuda: el FCF es una aproximacion.']


def _cyclical(row):
    valuation = row.get('valuation') or {}
    eps_quote = positive(valuation.get('normalized_eps_quote'))
    price, peers = positive(row.get('current_price')), positive(row.get('sector_avg_pe'))
    normalized_pe = price / eps_quote if eps_quote and price else None
    checks = {'per_normalizado_descuento': None if normalized_pe is None or peers is None
              else normalized_pe <= CYCLICAL_PE_DISCOUNT * peers}
    notes = ['PER sobre beneficio normalizado (menor entre el ultimo ejercicio y la mediana de tres): '
             + ('n/d' if normalized_pe is None else f'{normalized_pe:.1f}')
             + '. En un pico de ciclo el PER corriente parece barato sin serlo.']
    return 'ciclica', checks, 1, notes


def _tech(row):
    growth, margin, fcf = number(row.get('revenue_growth')), number(row.get('operating_margin')), number(row.get('fcf_yield'))
    checks = {'regla_del_40': None if growth is None or margin is None else growth + margin >= TECH_RULE_OF_40,
              'fcf_positivo': None if fcf is None else fcf > 0}
    return 'tecnologica', checks, 2, ['Regla del 40 = crecimiento de ingresos + margen operativo. No mide moat ni concentracion de clientes.']


def evaluate(row):
    """Devuelve {model, checks, status, notes, reasons} o None si el sector no tiene modelo propio."""
    if _is_bank_or_insurer(row):
        model = _bank(row)
    elif _is_reit(row):
        model = _reit(row)
    elif row.get('sector') in CYCLICAL_SECTORS:
        model = _cyclical(row)
    elif row.get('sector') == 'Technology':
        model = _tech(row)
    else:
        return None
    name, checks, minimum, notes = model
    fail, missing = _basic_risk(row)
    status = 'no_cumple' if fail else 'revisar_datos' if missing else _status(checks, minimum)
    return {'model': name, 'checks': checks, 'status': status, 'notes': notes, 'reasons': fail + missing}
