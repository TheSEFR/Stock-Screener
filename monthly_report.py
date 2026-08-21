"""
Informe retrospectivo (mensual, el dia 1 de cada mes) y anual (el 1 de
enero) que compara, por cada accion y agrupada por region (mismo criterio
region_for()/REGION_BY_COUNTRY que usa el informe principal): precio al
inicio del periodo que acaba de cerrar, precio real que alcanzo al cierre,
y el precio objetivo que tenian los analistas para esa accion en ese
momento.

Limitacion de datos: Yahoo Finance solo expone el precio objetivo ACTUAL
de los analistas, no el que tenian hace un mes o un ano. Por eso este
script empieza a guardar una foto (precio + objetivo) cada vez que corre,
en price_history.json (versionado en git), y solo puede comparar contra
periodos para los que ya tiene una foto guardada. La primera vez que se
ejecuta no hay historico: genera un EJEMPLO usando el precio/objetivo de
hoy en las 3 columnas, dejandolo indicado en el PDF.

Uso: python monthly_report.py [--example]
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import yfinance as yf
from fpdf.fonts import FontFace

from screener import (
    INK,
    NAVY,
    SECTION2_BG,
    SMALL_CAP_MAX,
    TRUMP_TRADE_THEMES,
    ReportPDF,
    _lighten,
    draw_cover_page,
    fiscal_year_end,
    load_watchlist,
    sanitize,
    section_header,
    send_telegram_document,
)

CATEGORY_ORDER = ["Principales", "Pequena capitalizacion", "Cesta tematica (Trump trade)"]


def fiscal_year_start(info: dict) -> str:
    """Inicio del año fiscal ACTUAL = el dia siguiente al cierre del año
    fiscal anterior (Yahoo no da un campo directo de 'inicio')."""
    ts = info.get("lastFiscalYearEnd")
    if not ts:
        return "n/d"
    return (datetime.fromtimestamp(ts) + timedelta(days=1)).strftime("%m/%y")


def categorize(symbol: str, market_cap: float | None) -> str:
    if symbol in TRUMP_TRADE_THEMES:
        return "Cesta tematica (Trump trade)"
    if market_cap is not None and market_cap < SMALL_CAP_MAX:
        return "Pequena capitalizacion"
    return "Principales"

TABLE_FILL = _lighten(NAVY, 0.88)
TABLE_STRIPE = _lighten(NAVY, 0.78)

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
        info = yf.Ticker(sym).info
        market_cap = info.get("marketCap")
        snapshot[sym] = {
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "target": info.get("targetMeanPrice"),
            "category": categorize(sym, market_cap),
            "fiscal_year_end": fiscal_year_end(info),
            "fiscal_year_start": fiscal_year_start(info),
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

    # Paginas de contenido en el mismo tono de la escala de color que usa
    # el informe principal (SECTION2_BG = "Principales acciones").
    pdf.page_background = SECTION2_BG
    pdf.add_page()
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 10, sanitize(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    if is_example:
        pdf.set_font("Helvetica", size=10, style="I")
        pdf.multi_cell(
            pdf.epw, 6,
            "EJEMPLO: todavia no hay historico guardado, asi que 'precio "
            "inicio' y 'precio real' usan el precio de hoy. El informe "
            "real (con datos distintos en cada columna) llegara cuando "
            "haya pasado un periodo completo desde que se empezo a "
            "guardar el historico.",
        )
    pdf.ln(4)

    for category, tickers in blocks.items():
        section_header(pdf, "Categoria", category)
        pdf.set_font("Helvetica", size=9)
        pdf.set_fill_color(*TABLE_FILL)
        headers = ["Ticker", "Inicio F.Y.", "Fin F.Y.", "Precio inicio F.Y.", "Precio real (mes actual)", "P. objetivo", "Diferencia"]
        # Anchos ajustados al contenido real, tabla centrada sin estirar
        # (ver nota en el informe principal sobre 'width' fijo).
        widths = (20, 16, 16, 26, 26, 20, 20)
        headings_style = FontFace(emphasis="B", color=(255, 255, 255), fill_color=NAVY)
        with pdf.table(
            col_widths=widths,
            width=sum(widths),
            align="LEFT",
            text_align=["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
            headings_style=headings_style,
            cell_fill_color=TABLE_STRIPE,
            cell_fill_mode="EVEN_ROWS",
            borders_layout="HORIZONTAL_LINES",
        ) as table:
            row = table.row()
            for h in headers:
                row.cell(h)
            for sym in sorted(tickers):
                now = current.get(sym, {})
                jan = january_baseline.get(sym, {})
                actual_price = now.get("price")
                target = now.get("target")
                january_price = jan.get("price")
                fy_start = now.get("fiscal_year_start", "n/d")
                fy_end = now.get("fiscal_year_end", "n/d")
                diff_txt = "n/d"
                if actual_price and target:
                    diff_pct = (actual_price - target) / target * 100
                    diff_txt = f"{diff_pct:+.1f}%"
                row = table.row()
                row.cell(sym)
                row.cell(fy_start)
                row.cell(fy_end)
                row.cell(f"{january_price:.2f}" if january_price else "n/d")
                row.cell(f"{actual_price:.2f}" if actual_price else "n/d")
                row.cell(f"{target:.2f}" if target else "n/d")
                row.cell(diff_txt)
        pdf.ln(4)

    section_header(pdf, "Referencia", "Que significa cada columna")
    pdf.set_font("Helvetica", size=9)
    pdf.multi_cell(
        pdf.epw, 5,
        sanitize(
            "Inicio F.Y. / Fin F.Y.: mes/año en que empieza y termina el "
            "año fiscal ACTUAL de cada empresa (no siempre coincide con el "
            "año natural: Apple cierra en septiembre, Microsoft en junio).\n"
            "Precio inicio F.Y.: precio capturado en el baseline de "
            "seguimiento mas antiguo disponible, como aproximacion al "
            "inicio del año fiscal (Yahoo no da precios historicos exactos "
            "a la fecha real de inicio de año fiscal de cada empresa). "
            "n/d hasta que haya una foto guardada.\n"
            "Precio real (mes actual): precio de la accion en el momento "
            "de generar este informe.\n"
            "P. objetivo: precio medio objetivo actual que los analistas "
            "de bancos/brokers tienen para la accion (consenso de Yahoo "
            "Finance).\n"
            "Diferencia: cuanto por encima o por debajo queda el precio "
            "real respecto a ese objetivo."
        ),
    )

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
