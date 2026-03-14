"""Parser for CalculiX .frd output files -- extracts stress and displacement fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.calculix.result_parser")


class FrdParseError(Exception):
    """Raised when .frd file parsing fails."""


def parse_frd_file(frd_path: str) -> dict[str, Any]:
    """Parse a CalculiX .frd result file and extract stress/displacement data.

    The .frd format is a fixed-width text format. Key sections:
    - ``2C`` header lines define the result block (STRESS, DISP, etc.)
    - ``100C`` lines define block labels and component names
    - ``-1`` lines contain node data
    - ``-3`` lines mark end of a node data block

    Args:
        frd_path: Path to the .frd file.

    Returns:
        Dict containing:
            - stress: Dict with von_mises per node, max/min/avg stats
            - displacement: Dict with magnitude per node, max/min/avg stats
            - node_count: Number of nodes with results
            - metadata: File info

    Raises:
        FileNotFoundError: If the file does not exist.
        FrdParseError: If the file cannot be parsed.
    """
    path = Path(frd_path)
    if not path.exists():
        raise FileNotFoundError(f"FRD file not found: {frd_path}")

    with tracer.start_as_current_span("calculix.parse_frd") as span:
        span.set_attribute("calculix.frd_file", frd_path)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            span.record_exception(exc)
            raise FrdParseError(f"Failed to read FRD file: {exc}") from exc

        lines = content.splitlines()

        stress_data = _extract_stress(lines)
        displacement_data = _extract_displacement(lines)

        node_count = max(
            len(stress_data.get("nodes", {})),
            len(displacement_data.get("nodes", {})),
        )
        span.set_attribute("calculix.node_count", node_count)

        result: dict[str, Any] = {
            "stress": stress_data,
            "displacement": displacement_data,
            "node_count": node_count,
            "metadata": {
                "file": frd_path,
                "file_size_bytes": path.stat().st_size,
                "line_count": len(lines),
            },
        }

        logger.info(
            "Parsed FRD file",
            frd_file=frd_path,
            node_count=node_count,
            has_stress=bool(stress_data.get("nodes")),
            has_displacement=bool(displacement_data.get("nodes")),
        )

        return result


def _extract_stress(lines: list[str]) -> dict[str, Any]:
    """Extract von Mises stress data from .frd lines.

    In .frd format, stress blocks are identified by a ``100C`` header line
    containing ``STRESS``. Subsequent ``100C`` lines (e.g. column headers)
    are part of the same block. Each node's stress is on a ``-1`` line with
    6 components (SXX, SYY, SZZ, SXY, SXZ, SYZ). Von Mises is computed
    from these components. The block ends at a ``-3`` line.

    Returns:
        Dict with keys: nodes (dict[node_id, von_mises]), max, min, avg.
    """
    nodes: dict[int, float] = {}
    in_stress_block = False

    for line in lines:
        stripped = line.strip()

        # Detect stress result block header (e.g. "100CL  101STRESS")
        if stripped.startswith("100C") and "STRESS" in line.upper():
            in_stress_block = True
            continue

        # 100C lines within the block are column headers -- skip them
        if in_stress_block and stripped.startswith("100C"):
            continue

        # A new 2C header means a new result block -- exit stress block
        if in_stress_block and stripped.startswith("2C"):
            in_stress_block = False
            # Check if this new block is also STRESS (unlikely but safe)
            if "STRESS" in line.upper():
                in_stress_block = True
            continue

        # End of data block
        if in_stress_block and stripped.startswith("-3"):
            in_stress_block = False
            continue

        # Node data lines start with " -1"
        if in_stress_block and line.startswith(" -1"):
            values = _parse_node_data_line(line)
            if values is not None and len(values) >= 7:
                node_id = int(values[0])
                # Components: SXX, SYY, SZZ, SXY, SXZ, SYZ
                sxx, syy, szz = values[1], values[2], values[3]
                sxy, sxz, syz = values[4], values[5], values[6]
                von_mises = _compute_von_mises(sxx, syy, szz, sxy, sxz, syz)
                nodes[node_id] = round(von_mises, 4)

    return _build_stats(nodes, "von_mises_mpa")


def _extract_displacement(lines: list[str]) -> dict[str, Any]:
    """Extract displacement data from .frd lines.

    Displacement blocks are identified by ``DISP`` in a ``100C`` header.
    Subsequent ``100C`` lines are column headers within the same block.
    Each node has 3 components (DX, DY, DZ). Magnitude is sqrt(dx^2+dy^2+dz^2).
    The block ends at a ``-3`` line.

    Returns:
        Dict with keys: nodes (dict[node_id, magnitude]), max, min, avg.
    """
    nodes: dict[int, float] = {}
    in_disp_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("100C") and "DISP" in line.upper():
            in_disp_block = True
            continue

        if in_disp_block and stripped.startswith("100C"):
            continue

        if in_disp_block and stripped.startswith("2C"):
            in_disp_block = False
            if "DISP" in line.upper():
                in_disp_block = True
            continue

        if in_disp_block and stripped.startswith("-3"):
            in_disp_block = False
            continue

        if in_disp_block and line.startswith(" -1"):
            values = _parse_node_data_line(line)
            if values is not None and len(values) >= 4:
                node_id = int(values[0])
                dx, dy, dz = values[1], values[2], values[3]
                magnitude = (dx**2 + dy**2 + dz**2) ** 0.5
                nodes[node_id] = round(magnitude, 6)

    return _build_stats(nodes, "magnitude_mm")


def _parse_node_data_line(line: str) -> list[float] | None:
    """Parse a -1 data line from .frd format.

    Format: " -1" followed by node ID and float values in fixed-width columns.
    The node ID occupies columns 3-12, values occupy 12-char columns after that.

    Returns:
        List of [node_id, val1, val2, ...] or None if parsing fails.
    """
    try:
        # Strip the " -1" prefix
        data_part = line[3:]
        # Node ID is first 10 chars
        node_id = float(data_part[:10].strip())
        # Remaining values are in 12-char columns
        rest = data_part[10:]
        values: list[float] = [node_id]
        i = 0
        while i < len(rest):
            chunk = rest[i : i + 12].strip()
            if chunk:
                values.append(float(chunk))
            i += 12
        return values
    except (ValueError, IndexError):
        return None


def _compute_von_mises(
    sxx: float, syy: float, szz: float, sxy: float, sxz: float, syz: float
) -> float:
    """Compute von Mises equivalent stress from 6 stress components."""
    term1 = (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
    term2 = 6.0 * (sxy**2 + sxz**2 + syz**2)
    return ((term1 + term2) / 2.0) ** 0.5


def _build_stats(nodes: dict[int, float], value_label: str) -> dict[str, Any]:
    """Build statistics from node data."""
    if not nodes:
        return {"nodes": {}, "max": 0.0, "min": 0.0, "avg": 0.0, "unit": value_label}

    values = list(nodes.values())
    return {
        "nodes": nodes,
        "max": max(values),
        "min": min(values),
        "avg": round(sum(values) / len(values), 4),
        "unit": value_label,
    }


def extract_results(frd_path: str, include_node_data: bool = True) -> dict[str, Any]:
    """High-level result extraction for MCP tool interface.

    This is the entry point called by the MCP server's ``calculix.extract_results`` tool.

    Args:
        frd_path: Path to the .frd file.
        include_node_data: Whether to include per-node data (can be large).

    Returns:
        Structured dict with stress/displacement summaries.
    """
    result = parse_frd_file(frd_path)

    if not include_node_data:
        # Strip per-node data to reduce payload size
        for field in ("stress", "displacement"):
            if field in result and "nodes" in result[field]:
                node_count = len(result[field]["nodes"])
                result[field]["nodes"] = {}
                result[field]["node_count_stripped"] = node_count

    return result
