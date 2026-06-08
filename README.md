# Trading Scanner Suite

A collection of options trading scanners built in Python. Each scanner identifies a different type of trade setup and suggests specific option contracts to consider. All trades require manual approval — the scanners find candidates, you decide whether to trade them.
The Congressional STOCK module isn't working due to not having access to live data. It's essentially a WIP wrapper for. I find that this is not even useful, as Congressional stock disclosures are more often done months after positions have been made.

---

## Scanners

| Script | Strategy | Best Used When |
|---|---|---|
| `scanner.py` | Post-earnings drift (PEAD) | Earnings season (Jan, Apr, Jul, Oct) |
| `mean_reversion_scanner.py` | Oversold bounce plays | After market selloffs |
| `uoa_scanner.py` | Unusual options activity | Daily, end of day |
| `deez_nutz.py` | Short squeeze setups | Any time |
| `csp_scanner.py` | Cash secured puts | When IV rank is elevated |
| `insider_scanner.py` | SEC Form 4 insider buying | Weekly 

---

## Setup

### 1. Install Python

Download Python 3.10+ from [python.org](https://python.org). During installation check **Add Python to PATH**.

### 2. Install dependencies

```
pip install yfinance pandas tabulate requests python-dotenv ta
```

### 3. Create your .env file

Create a file called `.env` in the project folder with your API credentials:

```
FINNHUB_API_KEY=your_key_here
TT_USERNAME=your_tastytrade_email
TT_PASSWORD=your_tastytrade_password
FMP_API_KEY=your_key_here
```

**Getting API keys (all free tiers available):**
- Finnhub: [finnhub.io/register](https://finnhub.io/register)
- Tastytrade: your existing account credentials
- FMP (for pelosi.py only): [financialmodelingprep.com](https://financialmodelingprep.com)

### 4. Configure your universe

Edit `config.py` to set the stocks you want to scan:

```python
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    # add or remove symbols as needed
]
```

---

## Running the scanners

### GUI launcher (recommended)

```
python launch.py
```

Opens a desktop window with buttons for each scanner. Click a scanner to run it, watch output stream live, and edit config from inside the app.

### Command line

```
python scanner.py                    # PEAD
python mean_reversion_scanner.py     # Mean reversion
python uoa_scanner.py                # Unusual options activity
python deez_nutz.py                  # Short squeeze
python csp_scanner.py                # Cash secured puts
python insider_scanner.py            # Insider buying
python pelosi.py                     # Congressional trades
```

---

## Utilities

### Check why a stock isn't showing up

```
python check_stock.py NVDA
```

Shows RSI, pullback %, IV rank, and which filters the stock fails.

### Backtest overnight vs intraday returns

```
python backtest_overnight.py AAPL
python backtest_overnight.py MU 2018-01-01
```

Compares buying at close vs open vs buy-and-hold over any period.

### OI history database QA

```
python db_qa.py
```

Inspect the SQLite database that tracks daily open interest for all universe symbols. Used by the UOA scanner to detect accumulation.

---

## Scanner details

### PEAD Scanner (`scanner.py`)

Finds stocks that beat EPS estimates and are still drifting upward. Based on the Post-Earnings Announcement Drift effect documented in academic research.

**Entry logic:**
- EPS beat of 5-50% vs analyst estimates
- Stock drifting UP since the report (negative drift excluded)
- If stock is down but QQQ is also down proportionally, flagged as macro dump (re-entry candidate)
- Calls with delta 0.30-0.55, DTE 14-60

**Key columns:**
- `Beat %` — size of EPS surprise
- `Drift %` — price change since ~earnings date
- `QQQ %` — what QQQ did over same window
- `Macro?` — flagged if decline correlates with broad market

---

### Mean Reversion Scanner (`mean_reversion_scanner.py`)

Finds stocks that are oversold relative to their recent history but still in long-term uptrends.

**Entry logic:**
- RSI(14) below 45
- Down 5%+ from 20-day intraday high
- Price above 200-day MA
- Declining sell-side volume (selling pressure fading)
- No earnings within DTE window

**Key columns:**
- `RSI` — below 45 = oversold
- `Pullback %` — how far from 20-day high
- `Above 200MA %` — buffer above long-term trend
- `IV Rank` — options cost indicator
- `Earnings` — upcoming earnings date if within window

For high-IV candidates (rank > 60), the scanner automatically suggests a bull call spread to reduce cost and IV exposure.

---

### Unusual Options Activity (`uoa_scanner.py`)

Detects unusually large options volume on specific strikes — a potential sign of informed institutional positioning.

**Entry logic:**
- Volume/OI ratio > 3x (fresh positioning)
- Dollar value > $100k (institutional size)
- OTM calls only, within 20% of current price
- DTE 14-60
- Earnings skip (14-day window)

**Key columns:**
- `Vol/OI` — today's volume vs existing open interest. 10x+ = very unusual
- `$ Value` — total dollar size of activity
- `Flow` — BUY/SELL/? estimated from bid/ask skew
- `OI Chg` — change in open interest vs yesterday (after first day of DB snapshots)
- `Flags` — scattered (retail noise), already up, ACCUMULATING

**OI History database:**
The scanner automatically snapshots open interest for all universe symbols daily into `oi_history.db`. After 2+ days of data, the `OI Chg` column shows whether positions are being accumulated. Use `db_qa.py` to inspect the database.

---

### Short Squeeze Scanner (`deez_nutz.py`)

Finds heavily shorted stocks that are starting to move up — the early stages of a potential squeeze.

**Entry logic:**
- Short interest > 15% of float (from Finviz)
- Days to cover > 5
- Price up 2%+ in last 5 days
- RSI below 75 (not already overbought)
- Volume on up-days > 1.2x average
- No earnings within 14 days

**Key columns:**
- `Short %` — % of float sold short
- `DTC` — days to cover at average volume
- `5d Gain` — recent price momentum
- `RSI` — momentum indicator, flagged with ⚡ if accelerating

Uses `SQUEEZE_UNIVERSE` from `config.py` — a separate list of higher-risk, higher-short-interest names.

---

### CSP Scanner (`csp_scanner.py`)

Finds put contracts worth selling for premium income. Targets high IV, liquid underlyings in uptrends with strikes likely to expire worthless.

**Entry logic:**
- IV rank > 40 (premium historically rich)
- Delta 0.10-0.35 (70-90% probability of expiring worthless)
- DTE 21-45
- Stock above 200-day MA
- No earnings within DTE window
- Annualized return > 12%

**Key columns:**
- `Prob OTM %` — estimated probability put expires worthless
- `Ann. Return %` — (premium / strike) × (365 / DTE) annualized
- `Capital $` — cash required to secure the put (strike × 100)
- `IV Rank` — higher = more premium available

**Exit rules:**
- Buy back at 50% profit
- Buy back if stock drops within 2% of strike
- Never hold through earnings

---

### Insider Buying Scanner (`insider_scanner.py`)

Scrapes SEC EDGAR Form 4 filings for open market purchases by company executives and directors.

**Entry logic:**
- Transaction code P only (open market purchase — not grants or option exercises)
- Transaction value > $50k
- Filed within last 30 days
- Stock not already up 15%+ since purchase

**Key columns:**
- `Avg Buy $` — average price insiders paid
- `Drift %` — how much stock moved since purchases
- `Insiders` — number of different insiders buying (2+ = cluster buy)
- `Largest Buyer` — biggest single purchaser

Note: SEC EDGAR has approximately 2-day filing delay.

---

### Congressional Trade Scanner (`pelosi.py`)

Tracks stock purchases by members of Congress disclosed under the STOCK Act. Requires a free FMP API key.

**Entry logic:**
- Purchase transactions only
- Filed within last 60 days
- Stock not already up 15%+ since purchase
- High-profile members (committee chairs, leadership) flagged as VIP

**Key columns:**
- `# Members` — how many Congress members bought this stock
- `VIP` — high-profile buyer with committee information advantage
- `Party` — political party of buyer(s)
- `Total $` — estimated combined value (Congress reports ranges, not exact amounts)

Note: Up to 45-day filing delay allowed under STOCK Act.

---

## Configuration

All strategy parameters are in `config.py` for PEAD, mean reversion, and UOA scanners.

CSP parameters are in `csp_scanner.py` in the `CSP_PARAMS` dict.
Squeeze parameters are in `deez_nutz.py` in the `SQUEEZE_PARAMS` dict.

**Common adjustments:**

```python
# Loosen PEAD to find more candidates
MIN_EPS_SURPRISE_PCT = 3.0   # default 5.0

# Tighter options filters
MIN_DELTA = 0.35             # default 0.30
MIN_BID_ASK_RATIO = 0.80     # default 0.70

# Expand universe
UNIVERSE = ["AAPL", "MSFT", ...]
```

---

## Important disclaimers

- These scanners are research tools, not financial advice
- All trades require manual review and approval before execution
- Past backtested performance does not guarantee future results
- Options trading involves significant risk including total loss of premium
- Always verify scanner output independently before trading
- Never risk more than you can afford to lose

---

## File structure

```
trading_scanner_suite/
├── launch.py                    # GUI launcher
├── config.py                    # Universe and shared parameters
├── scanner.py                   # PEAD scanner
├── mean_reversion_scanner.py    # Mean reversion scanner
├── uoa_scanner.py               # Unusual options activity
├── deez_nutz.py                 # Short squeeze scanner
├── csp_scanner.py               # Cash secured puts
├── insider_scanner.py           # SEC insider buying
├── pelosi.py                    # Congressional trades
├── backtest_overnight.py        # Overnight vs intraday backtest
├── check_stock.py               # Single stock diagnostic
├── db_qa.py                     # OI history database inspector
├── finnhub.py                   # Finnhub API client
├── tastytrade.py                # Tastytrade API client
├── display.py                   # Shared display utilities
├── requirements.txt             # Python dependencies
├── .env                         # API keys (never commit this)
├── .gitignore                   # Git exclusions
└── oi_history.db                # OI database (auto-created, never commit)
```
