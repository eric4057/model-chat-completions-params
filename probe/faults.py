"""部署症狀：錯誤來源、思考漏欄、vLLM /server_info 旗標摘取。

只看客戶端拿得到的 HTTP／body。不把成功回覆的全文拿去對關鍵字，避免模型作文裡出現 Traceback 被誤判。
"""

from __future__ import annotations

import json
import re
from typing import Any

THOUGHT_MARKERS = (
    "【思考過程】",
    "思考過程：",
    "思考過程:",
    "<think>",
    "</think>",
    "<|channel>thought",
    "<|think|>",
)
TOOL_MARKERS = (
    "<tool_call>",
    "</tool_call>",
    "<|tool_call|>",
    "<|tool_call|",
)

CLASS_ZH = {
    "transport_timeout": "連線逾時",
    "upstream_closed": "上游斷線",
    "auth": "認證失敗",
    "rate_limit": "限流",
    "gateway_param": "閘道參數未放行",
    "gateway_bug": "LiteLLM 錯誤",
    "vllm_engine": "vLLM 引擎錯誤",
    "client_reject": "客戶端拒絕",
    "server_error": "伺服器錯誤",
    "stream_error": "串流中途錯誤",
    "embedded_error": "HTTP 200 但 body 是 error",
    "malformed_response": "回覆無法解析",
}

# 這些類別代表這槍不是一份正常完成。pass／noop 要改成 fail。
# client_reject 不在這裡：官方應拒的 4xx 本來就是 pass。
OVERRIDE_VERDICT = frozenset(
    {
        "transport_timeout",
        "upstream_closed",
        "auth",
        "rate_limit",
        "gateway_param",
        "gateway_bug",
        "vllm_engine",
        "server_error",
        "stream_error",
        "embedded_error",
        "malformed_response",
    }
)
_SPECIFIC = frozenset(
    {
        "transport_timeout",
        "upstream_closed",
        "auth",
        "rate_limit",
        "gateway_param",
        "gateway_bug",
        "vllm_engine",
    }
)
_UNSET_FLAGS = frozenset({"", "none", "null"})
_FLAG_NAMES = ("reasoning_parser", "tool_call_parser", "reasoning_backend")


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return ""


def content_has_thought(content: Any) -> bool:
    text = as_text(content)
    return any(marker in text for marker in THOUGHT_MARKERS)


def content_has_tool_call(content: Any) -> bool:
    text = as_text(content)
    return any(marker in text for marker in TOOL_MARKERS)


def provider_reasoning_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return as_text(value).strip()
    if isinstance(value, dict):
        return as_text(value.get("text") or value.get("content") or "").strip()
    return ""


def observe_reasoning(result: dict[str, Any]) -> dict[str, Any]:
    """思考出現在哪個欄位。field 優先；截斷只在完全看不到思考時成立。"""
    field = bool(as_text(result.get("reasoning") or result.get("reasoning_content")).strip())
    provider = bool(provider_reasoning_text(result.get("provider_reasoning")))
    leaked = content_has_thought(result.get("content"))
    truncated = result.get("finish_reason") == "length"
    if field:
        kind = "field"
    elif provider:
        kind = "provider_only"
    elif leaked:
        kind = "content_leak"
    elif truncated:
        kind = "truncated"
    else:
        kind = "absent"
    return {
        "kind": kind,
        "truncated": truncated,
        "content_leak": leaked,
        "provider_only": provider and not field,
    }


def parse_error_object(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict) or "error" not in body:
        return None
    err = body["error"]
    if isinstance(err, str):
        return {"message": err, "type": None, "code": None, "param": None}
    if isinstance(err, dict):
        return {
            "message": str(err.get("message") or ""),
            "type": err.get("type"),
            "code": err.get("code"),
            "param": err.get("param"),
        }
    return None


def _is_gateway_param(low: str) -> bool:
    return (
        "unexpected keyword" in low
        or "unsupportedparamserror" in low
        or "extra inputs are not permitted" in low
    )


def _is_vllm(text: str, low: str) -> bool:
    if "EngineCore" in text or "reasoning parser" in low or "Traceback" in text:
        return True
    return "vllm.entrypoints" in low or "vllm.engine" in low


