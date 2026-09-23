"""
SQLite persistence layer — agent memory, predictions, and outcome evaluation.

Tables
------
market_snapshots    : one row per NSE data fetch (spot, PCR, IV, VIX, DMA, ...)
predictions         : one row per agent analysis, linked to a snapshot
prediction_outcomes : one row per (prediction x horizon) evaluation

The agent can READ from this database via get_historical_context_data().
Writing happens in the LangGraph save node.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path("./data/stockester.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    spot        REAL,
    expiry      TEXT,
    pcr         REAL,
    max_pain    REAL,
    iv_mean     REAL,
    vix         REAL,
    dma_20      REAL,
    dma_50      REAL,
    dma_signal  TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    INTEGER,
    timestamp      TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    stance         TEXT    NOT NULL,
    confidence     REAL,
    summary        TEXT,
    key_evidence   TEXT,
    uncertainties  TEXT,
    tools_used     TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id)
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   INTEGER NOT NULL,
    evaluated_at    TEXT    NOT NULL,
    horizon_hours   INTEGER NOT NULL,
    future_price    REAL,
    price_return    REAL,
    correct         INTEGER,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
"""

# Columns to add to the legacy predictions table if they are missing
_LEGACY_COLUMNS = [
    ("predictions", "key_evidence",  "TEXT",    "NULL"),
    ("predictions", "uncertainties", "TEXT",    "NULL"),
    ("predictions", "tools_used",    "TEXT",    "NULL"),
    ("predictions", "snapshot_id",   "INTEGER", "NULL"),
]


def init_db() -> None:
    """Create tables and apply any missing columns. Safe to call multiple times."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)

        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
        }
        for table, col, col_type, default in _LEGACY_COLUMNS:
            if col not in existing_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}"
                    )
                except sqlite3.OperationalError:
                    pass

    print("✅ Database initialized")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def save_snapshot(
    symbol: str,
    spot: float,
    expiry: Optional[str],
    pcr: float,
    max_pain: Optional[float],
    iv_mean: float,
    vix: Optional[float],
    dma_20: Optional[float],
    dma_50: Optional[float],
    dma_signal: str,
) -> int:
    """Insert a market snapshot row and return its id."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO market_snapshots
                (timestamp, symbol, spot, expiry, pcr, max_pain, iv_mean,
                 vix, dma_20, dma_50, dma_signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                symbol, spot, expiry, pcr, max_pain, iv_mean,
                vix, dma_20, dma_50, dma_signal,
            ),
        )
        return cur.lastrowid


def save_prediction(
    symbol: str,
    stance: str,
    confidence: float,
    summary: str,
    key_evidence: list,
    uncertainties: list,
    tools_used: list,
    snapshot_id: Optional[int] = None,
) -> int:
    """Insert a prediction row and return its id."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO predictions
                (snapshot_id, timestamp, symbol, stance, confidence, summary,
                 key_evidence, uncertainties, tools_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                datetime.now(timezone.utc).isoformat(),
                symbol,
                stance,
                confidence,
                summary,
                json.dumps(key_evidence),
                json.dumps(uncertainties),
                json.dumps(tools_used),
            ),
        )
        return cur.lastrowid


def save_outcome(
    prediction_id: int,
    horizon_hours: int,
    future_price: float,
    price_return: float,
    correct: Optional[int],
) -> None:
    """Insert or replace an outcome row for a given prediction and horizon."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO prediction_outcomes
                (prediction_id, evaluated_at, horizon_hours, future_price,
                 price_return, correct)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prediction_id,
                datetime.now(timezone.utc).isoformat(),
                horizon_hours,
                future_price,
                price_return,
                correct,
            ),
        )


# ---------------------------------------------------------------------------
# Reads  (used by the historical context tool and /performance command)
# ---------------------------------------------------------------------------

def get_historical_context_data(symbol: str, lookback_days: int = 30) -> dict:
    """
    Query recent predictions and outcomes for a symbol.

    Returns:
        total_predictions   int
        with_outcomes       int
        accuracy            float | None
        avg_confidence      float | None
        stance_counts       {bullish, bearish, neutral: int}
        recent              list of the 5 most recent predictions
    """
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.timestamp, p.stance, p.confidence, p.summary,
                   o.correct, o.price_return, o.horizon_hours
            FROM predictions p
            LEFT JOIN prediction_outcomes o ON o.prediction_id = p.id
            WHERE p.symbol = ?
              AND p.timestamp >= datetime('now', '-{lookback_days} days')
            ORDER BY p.timestamp DESC
            """,
            (symbol,),
        ).fetchall()

    if not rows:
        return {
            "total_predictions": 0,
            "with_outcomes": 0,
            "accuracy": None,
            "avg_confidence": None,
            "stance_counts": {"bullish": 0, "bearish": 0, "neutral": 0},
            "recent": [],
        }

    total = len(rows)
    evaluated = [r for r in rows if r["correct"] is not None]
    correct_count = sum(1 for r in evaluated if r["correct"] == 1)
    accuracy = correct_count / len(evaluated) if evaluated else None
    avg_conf = sum(r["confidence"] or 0.0 for r in rows) / total

    stance_counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for r in rows:
        s = (r["stance"] or "neutral").lower()
        if s in stance_counts:
            stance_counts[s] += 1

    recent = [
        {
            "timestamp": r["timestamp"],
            "stance": r["stance"],
            "confidence": r["confidence"],
            "correct": r["correct"],
            "return_pct": (
                round(r["price_return"] * 100, 2)
                if r["price_return"] is not None
                else None
            ),
        }
        for r in rows[:5]
    ]

    return {
        "total_predictions": total,
        "with_outcomes": len(evaluated),
        "accuracy": round(accuracy, 3) if accuracy is not None else None,
        "avg_confidence": round(avg_conf, 3),
        "stance_counts": stance_counts,
        "recent": recent,
    }


def get_recent_predictions(limit: int = 10) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_unevaluated_predictions(horizon_hours: int) -> list:
    """
    Return predictions old enough for the given horizon that have no outcome yet.
    Used by evaluate_predictions.py.
    """
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.*
            FROM predictions p
            WHERE p.timestamp <= datetime('now', '-{horizon_hours} hours')
              AND NOT EXISTS (
                  SELECT 1 FROM prediction_outcomes o
                  WHERE o.prediction_id = p.id
                    AND o.horizon_hours = ?
              )
            ORDER BY p.timestamp ASC
            """,
            (horizon_hours,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_performance_summary(symbol: str) -> dict:
    """Return accuracy stats per horizon — used by /performance Telegram command."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.horizon_hours,
                   COUNT(*) as total,
                   SUM(o.correct) as correct_sum,
                   AVG(o.price_return) as avg_return
            FROM prediction_outcomes o
            JOIN predictions p ON p.id = o.prediction_id
            WHERE p.symbol = ?
              AND o.correct IS NOT NULL
            GROUP BY o.horizon_hours
            ORDER BY o.horizon_hours
            """,
            (symbol,),
        ).fetchall()

    return {
        r["horizon_hours"]: {
            "total": r["total"],
            "correct": r["correct_sum"] or 0,
            "accuracy": round((r["correct_sum"] or 0) / r["total"], 3),
            "avg_return_pct": round((r["avg_return"] or 0) * 100, 2),
        }
        for r in rows
    }


if __name__ == "__main__":
    init_db()
    print(f"DB location: {DB_PATH.resolve()}")
