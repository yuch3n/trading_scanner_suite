"""
Debug - test different symbol formats against Tastytrade market data API
"""

import os, sys
from dotenv import load_dotenv
load_dotenv()

from tastytrade import Tastytrade

secret  = os.getenv("TT_CLIENT_SECRET")
refresh = os.getenv("TT_REFRESH_TOKEN")
tt = Tastytrade(secret, refresh)

# The raw symbol from chain: 'META  260622C00520000'
# Try different formats
raw    = 'META  260622C00520000'
clean1 = raw.strip()                          # 'META  260622C00520000'
clean2 = raw.replace('  ', ' ')              # 'META 260622C00520000'  
clean3 = raw.replace(' ', '')                 # 'META260622C00520000'

formats = {
    "raw":     raw,
    "strip":   clean1,
    "1space":  clean2,
    "nospace": clean3,
}

for name, sym in formats.items():
    params = [("symbols[]", sym)]
    r = tt.s.get(f"{tt.API}/market-data/options", params=params, timeout=15)
    print(f"{name:10s} | '{sym}' | status {r.status_code} | items: {len(r.json().get('data', {}).get('items', []))}")
    if r.json().get('data', {}).get('items'):
        print(f"  Sample: {r.json()['data']['items'][0]}")