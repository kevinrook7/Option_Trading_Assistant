"""
main.py — scheduled entry point for the Stockester agent.

The scheduler runs two weekday jobs (IST):
  09:25  analysis   — invokes the same LangGraph agent that Telegram uses
  15:45  evaluation — scores due predictions (evaluate_predictions.py)

Usage
-----
  python main.py          # starts scheduler
  python main.py --now    # run analysis immediately (for testing)
  python main.py --test   # same as --now
"""

import logging
import sys

# Emoji in log/print output crash cp1252 consoles when stdout is redirected
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage

from src.config_loader import load_config
from stockester_agent.agent.graph import graph
from stockester_agent.agent.tools import clear_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)


def run_scheduled_analysis() -> dict:
    """
    Entry point for the APScheduler job and --now/--test CLI flag.

    Invokes the LangGraph agent with notify_telegram=True so the result
    is automatically sent to Telegram after the analysis completes.
    """
    cfg = load_config("config.yaml")
    symbol = cfg["indices"][0]

    logger.info(f"Starting scheduled analysis for {symbol}...")
    clear_cache()  # ensure fresh NSE data on every scheduled run

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        f"Run a complete market analysis for {symbol}. "
                        "Use get_market_snapshot first, then decide whether "
                        "Monte Carlo or historical context would add useful information."
                    )
                )
            ],
            "symbol": symbol,
            "notify_telegram": True,
            "analysis": None,
            "snapshot_id": None,
        }
    )

    analysis = result.get("analysis") or {}
    logger.info(
        f"Analysis complete: stance={analysis.get('stance')}  "
        f"confidence={analysis.get('confidence')}"
    )
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--now", "--test"):
        print("Running in immediate mode...")
        run_scheduled_analysis()
    else:
        import pytz
        from apscheduler.schedulers.blocking import BlockingScheduler

        from evaluate_predictions import run_evaluation

        scheduler = BlockingScheduler(timezone=pytz.timezone("Asia/Kolkata"))
        scheduler.add_job(
            run_scheduled_analysis,
            "cron",
            hour=9,
            minute=25,
            day_of_week="mon-fri",
            misfire_grace_time=600,
        )
        # Evaluation looks prices up historically, so one run after the close
        # covers every horizon that became due during the day.
        scheduler.add_job(
            run_evaluation,
            "cron",
            hour=15,
            minute=45,
            day_of_week="mon-fri",
            misfire_grace_time=3600,
        )

        print("NIFTY agent scheduled for 9:25 AM IST every weekday.")
        print("Prediction evaluation scheduled for 3:45 PM IST every weekday.")
        print("Press Ctrl+C to stop.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown()
            print("Scheduler stopped.")
