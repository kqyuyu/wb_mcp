"""In-memory index of all Wildberries API methods."""

from __future__ import annotations

from typing import Any, Literal

from wb_mcp.schema.extractor import Method, MethodExtractor
from wb_mcp.schema.loader import load_wb_specs
from wb_mcp.schema.resolver import RefResolver

ApiLabel = Literal["wb"]

_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch"})


def _first_server_url(path_item: dict[str, Any]) -> str | None:
    """Return the first production server URL for a path item, if any.

    Wildberries declares one or more ``servers`` blocks at the path level;
    the first entry is the production domain (e.g.
    ``https://content-api.wildberries.ru``), the second is the sandbox
    domain. We prefer the first.
    """
    servers = path_item.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict):
            url = first.get("url")
            if isinstance(url, str) and url:
                return url.rstrip("/")
    return None


class Catalog:
    """Lookup-friendly view over the full set of extracted methods."""

    def __init__(self, methods: list[Method]) -> None:
        self.methods = methods
        self.by_operation_id: dict[str, Method] = {
            m.operation_id: m for m in methods if m.operation_id
        }
        self.by_path: dict[tuple[str, str, str], Method] = {
            (m.api, m.method, m.path): m for m in methods
        }
        self._sections: dict[tuple[str, str], list[Method]] = {}
        for m in methods:
            self._sections.setdefault((m.api, m.section), []).append(m)
        self._tags: dict[tuple[str, str], list[Method]] = {}
        for m in methods:
            self._tags.setdefault((m.api, m.tag), []).append(m)

    def get_by_operation_id(self, operation_id: str) -> Method | None:
        return self.by_operation_id.get(operation_id)

    def get_by_path(
        self, api: ApiLabel, http_method: str, path: str
    ) -> Method | None:
        return self.by_path.get((api, http_method.upper(), path))

    def find_by_path(self, path: str) -> list[Method]:
        return [m for m in self.methods if m.path == path]

    def list_sections(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for (api, section), methods in self._sections.items():
            result.append(
                {
                    "api": api,
                    "section": section,
                    "tag": methods[0].tag,
                    "count": len(methods),
                }
            )
        return sorted(result, key=lambda x: (x["api"], x["section"]))

    def list_categories(self) -> list[dict[str, Any]]:
        """Unique ``x-category`` values (token categories) with method counts."""
        counts: dict[str, int] = {}
        for m in self.methods:
            if m.category:
                counts[m.category] = counts.get(m.category, 0) + 1
        return [
            {"category": cat, "count": cnt}
            for cat, cnt in sorted(counts.items())
        ]

    def get_section(self, query: str) -> list[Method]:
        q = query.lower()
        seen: set[str] = set()
        out: list[Method] = []
        for m in self.methods:
            if m.operation_id in seen:
                continue
            if q in m.section.lower() or q in m.tag.lower():
                out.append(m)
                seen.add(m.operation_id)
        return out

    def get_category(self, category: str) -> list[Method]:
        q = category.lower()
        return [m for m in self.methods if m.category and m.category.lower() == q]

    @property
    def total_methods(self) -> int:
        return len(self.methods)


def load_catalog() -> Catalog:
    """Build the global Catalog from the bundled Wildberries YAML files."""
    methods: list[Method] = []
    for spec in load_wb_specs():
        resolver = RefResolver(spec)
        extractor = MethodExtractor(resolver)
        tags_display = {
            t.get("name", ""): t.get("x-displayName", t.get("name", ""))
            for t in spec.get("tags", [])
            if isinstance(t, dict)
        }
        for path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            base_url = _first_server_url(path_item)
            for http_method, op in path_item.items():
                if http_method not in _HTTP_METHODS:
                    continue
                if not isinstance(op, dict):
                    continue
                op_tags = op.get("tags") or ["other"]
                tag = str(op_tags[0]) if op_tags else "other"
                section = str(tags_display.get(tag, tag))
                category = op.get("x-category")
                if not isinstance(category, str) or not category:
                    category = None
                methods.append(
                    extractor.extract(
                        path,
                        http_method,
                        op,
                        section,
                        tag,
                        category=category,
                        base_url=base_url,
                    )
                )
    return Catalog(methods)