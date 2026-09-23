"""
LangChain tools for the Stockester agent.

Design: NSE is fetched ONCE via get_market_snapshot().
That result is cached at module level for 5 minutes (CACHE_TTL).
All other tools reuse the cached chain — they do NOT re-fetch NSE.

Available tools
---------------
get_market_snapshot      - fetch NSE once, return all indicators as JSON
run_monte_carlo_sim      - GBM simulation using cached spot
get_historical_context   - query SQLite for past predictions/outcomes
find_otm_sell_candidates - rank strikes for selling premium (optional)
find_otm_buy_candidates  - rank strikes for buying/direction (optional)
"""

import json
import time
from typing import Optional

from langchain.tools import tool

from stockester_agent.tools.data_fetcher import fetch_option_data
from stockester_agent.tools.monte_carlo import run_monte_carlo
from stockester_agent.memory.database import get_historical_context_data

# ---------------------------------------------------------------------------
# Module-level cache  (avoids duplicate NSE requests within one agent run)
# ---------------------------------------------------------------------------

_SNAPSHOT_CACHE: dict = {}  # symbol -> (timestamp, snapshot_dict)
_CHAIN_CACHE: dict = {}      # symbol -> (timestamp, chain, spot)
CACHE_TTL = 300              # seconds


def _cached_snapshot(symbol: str) -> Optional[dict]:
    entry = _SNAPSHOT_CACHE.get(symbol)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    return None


def _cached_chain(symbol: str) -> Optional[tuple]:
    """Return (chain, spot) from cache, or None if stale/missing."""
    entry = _CHAIN_CACHE.get(symbol)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1], entry[2]
    return None


def clear_cache() -> None:
    """Force fresh data on next tool call. Called between test runs."""
    _SNAPSHOT_CACHE.clear()
    _CHAIN_CACHE.clear()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def get_market_snapshot(symbol: str = "NIFTY") -> str:
    """
    Fetch live NSE option chain for symbol and compute all market indicators.
    Returns JSON with: spot, pcr, pcr_signal, max_pain, iv_mean, iv_signal,
    vix, dma_20, dma_50, dma_signal, expiry, num_calls, num_puts.

    This result is cached for 5 minutes — other tools reuse the same chain.
    Call this FIRST before run_monte_carlo_sim or find_otm_*_candidates.
    """
    cached = _cached_snapshot(symbol)
    if cached:
        return json.dumps({**cached, "_cached": True})

    chain, spot = fetch_option_data(symbol)
    if chain is None or not spot:
        return json.dumps({"error": f"Failed to fetch data for {symbol}"})

    # Cache the raw chain so other tools avoid re-fetching
    _CHAIN_CACHE[symbol] = (time.time(), chain, spot)

    # Compute all indicators from src.context (single unified call)
    from src.context import get_market_context
    ctx = get_market_context(chain, spot)

    snapshot = {
        "symbol": symbol,
        "spot": spot,
        "pcr": ctx["pcr"],
        "pcr_signal": ctx["pcr_signal"],
        "max_pain": ctx["max_pain"],
        "iv_mean": ctx["iv_mean"],
        "iv_signal": ctx["iv_signal"],
        "vix": ctx["vix"],
        "dma_20": ctx["dma_20"],
        "dma_50": ctx["dma_50"],
        "dma_signal": ctx["dma_signal"],
        "expiry": ctx["expiry"],
        "num_calls": len(chain["calls"]),
        "num_puts": len(chain["puts"]),
    }

    _SNAPSHOT_CACHE[symbol] = (time.time(), snapshot)
    return json.dumps(snapshot)


