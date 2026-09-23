"""
LangGraph agent state definition.

AgentState carries everything the graph nodes need:
  messages         - the LangChain message history (tool calls + results)
  symbol           - the market symbol being analysed (default NIFTY)
  notify_telegram  - if True, the telegram node sends an alert
  analysis         - the validated AnalystOutput dict (set by validate node)
  snapshot_id      - SQLite id of the saved market_snapshot (set by save node)
"""

from typing import Annotated, Optional, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    symbol: str
    notify_telegram: bool
    analysis: Optional[dict]    # populated by validate node
    snapshot_id: Optional[int]  # populated by save node
