"""Explicaciones en lenguaje llano del estado de cada accion (para la columna Est. del PDF).

Traduce los motivos tecnicos ('Sin dato verificable: eps', 'Menos de 3 de 4 senales...') a frases
que se entienden sin conocer el codigo. Un motivo desconocido se muestra tal cual, nunca se pierde.
"""
import re

STATE_LETTERS = {'candidata': 'C', 'revisar_datos': 'R', 'descartada_riesgo': 'D', 'no_cumple': 'N', 'duplicada': 'X'}

STATE_TITLES = {
    'C': 'Candidata',
    'R': 'Revisar',
    'D': 'Descartada por riesgo',
    'N': 'No cumple',
    'X': 'Duplicada',
}

STATE_TEXT = {
    'C': 'Pasa el filtro del informe: cumple al menos 3 de 4 criterios de valor, 2 de 4 de calidad y no tiene '
         'señales de riesgo. Es una candidata para estudiar, no una recomendación de compra.',
    'R': 'Faltan datos para juzgarla con seguridad. Por diseño, un dato que falta nunca cuenta como aprobado: '
         'hasta tenerlo, no se puede decir si es buena o mala.',
    'D': 'Se descarta por una señal de riesgo medida (por ejemplo pérdidas, deuda alta o poca liquidez), '
         'aunque el resto de sus cifras parezcan atractivas.',
    'N': 'Sus datos están completos, pero no alcanza los mínimos de valor o de calidad que pide el filtro.',
    'X': 'Es la misma empresa que otra de la lista que cotiza en otra bolsa. Solo se evalúa una de las dos.',
}

LEGEND = 'C = candidata   R = revisar (faltan datos)   D = descartada por riesgo   N = no cumple   X = duplicada'

_FIELD_NAMES = {
    'eps': 'el beneficio por acción', 'ebitda': 'el EBITDA (beneficio antes de intereses e impuestos)',
    'book_value': 'el valor contable por acción', 'net_debt_ebitda': 'la deuda neta o el EBITDA',
    'fcf_yield': 'la caja libre', 'avg_dollar_volume': 'el volumen de negociación',
    'market_cap_usd': 'la capitalización en dólares', 'current_price': 'el precio',
    'operating_margin': 'el margen operativo',
}

