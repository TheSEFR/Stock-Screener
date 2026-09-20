"""Regresiones offline de las alertas: python -m unittest -v test_alerts.py"""
import unittest

import alerts


def entry(day, *candidates):
    return {'date': day, 'candidates': list(candidates)}


def cand(symbol, **kw):
    return {'symbol': symbol, 'price': 100, 'study_price_limit': 80, 'fcf_yield': .08,
            'quality_ratio': .75, 'conviction': 'vigilar_precio', **kw}


class AlertsTests(unittest.TestCase):
    def kinds(self, previous, today):
        return {(a['symbol'], a['type']) for a in alerts.compare(previous, today)}

    def test_no_previous_means_no_alerts(self):
        self.assertEqual(alerts.compare(None, entry('2026-09-21', cand('A'))), [])

    def test_no_change_no_alerts(self):
        e = entry('d', cand('A'))
        self.assertEqual(alerts.compare(e, e), [])
        self.assertIn('Sin cambios', alerts.format_text([], 'a', 'b'))

    def test_first_run_message_is_not_misleading(self):
        self.assertIn('Primera decision', alerts.format_text([], None, '2026-09-21'))

    def test_alert_shows_company_name_for_numeric_tickers(self):
        today = entry('b', {'symbol': '005930.KS', 'name': 'Samsung'})
        text = alerts.format_text(alerts.compare(entry('a', cand('A')), today), 'a', 'b')
        self.assertIn('Samsung (005930.KS)', text)

    def test_entering_and_leaving(self):
        self.assertEqual(self.kinds(entry('a', cand('A')), entry('b', cand('B'))),
                         {('B', 'entra'), ('A', 'sale')})

    def test_price_enters_valuation_range(self):
        self.assertIn(('A', 'entra_rango_valoracion'),
                      self.kinds(entry('a', cand('A')), entry('b', cand('A', price=79))))

    def test_already_in_range_does_not_repeat(self):
        e = entry('a', cand('A', price=70))
        self.assertNotIn(('A', 'entra_rango_valoracion'), self.kinds(e, entry('b', cand('A', price=60))))

    def test_cash_deterioration(self):
        self.assertIn(('A', 'caja_deteriora'), self.kinds(entry('a', cand('A')), entry('b', cand('A', fcf_yield=.02))))
        self.assertIn(('A', 'caja_deteriora'), self.kinds(entry('a', cand('A')), entry('b', cand('A', fcf_yield=-.01))))

    def test_small_cash_change_is_not_alert(self):
        self.assertNotIn(('A', 'caja_deteriora'), self.kinds(entry('a', cand('A')), entry('b', cand('A', fcf_yield=.07))))

    def test_quality_drop_and_conviction_change(self):
        kinds = self.kinds(entry('a', cand('A')), entry('b', cand('A', quality_ratio=.5, conviction='revisar_calidad')))
        self.assertIn(('A', 'pierde_calidad'), kinds)
        self.assertIn(('A', 'cambia_prioridad'), kinds)

    def test_missing_fields_do_not_crash(self):
        self.assertEqual(alerts.compare(entry('a', {'symbol': 'A'}), entry('b', {'symbol': 'A'})), [])


if __name__ == '__main__':
    unittest.main()
