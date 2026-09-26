"""OpenRouter JSON-schema request helper."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request


def parse_json(text: str | None) -> dict:
    if not text:
        raise ValueError("Model returned an empty response")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = json.loads(text[text.index("{"):text.rindex("}") + 1])
    if not isinstance(value, dict):
        raise ValueError("Model response is not a JSON object")
    return value


def json_payload(
    *, model: str, system_prompt: str, user_prompt: str,
    schema_name: str, schema: dict, max_tokens: int,
    reasoning_effort: str | None = None,
) -> dict:
    """Build the chat-completions request body."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        "provider": {"require_parameters": True},
    }
    if not reasoning_effort:
        payload["temperature"] = 0
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
    return payload


def request_json(
    *, api_key: str, base_url: str, model: str, system_prompt: str,
    user_prompt: str, schema_name: str, schema: dict, max_tokens: int,
    usage: dict[str, float] | None = None, reasoning_effort: str | None = None,
) -> dict:
    payload = json_payload(
        model=model, system_prompt=system_prompt, user_prompt=user_prompt,
        schema_name=schema_name, schema=schema, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.load(response)
            if usage is not None:
                cost = (result.get("usage") or {}).get("cost", 0)
                usage["cost"] = usage.get("cost", 0) + float(cost or 0)
            return parse_json(result["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error = f"HTTP {exc.code}: {body}"
            if 400 <= exc.code < 500 and exc.code not in {408, 409, 429}:
                break
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            error = exc
        if attempt < 3:
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError(f"LLM request failed: {error}")
