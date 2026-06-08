"""Test scraping Capitol Trades HTML for trade data."""
import requests
from datetime import date, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Try the trades page with buy filter
urls = [
    "https://www.capitoltrades.com/trades?txType=buy&pageSize=96",
    "https://www.capitoltrades.com/trades?txType=P&pageSize=96",
    "https://www.capitoltrades.com/trades",
]

for url in urls:
    r = requests.get(url, headers=HEADERS, timeout=15)
    print(f"URL: {url}")
    print(f"Status: {r.status_code}")
    # Look for trade data patterns in the HTML
    html = r.text
    # Check for ticker symbols, politician names
    import re
    tickers = re.findall(r'"ticker"\s*:\s*"([A-Z]{1,5})"', html)
    politicians = re.findall(r'"politician"\s*:\s*"([^"]+)"', html)
    amounts = re.findall(r'"amount"\s*:\s*"([^"]+)"', html)
    # Also look for next.js data
    next_data = re.findall(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"Tickers found: {tickers[:5]}")
    print(f"Politicians: {politicians[:3]}")
    print(f"Next.js data: {'YES - ' + str(len(next_data[0])) + ' chars' if next_data else 'NO'}")
    if next_data:
        print(f"Preview: {next_data[0][:500]}")
    print()