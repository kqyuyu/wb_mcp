"""wb_call_method + wb_fetch_all — execute real Wildberries API calls.

Registered only when credentials are present in env. Before hitting the
network it runs guardrails:

1. Safety class (read / write / destructive) — requires ``confirm_write``
   / ``i_understand_this_modifies_data`` for anything that mutates data.
2. JSON Schema validation — request body is validated against the
   method's resolved schema.

Errors are always returned as a structured dict (see ``schema/errors.py``).
Callers can distinguish missing-credentials / schema-mismatch /
rate_limit / server / timeout cases programmatically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
from mcp.server.fastmcp import FastMCP

from wb_mcp.errors import (
    WBAuthError,
    WBClientValidationError,
    WBConflictError,
    WBError,
    WBForbiddenError,
    WBNotFoundError,
    WBRateLimitError,
    WBServerError,
    WBValidationError,
)
from wb_mcp.knowledge import KnowledgeBase, PaginationPattern
from wb_mcp.schema import Catalog
from wb_mcp.schema.errors import WBError as WBErrorModel
from wb_mcp.transport.wb import WBClient

MAX_RETRIES: int = 3
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

MAX_BACKOFF_SECONDS: int = 60

MAX_FETCH_ALL_ITEMS: int = 100_000

SLOW_ENDPOINTS: dict[str, asyncio.Semaphore] = {}
SLOW_ENDPOINT_MIN_DELAY: dict[str, float] = {}

_SLOW_LAST_CALL: dict[str, float] = {}
_SLOW_LOCK: asyncio.Lock | None = None


def _get_slow_lock() -> asyncio.Lock:
    global _SLOW_LOCK
    if _SLOW_LOCK is None:
        _SLOW_LOCK = asyncio.Lock()
    return _SLOW_LOCK


def _get_slow_semaphore(path: str) -> asyncio.Semaphore | None:
    if path not in SLOW_ENDPOINT_MIN_DELAY:
        return None
    sem = SLOW_ENDPOINTS.get(path)
    if sem is None:
        sem = asyncio.Semaphore(1)
        SLOW_ENDPOINTS[path] = sem
    return sem


def _err(error_type: str, message: str, **fields: Any) -> dict[str, Any]:
    """Build a structured error envelope using the WBError pydantic model."""
    return WBErrorModel(
        error=fields.pop("error", error_type),
        error_type=error_type,
        message=message,
        **fields,
    ).to_dict()


async def _execute_with_retry(
    request_func: Callable[[], Awaitable[dict[str, Any]]],
    *,
    operation_id: str,
    endpoint: str,
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """Run ``request_func`` with bounded retries on 429 / 5xx / timeout.

    Honours ``Retry-After`` when the upstream sends one; falls back to
    exponential backoff (1s, 2s, 4s) for the rest.
    """
    sem = _get_slow_semaphore(endpoint)
    if sem is not None:
        async with sem:
            await _slow_endpoint_pace(endpoint)
            return await _retry_loop(
                request_func,
                operation_id=operation_id,
                endpoint=endpoint,
                max_retries=max_retries,
            )
    return await _retry_loop(
        request_func,
        operation_id=operation_id,
        endpoint=endpoint,
        max_retries=max_retries,
    )


async def _slow_endpoint_pace(endpoint: str) -> None:
    """Sleep until at least ``MIN_DELAY`` has elapsed since the last call."""
    min_delay = SLOW_ENDPOINT_MIN_DELAY.get(endpoint)
    if not min_delay:
        return
    loop = asyncio.get_event_loop()
    async with _get_slow_lock():
        now = loop.time()
        last = _SLOW_LAST_CALL.get(endpoint)
        if last is not None:
            wait = (last + min_delay) - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
        _SLOW_LAST_CALL[endpoint] = now


async def _retry_loop(
    request_func: Callable[[], Awaitable[dict[str, Any]]],
    *,
    operation_id: str,
    endpoint: str,
    max_retries: int,
) -> dict[str, Any]:
    last_retry_after: int | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await request_func()
            return {"ok": True, "response": response}

        except WBRateLimitError as e:
            retry_after_raw = e.retry_after if e.retry_after is not None else 60
            try:
                retry_after = int(float(retry_after_raw))
            except (TypeError, ValueError):
                retry_after = 60
            last_retry_after = retry_after
            if attempt < max_retries:
                await asyncio.sleep(min(retry_after, MAX_BACKOFF_SECONDS))
                continue
            return _err(
                "rate_limit",
                f"Rate limit hit after {max_retries} retries",
                code=429,
                status_code=429,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=True,
                retry_after_seconds=retry_after,
                payload=e.payload,
            )

        except WBServerError as e:
            if "timeout" in (e.message or "").lower():
                last_exc_type: str = "timeout"
            else:
                last_exc_type = "server_error"
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** attempt, MAX_BACKOFF_SECONDS))
                continue
            return _err(
                last_exc_type,
                e.message or "upstream server error",
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=True,
                payload=e.payload,
            )

        except WBAuthError as e:
            return _err(
                "auth",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )
        except WBForbiddenError as e:
            return _err(
                "forbidden",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )
        except WBNotFoundError as e:
            return _err(
                "not_found",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )
        except WBConflictError as e:
            return _err(
                "conflict",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )
        except WBValidationError as e:
            return _err(
                "invalid_params",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )
        except WBError as e:
            return _err(
                "unknown",
                e.message,
                code=e.status_code,
                status_code=e.status_code,
                operation_id=operation_id,
                endpoint=endpoint,
                retryable=False,
                payload=e.payload,
            )

    return _err(
        "unknown",
        f"max retries ({max_retries}) exceeded",
        operation_id=operation_id,
        endpoint=endpoint,
        retryable=False,
        retry_after_seconds=last_retry_after,
    )


def register(
    mcp: FastMCP,
    catalog: Catalog,
    client: WBClient | None,
    *,
    knowledge: KnowledgeBase | None = None,
) -> None:
    @mcp.tool()
    async def wb_call_method(
        operation_id: str,
        params: dict[str, Any] | list[Any] | None = None,
        confirm_write: bool = False,
        i_understand_this_modifies_data: bool = False,
    ) -> dict[str, Any]:
        """Execute a real call against the Wildberries API.

        SAFETY MODEL — read methods just work; write/destructive methods
        require explicit confirmation flags. Each method's safety class is
        visible in `wb_describe_method` (`safety` field).

          - safety="read":        no flag needed
          - safety="write":       requires confirm_write=True
          - safety="destructive": requires BOTH confirm_write=True AND
                                  i_understand_this_modifies_data=True

        RATE LIMITS — 429 responses are retried up to MAX_RETRIES times
        honouring Retry-After.

        On any failure returns a structured ``WBError`` envelope —
        agents should inspect ``error_type`` and decide.

        Args:
            operation_id: e.g. "get_content_v2_cards_list" or a native
                operationId from the spec
            params: request body matching the method's request_schema.
                Can be a dict or a list (for endpoints that expect an array
                in the request body, e.g. post_content_v2_cards_update).
            confirm_write: required when method.safety == "write" or "destructive"
            i_understand_this_modifies_data: extra confirmation for destructive
        """
        return await _call_method(
            catalog=catalog,
            knowledge=knowledge,
            client=client,
            operation_id=operation_id,
            params=params,
            confirm_write=confirm_write,
            i_understand_this_modifies_data=i_understand_this_modifies_data,
        )

    @mcp.tool()
    async def wb_fetch_all(
        operation_id: str,
        params: dict[str, Any] | None = None,
        max_items: int = 10000,
    ) -> dict[str, Any]:
        """Fetch all pages of a paginated Wildberries endpoint.

        Walks the endpoint's pagination pattern (offset/page/last_id/cursor/
        page_token — see ``knowledge/pagination_patterns.yaml``) until the
        endpoint reports the last page or ``max_items`` is reached. Per-page
        rate limits are still enforced via the same machinery as
        ``wb_call_method``.

        Args:
            operation_id: same as wb_call_method, must support pagination
            params: request body WITHOUT offset/limit/last_id/cursor — the
                paginator owns those fields
            max_items: safety cap, range [1, MAX_FETCH_ALL_ITEMS]

        Returns:
            ``{"items": [...], "total_fetched": N, "truncated": bool,
              "pages_fetched": int}`` on success or a structured WBError
            on failure.
        """
        if not isinstance(max_items, int) or max_items < 1:
            return _err(
                "invalid_params",
                f"max_items must be a positive integer, got {max_items!r}",
                operation_id=operation_id,
            )
        if max_items > MAX_FETCH_ALL_ITEMS:
            return _err(
                "invalid_params",
                (
                    f"max_items={max_items} exceeds safety cap "
                    f"{MAX_FETCH_ALL_ITEMS}; raise MAX_FETCH_ALL_ITEMS or "
                    f"split your query."
                ),
                operation_id=operation_id,
            )
        if knowledge is None:
            return _err(
                "invalid_params",
                "knowledge base unavailable — pagination patterns not loaded",
                operation_id=operation_id,
            )
        pattern = knowledge.pagination_for(operation_id)
        if pattern is None:
            return _err(
                "invalid_params",
                f"{operation_id} has no pagination pattern — use wb_call_method",
                operation_id=operation_id,
            )
        return await _fetch_all_pages(
            catalog=catalog,
            knowledge=knowledge,
            client=client,
            operation_id=operation_id,
            base_params=params or {},
            pattern=pattern,
            max_items=max_items,
        )


async def _call_method(
    *,
    catalog: Catalog,
    knowledge: KnowledgeBase | None,
    client: WBClient | None,
    operation_id: str,
    params: dict[str, Any] | list[Any] | None,
    confirm_write: bool,
    i_understand_this_modifies_data: bool,
) -> dict[str, Any]:
    method = catalog.get_by_operation_id(operation_id)
    if method is None:
        return _err(
            "not_found",
            f"operation_id {operation_id!r} not found",
            operation_id=operation_id,
            error="NotFound",
        )

    if method.safety == "write" and not confirm_write:
        return _err(
            "write_requires_confirmation",
            (
                f"Method {operation_id} is classified as 'write' (modifies data on "
                f"Wildberries server-side) and requires explicit confirmation. Pass "
                f"confirm_write=True to proceed. Reason: {method.safety_reason}"
            ),
            operation_id=operation_id,
            error="WriteRequiresConfirmation",
            payload={"safety": method.safety, "safety_reason": method.safety_reason},
            safety=method.safety,
            safety_reason=method.safety_reason,
        )
    if method.safety == "destructive" and not (
        confirm_write and i_understand_this_modifies_data
    ):
        return _err(
            "destructive_requires_double_confirmation",
            (
                f"Method {operation_id} is classified as 'destructive' (deletes / "
                f"cancels / archives data) and requires BOTH confirm_write=True "
                f"AND i_understand_this_modifies_data=True. "
                f"Reason: {method.safety_reason}"
            ),
            operation_id=operation_id,
            error="DestructiveRequiresDoubleConfirmation",
            payload={"safety": method.safety, "safety_reason": method.safety_reason},
            safety=method.safety,
            safety_reason=method.safety_reason,
        )

    if client is None:
        return _err(
            "missing_credentials",
            (
                "Wildberries credentials not configured — "
                "set WB_API_KEY in the environment"
            ),
            operation_id=operation_id,
            error="MissingCredentials",
        )

    body = params or {}
    try:
        _validate(method.request_schema, body)
    except WBClientValidationError as e:
        return _err(
            "invalid_params",
            e.message,
            operation_id=operation_id,
            error="WBClientValidationError",
            payload=e.payload,
        )

    async def request_func() -> dict[str, Any]:
        return await client.request_for_method(
            method,
            json_body=body if method.method != "GET" else None,
            with_retry=False,
        )

    return await _execute_with_retry(
        request_func,
        operation_id=operation_id,
        endpoint=method.path,
    )


async def _fetch_all_pages(
    *,
    catalog: Catalog,
    knowledge: KnowledgeBase | None,
    client: WBClient | None,
    operation_id: str,
    base_params: dict[str, Any],
    pattern: PaginationPattern,
    max_items: int,
) -> dict[str, Any]:
    all_items: list[Any] = []
    pages_fetched = 0

    page_size = min(pattern.default_limit, pattern.max_limit, max(1, max_items))
    offset = 0
    page_number = 1
    cursor_value: str | None = None
    last_id_value: Any = None
    page_token_value: str | None = None
    prev_cursor_value: str | None = None
    prev_last_id_value: Any = object()
    prev_page_token_value: str | None = None

    while True:
        params: dict[str, Any] = dict(base_params)
        params[pattern.request_limit_field] = page_size

        offset_field = pattern.request_offset_field
        if pattern.type == "offset_limit" and offset_field:
            params[offset_field] = offset
        elif pattern.type == "page_number" and offset_field:
            params[offset_field] = page_number
        elif pattern.type == "last_id" and offset_field:
            if last_id_value is not None:
                params[offset_field] = last_id_value
            else:
                params.setdefault(offset_field, "")
        elif pattern.type == "cursor" and offset_field:
            if cursor_value is not None:
                params[offset_field] = cursor_value
            else:
                params.setdefault(offset_field, "")
        elif pattern.type == "page_token" and offset_field:
            if page_token_value is not None:
                params[offset_field] = page_token_value
            else:
                params.setdefault(offset_field, "")

        result = await _call_method(
            catalog=catalog,
            knowledge=knowledge,
            client=client,
            operation_id=operation_id,
            params=params,
            confirm_write=False,
            i_understand_this_modifies_data=False,
        )

        if not result.get("ok"):
            result.setdefault("partial_items", all_items)
            result.setdefault("pages_fetched", pages_fetched)
            return result

        response = result.get("response") or {}
        items = _extract_items(response, pattern.response_items_field)
        if not isinstance(items, list):
            items = []

        all_items.extend(items)
        pages_fetched += 1

        if len(all_items) >= max_items:
            break
        if not items or len(items) < page_size:
            break

        if pattern.type == "offset_limit":
            offset += page_size
        elif pattern.type == "page_number":
            page_number += 1
        elif pattern.type == "last_id":
            new_last_id: Any = _extract_field(
                response, pattern.response_total_field
            )
            if new_last_id in (None, "", 0):
                new_last_id = _last_id_from_item(items[-1])
                if new_last_id is None:
                    break
            if new_last_id == prev_last_id_value:
                break
            prev_last_id_value = new_last_id
            last_id_value = new_last_id
        elif pattern.type == "cursor":
            new_cursor = (
                _extract_field(response, pattern.response_total_field)
                or _extract_field(response, "cursor")
            )
            if not new_cursor:
                break
            if new_cursor == prev_cursor_value:
                break
            prev_cursor_value = new_cursor
            cursor_value = new_cursor
        elif pattern.type == "page_token":
            new_token = _extract_field(response, "next_page_token") or (
                _extract_field(response, pattern.response_total_field)
            )
            if not new_token:
                break
            if new_token == prev_page_token_value:
                break
            prev_page_token_value = new_token
            page_token_value = new_token

        await asyncio.sleep(0.05)

    truncated = len(all_items) >= max_items
    return {
        "ok": True,
        "items": all_items[:max_items],
        "total_fetched": min(len(all_items), max_items),
        "truncated": truncated,
        "pages_fetched": pages_fetched,
    }


def _extract_items(response: dict[str, Any], items_field: str) -> Any:
    """Return the array Wildberries nested at ``items_field``."""
    if items_field in response:
        return response[items_field]
    inner = response.get("result")
    if isinstance(inner, dict) and items_field in inner:
        return inner[items_field]
    if isinstance(inner, list) and items_field == "result":
        return inner
    return []


def _extract_field(response: dict[str, Any], field: str | None) -> Any:
    """Look up ``field`` on the response, walking into ``result`` if nested."""
    if not field:
        return None
    if field in response:
        return response[field]
    inner = response.get("result")
    if isinstance(inner, dict) and field in inner:
        return inner[field]
    return None


def _last_id_from_item(item: Any) -> Any:
    if isinstance(item, dict):
        for key in ("last_id", "id", "nmID", "supplierArticle", "orderId"):
            if key in item:
                return item[key]
    return None


def _validate(schema: dict[str, Any] | None, payload: dict[str, Any] | list[Any]) -> None:
    if not schema:
        return
    try:
        jsonschema.validate(
            payload,
            schema,
            cls=jsonschema.Draft202012Validator,
        )
    except jsonschema.ValidationError as e:
        raise WBClientValidationError(
            f"client-side validation failed: {e.message}",
            payload={
                "path": list(e.absolute_path),
                "validator": e.validator,
                "validator_value": e.validator_value,
            },
        ) from e
    except jsonschema.SchemaError:
        return
    except Exception as e:
        module = type(e).__module__ or ""
        if module.startswith(("jsonschema", "referencing")):
            return
        raise
