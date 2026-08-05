"""Wildberries API client — single Authorization header token auth.

Wildberries uses one API key per token category (content, marketplace,
statistics, advert, ...). Each category has its own domain; the extractor
stores the per-method ``base_url`` from the swagger ``servers`` block.
This client routes each call to the method's designated domain, falling
back to ``base_url`` when none is recorded.
"""

from __future__ import annotations

from typing import Any

from wb_mcp.transport.base import BaseClient
from wb_mcp.transport.ratelimit import RateLimitRegistry
from wb_mcp.transport.sandbox import sandbox_url

_DEFAULT_BASE_URL = "https://common-api.wildberries.ru"


class WBClient(BaseClient):
    base_url = _DEFAULT_BASE_URL
    api_label = "wb"

    def __init__(
        self,
        api_key: str,
        *,
        rate_limits: RateLimitRegistry,
        timeout: float = 30.0,
        max_retries: int = 3,
        sandbox: bool = False,
    ) -> None:
        super().__init__(rate_limits=rate_limits, timeout=timeout, max_retries=max_retries)
        self._api_key = api_key
        self._sandbox = sandbox

    async def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

    async def request_for_method(
        self,
        method: Any,
        *,
        json_body: dict[str, Any] | None = None,
        with_retry: bool = True,
    ) -> dict[str, Any]:
        """Execute a call routing to the method's designated base URL.

        Wildberries splits API methods across dozens of domains
        (content-api, marketplace-api, statistics-api, ...). The catalog
        records each method's ``base_url`` from the swagger ``servers``
        block; this helper swaps the client's base URL for the duration
        of the call.
        """
        base_url = getattr(method, "base_url", None) or self.base_url
        if self._sandbox:
            base_url = sandbox_url(base_url)
        if base_url != self.base_url:
            self._client.base_url = base_url
            try:
                return await self.request(
                    method.method,
                    method.path,
                    json_body=json_body,
                    operation_id=method.operation_id,
                    section=method.section,
                    with_retry=with_retry,
                )
            finally:
                self._client.base_url = self.base_url
        return await self.request(
            method.method,
            method.path,
            json_body=json_body,
            operation_id=method.operation_id,
            section=method.section,
            with_retry=with_retry,
        )
