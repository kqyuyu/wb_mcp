"""Loads Wildberries OpenAPI 3.0 YAML specs.

Wildberries publishes its API as a set of YAML files, one per domain
section (general, items, orders, promotion, analytics, reports, finances,
...). All of them are parsed and returned as a list of spec dicts.

The bundled YAML files live in the ``ozon_mcp.data`` package directory
(reused from the original repository layout — the new ``wb_mcp`` package
reads them without duplicating tens of megabytes of spec data).
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml

DATA_PKG = "ozon_mcp.data"


def load_wb_specs() -> list[dict[str, Any]]:
    """Read every bundled Wildberries YAML spec and return parsed dicts.

    Files are processed in sorted order (the leading number in the filename
    controls grouping, e.g. ``01-general.yaml``, ``02-items.yaml``).
    """
    pkg = files(DATA_PKG)
    specs: list[dict[str, Any]] = []
    for resource in sorted(pkg.iterdir()):
        if not resource.name.endswith(".yaml"):
            continue
        text = resource.read_text(encoding="utf-8")
        spec: Any = yaml.safe_load(text)
        if not isinstance(spec, dict) or "paths" not in spec:
            raise ValueError(f"{resource.name}: missing required OpenAPI 'paths' section")
        specs.append(spec)
    if not specs:
        raise ValueError(f"no Wildberries YAML specs found in {DATA_PKG}")
    return specs