@tool
def run_monte_carlo_sim(symbol: str = "NIFTY", days: int = 5, iterations: int = 5000) -> str:
    """
    Run a Monte Carlo GBM simulation for symbol over the given number of days.
    Returns JSON with: probability_up, probability_down, expected_price,
    simulations, horizon_days, spot.

    Uses cached spot from get_market_snapshot if available.
    Fetches historical NIFTY returns from yfinance to estimate drift and volatility.

    Note: Monte Carlo describes statistical tendencies, not certainties.
    """
    cached = _cached_chain(symbol)
    if cached is not None:
        _, spot = cached
    else:
        _, spot = fetch_option_data(symbol)
        if not spot:
            return json.dumps({"error": f"Could not get spot price for {symbol}"})

    sim_config = {
        "days_to_simulate": days,
        "num_iterations": iterations,
        "lookback_days": 252,
    }

    final_prices, prob_up = run_monte_carlo(spot, sim_config)

    result = {
        "probability_up": round(float(prob_up), 4),
        "probability_down": round(float(1 - prob_up), 4),
        "expected_price": round(float(final_prices.mean()), 2),
        "simulations": iterations,
        "horizon_days": days,
        "spot": spot,
        "note": "Monte Carlo describes statistical tendencies, not guaranteed outcomes.",
    }
    return json.dumps(result)


@tool
def get_historical_context(symbol: str = "NIFTY", lookback_days: int = 30) -> str:
    """
    Retrieve historical predictions and outcomes for symbol from the SQLite database.
    Returns JSON with: total_predictions, evaluated_predictions, accuracy,
    average_confidence, stance_counts, recent_predictions (last 5).

    Use this to understand how past conditions compared to today and whether
    previous predictions were correct.
    """
    data = get_historical_context_data(symbol, lookback_days)

    if data["total_predictions"] == 0:
        return json.dumps({
            "message": (
                f"No historical predictions found for {symbol} "
                f"in the last {lookback_days} days."
            ),
            "total_predictions": 0,
        })

    accuracy_str = (
        f"{data['accuracy']:.1%}" if data["accuracy"] is not None else "not yet evaluated"
    )

    return json.dumps({
        "symbol": symbol,
        "lookback_days": lookback_days,
        "total_predictions": data["total_predictions"],
        "evaluated_predictions": data["with_outcomes"],
        "accuracy": accuracy_str,
        "average_confidence": data["avg_confidence"],
        "stance_counts": data["stance_counts"],
        "recent_predictions": data["recent"],
    }, default=str)


@tool
def find_otm_sell_candidates(symbol: str = "NIFTY", top_n: int = 3) -> str:
    """
    Find top OTM option strikes suitable for SELLING premium (monthly expiry).
    Returns JSON list of: strike, type, premium, iv, distance_otm, score, expiry, oi.

    Only call this if the user asks about selling options or income strategies.
    Call get_market_snapshot first — this reuses the cached chain.
    """
    cached = _cached_chain(symbol)
    if cached is not None:
        chain, spot = cached
    else:
        chain, spot = fetch_option_data(symbol)
        if chain is None:
            return json.dumps({"error": f"No chain data for {symbol}"})

    from src.scorer import rank_strikes
    results = rank_strikes(chain, spot, top_n=top_n)
    return json.dumps(results)


@tool
def find_otm_buy_candidates(symbol: str = "NIFTY", top_n: int = 3) -> str:
    """
    Find top OTM option strikes suitable for BUYING (nearest weekly expiry).
    Returns JSON list of: strike, type, premium, iv, distance_otm, score, expiry, oi, rr.

    Only call this if the user asks about buying options or directional strategies.
    Call get_market_snapshot first — this reuses the cached chain.
    """
    cached = _cached_chain(symbol)
    if cached is not None:
        chain, spot = cached
    else:
        chain, spot = fetch_option_data(symbol)
        if chain is None:
            return json.dumps({"error": f"No chain data for {symbol}"})

    from src.scorer import rank_strikes_buy
    results = rank_strikes_buy(chain, spot, top_n=top_n)
    return json.dumps(results)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    get_market_snapshot,
    run_monte_carlo_sim,
    get_historical_context,
    find_otm_sell_candidates,
    find_otm_buy_candidates,
]
