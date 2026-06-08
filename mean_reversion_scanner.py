"""
Mean Reversion Options Scanner
--------------------------------
Finds oversold stocks in long-term uptrends that are likely to bounce,
then identifies call options to trade the reversion.

Heuristic:
  - RSI(14) < 35           — oversold
  - Price down 10%+ from 20-day high  — meaningful pullback
  - Price above 200-day MA — long-term uptrend intact
  - IV rank < 50           — options not too expensive
  - Declining down-day volume — selling pressure fading

Setup:
    pip install yfinance pandas tabulate python-dotenv ta

Run:
    python mean_reversion_scanner.py
"""

import os, sys, math, requests
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import ta
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install yfinance pandas tabulate ta python-dotenv")
    sys.exit(1)

load_dotenv()

from config import (
    MIN_DELTA, MAX_DELTA, MIN_DTE, MAX_DTE,
    MIN_OPEN_INTEREST, MIN_BID_ASK_RATIO,
    DELTA_WEIGHT, SPREAD_WEIGHT, UNIVERSE,
)

# ─── CONFIG ────────────────────────────────────────────────────────────────────

MR_PARAMS = {
    "max_rsi":              45,    # RSI must be below this (oversold)
    "min_pullback_pct":     5.0,   # must be down this much from 20-day high
    "require_above_200ma":  True,  # must be in long-term uptrend
    "max_iv_rank":          100,   # no hard cutoff — we suggest spreads for high-IV stocks
    "min_dte":              21,    # options DTE range
    "max_dte":              45,
    "exclude_earnings_within_dte": True,   # skip if earnings fall inside the trade window
    "warn_earnings_within_dte":    True,   # if False excludes, if True just warns
    "min_delta":            0.30,
    "max_delta":            0.55,
    "min_open_interest":    50,
    "min_bid_ask_ratio":    0.70,
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


# ─── NEWS FETCH ────────────────────────────────────────────────────────────────

SKIP_PHRASES = {
    "passive income", "retire", "millionaire", "best stocks", "top stocks",
    "buy right now", "should you buy", "magnificent", "i fought the law",
    "market cap", "price target raised", "price target lowered"
}

def get_recent_news(symbol, fh_key=None):
    """
    Fetches recent news via Google News RSS — no API key needed.
    Filters clickbait and returns the 2 most relevant headlines.
    """
    try:
        import xml.etree.ElementTree as ET
        url = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
        r   = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return "could not fetch news"

        root  = ET.fromstring(r.content)
        items = root.findall(".//item")
        good  = []
        for item in items:
            title = item.findtext("title", "")
            tl    = title.lower()
            if any(p in tl for p in SKIP_PHRASES):
                continue
            if title:
                good.append(title)
            if len(good) >= 2:
                break

        return " | ".join(good) if good else "no relevant headlines found"
    except Exception as e:
        return f"(news fetch failed: {e})"

# ─── EARNINGS DATE CHECK ──────────────────────────────────────────────────────

def get_next_earnings(symbol: str, within_days: int) -> dict | None:
    """
    Checks if the stock has earnings coming up within `within_days` days.
    Uses yfinance calendar data.
    Returns dict with date and days_away, or None if no upcoming earnings found.
    """
    try:
        ticker   = yf.Ticker(symbol)
        calendar = ticker.calendar

        if calendar is None:
            return None

        # yfinance returns calendar as a dict with Earnings Date key
        # Handle both old and new yfinance formats
        earnings_date = None

        if isinstance(calendar, dict):
            ed = calendar.get("Earnings Date")
            if ed is not None:
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)
                    earnings_date = ed[0] if ed else None
                else:
                    earnings_date = ed
        elif hasattr(calendar, "loc"):
            # DataFrame format (older yfinance)
            try:
                earnings_date = calendar.loc["Earnings Date"].iloc[0]
            except Exception:
                pass

        if earnings_date is None:
            return None

        # Normalize to date object
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        elif isinstance(earnings_date, str):
            earnings_date = datetime.strptime(earnings_date[:10], "%Y-%m-%d").date()

        today     = date.today()
        days_away = (earnings_date - today).days

        if 0 <= days_away <= within_days:
            return {
                "earnings_date": earnings_date.isoformat(),
                "days_away":     days_away,
            }
        return None
    except Exception:
        return None


# ─── STEP 1: SCAN FOR MEAN REVERSION SETUPS ───────────────────────────────────

