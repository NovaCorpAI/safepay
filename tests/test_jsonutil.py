"""Tests for the tolerant JSON extraction used on model replies."""

from __future__ import annotations

import pytest

from safepay.jsonutil import loads_lenient


def test_plain_object():
    assert loads_lenient('{"risk": 0.2, "aligned": true}') == {
        "risk": 0.2, "aligned": True
    }


def test_code_fence():
    raw = '```json\n{"legitimate": false, "risk": 0.9, "reason": "scam reports"}\n```'
    out = loads_lenient(raw)
    assert out["legitimate"] is False and out["risk"] == 0.9


def test_thinking_trace_before_object():
    raw = 'Let me think {not json here\n Final answer:\n{"aligned": false, "risk": 0.8, "reason": "mismatch"}'
    out = loads_lenient(raw)
    assert out["aligned"] is False and out["reason"] == "mismatch"


def test_regex_fallback_on_broken_json():
    # A value split across lines makes json.loads fail; the regex fallback recovers.
    raw = '{\n "aligned": false,\n "risk": 0.7,\n "reason": "goal\nmismatch"\n}'
    out = loads_lenient(raw)
    assert out["aligned"] is False
    assert out["risk"] == 0.7


def test_raises_when_no_json():
    with pytest.raises(ValueError):
        loads_lenient("no json at all here")
