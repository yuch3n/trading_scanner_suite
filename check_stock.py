"""
Quick diagnostic — shows signal values for any stock.
Usage: python check_stock.py NVDA
"""
import sys

try:
    import yfinance as yf
    import ta
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: python -m pip install yfinance ta")
    input("Press Enter to exit...")
    sys.exit(1)

symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "NVDA"
print(f"Checking {symbol}...")

try:
    hist = yf.Ticker(symbol).history(period="1y")

    if hist is None or len(hist) < 20:
        print(f"Not enough price data for {symbol}")
        input("Press Enter to exit...")
        sys.exit(1)

    close = hist["Close"]
    high  = hist["High"]
    volume = hist["Volume"]

    price    = float(close.iloc[-1])
    rsi      = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
    high20   = float(high.iloc[-20:].max())
    pullback = (high20 - price) / high20 * 100
    ma200    = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    above200 = ((price - ma200) / ma200 * 100) if ma200 else None

    # IV rank calculation
    returns  = close.pct_change().dropna()
    vol_21d  = float(returns.iloc[-21:].std() * (252 ** 0.5) * 100)
    vol_252d = returns.rolling(21).std().dropna() * (252 ** 0.5) * 100
    vol_min  = float(vol_252d.min())
    vol_max  = float(vol_252d.max())
    iv_rank  = ((vol_21d - vol_min) / (vol_max - vol_min) * 100) if vol_max > vol_min else 50

    # Volume fading on down days
    down_days       = hist[close.diff() < 0]
    recent_down_vol = float(down_days["Volume"].iloc[-5:].mean()) if len(down_days) >= 5 else 0
    prior_down_vol  = float(down_days["Volume"].iloc[-15:-5].mean()) if len(down_days) >= 15 else 0
    vol_fading      = recent_down_vol < prior_down_vol if prior_down_vol > 0 else False

    # RSI divergence
    rsi_series       = ta.momentum.RSIIndicator(close, window=14).rsi()
    price_low_recent = float(close.iloc[-5:].min())
    price_low_prior  = float(close.iloc[-15:-5].min())
    rsi_low_recent   = float(rsi_series.iloc[-5:].min())
    rsi_low_prior    = float(rsi_series.iloc[-15:-5].min())
    divergence       = (price_low_recent < price_low_prior) and (rsi_low_recent > rsi_low_prior)

    print(f"\n{symbol} full signal check:")
    print(f"  Price:        ${price:.2f}")
    print(f"  RSI(14):      {rsi:.1f}   {'✓' if rsi < 45 else '✗ need < 45'}")
    print(f"  Pullback:     {pullback:.1f}%  {'✓' if pullback > 5 else '✗ need > 5%'}")
    if above200 is not None:
        print(f"  Above 200MA:  {above200:.1f}%  {'✓' if above200 > 0 else '✗ below 200MA'}")
    print(f"  IV Rank:      {iv_rank:.1f}%  {'✓' if iv_rank < 70 else '✗ need < 70 (options too expensive)'}")
    print(f"  Vol fading:   {'✓ yes' if vol_fading else '  no'}")
    print(f"  RSI divergence: {'⚡ yes' if divergence else '  no'}")

    print()
    fails = []
    if rsi >= 45:        fails.append(f"RSI {rsi:.0f} too high (need < 45)")
    if pullback <= 5:    fails.append(f"pullback {pullback:.1f}% too small (need > 5%)")
    if above200 and above200 <= 0: fails.append("below 200MA")
    if iv_rank >= 70:    fails.append(f"IV rank {iv_rank:.0f} too high (need < 70)")

    if fails:
        print(f"  Filtered out because: {', '.join(fails)}")
    else:
        print(f"  → {symbol} passes all filters — should appear in scanner!")

except Exception as e:
    print(f"Error: {e}")
    import traceback; traceback.print_exc()

input("\nPress Enter to exit...")