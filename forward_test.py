"""Prueba hacia adelante: que paso con las acciones que el screener recomendo, por secciones.

No es un backtest: solo usa decisiones guardadas en su dia (history/decisiones.jsonl,
una linea por ejecucion, con las tres listas del informe) y precios posteriores reales.
Cada accion cuenta una vez por seccion, desde su primera aparicion, y se agrupa por el
mes en que entro para ver como ha crecido cada tanda. Tarda meses en tener muestra.

Uso: python forward_test.py [--history RUTA] [--min-days 30] [--send]
"""
import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

RULES_VERSION = 'v1-2026-09-20'  # cambiar al modificar umbrales o criterios
BENCHMARK = 'SPY'  # ETF del S&P 500; su cierre ajustado incluye dividendos
DEFAULT_HISTORY = Path(__file__).parent / 'history' / 'decisiones.jsonl'
MIN_SAMPLE_FOR_CONCLUSION = 30  # menos observaciones que esto no permite concluir nada
SECTIONS = {'principales': 'Principales acciones',
            'pequena_capitalizacion': 'Pequeña capitalización',
            'cesta_tematica': 'Cesta temática (lista fija, no filtrada)'}


def _compact(c):
    valuation = c.get('valuation') or {}
    return {'symbol': c['symbol'], 'name': c.get('name'), 'price': c.get('current_price'), 'currency': c.get('currency'),
            'score': c.get('opportunity_score'),
            # Campos para detectar cambios (alerts.py)
            'fcf_yield': c.get('fcf_yield'), 'quality_ratio': c.get('quality_ratio'),
            'conviction': valuation.get('conviction'), 'margin_of_safety': valuation.get('margin_of_safety'),
            'study_price_limit': valuation.get('study_price_limit')}


