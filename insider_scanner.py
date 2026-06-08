"""
Insider Buying Scanner (insider_scanner.py)
--------------------------------------------
Finds stocks where company executives and directors are buying
shares with their own personal money via SEC Form 4 filings.

Signals:
  - Open market purchases only (transaction code 'P')
  - Transaction value > $50k (meaningful conviction)
  - Multiple insiders buying in same 30-day window (cluster buying)
  - Stock not already run 15%+ since filing (still actionable)

Data: SEC EDGAR (free, official, ~2 day delay)

Setup:
    pip install yfinance pandas tabulate requests python-dotenv

Run:
    python insider_scanner.py
"""

import sys, time, math, re
from datetime import date, timedelta, datetime

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas tabulate requests")
    sys.exit(1)

from config import UNIVERSE, SQUEEZE_UNIVERSE

SCAN_UNIVERSE = list(dict.fromkeys(UNIVERSE + SQUEEZE_UNIVERSE))

# ─── CONFIG ────────────────────────────────────────────────────────────────────

INSIDER_PARAMS = {
    "min_transaction_value":  50_000,
    "lookback_days":          30,
    "min_insider_count":      1,
    "max_price_drift_pct":    15.0,
    "min_dte":                21,
    "max_dte":                60,
    "min_delta":              0.30,
    "max_delta":              0.55,
    "min_open_interest":      50,
    "min_bid_ask_ratio":      0.70,
}

# ─── BLACK-SCHOLES DELTA ───────────────────────────────────────────────────────

def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_delta(S, K, T, iv, r=0.05):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        return norm_cdf(d1)
    except Exception:
        return None

# ─── SEC EDGAR FORM 4 SCRAPER ─────────────────────────────────────────────────

