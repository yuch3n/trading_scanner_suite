"""
Unusual Options Activity (UOA) Scanner
----------------------------------------
Detects when large, concentrated options bets appear on a stock —
a sign that institutional or informed money is positioning for a move.

Signals we look for:
  - Volume/OI ratio > 3x       — fresh positioning, not just existing trades rolling
  - OTM calls                  — directional bet, not a hedge
  - Single expiration focus     — conviction on a specific date
  - Dollar value > $100k        — filters out retail noise
  - No earnings within 5 days  — avoids pre-earnings hedging being mistaken for signal

Data: yfinance (free, no API key needed)

Setup:
    pip install yfinance pandas tabulate python-dotenv ta

Run:
    python uoa_scanner.py
"""

import sys, math, time, sqlite3
from datetime import date, timedelta, datetime
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas tabulate")
    sys.exit(1)

from config import UNIVERSE

# ─── DATABASE ──────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "oi_history.db"

def init_db() -> sqlite3.Connection:
    """
    Initialize SQLite database for OI history.
    Creates table if it doesn't exist.

    Schema:
        symbol     — ticker symbol
        strike     — option strike price
        expiration — option expiration date
        opt_type   — 'call' or 'put'
        date       — snapshot date (today)
        open_interest — OI as of this date
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            symbol       TEXT,
            strike       REAL,
            expiration   TEXT,
            opt_type     TEXT,
            date         TEXT,
            open_interest INTEGER,
            PRIMARY KEY (symbol, strike, expiration, opt_type, date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_symbol_date
        ON oi_history (symbol, date)
    """)
    conn.commit()
    return conn


def snapshot_oi(conn: sqlite3.Connection, universe: list[str]):
    """
    For every symbol in universe, fetch the current options chain
    and save today's OI for all strikes/expirations to the database.
    Runs once per day — skips symbols already snapshotted today.
    """
    today     = date.today().isoformat()
    today_min = today
    today_max = (date.today() + timedelta(days=60)).isoformat()

    # Which symbols already have a complete snapshot today?
    # We track completion separately so partial snapshots get re-run cleanly
    cur = conn.execute(
        "SELECT symbol FROM oi_history WHERE date = ? GROUP BY symbol", (today,)
    )
    done_today = set(r[0] for r in cur.fetchall())
    remaining  = [s for s in universe if s not in done_today]

    if not remaining:
        print(f"  OI snapshot already complete for today ({today}). Skipping.\n")
        return

    print(f"  Snapshotting OI for {len(remaining)} symbols "
          f"({len(done_today)} already done today)...\n")

    rows = []
    for i, symbol in enumerate(remaining):

        try:
            ticker = yf.Ticker(symbol)
            exps   = ticker.options
        except Exception:
            continue

        for exp_str in exps:
            # Only store expirations within next 60 days
            if exp_str > today_max:
                continue
            try:
                chain = ticker.option_chain(exp_str)
                for opt_type, df in [("call", chain.calls), ("put", chain.puts)]:
                    for _, row in df.iterrows():
                        strike = row.get("strike")
                        oi     = row.get("openInterest") or 0
                        if strike and oi > 0:
                            rows.append((
                                symbol, float(strike), exp_str,
                                opt_type, today, int(oi)
                            ))
            except Exception:
                continue

        if (i + 1) % 10 == 0:
            # Batch insert every 10 symbols
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO oi_history VALUES (?,?,?,?,?,?)", rows
                )
                conn.commit()
                rows = []
            print(f"    {i+1}/{len(remaining)} symbols snapshotted...")
        time.sleep(0.2)

    # Insert remaining rows
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO oi_history VALUES (?,?,?,?,?,?)", rows
        )
        conn.commit()

    print(f"  OI snapshot complete.\n")


