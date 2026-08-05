"""MCP server entry-point for Wildberries API."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from wb_mcp.config import Settings
from wb_mcp.knowledge import KnowledgeBase, load_knowledge
from wb_mcp.schema import Catalog, MethodGraph, SearchIndex, load_catalog
from wb_mcp.tools import discovery, execution, graph as graph_tool, reference, workflow
from wb_mcp.transport.ratelimit import RateLimitRegistry
from wb_mcp.transport.wb import WBClient

log = structlog.get_logger()


def build_mcp(settings: Settings) -> FastMCP:
    """Build the FastMCP server with all tools registered."""
    _configure_logging()
    mcp = FastMCP(settings.server_name)

    log.info("wb_mcp_build", server_name=settings.server_name)

    catalog: Catalog = load_catalog()
    log.info(
        "wb_catalog_loaded",
        total_methods=catalog.total_methods,
        sections=[s["section"] for s in catalog.list_sections()][:20],
        categories=catalog.list_categories(),
    )

    knowledge: KnowledgeBase = load_knowledge()

    description_overrides_applied = 0
    for desc_override in knowledge.descriptions_overrides:
        method = catalog.get_by_operation_id(desc_override.operation_id)
        if method is None:
            continue
        current = (method.description or "").strip()
        override_text = desc_override.description.strip()
        if not current or len(override_text) > len(current):
            method.description = override_text
            description_overrides_applied += 1
    if description_overrides_applied:
        log.info(
            "wb_description_overrides_applied",
            applied=description_overrides_applied,
        )

    overrides_applied = 0
    for override in knowledge.safety_overrides:
        method = catalog.get_by_operation_id(override.operation_id)
        if method is not None and method.safety != override.safety:
            log.info(
                "wb_safety_override_applied",
                operation_id=override.operation_id,
                was=method.safety,
                now=override.safety,
                reason=override.reason,
            )
            method.safety = override.safety
            method.safety_reason = f"curated override: {override.reason}"
            overrides_applied += 1
    if overrides_applied:
        log.info("wb_safety_overrides_total", applied=overrides_applied)

    search = SearchIndex(catalog)
    graph = MethodGraph(catalog)
    graph.add_workflow_edges(
        [wf_chain(w) for w in knowledge.workflows if wf_chain(w)]
    )
    log.info(
        "wb_knowledge_loaded",
        workflows=len(knowledge.workflows),
        rate_limits=len(knowledge.rate_limits),
        errors=len(knowledge.errors),
        quirks=len(knowledge.quirks),
        graph_edges=graph.edge_count,
    )

    registry = RateLimitRegistry(knowledge)

    client: WBClient | None = None
    if settings.api_key:
        client = WBClient(settings.api_key, rate_limits=registry, sandbox=settings.sandbox)
        if settings.sandbox:
            log.info("wb_sandbox_enabled")

    discovery.register(mcp, catalog, search, graph=graph, knowledge=knowledge)
    reference.register(mcp, catalog, knowledge)
    workflow.register(mcp, knowledge)
    graph_tool.register(mcp, catalog, graph)
    execution.register(mcp, catalog, client, knowledge=knowledge)

    mcp._settings = {
        "confirm_write": settings.confirm_write,
        "credentials": bool(settings.api_key),
    }

    @mcp.tool()
    def wb_get_server_info() -> dict[str, Any]:
        """Get metadata about this Wildberries MCP server.

        Returns server name, catalog size, knowledge base summary, whether
        credentials are configured, and the write-confirmation mode.
        """
        return {
            "server": settings.server_name,
            "total_methods": catalog.total_methods,
            "sections": [s["section"] for s in catalog.list_sections()],
            "categories": catalog.list_categories(),
            "credentials_configured": settings.api_key is not None,
            "write_confirmation_enabled": settings.confirm_write,
            "sandbox_enabled": settings.sandbox,
            "workflows": len(knowledge.workflows),
            "rate_limits": len(knowledge.rate_limits),
            "errors_catalogued": len(knowledge.errors),
            "graph": {"nodes": graph.node_count, "edges": graph.edge_count},
        }

    return mcp


def wf_chain(w) -> list[str]:
    return [s.operation_id for s in w.steps]


def _configure_logging(level: str = "info") -> None:
    """Route all logs to stderr — MCP stdio protocol owns stdout."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        stream=sys.stderr,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )


def main() -> None:
    from wb_mcp.config import load_settings

    settings = load_settings()
    mcp = build_mcp(settings)
    mcp.run()


if __name__ == "__main__":
    main()