EDGAR_HEADERS = {
    "User-Agent": "personal-trading-scanner contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

def get_insider_filings(symbol: str, lookback_days: int) -> list[dict]:
    """
    Fetches recent Form 4 filings for a symbol from SEC EDGAR.
    Returns list of open-market purchase transactions only.
    Transaction code 'P' = open market purchase (what we want)
    Transaction code 'A' = award/grant (ignore)
    Transaction code 'M' = option exercise (ignore)
    Transaction code 'S' = sale (ignore)
    """
    purchases = []
    today     = date.today()
    cutoff    = today - timedelta(days=lookback_days)

    try:
        # Get list of recent Form 4 filings for this symbol
        r = requests.get(
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={symbol}&type=4&dateb=&owner=include&count=15",
            headers=EDGAR_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return []

        # Parse filing index links from HTML
        filing_links = re.findall(
            r'href="(/Archives/edgar/data/\d+/\d+/\d+-index\.htm)"',
            r.text
        )
        if not filing_links:
            return []

        for link in filing_links[:10]:
            try:
                fr = requests.get(f"https://www.sec.gov{link}", headers=EDGAR_HEADERS, timeout=8)
                if fr.status_code != 200:
                    continue

                # Get filing date
                date_match = re.search(r'Filing Date.*?(\d{4}-\d{2}-\d{2})', fr.text, re.DOTALL)
                if not date_match:
                    continue
                filing_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()

                if filing_date < cutoff:
                    break  # filings are newest first

                # Find XML file
                xml_links = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', fr.text)
                if not xml_links:
                    continue

                xr = requests.get(f"https://www.sec.gov{xml_links[0]}", headers=EDGAR_HEADERS, timeout=8)
                if xr.status_code != 200:
                    continue

                xml = xr.text

                # Insider name and title
                name_match  = re.search(r'<rptOwnerName>(.*?)</rptOwnerName>', xml)
                title_match = re.search(r'<officerTitle>(.*?)</officerTitle>', xml)
                name  = name_match.group(1).strip()  if name_match  else "Unknown"
                title = title_match.group(1).strip() if title_match else "Director/Other"

                # Find all non-derivative transactions
                transactions = re.findall(
                    r'<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>',
                    xml, re.DOTALL
                )

                for txn in transactions:
                    code_match = re.search(r'<transactionCode>(.*?)</transactionCode>', txn)
                    if not code_match or code_match.group(1).strip() != 'P':
                        continue  # open market purchases only

                    shares_match = re.search(
                        r'<transactionShares>.*?<value>([\d.]+)</value>', txn, re.DOTALL
                    )
                    price_match = re.search(
                        r'<transactionPricePerShare>.*?<value>([\d.]+)</value>', txn, re.DOTALL
                    )

                    if not shares_match or not price_match:
                        continue

                    shares = float(shares_match.group(1))
                    price  = float(price_match.group(1))
                    value  = shares * price

                    purchases.append({
                        "filing_date":  filing_date.isoformat(),
                        "insider_name": name,
                        "title":        title,
                        "shares":       int(shares),
                        "price":        round(price, 2),
                        "value":        round(value),
                    })

                time.sleep(0.3)

            except Exception:
                continue

    except Exception:
        return []

    return purchases


# ─── ANALYZE INSIDER ACTIVITY ─────────────────────────────────────────────────

def analyze_insider_activity(symbol: str, params: dict) -> dict | None:
    purchases = get_insider_filings(symbol, params["lookback_days"])
    if not purchases:
        return None

    # Filter by minimum value
    significant = [p for p in purchases if p["value"] >= params["min_transaction_value"]]
    if not significant:
        return None

    unique_insiders = len(set(p["insider_name"] for p in significant))
    if unique_insiders < params["min_insider_count"]:
        return None

    total_value = sum(p["value"] for p in significant)
    avg_price   = sum(p["price"] * p["value"] for p in significant) / total_value
    most_recent = max(significant, key=lambda x: x["filing_date"])
    largest     = max(significant, key=lambda x: x["value"])

    # Current price
    try:
        current_price = yf.Ticker(symbol).fast_info.last_price
        if not current_price or current_price <= 0:
            return None
    except Exception:
        return None

    price_drift = (current_price - avg_price) / avg_price * 100

    if price_drift > params["max_price_drift_pct"]:
        return None

    # Score
    value_score   = min(total_value / 500_000, 1.0)
    cluster_score = min(unique_insiders / 3, 1.0)
    freshness     = max(0, 1 - (
        date.today() - datetime.strptime(most_recent["filing_date"], "%Y-%m-%d").date()
    ).days / params["lookback_days"])
    drift_score   = max(0, 1 - abs(price_drift) / 15)

    score = round(
        value_score   * 0.35 +
        cluster_score * 0.30 +
        freshness     * 0.20 +
        drift_score   * 0.15,
        3
    )

    return {
        "symbol":             symbol,
        "current_price":      round(current_price, 2),
        "avg_buy_price":      round(avg_price, 2),
        "price_drift_pct":    round(price_drift, 1),
        "total_value":        round(total_value),
        "insider_count":      unique_insiders,
        "transaction_count":  len(significant),
        "largest_buyer":      largest["insider_name"],
        "largest_title":      largest["title"],
        "largest_value":      round(largest["value"]),
        "most_recent_date":   most_recent["filing_date"],
        "score":              score,
    }


# ─── FIND OPTIONS ──────────────────────────────────────────────────────────────

def find_calls(symbol: str, price: float, params: dict) -> dict | None:
    today   = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    best    = None

    try:
        ticker = yf.Ticker(symbol)
        exps   = ticker.options
    except Exception:
        return None

    for exp_str in exps:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (min_exp <= exp_date <= max_exp):
            continue
        dte = (exp_date - today).days
        try:
            calls = ticker.option_chain(exp_str).calls
        except Exception:
            continue
        T = dte / 365.0
        for _, row in calls.iterrows():
            iv     = row.get("impliedVolatility")
            bid    = row.get("bid")
            ask    = row.get("ask")
            oi     = row.get("openInterest") or 0
            strike = row.get("strike")
            if iv is None or pd.isna(iv) or float(iv) <= 0:
                continue
            delta = bs_delta(price, float(strike), T, float(iv))
            if delta is None:
                continue
            d = delta
            if not (params["min_delta"] <= d <= params["max_delta"]):
                continue
            if int(oi) < params["min_open_interest"]:
                continue
            bar, mid = 0.0, None
            if bid and ask and float(ask) > 0:
                bar = float(bid) / float(ask)
                mid = (float(bid) + float(ask)) / 2
            if bar < params["min_bid_ask_ratio"]:
                continue
            opt_score = (1 - abs(d - 0.40) / 0.15) * 0.5 + bar * 0.5
            if best is None or opt_score > best["opt_score"]:
                best = {
                    "strike":    float(strike),
                    "exp":       exp_str,
                    "dte":       dte,
                    "delta":     round(d, 3),
                    "iv":        round(float(iv) * 100, 1),
                    "oi":        int(oi),
                    "mid":       round(mid, 2) if mid else None,
                    "bar":       round(bar, 2),
                    "opt_score": round(opt_score, 3),
                }
    return best


# ─── MAIN SCAN ─────────────────────────────────────────────────────────────────

def run_scan(params: dict) -> list[dict]:
    results = []
    print(f"Scanning {len(SCAN_UNIVERSE)} symbols for insider buying...\n")
    print("  Fetching SEC EDGAR Form 4 filings (takes a few minutes)...\n")

    for symbol in SCAN_UNIVERSE:
        activity = analyze_insider_activity(symbol, params)
        if not activity:
            continue

        opt = find_calls(symbol, activity["current_price"], params)

        cluster_flag = " 🔥 CLUSTER" if activity["insider_count"] >= 2 else ""
        print(f"  ✓ {symbol:6s}  {activity['insider_count']} insider(s)  "
              f"${activity['total_value']:,.0f}  "
              f"drift {activity['price_drift_pct']:+.1f}%  "
              f"score {activity['score']}{cluster_flag}")

        result = {**activity}
        if opt:
            result.update({
                "strike": opt["strike"], "exp": opt["exp"],
                "dte":    opt["dte"],    "delta": opt["delta"],
                "iv":     opt["iv"],     "oi":    opt["oi"],
                "mid":    opt["mid"],    "bar":   opt["bar"],
            })
        else:
            result.update({
                "strike": None, "exp": None, "dte": None,
                "delta":  None, "iv":  None, "oi":  None,
                "mid":    None, "bar": None,
            })

        results.append(result)
        time.sleep(0.5)

    return results


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = [
    "symbol", "current_price", "avg_buy_price", "price_drift_pct",
    "insider_count", "total_value", "largest_buyer", "largest_title",
    "most_recent_date", "strike", "exp", "dte", "delta", "iv", "mid", "score"
]
HDRS = [
    "Symbol", "Price", "Avg Buy $", "Drift %",
    "Insiders", "Total $", "Largest Buyer", "Title",
    "Last Filing", "Strike", "Expiration", "DTE", "Delta", "IV %", "Mid $", "Score"
]

def display(df: pd.DataFrame):
    if df.empty:
        print("No insider buying found in the last 30 days.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["total_value"] = df["total_value"].apply(lambda x: f"${x:,.0f}" if x else "")

    print("\n" + "═"*140)
    print("  INSIDER BUYING SCANNER  —  SEC Form 4 Open Market Purchases")
    print("  Strategy: Follow executives buying their own stock with personal money")
    print("═"*140 + "\n")

    print(tabulate(
        df[COLS].head(20),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    print(f"""
{'─'*140}
HOW TO READ THIS:

  Avg Buy $     — Average price insiders paid. Close to current = fresh signal.
  Drift %       — How much stock moved since purchases. < 5% = still early.
  Insiders      — Number of different insiders buying. 2+ = cluster = much stronger signal.
  Total $       — Combined dollar value of all purchases in the window.
  Largest Buyer — The biggest single purchaser by dollar amount.

SIGNAL QUALITY:
  🔥 Cluster buy (2+ insiders)  — strongest signal, multiple people betting independently
  CEO / President buying        — highest conviction, they know the business best
  > $250k single purchase       — meaningful personal bet, not a token buy
  Near 52-week low              — buying at maximum pessimism

RED FLAGS:
  ✗ Option exercise then immediate purchase — may be tax-related, not conviction
  ✗ Very small purchase from a billionaire CEO — token buy, not meaningful
  ✗ Stock already up 10%+ since filing — signal may be priced in
  ✗ Purchase during a 10b5-1 plan — pre-scheduled, not discretionary

BEFORE TRADING:
  ✓ Verify on SEC EDGAR: https://www.sec.gov/cgi-bin/browse-edgar
  ✓ Check transaction type is 'P' (open market purchase), not 'A' or 'M'
  ✓ Read recent news — why are insiders buying now?
  ✓ Exit: hold 3-6 months OR close at 75% gain on premium
{'─'*140}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*60)
    print("  INSIDER BUYING SCANNER  —  SEC Form 4 Filings")
    print("  Data: SEC EDGAR (official, free, ~2 day delay)")
    print("═"*60 + "\n")

    results = run_scan(INSIDER_PARAMS)

    if not results:
        print("\nNo insider buying found.")
        print("This is normal — meaningful insider purchases are rare.")
        print("Try expanding lookback_days in INSIDER_PARAMS.")
        return

    df = pd.DataFrame(results)
    display(df)

    out = f"insider_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()