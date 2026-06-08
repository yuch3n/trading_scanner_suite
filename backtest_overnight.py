"""
Intraday vs Overnight Backtest
--------------------------------
Compares two strategies for a given stock over a decade:
  1. Overnight: buy at close, sell at next open
  2. Intraday:  buy at open, sell at close same day

Run:
    python backtest_overnight.py
"""

import sys
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas numpy")
    sys.exit(1)

# ─── CONFIG ────────────────────────────────────────────────────────────────────

# Accept symbol from command line or prompt user
if len(sys.argv) > 1:
    SYMBOL = sys.argv[1].upper()
else:
    SYMBOL = input("Enter ticker symbol (e.g. MU, NVDA, AAPL): ").strip().upper()
    if not SYMBOL:
        SYMBOL = "MU"

if len(sys.argv) > 2:
    START_DATE = sys.argv[2]
else:
    START_DATE = "2015-01-01"

END_DATE   = datetime.today().strftime("%Y-%m-%d")
CAPITAL    = 10_000

# ─── FETCH DATA ────────────────────────────────────────────────────────────────

print(f"\nFetching {SYMBOL} daily OHLC from {START_DATE} to {END_DATE}...")
df = yf.Ticker(SYMBOL).history(start=START_DATE, end=END_DATE, auto_adjust=True)

if df is None or len(df) < 10:
    print("Not enough data.")
    sys.exit(1)

df = df[["Open", "Close"]].copy()
df.index = df.index.tz_localize(None)
print(f"  {len(df)} trading days loaded\n")

# ─── STRATEGY RETURNS ──────────────────────────────────────────────────────────

# Overnight: buy at close, sell at next open
df["next_open"]       = df["Open"].shift(-1)
df["overnight_ret"]   = (df["next_open"] - df["Close"]) / df["Close"]

# Intraday: buy at open, sell at close same day
df["intraday_ret"]    = (df["Close"] - df["Open"]) / df["Open"]

# Buy and hold: buy at first open, sell at last close
df["bh_ret"]          = df["Close"].pct_change()

df = df.dropna(subset=["next_open", "overnight_ret", "intraday_ret"])

# Cumulative returns
df["cum_overnight"]   = (1 + df["overnight_ret"]).cumprod()
df["cum_intraday"]    = (1 + df["intraday_ret"]).cumprod()
df["cum_bh"]          = (1 + df["bh_ret"]).cumprod()

df["val_overnight"]   = CAPITAL * df["cum_overnight"]
df["val_intraday"]    = CAPITAL * df["cum_intraday"]
df["val_bh"]          = CAPITAL * df["cum_bh"]

# ─── STATS HELPER ──────────────────────────────────────────────────────────────

def calc_stats(rets: pd.Series, cum: pd.Series, label: str) -> dict:
    total_days   = len(rets)
    total_years  = total_days / 252
    total_ret    = (cum.iloc[-1] - 1) * 100
    annual_ret   = ((cum.iloc[-1]) ** (1 / total_years) - 1) * 100
    final_val    = CAPITAL * cum.iloc[-1]
    net_profit   = final_val - CAPITAL
    win_rate     = (rets > 0).sum() / total_days * 100
    avg_ret      = rets.mean() * 100
    avg_win      = rets[rets > 0].mean() * 100 if (rets > 0).any() else 0
    avg_loss     = rets[rets < 0].mean() * 100 if (rets < 0).any() else 0
    rolling_max  = cum.cummax()
    max_dd       = ((cum - rolling_max) / rolling_max).min() * 100
    sharpe       = (rets.mean() / rets.std()) * (252 ** 0.5) if rets.std() > 0 else 0
    best         = rets.max() * 100
    worst        = rets.min() * 100
    best_date    = rets.idxmax().strftime("%Y-%m-%d")
    worst_date   = rets.idxmin().strftime("%Y-%m-%d")
    return {
        "label":        label,
        "total_ret":    total_ret,
        "annual_ret":   annual_ret,
        "final_val":    final_val,
        "net_profit":   net_profit,
        "win_rate":     win_rate,
        "avg_ret":      avg_ret,
        "avg_win":      avg_win,
        "avg_loss":     avg_loss,
        "max_dd":       max_dd,
        "sharpe":       sharpe,
        "best":         best,
        "worst":        worst,
        "best_date":    best_date,
        "worst_date":   worst_date,
        "total_years":  total_years,
        "total_days":   total_days,
        "rets":         rets,
        "cum":          cum,
    }

s_on  = calc_stats(df["overnight_ret"], df["cum_overnight"], "Overnight")
s_id  = calc_stats(df["intraday_ret"],  df["cum_intraday"],  "Intraday")
s_bh  = calc_stats(df["bh_ret"],        df["cum_bh"],        "Buy & Hold")