def record_run(sections, path=DEFAULT_HISTORY, now=None):
    """Guarda las listas de hoy ({seccion: [filas]}; una lista suelta cuenta como 'principales').
    Una linea por dia: repetir el dia la reemplaza. 'candidates' = principales, para alerts.py."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(sections, dict):
        sections = {'principales': list(sections)}
    day = now.date().isoformat()
    compact = {name: [_compact(c) for c in rows] for name, rows in sections.items()}
    entry = {'date': day, 'rules': RULES_VERSION, 'candidates': compact.get('principales', []),
             'sections': compact}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [line for line in (path.read_text(encoding='utf-8').splitlines() if path.exists() else [])
            if line.strip() and json.loads(line).get('date') != day]
    kept.append(json.dumps(entry, ensure_ascii=False))
    path.write_text('\n'.join(kept) + '\n', encoding='utf-8')
    return entry


def load_history(path=DEFAULT_HISTORY):
    path = Path(path)
    if not path.exists():
        return []
    return sorted((json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()),
                  key=lambda e: e['date'])


def previous_entry(path, day):
    """Ultima decision guardada anterior a 'day' (YYYY-MM-DD), o None."""
    earlier = [e for e in load_history(path) if e['date'] < day]
    return earlier[-1] if earlier else None


def section_entries(entry):
    """Listas de una decision; las antiguas sin 'sections' solo tenian las principales."""
    return entry.get('sections') or {'principales': entry.get('candidates', [])}


def first_appearances(history):
    """{(seccion, ticker): (fecha, reglas, nombre)} con la primera vez que cada ticker fue recomendado en cada seccion."""
    first = {}
    for entry in history:
        for section, rows in section_entries(entry).items():
            for c in rows:
                first.setdefault((section, c['symbol']), (entry['date'], entry.get('rules'), c.get('name')))
    return first


def label(item):
    """Nombre legible: 'Samsung (005930.KS)' si el ticker es solo un codigo; el ticker si no hay nombre distinto."""
    name = item.get('name')
    return f"{name} ({item['symbol']})" if name and name != item['symbol'] else item['symbol']


def evaluate(history, series_fn, now=None, min_days=30):
    """series_fn(symbol, desde_fecha) -> (precio_inicial, precio_actual) o None.
    Devuelve {'results', 'no_price', 'too_recent', 'min_days'}. Los tickers sin precio
    (p. ej. dados de baja) se listan aparte: nunca se descartan en silencio."""
    now = now or datetime.now(timezone.utc)
    cache = {}

    def series(symbol, day):
        if (symbol, day) not in cache:
            cache[(symbol, day)] = series_fn(symbol, day)
        return cache[(symbol, day)]

    results, no_price, too_recent = [], [], []
    for (section, symbol), (day, rules, name) in first_appearances(history).items():
        item = {'section': section, 'symbol': symbol, 'name': name, 'date': day}
        age = (now.date() - datetime.fromisoformat(day).date()).days
        if age < min_days:
            too_recent.append(item)
            continue
        own, bench = series(symbol, day), series(BENCHMARK, day)
        if not own or not bench or not own[0] or not bench[0]:
            no_price.append(item)
            continue
        ret, bench_ret = own[1] / own[0] - 1, bench[1] / bench[0] - 1
        results.append({**item, 'rules': rules, 'days': age, 'cohort': day[:7],
                        'return': ret, 'benchmark': bench_ret, 'excess': ret - bench_ret})
    return {'results': results, 'no_price': no_price, 'too_recent': too_recent, 'min_days': min_days}


def _stats(rows):
    excess = [r['excess'] for r in rows]
    wins = sum(e > 0 for e in excess)
    return {'n': len(rows), 'return': statistics.mean(r['return'] for r in rows), 'excess': statistics.mean(excess),
            'median_excess': statistics.median(excess), 'wins': wins}


def _section_text(title, rows, pending, missing, min_days):
    lines = [f'## {title}', '']
    if rows:
        s = _stats(rows)
        best, worst = max(rows, key=lambda r: r['excess']), min(rows, key=lambda r: r['excess'])
        lines += [f"- Acciones evaluadas: {s['n']}. Rentabilidad media: {s['return']:+.1%}. "
                  f"Exceso medio sobre {BENCHMARK}: {s['excess']:+.1%} (mediano {s['median_excess']:+.1%}).",
                  f"- Superaron al indice: {s['wins']} de {s['n']} ({s['wins'] / s['n']:.0%}). "
                  f"Mejor: {label(best)} ({best['excess']:+.1%}); peor: {label(worst)} ({worst['excess']:+.1%}).", '',
                  '| Mes de entrada | Acciones | Rentabilidad media | Exceso medio |', '|---|---:|---:|---:|']
        for cohort in sorted({r['cohort'] for r in rows}):
            group = [r for r in rows if r['cohort'] == cohort]
            g = _stats(group)
            lines.append(f"| {cohort} | {g['n']} | {g['return']:+.1%} | {g['excess']:+.1%} |")
        lines.append('')
        for cohort in sorted({r['cohort'] for r in rows}):
            group = sorted((r for r in rows if r['cohort'] == cohort), key=lambda r: -r['excess'])
            lines.append(f"- **{cohort}:** " + ', '.join(f"{label(r)} {r['return']:+.1%} (exc. {r['excess']:+.1%})" for r in group))
        if len(rows) < MIN_SAMPLE_FOR_CONCLUSION:
            lines += ['', f'**Muestra de {len(rows)} < {MIN_SAMPLE_FOR_CONCLUSION}: no permite concluir si esta seccion aporta algo. Dato provisional.**']
    else:
        lines.append(f'Aun no hay acciones con al menos {min_days} dias de recorrido.')
    if pending:
        lines += ['', f'Pendientes (menos de {min_days} dias): ' + ', '.join(f"{label(p)} ({p['date']})" for p in sorted(pending, key=lambda p: p['date']))]
    if missing:
        lines += ['', 'Sin precio (baja de bolsa o cambio de ticker; NO se ignoran, revisar a mano): '
                  + ', '.join(sorted(label(m) for m in missing))]
    return lines + ['']


def summarize(evaluation):
    results, no_price, too_recent = evaluation['results'], evaluation['no_price'], evaluation['too_recent']
    min_days = evaluation.get('min_days', 30)
    lines = ['# Seguimiento mensual de las recomendaciones (prueba hacia adelante)', '']
    names = [n for n in SECTIONS if any(x['section'] == n for x in results + no_price + too_recent)]
    names += sorted({x['section'] for x in results + no_price + too_recent} - set(SECTIONS))
    evaluated = [n for n in names if any(r['section'] == n for r in results)]
    if evaluated:
        lines += ['| Seccion | Evaluadas | Rentabilidad media | Exceso medio | Superan al indice |', '|---|---:|---:|---:|---:|']
        for name in evaluated:
            s = _stats([r for r in results if r['section'] == name])
            lines.append(f"| {SECTIONS.get(name, name)} | {s['n']} | {s['return']:+.1%} | {s['excess']:+.1%} | {s['wins'] / s['n']:.0%} |")
        lines.append('')
    for name in names:
        pick = lambda seq: [x for x in seq if x['section'] == name]
        lines += _section_text(SECTIONS.get(name, name), pick(results), pick(too_recent), pick(no_price), min_days)
    if not names:
        lines.append('Aun no hay recomendaciones guardadas.')
    lines += ['## Limites', '',
              'Cada accion cuenta una vez por seccion, desde su primera aparicion. Rentabilidad con precio ajustado por '
              'dividendos, sin comisiones ni impuestos. El indice esta en USD y las acciones en su moneda local. La cesta '
              'tematica es una lista fija que no pasa el filtro. Con pocas acciones y un periodo corto los resultados '
              'pueden deberse al azar. No es un backtest (no usa datos historicos de cada fecha) ni garantiza rentabilidad futura.']
    return '\n'.join(lines)


def yahoo_series(symbol, day):
    import yfinance as yf
    try:
        end = datetime.now(timezone.utc) + timedelta(days=1)
        close = yf.Ticker(symbol).history(start=day, end=end.date().isoformat())['Close'].dropna()
    except Exception:
        return None
    return (float(close.iloc[0]), float(close.iloc[-1])) if len(close) >= 2 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--min-days', type=int, default=30)
    parser.add_argument('--send', action='store_true', help='Enviar el resumen a Telegram')
    args = parser.parse_args(argv)
    history = load_history(args.history)
    if not history:
        text = '# Seguimiento mensual\n\nAun no hay decisiones guardadas en ' + str(args.history)
    else:
        text = summarize(evaluate(history, yahoo_series, min_days=args.min_days))
    out = args.history.parent / 'prueba_adelante.md'
    out.write_text(text + '\n', encoding='utf-8')
    print(text)
    if args.send:
        import screener  # reutiliza el envio ya validado con reply_markup y errores seguros
        screener.send_telegram_document(str(out), 'Seguimiento mensual de las recomendaciones')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
