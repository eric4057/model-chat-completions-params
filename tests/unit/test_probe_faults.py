"""部署症狀與回覆解析。不打閘道。"""

from __future__ import annotations

from probe.faults import (
    classify_failure,
    extract_vllm_flags,
    finalize_verdict,
    observe_reasoning,
)
from probe.http_parse import parse_curl_stdout
from probe.report_table import endpoint_output
from probe.response import interpret_chat_response
from probe.verdict import (
    match_official_example,
    reasoning_channel_verdict,
    refine_effect,
    verdict_expect_reject,
    verdict_non_200,
)

_CURL_MARK = "__CURL_HTTP__"


def _ok_message(**message: object) -> dict:
    return {
        "http_status": 200,
        "finish_reason": "stop",
        "content": "答案",
        "reasoning": None,
        "reasoning_content": None,
        "provider_reasoning": None,
        **message,
    }


def test_classify_timeout_auth_and_rate_limit() -> None:
    assert classify_failure(status=None, message="curl timeout", curl_exit=28) == "transport_timeout"
    assert classify_failure(status=None, message="curl exit 7", curl_exit=7) == "upstream_closed"
    assert classify_failure(status=401, message="invalid key") == "auth"
    assert classify_failure(status=429, message="slow down") == "rate_limit"


def test_classify_gateway_vllm_and_plain_reject() -> None:
    assert (
        classify_failure(status=500, message="unexpected keyword argument 'chat_template_kwargs'")
        == "gateway_param"
    )
    assert (
        classify_failure(status=500, message="EngineCore encountered an error")
        == "vllm_engine"
    )
    assert (
        classify_failure(status=500, message="litellm.InternalServerError: hosted_vllm boom")
        == "gateway_bug"
    )
    assert classify_failure(status=400, message="invalid reasoning_effort") == "client_reject"
    assert (
        classify_failure(status=200, message="EngineCore died", from_stream=True)
        == "vllm_engine"
    )
    assert classify_failure(status=200, message="overloaded", from_stream=True) == "stream_error"


def test_success_body_is_not_scanned_for_engine_words() -> None:
    assert classify_failure(status=200, message="Traceback in the essay") is None


def test_observe_glm_style_leak_provider_and_truncation() -> None:
    leaked = observe_reasoning(
        _ok_message(content="【思考過程】先算再答。雞 12 兔 23。")
    )
    assert leaked["kind"] == "content_leak"
    provider = observe_reasoning(_ok_message(provider_reasoning="內部推理"))
    assert provider["kind"] == "provider_only"
    truncated = observe_reasoning(_ok_message(content="", finish_reason="length"))
    assert truncated["kind"] == "truncated"
    field = observe_reasoning(_ok_message(reasoning_content="想過了", content="【思考過程】也在正文"))
    assert field["kind"] == "field"


def test_official_example_names_leak_and_truncation() -> None:
    spec = {
        "expect_http": 200,
        "message_fields": ["content", "reasoning_content"],
        "docs_quote": "content 與 reasoning_content",
    }
    verdict, note, evidence = match_official_example(
        _ok_message(content="思考過程：先列式。答案 12。"),
        spec,
    )
    assert verdict == "fail"
    assert evidence["proof"] == "content_leak"
    assert "content" in note

    verdict, note, evidence = match_official_example(
        _ok_message(provider_reasoning="還沒映射"),
        spec,
    )
    assert verdict == "fail"
    assert evidence["proof"] == "unmapped_provider_field"

    verdict, _, evidence = match_official_example(
        _ok_message(content="", finish_reason="length"),
        spec,
    )
    assert verdict == "noop"
    assert evidence["proof"] == "truncated"


def test_disable_still_fails_when_thought_is_only_in_content() -> None:
    spec = {
        "expect_http": 200,
        "message_fields": ["content"],
        "absent_fields": ["reasoning_content"],
    }
    verdict, note, evidence = match_official_example(
        _ok_message(content="思考過程：關掉了仍在想。答案 1。"),
        spec,
    )
    assert verdict == "fail"
    assert evidence["proof"] == "content_leak"
    assert "關閉後" in note


def test_refine_effect_separates_leak_from_plain_absence() -> None:
    baseline = _ok_message(reasoning_content="預設有想")
    verdict, note, _ = refine_effect(
        effect="has_reasoning",
        disable=False,
        can_disable=True,
        value="enabled",
        default="enabled",
        result=_ok_message(content="<think>內部</think>答案"),
        baseline=baseline,
        group_results={},
    )
    assert verdict == "fail"
    assert "content" in note

    verdict, note = reasoning_channel_verdict(
        _ok_message(content="", finish_reason="length")
    )
    assert verdict == "noop"
    assert "length" in note

    verdict, note, _ = refine_effect(
        effect="no_reasoning",
        disable=True,
        can_disable=True,
        value="disabled",
        default="enabled",
        result=_ok_message(content="只有答案"),
        baseline=baseline,
        group_results={},
    )
    assert verdict == "pass"


