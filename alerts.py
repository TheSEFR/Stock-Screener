"""Alertas por cambios relevantes entre la decision anterior y la de hoy.

Compara dos entradas de history/decisiones.jsonl. Sin cambios => no hay alertas
y el envio diario puede omitirse (--only-changes), en vez de repetir el mismo informe.
"""
FCF_DETERIORATION = .5   # el FCL cae a menos de la mitad del anterior
QUALITY_DROP = .25       # la calidad (aciertos/4) baja al menos un escalon


def _by_symbol(entry):
    return {c['symbol']: c for c in (entry or {}).get('candidates', [])}


def _in_valuation_range(candidate):
    price, limit = candidate.get('price'), candidate.get('study_price_limit')
    return price is not None and limit is not None and price <= limit


def compare(previous, today):
    """Devuelve una lista de {symbol, type, detail}. Sin decision previa no hay alertas."""
    if not previous:
        return []
    before, now = _by_symbol(previous), _by_symbol(today)
    alerts = []
    for symbol in sorted(now.keys() - before.keys()):
        alerts.append({'symbol': symbol, 'type': 'entra', 'detail': 'Nueva candidata que pasa el filtro'})
    for symbol in sorted(before.keys() - now.keys()):
        alerts.append({'symbol': symbol, 'type': 'sale', 'detail': 'Deja de pasar el filtro'})
    for symbol in sorted(now.keys() & before.keys()):
        old, new = before[symbol], now[symbol]
        if _in_valuation_range(new) and not _in_valuation_range(old):
            alerts.append({'symbol': symbol, 'type': 'entra_rango_valoracion',
                           'detail': f"Precio {new['price']:.2f} <= umbral de estudio {new['study_price_limit']:.2f}"})
        old_fcf, new_fcf = old.get('fcf_yield'), new.get('fcf_yield')
        if old_fcf and old_fcf > 0 and new_fcf is not None and (new_fcf <= 0 or new_fcf < old_fcf * FCF_DETERIORATION):
            alerts.append({'symbol': symbol, 'type': 'caja_deteriora',
                           'detail': f'FCL {old_fcf:.1%} -> {new_fcf:.1%}'})
        old_q, new_q = old.get('quality_ratio'), new.get('quality_ratio')
        if old_q is not None and new_q is not None and new_q <= old_q - QUALITY_DROP:
            alerts.append({'symbol': symbol, 'type': 'pierde_calidad',
                           'detail': f'Calidad {old_q:.0%} -> {new_q:.0%}'})
        if old.get('conviction') and new.get('conviction') and old['conviction'] != new['conviction']:
            alerts.append({'symbol': symbol, 'type': 'cambia_prioridad',
                           'detail': f"{old['conviction']} -> {new['conviction']}"})
    for alert in alerts:  # nombre legible ("Samsung (005930.KS)") si el ticker es solo un codigo
        candidate = now.get(alert['symbol']) or before.get(alert['symbol']) or {}
        name = candidate.get('name')
        alert['name'] = f"{name} ({alert['symbol']})" if name and name != alert['symbol'] else alert['symbol']
    return alerts


def format_text(alerts, previous_date, today_date):
    if previous_date is None:
        return f'Primera decision guardada ({today_date}): aun no hay con que comparar.'
    if not alerts:
        return f'Sin cambios relevantes entre {previous_date} y {today_date}.'
    lines = [f'Cambios entre {previous_date} y {today_date}:']
    lines += [f"- {a.get('name') or a['symbol']}: {a['type']} ({a['detail']})" for a in alerts]
    return '\n'.join(lines)
