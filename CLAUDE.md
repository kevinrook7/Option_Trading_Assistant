# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

Stockester is an educational agentic AI system for analysing Indian stock market options (NSE NIFTY). It demonstrates how a LangGraph agent with a local Ollama LLM can dynamically choose tools, use persistent SQLite memory, and deliver structured analysis through Telegram.

**This system does NOT place trades or execute orders.**

---

## Architecture

```
Scheduler ──────────┐
                    │
Telegram ───────────┼──► LangGraph Agent (Ollama qwen3:8b)
                    │           │
CLI (run_agent.py) ─┘           │ decides which tools to call
                                │
              ┌─────────────────┼──────────────────────┐
              ▼                 ▼                      ▼
       get_market_snapshot  run_monte_carlo_sim  get_historical_context
       (NSE fetch, once)    (GBM simulation)     (SQLite query)
              │
              ├── find_otm_sell_candidates  (optional)
              └── find_otm_buy_candidates   (optional)
                                │
                                ▼
                           Analyst LLM
                          (structured JSON)
                                │
                             Validate
                          (Pydantic schema)
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                 SQLite                 Telegram
            (snapshot +             (if notify=True)
             prediction)
                    │
                    ▼
              Evaluation
           (evaluate_predictions.py)
```

### Key design principles

1. **One analysis brain** — scheduler, Telegram, and CLI all invoke the same LangGraph graph.
2. **NSE fetched once per run** — `get_market_snapshot` caches the chain for 5 minutes; other tools reuse it.
3. **Genuinely agentic** — the router LLM decides which tools to call and when to stop.
4. **Deterministic tools, LLM reasoning** — Python computes PCR, max pain, IV; the LLM synthesises.
5. **Structured output** — analyst produces JSON validated by Pydantic before saving/sending.
6. **SQLite as memory** — the agent can read past predictions via `get_historical_context`.

---

## LangGraph Flow

```
START
  └► router  (LLM with tools bound)
        │
        ├─ has tool_calls ─► ToolNode ─► router  (loop)
        │
        └─ no tool_calls  ─► analyst
                                │
                             validate  (Pydantic parse)
                                │
                              save     (SQLite)
                                │
                            telegram   (if notify_telegram=True)
                                │
                               END
```

---

## Commands

```bash
# Install dependencies
.venv/Scripts/pip install -r requirements.txt

# Run the scheduler (analysis 9:25 AM, evaluation 3:45 PM IST, Mon-Fri)
python main.py

# Run immediately (skips scheduler)
python main.py --now
python main.py --test

# Run CLI agent (live NSE data)
python run_agent.py

# Run CLI agent (mock data, no NSE)
python run_agent.py --mock

# Run CLI agent and send result to Telegram
python run_agent.py --telegram

# Run CLI agent with a custom query
python run_agent.py --query "What is the current NIFTY trend?"

# Run Telegram bot (listens for commands and messages)
python telegram_bot.py

# Evaluate past predictions against market outcomes
python evaluate_predictions.py

# Run tests (no NSE, no Ollama required)
.venv/Scripts/python -m pytest tests/ -v
```

---

## Available Tools

| Tool | When called | NSE fetch? |
|------|-------------|-----------|
| `get_market_snapshot` | First — always | Yes (cached) |
| `run_monte_carlo_sim` | When directional probability is useful | No (uses cached spot) |
| `get_historical_context` | When past patterns are relevant | No (SQLite) |
| `find_otm_sell_candidates` | Only if user asks about selling | No (uses cached chain) |
| `find_otm_buy_candidates` | Only if user asks about buying | No (uses cached chain) |

---

## SQLite Schema

**`market_snapshots`** — one row per NSE data fetch
- id, timestamp, symbol, spot, expiry, pcr, max_pain, iv_mean, vix, dma_20, dma_50, dma_signal

**`predictions`** — one row per agent analysis
- id, snapshot_id (FK), timestamp, symbol, stance, confidence, summary, key_evidence (JSON), uncertainties (JSON), tools_used (JSON)

**`prediction_outcomes`** — one row per (prediction × horizon)
- id, prediction_id (FK), evaluated_at, horizon_hours, future_price, price_return, correct

DB location: `data/stockester.db`

---

## Structured Output Schema

The analyst LLM outputs JSON validated by `AnalystOutput` (Pydantic):

```json
{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": 0.0–1.0,
  "summary": "2-4 sentence assessment",
  "key_evidence": ["PCR=0.8", "VIX=13.5"],
  "uncertainties": ["Low open interest data"]
}
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/scan [SYMBOL]` | Run full agent analysis (default: NIFTY) |
| `/history [SYMBOL]` | Show last 5 predictions from SQLite |
| `/performance` | Prediction accuracy by horizon (1h, 24h, 72h) |
| `/help` | List available commands |
| `<any other text>` | Sent to agent as free-form query |

