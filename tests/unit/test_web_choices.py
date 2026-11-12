"""閘道選擇與可勾選參數。不打網路。"""

from __future__ import annotations

import json

from probe.choices import apply_sampling, format_chat_call, parameter_choices, select_cases
from probe.gateways import overlay_endpoint, present_endpoints, resolve_endpoints
from probe.profile import load_profiles, match_profile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = load_profiles(ROOT / "configs" / "models")


def test_inner_reuses_shared_key_only_when_url_matches() -> None:
    endpoints = resolve_endpoints(
        {
            "OPENAI_BASE_URL": "https://inner-medusa.genai.nchc.org.tw/v1",
            "OPENAI_API_KEY": "inner-secret",
        }
    )
    by_id = {item.id: item for item in endpoints}
    assert by_id["inner"].configured is True
    assert by_id["inner"].api_key == "inner-secret"
    assert by_id["portal"].configured is False
    assert by_id["portal"].api_key == ""
    public = by_id["inner"].public()
    assert public["host"] == "inner-medusa.genai.nchc.org.tw"
    assert "api_key" not in public
    assert "secret" not in str(public)


def test_web_does_not_preload_api_key() -> None:
    env = {
        "OPENAI_BASE_URL": "https://inner-medusa.genai.nchc.org.tw/v1",
        "OPENAI_API_KEY": "inner-secret",
        "PORTAL_API_KEY": "portal-secret",
    }
    shown = present_endpoints(env)
    assert [item.api_key for item in shown] == ["", ""]
    assert all(item.configured is False for item in shown)
    typed = overlay_endpoint(shown[0], api_key="typed-by-user")
    again = present_endpoints(env, {"inner": typed})
    assert again[0].api_key == "typed-by-user"
    assert again[1].api_key == ""


def test_portal_key_is_separate() -> None:
    endpoints = resolve_endpoints(
        {
            "OPENAI_BASE_URL": "https://inner-medusa.genai.nchc.org.tw/v1",
            "OPENAI_API_KEY": "inner-secret",
            "PORTAL_API_KEY": "portal-secret",
        }
    )
    portal = next(item for item in endpoints if item.id == "portal")
    assert portal.configured is True
    assert portal.api_key == "portal-secret"


def test_overlay_portal_keeps_key_off_the_page() -> None:
    portal = next(item for item in resolve_endpoints({}) if item.id == "portal")
    assert portal.configured is False
    try:
        overlay_endpoint(portal, base_url="notaurl", api_key="portal-secret")
    except ValueError as exc:
        assert "http" in str(exc)
    else:
        raise AssertionError("應拒絕不合法網址")
    filled = overlay_endpoint(portal, api_key="portal-secret")
    assert filled.configured is True
    assert filled.base_url == portal.base_url
    public = filled.public()
    assert public["base_url"] == portal.base_url
    assert "api_key" not in public
    assert "portal-secret" not in str(public)
    kept = overlay_endpoint(filled, base_url="https://portal.example/v1", api_key="")
    assert kept.api_key == "portal-secret"
    assert kept.base_url == "https://portal.example/v1"


def test_gemma_and_kimi_choices() -> None:
    gemma = parameter_choices(match_profile("gemma-4-31B-it", PROFILES))
    gemma_ids = [choice["case_id"] for group in gemma["groups"] for choice in group["choices"]]
    assert "A.enable_thinking_true" in gemma_ids
    assert "A.enable_thinking_false" in gemma_ids
    assert not any(case_id.startswith("A.official_") for case_id in gemma_ids)
    assert "official_example" not in {group["param"] for group in gemma["groups"]}

    kimi = parameter_choices(match_profile("Kimi-K3", PROFILES))
    kimi_ids = [choice["case_id"] for group in kimi["groups"] for choice in group["choices"]]
    assert "A.effort_low" in kimi_ids
    assert "A.effort_high" in kimi_ids
    assert "A.effort_max" in kimi_ids
    assert "A.effort_none" not in kimi_ids
    assert "A.effort_medium" not in kimi_ids
    assert "A.effort_invalid" not in kimi_ids
    assert "E.temperature" not in kimi_ids
    assert "A.forbidden_thinking" not in kimi_ids
    selected = select_cases(match_profile("Kimi-K3", PROFILES), ["A.effort_low"])
    assert [case.case_id for case in selected] == ["A.effort_low"]
    assert len(select_cases(match_profile("gemma-4-31B-it", PROFILES), ["ALL"])) == len(gemma_ids)