def analyze_symbol(symbol: str, params: dict) -> dict | None:
    """
    Downloads 1 year of daily price data and checks all
    mean reversion criteria. Returns signal dict or None.
    """
    try:
        hist = yf.Ticker(symbol).history(period="1y")
    except Exception:
        return None

    if hist is None or len(hist) < 200:
        return None

    close       = hist["Close"]
    high        = hist["High"]   # intraday highs for accurate pullback
    volume      = hist["Volume"]
    today_price = float(close.iloc[-1])

    # ── RSI(14) ───────────────────────────────────────────────────────────────
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi = float(rsi_series.iloc[-1])
    if rsi > params["max_rsi"]:
        return None

    # ── Pullback from 20-day intraday high ────────────────────────────────────
    # Use actual intraday highs, not just closing prices
    high_20d     = float(high.iloc[-20:].max())
    pullback_pct = (high_20d - today_price) / high_20d * 100
    if pullback_pct < params["min_pullback_pct"]:
        return None

    # ── Above 200-day MA ──────────────────────────────────────────────────────
    ma_200 = float(close.rolling(200).mean().iloc[-1])
    if params["require_above_200ma"] and today_price < ma_200:
        return None

    # ── Realized vol rank as IV proxy ─────────────────────────────────────────
    # Lower rank = historically calm = cheaper options
    returns  = close.pct_change().dropna()
    vol_21d  = float(returns.iloc[-21:].std() * (252 ** 0.5) * 100)
    vol_252d = returns.rolling(21).std().dropna() * (252 ** 0.5) * 100
    vol_min  = float(vol_252d.min())
    vol_max  = float(vol_252d.max())
    iv_rank  = ((vol_21d - vol_min) / (vol_max - vol_min) * 100) if vol_max > vol_min else 50
    if iv_rank > params["max_iv_rank"]:
        return None

    # ── Down-day volume fading ────────────────────────────────────────────────
    # Only measure volume on days the stock closed down (actual selling pressure)
    down_days        = hist[close.diff() < 0]
    recent_down_vol  = float(down_days["Volume"].iloc[-5:].mean()) if len(down_days) >= 5 else 0
    prior_down_vol   = float(down_days["Volume"].iloc[-15:-5].mean()) if len(down_days) >= 15 else 0
    vol_fading       = recent_down_vol < prior_down_vol if prior_down_vol > 0 else False

    # ── RSI divergence check ──────────────────────────────────────────────────
    # Bullish divergence: price making lower lows but RSI making higher lows
    price_low_recent = float(close.iloc[-5:].min())
    price_low_prior  = float(close.iloc[-15:-5].min())
    rsi_low_recent   = float(rsi_series.iloc[-5:].min())
    rsi_low_prior    = float(rsi_series.iloc[-15:-5].min())
    rsi_divergence   = (price_low_recent < price_low_prior) and (rsi_low_recent > rsi_low_prior)

    # ── Upcoming earnings check ──────────────────────────────────────────────
    # If earnings fall within our trade window, IV will spike and crush our calls
    upcoming_earnings = get_next_earnings(symbol, params["max_dte"])

    # ── Distance from 200MA ───────────────────────────────────────────────────
    pct_above_200ma = (today_price - ma_200) / ma_200 * 100

    return {
        "symbol":           symbol,
        "price":            round(today_price, 2),
        "rsi":              round(rsi, 1),
        "pullback_pct":     round(pullback_pct, 1),
        "high_20d":         round(high_20d, 2),
        "ma_200":           round(ma_200, 2),
        "pct_above_200ma":  round(pct_above_200ma, 1),
        "iv_rank":          round(iv_rank, 1),
        "vol_fading":       vol_fading,
        "rsi_divergence":   rsi_divergence,
        "earnings_date":    upcoming_earnings["earnings_date"] if upcoming_earnings else None,
        "earnings_days":    upcoming_earnings["days_away"] if upcoming_earnings else None,
        "signal_score":     _signal_score(rsi, pullback_pct, iv_rank, vol_fading, rsi_divergence),
    }


def _signal_score(rsi, pullback_pct, iv_rank, vol_fading, rsi_divergence=False) -> float:
    """
    Composite signal strength score 0-1.
    Lower RSI = stronger oversold signal.
    Bigger pullback = more rubber band tension.
    Lower IV rank = cheaper options.
    Fading volume + RSI divergence = confirmation bonuses.
    """
    max_rsi        = MR_PARAMS["max_rsi"]
    rsi_score      = max(0, (max_rsi - rsi) / max_rsi)  # scales to actual threshold
    pullback_score = min(pullback_pct / 25, 1.0)
    iv_score       = max(0, (50 - iv_rank) / 50)
    vol_score      = 1.0 if vol_fading else 0.0
    div_bonus      = 0.10 if rsi_divergence else 0.0     # divergence is a strong signal
    return round(min(1.0,
        rsi_score * 0.35 +
        pullback_score * 0.25 +
        iv_score * 0.20 +
        vol_score * 0.10 +
        div_bonus +
        0.10  # base score so nothing scores 0 just for passing all filters
    ), 3)


