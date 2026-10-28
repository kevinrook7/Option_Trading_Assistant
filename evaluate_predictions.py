"""
evaluate_predictions.py — score past predictions against what NIFTY actually did.

For each prediction that is old enough for a configured horizon (1h, 24h, 72h)
and has not yet been evaluated at that horizon, this script:
  1. Takes the entry price from the linked market_snapshot (falls back to the
     yfinance price at prediction time if there is no snapshot).
  2. Looks up the NIFTY price AT prediction_time + horizon using yfinance
     hourly bars (the last bar that had closed by that time).
  3. Marks the prediction correct if the direction matches the stance.
  4. Writes the outcome to prediction_outcomes.

Because prices are looked up historically, it does not matter when this script
runs: a late run gives the same result as an on-time one. main.py schedules it
daily after market close; you can also run it by hand.

Usage
-----
  python evaluate_predictions.py

Scoring rules:
  - bullish and return > 0          -> correct
  - bearish and return < 0          -> correct
  - neutral                         -> always wrong (no directional bet)
  - market closed for the whole horizon window (no bar closed between
    prediction and target) -> correct = NULL, excluded from accuracy
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from src.config_loader import load_config
from stockester_agent.memory import (
    init_db,
    get_unevaluated_predictions,
    save_outcome,
    get_performance_summary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

NIFTY_TICKER = "^NSEI"
IST = "Asia/Kolkata"
MARKET_CLOSE = timedelta(hours=15, minutes=30)  # NSE close, IST


def parse_ts(ts: str) -> datetime:
    """Parse a stored prediction timestamp into an aware UTC datetime."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:  # naive timestamps are treated as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_hourly_closes(start: datetime) -> pd.Series | None:
    """
    Fetch NIFTY hourly closes from `start` (minus a buffer) until now.

    Returns a Series of close prices indexed by the UTC time each bar CLOSED
    (bar start + 1h, capped at the 15:30 IST market close). yfinance only
    serves hourly data for roughly the last 730 days.
    """
    try:
        hist = yf.Ticker(NIFTY_TICKER).history(
            start=(start - timedelta(days=5)).date(),
            end=(datetime.now(timezone.utc) + timedelta(days=1)).date(),
            interval="60m",
        )
    except Exception as e:
        logger.error(f"Failed to fetch NIFTY hourly history: {e}")
        return None

    if hist.empty:
        logger.error("yfinance returned no hourly NIFTY data.")
        return None

    idx = hist.index
    if idx.tz is None:
        idx = idx.tz_localize(IST)
    idx = idx.tz_convert(IST)

    bar_end = idx + pd.Timedelta(hours=1)
    session_close = idx.normalize() + MARKET_CLOSE
    bar_end = bar_end.where(bar_end <= session_close, session_close)

    closes = pd.Series(hist["Close"].to_numpy(), index=bar_end.tz_convert("UTC"))
    return closes.sort_index()


def price_asof(closes: pd.Series, when: datetime) -> tuple[float, pd.Timestamp] | None:
    """Last close at or before `when`, with the time that bar closed."""
    upto = closes[closes.index <= pd.Timestamp(when)]
    if upto.empty:
        return None
    return float(upto.iloc[-1]), upto.index[-1]


def stance_correct(stance: str, price_return: float) -> int:
    """Return 1 if direction matches stance, 0 otherwise."""
    stance = (stance or "").lower()
    if stance == "bullish":
        return 1 if price_return > 0 else 0
    if stance == "bearish":
        return 1 if price_return < 0 else 0
    return 0  # neutral = no directional bet


def run_evaluation() -> int:
    """Evaluate all due (prediction, horizon) pairs. Returns outcomes recorded."""
    init_db()
    cfg = load_config("config.yaml")
    horizons = cfg.get("evaluation", {}).get("horizons_hours", [1, 24, 72])

    due = {h: get_unevaluated_predictions(h) for h in horizons}
    all_preds = [p for preds in due.values() for p in preds]
    if not all_preds:
        logger.info("Nothing to evaluate.")
        return 0

    earliest = min(parse_ts(p["timestamp"]) for p in all_preds)
    closes = fetch_hourly_closes(earliest)
    if closes is None:
        logger.error("Cannot evaluate: price history unavailable.")
        return 0

    total_evaluated = 0
    for horizon_hours, predictions in due.items():
        logger.info(
            f"Horizon {horizon_hours}h: {len(predictions)} predictions to evaluate"
        )

        for pred in predictions:
            pred_id = pred["id"]
            stance = pred.get("stance") or "neutral"
            pred_time = parse_ts(pred["timestamp"])
            target_time = pred_time + timedelta(hours=horizon_hours)

            entry_price = pred.get("snapshot_spot")
            if not entry_price:
                at_entry = price_asof(closes, pred_time)
                if at_entry is None:
                    logger.warning(f"  Pred #{pred_id}: no entry price — skipping")
                    continue
                entry_price = at_entry[0]

            at_target = price_asof(closes, target_time)
            if at_target is None:
                logger.warning(
                    f"  Pred #{pred_id}: no price at {target_time:%Y-%m-%d %H:%M} UTC — skipping"
                )
                continue
            future_price, bar_closed_at = at_target

            price_return = (future_price - entry_price) / entry_price

            # If no bar closed inside (prediction, target], the market never
            # traded during the horizon — don't score it as right or wrong.
            if bar_closed_at <= pd.Timestamp(pred_time):
                correct = None
            else:
                correct = stance_correct(stance, price_return)

            save_outcome(
                prediction_id=pred_id,
                horizon_hours=horizon_hours,
                future_price=future_price,
                price_return=price_return,
                correct=correct,
            )
            total_evaluated += 1
            verdict = "n/a (market closed)" if correct is None else bool(correct)
            logger.info(
                f"  Pred #{pred_id}: stance={stance}  "
                f"entry={entry_price:.2f}  at+{horizon_hours}h={future_price:.2f}  "
                f"return={price_return:+.2%}  correct={verdict}"
            )

    logger.info(f"Evaluation complete. {total_evaluated} outcomes recorded.")

    perf = get_performance_summary("NIFTY")
    if perf:
        print("\nPerformance summary (NIFTY):")
        for hours, stats in sorted(perf.items()):
            print(
                f"  {hours}h: {stats['correct']}/{stats['total']}  "
                f"accuracy={stats['accuracy']:.1%}  "
                f"avg_return={stats['avg_return_pct']:+.2f}%"
            )
    return total_evaluated


if __name__ == "__main__":
    run_evaluation()