def test_glm_5_3_is_not_the_5_2_contract() -> None:
    five_three = match_profile("GLM-5.3", PROFILES)
    assert five_three.id == "glm-5.3"
    assert match_profile("GLM-5.3-high", PROFILES).id == "glm-5.3"
    ids = [choice["case_id"] for group in parameter_choices(five_three)["groups"] for choice in group["choices"]]
    assert "A.type_enabled" in ids
    assert "A.effort_low" in ids
    assert "A.effort_high" in ids
    assert "A.effort_max" in ids
    assert "A.type_disabled" not in ids
    assert "A.effort_xhigh" not in ids
    assert "A.effort_medium" not in ids
    assert "A.effort_minimal" not in ids
    assert "A.effort_none" not in ids

    five_two = match_profile("GLM-5.2", PROFILES)
    assert five_two.id == "glm-5"
    two_ids = [choice["case_id"] for group in parameter_choices(five_two)["groups"] for choice in group["choices"]]
    assert "A.type_disabled" in two_ids
    assert "A.effort_xhigh" in two_ids
    assert "A.effort_none" in two_ids


def test_glm_5_2_crosses_official_thinking_params() -> None:
    cases = select_cases(match_profile("GLM-5.2", PROFILES), ["ALL"])
    by_id = {case.case_id: case for case in cases}
    assert "X.type_enabled__effort_max" in by_id
    assert "X.type_disabled__effort_max" in by_id
    assert "X.type_enabled__effort_none" in by_id
    assert "X.type_enabled__effort_minimal" in by_id
    on = by_id["X.type_enabled__effort_max"]
    assert on.extra == {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}
    assert on.official["message_fields"] == ["content", "reasoning_content"]
    assert on.official["absent_fields"] == []
    off = by_id["X.type_enabled__effort_none"]
    assert off.official["absent_fields"] == ["reasoning_content"]
    assert by_id["X.type_disabled__effort_max"].official["absent_fields"] == ["reasoning_content"]

    gemma = select_cases(match_profile("gemma-4-31B-it", PROFILES), ["ALL"])
    assert not any(case.case_id.startswith("X.") for case in gemma)
    kimi = select_cases(match_profile("Kimi-K3", PROFILES), ["ALL"])
    assert not any(case.case_id.startswith("X.") for case in kimi)

    flash = select_cases(match_profile("GLM-5.3", PROFILES), ["ALL"])
    flash_ids = {case.case_id for case in flash}
    assert "X.type_enabled__effort_low" in flash_ids
    assert "X.type_disabled__effort_low" not in flash_ids


def test_minimax_models_follow_their_own_docs() -> None:
    m3 = parameter_choices(match_profile("MiniMax-M3", PROFILES))
    m3_ids = [choice["case_id"] for group in m3["groups"] for choice in group["choices"]]
    assert m3["profile_id"] == "minimax-m3"
    assert "A.type_adaptive" in m3_ids
    assert "A.type_disabled" in m3_ids
    assert "A.type_enabled" not in m3_ids
    assert "A.effort_max" not in m3_ids

    m2 = parameter_choices(match_profile("MiniMax-M2.7", PROFILES))
    m2_ids = [choice["case_id"] for group in m2["groups"] for choice in group["choices"]]
    assert m2["profile_id"] == "minimax-m2"
    assert "A.type_enabled" not in m2_ids
    assert "A.type_disabled" in m2_ids

    flash = parameter_choices(match_profile("MiniMax-M3.1-Flash", PROFILES))
    flash_ids = [choice["case_id"] for group in flash["groups"] for choice in group["choices"]]
    assert flash["profile_id"] == "minimax-m3.1"
    assert "A.type_adaptive" in flash_ids
    assert "A.type_disabled" not in flash_ids
    assert "A.effort_low" in flash_ids
    assert "A.effort_max" in flash_ids
    assert "A.effort_none" not in flash_ids


