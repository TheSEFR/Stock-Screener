"""Regresiones offline de los modelos sectoriales: python -m unittest -v test_sector_models.py"""
import unittest

import sector_models as m


def row(**kw):
    base = {'eps': 2.0, 'avg_dollar_volume': 5e7, 'quote_age_days': 1}
    base.update(kw)
    return base


class SectorModelTests(unittest.TestCase):
    def test_unknown_sector_has_no_model(self):
        self.assertIsNone(m.evaluate(row(sector='Healthcare')))

    def bank(self, **kw):
        base = dict(sector='Financial Services', industry='Banks - Regional',
                    price_to_book=.9, roe=.12, roa=.01)
        return m.evaluate(row(**{**base, **kw}))

    def test_bank_candidate(self):
        result = self.bank()
        self.assertEqual((result['model'], result['status']), ('banca_seguros', 'candidata_sector'))

    def test_bank_missing_data_is_review_not_pass(self):
        self.assertEqual(self.bank(roa=None)['status'], 'revisar_datos')

    def test_bank_expensive_and_weak_fails(self):
        self.assertEqual(self.bank(price_to_book=2.5, roe=.03, roa=.002)['status'], 'no_cumple')

    def test_losses_block_any_model(self):
        self.assertEqual(self.bank(eps=-1)['status'], 'no_cumple')

    def test_stale_quote_needs_review(self):
        self.assertEqual(self.bank(quote_age_days=30)['status'], 'revisar_datos')

    def test_reit_candidate_and_high_debt(self):
        good = dict(sector='Real Estate', fcf_yield=.06, net_debt_ebitda=5, operating_margin=.3)
        self.assertEqual(m.evaluate(row(**good))['status'], 'candidata_sector')
        self.assertEqual(m.evaluate(row(**{**good, 'net_debt_ebitda': 9}))['status'], 'no_cumple')

    def test_cyclical_uses_normalized_pe(self):
        cheap = dict(sector='Energy', current_price=10, sector_avg_pe=20, valuation={'normalized_eps_quote': 1.0})
        self.assertEqual(m.evaluate(row(**cheap))['status'], 'candidata_sector')  # PER normalizado 10 <= 16
        peak = {**cheap, 'valuation': {'normalized_eps_quote': .4}}  # PER normalizado 25
        self.assertEqual(m.evaluate(row(**peak))['status'], 'no_cumple')

    def test_cyclical_without_valuation_is_review(self):
        self.assertEqual(m.evaluate(row(sector='Energy', current_price=10, sector_avg_pe=20))['status'], 'revisar_datos')

    def test_tech_rule_of_40(self):
        ok = dict(sector='Technology', revenue_growth=.25, operating_margin=.20, fcf_yield=.03)
        self.assertEqual(m.evaluate(row(**ok))['status'], 'candidata_sector')
        slow = {**ok, 'revenue_growth': .05, 'operating_margin': .10}
        self.assertEqual(m.evaluate(row(**slow))['status'], 'no_cumple')


if __name__ == '__main__':
    unittest.main()
