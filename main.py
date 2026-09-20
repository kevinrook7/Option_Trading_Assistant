import csv
import json
import sys
import logging
from datetime import datetime
from pathlib import Path

from src.config_loader import load_config
from stockester_agent.tools.data_fetcher import fetch_option_data
from stockester_agent.tools.monte_carlo import run_monte_carlo
from src.scorer import rank_strikes, rank_strikes_buy
from src.context import get_market_context
from src.notifier import send_telegram_message

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CSV_LOG = Path("daily_log.csv")


# ---------------------------------------------------------------------------
# CSV logging
# ---------------------------------------------------------------------------

def _append_csv_log(date_str, spot, ctx, sell_list, buy_list, mc_prob_up_pct):
    """Append one row per run to daily_log.csv in the project root."""
    row = {
        'date':        date_str,
        'spot':        spot,
        'vix':         ctx.get('vix', ''),
        'dma_20':      ctx.get('dma_20', ''),
        'dma_50':      ctx.get('dma_50', ''),
        'dma_signal':  ctx.get('dma_signal', ''),
        'pcr':         ctx.get('pcr', ''),
        'max_pain':    ctx.get('max_pain', ''),
        'iv_mean':     ctx.get('iv_mean', ''),
        'mc_prob_up':  mc_prob_up_pct,
        'sell_strikes': json.dumps([
            {'strike': s['strike'], 'type': s['type'], 'premium': s['premium'], 'score': s['score']}
            for s in sell_list
        ]),
        'buy_strikes': json.dumps([
            {'strike': s['strike'], 'type': s['type'], 'premium': s['premium'], 'score': s['score']}
            for s in buy_list
        ]),
    }

    fieldnames = list(row.keys())
    write_header = not CSV_LOG.exists()

    with open(CSV_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    logger.info(f"Logged results to {CSV_LOG}")


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _build_message(date_str, spot, ctx, sell_list, buy_list, mc_prob_up_pct, mc_sentiment, top_n):
    lines = []

    # ── Header ────────────────────────────────────────────────────────────
    lines.append(f"<b>NIFTY OPTION SCANNER</b>")
    lines.append(f"📅 {date_str} | Spot: ₹{spot:,.2f}")
    lines.append("")

    # ── Market Context ────────────────────────────────────────────────────
    lines.append("<b>📊 MARKET CONTEXT</b>")

    vix_str = f"{ctx['vix']}" if ctx['vix'] is not None else "N/A"
    lines.append(f"  India VIX : {vix_str}  |  {ctx['iv_signal']}")

    if ctx['dma_20'] and ctx['dma_50']:
        lines.append(
            f"  20-DMA: {ctx['dma_20']:,}  |  50-DMA: {ctx['dma_50']:,}  →  {ctx['dma_signal']}"
        )
    else:
        lines.append(f"  DMA       : N/A")

    lines.append(f"  PCR       : {ctx['pcr']}  ({ctx['pcr_signal']})")
    lines.append(f"  Max Pain  : ₹{ctx['max_pain']:,}" if ctx['max_pain'] else "  Max Pain  : N/A")
    if ctx.get('expiry'):
        lines.append(f"  <i>(PCR / Max Pain for {ctx['expiry']} expiry)</i>")
    lines.append(f"  Mean IV   : {ctx['iv_mean']}%")
    lines.append("")

    # ── SELL Recommendations ──────────────────────────────────────────────
    lines.append(f"<b>📉 TOP {top_n} NIFTY OPTIONS TO SELL (Monthly Expiry)</b>")
    if not sell_list:
        lines.append("  ⚠️ No qualifying sell strikes found.")
    else:
        expiry = sell_list[0].get('expiry', 'N/A')
        lines.append(f"  Expiry: {expiry}")
        for s in sell_list:
            lines.append(
                f"  SELL <b>{s['strike']} {s['type']}</b> | "
                f"₹{s['premium']} | IV: {s['iv']}% | "
                f"OTM: {s['distance_otm']}% | OI: {s['oi']:,} | Score: {s['score']}"
            )
    lines.append("")

    # ── BUY Recommendations ───────────────────────────────────────────────
    lines.append(f"<b>📈 TOP {top_n} NIFTY OPTIONS TO BUY (Weekly Expiry)</b>")
    if not buy_list:
        lines.append("  ⚠️ No qualifying buy strikes found.")
    else:
        expiry = buy_list[0].get('expiry', 'N/A')
        lines.append(f"  Expiry: {expiry}")
        for s in buy_list:
            lines.append(
                f"  BUY  <b>{s['strike']} {s['type']}</b> | "
                f"₹{s['premium']} | IV: {s['iv']}% | "
                f"OTM: {s['distance_otm']}% | OI: {s['oi']:,} | R/R: {s['rr']}:1 | Score: {s['score']}"
            )
    lines.append("")

    # ── Monte Carlo ───────────────────────────────────────────────────────
    lines.append(f"<b>🎲 Monte Carlo (5-day sim)</b>")
    lines.append(f"  Prob Up: {mc_prob_up_pct}%  →  <b>{mc_sentiment}</b>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_daily_analysis():
    """Fetch NIFTY option chain, compute context, rank sell/buy strikes, notify."""
    logger.info("🚀 Starting NIFTY Option Scanner...")

    config  = load_config('config.yaml')
    index   = config['indices'][0]   # Always NIFTY
    top_n   = config['scoring'].get('top_n_to_notify', 5)

    # 1. Fetch option chain
    logger.info(f"Fetching option chain for {index}...")
    chain, spot = fetch_option_data(index)

    if chain is None or not spot:
        msg = f"❌ Failed to fetch data for {index}. Check internet connection."
        print(msg)
        send_telegram_message(msg)
        return

    # 2. Market context (VIX, DMA, PCR, Max Pain, IV)
    logger.info("Fetching market context...")
    ctx = get_market_context(chain, spot)

    # 3. Monte Carlo
    logger.info("Running Monte Carlo simulation...")
    _, prob_up     = run_monte_carlo(spot, config['simulation'])
    prob_up_pct    = round(prob_up * 100, 1)
    mc_sentiment   = ("Bullish" if prob_up_pct > 55 else
                      "Bearish" if prob_up_pct < 45 else "Neutral")

    # 4. Rank strikes
    logger.info("Ranking SELL strikes (monthly expiry)...")
    sell_list = rank_strikes(chain, spot, top_n=top_n)

    logger.info("Ranking BUY strikes (weekly expiry)...")
    buy_list = rank_strikes_buy(chain, spot, top_n=top_n)

    # 5. Build and send message
    date_str = datetime.now().strftime('%d-%b-%Y %H:%M IST')
    message  = _build_message(date_str, spot, ctx, sell_list, buy_list,
                               prob_up_pct, mc_sentiment, top_n)

    print(message)
    send_telegram_message(message)

    # 6. Log to CSV
    _append_csv_log(date_str, spot, ctx, sell_list, buy_list, prob_up_pct)

    logger.info("✅ Analysis complete.")
    return {'sell': sell_list, 'buy': buy_list, 'context': ctx}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from apscheduler.schedulers.blocking import BlockingScheduler
    import pytz

    if len(sys.argv) > 1 and sys.argv[1] in ["--now", "--test"]:
        print("🧪 Running in TEST mode (immediate execution)...")
        run_daily_analysis()
    else:
        scheduler = BlockingScheduler(timezone=pytz.timezone('Asia/Kolkata'))
        scheduler.add_job(run_daily_analysis, 'cron', hour=9, minute=25, day_of_week='mon-fri')

        print("📊 NIFTY Scanner scheduled for 9:25 AM IST every trading day.")
        print("⏳ Press Ctrl+C to stop the scheduler.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown()
            print("🛑 Scheduler stopped.")