def test_every_multi_param_model_has_a_cross_group() -> None:
    m3 = select_cases(match_profile("MiniMax-M3", PROFILES), ["ALL"])
    m3_ids = {case.case_id for case in m3}
    assert "X.type_adaptive__reasoning_split_true" in m3_ids
    assert "X.type_disabled__reasoning_split_false" in m3_ids
    split_false = next(case for case in m3 if case.case_id == "X.type_adaptive__reasoning_split_false")
    assert split_false.official["absent_fields"] == ["reasoning_content"]
    split_true = next(case for case in m3 if case.case_id == "X.type_adaptive__reasoning_split_true")
    assert "reasoning_content" in split_true.official["message_fields"]

    super_cases = select_cases(match_profile("NVIDIA-Nemotron-3-Super-120B-A12B", PROFILES), ["ALL"])
    super_ids = {case.case_id for case in super_cases}
    assert "X.effort_none__reasoning_budget_1024" in super_ids
    assert "X.effort_high__reasoning_budget_neg1" in super_ids
    none_budget = next(case for case in super_cases if case.case_id == "X.effort_none__reasoning_budget_1024")
    assert none_budget.official["absent_fields"] == ["reasoning_content"]

    flash = select_cases(match_profile("MiniMax-M3.1-Flash", PROFILES), ["ALL"])
    assert any(case.case_id.startswith("X.type_adaptive__effort_") and case.case_id.endswith("__reasoning_split_true") for case in flash)
    assert not any(case.case_id.startswith("X.") for case in select_cases(match_profile("MiniMax-M2.7", PROFILES), ["ALL"]))
    assert not any(case.case_id.startswith("X.") for case in select_cases(match_profile("gpt-oss-120b", PROFILES), ["ALL"]))


def test_muse_spark_open_has_four_effort_levels() -> None:
    profile = match_profile("Muse-Spark-Open-816B-A42B", PROFILES)
    assert profile.id == "muse-spark"
    ids = [choice["case_id"] for group in parameter_choices(profile)["groups"] for choice in group["choices"]]
    assert "A.effort_low" in ids
    assert "A.effort_medium" in ids
    assert "A.effort_high" in ids
    assert "A.effort_xhigh" in ids
    assert "A.effort_none" not in ids
    assert "A.effort_max" not in ids
    assert "A.effort_minimal" not in ids
    glimmer = parameter_choices(match_profile("Muse-Glimmer", PROFILES))
    assert glimmer["profile_id"] == "muse-glimmer"
    assert glimmer["groups"] == []


def test_locked_sampling_stays_at_the_official_default() -> None:
    kimi = parameter_choices(match_profile("Kimi-K3", PROFILES))
    fields = {item["name"]: item for item in kimi["sampling"]}
    assert fields["temperature"]["adjustable"] is False
    assert fields["temperature"]["default"] == 1.0
    assert fields["temperature"]["step"] == 0.1
    assert fields["top_p"]["default"] == 0.95
    assert fields["top_p"]["step"] == 0.01
    assert fields["presence_penalty"]["step"] == 0.1
    assert fields["n"]["default"] == 1
    assert fields["n"]["step"] == 1
    gemma_steps = {item["name"]: item["step"] for item in parameter_choices(match_profile("gemma-4-E4B-it", PROFILES))["sampling"]}
    assert gemma_steps["temperature"] == 0.1
    assert gemma_steps["top_p"] == 0.01
    assert gemma_steps["top_k"] == 1
    budget_step = {item["name"]: item["step"] for item in parameter_choices(match_profile("NVIDIA-Nemotron-3-Super-120B-A12B", PROFILES))["sampling"]}
    assert budget_step["reasoning_budget"] == 128
    profile = match_profile("Kimi-K3", PROFILES)
    changed = apply_sampling(profile, {"temperature": 0.2, "top_p": 0.5})
    values = {field.name: field.default for field in changed.sampling}
    assert values["temperature"] == 1.0
    assert values["top_p"] == 0.95
    try:
        apply_sampling(match_profile("Muse-Spark-Open-816B-A42B", PROFILES), {"top_k": 40})
    except ValueError as exc:
        assert "不送出" in str(exc)
    else:
        raise AssertionError("應拒絕送出官方關閉的欄位")
    minimax = {item["name"]: item for item in parameter_choices(match_profile("MiniMax-M3", PROFILES))["sampling"]}
    assert minimax["temperature"]["default"] == 1
    assert minimax["top_p"]["adjustable"] is False
    assert parameter_choices(match_profile("GLM-5.2", PROFILES))["sampling"] == []
    assert parameter_choices(match_profile("gpt-oss-120b", PROFILES))["sampling"] == []


