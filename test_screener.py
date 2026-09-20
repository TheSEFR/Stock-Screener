"""Regresiones offline: python -m unittest -v test_screener.py"""
import copy
import json
import tempfile
import uuid
from contextlib import contextmanager
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import screener_core as c
import screener as s
import conviction as v


@contextmanager
def test_directory():
    # mkdir con ACL heredada: TemporaryDirectory usa 0700, incompatible con
    # algunos ejecutores Windows aislados. Fixtures conservadas en .qa_tmp.
    path=Path(__file__).parent/'.qa_tmp'/uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path


class ScreenerTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=s.demo_snapshot()
        self.rows=s.replay(self.snapshot)
        self.good=copy.deepcopy(self.rows[0])

    def test_demo_selects_valid_candidate(self):
        self.assertTrue(self.good['eligible'])
        self.assertEqual(self.good['opportunity_score'],100)

    def test_fragile_conclusion_is_not_high_priority(self):
        # Precio de la demo (20): margen base 30% pero solo aguanta 51% de las hipotesis.
        self.assertTrue(self.good['historical']['history_healthy'])
        valuation=self.good['valuation']
        self.assertFalse(valuation['sensitivity']['robust'])
        self.assertEqual(valuation['conviction'],'vigilar_precio')
        self.assertTrue(any('fragil' in r for r in valuation['conviction_reasons']))

    def test_robust_conclusion_can_be_high_priority(self):
        self.good['current_price']=12
        valuation=v.scenarios(self.good,self.good['historical'],{'USD':1})
        self.assertTrue(valuation['sensitivity']['robust'])
        self.assertEqual(valuation['conviction'],'prioridad_alta_para_estudio')

    def test_historical_losses_block_conviction(self):
        statements=copy.deepcopy(self.snapshot['records'][0]['statements'])
        statements['income'][1]['Net Income']=-1
        h=v.historical_quality(statements)
        self.assertFalse(h['history_healthy'])

    def test_historical_dilution(self):
        statements=copy.deepcopy(self.snapshot['records'][0]['statements'])
        statements['income'][0]['Diluted Average Shares']=60e6
        h=v.historical_quality(statements)
        self.assertGreater(h['dilution_cagr'],.02)
        self.assertFalse(h['history_healthy'])

    def test_historical_fcf_negative(self):
        statements=copy.deepcopy(self.snapshot['records'][0]['statements'])
        statements['cashflow'][1]['Free Cash Flow']=-1
        self.assertFalse(v.historical_quality(statements)['history_healthy'])

    def test_historical_roic_missing(self):
        statements=copy.deepcopy(self.snapshot['records'][0]['statements'])
        statements['balance']=[]
        h=v.historical_quality(statements)
        self.assertFalse(h['history_healthy'])
        self.assertIsNone(h['min_roic'])

    def test_scenario_growth_cap(self):
        self.good['growth']=.8
        result=v.scenarios(self.good,self.good['historical'],{'USD':1})
        self.assertEqual(result['scenarios']['base']['eps_growth'],.08)
        self.assertEqual(result['scenarios']['favorable']['eps_growth'],.12)

    def test_scenario_discount_and_margin(self):
        result=self.good['valuation']
        base=result['scenarios']['base']
        self.assertAlmostEqual(base['present_value'],base['terminal_price']/1.12**5)
        self.assertAlmostEqual(result['margin_of_safety'],1-self.good['current_price']/base['present_value'])
        self.assertAlmostEqual(result['study_price_limit'],.75*base['present_value'])

    def test_scenario_no_data_no_value(self):
        self.assertFalse(v.scenarios(self.good,{},{}).get('valuation_available'))

    def test_scenario_not_high_for_expensive_price(self):
        self.good['current_price']=200
        result=v.scenarios(self.good,self.good['historical'],{'USD':1})
        self.assertNotEqual(result['conviction'],'prioridad_alta_para_estudio')

    def test_scenario_higher_discount_lowers_value(self):
        a=v.scenarios(self.good,self.good['historical'],{'USD':1},required_return=.1)
        b=v.scenarios(self.good,self.good['historical'],{'USD':1},required_return=.2)
        self.assertGreater(a['scenarios']['base']['present_value'],b['scenarios']['base']['present_value'])

    def test_scenario_pence_conversion(self):
        self.good.update(currency='GBp',financial_currency='GBP',current_price=2000)
        result=v.scenarios(self.good,self.good['historical'],{'GBP':1.25})
        self.assertAlmostEqual(result['normalized_eps_quote'],190)

    def test_losses_block(self):
        self.assertEqual(self.rows[6]['status'],'descartada_riesgo')

    def test_missing_cash_is_not_zero(self):
        self.assertIsNone(self.rows[7]['net_debt_ebitda'])
        self.assertFalse(self.rows[7]['eligible'])

    def test_finite_numbers(self):
        for x in [True,False,'bad',float('nan'),float('inf'),None]:
            self.assertIsNone(c.number(x))
        self.assertEqual(c.number('0'),0)

    def test_missing_growth_cannot_improve_score(self):
        self.good['growth']=-.2
        c.score(self.good)
        before=self.good['score_ratio']
        self.good['growth']=None
        c.score(self.good)
        self.assertEqual(before,self.good['score_ratio'])

    def test_insider_is_context_not_score(self):
        a,b=copy.deepcopy(self.good),copy.deepcopy(self.good)
        a['insider_buying']=True
        b['insider_buying']=None
        self.assertEqual(c.evaluate(a)['opportunity_score'],c.evaluate(b)['opportunity_score'])

    def test_risk_missing_blocks(self):
        for key in ['eps','ebitda','net_debt_ebitda','avg_dollar_volume','book_value','fcf_yield','market_cap_usd','current_price','operating_margin']:
            with self.subTest(key=key):
                row=copy.deepcopy(self.good)
                row[key]=None
                self.assertFalse(c.evaluate(row)['eligible'])
                self.assertEqual(s.risk_label(row),'Revisar')

    def test_stale_quote(self):
        self.good['quote_age_days']=8
        self.assertFalse(c.evaluate(self.good)['eligible'])

    def test_future_period(self):
        self.good['financial_age_days']=-1
        self.assertFalse(c.evaluate(self.good)['eligible'])

    def test_old_financials(self):
        self.good['financial_age_days']=181
        self.assertFalse(c.evaluate(self.good)['eligible'])

    def test_negative_equity(self):
        self.good['book_value']=-2
        self.good['roe']=3
        self.assertFalse(c.evaluate(self.good)['eligible'])

    def test_non_equity(self):
        self.good['quote_type']='ETF'
        self.assertFalse(c.evaluate(self.good)['eligible'])

    def test_banks_need_other_model(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info['sector']='Financial Services'
        row=c.normalize('BANK',info,{},lambda _:1)
        self.assertTrue(row['unsupported_model'])
        self.assertFalse(c.evaluate(row)['eligible'])

    def test_fx_pence(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info.update(currency='GBp',financialCurrency='GBP',currentPrice=2000)
        row=c.normalize('TEST.L',info,{},lambda _:1.25)
        self.assertEqual(row['market_cap_usd'],1.25e9)
        self.assertEqual(row['avg_dollar_volume'],50e6)
        self.assertAlmostEqual(row['fcf_yield'],.08)

    def test_fx_different_reporting_currency(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info['financialCurrency']='EUR'
        row=c.normalize('TEST',info,{}, {'USD':1,'EUR':1.2}.get)
        self.assertAlmostEqual(row['fcf_yield'],.096)

    def test_missing_fx(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info['currency']='JPY'
        row=c.normalize('TEST',info,{}, {'USD':1}.get)
        self.assertIsNone(row['avg_dollar_volume'])
        self.assertIsNone(row['market_cap_usd'])

    def test_missing_financial_currency(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info.pop('financialCurrency')
        row=c.normalize('TEST',info,{}, {'USD':1}.get)
        self.assertIsNone(row['fcf_yield'])

    def test_smallcap_uses_usd(self):
        self.assertTrue(s.is_small_cap({'market_cap':100e9,'market_cap_usd':.8e9}))
        self.assertFalse(s.is_small_cap({'market_cap':1e9,'market_cap_usd':3e9}))
        self.assertFalse(s.is_small_cap({'market_cap':1e6,'market_cap_usd':None}))

    def test_cap_currency_ambiguity(self):
        info=copy.deepcopy(self.snapshot['records'][0]['info'])
        info['marketCap']=10e9
        row=c.normalize('TEST',info,{}, {'USD':1}.get)
        self.assertTrue(row['cap_mismatch'])
        self.assertIsNone(row['market_cap_usd'])

    def test_no_self_comparison(self):
        self.assertEqual(self.good['sector_avg_pe'],19)
        lone=[copy.deepcopy(self.good)]
        c.add_peers(lone)
        self.assertIsNone(lone[0]['sector_avg_pe'])

    def test_peer_median_resists_outlier(self):
        rows=copy.deepcopy(self.rows)
        rows[-1]['pe']=10000
        c.add_peers(rows)
        self.assertEqual(rows[0]['sector_avg_pe'],19)

    def test_peers_cannot_cross_country(self):
        rows=copy.deepcopy(self.rows)
        rows[0]['country']='Spain'
        c.add_peers(rows)
        self.assertIsNone(rows[0]['sector_avg_pe'])

    def test_growth_forecast(self):
        result=c.expected_growth({'avg':2,'low':1.9,'high':2.1,'numberOfAnalysts':6},
                                 {'avg':2.5,'low':2.4,'high':2.6,'numberOfAnalysts':4})
        self.assertAlmostEqual(result['growth'],.25)
        self.assertTrue(result['growth_reliable'])
        self.assertEqual(result['num_analysts'],4)

    def test_unreliable_forecasts(self):
        base={'avg':2,'low':1.9,'high':2.1,'numberOfAnalysts':5}
        for change in [dict(avg=-1),dict(numberOfAnalysts=1),dict(high=20),dict(low=None),dict(avg=50,low=49,high=51)]:
            following={**base,**change}
            self.assertFalse(c.expected_growth(base,following)['growth_reliable'])

    def test_fmp_sorts_dates(self):
        records=[dict(date='2028-12-31',epsAvg=20,epsLow=19,epsHigh=21,numAnalystsEps=6),
                 dict(date='2027-12-31',epsAvg=12,epsLow=11,epsHigh=13,numAnalystsEps=7),
                 dict(date='2026-12-31',epsAvg=10,epsLow=9,epsHigh=11,numAnalystsEps=5)]
        result=c.fmp_estimates(records,datetime(2026,9,20,tzinfo=timezone.utc))
        self.assertAlmostEqual(result['growth'],.2)
        self.assertIn('2026-12-31',result['growth_period'])

    def test_fmp_rejects_nonconsecutive_years(self):
        self.assertEqual(c.fmp_estimates([{'date':'2026-12-31'},{'date':'2028-12-31'}],datetime(2026,9,20,tzinfo=timezone.utc)),{})

    def test_form4_transactions(self):
        now=datetime(2026,9,20,tzinfo=timezone.utc)
        xml='''<ownershipDocument xmlns="urn:test"><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>{date}</value></transactionDate><transactionCoding><transactionCode>{code}</transactionCode></transactionCoding><transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'''
        self.assertTrue(c.parse_form4(xml.format(date='2026-09-10',code='P'),now))
        self.assertFalse(c.parse_form4(xml.format(date='2025-09-10',code='P'),now))
        self.assertFalse(c.parse_form4(xml.format(date='2026-09-10',code='A'),now))
        self.assertIsNone(c.parse_form4(xml.format(date='bad',code='P'),now))

    def test_form4_bad_xml_not_negative(self):
        self.assertIsNone(c.parse_form4('<html>Denied</html>'))
        self.assertIsNone(c.parse_form4('broken'))

    def test_sec_requires_real_identity(self):
        client=unittest.mock.Mock(spec=["get"])
        self.assertIsNone(c.sec_insider('AAPL','',client))
        self.assertIsNone(c.sec_insider('AAPL','Bot x@example.com',client))
        client.get.assert_not_called()

    def test_sec_pipeline_downloads_raw_xml(self):
        now=datetime(2026,9,20,tzinfo=timezone.utc)
        client=unittest.mock.Mock(spec=["get"])
        client.get.side_effect=[{'0':{'ticker':'TEST','cik_str':123}},
            {'filings':{'recent':{'form':['4','10-K'],'filingDate':['2026-09-15','2026-01-01'],
                'accessionNumber':['123-26-0001','123-26-0000'],'primaryDocument':['xslF345X05/test.xml','old.htm']}}},
            '<ownershipDocument><nonDerivativeTransaction><transactionDate><value>2026-09-14</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction></ownershipDocument>']
        self.assertTrue(c.sec_insider('TEST','Unit test tester@unit.test',client,now))
        self.assertNotIn('xslF345',client.get.call_args.args[0])

    def test_sec_http_failure_is_unknown(self):
        client=unittest.mock.Mock(spec=["get"])
        client.get.side_effect=RuntimeError('Proveedor HTTP 403')
        self.assertIsNone(c.sec_insider('TEST','Unit test tester@unit.test',client))

    def test_sec_partial_window_unknown(self):
        client=unittest.mock.Mock(spec=["get"])
        client.get.side_effect=[{'0':{'ticker':'TEST','cik_str':123}},
            {'filings':{'recent':{'form':['10-Q'],'filingDate':['2026-09-15'],
                'accessionNumber':['123-26-0001'],'primaryDocument':['q.htm']}}}]
        self.assertIsNone(c.sec_insider('TEST','Unit test tester@unit.test',client,datetime(2026,9,20,tzinfo=timezone.utc)))

    def test_sec_complete_window_no_buys(self):
        client=unittest.mock.Mock(spec=["get"])
        client.get.side_effect=[{'0':{'ticker':'TEST','cik_str':123}},
            {'filings':{'recent':{'form':['10-K'],'filingDate':['2026-01-01'],
                'accessionNumber':['123-26-0001'],'primaryDocument':['k.htm']}}}]
        self.assertFalse(c.sec_insider('TEST','Unit test tester@unit.test',client,datetime(2026,9,20,tzinfo=timezone.utc)))

    def test_stable_ranking(self):
        self.assertEqual([r['symbol'] for r in c.rank(self.rows)], [r['symbol'] for r in c.rank(list(reversed(self.rows)))])

    def test_watchlist_dedup_comments(self):
        with test_directory() as d:
            p=Path(d)/'watchlist.txt'
            p.write_text('aapl # comment\nAAPL\n\n# skip\nBRK-B\n',encoding='utf-8')
            self.assertEqual(s.load_watchlist(p),['AAPL','BRK-B'])

    def test_ticker_failure_does_not_abort(self):
        fake=unittest.mock.Mock()
        bad=unittest.mock.Mock()
        type(bad).info=unittest.mock.PropertyMock(side_effect=ValueError('SECRET'))
        good=unittest.mock.Mock()
        good.info=self.snapshot['records'][0]['info']
        good.earnings_estimate=None
        fake.Ticker.side_effect=[bad,good]
        with patch.object(s,'yf',fake),patch.object(s.time,'sleep'),patch.object(s,'FMP_API_KEY',None):
            rows,_=s.analyze(['BAD','GOOD'])
        self.assertEqual(len(rows),1)
        self.assertEqual(s.analyze.errors[0]['symbol'],'BAD')
        self.assertNotIn('SECRET',json.dumps(s.analyze.errors))

    def test_demo_no_network_or_telegram(self):
        with test_directory() as d,patch.object(s.HTTP,'get',side_effect=AssertionError('Network')),patch.object(s,'send_telegram_document',side_effect=AssertionError('Send')):
            self.assertEqual(s.main(['--demo','--output',str(d)]),0)
            self.assertTrue((Path(d)/'resultados.csv').exists())
            self.assertFalse((Path(d)/'informe.html').exists())
            payload=json.loads((Path(d)/'resultados.json').read_text(encoding='utf-8'))
            self.assertEqual(len(payload['rows']),8)

    def test_bot_entrypoint_requests_pdf_and_send(self):
        with patch.object(s,'main',return_value=0) as main:
            s.generate_and_send_report()
        main.assert_called_once_with(['--pdf','--enrich','--send'])

    def test_bot_entrypoint_fails_loudly(self):
        with patch.object(s,'main',return_value=2):
            with self.assertRaises(RuntimeError):
                s.generate_and_send_report()

    def test_telegram_send_keeps_generate_button(self):
        response=unittest.mock.Mock(ok=True)
        response.json.return_value={'ok':True}
        with test_directory() as d,patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'t','TELEGRAM_CHAT_ID':'1'}),\
             patch.object(s.requests,'post',return_value=response) as post:
            pdf=Path(d)/'x.pdf'
            pdf.write_bytes(b'%PDF')
            s.send_telegram_document(str(pdf),'caption')
        self.assertEqual(json.loads(post.call_args.kwargs['data']['reply_markup']),s.GENERATE_NOW_BUTTON)

    def test_sensitivity_includes_base_and_worse_cases(self):
        result=self.good['valuation']
        sens=result['sensitivity']
        self.assertEqual(sens['combinations'],81)
        self.assertLessEqual(sens['min_margin'],result['margin_of_safety'])

    def test_sensitivity_flags_fragile_conclusion(self):
        self.good['current_price']=self.good['valuation']['scenarios']['base']['present_value']*.98
        sens=v.scenarios(self.good,self.good['historical'],{'USD':1})['sensitivity']
        self.assertFalse(sens['robust'])
        self.assertLess(sens['min_margin'],0)

    def test_sensitivity_low_required_return_does_not_crash(self):
        result=v.scenarios(self.good,self.good['historical'],{'USD':1},required_return=.01)
        self.assertEqual(result['sensitivity']['combinations'],81)

    def test_cache_disabled_by_default_and_corruption_is_ignored(self):
        now=datetime.now(timezone.utc)
        with test_directory() as d,patch.object(s,'CACHE_DIR',Path(d)):
            s._cache_save('GOOD',now,{'info':{'a':1},'estimates':{}})
            self.assertIsNone(s._cache_load('GOOD',now))  # sin --resume no se lee ni escribe
            self.assertFalse(any(Path(d).iterdir()))
            with patch.object(s,'USE_CACHE',True):
                path=s._cache_file('GOOD',now)
                path.parent.mkdir(parents=True)
                path.write_text('{roto',encoding='utf-8')
                self.assertIsNone(s._cache_load('GOOD',now))

    def test_resume_reuses_cached_download_without_network(self):
        info=self.snapshot['records'][0]['info']
        now=datetime.now(timezone.utc)
        with test_directory() as d,patch.object(s,'USE_CACHE',True),patch.object(s,'CACHE_DIR',Path(d)):
            s._cache_save('GOOD',now,{'info':info,'estimates':{}})
            fake=unittest.mock.Mock()
            with patch.object(s,'yf',fake),patch.object(s.time,'sleep'),\
                 patch.object(s,'_fetch_ticker',side_effect=AssertionError('Network')):
                rows,_=s.analyze(['GOOD'])
        self.assertEqual([r['symbol'] for r in rows],['GOOD'])

    def test_fetch_failure_is_not_cached(self):
        now=datetime.now(timezone.utc)
        bad=unittest.mock.Mock()
        type(bad).info=unittest.mock.PropertyMock(side_effect=ValueError('x'))
        fake=unittest.mock.Mock()
        fake.Ticker.return_value=bad
        with test_directory() as d,patch.object(s,'USE_CACHE',True),patch.object(s,'CACHE_DIR',Path(d)),\
             patch.object(s,'yf',fake),patch.object(s.time,'sleep'):
            rows,_=s.analyze(['BAD'])
            self.assertEqual(rows,[])
            self.assertIsNone(s._cache_load('BAD',now))

    def test_numeric_ticker_shows_company_name(self):
        info={'longName':'Samsung Electronics Co., Ltd.','shortName':'SamsungElec'}
        self.assertEqual(s.display_name(info,'005930.KS'),'Samsung')
        self.assertEqual(s.display_name(info,'AAPL'),'AAPL')
        self.assertEqual(s.shown_name({'symbol':'005930.KS','name':'Samsung'}),'Samsung (005930.KS)')
        self.assertEqual(s.shown_name({'symbol':'AAPL','name':'AAPL'}),'AAPL')
        self.assertEqual(s.shown_name({'symbol':'AAPL'}),'AAPL')

    def test_long_single_word_name_is_truncated_for_the_table(self):
        self.assertEqual(s.display_name({'longName':'Volkswagenwerke AG'},'0000.X'),'Volkswag.')

    def test_csv_escapes_formulas(self):
        self.rows[0]['symbol']='=1+1'
        with test_directory() as d:
            s.write_reports(self.rows,[],self.snapshot,Path(d))
            csv=(Path(d)/'resultados.csv').read_text(encoding='utf-8-sig')
            self.assertIn("'=1+1",csv)


if __name__=='__main__':
    unittest.main()
