"""Turns one Wildberries OpenAPI operation into a Method with clean JSON Schema."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from wb_mcp.schema.resolver import RefResolver

AUTH_HEADERS = frozenset({"Authorization"})


class Method(BaseModel):
    """One Wildberries API endpoint with fully resolved schemas.

    ``operation_id`` is generated from the path + HTTP method because the
    Wildberries specs only rarely carry an ``operationId`` field.
    """

    operation_id: str
    api: Literal["wb"]
    method: str
    path: str
    section: str
    tag: str
    summary: str
    description: str
    auth_type: str
    category: str | None = None
    base_url: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    response_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)
    response_descriptions: dict[str, str] = Field(default_factory=dict)
    deprecated: bool = False
    deprecation_note: str | None = None
    safety: Literal["read", "write", "destructive"] = "write"
    safety_reason: str | None = None


class MethodExtractor:
    def __init__(self, resolver: RefResolver) -> None:
        self.resolver = resolver
        self.auth_type = "Authorization: {api-key}"

    def extract(
        self,
        path: str,
        http_method: str,
        op: dict[str, Any],
        section: str,
        tag: str,
        *,
        category: str | None = None,
        base_url: str | None = None,
    ) -> Method:
        op = self.resolver.resolve(op)
        op = sanitize_schema(op)
        enrich_enums_from_description(op)

        parameters: list[dict[str, Any]] = []
        for p in op.get("parameters") or []:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if not name or name in AUTH_HEADERS:
                continue
            parameters.append(
                {
                    "name": name,
                    "in": p.get("in", ""),
                    "required": bool(p.get("required", False)),
                    "description": _clean(p.get("description", "")),
                    "schema": p.get("schema", {}),
                }
            )

        request_schema: dict[str, Any] | None = None
        rb = op.get("requestBody")
        if isinstance(rb, dict):
            content = rb.get("content") or {}
            json_content = content.get("application/json") or {}
            schema = json_content.get("schema")
            if isinstance(schema, dict) and schema:
                request_schema = schema

        response_schemas: dict[str, dict[str, Any]] = {}
        response_descriptions: dict[str, str] = {}
        for code, resp in (op.get("responses") or {}).items():
            if not isinstance(resp, dict):
                continue
            response_descriptions[code] = _clean(resp.get("description", ""))
            content = resp.get("content") or {}
            json_content = content.get("application/json") or {}
            schema = json_content.get("schema")
            if isinstance(schema, dict) and schema:
                response_schemas[code] = schema

        deprecated, deprecation_note = _detect_deprecated(op)
        safety, safety_reason = _classify_safety(http_method, path, op)

        operation_id = op.get("operationId") or _generate_operation_id(
            http_method, path
        )

        return Method(
            operation_id=operation_id,
            api="wb",
            method=http_method.upper(),
            path=path,
            section=section,
            tag=tag,
            summary=(op.get("summary") or "").strip(),
            description=_clean(op.get("description", "")),
            auth_type=self.auth_type,
            category=category,
            base_url=base_url,
            parameters=parameters,
            request_schema=request_schema,
            response_schemas=response_schemas,
            response_descriptions=response_descriptions,
            deprecated=deprecated,
            deprecation_note=deprecation_note,
            safety=safety,
            safety_reason=safety_reason,
        )


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _generate_operation_id(http_method: str, path: str) -> str:
    """Generate a stable operation_id from path + HTTP method.

    Example: ``get`` + ``/content/v2/cards/list`` →
    ``get_content_v2_cards_list``.
    """
    op = http_method.lower()
    p = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    p = re.sub(r"[^A-Za-z0-9_]+", "_", p).strip("_")
    return f"{op}_{p}" if p else op


_DROP_IF_NULL_KEYS = frozenset(
    {
        "description",
        "title",
        "format",
        "$comment",
        "$id",
        "$schema",
        "contentMediaType",
        "contentEncoding",
        "enum",
        "examples",
        "required",
        "items",
        "properties",
        "additionalProperties",
        "patternProperties",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
    }
)


def _clean(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


_VALID_JSON_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"}
)
_TYPE_COERCIONS = {
    "int": "integer",
    "int32": "integer",
    "int64": "integer",
    "long": "integer",
    "float": "number",
    "double": "number",
    "bool": "boolean",
    "timestamp": "string",
    "date-time": "string",
    "date": "string",
    "uint64": "integer",
    "uint32": "integer",
    "uint16": "integer",
    "plaintext": "string",
    "uuid": "string",
}
_VALID_FORMATS = frozenset(
    {
        "date-time", "time", "date", "duration",
        "email", "idn-email",
        "hostname", "idn-hostname",
        "ipv4", "ipv6",
        "uri", "uri-reference", "iri", "iri-reference", "uuid",
        "uri-template",
        "json-pointer", "relative-json-pointer",
        "regex",
        "int32", "int64", "float", "double", "byte", "binary", "password",
    }
)


def sanitize_schema(node: Any) -> Any:
    """Make Wildberries' spec fragments safe to feed to jsonschema validators.

    Handles the same quirks as the original Ozon extractor plus a couple of
    Wildberries-specific ones (``uint64`` types, ``plaintext`` formats).
    """
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for k, v in node.items():
            if v is None and k in _DROP_IF_NULL_KEYS:
                continue
            if k == "type":
                fixed = _fix_type(v)
                if fixed is not None:
                    result[k] = fixed
                continue
            if k == "required" and isinstance(v, bool):
                continue
            if k == "format" and isinstance(v, str) and v not in _VALID_FORMATS:
                continue
            if k == "pattern" and isinstance(v, str):
                try:
                    re.compile(v)
                except re.error:
                    continue
            result[k] = sanitize_schema(v)
        return result
    if isinstance(node, list):
        return [sanitize_schema(item) for item in node]
    return node


def _fix_type(value: Any) -> Any:
    if isinstance(value, list):
        cleaned = [_fix_type(v) for v in value if _fix_type(v) is not None]
        return cleaned or None
    if not isinstance(value, str):
        return None
    if value in _VALID_JSON_TYPES:
        return value
    coerced = _TYPE_COERCIONS.get(value)
    if coerced:
        return coerced
    return None


_ENUM_BACKTICK_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_\-]{1,49})`")
_MIN_ENUM_VALUES = 3


def enrich_enums_from_description(schema: Any) -> Any:
    """Walk the schema and fill in missing enum lists from descriptions.

    Wildberries' docs often document enum values as markdown bullet lists in
    the description instead of a proper JSON Schema ``enum``. Same
    conservative heuristics as the Ozon extractor.
    """
    if isinstance(schema, dict):
        for value in schema.values():
            if isinstance(value, dict):
                enrich_enums_from_description(value)
            elif isinstance(value, list):
                for item in value:
                    enrich_enums_from_description(item)

        if _is_enum_eligible(schema):
            extracted = _extract_enum_values(schema.get("description", ""))
            if extracted is not None:
                target = schema
                if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
                    target = schema["items"]
                if "enum" not in target or not target.get("enum"):
                    target["enum"] = extracted
                    target["x-enum-source"] = "description"
    elif isinstance(schema, list):
        for item in schema:
            enrich_enums_from_description(item)
    return schema


def _is_enum_eligible(schema: dict[str, Any]) -> bool:
    if not schema.get("description"):
        return False
    t = schema.get("type")
    if t == "string":
        existing = schema.get("enum")
        return not existing
    if t == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return False
        if items.get("type") != "string":
            return False
        return not items.get("enum")
    return False


def _extract_enum_values(description: str) -> list[str] | None:
    if not description or "`" not in description:
        return None
    matches = _ENUM_BACKTICK_RE.findall(description)
    if len(matches) < _MIN_ENUM_VALUES:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            out.append(m)
    if len(out) < _MIN_ENUM_VALUES:
        return None
    return out


_READ_VERBS = frozenset(
    {
        "list", "info", "get", "tree", "totals", "count",
        "details", "history", "search", "find", "view", "show",
        "describe", "summary", "export", "fetch", "lookup",
        "preview", "status", "available", "values", "sources",
        "calendar", "news", "rating", "balance", "stocks", "orders",
    }
)

_WRITE_VERBS = frozenset(
    {
        "create", "update", "change", "set", "add", "edit", "save",
        "import", "upload", "send", "notify", "sync", "refresh",
        "activate", "deactivate", "enable", "disable", "start", "stop",
        "move", "pack", "ship", "draft", "apply", "calculate",
        "schedule", "reorder", "answer", "file", "print",
        "migrate", "transfer", "take", "redirect", "copy", "clone",
        "push", "pull", "patch", "replace",
        "finish", "pay", "checkout", "feedback", "ack", "confirm",
        "submit", "process", "execute", "register", "unregister",
        "attach", "detach", "bind", "unbind", "compensate", "verify",
        "receive", "discount", "discounts", "discounted",
        "split", "merge", "label", "labels", "request", "generate",
        "mark", "update", "edit", "change", "upsert",
    }
)

_DESTRUCTIVE_VERBS = frozenset(
    {
        "delete", "remove", "cancel", "archive", "unarchive",
        "destroy", "purge", "reject", "decline", "withdraw",
    }
)


def _classify_safety(
    method_http: str, path: str, op: dict[str, Any]
) -> tuple[str, str]:
    """Classify a method's safety: read | write | destructive.

    Wildberries' YAML carries an explicit ``x-readonly-method`` boolean on
    most operations — that is the primary signal. When it is missing we
    fall back to path-segment / operationId verb heuristics.
    """
    readonly = op.get("x-readonly-method")
    if isinstance(readonly, bool):
        if readonly:
            return "read", "x-readonly-method: true"
        destructive, reason = _verb_based_safety(method_http, path, op)
        if destructive == "destructive":
            return destructive, reason
        return "write", "x-readonly-method: false (default to write)"

    return _verb_based_safety(method_http, path, op)


def _verb_based_safety(
    method_http: str, path: str, op: dict[str, Any]
) -> tuple[str, str]:
    operation_id = op.get("operationId") or _generate_operation_id(method_http, path)
    p = path.rstrip("/").lower()
    segments = [s for s in p.split("/") if s]
    last_segment = segments[-1] if segments else ""
    if last_segment.startswith("{") and len(segments) >= 2:
        last_segment = segments[-2]

    last_tokens = set(re.split(r"[_\-]", last_segment))
    op_tokens = {w.lower() for w in re.findall(r"[A-Za-z][a-z0-9]+", operation_id)}

    if last_tokens & _DESTRUCTIVE_VERBS:
        matched = sorted(last_tokens & _DESTRUCTIVE_VERBS)[0]
        return "destructive", f"path segment '{last_segment}' contains '{matched}'"
    if last_tokens & _WRITE_VERBS:
        matched = sorted(last_tokens & _WRITE_VERBS)[0]
        return "write", f"path segment '{last_segment}' contains '{matched}'"
    if last_tokens & _READ_VERBS:
        matched = sorted(last_tokens & _READ_VERBS)[0]
        return "read", f"path segment '{last_segment}' contains '{matched}'"

    if op_tokens & _DESTRUCTIVE_VERBS:
        matched = sorted(op_tokens & _DESTRUCTIVE_VERBS)[0]
        return "destructive", f"operationId contains '{matched}'"
    if op_tokens & _WRITE_VERBS:
        matched = sorted(op_tokens & _WRITE_VERBS)[0]
        return "write", f"operationId contains '{matched}'"
    if op_tokens & _READ_VERBS:
        matched = sorted(op_tokens & _READ_VERBS)[0]
        return "read", f"operationId contains '{matched}'"

    http = method_http.upper()
    if http == "DELETE":
        return "destructive", "HTTP DELETE"
    if http in ("PUT", "PATCH"):
        return "write", f"HTTP {http}"
    if http in ("GET", "HEAD"):
        return "read", f"HTTP {http}"

    return "write", "POST without read indicators (default-to-write)"


_DEPRECATION_KEYWORDS = (
    "устарел",
    "устаревш",
    "устарева",
    "deprecated",
    "obsolete",
    "не используйте",
    "больше не доступен",
    "будет отключ",
    "no longer",
    "use instead",
    "переключитесь на",
)


def _detect_deprecated(op: dict[str, Any]) -> tuple[bool, str | None]:
    """Detect deprecated methods from the ``deprecated`` flag or description."""
    if op.get("deprecated") is True:
        return True, "marked deprecated in OpenAPI spec"

    text_blob = " ".join(
        [
            op.get("description", "") or "",
            op.get("summary", "") or "",
        ]
    ).lower()
    for kw in _DEPRECATION_KEYWORDS:
        if kw in text_blob:
            sentences = re.split(r"[.!?\n]", op.get("description", "") or "")
            for s in sentences:
                if any(k in s.lower() for k in _DEPRECATION_KEYWORDS):
                    return True, _clean(s)[:200]
            return True, "marked deprecated in description"
    return False, None