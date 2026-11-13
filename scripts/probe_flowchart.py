#!/usr/bin/env python3
"""Walk ARCHITECTURE.md baseline A–F plus Profile-generated contract cases.

Required env: OPENAI_BASE_URL  OPENAI_API_KEY  MODEL
Optional: PROBE_MODE=probe|enforce  (預設 probe；enforce＝官方不允許就在本機 400)
Optional: REASONING_EFFORT=low,high,max  (逗號分隔；空白＝依 Profile 官方枚舉)
Optional: PROBE_SCOPE=official|full  (預設 official＝官方特殊參數＋文件寫死的回覆欄位；full＝A–F 考卷)
Optional: STREAM_STEPS=true|false  (true=全部 stream；false=全關；不設=D/F 照常)

特殊參數契約在 configs/models/*.json。ENABLE_THINKING 不再汙染全程；Gemma 的
enable_thinking 由 Profile flag 產生獨立對照槍。
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probe.cases import (
    ProbeCase,
    cases_by_step,
    generate_contract_cases,
    generate_thinking_stream_combos,
    messages_for_case,
    official_scope_cases,
)
from probe.extra import deep_merge
from probe.gateways import parse_env_text
from probe.faults import clip_text, content_has_tool_call, extract_vllm_flags, finalize_verdict
from probe.flow import build_flow, render_flow_md
from probe.http_parse import parse_curl_stdout
from probe.profile import ModelProfile, load_profiles, match_profile, unknown_profile
from probe.report_table import render_markdown_table, comparison_rows, write_report_md
from probe.response import EMPTY_FIELDS as EMPTY
from probe.response import interpret_chat_response, response_document
from probe.verdict import (
    has_reasoning,
    match_official_example,
    official_reply_spec,
    reasoning_channel_verdict,
    refine_effect,
    verdict_expect_reject,
    verdict_http,
    verdict_non_200,
)

ENV_SOURCES: dict[str, str] = {}
PROBE_ENV = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "MODEL",
    "PROBE_MODE",
    "REASONING_EFFORT",
    "ENABLE_THINKING",
    "STREAM_STEPS",
    "PROBE_SCOPE",
    "VLLM_BASE_URL",
    "LITELLM_REQUEST_DEBUG",
)
TIMEOUT_S = 180
MODELS_DIR = ROOT / "configs" / "models"

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


def load_env_file() -> None:
    for key in PROBE_ENV:
        if os.environ.get(key):
            ENV_SOURCES[key] = "shell"
    path = ROOT / ".env"
    if not path.is_file():
        return
    for key, val in parse_env_text(path.read_text(encoding="utf-8")).items():
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
_SCOPE_RAW = os.environ.get("PROBE_SCOPE", "").strip().lower()
PROBE_SCOPE = _SCOPE_RAW or "official"


def requested_effort() -> list[str] | None:
    """None＝REASONING_EFFORT 空白（走 Profile 官方枚舉）。"""
    raw = os.environ.get("REASONING_EFFORT", "").strip()
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def effort_values(cases: list[ProbeCase]) -> list[str]:
    return [
        str(c.value)
        for c in cases
        if c.param_name == "reasoning_effort" and c.expect != "reject"
    ]


# 由 run() 依閘道 model id 填入。
MODEL_PROFILE: ModelProfile = unknown_profile()
CONTRACT_CASES: list[ProbeCase] = []
ENABLE_THINKING_ENV = os.environ.get("ENABLE_THINKING", "").strip().lower() in (
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
LITELLM_REQUEST_DEBUG = os.environ.get("LITELLM_REQUEST_DEBUG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").strip().rstrip("/")


def use_stream(default: bool) -> bool:
    """STREAM_STEPS=true → 全開；false → 全關；不設 → 用 default（D/F 才是 True）。"""
    if STREAM_STEPS is None:
        return default
    return STREAM_STEPS


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
    timeout_s: int = TIMEOUT_S,
) -> dict[str, Any]:
    cmd = [
        "curl",
        "-sS",
        "-D",
        "-",
        "-w",
        f"\n{_CURL_MARK}%{{http_code}}",
        "--max-time",
        str(timeout_s),
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
            timeout=timeout_s + 10,
        )
    except FileNotFoundError:
        return {
            "http_status": None,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
            "headers": {},
            "curl_exit": None,
            "error": "找不到 curl",
        }
    except subprocess.TimeoutExpired:
        return {
            "http_status": None,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
            "headers": {},
            "curl_exit": 28,
            "error": "curl timeout",
        }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    parsed = parse_curl_stdout(stdout)
    status = parsed["http_status"]
    raw = parsed["body"]
    err = None
    if status is None:
        err = stderr or f"curl exit {proc.returncode}"
    elif status >= 400:
        err = clip_text(raw, 400)
    return {
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "raw": raw,
        "headers": parsed["headers"],
        "curl_exit": proc.returncode,
        "error": err,
    }


def get_json_url(url: str, *, timeout_s: int = TIMEOUT_S) -> dict[str, Any]:
    r = _curl("GET", url, timeout_s=timeout_s)
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


def get_json(path: str) -> dict[str, Any]:
    return get_json_url(f"{BASE_URL}{path}")


def request_sent(body: dict[str, Any]) -> dict[str, Any]:
    """POST body minus full messages（證明有沒有帶 reasoning_effort 等）。"""
    snap = {k: v for k, v in body.items() if k != "messages"}
    msgs = body.get("messages") or []
    snap["message_count"] = len(msgs)
    if msgs and isinstance(msgs[0], dict):
        snap["first_user"] = clip_text(msgs[0].get("content"), 80)
    return snap


def post_chat(
    payload: dict[str, Any],
    *,
    stream: bool = False,
    honor_stream_env: bool = True,
) -> dict[str, Any]:
    body = dict(payload)
    stream = use_stream(stream) if honor_stream_env else stream
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
    result = interpret_chat_response(
        status=r["http_status"],
        raw=r["raw"],
        elapsed_ms=r["elapsed_ms"],
        error=r["error"],
        headers=r.get("headers") or {},
        curl_exit=r.get("curl_exit"),
        stream=stream,
        sent=sent,
    )
    result["request_body"] = body
    result["response_json"] = response_document(r["raw"])
    return result


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
    official: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r = result or {}
    verdict, notes = finalize_verdict(verdict, notes, r)
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
        "content_preview": clip_text(r.get("content"), 160),
        "reasoning_preview": clip_text(r.get("reasoning") or r.get("reasoning_content"), 160),
        "error_preview": r.get("error"),
        "notes": notes,
        "request": r.get("request") if result else None,
        "local_reject": bool(r.get("local_reject")),
        "response_headers": r.get("response_headers") if result else None,
        "fault": r.get("fault") if result else None,
        "reasoning_obs": r.get("reasoning_obs") if result else None,
        "compare": compare,
        "contract": contract,
        "official": official,
        "request_body": r.get("request_body") if result else None,
        "response_json": r.get("response_json") if result else None,
    }


def payload(model: str, messages: list[dict[str, Any]], extra: dict | None = None) -> dict[str, Any]:
    defaults = MODEL_PROFILE.request_defaults
    p: dict[str, Any] = {
        "model": model,
        "messages": messages,
        defaults.length_param: defaults.max_output,
    }
    for sample in getattr(MODEL_PROFILE, "sampling", ()):
        if sample.send and sample.default is not None:
            p[sample.name] = sample.default
    if extra:
        p = deep_merge(p, extra)
    if LITELLM_REQUEST_DEBUG:
        p["litellm_request_debug"] = True
    return p


def contract_note_for(
    case: ProbeCase,
    *,
    finding: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    effort = MODEL_PROFILE.param("reasoning_effort")
    note: dict[str, Any] = {
        "family": MODEL_PROFILE.family,
        "profile_id": MODEL_PROFILE.id,
        "param": case.param_name,
        "in_official": case.in_official,
        "claimed": case.claimed,
    }
    if effort:
        note["official_effort"] = list(effort.values) or None
        note["can_disable_thinking"] = effort.can_disable
    if finding:
        note["finding"] = finding
    if decision:
        note["decision"] = decision
    return note


def execute_contract_cases(
    model: str,
    msgs: list[dict[str, Any]],
    cases: list[ProbeCase],
    *,
    baseline: dict[str, Any],
    scope: str | None = None,
) -> list[dict[str, Any]]:
    pending: list[tuple[ProbeCase, dict[str, Any], dict[str, Any]]] = []
    group_results: dict[str, dict[Any, dict[str, Any]]] = {}
    rows_out: list[dict[str, Any]] = []
    scope = scope or PROBE_SCOPE
    official = scope == "official"
    base_prompt = ""
    if msgs:
        base_prompt = str(msgs[0].get("content") or "")
    source = MODEL_PROFILE.source[0] if MODEL_PROFILE.source else ""

    for case in cases:
        if case.case_id == "A.multiturn":
            continue
        sent_snap = {"model": model, **case.extra}
        if case.enforce_local:
            result = local_reject(case.reject_body or {}, sent_snap)
            v, n = "pass", "enforce：本機回 400，未送閘道"
            finding, decision = None, "rejected_locally"
            compare = None
            rows_out.append(
                row(
                    step=case.step,
                    case_id=case.case_id,
                    desc=case.desc,
                    result=result,
                    verdict=v,
                    notes=n,
                    contract=contract_note_for(case, finding=finding, decision=decision),
                    compare=compare,
                )
            )
            continue

        case_msgs = messages_for_case(base_prompt, case)
        stream = False
        honor_stream_env = not (case.force_non_stream or official)
        if case.stream_override is not None:
            stream = case.stream_override
            honor_stream_env = False
        result = post_chat(
            payload(model, case_msgs, case.extra),
            stream=stream,
            honor_stream_env=honor_stream_env,
        )
        use_docs_fields = case.effect == "official_shape" or (
            official and case.expect == "effect"
        )
        if use_docs_fields:
            spec = official_reply_spec(case, source=source)
            v, n, official_ev = match_official_example(result, spec)
            rows_out.append(
                row(
                    step=case.step,
                    case_id=case.case_id,
                    desc=case.desc,
                    result=result,
                    verdict=v,
                    notes=n,
                    contract=contract_note_for(case),
                    official=official_ev,
                )
            )
            continue
        v, n = verdict_http(case.expect, result.get("http_status"), what=case.desc)
        finding = None
        if case.expect == "reject" and result.get("http_status") == 200:
            finding = "gateway_wider_than_official"
        if (
            case.expect == "effect"
            and v == "pass"
            and result.get("http_status") == 200
        ):
            pending.append((case, result, {}))
            if case.compare_group:
                group_results.setdefault(case.compare_group, {})[case.value] = result
            continue
        if case.in_official is False and result.get("http_status") == 200:
            finding = finding or "gateway_wider_than_official"
            n = (n + "；" if n else "") + "官方沒有此值，閘道仍 200（閘道比官方寬）"
        rows_out.append(
            row(
                step=case.step,
                case_id=case.case_id,
                desc=case.desc,
                result=result,
                verdict=v,
                notes=n,
                contract=contract_note_for(case, finding=finding),
            )
        )

    pending_rows: list[dict[str, Any]] = []
    for case, result, _ in pending:
        v, n, compare = refine_effect(
            effect=case.effect,
            disable=case.disable,
            can_disable=case.can_disable,
            value=case.value,
            default=case.default,
            result=result,
            baseline=baseline,
            group_results=group_results.get(case.compare_group or "", {}),
        )
        pending_rows.append(
            row(
                step=case.step,
                case_id=case.case_id,
                desc=case.desc,
                result=result,
                verdict=v,
                notes=n,
                compare=compare,
                contract=contract_note_for(case),
            )
        )

    # Keep generated order: splice pending effect rows back by case_id.
    pending_by_id = {r["case_id"]: r for r in pending_rows}
    merged: list[dict[str, Any]] = []
    seen = {r["case_id"] for r in rows_out}
    for case in cases:
        if case.case_id == "A.multiturn":
            continue
        if case.case_id in pending_by_id:
            merged.append(pending_by_id[case.case_id])
        elif case.case_id in seen:
            merged.append(next(r for r in rows_out if r["case_id"] == case.case_id))
    return merged


def user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


def step_a(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = profile["prompt"]
    a_cases = cases_by_step(CONTRACT_CASES, "A")
    if PROBE_SCOPE == "official":
        scoped = official_scope_cases(a_cases)
        return execute_contract_cases(
            model,
            user(prompt),
            scoped,
            baseline={"http_status": None},
            scope="official",
        )

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

    out.extend(
        execute_contract_cases(
            model,
            user(prompt),
            a_cases,
            baseline=r_on,
            scope="full",
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
        echo = next((c for c in CONTRACT_CASES if c.case_id == "A.multiturn"), None)
        out.append(
            row(
                step="A",
                case_id="A.multiturn",
                desc="多輪回傳完整 assistant",
                result=r2,
                verdict="pass" if r2.get("http_status") == 200 else "fail",
                contract=contract_note_for(echo) if echo else None,
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
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif r.get("tool_calls"):
        v, n = "pass", ""
    elif content_has_tool_call(r.get("content")):
        v, n = "fail", "工具呼叫寫在 content，沒有 tool_calls"
    else:
        v, n = "pass", "無 tool_calls（soft：模型可能選擇不呼叫）"
    out.append(
        row(
            step="C",
            case_id="C.auto",
            desc="tool_choice=auto",
            result=r,
            verdict=v,
            notes=n,
        )
    )
    r = post_chat(tools_payload("required"))
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif r.get("tool_calls"):
        v, n = "pass", ""
    elif content_has_tool_call(r.get("content")):
        v, n = "fail", "工具呼叫寫在 content，沒有 tool_calls"
    else:
        v, n = "noop", "required 無 tool_calls"
    out.append(
        row(step="C", case_id="C.required", desc="tool_choice=required", result=r, verdict=v, notes=n)
    )
    r = post_chat(tools_payload("none"))
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif r.get("tool_calls"):
        v, n = "noop", "none 仍有 tool_calls"
    elif content_has_tool_call(r.get("content")):
        v, n = "fail", "工具呼叫寫在 content，沒有 tool_calls"
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
    if r.get("http_status") != 200:
        v, n = "fail", "stream 應 200"
    else:
        v, n = reasoning_channel_verdict(r)
    out.append(
        row(
            step="D",
            case_id="D.reasoning_delta",
            desc="reasoning 獨立 delta",
            result=r,
            verdict=v,
            notes="" if v == "pass" else n,
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
    elif content_has_tool_call(r3.get("content")):
        v, n = "fail", "工具呼叫寫在 content，沒有 tool_calls"
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
    locked = cases_by_step(CONTRACT_CASES, "E")
    if locked:
        return execute_contract_cases(
            model,
            user(profile["prompt"]),
            locked,
            baseline={"http_status": None},
        )
    if PROBE_SCOPE == "official":
        return []

    r = post_chat(payload(model, user(profile["prompt"]), {"temperature": 0.2}))
    status = r.get("http_status")
    if status == 200:
        v, n = (
            "noop",
            "閘道接受 temperature=0.2；未比對輸出，不能證明 sampling 生效",
        )
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
        )
    ]


def step_f(model: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    combos = generate_thinking_stream_combos(MODEL_PROFILE)
    if combos:
        out.extend(
            execute_contract_cases(
                model,
                user(profile["prompt"]),
                combos,
                baseline={"http_status": None},
                scope="full",
            )
        )
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
    if r.get("http_status") != 200:
        v, n = "fail", ""
    elif r.get("tool_calls") or r.get("saw_tool_delta"):
        v, n = "pass", ""
    elif content_has_tool_call(r.get("content")):
        v, n = "fail", "工具呼叫寫在 content，沒有 tool_calls"
    else:
        v, n = "pass", "無 tool_calls（soft：auto 可以不叫）"
    out.append(
        row(
            step="F",
            case_id="F.stream_tools",
            desc="stream + tools",
            result=r,
            verdict=v,
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
STEPS_FULL = ("A", "B", "C", "D", "E", "F")
STEPS_OFFICIAL = ("A", "E")


def active_steps() -> tuple[str, ...]:
    if PROBE_SCOPE == "official":
        return STEPS_OFFICIAL
    return STEPS_FULL
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
        "來源": "configs/models/*.json；出處連結在各檔 source。PARAMS.md 是註解。",
        "profile_id": "命中的 family id；unknown＝只跑 baseline",
        "in_official": "false＝官方枚舉沒有此值",
        "finding": "gateway_wider_than_official＝閘道比官方寬，不代表模型官方支援",
        "decision": "rejected_locally＝enforce 模式本機 400，未送閘道",
        "expect": "accept＝允許且 2xx；reject＝應 4xx；effect＝還要看行為",
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
        "intensity_testable": "false＝本輪無法用思考量比較檔位（例如沒有 reasoning_tokens 且字數不可靠）",
    },
    "official": {
        "何時有": "official scope 的 effect 槍、full 的 A.official_*、以及 F.think_* 思考×串流組合",
        "matches_example": "true＝符合官方文件寫死的回覆欄位（有／無 content、reasoning_content）",
        "proof": "response_shape＝只證明欄位；unexpected_field＝不該出現的欄卻出現了；content_leak＝思考寫在 content；unmapped_provider_field＝只在 provider_specific_fields；truncated＝finish_reason=length",
        "proof_layer": "docs_response_fields＝文件寫死那層，不是範例作文全文",
        "source": "官網 URL",
        "not_proven": "模型輸出全文（非決定性；多數文件是占位字）；也不是 reasoning_tokens 檔位排名",
    },
    "probe_scope": {
        "official": "只測官方特殊參數＋文件寫死的回覆欄位／應拒 4xx；跳過 B–F 與未鎖定 temperature",
        "full": "A–F 通用考卷 + 契約槍（effect 可用思考量排名）",
    },
    "official_vs_gateway": "configs/models/*.json 的 source／official_examples 是廠商契約。閘道 200／枚舉較寬不代表官方有該檔。",
    "fault": {
        "何時有": "這槍不是正常完成，或 4xx／5xx 能分辨來源時",
        "class": "transport_timeout／upstream_closed／auth／rate_limit／gateway_param／gateway_bug／vllm_engine／client_reject／server_error／stream_error／embedded_error／malformed_response",
        "detail": "寫進 notes 的一句話。client_reject 的 4xx 若是官方應拒，verdict 仍是 pass",
    },
    "reasoning_obs": {
        "kind": "field＝有 reasoning 欄；content_leak＝思考寫在 content；provider_only＝只在 provider_specific_fields.reasoning；truncated＝finish_reason=length 且看不到思考；absent＝沒有",
    },
    "response_headers": "只留 x-litellm-call-id、x-litellm-model-id、x-litellm-model-api-base、x-litellm-version、x-request-id",
    "vllm": "有設 VLLM_BASE_URL 才會出現。flags 來自 GET /server_info，需要 vLLM 行程的 VLLM_SERVER_DEV_MODE=1。不存原始 body。",
    "litellm_request_debug": "true＝請求多了這個欄位，LiteLLM 把轉發 curl 印在 Proxy 日誌，不會回到這份 JSON。",
    "flow": "固定四段：對上模型 → vLLM 旗標 → 探測 → 歸類。conclusion 是這次能指名的部署問題；status=pass／fail／noop／empty。",
}


def collect_vllm_meta() -> dict[str, Any]:
    """直連 vLLM 的 /version 與 /server_info。失敗不中止探測。"""
    if not VLLM_BASE_URL:
        return {"configured": False}
    version_res = get_json_url(f"{VLLM_BASE_URL}/version", timeout_s=15)
    info_res = get_json_url(f"{VLLM_BASE_URL}/server_info", timeout_s=15)
    version = None
    if version_res["ok"] and isinstance(version_res["body"], dict):
        version = version_res["body"].get("version")
    flags = extract_vllm_flags(info_res["body"]) if info_res["ok"] else {}
    detail = None
    if not info_res["ok"]:
        detail = (
            "GET /server_info 失敗。vLLM 需設 VLLM_SERVER_DEV_MODE=1，"
            "而且這台機器要打得到 VLLM_BASE_URL（不是閘道 /v1）。"
        )
    return {
        "configured": True,
        "base_url": VLLM_BASE_URL,
        "version": version,
        "server_info_http": info_res["http_status"],
        "flags": flags,
        "detail": detail,
    }


def run() -> int:
    global MODEL_PROFILE, CONTRACT_CASES

    if not BASE_URL or not API_KEY:
        print(
            "請設環境變數 OPENAI_BASE_URL、OPENAI_API_KEY（export 或專案根目錄 .env）",
            file=sys.stderr,
        )
        return 2
    if PROBE_MODE not in ("probe", "enforce"):
        print(f"PROBE_MODE 只能是 probe 或 enforce（收到 {PROBE_MODE}）", file=sys.stderr)
        return 2
    if PROBE_SCOPE not in ("official", "full"):
        print(f"PROBE_SCOPE 只能是 official 或 full（收到 {PROBE_SCOPE}）", file=sys.stderr)
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

    exam = load_profile()
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
    catalogs = load_profiles(MODELS_DIR)
    MODEL_PROFILE = match_profile(model, catalogs)
    CONTRACT_CASES = generate_contract_cases(
        MODEL_PROFILE,
        probe_mode=PROBE_MODE,
        effort_override=requested_effort(),
    )
    effort_param = MODEL_PROFILE.param("reasoning_effort")
    blocked = [c.value for c in CONTRACT_CASES if c.enforce_local]
    print(f"probe_mode={PROBE_MODE}")
    print(f"probe_scope={PROBE_SCOPE}")
    print(f"profile={MODEL_PROFILE.id}")
    print(f"contract={MODEL_PROFILE.family or '(未知：只跑 baseline)'}")
    official = ",".join(str(v) for v in (effort_param.values if effort_param else ())) or "(無)"
    print(f"official_effort={official}")
    print(f"reasoning_effort={','.join(effort_values(CONTRACT_CASES)) or '(不送此欄)'}")
    if blocked:
        print(f"blocked_locally={','.join(str(v) for v in blocked)}")
    if ENABLE_THINKING_ENV:
        print("ENABLE_THINKING env 已忽略：改由 Profile flag 產生獨立對照槍，不再汙染全程")
    if PROBE_SCOPE == "official":
        scoped = official_scope_cases(CONTRACT_CASES)
        print("official scope：只測官方特殊參數＋文件寫死的回覆欄位（不比全文、不用思考量排名）")
        if not scoped:
            print("此模型沒有官方特殊參數／官網範例；official scope 不會打 B–F")
    stream_label = (
        "true"
        if STREAM_STEPS is True
        else "false"
        if STREAM_STEPS is False
        else "default(D/F only)"
    )
    print(f"stream_steps={stream_label}")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    tag = model.replace("/", "_")
    out_path = ROOT / "results" / f"flowchart_{tag}_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    steps = active_steps()
    report: dict[str, Any] = {
        "meta": {
            "base_url": BASE_URL,
            "profile": "generic",
            "model_profile": MODEL_PROFILE.id,
            "probe_mode": PROBE_MODE,
            "probe_scope": PROBE_SCOPE,
            "contract": {
                "family": MODEL_PROFILE.family,
                "known": MODEL_PROFILE.known,
                "profile_id": MODEL_PROFILE.id,
                "official_effort": list(effort_param.values) if effort_param else None,
                "effort_default": effort_param.default if effort_param else None,
                "can_disable_thinking": effort_param.can_disable if effort_param else None,
                "length_param": MODEL_PROFILE.request_defaults.length_param,
                "note": MODEL_PROFILE.note,
                "source": list(MODEL_PROFILE.source),
            },
            "effort": effort_values(CONTRACT_CASES),
            "effort_blocked_locally": [str(v) for v in blocked],
            "stream_steps": (
                True if STREAM_STEPS is True else False if STREAM_STEPS is False else "default"
            ),
            "steps": list(steps),
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "read": JSON_READ,
            "vllm": collect_vllm_meta(),
            "litellm_request_debug": LITELLM_REQUEST_DEBUG,
        },
        "models": {},
    }
    vllm_meta = report["meta"]["vllm"]
    if vllm_meta.get("configured"):
        print(f"vllm={vllm_meta.get('base_url')} flags={vllm_meta.get('flags')}")
        if vllm_meta.get("detail"):
            print(vllm_meta["detail"])
    if LITELLM_REQUEST_DEBUG:
        print("litellm_request_debug=true（轉發 body 在 Proxy 日誌，用 x-litellm-call-id 對）")

    print("-" * 80)
    print(f"RUN {model} steps={list(steps)}")
    results: list[dict[str, Any]] = []
    for letter in steps:
        chunk = RUNNERS[letter](model, exam)
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
        "effort": effort_values(CONTRACT_CASES),
        "results": results,
    }

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    report["meta"]["counts"] = {VERDICT_ZH.get(k, k): v for k, v in counts.items()}
    report["meta"]["flow"] = build_flow(
        model=model,
        profile_id=MODEL_PROFILE.id,
        family=MODEL_PROFILE.family,
        probe_scope=PROBE_SCOPE,
        vllm=report["meta"]["vllm"],
        results=results,
        litellm_request_debug=LITELLM_REQUEST_DEBUG,
    )
    report["meta"]["finished_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = write_report_md(out_path, report)
    print("-" * 80)
    print(render_flow_md(report["meta"]["flow"]).rstrip())
    print("-" * 80)
    print("counts:", report["meta"]["counts"])
    for model_name, block in report["models"].items():
        print()
        print(f"## {model_name}")
        print(render_markdown_table(comparison_rows(block.get("results") or [])))
    print()
    print("wrote", out_path)
    print("table", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
