"""拆 curl -D - 與 -w 寫在 stdout 的狀態碼。"""

from __future__ import annotations

import re
from typing import Any

CURL_MARK = "__CURL_HTTP__"
DIAGNOSTIC_HEADERS = (
    "x-litellm-call-id",
    "x-litellm-model-id",
    "x-litellm-model-api-base",
    "x-litellm-version",
    "x-request-id",
)
_STATUS_LINE = re.compile(r"HTTP/\d+(?:\.\d+)?\s+(\d+)")


def split_curl_output(stdout: str) -> tuple[str, int | None]:
    """回傳 (headers+body, write-out HTTP code)。code 0 視為沒有狀態。"""
    if CURL_MARK not in stdout:
        return stdout, None
    raw, _, code = stdout.rpartition(CURL_MARK)
    try:
        status: int | None = int(code.strip())
    except ValueError:
        status = None
    if status == 0:
        status = None
    return raw, status


def split_headers(raw: str) -> tuple[dict[str, str], str]:
    """第一個空行之前是 header。body 裡的空行保留。"""
    if not raw.startswith("HTTP/"):
        return {}, raw
    if "\r\n\r\n" in raw:
        header_blob, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        header_blob, body = raw.split("\n\n", 1)
    else:
        return {}, raw
    headers: dict[str, str] = {}
    for line in header_blob.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers, body


def status_from_headers(header_blob_first_line: str) -> int | None:
    match = _STATUS_LINE.match(header_blob_first_line.strip())
    if not match:
        return None
    return int(match.group(1))


def select_diagnostic_headers(headers: dict[str, str]) -> dict[str, str]:
    picked: dict[str, str] = {}
    for key in DIAGNOSTIC_HEADERS:
        value = headers.get(key)
        if value:
            picked[key] = value
    return picked


def parse_curl_stdout(stdout: str) -> dict[str, Any]:
    raw, writeout_status = split_curl_output(stdout)
    headers, body = split_headers(raw)
    header_status = None
    if raw.startswith("HTTP/"):
        header_status = status_from_headers(raw.splitlines()[0])
    status = writeout_status if writeout_status is not None else header_status
    return {
        "http_status": status,
        "headers": headers,
        "body": body,
    }