_EXACT = {
    'Menos de 7 de 8 criterios con datos válidos': 'Faltan datos para evaluar varios criterios (hacen falta al menos 7 de 8).',
    'Menos de 3 de 4 señales de valor/crecimiento': 'Cumple menos de 3 de los 4 criterios de valor y crecimiento.',
    'Menos de 2 de 4 señales de calidad': 'Cumple menos de 2 de los 4 criterios de calidad.',
    'EPS no positivo': 'Pierde dinero: el beneficio por acción no es positivo.',
    'EBITDA no positivo': 'Su beneficio operativo antes de amortizaciones (EBITDA) no es positivo.',
    'Deuda neta/EBITDA > 4': 'Está muy endeudada: su deuda neta supera 4 veces el EBITDA.',
    'Liquidez diaria < 1 M USD': 'Se negocia poco: menos de 1 millón de dólares al día, y podría costar comprarla o venderla.',
    'Patrimonio por acción no positivo': 'Su patrimonio por acción no es positivo: debe más de lo que tiene.',
    'FCF no positivo': 'Su caja libre no es positiva: gasta más caja de la que genera.',
    'Margen operativo no positivo': 'Su margen operativo no es positivo: no gana dinero con su actividad.',
    'Capitalización no positiva': 'La capitalización que da la fuente no es válida.',
    'Precio no positivo': 'El precio que da la fuente no es válido.',
    'Cotización sin fecha válida o desactualizada': 'El precio es viejo (más de 7 días) o no tiene fecha.',
    'Periodo financiero sin fecha válida o desactualizado': 'Las últimas cuentas tienen más de 180 días o no tienen fecha.',
    'Banca/seguros/inmobiliario requieren modelo específico':
        'Es banca, seguro o inmobiliaria: no se puede juzgar con los mismos ratios que una empresa industrial.',
    'Instrumento no confirmado como acción': 'No está confirmado que sea una acción (podría ser un fondo u otro producto).',
    'Revisar unidades de capitalización / ADR / clases de acciones':
        'Los datos de capitalización no cuadran (posible ADR o varias clases de acciones): hay que revisarlo a mano.',
    # Historial y valoracion (seccion 6)
    'Se requieren tres ejercicios anuales comunes de beneficios y caja':
        'Faltan las cuentas anuales de 3 años para comprobar su trayectoria.',
    'Histórico anual desactualizado o no consecutivo': 'Sus cuentas anuales están desactualizadas o tienen huecos.',
    'EPS diluido no positivo o ausente en algún ejercicio': 'En algún año perdió dinero o no hay dato de su beneficio.',
    'La caja libre disminuye entre el primer y último ejercicio': 'Su caja libre ha bajado en los últimos 3 años.',
    'FCF no positivo o ausente en algún ejercicio': 'En algún año su caja libre fue negativa o no hay dato.',
    'Conversión de beneficio a caja operativa inferior al 80%': 'Convierte en caja menos del 80% de lo que gana.',
    'Beneficio/caja operativa insuficientes para validar conversión':
        'Faltan datos para comprobar si el beneficio se convierte en caja.',
    'Dilución media anual superior al 2%':
        'Emite acciones nuevas a buen ritmo (más del 2% al año), lo que diluye a los accionistas.',
    'Sin histórico completo de acciones diluidas': 'Falta el número de acciones de algunos años.',
    'Ingresos históricos ausentes o en descenso': 'Sus ingresos han bajado en estos años o faltan datos.',
    'ROIC estimado inferior al 12% en algún ejercicio':
        'Su rentabilidad sobre el capital invertido (ROIC) fue inferior al 12% algún año.',
    'Se requieren al menos dos ROIC con capital medio verificable':
        'No se puede calcular su rentabilidad sobre el capital invertido (ROIC): faltan datos de su balance en la fuente. '
        'Es un problema de datos, no necesariamente de la empresa.',
    'Faltan EPS normalizados, divisa, cotización o pares suficientes':
        'Faltan datos (beneficio normalizado, moneda o comparables) para calcular cuánto vale.',
    'Resolver modelo sectorial o discrepancia de unidades/ADR':
        'Hay que revisar a mano un posible problema de unidades o de tipo de acción.',
    'Crecimiento futuro insuficientemente sustentado':
        'El crecimiento que estiman los analistas no es fiable (pocos analistas o muy distintos entre sí).',
    'No supera el filtro principal': 'No pasa el filtro principal del informe.',
    'Deuda neta/EBITDA superior a 2 o sin dato': 'Su deuda es alta (más de 2 veces el EBITDA) o no hay dato.',
    'Margen de seguridad base inferior al 25%':
        'El precio actual está cerca o por encima de lo que vale en el escenario base (margen inferior al 25%).',
    'Escenario adverso implica pérdida anual superior al 5%': 'En el escenario adverso perdería más de un 5% al año.',
    'Discrepancia entre Yahoo y SEC en ingresos o beneficio': 'Las cifras de Yahoo no coinciden con las de la SEC.',
}

VALUATION_LABELS = {
    'prioridad_alta_para_estudio': 'Prioridad alta para estudio',
    'vigilar_precio': 'Vigilar precio: la empresa cumple, pero el precio actual no deja margen suficiente',
    'revisar_historial': 'Revisar historial: hay un problema con sus cuentas de los últimos años o con los datos',
    'sin_valoracion': 'Sin valoración: faltan datos para calcularla',
}


def state_letter(row):
    return STATE_LETTERS.get(row.get('status'), '?')


def valuation_label(key):
    return VALUATION_LABELS.get(key, key or 'Sin valoración')


