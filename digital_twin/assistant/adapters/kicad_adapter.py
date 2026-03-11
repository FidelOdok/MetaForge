"""KiCad file change adapter.

Parses KiCad S-expression schematic (``.kicad_sch``) and PCB
(``.kicad_pcb``) files, extracting component references, values,
footprints, track widths, via counts, and board dimensions.
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
tracer = get_tracer("digital_twin.assistant.adapters.kicad")

# ---------------------------------------------------------------------------
# S-expression regex helpers (simple, non-recursive)
# ---------------------------------------------------------------------------

# Match (symbol (property "Reference" "R1" ...) (property "Value" "10k" ...) ...)
_SYMBOL_BLOCK_RE = re.compile(
    r"\(symbol\s[^()]*"  # opening (symbol ...
    r"(?:\([^()]*(?:\([^()]*\))*[^()]*\)\s*)*"  # nested parens
    r"\)",
    re.DOTALL,
)

_PROPERTY_RE = re.compile(
    r'\(property\s+"([^"]+)"\s+"([^"]*)"',
)

# PCB patterns
_TRACK_WIDTH_RE = re.compile(r"\(segment\s.*?\(width\s+([\d.]+)\)")
_VIA_RE = re.compile(r"\(via\s")
_BOARD_AREA_RE = re.compile(
    r"\(gr_rect\s[^)]*\(start\s+([\d.]+)\s+([\d.]+)\)\s*\(end\s+([\d.]+)\s+([\d.]+)\)"
)


class KicadAdapter(FileChangeAdapter):
    """Parse KiCad schematic and PCB files into graph mutations."""

    @property
    def supported_extensions(self) -> set[str]:
        return {".kicad_sch", ".kicad_pcb"}

    async def parse_change(self, event: FileChangeEvent) -> list[GraphMutation]:
        """Parse a KiCad file change into graph mutations."""
        with tracer.start_as_current_span("kicad.parse_change") as span:
            span.set_attribute("file.path", event.path)
            span.set_attribute("file.change_type", str(event.change_type))

            if event.change_type == ChangeType.DELETED:
                return [
                    GraphMutation(
                        mutation_type=MutationType.NODE_DELETED,
                        node_type="kicad_file",
                        node_id=event.path,
                        source_file=event.path,
                    )
                ]

            path = Path(event.path)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.error("kicad_read_failed", path=event.path, error=str(exc))
                span.record_exception(exc)
                return []

            try:
                if path.suffix == ".kicad_sch":
                    mutations = self._parse_schematic(content, event.path)
                else:
                    mutations = self._parse_pcb(content, event.path)
            except Exception as exc:
                logger.error(
                    "kicad_parse_failed",
                    path=event.path,
                    suffix=path.suffix,
                    error=str(exc),
                )
                span.record_exception(exc)
                return []

            span.set_attribute("kicad.mutation_count", len(mutations))
            logger.info(
                "kicad_parsed",
                path=event.path,
                mutation_count=len(mutations),
            )
            return mutations

    # -- Schematic parsing --------------------------------------------------

    def _parse_schematic(self, content: str, source: str) -> list[GraphMutation]:
        """Extract component references, values, and footprints."""
        mutations: list[GraphMutation] = []
        components = self._extract_components(content)

        for comp in components:
            ref = comp.get("Reference", "")
            if not ref:
                continue
            mutation_type = MutationType.NODE_UPDATED
            mutations.append(
                GraphMutation(
                    mutation_type=mutation_type,
                    node_type="schematic_component",
                    node_id=f"{source}::{ref}",
                    properties={
                        "reference": ref,
                        "value": comp.get("Value", ""),
                        "footprint": comp.get("Footprint", ""),
                        "source_file": source,
                    },
                    source_file=source,
                )
            )
        return mutations

    def _extract_components(self, content: str) -> list[dict[str, str]]:
        """Extract property dicts from (symbol ...) blocks."""
        components: list[dict[str, str]] = []
        for match in _SYMBOL_BLOCK_RE.finditer(content):
            block = match.group(0)
            props: dict[str, str] = {}
            for prop_match in _PROPERTY_RE.finditer(block):
                props[prop_match.group(1)] = prop_match.group(2)
            if props:
                components.append(props)
        return components

    # -- PCB parsing --------------------------------------------------------

    def _parse_pcb(self, content: str, source: str) -> list[GraphMutation]:
        """Extract track widths, via count, and board dimensions."""
        mutations: list[GraphMutation] = []

        # Track widths
        track_widths = sorted(set(_TRACK_WIDTH_RE.findall(content)))

        # Via count
        via_count = len(_VIA_RE.findall(content))

        # Board dimensions (look for gr_rect on Edge.Cuts or just any gr_rect)
        board_dims: dict[str, float] = {}
        area_match = _BOARD_AREA_RE.search(content)
        if area_match:
            x1, y1, x2, y2 = (float(v) for v in area_match.groups())
            board_dims = {
                "width_mm": abs(x2 - x1),
                "height_mm": abs(y2 - y1),
            }

        mutations.append(
            GraphMutation(
                mutation_type=MutationType.NODE_UPDATED,
                node_type="pcb_layout",
                node_id=f"{source}::pcb",
                properties={
                    "track_widths_mm": track_widths,
                    "via_count": via_count,
                    "source_file": source,
                    **board_dims,
                },
                source_file=source,
            )
        )
        return mutations
