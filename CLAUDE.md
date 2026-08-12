# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stockester is a Python automated scanner for Indian stock market options (NSE indices: NIFTY, BANKNIFTY). It runs as a scheduled script that fetches live NSE option chain data, computes metrics, scores opportunities, and sends a Telegram alert at 9:25 AM IST on weekdays.

## Commands

```bash
# Install dependencies (use the project venv)
.venv/Scripts/pip install -r requirements.txt   # Windows
# or
pip install -r requirements.txt

# Run immediately (skips the scheduler, triggers analysis now)
python main.py --now

# Run in test mode
python main.py --test

# Run in scheduled mode (fires Mon-Fri at 9:25 AM IST)
python main.py
```

There is no lint or test suite. `test_nse.py` is a throwaway library sanity check, not a real test.

## Architecture

The codebase is a linear analysis pipeline. `main.py` orchestrates all modules sequentially in `run_daily_analysis()`:

```
data_fetcher → indicators → monte_carlo → scorer → notifier
```

**Module responsibilities (`src/`):**
- `config_loader.py` — loads `config.yaml` (PyYAML) and `.env` secrets (python-dotenv)
- `data_fetcher.py` — fetches NSE option chain via `nse` library; falls back to direct HTTP to `nseindia.com` API if the library fails
- `indicators.py` — computes Max Pain (strike minimizing total option payout), PCR (put-call ratio), and mean IV across all strikes
- `monte_carlo.py` — runs 5000 Geometric Brownian Motion simulations over 5 days; returns probability of price ending above current spot
- `scorer.py` — computes weighted composite score: Max Pain Distance (40%), PCR (30%), IV Percentile (30%), Monte Carlo bonus (+10% of `prob_up`)
- `notifier.py` — sends HTML-formatted alert via Telegram Bot API

**Data:**
- `config.yaml` — indices list, simulation params, scoring weights, `top_n_to_notify`
- `data/opt-expiry.json` — cached NSE option expiry dates
- `.env` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TIMEZONE=Asia/Kolkata`

## Important Notes


- The virtual environment is at `.venv/` (Python interpreter: `.venv/Scripts/python.exe` on Windows).
- Scheduling uses APScheduler with `Asia/Kolkata` timezone; the job is a cron trigger firing Mon-Fri at 09:25.
- The `nse` library occasionally fails due to NSE rate limiting or session issues — `data_fetcher.py` has an HTTP fallback for this.
