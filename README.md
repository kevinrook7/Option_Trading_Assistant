# Stockester

An agentic AI system that analyses NSE NIFTY options and gives a structured market view. It runs on a local LLM through Ollama. A LangGraph agent decides which analysis tools to call, combines the results into a validated JSON verdict, saves it to SQLite and sends it to Telegram. Each prediction is later checked against what the market actually did.

> **Educational project. Not financial advice.** Stockester does not place trades, connect to a broker or guarantee accuracy.

---

## Features

- **Agentic tool use:** the router LLM chooses which tools to call and when to stop. No step runs on a fixed schedule.
- **Deterministic tools:** Python computes PCR, max pain, implied volatility, India VIX, moving averages and Monte Carlo probabilities. The LLM only interprets the numbers.
- **Structured output:** the analyst's JSON is checked by a Pydantic schema, with a safe fallback if it fails.
- **Persistent memory:** past predictions and their outcomes are stored in SQLite, and the agent can read them back as context.
- **Self-evaluation:** each prediction is scored at 1h, 24h and 72h against real NIFTY prices.
- **Three ways to run it:** a weekday scheduler, a two-way Telegram bot and a CLI, all using the same agent graph.
- **Runs locally:** uses Ollama (`qwen3:8b` by default) with no paid LLM APIs.
- **Mock mode:** synthetic option chain data for offline development and tests.

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
       (NSE fetch, cached)  (GBM simulation)     (SQLite query)
              │
              ├── find_otm_sell_candidates  (optional)
              └── find_otm_buy_candidates   (optional)
                                │
                                ▼
                     Analyst LLM → Pydantic validate
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
                 SQLite                 Telegram
                    │
                    ▼
       evaluate_predictions.py  (scores at 1h / 24h / 72h)
```

### Agent flow (LangGraph)

```
START → router ──(tool calls)──► ToolNode ──► router   (loop)
           │
           └──(done)──► analyst → validate → save → telegram → END
```

---

## Tech stack

| Area | Tools |
|---|---|
| Agent orchestration | LangGraph, LangChain |
| LLM | Ollama + `qwen3:8b` (thinking disabled) |
| Validation | Pydantic v2 |
| Market data | `nse` library (with HTTP fallback), yfinance |
| Numerics | pandas, NumPy |
| Storage | SQLite |
| Scheduling | APScheduler, pytz |
| Messaging | Telegram Bot API |
| Config | YAML, python-dotenv |
| Testing | pytest |

---

## Tools available to the agent

| Tool | Purpose |
|---|---|
| `get_market_snapshot` | Fetches the NSE option chain and computes spot, PCR, max pain, IV, VIX and the 20/50-day moving averages. Called first and cached for 5 minutes. |
| `run_monte_carlo_sim` | Geometric Brownian Motion simulation of NIFTY over the next few days, giving the probability of an up or down move. |
| `get_historical_context` | Recent predictions, stance counts and evaluated accuracy from SQLite. |
| `find_otm_sell_candidates` | Ranks out-of-the-money strikes for selling premium. Only called when the user asks about selling. |
| `find_otm_buy_candidates` | Ranks strikes for buying options. Only called when the user asks about buying. |

---

## Example output

```json
{
  "stance": "neutral",
  "confidence": 0.75,
  "summary": "NIFTY shows neutral sentiment with PCR and DMA signals, but Monte Carlo suggests slight downside bias.",
  "key_evidence": [
    "PCR=1.029 (Neutral)",
    "Monte Carlo probability_down=55.64%",
    "DMA_20=22678.42"
  ],
  "uncertainties": [
    "No recent historical predictions",
    "Monte Carlo is probabilistic, not deterministic"
  ]
}
```

---

## Getting started

### Prerequisites

- Python 3.12
- [Ollama](https://ollama.com) installed and running
- A Telegram bot token from [@BotFather](https://t.me/BotFather), only needed for Telegram features

### Setup

```bash
git clone https://github.com/kevinrook7/Option_Trading_Assistant.git
cd Option_Trading_Assistant

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
ollama pull qwen3:8b
```

Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TIMEZONE=Asia/Kolkata
# MOCK_NSE=true   # optional: use synthetic data instead of live NSE
```

