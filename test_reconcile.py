"""Regresiones offline del contraste Yahoo/SEC: python -m unittest -v test_reconcile.py"""
import unittest
from unittest.mock import Mock

import reconcile as r

UA = 'Prueba tester@unit.test'


def fact(end, val, start=None, form='10-K', fp='FY', filed='2026-02-01'):
    year = int(end[:4])
    return {'end': end, 'val': val, 'start': start or f'{year - 1}{end[4:]}', 'form': form, 'fp': fp, 'filed': filed}


def facts(revenue=(), net_income=()):
    concepts = {}
    if revenue:
        concepts['Revenues'] = {'units': {'USD': list(revenue)}}
    if net_income:
        concepts['NetIncomeLoss'] = {'units': {'USD': list(net_income)}}
    return {'facts': {'us-gaap': concepts}}


def statements(revenue, net_income, date='2025-12-31'):
    return {'income': [{'date': date, 'Total Revenue': revenue, 'Net Income': net_income}]}


class ReconcileTests(unittest.TestCase):
    def test_matching_figures_are_ok(self):
        sec = facts([fact('2025-12-31', 1000)], [fact('2025-12-31', 100)])
        self.assertEqual(r.reconcile(statements(1010, 99), sec)['status'], 'ok')

    def test_large_difference_is_flagged(self):
        sec = facts([fact('2025-12-31', 1000)], [fact('2025-12-31', 100)])
        result = r.reconcile(statements(1500, 100), sec)
        self.assertEqual(result['status'], 'discrepancia')
        self.assertFalse(next(c for c in result['checks'] if c['metric'] == 'revenue')['ok'])

    def test_no_comparable_concept_is_not_a_discrepancy(self):
        self.assertEqual(r.reconcile(statements(1000, 100), facts())['status'], 'sin_datos_comparables')

    def test_non_usd_reporters_are_not_compared(self):
        sec = facts([fact('2025-12-31', 1000)])
        self.assertEqual(r.reconcile(statements(5, 5), sec, 'EUR')['status'], 'sin_datos_comparables')

    def test_quarterly_and_non_10k_facts_are_ignored(self):
        sec = facts([fact('2025-12-31', 1, start='2025-10-01'), fact('2025-12-31', 2, form='10-Q')])
        self.assertEqual(r.annual_values(sec, 'Revenues'), {})

    def test_latest_filing_wins(self):
        sec = facts([fact('2025-12-31', 1000, filed='2026-02-01'), fact('2025-12-31', 900, filed='2026-05-01')])
        self.assertEqual(r.annual_values(sec, 'Revenues'), {'2025-12-31': 900})

    def test_no_identity_means_unverified_without_network(self):
        client = Mock(spec=['get'])
        for ua in ('', 'Bot x@example.com', None):
            self.assertEqual(r.check_symbol('AAPL', {}, 'USD', ua, client)['status'], 'sin_verificar')
        client.get.assert_not_called()

    def test_fetch_failure_is_unverified(self):
        client = Mock(spec=['get'])
        client.get.side_effect = RuntimeError('Proveedor HTTP 403')
        self.assertEqual(r.check_symbol('AAPL', {}, 'USD', UA, client)['status'], 'sin_verificar')

    def test_full_flow_with_mocked_sec(self):
        client = Mock(spec=['get'])
        client.get.side_effect = [{'0': {'ticker': 'TEST', 'cik_str': 123}},
                                  facts([fact('2025-12-31', 1000)], [fact('2025-12-31', 100)])]
        result = r.check_symbol('TEST', statements(1000, 100), 'USD', UA, client)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('CIK0000000123', client.get.call_args_list[1].args[0])


if __name__ == '__main__':
    unittest.main()
