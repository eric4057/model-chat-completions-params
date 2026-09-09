"""Nested request extra helpers."""

from __future__ import annotations

from typing import Any


def set_path(dotted: str, value: Any) -> dict[str, Any]:
    """Build a nested dict from a dotted field name."""
    root: dict[str, Any] = {}
    cur = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt: dict[str, Any] = {}
        cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return root


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Shallow-copy merge; nested dicts recurse. extra wins on conflict."""
    out = dict(base)
    for key, val in extra.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(val, dict):
            out[key] = deep_merge(existing, val)
        else:
            out[key] = val
    return out
