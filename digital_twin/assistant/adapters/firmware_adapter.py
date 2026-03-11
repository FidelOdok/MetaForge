"""Firmware / source code file change adapter.

Uses simple regex-based parsing (not full AST) to extract function
definitions, ``#include`` directives, and ``#define`` constants from
C/C++/Python source files.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from digital_twin.assistant.adapters.base import (
    FileChangeAdapter,
    GraphMutation,
    MutationType,
)
from digital_twin.assistant.watcher import ChangeType, FileChangeEvent
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.assistant.adapters.firmware")

# ---------------------------------------------------------------------------
# Regex patterns for C/C++ and Python
# ---------------------------------------------------------------------------

# C/C++ function: return_type func_name(...)
_C_FUNC_RE = re.compile(
    r"^[ \t]*(?:static\s+|inline\s+|extern\s+)*"
    r"(?:[\w*]+\s+)+(\w+)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)

# #include "..." or #include <...>
_INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^">]+)[>"]', re.MULTILINE)

# #define NAME ...
_DEFINE_RE = re.compile(r"^\s*#define\s+(\w+)(?:\s+(.+))?$", re.MULTILINE)

# Python def func_name(...)
_PY_FUNC_RE = re.compile(r"^[ \t]*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)


class FirmwareAdapter(FileChangeAdapter):
    """Parse C/C++/Python source files into graph mutations."""

    @property
    def supported_extensions(self) -> set[str]:
        return {".c", ".h", ".cpp", ".hpp", ".py"}

    async def parse_change(self, event: FileChangeEvent) -> list[GraphMutation]:
        """Parse a source file change into graph mutations."""
        with tracer.start_as_current_span("firmware.parse_change") as span:
            span.set_attribute("file.path", event.path)
            span.set_attribute("file.change_type", str(event.change_type))

            if event.change_type == ChangeType.DELETED:
                return [
                    GraphMutation(
                        mutation_type=MutationType.NODE_DELETED,
                        node_type="source_file",
                        node_id=event.path,
                        source_file=event.path,
                    )
                ]

            path = Path(event.path)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.error("firmware_read_failed", path=event.path, error=str(exc))
                span.record_exception(exc)
                return []

            try:
                if path.suffix == ".py":
                    mutations = self._parse_python(content, event.path)
                else:
                    mutations = self._parse_c(content, event.path)
            except Exception as exc:
                logger.error(
                    "firmware_parse_failed",
                    path=event.path,
                    suffix=path.suffix,
                    error=str(exc),
                )
                span.record_exception(exc)
                return []

            span.set_attribute("firmware.mutation_count", len(mutations))
            logger.info(
                "firmware_parsed",
                path=event.path,
                mutation_count=len(mutations),
            )
            return mutations

    # -- C/C++ parsing ------------------------------------------------------

    def _parse_c(self, content: str, source: str) -> list[GraphMutation]:
        """Extract functions, includes, and defines from C/C++ source."""
        mutations: list[GraphMutation] = []

        # Functions
        functions = _C_FUNC_RE.findall(content)
        for func_name in functions:
            mutations.append(
                GraphMutation(
                    mutation_type=MutationType.NODE_UPDATED,
                    node_type="function",
                    node_id=f"{source}::{func_name}",
                    properties={
                        "name": func_name,
                        "language": "c",
                        "source_file": source,
                    },
                    source_file=source,
                )
            )

        # Includes
        includes = _INCLUDE_RE.findall(content)
        if includes:
            mutations.append(
                GraphMutation(
                    mutation_type=MutationType.NODE_UPDATED,
                    node_type="source_file",
                    node_id=f"{source}::includes",
                    properties={
                        "includes": includes,
                        "source_file": source,
                    },
                    source_file=source,
                )
            )

        # Defines
        defines: dict[str, str] = {}
        for match in _DEFINE_RE.finditer(content):
            name = match.group(1)
            value = (match.group(2) or "").strip()
            defines[name] = value

        if defines:
            mutations.append(
                GraphMutation(
                    mutation_type=MutationType.NODE_UPDATED,
                    node_type="source_file",
                    node_id=f"{source}::defines",
                    properties={
                        "defines": defines,
                        "source_file": source,
                    },
                    source_file=source,
                )
            )

        return mutations

    # -- Python parsing -----------------------------------------------------

    def _parse_python(self, content: str, source: str) -> list[GraphMutation]:
        """Extract function definitions from Python source."""
        mutations: list[GraphMutation] = []

        functions = _PY_FUNC_RE.findall(content)
        for func_name in functions:
            mutations.append(
                GraphMutation(
                    mutation_type=MutationType.NODE_UPDATED,
                    node_type="function",
                    node_id=f"{source}::{func_name}",
                    properties={
                        "name": func_name,
                        "language": "python",
                        "source_file": source,
                    },
                    source_file=source,
                )
            )

        return mutations
