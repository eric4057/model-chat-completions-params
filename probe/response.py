"""把 chat completions 的 HTTP 結果收成探測用的 result dict。"""

from __future__ import annotations

import json
from typing import Any

from probe.faults import (
    build_fault,
    clip_text,
    fault_from_body,
    malformed_fault,
    observe_reasoning,
    parse_error_object,
    provider_reasoning_text,
)
from probe.http_parse import select_diagnostic_headers

EMPTY_FIELDS = {
    "content": None,
    "reasoning": None,
    "reasoning_content": None,
    "provider_reasoning": None,
    "tool_calls": None,
    "message_keys": [],
    "finish_reason": None,
    "usage": None,
    "saw_done": False,
    "saw_usage": False,
    "saw_tool_delta": False,
}


def _provider_reasoning(msg: dict[str, Any]) -> str | None:
    fields = msg.get("provider_specific_fields")
    if not isinstance(fields, dict):
        return None
    text = provider_reasoning_text(fields.get("reasoning"))
    return text or None


def _merge_tool_delta(buckets: dict[int, dict[str, Any]], deltas: Any) -> None:
    if not isinstance(deltas, list):
        return
    for item in deltas:
        if not isinstance(item, dict):
            continue
        idx = item.get("index", 0)
        if not isinstance(idx, int):
            idx = 0
        slot = buckets.setdefault(
            idx,
            {
                "id": None,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if item.get("id"):
            slot["id"] = item["id"]
        if item.get("type"):
            slot["type"] = item["type"]
        fn = item.get("function") or {}
        if not isinstance(fn, dict):
            continue
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


def _base(
    *,
    status: int | None,
    elapsed_ms: int,
    error: str | None,
    stream: bool,
    sent: dict[str, Any],
    headers: dict[str, str],
    fault: dict[str, Any] | None,
    fields: dict[str, Any],
) -> dict[str, Any]:
    observed = observe_reasoning(fields)
    return {
        "ok": fault is None and status == 200,
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "stream": stream,
        "request": sent,
        "local_reject": False,
        "response_headers": select_diagnostic_headers(headers),
        "fault": fault,
        "reasoning_obs": observed,
        **fields,
    }


def _failure(
    *,
    status: int | None,
    elapsed_ms: int,
    error: str | None,
    stream: bool,
    sent: dict[str, Any],
    headers: dict[str, str],
    fault: dict[str, Any] | None,
) -> dict[str, Any]:
    return _base(
        status=status,
        elapsed_ms=elapsed_ms,
        error=error,
        stream=stream,
        sent=sent,
        headers=headers,
        fault=fault,
        fields=dict(EMPTY_FIELDS),
    )


def _interpret_stream(
    *,
    status: int,
    raw: str,
    elapsed_ms: int,
    sent: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            body = json.loads(stripped)
        except json.JSONDecodeError:
            body = None
        parsed = parse_error_object(body)
        if parsed is not None:
            fault = build_fault(
                status=status,
                message=str(parsed.get("message") or ""),
                error_type=parsed.get("type"),
                error_code=parsed.get("code"),
                error_param=parsed.get("param"),
                from_stream=True,
                embedded=True,
            )
            return _failure(
                status=status,
                elapsed_ms=elapsed_ms,
                error=clip_text(raw, 400),
                stream=True,
                sent=sent,
                headers=headers,
                fault=fault,
            )

    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    provider_parts: list[str] = []
    tool_buckets: dict[int, dict[str, Any]] = {}
    saw_done = False
    saw_usage = False
    saw_tool_delta = False
    finish_reason = None
    usage = None
    stream_error: dict[str, Any] | None = None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            saw_done = True
            continue
        if not chunk:
            continue
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        parsed_error = parse_error_object(obj)
        if parsed_error is not None:
            stream_error = parsed_error
            continue
        if obj.get("usage"):
            saw_usage = True
            usage = obj["usage"]
        choice = (obj.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            continue
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            continue
        if delta.get("reasoning_content"):
            reasoning_parts.append(str(delta["reasoning_content"]))
        if delta.get("reasoning"):
            reasoning_parts.append(str(delta["reasoning"]))
        if delta.get("content"):
            content_parts.append(str(delta["content"]))
        provider = _provider_reasoning(delta)
        if provider:
            provider_parts.append(provider)
        if delta.get("tool_calls"):
            saw_tool_delta = True
            _merge_tool_delta(tool_buckets, delta["tool_calls"])
    reason = "".join(reasoning_parts) or None
    provider_reasoning = "".join(provider_parts) or None
    tool_calls = [tool_buckets[i] for i in sorted(tool_buckets)] or None
    fields = {
        "content": "".join(content_parts),
        "reasoning": reason,
        "reasoning_content": reason,
        "provider_reasoning": provider_reasoning,
        "tool_calls": tool_calls,
        "message_keys": [],
        "finish_reason": finish_reason,
        "usage": usage,
        "saw_done": saw_done,
        "saw_usage": saw_usage,
        "saw_tool_delta": saw_tool_delta,
    }
    fault = None
    error = None
    if stream_error is not None:
        fault = build_fault(
            status=status,
            message=str(stream_error.get("message") or ""),
            error_type=stream_error.get("type"),
            error_code=stream_error.get("code"),
            error_param=stream_error.get("param"),
            from_stream=True,
        )
        error = clip_text(stream_error.get("message") or stream_error, 400)
    return _base(
        status=status,
        elapsed_ms=elapsed_ms,
        error=error,
        stream=True,
        sent=sent,
        headers=headers,
        fault=fault,
        fields=fields,
    )


def _interpret_json(
    *,
    status: int,
    raw: str,
    elapsed_ms: int,
    sent: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error=f"JSONDecodeError: {exc}",
            stream=False,
            sent=sent,
            headers=headers,
            fault=malformed_fault(f"回覆不是 JSON：{exc}"),
        )
    if not isinstance(parsed, dict):
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error="回覆不是 JSON object",
            stream=False,
            sent=sent,
            headers=headers,
            fault=malformed_fault("回覆不是 JSON object"),
        )
    embedded = parse_error_object(parsed)
    if embedded is not None:
        fault = build_fault(
            status=status,
            message=str(embedded.get("message") or ""),
            error_type=embedded.get("type"),
            error_code=embedded.get("code"),
            error_param=embedded.get("param"),
            embedded=True,
        )
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error=clip_text(raw, 400),
            stream=False,
            sent=sent,
            headers=headers,
            fault=fault,
        )
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error="HTTP 200 但沒有 choices",
            stream=False,
            sent=sent,
            headers=headers,
            fault=malformed_fault("HTTP 200 但沒有 choices"),
        )
    msg = choices[0].get("message") or {}
    if not isinstance(msg, dict):
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error="choices[0].message 不是 object",
            stream=False,
            sent=sent,
            headers=headers,
            fault=malformed_fault("choices[0].message 不是 object"),
        )
    fields = {
        "content": msg.get("content"),
        "reasoning": msg.get("reasoning"),
        "reasoning_content": msg.get("reasoning_content"),
        "provider_reasoning": _provider_reasoning(msg),
        "tool_calls": msg.get("tool_calls"),
        "message_keys": sorted(msg.keys()),
        "finish_reason": choices[0].get("finish_reason"),
        "usage": parsed.get("usage"),
        "saw_done": False,
        "saw_usage": False,
        "saw_tool_delta": False,
    }
    return _base(
        status=status,
        elapsed_ms=elapsed_ms,
        error=None,
        stream=False,
        sent=sent,
        headers=headers,
        fault=None,
        fields=fields,
    )


