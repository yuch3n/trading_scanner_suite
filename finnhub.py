"""
Finnhub API client.
Free tier: 60 requests/minute.
Get a key at https://finnhub.io/register
"""

import time
import requests


class Finnhub:
    BASE = "https://finnhub.io/api/v1"
    RATE_LIMIT_PAUSE_EVERY = 55   # pause after this many requests
    RATE_LIMIT_SLEEP       = 65   # seconds to sleep

    def __init__(self, api_key: str):
        self.s = requests.Session()
        self.s.headers["X-Finnhub-Token"] = api_key
        self._req_count = 0

    def _get(self, path: str, params: dict = {}) -> dict | list | None:
        self._req_count += 1
        if self._req_count % self.RATE_LIMIT_PAUSE_EVERY == 0:
            print(f"  (rate limit pause {self.RATE_LIMIT_SLEEP}s...)")
            time.sleep(self.RATE_LIMIT_SLEEP)
        try:
            r = self.s.get(f"{self.BASE}{path}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def earnings(self, symbol: str) -> list[dict]:
        """
        Returns last 4 quarters of earnings with actual EPS, estimate, and surprise %.
        Example entry:
          {'actual': 2.01, 'estimate': 1.99, 'period': '2026-03-31',
           'surprise': 0.02, 'surprisePercent': 1.08, 'symbol': 'AAPL'}
        """
        data = self._get("/stock/earnings", {"symbol": symbol, "limit": 4})
        return data if isinstance(data, list) else []

    def quote(self, symbol: str) -> dict | None:
        """
        Returns current price quote.
        Keys: c (current), pc (prev close), h (high), l (low), o (open)
        """
        return self._get("/quote", {"symbol": symbol})
