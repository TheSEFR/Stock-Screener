"""Persistencia del negocio y valoración por escenarios, sin datos futuros conocidos.

No predice precios: calcula resultados condicionados a EPS, crecimiento y múltiplo.
Los flujos intermedios/dividendos no se incluyen. Descuento nominal por defecto 12%.
"""
from datetime import datetime, timezone
import statistics
from screener_core import number, positive, utc_date, quote_currency


def table_records(frame):
    """Convierte tablas anuales yfinance a registros JSON por cierre fiscal."""
    if frame is None or frame.empty:
        return []
    result=[]
    for column in frame.columns:
        date=utc_date(str(column))
        if date:
            result.append({'date':date.date().isoformat(),
                           **{str(k):number(v) for k,v in frame[column].to_dict().items()}})
    return result


def historical_quality(statements, now=None):
    now=now or datetime.now(timezone.utc)
    maps={name:{r['date']:r for r in statements.get(name,[]) if r.get('date')}
          for name in ('income','cashflow','balance')}
    common=sorted(set(maps['income']) & set(maps['cashflow']),reverse=True)
    common=[d for d in common if utc_date(d) and utc_date(d)<=now][:3]
    result={'history_years':len(common),'history_healthy':False,'history_reasons':[],
            'normalized_eps':None,'dilution_cagr':None,'cash_conversion':None,
            'fcf_cagr':None,'min_roic':None,'roic_years':0,'annual_periods':common}
    reasons=result['history_reasons']
    if len(common)<3:
        reasons.append('Se requieren tres ejercicios anuales comunes de beneficios y caja')
        return result
    if (now-utc_date(common[0])).days>450 or any(not 300 <= (utc_date(a)-utc_date(b)).days <= 400 for a,b in zip(common,common[1:])):
        reasons.append('Histórico anual desactualizado o no consecutivo')
        return result
    eps=[];fcf=[];income=[];ocf=[];shares=[];revenues=[];roics=[]
    for date in common:
        inc,cf=maps['income'][date],maps['cashflow'][date]
        eps.append(positive(inc.get('Diluted EPS')))
        income.append(positive(inc.get('Net Income')))
        ocf.append(positive(cf.get('Operating Cash Flow')))
        shares.append(positive(inc.get('Diluted Average Shares')))
        revenues.append(positive(inc.get('Total Revenue')))
        free=number(cf.get('Free Cash Flow'))
        if free is None:
            operating,capex=number(cf.get('Operating Cash Flow')),number(cf.get('Capital Expenditure'))
            free=operating-abs(capex) if operating is not None and capex is not None else None
        fcf.append(positive(free))
        balance=maps['balance'].get(date,{})
        prior_dates=sorted(d for d in maps['balance'] if 300 <= (utc_date(date)-utc_date(d)).days <= 400)
        prior=maps['balance'].get(prior_dates[-1],{}) if prior_dates else {}
        def capital(row):
            debt,equity,cash=[number(row.get(k)) for k in ('Total Debt','Stockholders Equity','Cash Cash Equivalents And Short Term Investments')]
            return positive(debt+equity-cash) if all(v is not None for v in (debt,equity,cash)) else None
        start,end=capital(prior),capital(balance)
        ebit,pretax,tax=positive(inc.get('EBIT')),positive(inc.get('Pretax Income')),number(inc.get('Tax Provision'))
        if start and end and ebit and pretax and tax is not None and 0<=tax/pretax<=.5:
            roics.append(ebit*(1-tax/pretax)/((start+end)/2))
    if all(v is not None for v in eps):
        result['normalized_eps']=min(eps[0],statistics.median(eps))
    else:
        reasons.append('EPS diluido no positivo o ausente en algún ejercicio')
    years=(utc_date(common[0])-utc_date(common[-1])).days/365.25
    if all(v is not None for v in fcf):
        result['fcf_cagr']=(fcf[0]/fcf[-1])**(1/years)-1
        if result['fcf_cagr']<0:
            reasons.append('La caja libre disminuye entre el primer y último ejercicio')
    else:
        reasons.append('FCF no positivo o ausente en algún ejercicio')
    if all(v is not None for v in income+ocf):
        result['cash_conversion']=sum(ocf)/sum(income)
        if result['cash_conversion']<.8:
            reasons.append('Conversión de beneficio a caja operativa inferior al 80%')
    else:
        reasons.append('Beneficio/caja operativa insuficientes para validar conversión')
    if all(v is not None for v in shares):
        result['dilution_cagr']=(shares[0]/shares[-1])**(1/years)-1
        if result['dilution_cagr']>.02:
            reasons.append('Dilución media anual superior al 2%')
    else:
        reasons.append('Sin histórico completo de acciones diluidas')
    if not all(v is not None for v in revenues) or revenues[0]<revenues[-1]:
        reasons.append('Ingresos históricos ausentes o en descenso')
    result['roic_years']=len(roics)
    if len(roics)>=2:
        result['min_roic']=min(roics)
        if min(roics)<.12:
            reasons.append('ROIC estimado inferior al 12% en algún ejercicio')
    else:
        reasons.append('Se requieren al menos dos ROIC con capital medio verificable')
    result['history_healthy']=not reasons
    return result


