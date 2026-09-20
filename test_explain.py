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

    def test_every_translatable_reason_has_an_example_in_the_guide(self):
        guided = {reason for _, items in e.GUIDE for reason, _, _ in items}
        self.assertEqual(set(e._EXACT) - guided, set())  # ningun motivo traducible sin ejemplo
        for _, items in e.GUIDE:
            for reason, title, example in items:
                self.assertTrue(title and len(example) > 40, reason)
                self.assertNotEqual(e.humanize(reason), reason, reason)  # el texto llano existe

    def test_texts_only_use_characters_the_pdf_font_can_draw(self):
        # La fuente del PDF es latin-1: un caracter fuera (como el simbolo del euro) desaparece en silencio.
        texts = list(e._EXACT.values()) + list(e.STATE_TEXT.values()) + list(e.VALUATION_LABELS.values()) + [e.LEGEND]
        texts += [title + ' ' + example for _, items in e.GUIDE for _, title, example in items]
        for text in texts:
            text.encode('latin-1')  # lanza UnicodeEncodeError si hay un caracter que no se puede dibujar

    def test_guide_has_no_repeated_reasons(self):
        reasons = [reason for _, items in e.GUIDE for reason, _, _ in items]
        self.assertEqual(len(reasons), len(set(reasons)))

    def test_points_include_the_stocks_own_figures(self):
        row = {'status': 'descartada_riesgo', 'net_debt_ebitda': 5.2, 'fcf_yield': -0.004, 'reasons': [
            'Deuda neta/EBITDA > 4', 'FCF no positivo']}
        points = e.explain(row)['points']
        self.assertIn('En su caso: su deuda neta es 5,2 veces su EBITDA', points[0])
        self.assertIn('-0,4%', points[1])

    def test_value_criteria_are_listed_in_plain_words(self):
        row = {'status': 'no_cumple', 'reasons': ['Menos de 3 de 4 señales de valor/crecimiento'],
               'checks': {'pe_descuento_20': False, 'fcf_5': True, 'eps_futuro_15': None, 'ingresos_5': False}}
        text = e.explain(row)['points'][0]
        self.assertIn('Cumple: caja libre', text)
        self.assertIn('No cumple: P/E', text)
        self.assertIn('Sin dato: beneficio estimado', text)

    def test_missing_criteria_are_named(self):
        row = {'status': 'revisar_datos', 'reasons': ['Menos de 7 de 8 criterios con datos válidos'],
               'checks': {'pe_descuento_20': None, 'fcf_5': True}, 'quality_checks': {'roe_15': None}}
        text = e.explain(row)['points'][0]
        self.assertIn('no se pudo comprobar', text)
        self.assertIn('P/E', text)
        self.assertIn('ROE', text)

    def test_valuation_figures(self):
        row = {'status': 'candidata', 'reasons': [], 'valuation': {
            'valuation_available': True, 'conviction': 'vigilar_precio', 'margin_of_safety': -0.34,
            'sensitivity': {'share_margin_ok': 0.0},
            'conviction_reasons': ['Margen de seguridad base inferior al 25%',
                                   'Conclusion fragil: el margen de seguridad no aguanta hipotesis peores (ver sensibilidad)']}}
        points = e.explain(row)['valuation_points']
        self.assertIn('-34%', points[0])
        self.assertIn('0%', points[1])

    def test_missing_figure_does_not_break_the_reason(self):
        text = e.explain({'status': 'descartada_riesgo', 'reasons': ['Deuda neta/EBITDA > 4']})['points'][0]
        self.assertIn('muy endeudada', text)
        self.assertNotIn('En su caso', text)  # sin cifra no se inventa ni se escribe "n/d"

    def test_valuation_labels(self):
        self.assertEqual(e.valuation_label('prioridad_alta_para_estudio'), 'Prioridad alta para estudio')
        self.assertEqual(e.valuation_label('vigilar_precio').split(':')[0], 'Vigilar precio')
        self.assertEqual(e.valuation_label(None), 'Sin valoración')


if __name__ == '__main__':
    unittest.main()
