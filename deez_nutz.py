"""
Short Squeeze Scanner (deez_nutz.py)
--------------------------------------
Finds stocks with high short interest that are starting to move up —
the setup for a potential short squeeze.

Signals:
  - Short interest > 15% of float
  - Days to cover > 5
  - Price up 2%+ over last 5 days (squeeze starting)
  - Rising volume on up days (buyers coming in)
  - Price above 20-day MA (uptrend supporting)
  - RSI < 75 (not already overbought)
  - No earnings within 14 days (avoids pre-earnings hedging)

Data:
  - Short interest: Finviz (free, scraped)
  - Price/volume/RSI: yfinance (free)
  - News: Google News RSS (free)

Setup:
    pip install yfinance pandas tabulate requests python-dotenv ta

Run:
    python deez_nutz.py
"""

import sys, time, math, re
import xml.etree.ElementTree as ET
from datetime import date, timedelta, datetime
from collections import Counter

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import requests
    import ta
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas tabulate requests ta")
    sys.exit(1)

from config import SQUEEZE_UNIVERSE

SCAN_UNIVERSE = SQUEEZE_UNIVERSE

# ─── CONFIG ────────────────────────────────────────────────────────────────────

SQUEEZE_PARAMS = {
    "min_short_interest_pct":    15.0,
    "min_days_to_cover":          5.0,
    "min_5d_gain_pct":            2.0,
    "require_above_20ma":         True,
    "min_volume_ratio":           1.2,
    "max_rsi":                   75.0,  # filter overbought — squeeze may be done
    "skip_near_earnings_days":   14,    # avoid pre-earnings short hedging
    "min_dte":                   14,
    "max_dte":                   45,
    "min_delta":                 0.30,
    "max_delta":                 0.60,
    "min_open_interest":         50,
    "min_bid_ask_ratio":         0.70,
}

# ─── BLACK-SCHOLES DELTA ───────────────────────────────────────────────────────

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_delta(S, K, T, iv, r=0.05):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        return norm_cdf(d1)
    except Exception:
        return None

# ─── EARNINGS CHECK ────────────────────────────────────────────────────────────

def has_near_earnings(symbol: str, within_days: int) -> bool:
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return False
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if hasattr(ed, "__iter__") and not isinstance(ed, str):
                ed = list(ed)
                ed = ed[0] if ed else None
        elif hasattr(cal, "loc"):
            ed = cal.loc["Earnings Date"].iloc[0]
        if ed is None:
            return False
        if hasattr(ed, "date"):
            ed = ed.date()
        elif isinstance(ed, str):
            ed = datetime.strptime(ed[:10], "%Y-%m-%d").date()
        return 0 <= (ed - date.today()).days <= within_days
    except Exception:
        return False

# ─── NEWS FETCH ────────────────────────────────────────────────────────────────

SKIP_PHRASES = {
    "passive income", "retire", "millionaire", "best stocks",
    "buy right now", "should you buy", "magnificent", "i fought the law",
}

def get_news(symbol: str) -> str:
    try:
        url  = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
        r    = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        good = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            if any(p in title.lower() for p in SKIP_PHRASES):
                continue
            if title:
                good.append(title)
            if len(good) >= 2:
                break
        return " | ".join(good) if good else "no recent news"
    except Exception:
        return "(news fetch failed)"

# ─── FINVIZ SHORT INTEREST SCRAPER ────────────────────────────────────────────

def get_short_interest(symbol: str) -> dict | None:
    """
    Scrapes short interest data from Finviz.
    Returns short_float_pct, days_to_cover, and borrow_rate.
    Note: updated twice monthly per FINRA schedule.
    """
    try:
        url     = f"https://finviz.com/quote.ashx?t={symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r       = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None

        html = r.text

        # Short float %
        short_float = None
        m = re.search(r'Short Float[^<]*</td>\s*<td[^>]*>([\d.]+)%', html, re.DOTALL)
        if not m:
            m = re.search(r'Short Float.*?(\d+\.?\d*)%', html, re.DOTALL)
        if m:
            short_float = float(m.group(1))

        # Short ratio / days to cover
        days_to_cover = None
        m = re.search(r'Short Ratio[^<]*</td>\s*<td[^>]*>([\d.]+)', html, re.DOTALL)
        if not m:
            m = re.search(r'Short Ratio.*?(\d+\.?\d*)', html, re.DOTALL)
        if m:
            days_to_cover = float(m.group(1))

        # Short interest change — is it increasing or decreasing?
        # Finviz shows "Short Interest" as share count sometimes
        si_trend = None
        m = re.search(r'Short Interest.*?(\d[\d,]+)', html, re.DOTALL)
        if m:
            si_trend = "available"

        if short_float is None and days_to_cover is None:
            return None

        return {
            "short_float_pct": short_float,
            "days_to_cover":   days_to_cover,
            "si_data_found":   True,
        }
    except Exception:
        return None