def test_adjustable_sampling_keeps_the_other_defaults() -> None:
    profile = match_profile("gemma-4-E4B-it", PROFILES)
    changed = apply_sampling(profile, {"temperature": 0.7})
    values = {field.name: field.default for field in changed.sampling}
    assert values["temperature"] == 0.7
    assert values["top_p"] == 0.95
    assert values["top_k"] == 64
    assert {field.name: field.default for field in profile.sampling}["temperature"] == 1.0
    muse = {item["name"]: item for item in parameter_choices(match_profile("Muse-Spark-Open-816B-A42B", PROFILES))["sampling"]}
    assert muse["temperature"]["adjustable"] is True
    assert muse["top_p"]["default"] == 1.0
    assert muse["top_k"]["send"] is False
    budget = match_profile("NVIDIA-Nemotron-3-Super-120B-A12B", PROFILES)
    narrowed = apply_sampling(budget, {"reasoning_budget": 2048})
    assert {field.name: field.default for field in narrowed.sampling}["reasoning_budget"] == 2048
    try:
        apply_sampling(budget, {"reasoning_budget": 999999})
    except ValueError as exc:
        assert "32768" in str(exc)
    else:
        raise AssertionError("應拒絕超出官方範圍的預算")

    import importlib.util

    spec = importlib.util.spec_from_file_location("probe_flowchart_test", ROOT / "scripts" / "probe_flowchart.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = module.MODEL_PROFILE
    try:
        module.MODEL_PROFILE = changed
        body = module.payload("gemma-4-E4B-it", [{"role": "user", "content": "hi"}])
    finally:
        module.MODEL_PROFILE = previous
    assert body["temperature"] == 0.7
    assert body["top_p"] == 0.95
    assert body["top_k"] == 64
    assert "max_tokens" in body


def test_output_limit_keeps_the_model_length_field() -> None:
    from probe.choices import apply_output_limit

    profile = match_profile("Kimi-K3", PROFILES)
    updated = apply_output_limit(profile, 2048)
    assert updated.request_defaults.length_param == "max_completion_tokens"
    assert updated.request_defaults.max_output == 2048
    assert profile.request_defaults.max_output == 1024
    glm = apply_output_limit(match_profile("GLM-5.2", PROFILES), 4096)
    assert glm.request_defaults.length_param == "max_tokens"
    assert glm.request_defaults.max_output == 4096
    try:
        apply_output_limit(profile, 512)
    except ValueError as exc:
        assert "1024" in str(exc)
    else:
        raise AssertionError("應拒絕清單外的長度")


def test_unknown_case_is_rejected() -> None:
    profile = match_profile("gemma-4-31B-it", PROFILES)
    try:
        select_cases(profile, ["A.not_a_param"])
    except ValueError as exc:
        assert "A.not_a_param" in str(exc)
    else:
        raise AssertionError("應拒絕未知 case")


def test_response_document_keeps_json_shape_and_clips_long_text() -> None:
    from probe.response import response_document

    raw = json.dumps(
        {
            "model": "Kimi-K3",
            "choices": [{"message": {"content": "x" * 2500, "reasoning_content": "想"}}],
        },
        ensure_ascii=False,
    )
    shown = response_document(raw)
    parsed = json.loads(shown)
    assert parsed["model"] == "Kimi-K3"
    assert parsed["choices"][0]["message"]["content"].endswith("…")
    assert len(parsed["choices"][0]["message"]["content"]) == 2001
    assert parsed["choices"][0]["message"]["reasoning_content"] == "想"
    assert "sk-" not in shown
    assert response_document("不是 JSON") == "不是 JSON"


def test_request_code_is_the_sent_call_without_a_key() -> None:
    code = format_chat_call(
        {
            "model": "Kimi-K3",
            "messages": [{"role": "user", "content": "量子力學是什麼？ [nonce=A.effort_low:1]"}],
            "max_completion_tokens": 1024,
            "temperature": 1.0,
            "reasoning_effort": "high",
        },
        url="https://inner.example/v1",
        env_file="/home/ubuntu/model-chat-completions-params/.env",
    )
    assert not code.startswith("#!")
    assert 'source "/home/ubuntu/model-chat-completions-params/.env"' in code
    assert 'curl -sS --max-time 180 \\\n  "https://inner.example/v1/chat/completions"' in code
    assert 'Authorization: Bearer ${OPENAI_API_KEY}' in code
    assert '"model": "Kimi-K3"' in code
    assert '"content": "量子力學是什麼？"' in code
    assert "nonce" not in code
    assert '"temperature": 1.0' in code
    assert '"reasoning_effort": "high"' in code
    assert " | jq -r '.choices[0].message.content'" in code
    assert "sk-" not in code
    assert "import " not in code
    import subprocess

    quoted = format_chat_call(
        {"model": "Kimi-K3", "messages": [{"role": "user", "content": "it's"}]},
        url="https://inner.example/v1",
    )
    assert '"content": "it' + "'\\''" + 's"' in quoted
    quoted_check = subprocess.run(["bash", "-n"], input=quoted, text=True, capture_output=True)
    assert quoted_check.returncode == 0, quoted_check.stderr
    checked = subprocess.run(["bash", "-n"], input=code, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr


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
