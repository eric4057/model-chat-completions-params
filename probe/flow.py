"""一次探測的固定流程：對上模型 → vLLM 旗標 → 打契約槍 → 歸類問題。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# 歸類順序就是報告順序。後面三項只代表這槍無法下結論。
_INCONCLUSIVE = frozenset({"still_on", "truncated", "not_effective"})


def _kind(row: dict[str, Any]) -> str | None:
    obs = row.get("reasoning_obs") or {}
    if isinstance(obs, dict):
        kind = obs.get("kind")
        return str(kind) if kind else None
    return None


def _proof(row: dict[str, Any]) -> str | None:
    official = row.get("official") or {}
    if isinstance(official, dict) and official.get("proof"):
        return str(official["proof"])
    return None


def _fault(row: dict[str, Any]) -> str | None:
    fault = row.get("fault") or {}
    if isinstance(fault, dict) and fault.get("class"):
        return str(fault["class"])
    return None


def _contract_finding(row: dict[str, Any]) -> str | None:
    contract = row.get("contract") or {}
    if isinstance(contract, dict) and contract.get("finding"):
        return str(contract["finding"])
    return None


def _case_id(row: dict[str, Any]) -> str:
    return str(row.get("case_id") or "?")


_CHECKS: tuple[tuple[str, str, Callable[[dict[str, Any]], bool]], ...] = (
    ("auth", "認證或權限失敗，請求沒有進到模型", lambda row: _fault(row) == "auth"),
    ("rate_limit", "閘道限流", lambda row: _fault(row) == "rate_limit"),
    (
        "transport",
        "連線逾時或上游斷線",
        lambda row: _fault(row) in {"transport_timeout", "upstream_closed"},
    ),
    ("vllm_engine", "vLLM 引擎或 parser 報錯", lambda row: _fault(row) == "vllm_engine"),
    ("gateway_bug", "LiteLLM 自己報錯", lambda row: _fault(row) == "gateway_bug"),
    (
        "gateway_param",
        "LiteLLM 沒放行這個參數",
        lambda row: _fault(row) == "gateway_param",
    ),
    (
        "server_error",
        "伺服器 5xx，訊息對不上 vLLM 或 LiteLLM",
        lambda row: _fault(row) == "server_error",
    ),
    (
        "stream_error",
        "連線成功，生成中途失敗",
        lambda row: _fault(row) in {"stream_error", "embedded_error"},
    ),
    (
        "malformed",
        "HTTP 200 但回覆沒有 choices 或不是 JSON",
        lambda row: _fault(row) == "malformed_response",
    ),
    (
        "content_leak",
        "思考寫在 content，reasoning 欄位是空的",
        lambda row: _kind(row) == "content_leak" or _proof(row) == "content_leak",
    ),
    (
        "unmapped_provider_field",
        "推理在 provider_specific_fields，沒有映射到 reasoning 欄位",
        lambda row: _kind(row) == "provider_only" or _proof(row) == "unmapped_provider_field",
    ),
    (
        "gateway_wider",
        "閘道比官方寬，接受了官方應拒的值",
        lambda row: _contract_finding(row) == "gateway_wider_than_official",
    ),
    ("param_rejected", "閘道 4xx，不收這個官方參數", lambda row: row.get("verdict") == "skip"),
    (
        "still_on",
        "reasoning 欄還在，這槍沒有證明開關或檔位生效",
        lambda row: row.get("verdict") == "noop" and _kind(row) == "field",
    ),
    (
        "truncated",
        "finish_reason=length，這幾槍無法判斷思考有沒有產生",
        lambda row: _kind(row) == "truncated" or _proof(row) == "truncated",
    ),
    (
        "not_effective",
        "HTTP 200 但沒有 reasoning 欄，分不出是參數被丟還是 parser 沒拆",
        lambda row: row.get("verdict") == "noop" and _kind(row) in {None, "absent"},
    ),
    (
        "shape_mismatch",
        "回覆欄位跟官方契約不一致",
        lambda row: _shape_mismatch(row),
    ),
)


def _shape_mismatch(row: dict[str, Any]) -> bool:
    """欄位對不上，而且還沒被更具體的錯誤歸類。"""
    if row.get("verdict") != "fail":
        return False
    if _fault(row) or _contract_finding(row):
        return False
    if _kind(row) in {"content_leak", "provider_only", "truncated"}:
        return False
    if _proof(row) in {"content_leak", "unmapped_provider_field", "truncated"}:
        return False
    official = row.get("official") or {}
    if isinstance(official, dict) and official.get("matches_example") is False:
        return True
    return _proof(row) in {"shape_mismatch", "http_mismatch", "unexpected_field"}


def collect_findings(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for problem_id, summary, matches in _CHECKS:
        case_ids = [_case_id(row) for row in results if matches(row)]
        if not case_ids:
            continue
        findings.append({"id": problem_id, "summary": summary, "case_ids": case_ids})
    return findings


def _conclude(findings: list[dict[str, Any]], result_count: int) -> tuple[str, str]:
    if result_count == 0:
        return "empty", "這次沒有契約槍。這個 family 沒有官方特殊參數，或 official 範圍是空的"
    actionable = [item for item in findings if item["id"] not in _INCONCLUSIVE]
    inconclusive = [item for item in findings if item["id"] in _INCONCLUSIVE]
    if actionable:
        text = "；".join(item["summary"] for item in actionable)
        if inconclusive:
            text += f"。另外 {sum(len(item['case_ids']) for item in inconclusive)} 槍無法下結論"
        return "fail", text
    if inconclusive:
        return "noop", "；".join(item["summary"] for item in inconclusive)
    return "pass", "這次契約槍沒有看到部署問題"


def _vllm_stage(vllm: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, str]:
    if not vllm.get("configured"):
        return {
            "id": "vllm",
            "title": "vLLM 旗標",
            "status": "skipped",
            "summary": "未設 VLLM_BASE_URL，這段看不到啟動旗標",
        }
    if vllm.get("detail"):
        return {
            "id": "vllm",
            "title": "vLLM 旗標",
            "status": "fail",
            "summary": str(vllm["detail"]),
        }
    flags = vllm.get("flags") or {}
    parser = flags.get("reasoning_parser") or flags.get("reasoning_backend")
    tool_parser = flags.get("tool_call_parser")
    bits = [
        f"reasoning_parser={parser}" if parser else "reasoning_parser 是空的",
    ]
    if tool_parser:
        bits.append(f"tool_call_parser={tool_parser}")
    summary = "、".join(bits)
    leaked = any(item["id"] == "content_leak" for item in findings)
    if leaked and not parser:
        summary += "。思考留在 content，和沒掛 reasoning parser 一致"
    elif leaked and parser:
        summary += (
            f"。已掛 {parser}，思考仍留在 content："
            "用 x-litellm-call-id 對 Proxy 日誌，看參數有沒有轉發"
        )
    version = vllm.get("version")
    if version:
        summary = f"vLLM {version}；{summary}"
    return {"id": "vllm", "title": "vLLM 旗標", "status": "done", "summary": summary}


def _count_text(results: list[dict[str, Any]]) -> str:
    labels = (
        ("pass", "可用"),
        ("fail", "不可用"),
        ("noop", "接受但未生效"),
        ("skip", "跳過"),
    )
    counts = {key: 0 for key, _ in labels}
    for row in results:
        verdict = str(row.get("verdict") or "")
        if verdict in counts:
            counts[verdict] += 1
    parts = [f"{label} {counts[key]}" for key, label in labels if counts[key]]
    detail = "、".join(parts) if parts else "沒有判定"
    return f"{len(results)} 槍（{detail}）"


def build_flow(
    *,
    model: str,
    profile_id: str,
    family: str | None,
    probe_scope: str,
    vllm: dict[str, Any] | None,
    results: list[dict[str, Any]],
    litellm_request_debug: bool = False,
) -> dict[str, Any]:
    """純函數。呼叫端負責打閘道；這裡只把已經拿到的結果排成流程。"""
    findings = collect_findings(results)
    status, conclusion = _conclude(findings, len(results))
    family_text = family or "沒有對上官方契約"
    probe_summary = f"{probe_scope}，{_count_text(results)}"
    if litellm_request_debug:
        probe_summary += "。已送 litellm_request_debug，轉發 body 在 Proxy 日誌"
    stages = [
        {
            "id": "match",
            "title": "對上模型",
            "status": "done",
            "summary": f"{model} → {profile_id}（{family_text}）",
        },
        _vllm_stage(vllm or {}, findings),
        {
            "id": "probe",
            "title": "探測",
            "status": "empty" if not results else "done",
            "summary": probe_summary,
        },
        {
            "id": "classify",
            "title": "歸類",
            "status": status,
            "summary": conclusion,
        },
    ]
    return {
        "stages": stages,
        "findings": findings,
        "status": status,
        "conclusion": conclusion,
    }


def render_flow_md(flow: dict[str, Any]) -> str:
    lines = ["## 流程", ""]
    for index, stage in enumerate(flow.get("stages") or [], 1):
        lines.append(f"{index}. {stage.get('title')}：{stage.get('summary')}")
    findings = flow.get("findings") or []
    if findings:
        lines.append("")
        lines.append("歸類明細：")
        for item in findings:
            ids = "、".join(item.get("case_ids") or [])
            lines.append(f"- {item.get('summary')}（{ids}）")
    lines.append("")
    lines.append(f"結論：{flow.get('conclusion') or ''}")
    lines.append("")
    return "\n".join(lines)