def find_setups(params: dict) -> list[dict]:
    candidates = []
    print(f"Scanning {len(UNIVERSE)} symbols for mean reversion setups...\n")

    near_misses = []
    for symbol in UNIVERSE:
        result = analyze_symbol(symbol, params)
        if result:
            flag     = "✓ vol fading" if result["vol_fading"] else "  vol steady"
            div_flag = " ⚡divergence" if result["rsi_divergence"] else ""

            # Earnings warning / exclusion
            earn_flag = ""
            if result["earnings_date"]:
                days = result["earnings_days"]
                earn_flag = f"  ⚠ earnings in {days}d"
                if params.get("exclude_earnings_within_dte") and not params.get("warn_earnings_within_dte"):
                    print(f"  ✗ {symbol:6s}  skipped — earnings in {days} days (within trade window)")
                    continue

            print(f"  ✓ {symbol:6s}  RSI {result['rsi']:.0f}  pullback -{result['pullback_pct']:.1f}%  IV rank {result['iv_rank']:.0f}  {flag}{div_flag}{earn_flag}")
            news = get_recent_news(symbol)
            result["news"] = news
            candidates.append(result)
        else:
            # Still collect stats for near-miss reporting
            try:
                import yfinance as yf
                import ta as _ta
                hist = yf.Ticker(symbol).history(period="1y")
                if hist is not None and len(hist) >= 200:
                    close = hist["Close"]
                    rsi = float(_ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
                    high_20d = float(close.iloc[-20:].max())
                    pullback = (high_20d - float(close.iloc[-1])) / high_20d * 100
                    near_misses.append((symbol, round(rsi,1), round(pullback,1)))
            except Exception:
                pass

    if not candidates and near_misses:
        near_misses.sort(key=lambda x: x[1])  # sort by RSI ascending
        print("Closest to triggering (lowest RSI):")
        for sym, rsi, pb in near_misses[:10]:
            print(f"    {sym:6s}  RSI {rsi:.0f}  pullback -{pb:.1f}%")

    print(f"\nFound {len(candidates)} mean reversion candidates\n")
    return candidates


# ─── STEP 2: FIND CALL OPTIONS ────────────────────────────────────────────────

def find_calls(candidates: list[dict], params: dict) -> list[dict]:
    today   = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    results = []

    print("Fetching options chains...\n")

    for c in candidates:
        symbol = c["symbol"]
        print(f"  → {symbol}", end="", flush=True)

        try:
            ticker = yf.Ticker(symbol)
            exps   = ticker.options
        except Exception as e:
            print(f"  ✗ ({e})")
            continue

        best = None

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

                delta = bs_delta(c["price"], float(strike), T, float(iv))
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

                # Score: signal score + option quality
                opt_score = (1 - abs(d - 0.40) / 0.15) * 0.5 + bar * 0.5
                total     = round(c["signal_score"] * 0.6 + opt_score * 0.4, 3)

                row_data = {
                    **c,
                    "strike": float(strike),
                    "exp":    exp_str,
                    "dte":    dte,
                    "delta":  round(d, 3),
                    "iv":     round(float(iv) * 100, 1),
                    "oi":     int(oi),
                    "mid":    round(mid, 2) if mid else None,
                    "bar":    round(bar, 2),
                    "score":  total,
                }

                if best is None or total > best["score"]:
                    best = row_data

        if best:
            # For high-IV stocks (rank > 60), find a bull call spread
            if c["iv_rank"] > 60:
                spread = find_spread(ticker, best["exp"], best["strike"],
                                     best["iv"], c["price"], best["dte"], params)
                if spread:
                    best.update(spread)
                    net_debit = round(best["mid"] - spread["short_mid"], 2)
                    max_profit = round((spread["short_strike"] - best["strike"]) - net_debit, 2)
                    best["spread_debit"]     = net_debit
                    best["spread_max_profit"] = max_profit
                    best["spread_rr"]         = round(max_profit / net_debit, 2) if net_debit > 0 else None
                else:
                    best["short_strike"] = None
                    best["spread_debit"] = None
                    best["spread_max_profit"] = None
                    best["spread_rr"] = None
            else:
                best["short_strike"] = None
                best["spread_debit"] = None
                best["spread_max_profit"] = None
                best["spread_rr"] = None

            results.append(best)
            iv_note = f"  ⚠ high IV {c['iv_rank']:.0f} → spread suggested" if c["iv_rank"] > 60 else ""
            print(f"   ${best['strike']} {best['exp']}  δ{best['delta']}  score {best['score']}{iv_note}")
        else:
            print("  (no options matched)")

    return results


# ─── BULL CALL SPREAD FINDER ──────────────────────────────────────────────────

def find_spread(ticker, exp_str, long_strike, long_iv, price, dte, params):
    """
    For high-IV stocks, find a bull call spread:
    Buy the long call (delta ~0.40) and sell a higher strike call (delta ~0.20)
    to reduce cost and IV exposure.
    Returns spread details or None.
    """
    try:
        calls = ticker.option_chain(exp_str).calls
    except Exception:
        return None

    T = dte / 365.0
    short_best = None
    short_best_score = 999

    for _, row in calls.iterrows():
        iv     = row.get("impliedVolatility")
        bid    = row.get("bid")
        ask    = row.get("ask")
        strike = row.get("strike")
        oi     = row.get("openInterest") or 0

        if iv is None or pd.isna(iv) or float(iv) <= 0:
            continue
        if float(strike) <= long_strike:
            continue
        if int(oi) < params["min_open_interest"]:
            continue

        delta = bs_delta(price, float(strike), T, float(iv))
        if delta is None:
            continue

        # Short leg: delta 0.15-0.25 (about 5-15% OTM from long strike)
        if not (0.15 <= delta <= 0.25):
            continue

        bid_val = float(bid) if bid else 0
        mid     = (float(bid) + float(ask)) / 2 if bid and ask and float(ask) > 0 else None
        if mid is None:
            continue

        # Pick short leg closest to delta 0.20
        dist = abs(delta - 0.20)
        if dist < short_best_score:
            short_best_score = dist
            short_best = {
                "short_strike": float(strike),
                "short_mid":    round(mid, 2),
                "short_delta":  round(delta, 3),
                "short_iv":     round(float(iv) * 100, 1),
            }

    return short_best


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = [
    "symbol", "price", "rsi", "pullback_pct", "pct_above_200ma",
    "iv_rank", "earnings_date", "strike", "exp", "dte", "delta", "iv", "oi", "mid", "bar", "score"
]
NEWS_COL = ["symbol", "news"]
HDRS = [
    "Symbol", "Price", "RSI", "Pullback %", "Above 200MA %",
    "IV Rank", "Earnings", "Strike", "Expiration", "DTE", "Delta", "IV %", "OI", "Mid $", "B/A", "Score"
]

def display(df: pd.DataFrame):
    if df.empty:
        print("No candidates found. Try loosening MR_PARAMS filters.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "═"*130)
    print("  MEAN REVERSION RESULTS  —  Oversold Bounce Call Candidates")
    print("  Strategy: Buy calls on oversold stocks in uptrends  |  Target: 3-6 week bounce")
    print("═"*130 + "\n")

    print(tabulate(
        df[COLS].head(20),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    # Show spread suggestions for high-IV candidates
    spread_rows = df[df["short_strike"].notna()]
    if not spread_rows.empty:
        print("\nBull call spread suggestions (for high-IV stocks):\n")
        print("  A spread cuts your cost and IV risk by selling a higher strike call against your long call.")
        print("  Max profit is capped but you need much less movement to be profitable.\n")
        for _, row in spread_rows.iterrows():
            print(f"  {row['symbol']:6s}  Expiration: {row['exp']}  ({row['dte']} DTE)")
            print(f"         Buy  ${row['strike']:.0f} call  @ ${row['mid']:.2f}")
            print(f"         Sell ${row['short_strike']:.0f} call  @ ${row['short_mid']:.2f}")
            print(f"         Net debit: ${row['spread_debit']:.2f}  |  Max profit: ${row['spread_max_profit']:.2f}  |  R/R: {row['spread_rr']:.1f}x")
            print()

    print("\nRecent news (last 30 days):\n")
    for _, row in df[NEWS_COL].head(20).iterrows():
        print(f"  {row['symbol']:6s}: {row['news'][:120]}")
    print()

    print(f"""
{'─'*130}
BEFORE TRADING — CHECK EACH CANDIDATE:
  ✓ Understand WHY the stock pulled back — is it company-specific bad news or just market noise?
     Company-specific bad news (lawsuit, fraud, guidance cut) = skip it, it may not bounce
     Market/sector noise (macro fear, rotation) = good candidate, rubber band effect likely
  ✓ Check for upcoming earnings — avoid if earnings are within your DTE window (IV crush risk)
  ✓ Volume on recent down days should be lower than earlier selling — confirms fading pressure
  ✓ RSI divergence is a bonus — price making lower lows but RSI making higher lows = reversal near
  ✓ Exit: 50% gain on premium OR when RSI recovers above 50 OR 7 days before expiration
{'─'*130}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*60)
    print("  MEAN REVERSION OPTIONS SCANNER")
    print("═"*60 + "\n")

    candidates = find_setups(MR_PARAMS)
    if not candidates:
        print("No setups found. Market may be in a strong trend with few oversold stocks.")
        print("Try raising max_rsi or lowering min_pullback_pct in MR_PARAMS.")
        return

    results = find_calls(candidates, MR_PARAMS)
    if not results:
        print("Setups found but no options matched. Try loosening delta or DTE range.")
        return

    df = pd.DataFrame(results)
    display(df)

    out = f"mr_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()