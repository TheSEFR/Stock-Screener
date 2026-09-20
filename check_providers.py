"""Diagnóstico reproducible de proveedores, sin PDF, HTML ni envíos.

python check_providers.py --symbols AAPL MSFT SAP.DE SHEL.L 7203.T 005930.KS
SEC requiere SEC_EDGAR_USER_AGENT real en .env o entorno.
"""
import argparse
import json
import re
from datetime import datetime,timezone
from pathlib import Path
import screener as s
import screener_core as core


def check_sec():
    ua=s.SEC_EDGAR_USER_AGENT
    if not ua or not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+',ua) or 'example.com' in ua:
        return {'status':'pendiente_identificacion','reason':'Configurar nombre y correo reales en SEC_EDGAR_USER_AGENT. No se ha probado la conexión SEC desde el script.'}
    client=core.HttpClient()
    result={'status':'partial','checks':{}}
    tests=[('ticker_map','https://www.sec.gov/files/company_tickers.json'),
           ('submissions','https://data.sec.gov/submissions/CIK0000320193.json'),
           ('companyfacts','https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json')]
    documents={}
    for key,url in tests:
        try:
            data=client.get(url,ua)
            valid=isinstance(data,dict) and bool(data)
            if key=='submissions':valid=valid and 'filings' in data
            if key=='companyfacts':valid=valid and 'facts' in data
            if key=='ticker_map':valid=valid and any(isinstance(v,dict) and v.get('ticker')=='AAPL' for v in data.values())
            result['checks'][key]='ok' if valid else 'unexpected_schema'
            documents[key]=data
        except Exception as exc:
            result['checks'][key]=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
    recent=documents.get('submissions',{}).get('filings',{}).get('recent',{})
    for form,accession,doc in zip(recent.get('form',[]),recent.get('accessionNumber',[]),recent.get('primaryDocument',[])):
        if form!='4':continue
        doc=re.sub(r'^xsl[^/]+/','',doc)
        try:
            raw=client.get(f'https://www.sec.gov/Archives/edgar/data/320193/{accession.replace("-","")}/{doc}',ua,False)
            parsed=core.parse_form4(raw)
            result['checks']['form4_xml']='ok' if parsed is not None else 'unknown_or_invalid_xml'
            result['sample_form4_accession']=accession
        except Exception as exc:
            result['checks']['form4_xml']=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
        break
    if 'form4_xml' not in result['checks']:
        result['checks']['form4_xml']='no_sample_available'
    if all(v=='ok' for v in result['checks'].values()):result['status']='ok'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',nargs='+',default=['AAPL','MSFT','SAP.DE','SHEL.L','7203.T','005930.KS'])
    parser.add_argument('--output',type=Path,default=Path('diagnostico_proveedores.json'))
    args=parser.parse_args()
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'yahoo':{},'sec':{}}
    if s.yf is None:
        result['yahoo']={'status':'missing_dependency','reason':'Instala requirements.txt'}
    else:
        rows,_=s.analyze(args.symbols)
        result['yahoo']={'status':'ok' if len(rows)==len(set(args.symbols)) and not s.analyze.errors else 'partial',
             'requested':args.symbols,'received':len(rows),'errors':s.analyze.errors,
             'tickers':[{k:r.get(k) for k in ('symbol','currency','financial_currency','current_price','quote_at',
                 'financial_period','market_cap_usd','avg_dollar_volume','growth_source','num_analysts',
                 'growth_reliable','cap_mismatch')}|{'history_years':r['historical']['history_years'],
                 'roic_years':r['historical']['roic_years']} for r in rows]}
    result['sec']=check_sec()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(s.clean_json(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['yahoo']['status']=='ok' and result['sec']['status']=='ok' else 2


if __name__=='__main__':
    raise SystemExit(main())
