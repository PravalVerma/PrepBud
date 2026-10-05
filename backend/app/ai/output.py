"""Provider-agnostic structured-output parsing (ADR-003: JSON mode where available,
prompt-based JSON otherwise — so the parser must tolerate fences and chatter)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.providers.base import LLMOutputError

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Return the first JSON object/array found in ``text``.

    Handles bare JSON, ```json fenced blocks, and JSON surrounded by prose.
    Raises `LLMOutputError` if nothing parseable is present.
    """
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        stripped = candidate.strip()
        try:
            return json.loads(stripped)
        except ValueError:
            pass
        for i, ch in enumerate(stripped):
            if ch in "{[":
                try:
                    value, _ = decoder.raw_decode(stripped, i)
                except ValueError:
                    continue
                return value
    raise LLMOutputError("Model output did not contain valid JSON")
