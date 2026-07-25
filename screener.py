"""
Screener de oportunidades de compra: P/E vs sector, PEG, crecimiento de
beneficios e insider buying. Rankea la watchlist, genera un PDF en tabla
con el top 10 (y titulares de noticias recientes) y lo envia a Telegram.

Uso: python screener.py
"""
import json
import os
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from fpdf import FPDF

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.txt")
INSIDER_LOOKBACK_DAYS = 90
PEG_MAX = 1.5
EARNINGS_GROWTH_MIN = 0.15  # 15%
TOP_N = 10
NEWS_PER_TICKER = 2
DESCRIPTION_MAX_CHARS = 500

STRONG_BUY_GRADES = {
    "buy", "strong buy", "outperform", "overweight",
    "market outperform", "sector outperform", "long-term buy",
}

POSITIVE_WORDS = (
    "beat", "beats", "surge", "surges", "soar", "soars", "record", "upgrade",
    "outperform", "growth", "rally", "rallies", "strong", "raises", "tops",
    "jump", "jumps", "gain", "gains", "expands", "wins", "profit",
)
NEGATIVE_WORDS = (
    "falls", "fall", "drop", "drops", "plunge", "plunges", "cuts", "cut",
    "downgrade", "miss", "misses", "weak", "concern", "concerns", "lawsuit",
    "probe", "recall", "warns", "warning", "slump", "sell-off", "tumbles",
    "loss", "losses", "layoffs", "investigation",
)


def load_watchlist() -> list[str]:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return [
            line.strip().upper()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def has_recent_insider_buying(ticker: yf.Ticker) -> bool | None:
    """True/False si hay datos de transacciones de insiders; None si Yahoo no
    publica ese dato para el ticker (habitual en acciones que no reportan a
    la SEC, es decir la mayoria de acciones no estadounidenses). Devolver
    None en vez de False evita penalizar a esas acciones en el score."""
    try:
        df = ticker.insider_transactions
    except Exception:
        return None
    if df is None or df.empty or "Transaction" not in df or "Start Date" not in df:
        return None
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


REGION_BY_COUNTRY = {
    "United States": "EEUU", "Canada": "EEUU/Canada",
    "Germany": "Europa", "France": "Europa", "Switzerland": "Europa",
    "Netherlands": "Europa", "Spain": "Europa", "Italy": "Europa",
    "United Kingdom": "Europa", "Sweden": "Europa", "Belgium": "Europa",
    "China": "Asia/China", "Taiwan": "Asia/China", "South Korea": "Asia/China",
    "Japan": "Asia/China", "Hong Kong": "Asia/China",
}


def region_for(country: str | None) -> str:
    return REGION_BY_COUNTRY.get(country or "", "Otros")


def analyze(symbols: list[str]) -> tuple[list[dict], float | None]:
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
                "description_en": (info.get("longBusinessSummary") or "")[:DESCRIPTION_MAX_CHARS],
            }
        )

    # P/E medio POR SECTOR: comparar una farmaceutica suiza contra un
    # semiconductor de EEUU con un unico promedio global no es representativo.
    pe_by_sector = defaultdict(list)
    for r in rows:
        if r["pe"]:
            pe_by_sector[r["sector"]].append(r["pe"])
    sector_avg_pe = {sector: sum(vals) / len(vals) for sector, vals in pe_by_sector.items()}

    valid_pe = [r["pe"] for r in rows if r["pe"]]
    global_avg_pe = sum(valid_pe) / len(valid_pe) if valid_pe else None
    for r in rows:
        r["sector_avg_pe"] = sector_avg_pe.get(r["sector"], global_avg_pe)

    return rows, global_avg_pe


