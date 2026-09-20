"""Regresiones offline de la prueba hacia adelante: python -m unittest -v test_forward_test.py"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import forward_test as f

NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)


def entry(day, *symbols, **sections):
    """Decision antigua (solo 'candidates') o, con sections=..., decision nueva por secciones."""
    e = {'date': day, 'rules': 'v1', 'candidates': [{'symbol': s} for s in symbols]}
    if sections:
        e['sections'] = {name: [{'symbol': s} for s in syms] for name, syms in sections.items()}
    return e


def fixed(prices):
    return lambda symbol, day: prices.get(symbol)


class ForwardTestTests(unittest.TestCase):
    def test_record_run_replaces_same_day(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'h.jsonl'
            day = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
            f.record_run([{'symbol': 'AAA', 'current_price': 1}], path, day)
            f.record_run([{'symbol': 'BBB', 'current_price': 2}], path, day)
            history = f.load_history(path)
        self.assertEqual(len(history), 1)
        self.assertEqual([c['symbol'] for c in history[0]['candidates']], ['BBB'])

    def test_record_run_saves_all_sections_and_keeps_candidates_for_alerts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'h.jsonl'
            f.record_run({'principales': [{'symbol': 'AAA'}], 'pequena_capitalizacion': [{'symbol': 'SML'}],
                          'cesta_tematica': [{'symbol': 'THM'}]}, path, NOW)
            saved = f.load_history(path)[0]
        self.assertEqual(set(saved['sections']), {'principales', 'pequena_capitalizacion', 'cesta_tematica'})
        self.assertEqual([c['symbol'] for c in saved['candidates']], ['AAA'])

    def test_old_entries_count_as_principal(self):
        self.assertEqual(set(f.first_appearances([entry('2026-08-01', 'AAA')])), {('principales', 'AAA')})

    def test_ticker_counts_once_per_section_from_first_appearance(self):
        history = [entry('2026-08-01', principales=['AAA'], cesta_tematica=['AAA']),
                   entry('2026-09-01', principales=['AAA', 'BBB'])]
        first = f.first_appearances(history)
        self.assertEqual(first[('principales', 'AAA')][0], '2026-08-01')
        self.assertEqual(first[('principales', 'BBB')][0], '2026-09-01')
        self.assertIn(('cesta_tematica', 'AAA'), first)

    def test_excess_return_vs_benchmark(self):
        ev = f.evaluate([entry('2026-08-01', 'AAA')], fixed({'AAA': (100, 120), f.BENCHMARK: (100, 110)}), NOW)
        self.assertAlmostEqual(ev['results'][0]['excess'], .10)
        self.assertEqual(ev['results'][0]['cohort'], '2026-08')
        self.assertEqual((ev['no_price'], ev['too_recent']), ([], []))

    def test_recent_candidates_are_not_evaluated(self):
        ev = f.evaluate([entry('2026-11-20', 'AAA')], lambda s, d: (1, 2), NOW)
        self.assertEqual(ev['results'], [])
        self.assertEqual(ev['too_recent'][0]['symbol'], 'AAA')

    def test_missing_price_is_listed_not_dropped(self):
        ev = f.evaluate([entry('2026-08-01', 'GONE')], fixed({f.BENCHMARK: (100, 110)}), NOW)
        self.assertEqual((ev['results'], [m['symbol'] for m in ev['no_price']]), ([], ['GONE']))
        self.assertIn('GONE', f.summarize(ev))

    def test_prices_are_fetched_once_per_symbol_and_day(self):
        calls = []
        def series(symbol, day):
            calls.append((symbol, day))
            return (100, 110)
        f.evaluate([entry('2026-08-01', principales=['AAA'], cesta_tematica=['AAA'])], series, NOW)
        self.assertEqual(len(calls), len(set(calls)))

    def test_summary_splits_by_section_and_month(self):
        history = [entry('2026-08-01', principales=['AAA'], pequena_capitalizacion=['SML']),
                   entry('2026-09-01', principales=['BBB'])]
        prices = {'AAA': (100, 130), 'SML': (100, 90), 'BBB': (100, 105), f.BENCHMARK: (100, 110)}
        text = f.summarize(f.evaluate(history, fixed(prices), NOW))
        for expected in ('Principales acciones', 'Pequeña capitalización', '| 2026-08 |', '| 2026-09 |', 'AAA +30.0%', 'SML -10.0%'):
            self.assertIn(expected, text)

    def test_summary_uses_company_name_for_numeric_tickers(self):
        e = {'date': '2026-08-01', 'rules': 'v1', 'sections': {'principales': [{'symbol': '005930.KS', 'name': 'Samsung'}]}}
        self.assertIn('Samsung (005930.KS)', f.summarize(f.evaluate([e], lambda s, d: (100, 110), NOW)))

    def test_small_sample_is_flagged(self):
        ev = f.evaluate([entry('2026-08-01', 'AAA')], lambda s, d: (100, 110), NOW)
        self.assertIn('no permite concluir', f.summarize(ev))

    def test_pending_are_listed(self):
        ev = f.evaluate([entry('2026-11-25', principales=['NEW'])], lambda s, d: (100, 110), NOW)
        self.assertIn('Pendientes', f.summarize(ev))
        self.assertIn('NEW', f.summarize(ev))

    def test_empty_history(self):
        self.assertEqual(f.load_history(Path(tempfile.gettempdir()) / 'no-existe-h.jsonl'), [])
        self.assertIn('Aun no hay', f.summarize({'results': [], 'no_price': [], 'too_recent': []}))


if __name__ == '__main__':
    unittest.main()