# ─── PRICE / VOLUME / RSI ANALYSIS ───────────────────────────────────────────

def analyze_price_action(symbol: str, params: dict) -> dict | None:
    try:
        hist = yf.Ticker(symbol).history(period="6mo")
    except Exception:
        return None

    if hist is None or len(hist) < 25:
        return None

    close  = hist["Close"]
    high   = hist["High"]   # intraday highs for accurate 52w high
    volume = hist["Volume"]
    price  = float(close.iloc[-1])

    # 5-day gain
    price_5d_ago = float(close.iloc[-6]) if len(close) >= 6 else price
    gain_5d      = (price - price_5d_ago) / price_5d_ago * 100
    if gain_5d < params["min_5d_gain_pct"]:
        return None

    # Above 20-day MA
    ma_20 = float(close.rolling(20).mean().iloc[-1])
    if params["require_above_20ma"] and price < ma_20:
        return None

    # RSI check — filter overbought (squeeze likely done)
    rsi = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
    if rsi > params["max_rsi"]:
        return None

    # Volume ratio — only count UP days to confirm buying pressure
    up_days        = hist[close.diff() > 0]
    recent_up_vol  = float(up_days["Volume"].iloc[-5:].mean()) if len(up_days) >= 5 else 0
    avg_vol        = float(volume.iloc[-20:].mean())
    vol_ratio      = recent_up_vol / avg_vol if avg_vol > 0 else 1.0
    if vol_ratio < params["min_volume_ratio"]:
        return None

    # 52-week high proximity using intraday highs
    high_52w      = float(high.iloc[-252:].max()) if len(high) >= 252 else float(high.max())
    pct_from_high = (high_52w - price) / high_52w * 100

    # RSI divergence — price higher lows but RSI higher lows = momentum building
    rsi_series       = ta.momentum.RSIIndicator(close, window=14).rsi()
    price_low_recent = float(close.iloc[-5:].min())
    price_low_prior  = float(close.iloc[-15:-5].min())
    rsi_low_recent   = float(rsi_series.iloc[-5:].min())
    rsi_low_prior    = float(rsi_series.iloc[-15:-5].min())
    rsi_rising       = rsi_low_recent > rsi_low_prior  # momentum accelerating

    return {
        "price":          round(price, 2),
        "gain_5d":        round(gain_5d, 1),
        "rsi":            round(rsi, 1),
        "ma_20":          round(ma_20, 2),
        "vol_ratio":      round(vol_ratio, 2),
        "high_52w":       round(high_52w, 2),
        "pct_from_high":  round(pct_from_high, 1),
        "rsi_rising":     rsi_rising,
    }

# ─── SCORE ─────────────────────────────────────────────────────────────────────

def squeeze_score(short_float, days_to_cover, gain_5d, vol_ratio,
                  pct_from_high, rsi, rsi_rising) -> float:
    si_score       = min((short_float - 15) / 35, 1.0)
    dtc_score      = min((days_to_cover - 5) / 15, 1.0)
    momentum_score = min(gain_5d / 15, 1.0)
    vol_score      = min((vol_ratio - 1.2) / 2, 1.0)
    high_score     = max(0, 1 - pct_from_high / 30)
    # RSI sweet spot: 50-65 is ideal for a squeeze in progress
    rsi_score      = max(0, 1 - abs(rsi - 60) / 25)
    rsi_bonus      = 0.05 if rsi_rising else 0.0

    return round(min(1.0,
        si_score       * 0.28 +
        dtc_score      * 0.22 +
        momentum_score * 0.18 +
        vol_score      * 0.12 +
        high_score     * 0.10 +
        rsi_score      * 0.10 +
        rsi_bonus
    ), 3)

# ─── FIND OPTIONS ──────────────────────────────────────────────────────────────

