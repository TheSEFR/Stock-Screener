"""Regresiones offline de las explicaciones en lenguaje llano: python -m unittest -v test_explain.py"""
import unittest

import explain as e


class ExplainTests(unittest.TestCase):
    def test_state_letters(self):
        for status, letter in (('candidata', 'C'), ('revisar_datos', 'R'), ('descartada_riesgo', 'D'),
                               ('no_cumple', 'N'), ('duplicada', 'X'), ('otro', '?')):
            self.assertEqual(e.state_letter({'status': status}), letter)

    def test_known_reasons_are_translated_to_plain_language(self):
        for technical in ('Menos de 7 de 8 criterios con datos válidos', 'EPS no positivo', 'FCF no positivo',
                          'Deuda neta/EBITDA > 4', 'Se requieren al menos dos ROIC con capital medio verificable',
                          'Conclusion fragil: el margen de seguridad no aguanta hipotesis peores (ver sensibilidad)'):
            self.assertNotEqual(e.humanize(technical), technical)

    def test_missing_field_reasons_name_the_missing_data(self):
        self.assertIn('beneficio por acción', e.humanize('Sin dato verificable: eps'))
        self.assertIn('valor contable', e.humanize('Sin dato verificable: book_value'))
        self.assertIn('caja libre', e.humanize('Sin dato verificable: fcf_yield'))

    def test_unknown_reason_is_kept_not_lost(self):
        self.assertEqual(e.humanize('Motivo nuevo que nadie tradujo'), 'Motivo nuevo que nadie tradujo')

    def test_duplicate_reason_names_the_other_listing(self):
        text = e.humanize('Misma empresa que BABA (otra bolsa): se evalua solo esa cotizacion')
        self.assertIn('BABA', text)

    def test_review_row_explains_why_and_dedupes(self):
        row = {'status': 'revisar_datos', 'reasons': ['Sin dato verificable: eps', 'Sin dato verificable: eps',
                                                      'Menos de 7 de 8 criterios con datos válidos']}
        info = e.explain(row)
        self.assertEqual((info['letter'], info['title']), ('R', 'Revisar'))
        self.assertEqual(len(info['points']), 2)
        self.assertIsNone(info['valuation'])

    def test_candidate_shows_valuation_in_plain_words(self):
        row = {'status': 'candidata', 'reasons': [],
               'valuation': {'valuation_available': True, 'conviction': 'revisar_historial',
                             'conviction_reasons': ['Se requieren al menos dos ROIC con capital medio verificable']}}
        info = e.explain(row)
        self.assertTrue(info['valuation'].startswith('Revisar historial'))
        self.assertIn('faltan datos de su balance', info['valuation_points'][0])

    def test_valuation_labels(self):
        self.assertEqual(e.valuation_label('prioridad_alta_para_estudio'), 'Prioridad alta para estudio')
        self.assertEqual(e.valuation_label('vigilar_precio').split(':')[0], 'Vigilar precio')
        self.assertEqual(e.valuation_label(None), 'Sin valoración')


if __name__ == '__main__':
    unittest.main()
