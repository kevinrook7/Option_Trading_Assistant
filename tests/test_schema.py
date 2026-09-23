"""
Tests for the AnalystOutput Pydantic schema.
No NSE, no Ollama, no network calls.
"""

import pytest
from stockester_agent.agent.schema import AnalystOutput, Stance


def test_valid_bullish():
    out = AnalystOutput(
        stance=Stance.bullish,
        confidence=0.72,
        summary="Market looks strong.",
        key_evidence=["PCR=0.8", "VIX=13"],
        uncertainties=["Low liquidity"],
    )
    assert out.stance == Stance.bullish
    assert out.confidence == 0.72


def test_confidence_bounds():
    with pytest.raises(Exception):
        AnalystOutput(stance=Stance.neutral, confidence=1.5, summary="x")
    with pytest.raises(Exception):
        AnalystOutput(stance=Stance.neutral, confidence=-0.1, summary="x")


def test_invalid_stance():
    with pytest.raises(Exception):
        AnalystOutput(stance="very_bullish", confidence=0.5, summary="x")


def test_parse_clean_json():
    text = '{"stance": "bearish", "confidence": 0.6, "summary": "test", "key_evidence": [], "uncertainties": []}'
    out = AnalystOutput.parse_llm_output(text)
    assert out.stance == Stance.bearish
    assert out.confidence == 0.6


def test_parse_markdown_json():
    text = "```json\n{\"stance\": \"neutral\", \"confidence\": 0.4, \"summary\": \"mixed\", \"key_evidence\": [\"PCR=1.0\"], \"uncertainties\": []}\n```"
    out = AnalystOutput.parse_llm_output(text)
    assert out.stance == Stance.neutral


def test_parse_fallback():
    out = AnalystOutput.parse_llm_output("This is just free text with no JSON.")
    assert out.stance == Stance.neutral
    assert out.confidence == 0.0


def test_telegram_format():
    out = AnalystOutput(
        stance=Stance.bullish,
        confidence=0.8,
        summary="Strong bullish momentum.",
        key_evidence=["PCR below 0.8"],
        uncertainties=["High VIX"],
    )
    html = out.to_telegram_html()
    assert "BULLISH" in html
    assert "80%" in html
    assert "PCR below 0.8" in html
    assert "High VIX" in html
    assert "not financial advice" in html
