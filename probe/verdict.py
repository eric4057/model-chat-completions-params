"""HTTP-agnostic verdict helpers and effect observers."""

from __future__ import annotations

from typing import Any


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


def verdict_http(expect: str, status: int | None, *, what: str) -> tuple[str, str]:
    """accept/effect 要 200；reject 要 4xx。effect 的行為判定另走 refine_effect。"""
    if expect == "reject":
        v, n = verdict_expect_reject(status)
        if v == "noop":
            return "fail", f"契約不符：{what} 官方應拒，閘道卻 HTTP {status}"
        return v, n
    if status == 200:
        return "pass", ""
    return verdict_non_200(status, what)


def has_reasoning(result: dict[str, Any]) -> bool:
    return bool(result.get("reasoning") or result.get("reasoning_content"))


def _official_field_observed(result: dict[str, Any], name: str) -> dict[str, Any]:
    """官網範例欄位是否非空。reasoning_content／reasoning 互為閘道別名。"""
    keys = tuple(result.get("message_keys") or [])
    if name in ("reasoning", "reasoning_content"):
        nonempty = has_reasoning(result)
        present_key = None
        if result.get("reasoning_content"):
            present_key = "reasoning_content"
        elif result.get("reasoning"):
            present_key = "reasoning"
        return {
            "expected": "nonempty",
            "ok": nonempty,
            "observed_key": present_key,
            "alias_accepted": nonempty and present_key is not None and present_key != name,
        }
    if name == "content":
        text = (result.get("content") or "").strip()
        return {"expected": "nonempty", "ok": bool(text), "observed_key": "content" if text else None}
    if name == "tool_calls":
        ok = bool(result.get("tool_calls"))
        return {"expected": "nonempty", "ok": ok, "observed_key": "tool_calls" if ok else None}
    ok = name in keys
    return {"expected": "present", "ok": ok, "observed_key": name if ok else None, "message_keys": keys}


