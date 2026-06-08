# PEAD Options Scanner

A Post-Earnings Announcement Drift (PEAD) options scanner that:
1. Fetches earnings surprise data from Finnhub
2. Filters for stocks that beat EPS by 5-50%
3. Pulls options chains from Tastytrade REST API
4. Ranks call options by a scoring heuristic (delta, spread quality, surprise size)

## Project structure

- `scanner.py` — main scanner script (run this)
- `config.py` — all tunable parameters in one place
- `finnhub.py` — Finnhub API client
- `tastytrade.py` — Tastytrade REST API client
- `display.py` — results formatting and output
- `.env` — credentials (never commit this)
- `requirements.txt` — dependencies

## Running the scanner

```bash
python scanner.py
```

## Credentials

Set in `.env`:
```
FINNHUB_API_KEY=your_key
TT_USERNAME=your_tastytrade_email
TT_PASSWORD=your_tastytrade_password
```

## Common tasks for Claude Code

- "Add a put scanner for bearish PEAD setups"
- "Add email alerts when a high-score candidate is found"
- "Backtest the strategy on historical data"
- "Add a filter for minimum average daily volume"
- "Save results to a SQLite database instead of CSV"
- "Schedule the scanner to run every morning at 9am"
- "Add a minimum market cap filter"
- "Plot the results in a chart"