def get_oi_change(conn: sqlite3.Connection, symbol: str,
                  strike: float, expiration: str, opt_type: str) -> dict | None:
    """
    Returns OI change for a specific option vs yesterday and 7 days ago.
    Used to detect accumulation (gradually building positions).
    """
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    week_ago  = (date.today() - timedelta(days=7)).isoformat()

    cur = conn.execute("""
        SELECT date, open_interest FROM oi_history
        WHERE symbol=? AND strike=? AND expiration=? AND opt_type=?
        AND date >= ?
        ORDER BY date DESC
        LIMIT 10
    """, (symbol, strike, expiration, opt_type, week_ago))

    rows = cur.fetchall()
    if not rows:
        return None

    by_date = {r[0]: r[1] for r in rows}
    oi_today     = by_date.get(today)
    oi_yesterday = by_date.get(yesterday)

    # Find closest available prior date
    sorted_dates = sorted(by_date.keys(), reverse=True)
    prior_dates  = [d for d in sorted_dates if d < today]
    oi_prior     = by_date.get(prior_dates[0]) if prior_dates else None
    prior_date   = prior_dates[0] if prior_dates else None

    oi_week = by_date.get(week_ago) or (by_date.get(sorted_dates[-1]) if sorted_dates else None)

    if oi_today is None or oi_prior is None:
        return None

    day_change  = oi_today - oi_prior
    day_chg_pct = round(day_change / oi_prior * 100, 1) if oi_prior > 0 else 0

    week_change  = (oi_today - oi_week) if oi_week else None
    week_chg_pct = round(week_change / oi_week * 100, 1) if oi_week and oi_week > 0 else None

    # Detect accumulation: OI growing steadily over multiple days
    oi_values   = [by_date[d] for d in sorted(by_date.keys())]
    accumulating = (len(oi_values) >= 3 and
                    all(oi_values[i] <= oi_values[i+1] for i in range(len(oi_values)-1)))

    return {
        "oi_today":      oi_today,
        "oi_prior":      oi_prior,
        "prior_date":    prior_date,
        "day_change":    day_change,
        "day_chg_pct":   day_chg_pct,
        "week_change":   week_change,
        "week_chg_pct":  week_chg_pct,
        "accumulating":  accumulating,
        "days_tracked":  len(rows),
    }

# ─── CONFIG ────────────────────────────────────────────────────────────────────

