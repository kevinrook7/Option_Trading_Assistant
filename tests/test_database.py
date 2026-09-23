"""
Tests for SQLite database operations.
Uses a temporary database file — no production data is touched.
"""

import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    """Redirect DB_PATH to a temp file for each test."""
    import stockester_agent.memory.database as db_module
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()
    yield db_path


def test_init_db_creates_tables(tmp_path):
    import stockester_agent.memory.database as db_module
    conn = db_module.get_connection()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "market_snapshots" in tables
    assert "predictions" in tables
    assert "prediction_outcomes" in tables


def test_save_and_read_snapshot():
    import stockester_agent.memory.database as db_module
    sid = db_module.save_snapshot(
        symbol="NIFTY",
        spot=24500.0,
        expiry="25-09-2026",
        pcr=0.95,
        max_pain=24400.0,
        iv_mean=14.5,
        vix=13.2,
        dma_20=24300.0,
        dma_50=24100.0,
        dma_signal="Bullish",
    )
    assert isinstance(sid, int)
    assert sid > 0

    conn = db_module.get_connection()
    row = conn.execute("SELECT * FROM market_snapshots WHERE id=?", (sid,)).fetchone()
    assert row["symbol"] == "NIFTY"
    assert row["spot"] == 24500.0
    assert row["pcr"] == 0.95


def test_save_and_read_prediction():
    import stockester_agent.memory.database as db_module
    pred_id = db_module.save_prediction(
        symbol="NIFTY",
        stance="bullish",
        confidence=0.72,
        summary="Looking good.",
        key_evidence=["PCR=0.9"],
        uncertainties=["VIX spike"],
        tools_used=["get_market_snapshot"],
        snapshot_id=None,
    )
    assert isinstance(pred_id, int)

    preds = db_module.get_recent_predictions(limit=5)
    assert len(preds) == 1
    assert preds[0]["stance"] == "bullish"
    assert preds[0]["confidence"] == 0.72


def test_save_outcome():
    import stockester_agent.memory.database as db_module
    pred_id = db_module.save_prediction(
        symbol="NIFTY",
        stance="bearish",
        confidence=0.6,
        summary="Bearish signals.",
        key_evidence=[],
        uncertainties=[],
        tools_used=[],
    )
    db_module.save_outcome(
        prediction_id=pred_id,
        horizon_hours=24,
        future_price=24200.0,
        price_return=-0.012,
        correct=1,
    )

    conn = db_module.get_connection()
    row = conn.execute(
        "SELECT * FROM prediction_outcomes WHERE prediction_id=?", (pred_id,)
    ).fetchone()
    assert row is not None
    assert row["correct"] == 1
    assert row["horizon_hours"] == 24


def test_historical_context_empty():
    import stockester_agent.memory.database as db_module
    data = db_module.get_historical_context_data("NIFTY", lookback_days=30)
    assert data["total_predictions"] == 0
    assert data["accuracy"] is None


def test_historical_context_with_data():
    import stockester_agent.memory.database as db_module
    for stance, correct in [("bullish", 1), ("bearish", 0), ("neutral", 0)]:
        pid = db_module.save_prediction(
            symbol="NIFTY",
            stance=stance,
            confidence=0.5,
            summary="test",
            key_evidence=[],
            uncertainties=[],
            tools_used=[],
        )
        db_module.save_outcome(
            prediction_id=pid,
            horizon_hours=24,
            future_price=24500.0,
            price_return=0.005,
            correct=correct,
        )

    data = db_module.get_historical_context_data("NIFTY", lookback_days=30)
    assert data["total_predictions"] == 3
    assert data["with_outcomes"] == 3
    assert data["accuracy"] == pytest.approx(1 / 3, abs=0.01)
    assert data["stance_counts"]["bullish"] == 1
