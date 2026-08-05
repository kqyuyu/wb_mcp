"""Recursive $ref resolver for OpenAPI 3.0 specs.

Walks any node and inlines all $ref pointers, regardless of whether they live
inside a schema, requestBody, response, or parameter. Cycles are detected via
a stack of refs currently being resolved — if a ref reappears in that stack,
we emit a `{"$ref": ...}` placeholder to terminate, instead of recursing forever.
"""

from __future__ import annotations

from typing import Any


class RefResolver:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    def resolve(self, node: Any) -> Any:
        """Return a deep copy of *node* with every $ref inlined."""
        return self._inline(node, ())

    def _inline(self, node: Any, path: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"]
                if ref in path:
                    target_name = ref.rsplit("/", 1)[-1]
                    return {
                        "type": "object",
                        "x-cycle-ref": target_name,
                        "description": f"Recursive reference to {target_name}",
                    }
                target = self._lookup(ref)
                if target is None:
                    target_name = ref.rsplit("/", 1)[-1]
                    return {
                        "type": "object",
                        "x-broken-ref": target_name,
                        "description": f"Broken reference to {target_name}",
                    }
                resolved = self._inline(target, (*path, ref))
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                if siblings and isinstance(resolved, dict):
                    merged = dict(resolved)
                    for k, v in siblings.items():
                        merged[k] = self._inline(v, path)
                    return merged
                return resolved
            return {k: self._inline(v, path) for k, v in node.items()}
        if isinstance(node, list):
            return [self._inline(item, path) for item in node]
        return node

    def _lookup(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            return None
        parts = ref[2:].split("/")
        cursor: Any = self.spec
        for part in parts:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(cursor, dict) or part not in cursor:
                return None
            cursor = cursor[part]
        return cursor