def match_official_example(
    result: dict[str, Any],
    spec: dict[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    """比對官網範例的回傳形狀。不比全文（官網範例幾乎都是 placeholder）。"""
    spec = spec or {}
    fields = [str(f) for f in (spec.get("message_fields") or ())]
    shape = {name: _official_field_observed(result, name) for name in fields}
    missing = [name for name, detail in shape.items() if not detail.get("ok")]
    evidence: dict[str, Any] = {
        "id": spec.get("id"),
        "source": spec.get("source"),
        "docs_quote": spec.get("docs_quote") or "",
        "prompt": spec.get("prompt"),
        "request_extra": spec.get("request") or {},
        "shape": shape,
        "matches_example": not missing,
        "proof": "response_shape",
        "proof_layer": spec.get("proof_layer") or "docs_response_fields",
        "not_proven": "官方範例輸出全文（模型非決定性，且多數文件是占位字）",
    }
    if result.get("http_status") != int(spec.get("expect_http") or 200):
        evidence["matches_example"] = False
        evidence["proof"] = "http_mismatch"
        return (
            "fail",
            f"官網範例應 HTTP {spec.get('expect_http') or 200}，實際 {result.get('http_status')}",
            evidence,
        )
    if missing:
        evidence["proof"] = "shape_mismatch"
        evidence["matches_example"] = False
        return (
            "fail",
            "不符合官方寫死的回覆欄位：缺 " + ",".join(missing),
            evidence,
        )
    extra_present: list[str] = []
    absent_report: dict[str, Any] = {}
    for name in [str(f) for f in (spec.get("absent_fields") or ())]:
        observed = _official_field_observed(result, name)
        should_miss = not observed.get("ok")
        absent_report[name] = {
            "expected": "absent",
            "ok": should_miss,
            "observed_key": observed.get("observed_key"),
        }
        if not should_miss:
            extra_present.append(name)
    if absent_report:
        evidence["absent"] = absent_report
    if extra_present:
        evidence["matches_example"] = False
        evidence["proof"] = "unexpected_field"
        return (
            "fail",
            "不符合官方寫死的回覆：不應出現 " + ",".join(extra_present),
            evidence,
        )
    evidence["matches_example"] = True
    alias_notes = [
        f"{name} 閘道欄名是 {detail.get('observed_key')}"
        for name, detail in shape.items()
        if detail.get("alias_accepted")
    ]
    note = "符合官方文件寫死的回覆欄位（非全文）"
    if alias_notes:
        note += "；" + "；".join(alias_notes)
    return "pass", note, evidence


def official_reply_spec(case: Any, *, source: str) -> dict[str, Any]:
    """文件寫死的回覆層：欄位有／無，不是範例作文。"""
    if getattr(case, "official", None):
        spec = dict(case.official)
        spec.setdefault("expect_http", 200)
        spec.setdefault("request", dict(case.extra or {}))
        return spec
    base: dict[str, Any] = {
        "id": case.case_id,
        "source": source,
        "request": dict(case.extra or {}),
        "expect_http": 200,
        "proof_layer": "docs_response_fields",
    }
    if case.disable or case.effect in ("no_reasoning",):
        return {
            **base,
            "docs_quote": "官方：關閉思考後不回 reasoning",
            "message_fields": ["content"],
            "absent_fields": ["reasoning_content"],
        }
    if case.effect in ("has_reasoning", "reasoning_tokens_rank", "official_shape"):
        return {
            **base,
            "docs_quote": "官方：回 content 與 reasoning／reasoning_content",
            "message_fields": ["content", "reasoning_content"],
        }
    return {**base, "message_fields": []}


def reason_chars(result: dict[str, Any]) -> int:
    return len(result.get("reasoning") or result.get("reasoning_content") or "")


def reasoning_tokens(result: dict[str, Any]) -> int | None:
    det = (result.get("usage") or {}).get("completion_tokens_details") or {}
    tok = det.get("reasoning_tokens")
    return int(tok) if isinstance(tok, (int, float)) else None


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
    this_tok, other_tok = reasoning_tokens(shot), reasoning_tokens(baseline)
    if this_tok is not None and other_tok is not None:
        bigger = max(this_tok, other_tok, 1)
        out.update(
            metric="reasoning_tokens",
            this=this_tok,
            other=other_tok,
            similar=abs(this_tok - other_tok) <= max(8, bigger * 30 // 100),
        )
        return out
    this_chars, other_chars = reason_chars(shot), reason_chars(baseline)
    bigger = max(this_chars, other_chars, 1)
    out.update(
        metric="reasoning_chars",
        this=this_chars,
        other=other_chars,
        similar=abs(this_chars - other_chars) <= max(40, bigger * 30 // 100),
    )
    return out


def looks_like_baseline(shot: dict[str, Any], baseline: dict[str, Any]) -> bool:
    if has_reasoning(shot) != has_reasoning(baseline):
        return False
    return bool(similar_thinking(shot, baseline).get("similar"))


def refine_effect(
    *,
    effect: str | None,
    disable: bool,
    can_disable: bool | None,
    value: Any,
    default: Any,
    result: dict[str, Any],
    baseline: dict[str, Any],
    group_results: dict[Any, dict[str, Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    """把 200 的 effect case 收成 pass/noop/fail。HTTP 非 200 不應進來。"""
    if effect in (None, "http_ok"):
        return "pass", "", None

    if effect == "no_reasoning" or (effect == "has_reasoning" and disable):
        if has_reasoning(result):
            if can_disable is False:
                return "fail", "契約不符：官方不允許關 thinking，閘道卻仍有 reasoning", None
            return "noop", "關閉後仍有 reasoning", None
        if not has_reasoning(baseline):
            return "noop", "預設槍本來就沒有 reasoning，證明不了關閉有作用", None
        return "pass", "無 reasoning 欄位", None

    if effect == "hidden_reasoning":
        if has_reasoning(result):
            return "noop", "include_reasoning=false 仍回傳 reasoning 欄位", None
        if not (result.get("content") or "").strip():
            return "noop", "沒有 reasoning 欄位，但也沒有 content，無法證明只是隱藏", None
        return "pass", "無 reasoning 欄位且仍有 content", None

    if effect == "has_reasoning":
        if has_reasoning(result):
            return "pass", "有 reasoning 欄位", None
        return "noop", "無 reasoning 欄位（查閘道是否剝除，或參數未注入）", None

    if effect == "reasoning_tokens_rank":
        compare = _rank_compare(result, baseline, group_results)
        if not has_reasoning(result):
            compare["vs_default"] = "unknown"
            return (
                "noop",
                f"完全沒有 reasoning，無法比強度（reason_chars={reason_chars(result)}）",
                compare,
            )
        like_default = compare["vs_default"] == "like_default"
        like_max = compare["vs_max"] == "like_max"
        max_is_default = bool(group_results.get("max")) and looks_like_baseline(
            group_results["max"], baseline
        )
        if like_default or (max_is_default and like_max) or value == default:
            if like_default or value == default:
                compare["vs_default"] = "like_default"
            return (
                "noop",
                "與預設（或 max）無差，可能忽略此值或此檔就是預設",
                compare,
            )
        return (
            "pass",
            "異於預設；不能證明就是獨立檔位，只能證明不是預設",
            compare,
        )

    return "pass", f"未實作的 effect={effect}，僅證明 HTTP 200", None


def _rank_compare(
    result: dict[str, Any],
    baseline: dict[str, Any],
    group_results: dict[Any, dict[str, Any]],
) -> dict[str, Any]:
    vs_def = similar_thinking(result, baseline)
    max_shot = group_results.get("max")
    vs_max = similar_thinking(result, max_shot) if max_shot is not None else None
    like_default = bool(vs_def.get("similar"))
    like_max = bool(vs_max and vs_max.get("similar"))
    return {
        "vs_default": "like_default" if like_default else "not_default",
        "vs_max": (
            None if vs_max is None else ("like_max" if like_max else "not_like_max")
        ),
        "metric": vs_def.get("metric"),
        "this": vs_def.get("this"),
        "default": vs_def.get("other"),
        "max": None if vs_max is None else vs_max.get("other"),
        "intensity_testable": True,
    }