UOA_PARAMS = {
    # Volume/OI ratio — how many times today's volume exceeds open interest
    # > 3x means significant fresh positioning
    "min_vol_oi_ratio":      3.0,

    # Minimum dollar value of the unusual activity
    # volume * mid_price * 100 (each contract = 100 shares)
    "min_dollar_value":      100_000,

    # Only look at OTM calls (strike > current price)
    # ITM calls could just be stock replacement / hedging
    "otm_only":              True,

    # Max % OTM — don't flag lottery tickets 50% out of the money
    "max_otm_pct":           20.0,

    # Minimum open interest (some baseline liquidity)
    "min_open_interest":     50,

    # Expiration window — ignore very short (gambling) or very long (LEAPS)
    "min_dte":               14,   # raised from 7 — sub-2-week = gambling territory
    "max_dte":               60,

    # Avoid pre-earnings positioning (not a real signal)
    "skip_near_earnings_days": 14,  # raised from 5 — covers most reporting windows
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


# ─── SCAN ONE SYMBOL ───────────────────────────────────────────────────────────

def safe_int(v) -> int:
    try:
        import math
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0
        return int(v)
    except Exception:
        return 0

def safe_float(v, decimals=2) -> float:
    try:
        import math
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0
        return round(float(v), decimals)
    except Exception:
        return 0.0

def scan_symbol(symbol: str, params: dict, conn=None) -> list[dict]:
    today = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    flags = []

    try:
        ticker     = yf.Ticker(symbol)
        price      = ticker.fast_info.last_price
        prev_close = ticker.fast_info.previous_close
        if not price or price <= 0:
            return []
        today_chg_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

        exps = ticker.options
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
            chain = ticker.option_chain(exp_str)
            calls = chain.calls
        except Exception:
            continue

        for _, row in calls.iterrows():
            strike = row.get("strike")
            volume = row.get("volume") or 0
            oi     = row.get("openInterest") or 0
            bid    = row.get("bid")
            ask    = row.get("ask")
            iv     = row.get("impliedVolatility")

            if not strike or volume < 1 or oi < params["min_open_interest"]:
                continue

            # OTM filter
            otm_pct = (float(strike) - price) / price * 100
            if params["otm_only"] and otm_pct <= 0:
                continue
            if otm_pct > params["max_otm_pct"]:
                continue
            if otm_pct < -1:  # slightly ITM is ok, very ITM is not
                continue

            # Volume/OI ratio
            vol_oi = float(volume) / float(oi) if oi > 0 else 0
            if vol_oi < params["min_vol_oi_ratio"]:
                continue

            # Dollar value
            mid = (float(bid) + float(ask)) / 2 if bid and ask and float(ask) > 0 else None
            if mid is None:
                continue
            dollar_value = volume * mid * 100
            if dollar_value < params["min_dollar_value"]:
                continue

            # Bar quality
            bar = float(bid) / float(ask) if ask and float(ask) > 0 else 0

            # Flow bias — bid/ask skew
            # If mid price is closer to ask, more likely buyer-driven
            # If mid price is closer to bid, more likely seller-driven
            bid_val = float(bid) if bid else 0
            ask_val = float(ask) if ask and float(ask) > 0 else 0
            mid_val = (bid_val + ask_val) / 2 if ask_val > 0 else 0
            spread  = ask_val - bid_val
            if spread > 0 and mid_val > 0:
                bid_ask_skew = (mid_val - bid_val) / spread
            else:
                bid_ask_skew = 0.5

            if bid_ask_skew >= 0.65:
                flow_bias = "BUY"
            elif bid_ask_skew <= 0.35:
                flow_bias = "SELL"
            else:
                flow_bias = "?"

            # Score — higher vol/OI and dollar value = stronger signal
            vol_oi_score  = min(vol_oi / 20, 1.0)         # caps at 20x
            dollar_score  = min(dollar_value / 1_000_000, 1.0)  # caps at $1M
            otm_score     = max(0, 1 - otm_pct / 20)      # prefer closer to ATM
            # DTE quality: penalize very short DTE (< 14 days = gambling)
            dte_score     = min((dte - 14) / 30, 1.0) if dte >= 14 else 0.0
            # Penalize if stock already up 3%+ today (move may be done)
            chg_penalty   = max(0, 1 - today_chg_pct / 5) if today_chg_pct > 0 else 1.0
            score = round((
                vol_oi_score * 0.40 +
                dollar_score * 0.30 +
                otm_score    * 0.15 +
                dte_score    * 0.15
            ) * chg_penalty * (1.0 if flow_bias == "BUY" else 0.75 if flow_bias == "?" else 0.5), 3)

            flags.append({
                "symbol":        symbol,
                "price":         safe_float(price),
                "today_chg_pct": safe_float(today_chg_pct, 1),
                "flow_bias":     flow_bias,
                "bid_ask_skew":  safe_float(bid_ask_skew),
                "strike":        safe_float(strike),
                "exp":           exp_str,
                "dte":           dte,
                "otm_pct":       safe_float(otm_pct, 1),
                "volume":        safe_int(volume),
                "open_interest": safe_int(oi),
                "vol_oi_ratio":  safe_float(vol_oi, 1),
                "mid":           safe_float(mid),
                "dollar_value":  safe_int(dollar_value),
                "iv":            safe_float(float(iv) * 100, 1) if iv is not None else None,
                "bar":           safe_float(bar),
                "score":         safe_float(score, 3),
            })

    return flags


# ─── MAIN SCAN ─────────────────────────────────────────────────────────────────

def run_scan(params: dict) -> list[dict]:
    all_flags = []

    # Initialize database and snapshot today's OI
    print("Initializing OI history database...")
    conn = init_db()
    print("Snapshotting OI data for universe...")
    snapshot_oi(conn, UNIVERSE)

    print(f"Scanning {len(UNIVERSE)} symbols for unusual options activity...\n")

    for i, symbol in enumerate(UNIVERSE):
        # Skip if earnings are coming up (pre-earnings hedging = false signal)
        if has_near_earnings(symbol, params["skip_near_earnings_days"]):
            print(f"  skip {symbol:6s}  (earnings within {params['skip_near_earnings_days']} days)")
            continue

        flags = scan_symbol(symbol, params, conn)
        if flags:
            best = max(flags, key=lambda x: x["score"])
            print(f"  🔥 {symbol:6s}  ${best['strike']} call  vol/OI {best['vol_oi_ratio']:.1f}x  "
                  f"${best['dollar_value']:,.0f}  +{best['otm_pct']:.1f}% OTM  exp {best['exp']}")
            all_flags.extend(flags)

        time.sleep(0.15)

    # Flag symbols with scattered activity (5+ strikes = likely retail, not institutional)
    from collections import Counter
    sym_counts = Counter(f["symbol"] for f in all_flags)
    for f in all_flags:
        f["strike_count"] = sym_counts[f["symbol"]]
        flags_list = []
        # Penalize scattered activity
        if f["strike_count"] >= 5:
            f["score"] = round(f["score"] * 0.7, 3)
            flags_list.append("scattered")
        if f["today_chg_pct"] >= 3:
            f["score"] = round(f["score"] * 0.8, 3)
            flags_list.append("already up")
        # Boost accumulation signal
        if f.get("accumulating"):
            f["score"] = round(min(1.0, f["score"] * 1.15), 3)
            flags_list.append("ACCUMULATING")
        if f.get("oi_day_chg") and f["oi_day_chg"] > 0:
            flags_list.append(f"OI+{f['oi_day_chg_pct']:.0f}%")
        f["flags"] = " | ".join(flags_list)

    return all_flags


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS_BASE = [
    "symbol", "price", "today_chg_pct", "flow_bias", "strike", "otm_pct", "exp", "dte",
    "volume", "open_interest", "vol_oi_ratio",
    "dollar_value", "mid", "iv", "bar", "score", "flags"
]
HDRS_BASE = [
    "Symbol", "Price", "Today %", "Flow", "Strike", "OTM %", "Expiration", "DTE",
    "Volume", "OI", "Vol/OI",
    "$ Value", "Mid $", "IV %", "B/A", "Score", "Flags"
]
COLS_OI = COLS_BASE[:11] + ["oi_day_chg", "oi_day_chg_pct"] + COLS_BASE[11:]
HDRS_OI = HDRS_BASE[:11] + ["OI Chg", "OI Chg %"] + HDRS_BASE[11:]

def display(df: pd.DataFrame):
    if df.empty:
        print("No unusual activity found today.")
        print("Either the market is quiet or try lowering min_vol_oi_ratio or min_dollar_value.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    # Format dollar value
    df["dollar_value"] = df["dollar_value"].apply(lambda x: f"${x:,.0f}")

    print("\n" + "═"*120)
    print("  UNUSUAL OPTIONS ACTIVITY  —  Large Directional Call Bets")
    print("  Strategy: Follow institutional positioning  |  Enter same day or next morning")
    print("═"*120 + "\n")

    # Use OI change columns only if they exist in the dataframe
    if "oi_day_chg" in df.columns and df["oi_day_chg"].notna().any():
        cols, hdrs = COLS_OI, HDRS_OI
    else:
        cols, hdrs = COLS_BASE, HDRS_BASE

    print(tabulate(
        df[cols].head(25),
        headers=hdrs,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    print(f"""
{'─'*120}
HOW TO USE UOA:

  Flow column explained:
    BUY  = mid price closer to ask — likely buyer initiated (bullish signal)
    SELL = mid price closer to bid — likely seller initiated (treat with caution)
    ?    = ambiguous, mid price near the midpoint

  High conviction signals:
    ✓ Flow = BUY — trade executed closer to ask, consistent with buying
    ✓ Vol/OI ratio > 10x — very unusual, someone is making a big fresh bet
    ✓ Dollar value > $500k — institutional size, not retail noise
    ✓ Single strike/expiration concentration — conviction, not a spread
    ✓ OTM 2-8% — not a hedge (too cheap), not a lottery (too far out)

  Red flags to watch for:
    ✗ Activity on the same day as an M&A rumor or news item — already priced in
    ✗ Very short DTE (< 7 days) — could be a weekly gamble, not informed flow
    ✗ Stock already up 3%+ today — chasing, the move may be done
    ✗ Low IV — the option may already be pricing a known event

  Entry approach:
    — Enter the same call that showed unusual activity (or next morning at open)
    — Size small — UOA is a leading indicator, not a guarantee
    — Exit if stock doesn't move within 3-5 days (the signal may have been wrong)
    — Take 50-75% profit if it moves quickly in your direction
{'─'*120}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*60)
    print("  UNUSUAL OPTIONS ACTIVITY SCANNER")
    print("═"*60 + "\n")

    flags   = run_scan(UOA_PARAMS)

    if not flags:
        print("\nNo unusual activity detected across universe.")
        return

    df = pd.DataFrame(flags)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    display(df)

    out = f"uoa_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()