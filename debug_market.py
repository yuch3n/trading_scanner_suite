"""
Debug script - shows raw market data for a few META options.
Run with: python debug_market.py
"""

import os, sys
from datetime import date, timedelta, datetime
from dotenv import load_dotenv
load_dotenv()

from tastytrade import Tastytrade

secret  = os.getenv("TT_CLIENT_SECRET")
refresh = os.getenv("TT_REFRESH_TOKEN")
tt = Tastytrade(secret, refresh)

# Get META chain
chain = tt.option_chain("META")
today   = date.today()
min_exp = today + timedelta(days=14)
max_exp = today + timedelta(days=60)

# Collect a few call symbols in range
sample_syms = []
for exp_block in chain.get("expirations", []):
    exp_str = exp_block.get("expiration-date", "")
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
    except:
        continue
    if not (min_exp <= exp_date <= max_exp):
        continue
    for strike in exp_block.get("strikes", [])[:3]:
        call = strike.get("call")
        if call:
            sample_syms.append(call.strip())
    if len(sample_syms) >= 6:
        break

print(f"Sample option symbols: {sample_syms}\n")

mkt = tt.market_data(sample_syms)
print(f"Market data returned {len(mkt)} entries\n")
for sym, data in list(mkt.items())[:3]:
    print(f"{sym}:")
    print(f"  {data}\n")