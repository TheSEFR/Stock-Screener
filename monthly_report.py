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
from datetime import datetime

import yfinance as yf
from fpdf import FPDF
from fpdf.fonts import FontFace

from screener import (
    INK,
    NAVY,
    load_watchlist,
    region_for,
    sanitize,
    section_header,
    send_telegram_document,
)

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
        snapshot[sym] = {
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "target": info.get("targetMeanPrice"),
            "region": region_for(info.get("country")),
        }
    return snapshot


def group_by_region(snapshot: dict[str, dict]) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = defaultdict(list)
    for sym, data in snapshot.items():
        blocks[data["region"]].append(sym)
    return dict(blocks)


def build_comparison_pdf(
    baseline: dict[str, dict],
    current: dict[str, dict],
    title: str,
    is_example: bool,
) -> str:
    blocks = group_by_region(current)
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(True, margin=15)
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

    for region, tickers in blocks.items():
        section_header(pdf, "Bloque", region)
        pdf.set_font("Helvetica", size=9)
        headers = ["Ticker", "Precio inicio periodo", "Precio real alcanzado", "Objetivo analistas", "Diferencia vs objetivo"]
        widths = (25, 40, 40, 40, 40)
        headings_style = FontFace(emphasis="B", color=INK, fill_color=(230, 235, 242))
        with pdf.table(
            col_widths=widths,
            text_align="LEFT",
            headings_style=headings_style,
            cell_fill_color=(240, 243, 248),
            cell_fill_mode="EVEN_ROWS",
            borders_layout="HORIZONTAL_LINES",
        ) as table:
            row = table.row()
            for h in headers:
                row.cell(h)
            for sym in sorted(tickers):
                base = baseline.get(sym, {})
                now = current.get(sym, {})
                start_price = base.get("price")
                actual_price = now.get("price")
                target = base.get("target")
                diff_txt = "n/d"
                if actual_price and target:
                    diff_pct = (actual_price - target) / target * 100
                    diff_txt = f"{diff_pct:+.1f}%"
                row = table.row()
                row.cell(sym)
                row.cell(f"{start_price:.2f}" if start_price else "n/d")
                row.cell(f"{actual_price:.2f}" if actual_price else "n/d")
                row.cell(f"{target:.2f}" if target else "n/d")
                row.cell(diff_txt)
        pdf.ln(4)

    section_header(pdf, "Referencia", "Que significa cada columna")
    pdf.set_font("Helvetica", size=9)
    pdf.multi_cell(
        pdf.epw, 5,
        sanitize(
            "Precio inicio periodo: precio de la accion capturado el primer "
            "dia del periodo (mes o ano) que acaba de cerrar.\n"
            "Precio real alcanzado: precio de la accion en el momento de "
            "generar este informe (cierre del periodo).\n"
            "Objetivo analistas: precio medio objetivo que los analistas de "
            "bancos/brokers tenian para la accion al inicio del periodo "
            "(consenso de Yahoo Finance).\n"
            "Diferencia vs objetivo: cuanto por encima o por debajo quedo "
            "el precio real respecto a ese objetivo."
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
    pdf_path = build_comparison_pdf(
        monthly_baseline, current_snapshot,
        f"Informe mensual - cierre de {today.strftime('%B %Y')}",
        is_monthly_example,
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
        )
        send_telegram_document(pdf_path_year, caption=f"Informe anual ({today.year})")
        state["yearly"][year_key] = current_snapshot

    save_state(state)


if __name__ == "__main__":
    main()
