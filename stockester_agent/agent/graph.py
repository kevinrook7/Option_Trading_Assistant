"""
LangGraph agent graph for Stockester.

Graph flow
----------
START
  -> router   (LLM decides which tools to call)
       |
       +-- has tool_calls --> ToolNode --> router  (loop)
       |
       +-- no tool_calls  --> analyst   (LLM writes structured JSON analysis)
                                |
                             validate   (Pydantic parse + schema validation)
                                |
                              save      (write snapshot + prediction to SQLite)
                                |
                            telegram    (send Telegram message if notify=True)
                                |
                               END

Key design decisions
--------------------
- Router is genuinely agentic: it decides WHICH tools to call and WHEN to stop.
  The system prompt guides it but does NOT force all tools to run every time.
- Analyst writes structured JSON (AnalystOutput schema).
- Validate node parses the JSON with Pydantic; falls back to neutral on failure.
- Save node writes market_snapshots + predictions to SQLite.
- Telegram node formats and sends the result; skipped if notify_telegram=False.
- LLM model name and token counts come from config.yaml.
"""

import json
import sys
import os

sys.path.insert(0, os.getcwd())

from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from src.config_loader import load_config
from stockester_agent.agent.state import AgentState
from stockester_agent.agent.schema import AnalystOutput
from stockester_agent.agent.tools import ALL_TOOLS, clear_cache
from stockester_agent.memory import init_db, save_snapshot, save_prediction

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

_cfg = load_config("config.yaml")
_ollama = _cfg.get("ollama", {})
_MODEL = _ollama.get("model", "qwen3:8b")
_ROUTER_TOKENS = _ollama.get("router_tokens", 512)
_ANALYST_TOKENS = _ollama.get("analyst_tokens", 1024)
_CTX = _ollama.get("context_window", 8192)

# ---------------------------------------------------------------------------
# LLMs
# Note: reasoning=False maps to Ollama think=False for qwen3 models.
# ---------------------------------------------------------------------------

router_llm = ChatOllama(
    model=_MODEL,
    temperature=0,
    num_ctx=_CTX,
    num_predict=_ROUTER_TOKENS,
    reasoning=False,
).bind_tools(ALL_TOOLS)

analyst_llm = ChatOllama(
    model=_MODEL,
    temperature=0,
    num_ctx=_CTX,
    num_predict=_ANALYST_TOKENS,
    reasoning=False,
)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_ROUTER = SystemMessage(content=(
    "You are a NIFTY options market analyst with access to financial tools.\n\n"
    "Your job is to gather enough market data to form a view, then stop calling tools.\n\n"
    "Available tools:\n"
    "  get_market_snapshot      - fetch spot, PCR, max pain, IV, VIX, DMA (call this FIRST)\n"
    "  run_monte_carlo_sim      - GBM simulation of 5-day price range\n"
    "  get_historical_context   - past predictions and accuracy from memory\n"
    "  find_otm_sell_candidates - top strikes for selling premium (optional)\n"
    "  find_otm_buy_candidates  - top strikes for buying options (optional)\n\n"
    "Rules:\n"
    "  1. Always call get_market_snapshot first.\n"
    "  2. Decide whether Monte Carlo or historical context would add useful information.\n"
    "  3. Only call find_otm_* if the user specifically asks about option strategies.\n"
    "  4. Stop calling tools once you have enough data to form a view.\n"
    "  5. Do NOT write any analysis yet — that is done by a separate analyst step."
))

SYSTEM_ANALYST = SystemMessage(content=(
    "You are a senior NIFTY options market analyst.\n\n"
    "You have received tool data from the router. Synthesise it into a structured analysis.\n\n"
    "Output ONLY a valid JSON object matching this schema — no markdown, no extra text:\n\n"
    "{\n"
    '  "stance": "bullish" | "bearish" | "neutral",\n'
    '  "confidence": <float 0.0-1.0>,\n'
    '  "summary": "<2-4 sentence market assessment>",\n'
    '  "key_evidence": ["<data point>", ...],\n'
    '  "uncertainties": ["<risk factor>", ...]\n'
    "}\n\n"
    "Guidelines:\n"
    "  - Base stance on the quantitative data, not intuition.\n"
    "  - Confidence reflects data quality and signal agreement, not certainty.\n"
    "  - key_evidence should cite specific numbers (PCR=1.2, VIX=14.5, etc.).\n"
    "  - uncertainties should note data gaps or conflicting signals.\n"
    "  - Do NOT recommend trades or positions. This is analysis only.\n"
    "  - Output valid JSON only — no text before or after the JSON object."
))

# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def call_router(state: AgentState) -> dict:
    """Agentic router: LLM decides which tools to call (or stops and goes to analyst)."""
    messages = [SYSTEM_ROUTER] + list(state["messages"])
    print("  [router] thinking...", flush=True)
    response = router_llm.invoke(messages)

    tool_names = [tc["name"] for tc in (response.tool_calls or [])]
    if tool_names:
        print(f"  [router] calling: {tool_names}", flush=True)
    else:
        print("  [router] done — moving to analyst", flush=True)

    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    """Route to ToolNode if the router made tool calls, else to analyst."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "analyst"


def call_analyst(state: AgentState) -> dict:
    """Analyst LLM: synthesises tool results into structured JSON output."""
    messages = [SYSTEM_ANALYST] + list(state["messages"])
    print("  [analyst] reasoning...", flush=True)
    response = analyst_llm.invoke(messages)
    return {"messages": [response]}


def validate_output(state: AgentState) -> dict:
    """
    Parse and validate the analyst's JSON output with Pydantic.
    Stores the validated dict in state['analysis'].
    Falls back to neutral if parsing fails.
    """
    last_content = state["messages"][-1].content
    output = AnalystOutput.parse_llm_output(last_content)
    print(
        f"  [validate] stance={output.stance.value}  confidence={output.confidence:.0%}",
        flush=True,
    )
    return {"analysis": output.model_dump()}


def save_to_db(state: AgentState) -> dict:
    """
    Save market snapshot and prediction to SQLite.
    Reads the validated analysis from state['analysis'].
    """
    analysis = state.get("analysis") or {}
    symbol = state.get("symbol", "NIFTY")

    # Collect snapshot data and tools used from tool messages
    snap_data: dict = {}
    tools_used: list = []

    for msg in state["messages"]:
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tools_used.append(tc["name"])

        if msg.__class__.__name__ == "ToolMessage" and msg.name == "get_market_snapshot":
            try:
                snap_data = json.loads(msg.content)
            except Exception:
                pass

    # Save snapshot if we have one
    snapshot_id = None
    if snap_data and "spot" in snap_data:
        snapshot_id = save_snapshot(
            symbol=symbol,
            spot=snap_data.get("spot", 0.0),
            expiry=snap_data.get("expiry"),
            pcr=snap_data.get("pcr", 0.0),
            max_pain=snap_data.get("max_pain"),
            iv_mean=snap_data.get("iv_mean", 0.0),
            vix=snap_data.get("vix"),
            dma_20=snap_data.get("dma_20"),
            dma_50=snap_data.get("dma_50"),
            dma_signal=snap_data.get("dma_signal", "N/A"),
        )

    # Save prediction
    pred_id = save_prediction(
        symbol=symbol,
        stance=analysis.get("stance", "neutral"),
        confidence=analysis.get("confidence", 0.0),
        summary=analysis.get("summary", ""),
        key_evidence=analysis.get("key_evidence", []),
        uncertainties=analysis.get("uncertainties", []),
        tools_used=tools_used,
        snapshot_id=snapshot_id,
    )
    print(f"  [save] snapshot_id={snapshot_id}  prediction_id={pred_id}", flush=True)
    return {"snapshot_id": snapshot_id}


def send_telegram(state: AgentState) -> dict:
    """
    Send the analysis as a Telegram message if notify_telegram is True.
    Uses the validated analysis from state['analysis'].
    """
    if not state.get("notify_telegram", False):
        return {}

    analysis = state.get("analysis") or {}
    try:
        output = AnalystOutput.model_validate(analysis)
        message = output.to_telegram_html()
    except Exception as e:
        message = f"Analysis complete but formatting failed: {e}"

    from src.notifier import send_telegram_message
    send_telegram_message(message)
    print("  [telegram] message sent", flush=True)
    return {}


# ---------------------------------------------------------------------------
# Build and compile the graph
# ---------------------------------------------------------------------------

init_db()

builder = StateGraph(AgentState)

builder.add_node("router",   call_router)
builder.add_node("tools",    ToolNode(ALL_TOOLS))
builder.add_node("analyst",  call_analyst)
builder.add_node("validate", validate_output)
builder.add_node("save",     save_to_db)
builder.add_node("telegram", send_telegram)

builder.set_entry_point("router")

builder.add_conditional_edges(
    "router",
    should_continue,
    {"tools": "tools", "analyst": "analyst"},
)
builder.add_edge("tools",    "router")    # loop: tools -> router
builder.add_edge("analyst",  "validate")  # analyst -> validate
builder.add_edge("validate", "save")      # validate -> save
builder.add_edge("save",     "telegram")  # save -> telegram
builder.add_edge("telegram", END)

graph = builder.compile()
