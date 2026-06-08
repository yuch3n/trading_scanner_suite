"""
Debug - check what yfinance returns for META options
"""
import yfinance as yf
import pandas as pd
from datetime import date, timedelta, datetime

today   = date.today()
min_exp = today + timedelta(days=14)
max_exp = today + timedelta(days=60)

ticker = yf.Ticker("META")
print(f"Available expirations: {ticker.options}\n")

for exp_str in ticker.options:
    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
    if not (min_exp <= exp_date <= max_exp):
        continue
    chain = ticker.option_chain(exp_str)
    calls = chain.calls
    print(f"Expiration: {exp_str}  ({len(calls)} calls)")
    print(f"Columns: {list(calls.columns)}\n")
    print(calls[["strike","bid","ask","impliedVolatility","openInterest","delta"]].head(5) if "delta" in calls.columns else calls[["strike","bid","ask","impliedVolatility","openInterest"]].head(5))
    print()
    break