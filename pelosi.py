"""
pelosi.py — Congressional Stock Trade Scanner
------------------------------------------------
Tracks stock purchases made by members of Congress disclosed
under the STOCK Act. Finds recent buys still worth trading.

"If you can't beat 'em, join 'em."

Data source: Financial Modeling Prep (FMP) — free tier, 250 calls/day
Sign up free at: https://financialmodelingprep.com

Add to your .env:
    FMP_API_KEY=your_key_here

Run:
    python pelosi.py
"""

import sys, os, time, math
from datetime import date, timedelta, datetime

try:
    import yfinance as yf
    import pandas as pd
    from tabulate import tabulate
    import requests
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance pandas tabulate requests python-dotenv")
    sys.exit(1)

load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────────────────────

PELOSI_PARAMS = {
    "lookback_days":         60,    # how far back to look for trades
    "max_price_drift_pct":   15.0,  # skip if stock already ran this much
    "min_amount":            15_000, # minimum estimated trade value
    "min_dte":               21,
    "max_dte":               60,
    "min_delta":             0.30,
    "max_delta":             0.55,
    "min_open_interest":     50,
    "min_bid_ask_ratio":     0.70,
}

# Members with committee positions giving them informational advantage
HIGH_PROFILE = {
    "Pelosi", "Johnson", "Jeffries", "Scalise", "Thune", "Schumer",
    "Tuberville", "Reed", "Wicker", "Cotton", "Warner", "Rubio",
    "Wyden", "Crapo", "Smith", "Neal", "Brady", "Khanna", "Lieu",
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

# ─── AMOUNT PARSER ─────────────────────────────────────────────────────────────

def parse_amount(amount_str: str) -> float:
    """Congress reports ranges like '$15,001 - $50,000'. Take the midpoint."""
    if not amount_str:
        return 0
    cleaned = str(amount_str).replace("$", "").replace(",", "").replace("+", "")
    parts   = [p.strip() for p in cleaned.split("-") if p.strip()]
    try:
        if len(parts) >= 2:
            return (float(parts[0]) + float(parts[1])) / 2
        elif len(parts) == 1:
            return float(parts[0])
    except Exception:
        pass
    return 0

# ─── FMP DATA FETCHER ──────────────────────────────────────────────────────────

FMP_BASE = "https://financialmodelingprep.com/api/v4"

def fetch_senate_trades(api_key: str, lookback_days: int) -> list[dict]:
    """Fetch Senate STOCK Act disclosures from FMP."""
    trades  = []
    cutoff  = date.today() - timedelta(days=lookback_days)
    page    = 0

    while True:
        try:
            r = requests.get(
                f"{FMP_BASE}/senate-trading",
                params={"apikey": api_key, "page": page},
                timeout=15,
            )
            if r.status_code != 200:
                print(f"  Senate API error: {r.status_code}")
                break

            data = r.json()
            if not data:
                break

            found_old = False
            for item in data:
                txn_date_str = (item.get("transactionDate") or "")[:10]
                try:
                    txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                if txn_date < cutoff:
                    found_old = True
                    continue

                # Purchases only
                txn_type = (item.get("type") or "").lower()
                if "purchase" not in txn_type and "buy" not in txn_type:
                    continue

                ticker = (item.get("ticker") or "").strip().upper()
                if not ticker or ticker in ("N/A", "--", "", "UNKNOWN"):
                    continue

                amount = parse_amount(item.get("amount", ""))

                trades.append({
                    "member":   item.get("senator") or item.get("name") or "Unknown",
                    "chamber":  "Senate",
                    "ticker":   ticker,
                    "asset":    item.get("assetDescription") or item.get("asset") or "",
                    "type":     item.get("type", ""),
                    "amount":   amount,
                    "txn_date": txn_date_str,
                    "filed":    (item.get("disclosureDate") or "")[:10],
                    "party":    item.get("party") or "",
                    "state":    item.get("state") or "",
                })

            if found_old or len(data) < 20:
                break
            page += 1
            time.sleep(0.3)

        except Exception as e:
            print(f"  Senate fetch error: {e}")
            break

    return trades


def fetch_house_trades(api_key: str, lookback_days: int) -> list[dict]:
    """Fetch House STOCK Act disclosures from FMP."""
    trades  = []
    cutoff  = date.today() - timedelta(days=lookback_days)
    page    = 0

    while True:
        try:
            r = requests.get(
                f"{FMP_BASE}/senate-trading",  # FMP uses same endpoint for both chambers
                params={"apikey": api_key, "page": page, "chamber": "house"},
                timeout=15,
            )

            # FMP may also have a separate house endpoint
            if r.status_code == 404:
                r = requests.get(
                    f"{FMP_BASE}/house-trading",
                    params={"apikey": api_key, "page": page},
                    timeout=15,
                )

            if r.status_code != 200:
                print(f"  House API error: {r.status_code}")
                break

            data = r.json()
            if not data:
                break

            found_old = False
            for item in data:
                txn_date_str = (item.get("transactionDate") or item.get("date") or "")[:10]
                try:
                    txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                if txn_date < cutoff:
                    found_old = True
                    continue

                txn_type = (item.get("type") or item.get("transactionType") or "").lower()
                if "purchase" not in txn_type and "buy" not in txn_type:
                    continue

                ticker = (item.get("ticker") or "").strip().upper()
                if not ticker or ticker in ("N/A", "--", "", "UNKNOWN"):
                    continue

                amount = parse_amount(item.get("amount", ""))
                member = item.get("representative") or item.get("name") or "Unknown"

                trades.append({
                    "member":   member,
                    "chamber":  "House",
                    "ticker":   ticker,
                    "asset":    item.get("assetDescription") or item.get("asset") or "",
                    "type":     item.get("type", ""),
                    "amount":   amount,
                    "txn_date": txn_date_str,
                    "filed":    (item.get("disclosureDate") or "")[:10],
                    "party":    item.get("party") or "",
                    "state":    item.get("state") or "",
                })

            if found_old or len(data) < 20:
                break
            page += 1
            time.sleep(0.3)

        except Exception as e:
            print(f"  House fetch error: {e}")
            break

    return trades


# ─── ANALYZE TRADES ────────────────────────────────────────────────────────────

def analyze_trades(all_trades: list[dict], params: dict) -> list[dict]:
    trades  = [t for t in all_trades if t["amount"] >= params["min_amount"]]
    today   = date.today()
    results = []

    # Group by ticker
    by_ticker = {}
    for t in trades:
        by_ticker.setdefault(t["ticker"], []).append(t)

    for ticker, txns in by_ticker.items():
        # Get current price
        try:
            price = yf.Ticker(ticker).fast_info.last_price
            if not price or price <= 0:
                continue
        except Exception:
            continue

        # Estimate buy price from history around trade date
        oldest = min(
            datetime.strptime(t["txn_date"][:10], "%Y-%m-%d").date()
            for t in txns if t.get("txn_date") and len(t["txn_date"]) >= 10
        )
        try:
            hist      = yf.Ticker(ticker).history(
                start=oldest.isoformat(),
                end=(oldest + timedelta(days=5)).isoformat()
            )
            buy_price = float(hist["Close"].iloc[0]) if len(hist) > 0 else price
        except Exception:
            buy_price = price

        price_drift = (price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        if price_drift > params["max_price_drift_pct"]:
            continue

        total_amount = sum(t["amount"] for t in txns)
        members      = list(set(t["member"] for t in txns))
        member_count = len(members)
        most_recent  = max(t["txn_date"] for t in txns if t.get("txn_date"))
        days_since   = (today - datetime.strptime(most_recent[:10], "%Y-%m-%d").date()).days
        chambers     = "/".join(set(t["chamber"] for t in txns))
        parties      = "/".join(set(t["party"] for t in txns if t.get("party")))

        # Check high-profile buyers
        vip = [m for m in members if any(hp.lower() in m.lower() for hp in HIGH_PROFILE)]

        # Score
        amount_score    = min(total_amount / 250_000, 1.0)
        cluster_score   = min(member_count / 4, 1.0)
        freshness_score = max(0, 1 - days_since / params["lookback_days"])
        drift_score     = max(0, 1 - abs(price_drift) / 15)
        vip_score       = 1.0 if vip else 0.0

        score = round(
            amount_score    * 0.30 +
            cluster_score   * 0.25 +
            freshness_score * 0.20 +
            drift_score     * 0.15 +
            vip_score       * 0.10,
            3
        )

        results.append({
            "ticker":          ticker,
            "price":           round(price, 2),
            "buy_price":       round(buy_price, 2),
            "price_drift_pct": round(price_drift, 1),
            "member_count":    member_count,
            "members":         ", ".join(members[:3]) + (" ..." if len(members) > 3 else ""),
            "vip":             ", ".join(vip) if vip else "",
            "party":           parties,
            "total_amount":    round(total_amount),
            "most_recent":     most_recent[:10],
            "days_since":      days_since,
            "chamber":         chambers,
            "score":           score,
        })

        time.sleep(0.15)

    return results


# ─── FIND OPTIONS ──────────────────────────────────────────────────────────────

def find_calls(ticker: str, price: float, params: dict) -> dict | None:
    today   = date.today()
    min_exp = today + timedelta(days=params["min_dte"])
    max_exp = today + timedelta(days=params["max_dte"])
    best    = None

    try:
        yft  = yf.Ticker(ticker)
        exps = yft.options
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
            calls = yft.option_chain(exp_str).calls
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


# ─── DISPLAY ───────────────────────────────────────────────────────────────────

COLS = [
    "ticker", "price", "buy_price", "price_drift_pct",
    "member_count", "members", "vip", "party",
    "total_amount", "most_recent", "chamber",
    "strike", "exp", "dte", "delta", "iv", "mid", "score"
]
HDRS = [
    "Ticker", "Price", "Buy $", "Drift %",
    "# Members", "Members", "VIP", "Party",
    "Total $", "Last Trade", "Chamber",
    "Strike", "Expiration", "DTE", "Delta", "IV %", "Mid $", "Score"
]

def display(df: pd.DataFrame):
    if df.empty:
        print("No congressional purchases found in the window.")
        return

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["total_amount"] = df["total_amount"].apply(lambda x: f"${x:,.0f}" if x else "")

    print("\n" + "═"*150)
    print("  PELOSI.PY  —  CONGRESSIONAL STOCK PURCHASE TRACKER")
    print("  STOCK Act disclosures | House + Senate | Purchases only | Data: FMP")
    print("═"*150 + "\n")

    print(tabulate(
        df[COLS].head(25),
        headers=HDRS,
        tablefmt="rounded_outline",
        floatfmt=".2f",
        showindex=True,
    ))

    print(f"""
{'─'*150}
HOW TO READ THIS:

  Buy $        — Estimated price paid based on historical data around trade date
  Drift %      — How much stock moved since purchase. < 5% = fresh, still actionable
  # Members    — How many different Congress members bought this stock recently
  VIP          — High-profile member (committee chair, leadership) = higher info advantage
  Party        — Political party of buyer(s)
  Total $      — Combined estimated value (Congress reports ranges, not exact values)
  Chamber      — House, Senate, or both

SIGNAL QUALITY:
  🏆 VIP buyer (committee chair, leadership) = strongest signal
  🔥 Multiple members buying same stock = unusual consensus
  💰 Large amount + recent filing = high conviction
  ⚡ Both parties buying = bipartisan signal, very rare and very strong

CAVEATS:
  ✗ Up to 45-day filing delay — some trades may be old news by the time you see them
  ✗ Amounts are ranges — totals are midpoint estimates, not exact values
  ✗ Some trades are spouse/dependent purchases, not the member themselves
  ✓ Verify at: https://efts.sec.gov or https://disclosures.house.gov
  ✓ Check what committee the member sits on before trading

BEFORE TRADING:
  ✓ Is there pending legislation in this sector?
  ✓ Has the stock already moved significantly since the trade date?
  ✓ Is the VIP member on a relevant committee (Finance, Armed Services, Tech)?
  ✓ Exit plan: hold 1-3 months OR close at 75% gain on premium
{'─'*150}
""")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*65)
    print("  🏛️  PELOSI.PY  —  CONGRESSIONAL TRADE SCANNER")
    print("  Following the money on Capitol Hill since 2012")
    print("  Data: Financial Modeling Prep (FMP) — STOCK Act disclosures")
    print("═"*65 + "\n")

    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        print("ERROR: Set FMP_API_KEY in your .env file")
        print("Get a free key at: https://financialmodelingprep.com\n")
        sys.exit(1)

    print("Fetching Senate trades...")
    senate = fetch_senate_trades(api_key, PELOSI_PARAMS["lookback_days"])
    print(f"  {len(senate)} Senate purchase(s) found\n")

    print("Fetching House trades...")
    house = fetch_house_trades(api_key, PELOSI_PARAMS["lookback_days"])
    print(f"  {len(house)} House purchase(s) found\n")

    all_trades = senate + house

    if not all_trades:
        print("No trades fetched.")
        print("Check your FMP_API_KEY in .env and try again.")
        print("Free tier: https://financialmodelingprep.com\n")
        return

    print(f"Analyzing {len(all_trades)} total purchases...\n")
    results = analyze_trades(all_trades, PELOSI_PARAMS)

    if not results:
        print("No actionable trades after filters.")
        print("Try increasing lookback_days or max_price_drift_pct in PELOSI_PARAMS.")
        return

    print("\nFetching options chains...\n")
    for r in results:
        opt = find_calls(r["ticker"], r["price"], PELOSI_PARAMS)
        if opt:
            r.update({
                "strike": opt["strike"], "exp": opt["exp"],
                "dte":    opt["dte"],    "delta": opt["delta"],
                "iv":     opt["iv"],     "oi":    opt["oi"],
                "mid":    opt["mid"],    "bar":   opt["bar"],
            })
        else:
            r.update({
                "strike": None, "exp": None, "dte": None,
                "delta":  None, "iv":  None, "oi":  None,
                "mid":    None, "bar": None,
            })
        vip_flag     = " 🏆 VIP"     if r.get("vip")          else ""
        cluster_flag = " 🔥 CLUSTER" if r["member_count"] >= 2 else ""
        print(f"  {r['ticker']:6s}  {r['member_count']} member(s)  "
              f"drift {r['price_drift_pct']:+.1f}%  "
              f"score {r['score']}{vip_flag}{cluster_flag}")

    df = pd.DataFrame(results)
    display(df)

    out = f"pelosi_scan_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Saved to: {out}\n")


if __name__ == "__main__":
    main()