def score(r: dict) -> None:
    """Cada check vale None cuando no hay dato para evaluarlo (no aplica),
    en vez de contar como fallo. Asi una accion de un mercado donde Yahoo no
    publica insider trading (la mayoria fuera de EEUU) no queda penalizada
    frente a una accion estadounidense por un dato que nunca podra tener."""
    avg_pe = r.get("sector_avg_pe")
    checks = {
        "pe_bajo": None if avg_pe is None or r["pe"] is None else r["pe"] < avg_pe,
        "peg_bueno": None if r["peg"] is None else r["peg"] < PEG_MAX,
        "crecimiento": None if r["growth"] is None else r["growth"] > EARNINGS_GROWTH_MIN,
        "insider_buying": r["insider_buying"],
    }
    r["checks"] = checks
    applicable = [v for v in checks.values() if v is not None]
    r["checks_applicable"] = len(applicable)
    r["score"] = sum(applicable)
    r["score_ratio"] = (r["score"] / len(applicable)) if applicable else 0.0


def rank_top(rows: list[dict], n: int = TOP_N) -> list[dict]:
    for r in rows:
        score(r)

    def sort_key(r: dict) -> tuple:
        # Desempate deliberado: NO usar el orden del watchlist.txt (que
        # empieza por EEUU) como criterio implicito via sorted() estable,
        # eso favorecia sistematicamente a las primeras acciones de la lista.
        peg = r["peg"] if r["peg"] is not None else float("inf")
        return (-r["score_ratio"], -r["score"], peg)

    ranked = sorted(rows, key=sort_key)
    return ranked[:n]


def translate(text: str, target: str = "es") -> str:
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target=target).translate(text) or text
    except Exception:
        return text  # servicio de traduccion caido: mostramos el original


def crude_sentiment(text: str) -> str:
    """Heuristica por palabras clave, NO es analisis experto ni de un LLM."""
    low = text.lower()
    positive = any(w in low for w in POSITIVE_WORDS)
    negative = any(w in low for w in NEGATIVE_WORDS)
    if positive and not negative:
        return "Posible impacto positivo (heuristica)"
    if negative and not positive:
        return "Posible impacto negativo (heuristica)"
    return "Impacto incierto / mixto (heuristica)"


def get_strong_buy_banks(symbol: str, limit: int = 4) -> list[str]:
    try:
        df = yf.Ticker(symbol).upgrades_downgrades
    except Exception:
        return []
    if df is None or df.empty or "ToGrade" not in df or "Firm" not in df:
        return []
    df = df.sort_index(ascending=False)
    mask = df["ToGrade"].str.lower().isin(STRONG_BUY_GRADES)
    firms = df.loc[mask, "Firm"].dropna().unique().tolist()
    return firms[:limit]


def get_recent_news_detailed(symbol: str, limit: int = NEWS_PER_TICKER) -> list[dict]:
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    out = []
    for item in items[:limit]:
        content = item.get("content", item)  # yfinance nuevo anida en 'content'
        title = content.get("title")
        if not title:
            continue
        summary = content.get("summary") or ""
        link = (content.get("canonicalUrl") or {}).get("url", "")
        out.append(
            {
                "title_es": translate(title),
                "summary_es": translate(summary),
                "link": link,
                "sentiment": crude_sentiment(f"{title} {summary}"),
            }
        )
    return out


