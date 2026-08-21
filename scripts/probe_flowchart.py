#!/usr/bin/env python3
"""Walk ARCHITECTURE.md steps A–F against OpenAI-compatible /v1.

Required env: OPENAI_BASE_URL  OPENAI_API_KEY  MODEL
Optional: PROBE_MODE=probe|enforce  (預設 probe；enforce＝官方不允許就在本機 400)
Optional: REASONING_EFFORT=low,high,max  (逗號分隔；空白＝依官方契約，不掃聯集)
Optional: ENABLE_THINKING=true  (inject chat_template_kwargs.enable_thinking)
Optional: STREAM_STEPS=true|false  (true=全部 stream；false=全關；不設=D/F 照常)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ENV_SOURCES: dict[str, str] = {}
PROBE_ENV = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "MODEL",
    "PROBE_MODE",
    "REASONING_EFFORT",
    "ENABLE_THINKING",
    "STREAM_STEPS",
)
TIMEOUT_S = 180
MAX_TOKENS = 1024
DEFAULT_EFFORT = ("low", "medium", "high", "max")

# 官方契約。key 是閘道 model id 的小寫子串；細節與出處見 PARAMS.md。
# effort 空 tuple + known=True → 該模型完全沒有 reasoning_effort（unsupported_parameter）。
CONTRACTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "gemma-4",
        {
            "known": True,
            "family": "Google Gemma 4",
            "effort": ("low", "medium", "high", "none"),
            "effort_default": None,
            "can_disable_thinking": True,
            "sampling_fixed": False,
            "note": "官方 thinking 是開／關；這組值是 vLLM 注入 enable_thinking 用，不是官方強度檔",
        },
    ),
    (
        "nemotron-3-ultra",
        {
            "known": True,
            "family": "NVIDIA Nemotron 3 Ultra",
            "effort": ("none", "medium", "high"),
            "effort_default": "high",
            "can_disable_thinking": True,
            "sampling_fixed": False,
            "note": "另有 reasoning_budget（本腳本不打）",
        },
    ),
    (
        "kimi-k3",
        {
            "known": True,
            "family": "Moonshot Kimi K3",
            "effort": ("low", "high", "max"),
            "effort_default": "max",
            "can_disable_thinking": False,
            "sampling_fixed": True,
            "note": "thinking 永遠開；temperature/top_p/n/penalties 官方固定",
        },
    ),
    (
        "gpt-oss-120b",
        {
            "known": True,
            "family": "OpenAI gpt-oss-120b",
            "effort": ("low", "medium", "high"),
            "effort_default": "medium",
            "can_disable_thinking": False,
            "sampling_fixed": False,
            "note": "永遠 reasoning；官方只有 low/medium/high",
        },
    ),
)
UNKNOWN_CONTRACT: dict[str, Any] = {
    "known": False,
    "family": None,
    "effort": (),
    "effort_default": None,
    "can_disable_thinking": None,
    "sampling_fixed": None,
    "note": "契約未知：不在本機攔參數，只記閘道行為",
}

JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}},
            "required": ["ok", "n"],
            "additionalProperties": False,
        },
    },
}
WEATHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def _parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip("'\"")
        if key:
            out[key] = val
    return out


def load_env_file() -> None:
    for key in PROBE_ENV:
        if os.environ.get(key):
            ENV_SOURCES[key] = "shell"
    path = ROOT / ".env"
    if not path.is_file():
        return
    for key, val in _parse_env_text(path.read_text(encoding="utf-8")).items():
        if key in os.environ:
            ENV_SOURCES.setdefault(key, "shell")
            continue
        os.environ[key] = val
        ENV_SOURCES[key] = ".env"


def env_source_note() -> None:
    for key in PROBE_ENV:
        raw = os.environ.get(key, "")
        src = ENV_SOURCES.get(key, "unset")
        shown = "(set)" if key == "OPENAI_API_KEY" and raw else (raw or "(empty)")
        print(f"  {key}={shown}  [{src}]")


load_env_file()
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")


PROBE_MODE = os.environ.get("PROBE_MODE", "").strip().lower() or "probe"


def resolve_contract(model_id: str) -> dict[str, Any]:
    low = model_id.lower()
    for key, contract in CONTRACTS:
        if key in low:
            return contract
    return UNKNOWN_CONTRACT


def openai_error(
    message: str,
    code: str,
    *,
    param: str,
    allowed: list[str] | None = None,
) -> dict[str, Any]:
    """OpenAI Chat Completions 的 400 body 形狀。"""
    err: dict[str, Any] = {
        "message": message,
        "type": "invalid_request_error",
        "param": param,
        "code": code,
    }
    if allowed:
        err["allowed"] = allowed
    return {"error": err}


def contract_error(contract: dict[str, Any], value: str) -> dict[str, Any] | None:
    """官方契約不允許此 reasoning_effort 就回 400 body；允許或契約未知回 None。"""
    if not contract["known"]:
        return None
    official = tuple(contract["effort"])
    if not official:
        return openai_error(
            "Unsupported parameter: 'reasoning_effort' is not supported with this model.",
            "unsupported_parameter",
            param="reasoning_effort",
        )
    if value in official:
        return None
    return openai_error(
        f"Invalid value: 'reasoning_effort' does not support '{value}'. "
        f"Supported values are: {', '.join(official)}.",
        "invalid_value",
        param="reasoning_effort",
        allowed=list(official),
    )


def requested_effort() -> list[str] | None:
    """None＝REASONING_EFFORT 空白（走契約預設）。"""
    raw = os.environ.get("REASONING_EFFORT", "").strip()
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def plan_effort(
    contract: dict[str, Any],
) -> tuple[tuple[str, ...], bool, list[dict[str, Any]]]:
    """回傳 (要送出的檔位, 是否打 none, 本機依契約攔下的項)。

    空白 REASONING_EFFORT：enforce＝不送該欄（走官方預設）；probe＝只掃官方枚舉。
    契約未知才退回 DEFAULT_EFFORT 聯集。
    """
    asked = requested_effort()
    official = tuple(contract["effort"])

    if asked is None:
        if PROBE_MODE == "enforce":
            return (), False, []
        vals = official if contract["known"] else DEFAULT_EFFORT + ("none",)
        return tuple(v for v in vals if v != "none"), "none" in vals, []

    send: list[str] = []
    blocked: list[dict[str, Any]] = []
    for val in asked:
        err = contract_error(contract, val)
        if err and PROBE_MODE == "enforce":
            blocked.append({"value": val, "error": err})
        else:
            send.append(val)
    return tuple(v for v in send if v != "none"), "none" in send, blocked


def effort_meta() -> list[str]:
    out = list(EFFORT)
    if SWEEP_NONE:
        out.append("none")
    return out


# 由 run() 依實際 model id 的契約填入。
CONTRACT: dict[str, Any] = UNKNOWN_CONTRACT
EFFORT: tuple[str, ...] = ()
SWEEP_NONE = False
EFFORT_BLOCKED: list[dict[str, Any]] = []
ENABLE_THINKING = os.environ.get("ENABLE_THINKING", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_STREAM_RAW = os.environ.get("STREAM_STEPS", "").strip().lower()
# None = 預設（D/F 照常）；True = 全強制串流；False = 全關
STREAM_STEPS: bool | None = (
    True if _STREAM_RAW in ("1", "true", "yes", "on")
    else False if _STREAM_RAW in ("0", "false", "no", "off")
    else None
)


def use_stream(default: bool) -> bool:
    """STREAM_STEPS=true → 全開；false → 全關；不設 → 用 default（D/F 才是 True）。"""
    if STREAM_STEPS is None:
        return default
    return STREAM_STEPS


def _preview(text: Any, n: int = 160) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def _loads_json_content(content: str | None) -> Any | None:
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        return None


def _json_ok(content: str | None) -> bool:
    return _loads_json_content(content) is not None


def _probe_schema_ok(content: str | None) -> bool:
    """B.json_schema / F.schema_reasoning：必須是 {ok: bool, n: int}，不可多欄。"""
    obj = _loads_json_content(content)
    if not isinstance(obj, dict) or set(obj.keys()) != {"ok", "n"}:
        return False
    n = obj.get("n")
    return isinstance(obj.get("ok"), bool) and isinstance(n, int) and not isinstance(n, bool)


def verdict_expect_reject(status: int | None) -> tuple[str, str]:
    """非法參數應 4xx。5xx 是伺服器錯，不算驗證通過。"""
    if status is None:
        return "fail", "無 HTTP 狀態（timeout／curl）"
    if 400 <= status <= 499:
        return "pass", f"HTTP {status} 客戶端拒絕"
    if status >= 500:
        return "fail", f"HTTP {status} 是伺服器錯誤，不是正確拒絕"
    return "noop", f"HTTP {status} 未拒絕非法值"


def verdict_non_200(status: int | None, what: str) -> tuple[str, str]:
    if status is None:
        return "fail", f"{what}：無 HTTP 狀態（timeout／curl）"
    if 400 <= status <= 499:
        return "skip", f"{what} 不接受（HTTP {status}）"
    return "fail", f"{what} HTTP {status}"


def local_reject(body: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """enforce：依官方契約在本機回 400，不送閘道。"""
    return {
        "ok": False,
        "http_status": 400,
        "elapsed_ms": 0,
        "error": json.dumps(body, ensure_ascii=False),
        "stream": False,
        "request": sent,
        "local_reject": True,
        **EMPTY,
    }


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


def load_profile() -> dict[str, Any]:
    return json.loads(
        (ROOT / "configs" / "profiles" / "generic.json").read_text(encoding="utf-8")
    )


def resolve_model() -> str:
    m = os.environ.get("MODEL", "").strip()
    if m.lower().startswith("model="):
        m = m.split("=", 1)[1].strip()
    return m


def match_gateway_id(requested: str, available: set[str]) -> str | None:
    if requested in available:
        return requested
    hits = [a for a in available if a.lower() == requested.lower()]
    if len(hits) == 1:
        print(f"MODEL={requested} → {hits[0]}（閘道 id 大小寫不同）")
        return hits[0]
    close = [
        a
        for a in sorted(available)
        if requested.lower() in a.lower() or a.lower() in requested.lower()
    ]
    print(f"SKIP：閘道沒有 {requested}", file=sys.stderr)
    if close:
        print("接近的 id：", file=sys.stderr)
        for a in close[:20]:
            print(f"  {a}", file=sys.stderr)
    return None


_CURL_MARK = "__CURL_HTTP__"


def _curl(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    cmd = [
        "curl",
        "-sS",
        "-w",
        f"\n{_CURL_MARK}%{{http_code}}",
        "--max-time",
        str(TIMEOUT_S),
        "-X",
        method,
        "-H",
        f"Authorization: Bearer {API_KEY}",
    ]
    if data is not None:
        cmd.extend(["-H", "Content-Type: application/json", "--data-binary", "@-"])
    if stream:
        cmd.append("-N")
    cmd.append(url)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            timeout=TIMEOUT_S + 10,
        )
    except FileNotFoundError:
        return {
            "http_status": None,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
            "error": "找不到 curl",
        }
    except subprocess.TimeoutExpired:
        return {
            "http_status": None,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
            "error": "curl timeout",
        }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    if _CURL_MARK in stdout:
        raw, _, code = stdout.rpartition(_CURL_MARK)
        try:
            status: int | None = int(code.strip())
        except ValueError:
            status = None
        if status == 0:
            status = None
    else:
        raw, status = stdout, None
    err = None
    if status is None:
        err = stderr or f"curl exit {proc.returncode}"
    elif status >= 400:
        err = _preview(raw, 400)
    return {
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "raw": raw,
        "error": err,
    }


def get_json(path: str) -> dict[str, Any]:
    r = _curl("GET", f"{BASE_URL}{path}")
    if r["http_status"] == 200:
        try:
            body = json.loads(r["raw"])
        except json.JSONDecodeError as e:
            return {
                "ok": False,
                "http_status": 200,
                "elapsed_ms": r["elapsed_ms"],
                "body": None,
                "error": f"JSONDecodeError: {e}",
            }
        return {
            "ok": True,
            "http_status": 200,
            "elapsed_ms": r["elapsed_ms"],
            "body": body,
            "error": None,
        }
    return {
        "ok": False,
        "http_status": r["http_status"],
        "elapsed_ms": r["elapsed_ms"],
        "body": None,
        "error": r["error"],
    }


EMPTY = {
    "content": None,
    "reasoning": None,
    "reasoning_content": None,
    "tool_calls": None,
    "message_keys": [],
    "finish_reason": None,
    "usage": None,
    "saw_done": False,
    "saw_usage": False,
    "saw_tool_delta": False,
}


def request_sent(body: dict[str, Any]) -> dict[str, Any]:
    """POST body minus full messages（證明有沒有帶 reasoning_effort 等）。"""
    snap = {k: v for k, v in body.items() if k != "messages"}
    msgs = body.get("messages") or []
    snap["message_count"] = len(msgs)
    if msgs and isinstance(msgs[0], dict):
        snap["first_user"] = _preview(msgs[0].get("content"), 80)
    return snap


def post_chat(payload: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
    body = dict(payload)
    stream = use_stream(stream)
    if stream:
        body["stream"] = True
        # 串流下不要 usage 就不會有 usage chunk，reasoning_tokens 會整輪拿不到
        body["stream_options"] = {"include_usage": True}
    sent = request_sent(body)
    r = _curl(
        "POST",
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        stream=stream,
    )
    status, raw, elapsed_ms = r["http_status"], r["raw"], r["elapsed_ms"]
    if status is None:
        return {
            "ok": False,
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "error": r["error"],
            "stream": stream,
            "request": sent,
            **EMPTY,
        }
    if status != 200:
        return {
            "ok": False,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "error": r["error"],
            "stream": stream,
            "request": sent,
            **EMPTY,
        }
    if stream:
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_buckets: dict[int, dict[str, Any]] = {}
        saw_done = False
        saw_usage = False
        saw_tool_delta = False
        finish_reason = None
        usage = None
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
            if obj.get("usage"):
                saw_usage = True
                usage = obj["usage"]
            choice = (obj.get("choices") or [{}])[0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            if delta.get("reasoning"):
                reasoning_parts.append(delta["reasoning"])
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("tool_calls"):
                saw_tool_delta = True
                _merge_tool_delta(tool_buckets, delta["tool_calls"])
        reason = "".join(reasoning_parts) or None
        tool_calls = [tool_buckets[i] for i in sorted(tool_buckets)] or None
        return {
            "ok": True,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "error": None,
            "stream": True,
            "request": sent,
            "content": "".join(content_parts),
            "reasoning": reason,
            "reasoning_content": reason,
            "tool_calls": tool_calls,
            "message_keys": [],
            "finish_reason": finish_reason,
            "usage": usage,
            "saw_done": saw_done,
            "saw_usage": saw_usage,
            "saw_tool_delta": saw_tool_delta,
        }
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "error": f"JSONDecodeError: {e}",
            "stream": False,
            "request": sent,
            **EMPTY,
        }
    try:
        msg = parsed["choices"][0]["message"]
    except Exception:
        msg = {}
    return {
        "ok": True,
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "error": None,
        "stream": False,
        "request": sent,
        "content": msg.get("content"),
        "reasoning": msg.get("reasoning"),
        "reasoning_content": msg.get("reasoning_content"),
        "tool_calls": msg.get("tool_calls"),
        "message_keys": sorted(msg.keys()) if msg else [],
        "finish_reason": (parsed.get("choices") or [{}])[0].get("finish_reason"),
        "usage": parsed.get("usage"),
        "saw_done": False,
        "saw_usage": False,
        "saw_tool_delta": False,
    }


def has_reasoning(r: dict[str, Any]) -> bool:
    return bool(r.get("reasoning") or r.get("reasoning_content"))


def reason_chars(r: dict[str, Any]) -> int:
    return len(r.get("reasoning") or r.get("reasoning_content") or "")


def reasoning_tokens(r: dict[str, Any]) -> int | None:
    det = ((r.get("usage") or {}).get("completion_tokens_details") or {})
    t = det.get("reasoning_tokens")
    return int(t) if isinstance(t, (int, float)) else None


def similar_thinking(shot: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """同一題思考量是否像基準（兩邊都有 reasoning_tokens 才比 token；否則字數）。"""
    out: dict[str, Any] = {
        "metric": None,
        "this": None,
        "other": None,
        "similar": None,
    }
    if baseline.get("http_status") != 200 or shot.get("http_status") != 200:
        return out
    if has_reasoning(shot) != has_reasoning(baseline):
        out["similar"] = False
        return out
    ta, tb = reasoning_tokens(shot), reasoning_tokens(baseline)
    if ta is not None and tb is not None:
        bigger = max(ta, tb, 1)
        out.update(
            metric="reasoning_tokens",
            this=ta,
            other=tb,
            similar=abs(ta - tb) <= max(8, bigger * 30 // 100),
        )
        return out
    a, b = reason_chars(shot), reason_chars(baseline)
    bigger = max(a, b, 1)
    out.update(
        metric="reasoning_chars",
        this=a,
        other=b,
        similar=abs(a - b) <= max(40, bigger * 30 // 100),
    )
    return out


def looks_like_baseline(shot: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """200 + same thinking on/off and similar length → value likely ignored or is the default."""
    if has_reasoning(shot) != has_reasoning(baseline):
        return False
    return bool(similar_thinking(shot, baseline).get("similar"))


def row(
    *,
    step: str,
    case_id: str,
    desc: str,
    result: dict[str, Any] | None,
    verdict: str,
    notes: str = "",
    compare: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r = result or {}
    return {
        "step": step,
        "case_id": case_id,
        "desc": desc,
        "verdict": verdict,
        "http_status": r.get("http_status"),
        "elapsed_ms": r.get("elapsed_ms"),
        "has_reasoning": has_reasoning(r) if result else None,
        "has_tool_calls": bool(r.get("tool_calls")) if result else None,
        "json_ok": _json_ok(r.get("content")) if result else None,
        "reasoning_chars": len(
            (r.get("reasoning") or r.get("reasoning_content") or "")
        )
        if result
        else None,
        "usage": r.get("usage") if result else None,
        "finish_reason": r.get("finish_reason"),
        "content_preview": _preview(r.get("content")),
        "reasoning_preview": _preview(
            r.get("reasoning") or r.get("reasoning_content")
        ),
        "error_preview": r.get("error"),
        "notes": notes,
        "request": r.get("request") if result else None,
        "local_reject": bool(r.get("local_reject")),
        "compare": compare,
        "contract": contract,
    }


def payload(model: str, messages: list[dict[str, Any]], extra: dict | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
    }
    if extra:
        p.update(extra)
    if ENABLE_THINKING:
        kwargs = dict(p.get("chat_template_kwargs") or {})
        kwargs["enable_thinking"] = True
        p["chat_template_kwargs"] = kwargs
    return p


def user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


def step_a(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = profile["prompt"]
    nonce = f" [nonce={int(time.time())}]"
    msgs = user(prompt + nonce)
    out: list[dict[str, Any]] = []

    r_on = post_chat(payload(model, msgs))
    if r_on.get("http_status") != 200:
        v, n = "fail", "應成功卻失敗"
    elif has_reasoning(r_on):
        v, n = "pass", "預設有 reasoning 欄位"
    else:
        v, n = "noop", "無 reasoning 欄位（查閘道是否剝除，或需官方 extra 才開）"
    out.append(
        row(step="A", case_id="A.enable", desc="預設一槍（看 reasoning）", result=r_on, verdict=v, notes=n)
    )

    for item in EFFORT_BLOCKED:
        err = item["error"]["error"]
        r_b = local_reject(
            item["error"], {"model": model, "reasoning_effort": item["value"]}
        )
        out.append(
            row(
                step="A",
                case_id=f"A.effort_{item['value']}",
                desc=f"reasoning_effort={item['value']}（官方不允許）",
                result=r_b,
                verdict="pass",
                notes=f"enforce：本機回 400 {err['code']}，未送閘道",
                contract={
                    "family": CONTRACT["family"],
                    "official_effort": list(CONTRACT["effort"]) or None,
                    "in_official": False,
                    "decision": "rejected_locally",
                },
            )
        )

    effort_shots: dict[str, dict[str, Any]] = {}
    for val in EFFORT:
        effort_shots[val] = post_chat(payload(model, msgs, {"reasoning_effort": val}))
    max_shot = effort_shots.get("max") if "max" in effort_shots else None
    max_is_default = bool(
        max_shot
        and max_shot.get("http_status") == 200
        and looks_like_baseline(max_shot, r_on)
    )

    for val, r_e in effort_shots.items():
        chars = reason_chars(r_e)
        tok = (r_e.get("usage") or {}).get("completion_tokens")
        vs_def = similar_thinking(r_e, r_on)
        vs_max = similar_thinking(r_e, max_shot) if max_shot and val != "max" else None
        like_default = bool(vs_def.get("similar"))
        like_max = bool(vs_max and vs_max.get("similar"))
        compare = {
            "vs_default": "like_default" if like_default else "not_default",
            "vs_max": (
                None
                if vs_max is None
                else ("like_max" if like_max else "not_like_max")
            ),
            "metric": vs_def.get("metric"),
            "this": vs_def.get("this"),
            "default": vs_def.get("other"),
            "max": None if vs_max is None else vs_max.get("other"),
            "intensity_testable": not ENABLE_THINKING,
        }
        if r_e.get("http_status") != 200:
            v, n = verdict_non_200(r_e.get("http_status"), f"reasoning_effort={val}")
            compare["vs_default"] = "unknown"
        elif not has_reasoning(r_e):
            v, n = "noop", f"完全沒有 reasoning，無法比強度（reason_chars={chars} tokens={tok}）"
            compare["vs_default"] = "unknown"
        elif ENABLE_THINKING:
            v, n = (
                "pass",
                "已強制 enable_thinking，effort 強度無法比；本槍只證明有送到且有 reasoning",
            )
        elif like_default or (max_is_default and like_max):
            v, n = (
                "noop",
                "與預設（或 max）無差，可能忽略此值或此檔就是預設",
            )
            compare["vs_default"] = "like_default"
        else:
            v, n = (
                "pass",
                "異於預設；不能證明就是獨立檔位，只能證明不是預設",
            )
        in_official = val in tuple(CONTRACT["effort"]) if CONTRACT["known"] else None
        contract_note: dict[str, Any] = {
            "family": CONTRACT["family"],
            "official_effort": list(CONTRACT["effort"]) or None,
            "in_official": in_official,
        }
        if in_official is False and r_e.get("http_status") == 200:
            contract_note["finding"] = "gateway_wider_than_official"
            n = (n + "；" if n else "") + "官方沒有此值，閘道仍 200（閘道比官方寬）"
        out.append(
            row(
                step="A",
                case_id=f"A.effort_{val}",
                desc=f"reasoning_effort={val}",
                result=r_e,
                verdict=v,
                notes=n,
                compare=compare,
                contract=contract_note,
            )
        )

    if PROBE_MODE == "enforce":
        body = openai_error(
            "Invalid value: 'reasoning_effort' does not support 'not-a-level'.",
            "invalid_value",
            param="reasoning_effort",
            allowed=list(CONTRACT["effort"]) or None,
        )
        r_bad = local_reject(body, {"model": model, "reasoning_effort": "not-a-level"})
        v, n = "pass", "enforce：本機回 400 invalid_value，未送閘道"
    else:
        r_bad = post_chat(payload(model, msgs, {"reasoning_effort": "not-a-level"}))
        v, n = verdict_expect_reject(r_bad.get("http_status"))
    out.append(
        row(
            step="A",
            case_id="A.effort_invalid",
            desc="reasoning_effort=not-a-level 應拒",
            result=r_bad,
            verdict=v,
            notes=n,
        )
    )

    if SWEEP_NONE:
        r_n = post_chat(payload(model, msgs, {"reasoning_effort": "none"}))
        if r_n.get("http_status") != 200:
            v, n = verdict_non_200(r_n.get("http_status"), "reasoning_effort=none")
        elif has_reasoning(r_n):
            v, n = "noop", "none 仍有 reasoning"
        elif CONTRACT["can_disable_thinking"] is False:
            v, n = "fail", "契約不符：官方不允許關 thinking，閘道卻關掉了"
        elif not has_reasoning(r_on):
            v, n = "noop", "預設槍本來就沒有 reasoning，證明不了 none 有作用"
        else:
            v, n = "pass", ""
        out.append(
            row(
                step="A",
                case_id="A.effort_none",
                desc="reasoning_effort=none",
                result=r_n,
                verdict=v,
                notes=n,
                contract={
                    "family": CONTRACT["family"],
                    "can_disable_thinking": CONTRACT["can_disable_thinking"],
                    "in_official": (
                        "none" in tuple(CONTRACT["effort"])
                        if CONTRACT["known"]
                        else None
                    ),
                },
            )
        )

    if r_on.get("http_status") == 200:
        asst: dict[str, Any] = {"role": "assistant", "content": r_on.get("content") or ""}
        if r_on.get("reasoning_content") or r_on.get("reasoning"):
            asst["reasoning_content"] = r_on.get("reasoning_content") or r_on.get("reasoning")
        r2 = post_chat(
            payload(
                model,
                [
                    *msgs,
                    asst,
                    {"role": "user", "content": "上一題答案再確認一次，只回數字。"},
                ],
            )
        )
        out.append(
            row(
                step="A",
                case_id="A.multiturn",
                desc="多輪回傳完整 assistant",
                result=r2,
                verdict="pass" if r2.get("http_status") == 200 else "fail",
            )
        )
    return out


def step_b(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    q = user(profile["prompt_json"])
    out: list[dict[str, Any]] = []
    r = post_chat(payload(model, q))
    out.append(
        row(
            step="B",
            case_id="B.none",
            desc="不帶 response_format",
            result=r,
            verdict="pass" if r.get("http_status") == 200 else "fail",
            notes="soft：不一定是 JSON",
        )
    )
    r = post_chat(payload(model, q, {"response_format": {"type": "json_object"}}))
    v = (
        "pass"
        if r.get("http_status") == 200 and _json_ok(r.get("content"))
        else ("fail" if r.get("http_status") != 200 else "noop")
    )
    out.append(row(step="B", case_id="B.json_object", desc="json_object", result=r, verdict=v))
    r = post_chat(payload(model, q, {"response_format": JSON_SCHEMA}))
    v = (
        "pass"
        if r.get("http_status") == 200 and _probe_schema_ok(r.get("content"))
        else ("fail" if r.get("http_status") != 200 else "noop")
    )
    n = "" if v == "pass" else ("content 不是 schema 的 {ok, n}" if r.get("http_status") == 200 else "")
    out.append(
        row(step="B", case_id="B.json_schema", desc="json_schema strict", result=r, verdict=v, notes=n)
    )
    r = post_chat(payload(model, q, {"response_format": "json"}))
    v, n = verdict_expect_reject(r.get("http_status"))
    out.append(
        row(step="B", case_id="B.string_json", desc="非標準字串 json 應拒", result=r, verdict=v, notes=n)
    )
    return out


def step_c(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    q = user(profile["prompt_tool"])

    def tools_payload(choice: Any) -> dict[str, Any]:
        return payload(model, q, {"tools": WEATHER_TOOLS, "tool_choice": choice})

    out: list[dict[str, Any]] = []
    r = post_chat(tools_payload("auto"))
    n = "" if r.get("tool_calls") else "無 tool_calls（soft：模型可能選擇不呼叫）"
    out.append(
        row(
            step="C",
            case_id="C.auto",
            desc="tool_choice=auto",
            result=r,
            verdict="pass" if r.get("http_status") == 200 else "fail",
            notes=n,
        )
    )
    r = post_chat(tools_payload("required"))
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif not r.get("tool_calls"):
        v, n = "noop", "required 無 tool_calls"
    else:
        v, n = "pass", ""
    out.append(
        row(step="C", case_id="C.required", desc="tool_choice=required", result=r, verdict=v, notes=n)
    )
    r = post_chat(tools_payload("none"))
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif r.get("tool_calls"):
        v, n = "noop", "none 仍有 tool_calls"
    else:
        v, n = "pass", ""
    out.append(row(step="C", case_id="C.none", desc="tool_choice=none", result=r, verdict=v, notes=n))
    return out


def step_d(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    r = post_chat(payload(model, user(profile["prompt"])), stream=True)
    out: list[dict[str, Any]] = []
    if r.get("http_status") != 200:
        v, n = "fail", "stream 應 200"
    elif not (r.get("content") or has_reasoning(r)):
        v, n = "noop", "delta 全空"
    else:
        v, n = "pass", ""
    out.append(row(step="D", case_id="D.sse", desc="stream=true SSE", result=r, verdict=v, notes=n))
    v = "pass" if has_reasoning(r) else "noop"
    out.append(
        row(
            step="D",
            case_id="D.reasoning_delta",
            desc="reasoning 獨立 delta",
            result=r,
            verdict=v,
            notes="" if v == "pass" else "stream 沒有獨立 reasoning delta",
        )
    )
    out.append(
        row(
            step="D",
            case_id="D.done",
            desc="結尾 [DONE]",
            result=r,
            verdict="pass"
            if r.get("http_status") == 200 and r.get("saw_done")
            else ("fail" if r.get("http_status") != 200 else "noop"),
            notes="" if r.get("saw_done") else "未看到 [DONE]",
        )
    )
    r3 = post_chat(
        payload(
            model,
            user(profile["prompt_tool"]),
            {"tools": WEATHER_TOOLS, "tool_choice": "required"},
        ),
        stream=True,
    )
    if r3.get("http_status") != 200:
        v, n = "fail", "stream + required tools 應 200"
    elif r3.get("saw_tool_delta") or r3.get("tool_calls"):
        v, n = "pass", ""
    else:
        v, n = "noop", "required 但沒有 tool_calls delta"
    out.append(
        row(
            step="D",
            case_id="D.tool_delta",
            desc="stream + tools",
            result=r3,
            verdict=v,
            notes=n,
        )
    )
    return out


def step_e(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    fixed = CONTRACT["sampling_fixed"]
    contract_note: dict[str, Any] = {
        "family": CONTRACT["family"],
        "sampling_fixed": fixed,
    }
    if fixed and PROBE_MODE == "enforce":
        body = openai_error(
            "Invalid value: 'temperature' is fixed for this model and cannot be changed.",
            "invalid_value",
            param="temperature",
        )
        r = local_reject(body, {"model": model, "temperature": 0.2})
        v, n = "pass", "enforce：官方固定 sampling，本機回 400 invalid_value，未送閘道"
        contract_note["decision"] = "rejected_locally"
        return [
            row(
                step="E",
                case_id="E.temperature",
                desc="temperature=0.2",
                result=r,
                verdict=v,
                notes=n,
                contract=contract_note,
            )
        ]

    r = post_chat(payload(model, user(profile["prompt"]), {"temperature": 0.2}))
    status = r.get("http_status")
    if status == 200 and fixed:
        v, n = "fail", "契約不符：官方固定 sampling 應拒，閘道卻回 200"
        contract_note["finding"] = "gateway_wider_than_official"
    elif status == 200:
        v, n = (
            "noop",
            "閘道接受 temperature=0.2；未比對輸出，不能證明 sampling 生效",
        )
    elif fixed and status is not None and 400 <= status <= 499:
        v, n = "pass", f"HTTP {status}：與官方一致（固定 sampling 應拒）"
    else:
        v, n = verdict_non_200(status, "temperature=0.2")
    return [
        row(
            step="E",
            case_id="E.temperature",
            desc="temperature=0.2",
            result=r,
            verdict=v,
            notes=n,
            contract=contract_note,
        )
    ]


def step_f(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    r = post_chat(
        payload(model, user(profile["prompt_json"]), {"response_format": JSON_SCHEMA})
    )
    v = (
        "pass"
        if r.get("http_status") == 200 and _probe_schema_ok(r.get("content"))
        else ("fail" if r.get("http_status") != 200 else "noop")
    )
    n = "只 parse content"
    if v == "noop":
        n = "只 parse content；不是 schema 的 {ok, n}"
    out.append(
        row(
            step="F",
            case_id="F.schema_reasoning",
            desc="schema + 預設請求",
            result=r,
            verdict=v,
            notes=n,
        )
    )
    r = post_chat(payload(model, user(profile["prompt"])), stream=True)
    v = (
        "pass"
        if r.get("http_status") == 200 and (r.get("content") or has_reasoning(r))
        else "fail"
    )
    out.append(
        row(step="F", case_id="F.stream_reasoning", desc="stream + 預設請求", result=r, verdict=v)
    )
    r = post_chat(
        payload(
            model,
            user(profile["prompt_tool"]),
            {"tools": WEATHER_TOOLS, "tool_choice": "auto"},
        ),
        stream=True,
    )
    n = (
        ""
        if r.get("tool_calls") or r.get("saw_tool_delta")
        else "無 tool_calls（soft：auto 可以不叫）"
    )
    out.append(
        row(
            step="F",
            case_id="F.stream_tools",
            desc="stream + tools",
            result=r,
            verdict="pass" if r.get("http_status") == 200 else "fail",
            notes=n,
        )
    )
    return out


RUNNERS = {
    "A": step_a,
    "B": step_b,
    "C": step_c,
    "D": step_d,
    "E": step_e,
    "F": step_f,
}
STEPS = ("A", "B", "C", "D", "E", "F")
VERDICT_ZH = {
    "pass": "可用",
    "fail": "不可用",
    "noop": "接受但未生效",
    "skip": "跳過",
}

# 寫進每份 results JSON，讀檔即可，不必另寫 RESULTS.md
JSON_READ = {
    "verdict": {
        "pass": "OK：這題預期成立（含 enforce 依契約在本機正確 400）",
        "fail": "FAIL：應成功卻失敗；非法參數回 5xx；或契約不符（官方應拒、閘道卻 200）",
        "noop": "NOOP：HTTP 2xx 但行為沒對上，或只證明閘道接受、無法證明生效",
        "skip": "SKIP：該參數 4xx 不接受",
    },
    "probe_mode": {
        "probe": "探測閘道：只掃官方枚舉，另打一槍非法值驗證應 4xx",
        "enforce": "當服務：官方不允許就在本機回 OpenAI 形狀 400，不送閘道",
    },
    "contract": {
        "來源": "各家官方文件，寫在腳本 CONTRACTS；對照說明見 PARAMS.md",
        "in_official": "false＝官方枚舉沒有此值",
        "finding": "gateway_wider_than_official＝閘道比官方寬，不代表模型官方支援",
        "decision": "rejected_locally＝enforce 模式本機 400，未送閘道",
    },
    "local_reject": "true＝本機依契約回 400，這筆沒有打閘道",
    "request": "實際 POST 出去的欄位（不含完整 messages）。有 reasoning_effort 只證明有送到閘道。",
    "compare": {
        "何時有": "僅 A.effort_* 槍",
        "metric": "優先 usage.completion_tokens_details.reasoning_tokens，沒有才用字數",
        "vs_default": {
            "like_default": "思考量≈不帶參數；可能忽略此值，或此檔就是預設",
            "not_default": "不是預設。不能證明就是官方獨立檔（例如不能證明 medium 真的是 medium）",
            "unknown": "無法比（該槍 4xx，或整槍完全沒有 reasoning）",
        },
        "vs_max": {
            "like_max": "思考量≈本次 max 槍",
            "not_like_max": "不像 max",
            "null": "沒掃 max，或本槍就是 max",
        },
        "intensity_testable": "false＝本輪有 ENABLE_THINKING=true，不能用 vs_default 判斷 low/high 強弱（Gemma 常見）",
    },
    "official_vs_gateway": "PARAMS.md 是廠商契約。閘道 200／枚舉較寬不代表官方有該檔。",
}


def run() -> int:
    global CONTRACT, EFFORT, SWEEP_NONE, EFFORT_BLOCKED

    if not BASE_URL or not API_KEY:
        print(
            "請設環境變數 OPENAI_BASE_URL、OPENAI_API_KEY（export 或專案根目錄 .env）",
            file=sys.stderr,
        )
        return 2
    if PROBE_MODE not in ("probe", "enforce"):
        print(f"PROBE_MODE 只能是 probe 或 enforce（收到 {PROBE_MODE}）", file=sys.stderr)
        return 2
    if not shutil.which("curl"):
        print("找不到 curl（請安裝後再跑）", file=sys.stderr)
        return 2
    requested = resolve_model()
    if not requested:
        print(
            "請設環境變數 MODEL（閘道 GET /models 上的 id；export 或 .env）",
            file=sys.stderr,
        )
        return 2

    profile = load_profile()
    print(f"base_url={BASE_URL}")
    env_source_note()
    print(f"MODEL={requested}")
    print("GET /models …")
    listed = get_json("/models")
    if not listed["ok"]:
        print(f"STOP: GET /models failed HTTP {listed['http_status']} {listed['error']}")
        return 1
    available = {
        m.get("id") for m in (listed["body"] or {}).get("data") or [] if m.get("id")
    }
    print(f"gateway has {len(available)} models")
    model = match_gateway_id(requested, available)
    if not model:
        return 2
    print(f"using {model}")
    CONTRACT = resolve_contract(model)
    EFFORT, SWEEP_NONE, EFFORT_BLOCKED = plan_effort(CONTRACT)
    print(f"probe_mode={PROBE_MODE}")
    print(f"contract={CONTRACT['family'] or '(未知：只記閘道行為)'}")
    print(f"official_effort={','.join(CONTRACT['effort']) or '(無)'}")
    print(f"reasoning_effort={','.join(effort_meta()) or '(不送此欄)'}")
    if EFFORT_BLOCKED:
        print(f"blocked_locally={','.join(b['value'] for b in EFFORT_BLOCKED)}")
    print(f"enable_thinking={'true' if ENABLE_THINKING else 'false'}")
    stream_label = "true" if STREAM_STEPS is True else "false" if STREAM_STEPS is False else "default(D/F only)"
    print(f"stream_steps={stream_label}")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    tag = model.replace("/", "_")
    out_path = ROOT / "results" / f"flowchart_{tag}_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "meta": {
            "base_url": BASE_URL,
            "profile": "generic",
            "probe_mode": PROBE_MODE,
            "contract": {
                "family": CONTRACT["family"],
                "known": CONTRACT["known"],
                "official_effort": list(CONTRACT["effort"]) or None,
                "effort_default": CONTRACT["effort_default"],
                "can_disable_thinking": CONTRACT["can_disable_thinking"],
                "sampling_fixed": CONTRACT["sampling_fixed"],
                "note": CONTRACT["note"],
            },
            "effort": effort_meta(),
            "effort_blocked_locally": [b["value"] for b in EFFORT_BLOCKED],
            "enable_thinking": ENABLE_THINKING,
            "stream_steps": (True if STREAM_STEPS is True else False if STREAM_STEPS is False else "default"),
            "steps": list(STEPS),
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "read": JSON_READ,
        },
        "models": {},
    }

    print("-" * 80)
    print(f"RUN {model} steps={list(STEPS)}")
    results: list[dict[str, Any]] = []
    for letter in STEPS:
        chunk = RUNNERS[letter](model, profile)
        results.extend(chunk)
        for r in chunk:
            flag = {
                "pass": "OK",
                "fail": "FAIL",
                "noop": "NOOP",
                "skip": "SKIP",
            }[r["verdict"]]
            print(
                f"[{flag}] {r['case_id']:28} HTTP {r['http_status']} "
                f"{r['elapsed_ms']}ms {r['desc']}"
            )
            if r.get("notes"):
                print(f"       {r['notes']}")
    report["models"][model] = {
        "present": True,
        "effort": effort_meta(),
        "results": results,
    }

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    report["meta"]["counts"] = {VERDICT_ZH.get(k, k): v for k, v in counts.items()}
    report["meta"]["finished_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 80)
    print("counts:", report["meta"]["counts"])
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
