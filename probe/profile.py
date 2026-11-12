"""Load and match Model Profile JSON.

Match is exact id / basename prefix / regex. Never substring-contains.
When several profiles match, the highest specificity score wins.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = frozenset({"enum", "flag", "int_range", "locked", "forbidden", "echo"})
LENGTH_PARAMS = frozenset({"max_tokens", "max_completion_tokens"})


class ProfileError(ValueError):
    """Invalid profile JSON or match configuration."""


@dataclass(frozen=True)
class MatchSpec:
    id_equals: tuple[str, ...] = ()
    id_prefix: tuple[str, ...] = ()
    id_regex: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestDefaults:
    length_param: str = "max_tokens"
    max_output: int = 1024


@dataclass(frozen=True)
class SamplingField:
    """官方取樣欄。adjustable 為 false 時網站只註記預設，請求仍送出。"""

    name: str
    default: int | float | None
    adjustable: bool
    send: bool
    note: str
    integer: bool = False
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float = 1


@dataclass(frozen=True)
class OfficialExample:
    id: str
    source: str
    prompt: str
    request: dict[str, Any]
    message_fields: tuple[str, ...]
    expect_http: int = 200
    docs_quote: str = ""
    absent_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str
    values: tuple[Any, ...] = ()
    default: Any = None
    reject: tuple[Any, ...] = ()
    disable_values: tuple[Any, ...] = ()
    can_disable: bool | None = None
    effect: str | None = None
    true_effect: str | None = None
    false_effect: str | None = None
    invalid_example: Any = "not-a-level"
    locked_value: Any = None
    probe_value: Any = None
    probe_in_range: tuple[Any, ...] = ()
    probe_out_of_range: tuple[Any, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class ModelProfile:
    id: str
    family: str | None
    known: bool
    match: MatchSpec
    transport: str
    request_defaults: RequestDefaults
    baseline: bool
    note: str
    source: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    official_examples: tuple[OfficialExample, ...] = ()
    sampling: tuple[SamplingField, ...] = ()
    path: Path | None = field(default=None, compare=False)

    def param(self, name: str) -> ParamSpec | None:
        for item in self.params:
            if item.name == name:
                return item
        return None


def _tuple_of(raw: Any) -> tuple[Any, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProfileError(f"expected list, got {type(raw).__name__}")
    return tuple(raw)


def _parse_param(raw: dict[str, Any], *, origin: str) -> ParamSpec:
    name = raw.get("name")
    kind = raw.get("kind")
    if not isinstance(name, str) or not name:
        raise ProfileError(f"{origin}: param.name 必須是非空字串")
    if kind not in KINDS:
        raise ProfileError(f"{origin}: {name} kind 必須是 {sorted(KINDS)}")
    return ParamSpec(
        name=name,
        kind=kind,
        values=_tuple_of(raw.get("values")),
        default=raw.get("default"),
        reject=_tuple_of(raw.get("reject")),
        disable_values=_tuple_of(raw.get("disable_values")),
        can_disable=raw.get("can_disable"),
        effect=raw.get("effect"),
        true_effect=raw.get("true_effect"),
        false_effect=raw.get("false_effect"),
        invalid_example=raw.get("invalid_example", "not-a-level"),
        locked_value=raw.get("locked_value"),
        probe_value=raw.get("probe_value"),
        probe_in_range=_tuple_of(raw.get("probe_in_range")),
        probe_out_of_range=_tuple_of(raw.get("probe_out_of_range")),
        note=str(raw.get("note") or ""),
    )


def _parse_official_example(raw: dict[str, Any], *, origin: str) -> OfficialExample:
    eid = raw.get("id")
    source = raw.get("source")
    prompt = raw.get("prompt")
    request = raw.get("request")
    expect = raw.get("expect")
    if not isinstance(eid, str) or not eid:
        raise ProfileError(f"{origin}: official_examples.id 必須是非空字串")
    if not isinstance(source, str) or not source.startswith("http"):
        raise ProfileError(f"{origin}: {eid} source 必須是 http(s) 官方 URL")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProfileError(f"{origin}: {eid} prompt 必須是官網範例原文")
    if not isinstance(request, dict):
        raise ProfileError(f"{origin}: {eid} request 必須是 object")
    if not isinstance(expect, dict):
        raise ProfileError(f"{origin}: {eid} expect 必須是 object")
    fields = expect.get("message_fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f for f in fields):
        raise ProfileError(f"{origin}: {eid} expect.message_fields 必須是非空字串陣列")
    http = expect.get("http", 200)
    if not isinstance(http, int):
        raise ProfileError(f"{origin}: {eid} expect.http 必須是整數")
    absent = expect.get("absent_fields") or []
    if not isinstance(absent, list) or not all(isinstance(f, str) and f for f in absent):
        raise ProfileError(f"{origin}: {eid} expect.absent_fields 必須是字串陣列")
    return OfficialExample(
        id=eid,
        source=source,
        prompt=prompt.strip(),
        request=dict(request),
        message_fields=tuple(fields),
        expect_http=http,
        docs_quote=str(raw.get("docs_quote") or ""),
        absent_fields=tuple(absent),
    )


def _parse_sampling(raw: dict[str, Any], *, origin: str) -> SamplingField:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ProfileError(f"{origin}: sampling.name 必須是非空字串")
    send = bool(raw.get("send", True))
    default = raw.get("default")
    if send and (isinstance(default, bool) or not isinstance(default, (int, float))):
        raise ProfileError(f"{origin}: {name} default 必須是數字")
    if not send and default is not None and (isinstance(default, bool) or not isinstance(default, (int, float))):
        raise ProfileError(f"{origin}: {name} default 必須是數字或省略")
    integer = bool(raw.get("integer", False))
    if integer and isinstance(default, float) and not default.is_integer():
        raise ProfileError(f"{origin}: {name} default 必須是整數")
    minimum = _optional_number(raw.get("minimum"), origin=origin, label="minimum")
    maximum = _optional_number(raw.get("maximum"), origin=origin, label="maximum")
    step = raw.get("step", _default_sampling_step(name, integer))
    if isinstance(step, bool) or not isinstance(step, (int, float)) or step <= 0:
        raise ProfileError(f"{origin}: {name} step 必須是正數")
    return SamplingField(
        name=name,
        default=int(default) if integer and isinstance(default, float) else default,
        adjustable=bool(raw.get("adjustable", False)),
        send=send,
        note=str(raw.get("note") or ""),
        integer=integer,
        minimum=minimum,
        maximum=maximum,
        step=step,
    )


def _default_sampling_step(name: str, integer: bool) -> int | float:
    if integer:
        return 1
    return {"temperature": 0.1, "top_p": 0.01, "presence_penalty": 0.1, "frequency_penalty": 0.1}.get(name, 0.1)


def _optional_number(value: Any, *, origin: str, label: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{origin}: {label} 必須是數字")
    return value


def parse_profile(raw: dict[str, Any], *, origin: str, path: Path | None = None) -> ModelProfile:
    for key in ("id", "family", "known", "match", "params"):
        if key not in raw:
            raise ProfileError(f"{origin}: 缺少 {key}")
    if not isinstance(raw["id"], str) or not raw["id"]:
        raise ProfileError(f"{origin}: id 必須是非空字串")
    if not isinstance(raw["known"], bool):
        raise ProfileError(f"{origin}: known 必須是 bool")
    if not isinstance(raw["params"], list):
        raise ProfileError(f"{origin}: params 必須是 list")
    match_raw = raw["match"] or {}
    if not isinstance(match_raw, dict):
        raise ProfileError(f"{origin}: match 必須是 object")
    defaults_raw = raw.get("request_defaults") or {}
    if not isinstance(defaults_raw, dict):
        raise ProfileError(f"{origin}: request_defaults 必須是 object")
    length_param = defaults_raw.get("length_param", "max_tokens")
    if length_param not in LENGTH_PARAMS:
        raise ProfileError(f"{origin}: length_param 必須是 {sorted(LENGTH_PARAMS)}")
    max_output = defaults_raw.get("max_output", 1024)
    if not isinstance(max_output, int) or max_output < 1:
        raise ProfileError(f"{origin}: max_output 必須是正整數")
    family = raw["family"]
    if family is not None and not isinstance(family, str):
        raise ProfileError(f"{origin}: family 必須是字串")
    params_list: list[ParamSpec] = []
    for i, item in enumerate(raw["params"]):
        if not isinstance(item, dict):
            raise ProfileError(f"{origin} params[{i}] 必須是 object")
        params_list.append(_parse_param(item, origin=f"{origin} params[{i}]"))
    params = tuple(params_list)
    examples_raw = raw.get("official_examples") or []
    if not isinstance(examples_raw, list):
        raise ProfileError(f"{origin}: official_examples 必須是 list")
    examples: list[OfficialExample] = []
    for i, item in enumerate(examples_raw):
        if not isinstance(item, dict):
            raise ProfileError(f"{origin} official_examples[{i}] 必須是 object")
        examples.append(_parse_official_example(item, origin=f"{origin} official_examples[{i}]"))
    sampling_raw = raw.get("sampling") or []
    if not isinstance(sampling_raw, list):
        raise ProfileError(f"{origin}: sampling 必須是 list")
    sampling: list[SamplingField] = []
    for i, item in enumerate(sampling_raw):
        if not isinstance(item, dict):
            raise ProfileError(f"{origin} sampling[{i}] 必須是 object")
        sampling.append(_parse_sampling(item, origin=f"{origin} sampling[{i}]"))
    source = raw.get("source") or []
    if not isinstance(source, list) or not all(isinstance(s, str) for s in source):
        raise ProfileError(f"{origin}: source 必須是字串陣列")
    return ModelProfile(
        id=raw["id"],
        family=family,
        known=raw["known"],
        match=MatchSpec(
            id_equals=tuple(s.lower() for s in _tuple_of(match_raw.get("id_equals"))),
            id_prefix=tuple(s.lower() for s in _tuple_of(match_raw.get("id_prefix"))),
            id_regex=tuple(_tuple_of(match_raw.get("id_regex"))),
        ),
        transport=str(raw.get("transport") or "openai_chat"),
        request_defaults=RequestDefaults(length_param=length_param, max_output=max_output),
        baseline=bool(raw.get("baseline", True)),
        note=str(raw.get("note") or ""),
        source=tuple(source),
        params=params,
        official_examples=tuple(examples),
        sampling=tuple(sampling),
        path=path,
    )


def unknown_profile() -> ModelProfile:
    return ModelProfile(
        id="unknown",
        family=None,
        known=False,
        match=MatchSpec(),
        transport="openai_chat",
        request_defaults=RequestDefaults(),
        baseline=True,
        note="契約未知：只跑 baseline OpenAI 能力，不在本機攔參數",
        source=(),
        params=(),
        official_examples=(),
        path=None,
    )


def load_profiles(directory: Path) -> tuple[ModelProfile, ...]:
    if not directory.is_dir():
        raise ProfileError(f"找不到 profile 目錄：{directory}")
    profiles: list[ModelProfile] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".schema.json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ProfileError(f"{path.name}: JSON 無效：{e}") from e
        if not isinstance(raw, dict):
            raise ProfileError(f"{path.name}: 根必須是 object")
        profiles.append(parse_profile(raw, origin=path.name, path=path))
    ids = [p.id for p in profiles]
    if len(ids) != len(set(ids)):
        raise ProfileError(f"profile id 重複：{ids}")
    return tuple(profiles)


def _candidate_ids(model_id: str) -> tuple[str, str]:
    low = model_id.strip().lower()
    base = low.rsplit("/", 1)[-1]
    return low, base


def match_score(model_id: str, spec: MatchSpec) -> int:
    """Higher is more specific. 0 = no match."""
    full, base = _candidate_ids(model_id)
    best = 0
    for eq in spec.id_equals:
        if full == eq:
            best = max(best, 1000 + len(eq))
        elif base == eq:
            best = max(best, 900 + len(eq))
    for prefix in spec.id_prefix:
        if not prefix:
            continue
        if full == prefix or full.startswith(prefix):
            best = max(best, 500 + len(prefix))
        elif base == prefix or base.startswith(prefix):
            best = max(best, 450 + len(prefix))
    for pattern in spec.id_regex:
        try:
            m = re.search(pattern, full, flags=re.IGNORECASE)
        except re.error as e:
            raise ProfileError(f"無效 regex {pattern!r}: {e}") from e
        if m:
            best = max(best, 200 + (m.end() - m.start()))
    return best


def match_profile(model_id: str, profiles: tuple[ModelProfile, ...]) -> ModelProfile:
    ranked: list[tuple[int, ModelProfile]] = []
    for profile in profiles:
        score = match_score(model_id, profile.match)
        if score:
            ranked.append((score, profile))
    if not ranked:
        return unknown_profile()
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return ranked[0][1]
