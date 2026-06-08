"""
PEAD Options Scanner — Post-Earnings Announcement Drift
Uses Finnhub for earnings data, yfinance for options chains.
No Tastytrade connection needed for scanning.

Setup:
    pip install yfinance requests pandas tabulate python-dotenv

    .env file:
        FINNHUB_API_KEY=your_key   # free at finnhub.io
"""

import os, sys, time, math
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install yfinance requests pandas tabulate python-dotenv")
    sys.exit(1)

load_dotenv()

# ─── BLACK-SCHOLES DELTA ───────────────────────────────────────────────────────

def norm_cdf(x):
    """Standard normal CDF approximation."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_delta(S, K, T, iv):
    """
    Black-Scholes delta for a call option.
    S  = current stock price
    K  = strike price
    T  = time to expiration in years
    iv = implied volatility (as decimal, e.g. 0.30 for 30%)
    r  = risk-free rate (approximate)
    """
    r = 0.05  # approximate risk-free rate
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        return norm_cdf(d1)
    except Exception:
        return None

from config import (
    MIN_EPS_SURPRISE_PCT, MAX_EPS_SURPRISE_PCT, MAX_QUARTER_AGE_DAYS,
    MIN_DELTA, MAX_DELTA, MIN_DTE, MAX_DTE,
    MIN_OPEN_INTEREST, MIN_BID_ASK_RATIO,
    DELTA_WEIGHT, SPREAD_WEIGHT, SURPRISE_WEIGHT,
    UNIVERSE,
)

# ─── FINNHUB CLIENT ────────────────────────────────────────────────────────────

class Finnhub:
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["X-Finnhub-Token"] = key
        self._req = 0

    def get(self, path, params={}):
        self._req += 1
        if self._req % 55 == 0:
            print("  (rate limit pause 65s...)")
            time.sleep(65)
        try:
            r = self.s.get(f"{self.BASE}{path}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def earnings(self, symbol):
        d = self.get("/stock/earnings", {"symbol": symbol, "limit": 4})
        return d if isinstance(d, list) else []

    def quote(self, symbol):
        return self.get("/quote", {"symbol": symbol})


# ─── STEP 1: FIND EARNINGS BEATS ──────────────────────────────────────────────

def find_beats(fh):
    today  = date.today()
    cutoff = today - timedelta(days=MAX_QUARTER_AGE_DAYS)
    candidates = []

    print(f"Scanning {len(UNIVERSE)} symbols for earnings beats...\n")

    for i, symbol in enumerate(UNIVERSE):
        rows = fh.earnings(symbol)
        if not rows:
            continue

        latest       = rows[0]
        actual       = latest.get("actual")
        estimate     = latest.get("estimate")
        surprise_pct = latest.get("surprisePercent")
        period_str   = latest.get("period", "")

        if actual is None or estimate is None or surprise_pct is None:
            continue

        try:
            period_date = datetime.strptime(period_str, "%Y-%m-%d").date()
        except Exception:
            continue

        if period_date < cutoff:
            continue

        # Skip quarters that haven't ended yet (future dates)
        if period_date > today:
            continue

        if not (MIN_EPS_SURPRISE_PCT <= surprise_pct <= MAX_EPS_SURPRISE_PCT):
            continue

        quote = fh.quote(symbol)
        if not quote or not quote.get("c"):
            continue

        price = quote["c"]

        # Get price drift since ~earnings announcement date
        # Companies typically report 3-5 weeks after quarter end
        try:
            approx_report = period_date + timedelta(weeks=4)
            if approx_report >= today:
                approx_report = today - timedelta(days=7)
            start_str = approx_report.isoformat()

            # Fetch stock history and QQQ history over same window
            hist     = yf.Ticker(symbol).history(start=start_str)
            qqq_hist = yf.Ticker("QQQ").history(start=start_str)

            if hist is not None and len(hist) >= 2:
                price_at_report = float(hist["Close"].iloc[0])
                price_now       = float(hist["Close"].iloc[-1])
                drift_pct       = round((price_now - price_at_report) / price_at_report * 100, 1)
            else:
                drift_pct = None

            # Calculate QQQ drift over same window
            if qqq_hist is not None and len(qqq_hist) >= 2:
                qqq_start = float(qqq_hist["Close"].iloc[0])
                qqq_now   = float(qqq_hist["Close"].iloc[-1])
                qqq_drift = round((qqq_now - qqq_start) / qqq_start * 100, 1)
            else:
                qqq_drift = None

        except Exception:
            drift_pct = None
            qqq_drift = None

        # Macro dump check:
        # If stock is down but QQQ is also down by a similar amount,
        # the decline is likely macro-driven, not company-specific.
        # We flag these as "macro dump" candidates instead of excluding them.
        macro_dump = False
        if (drift_pct is not None and drift_pct <= -2 and
                qqq_drift is not None and qqq_drift <= -1):
            # Stock beta approximation: if stock fell <= 2x QQQ drop, it's macro
            # e.g. QQQ -3%, stock -5% = stock moved 1.67x QQQ = likely macro
            if qqq_drift != 0:
                relative_move = drift_pct / qqq_drift
                if relative_move <= 2.5:
                    macro_dump = True

        # Trend indicator
        if drift_pct is None:
            trend = "?"
        elif drift_pct >= 2:
            trend = f"+{drift_pct}% up"
        elif drift_pct <= -2:
            trend = f"{drift_pct}% DOWN"
        else:
            trend = f"{drift_pct}% flat"

        qqq_note = f"  (QQQ {qqq_drift:+.1f}%)" if qqq_drift is not None else ""

        # Exclude stocks drifting down for company-specific reasons
        # But keep macro dump candidates — they may be re-entry opportunities
        if drift_pct is not None and drift_pct <= -2 and not macro_dump:
            print(f"  skip {symbol:6s}  beat: +{surprise_pct:.1f}%  since report: {trend}{qqq_note}  (company-specific decline)")
            continue

        if macro_dump:
            print(f"  MACRO {symbol:6s}  beat: +{surprise_pct:.1f}%  since report: {trend}{qqq_note}  (macro dump — re-entry candidate)")
        else:
            print(f"  ok   {symbol:6s}  beat: +{surprise_pct:.1f}%  since report: {trend}  price: ${price:.2f}")

        candidates.append({
            "symbol":       symbol,
            "quarter_end":  period_str,
            "actual_eps":   round(actual, 2),
            "estimate_eps": round(estimate, 2),
            "surprise_pct": round(surprise_pct, 1),
            "price":        round(price, 2),
            "drift_pct":    drift_pct,
            "qqq_drift":    qqq_drift,
            "macro_dump":   macro_dump,
        })

        time.sleep(0.2)

    print(f"\nFound {len(candidates)} candidates\n")
    return candidates


# ─── STEP 2: FIND CALLS VIA YFINANCE ─────────────────────────────────────────

def score_option(delta, bar, surprise_pct):
    delta_score    = max(0, 1 - abs(delta - 0.40) / 0.15)
    spread_score   = bar
    surprise_score = min(surprise_pct / 50, 1.0)
    return delta_score * DELTA_WEIGHT + spread_score * SPREAD_WEIGHT + surprise_score * SURPRISE_WEIGHT


def find_calls(candidates):
    today   = date.today()
    min_exp = today + timedelta(days=MIN_DTE)
    max_exp = today + timedelta(days=MAX_DTE)
    results = []

    print("Fetching options chains via Yahoo Finance...\n")

    for c in candidates:
        symbol = c["symbol"]
        print(f"  → {symbol}", end="", flush=True)

        try:
            ticker = yf.Ticker(symbol)
            exps   = ticker.options  # tuple of expiration date strings
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
                chain = ticker.option_chain(exp_str)
                calls = chain.calls
            except Exception:
                continue

            T = dte / 365.0  # time to expiry in years
            for _, row in calls.iterrows():
                iv     = row.get("impliedVolatility")
                bid    = row.get("bid")
                ask    = row.get("ask")
                oi     = row.get("openInterest") or 0
                strike = row.get("strike")

                if iv is None or pd.isna(iv) or float(iv) <= 0:
                    continue

                # Calculate delta using Black-Scholes
                delta = bs_delta(c["price"], float(strike), T, float(iv))
                if delta is None:
                    continue

                d = delta
                if not (MIN_DELTA <= d <= MAX_DELTA):
                    continue
                if int(oi) < MIN_OPEN_INTEREST:
                    continue

                bar, mid = 0.0, None
                if bid and ask and float(ask) > 0:
                    bar = float(bid) / float(ask)
                    mid = (float(bid) + float(ask)) / 2

                if bar < MIN_BID_ASK_RATIO:
                    continue

                s = score_option(d, bar, c["surprise_pct"])

                result = {
                    **c,
                    "strike": float(strike),
                    "exp":    exp_str,
                    "dte":    dte,
                    "delta":  round(d, 3),
                    "iv":     round(float(iv) * 100, 1),
                    "oi":     int(oi),
                    "mid":    round(mid, 2) if mid else None,
                    "bar":    round(bar, 2),
                    "score":  round(s, 3),
                }

                if best is None or s > best["score"]:
                    best = result

        if best:
            results.append(best)
            print(f"   ${best['strike']} {best['exp']}  δ{best['delta']}  score {best['score']}")
        else:
            print("  (no options matched filters)")

    return results


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = ["symbol","quarter_end","surprise_pct","drift_pct","qqq_drift","macro_dump","price","strike","exp","dte","delta","iv","oi","mid","bar","score"]
HDRS = ["Symbol","Qtr End","Beat %","Drift %","QQQ %","Macro?","Price","Strike","Expiration","DTE","Delta","IV %","OI","Mid $","B/A","Score"]

def format_drift(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "n/a"
    elif val >= 2:
        return f"+{val:.1f}% ↑"
    elif val <= -2:
        return f"{val:.1f}% ↓"
    else:
        return f"{val:.1f}% →"

def display(df):
    if df.empty:
        print("No candidates found.")
        return
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["macro_dump"] = df["macro_dump"].apply(lambda x: "MACRO DUMP" if x else "")
    df["drift_pct"] = df["drift_pct"].apply(format_drift)
    print("\n" + "═"*110)
    print("  PEAD RESULTS  —  Post-Earnings Drift Call Candidates")
    print("  Strategy: Buy calls after 5%+ EPS beat  |  Target: 2-3 week hold  |  Exit at 50% gain")
    print("═"*110 + "\n")
    print(tabulate(df[COLS].head(30), headers=HDRS, tablefmt="rounded_outline", floatfmt=".2f", showindex=True))
    print(f"""
{'─'*110}
BEFORE TRADING — CHECK EACH CANDIDATE:
  ✓ Beat was real operating earnings, not a one-time tax gain or asset sale
  ✓ Guidance was raised or maintained
  ✓ Stock has not already run 5%+ since the report
  ✓ Avoid entering Friday afternoon — weekend theta erodes premium
  ✓ Exit: 50% gain on premium OR 7 days before expiration — whichever comes first
{'─'*110}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*60)
    print("  PEAD OPTIONS SCANNER  —  Post-Earnings Drift")
    print("═"*60 + "\n")

    fh_key = os.getenv("FINNHUB_API_KEY")
    if not fh_key:
        print("ERROR: Set FINNHUB_API_KEY in .env\n"); sys.exit(1)

    fh = Finnhub(fh_key)

    candidates = find_beats(fh)
    if not candidates:
        print("No beats found. Try lowering MIN_EPS_SURPRISE_PCT in config.py.")
        return

    results = find_calls(candidates)
    if not results:
        print("Beats found but no options matched. Try loosening filters in config.py.")
        return

    df = pd.DataFrame(results)
    display(df)

    out = f"pead_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")

if __name__ == "__main__":
    main()