#!/usr/bin/env python3
"""本機網站：選 INNER／PORTAL、模型、官方參數，再跟文件欄位比對。

金鑰留在行程裡。瀏覽器只拿得到閘道 host 與探測結果。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probe.choices import (
    COMPARE_NOTE,
    MATCH_LABEL,
    apply_output_limit,
    apply_sampling,
    format_chat_call,
    parameter_choices,
    select_cases,
)
from probe.faults import clip_text
from probe.flow import build_flow
from probe.gateways import GatewayEndpoint, overlay_endpoint, parse_env_text, present_endpoints
from probe.profile import load_profiles, match_profile
from probe.report_table import endpoint_output, official_output, output_error

STATIC = Path(__file__).resolve().parent / "static"
MODELS_DIR = ROOT / "configs" / "models"
GENERIC = ROOT / "configs" / "profiles" / "generic.json"
HOST = "0.0.0.0"
PORT = 8787
_LOCK = threading.Lock()
_SESSION_LOCK = threading.Lock()
_SESSION: dict[str, GatewayEndpoint] = {}
_PROBE = None


def _read_env_file() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    return parse_env_text(path.read_text(encoding="utf-8"))


def runtime_env() -> dict[str, str]:
    """已 export 的變數蓋過 .env，與探測腳本相同。"""
    merged = _read_env_file()
    merged.update({key: value for key, value in os.environ.items() if value})
    return merged


def _probe():
    global _PROBE
    if _PROBE is None:
        path = ROOT / "scripts" / "probe_flowchart.py"
        spec = importlib.util.spec_from_file_location("probe_flowchart_web", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("無法載入探測腳本")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PROBE = module
    return _PROBE


def _profiles():
    return load_profiles(MODELS_DIR)


def _prompt() -> str:
    body = json.loads(GENERIC.read_text(encoding="utf-8"))
    return str(body["prompt"])


def _use_gateway(endpoint: GatewayEndpoint) -> Any:
    probe = _probe()
    probe.BASE_URL = endpoint.base_url.rstrip("/")
    probe.API_KEY = endpoint.api_key
    return probe


def list_models(endpoint: GatewayEndpoint) -> list[dict[str, Any]]:
    with _LOCK:
        probe = _use_gateway(endpoint)
        listed = probe.get_json("/models")
    if not listed.get("ok"):
        status = listed.get("http_status")
        raise RuntimeError(f"GET /models 失敗 HTTP {status}")
    ids = sorted(
        {
            str(item.get("id"))
            for item in (listed.get("body") or {}).get("data") or []
            if item.get("id")
        }
    )
    profiles = _profiles()
    rows = []
    for model_id in ids:
        profile = match_profile(model_id, profiles)
        choice = parameter_choices(profile)
        rows.append(
            {
                "id": model_id,
                "profile_id": profile.id,
                "family": profile.family,
                "has_params": bool(choice["groups"]),
            }
        )
    rows.sort(key=lambda item: (not item["has_params"], item["id"].lower()))
    return rows


def describe_model(endpoint: GatewayEndpoint, model_id: str) -> dict[str, Any]:
    models = list_models(endpoint)
    known = {item["id"]: item for item in models}
    if model_id not in known:
        raise ValueError(f"閘道上沒有 {model_id}")
    profile = match_profile(model_id, _profiles())
    body = parameter_choices(profile)
    body["model"] = model_id
    body["endpoint"] = endpoint.public()
    return body


def run_probe(
    endpoint: GatewayEndpoint,
    model_id: str,
    case_ids: list[str],
    max_output: int | None = None,
    sampling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = match_profile(model_id, _profiles())
    if max_output is not None:
        profile = apply_output_limit(profile, max_output)
    if sampling:
        profile = apply_sampling(profile, sampling)
    try:
        selected = select_cases(profile, case_ids)
    except ValueError:
        raise
    if not selected:
        flow = build_flow(
            model=model_id,
            profile_id=profile.id,
            family=profile.family,
            probe_scope="official",
            vllm={"configured": False},
            results=[],
        )
        return {
            "model": model_id,
            "family": profile.family,
            "compare_note": COMPARE_NOTE,
            "flow": flow,
            "rows": [],
        }
    with _LOCK:
        probe = _use_gateway(endpoint)
        probe.MODEL_PROFILE = profile
        probe.PROBE_SCOPE = "official"
        rows = probe.execute_contract_cases(
            model_id,
            [{"role": "user", "content": _prompt()}],
            selected,
            baseline={"http_status": None},
            scope="official",
        )
    flow = build_flow(
        model=model_id,
        profile_id=profile.id,
        family=profile.family,
        probe_scope="official",
        vllm={"configured": False},
        results=rows,
    )
    table = []
    for row in rows:
        verdict = str(row.get("verdict") or "")
        official = row.get("official") or {}
        table.append(
            {
                "case_id": row.get("case_id"),
                "desc": row.get("desc"),
                "verdict": verdict,
                "match": MATCH_LABEL.get(verdict, verdict),
                "http_status": row.get("http_status"),
                "official": official_output(row),
                "actual": endpoint_output(row),
                "difference": output_error(row) or "—",
                "source": official.get("source") or "",
                "docs_quote": official.get("docs_quote") or "",
                "notes": str(row.get("notes") or "").strip(),
                "has_reasoning": bool(row.get("has_reasoning")),
                "content_preview": clip_text(row.get("content_preview"), 180, collapse=True),
                "reasoning_preview": clip_text(row.get("reasoning_preview"), 180, collapse=True),
                "error_preview": clip_text(row.get("error_preview"), 180, collapse=True),
                "response_json": row.get("response_json") or "",
                "request_code": format_chat_call(
                    row.get("request_body") or {},
                    url=endpoint.base_url,
                    env_file=str(ROOT / ".env"),
                )
                if row.get("request_body")
                else "",
            }
        )
    return {
        "model": model_id,
        "family": profile.family,
        "compare_note": COMPARE_NOTE,
        "flow": flow,
        "rows": table,
    }


def live_endpoints() -> tuple[GatewayEndpoint, ...]:
    with _SESSION_LOCK:
        session = dict(_SESSION)
    return present_endpoints(runtime_env(), session)


def live_endpoint(endpoint_id: str) -> GatewayEndpoint:
    found = next((item for item in live_endpoints() if item.id == endpoint_id), None)
    if found is None:
        raise ValueError("endpoint 只能是 inner 或 portal")
    if not found.configured:
        raise ValueError(f"{found.label} 尚未設定 API 金鑰")
    return found


def save_credentials(endpoint_id: str, base_url: str, api_key: str) -> GatewayEndpoint:
    current = next((item for item in live_endpoints() if item.id == endpoint_id), None)
    if current is None:
        raise ValueError("endpoint 只能是 inner 或 portal")
    updated = overlay_endpoint(current, base_url=base_url, api_key=api_key)
    if not updated.configured:
        raise ValueError("請輸入 API 金鑰。")
    with _SESSION_LOCK:
        _SESSION[endpoint_id] = updated
    return updated


class App(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 65536:
            raise ValueError("請求過大")
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("請求必須是 JSON object")
        return body

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                page = (STATIC / "index.html").read_bytes()
                self._send(200, page, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/endpoints":
                self._json(
                    200,
                    {
                        "compare_note": COMPARE_NOTE,
                        "endpoints": [item.public() for item in live_endpoints()],
                    },
                )
                return
            query = parse_qs(parsed.query)
            if parsed.path == "/api/models":
                endpoint = live_endpoint((query.get("endpoint") or [""])[0])
                self._json(200, {"models": list_models(endpoint)})
                return
            if parsed.path == "/api/params":
                endpoint = live_endpoint((query.get("endpoint") or [""])[0])
                model = (query.get("model") or [""])[0].strip()
                if not model:
                    raise ValueError("請選擇模型")
                self._json(200, describe_model(endpoint, model))
                return
            self._json(404, {"error": "找不到這個路徑"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(502, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"伺服器錯誤：{exc}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/credentials":
                saved = save_credentials(
                    str(body.get("endpoint") or ""),
                    str(body.get("base_url") or ""),
                    str(body.get("api_key") or ""),
                )
                self._json(200, saved.public())
                return
            if parsed.path != "/api/run":
                self._json(404, {"error": "找不到這個路徑"})
                return
            endpoint = live_endpoint(str(body.get("endpoint") or ""))
            model = str(body.get("model") or "").strip()
            if not model:
                raise ValueError("請選擇模型")
            case_ids = body.get("case_ids") or ["ALL"]
            if not isinstance(case_ids, list) or not all(isinstance(item, str) for item in case_ids):
                raise ValueError("case_ids 必須是字串陣列")
            raw_limit = body.get("max_output")
            max_output = None if raw_limit is None else raw_limit
            raw_sampling = body.get("sampling") or {}
            if not isinstance(raw_sampling, dict):
                raise ValueError("sampling 必須是 object")
            self._json(200, run_probe(endpoint, model, case_ids, max_output, raw_sampling))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(502, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"伺服器錯誤：{exc}"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), App)
    print(f"http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
