"""
Cash Secured Put (CSP) Scanner
--------------------------------
Finds put contracts worth selling for premium income.
Targets high IV, liquid underlyings in uptrends with
strikes that have a good probability of expiring worthless.

Strategy:
  - Sell OTM puts on stocks you wouldn't mind owning
  - Collect premium upfront
  - Let them expire worthless (ideal) or buy back at 50% profit

Key metrics:
  - IV rank > 50 (premium historically rich)
  - Delta 0.15-0.30 (70-85% probability of expiring worthless)
  - DTE 21-45 (theta decay sweet spot)
  - Annualized return > 15%
  - Stock in uptrend (above 200MA)
  - No earnings within DTE window

Setup:
    pip install yfinance pandas tabulate requests python-dotenv ta

Run:
    python csp_scanner.py
"""

import sys, math, time
from datetime import date, timedelta, datetime

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import ta
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas tabulate ta")
    sys.exit(1)

from config import UNIVERSE

# ─── CONFIG ────────────────────────────────────────────────────────────────────

CSP_PARAMS = {
    # IV rank — how expensive options are relative to history
    # Higher = more premium to collect
    "min_iv_rank":          40,    # minimum IV rank %

    # Delta range — probability of expiring worthless = 1 - abs(delta)
    # 0.20 delta = ~80% probability of expiring worthless
    "min_delta":            0.10,  # lower = further OTM, safer but less premium
    "max_delta":            0.35,  # higher = more premium but more assignment risk

    # DTE range
    "min_dte":              21,
    "max_dte":              45,

    # Minimum premium to bother (per contract = this × 100)
    "min_premium":          0.30,

    # Minimum annualized return on capital
    # (premium / strike) × (365 / dte) × 100
    "min_annual_return":    12.0,  # %

    # Bid/ask quality
    "min_bid_ask_ratio":    0.75,

    # Minimum open interest for liquidity
    "min_open_interest":    100,

    # Stock must be in uptrend
    "require_above_200ma":  True,

    # Skip if earnings within DTE window (IV crush works against you if already short)
    "skip_earnings":        True,

    # Maximum % OTM — don't go so far out the premium is worthless
    "max_otm_pct":          15.0,

    # Minimum % OTM — don't sell too close to the money
    "min_otm_pct":          3.0,
}

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


# ─── IV RANK CALCULATION ───────────────────────────────────────────────────────

def get_iv_rank(symbol: str) -> float | None:
    """
    Calculates IV rank using realized volatility as a proxy.
    IV rank = (current vol - 52w low vol) / (52w high vol - 52w low vol) * 100
    """
    try:
        hist    = yf.Ticker(symbol).history(period="1y")
        if hist is None or len(hist) < 30:
            return None
        returns  = hist["Close"].pct_change().dropna()
        vol_21d  = float(returns.iloc[-21:].std() * (252 ** 0.5) * 100)
        vol_252d = returns.rolling(21).std().dropna() * (252 ** 0.5) * 100
        vol_min  = float(vol_252d.min())
        vol_max  = float(vol_252d.max())
        if vol_max == vol_min:
            return 50.0
        return round((vol_21d - vol_min) / (vol_max - vol_min) * 100, 1)
    except Exception:
        return None


# ─── TREND CHECK ───────────────────────────────────────────────────────────────