def enrich_top(top: list[dict]) -> list[dict]:
    """Trabajo 'caro' (traduccion, notas de bancos, noticias) solo para el
    top ya rankeado, no para toda la watchlist."""
    for o in top:
        o["description_es"] = translate(o["description_en"])
        o["strong_buy_banks"] = get_strong_buy_banks(o["symbol"])
        o["news"] = get_recent_news_detailed(o["symbol"])
    return top


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
    ("Score", "Numero de criterios cumplidos sobre el total de criterios con "
              "datos disponibles para ese ticker (ej. 3/3 si a esa accion "
              "solo le aplican 3 de los 4 criterios). El divisor puede "
              "variar entre acciones: no todos los mercados publican los "
              "mismos datos (ej. insider buying, ver mas abajo), asi que un "
              "3/3 y un 3/4 no son directamente comparables en puntos "
              "brutos, solo en proporcion de aciertos."),
    ("P/E", "Precio / Beneficio por accion (trailing). Cuantas veces el "
            "beneficio anual se paga por la accion. En esta tabla se compara "
            "contra el promedio DEL MISMO SECTOR (no el global), para no "
            "comparar por ejemplo un banco con una tecnologica. Como "
            "referencia general (varía mucho por sector): por debajo de 15 "
            "se suele considerar barato, entre 15 y 25 razonable, por "
            "encima de 25-30 caro / de alto crecimiento."),
    ("PEG", "P/E dividido por el % de crecimiento esperado de beneficios. "
            "Por debajo de 1.5 sugiere que el precio no esta sobrepagando "
            "ese crecimiento; por debajo de 1 se suele considerar barato."),
    ("Crecim.", "Crecimiento interanual del beneficio por accion (EPS), "
                "segun estimacion de Yahoo Finance. Por encima del 15% se "
                "considera fuerte."),
    ("Insider buy", "Si algun directivo o accionista relevante compro "
                     "acciones con su propio dinero en los ultimos 90 dias "
                     "(dato de comunicados SEC Form 4, via Yahoo Finance). "
                     "N/D significa que Yahoo no publica este dato para ese "
                     "ticker, algo habitual fuera de EEUU (solo las "
                     "empresas que reportan a la SEC presentan Form 4); en "
                     "ese caso el criterio no cuenta ni a favor ni en "
                     "contra en el score."),
    ("Recomendacion", "Consenso agregado de analistas de bancos y brokers "
                       "que cubren la accion (Yahoo Finance recopila estas "
                       "notas). COMPRA FUERTE = mayoria buy/strong buy, "
                       "COMPRA NEUTRAL = mayoria hold, NO COMPRAR = mayoria "
                       "underperform/sell, N/D = sin cobertura suficiente."),
    ("Bancos", "Firmas de analisis (bancos de inversion, brokers) cuya nota "
               "mas reciente sobre la accion fue de compra/sobreponderar. "
               "Fuente: notas de upgrade/downgrade recopiladas por Yahoo "
               "Finance, no son recomendaciones propias."),
    ("Sentimiento noticia", "Etiqueta automatica por palabras clave sobre el "
                             "titular+resumen de cada noticia. Es una "
                             "heuristica simple, NO un analisis experto ni "
                             "generado por IA: leela como orientacion, no "
                             "como veredicto."),
]

HEADER_FILL = (30, 60, 90)      # portada / tabla - azul
DESC_FILL = (34, 120, 80)       # descripciones - verde
NEWS_FILL = (200, 110, 20)      # noticias - naranja
GLOSSARY_FILL = (90, 50, 120)   # glosario - morado
ZEBRA_FILL = (235, 238, 242)


def pe_verdict(pe: float | None) -> str:
    if pe is None:
        return "n/d"
    if pe < 15:
        return "barato (ref. general)"
    if pe <= 25:
        return "razonable (ref. general)"
    return "caro / alto crecimiento (ref. general)"


class ReportPDF(FPDF):
    def footer(self) -> None:
        if self.page_no() == 1:
            return  # portada con fondo de color propio, sin pie de pagina
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 8, sanitize(f"Pagina {self.page_no()} de {{nb}}"), align="C")


def section_header(pdf: FPDF, text: str, color: tuple[int, int, int]) -> None:
    pdf.set_fill_color(*color)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", size=14, style="B")
    pdf.cell(pdf.epw, 10, sanitize(text), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)


def render_toc(pdf: FPDF, outline) -> None:
    pdf.set_font("Helvetica", size=16, style="B")
    pdf.set_text_color(*HEADER_FILL)
    pdf.cell(0, 12, "Indice", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)
    pdf.set_font("Helvetica", size=12)
    for section in outline:
        link = pdf.add_link(page=section.page_number)
        indent = "    " * section.level
        pdf.cell(
            0, 10,
            sanitize(f"{indent}{section.name}  ...  pag. {section.page_number}"),
            new_x="LMARGIN", new_y="NEXT", link=link,
        )


