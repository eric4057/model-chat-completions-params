"""流程歸類。不打閘道。"""

from __future__ import annotations

from probe.flow import build_flow, render_flow_md
from probe.report_table import output_error


def _row(case_id: str, **fields: object) -> dict:
    return {"case_id": case_id, "verdict": "pass", **fields}


def test_content_leak_becomes_the_conclusion() -> None:
    flow = build_flow(
        model="GLM-5.2",
        profile_id="glm-5",
        family="Zhipu GLM-5",
        probe_scope="official",
        vllm={"configured": False},
        results=[
            _row(
                "A.type_enabled",
                verdict="fail",
                reasoning_obs={"kind": "content_leak"},
            ),
            _row(
                "A.effort_none",
                verdict="fail",
                official={"proof": "content_leak"},
            ),
            _row("A.effort_max", verdict="pass", reasoning_obs={"kind": "field"}),
        ],
    )
    assert flow["status"] == "fail"
    assert "思考寫在 content" in flow["conclusion"]
    leak = next(item for item in flow["findings"] if item["id"] == "content_leak")
    assert leak["case_ids"] == ["A.type_enabled", "A.effort_none"]
    text = render_flow_md(flow)
    assert "1. 對上模型：GLM-5.2 → glm-5" in text
    assert "未設 VLLM_BASE_URL" in text
    assert "A.type_enabled" in text


def test_empty_parser_is_linked_to_content_leak() -> None:
    flow = build_flow(
        model="gemma-4-31B-it",
        profile_id="gemma-4",
        family="Gemma 4",
        probe_scope="official",
        vllm={"configured": True, "version": "0.10.0", "flags": {"reasoning_parser": None}},
        results=[_row("A.enable_thinking_true", verdict="fail", reasoning_obs={"kind": "content_leak"})],
    )
    vllm = next(stage for stage in flow["stages"] if stage["id"] == "vllm")
    assert "reasoning_parser 是空的" in vllm["summary"]
    assert "沒掛 reasoning parser" in vllm["summary"]


def test_set_parser_still_asks_for_proxy_log() -> None:
    flow = build_flow(
        model="gemma-4-31B-it",
        profile_id="gemma-4",
        family="Gemma 4",
        probe_scope="official",
        vllm={"configured": True, "flags": {"reasoning_parser": "gemma4"}},
        results=[_row("A.enable_thinking_true", verdict="fail", reasoning_obs={"kind": "content_leak"})],
    )
    vllm = next(stage for stage in flow["stages"] if stage["id"] == "vllm")
    assert "gemma4" in vllm["summary"]
    assert "x-litellm-call-id" in vllm["summary"]


def test_only_truncation_stays_inconclusive() -> None:
    flow = build_flow(
        model="m",
        profile_id="unknown",
        family=None,
        probe_scope="official",
        vllm={"configured": False},
        results=[_row("A.effort_low", verdict="noop", reasoning_obs={"kind": "truncated"})],
    )
    assert flow["status"] == "noop"
    assert "length" in flow["conclusion"]


def test_clean_run_passes() -> None:
    flow = build_flow(
        model="m",
        profile_id="kimi-k3",
        family="Kimi",
        probe_scope="official",
        vllm={"configured": False},
        results=[_row("A.effort_low", verdict="pass", reasoning_obs={"kind": "field"})],
        litellm_request_debug=True,
    )
    assert flow["status"] == "pass"
    assert flow["findings"] == []
    probe = next(stage for stage in flow["stages"] if stage["id"] == "probe")
    assert "litellm_request_debug" in probe["summary"]


def test_auth_and_skipped_param_are_named() -> None:
    flow = build_flow(
        model="m",
        profile_id="glm-5",
        family="Zhipu GLM-5",
        probe_scope="full",
        vllm={"configured": False},
        results=[
            _row("A.effort_low", verdict="fail", fault={"class": "auth"}),
            _row("A.effort_high", verdict="skip", fault={"class": "client_reject"}),
        ],
    )
    ids = [item["id"] for item in flow["findings"]]
    assert ids == ["auth", "param_rejected"]
    assert flow["status"] == "fail"


def test_field_mismatch_is_named_in_the_conclusion() -> None:
    flow = build_flow(
        model="GLM-5.3-high",
        profile_id="glm-5.3",
        family="Zhipu GLM-5.3",
        probe_scope="official",
        vllm={"configured": False},
        results=[
            _row(
                "A.type_enabled",
                verdict="fail",
                notes="不符合官方寫死的回覆欄位：缺 content",
                official={"matches_example": False, "proof": "shape_mismatch"},
                reasoning_obs={"kind": "field"},
            ),
            _row(
                "A.effort_low",
                verdict="fail",
                notes="不符合官方寫死的回覆欄位：缺 reasoning_content",
                official={"matches_example": False, "proof": "shape_mismatch"},
                reasoning_obs={"kind": "absent"},
            ),
        ],
    )
    assert flow["status"] == "fail"
    assert flow["conclusion"] == "回覆欄位跟官方契約不一致"
    mismatch = next(item for item in flow["findings"] if item["id"] == "shape_mismatch")
    assert mismatch["case_ids"] == ["A.type_enabled", "A.effort_low"]


def test_output_error_mentions_each_missing_field_once() -> None:
    text = output_error(
        {
            "verdict": "fail",
            "notes": "不符合官方寫死的回覆欄位：缺 content",
            "official": {
                "matches_example": False,
                "proof": "shape_mismatch",
                "shape": {"content": {"ok": False}, "reasoning_content": {"ok": True}},
            },
        }
    )
    assert text == "FAIL；不符合官方寫死的回覆欄位：缺 content"
    assert "shape_mismatch" not in text


def test_no_cases_is_an_empty_flow() -> None:
    flow = build_flow(
        model="embed-1",
        profile_id="unknown",
        family=None,
        probe_scope="official",
        vllm=None,
        results=[],
    )
    assert flow["status"] == "empty"
    assert "沒有契約槍" in flow["conclusion"]


def _run_all() -> int:
    import traceback

    failed = 0
    names = [name for name in globals() if name.startswith("test_") and callable(globals()[name])]
    for name in sorted(names):
        try:
            globals()[name]()
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"ok {name}")
    return failed


if __name__ == "__main__":
    raise SystemExit(_run_all())