Non-secret settings, such as the model name, token limits, simulation size and evaluation horizons, are in `config.yaml`.

---

## Usage

```bash
# One-off analysis from the CLI (live NSE data)
python run_agent.py

# Offline run with synthetic data (not saved to the database)
python run_agent.py --mock

# Custom question
python run_agent.py --query "What is the current NIFTY trend?"

# Send the result to Telegram
python run_agent.py --telegram

# Scheduler: analysis at 09:25 IST, evaluation at 15:45 IST, Mon–Fri
python main.py

# Run the scheduled analysis once, right now
python main.py --now

# Two-way Telegram bot
python telegram_bot.py

# Score past predictions by hand
python evaluate_predictions.py
```

`main.py` and `telegram_bot.py` are separate processes. Run them in separate terminals. Each agent run takes about 1–3 minutes on local hardware.

### Telegram commands

| Command | Description |
|---|---|
| `/scan [SYMBOL]` | Run a full agent analysis (default `NIFTY`) |
| `/history [SYMBOL]` | Show the last 5 predictions and their results |
| `/performance` | Accuracy by horizon (1h, 24h, 72h) |
| `/help` | List commands |
| any other text | Sent to the agent as a free-form question |

The bot only responds to the chat set in `TELEGRAM_CHAT_ID`.

---

## How evaluation works

For each prediction that has reached a horizon (1h, 24h or 72h):

1. **Entry price:** NIFTY spot from the market snapshot saved with the prediction.
2. **Exit price:** the NIFTY hourly close at *prediction time + horizon*, from yfinance. Because the price is looked up historically, a late run gives the same result as an on-time one.
3. **Bullish** is correct if the price rose and **bearish** is correct if it fell. **Neutral** currently always counts as incorrect.
4. If the market was closed for the whole horizon, the outcome is left unscored and doesn't affect accuracy.

The agent does not learn by fine-tuning. "Memory" means past predictions and outcomes are read back from SQLite through `get_historical_context`.

---

## Project structure

```
main.py                   scheduler entry point
run_agent.py              CLI entry point
telegram_bot.py           Telegram polling bot
evaluate_predictions.py   outcome scoring
config.yaml               non-secret settings

src/
  config_loader.py        loads config.yaml + .env
  context.py              VIX, moving averages, PCR, max pain, IV
  scorer.py               OTM strike ranking
  notifier.py             Telegram sender

stockester_agent/
  agent/                  LangGraph graph, state, tools, Pydantic schema
  tools/                  NSE fetcher, indicators, Monte Carlo, helpers
  memory/                 SQLite schema, reads and writes

tests/                    pytest suite (no Ollama or NSE required)
```

---

## Testing

```bash
python -m pytest tests/ -v
```

The tests use mock data and a temporary database, so they don't need Ollama, network access or live NSE data.

---

## Known limitations and roadmap

- [ ] Fix the implied volatility calculation. It currently averages every strike and expiry and should use near-expiry, near-the-money strikes.
- [ ] Score neutral predictions fairly, for example correct when the move is under ±0.5%.
- [ ] Skip NSE market holidays in the scheduler.
- [ ] Compare accuracy against simple baselines (always bullish, Monte Carlo direction).
- [ ] Support more than one index (`config.yaml` accepts a list, but only the first entry is used).
- [ ] Pin dependency versions.

The NSE site rate-limits requests. `data_fetcher.py` falls back to direct HTTP when the library call fails.

---

## Disclaimer

This project is for learning and research only. Its output is not investment advice. Options trading carries significant risk. Do your own research or consult a licensed advisor before making any financial decision.