def build_pdf(top: list[dict], rows: list[dict], avg_pe: float | None) -> str:
    from fpdf.fonts import FontFace

    avg_txt = f"{avg_pe:.1f}" if avg_pe else "n/a"
    coverage = Counter(region_for(r["country"]) for r in rows)
    coverage_txt = " - ".join(f"{region}: {n}" for region, n in coverage.most_common())
    top_coverage = Counter(region_for(o["country"]) for o in top)
    top_coverage_txt = " - ".join(f"{region}: {n}" for region, n in top_coverage.most_common())

    pdf = ReportPDF(orientation="L", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=15)

    # Enlaces internos del glosario (P/E, PEG, etc. -> definicion). fpdf
    # exige pagina asignada desde ya; se corrigen al final del todo.
    glossary_links = {name: pdf.add_link(page=1) for name, _ in GLOSSARY}

    # --- Portada ---
    pdf.add_page()
    pdf.set_fill_color(*HEADER_FILL)
    pdf.rect(0, 0, pdf.w, pdf.h, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_y(pdf.h / 2 - 30)
    pdf.set_font("Helvetica", size=26, style="B")
    pdf.cell(0, 14, "Screener de acciones", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=14)
    pdf.cell(0, 10, sanitize(f"Informe generado el {datetime.now():%d/%m/%Y a las %H:%M}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"Top {len(top)} de la watchlist analizada - P/E medio global: {avg_txt}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 6, sanitize(f"Cobertura del analisis: {len(rows)} acciones - {coverage_txt}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, sanitize(f"Mercados en este Top {len(top)}: {top_coverage_txt}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", size=9, style="I")
    pdf.multi_cell(
        pdf.epw, 5,
        "Informe automatico basado en datos publicos (Yahoo Finance). No "
        "constituye asesoramiento financiero ni recomendacion de inversion "
        "personalizada.",
        align="C",
    )
    pdf.set_text_color(0, 0, 0)

    # --- Indice (pagina reservada, se rellena sola al final) ---
    pdf.add_page()
    pdf.insert_toc_placeholder(render_toc)

    # --- Seccion 1: Tabla resumen ---
    # Sin add_page() aqui: insert_toc_placeholder ya salto a una pagina
    # nueva; añadir otra generaba una pagina en blanco de mas en cada informe.
    pdf.start_section("Tabla resumen (Top 10)")
    section_header(pdf, "1. Tabla resumen (Top 10)", HEADER_FILL)
    pdf.set_font("Helvetica", size=8, style="I")
    pdf.cell(0, 6, "Toca los encabezados de columna para saltar a la explicacion de cada variable (seccion Glosario).", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", size=8)
    headers = ["#", "Ticker", "Pais", "Sector", "Score", "P/E", "PEG", "Crecim.", "Insider buy", "Recomendacion"]
    link_cols = {"Score", "P/E", "PEG", "Crecim.", "Insider buy", "Recomendacion"}
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
            row.cell(h, link=glossary_links[h] if h in link_cols else None)
        for i, o in enumerate(top, start=1):
            row = table.row()
            row.cell(str(i))
            row.cell(o["symbol"])
            row.cell(sanitize(o["country"] or "n/a"))
            row.cell(sanitize(o["sector"] or "n/a"))
            row.cell(f"{o['score']}/{o['checks_applicable']}")
            row.cell(f"{o['pe']:.1f}" if o["pe"] else "n/a")
            row.cell(f"{o['peg']:.2f}" if o["peg"] else "n/a")
            row.cell(f"{o['growth']*100:.1f}%" if o["growth"] else "n/a")
            insider = o["insider_buying"]
            row.cell("N/D" if insider is None else ("Si" if insider else "No"))
            row.cell(o["recommendation"])

    # --- Seccion 2: Descripcion detallada por accion ---
    # add_page() antes de start_section: si no, el indice enlaza a la
    # pagina anterior (la de la tabla), no a la de esta seccion.
    pdf.add_page()
    pdf.start_section("Descripcion detallada por accion")
    section_header(pdf, "2. Descripcion detallada por accion", DESC_FILL)
    for i, o in enumerate(top, start=1):
        pdf.set_font("Helvetica", size=12, style="B")
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 8, sanitize(f"{i}. {o['symbol']} ({o['sector'] or 'n/a'}, {o['country'] or 'n/a'})"), new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", size=9)
        pdf.set_x(pdf.l_margin)
        pe_txt = f"{o['pe']:.1f}" if o["pe"] else "n/d"
        sector_avg_txt = f"{o['sector_avg_pe']:.1f}" if o.get("sector_avg_pe") else "n/d"
        pdf.cell(
            0, 6,
            sanitize(f"P/E: {pe_txt} -> {pe_verdict(o['pe'])} | media del sector ({o['sector'] or 'n/a'}): {sector_avg_txt}"),
            link=glossary_links["P/E"], new_x="LMARGIN", new_y="NEXT",
        )

        banks = o.get("strong_buy_banks") or []
        banks_txt = ", ".join(banks) if banks else "sin nota de compra fuerte reciente"
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, "Bancos/entidades con compra fuerte:", link=glossary_links["Bancos"])
        pdf.ln(6)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, sanitize(banks_txt))

        pdf.set_x(pdf.l_margin)
        description = o.get("description_es") or "Sin descripcion disponible."
        pdf.multi_cell(pdf.epw, 5, sanitize(description))
        pdf.ln(4)

    # --- Seccion 3: Noticias recientes (al final, antes del glosario) ---
    pdf.add_page()
    pdf.start_section("Noticias recientes")
    section_header(pdf, "3. Noticias recientes (traducidas)", NEWS_FILL)
    for o in top:
        news = o.get("news") or []
        if not news:
            continue
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, sanitize(o["symbol"]), new_x="LMARGIN", new_y="NEXT")
        for item in news:
            pdf.set_font("Helvetica", size=9, style="B")
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                pdf.epw, 5, sanitize(f"- {item['title_es']}"),
                link=item["link"] or None,
            )
            if item["summary_es"]:
                pdf.set_font("Helvetica", size=9)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(pdf.epw, 5, sanitize(item["summary_es"]))
            pdf.set_font("Helvetica", size=8, style="I")
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 5, sanitize(item["sentiment"]), link=glossary_links["Sentimiento noticia"], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        pdf.ln(2)

    # --- Seccion 4: Glosario (aqui aterrizan todos los hipervinculos) ---
    pdf.add_page()
    pdf.start_section("Glosario de variables")
    glossary_page = pdf.page_no()
    section_header(pdf, "4. Glosario de variables", GLOSSARY_FILL)
    for name, explanation in GLOSSARY:
        pdf.set_font("Helvetica", size=11, style="B")
        pdf.set_x(pdf.l_margin)
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


GENERATE_NOW_BUTTON = {
    "inline_keyboard": [[{"text": "Generar informe ahora", "callback_data": "informe"}]]
}


def send_telegram_document(path: str, caption: str) -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
                "reply_markup": json.dumps(GENERATE_NOW_BUTTON),
            },
            files={"document": f},
            timeout=30,
        )
    resp.raise_for_status()


def generate_and_send_report() -> None:
    symbols = load_watchlist()
    rows, avg_pe = analyze(symbols)
    top = rank_top(rows)
    top = enrich_top(top)
    pdf_path = build_pdf(top, rows, avg_pe)
    print(f"PDF generado: {pdf_path}")
    send_telegram_document(pdf_path, caption=f"Screener - Top {len(top)} ({datetime.now():%d/%m/%Y})")


if __name__ == "__main__":
    generate_and_send_report()