# ─── DISPLAY ───────────────────────────────────────────────────────────────────

print("=" * 70)
print(f"  BACKTEST RESULTS — {SYMBOL}")
print(f"  Period: {START_DATE} to {END_DATE}  ({s_on['total_years']:.1f} years)")
print("=" * 70)

print(f"""
{'Metric':<28} {'Overnight':>12} {'Intraday':>12} {'Buy & Hold':>12}
{'─'*66}
  {'Starting capital':<26} {'$'+f'{CAPITAL:,.0f}':>12} {'$'+f'{CAPITAL:,.0f}':>12} {'$'+f'{CAPITAL:,.0f}':>12}
  {'Final value':<26} {'$'+f"{s_on['final_val']:,.0f}":>12} {'$'+f"{s_id['final_val']:,.0f}":>12} {'$'+f"{s_bh['final_val']:,.0f}":>12}
  {'Net profit':<26} {'$'+f"{s_on['net_profit']:,.0f}":>12} {'$'+f"{s_id['net_profit']:,.0f}":>12} {'$'+f"{s_bh['net_profit']:,.0f}":>12}
  {'Total return':<26} {s_on['total_ret']:>11.1f}% {s_id['total_ret']:>11.1f}% {s_bh['total_ret']:>11.1f}%
  {'Annualized return':<26} {s_on['annual_ret']:>11.1f}% {s_id['annual_ret']:>11.1f}% {s_bh['annual_ret']:>11.1f}%
{'─'*66}
  {'Win rate':<26} {s_on['win_rate']:>11.1f}% {s_id['win_rate']:>11.1f}% {s_bh['win_rate']:>11.1f}%
  {'Avg daily return':<26} {s_on['avg_ret']:>11.3f}% {s_id['avg_ret']:>11.3f}% {s_bh['avg_ret']:>11.3f}%
  {'Avg win':<26} {s_on['avg_win']:>11.3f}% {s_id['avg_win']:>11.3f}% {s_bh['avg_win']:>11.3f}%
  {'Avg loss':<26} {s_on['avg_loss']:>11.3f}% {s_id['avg_loss']:>11.3f}% {s_bh['avg_loss']:>11.3f}%
  {'Max drawdown':<26} {s_on['max_dd']:>11.1f}% {s_id['max_dd']:>11.1f}% {s_bh['max_dd']:>11.1f}%
  {'Sharpe ratio':<26} {s_on['sharpe']:>12.2f} {s_id['sharpe']:>12.2f} {s_bh['sharpe']:>12.2f}
{'─'*66}
  {'Best single day':<26} {s_on['best']:>+11.2f}% {s_id['best']:>+11.2f}% {s_bh['best']:>+11.2f}%
  {'Worst single day':<26} {s_on['worst']:>+11.2f}% {s_id['worst']:>+11.2f}% {s_bh['worst']:>+11.2f}%
""")

# Year by year
print("YEAR BY YEAR RETURNS")
print(f"{'─'*55}")
print(f"  {'Year':<6} {'Overnight':>10} {'Intraday':>10} {'Buy & Hold':>10}")
print(f"  {'─'*50}")

df["year"] = df.index.year
for year, grp in df.groupby("year"):
    on_yr  = ((1 + grp["overnight_ret"]).prod() - 1) * 100
    id_yr  = ((1 + grp["intraday_ret"]).prod() - 1) * 100
    bh_yr  = ((1 + grp["bh_ret"]).prod() - 1) * 100
    print(f"  {int(year):<6} {on_yr:>+9.1f}%  {id_yr:>+9.1f}%  {bh_yr:>+9.1f}%")

print(f"\n{'─'*55}")
print("""
WHAT THIS TELLS YOU:
  Overnight return = gap between yesterday's close and today's open
  Intraday return  = movement from open to close within the same day

  If overnight >> intraday: most of MU's returns happen while market is closed
    → earnings releases, analyst upgrades, news all hit overnight
    → holding overnight captures institutional repositioning

  If intraday >> overnight: most movement happens during market hours
    → retail and day-trader driven, momentum plays during session

  If buy & hold >> both combined: the two strategies have offsetting losses
    → some days the overnight gain is wiped out intraday or vice versa

NOTE: Does not account for commissions, slippage, or taxes.
""")

# Save
out = f"backtest_{SYMBOL}_comparison.csv"
df[["Open","Close","overnight_ret","intraday_ret","bh_ret",
    "cum_overnight","cum_intraday","cum_bh",
    "val_overnight","val_intraday","val_bh"]].to_csv(out)
print(f"Daily data saved to: {out}\n")