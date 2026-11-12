"""把一份 Model Profile 收成網站上可勾選的官方參數。"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import replace
from typing import Any

_PROBE_NONCE = re.compile(r" \[nonce=[^]]*\]\s*$")

from probe.cases import ProbeCase, _slug, generate_contract_cases, official_scope_cases
from probe.extra import deep_merge, set_path
from probe.profile import ModelProfile, ParamSpec

COMPARE_NOTE = (
    "「一致」是指 HTTP 狀態與官方文件寫死的欄位相同："
    "content、reasoning 該有就有、該沒有就沒有，該拒的參數要回 4xx。"
    "不比模型生成的全文。官方範例多半是占位句，全文不會穩定逐字相同。"
)

MATCH_LABEL = {
    "pass": "一致",
    "fail": "不一致",
    "noop": "無法判定",
    "skip": "閘道不收",
}

OUTPUT_LIMITS = (1024, 2048, 4096)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _cross_values(param: ParamSpec) -> tuple[Any, ...]:
    """官方要送的值。不含應拒值、超範圍值。"""
    if param.kind == "enum":
        return tuple(param.values)
    if param.kind == "flag":
        return (True, False)
    if param.kind == "int_range":
        return tuple(param.probe_in_range)
    return ()


def _turns_thinking_off(param: ParamSpec, value: Any) -> bool:
    """這個值在官方契約裡會關思考。官方寫明關了仍會想的，不算關掉。"""
    if param.can_disable is False:
        return False
    if param.kind == "enum":
        return value in param.disable_values
    return value is False and param.false_effect == "no_reasoning"


def _cross_piece(param: ParamSpec, value: Any) -> str:
    leaf = {
        "thinking.type": "type",
        "reasoning_effort": "effort",
        "chat_template_kwargs.enable_thinking": "enable_thinking",
    }.get(param.name, param.name.rsplit(".", 1)[-1])
    slug = str(value).lower() if isinstance(value, bool) else _slug(value)
    return f"{leaf}_{slug}"


def _hides_reasoning_field(param: ParamSpec, value: Any) -> bool:
    """這個值只改回傳位置，思考留在 content，不另給 reasoning_content。"""
    return param.name == "reasoning_split" and value is False


def cross_cases(profile: ModelProfile) -> list[ProbeCase]:
    """兩個以上官方參數時，把每個官方值放進同一請求。只有一個參數的模型無法交叉。"""
    axes = [
        (param, _cross_values(param))
        for param in profile.params
        if _cross_values(param)
    ]
    if len(axes) < 2:
        return []
    source = profile.source[0] if profile.source else ""
    cases: list[ProbeCase] = []
    value_lists = [values for _, values in axes]
    for combo in itertools.product(*value_lists):
        extra: dict[str, Any] = {}
        parts: list[str] = []
        pieces: list[str] = []
        turns_off = False
        hides_field = False
        for (param, _), value in zip(axes, combo):
            extra = deep_merge(extra, set_path(param.name, value))
            parts.append(f"{param.name}={value}")
            pieces.append(_cross_piece(param, value))
            turns_off = turns_off or _turns_thinking_off(param, value)
            hides_field = hides_field or _hides_reasoning_field(param, value)
        if turns_off or hides_field:
            fields = ["content"]
            absent = ["reasoning_content"]
            quote = (
                "交叉送出。官方有關思考的值，或不另拆 reasoning 欄，不回 reasoning_content"
                if hides_field and not turns_off
                else "交叉送出。官方有關思考的值，不回 reasoning_content"
            )
        else:
            fields = ["content", "reasoning_content"]
            absent = []
            quote = "交叉送出。思考開著，回 content 與 reasoning_content"
        cases.append(
            ProbeCase(
                case_id="X." + "__".join(pieces),
                step="A",
                desc=" × ".join(parts),
                extra=extra,
                expect="effect",
                effect="official_shape",
                param_name="交叉比對",
                value=dict(zip((param.name for param, _ in axes), combo)),
                in_official=True,
                enforce_local=False,
                claimed=True,
                disable=turns_off,
                note="同一請求送出這些官方值",
                official={
                    "source": source,
                    "docs_quote": quote,
                    "request": extra,
                    "message_fields": fields,
                    "absent_fields": absent,
                    "expect_http": 200,
                    "proof_layer": "docs_response_fields",
                },
            )
        )
    return cases


def contract_cases(profile: ModelProfile) -> list[ProbeCase]:
    """只留官方契約列出的值。不列官網範例、寫死的應拒值、invalid_example。"""
    base = [
        case
        for case in official_scope_cases(generate_contract_cases(profile, probe_mode="probe"))
        if case.in_official is True and case.expect != "reject" and case.param_name != "official_example"
    ]
    return base + cross_cases(profile)


def sampling_public(profile: ModelProfile) -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "default": field.default,
            "adjustable": field.adjustable,
            "send": field.send,
            "note": field.note,
            "integer": field.integer,
            "minimum": field.minimum,
            "maximum": field.maximum,
            "step": field.step,
        }
        for field in profile.sampling
    ]


def _coerce_sampling(field: Any, value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field.name} 必須是數字")
    if field.integer:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{field.name} 必須是整數")
        value = int(value)
    if field.minimum is not None and value < field.minimum:
        raise ValueError(f"{field.name} 不能小於 {field.minimum}")
    if field.maximum is not None and value > field.maximum:
        raise ValueError(f"{field.name} 不能大於 {field.maximum}")
    return value


def apply_sampling(profile: ModelProfile, overrides: dict[str, Any]) -> ModelProfile:
    """可調整的欄位才採用這次填的值。規格不允許調整的欄位維持官方預設。"""
    if not overrides:
        return profile
    by_name = {field.name: field for field in profile.sampling}
    updated: dict[str, Any] = {}
    for name, value in overrides.items():
        field = by_name.get(str(name))
        if field is None or not field.send:
            raise ValueError(f"{name} 官方不送出此參數")
        if not field.adjustable:
            continue
        updated[field.name] = _coerce_sampling(field, value)
    fields = tuple(
        replace(field, default=updated[field.name]) if field.name in updated else field
        for field in profile.sampling
    )
    return replace(profile, sampling=fields)


def apply_output_limit(profile: ModelProfile, limit: int) -> ModelProfile:
    """換成這次探測要送的輸出長度。欄位名仍用該模型的 length_param。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit not in OUTPUT_LIMITS:
        allowed = "、".join(str(item) for item in OUTPUT_LIMITS)
        raise ValueError(f"輸出長度只能是 {allowed}")
    defaults = replace(profile.request_defaults, max_output=limit)
    return replace(profile, request_defaults=defaults)


