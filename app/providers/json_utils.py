"""JSON extraction helpers for LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.exceptions import AiParseError


def extract_json_object(raw: str) -> Any:
    trimmed = raw.strip()
    if not trimmed:
        raise AiParseError("AI returned an empty response")

    def try_parse(value: str) -> Any | None:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    direct = try_parse(trimmed)
    if direct is not None:
        return direct

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", trimmed, flags=re.IGNORECASE)
    if fenced and fenced.group(1):
        parsed = try_parse(fenced.group(1).strip())
        if parsed is not None:
            return parsed

    start = trimmed.find("{")
    if start >= 0:
        end = trimmed.rfind("}")
        if end > start:
            parsed = try_parse(trimmed[start : end + 1])
            if parsed is not None:
                return parsed
        repaired = _repair_truncated_json(trimmed[start:])
        if repaired is not None:
            return repaired

    raise AiParseError("Unable to parse AI JSON response")


def _repair_truncated_json(fragment: str) -> Any | None:
    candidate = fragment.strip()
    candidate = re.sub(r',\s*("[^"]*)?$', "", candidate)
    candidate = re.sub(r':\s*("[^"]*)?$', ": null", candidate)

    if candidate.count('"') % 2 != 0:
        candidate += '"'

    open_arr = candidate.count("[")
    close_arr = candidate.count("]")
    if open_arr > close_arr:
        candidate += "]" * (open_arr - close_arr)

    opens = candidate.count("{")
    closes = candidate.count("}")
    if opens > closes:
        candidate += "}" * (opens - closes)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
