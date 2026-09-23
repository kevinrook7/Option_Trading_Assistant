"""
Tests for the Monte Carlo simulation.
No NSE data, no Ollama. Uses fixed spot and mock yfinance via monkeypatch.
"""

import numpy as np
import pytest


def test_run_monte_carlo_basic():
    from stockester_agent.tools.monte_carlo import run_monte_carlo

    spot = 24500.0
    cfg = {"days_to_simulate": 5, "num_iterations": 1000, "lookback_days": 252}
    final_prices, prob_up = run_monte_carlo(spot, cfg)

    assert len(final_prices) == 1000
    assert 0.0 <= prob_up <= 1.0
    # All simulated prices should be positive
    assert (final_prices > 0).all()


def test_prob_up_range():
    from stockester_agent.tools.monte_carlo import run_monte_carlo

    spot = 24500.0
    cfg = {"days_to_simulate": 5, "num_iterations": 5000, "lookback_days": 252}
    _, prob_up = run_monte_carlo(spot, cfg)

    # With a typical drift the prob should be between 30% and 70%
    assert 0.30 < prob_up < 0.70, f"Unexpected prob_up: {prob_up}"


def test_expected_price_near_spot():
    from stockester_agent.tools.monte_carlo import run_monte_carlo

    spot = 24500.0
    cfg = {"days_to_simulate": 1, "num_iterations": 10000, "lookback_days": 252}
    final_prices, _ = run_monte_carlo(spot, cfg)

    # Over 1 day the expected price should be close to spot (within 5%)
    mean_price = final_prices.mean()
    assert abs(mean_price - spot) / spot < 0.05, f"Mean price too far from spot: {mean_price}"


def test_fetch_nifty_metrics_fallback(monkeypatch):
    """If yfinance fails, fallback values should be returned."""
    import stockester_agent.tools.monte_carlo as mc_module

    class MockTicker:
        def __init__(self, *args, **kwargs):
            pass
        def history(self, *args, **kwargs):
            import pandas as pd
            return pd.DataFrame()  # empty — triggers fallback

    monkeypatch.setattr(mc_module.yf, "Ticker", MockTicker)

    mu, sigma = mc_module.fetch_nifty_metrics(lookback_days=252)
    # Fallback values
    assert mu == pytest.approx(0.0005)
    assert sigma == pytest.approx(0.015)