def find_calls(symbol: str, price: float, params: dict) -> dict | None:
    today   = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    best    = None

    try:
        ticker = yf.Ticker(symbol)
        exps   = ticker.options
    except Exception:
        return None

    for exp_str in exps:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (min_exp <= exp_date <= max_exp):
            continue
        dte = (exp_date - today).days
        try:
            calls = ticker.option_chain(exp_str).calls
        except Exception:
            continue
        T = dte / 365.0
        for _, row in calls.iterrows():
            iv     = row.get("impliedVolatility")
            bid    = row.get("bid")
            ask    = row.get("ask")
            oi     = row.get("openInterest") or 0
            strike = row.get("strike")
            if iv is None or pd.isna(iv) or float(iv) <= 0:
                continue
            delta = bs_delta(price, float(strike), T, float(iv))
            if delta is None:
                continue
            d = delta
            if not (params["min_delta"] <= d <= params["max_delta"]):
                continue
            if int(oi) < params["min_open_interest"]:
                continue
            bar, mid = 0.0, None
            if bid and ask and float(ask) > 0:
                bar = float(bid) / float(ask)
                mid = (float(bid) + float(ask)) / 2
            if bar < params["min_bid_ask_ratio"]:
                continue
            opt_score = (1 - abs(d - 0.45) / 0.15) * 0.6 + bar * 0.4
            if best is None or opt_score > best["opt_score"]:
                best = {
                    "strike":    float(strike),
                    "exp":       exp_str,
                    "dte":       dte,
                    "delta":     round(d, 3),
                    "iv":        round(float(iv) * 100, 1),
                    "oi":        int(oi),
                    "mid":       round(mid, 2) if mid else None,
                    "bar":       round(bar, 2),
                    "opt_score": round(opt_score, 3),
                }
    return best

# ─── MAIN SCAN ─────────────────────────────────────────────────────────────────

def run_scan(params: dict) -> list[dict]:
    results    = []
    si_found   = 0
    si_missing = 0

    print(f"Scanning {len(SCAN_UNIVERSE)} symbols for short squeeze setups...\n")
    print("  (Short interest from Finviz — updated twice monthly per FINRA)\n")

    for symbol in SCAN_UNIVERSE:

        # Earnings check
        if has_near_earnings(symbol, params["skip_near_earnings_days"]):
            print(f"  skip {symbol:6s}  (earnings within {params['skip_near_earnings_days']} days)")
            continue

        # Short interest
        si = get_short_interest(symbol)
        if not si:
            si_missing += 1
            continue
        si_found += 1

        short_float   = si.get("short_float_pct")
        days_to_cover = si.get("days_to_cover")

        if short_float is None or short_float < params["min_short_interest_pct"]:
            continue
        if days_to_cover is None or days_to_cover < params["min_days_to_cover"]:
            continue

        # Quick check — is this symbol still trading?
        try:
            info = yf.Ticker(symbol).fast_info
            if not info or not info.last_price or info.last_price <= 0:
                print(f"  skip {symbol:6s}  (no price data — possibly delisted)")
                continue
        except Exception:
            print(f"  skip {symbol:6s}  (possibly delisted)")
            continue

        # Price action + RSI
        pa = analyze_price_action(symbol, params)
        if not pa:
            continue

        # Score
        score = squeeze_score(
            short_float, days_to_cover,
            pa["gain_5d"], pa["vol_ratio"],
            pa["pct_from_high"], pa["rsi"], pa["rsi_rising"]
        )

        # News
        news = get_news(symbol)

        # Options
        opt = find_calls(symbol, pa["price"], params)

        rsi_flag = " ⚡RSI rising" if pa["rsi_rising"] else ""
        print(f"  🔥 {symbol:6s}  short {short_float:.1f}%  DTC {days_to_cover:.1f}  "
              f"+{pa['gain_5d']:.1f}% 5d  RSI {pa['rsi']:.0f}  vol {pa['vol_ratio']:.1f}x  "
              f"score {score}{rsi_flag}")

        result = {
            "symbol":        symbol,
            "price":         pa["price"],
            "short_float":   round(short_float, 1),
            "days_to_cover": round(days_to_cover, 1),
            "gain_5d":       pa["gain_5d"],
            "rsi":           pa["rsi"],
            "rsi_rising":    pa["rsi_rising"],
            "vol_ratio":     pa["vol_ratio"],
            "pct_from_high": pa["pct_from_high"],
            "score":         score,
            "news":          news,
        }

        if opt:
            result.update({
                "strike": opt["strike"], "exp": opt["exp"],
                "dte":    opt["dte"],    "delta": opt["delta"],
                "iv":     opt["iv"],     "oi": opt["oi"],
                "mid":    opt["mid"],    "bar": opt["bar"],
            })
        else:
            result.update({
                "strike": None, "exp": None, "dte": None,
                "delta":  None, "iv":  None, "oi":  None,
                "mid":    None, "bar": None,
            })

        results.append(result)
        time.sleep(1.0)

    print(f"\n  Short interest data: {si_found} symbols found, {si_missing} missing")
    if si_missing > si_found:
        print("  ⚠ More missing than found — Finviz may be blocking scraping.")
        print("    Try running again in a few minutes.\n")

    return results

# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = [
    "symbol", "price", "short_float", "days_to_cover",
    "gain_5d", "rsi", "vol_ratio", "pct_from_high",
    "strike", "exp", "dte", "delta", "iv", "oi", "mid", "bar", "score"
]
HDRS = [
    "Symbol", "Price", "Short %", "DTC",
    "5d Gain", "RSI", "Vol Ratio", "% From High",
    "Strike", "Expiration", "DTE", "Delta", "IV %", "OI", "Mid $", "B/A", "Score"
]

