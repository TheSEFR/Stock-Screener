"""
Screener de oportunidades de compra: P/E vs sector, PEG, crecimiento de
beneficios e insider buying. Rankea la watchlist y envia el top 10 (con
titulares de noticias recientes de cada una) a Telegram.

Uso: python screener.py
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

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
                "pe": pe,
                "growth": growth,
                "peg": peg,
                "insider_buying": has_recent_insider_buying(t),
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


def format_message(top: list[dict], avg_pe: float | None) -> str:
    if not top:
        return "Screener: sin datos disponibles hoy."
    avg_txt = f"{avg_pe:.1f}" if avg_pe else "n/a"
    lines = [f"Top {len(top)} de la watchlist (P/E medio del grupo: {avg_txt}):\n"]
    for i, o in enumerate(top, start=1):
        pe_txt = f"{o['pe']:.1f}" if o["pe"] else "n/a"
        peg_txt = f"{o['peg']:.2f}" if o["peg"] else "n/a"
        growth_txt = f"{o['growth']*100:.1f}%" if o["growth"] else "n/a"
        lines.append(
            f"{i}. {o['symbol']} ({o['sector']}) — puntuacion {o['score']}/4 — "
            f"P/E {pe_txt} | PEG {peg_txt} | crecimiento {growth_txt} | "
            f"insider buying: {'si' if o['insider_buying'] else 'no'}"
        )
        for headline in get_recent_news(o["symbol"]):
            lines.append(f"    - {headline}")
    return "\n".join(lines)


def send_telegram(message: str) -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=15)
    resp.raise_for_status()


def main() -> None:
    symbols = load_watchlist()
    rows, avg_pe = analyze(symbols)
    top = rank_top(rows, avg_pe)
    message = format_message(top, avg_pe)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
