#!/usr/bin/env python3
"""Walk ARCHITECTURE.md steps A–F against OpenAI-compatible /v1.

Required env: OPENAI_BASE_URL  OPENAI_API_KEY  MODEL
Optional: REASONING_EFFORT=low,high,max  (comma list; unset = probe union)
Optional: ENABLE_THINKING=true  (inject chat_template_kwargs.enable_thinking)
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
    "REASONING_EFFORT",
    "ENABLE_THINKING",
)
TIMEOUT_S = 180
MAX_TOKENS = 512
DEFAULT_EFFORT = ("low", "medium", "high", "max")

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


def parse_effort() -> tuple[tuple[str, ...], bool]:
    raw = os.environ.get("REASONING_EFFORT", "").strip()
    if not raw:
        return DEFAULT_EFFORT, True
    levels: list[str] = []
    sweep_none = False
    for part in raw.split(","):
        val = part.strip()
        if not val:
            continue
        if val.lower() == "none":
            sweep_none = True
        else:
            levels.append(val)
    return tuple(levels), sweep_none


def effort_meta() -> list[str]:
    out = list(EFFORT)
    if SWEEP_NONE:
        out.append("none")
    return out


EFFORT, SWEEP_NONE = parse_effort()
ENABLE_THINKING = os.environ.get("ENABLE_THINKING", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _preview(text: Any, n: int = 160) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def _json_ok(content: str | None) -> bool:
    if not content:
        return False
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        json.loads(text)
        return True
    except Exception:
        return False


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


def post_chat(payload: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
    body = dict(payload)
    if stream:
        body["stream"] = True
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
            **EMPTY,
        }
    if status != 200:
        return {
            "ok": False,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "error": r["error"],
            "stream": stream,
            **EMPTY,
        }
    if stream:
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        saw_done = False
        saw_usage = False
        saw_tool_delta = False
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
            delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            if delta.get("reasoning"):
                reasoning_parts.append(delta["reasoning"])
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("tool_calls"):
                saw_tool_delta = True
        reason = "".join(reasoning_parts) or None
        return {
            "ok": True,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "error": None,
            "stream": True,
            "content": "".join(content_parts),
            "reasoning": reason,
            "reasoning_content": reason,
            "tool_calls": None,
            "message_keys": [],
            "finish_reason": None,
            "usage": None,
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


def looks_like_baseline(shot: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """200 + same thinking on/off and similar length → value likely ignored or is the default."""
    if baseline.get("http_status") != 200 or shot.get("http_status") != 200:
        return False
    if has_reasoning(shot) != has_reasoning(baseline):
        return False
    a, b = reason_chars(shot), reason_chars(baseline)
    if a == b:
        return True
    bigger = max(a, b, 1)
    return abs(a - b) <= max(40, bigger * 30 // 100)


def row(
    *,
    step: str,
    case_id: str,
    desc: str,
    result: dict[str, Any] | None,
    verdict: str,
    notes: str = "",
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

    for val in EFFORT:
        r_e = post_chat(payload(model, msgs, {"reasoning_effort": val}))
        chars = len(r_e.get("reasoning") or r_e.get("reasoning_content") or "")
        tok = (r_e.get("usage") or {}).get("completion_tokens")
        if r_e.get("http_status") != 200:
            v, n = "skip", f"reasoning_effort={val} 不接受"
        elif not has_reasoning(r_e):
            v, n = "noop", f"reason_chars={chars} tokens={tok}"
        elif looks_like_baseline(r_e, r_on):
            v, n = (
                "noop",
                f"與預設無差，可能忽略此值或此檔就是預設；"
                f"reason_chars={chars} tokens={tok} 預設={reason_chars(r_on)}字",
            )
        else:
            v, n = (
                "pass",
                f"reason_chars={chars} tokens={tok}（異於預設 {reason_chars(r_on)}字）",
            )
        out.append(
            row(
                step="A",
                case_id=f"A.effort_{val}",
                desc=f"reasoning_effort={val}",
                result=r_e,
                verdict=v,
                notes=n,
            )
        )

    r_bad = post_chat(payload(model, msgs, {"reasoning_effort": "not-a-level"}))
    v = "pass" if (r_bad.get("http_status") or 0) >= 400 else "noop"
    out.append(
        row(
            step="A",
            case_id="A.effort_invalid",
            desc="reasoning_effort=not-a-level 應拒",
            result=r_bad,
            verdict=v,
            notes="" if v == "pass" else "非官方枚舉卻 200",
        )
    )

    if SWEEP_NONE:
        r_n = post_chat(payload(model, msgs, {"reasoning_effort": "none"}))
        if r_n.get("http_status") != 200:
            v, n = "skip", "reasoning_effort=none 不接受"
        elif has_reasoning(r_n):
            v, n = "noop", "none 仍有 reasoning"
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
        if r.get("http_status") == 200 and _json_ok(r.get("content"))
        else ("fail" if r.get("http_status") != 200 else "noop")
    )
    out.append(row(step="B", case_id="B.json_schema", desc="json_schema strict", result=r, verdict=v))
    r = post_chat(payload(model, q, {"response_format": "json"}))
    v = "pass" if (r.get("http_status") or 0) >= 400 else "noop"
    out.append(
        row(step="B", case_id="B.string_json", desc="非標準字串 json 應拒", result=r, verdict=v)
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
    n = "" if r3.get("saw_tool_delta") else "未看到 tool_calls delta（soft）"
    out.append(
        row(
            step="D",
            case_id="D.tool_delta",
            desc="stream + tools",
            result=r3,
            verdict="pass" if r3.get("http_status") == 200 else "fail",
            notes=n,
        )
    )
    return out


def step_e(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    r = post_chat(payload(model, user(profile["prompt"]), {"temperature": 0.2}))
    return [
        row(
            step="E",
            case_id="E.temperature",
            desc="temperature=0.2",
            result=r,
            verdict="pass" if r.get("http_status") == 200 else "fail",
            notes="腳本只記閘道是否接受；官方若固定勿改，對照 PARAMS.md",
        )
    ]


def step_f(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    r = post_chat(
        payload(model, user(profile["prompt_json"]), {"response_format": JSON_SCHEMA})
    )
    v = (
        "pass"
        if r.get("http_status") == 200 and _json_ok(r.get("content"))
        else ("fail" if r.get("http_status") != 200 else "noop")
    )
    out.append(
        row(
            step="F",
            case_id="F.schema_reasoning",
            desc="schema + 預設請求",
            result=r,
            verdict=v,
            notes="只 parse content",
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
    out.append(
        row(
            step="F",
            case_id="F.stream_tools",
            desc="stream + tools",
            result=r,
            verdict="pass" if r.get("http_status") == 200 else "fail",
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


def run() -> int:
    if not BASE_URL or not API_KEY:
        print(
            "請設環境變數 OPENAI_BASE_URL、OPENAI_API_KEY（export 或專案根目錄 .env）",
            file=sys.stderr,
        )
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
    print(f"reasoning_effort={','.join(effort_meta()) or '(none)'}")
    print(f"enable_thinking={'true' if ENABLE_THINKING else 'false'}")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    tag = model.replace("/", "_")
    out_path = ROOT / "results" / f"flowchart_{tag}_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "meta": {
            "base_url": BASE_URL,
            "profile": "generic",
            "effort": effort_meta(),
            "enable_thinking": ENABLE_THINKING,
            "steps": list(STEPS),
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
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