def _classify_core(*, status: int | None, message: str, curl_exit: int | None) -> str | None:
    text = message or ""
    low = text.lower()
    if curl_exit == 28 or "curl timeout" in low or "operation timed out" in low:
        return "transport_timeout"
    if status is None:
        return "upstream_closed"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if _is_gateway_param(low):
        return "gateway_param"
    if _is_vllm(text, low):
        return "vllm_engine"
    if status >= 500 and "litellm" in low:
        return "gateway_bug"
    if 400 <= status <= 499:
        return "client_reject"
    if status >= 500:
        return "server_error"
    return None


def classify_failure(
    *,
    status: int | None,
    message: str = "",
    curl_exit: int | None = None,
    from_stream: bool = False,
    embedded: bool = False,
) -> str | None:
    kind = _classify_core(status=status, message=message, curl_exit=curl_exit)
    if from_stream:
        return kind if kind in _SPECIFIC else "stream_error"
    if embedded:
        return kind if kind in _SPECIFIC else "embedded_error"
    if status == 200:
        return None
    return kind


def _clip(text: str, limit: int = 180) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def fault_detail(kind: str, message: str, param: Any = None) -> str:
    label = CLASS_ZH.get(kind, kind)
    param_bit = f"（param={param}）" if param else ""
    clipped = _clip(message)
    if clipped:
        return f"{label}{param_bit}：{clipped}"
    return f"{label}{param_bit}"


def build_fault(
    *,
    status: int | None,
    message: str = "",
    error_type: Any = None,
    error_code: Any = None,
    error_param: Any = None,
    curl_exit: int | None = None,
    from_stream: bool = False,
    embedded: bool = False,
) -> dict[str, Any] | None:
    kind = classify_failure(
        status=status,
        message=message,
        curl_exit=curl_exit,
        from_stream=from_stream,
        embedded=embedded,
    )
    if kind is None:
        return None
    return {
        "class": kind,
        "detail": fault_detail(kind, message, error_param),
        "message": _clip(message),
        "type": error_type,
        "code": error_code,
        "param": error_param,
    }


def fault_from_body(
    *,
    status: int | None,
    raw: str,
    transport_error: str | None,
    curl_exit: int | None,
) -> dict[str, Any] | None:
    """非串流 body。成功的 choices JSON 不會被拿去掃錯誤關鍵字。"""
    parsed: dict[str, Any] | None = None
    embedded = False
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict):
            parsed = parse_error_object(body)
            embedded = parsed is not None and status == 200
    if parsed is None and status == 200:
        return None
    message = ""
    if parsed:
        message = str(parsed.get("message") or "")
    elif status != 200:
        message = transport_error or raw or ""
    return build_fault(
        status=status,
        message=message,
        error_type=None if parsed is None else parsed.get("type"),
        error_code=None if parsed is None else parsed.get("code"),
        error_param=None if parsed is None else parsed.get("param"),
        curl_exit=curl_exit,
        embedded=embedded,
    )


def malformed_fault(detail: str) -> dict[str, Any]:
    return {
        "class": "malformed_response",
        "detail": detail,
        "message": detail,
        "type": None,
        "code": None,
        "param": None,
    }


def finalize_verdict(verdict: str, notes: str, result: dict[str, Any]) -> tuple[str, str]:
    """傳輸／引擎／壞掉的 body 蓋過 pass 與 noop。應拒而得到的 4xx 維持 pass。"""
    fault = result.get("fault") or {}
    if not isinstance(fault, dict):
        return verdict, notes
    kind = fault.get("class")
    detail = str(fault.get("detail") or "")
    if kind in OVERRIDE_VERDICT and verdict in ("pass", "noop"):
        return "fail", detail or notes
    if detail and detail not in (notes or ""):
        notes = f"{notes}；{detail}" if notes else detail
    return verdict, notes


def _flag_value(blob: str, name: str) -> str | None:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){name}(?!_)\s*['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_.:-]*)"
    )
    match = pattern.search(blob)
    if not match:
        return None
    value = match.group(1)
    if value.lower() in _UNSET_FLAGS:
        return None
    return value


def extract_vllm_flags(body: Any) -> dict[str, str | None]:
    """從 /server_info 取出 parser 旗標。不回傳原始 body，避免帶出環境變數。"""
    if isinstance(body, str):
        blob = body
    else:
        blob = json.dumps(body, ensure_ascii=False)
    return {name: _flag_value(blob, name) for name in _FLAG_NAMES}
