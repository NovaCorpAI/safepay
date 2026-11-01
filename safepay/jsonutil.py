"""Tolerant JSON extraction for model replies.

Reasoning models do not always return clean JSON: they may wrap it in a code
fence, prepend a short thinking trace, or split a value across lines. This
helper recovers the intended object: it strips fences, scans for the last
complete top-level ``{...}`` object and parses that, and as a last resort pulls
the known fields out with regexes so a stray newline never sinks a decision.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        # drop the opening fence (``` or ```json) and the trailing fence
        raw = re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _last_object(raw: str) -> str | None:
    """Return the last balanced top-level {...} substring, if any."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    spans.append((start, i + 1))
    if not spans:
        return None
    s, e = spans[-1]
    return raw[s:e]


def _regex_fields(raw: str) -> dict[str, Any]:
    """Last-resort field puller for the small, known key sets we ask for."""
    out: dict[str, Any] = {}
    for key in ("aligned", "legitimate"):
        m = re.search(rf'"{key}"\s*:\s*(true|false)', raw, re.IGNORECASE)
        if m:
            out[key] = m.group(1).lower() == "true"
    m = re.search(r'"risk"\s*:\s*([0-9]*\.?[0-9]+)', raw)
    if m:
        out["risk"] = float(m.group(1))
    m = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)
    if m:
        out["reason"] = m.group(1).replace("\\n", " ").strip()
    return out


def loads_lenient(raw: str) -> dict[str, Any]:
    """Best-effort parse of a model reply into a dict. Raises only if nothing works."""
    text = _strip_fences(raw)
    for candidate in (text, _last_object(text)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    fields = _regex_fields(text)
    if fields:
        return fields
    raise ValueError(f"no JSON object found in model reply: {raw[:120]!r}")
