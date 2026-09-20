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
        'points': _unique(humanize(r) for r in row.get('reasons', [])),
        'valuation': valuation_label(valuation.get('conviction')) if show_valuation else None,
        'valuation_points': _unique(humanize(r) for r in valuation.get('conviction_reasons', [])) if show_valuation else [],
    }