def interpret_chat_response(
    *,
    status: int | None,
    raw: str,
    elapsed_ms: int,
    error: str | None,
    headers: dict[str, str],
    curl_exit: int | None,
    stream: bool,
    sent: dict[str, Any],
) -> dict[str, Any]:
    if status is None or status != 200:
        fault = fault_from_body(
            status=status,
            raw=raw,
            transport_error=error,
            curl_exit=curl_exit,
        )
        shown = error if status is None else (error or clip_text(raw, 400))
        return _failure(
            status=status,
            elapsed_ms=elapsed_ms,
            error=shown,
            stream=stream,
            sent=sent,
            headers=headers,
            fault=fault,
        )
    if stream:
        return _interpret_stream(
            status=status,
            raw=raw,
            elapsed_ms=elapsed_ms,
            sent=sent,
            headers=headers,
        )
    return _interpret_json(
        status=status,
        raw=raw,
        elapsed_ms=elapsed_ms,
        sent=sent,
        headers=headers,
    )


_JSON_TEXT_LIMIT = 2000


def _clip_json(value: Any, limit: int = _JSON_TEXT_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, dict):
        return {str(key): _clip_json(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_json(item, limit) for item in value]
    return value


def response_document(raw: str) -> str:
    """給畫面看的回應 JSON。長文字截斷，結構保留。"""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:8000]
    return json.dumps(_clip_json(parsed), ensure_ascii=False, indent=2)
