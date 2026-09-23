"""
evaluate_predictions.py — fetch current NIFTY price and evaluate past predictions.

For each prediction that is old enough for a configured horizon (1h, 24h, 72h)
and has not yet been evaluated at that horizon, this script:
  1. Fetches the current NIFTY spot from yfinance.
  2. Computes the return since the prediction timestamp.
  3. Marks the prediction correct if the direction matches the stance.
  4. Writes the outcome to prediction_outcomes.

Usage
-----
  python evaluate_predictions.py

Run this periodically (e.g. via cron or a second APScheduler job).
A prediction is "correct" if:
  - bullish and return > 0
  - bearish and return < 0
  - neutral  always considered wrong for simplicity (no directional bet)
"""

import sys
import os
import logging
from datetime import datetime, timezone

import yfinance as yf

sys.path.insert(0, os.getcwd())

from src.config_loader import load_config
from stockester_agent.memory import (
    get_unevaluated_predictions,
    save_outcome,
    get_performance_summary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_current_nifty() -> float | None:
    """Fetch the latest NIFTY 50 close price from yfinance."""
    try:
        hist = yf.Ticker("^NSEI").history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.error(f"Failed to fetch NIFTY price: {e}")
    return None


def stance_correct(stance: str, price_return: float) -> int:
    """Return 1 if direction matches stance, 0 otherwise."""
    stance = (stance or "").lower()
    if stance == "bullish":
        return 1 if price_return > 0 else 0
    if stance == "bearish":
        return 1 if price_return < 0 else 0
    return 0  # neutral = no directional bet


def run_evaluation() -> None:
    cfg = load_config("config.yaml")
    horizons = cfg.get("evaluation", {}).get("horizons_hours", [1, 24, 72])

    current_price = fetch_current_nifty()
    if current_price is None:
        logger.error("Cannot evaluate: NIFTY price fetch failed.")
        return

    logger.info(f"Current NIFTY price: {current_price:.2f}")
    total_evaluated = 0

    for horizon_hours in horizons:
        predictions = get_unevaluated_predictions(horizon_hours)
        logger.info(
            f"Horizon {horizon_hours}h: {len(predictions)} predictions to evaluate"
        )

        for pred in predictions:
            # We don't have the prediction-time price stored in predictions.
            # We use the market_snapshot spot if available; otherwise skip.
            # (For this first version we store the current price as future_price
            #  and compute return from snapshot. A full implementation would store
            #  the spot at prediction time and fetch future price separately.)
            pred_id = pred["id"]
            stance = pred.get("stance", "neutral")

            # Approximate: use current price as the evaluation price.
            # The snapshot price is in market_snapshots; for simplicity we
            # treat price_return as 0 since we don't have historical spot here.
            # A proper implementation would join market_snapshots.
            from stockester_agent.memory.database import get_connection
            with get_connection() as conn:
                snap = conn.execute(
                    "SELECT spot FROM market_snapshots WHERE id = ?",
                    (pred.get("snapshot_id"),),
                ).fetchone()

            if snap and snap["spot"]:
                entry_price = snap["spot"]
                price_return = (current_price - entry_price) / entry_price
            else:
                logger.warning(
                    f"  Pred #{pred_id}: no snapshot — using return=0"
                )
                entry_price = current_price
                price_return = 0.0

            correct = stance_correct(stance, price_return)
            save_outcome(
                prediction_id=pred_id,
                horizon_hours=horizon_hours,
                future_price=current_price,
                price_return=price_return,
                correct=correct,
            )
            total_evaluated += 1
            logger.info(
                f"  Pred #{pred_id}: stance={stance}  "
                f"entry={entry_price:.0f}  current={current_price:.0f}  "
                f"return={price_return:+.2%}  correct={bool(correct)}"
            )

    logger.info(f"Evaluation complete. {total_evaluated} outcomes recorded.")

    # Print summary
    perf = get_performance_summary("NIFTY")
    if perf:
        print("\nPerformance summary (NIFTY):")
        for hours, stats in sorted(perf.items()):
            print(
                f"  {hours}h: {stats['correct']}/{stats['total']}  "
                f"accuracy={stats['accuracy']:.1%}  "
                f"avg_return={stats['avg_return_pct']:+.2f}%"
            )


if __name__ == "__main__":
    run_evaluation()
