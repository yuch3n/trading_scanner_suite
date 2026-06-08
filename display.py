"""
Results formatting and display for the PEAD scanner.
"""

import pandas as pd
from tabulate import tabulate

COLS = [
    "symbol", "quarter_end", "surprise_pct", "price",
    "strike", "exp", "dte", "delta", "iv", "oi", "mid", "bar", "score"
]
HDRS = [
    "Symbol", "Qtr End", "Beat %", "Price",
    "Strike", "Expiration", "DTE", "Delta", "IV %", "OI", "Mid $", "B/A", "Score"
]


def show_results(df: pd.DataFrame):
    if df.empty:
        print("No candidates found.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "═" * 110)
    print("  PEAD RESULTS  —  Post-Earnings Drift Call Candidates")
    print("  Strategy: Buy calls after 5%+ EPS beat  |  Target: 2-3 week hold  |  Exit at 50% gain")
    print("═" * 110 + "\n")

    print(tabulate(
        df[COLS].head(30),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    print(f"""
{'─' * 110}
BEFORE TRADING — CHECK EACH CANDIDATE:
  ✓ Beat was real operating earnings, not a one-time tax gain or asset sale
  ✓ Guidance was raised or maintained (not just a backward-looking beat)
  ✓ Stock has not already run 5%+ since the report (drift may be priced in)
  ✓ Avoid entering Friday afternoon — weekend theta erodes premium
  ✓ Exit plan: close at 50% gain on premium OR 7 days before expiration
{'─' * 110}
""")


def save_csv(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False)
    print(f"Results saved to: {path}\n")
