#!/usr/bin/env python3
"""Probe /v1/chat/completions parameter support for Gemma-4 / Kimi-K3 style APIs.

Env:
  OPENAI_BASE_URL   default: https://inner-medusa.genai.nchc.org.tw/v1
  OPENAI_API_KEY    required
  MODELS            comma-separated, default: gemma-4-31B-it
  TIMEOUT_S         default: 180
  MAX_TOKENS        default: 512
  OUT_JSON          default: ../results/probe_<timestamp>.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get(
    "OPENAI_BASE_URL", "https://inner-medusa.genai.nchc.org.tw/v1"
).rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODELS = [
    m.strip()
    for m in os.environ.get("MODELS", "gemma-4-31B-it").split(",")
    if m.strip()
]
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "180"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "512"))


JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "n": {"type": "integer"},
            },
            "required": ["ok", "n"],
            "additionalProperties": False,
        },
    },
}


def _preview(text: str | None, n: int = 160) -> str:
    if text is None:
        return ""
    text = text.replace("\n", "\\n")
    return text if len(text) <= n else text[: n - 1] + "…"


def _extract_message_fields(body: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {
            "content": None,
            "reasoning": None,
            "reasoning_content": None,
            "message_keys": [],
        }
    return {
        "content": msg.get("content"),
        "reasoning": msg.get("reasoning"),
        "reasoning_content": msg.get("reasoning_content"),
        "message_keys": sorted(msg.keys()),
    }


def _json_loads_ok(content: str | None) -> bool:
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


def post_chat(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE_URL}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"_raw": raw}
            fields = _extract_message_fields(body) if isinstance(body, dict) else {}
            return {
                "ok": True,
                "http_status": resp.status,
                "elapsed_ms": elapsed_ms,
                "error": None,
                "body": body,
                **fields,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        try:
            err_body = json.loads(raw)
        except json.JSONDecodeError:
            err_body = {"_raw": raw}
        return {
            "ok": False,
            "http_status": e.code,
            "elapsed_ms": elapsed_ms,
            "error": _preview(raw, 400),
            "body": err_body,
            "content": None,
            "reasoning": None,
            "reasoning_content": None,
            "message_keys": [],
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "error": f"{type(e).__name__}: {e}",
            "body": None,
            "content": None,
            "reasoning": None,
            "reasoning_content": None,
            "message_keys": [],
        }


def case_matrix(model: str) -> list[dict[str, Any]]:
    """Official-parameter-oriented cases for Gemma-4 / Kimi-K3 style gateways."""
    base_msgs_json = [
        {
            "role": "user",
            "content": 'Reply with ONLY JSON: {"ok": true, "n": 1}',
        }
    ]
    base_msgs_reason = [
        {
            "role": "user",
            "content": "What is 15 * 37? Brief answer.",
        }
    ]

    cases: list[dict[str, Any]] = [
        {
            "id": "baseline",
            "group": "baseline",
            "desc": "No special params",
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
            },
        },
        {
            "id": "rf_none_json_prompt",
            "group": "response_format",
            "desc": "JSON via prompt only",
            "payload": {
                "model": model,
                "messages": base_msgs_json,
                "max_tokens": MAX_TOKENS,
            },
            "expect_json": True,
        },
        {
            "id": "rf_json_object",
            "group": "response_format",
            "desc": 'response_format={"type":"json_object"}',
            "payload": {
                "model": model,
                "messages": base_msgs_json,
                "max_tokens": MAX_TOKENS,
                "response_format": {"type": "json_object"},
            },
            "expect_json": True,
        },
        {
            "id": "rf_json_schema",
            "group": "response_format",
            "desc": 'response_format={"type":"json_schema",...}',
            "payload": {
                "model": model,
                "messages": base_msgs_json,
                "max_tokens": MAX_TOKENS,
                "response_format": JSON_SCHEMA,
            },
            "expect_json": True,
        },
        {
            "id": "rf_string_json",
            "group": "response_format",
            "desc": 'response_format="json" (non-standard)',
            "payload": {
                "model": model,
                "messages": base_msgs_json,
                "max_tokens": MAX_TOKENS,
                "response_format": "json",
            },
            "expect_json": True,
        },
        # Gemma-4 / vLLM / Google MaaS
        {
            "id": "gemma_enable_thinking_true",
            "group": "reasoning_gemma",
            "desc": "chat_template_kwargs.enable_thinking=true",
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            "expect_reasoning": True,
        },
        {
            "id": "gemma_enable_thinking_false",
            "group": "reasoning_gemma",
            "desc": "chat_template_kwargs.enable_thinking=false",
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            "expect_reasoning": False,
        },
        {
            "id": "gemma_enable_thinking_skip_false",
            "group": "reasoning_gemma",
            "desc": "enable_thinking=true + skip_special_tokens=false",
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "chat_template_kwargs": {"enable_thinking": True},
                "skip_special_tokens": False,
            },
            "expect_reasoning": True,
        },
        {
            "id": "gemma_reasoning_effort_high",
            "group": "reasoning_gemma",
            "desc": 'top-level reasoning_effort="high" (vLLM auto enable_thinking)',
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "reasoning_effort": "high",
            },
            "expect_reasoning": True,
        },
        {
            "id": "gemma_reasoning_effort_none",
            "group": "reasoning_gemma",
            "desc": 'top-level reasoning_effort="none"',
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "reasoning_effort": "none",
            },
            "expect_reasoning": False,
        },
        {
            "id": "gemma_include_reasoning_false",
            "group": "reasoning_gemma",
            "desc": "enable_thinking=true + include_reasoning=false",
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "chat_template_kwargs": {"enable_thinking": True},
                "include_reasoning": False,
            },
            "expect_reasoning": False,
        },
        # Kimi-K3 style (also useful to see if gateway rejects)
        {
            "id": "kimi_reasoning_effort_low",
            "group": "reasoning_kimi",
            "desc": 'reasoning_effort="low" (Kimi K3 official)',
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "reasoning_effort": "low",
            },
        },
        {
            "id": "kimi_reasoning_effort_max",
            "group": "reasoning_kimi",
            "desc": 'reasoning_effort="max" (Kimi K3 default)',
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "reasoning_effort": "max",
            },
        },
        {
            "id": "kimi_thinking_enabled",
            "group": "reasoning_kimi",
            "desc": 'thinking={"type":"enabled"} (K2.x; K3 should not use)',
            "payload": {
                "model": model,
                "messages": base_msgs_reason,
                "max_tokens": MAX_TOKENS,
                "thinking": {"type": "enabled"},
            },
        },
        {
            "id": "combo_json_schema_thinking",
            "group": "combo",
            "desc": "json_schema + enable_thinking=true",
            "payload": {
                "model": model,
                "messages": base_msgs_json,
                "max_tokens": MAX_TOKENS,
                "response_format": JSON_SCHEMA,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            "expect_json": True,
        },
    ]
    return cases


def evaluate(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    has_reasoning = bool(result.get("reasoning") or result.get("reasoning_content"))
    content = result.get("content")
    json_ok = _json_loads_ok(content) if case.get("expect_json") else None

    verdict = "pass" if result.get("http_status") == 200 else "fail"
    notes: list[str] = []

    if case.get("expect_json") is True and result.get("http_status") == 200:
        if not json_ok:
            verdict = "warn"
            notes.append("HTTP 200 but content not valid JSON")

    if case.get("expect_reasoning") is True and result.get("http_status") == 200:
        if not has_reasoning:
            # reasoning may be merged into content with channel tokens
            content_s = content or ""
            if "<|channel>" in content_s or "thought" in content_s[:80].lower():
                verdict = "warn"
                notes.append("no reasoning field; possible unparsed thought in content")
            else:
                verdict = "warn"
                notes.append("no reasoning / reasoning_content in message")

    if case.get("expect_reasoning") is False and result.get("http_status") == 200:
        if has_reasoning:
            verdict = "warn"
            notes.append("reasoning still present when expected off/suppressed")

    return {
        "verdict": verdict,
        "has_reasoning_field": has_reasoning,
        "json_loads_ok": json_ok,
        "notes": notes,
    }


def run() -> int:
    if not API_KEY:
        print("OPENAI_API_KEY is required", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out_path = Path(
        os.environ.get(
            "OUT_JSON",
            str(
                Path(__file__).resolve().parent.parent
                / "results"
                / f"probe_{stamp}.json"
            ),
        )
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "meta": {
            "base_url": BASE_URL,
            "models": MODELS,
            "timeout_s": TIMEOUT_S,
            "max_tokens": MAX_TOKENS,
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
        },
        "results": [],
    }

    print(f"base_url={BASE_URL}")
    print(f"models={MODELS}")
    print("-" * 88)

    for model in MODELS:
        for case in case_matrix(model):
            result = post_chat(case["payload"])
            eval_ = evaluate(case, result)
            row = {
                "model": model,
                "case_id": case["id"],
                "group": case["group"],
                "desc": case["desc"],
                "http_status": result["http_status"],
                "elapsed_ms": result["elapsed_ms"],
                "verdict": eval_["verdict"],
                "has_reasoning_field": eval_["has_reasoning_field"],
                "json_loads_ok": eval_["json_loads_ok"],
                "message_keys": result.get("message_keys") or [],
                "content_preview": _preview(result.get("content")),
                "reasoning_preview": _preview(
                    result.get("reasoning") or result.get("reasoning_content")
                ),
                "error_preview": result.get("error"),
                "notes": eval_["notes"],
            }
            report["results"].append(row)
            flag = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}[row["verdict"]]
            print(
                f"[{flag}] {model} | {case['id']} | HTTP {row['http_status']} | "
                f"{row['elapsed_ms']}ms | reason={row['has_reasoning_field']} | "
                f"json={row['json_loads_ok']}"
            )
            if row["notes"]:
                print(f"       notes: {'; '.join(row['notes'])}")
            if row["error_preview"] and row["verdict"] == "fail":
                print(f"       err: {row['error_preview'][:200]}")

    report["meta"]["finished_at"] = (
        datetime.now(timezone.utc).astimezone().isoformat()
    )
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 88)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
