from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from probe.report_table import (
    comparison_rows,
    endpoint_output,
    official_output,
    output_error,
    render_markdown_table,
    render_report_md,
    write_report_md,
)


def _row(**kwargs):
    base = {
        "step": "A",
        "case_id": "A.effort_low",
        "desc": "reasoning_effort=low",
        "verdict": "pass",
        "http_status": 200,
        "has_reasoning": True,
        "content_preview": "量子力學是…",
        "reasoning_preview": "think",
        "error_preview": None,
        "notes": "符合官方文件寫死的回覆欄位（非全文）",
        "request": {"model": "Kimi-K3", "reasoning_effort": "low", "message_count": 1},
        "local_reject": False,
        "contract": {"finding": None},
        "official": {
            "docs_quote": "官方：回 content 與 reasoning／reasoning_content",
            "expect_http": 200,
            "request_extra": {"reasoning_effort": "low"},
            "shape": {
                "content": {"expected": "nonempty", "ok": True},
                "reasoning_content": {"expected": "nonempty", "ok": True},
            },
            "matches_example": True,
        },
    }
    base.update(kwargs)
    return base


class TestReportTable(unittest.TestCase):
    def test_pass_has_empty_error(self) -> None:
        row = _row()
        self.assertIn("應 HTTP 200", official_output(row))
        self.assertIn("content 非空", official_output(row))
        self.assertIn("HTTP 200", endpoint_output(row))
        self.assertIn("content=", endpoint_output(row))
        self.assertEqual(output_error(row), "")

    def test_reject_200_is_error(self) -> None:
        row = _row(
            case_id="A.effort_none",
            desc="reasoning_effort=none 應拒",
            verdict="fail",
            has_reasoning=False,
            content_preview="嗨",
            reasoning_preview="",
            notes="契約不符：官方應拒，閘道卻 HTTP 200",
            official=None,
            contract={"finding": "gateway_wider_than_official"},
            request={"reasoning_effort": "none"},
        )
        self.assertIn("應 HTTP 4xx", official_output(row))
        self.assertIn("HTTP 200", endpoint_output(row))
        err = output_error(row)
        self.assertIn("FAIL", err)
        self.assertIn("閘道比官方寬", err)
        self.assertEqual(err.count("閘道比官方寬"), 1)

    def test_missing_content_listed(self) -> None:
        row = _row(
            verdict="fail",
            has_reasoning=True,
            content_preview="",
            notes="不符合官方寫死的回覆欄位：缺 content",
            official={
                "docs_quote": "官方：關閉思考後不回 reasoning",
                "expect_http": 200,
                "shape": {"content": {"expected": "nonempty", "ok": False}},
                "matches_example": False,
                "proof": "shape_mismatch",
            },
        )
        err = output_error(row)
        self.assertIn("缺 content", err)
        self.assertIn("content 空", endpoint_output(row))

    def test_locked_sampling_without_应拒_in_desc(self) -> None:
        row = _row(
            case_id="E.temperature",
            desc="temperature=0.2",
            verdict="fail",
            official=None,
            notes="契約不符：temperature=0.2 官方應拒，閘道卻 HTTP 200",
            contract={"finding": "gateway_wider_than_official"},
            request={"temperature": 0.2},
        )
        self.assertIn("應 HTTP 4xx", official_output(row))

    def test_markdown_three_columns(self) -> None:
        table = render_markdown_table(comparison_rows([_row()]))
        self.assertIn("| 官方輸出 | endpoint 輸出 | 輸出錯誤 |", table)
        self.assertIn("—", table)

    def test_write_md_from_report(self) -> None:
        report = {
            "meta": {
                "model_profile": "kimi-k3",
                "probe_scope": "official",
                "base_url": "https://example.test/v1",
                "counts": {"可用": 1, "不可用": 0},
            },
            "models": {"Kimi-K3": {"results": [_row()]}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "flowchart_Kimi-K3_x.json"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            md_path = write_report_md(json_path)
            text = md_path.read_text(encoding="utf-8")
            self.assertTrue(md_path.name.endswith(".md"))
            self.assertIn("Kimi-K3", text)
            self.assertIn("| 官方輸出 | endpoint 輸出 | 輸出錯誤 |", text)
            self.assertIn("探測對照表", render_report_md(report))


if __name__ == "__main__":
    unittest.main()
