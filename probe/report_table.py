"""把探測 JSON 編成「官方輸出 / endpoint 輸出 / 輸出錯誤」對照表。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_SKIP_REQUEST = frozenset({"model", "messages", "message_count", "first_user"})
_VERDICT_ZH = {
    "pass": "OK",
    "fail": "FAIL",
    "noop": "NOOP",
    "skip": "SKIP",
}


def _clip(text: Any, limit: int = 96) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def _md_cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _request_extras(request: dict[str, Any] | None) -> str:
    if not request:
        return ""
    parts: list[str] = []
    for key, value in request.items():
        if key in _SKIP_REQUEST:
            continue
        parts.append(f"{key}={value}")
    return "；".join(parts)


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
    bits: list[str] = []
    if row.get("has_reasoning"):
        bits.append("有 reasoning")
    else:
        bits.append("無 reasoning")
    content = (row.get("content_preview") or "").strip()
    if content:
        bits.append("content=" + _clip(content, 72))
    else:
        bits.append("content 空")
    reasoning = (row.get("reasoning_preview") or "").strip()
    if reasoning:
        bits.append("reasoning=" + _clip(reasoning, 72))
    err = row.get("error_preview")
    if err:
        bits.append("error=" + _clip(err, 80))
    return "；".join(bits)


def official_output(row: dict[str, Any]) -> str:
    """文件寫死的預期：HTTP、欄位有／無。不是範例作文全文。"""
    case_id = str(row.get("case_id") or "")
    desc = str(row.get("desc") or "")
    official = row.get("official") or {}
    parts = [p for p in (case_id, desc) if p]
    quote = str(official.get("docs_quote") or "").strip()
    if quote:
        parts.append(quote)
    fields = _expected_fields(official)
    if fields:
        parts.append("、".join(fields))
    expect_http = official.get("expect_http")
    if expect_http is not None:
        parts.append(f"應 HTTP {expect_http}")
    elif "應拒" in desc or "應拒" in str(row.get("notes") or ""):
        parts.append("應 HTTP 4xx")
    elif (row.get("contract") or {}).get("finding") == "gateway_wider_than_official":
        parts.append("應 HTTP 4xx")
    extras = _request_extras(official.get("request_extra") or official.get("request"))
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


def endpoint_output(row: dict[str, Any]) -> str:
    """閘道實際回覆（HTTP + 觀測欄位）。"""
    status = row.get("http_status")
    bits = [f"HTTP {status}"]
    if row.get("local_reject"):
        bits.append("本機 400 未送閘道")
    bits.append(_observed_fields(row))
    extras = _request_extras(row.get("request"))
    if extras:
        bits.append("已送 " + extras)
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
        if missing:
            bits.append("缺 " + "、".join(missing))
        if extra:
            bits.append("不該出現 " + "、".join(extra))
        proof = official.get("proof")
        if proof and proof not in {"response_shape"}:
            bits.append(str(proof))
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
        "官方輸出＝文件寫死的 HTTP／欄位；endpoint 輸出＝閘道實際回覆；輸出錯誤＝兩者對不上。",
        "不比範例作文全文，也不用思考量排名當官方證明。",
        "",
    ]
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
