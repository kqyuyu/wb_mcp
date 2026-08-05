"""Reference tools — rate limits, error catalog, code examples."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from wb_mcp.knowledge import KnowledgeBase
from wb_mcp.schema import Catalog
from wb_mcp.schema.errors import make_error


def register(mcp: FastMCP, catalog: Catalog, kb: KnowledgeBase) -> None:
    @mcp.tool()
    def wb_get_rate_limits(
        operation_id: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Look up rate limits for a method, section, or the whole API.

        Without arguments returns all known limits. With operation_id, returns
        the most specific limit (per-method overrides per-section overrides global).

        NOTE: The knowledge base is populated incrementally; empty results mean
        no limit data has been curated for that scope yet.
        """
        if operation_id:
            m = catalog.get_by_operation_id(operation_id)
            if m is None:
                return make_error(
                    "not_found",
                    f"operation_id {operation_id!r} not found",
                    operation_id=operation_id,
                    error="NotFound",
                )
            limit = kb.rate_limit_for(operation_id, api=m.api, section=m.section)
            return {
                "operation_id": operation_id,
                "rate_limit": limit.model_dump() if limit else None,
            }
        if section:
            matching = [r for r in kb.rate_limits if r.section == section]
            return {
                "section": section,
                "limits": [r.model_dump() for r in matching],
            }
        return {
            "all_limits": [r.model_dump() for r in kb.rate_limits],
        }

    @mcp.tool()
    def wb_get_error_catalog(
        code: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Look up Wildberries API errors and their solutions.

        Without arguments returns all known errors. With code (e.g. "429" or
        "WrongParameterValue") filters by code. With operation_id returns errors
        specific to that method plus all generic ones.
        """
        if code:
            matches = kb.errors_by_code(code)
            return {
                "code": code,
                "count": len(matches),
                "errors": [e.model_dump() for e in matches],
            }
        if operation_id:
            specific = kb.errors_for(operation_id)
            generic = [e for e in kb.errors if e.operation_id is None]
            return {
                "operation_id": operation_id,
                "specific": [e.model_dump() for e in specific],
                "generic": [e.model_dump() for e in generic],
            }
        return {
            "count": len(kb.errors),
            "errors": [e.model_dump() for e in kb.errors],
        }

    @mcp.tool()
    def wb_get_examples(operation_id: str) -> dict[str, Any]:
        """Get hand-crafted request examples for one method.

        Examples are real, validated payloads matching the method's request
        schema — copy them as starting points for your own calls.
        """
        if catalog.get_by_operation_id(operation_id) is None:
            return make_error(
                "not_found",
                f"operation_id {operation_id!r} not found",
                operation_id=operation_id,
                error="NotFound",
            )
        examples = kb.examples_for(operation_id)
        return {
            "operation_id": operation_id,
            "count": len(examples),
            "examples": [e.model_dump() for e in examples],
        }