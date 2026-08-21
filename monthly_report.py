"""
Informe retrospectivo (mensual, el dia 1 de cada mes) y anual (el 1 de
enero) que compara, por cada accion y agrupada por categoria (Principales/
Pequeña capitalizacion/Cesta tematica, igual que el informe principal): el
precio REAL de cierre en la fecha de inicio del año fiscal de esa empresa
(via yfinance .history(), historico real, no una aproximacion) frente al
precio real actual y el precio objetivo que tienen los analistas hoy.

Limitacion de datos: Yahoo Finance solo expone el precio OBJETIVO actual
de los analistas, no el que tenian en el pasado (a diferencia del precio
de la accion en si, que si tiene historico real). Por eso la columna
"P. objetivo" siempre es la de hoy, mientras que "Precio inicio F.Y." si
es un dato historico real desde la primera ejecucion.

Uso: python monthly_report.py [--example]
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import yfinance as yf

from screener import (
    HAIRLINE,
    INDICE_BG,
    INK,
    MAX_TABLE_ROW_HEIGHT,
    NAVY,
    SECTION2_BG,
    SMALL_CAP_MAX,
    TRUMP_TRADE_THEMES,
    ReportPDF,
    draw_cover_page,
    fiscal_year_end,
    load_watchlist,
    render_table,
    sanitize,
    section_header,
    send_telegram_document,
)


def render_toc(pdf, outline) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=22, style="B")
    pdf.set_text_color(*INK)
    pdf.cell(0, 13, "Indice", new_x="LMARGIN", new_y="NEXT")
    y_line = pdf.get_y() + 1
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, y_line, pdf.w - pdf.r_margin, y_line)
    pdf.ln(10)
    pdf.set_font("Helvetica", size=13)
    for section in outline:
        link = pdf.add_link(page=section.page_number)
        pdf.set_x(pdf.l_margin)
        pdf.cell(
            0, 11,
            sanitize(f"{section.name}  {'.' * 60}  pag. {section.page_number}"),
            new_x="LMARGIN", new_y="NEXT", link=link,
        )

CATEGORY_ORDER = ["Principales", "Pequena capitalizacion", "Cesta tematica (Trump trade)"]


def fiscal_year_start(info: dict) -> str:
    """Inicio del año fiscal ACTUAL = el dia siguiente al cierre del año
    fiscal anterior (Yahoo no da un campo directo de 'inicio')."""
    ts = info.get("lastFiscalYearEnd")
    if not ts:
        return "n/d"
    return (datetime.fromtimestamp(ts) + timedelta(days=1)).strftime("%m/%y")


def price_on_date(ticker: yf.Ticker, date: datetime) -> float | None:
    """Precio de cierre REAL en la fecha dada (o el primer dia de mercado
    abierto en la semana siguiente, por si cae en fin de semana/festivo).
    A diferencia del precio objetivo de analistas, Yahoo si da historico
    real de precios, asi que esto no es una aproximacion ni un placeholder."""
    end = date + timedelta(days=7)
    try:
        hist = ticker.history(start=date.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    except Exception:
        return None
    if hist.empty:
        return None
    return float(hist["Close"].iloc[0])


def categorize(symbol: str, market_cap: float | None) -> str:
    if symbol in TRUMP_TRADE_THEMES:
        return "Cesta tematica (Trump trade)"
    if market_cap is not None and market_cap < SMALL_CAP_MAX:
        return "Pequena capitalizacion"
    return "Principales"

STATE_FILE = os.path.join(os.path.dirname(__file__), "price_history.json")


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"monthly": {}, "yearly": {}}
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def snapshot_prices(symbols: list[str]) -> dict[str, dict]:
    snapshot = {}
    for sym in symbols:
        ticker = yf.Ticker(sym)
        info = ticker.info
        market_cap = info.get("marketCap")

        fy_start_ts = info.get("lastFiscalYearEnd")
        fy_start_price = None
        if fy_start_ts:
            fy_start_date = datetime.fromtimestamp(fy_start_ts) + timedelta(days=1)
            fy_start_price = price_on_date(ticker, fy_start_date)

        snapshot[sym] = {
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "target": info.get("targetMeanPrice"),
            "category": categorize(sym, market_cap),
            "fiscal_year_end": fiscal_year_end(info),
            "fiscal_year_start": fiscal_year_start(info),
            "fiscal_year_start_price": fy_start_price,
        }
    return snapshot


def group_by_category(snapshot: dict[str, dict]) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = defaultdict(list)
    for sym, data in snapshot.items():
        blocks[data["category"]].append(sym)
    # Orden fijo (Principales, Pequena capitalizacion, Cesta tematica),
    # igual que las secciones del informe principal, no orden de aparicion.
    return {cat: blocks[cat] for cat in CATEGORY_ORDER if cat in blocks}


def build_comparison_pdf(
    baseline: dict[str, dict],
    current: dict[str, dict],
    title: str,
    is_example: bool,
    january_baseline: dict[str, dict] | None = None,
) -> str:
    january_baseline = january_baseline or {}
    blocks = group_by_category(current)
    # Mismas hojas que el informe principal (A4 horizontal), no verticales.
    pdf = ReportPDF(orientation="L", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(left=20, top=10, right=20)

    # Portada identica a la del informe principal (mismo logo, firma en
    # arabe, colores), solo cambia el titulo.
    draw_cover_page(pdf, "Tabla seguimiento acciones")

    # --- Indice (pagina reservada, se rellena sola al final con las
    # secciones registradas via start_section mas abajo). allow_extra_pages
    # desactivado: con 4-5 entradas fijas, 1 pagina siempre sobra.
    pdf.page_background = INDICE_BG
    pdf.add_page()
    # El fondo se cambia AQUI (antes de insert_toc_placeholder, no despues):
    # ese metodo salta internamente a una pagina nueva para la 1a categoria,
    # y esa pagina nace ya con este fondo puesto (ver nota igual en el
    # informe principal).
    pdf.page_background = SECTION2_BG
    pdf.insert_toc_placeholder(render_toc, pages=1)

    # --- Una seccion (hoja nueva) por categoria: misma funcion de tabla
    # que el informe principal (render_table), centrada con un ancho algo
    # mayor que el minimo (no estirada a toda la pagina).
    headers = ["Ticker", "F.Y.", "Precio inicio F.Y.", "P. real actual", "P. objetivo", "Diferencia"]
    widths = (22, 24, 30, 30, 24, 24)
    align = ["L", "L", "R", "R", "R", "R"]
    table_width = sum(widths) + 15  # "aumenta muy poco el ancho"

    def render_category_rows(tickers: list[str]) -> list[list[str]]:
        table_rows = []
        for sym in tickers:
            now = current.get(sym, {})
            actual_price = now.get("price")
            target = now.get("target")
            fy_start_price = now.get("fiscal_year_start_price")
            fy = f"{now.get('fiscal_year_start', 'n/d')} - {now.get('fiscal_year_end', 'n/d')}"
            diff_txt = "n/d"
            if actual_price and target:
                diff_pct = (actual_price - target) / target * 100
                diff_txt = f"{diff_pct:+.1f}%"
            table_rows.append([
                sym,
                fy,
                f"{fy_start_price:.2f}" if fy_start_price else "n/d",
                f"{actual_price:.2f}" if actual_price else "n/d",
                f"{target:.2f}" if target else "n/d",
                diff_txt,
            ])
        return table_rows

    # Principales y Pequena capitalizacion: partidas en 2 paginas (mitad
    # de acciones en cada una), cada mitad centrada. Cesta tematica tiene
    # pocas acciones y se queda en una sola pagina.
    # Solo se parte en 2 paginas si la categoria tiene bastantes acciones
    # (no por su nombre): con 8-9 tickers en 2 mitades quedaban paginas casi
    # vacias, igual de mal que estirarlas de mas.
    SPLIT_THRESHOLD = 15
    first_section = True
    for idx, (category, tickers) in enumerate(blocks.items(), start=1):
        section_label = f"{idx}. {category}"
        pdf.start_section(section_label)
        # Sin add_page() en la 1a categoria: insert_toc_placeholder ya
        # salto a una pagina nueva; añadir otra generaba la hoja en blanco.
        if not first_section:
            pdf.add_page()
        first_section = False
        section_header(pdf, "Categoria", section_label)

        tickers = sorted(tickers)
        if len(tickers) > SPLIT_THRESHOLD:
            mid = (len(tickers) + 1) // 2
            halves = [tickers[:mid], tickers[mid:]]
        else:
            halves = [tickers]

        for i, half in enumerate(halves):
            if i > 0:
                pdf.add_page()
            # Centrado VERTICAL: sin esto la tabla queda pegada arriba con
            # toda la hoja vacia debajo (render_table limita la altura de
            # fila a MAX_TABLE_ROW_HEIGHT, y con pocas filas eso deja mucho
            # hueco). Se calcula el alto estimado de la tabla y se baja el
            # cursor la mitad del sobrante antes de dibujarla.
            n_rows = len(half) + 1
            estimated_height = n_rows * MAX_TABLE_ROW_HEIGHT
            available_h = (pdf.h - pdf.b_margin) - pdf.get_y()
            pdf.set_y(pdf.get_y() + max(0, (available_h - estimated_height) / 2))
            render_table(
                pdf, headers, widths, align, render_category_rows(half),
                section_bg=SECTION2_BG, table_width=table_width, table_align="CENTER",
            )

    # --- Glosario (hoja nueva, al final): titulo arriba (como las demas
    # secciones), texto a ancho completo (sin columna estrecha) pero
    # centrado VERTICALMENTE dentro del espacio que queda bajo el titulo.
    glosario_label = f"{len(blocks) + 1}. Glosario"
    pdf.start_section(glosario_label)
    pdf.add_page()

    intro_text = (
        "Este informe compara, para cada accion de tu watchlist, el precio "
        "de inicio del año fiscal frente al precio real actual y el precio "
        "objetivo que tienen los analistas — asi puedes ver de un vistazo "
        "si la accion va mejor o peor de lo esperado.\n\n"
        "Categorias: 'Principales' son las acciones grandes de la "
        "watchlist. 'Pequeña capitalizacion' son empresas con "
        "capitalizacion de mercado menor a 2.000 millones de dolares (mas "
        "volatiles, con menos cobertura de analistas). 'Cesta tematica "
        "(Trump trade)' es un grupo de acciones que la prensa financiera "
        "asocia a politicas de la administracion Trump (aranceles, "
        "defensa, desregulacion...); no es una recomendacion de compra ni "
        "de venta, solo documenta una narrativa de mercado.\n\n"
        "Que significa cada columna:\n"
    )
    columns_text = (
        "Inicio F.Y. / Fin F.Y.: mes/año en que empieza y termina el "
        "año fiscal ACTUAL de cada empresa (no siempre coincide con el "
        "año natural: Apple cierra en septiembre, Microsoft en junio).\n"
        "Precio inicio F.Y.: precio REAL de cierre de la accion en la "
        "fecha de inicio de su año fiscal (o el primer dia de mercado "
        "abierto siguiente, si esa fecha cae en fin de semana/festivo). "
        "Yahoo Finance si da historico real de precios (a diferencia del "
        "precio objetivo de analistas, que solo existe a dia de hoy), asi "
        "que este dato es real desde la primera vez que se genera el "
        "informe, no una aproximacion. n/d si esa fecha cae fuera del "
        "historico disponible para esa accion.\n"
        "P. real actual: precio de la accion en el momento de generar "
        "este informe.\n"
        "P. objetivo: precio medio objetivo actual que los analistas "
        "de bancos/brokers tienen para la accion (consenso de Yahoo "
        "Finance). Este dato SI es solo el de hoy: Yahoo no expone el "
        "objetivo que tenian los analistas en el pasado.\n"
        "Diferencia: cuanto por encima o por debajo queda el precio "
        "real respecto a ese objetivo."
    )
    full_text = intro_text + columns_text

    section_header(pdf, "Referencia", glosario_label)

    pdf.set_font("Helvetica", size=11)
    body_h = pdf.multi_cell(pdf.epw, 6, sanitize(full_text), dry_run=True, output="HEIGHT")
    available_h = (pdf.h - pdf.b_margin) - pdf.get_y()
    pdf.set_y(pdf.get_y() + max(0, (available_h - body_h) / 5))
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, 6, sanitize(full_text))

    out_path = os.path.join(os.path.dirname(__file__), "informe_periodo.pdf")
    pdf.output(out_path)
    return out_path


def main() -> None:
    force_example = "--example" in sys.argv
    symbols = load_watchlist()
    state = load_state()
    today = datetime.now()
    month_key = today.strftime("%Y-%m")
    year_key = today.strftime("%Y")

    current_snapshot = snapshot_prices(symbols)

    # --- Mensual ---
    prev_month_baseline = state["monthly"].get(month_key)
    is_monthly_example = force_example or prev_month_baseline is None
    monthly_baseline = prev_month_baseline or current_snapshot
    january_baseline = state["yearly"].get(year_key) or current_snapshot
    pdf_path = build_comparison_pdf(
        monthly_baseline, current_snapshot,
        f"Informe mensual - cierre de {today.strftime('%B %Y')}",
        is_monthly_example,
        january_baseline=january_baseline,
    )
    send_telegram_document(pdf_path, caption=f"Informe mensual ({today.strftime('%d/%m/%Y')})")

    # Guarda la foto de HOY como baseline del mes que empieza ahora.
    state["monthly"][month_key] = current_snapshot

    # --- Anual (solo el 1 de enero, o si se fuerza el ejemplo) ---
    if today.month == 1 or force_example:
        prev_year_baseline = state["yearly"].get(year_key)
        is_yearly_example = force_example or prev_year_baseline is None
        yearly_baseline = prev_year_baseline or current_snapshot
        pdf_path_year = build_comparison_pdf(
            yearly_baseline, current_snapshot,
            f"Informe anual - cierre de {today.year}",
            is_yearly_example,
            january_baseline=yearly_baseline,
        )
        send_telegram_document(pdf_path_year, caption=f"Informe anual ({today.year})")
        state["yearly"][year_key] = current_snapshot

    save_state(state)


if __name__ == "__main__":
    main()
