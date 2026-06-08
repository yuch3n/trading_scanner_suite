"""
All tunable parameters for the PEAD scanner.
Edit this file to adjust strategy rules.
"""

# ── Earnings filters ──────────────────────────────────────────────────────────

# EPS beat range. Research shows 10-19% is the sweet spot for drift.
# We default to 5-50% to catch more candidates; tighten for live trading.
MIN_EPS_SURPRISE_PCT = 5.0
MAX_EPS_SURPRISE_PCT = 50.0

# How old the fiscal quarter end can be (days).
# Companies typically report 2-6 weeks after quarter end.
MAX_QUARTER_AGE_DAYS = 90

# ── Options filters ───────────────────────────────────────────────────────────

MIN_DELTA         = 0.30   # lower = cheaper, less likely to profit
MAX_DELTA         = 0.55   # higher = more expensive, more like owning stock
MIN_DTE           = 14     # minimum days to expiration
MAX_DTE           = 60     # maximum days to expiration
MIN_OPEN_INTEREST = 50     # liquidity filter
MIN_BID_ASK_RATIO = 0.70   # bid/ask quality: 1.0 = perfect, 0.0 = no bid

# ── Universe ──────────────────────────────────────────────────────────────────
# Stocks to scan. Add or remove symbols as needed.

UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AMD","NFLX","CRM",
    "ORCL","ADBE","INTC","QCOM","TXN","MU","AVGO","NOW","SNOW","PLTR",
    "HOOD","UNH","SPOT","RDDT"
]

# ── Scoring weights ───────────────────────────────────────────────────────────
# Score = delta_score * DELTA_WEIGHT + spread_score * SPREAD_WEIGHT + surprise_score * SURPRISE_WEIGHT
# Must sum to 1.0

DELTA_WEIGHT    = 0.50   # how close delta is to 0.40 (the target)
SPREAD_WEIGHT   = 0.30   # bid/ask ratio quality
SURPRISE_WEIGHT = 0.20   # size of the EPS beat

# ── Short squeeze universe ────────────────────────────────────────────────────
# Mid-cap and micro-cap stocks with historically high short interest.
# Used by deez_nutz.py. These are higher risk than the main universe —
# many are speculative, unprofitable, or have real business challenges.
# That's exactly WHY they're heavily shorted and squeeze-prone.

SQUEEZE_UNIVERSE = [
    # Meme / retail favorites — classic squeeze history
    "GME", "AMC", "KOSS",

    # EV / clean energy — heavily shorted on execution concerns
    "RIVN", "LCID",
    "PLUG", "FCEL", "BLNK", "CHPT", "STEM",

    # Biotech — binary events, high short interest common
    "NVAX", "VXRT", "OCGN", "SAVA",

    # Speculative tech / fintech
    "OPEN", "UWMC", "SPCE",

    # Retail / consumer under pressure
    "BYND", "EVGO", "HIMS", "AFRM", "UPST",

    # Mid-cap with notable short interest
    "TDOC", "PTON", "ZM", "DKNG",
    "SOFI", "MSTR", "RIOT", "MARA", "CLSK",

    # Crypto adjacent
    "COIN", "HOOD", "HUT", "BITF", "CIFR",
]