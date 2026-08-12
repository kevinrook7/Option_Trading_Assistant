"""
Market context signals for NIFTY option analysis.

Fetches:
  - India VIX  (Yahoo Finance: ^INDIAVIX)
  - NIFTY 20-DMA and 50-DMA  (Yahoo Finance: ^NSEI)

Uses existing indicators.py for PCR, Max Pain, and mean IV.
"""

import yfinance as yf

from src.indicators import calculate_max_pain, calculate_pcr, get_iv_percentile


# ---------------------------------------------------------------------------
# External data helpers
# ---------------------------------------------------------------------------

def fetch_india_vix() -> float | None:
    """Return the latest India VIX close, or None on failure."""
    try:
        hist = yf.Ticker("^INDIAVIX").history(period="2d")
        if not hist.empty:
            return round(float(hist['Close'].iloc[-1]), 2)
    except Exception as e:
        print(f"VIX fetch failed: {e}")
    return None


def fetch_nifty_dma(short: int = 20, long: int = 50) -> tuple[float | None, float | None]:
    """
    Return (20-DMA, 50-DMA) for NIFTY 50.
    Fetches 90 calendar days of history so the 50-DMA is always computable.
    Returns (None, None) on failure.
    """
    try:
        hist = yf.Ticker("^NSEI").history(period="90d")
        if hist.empty or len(hist) < long:
            print(f"DMA fetch: insufficient history ({len(hist)} bars, need {long})")
            return None, None
        close    = hist['Close']
        dma_s    = round(float(close.rolling(short).mean().iloc[-1]), 2)
        dma_l    = round(float(close.rolling(long).mean().iloc[-1]), 2)
        return dma_s, dma_l
    except Exception as e:
        print(f"DMA fetch failed: {e}")
    return None, None


# ---------------------------------------------------------------------------
# Aggregated context
# ---------------------------------------------------------------------------

def get_market_context(chain: dict, spot: float) -> dict:
    """
    Collect all market context signals into a single dict.

    Keys returned:
        vix         – India VIX (float or None)
        dma_20      – 20-day moving average (float or None)
        dma_50      – 50-day moving average (float or None)
        dma_signal  – 'Bullish' | 'Bearish' | 'Neutral' | 'N/A'
        pcr         – Put-Call Ratio (float)
        pcr_signal  – human-readable PCR interpretation (str)
        max_pain    – Max Pain strike (int or None)
        iv_mean     – Mean IV across the chain (float, %)
        iv_signal   – 'High IV' | 'Low IV' | 'Normal IV'
    """
    vix            = fetch_india_vix()
    dma_20, dma_50 = fetch_nifty_dma()
    pcr            = calculate_pcr(chain)
    max_pain       = calculate_max_pain(chain, spot)
    iv_mean        = get_iv_percentile(chain)   # returns mean IV * 100

    # DMA signal: spot position relative to both MAs
    if dma_20 is not None and dma_50 is not None:
        if spot > dma_20 > dma_50:
            dma_signal = "Bullish"
        elif spot < dma_20 < dma_50:
            dma_signal = "Bearish"
        else:
            dma_signal = "Neutral"
    else:
        dma_signal = "N/A"

    # PCR signal
    if pcr > 1.2:
        pcr_signal = "Bearish (Put heavy)"
    elif pcr < 0.8:
        pcr_signal = "Bullish (Call heavy)"
    else:
        pcr_signal = "Neutral"

    # IV signal (rough thresholds for NIFTY)
    if iv_mean >= 20:
        iv_signal = "High IV (good for selling)"
    elif iv_mean <= 12:
        iv_signal = "Low IV (good for buying)"
    else:
        iv_signal = "Normal IV"

    return {
        'vix':        vix,
        'dma_20':     dma_20,
        'dma_50':     dma_50,
        'dma_signal': dma_signal,
        'pcr':        round(pcr, 3),
        'pcr_signal': pcr_signal,
        'max_pain':   int(max_pain) if max_pain else None,
        'iv_mean':    round(iv_mean, 2),
        'iv_signal':  iv_signal,
    }
