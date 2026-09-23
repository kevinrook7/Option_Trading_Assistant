"""
Pydantic schema for structured analyst output.

The analyst LLM is asked to return JSON matching this schema.
The validate node parses and validates it before saving/sending.
"""

import json
import re
from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class Stance(str, Enum):
    bullish = "bullish"
    bearish = "bearish"
    neutral = "neutral"


class AnalystOutput(BaseModel):
    """Structured market analysis output."""

    stance: Stance = Field(description="Market stance: bullish, bearish, or neutral")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence level from 0.0 to 1.0"
    )
    summary: str = Field(description="Concise market assessment (2-4 sentences)")
    key_evidence: List[str] = Field(
        default_factory=list,
        description="Key data points supporting the stance",
    )
    uncertainties: List[str] = Field(
        default_factory=list,
        description="Risk factors or data gaps that could invalidate the stance",
    )

    def to_telegram_html(self) -> str:
        """Format the analysis as an HTML Telegram message."""
        icon = {"bullish": "&#128200;", "bearish": "&#128201;", "neutral": "&#9878;"}[
            self.stance.value
        ]
        pct = f"{self.confidence:.0%}"

        lines = [
            f"<b>NIFTY Market Analysis</b> {icon}",
            f"Stance: <b>{self.stance.value.upper()}</b>  |  Confidence: <b>{pct}</b>",
            "",
            "<b>Summary</b>",
            self.summary,
        ]

        if self.key_evidence:
            lines.append("")
            lines.append("<b>Key Evidence</b>")
            for item in self.key_evidence:
                lines.append(f"  - {item}")

        if self.uncertainties:
            lines.append("")
            lines.append("<b>Uncertainties</b>")
            for item in self.uncertainties:
                lines.append(f"  ! {item}")

        lines.append("")
        lines.append("<i>Educational only — not financial advice.</i>")
        return "\n".join(lines)

    @classmethod
    def parse_llm_output(cls, text: str) -> "AnalystOutput":
        """
        Extract and validate JSON from LLM text output.
        Handles markdown code blocks. Falls back to neutral on parse failure.
        """
        cleaned = text.strip()

        # Strip markdown code fences if present
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
            if match:
                cleaned = match.group(1).strip()

        # Try direct parse
        try:
            data = json.loads(cleaned)
            return cls.model_validate(data)
        except Exception:
            pass

        # Try to find a JSON object anywhere in the text
        try:
            match = re.search(r"\{[\s\S]+\}", cleaned)
            if match:
                data = json.loads(match.group(0))
                return cls.model_validate(data)
        except Exception:
            pass

        # Last resort: return neutral with a parse-failure note
        return cls(
            stance=Stance.neutral,
            confidence=0.0,
            summary="Analysis could not be parsed from LLM output.",
            key_evidence=[],
            uncertainties=["LLM output did not match expected JSON schema."],
        )
