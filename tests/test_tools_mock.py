"""
Tests for the LangChain agent tools using MOCK_NSE=true.
No live NSE calls. No Ollama. Tests tool logic and caching behaviour.
"""

import json
import os
import pytest


@pytest.fixture(autouse=True)
def mock_nse_env(monkeypatch):
    """Enable mock mode for all tests in this file."""
    monkeypatch.setenv("MOCK_NSE", "true")


@pytest.fixture(autouse=True)
def clear_tool_cache():
    """Reset the module-level cache before each test."""
    from stockester_agent.agent.tools import clear_cache
    clear_cache()
    yield
    clear_cache()


def test_get_market_snapshot_returns_expected_keys():
    from stockester_agent.agent.tools import get_market_snapshot
    result = json.loads(get_market_snapshot.invoke({"symbol": "NIFTY"}))

    assert "error" not in result
    for key in ("spot", "pcr", "max_pain", "iv_mean", "vix", "dma_signal", "num_calls", "num_puts"):
        assert key in result, f"Missing key: {key}"

    assert result["spot"] > 0
    assert result["num_calls"] > 0
    assert result["num_puts"] > 0


def test_get_market_snapshot_caches():
    from stockester_agent.agent.tools import get_market_snapshot, _SNAPSHOT_CACHE
    result1 = json.loads(get_market_snapshot.invoke({"symbol": "NIFTY"}))
    result2 = json.loads(get_market_snapshot.invoke({"symbol": "NIFTY"}))

    # Second call should return cached data
    assert result2.get("_cached") is True
    assert result1["spot"] == result2["spot"]


def test_run_monte_carlo_sim():
    # First populate the chain cache via snapshot
    from stockester_agent.agent.tools import get_market_snapshot, run_monte_carlo_sim
    get_market_snapshot.invoke({"symbol": "NIFTY"})

    result = json.loads(run_monte_carlo_sim.invoke({"symbol": "NIFTY", "days": 5, "iterations": 1000}))

    assert "error" not in result
    assert "probability_up" in result
    assert "probability_down" in result
    assert 0.0 <= result["probability_up"] <= 1.0
    assert abs(result["probability_up"] + result["probability_down"] - 1.0) < 0.001


def test_get_historical_context_empty(tmp_path, monkeypatch):
    from stockester_agent.agent.tools import get_historical_context
    import stockester_agent.memory.database as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()

    result = json.loads(get_historical_context.invoke({"symbol": "NIFTY", "lookback_days": 30}))
    assert result["total_predictions"] == 0


def test_find_otm_sell_candidates():
    from stockester_agent.agent.tools import get_market_snapshot, find_otm_sell_candidates
    get_market_snapshot.invoke({"symbol": "NIFTY"})

    result = json.loads(find_otm_sell_candidates.invoke({"symbol": "NIFTY", "top_n": 3}))
    assert isinstance(result, list)
    # May be empty if mock chain has no qualifying strikes, but should not error
    for strike in result:
        assert "strike" in strike
        assert "type" in strike
        assert strike["type"] in ("CE", "PE")


def test_find_otm_buy_candidates():
    from stockester_agent.agent.tools import get_market_snapshot, find_otm_buy_candidates
    get_market_snapshot.invoke({"symbol": "NIFTY"})

    result = json.loads(find_otm_buy_candidates.invoke({"symbol": "NIFTY", "top_n": 3}))
    assert isinstance(result, list)
