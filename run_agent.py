"""
run_agent.py — CLI entry point for a single agent run.

Usage
-----
  python run_agent.py                        # live NSE data, no Telegram
  python run_agent.py --mock                 # synthetic data (MOCK_NSE=true)
  python run_agent.py --telegram             # send result to Telegram
  python run_agent.py --query "your text"   # custom query (default: daily scan)
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from langchain_core.messages import HumanMessage

DEFAULT_QUERY = (
    "Run a complete market analysis for NIFTY. "
    "Start with get_market_snapshot, then decide whether "
    "Monte Carlo or historical context would add useful information."
)


def main():
    args = sys.argv[1:]
    mock = "--mock" in args
    telegram = "--telegram" in args

    query_idx = next((i for i, a in enumerate(args) if a == "--query"), None)
    query = args[query_idx + 1] if query_idx is not None and query_idx + 1 < len(args) else DEFAULT_QUERY

    if mock:
        os.environ["MOCK_NSE"] = "true"
        print("[mock mode] Using synthetic NSE data.")

    from stockester_agent.agent.graph import graph
    from stockester_agent.agent.tools import clear_cache

    clear_cache()

    print(f"Starting agent run...\nQuery: {query}\n")

    result = graph.invoke(
        {
            "messages": [HumanMessage(content=query)],
            "symbol": "NIFTY",
            "notify_telegram": telegram,
            "analysis": None,
            "snapshot_id": None,
        }
    )

    # Print structured analysis
    analysis = result.get("analysis") or {}
    print("\n" + "=" * 50)
    print("ANALYSIS")
    print("=" * 50)
    print(json.dumps(analysis, indent=2))

    # Print tools used
    print("\nTOOLS CALLED")
    print("-" * 50)
    for msg in result["messages"]:
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                print(f"  {tc['name']}({tc.get('args', {})})")

    if telegram:
        print("\n[Telegram message sent]")


if __name__ == "__main__":
    main()