def humanize(reason):
    """Frase llana para un motivo tecnico; si no se conoce, el original (nunca se pierde)."""
    text = (reason or '').strip()
    if text in _EXACT:
        return _EXACT[text]
    match = re.fullmatch(r'Sin dato verificable: (\w+)', text)
    if match:
        what = _FIELD_NAMES.get(match.group(1), match.group(1))
        return f'No hay dato fiable de {what} en la fuente, así que no se puede comprobar.'
    match = re.fullmatch(r'Misma empresa que (\S+) .*', text)
    if match:
        return f'Es la misma empresa que {match.group(1)}, que cotiza en otra bolsa: se evalúa solo esa.'
    if text.startswith('Conclusion fragil') or text.startswith('Conclusión frágil'):
        return 'Su valoración cambia mucho si las hipótesis empeoran: no es una conclusión firme.'
    return text


_VALUE_CHECKS = {
    'pe_descuento_20': 'P/E al menos un 20% por debajo de sus comparables',
    'fcf_5': 'caja libre de al menos el 5% de su valor en bolsa',
    'eps_futuro_15': 'beneficio estimado con un crecimiento de al menos el 15%',
    'ingresos_5': 'ingresos creciendo al menos un 5%',
}
_QUALITY_CHECKS = {
    'roe_15': 'rentabilidad sobre su patrimonio (ROE) de al menos el 15%',
    'margen_positivo_pares': 'margen operativo positivo y no inferior al de sus comparables',
    'deuda_patrimonio': 'deuda que no supera a su patrimonio',
    'liquidez_1_5': 'holgura para pagar sus deudas a corto plazo (ratio corriente de al menos 1,5)',
}


def _pct(value, decimals=1):
    return 'n/d' if value is None else f'{value * 100:.{decimals}f}%'.replace('.', ',')


def _num(value, decimals=1):
    return 'n/d' if value is None else f'{value:.{decimals}f}'.replace('.', ',')


def _which(checks, names):
    """'Cumple: ... No cumple: ... Sin dato: ...' con los criterios en palabras normales."""
    groups = {'Cumple': [], 'No cumple': [], 'Sin dato': []}
    for key, label in names.items():
        value = (checks or {}).get(key)
        groups['Sin dato' if value is None else 'Cumple' if value else 'No cumple'].append(label)
    return ' '.join(f"{title}: {'; '.join(items)}." for title, items in groups.items() if items)


def _own_figure(reason, row):
    """Las cifras reales de ESTA accion para un motivo, o '' si no hay dato. Se muestra tras 'En su caso:'."""
    valuation = row.get('valuation') or {}
    if reason == 'EPS no positivo':
        return f"su beneficio por acción es {_num(row.get('eps'), 2)}."
    if reason == 'Deuda neta/EBITDA > 4':
        return f"su deuda neta es {_num(row.get('net_debt_ebitda'))} veces su EBITDA (el máximo es 4)."
    if reason == 'FCF no positivo':
        return f"su caja libre es del {_pct(row.get('fcf_yield'))} de su valor en bolsa."
    if reason == 'Liquidez diaria < 1 M USD' and row.get('avg_dollar_volume') is not None:
        return f"se negocia por unos {_num(row['avg_dollar_volume'] / 1e6, 2)} millones de dólares al día."
    if reason == 'Margen operativo no positivo':
        return f"su margen operativo es del {_pct(row.get('operating_margin'))}."
    if reason == 'Cotización sin fecha válida o desactualizada' and row.get('quote_age_days') is not None:
        return f"su última cotización tiene {_num(row['quote_age_days'], 0)} días."
    if reason == 'Periodo financiero sin fecha válida o desactualizado' and row.get('financial_age_days') is not None:
        return f"sus últimas cuentas tienen {_num(row['financial_age_days'], 0)} días."
    if reason == 'Menos de 3 de 4 señales de valor/crecimiento':
        return _which(row.get('checks'), _VALUE_CHECKS)
    if reason == 'Menos de 2 de 4 señales de calidad':
        return _which(row.get('quality_checks'), _QUALITY_CHECKS)
    if reason == 'Menos de 7 de 8 criterios con datos válidos':
        missing = [label for key, label in {**_VALUE_CHECKS, **_QUALITY_CHECKS}.items()
                   if {**(row.get('checks') or {}), **(row.get('quality_checks') or {})}.get(key, 'n') is None]
        return ('no se pudo comprobar: ' + '; '.join(missing) + '.') if missing else ''
    if reason == 'Deuda neta/EBITDA superior a 2 o sin dato':
        return f"su deuda neta es {_num(row.get('net_debt_ebitda'))} veces su EBITDA (el máximo es 2)."
    if reason == 'Margen de seguridad base inferior al 25%' and valuation.get('margin_of_safety') is not None:
        return f"su margen de seguridad base es del {_pct(valuation['margin_of_safety'], 0)} (se pide el 25%)."
    if reason.startswith('Conclusion fragil') and (valuation.get('sensitivity') or {}).get('share_margin_ok') is not None:
        return f"el margen llega al 25% en solo el {_pct(valuation['sensitivity']['share_margin_ok'], 0)} de las 81 combinaciones probadas."
    if reason == 'Escenario adverso implica pérdida anual superior al 5%':
        rate = ((valuation.get('scenarios') or {}).get('adverso') or {}).get('annual_price_return')
        return '' if rate is None else f"en el escenario adverso el precio evolucionaría un {_pct(rate)} al año."
    if reason == 'Crecimiento futuro insuficientemente sustentado' and row.get('num_analysts') is not None:
        return f"solo hay {_num(row['num_analysts'], 0)} analistas de beneficio por acción con estimaciones completas."
    if reason == 'Discrepancia entre Yahoo y SEC en ingresos o beneficio':
        bad = next((c for c in (row.get('reconciliation') or {}).get('checks', []) if not c.get('ok')), None)
        return '' if not bad else f"en {bad['metric']} Yahoo dice {bad['yahoo']:,.0f} y la SEC {bad['sec']:,.0f}.".replace(',', '.')
    return ''