def get_trend(symbol: str) -> dict | None:
    """
    Checks if stock is in an uptrend.
    Returns price, 200MA, RSI, and whether above 200MA.
    """
    try:
        hist = yf.Ticker(symbol).history(period="1y")
        if hist is None or len(hist) < 200:
            return None
        close = hist["Close"]
        price = float(close.iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        ma50  = float(close.rolling(50).mean().iloc[-1])
        rsi   = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
        return {
            "price":        round(price, 2),
            "ma200":        round(ma200, 2),
            "ma50":         round(ma50, 2),
            "above_200ma":  price > ma200,
            "above_50ma":   price > ma50,
            "rsi":          round(rsi, 1),
            "pct_above_200": round((price - ma200) / ma200 * 100, 1),
        }
    except Exception:
        return None


# ─── SCAN ONE SYMBOL ───────────────────────────────────────────────────────────

def scan_symbol(symbol: str, params: dict) -> list[dict]:
    today   = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    results = []

    # Earnings check
    if params["skip_earnings"] and has_near_earnings(symbol, params["max_dte"]):
        return []

    # Trend check
    trend = get_trend(symbol)
    if not trend:
        return []
    if params["require_above_200ma"] and not trend["above_200ma"]:
        return []

    # IV rank
    iv_rank = get_iv_rank(symbol)
    if iv_rank is None or iv_rank < params["min_iv_rank"]:
        return []

    price = trend["price"]

    # Fetch options chain
    try:
        ticker = yf.Ticker(symbol)
        exps   = ticker.options
    except Exception:
        return []

    for exp_str in exps:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (min_exp <= exp_date <= max_exp):
            continue

        dte = (exp_date - today).days

        try:
            puts = ticker.option_chain(exp_str).puts
        except Exception:
            continue

        for _, row in puts.iterrows():
            strike = row.get("strike")
            iv     = row.get("impliedVolatility")
            bid    = row.get("bid")
            ask    = row.get("ask")
            oi     = row.get("openInterest") or 0
            volume = row.get("volume") or 0

            if strike is None or iv is None:
                continue
            if pd.isna(iv) or float(iv) <= 0:
                continue

            strike_f = float(strike)
            iv_pct   = float(iv) * 100

            # OTM % — how far below current price
            otm_pct = (price - strike_f) / price * 100
            if not (params["min_otm_pct"] <= otm_pct <= params["max_otm_pct"]):
                continue

            if int(oi) < params["min_open_interest"]:
                continue

            # Bid/ask quality
            bar, mid = 0.0, None
            if bid and ask and float(ask) > 0:
                bar = float(bid) / float(ask)
                mid = (float(bid) + float(ask)) / 2
            if bar < params["min_bid_ask_ratio"]:
                continue
            if mid is None or mid < params["min_premium"]:
                continue

            # Delta approximation using Black-Scholes
            # For puts, delta is negative — we use absolute value
            T = dte / 365.0
            r = 0.05
            try:
                d1    = (math.log(price / strike_f) + (r + 0.5 * (float(iv) ** 2)) * T) / (float(iv) * math.sqrt(T))
                delta = -(1 - (1.0 + math.erf(d1 / math.sqrt(2.0))) / 2.0)
            except Exception:
                continue

            abs_delta = abs(delta)
            if not (params["min_delta"] <= abs_delta <= params["max_delta"]):
                continue

            # Annualized return on capital
            # Capital required = strike × 100 (cash secured)
            # Return = premium / capital × (365/dte)
            capital        = strike_f * 100
            annual_return  = (mid / strike_f) * (365 / dte) * 100
            if annual_return < params["min_annual_return"]:
                continue

            # Probability of expiring worthless = 1 - abs(delta)
            prob_worthless = round((1 - abs_delta) * 100, 1)

            # Score — prioritize high IV rank, good premium, high probability
            iv_score    = min(iv_rank / 100, 1.0)
            prob_score  = prob_worthless / 100
            return_score = min(annual_return / 50, 1.0)  # caps at 50% annualized
            bar_score   = bar

            score = round(
                iv_score     * 0.30 +
                prob_score   * 0.30 +
                return_score * 0.25 +
                bar_score    * 0.15,
                3
            )

            results.append({
                "symbol":         symbol,
                "price":          round(price, 2),
                "strike":         strike_f,
                "otm_pct":        round(otm_pct, 1),
                "exp":            exp_str,
                "dte":            dte,
                "delta":          round(delta, 3),
                "prob_worthless": prob_worthless,
                "iv":             round(iv_pct, 1),
                "iv_rank":        iv_rank,
                "bid":            round(float(bid), 2) if bid else None,
                "ask":            round(float(ask), 2) if ask else None,
                "mid":            round(mid, 2),
                "bar":            round(bar, 2),
                "oi":             int(oi),
                "volume":         int(volume),
                "capital_req":    round(capital),
                "annual_return":  round(annual_return, 1),
                "pct_above_200":  trend["pct_above_200"],
                "rsi":            trend["rsi"],
                "score":          score,
            })

    return results


# ─── MAIN SCAN ─────────────────────────────────────────────────────────────────

def run_scan(params: dict) -> list[dict]:
    all_results = []
    print(f"Scanning {len(UNIVERSE)} symbols for CSP opportunities...\n")

    for symbol in UNIVERSE:
        results = scan_symbol(symbol, params)
        if results:
            best = max(results, key=lambda x: x["score"])
            print(f"  ok  {symbol:6s}  ${best['strike']} put  "
                  f"{best['prob_worthless']:.0f}% prob worthless  "
                  f"{best['annual_return']:.1f}% ann. return  "
                  f"IV rank {best['iv_rank']:.0f}  score {best['score']}")
            all_results.extend(results)
        time.sleep(0.2)

    return all_results


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = [
    "symbol", "price", "strike", "otm_pct", "exp", "dte",
    "delta", "prob_worthless", "iv", "iv_rank",
    "mid", "bar", "oi", "capital_req", "annual_return", "score"
]
HDRS = [
    "Symbol", "Price", "Strike", "OTM %", "Expiration", "DTE",
    "Delta", "Prob OTM %", "IV %", "IV Rank",
    "Mid $", "B/A", "OI", "Capital $", "Ann. Return %", "Score"
]

def display(df: pd.DataFrame):
    if df.empty:
        print("No CSP candidates found.")
        print("Try lowering min_iv_rank or min_annual_return in CSP_PARAMS.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["capital_req"] = df["capital_req"].apply(lambda x: f"${x:,.0f}")

    print("\n" + "="*130)
    print("  CASH SECURED PUT SCANNER  —  Sell Premium, Let Time Work For You")
    print("  Strategy: Sell OTM puts, collect premium, let expire worthless")
    print("="*130 + "\n")

    print(tabulate(
        df[COLS].head(25),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    print(f"""
{'─'*130}
HOW TO READ THIS:

  Strike       — The price you're agreeing to buy the stock at if assigned
  OTM %        — How far below current price your strike is
  Prob OTM %   — Estimated probability the put expires worthless (you keep all premium)
  IV Rank      — How expensive options are vs history. Higher = more premium to collect.
  Mid $        — Premium you collect per share. × 100 = total premium per contract.
  Capital $    — Cash you need to secure the put (strike × 100). This is your max risk.
  Ann. Return  — (premium / strike) × (365/DTE). What this trade yields annualized.

ENTRY CHECKLIST:
  ✓ Only sell puts on stocks you genuinely want to own at the strike price
  ✓ Make sure you have the cash to cover assignment (strike × 100 per contract)
  ✓ Check that IV rank is high — you want to sell premium when options are expensive
  ✓ Confirm no earnings within the DTE window (surprise can blow through your strike)
  ✓ Higher OTM % = safer but less premium. Find your comfort level.

EXIT RULES:
  ✓ Buy back at 50% profit (mid drops from $2.00 to $1.00) — don't be greedy
  ✓ Buy back if stock drops to within 2% of your strike — cut the loss early
  ✓ Never hold through earnings if you entered after the last report
  ✓ If assigned: you now own the stock at your strike price — sell covered calls to recover
{'─'*130}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  CASH SECURED PUT SCANNER")
    print("  Sell puts. Collect premium. Repeat.")
    print("="*60 + "\n")

    results = run_scan(CSP_PARAMS)
    if not results:
        print("\nNo candidates found. Try adjusting CSP_PARAMS.")
        return

    df = pd.DataFrame(results)
    display(df)

    out = f"csp_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()