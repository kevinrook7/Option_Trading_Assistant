# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stockester is a Python tool for analyzing Indian stock market options (NSE NIFTY). It has two coexisting systems:

1. **`main.py` — scheduled pipeline** that fetches live NSE option chain data, scores sell/buy opportunities, and sends a Telegram alert at 9:25 AM IST on weekdays.
2. **`stockester_agent/` — LangGraph agent** that uses a local Ollama LLM (qwen3:8b) to reason over the same market data and produce a Bullish/Bearish/Neutral stance, saved to SQLite.

## Commands

```bash
# Install dependencies (use the project venv)
.venv/Scripts/pip install -r requirements.txt   # Windows

# Run the scheduled pipeline (fires Mon-Fri at 9:25 AM IST)
python main.py

# Run the pipeline immediately (skips the scheduler)
python main.py --now
python main.py --test

# Run the LangGraph agent (requires Ollama running locally with qwen3:8b)
python run_agent.py
```

There is no real test suite. `test_nse.py`, `test_agent.py`, `test_tools.py`, `test_no_think.py` are throwaway sanity checks.

## Architecture

### System 1: `main.py` pipeline

`main.py` orchestrates a linear analysis in `run_daily_analysis()`:

```
fetch_option_data → get_market_context → run_monte_carlo → rank_strikes / rank_strikes_buy → notifier
```

**Module responsibilities (`src/`):**
- `config_loader.py` — loads `config.yaml` (PyYAML) and `.env` secrets (python-dotenv)
- `context.py` — fetches India VIX and NIFTY 20/50-DMA via `yfinance`; computes PCR, Max Pain, and mean IV; returns a single market context dict
- `scorer.py` — ranks OTM NIFTY strikes for selling (monthly expiry, last Tuesday) and buying (nearest weekly expiry); scoring is a weighted sum of IV, distance-OTM, and OI using weights from `config.yaml`
- `notifier.py` — sends HTML-formatted Telegram alert via Bot API

> **Note:** `src/context.py` still imports from `src/indicators.py` which has been deleted. This path is broken and needs to be updated to import from `stockester_agent/tools/indicators.py`.

### System 2: `stockester_agent/` (LangGraph agent)

Entry point: `run_agent.py`

**Graph flow:** `router → tools → analyst → save → END`

- `router` loops, calling tools until all market data is collected
- `tools` (ToolNode) executes whichever LangChain tools the router requests
- `analyst` writes a structured market assessment ending with `STANCE: <...>` and `CONFIDENCE: <...>`
- `save` parses the analyst output and persists to SQLite

**Module responsibilities (`stockester_agent/`):**
- `agent/graph.py` — defines the LangGraph state graph; two Ollama LLMs: fast router (256 tokens) and slow analyst (1024 tokens), both `qwen3:8b`
- `agent/state.py` — `AgentState`: just a messages list with LangGraph `add_messages` reducer
- `agent/tools.py` — five LangChain `@tool` functions: `get_nifty_chain`, `get_pcr`, `get_max_pain`, `get_iv`, `get_monte_carlo`
- `tools/data_fetcher.py` — fetches NSE option chain (nse library + HTTP fallback)
- `tools/indicators.py` — computes PCR, Max Pain, mean IV, expiry helpers
- `tools/monte_carlo.py` — 5000-iteration GBM simulation; returns probability of NIFTY finishing up over 5 days
- `tools/option_utils.py` — shared helpers: `_parse_expiries`, `_otm_filter`, `_liquidity_filter`
- `memory/database.py` — SQLite schema and helpers; stores predictions and outcomes tables at `data/stockester.db`

## Data / Config

- `config.yaml` — indices list, simulation params, scoring weights, `top_n_to_notify`
- `data/opt-expiry.json` — cached NSE option expiry dates
- `data/stockester.db` — SQLite database (agent predictions and outcomes)
- `.env` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TIMEZONE=Asia/Kolkata`

## Important Notes

- The virtual environment is at `.venv/` (Python interpreter: `.venv/Scripts/python.exe` on Windows).
- The `main.py` pipeline scheduler uses APScheduler with `Asia/Kolkata` timezone, firing Mon-Fri at 09:25.
- The LangGraph agent requires Ollama running locally with `qwen3:8b` pulled.
- The `nse` library occasionally fails due to NSE rate limiting — `stockester_agent/tools/data_fetcher.py` has an HTTP fallback to the `nseindia.com` API.
- `src/context.py` has a broken import (`from src.indicators import ...`) — `src/indicators.py` was deleted and its logic moved to `stockester_agent/tools/indicators.py`.
