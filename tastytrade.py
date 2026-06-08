"""
Tastytrade REST API client using OAuth authentication.
"""

import sys
import time
import requests


class Tastytrade:
    API       = "https://api.tastytrade.com"
    TOKEN_URL = "https://api.tastytrade.com/oauth/token"

    def __init__(self, client_secret: str, refresh_token: str):
        self.s = requests.Session()
        self.s.headers["Content-Type"] = "application/json"
        self._authenticate(client_secret, refresh_token)

    def _authenticate(self, client_secret: str, refresh_token: str):
        r = self.s.post(self.TOKEN_URL, json={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_secret": client_secret,
        })
        if r.status_code != 200:
            print(f"Tastytrade OAuth failed ({r.status_code}): {r.text}")
            sys.exit(1)
        access_token = r.json().get("access_token")
        if not access_token:
            print(f"No access token in response: {r.text}")
            sys.exit(1)
        self.s.headers["Authorization"] = f"Bearer {access_token}"
        print("Connected to Tastytrade.")

    def option_chain(self, symbol: str) -> dict | None:
        """Returns first item from nested chain, which contains expirations directly."""
        try:
            r = self.s.get(f"{self.API}/option-chains/{symbol}/nested", timeout=15)
            if r.status_code != 200:
                return None
            data  = r.json().get("data", {})
            items = data.get("items", [])
            return items[0] if items else None
        except Exception:
            return None

    def market_data(self, option_symbols: list[str]) -> dict[str, dict]:
        """
        Fetches market data in small batches to avoid connection resets.
        Retries once on failure.
        """
        if not option_symbols:
            return {}

        result = {}
        batch_size = 50  # small batches to avoid server dropping connection

        for i in range(0, len(option_symbols), batch_size):
            batch = option_symbols[i:i + batch_size]
            data  = self._fetch_market_data_batch(batch)
            result.update(data)
            if i + batch_size < len(option_symbols):
                time.sleep(0.3)  # small pause between batches

        return result

    def _fetch_market_data_batch(self, symbols: list[str], retry: bool = True) -> dict:
        """Fetch one batch, retry once on connection error."""
        params = [("symbols[]", s) for s in symbols]
        try:
            r = self.s.get(
                f"{self.API}/market-data/options",
                params=params,
                timeout=15,
            )
            if r.status_code != 200:
                return {}
            items = r.json().get("data", {}).get("items", [])
            return {i["symbol"]: i for i in items}
        except Exception:
            if retry:
                time.sleep(2)
                return self._fetch_market_data_batch(symbols, retry=False)
            return {}