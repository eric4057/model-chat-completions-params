"""把探測 JSON 編成「官方輸出 / endpoint 輸出 / 輸出錯誤」對照表。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from probe.faults import clip_text
from probe.flow import render_flow_md

ROOT = Path(__file__).resolve().parents[1]
_SKIP_REQUEST = frozenset({"model", "messages", "message_count", "first_user"})
# 每槍都會帶、不是這題在測的欄。對照表不重複印。
_BOILERPLATE = frozenset(
    {
        "max_tokens",
        "max_completion_tokens",
        "stream_options",
        "litellm_request_debug",
    }
)
_VERDICT_ZH = {
    "pass": "OK",
    "fail": "FAIL",
    "noop": "NOOP",
    "skip": "SKIP",
}


def _md_cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _request_extras(
    request: dict[str, Any] | None,
    *,
    skip: frozenset[str] = frozenset(),
) -> str:
    if not request:
        return ""
    parts: list[str] = []
    for key, value in request.items():
        if key in _SKIP_REQUEST or key in skip:
            continue
        parts.append(f"{key}={value}")
    return "；".join(parts)


def _focus_request(official: dict[str, Any]) -> dict[str, Any]:
    raw = official.get("request_extra") or official.get("request") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if key not in _SKIP_REQUEST and key not in _BOILERPLATE
    }


def _desc_covers(desc: str, request: dict[str, Any]) -> bool:
    keys = list(request)
    return bool(keys) and all(key in desc for key in keys)


def _mentioned(quote: str, name: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", quote) is not None


def _quote_covers_fields(quote: str, fields: list[str]) -> bool:
    if not fields:
        return True
    low = quote.lower()
    for label in fields:
        name = label.split()[0].lower()
        stem = name.removesuffix("_content")
        if not _mentioned(low, name) and not _mentioned(low, stem):
            return False
    return True


def _expected_fields(official: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    shape = official.get("shape") or {}
    for name, detail in shape.items():
        if not isinstance(detail, dict):
            continue
        if detail.get("expected") == "nonempty":
            bits.append(f"{name} 非空")
        elif detail.get("expected") == "absent":
            bits.append(f"{name} 不該出現")
        else:
            bits.append(str(name))
    for name in official.get("message_fields") or []:
        label = f"{name} 非空"
        if label not in bits:
            bits.append(label)
    for name in official.get("absent_fields") or []:
        label = f"{name} 不該出現"
        if label not in bits:
            bits.append(label)
    absent = official.get("absent") or {}
    for name, detail in absent.items():
        if isinstance(detail, dict) and detail.get("expected") == "absent":
            label = f"{name} 不該出現"
            if label not in bits:
                bits.append(label)
    return bits


def _observed_fields(row: dict[str, Any]) -> str:
    """欄位有無。不貼 content／reasoning 全文；全文在 response JSON。"""
    bits: list[str] = []
    bits.append("有 reasoning" if row.get("has_reasoning") else "無 reasoning")
    bits.append("有 content" if (row.get("content_preview") or "").strip() else "content 空")
    err = row.get("error_preview")
    if err:
        bits.append("error=" + clip_text(err, 80, collapse=True))
    obs = row.get("reasoning_obs") or {}
    kind = obs.get("kind")
    if kind and kind not in {"field", "absent"}:
        bits.append(f"reasoning={kind}")
    fault = row.get("fault") or {}
    if isinstance(fault, dict) and fault.get("class"):
        bits.append(str(fault["class"]))
    call_id = (row.get("response_headers") or {}).get("x-litellm-call-id")
    if call_id:
        bits.append(f"call_id={call_id}")
    return "；".join(bits)


def official_output(row: dict[str, Any]) -> str:
    """文件寫死的預期：題號、送了哪個參數、欄位有／無。同一件事只寫一次。"""
    case_id = str(row.get("case_id") or "")
    desc = str(row.get("desc") or "")
    official = row.get("official") or {}
    parts = [part for part in (case_id, desc) if part]
    if case_id and desc == case_id:
        parts = [case_id]
    quote = str(official.get("docs_quote") or "").strip()
    fields = _expected_fields(official)
    if quote:
        parts.append(quote)
    if fields and not (quote and _quote_covers_fields(quote, fields)):
        parts.append("、".join(fields))
    expect_http = official.get("expect_http")
    if expect_http not in (None, 200):
        parts.append(f"應 HTTP {expect_http}")
    elif "應拒" in desc or "應拒" in str(row.get("notes") or ""):
        parts.append("應 HTTP 4xx")
    elif (row.get("contract") or {}).get("finding") == "gateway_wider_than_official":
        parts.append("應 HTTP 4xx")
    focus = _focus_request(official)
    if focus and not _desc_covers(desc, focus):
        extras = _request_extras(focus)
        if extras:
            parts.append(extras)
    # 去重但保序
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    return "；".join(unique) or case_id


def _sent_clause(row: dict[str, Any]) -> str:
    request = row.get("request") or {}
    if not isinstance(request, dict):
        return ""
    focus = _focus_request(row.get("official") or {})
    if focus:
        sent = {key: request[key] for key in focus if key in request}
        missing = [key for key in focus if key not in request]
        parts: list[str] = []
        if sent:
            parts.append("已送 " + _request_extras(sent))
        if missing:
            parts.append("未送 " + "、".join(missing))
        return "；".join(parts)
    extras = _request_extras(request, skip=_BOILERPLATE)
    return ("已送 " + extras) if extras else ""


def endpoint_output(row: dict[str, Any]) -> str:
    """閘道實際回覆：HTTP、欄位有無、這題參數有沒有送出。不含作文預覽。"""
    status = row.get("http_status")
    bits = [f"HTTP {status}"]
    if row.get("local_reject"):
        bits.append("本機 400 未送閘道")
    bits.append(_observed_fields(row))
    sent = _sent_clause(row)
    if sent:
        bits.append(sent)
    return "；".join(bits)


def output_error(row: dict[str, Any]) -> str:
    """官方 vs endpoint 對不上才寫；通過則空字串。"""
    verdict = str(row.get("verdict") or "")
    if verdict == "pass":
        return ""
    bits: list[str] = [_VERDICT_ZH.get(verdict, verdict)]
    notes = str(row.get("notes") or "").strip()
    if notes:
        bits.append(notes)
    contract = row.get("contract") or {}
    finding = contract.get("finding")
    if finding == "gateway_wider_than_official" and "閘道比官方寬" not in notes:
        bits.append("閘道比官方寬")
    official = row.get("official") or {}
    if official.get("matches_example") is False:
        missing = [
            name
            for name, detail in (official.get("shape") or {}).items()
            if isinstance(detail, dict) and not detail.get("ok")
        ]
        extra = [
            name
            for name, detail in (official.get("absent") or {}).items()
            if isinstance(detail, dict) and not detail.get("ok")
        ]
        fresh_missing = [name for name in missing if f"缺 {name}" not in notes]
        if fresh_missing:
            bits.append("缺 " + "、".join(fresh_missing))
        fresh_extra = [name for name in extra if f"不該出現 {name}" not in notes and name not in notes]
        if fresh_extra:
            bits.append("不該出現 " + "、".join(fresh_extra))
    # 去重
    seen: set[str] = set()
    unique: list[str] = []
    for part in bits:
        if part and part not in seen:
            seen.add(part)
            unique.append(part)
    return "；".join(unique)


def comparison_rows(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in results:
        rows.append(
            {
                "official": official_output(item),
                "endpoint": endpoint_output(item),
                "error": output_error(item),
            }
        )
    return rows


def render_markdown_table(comparison: list[dict[str, str]]) -> str:
    lines = [
        "| 官方輸出 | endpoint 輸出 | 輸出錯誤 |",
        "|---|---|---|",
    ]
    for row in comparison:
        err = row.get("error") or "—"
        lines.append(
            f"| {_md_cell(row.get('official', ''))} "
            f"| {_md_cell(row.get('endpoint', ''))} "
            f"| {_md_cell(err)} |"
        )
    return "\n".join(lines)


def iter_model_results(report: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    models = report.get("models") or {}
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for name, block in models.items():
        out.append((str(name), list((block or {}).get("results") or [])))
    return out


def render_report_md(report: dict[str, Any]) -> str:
    meta = report.get("meta") or {}
    counts = meta.get("counts") or {}
    count_text = "　".join(f"{k} {v}" for k, v in counts.items()) or "（無）"
    parts = [
        "# 探測對照表",
        "",
        f"- 模型 Profile：`{meta.get('model_profile')}`",
        f"- PROBE_SCOPE：`{meta.get('probe_scope')}`",
        f"- 閘道：`{meta.get('base_url')}`",
        f"- 判定：{count_text}",
        "",
    ]
    vllm = meta.get("vllm") or {}
    if vllm.get("configured"):
        flags = vllm.get("flags") or {}
        flag_text = "　".join(f"{key}={value}" for key, value in flags.items()) or "（沒讀到旗標）"
        parts.append(f"- vLLM `{vllm.get('base_url')}`：{flag_text}")
        if vllm.get("detail"):
            parts.append(f"- vLLM：{vllm['detail']}")
        parts.append("")
    parts.extend([
        "官方輸出＝文件寫死的 HTTP／欄位；endpoint 輸出＝閘道實際回覆；輸出錯誤＝兩者對不上。",
        "不比範例作文全文，也不用思考量排名當官方證明。",
        "",
    ])
    flow = meta.get("flow")
    if isinstance(flow, dict) and flow.get("stages"):
        parts.append(render_flow_md(flow).rstrip())
        parts.append("")
    for model, results in iter_model_results(report):
        parts.append(f"## {model}")
        parts.append("")
        parts.append(render_markdown_table(comparison_rows(results)))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_report_md(json_path: Path, report: dict[str, Any] | None = None) -> Path:
    path = Path(json_path)
    if report is None:
        report = json.loads(path.read_text(encoding="utf-8"))
    md_path = path.with_suffix(".md")
    md_path.write_text(render_report_md(report), encoding="utf-8")
    return md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 flowchart_*.json 編成對照表 Markdown")
    parser.add_argument(
        "json_files",
        nargs="*",
        type=Path,
        help="results/flowchart_*.json；省略則轉 results/ 底下全部",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=ROOT / "results",
        help="未指定檔案時掃描的目錄",
    )
    args = parser.parse_args(argv)
    files = list(args.json_files)
    if not files:
        files = sorted(args.dir.glob("flowchart_*.json"))
    if not files:
        print("沒有 flowchart_*.json", file=__import__("sys").stderr)
        return 2
    for path in files:
        md_path = write_report_md(path)
        print("wrote", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
