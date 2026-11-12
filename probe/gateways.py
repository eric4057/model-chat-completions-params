"""Portal / Inner 閘道位址。金鑰只留在伺服器行程，不回給瀏覽器。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class GatewayEndpoint:
    id: str
    label: str
    base_url: str
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def public(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "label": self.label,
            "configured": self.configured,
            "host": urlparse(self.base_url).netloc,
            "base_url": self.base_url,
        }


_SPECS = (
    {
        "id": "inner",
        "label": "INNER",
        "url_env": "INNER_BASE_URL",
        "default_url": "https://inner-medusa.genai.nchc.org.tw/v1",
        "key_env": "INNER_API_KEY",
    },
    {
        "id": "portal",
        "label": "PORTAL",
        "url_env": "PORTAL_BASE_URL",
        "default_url": "https://portal.genai.nchc.org.tw/api/v1",
        "key_env": "PORTAL_API_KEY",
    },
)


def resolve_endpoints(env: dict[str, str]) -> tuple[GatewayEndpoint, ...]:
    """INNER／PORTAL。專用金鑰優先；沒有時，只有網址與 OPENAI_BASE_URL 相同才沿用 OPENAI_API_KEY。"""
    shared_url = env.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    shared_key = env.get("OPENAI_API_KEY", "").strip()
    found: list[GatewayEndpoint] = []
    for spec in _SPECS:
        base_url = (env.get(spec["url_env"], "").strip() or spec["default_url"]).rstrip("/")
        api_key = env.get(spec["key_env"], "").strip()
        if not api_key and shared_key and base_url == shared_url:
            api_key = shared_key
        found.append(
            GatewayEndpoint(
                id=spec["id"],
                label=spec["label"],
                base_url=base_url,
                api_key=api_key,
            )
        )
    return tuple(found)


def overlay_endpoint(
    endpoint: GatewayEndpoint,
    *,
    base_url: str = "",
    api_key: str = "",
) -> GatewayEndpoint:
    """用畫面上填的網址或金鑰蓋過預設。空白金鑰沿用原本那把，不回傳金鑰以外的內容。"""
    url = (base_url or endpoint.base_url).strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint 必須是 http 或 https")
    key = api_key.strip() or endpoint.api_key
    return GatewayEndpoint(
        id=endpoint.id,
        label=endpoint.label,
        base_url=url,
        api_key=key,
    )


def present_endpoints(
    env: dict[str, str],
    session: dict[str, GatewayEndpoint] | None = None,
) -> tuple[GatewayEndpoint, ...]:
    """網站用。網址可以有預設，金鑰不從環境變數帶入。session 只留使用者這次填的。"""
    saved = session or {}
    shown: list[GatewayEndpoint] = []
    for item in resolve_endpoints(env):
        blank = GatewayEndpoint(item.id, item.label, item.base_url, "")
        shown.append(saved.get(item.id, blank))
    return tuple(shown)


def endpoint_by_id(env: dict[str, str], endpoint_id: str) -> GatewayEndpoint | None:
    for item in resolve_endpoints(env):
        if item.id == endpoint_id:
            return item
    return None
