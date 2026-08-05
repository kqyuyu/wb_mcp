"""OpenAPI YAML → JSON Schema engine — the core of wb-mcp.

Public surface:
    Method        — pydantic model representing one Wildberries API method
    Catalog       — in-memory index of all methods
    SearchIndex   — BM25 search over the catalog
    load_catalog  — factory that builds Catalog from bundled YAML specs
"""

from wb_mcp.schema.catalog import Catalog, load_catalog
from wb_mcp.schema.extractor import Method
from wb_mcp.schema.graph import MethodGraph
from wb_mcp.schema.search import SearchIndex, SearchResult

__all__ = [
    "Catalog",
    "Method",
    "MethodGraph",
    "SearchIndex",
    "SearchResult",
    "load_catalog",
]