Only messages from `TELEGRAM_CHAT_ID` are processed; all others are silently ignored.

---

## Configuration

**`.env`** (secrets — never commit)
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TIMEZONE=Asia/Kolkata
MOCK_NSE=true          # optional: use synthetic data instead of live NSE
```

**`config.yaml`** (non-secret settings)
```yaml
ollama:
  model: "qwen3:8b"    # change model name here, not in code
  router_tokens: 512
  analyst_tokens: 1024
  context_window: 8192

evaluation:
  horizons_hours: [1, 24, 72]
```

---

## Scheduler Behaviour

`python main.py` runs two weekday APScheduler jobs (IST):

- 09:25 — `run_scheduled_analysis()` invokes the LangGraph agent with `notify_telegram=True`. The result is saved to SQLite and sent to Telegram.
- 15:45 — `run_evaluation()` from `evaluate_predictions.py` scores every (prediction × horizon) that has become due.

---

## Prediction Evaluation

`evaluate_predictions.py` (scheduled by `main.py`, or run by hand) scores past predictions:

1. Entry price = linked `market_snapshots.spot` (fallback: yfinance price at prediction time).
2. Price at `prediction_time + horizon` is looked up from yfinance hourly bars (last bar closed by then), so late runs give the same result as on-time ones.
3. `correct=1` if direction matches stance (bullish → positive return, bearish → negative). Neutral always scores 0.
4. If no bar closed between prediction and target (market shut for the whole horizon), `correct=NULL` and it's excluded from accuracy.
5. One row per (prediction, horizon) in `prediction_outcomes` (unique index).

Timestamps are stored as ISO-8601 UTC with a `T` separator; always compare with `datetime(timestamp)` in SQL, never as raw strings.

Use `/performance` in Telegram to see accuracy statistics.

---

## How Historical Memory Works

The agent is NOT fine-tuned on historical data. Instead:

1. Each run saves a prediction to SQLite.
2. The `get_historical_context` tool queries SQLite for recent predictions and their outcomes.
3. The analyst LLM receives this summary as additional context.
4. This is persistent memory, not model weight updates.

---

## Mock Mode

Set `MOCK_NSE=true` in `.env` or run `python run_agent.py --mock` to use synthetic option chain data instead of hitting NSE. This enables offline development and testing without network access or rate-limit risk.

Mock runs are NOT saved to SQLite (the save node skips when `MOCK_NSE` is set), so synthetic predictions never get scored against real prices.

---

## Testing

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Tests:
- `tests/test_schema.py` — Pydantic schema validation
- `tests/test_database.py` — SQLite read/write with temp DB
- `tests/test_monte_carlo.py` — GBM simulation correctness
- `tests/test_tools_mock.py` — agent tools with MOCK_NSE=true

Tests do NOT require Ollama or live NSE data.

---

## What This System Does NOT Do

- Place orders or execute trades
- Connect to a broker API
- Use real-time streaming data
- Fine-tune or retrain the Ollama model
- Use vector databases or RAG
- Guarantee prediction accuracy
- Provide financial advice

---

## Module Map

```
main.py               — scheduler entry point (invokes graph)
run_agent.py          — CLI entry point
telegram_bot.py       — Telegram polling loop + command dispatcher
evaluate_predictions.py — outcome evaluation script

src/
  config_loader.py    — loads config.yaml + .env
  context.py          — computes VIX, DMA, PCR, max pain, IV (one call)
  scorer.py           — ranks OTM strikes for selling/buying
  notifier.py         — sends Telegram messages via Bot API

stockester_agent/
  agent/
    graph.py          — LangGraph graph definition
    state.py          — AgentState TypedDict
    tools.py          — LangChain @tool functions (with NSE cache)
    schema.py         — AnalystOutput Pydantic model
  tools/
    data_fetcher.py   — NSE option chain fetch + mock mode
    indicators.py     — PCR, max pain, IV, expiry helpers
    monte_carlo.py    — GBM simulation
    option_utils.py   — shared helpers (_parse_expiries, etc.)
  memory/
    database.py       — SQLite schema, reads, writes
    __init__.py       — public API

tests/
  test_schema.py
  test_database.py
  test_monte_carlo.py
  test_tools_mock.py
```

---

## Important Notes

- The virtual environment is at `.venv/` (interpreter: `.venv/Scripts/python.exe` on Windows).
- The LangGraph agent requires Ollama running locally with `qwen3:8b` pulled.
- `reasoning=False` in ChatOllama maps to Ollama `think=false` — disables qwen3 thinking tokens.
- `telegram_bot.py` and `main.py` are independent processes; run in separate terminals.
- The NSE library occasionally fails due to rate limiting — `data_fetcher.py` has an HTTP fallback.
- Each agent run makes 1 NSE request (cached) + 1-2 yfinance requests + 2 Ollama LLM passes. Expect 1-3 minutes on local hardware.
