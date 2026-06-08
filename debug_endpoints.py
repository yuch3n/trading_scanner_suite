"""
Debug - probe Tastytrade API endpoints to find working market data
"""

import os
from dotenv import load_dotenv
load_dotenv()

from tastytrade import Tastytrade

secret  = os.getenv("TT_CLIENT_SECRET")
refresh = os.getenv("TT_REFRESH_TOKEN")
tt = Tastytrade(secret, refresh)

sym = "META  260622C00520000"

endpoints = [
    f"/market-data/options",
    f"/option-chains/META/option-data",
    f"/market-metrics/historic-corporate-events/dividends/META",
    f"/market-data",
]

# Also try fetching a single option directly
single_endpoints = [
    f"/instruments/options/{sym.strip()}",
    f"/instruments/options/{sym.replace(' ', '%20')}",
    f"/quotes",
]

print("Testing endpoints:\n")
for ep in endpoints:
    r = tt.s.get(f"{tt.API}{ep}", timeout=10)
    print(f"  {ep} -> {r.status_code}")

print("\nTesting single option fetch:")
for ep in single_endpoints:
    r = tt.s.get(f"{tt.API}{ep}", timeout=10)
    print(f"  {ep} -> {r.status_code}: {r.text[:100]}")

# Try the streamer symbols instead
print("\nTesting with streamer symbol format:")
streamer_sym = ".META260622C520"
r = tt.s.get(f"{tt.API}/market-data/options", params=[("symbols[]", streamer_sym)], timeout=10)
print(f"  streamer symbol -> {r.status_code}: {r.text[:200]}")