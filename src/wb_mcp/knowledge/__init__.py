"""Curated Wildberries API knowledge — workflows, rate limits, errors, quirks, examples.

These YAML files complement the auto-extracted swagger schemas with information
that no machine can derive from OpenAPI alone. Loaded once at server start.
"""

from wb_mcp.knowledge.loader import KnowledgeBase, load_knowledge
from wb_mcp.knowledge.models import (
    DescriptionOverride,
    ErrorEntry,
    MethodExample,
    PaginationPattern,
    Quirk,
    RateLimit,
    SafetyOverride,
    Workflow,
    WorkflowStep,
)

__all__ = [
    "DescriptionOverride",
    "ErrorEntry",
    "KnowledgeBase",
    "MethodExample",
    "PaginationPattern",
    "Quirk",
    "RateLimit",
    "SafetyOverride",
    "Workflow",
    "WorkflowStep",
    "load_knowledge",
]