def test_reject_does_not_treat_auth_as_parameter_rejection() -> None:
    assert verdict_expect_reject(400)[0] == "pass"
    assert verdict_expect_reject(401)[0] == "fail"
    assert verdict_expect_reject(429)[0] == "fail"
    assert verdict_non_200(403, "temperature")[0] == "fail"
    assert verdict_non_200(400, "temperature")[0] == "skip"


def test_finalize_overrides_noop_but_keeps_expected_4xx() -> None:
    malformed = {"fault": {"class": "malformed_response", "detail": "HTTP 200 但沒有 choices"}}
    assert finalize_verdict("noop", "無 reasoning", malformed)[0] == "fail"
    kept, notes = finalize_verdict(
        "pass",
        "HTTP 400 客戶端拒絕",
        {"fault": {"class": "client_reject", "detail": "客戶端拒絕：bad enum"}},
    )
    assert kept == "pass"
    assert "bad enum" in notes


def test_curl_stdout_keeps_body_and_litellm_header() -> None:
    stdout = (
        "HTTP/1.1 200 OK\r\n"
        "x-litellm-call-id: abc-123\r\n"
        "Content-Type: application/json\r\n"
        "\r\n"
        '{"choices":[{"message":{"content":"ok"}}]}\n'
        f"{_CURL_MARK}200"
    )
    parsed = parse_curl_stdout(stdout)
    assert parsed["http_status"] == 200
    assert parsed["headers"]["x-litellm-call-id"] == "abc-123"
    assert parsed["body"].startswith('{"choices"')


def test_interpret_stream_error_and_provider_field() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"【思考過程】"}}]}\n'
        'data: {"error":{"message":"EngineCore died","type":"InternalServerError"}}\n'
        "data: [DONE]\n"
    )
    result = interpret_chat_response(
        status=200,
        raw=sse,
        elapsed_ms=10,
        error=None,
        headers={"x-litellm-call-id": "call-1", "set-cookie": "secret"},
        curl_exit=0,
        stream=True,
        sent={"model": "m"},
    )
    assert result["fault"]["class"] == "vllm_engine"
    assert result["response_headers"] == {"x-litellm-call-id": "call-1"}
    assert result["saw_done"] is True
    assert "【思考過程】" in result["content"]

    body = (
        '{"choices":[{"finish_reason":"stop","message":'
        '{"content":"答案","provider_specific_fields":{"reasoning":"藏在這裡"}}}]}'
    )
    result = interpret_chat_response(
        status=200,
        raw=body,
        elapsed_ms=5,
        error=None,
        headers={},
        curl_exit=0,
        stream=False,
        sent={},
    )
    assert result["reasoning_obs"]["kind"] == "provider_only"
    assert result["ok"] is True


def test_interpret_http_200_without_choices_is_malformed() -> None:
    result = interpret_chat_response(
        status=200,
        raw="{}",
        elapsed_ms=1,
        error=None,
        headers={},
        curl_exit=0,
        stream=False,
        sent={},
    )
    assert result["ok"] is False
    assert result["fault"]["class"] == "malformed_response"


def test_extract_vllm_flags_ignores_plugin_name_and_empty() -> None:
    blob = (
        "StructuredOutputsConfig(reasoning_parser='gemma4', "
        "reasoning_parser_plugin='', tool_call_parser=None)"
    )
    flags = extract_vllm_flags(blob)
    assert flags["reasoning_parser"] == "gemma4"
    assert flags["tool_call_parser"] is None


def test_endpoint_output_shows_leak_kind() -> None:
    text = endpoint_output(
        {
            "http_status": 200,
            "content_preview": "思考過程：x",
            "reasoning_preview": "這段不該進對照表",
            "reasoning_obs": {"kind": "content_leak"},
            "response_headers": {"x-litellm-call-id": "abc"},
            "fault": None,
            "request": {"max_tokens": 1024, "chat_template_kwargs": {"enable_thinking": True}},
        }
    )
    assert "reasoning=content_leak" in text
    assert "call_id=abc" in text
    assert "有 content" in text
    assert "思考過程" not in text
    assert "這段不該進對照表" not in text
    assert "max_tokens" not in text


def test_official_output_states_each_fact_once() -> None:
    from probe.report_table import official_output

    text = official_output(
        {
            "case_id": "A.enable_thinking_true",
            "desc": "chat_template_kwargs.enable_thinking=True",
            "official": {
                "docs_quote": "官方：回 content 與 reasoning／reasoning_content",
                "message_fields": ["content", "reasoning_content"],
                "expect_http": 200,
                "request": {"chat_template_kwargs": {"enable_thinking": True}, "max_tokens": 1024},
            },
        }
    )
    assert text.count("enable_thinking=True") == 1
    assert "content 非空" not in text
    assert "應 HTTP 200" not in text
    assert "max_tokens" not in text
    assert "官方：回 content 與 reasoning／reasoning_content" in text


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