def parameter_choices(profile: ModelProfile) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for case in contract_cases(profile):
        official = case.official or {}
        choice = {
            "case_id": case.case_id,
            "param": case.param_name,
            "value": _jsonable(case.value),
            "label": case.desc,
            "expect": "應拒" if case.expect == "reject" else "應符合文件",
            "source": official.get("source") or "",
            "docs_quote": official.get("docs_quote") or case.note,
        }
        if case.param_name not in grouped:
            grouped[case.param_name] = []
            order.append(case.param_name)
        grouped[case.param_name].append(choice)
    return {
        "profile_id": profile.id,
        "family": profile.family,
        "known": profile.known and bool(order),
        "source": list(profile.source),
        "compare_note": COMPARE_NOTE,
        "length_param": profile.request_defaults.length_param,
        "output_limits": list(OUTPUT_LIMITS),
        "output_limit": profile.request_defaults.max_output,
        "sampling": sampling_public(profile),
        "groups": [{"param": name, "choices": grouped[name]} for name in order],
    }


def select_cases(profile: ModelProfile, case_ids: list[str]) -> list[ProbeCase]:
    """case_ids 含 ALL，或列出要跑的 case_id。順序跟契約相同。"""
    cases = contract_cases(profile)
    if not case_ids or "ALL" in case_ids:
        return cases
    wanted = set(case_ids)
    unknown = wanted.difference(case.case_id for case in cases)
    if unknown:
        names = "、".join(sorted(unknown))
        raise ValueError(f"沒有這些參數：{names}")
    return [case for case in cases if case.case_id in wanted]


def _copy_payload(body: dict[str, Any]) -> dict[str, Any]:
    """複製用的請求。拿掉探測用的 nonce，避免腳本被快取標記拉長。"""
    payload = {key: value for key, value in body.items() if key != "litellm_request_debug"}
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    cleaned = []
    for item in messages:
        if isinstance(item, dict) and isinstance(item.get("content"), str):
            item = {**item, "content": _PROBE_NONCE.sub("", item["content"])}
        cleaned.append(item)
    payload["messages"] = cleaned
    return payload


def _shell_single_quoted(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def format_chat_call(body: dict[str, Any], *, url: str = "", env_file: str = "") -> str:
    """可貼上終端機的 curl。金鑰不寫進程式；沒匯出時改讀 env_file。"""
    endpoint = f"{url.rstrip('/')}/chat/completions" if url else ""
    document = json.dumps(_copy_payload(body), ensure_ascii=False, indent=2)
    lines = []
    if env_file:
        lines.append(
            '[[ -n ${OPENAI_API_KEY:-} ]] || { set -a; source '
            + json.dumps(env_file)
            + "; set +a; }"
        )
    lines.extend(
        [
            "curl -sS --max-time 180 \\",
            f"  {json.dumps(endpoint)} \\",
            '  -H "Authorization: Bearer ${OPENAI_API_KEY}" \\',
            '  -H "Content-Type: application/json" \\',
            "  -d "
            + _shell_single_quoted(document)
            + " | jq -r '.choices[0].message.content'",
            "",
        ]
    )
    return "\n".join(lines)