def _with_figure(reason, row):
    plain, own = humanize(reason), _own_figure(reason, row)
    if 'n/d' in own:  # con una cifra que falta mejor no decir nada que "su deuda es n/d veces..."
        own = ''
    return f'{plain} En su caso: {own}' if own else plain


# Guia de motivos: (grupo, [(motivo tecnico, titulo corto, ejemplo)]). El texto llano sale de humanize().
GUIDE = [
    ('Descartada por riesgo (D): una señal de riesgo medida', [
        ('EPS no positivo', 'Pierde dinero',
         'Una empresa vende 50 millones pero pierde 2 millones al año: por cada acción pierde 0,20 euros. Aunque su cotización parezca barata, no gana dinero.'),
        ('EBITDA no positivo', 'Su actividad no genera beneficio operativo',
         'Antes de intereses, impuestos y amortizaciones ya pierde 10 millones: con su negocio actual le costaría pagar sus deudas.'),
        ('FCF no positivo', 'Su caja libre es negativa',
         'Declara 20 millones de beneficio, pero entre inversiones y gastos se le van 25 millones de caja: su caja libre es de -5 millones. Gana sobre el papel, pero le entra menos dinero del que sale.'),
        ('Margen operativo no positivo', 'No gana dinero con su actividad',
         'Vende 100 millones y producir y vender le cuesta 105: margen operativo del -5%.'),
        ('Patrimonio por acción no positivo', 'Debe más de lo que tiene',
         'Tiene 80 millones en bienes y debe 100 millones: su patrimonio es de -20 millones.'),
        ('Deuda neta/EBITDA > 4', 'Deuda demasiado alta',
         'Gana 100 millones al año antes de intereses y debe 500 millones netos: necesitaría 5 años solo para pagar la deuda. El límite es 4.'),
        ('Liquidez diaria < 1 M USD', 'Se negocia muy poco',
         'Si cada día solo se compran y venden 300.000 dólares de sus acciones, comprar 50.000 dólares ya mueve el precio y salir puede costar caro.'),
        ('Capitalización no positiva', 'El valor en bolsa no es válido',
         'La fuente devuelve un valor en bolsa de 0 o negativo, algo imposible: se trata como un error de datos y no se evalúa.'),
        ('Precio no positivo', 'El precio no es válido',
         'La fuente devuelve un precio de 0 para una acción que cotiza: es un error de datos, y con él no se puede calcular nada.'),
    ]),
    ('Revisar (R): faltan datos o hay algo que confirmar', [
        ('Menos de 7 de 8 criterios con datos válidos', 'Faltan datos de varios criterios',
         'De los 8 criterios, la fuente no da el crecimiento estimado ni el margen de sus comparables: solo se comprueban 6. Con 6 no se decide.'),
        ('Sin dato verificable: eps', 'Falta un dato clave',
         'Yahoo no publica el beneficio por acción de una empresa coreana: sin él no se sabe si gana dinero, y un dato que falta nunca cuenta como aprobado.'),
        ('Cotización sin fecha válida o desactualizada', 'El precio es viejo',
         'El último precio es de hace 12 días (el límite es 7): el mercado puede haberse movido mucho desde entonces.'),
        ('Periodo financiero sin fecha válida o desactualizado', 'Las cuentas son viejas',
         'Sus últimas cuentas son de hace 8 meses (el límite es 180 días): la empresa puede haber cambiado mucho.'),
        ('Banca/seguros/inmobiliario requieren modelo específico', 'Banco, seguro o inmobiliaria',
         'Un banco tiene "deuda" porque los depósitos de sus clientes son deuda para él. Con las reglas de una fábrica parecería estar en quiebra.'),
        ('Instrumento no confirmado como acción', 'Quizá no es una acción',
         'Un fondo cotizado (ETF) no tiene beneficios propios como una empresa, así que sus ratios no significan lo mismo.'),
        ('Revisar unidades de capitalización / ADR / clases de acciones', 'Los datos de tamaño no cuadran',
         'Un ADR puede equivaler a 5 acciones locales: si se mezcla el precio de un ADR con las cuentas por acción local salen ratios absurdos.'),
    ]),
    ('No cumple (N): datos completos, pero no alcanza los mínimos', [
        ('Menos de 3 de 4 señales de valor/crecimiento', 'Poco valor o crecimiento',
         'Solo cumple 2 de 4: sus ingresos crecen, pero es más cara que sus comparables y su caja libre es del 2% (se pide el 5%).'),
        ('Menos de 2 de 4 señales de calidad', 'Poca calidad',
         'Solo cumple 1 de 4: tiene liquidez de sobra, pero su ROE es del 6%, su margen es menor que el de sus comparables y su deuda supera su patrimonio.'),
        ('Misma empresa que BABA (otra bolsa): se evalua solo esa cotizacion', 'Duplicada (X)',
         'Alibaba cotiza en Nueva York (BABA) y en Hong Kong (9988.HK): es la misma empresa, así que solo se evalúa la más negociada.'),
    ]),
    ('Valoración (sección 6): por qué una candidata pide cautela', [
        ('Se requieren al menos dos ROIC con capital medio verificable', 'No se puede calcular su ROIC',
         'Para medir cuánto rinde el capital que usa la empresa hacen falta sus balances de dos años; si la fuente solo da uno, no se puede calcular. Es falta de datos, no una mala empresa.'),
        ('Se requieren tres ejercicios anuales comunes de beneficios y caja', 'Faltan cuentas de 3 años',
         'Solo hay cuentas de 2 de los últimos 3 años: no se puede comprobar su trayectoria completa.'),
        ('Histórico anual desactualizado o no consecutivo', 'Cuentas antiguas o con huecos',
         'Las últimas cuentas anuales son de hace dos años y falta el año intermedio.'),
        ('EPS diluido no positivo o ausente en algún ejercicio', 'Perdió dinero algún año',
         'Ganó 3 años seguidos, pero hace dos años perdió 0,40 euros por acción: la trayectoria no es estable.'),
        ('La caja libre disminuye entre el primer y último ejercicio', 'Su caja libre ha bajado',
         'Hace 3 años generaba 100 millones de caja libre y el último año 70: cada vez le sobra menos dinero.'),
        ('FCF no positivo o ausente en algún ejercicio', 'Caja libre negativa algún año',
         'Un año gastó más caja de la que generó (-15 millones), aunque los otros fueran positivos.'),
        ('Conversión de beneficio a caja operativa inferior al 80%', 'Su beneficio no se convierte en caja',
         'Declara 100 millones de beneficio en 3 años pero solo 60 llegan como caja de su actividad: el resto está en facturas sin cobrar o en ajustes contables.'),
        ('Beneficio/caja operativa insuficientes para validar conversión', 'Faltan datos de caja',
         'No hay dato de la caja de su actividad en algún año, así que no se puede comprobar si el beneficio es real.'),
        ('Dilución media anual superior al 2%', 'Emite muchas acciones nuevas',
         'Tenía 100 millones de acciones y hoy 110: tu parte de la empresa se ha reducido un 10% aunque tú no vendas nada.'),
        ('Sin histórico completo de acciones diluidas', 'Falta el número de acciones',
         'Sin saber cuántas acciones había cada año no se puede saber si se han emitido nuevas.'),
        ('Ingresos históricos ausentes o en descenso', 'Sus ingresos bajan',
         'Vendía 500 millones hace 3 años y hoy 450: la empresa se encoge.'),
        ('ROIC estimado inferior al 12% en algún ejercicio', 'Rinde poco el capital que usa',
         'Por cada 100 euros que la empresa tiene invertidos gana 8 euros al año (ROIC 8%), menos del 12% que se exige.'),
        ('Faltan EPS normalizados, divisa, cotización o pares suficientes', 'No se puede calcular cuánto vale',
         'Sin un beneficio normalizado ni comparables no hay base para estimar un valor razonable.'),
        ('Resolver modelo sectorial o discrepancia de unidades/ADR', 'Hay que revisar algo a mano',
         'El precio está en peniques y las cuentas en libras: hasta confirmarlo, cualquier valoración sería errónea.'),
        ('Crecimiento futuro insuficientemente sustentado', 'El crecimiento estimado no es fiable',
         'Solo 2 analistas estiman su beneficio, uno espera +5% y otro +60%: la media (+32%) no significa nada.'),
        ('No supera el filtro principal', 'No pasa el filtro principal',
         'Aunque su valoración salga bien, no cumple los mínimos de valor y calidad del filtro, así que no se le da prioridad.'),
        ('Deuda neta/EBITDA superior a 2 o sin dato', 'Deuda alta para la etiqueta más alta',
         'Debe 300 millones netos y gana 100 antes de intereses: 3 veces su EBITDA. Vale para pasar el filtro (máx. 4) pero no para la prioridad alta (máx. 2).'),
        ('Margen de seguridad base inferior al 25%', 'El precio deja poco margen',
         'Según el escenario base la empresa vale 100 euros por acción y cotiza a 90 euros: solo hay un 10% de colchón, y se pide el 25%. Si cotiza a 110 euros, el margen es negativo: ya está por encima de su valor calculado.'),
        ('Escenario adverso implica pérdida anual superior al 5%', 'Si sale mal, pierde mucho',
         'Si los beneficios no crecen y el múltiplo baja, el precio caería un 8% al año durante 5 años.'),
        ('Conclusion fragil: el margen de seguridad no aguanta hipotesis peores (ver sensibilidad)', 'Conclusión frágil',
         'Con las hipótesis base parece barata, pero si el crecimiento baja 3 puntos o el múltiplo un 20% ya deja de serlo: el resultado depende de que todo salga bien.'),
        ('Discrepancia entre Yahoo y SEC en ingresos o beneficio', 'Los datos no coinciden con la SEC',
         'Yahoo dice que vendió 1.200 millones y la SEC 1.000: una de las dos fuentes tiene un dato mal, y mientras no se aclare no se puede fiar.'),
    ]),
]


def _unique(items):
    seen, result = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def explain(row):
    """{'letter','title','text','points','valuation','valuation_points'} en lenguaje llano."""
    letter = state_letter(row)
    valuation = row.get('valuation') or {}
    show_valuation = valuation.get('valuation_available') or valuation.get('conviction') not in (None, 'sin_valoracion')
    return {
        'letter': letter,
        'title': STATE_TITLES.get(letter, 'Sin clasificar'),
        'text': STATE_TEXT.get(letter, ''),
        'points': _unique(_with_figure(r, row) for r in row.get('reasons', [])),
        'valuation': valuation_label(valuation.get('conviction')) if show_valuation else None,
        'valuation_points': _unique(_with_figure(r, row) for r in valuation.get('conviction_reasons', [])) if show_valuation else [],
    }