def sensitivity(eps_quote, base_growth, peer_pe, price, horizon, required_return):
    """Margen de seguridad en 81 combinaciones de hipótesis peores y mejores que la base:
    crecimiento +-3 pp, PER de salida x0,8/x1/x1,2, beneficio (márgenes, deuda) x0,8/x1/x1,1
    y tasa exigida -2/0/+3 pp. Muestra cuánto se degrada la conclusión, no la predice."""
    margins=[]
    for dg in (-.03,0,.03):
        for pe_factor in (.8,1,1.2):
            for eps_factor in (.8,1,1.1):
                for dr in (-.02,0,.03):
                    growth=max(-.10,base_growth+dg)
                    rate=max(.01,required_return+dr)
                    terminal=eps_quote*eps_factor*(1+growth)**horizon*min(peer_pe,18)*pe_factor
                    margins.append(1-price/(terminal/(1+rate)**horizon))
    share=sum(m>=.25 for m in margins)/len(margins)
    return {'combinations':len(margins),'min_margin':min(margins),
            'median_margin':statistics.median(margins),'share_margin_ok':share,
            'robust':share>=.75 and min(margins)>0}


def scenarios(row, historical, rates, horizon=5, required_return=.12):
    if not 1<=horizon<=10 or not .01<=required_return<=.4:
        raise ValueError('Horizonte 1–10 años y rentabilidad exigida 1%–40%')
    result={'valuation_available':False,'conviction':'sin_valoracion',
            'conviction_reasons':list(historical.get('history_reasons',[])),
            'horizon_years':horizon,'required_return':required_return,'scenarios':{}}
    eps=positive(historical.get('normalized_eps'))
    quote,scale=quote_currency(row.get('currency'))
    reporting=row.get('financial_currency')
    fx_report,fx_quote=positive(rates.get(reporting)),positive(rates.get(quote))
    price=positive(row.get('current_price'))
    peer=positive(row.get('sector_avg_pe'))
    if not all(v is not None for v in (eps,fx_report,fx_quote,price,peer)):
        result['conviction_reasons'].append('Faltan EPS normalizados, divisa, cotización o pares suficientes')
        return result
    if row.get('cap_mismatch') or row.get('unsupported_model'):
        result['conviction_reasons'].append('Resolver modelo sectorial o discrepancia de unidades/ADR')
        return result
    # EPS históricos en moneda financiera -> unidad usada por el precio (incluye GBp).
    eps_quote=eps*fx_report/fx_quote/scale
    growth=number(row.get('growth'))
    trusted=bool(row.get('growth_reliable') and growth is not None)
    if not trusted:
        result['conviction_reasons'].append('Crecimiento futuro insuficientemente sustentado')
    # No extrapolar sin límite un único año de consenso.
    base_growth=max(0,min(.08,growth)) if trusted else 0
    bull_growth=max(0,min(.12,growth)) if trusted else .04
    assumptions={'adverso':(-.05,min(peer,12)), 'base':(base_growth,min(peer,18)),
                 'favorable':(bull_growth,min(peer,22))}
    for label,(growth_rate,multiple) in assumptions.items():
        terminal_price=eps_quote*(1+growth_rate)**horizon*multiple
        present=terminal_price/(1+required_return)**horizon
        result['scenarios'][label]={'eps_growth':growth_rate,'exit_pe':multiple,
            'terminal_price':terminal_price,'present_value':present,
            'annual_price_return':(terminal_price/price)**(1/horizon)-1}
    base=result['scenarios']['base'];bear=result['scenarios']['adverso']
    result['valuation_available']=True
    result['normalized_eps_quote']=eps_quote
    result['margin_of_safety']=1-price/base['present_value']
    result['study_price_limit']=.75*base['present_value']
    result['sensitivity']=sensitivity(eps_quote,base_growth,peer,price,horizon,required_return)
    result['conviction']='vigilar_precio'
    if not historical.get('history_healthy') or not row.get('eligible') or not trusted:
        result['conviction']='revisar_calidad'
    if not row.get('eligible'):
        result['conviction_reasons'].append('No supera el filtro principal')
    leverage=number(row.get('net_debt_ebitda'))
    if leverage is None or leverage>2:
        result['conviction_reasons'].append('Deuda neta/EBITDA superior a 2 o sin dato')
    if not result['sensitivity']['robust']:
        result['conviction_reasons'].append('Conclusion fragil: el margen de seguridad no aguanta hipotesis peores (ver sensibilidad)')
    if result['margin_of_safety']<.25:
        result['conviction_reasons'].append('Margen de seguridad base inferior al 25%')
    if bear['annual_price_return']<-.05:
        result['conviction_reasons'].append('Escenario adverso implica pérdida anual superior al 5%')
    if not result['conviction_reasons'] and historical.get('history_healthy') and trusted:
        result['conviction']='prioridad_alta_para_estudio'
    return result