def display(df: pd.DataFrame):
    if df.empty:
        print("No squeeze candidates found.")
        print("Try lowering min_short_interest_pct or min_5d_gain_pct in SQUEEZE_PARAMS.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "═"*150)
    print("  SHORT SQUEEZE SCANNER  (deez_nutz.py)  —  High Short Interest + Rising Price")
    print("  Strategy: Buy calls on heavily shorted stocks starting to move up")
    print("═"*150 + "\n")

    print(tabulate(
        df[COLS].head(20),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    # News section
    print("\nRecent news:\n")
    for _, row in df.head(10).iterrows():
        rsi_tag = " ⚡" if row["rsi_rising"] else ""
        print(f"  {row['symbol']:6s}{rsi_tag}: {str(row['news'])[:120]}")

    print(f"""
{'─'*150}
HOW TO READ THIS:

  Short %      — % of float sold short. > 20% = heavy, > 30% = extreme
  DTC          — Days To Cover. Higher = shorts more trapped = bigger squeeze potential
  5d Gain      — Price move in last 5 days. Squeeze already starting.
  RSI          — Momentum. 50-70 = sweet spot. > 75 filtered out (overbought, squeeze may be done)
  Vol Ratio    — Recent UP-day volume vs average. > 2x = strong buying pressure confirmed
  % From High  — Distance from 52-week high. Near 0% = shorts most underwater and desperate to cover
  ⚡ RSI rising — RSI momentum accelerating = squeeze gaining strength

BEFORE TRADING:
  ✓ Read the news — understand WHY the stock is heavily shorted
     Valid thesis (fraud, broken business) → shorts may be right, skip it
     Stale thesis (old news, sector rotation) → good squeeze candidate
  ✓ Check for a catalyst — earnings surprise, partnership, product launch accelerates squeezes
  ✓ Size small — squeezes reverse violently once shorts finish covering
  ✓ Exit when RSI > 75 or premium doubles — don't get greedy
  ✓ Never hold through earnings on a squeeze trade
{'─'*150}
""")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print("\033[92;1m" + "=" * 70)
    print("\033[92;1m" + """
  ███████╗██╗  ██╗ ██████╗ ██████╗ ████████╗
  ██╔════╝██║  ██║██╔═══██╗██╔══██╗╚══██╔══╝
  ███████╗███████║██║   ██║██████╔╝   ██║
  ╚════██║██╔══██║██║   ██║██╔══██╗   ██║
  ███████║██║  ██║╚██████╔╝██║  ██║   ██║
  ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝

  ███████╗ ██████╗ ██╗   ██╗███████╗███████╗███████╗███████╗
  ██╔════╝██╔═══██╗██║   ██║██╔════╝██╔════╝╚══███╔╝██╔════╝
  ███████╗██║   ██║██║   ██║█████╗  █████╗    ███╔╝ █████╗
  ╚════██║██║▄▄ ██║██║   ██║██╔══╝  ██╔══╝   ███╔╝  ██╔══╝
  ███████║╚██████╔╝╚██████╔╝███████╗███████╗███████╗███████╗
  ╚══════╝ ╚══▀▀═╝  ╚═════╝ ╚══════╝╚══════╝╚══════╝╚══════╝

  ██████╗ ███████╗███████╗███████╗    ███╗   ██╗██╗   ██╗████████╗███████╗
  ██╔══██╗██╔════╝██╔════╝╚══███╔╝    ████╗  ██║██║   ██║╚══██╔══╝╚══███╔╝
  ██║  ██║█████╗  █████╗    ███╔╝     ██╔██╗ ██║██║   ██║   ██║     ███╔╝
  ██║  ██║██╔══╝  ██╔══╝   ███╔╝      ██║╚██╗██║██║   ██║   ██║    ███╔╝
  ██████╔╝███████╗███████╗███████╗    ██║ ╚████║╚██████╔╝   ██║   ███████╗
  ╚═════╝ ╚══════╝╚══════╝╚══════╝    ╚═╝  ╚═══╝ ╚═════╝    ╚═╝   ╚══════╝
""" + "\033[0m")
    print("\033[92;1m" + "=" * 70 + "\033[0m")
    print("\033[93m" + "  🥜  " * 10 + "\033[0m")
    print()

    results = run_scan(SQUEEZE_PARAMS)

    if not results:
        print("\nNo squeeze candidates found.")
        print("Try lowering min_short_interest_pct or min_5d_gain_pct in SQUEEZE_PARAMS.")
        return

    df = pd.DataFrame(results)
    display(df)

    out = f"squeeze_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()