"""
Screener de oportunidades de compra: P/E vs sector, PEG, crecimiento de
beneficios e insider buying. Rankea la watchlist, genera un PDF en tabla
con el top 10 (y titulares de noticias recientes) y lo envia a Telegram.

Uso: python screener.py
"""
import os
import unicodedata
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from fpdf import FPDF

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.txt")
INSIDER_LOOKBACK_DAYS = 90
PEG_MAX = 1.5
EARNINGS_GROWTH_MIN = 0.15  # 15%
TOP_N = 10
NEWS_PER_TICKER = 2


def load_watchlist() -> list[str]:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return [
            line.strip().upper()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def has_recent_insider_buying(ticker: yf.Ticker) -> bool:
    try:
        df = ticker.insider_transactions
    except Exception:
        return False
    if df is None or df.empty or "Transaction" not in df or "Start Date" not in df:
        return False
    cutoff = datetime.now() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    buys = df[df["Transaction"].str.contains("Buy", case=False, na=False)]
    recent = buys[pd.to_datetime(buys["Start Date"], errors="coerce") >= cutoff]
    return not recent.empty


RECOMMENDATION_LABELS = {
    "strong_buy": "COMPRA FUERTE",
    "buy": "COMPRA FUERTE",
    "hold": "COMPRA NEUTRAL",
    "underperform": "NO COMPRAR",
    "sell": "NO COMPRAR",
}


def recommendation_label(key: str | None) -> str:
    return RECOMMENDATION_LABELS.get((key or "").lower(), "N/D")


def analyze(symbols: list[str]) -> tuple[list[dict], float]:
    rows = []
    for sym in symbols:
        t = yf.Ticker(sym)
        info = t.info
        pe = info.get("trailingPE")
        growth = info.get("earningsGrowth")  # fraccion, ej 0.18 = 18%
        peg = (pe / (growth * 100)) if pe and growth and growth > 0 else None
        rows.append(
            {
                "symbol": sym,
                "sector": info.get("sector"),
                "country": info.get("country"),
                "pe": pe,
                "growth": growth,
                "peg": peg,
                "insider_buying": has_recent_insider_buying(t),
                "recommendation": recommendation_label(info.get("recommendationKey")),
            }
        )
    valid_pe = [r["pe"] for r in rows if r["pe"]]
    sector_avg_pe = sum(valid_pe) / len(valid_pe) if valid_pe else None
    return rows, sector_avg_pe


def score(r: dict, avg_pe: float | None) -> int:
    checks = {
        "pe_bajo": avg_pe is not None and r["pe"] is not None and r["pe"] < avg_pe,
        "peg_bueno": r["peg"] is not None and r["peg"] < PEG_MAX,
        "crecimiento": r["growth"] is not None and r["growth"] > EARNINGS_GROWTH_MIN,
        "insider_buying": r["insider_buying"],
    }
    r["checks"] = checks
    return sum(checks.values())


def rank_top(rows: list[dict], avg_pe: float | None, n: int = TOP_N) -> list[dict]:
    for r in rows:
        r["score"] = score(r, avg_pe)
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    return ranked[:n]


def get_recent_news(symbol: str, limit: int = NEWS_PER_TICKER) -> list[str]:
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    titles = []
    for item in items[:limit]:
        content = item.get("content", item)  # yfinance nuevo anida en 'content'
        title = content.get("title")
        if title:
            titles.append(title)
    return titles


def sanitize(text: str) -> str:
    """Normaliza puntuacion 'inteligente' y descarta lo que no cabe en latin-1
    (fuentes PDF core no soportan unicode completo, ni en Windows ni en el
    runner de GitHub Actions)."""
    replacements = {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFC", text)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


GLOSSARY = [
    ("P/E", "Precio / Beneficio por accion (trailing). Cuantas veces el "
            "beneficio anual se paga por la accion. Se compara contra el "
            "promedio del grupo analizado, no en terminos absolutos."),
    ("PEG", "P/E dividido por el % de crecimiento esperado de beneficios. "
            "Por debajo de 1.5 sugiere que el precio no esta sobrepagando "
            "ese crecimiento."),
    ("Crecim.", "Crecimiento interanual del beneficio por accion (EPS), "
                "segun estimacion de Yahoo Finance. Por encima del 15% se "
                "considera fuerte."),
    ("Insider buy", "Si algun directivo o accionista relevante compro "
                     "acciones con su propio dinero en los ultimos 90 dias "
                     "(dato de comunicados SEC Form 4, via Yahoo Finance)."),
    ("Recomendacion", "Consenso agregado de analistas de bancos y brokers "
                       "que cubren la accion (Yahoo Finance recopila estas "
                       "notas). COMPRA FUERTE = mayoria buy/strong buy, "
                       "COMPRA NEUTRAL = mayoria hold, NO COMPRAR = mayoria "
                       "underperform/sell, N/D = sin cobertura suficiente."),
]

HEADER_FILL = (30, 60, 90)
ZEBRA_FILL = (235, 238, 242)


def build_pdf(top: list[dict], avg_pe: float | None) -> str:
    from fpdf.fonts import FontFace

    avg_txt = f"{avg_pe:.1f}" if avg_pe else "n/a"
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(True, margin=15)

    # Enlaces internos (uno por termino del glosario). fpdf exige pagina
    # asignada desde ya; apuntan de forma provisional a la pagina 1 y se
    # corrigen mas abajo, una vez sabemos en que pagina cae el glosario.
    glossary_links = {name: pdf.add_link(page=1) for name, _ in GLOSSARY}

    pdf.add_page()
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*HEADER_FILL)
    pdf.cell(0, 10, sanitize(f"Screener de acciones - {datetime.now():%d/%m/%Y %H:%M}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 6, sanitize(f"P/E medio del grupo analizado: {avg_txt}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.cell(0, 6, "Toca los encabezados de columna para saltar a la explicacion de cada variable.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", size=8)
    headers = ["#", "Ticker", "Pais", "Sector", "Score/4", "P/E", "PEG", "Crecim.", "Insider buy", "Recomendacion"]
    link_cols = {"P/E": "P/E", "PEG": "PEG", "Crecim.": "Crecim.", "Insider buy": "Insider buy", "Recomendacion": "Recomendacion"}
    widths = (7, 16, 22, 38, 14, 14, 14, 16, 20, 30)
    headings_style = FontFace(emphasis="B", color=(255, 255, 255), fill_color=HEADER_FILL)
    with pdf.table(
        col_widths=widths,
        text_align="LEFT",
        headings_style=headings_style,
        cell_fill_color=ZEBRA_FILL,
        cell_fill_mode="EVEN_ROWS",
    ) as table:
        row = table.row()
        for h in headers:
            row.cell(h, link=glossary_links[link_cols[h]] if h in link_cols else None)
        for i, o in enumerate(top, start=1):
            row = table.row()
            row.cell(str(i))
            row.cell(o["symbol"])
            row.cell(sanitize(o["country"] or "n/a"))
            row.cell(sanitize(o["sector"] or "n/a"))
            row.cell(f"{o['score']}/4")
            row.cell(f"{o['pe']:.1f}" if o["pe"] else "n/a")
            row.cell(f"{o['peg']:.2f}" if o["peg"] else "n/a")
            row.cell(f"{o['growth']*100:.1f}%" if o["growth"] else "n/a")
            row.cell("Si" if o["insider_buying"] else "No")
            row.cell(o["recommendation"])

    pdf.set_x(pdf.l_margin)
    pdf.ln(6)
    pdf.set_font("Helvetica", size=12, style="B")
    pdf.cell(pdf.epw, 8, "Titulares recientes", new_x="LMARGIN", new_y="NEXT")
    for o in top:
        headlines = get_recent_news(o["symbol"])
        if not headlines:
            continue
        pdf.set_font("Helvetica", size=10, style="B")
        pdf.cell(pdf.epw, 6, sanitize(o["symbol"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        for headline in headlines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.epw, 5, sanitize(f"- {headline}"))
        pdf.ln(2)

    # Pagina de glosario: aqui aterrizan los hipervinculos de la cabecera.
    pdf.add_page()
    glossary_page = pdf.page_no()
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*HEADER_FILL)
    pdf.cell(0, 10, "Glosario de variables", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    for name, explanation in GLOSSARY:
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.cell(0, 7, sanitize(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, sanitize(explanation))
        pdf.ln(3)
    for name in glossary_links:
        pdf.set_link(glossary_links[name], page=glossary_page)

    out_path = os.path.join(os.path.dirname(__file__), "informe.pdf")
    pdf.output(out_path)
    return out_path


def send_telegram_document(path: str, caption: str) -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"document": f},
            timeout=30,
        )
    resp.raise_for_status()


def main() -> None:
    symbols = load_watchlist()
    rows, avg_pe = analyze(symbols)
    top = rank_top(rows, avg_pe)
    pdf_path = build_pdf(top, avg_pe)
    print(f"PDF generado: {pdf_path}")
    send_telegram_document(pdf_path, caption=f"Screener - Top {len(top)} ({datetime.now():%d/%m/%Y})")


if __name__ == "__main__":
    main()
