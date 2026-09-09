"""Generate probe cases from a Model Profile.

The runner executes these cases. Adding a family means adding JSON, not a new STEPS letter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from probe.extra import set_path
from probe.profile import ModelProfile, ParamSpec
from probe.verdict import openai_error

KNOWN_ENUM_INVALID = "not-a-level"


@dataclass(frozen=True)
class ProbeCase:
    case_id: str
    step: str
    desc: str
    extra: dict[str, Any]
    expect: str
    effect: str | None
    param_name: str
    value: Any
    in_official: bool | None
    enforce_local: bool
    claimed: bool
    disable: bool = False
    can_disable: bool | None = None
    compare_group: str | None = None
    default: Any = None
    note: str = ""
    reject_body: dict[str, Any] | None = field(default=None, compare=False)
    prompt: str | None = None
    force_non_stream: bool = False
    stream_override: bool | None = None
    official: dict[str, Any] | None = field(default=None, compare=False)


def _leaf_name(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _slug(value: Any) -> str:
    if isinstance(value, int) and value < 0:
        return f"neg{abs(value)}"
    text = str(value)
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "val"


def _reject_body(param: ParamSpec, value: Any) -> dict[str, Any]:
    if param.kind == "forbidden":
        return openai_error(
            f"Unsupported parameter: '{param.name}' is not supported with this model.",
            "unsupported_parameter",
            param=param.name,
        )
    if param.kind == "locked":
        return openai_error(
            f"Invalid value: '{param.name}' is fixed for this model and cannot be changed.",
            "invalid_value",
            param=param.name,
        )
    if param.kind == "enum" and not param.values:
        return openai_error(
            f"Unsupported parameter: '{param.name}' is not supported with this model.",
            "unsupported_parameter",
            param=param.name,
        )
    allowed = [str(v) for v in param.values] if param.values else None
    return openai_error(
        f"Invalid value: '{param.name}' does not support '{value}'. "
        + (f"Supported values are: {', '.join(allowed)}." if allowed else ""),
        "invalid_value",
        param=param.name,
        allowed=allowed,
    )


def _case(
    *,
    case_id: str,
    step: str,
    desc: str,
    param: ParamSpec,
    value: Any,
    extra: dict[str, Any],
    expect: str,
    effect: str | None,
    in_official: bool | None,
    probe_mode: str,
    claimed: bool,
    disable: bool = False,
    compare_group: str | None = None,
) -> ProbeCase:
    reject = expect == "reject"
    enforce_local = claimed and probe_mode == "enforce" and reject
    return ProbeCase(
        case_id=case_id,
        step=step,
        desc=desc,
        extra=extra,
        expect=expect,
        effect=effect if expect == "effect" else None,
        param_name=param.name,
        value=value,
        in_official=in_official,
        enforce_local=enforce_local,
        claimed=claimed,
        disable=disable,
        can_disable=param.can_disable,
        compare_group=compare_group,
        default=param.default,
        note=param.note,
        reject_body=_reject_body(param, value) if reject else None,
    )


def _gen_flag(param: ParamSpec, *, probe_mode: str, claimed: bool) -> list[ProbeCase]:
    cases: list[ProbeCase] = []
    for flag, effect_key in ((True, param.true_effect), (False, param.false_effect)):
        effect = effect_key or param.effect
        extra = set_path(param.name, flag)
        leaf = _leaf_name(param.name)
        disable = flag is False and effect in ("no_reasoning", "has_reasoning")
        cases.append(
            _case(
                case_id=f"A.{leaf}_{str(flag).lower()}",
                step="A",
                desc=f"{param.name}={flag}",
                param=param,
                value=flag,
                extra=extra,
                expect="effect" if effect else "accept",
                effect=effect,
                in_official=True if claimed else None,
                probe_mode=probe_mode,
                claimed=claimed,
                disable=disable,
            )
        )
    return cases


def _enum_case_id(param: ParamSpec, value: Any, *, reject: bool) -> str:
    leaf = _leaf_name(param.name)
    if param.name == "reasoning_effort":
        if reject and value == KNOWN_ENUM_INVALID:
            return "A.effort_invalid"
        return f"A.effort_{_slug(value)}"
    if reject and value == param.invalid_example:
        return f"A.{leaf}_invalid"
    return f"A.{leaf}_{_slug(value)}"


def _gen_enum(
    param: ParamSpec,
    *,
    probe_mode: str,
    claimed: bool,
    override: list[Any] | None,
) -> list[ProbeCase]:
    official = tuple(param.values)
    disable_values = set(param.disable_values)
    reject_values: list[Any] = list(param.reject)
    if param.invalid_example not in official and param.invalid_example not in reject_values:
        reject_values.append(param.invalid_example)

    send: list[Any]
    extra_reject: list[Any] = []
    if override is None:
        send = list(official)
    else:
        send = []
        for val in override:
            if (not claimed) or val in official:
                send.append(val)
            else:
                extra_reject.append(val)
        reject_values = extra_reject + [v for v in reject_values if v not in extra_reject]

    if probe_mode == "enforce" and override is None:
        send = []

    cases: list[ProbeCase] = []
    group = param.name if param.effect == "reasoning_tokens_rank" else None
    for val in send:
        disable = val in disable_values
        effect = param.effect
        expect = "effect" if effect else "accept"
        cases.append(
            _case(
                case_id=_enum_case_id(param, val, reject=False),
                step="A",
                desc=f"{param.name}={val}",
                param=param,
                value=val,
                extra=set_path(param.name, val),
                expect=expect,
                effect=effect,
                in_official=True if claimed else None,
                probe_mode=probe_mode,
                claimed=claimed,
                disable=disable,
                compare_group=group,
            )
        )

    if not claimed:
        return cases

    seen: set[str] = {c.case_id for c in cases}
    for val in reject_values:
        cid = _enum_case_id(param, val, reject=True)
        if cid in seen:
            continue
        seen.add(cid)
        desc = f"{param.name}={val} 應拒"
        if val == param.invalid_example:
            desc = f"{param.name}={val} 應拒"
        cases.append(
            _case(
                case_id=cid,
                step="A",
                desc=desc,
                param=param,
                value=val,
                extra=set_path(param.name, val),
                expect="reject",
                effect=None,
                in_official=False,
                probe_mode=probe_mode,
                claimed=True,
            )
        )
    return cases


def _gen_int_range(param: ParamSpec, *, probe_mode: str, claimed: bool) -> list[ProbeCase]:
    cases: list[ProbeCase] = []
    leaf = _leaf_name(param.name)
    for val in param.probe_in_range:
        effect = param.effect
        cases.append(
            _case(
                case_id=f"A.{leaf}_{_slug(val)}",
                step="A",
                desc=f"{param.name}={val}",
                param=param,
                value=val,
                extra=set_path(param.name, val),
                expect="effect" if effect else "accept",
                effect=effect,
                in_official=True if claimed else None,
                probe_mode=probe_mode,
                claimed=claimed,
            )
        )
    for val in param.probe_out_of_range:
        cases.append(
            _case(
                case_id=f"A.{leaf}_{_slug(val)}_reject",
                step="A",
                desc=f"{param.name}={val} 應拒",
                param=param,
                value=val,
                extra=set_path(param.name, val),
                expect="reject",
                effect=None,
                in_official=False,
                probe_mode=probe_mode,
                claimed=claimed,
            )
        )
    return cases


def _gen_locked(param: ParamSpec, *, probe_mode: str, claimed: bool) -> ProbeCase:
    value = param.probe_value
    return _case(
        case_id=f"E.{_leaf_name(param.name)}",
        step="E",
        desc=f"{param.name}={value}",
        param=param,
        value=value,
        extra=set_path(param.name, value),
        expect="reject",
        effect=None,
        in_official=False,
        probe_mode=probe_mode,
        claimed=claimed,
    )


def _gen_forbidden(param: ParamSpec, *, probe_mode: str, claimed: bool) -> ProbeCase:
    value = param.probe_value
    return _case(
        case_id=f"A.forbidden_{_leaf_name(param.name)}",
        step="A",
        desc=f"送 {param.name} 應拒",
        param=param,
        value=value,
        extra=set_path(param.name, value),
        expect="reject",
        effect=None,
        in_official=False,
        probe_mode=probe_mode,
        claimed=claimed,
    )


def _gen_echo(param: ParamSpec, *, probe_mode: str, claimed: bool) -> ProbeCase:
    return _case(
        case_id="A.multiturn",
        step="A",
        desc="多輪回傳完整 assistant",
        param=param,
        value=None,
        extra={},
        expect="effect",
        effect=param.effect or "http_ok",
        in_official=True if claimed else None,
        probe_mode=probe_mode,
        claimed=claimed,
    )


def generate_contract_cases(
    profile: ModelProfile,
    *,
    probe_mode: str,
    effort_override: list[str] | None = None,
) -> list[ProbeCase]:
    """Build ordered contract cases. Unknown profiles yield no claimed cases.

    effort_override 只影響 name=reasoning_effort 的 enum（與既有 REASONING_EFFORT env 對齊）。
    probe_mode=enforce 且未指定 override 時，不送官方 enum 值（走預設），但仍產生 reject 槍（本機 400）。
    """
    if probe_mode not in ("probe", "enforce"):
        raise ValueError(f"probe_mode 只能是 probe 或 enforce（收到 {probe_mode}）")

    claimed = profile.known
    by_kind: dict[str, list[ParamSpec]] = {
        "flag": [],
        "enum": [],
        "int_range": [],
        "forbidden": [],
        "echo": [],
        "locked": [],
    }
    for param in profile.params:
        by_kind[param.kind].append(param)

    cases: list[ProbeCase] = []
    for param in by_kind["flag"]:
        cases.extend(_gen_flag(param, probe_mode=probe_mode, claimed=claimed))
    for param in by_kind["enum"]:
        override = effort_override if param.name == "reasoning_effort" else None
        cases.extend(
            _gen_enum(
                param,
                probe_mode=probe_mode,
                claimed=claimed,
                override=override,
            )
        )
    for param in by_kind["int_range"]:
        cases.extend(_gen_int_range(param, probe_mode=probe_mode, claimed=claimed))
    for param in by_kind["forbidden"]:
        cases.append(_gen_forbidden(param, probe_mode=probe_mode, claimed=claimed))
    for param in by_kind["echo"]:
        cases.append(_gen_echo(param, probe_mode=probe_mode, claimed=claimed))
    for param in by_kind["locked"]:
        cases.append(_gen_locked(param, probe_mode=probe_mode, claimed=claimed))
    cases.extend(_gen_official_examples(profile, claimed=claimed))
    return cases


def _gen_official_examples(profile: ModelProfile, *, claimed: bool) -> list[ProbeCase]:
    if not claimed:
        return []
    cases: list[ProbeCase] = []
    for example in profile.official_examples:
        spec = {
            "id": example.id,
            "source": example.source,
            "docs_quote": example.docs_quote,
            "prompt": example.prompt,
            "request": dict(example.request),
            "message_fields": list(example.message_fields),
            "absent_fields": list(example.absent_fields),
            "expect_http": example.expect_http,
            "proof_layer": "docs_response_fields",
        }
        cases.append(
            ProbeCase(
                case_id=f"A.official_{_slug(example.id)}",
                step="A",
                desc=f"官網範例 {example.id}",
                extra=dict(example.request),
                expect="effect",
                effect="official_shape",
                param_name="official_example",
                value=example.id,
                in_official=True,
                enforce_local=False,
                claimed=True,
                prompt=example.prompt,
                force_non_stream=True,
                official=spec,
                note=example.source,
            )
        )
    return cases


def cases_by_step(cases: list[ProbeCase], step: str) -> list[ProbeCase]:
    return [c for c in cases if c.step == step]


_ECHO_PARAMS = frozenset({"assistant_echo"})
_ECHO_IDS = frozenset({"A.multiturn"})


def official_scope_cases(cases: list[ProbeCase]) -> list[ProbeCase]:
    """官方 scope：特殊參數 + 官網範例。不加 echo／通用考卷／思考×串流組合。"""
    return [
        case
        for case in cases
        if case.step != "F"
        and case.param_name not in _ECHO_PARAMS
        and case.case_id not in _ECHO_IDS
    ]


_OFF_VALUE_NAMES = frozenset({"disabled", "none", "false", "off", "minimal"})


def _thinking_switch_param(profile: ModelProfile) -> ParamSpec | None:
    """這家用來開／關思考的契約欄。不是強度檔的每一格，也不是 reasoning_split。"""
    for param in profile.params:
        if (
            param.kind == "flag"
            and param.true_effect == "has_reasoning"
            and param.false_effect in ("no_reasoning", "has_reasoning")
        ):
            return param
    for param in profile.params:
        if param.kind == "enum" and param.name == "thinking.type":
            return param
    for param in profile.params:
        if param.kind == "enum" and param.name == "reasoning_effort":
            return param
    return None


def _on_switch_value(param: ParamSpec) -> Any:
    if param.kind == "flag":
        return True
    disabled = set(param.disable_values)
    if param.default in param.values and param.default not in disabled:
        return param.default
    for val in param.values:
        if val not in disabled:
            return val
    return None


def _off_switch_value(param: ParamSpec) -> Any:
    if param.kind == "flag":
        return False
    if param.disable_values:
        return param.disable_values[0]
    on_val = _on_switch_value(param)
    for val in param.values:
        if val == on_val:
            continue
        if str(val).lower() in _OFF_VALUE_NAMES:
            return val
    return None


def generate_thinking_stream_combos(profile: ModelProfile) -> list[ProbeCase]:
    """思考開／關 × 串流開／關。只在 full 的 F 使用；不受 STREAM_STEPS 影響。

    沒有思考開關的 family 回空清單。不能關思考的（Kimi、gpt-oss）只打「開」× 串流兩槍。
    送出關檔但官方仍會想的（MiniMax M2 disabled）仍產生「關」槍，判定跟 A 一樣要有 reasoning。
    """
    if not profile.known:
        return []
    param = _thinking_switch_param(profile)
    if param is None:
        return []
    on_val = _on_switch_value(param)
    if on_val is None:
        return []
    levels: list[tuple[str, Any, bool]] = [
        ("on", on_val, False),
    ]
    off_val = _off_switch_value(param)
    if off_val is not None and off_val != on_val:
        disable = off_val in set(param.disable_values) or (
            param.kind == "flag" and param.false_effect == "no_reasoning"
        )
        levels.append(("off", off_val, disable))

    cases: list[ProbeCase] = []
    for switch, value, disable in levels:
        extra = set_path(param.name, value)
        for stream, channel, zh_stream in (
            (True, "stream", "串流"),
            (False, "sync", "非串流"),
        ):
            zh_think = "開" if switch == "on" else "關"
            cases.append(
                ProbeCase(
                    case_id=f"F.think_{switch}_{channel}",
                    step="F",
                    desc=f"思考{zh_think} × {zh_stream}（{param.name}={value}）",
                    extra=extra,
                    expect="effect",
                    effect="official_shape",
                    param_name=param.name,
                    value=value,
                    in_official=True,
                    enforce_local=False,
                    claimed=True,
                    disable=disable,
                    can_disable=param.can_disable,
                    default=param.default,
                    note=param.note,
                    force_non_stream=not stream,
                    stream_override=stream,
                )
            )
    return cases


def messages_for_case(base_prompt: str, case: ProbeCase) -> list[dict[str, Any]]:
    """官網範例用原文 prompt；其餘每槍獨立 nonce，避免閘道依 messages 快取而忽略 extra。"""
    if case.prompt:
        return [{"role": "user", "content": case.prompt}]
    nonce = f" [nonce={case.case_id}:{time.time_ns()}]"
    return [{"role": "user", "content": base_prompt + nonce}]
