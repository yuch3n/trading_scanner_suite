"""
Debug script - shows raw option chain structure from Tastytrade.
Run with: python debug_chain.py
"""

import os, sys
from dotenv import load_dotenv
load_dotenv()

from tastytrade import Tastytrade

secret  = os.getenv("TT_CLIENT_SECRET")
refresh = os.getenv("TT_REFRESH_TOKEN")

if not secret or not refresh:
    print("ERROR: Set TT_CLIENT_SECRET and TT_REFRESH_TOKEN in .env")
    sys.exit(1)

tt = Tastytrade(secret, refresh)

print("\nFetching META option chain...\n")
chain = tt.option_chain("META")

if not chain:
    print("No chain returned - check OAuth scopes")
    sys.exit(1)

print(f"Top-level keys: {list(chain.keys())}")
print(f"Number of expirations: {len(chain.get('expirations', []))}")

exps = chain.get("expirations", [])
if exps:
    first = exps[0]
    print(f"\nFirst expiration keys: {list(first.keys())}")
    print(f"First expiration data: {first}")
    strikes = first.get("strikes", [])
    if strikes:
        print(f"\nFirst strike keys: {list(strikes[0].keys())}")
        print(f"First strike data: {strikes[0]}")
else:
    print("\nRaw chain data:")
    import json
    print(json.dumps(chain, indent=2)[:2000])