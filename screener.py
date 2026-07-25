"""
Screener de oportunidades de compra: P/E vs sector, PEG, crecimiento de
beneficios e insider buying. Envia un resumen a Telegram.

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


def find_opportunities(rows: list[dict], avg_pe: float | None) -> list[dict]:
    opportunities = []
    for r in rows:
        if not avg_pe or not r["pe"]:
            continue
        checks = {
            "pe_bajo": r["pe"] < avg_pe,
            "peg_bueno": r["peg"] is not None and r["peg"] < PEG_MAX,
            "crecimiento": r["growth"] is not None and r["growth"] > EARNINGS_GROWTH_MIN,
            "insider_buying": r["insider_buying"],
        }
        # Oportunidad = P/E bajo + al menos 2 de las otras 3 senales
        if checks["pe_bajo"] and sum(checks.values()) >= 3:
            opportunities.append({**r, "checks": checks})
    return opportunities


def format_message(opportunities: list[dict], avg_pe: float | None) -> str:
    if not opportunities:
        return "Screener: sin oportunidades claras hoy en la watchlist."
    lines = [f"Oportunidades detectadas (P/E medio watchlist: {avg_pe:.1f}):\n"]
    for o in opportunities:
        peg_txt = f"{o['peg']:.2f}" if o["peg"] else "n/a"
        growth_txt = f"{o['growth']*100:.1f}%" if o["growth"] else "n/a"
        lines.append(
            f"• {o['symbol']} ({o['sector']}) — P/E {o['pe']:.1f} | "
            f"PEG {peg_txt} | crecimiento {growth_txt} | "
            f"insider buying: {'si' if o['insider_buying'] else 'no'}"
        )
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
    opportunities = find_opportunities(rows, avg_pe)
    message = format_message(opportunities, avg_pe)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
